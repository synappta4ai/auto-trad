"""Motor: ciclo principal, gestión de posiciones, topes de riesgo y programación del aprendizaje."""
import datetime as dt
import logging
import math
import threading
import time

import pandas as pd

from . import optimizer, regime, sizing, strategies
from .broker import NoBalance, client_id
from .reconcile import run_reconcile
from .strategies import atr

log = logging.getLogger("engine")


class Engine:
    def __init__(self, cfg, store, feed, broker):
        self.cfg, self.store, self.feed, self.broker = cfg, store, feed, broker
        self.lock = threading.RLock()
        self.running = False          # Flag para controlar start/stop
        self.stop_ev = threading.Event()
        self.wake_ev = threading.Event()   # despierta el bucle (retrain manual, etc.)
        self._thread = None
        self.tick_count = 0
        self.last_tick_at = 0.0
        self.train_state = {"status": "idle", "stage": "inactivo", "done": 0, "total": 0,
                            "fold": 0, "folds": self.cfg.wf_folds, "symbol": None}
        self._snap_cache = None
        self.retrain_flag = False
        self.retrain_reason = None
        self.train_mode = cfg.train_mode   # scheduled | continuous (se puede cambiar en caliente)
        self.last_busy_at = 0.0            # fin del último entrenamiento (para el cooldown)
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
        self._snap_cache = {"mode": cfg.mode, "running": False, "busy": False,
                            "equity": S["cash"], "cash": S["cash"], "peak": S["peak"], "drawdown_pct": 0.0,
                            "halted": False, "halt_reason": "", "pause_reason": "", "trades_today": 0,
                            "positions": {}, "champions": {}, "risk_mult": {}, "vol_ratio": {},
                            "last_train": S.get("last_train"), "next_train": None, "prices": {},
                            "tick": 0, "last_tick": 0, "poll_seconds": cfg.poll_seconds,
                            "learning_enabled": cfg.learning_enabled,
                            "retrain_interval_hours": cfg.retrain_interval_hours,
                            "train_mode": self.train_mode, "trainings": int(S.get("trainings", 0)),
                            "bars_since_train": 0, "train_min_new_bars": cfg.train_min_new_bars,
                            "train_cooldown_seconds": cfg.train_cooldown_seconds,
                            "last_busy_at": 0.0,
                            "training": dict(self.train_state)}

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
    def start(self):
        """Arranca el bucle continuo en un hilo daemon. Idempotente.

        Limpia el kill switch para que el botón Iniciar funcione aunque el
        motor esté haltado o pausado por pérdidas.
        """
        with self.lock:
            if self.running and self._thread and self._thread.is_alive():
                return False
            eq = self._equity()
            self.S.update(halted=False, halt_reason="", pause_reason="", peak=eq)
            self.retrain_flag = self.cfg.retrain_on_start
            self.retrain_reason = "arranque" if self.cfg.retrain_on_start else None
            self.stop_ev.clear()
            self.wake_ev.clear()
            self.running = True
            self._event("start", {"mode": self.cfg.mode, "symbols": self.cfg.symbols})
            self.store.put("state", self.S)
            if self.cfg.reconcile_on_start:
                try:
                    rep = run_reconcile(self)
                    if rep.get("status") != "skipped":
                        self._event("reconcile", rep)
                    if rep.get("status") == "needs_attention":
                        # sigue el bucle (los stops por software gestionan lo abierto),
                        # pero no se abren posiciones nuevas hasta revisarlo
                        self.S.update(halted=True, halt_reason="reconciliacion_incompleta")
                        log.error("reconciliación incompleta: no se abren posiciones nuevas")
                except Exception as ex:
                    # sin saber qué hay en el exchange no se opera: fallar en cerrado
                    log.exception("reconciliación fallida: no se arranca")
                    self.S.update(halted=True, halt_reason="reconciliacion_fallida")
                    self._event("halt", {"reason": "reconciliacion_fallida", "error": str(ex)})
                    self.store.put("state", self.S)
                    self.running = False
                    return False
        self._thread = threading.Thread(target=self.run, name="engine", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Detiene el bucle continuo. Idempotente."""
        with self.lock:
            was = self.running
            self.running = False
            self.stop_ev.set()
            self.wake_ev.set()
            if was:
                self._event("stop", {"equity": round(self._equity(), 4)})
            self.store.put("state", self.S)
        return was

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
                delay = self.cfg.poll_seconds
                if self.S.get("halted", False) or self.S.get("pause_reason", ""):
                    delay = max(delay, 5)
                self.wake_ev.wait(delay)
                self.wake_ev.clear()
        finally:
            self.running = False

    def tick(self):
        with self.lock:
            cfg, S = self.cfg, self.S
            now = time.time()
            self.tick_count += 1
            self.last_tick_at = now
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
        eq = self._equity()
        mult = S["risk_mult"].get(sym, 1.0)
        qty = sizing.entry_qty(cfg, eq, S["cash"], self._exposure(), price, a, mult, side)
        if qty <= 0:
            log.info("entrada %s omitida: tope de exposición o nocional < MIN_NOTIONAL %.2f", sym, cfg.min_notional)
            return
        fill, fee, q = self.broker.execute(sym, side, qty, price, cid=self._cid(sym, "open"))
        S["cash"] += (-q * fill - fee) if side > 0 else (q * fill - fee)
        stop, tp = sizing.stop_and_tp(cfg, fill, side, a)
        champ = S["champions"].get(sym) or {}
        S["positions"][sym] = {
            "side": side, "qty": q, "entry": fill, "fee": fee, "stop": stop, "tp": tp,
            "opened_bar": self.bar_ts[sym], "opened_at": time.time(),
            "strategy": champ.get("strategy"), "params": champ.get("params"), "risk_mult": mult,
            "stop_order_id": None}
        S["trades_today"] += 1
        self._event("open", {"symbol": sym, "side": side, "qty": q, "price": fill, "stop": S["positions"][sym]["stop"],
                             "tp": S["positions"][sym]["tp"], "risk_mult": mult, "strategy": champ.get("strategy")})
        self._protect(sym)

    def _cid(self, sym, kind):
        return client_id(self.cfg, sym, kind)

    def _protect(self, sym):
        """Stop nativo en el exchange. Si no se puede colocar, se cierra la
        posición: es preferible no operar a operar sin protección."""
        pos = self.S["positions"].get(sym)
        if not pos or not (self.cfg.native_stops and self.cfg.mode == "live"):
            return None
        try:
            oid = self.broker.protect(sym, pos["side"], pos["qty"], pos["stop"], self._cid(sym, "stop"))
        except Exception as ex:
            log.error("stop nativo falló en %s: %s", sym, ex)
            self._event("protect_failed", {"symbol": sym, "error": str(ex),
                                           "msg": "se cierra la posición: no se opera sin protección"})
            self._close(sym, self.prices.get(sym, pos["entry"]), "protect_failed")
            return None
        if not oid:
            log.warning("el exchange no devolvió id para el stop de %s", sym)
            return None
        pos["stop_order_id"] = oid
        self._event("protect", {"symbol": sym, "order_id": oid, "stop": pos["stop"]})
        return oid

    def _close(self, sym, ref, reason):
        cfg, S = self.cfg, self.S
        pos = S["positions"][sym]
        if pos.get("stop_order_id"):
            # se cancela antes de vender para que el stop no compita con el cierre
            try:
                self.broker.cancel(sym, pos["stop_order_id"])
            except Exception as ex:
                log.warning("no se pudo cancelar el stop nativo de %s: %s", sym, ex)
            pos["stop_order_id"] = None
        try:
            fill, fee, q = self.broker.execute(sym, -pos["side"], pos["qty"], ref,
                                               cid=self._cid(sym, "close"), reduce_only=True)
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
        if sym in S["positions"]:
            self._protect(sym)   # cierre parcial: el resto sigue necesitando stop
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
        new_bars = self._bars_since_train(dfs)
        S["bars_since_train"] = new_bars
        if self.retrain_flag:
            reason = self.retrain_reason or "manual"
        elif lt is None:
            reason = "inicial"
        elif self.train_mode == "continuous":
            # Aprendizaje continuo: si hay datos nuevos (o no se exige), se
            # reentrena otra vez en cuanto pasa el cooldown.
            ready = now - self.last_busy_at >= cfg.train_cooldown_seconds
            if ready and (cfg.train_min_new_bars <= 0 or new_bars >= cfg.train_min_new_bars):
                reason = "continuo"
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
        self.train_state = {"status": "running", "stage": "preparando", "done": 0, "total": 0,
                            "fold": 0, "folds": cfg.wf_folds, "symbol": None, "reason": reason,
                            "started": now, "symbols": list(dfs), "symbol_index": 0}
        try:
            for idx, (sym, df) in enumerate(dfs.items()):
                t0 = time.time()
                old = S["champions"].get(sym)

                def progress(ev, _sym=sym, _idx=idx):
                    st = self.train_state
                    st.update(ev)
                    st["symbol"], st["symbol_index"] = _sym, _idx
                    st["updated"] = time.time()

                progress({"stage": "preparando", "done": 0, "total": 0, "fold": 0})
                new, rep = optimizer.train_symbol(df, cfg, old, progress=progress)
                rep.update(reason=reason, at=now, seconds=round(time.time() - t0, 2))
                S["learning"][sym] = rep
                self.store.add_learning_report(sym, rep)
                S.setdefault("train_bar_ts", {})[sym] = int(df.index[-1].timestamp())
                same = (old or {}).get("strategy") == (new or {}).get("strategy") and (old or {}).get("params") == (new or {}).get("params")
                S["champions"][sym] = new
                if not same:
                    S["prev_desired"][sym] = None
                self._event("train", {"symbol": sym, "reason": reason, "decision": rep.get("decision", rep["status"]),
                                      "candidates": rep.get("candidates"), "seconds": rep.get("seconds"),
                                      "champion": None if new is None else {"strategy": new["strategy"], "params": new["params"]},
                                      "gate_fails": rep.get("gate_fails")})
            S["last_train"] = now
            S["trainings"] = int(S.get("trainings", 0)) + 1
            self.last_busy_at = time.time()
            self.train_state = {"status": "idle", "stage": "completado", "done": 1, "total": 1,
                                "fold": cfg.wf_folds, "folds": cfg.wf_folds,
                                "symbol": None, "reason": reason,
                                "started": now, "finished": time.time(),
                                "seconds": round(time.time() - now, 2)}
        except Exception:
            self.train_state = {"status": "error", "stage": "error", "done": 0, "total": 0,
                                "fold": 0, "folds": cfg.wf_folds, "symbol": None,
                                "reason": reason, "started": now, "finished": time.time()}
            raise

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

    def flatten(self, reason="manual"):
        """Cierra todo lo abierto. Lo usa el kill switch, la API y el watchdog."""
        with self.lock:
            for sym in list(self.S["positions"]):
                try:
                    price = self.prices.get(sym) or self.feed.last_price(sym)
                    self._close(sym, price, reason)
                except Exception:
                    log.exception("no se pudo cerrar %s (%s)", sym, reason)
            self.store.put("state", self.S)

    def close_all(self):
        self.flatten("manual")

    def request_retrain(self):
        self.retrain_flag = True
        self.retrain_reason = "manual"
        self.wake_ev.set()

    def set_train_mode(self, mode):
        """Cambia entre aprendizaje programado y continuo, en caliente."""
        mode = (mode or "").strip().lower()
        if mode not in ("scheduled", "continuous"):
            raise ValueError("modo inválido: usa 'scheduled' o 'continuous'")
        with self.lock:
            self.train_mode = mode
            self.last_busy_at = 0.0   # que el próximo ciclo arranque ya
        self._event("train_mode", {"mode": mode})
        self.wake_ev.set()
        return mode

    def _bars_since_train(self, dfs):
        """Velas nuevas desde el último entrenamiento (el mínimo entre símbolos)."""
        cfg, seen = self.cfg, self.S.get("train_bar_ts") or {}
        out = []
        for sym, df in dfs.items():
            prev = seen.get(sym)
            if prev is None:
                out.append(cfg.history_bars)
                continue
            ts = int(df.index[-1].timestamp())
            out.append(max(0, int(round((ts - prev) / cfg.tf_seconds))))
        return min(out) if out else 0

    def snapshot(self):
        # Durante un entrenamiento el lock está tomado: en vez de bloquear la
        # API devolvemos el último estado conocido + el progreso en vivo.
        if not self.lock.acquire(timeout=0.05):
            snap = dict(self._snap_cache or {})
            snap.update(busy=True, running=self.running, training=dict(self.train_state),
                        tick=self.tick_count, last_tick=self.last_tick_at,
                        train_mode=self.train_mode)
            return snap
        try:
            S, eq = self.S, self._equity()
            syms = self.cfg.symbols
            pos = {}
            for s, p in S["positions"].items():
                if s not in syms:
                    continue
                px = self.prices.get(s, p["entry"])
                pos[s] = dict(p, price=px, unrealized=round(p["side"] * (px - p["entry"]) * p["qty"], 4))
            champions = {s: c for s, c in S["champions"].items() if s in syms}
            risk_mult = {s: m for s, m in S["risk_mult"].items() if s in syms}
            vol_ratio = {s: round(r, 3) for s, r in S["vol_ratio"].items() if s in syms}
            lt = S["last_train"]
            next_train = None
            if self.cfg.learning_enabled and lt and self.train_mode == "scheduled":
                next_train = lt + self.cfg.retrain_interval_hours * 3600
            snap = {"mode": self.cfg.mode, "running": self.running, "busy": False,
                    "equity": round(eq, 4), "cash": round(S["cash"], 4), "peak": round(S["peak"], 4),
                    "drawdown_pct": round(S["dd"], 3), "halted": S["halted"], "halt_reason": S["halt_reason"],
                    "pause_reason": S["pause_reason"], "trades_today": S["trades_today"], "positions": pos,
                    "champions": champions, "risk_mult": risk_mult, "vol_ratio": vol_ratio,
                    "last_train": lt, "next_train": next_train, "prices": self.prices,
                    "tick": self.tick_count, "last_tick": self.last_tick_at,
                    "poll_seconds": self.cfg.poll_seconds, "training": dict(self.train_state),
                    "learning_enabled": self.cfg.learning_enabled,
                    "retrain_interval_hours": self.cfg.retrain_interval_hours,
                    "train_mode": self.train_mode, "trainings": int(S.get("trainings", 0)),
                    "bars_since_train": int(S.get("bars_since_train", 0)),
                    "train_min_new_bars": self.cfg.train_min_new_bars,
                    "train_cooldown_seconds": self.cfg.train_cooldown_seconds,
                    "last_busy_at": self.last_busy_at}
            self._snap_cache = snap
            return snap
        finally:
            self.lock.release()
