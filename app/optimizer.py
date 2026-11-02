"""Backtest con ejecución simulada + walk-forward + champion/challenger.

Reglas (ver README):
 1. Cada combinación se simula bar a bar con `sim.simulate`: stop ATR, sizing
    por riesgo, topes, cooldown y costos del broker. Para cada fold se elige la
    mejor SOLO con la ventana de entrenamiento y se mide en el test siguiente,
    que esa elección nunca vio.
 2. Los tests concatenados (OOS) estiman si *el procedimiento* tiene ventaja
    real, comparados contra comprar y mantener el activo (benchmark).
 3. Si el OOS agregado no pasa los umbrales -> "sin ventaja": no se promueve
    nada (y opcionalmente se retira al campeón, quedando el símbolo en
    liquidez).
 4. Si pasa, el retador (mejor del último fold) debe superar al campeón en el
    último tramo OOS por PROMOTE_MARGIN_PCT.
"""
import atexit
import logging
import multiprocessing
import random
import threading
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from . import sim, strategies

log = logging.getLogger("optimizer")

# Puntaje imposible: candidato sin operaciones suficientes en la ventana.
_NO_SCORE = -1e9
# Trabajo mínimo (velas × candidatos) para que paralelizar compense: arrancar
# procesos en Windows cuesta ~1-2 s cada uno.
_PARALLEL_MIN_WORK = 1_500_000


def _sharpe(ret, bpy):
    ret = np.asarray(ret, dtype=float)
    if len(ret) < 5:
        return 0.0
    sd = float(ret.std())
    return float(ret.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0


def _eq_from_ret(ret):
    eq = np.empty(len(ret) + 1, dtype=float)
    eq[0] = 1.0
    ret = np.asarray(ret, dtype=float)
    if len(ret):
        np.cumprod(1.0 + ret, out=eq[1:])
    return eq


def metrics(eq, ret, trades, bpy):
    """Métricas de una ventana. Sharpe y drawdown por vela; PnL por operación."""
    eq = np.asarray(eq, dtype=float)
    ret = np.asarray(ret, dtype=float)
    out = {"bars": int(len(ret)), "trades": len(trades), "sharpe": 0.0, "pf": 0.0,
           "max_dd_pct": 0.0, "return_pct": 0.0, "win_rate": 0.0, "avg_r": 0.0}
    if len(eq) >= 2:
        good = np.isfinite(eq) & (eq > 0)
        if good.sum() >= 2:
            e = eq[good]
            peak = np.maximum.accumulate(e)
            out["max_dd_pct"] = round(float(((peak - e) / peak).max() * 100.0), 2)
            out["return_pct"] = round(float((e[-1] / e[0] - 1.0) * 100.0), 2)
    out["sharpe"] = round(_sharpe(ret, bpy), 3)
    if trades:
        pnls = np.array([t["pnl"] for t in trades], dtype=float)
        gain, loss = float(pnls[pnls > 0].sum()), float(-pnls[pnls < 0].sum())
        out["pf"] = round(gain / loss if loss > 0 else (99.0 if gain > 0 else 0.0), 3)
        out["win_rate"] = round(float((pnls > 0).mean() * 100.0), 1)
        out["avg_r"] = round(float(np.mean([t["r"] for t in trades])), 3)
    return out


def _window(run, a, b):
    """Recorta un resultado de simulación a la ventana [a, b)."""
    trades = [t for t in run["trades"] if a <= t["i"] < b]
    return run["equity"][a:b], run["ret"][a:b], trades


def _trades_between(trade_bars, a, b):
    return int(np.searchsorted(trade_bars, b) - np.searchsorted(trade_bars, a))


def _score(ret, trade_bars, a, b, min_trades, bpy):
    if b - a < 5:
        return _NO_SCORE
    if _trades_between(trade_bars, a, b) < max(1, min_trades):
        return _NO_SCORE
    return _sharpe(ret[a:b], bpy)


def _gates(m, cfg, excess_pct=0.0):
    fails = []
    if m["trades"] < cfg.min_oos_trades:
        fails.append(f"trades {m['trades']}<{cfg.min_oos_trades}")
    if m["sharpe"] < cfg.min_oos_sharpe:
        fails.append(f"sharpe {m['sharpe']}<{cfg.min_oos_sharpe}")
    if m["pf"] < cfg.min_oos_profit_factor:
        fails.append(f"pf {m['pf']}<{cfg.min_oos_profit_factor}")
    if m["max_dd_pct"] > cfg.max_oos_drawdown_pct:
        fails.append(f"dd {m['max_dd_pct']}>{cfg.max_oos_drawdown_pct}")
    if cfg.require_beat_benchmark and excess_pct <= 0:
        fails.append(f"no_supera_benchmark {round(excess_pct, 2)}pp<=0")
    return fails


def _sim(df, pos, cfg, atr_arr, vr):
    return sim.simulate(df, pos, cfg, atr_arr, vr)


def _sim_start(cfg, first_scored):
    """Primera vela que hay que simular.

    Solo se puntúa desde `first_scored` en adelante, pero la simulación y los
    indicadores necesitan un margen previo para llegar con estado real: se
    recorta lo anterior y se ahorra recorrer historia que nadie mira (con 5
    años de velas y ventanas cortas era la mayor parte del trabajo).
    """
    warm = [int(cfg.train_window_bars), 500,
            max(cfg.ema_slow) * 3, max(cfg.breakout_lookback) * 2]
    if "ml_logit" in cfg.strategies and cfg.ml_train_bars:
        warm.append(max(cfg.ml_train_bars) + int(cfg.ml_horizon) + 120)
    return max(0, int(first_scored - max(warm)))


def _workers(cfg, candidates, bars):
    """Núcleos a usar. 0 = automático; 1 = siempre en serie."""
    want = int(getattr(cfg, "parallel_workers", 0) or 0)
    if want == 1:
        return 1
    n = want if want > 1 else max(1, min(8, (multiprocessing.cpu_count() or 2) - 1))
    if want <= 0 and (candidates < 8 or bars * candidates < _PARALLEL_MIN_WORK):
        return 1
    return max(1, min(n, candidates))


# --- reparto entre procesos ---------------------------------------------------
# Los procesos se reutilizan entre entrenamientos (arrancarlos cuesta ~3 s en
# Windows: importar pandas en cada hijo). El trabajo viaja con cada lote, así
# que el pool sirve aunque cambien las velas o la configuración; se cierra solo
# tras un rato sin usarse para no dejar procesos de pandas en memoria.
_POOL = {}
_POOL_LOCK = threading.Lock()
_POOL_IDLE_SECONDS = 180.0


def _shutdown_pool(*_args):
    ex = _POOL.pop("ex", None)
    _POOL.pop("workers", None)
    timer = _POOL.pop("timer", None)
    if timer is not None:
        timer.cancel()
    if ex is not None:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:
            log.debug("no se pudo cerrar el pool", exc_info=True)


atexit.register(_shutdown_pool)


def _arm_idle_timer():
    timer = _POOL.pop("timer", None)
    if timer is not None:
        timer.cancel()
    timer = threading.Timer(_POOL_IDLE_SECONDS, _shutdown_pool)
    timer.daemon = True
    _POOL["timer"] = timer
    timer.start()


def _pool(workers):
    """Pool reutilizable con `workers` procesos (se recrea si cambia el tamaño)."""
    with _POOL_LOCK:
        ex = _POOL.get("ex")
        if ex is not None and _POOL.get("workers") == workers:
            _arm_idle_timer()
            return ex
        _shutdown_pool()
        ctx = multiprocessing.get_context("spawn")
        ex = ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
        _POOL.update(ex=ex, workers=workers)
        _arm_idle_timer()
        return ex


def _score_chunk(payload):
    """Simula un lote de candidatos. Devuelve [(índice, [puntaje por fold])]."""
    df, cfg, atr_arr, vr, bounds, min_train_trades, bpy, items = payload
    out = []
    for idx, name, p in items:
        run = _sim(df, strategies.positions(name, df, p, cfg), cfg, atr_arr, vr)
        out.append((idx, [_score(run["ret"], run["trade_bars"], a, b,
                                min_train_trades, bpy)
                          for (a, b, _te_a, _te_b) in bounds]))
    return out


def _scores_parallel(cands, df, cfg, atr_arr, vr, bounds, min_train_trades, bpy,
                     workers, tick):
    """Puntúa todos los candidatos repartidos entre procesos."""
    items = [(i, name, p) for i, (name, p) in enumerate(cands)]
    total = len(items)
    # round-robin: lotes parecidos y 2 por núcleo para equilibrar si uno es lento
    chunks = [items[i::workers * 2] for i in range(workers * 2)]
    chunks = [c for c in chunks if c]
    payloads = [(df, cfg, atr_arr, vr, bounds, min_train_trades, bpy, c) for c in chunks]
    scores, done, step = {}, 0, max(1, total // 25)
    ex = _pool(workers)
    try:
        for part in ex.map(_score_chunk, payloads):
            for idx, sc in part:
                scores[idx] = sc
            before, done = done, done + len(part)
            if done // step != before // step or done == total:
                tick({"stage": "backtest", "done": min(done, total), "total": total,
                      "fold": 0, "workers": workers})
    except Exception:
        _shutdown_pool()      # un pool roto no sirve para el siguiente intento
        raise
    return scores


def train_symbol(df, cfg, champion, progress=None):
    """Devuelve (nuevo_campeón|None, reporte). `champion` = {'strategy','params',...} o None.

    `progress`, si se pasa, recibe dicts con el avance: candidatos simulados,
    fold actual y decisión final. Sirve para mostrar el entrenamiento en vivo.
    """
    def tick(ev):
        if progress is not None:
            progress(ev)

    TR, T, K = cfg.train_window_bars, cfg.test_window_bars, cfg.wf_folds
    n = len(df)
    if n < TR + K * T + 100:
        return champion, {"status": "datos_insuficientes", "bars": n, "needed": TR + K * T + 100}

    n_full = n
    start = _sim_start(cfg, n - K * T - TR)
    if start > 0:
        df = df.iloc[start:]
        n = len(df)

    cands = strategies.candidates(cfg)
    if len(cands) > cfg.max_candidates:
        cands = random.Random(0).sample(cands, cfg.max_candidates)

    close = df["close"].to_numpy(dtype=float)
    atr_arr = strategies.atr(df, cfg.atr_period).to_numpy(dtype=float)
    vr = sim.vol_ratio_series(close, cfg.drift_short_bars, cfg.drift_long_bars)
    bpy, embargo = cfg.bpy, max(0, int(cfg.embargo))
    total = len(cands)
    step = max(1, total // 25)
    tick({"stage": "backtest", "done": 0, "total": total, "fold": 0, "folds": K})

    first_test = n - K * T
    bounds = []
    for k in range(K):
        t0 = first_test + k * T
        bounds.append((t0 - TR, t0 - embargo, t0, t0 + T))
    best = [None] * K      # (score, índice de candidato)

    # --- 1) simular cada candidato y puntuarlo en cada fold (sin guardar todo)
    workers = _workers(cfg, total, n)
    scores = {}
    if workers > 1:
        try:
            scores = _scores_parallel(cands, df, cfg, atr_arr, vr, bounds,
                                      cfg.min_train_trades, bpy, workers, tick)
        except Exception as exc:      # sin pool (permisos, memoria, arranque...)
            log.warning("paralelismo no disponible (%s): se sigue en serie", exc)
            scores = {}
    if not scores:
        for i, (name, p) in enumerate(cands):
            run = _sim(df, strategies.positions(name, df, p, cfg), cfg, atr_arr, vr)
            scores[i] = [_score(run["ret"], run["trade_bars"], tr_a, tr_b,
                                cfg.min_train_trades, bpy)
                         for (tr_a, tr_b, _te_a, _te_b) in bounds]
            if (i + 1) % step == 0 or i + 1 == total:
                tick({"stage": "backtest", "done": i + 1, "total": total, "fold": 0, "folds": K})
    for k in range(K):
        for i in range(total):
            sc = scores[i][k]
            if best[k] is None or sc > best[k][0]:
                best[k] = (sc, i)

    # --- 2) folds: el ganador de cada uno se mide en su test (nunca visto)
    cache = {}

    def run_of(idx):
        if idx not in cache:
            name, p = cands[idx]
            cache[idx] = _sim(df, strategies.positions(name, df, p, cfg), cfg, atr_arr, vr)
        return cache[idx]

    oos_r, oos_trades, folds = [], [], []
    for k, (tr_a, tr_b, te_a, te_b) in enumerate(bounds):
        idx = best[k][1] if best[k] is not None else 0
        name, p = cands[idx]
        run = run_of(idx)
        eq_w, ret_w, tr_w = _window(run, te_a, te_b)
        oos_r.append(ret_w)
        oos_trades.extend(tr_w)
        folds.append({"strategy": name, "params": p,
                      "train_sharpe": round(_sharpe(run["ret"][tr_a:tr_b], bpy), 3),
                      "test": metrics(eq_w, ret_w, tr_w, bpy)})
        tick({"stage": "fold", "done": k + 1, "total": K, "fold": k + 1, "folds": K,
              "best": {"strategy": name, "params": p}})

    oos_cat = np.concatenate(oos_r) if oos_r else np.zeros(0)
    wf = metrics(_eq_from_ret(oos_cat), oos_cat, oos_trades, bpy)
    bh_pct = float((close[-1] / close[first_test - 1] - 1.0) * 100.0) if first_test > 0 and close[first_test - 1] > 0 else 0.0
    excess = wf["return_pct"] - bh_pct
    wf["excess_vs_bh_pct"] = round(excess, 2)
    fails = _gates(wf, cfg, excess)
    report = {"status": "ok", "bars": n_full, "sim_bars": n, "warmup_bars": start,
              "workers": workers, "candidates": len(cands), "exec_model": "sim",
              "embargo_bars": embargo, "wf_oos": wf, "benchmark_oos_pct": round(bh_pct, 2),
              "excess_oos_pct": round(excess, 2), "folds": folds, "gate_fails": fails}
    tick({"stage": "decision", "done": 1, "total": 1, "fold": K, "folds": K})

    if fails:
        report["decision"] = "sin_ventaja"
        if champion is not None and cfg.demote_on_fail:
            report["decision"] = "sin_ventaja_campeon_retirado"
            return None, report
        return champion, report

    last = folds[-1]
    challenger = {"strategy": last["strategy"], "params": last["params"]}
    ch_m = last["test"]
    if champion is None:
        report["decision"] = "promovido_sin_campeon"
        return dict(challenger, oos=ch_m, wf_oos=wf), report
    if champion["strategy"] == challenger["strategy"] and champion["params"] == challenger["params"]:
        report["decision"] = "campeon_confirmado"
        return dict(champion, oos=ch_m, wf_oos=wf), report
    c_run = _sim(df, strategies.positions(champion["strategy"], df, champion["params"], cfg), cfg, atr_arr, vr)
    t0 = n - T
    c_eq, c_ret, c_tr = _window(c_run, t0, n)
    c_m = metrics(c_eq, c_ret, c_tr, bpy)
    report["champion_last_oos"], report["challenger_last_oos"] = c_m, ch_m
    thr = c_m["sharpe"] + abs(c_m["sharpe"]) * cfg.promote_margin_pct / 100.0
    if ch_m["sharpe"] > thr and ch_m["sharpe"] > 0:
        report["decision"] = "retador_promovido"
        return dict(challenger, oos=ch_m, wf_oos=wf), report
    report["decision"] = "campeon_se_mantiene"
    return dict(champion, wf_oos=wf), report
