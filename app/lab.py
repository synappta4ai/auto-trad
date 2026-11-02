"""Laboratorio: entrenar y probar con las velas ya guardadas en local.

El ciclo del motor entrena cuando toca y opera; esto es el banco de pruebas:
carga la caché de velas (`candles` en SQLite), lanza un walk-forward completo y
devuelve el reporte. No toca campeones, posiciones ni el estado del motor: un
experimento no debe cambiar lo que el bot está haciendo.

Trabaja en un hilo propio y publica su estado, para que la página de
configuración pueda ir mostrando el avance sin bloquear la petición HTTP.
"""
import copy
import logging
import threading
import time

from . import optimizer
from .data import make_feed

log = logging.getLogger("lab")


def needed(cfg):
    """Velas mínimas para que el walk-forward tenga las ventanas completas."""
    return cfg.train_window_bars + cfg.wf_folds * cfg.test_window_bars + 200


class Lab:
    def __init__(self, cfg, store, engine=None):
        self.cfg, self.store, self.engine = cfg, store, engine
        self.lock = threading.Lock()
        self._thread = None
        self.state = {"status": "idle", "task": None, "symbol": None, "started": 0.0,
                      "finished": 0.0, "error": None, "report": None, "progress": None}

    # ------------------------------------------------------------------ estado
    def snapshot(self):
        with self.lock:
            st = dict(self.state)
        st.update(needed=needed(self.cfg), timeframe=self.cfg.timeframe,
                  symbols=list(self.cfg.symbols))
        return st

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _fail(self, exc):
        log.exception("laboratorio: %s", exc)
        msg = str(exc) or exc.__class__.__name__
        self._set(status="error", error=msg, finished=time.time())
        return {"ok": False, "error": msg}

    def _start(self, task, symbol, fn):
        with self.lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("el laboratorio ya está trabajando; espera a que termine")
            self.state = {"status": "running", "task": task, "symbol": symbol, "started": time.time(),
                          "finished": 0.0, "error": None, "report": None, "progress": None}
            self._thread = threading.Thread(target=fn, name="lab-" + task, daemon=True)
            self._thread.start()
        return {"ok": True, "msg": task, "symbol": symbol}

    # ------------------------------------------------------------------ datos
    def data_info(self):
        """Qué velas hay guardadas y si alcanzan para entrenar."""
        cfg = self.cfg
        need = needed(cfg)
        rows = self.store.candle_stats()
        have = {(r["symbol"], r["timeframe"]) for r in rows}
        for sym in cfg.symbols:
            if (sym, cfg.timeframe) not in have:
                rows.append({"symbol": sym, "timeframe": cfg.timeframe, "bars": 0,
                             "first": None, "last": None})
        for r in rows:
            r["ready"] = r["bars"] >= need
        rows.sort(key=lambda r: (r["symbol"], r["timeframe"]))
        return {"rows": rows, "needed": need, "timeframe": cfg.timeframe,
                "history_bars": cfg.history_bars, "source": cfg.data_source,
                "note": (f"walk-forward: {cfg.train_window_bars} + {cfg.wf_folds}×"
                         f"{cfg.test_window_bars} + 200 = {need} velas")}

    def fetch(self, symbol=None, bars=None):
        """Descarga histórico del exchange y lo guarda en la caché local."""
        if self.cfg.data_source != "exchange":
            raise ValueError("DATA_SOURCE=synthetic: no hay histórico que descargar "
                             "(cambia a exchange en la configuración y reinicia)")
        syms = [symbol] if symbol else list(self.cfg.symbols)
        want = max(int(bars or self.cfg.history_bars), needed(self.cfg))
        cfg = copy.copy(self.cfg)
        cfg.history_bars = want

        def run():
            try:
                feed = make_feed(cfg, self.store)
                out = {}
                for sym in syms:
                    self._set(progress={"stage": "descargando", "symbol": sym, "done": 0, "total": 0})
                    df = feed.candles(sym)
                    out[sym] = self.store.candle_stat(sym, cfg.timeframe)
                    self._set(progress={"stage": "descargando", "symbol": sym,
                                        "done": int(len(df) if df is not None else 0), "total": want})
                self._set(status="done", finished=time.time(), report={"bars": out})
            except Exception as exc:      # red, mercado inexistente, exchange caído...
                self._fail(exc)

        return self._start("descargar", symbol, run)

    # ------------------------------------------------------------------ entrenar
    def train(self, symbol=None, bars=None):
        """Walk-forward sobre la caché, sin red ni cambios en el bot."""
        syms = [symbol] if symbol else list(self.cfg.symbols)
        limit = int(bars or self.cfg.history_bars)
        need = needed(self.cfg)

        def run():
            try:
                out = {}
                for sym in syms:
                    df = self.store.candles(sym, self.cfg.timeframe, limit)
                    n = 0 if df is None else len(df)
                    if n < need:
                        out[sym] = {"status": "datos_insuficientes", "bars": n, "needed": need}
                        continue
                    t0 = time.time()

                    def progress(ev, _sym=sym):
                        self._set(progress=dict(ev, symbol=_sym))

                    _, rep = optimizer.train_symbol(df, self.cfg, None, progress=progress)
                    rep.update(reason="laboratorio", at=time.time(), source="cache", symbol=sym,
                               seconds=round(time.time() - t0, 2))
                    self.store.add_learning_report(sym, rep)
                    out[sym] = rep
                    log.info("laboratorio %s: %s (%s velas, %ss)", sym,
                             rep.get("decision", rep.get("status")), n, rep.get("seconds"))
                self._set(status="done", finished=time.time(), report=out)
            except Exception as exc:
                self._fail(exc)

        return self._start("entrenar", symbol, run)
