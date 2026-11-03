"""Barridos: buscar ventaja fuera de un solo símbolo y una sola temporalidad.

Un barrido es la misma rejilla de estrategias probada, celda a celda, sobre cada
combinación de símbolo × temporalidad. Cada celda es un walk-forward completo
con su propio train/test, así que su resultado OOS nunca ha visto el tramo que
se mide. Todo intento se guarda en la tabla `experiments`: sin ese registro no
se puede distinguir una ventaja que se repite de un acierto por azar.

Reglas que aplica el barrido (y que no se saltan desde aquí):
 - Se respetan las puertas de la configuración (`MIN_OOS_*`, benchmark opcional).
 - Una celda solo cuenta como «con ventaja» si pasa las puertas y además supera
   a comprar y mantener.
 - El veredicto del conjunto es `repetible` solo si la ventaja aparece en más de
   una celda y en símbolos o temporalidades distintas: una única celda ganadora
   entre veinte es ruido, no un hallazgo.

No toca campeones, posiciones ni el estado del motor: es investigación.
"""
import argparse
import copy
import json
import logging
import threading
import time

from . import optimizer
from .data import make_feed, tf_seconds

log = logging.getLogger("sweep")

# Universo por defecto: los pares USDT con más liquidez. Se puede sustituir
# desde la página o por CLI; no hace falta que coincida con los del bot.
UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
            "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOGE/USDT", "DOT/USDT"]
TIMEFRAMES = ("15m", "1h", "4h", "1d")
MAX_CELLS = 120          # un barrido más grande no se lee, se abandona
MAX_SYMBOLS = 30
MIN_BARS = 300

_PASSING = ("promovido_sin_campeon", "campeon_confirmado", "retador_promovido",
            "campeon_se_mantiene")


def _symbols(raw):
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    out = []
    for x in items:
        s = str(x).strip().upper()
        if not s:
            continue
        head, _, tail = s.partition("/")
        if not head or not tail or not head.isalnum() or not tail.isalnum():
            raise ValueError(f"símbolo inválido: {x} (usa BASE/QUOTE, ej. BTC/USDT)")
        if s not in out:
            out.append(s)
    if not out:
        raise ValueError("hace falta al menos un símbolo")
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"demasiados símbolos: {len(out)} (máximo {MAX_SYMBOLS})")
    if len({s.split("/")[-1] for s in out}) > 1:
        raise ValueError("todos los símbolos deben compartir la misma moneda cotizada (ej. /USDT)")
    return out


def _timeframes(raw):
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    out = []
    for x in items:
        tf = str(x).strip().lower()
        if not tf:
            continue
        try:
            tf_seconds(tf)
        except (KeyError, ValueError):
            raise ValueError(f"temporalidad inválida: {x} (usa 15m, 1h, 4h, 1d...)") from None
        if tf not in out:
            out.append(tf)
    if not out:
        raise ValueError("hace falta al menos una temporalidad")
    return out


def needed(cfg):
    """Velas mínimas para que el walk-forward tenga las ventanas completas."""
    return cfg.train_window_bars + cfg.wf_folds * cfg.test_window_bars + 200


def summarize(rows):
    """Veredicto del conjunto: cuántas celdas hay y si la ventaja se repite."""
    cells = [r for r in rows if r.get("sharpe") is not None]
    skipped = [r for r in rows if r.get("sharpe") is None]
    passing = [r for r in cells if not r.get("gate_fails")]
    edge = [r for r in passing if (r.get("excess_pct") or 0) > 0]
    syms = sorted({r["symbol"] for r in edge})
    tfs = sorted({r["timeframe"] for r in edge})
    trials = sum(r.get("candidates") or 0 for r in cells)
    best = max(cells, key=lambda r: r.get("excess_pct") or -1e12) if cells else None
    if not cells:
        verdict = "sin_datos"
    elif len(edge) >= 2 and (len(syms) >= 2 or len(tfs) >= 2):
        verdict = "repetible"
    elif edge:
        verdict = "aislado"
    else:
        verdict = "sin_ventaja"
    out = {"cells": len(rows), "evaluated": len(cells), "skipped": len(skipped),
           "passing": len(passing), "edge": len(edge), "verdict": verdict,
           "trials": trials, "symbols_with_edge": syms, "timeframes_with_edge": tfs,
           "best": None,
           "note": (f"{trials} combinaciones probadas en {len(cells)} celdas: con tantas "
                    "pruebas, alguna gana por azar. Solo cuenta lo que se repite en más "
                    "de un símbolo o temporalidad.")}
    if best is not None:
        out["best"] = {k: best.get(k) for k in ("symbol", "timeframe", "decision", "strategy",
                                                "sharpe", "pf", "trades", "excess_pct")}
    return out


class Sweep:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.lock = threading.Lock()
        self._thread = None
        self._feeds = {}
        self.state = {"status": "idle", "run_id": None, "started": 0.0, "finished": 0.0,
                      "error": None, "done": 0, "total": 0, "current": None,
                      "rows": [], "summary": {}, "seconds": None}

    # ------------------------------------------------------------------ estado
    def snapshot(self):
        with self.lock:
            st = dict(self.state)
            st["rows"] = [dict(r) for r in self.state["rows"]]
            st["summary"] = dict(self.state["summary"] or {})
        if not st.get("run_symbols"):
            # Tras un reinicio el barrido en curso se pierde, pero sus celdas
            # siguen en la base: se recupera lo último lanzado para que la
            # página muestre qué combinaciones se probaron por última vez.
            try:
                rid = self.store.latest_run_id()
                rows = self.store.experiments(rid, MAX_CELLS) if rid else []
            except Exception:
                rows = []
            if rows:
                st["run_id"] = rid
                st["run_symbols"] = sorted({r["symbol"] for r in rows})
                st["run_timeframes"] = sorted({r["timeframe"] for r in rows})
                # el máximo de la corrida: cada celda puede tener menos velas
                # según lo que el exchange tenga para esa temporalidad
                st["bars"] = max((r.get("bars") or 0) for r in rows) or st.get("bars")
                st["run_folds"] = rows[0].get("folds") or self.cfg.wf_folds
                st["test_bars"] = rows[0].get("test_bars") or self.cfg.test_window_bars
        st.update(symbols=list(self.cfg.symbols), universe=list(UNIVERSE),
                  timeframes=list(TIMEFRAMES), history_bars=self.cfg.history_bars,
                  cfg_timeframe=self.cfg.timeframe,
                  folds=self.cfg.wf_folds,                  train_bars=self.cfg.train_window_bars,
                  test_bars=st.get("test_bars") or self.cfg.test_window_bars, source=self.cfg.data_source,
                  needed=needed(self.cfg))
        return st

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    # ------------------------------------------------------------------ arranque
    def _prepare(self, payload):
        p = payload or {}
        syms = _symbols(p.get("symbols") or self.cfg.symbols)
        tfs = _timeframes(p.get("timeframes") or [self.cfg.timeframe])
        bars = int(p.get("bars") or self.cfg.history_bars)
        folds = int(p.get("folds") or self.cfg.wf_folds)
        train = int(p.get("train_bars") or self.cfg.train_window_bars)
        test = int(p.get("test_bars") or self.cfg.test_window_bars)
        if bars < MIN_BARS:
            raise ValueError(f"hacen falta al menos {MIN_BARS} velas por celda")
        if folds < 1 or test < 5 or train < 100:
            raise ValueError("ventanas inválidas: train >= 100, test >= 5, folds >= 1")
        if len(syms) * len(tfs) > MAX_CELLS:
            raise ValueError(f"{len(syms)}×{len(tfs)} = {len(syms) * len(tfs)} celdas; "
                             f"el máximo es {MAX_CELLS}")
        cfg = copy.copy(self.cfg)
        cfg.history_bars, cfg.wf_folds = bars, folds
        cfg.train_window_bars, cfg.test_window_bars = train, test
        cfg.validate()   # mensajes de ventanas / símbolos ya probados
        return syms, tfs, cfg

    def start(self, payload=None):
        """Lanza el barrido en un hilo y devuelve el run_id."""
        syms, tfs, cfg = self._prepare(payload)
        with self.lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("ya hay un barrido en marcha; espera a que termine")
            run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{len(syms)}x{len(tfs)}"
            self.state = {"status": "running", "run_id": run_id, "started": time.time(),
                          "finished": 0.0, "error": None, "done": 0,
                          "total": len(syms) * len(tfs), "current": None,
                          "rows": [], "summary": {}, "seconds": None,
                          "run_symbols": list(syms), "run_timeframes": list(tfs),
                          "bars": cfg.history_bars, "run_folds": cfg.wf_folds}
            self._thread = threading.Thread(target=self._run, args=(run_id, syms, tfs, cfg),
                                            name="sweep", daemon=True)
            self._thread.start()
        log.info("barrido %s: %s símbolos × %s temporalidades", run_id, len(syms), len(tfs))
        return {"ok": True, "run_id": run_id, "cells": len(syms) * len(tfs),
                "symbols": syms, "timeframes": tfs}

    def run(self, payload=None):
        """Igual que `start`, pero en el hilo actual (CLI y pruebas)."""
        syms, tfs, cfg = self._prepare(payload)
        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{len(syms)}x{len(tfs)}"
        self._set(status="running", run_id=run_id, started=time.time(), finished=0.0,
                  error=None, done=0, total=len(syms) * len(tfs), current=None,
                  rows=[], summary={}, seconds=None, run_symbols=list(syms),
                  run_timeframes=list(tfs), bars=cfg.history_bars, run_folds=cfg.wf_folds)
        self._run(run_id, syms, tfs, cfg)
        return self.snapshot()

    # ------------------------------------------------------------------ trabajo
    def _run(self, run_id, syms, tfs, cfg):
        t0 = time.time()
        rows = []
        try:
            for sym in syms:
                for tf in tfs:
                    self._set(current={"symbol": sym, "timeframe": tf,
                                       "stage": "preparando", "done": 0, "total": 0})
                    row = self._cell(run_id, sym, tf, cfg)
                    rows.append(row)
                    try:
                        self.store.add_experiment(run_id, row)
                    except Exception:      # un fallo al guardar no invalida la medida
                        log.exception("no se pudo guardar el intento %s %s", sym, tf)
                    self._set(rows=list(rows), done=len(rows), summary=summarize(rows))
            self._set(status="done", finished=time.time(), current=None,
                      summary=summarize(rows), seconds=round(time.time() - t0, 1))
            log.info("barrido %s terminado: %s", run_id, summarize(rows)["verdict"])
        except Exception as exc:
            log.exception("barrido %s interrumpido", run_id)
            self._set(status="error", error=str(exc) or exc.__class__.__name__,
                      finished=time.time(), current=None, summary=summarize(rows))

    def _feed(self, cfg):
        """Un feed por temporalidad: `load_markets` se paga una vez, no por celda."""
        if cfg.data_source != "exchange":
            return None
        feed = self._feeds.get(cfg.timeframe)
        if feed is None:
            feed = make_feed(cfg, self.store)
            self._feeds[cfg.timeframe] = feed
        return feed

    def _candles(self, sym, cfg):
        """Velas de la celda: primero la caché local, si no alcanza se descargan."""
        want = cfg.history_bars
        df = self.store.candles(sym, cfg.timeframe, want)
        if df is not None and len(df) >= needed(cfg):
            return df
        feed = self._feed(cfg)
        if feed is None:
            return df
        self._set(current={"symbol": sym, "timeframe": cfg.timeframe,
                           "stage": "descargando", "done": 0, "total": want})
        try:
            feed.candles(sym)
        except Exception as exc:
            raise RuntimeError(f"no se pudo descargar {sym} {cfg.timeframe}: {exc}") from None
        return self.store.candles(sym, cfg.timeframe, want)

    def _cell(self, run_id, sym, tf, cfg):
        t0 = time.time()
        c = copy.copy(cfg)
        c.timeframe = tf
        base = {"symbol": sym, "timeframe": tf, "run_id": run_id,
                "test_bars": c.test_window_bars, "folds": c.wf_folds}
        try:
            df = self._candles(sym, c)
        except Exception as exc:
            return dict(base, status="error", decision="error", bars=0,
                        error=str(exc), seconds=round(time.time() - t0, 2))
        n = 0 if df is None else len(df)
        need = needed(c)
        if n < need:
            return dict(base, status="datos_insuficientes", decision="datos_insuficientes",
                        bars=n, needed=need, seconds=round(time.time() - t0, 2))

        def progress(ev, _sym=sym, _tf=tf):
            self._set(current=dict(ev, symbol=_sym, timeframe=_tf))

        progress({"stage": "backtest", "done": 0, "total": 0, "fold": 0})
        _, rep = optimizer.train_symbol(df, c, None, progress=progress)
        wf = rep.get("wf_oos") or {}
        row = dict(base, status=rep.get("status"), decision=rep.get("decision", rep.get("status")),
                   strategy=(rep.get("folds") or [{}])[-1].get("strategy"),
                   params=(rep.get("folds") or [{}])[-1].get("params"),
                   sharpe=wf.get("sharpe"), pf=wf.get("pf"), trades=wf.get("trades"),
                   dd_pct=wf.get("max_dd_pct"), ret_pct=wf.get("return_pct"),
                   bh_pct=rep.get("benchmark_oos_pct"), excess_pct=rep.get("excess_oos_pct"),
                   bars=rep.get("bars", n), candidates=rep.get("candidates"),
                   workers=rep.get("workers"), gate_fails=rep.get("gate_fails") or [],
                   seconds=round(time.time() - t0, 2))
        log.info("barrido %s %s %s: %s (exceso %s pp, %ss)", run_id, sym, tf,
                 row["decision"], row["excess_pct"], row["seconds"])
        return row


# ---------------------------------------------------------------------- CLI
def main(argv=None):
    """Barrido sin panel: `python -m app.sweep --symbols ... --timeframes ...`."""
    from .config import Config
    from .settings import load
    from .store import Store

    ap = argparse.ArgumentParser(description="Barrido símbolo × temporalidad")
    ap.add_argument("--symbols", default="", help="BASE/QUOTE separados por coma")
    ap.add_argument("--timeframes", default="", help="15m,1h,4h,1d...")
    ap.add_argument("--bars", type=int, default=0, help="velas por celda (0 = HISTORY_BARS)")
    ap.add_argument("--folds", type=int, default=0, help="folds del walk-forward")
    ap.add_argument("--train", type=int, default=0, help="ventana de entrenamiento")
    ap.add_argument("--test", type=int, default=0, help="ventana de test")
    ap.add_argument("--json", action="store_true", help="salida completa en JSON")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config()
    store = Store(cfg.data_dir)
    load(cfg, store)
    sweep = Sweep(cfg, store)
    out = sweep.run({"symbols": a.symbols or cfg.symbols,
                     "timeframes": a.timeframes or [cfg.timeframe],
                     "bars": a.bars, "folds": a.folds,
                     "train_bars": a.train, "test_bars": a.test})
    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["status"] == "done" else 1
    print(f"\nbarrido {out['run_id']}: {out['status']}")
    for r in out["rows"]:
        print(f"  {r['symbol']:<10} {r['timeframe']:<4} {str(r['decision']):<28} "
              f"trades {r.get('trades')} sharpe {r.get('sharpe')} "
              f"exceso {r.get('excess_pct')} pp ({r.get('seconds')}s)")
    s = out["summary"] or {}
    print(f"veredicto: {s.get('verdict')} · con ventaja {s.get('edge')}/{s.get('evaluated')}"
          f" · {s.get('trials')} combinaciones probadas")
    return 0 if out["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
