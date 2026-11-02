"""Motor: ciclo principal, gestión de posiciones, topes de riesgo y programación del aprendizaje."""
import datetime as dt
import logging
import math
import threading
import time

import pandas as pd

from . import optimizer, regime, strategies
from .broker import NoBalance
from .strategies import atr

log = logging.getLogger("engine")


class Engine:
    def __init__(self, cfg, store, feed, broker):
        self.cfg, self.store, self.feed, self.broker = cfg, store, feed, broker
        self.lock = threading.RLock()
        self.running = False          # Flag para controlar start/stop
        self.stop_ev = threading.Event()
        self.retrain_flag = False
        self.prices, self.bar_ts = {}, {}
        self.last_equity_log = 0.0
        S = store.get("state")
        if S is None:
            if cfg.mode == "live":
                free = broker.quote_free()
                if free is not None and free < cfg.initial_capital:
                    raise RuntimeError(f"Saldo libre {free} < INITIAL_CAPITAL {cfg.initial_capital}")
            S = {"cash": cfg.initial_capital, "peak": cfg.initial_capital, "dd": 0.0, "day": "", "day_start": cfg.initial_capital,
                 "week": "", "week_start": cfg.initial_capital, "halted": False, "halt_reason": "", "pause_reason": "",
                 "trades_today": 0, "last_train": None}
        for k in ("positions", "champions", "learning", "prev_desired", "cooldown", "vol_ratio", "risk_mult"):
            S.setdefault(k, {})
        self.S = S

    # ------------------------------------------------------------------ utilidades
    def _equity(self):
        S = self.S
        eq = S["cash"]
        for sym, p in S["positions"].items():
            eq += p["side"] * p["qty"] * self.prices.get(sym, p["entry"])
        return eq

    def _exposure(self):
        return sum(p["qty"] * self.prices.get(s, p["entry"]) for s, p in self.S["positions"].items())

    def _event(self, typ, data):
        log.info("EVENT %s %s", typ, data)
        self.store.add_event(typ, data)

    # ------------------------------------------------------------------ ciclo
    def run(self):
        log.info("Motor iniciado | modo=%s fuente=%s símbolos=%s tf=%s", self.cfg.mode, self.cfg.data_source, self.cfg.symbols, self.cfg.timeframe)
        self.running = True
        try:
            while self.running and not self.stop_ev.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("error en tick (se reintenta)")
                # Si no estamos halted y no hay pause, esperar pol seconds
                if not self.S.get("halted", False) and not self.S.get("pause_reason", ""):
                    self.stop_ev.wait(self.cfg.poll_seconds)
                else:
                    # Si está halted o pausado, esperar más largo o hasta que cambie
                    self.stop_ev.wait(max(self.cfg.poll_seconds, 5))
        finally:
            self.running = False

    def tick(self):
        with self.lock:
            cfg, S = self.cfg, self.S
            now = time.time()
            self.feed.advance()
            dfs, new_bar = {}, {}
            for sym in cfg.symbols:
                df = self.feed.candles(sym)
                if df is None or len(df) < 60:
                    continue
                dfs[sym] = df
                self.prices[sym] = self.feed.last_price(sym)
                ts = int(df.index[-1].timestamp() * 1000)
                self.bar_ts[sym] = ts
                new_bar[sym] = ts != S.get("last_bar", {}).get(sym)
                S["vol_ratio"][sym] = regime.vol_ratio(df["close"].to_numpy(), cfg.drift_short_bars, cfg.drift_long_bars)
                self._manage_exits(sym, df)
            self._update_risk(self._equity(), now)
            self._maybe_train(dfs, now)
            for sym, df in dfs.items():
                if new_bar[sym]:
                    self._on_new_bar(sym, df)
                    S.setdefault("last_bar", {})[sym] = self.bar_ts[sym]
            eq = self._equity()
            if now - self.last_equity_log >= cfg.equity_log_seconds:
                self.store.add_equity(eq, S["cash"], S["dd"])
                self.last_equity_log = now
            self.store.put("state", S)

    # ------------------------------------------------------------------ riesgo
    def _update_risk(self, equity, now):
        cfg, S = self.cfg, self.S
        d = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        day, week = d.strftime("%Y-%m-%d"), d.strftime("%G-W%V")
        if S["day"] != day:
            S.update(day=day, day_start=equity, trades_today=0)
        if S["week"] != week:
            S.update(week=week, week_start=equity)
        S["peak"] = max(S["peak"], equity)
        S["dd"] = (S["peak"] - equity) / S["peak"] * 100 if S["peak"] > 0 else 0.0
        dl = (S["day_start"] - equity) / S["day_start"] * 100 if S["day_start"] > 0 else 0
        wl = (S["week_start"] - equity) / S["week_start"] * 100 if S["week_start"] > 0 else 0
        S["pause_reason"] = "perdida_diaria" if dl >= cfg.max_daily_loss_pct else ("perdida_semanal" if wl >= cfg.max_weekly_loss_pct else "")
        if not S["halted"] and S["dd"] >= cfg.max_drawdown_pct:
            self.halt(f"drawdown {S['dd']:.1f}% >= MAX_DRAWDOWN_PCT {cfg.max_drawdown_pct}%")
        for sym, ratio in S["vol_ratio"].items():
            S["risk_mult"][sym] = round(regime.risk_multiplier(cfg, ratio, S["dd"]), 3)

    def _entry_allowed(self, sym):
        cfg, S = self.cfg, self.S
        if S["halted"]:
            return False, "kill_switch"
        if S["pause_reason"]:
            return False, S["pause_reason"]
        if S["trades_today"] >= cfg.max_trades_per_day:
            return False, "max_trades_por_dia"
        if len(S["positions"]) >= cfg.max_open_positions:
            return False, "max_posiciones_abiertas"
        if self.bar_ts.get(sym, 0) < S["cooldown"].get(sym, 0):
            return False, "cooldown"
        return True, ""

    # ------------------------------------------------------------------ operaciones
    def _open(self, sym, side, price, df):
        cfg, S = self.cfg, self.S
        ok, why = self._entry_allowed(sym)
        if not ok:
            log.info("entrada %s bloqueada: %s", sym, why)
            return
        a = float(atr(df, cfg.atr_period).iloc[-1])
        if not math.isfinite(a) or a <= 0:
            return
        stop_dist = a * cfg.stop_atr_mult
        eq = self._equity()
        mult = S["risk_mult"].get(sym, 1.0)
        qty = eq * cfg.risk_per_trade_pct / 100 * mult / stop_dist
        max_notional = min(eq * cfg.max_position_pct / 100, eq * cfg.max_total_exposure_pct / 100 - self._exposure())
        if side > 0:
            max_notional = min(max_notional, S["cash"] * 0.995)
        qty = min(qty, max(0.0, max_notional) / price)
        if qty * price < cfg.min_notional:
            log.info("entrada %s omitida: nocional %.2f < MIN_NOTIONAL %.2f", sym, qty * price, cfg.min_notional)
            return
        fill, fee, q = self.broker.execute(sym, side, qty, price)
        S["cash"] += (-q * fill - fee) if side > 0 else (q * fill - fee)
        champ = S["champions"].get(sym) or {}
        S["positions"][sym] = {
            "side": side, "qty": q, "entry": fill, "fee": fee, "stop": fill - side * stop_dist,
            "tp": (fill + side * stop_dist * cfg.take_profit_rr) if cfg.take_profit_rr > 0 else None,
            "opened_bar": self.bar_ts[sym], "opened_at": time.time(),
            "strategy": champ.get("strategy"), "params": champ.get("params"), "risk_mult": mult}
        S["trades_today"] += 1
        self._event("open", {"symbol": sym, "side": side, "qty": q, "price": fill, "stop": S["positions"][sym]["stop"],
                             "tp": S["positions"][sym]["tp"], "risk_mult": mult, "strategy": champ.get("strategy")})

    def _close(self, sym, ref, reason):
        cfg, S = self.cfg, self.S
        pos = S["positions"][sym]
        try:
            fill, fee, q = self.broker.execute(sym, -pos["side"], pos["qty"], ref)
        except NoBalance as ex:
            # el exchange no tiene saldo para cerrar: se retira del estado y se avisa para conciliar a mano
            del S["positions"][sym]
            self._event("reconcile_needed", {"symbol": sym, "error": str(ex), "msg": "revisa el saldo en el exchange; la posición se retiró del estado"})
            return
        S["cash"] += (q * fill - fee) if pos["side"] > 0 else (-q * fill - fee)
        frac = min(1.0, q / pos["qty"]) if pos["qty"] > 0 else 1.0
        entry_fee = pos["fee"] * frac
        pnl = pos["side"] * (fill - pos["entry"]) * q - fee - entry_fee
        if frac < 0.99:  # cierre parcial: la posición sigue abierta con el resto
            pos["qty"] -= q
            pos["fee"] -= entry_fee
            self._event("partial_close", {"symbol": sym, "closed_qty": q, "remaining_qty": pos["qty"]})
        else:
            del S["positions"][sym]
        if pnl < 0:
            S["cooldown"][sym] = self.bar_ts.get(sym, 0) + cfg.cooldown_bars * cfg.tf_seconds * 1000
        self.store.add_trade({"symbol": sym, "side": pos["side"], "qty": q, "entry": pos["entry"], "exit": fill, "pnl": pnl,
                              "fees": fee + entry_fee, "reason": reason, "strategy": pos["strategy"], "params": pos["params"]})
        self._event("close", {"symbol": sym, "reason": reason, "pnl": round(pnl, 4), "exit": fill})

    def _manage_exits(self, sym, df):
        pos = self.S["positions"].get(sym)
        if not pos:
            return
        price = self.prices[sym]
        hi = lo = price
        since = df[df.index > pd.to_datetime(pos["opened_bar"], unit="ms", utc=True)]
        if len(since):
            hi, lo = max(hi, float(since["high"].max())), min(lo, float(since["low"].min()))
        side, stop, tp = pos["side"], pos["stop"], pos["tp"]
        reason = ref = None
        if side > 0:
            if lo <= stop:
                reason, ref = "stop", min(stop, price)
            elif tp and hi >= tp:
                reason, ref = "take_profit", tp
        else:
            if hi >= stop:
                reason, ref = "stop", max(stop, price)
            elif tp and lo <= tp:
                reason, ref = "take_profit", tp
        if reason:
            self._close(sym, ref, reason)

    def _on_new_bar(self, sym, df):
        cfg, S = self.cfg, self.S
        champ = S["champions"].get(sym)
        desired = 0
        if champ and not S["halted"]:
            desired = int(strategies.positions(champ["strategy"], df, champ["params"], cfg)[-1])
        price = self.prices[sym]
        pos = S["positions"].get(sym)
        cur = pos["side"] if pos else 0
        if cur != 0 and desired != cur:
            self._close(sym, price, "signal")
        if desired != 0 and sym not in S["positions"]:
            prev = S["prev_desired"].get(sym)
            allow = cfg.enter_on_start if prev is None else (prev != desired or not cfg.reentry_requires_reset)
            if allow:
                self._open(sym, desired, price, df)
        S["prev_desired"][sym] = desired

    # ------------------------------------------------------------------ aprendizaje
    def _maybe_train(self, dfs, now):
        cfg, S = self.cfg, self.S
        if not cfg.learning_enabled:
            name = cfg.fixed_strategy or cfg.strategies[0]
            first = strategies.REGISTRY[name][0](cfg)[0]
            for sym in cfg.symbols:
                S["champions"].setdefault(sym, {"strategy": name, "params": first, "fixed": True})
            return
        lt, reason = S["last_train"], None
        if self.retrain_flag:
            reason = "manual"
        elif lt is None:
            reason = "inicial"
        elif now - lt >= cfg.retrain_interval_hours * 3600:
            reason = "programado"
        elif cfg.retrain_on_drift and now - lt >= cfg.drift_min_hours * 3600:
            pnls = [t["pnl"] for t in self.store.trades(max(1, cfg.drift_loss_streak))]
            for sym in dfs:
                d = regime.drift_detected(cfg, S["vol_ratio"].get(sym, 1.0), pnls)
                if d:
                    reason = "deriva:" + d
                    break
        if not reason:
            return
        self.retrain_flag = False
        for sym, df in dfs.items():
            t0 = time.time()
            old = S["champions"].get(sym)
            new, rep = optimizer.train_symbol(df, cfg, old)
            rep.update(reason=reason, at=now, seconds=round(time.time() - t0, 2))
            S["learning"][sym] = rep
            same = (old or {}).get("strategy") == (new or {}).get("strategy") and (old or {}).get("params") == (new or {}).get("params")
            S["champions"][sym] = new
            if not same:
                S["prev_desired"][sym] = None
            self._event("train", {"symbol": sym, "reason": reason, "decision": rep.get("decision", rep["status"]),
                                  "champion": None if new is None else {"strategy": new["strategy"], "params": new["params"]},
                                  "gate_fails": rep.get("gate_fails")})
        S["last_train"] = now

    # ------------------------------------------------------------------ control (API)
    def halt(self, reason="manual"):
        with self.lock:
            self.S["halted"], self.S["halt_reason"] = True, reason
            self._event("halt", {"reason": reason})
            if self.cfg.kill_switch_close_positions:
                for sym in list(self.S["positions"]):
                    try:
                        self._close(sym, self.prices.get(sym, self.S["positions"][sym]["entry"]), "kill_switch")
                    except Exception:
                        log.exception("no se pudo cerrar %s en kill switch", sym)
            self.store.put("state", self.S)

    def resume(self):
        with self.lock:
            eq = self._equity()
            self.S.update(halted=False, halt_reason="", peak=eq)
            self._event("resume", {"equity": eq})
            self.store.put("state", self.S)

    def close_all(self):
        with self.lock:
            for sym in list(self.S["positions"]):
                self._close(sym, self.prices.get(sym, self.S["positions"][sym]["entry"]), "manual")
            self.store.put("state", self.S)

    def request_retrain(self):
        self.retrain_flag = True

    def snapshot(self):
        with self.lock:
            S, eq = self.S, self._equity()
            pos = {}
            for s, p in S["positions"].items():
                px = self.prices.get(s, p["entry"])
                pos[s] = dict(p, price=px, unrealized=round(p["side"] * (px - p["entry"]) * p["qty"], 4))
            return {"mode": self.cfg.mode, "equity": round(eq, 4), "cash": round(S["cash"], 4), "peak": round(S["peak"], 4),
                    "drawdown_pct": round(S["dd"], 3), "halted": S["halted"], "halt_reason": S["halt_reason"],
                    "pause_reason": S["pause_reason"], "trades_today": S["trades_today"], "positions": pos,
                    "champions": S["champions"], "risk_mult": S["risk_mult"], "vol_ratio": {k: round(v, 3) for k, v in S["vol_ratio"].items()},
                    "last_train": S["last_train"], "prices": self.prices}
