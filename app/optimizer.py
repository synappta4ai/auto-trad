"""Backtest vectorizado + walk-forward + decisión champion/challenger.

Regla (ver README):
 1. Para cada fold, se eligen parámetros SOLO con la ventana de entrenamiento previa y se miden
    en la ventana de test siguiente. Los tests concatenados (OOS) estiman si el *procedimiento*
    tiene ventaja real.
 2. Si el OOS agregado no pasa los umbrales -> "sin ventaja": no se promueve nada (y opcionalmente
    se retira al campeón actual, quedando el símbolo en liquidez).
 3. Si pasa, el retador (mejor del último fold) debe superar al campeón en el último tramo OOS por
    PROMOTE_MARGIN_PCT para reemplazarlo.
"""
import random

import numpy as np

from . import strategies


def bar_returns(close, pos, cost_side):
    """Retorno por vela de mantener pos[t-1] durante la vela t, con costo por cambio de posición."""
    r = np.zeros(len(close))
    r[1:] = close[1:] / close[:-1] - 1
    held = np.zeros(len(close))
    held[1:] = pos[:-1]
    turn = np.zeros(len(close))
    turn[1:] = np.abs(held[1:] - held[:-1])
    return held * r - cost_side * turn, held


def metrics(ret, held, bpy):
    n = len(ret)
    if n < 5:
        return {"bars": n, "trades": 0, "sharpe": 0.0, "pf": 0.0, "max_dd_pct": 0.0, "return_pct": 0.0}
    sd = ret.std()
    sharpe = float(ret.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0
    eq = np.cumprod(1 + ret)
    dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100)
    gain, loss = ret[ret > 0].sum(), -ret[ret < 0].sum()
    pf = float(gain / loss) if loss > 0 else (99.0 if gain > 0 else 0.0)
    h = held
    trades = int(((h[1:] != 0) & (h[1:] != h[:-1])).sum())
    return {"bars": n, "trades": trades, "sharpe": round(sharpe, 3), "pf": round(min(pf, 99.0), 3),
            "max_dd_pct": round(dd, 2), "return_pct": round((eq[-1] - 1) * 100, 2)}


def _score(ret, held, bpy, min_trades=5):
    m = metrics(ret, held, bpy)
    return m["sharpe"] if m["trades"] >= min_trades else -1e9


def _gates(m, cfg):
    fails = []
    if m["trades"] < cfg.min_oos_trades:
        fails.append(f"trades {m['trades']}<{cfg.min_oos_trades}")
    if m["sharpe"] < cfg.min_oos_sharpe:
        fails.append(f"sharpe {m['sharpe']}<{cfg.min_oos_sharpe}")
    if m["pf"] < cfg.min_oos_profit_factor:
        fails.append(f"pf {m['pf']}<{cfg.min_oos_profit_factor}")
    if m["max_dd_pct"] > cfg.max_oos_drawdown_pct:
        fails.append(f"dd {m['max_dd_pct']}>{cfg.max_oos_drawdown_pct}")
    return fails


def train_symbol(df, cfg, champion, progress=None):
    """Devuelve (nuevo_campeón|None, reporte). `champion` = {'strategy','params',...} o None.

    `progress`, si se pasa, recibe dicts con el avance: candidatos evaluados,
    fold actual y decisión final. Sirve para mostrar el entrenamiento en vivo.
    """
    def tick(ev):
        if progress is not None:
            progress(ev)

    TR, T, K = cfg.train_window_bars, cfg.test_window_bars, cfg.wf_folds
    n = len(df)
    if n < TR + K * T + 100:
        return champion, {"status": "datos_insuficientes", "bars": n, "needed": TR + K * T + 100}
    cands = strategies.candidates(cfg)
    if len(cands) > cfg.max_candidates:
        cands = random.Random(0).sample(cands, cfg.max_candidates)
    close = df["close"].to_numpy()
    bpy, cost = cfg.bpy, cfg.cost_side
    rets, helds = [], []
    total = len(cands)
    step = max(1, total // 25)
    tick({"stage": "backtest", "done": 0, "total": total, "fold": 0, "folds": K})
    for i, (name, p) in enumerate(cands):
        ret, held = bar_returns(close, strategies.positions(name, df, p, cfg), cost)
        rets.append(ret)
        helds.append(held)
        if (i + 1) % step == 0 or i + 1 == total:
            tick({"stage": "backtest", "done": i + 1, "total": total, "fold": 0, "folds": K})

    first_test = n - K * T
    oos_r, oos_h, folds = [], [], []
    for k in range(K):
        t0 = first_test + k * T
        t1 = t0 + T
        tr0 = t0 - TR
        sc = [_score(rets[i][tr0:t0], helds[i][tr0:t0], bpy) for i in range(len(cands))]
        b = int(np.argmax(sc))
        oos_r.append(rets[b][t0:t1])
        oos_h.append(helds[b][t0:t1])
        folds.append({"strategy": cands[b][0], "params": cands[b][1], "train_sharpe": round(sc[b], 3),
                      "test": metrics(rets[b][t0:t1], helds[b][t0:t1], bpy)})
        tick({"stage": "fold", "done": k + 1, "total": K, "fold": k + 1, "folds": K,
              "best": {"strategy": cands[b][0], "params": cands[b][1]}})
    wf = metrics(np.concatenate(oos_r), np.concatenate(oos_h), bpy)
    fails = _gates(wf, cfg)
    report = {"status": "ok", "bars": n, "candidates": len(cands), "wf_oos": wf, "folds": folds, "gate_fails": fails}
    tick({"stage": "decision", "done": 1, "total": 1, "fold": K, "folds": K})

    if fails:
        report["decision"] = "sin_ventaja"
        if champion is not None and cfg.demote_on_fail:
            report["decision"] = "sin_ventaja_campeon_retirado"
            return None, report
        return champion, report

    last = folds[-1]
    challenger = {"strategy": last["strategy"], "params": last["params"]}
    t0, t1 = n - T, n
    ch_m = last["test"]
    if champion is None:
        report["decision"] = "promovido_sin_campeon"
        new = dict(challenger, oos=ch_m, wf_oos=wf)
        return new, report
    if champion["strategy"] == challenger["strategy"] and champion["params"] == challenger["params"]:
        report["decision"] = "campeon_confirmado"
        return dict(champion, oos=ch_m, wf_oos=wf), report
    cret, cheld = bar_returns(close, strategies.positions(champion["strategy"], df, champion["params"], cfg), cost)
    c_m = metrics(cret[t0:t1], cheld[t0:t1], bpy)
    report["champion_last_oos"], report["challenger_last_oos"] = c_m, ch_m
    thr = c_m["sharpe"] + abs(c_m["sharpe"]) * cfg.promote_margin_pct / 100.0
    if ch_m["sharpe"] > thr and ch_m["sharpe"] > 0:
        report["decision"] = "retador_promovido"
        return dict(challenger, oos=ch_m, wf_oos=wf), report
    report["decision"] = "campeon_se_mantiene"
    return dict(champion, wf_oos=wf), report
