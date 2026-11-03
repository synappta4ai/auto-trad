"""Simulador de opciones binarias a un minuto sobre velas reales (paper).

No conecta con ningún bróker ni envía órdenes: al cierre de cada vela de 1m
apunta la apuesta que haría la regla y la liquida contra el cierre de la vela
siguiente, con un payout configurable. Sirve para ver la mecánica en vivo y,
sobre todo, para comprobar que el acierto real no llega al umbral del payout.
"""
import copy
import logging
import threading
import time

import numpy as np

from .binary import breakeven, ev_per_bet, latest_signal
from .data import make_feed

log = logging.getLogger("live1m")

TF = "1m"
MIN_CANDLES = 120
CHART_BARS = 60
MAX_BETS = 300
REFRESH_SECONDS = 15
TICK_SECONDS = 2


def direction_for(close, mode="every", window=1, quantile=0.90, side="reversion"):
    """Dirección para la próxima vela usando solo velas ya cerradas.

    `mode="every"` apuesta siempre (en contra o a favor del último movimiento);
    `mode="signal"` solo cuando el movimiento supera el cuantil.
    """
    close = np.asarray(close, dtype=float)
    if close.size < max(MIN_CANDLES, window + 2):
        return 0
    if mode == "signal":
        sig = latest_signal(close, horizon=1, window=window, quantile=quantile, side=side)
        if not sig or not sig.get("extreme"):
            return 0
        return int(sig["direction"])
    move = close[-1] - close[-1 - window]
    if move == 0:
        return 0
    follow = 1 if move > 0 else -1
    return follow if side == "momentum" else -follow


def settle_bet(bet, close, ts, payout):
    """Liquida una apuesta al vencimiento contra el cierre de esa vela.

    Empate (cierre igual a la entrada) cuenta como pérdida, como en un bróker
    real de binarias.
    """
    # `ts` es la apertura de la vela que liquida: cuenta si CIERRA en el vencimiento
    if bet is None or int(ts) + 60 < int(bet["expiry_ts"]):
        return None
    close = float(close)
    entry = float(bet["entry"])
    tie = close == entry
    won = (not tie) and ((close > entry) if bet["direction"] > 0 else (close < entry))
    pnl = round(float(bet["stake"]) * (float(payout) if won else -1.0), 2)
    return {"won": bool(won), "tie": bool(tie), "close": close, "close_ts": int(ts),
            "pnl": pnl, "balance_after": round(float(bet["balance_before"]) + pnl, 2)}


class Live1m:
    """Un hilo que, cada dos segundos, mira la última vela de 1m y gestiona la apuesta."""

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.lock = threading.Lock()
        self.thread = None
        self.stop_evt = threading.Event()
        self.feed = None
        self.state = {"status": "idle", "error": None, "bets": [], "chart": [], "equity": [],
                      "pending": None, "settled": 0, "wins": 0, "losses": 0, "streak": 0,
                      "worst_streak": 0, "balance": 0.0, "bankroll": 0.0, "payout": 0.82,
                      "symbol": None, "stake": 0.0, "mode": "every", "quantile": 0.9,
                      "window": 1, "side": "reversion", "refresh": True, "refreshed": 0.0,
                      "last_bar_ts": 0, "price": None, "started": 0.0}

    # ------------------------------------------------------------------ arranque
    def _params(self, payload):
        from .sweep import _symbols

        p = payload or {}
        sym = (p.get("symbol") or (self.cfg.symbols[0] if self.cfg.symbols else "BTC/USDT"))
        symbol = _symbols([sym])[0]

        def num(key, default):
            """Cero es un valor válido (y debe fallar), así que no se usa `or`."""
            v = p.get(key, None)
            return float(default) if v is None or v == "" else float(v)

        stake = num("stake", 5.0)
        bankroll = num("bankroll", 1000.0)
        payout = num("payout", 0.82)
        if payout >= 1:
            payout /= 100.0
        mode = (p.get("mode") or "every").strip().lower()
        quantile = num("quantile", 0.90)
        window = int(num("window", 1))
        side = (p.get("side") or "reversion").strip().lower()
        if stake <= 0:
            raise ValueError("la apuesta debe ser mayor que 0")
        if bankroll < stake:
            raise ValueError("la banca no puede ser menor que la apuesta")
        if not 0.5 <= payout <= 0.99:
            raise ValueError("payout entre 0.5 y 0.99 (50 % – 99 %)")
        if mode not in ("every", "signal"):
            raise ValueError("modo 'every' (cada vela) o 'signal' (solo señal extrema)")
        if not 0.5 <= quantile <= 0.99:
            raise ValueError("cuantil entre 0.5 y 0.99")
        if not 1 <= window <= 50:
            raise ValueError("ventana entre 1 y 50 velas")
        if side not in ("reversion", "momentum"):
            raise ValueError("lado 'reversion' o 'momentum'")
        return {"symbol": symbol, "stake": stake, "bankroll": bankroll, "payout": payout,
                "mode": mode, "quantile": quantile, "window": window, "side": side,
                "refresh": bool(p.get("refresh", self.cfg.data_source == "exchange"))}

    def _reset(self, p):
        with self.lock:
            self.state = {
                "status": "running", "error": None, "started": time.time(),
                "symbol": p["symbol"], "stake": p["stake"], "payout": p["payout"],
                "bankroll": p["bankroll"], "balance": p["bankroll"], "mode": p["mode"],
                "quantile": p["quantile"], "window": p["window"], "side": p["side"],
                "refresh": p["refresh"], "refreshed": 0.0, "pending": None, "bets": [],
                "settled": 0, "wins": 0, "losses": 0, "streak": 0, "worst_streak": 0,
                "equity": [], "chart": [], "last_bar_ts": 0, "price": None,
            }

    def start(self, payload=None):
        p = self._params(payload)
        if self.thread is not None and self.thread.is_alive():
            raise RuntimeError("el simulador ya está en marcha; páralo antes de reiniciar")
        self.stop_evt = threading.Event()
        self._reset(p)
        self.thread = threading.Thread(target=self._loop, name="live1m", daemon=True)
        self.thread.start()
        return {"ok": True, "symbol": p["symbol"], "payout": p["payout"], "mode": p["mode"],
                "stake": p["stake"], "bankroll": p["bankroll"]}

    def stop(self):
        self.stop_evt.set()
        th = self.thread
        if th is not None and th.is_alive():
            th.join(timeout=5)
        self.thread = None
        with self.lock:
            if self.state["status"] == "running":
                self.state["status"] = "stopped"
            settled = self.state["settled"]
        return {"ok": True, "settled": settled}

    # -------------------------------------------------------------------- ciclo
    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                self._tick()
            except Exception as exc:               # un fallo puntual no mata el hilo
                log.exception("simulador 1m")
                with self.lock:
                    self.state["error"] = str(exc) or exc.__class__.__name__
            self.stop_evt.wait(TICK_SECONDS)

    def _candles(self):
        with self.lock:
            sym = self.state["symbol"]
            refresh = self.state["refresh"]
            now = time.time()
            stale = now - self.state["refreshed"] >= REFRESH_SECONDS
        if refresh and self.feed is None and self.cfg.data_source == "exchange":
            c = copy.copy(self.cfg)
            c.timeframe = TF
            c.history_bars = 400
            self.feed = make_feed(c, self.store)
        if refresh and self.feed is not None and stale:
            self.feed.candles(sym)                  # guarda en la caché las velas cerradas
            with self.lock:
                self.state["refreshed"] = now
        return self.store.candles(sym, TF, limit=1500)

    def _tick(self):
        with self.lock:
            st = self.state
            if st["status"] != "running":
                return
            sym, stake, payout = st["symbol"], st["stake"], st["payout"]
            mode, quantile = st["mode"], st["quantile"]
            window, side = st["window"], st["side"]
        try:
            df = self._candles()
        except Exception as exc:
            with self.lock:
                self.state["error"] = "no se pudieron traer velas: %s" % (str(exc) or exc.__class__.__name__)
            return
        if df is None or len(df) < MIN_CANDLES:
            with self.lock:
                self.state["error"] = ("faltan velas de 1m de %s (%s)" %
                                       (sym, 0 if df is None else len(df)))
            return
        ts = [int(t.timestamp()) for t in df.index]
        close = df["close"].to_numpy(dtype=float)
        last_ts, last_close = ts[-1], float(close[-1])
        with self.lock:
            st = self.state
            if st["status"] != "running":
                return
            st["error"] = None
            st["price"] = last_close
            lo = max(0, len(ts) - CHART_BARS)
            st["chart"] = [{"t": ts[i], "o": float(df["open"].iat[i]),
                            "h": float(df["high"].iat[i]), "l": float(df["low"].iat[i]),
                            "c": float(close[i])} for i in range(lo, len(ts))]
            pending = st["pending"]
            if pending is not None:
                # se liquida con la vela que CIERRA en el vencimiento: su ts es el de apertura
                j = next((i for i, t in enumerate(ts) if t + 60 >= pending["expiry_ts"]), None)
                out = settle_bet(pending, close[j], ts[j], payout) if j is not None else None
                if out is not None:
                    st["bets"].insert(0, dict(pending, result=out))
                    del st["bets"][MAX_BETS:]
                    st["pending"] = None
                    st["settled"] += 1
                    st["balance"] = out["balance_after"]
                    if out["won"]:
                        st["wins"] += 1
                        st["streak"] = st["streak"] + 1 if st["streak"] > 0 else 1
                    else:
                        st["losses"] += 1
                        st["streak"] = st["streak"] - 1 if st["streak"] < 0 else -1
                    st["worst_streak"] = max(st["worst_streak"], -min(0, st["streak"]))
                    st["equity"].append([ts[j], st["balance"]])
                    del st["equity"][:-MAX_BETS]
                    if st["balance"] < stake:
                        st["status"] = "broke"
                        st["error"] = "sin saldo para seguir"
                        self.stop_evt.set()
            if st["status"] == "running" and st["pending"] is None and last_ts > st["last_bar_ts"]:
                st["last_bar_ts"] = last_ts
                d = direction_for(close, mode=mode, window=window, quantile=quantile, side=side)
                if d:
                    # el vencimiento es el próximo cierre de minuto, no "60 s desde la vela
                    # guardada": la vela cerrada puede tener ya medio minuto de retraso.
                    expiry = max((int(time.time()) // 60 + 1) * 60, last_ts + 60)
                    st["pending"] = {"direction": int(d), "entry": last_close, "entry_ts": last_ts,
                                     "expiry_ts": expiry, "stake": stake, "payout": payout,
                                     "balance_before": st["balance"]}

    def run_once(self, payload=None):
        """Un solo ciclo, bloqueando (pruebas y arranque en frío)."""
        if payload is not None:
            self._reset(self._params(payload))
        self._tick()
        return self.snapshot()

    # ------------------------------------------------------------------ lectura
    def snapshot(self):
        with self.lock:
            st = {k: v for k, v in self.state.items()
                  if k not in ("bets", "chart", "equity", "pending")}
            st["bets"] = [dict(b) for b in self.state["bets"][:40]]
            st["chart"] = [dict(c) for c in self.state["chart"]]
            st["equity"] = [list(e) for e in self.state["equity"]]
            st["pending"] = dict(self.state["pending"]) if self.state["pending"] else None
            st["running"] = self.state["status"] == "running"
            st["breakeven_pct"] = round(breakeven(self.state["payout"]) * 100, 2)
            won = self.state["wins"]
            n = self.state["settled"]
            st["hit_pct"] = round(won / n * 100, 2) if n else None
            st["ev_per_bet"] = round(ev_per_bet(won / n, self.state["payout"]), 4) if n else None
            st["net"] = round(self.state["balance"] - self.state["bankroll"], 2)
        st["expires_in"] = max(0, int(st["pending"]["expiry_ts"] - time.time())) if st["pending"] else 0
        st["defaults"] = {"symbol": self.cfg.symbols[0] if self.cfg.symbols else "BTC/USDT",
                          "payout": 0.82, "stake": 5.0, "bankroll": 1000.0, "mode": "every"}
        return st
