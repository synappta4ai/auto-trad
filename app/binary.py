"""Laboratorio binario: medir si una regla direccional supera el umbral del payout.

Una opción binaria paga un importe fijo si aciertas y pierde el stake si fallas:
no hay stop, ni salidas parciales, ni comisiones. Por eso el único número que
importa es el acierto frente al umbral `1/(1+payout)`, y ese umbral no depende
del modelo, depende del bróker.

Esto no opera nada: mide. Trabaja con las velas guardadas en SQLite, elige el
umbral de "movimiento extremo" SOLO con la ventana de entrenamiento y evalúa en
la siguiente, sin solapar apuestas que aún no habían vencido. Devuelve acierto
con intervalo de confianza, valor esperado por apuesta para varios payouts,
drawdown en stakes y cuántas apuestas harían falta para demostrar la ventaja.

En la UE las binarias están prohibidas para clientes minoristas, así que esto
sirve para decidir con datos si el camino merece la pena, no para conectarlo a
un bróker.
"""
import argparse
import copy
import json
import logging
import math
import threading
import time

import numpy as np

from .data import make_feed, tf_seconds

log = logging.getLogger("binary")

PAYOUTS = (0.70, 0.75, 0.80, 0.85, 0.90)
HORIZONS = (1, 4, 24)
QUANTILES = (0.80, 0.90, 0.95)
TIMEFRAMES = ("15m", "1h", "4h", "1d")
MAX_SYMBOLS = 30


def breakeven(payout):
    """Acierto mínimo para no perder dinero con ese payout."""
    return 1.0 / (1.0 + float(payout))


def ev_per_bet(p, payout):
    """Valor esperado por apuesta unitaria (stake = 1)."""
    return p * float(payout) - (1.0 - p)


def kelly(p, payout):
    """Fracción de banca de Kelly para un pago de `payout` a 1. Negativa -> 0."""
    b = float(payout)
    f = (p * (1.0 + b) - 1.0) / b
    return max(0.0, f)


def wilson(k, n, z=1.96):
    """Intervalo de confianza del acierto (Wilson, válido con n moderado)."""
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def bets_to_prove(p, payout, z=1.96):
    """Apuestas necesarias para que la ventaja supere el ruido (95 %)."""
    edge = ev_per_bet(p, payout)
    if edge <= 0:
        return None
    sd = math.sqrt(max(p * (1 - p), 1e-12)) * (1.0 + float(payout))
    return int(math.ceil((z * sd / edge) ** 2))


def _max_drawdown(pnls):
    eq = 0.0
    peak = 0.0
    worst = 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        worst = max(worst, peak - eq)
    return float(worst)


def _longest_streak(pnls):
    run = best = 0
    for x in pnls:
        run = run + 1 if x < 0 else 0
        best = max(best, run)
    return int(best)


def walk_forward(close, horizon=1, window=1, quantile=0.90, payout=0.85,
                 train_bars=20000, test_bars=4000, folds=5, side="reversion"):
    """Acierto fuera de muestra de apostar contra (o a favor de) movimientos extremos.

    `side="reversion"` apuesta a que el precio se gira tras un movimiento mayor
    que el cuantil `quantile` de |retorno|; `side="momentum"` apuesta a que sigue.
    El umbral de "extremo" se calcula solo con la ventana de entrenamiento.
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n < train_bars + test_bars + horizon + 100:
        raise ValueError(f"hacen falta al menos {train_bars + test_bars + horizon + 100} "
                         f"velas y hay {n}")
    lr = np.full(n, np.nan)
    lr[window:] = np.log(close[window:] / close[:-window])
    fwd = np.full(n, np.nan)
    fwd[:n - horizon] = np.log(close[horizon:] / close[:-horizon])

    out_folds, pnls, wins_all, bets_all, ties_all = [], [], 0, 0, 0
    start = 0
    for k in range(int(folds)):
        tr_a, tr_b = start, start + train_bars
        te_a = tr_b
        te_b = min(tr_b + test_bars, n - horizon)
        if te_b - te_a < 100 or tr_b > n:
            break
        train = np.abs(lr[tr_a:tr_b])
        train = train[np.isfinite(train)]
        if train.size < 200:
            break
        thr = float(np.quantile(train, quantile))
        idx = np.arange(te_a, te_b)
        # la apuesta tiene que vencer dentro de su propio tramo de test: si no,
        # se estaría midiendo con precios de la ventana siguiente
        ok = (np.isfinite(lr[idx]) & np.isfinite(fwd[idx])
              & (np.abs(lr[idx]) >= thr) & (idx + horizon <= te_b))
        idx = idx[ok]
        ties = int(np.sum(fwd[idx] == 0))
        idx = idx[fwd[idx] != 0]
        if idx.size:
            direction = np.sign(lr[idx]) if side == "momentum" else -np.sign(lr[idx])
            win = np.sign(fwd[idx]) == direction
            pnl = np.where(win, float(payout), -1.0)
            pnls.extend(pnl.tolist())
            wins_all += int(win.sum())
            bets_all += int(idx.size)
            ties_all += ties
            out_folds.append({
                "fold": k + 1, "threshold": round(thr, 5),
                "train_first": int(tr_a), "train_last": int(tr_b),
                "test_first": int(te_a), "test_last": int(te_b),
                "first_index": int(idx[0]), "last_index": int(idx[-1]),
                "bets": int(idx.size), "wins": int(win.sum()), "ties": ties,
                "hit_pct": round(float(win.mean()) * 100, 2),
            })
        start += int(test_bars)

    if not bets_all:
        raise ValueError("sin apuestas: baja el cuantil o amplía las ventanas")

    p = wins_all / bets_all
    lo, hi = wilson(wins_all, bets_all)
    res = {"horizon": int(horizon), "window": int(window), "quantile": float(quantile),
           "side": side, "payout": float(payout), "bets": bets_all, "wins": wins_all,
           "ties": ties_all, "hit_pct": round(p * 100, 3),
           "ci_low_pct": round(lo * 100, 3), "ci_high_pct": round(hi * 100, 3),
           "breakeven_pct": round(breakeven(payout) * 100, 3),
           "ev_per_bet": round(ev_per_bet(p, payout), 4),
           "kelly_fraction": round(kelly(p, payout), 4),
           "max_drawdown_stakes": round(_max_drawdown(pnls), 2),
           "worst_loss_streak": _longest_streak(pnls),
           "net_stakes": round(float(np.sum(pnls)), 2),
           "bets_to_prove": bets_to_prove(p, payout),
           "ev_by_payout": {f"{int(pt * 100)}%": round(ev_per_bet(p, pt), 4) for pt in PAYOUTS},
           "folds": out_folds}
    res["verdict"] = _verdict(lo, hi, breakeven(payout))
    return res


def _verdict(lo, hi, be):
    if lo > be:
        return "ventaja"
    if hi < be:
        return "sin_ventaja"
    return "indeterminado"


def summarize(results, payout=0.85):
    """¿La ventaja se repite entre símbolos o es una celda afortunada?"""
    valid = [r for r in results if r.get("bets")]
    if not valid:
        return {"symbols": 0, "verdict": "sin_datos"}
    with_edge = [r for r in valid if r["verdict"] == "ventaja"]
    be = breakeven(payout) * 100
    return {"symbols": len(valid), "with_edge": len(with_edge),
            "with_edge_symbols": [r["symbol"] for r in with_edge if r.get("symbol")],
            "median_hit_pct": round(float(np.median([r["hit_pct"] for r in valid])), 2),
            "breakeven_pct": round(be, 2),
            "verdict": ("repetible" if len(with_edge) >= max(2, len(valid) // 2) else
                        "aislado" if with_edge else "sin_ventaja")}


def latest_signal(close, horizon=1, window=1, quantile=0.90, side="reversion",
                  train_bars=20000):
    """Señal de la última vela cerrada: ¿es extrema, hacia dónde y cuándo vence?

    El umbral se calcula con las velas anteriores a la señal; la vela actual solo
    se compara contra él, nunca participa en su propio umbral.
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n < window + 3:
        return None
    train_bars = min(int(train_bars), max(200, n - window - 2))
    lr = np.log(close[window:] / close[:-window])
    if lr.size < 3:
        return None
    hist = np.abs(lr[-(train_bars + 1):-1])
    if hist.size < 50:
        return None
    thr = float(np.quantile(hist, quantile))
    move = float(lr[-1])
    direction = 0
    if move != 0:
        follow = 1 if move > 0 else -1
        direction = follow if side == "momentum" else -follow
    return {"threshold_pct": round(thr * 100, 3), "move_pct": round(move * 100, 3),
            "extreme": bool(abs(move) >= thr), "direction": int(direction),
            "price": float(close[-1]), "bars_to_expiry": int(horizon),
            "train_bars": int(train_bars), "train_pct": round(hist.size / max(1, lr.size) * 100, 1)}


def fit_windows(n, horizon, train, test, folds):
    """Encoge las ventanas para que quepan en las velas disponibles.

    Así el panel funciona igual con 44.000 velas de 1h que con 20.000 de 4h, y
    lo que se usó queda escrito en el resultado en vez de fallar en silencio.
    """
    train, test, folds = int(train), int(test), int(folds)
    if n >= train + test * folds + horizon + 100:
        return train, test
    total = n - horizon - 100
    if total < 500 + 200 * folds:
        raise ValueError(f"hacen falta al menos {500 + 200 * folds + horizon + 100} velas "
                         f"y hay {n}")
    ratio = train / float(train + test)
    new_train = max(500, int(total * ratio))
    new_test = max(200, (total - new_train) // folds)
    while new_train + new_test * folds > total and new_test > 200:
        new_test -= 1
    return new_train, new_test


def _close_series(store, symbol, timeframe, limit=None):
    df = store.candles(symbol, timeframe, limit)
    if df is None or not len(df):
        raise ValueError(f"sin velas guardadas de {symbol} {timeframe}")
    return df["close"].to_numpy(dtype=float)


class BinaryLab:
    """Ejecuta el laboratorio en un hilo y publica su estado para el panel.

    No toca campeones, posiciones ni el motor: solo lee velas (las que el bot va
    guardando) y calcula. Con `refresh` pide además las últimas al exchange.
    """

    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.lock = threading.Lock()
        self._thread = None
        self._feeds = {}
        self.state = {"status": "idle", "started": 0.0, "finished": 0.0, "error": None,
                      "done": 0, "total": 0, "current": None, "rows": [],
                      "summary": {}, "config": {}}

    def snapshot(self):
        with self.lock:
            st = dict(self.state)
            st["rows"] = [dict(r) for r in self.state["rows"]]
            st["summary"] = dict(self.state["summary"] or {})
        st.update(timeframes=list(TIMEFRAMES), symbols=list(self.cfg.symbols),
                  defaults={"timeframe": self.cfg.timeframe, "horizon": 1, "window": 1,
                            "quantile": 0.90, "payout": 0.85, "side": "reversion",
                            "train": 20000, "test": 4000, "folds": 5})
        return st

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    # ---------------------------------------------------------------- arranque
    def _params(self, payload):
        from .sweep import _symbols                # mismo validador de símbolos
        p = payload or {}
        syms = _symbols(p.get("symbols") or self.cfg.symbols)
        tf = (p.get("timeframe") or self.cfg.timeframe).strip().lower()
        if tf not in TIMEFRAMES:
            raise ValueError("temporalidad no soportada: %s (usa %s)"
                             % (tf, ", ".join(TIMEFRAMES)))
        horizon = int(p.get("horizon") or 1)
        window = int(p.get("window") or 1)
        quantile = float(p.get("quantile") or 0.90)
        payout = float(p.get("payout") or 0.85)
        side = (p.get("side") or "reversion").strip().lower()
        train = int(p.get("train") or 20000)
        test = int(p.get("test") or 4000)
        folds = int(p.get("folds") or 5)
        if not 1 <= horizon <= 100:
            raise ValueError("horizonte entre 1 y 100 velas")
        if not 1 <= window <= 50:
            raise ValueError("ventana de entrada entre 1 y 50 velas")
        if not 0.50 <= quantile < 1.0:
            raise ValueError("cuantil entre 0,50 y 0,99")
        if not 0.50 <= payout <= 0.99:
            raise ValueError("payout entre 0,50 y 0,99 (0,85 = paga 85 %)")
        if side not in ("reversion", "momentum"):
            raise ValueError("lado inválido: reversion o momentum")
        if train < 500 or test < 200 or not 1 <= folds <= 20:
            raise ValueError("ventanas inválidas: train >= 500, test >= 200, folds 1-20")
        return {"symbols": syms, "timeframe": tf, "horizon": horizon, "window": window,
                "quantile": quantile, "payout": payout, "side": side, "train": train,
                "test": test, "folds": folds, "refresh": bool(p.get("refresh"))}

    def start(self, payload=None):
        p = self._params(payload)
        with self.lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("el laboratorio binario ya está calculando; espera")
            self.state = {"status": "running", "started": time.time(), "finished": 0.0,
                          "error": None, "done": 0, "total": len(p["symbols"]),
                          "current": None, "rows": [], "summary": {},
                          "config": {k: v for k, v in p.items() if k != "symbols"},
                          "symbols_used": p["symbols"]}
            self._thread = threading.Thread(target=self._run, args=(p,), name="binary-lab",
                                            daemon=True)
            self._thread.start()
        return {"ok": True, "symbols": p["symbols"], "cells": len(p["symbols"])}

    def run(self, payload=None):
        """Igual que `start`, pero bloqueando (pruebas y CLI)."""
        p = self._params(payload)
        self._set(status="running", started=time.time(), finished=0.0, error=None,
                  done=0, total=len(p["symbols"]), current=None, rows=[], summary={},
                  config={k: v for k, v in p.items() if k != "symbols"},
                  symbols_used=p["symbols"])
        self._run(p)
        return self.snapshot()

    # ------------------------------------------------------------------ trabajo
    def _feed(self, tf, train, test, folds):
        """Feed por temporalidad para traer las últimas velas (solo si se pide)."""
        feed = self._feeds.get(tf)
        if feed is None:
            c = copy.copy(self.cfg)
            c.timeframe = tf
            c.history_bars = max(self.cfg.history_bars, (train + test * folds + 500))
            if c.data_source != "exchange":
                return None
            feed = make_feed(c, self.store)
            self._feeds[tf] = feed
        return feed

    def _series(self, sym, p):
        tf = p["timeframe"]
        if p["refresh"]:
            feed = self._feed(tf, p["train"], p["test"], p["folds"])
            if feed is not None:
                feed.candles(sym)     # guarda en la caché lo que falte
        df = self.store.candles(sym, tf)
        if df is None or not len(df):
            raise ValueError(f"sin velas guardadas de {sym} {tf}")
        return df["close"].to_numpy(dtype=float), int(df.index[-1].timestamp() * 1000)

    def _run(self, p):
        rows = []
        try:
            for i, sym in enumerate(p["symbols"]):
                self._set(current={"symbol": sym, "stage": "calculando",
                                   "done": i, "total": len(p["symbols"])})
                try:
                    close, last_ts = self._series(sym, p)
                    train, test = fit_windows(len(close), p["horizon"], p["train"],
                                              p["test"], p["folds"])
                    r = walk_forward(close, horizon=p["horizon"], window=p["window"],
                                     quantile=p["quantile"], payout=p["payout"],
                                     train_bars=train, test_bars=test, folds=p["folds"],
                                     side=p["side"])
                    r.update(symbol=sym, last_bar_ts=last_ts, train_bars=train,
                             test_bars=test, bars=len(close),
                             signal=latest_signal(close, horizon=p["horizon"],
                                                  window=p["window"],
                                                  quantile=p["quantile"],
                                                  side=p["side"], train_bars=train))
                except Exception as exc:
                    row = {"symbol": sym, "error": str(exc) or exc.__class__.__name__}
                    r = row
                rows.append(r)
                self._set(rows=list(rows), done=i + 1, summary=summarize(rows, p["payout"]))
            self._set(status="done", finished=time.time(), current=None,
                      summary=summarize(rows, p["payout"]))
            log.info("laboratorio binario: %s", summarize(rows, p["payout"])["verdict"])
        except Exception as exc:
            log.exception("laboratorio binario interrumpido")
            self._set(status="error", error=str(exc) or exc.__class__.__name__,
                      finished=time.time(), current=None, summary=summarize(rows, p["payout"]))


def main(argv=None):
    from .config import Config
    from .store import Store

    ap = argparse.ArgumentParser(description="Laboratorio binario (backtest, no operar)")
    ap.add_argument("--symbols", default="", help="BASE/QUOTE separados por coma")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--horizon", type=int, default=1, help="velas hasta el vencimiento")
    ap.add_argument("--window", type=int, default=1, help="velas del movimiento de entrada")
    ap.add_argument("--quantile", type=float, default=0.90,
                    help="cuantil de |retorno| que define 'movimiento extremo'")
    ap.add_argument("--payout", type=float, default=0.85, help="0.85 = pagar 85 %%")
    ap.add_argument("--side", default="reversion", choices=("reversion", "momentum"))
    ap.add_argument("--train", type=int, default=20000)
    ap.add_argument("--test", type=int, default=4000)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--sweep", action="store_true",
                    help="recorre cuantiles y horizontes para ver si algo se repite")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    cfg = Config()
    store = Store(cfg.data_dir)
    symbols = [s.strip().upper() for s in (a.symbols or ",".join(cfg.symbols)).split(",") if s.strip()]

    results = []
    for sym in symbols:
        close = _close_series(store, sym, a.timeframe)
        if a.sweep:
            for h in HORIZONS:
                for q in QUANTILES:
                    try:
                        r = walk_forward(close, horizon=h, window=a.window, quantile=q,
                                         payout=a.payout, train_bars=a.train,
                                         test_bars=a.test, folds=a.folds, side=a.side)
                    except ValueError as exc:
                        print(f"{sym} h={h} q={q}: {exc}")
                        continue
                    r["symbol"] = sym
                    results.append(r)
                    print(f"{sym:<10} h={h:<3} q={q:<5} acierto {r['hit_pct']:6.2f}% "
                          f"[{r['ci_low_pct']:5.2f}, {r['ci_high_pct']:5.2f}] "
                          f"n={r['bets']:<5} umbral {r['breakeven_pct']:5.2f}% -> {r['verdict']}")
        else:
            r = walk_forward(close, horizon=a.horizon, window=a.window, quantile=a.quantile,
                             payout=a.payout, train_bars=a.train, test_bars=a.test,
                             folds=a.folds, side=a.side)
            r["symbol"] = sym
            results.append(r)
            print(f"\n{sym} {a.timeframe} · h={a.horizon} · q={a.quantile} · {a.side}")
            print(f"  apuestas {r['bets']} · acierto {r['hit_pct']:.2f}% "
                  f"[{r['ci_low_pct']:.2f}, {r['ci_high_pct']:.2f}] · "
                  f"umbral {r['breakeven_pct']:.2f}% · EV {r['ev_per_bet']:+.4f}/apuesta")
            print(f"  neto {r['net_stakes']:+.2f} stakes · peor racha {r['worst_loss_streak']} · "
                  f"drawdown {r['max_drawdown_stakes']:.2f} stakes · "
                  f"apuestas para demostrarlo: {r['bets_to_prove']}")
            print("  EV por payout: " + " · ".join(f"{k} {v:+.3f}" for k, v in r["ev_by_payout"].items()))
            print("  folds: " + " · ".join(f"f{f['fold']} {f['hit_pct']:.1f}% (n={f['bets']})"
                                           for f in r["folds"]))

    if a.json:
        print(json.dumps(results, indent=2, default=str))
    elif a.sweep or len(results) > 1:
        print("\n" + json.dumps(summarize(results, a.payout), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
