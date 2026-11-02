"""Simulación bar a bar de la ejecución real, para el walk-forward.

Antes el optimizador medía la *señal* (retorno por vela con costos por cambio
de posición) mientras el motor ejecutaba otra cosa: stop ATR, sizing por
riesgo, topes, cooldown. Este módulo cierra esa brecha: el OOS pasa por aquí y
usa las mismas fórmulas (`sizing`) y el mismo modelo de costos (`broker`) que
el motor en vivo.

Orden de cada vela, igual que el motor:
 1. salidas por stop / take profit (con huecos: si la vela abre más allá del
    nivel, el fill es el open, no el nivel);
 2. salida por cambio de señal;
 3. entrada, solo si la señal es nueva (o `ENTER_ON_START` en la primera vela),
    fuera de cooldown y con sizing válido.

El bucle interior es el punto caliente del walk-forward (candidatos × velas),
así que trabaja con listas de floats de Python en vez de escalares numpy: crear
un `np.float64` por vela y por serie costaba más que la propia aritmética.
"""
import math

import numpy as np
import pandas as pd

from . import broker, regime, sizing, strategies


def vol_ratio_series(close, short, long):
    """Volatilidad corta / larga por vela (sin mirar el futuro)."""
    c = np.asarray(close, dtype=float)
    n = len(c)
    r = np.zeros(n)
    if n > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            r[1:] = np.diff(np.log(np.maximum(c, 1e-12)))
    r[~np.isfinite(r)] = 0.0
    short, long = max(2, int(short)), max(3, int(long))
    rs = pd.Series(r)
    lo = rs.rolling(long, min_periods=min(long, max(2, n))).std()
    sh = rs.rolling(short, min_periods=min(short, max(2, n))).std()
    with np.errstate(divide="ignore", invalid="ignore"):
        # copy=True: pandas puede devolver un array de solo lectura (pandas >= 2.2)
        ratio = (sh / lo).to_numpy(dtype=float, copy=True)
    ratio[~np.isfinite(ratio)] = 1.0
    return ratio


def _empty(n):
    return {"equity": np.full(n, np.nan), "ret": np.zeros(n),
            "trades": [], "trade_bars": np.zeros(0, dtype=int),
            "final_equity": float("nan"), "open_at_end": False}


def _col(df, name):
    """Columna como lista de floats (o None si no existe)."""
    return None if name not in df.columns else df[name].to_numpy(dtype=float).tolist()


def simulate(df, desired, cfg, atr_series=None, vol_ratio=None):
    """Simula el motor sobre `df` con la señal `desired` ({-1,0,1} por vela).

    Devuelve equity por vela, retornos por vela, operaciones cerradas (con su
    PnL en quote y en R) y el resultado final.
    """
    n = len(df)
    if n == 0:
        return _empty(0)

    close = df["close"].to_numpy(dtype=float).tolist()
    high = _col(df, "high") or close
    low = _col(df, "low") or close
    open_ = _col(df, "open") or close
    want_all = np.asarray(desired).astype(np.int64).ravel().tolist()
    if len(want_all) < n:
        want_all += [0] * (n - len(want_all))
    if atr_series is None:
        atr_series = strategies.atr(df, cfg.atr_period)
    atr_arr = np.asarray(atr_series, dtype=float).tolist()
    na = len(atr_arr)
    vr = None if vol_ratio is None else np.asarray(vol_ratio, dtype=float).tolist()
    nvr = 0 if vr is None else len(vr)

    eq = [0.0] * n
    cash = float(cfg.initial_capital)
    peak = cash
    dd = 0.0
    side = 0
    qty = 0.0
    entry = 0.0
    stop = 0.0
    tp = None
    entry_fee = 0.0
    risk_amt = 0.0
    cooldown_until = -1
    trades = []

    inf = math.isfinite
    cd_bars = int(cfg.cooldown_bars)
    reentry_reset = bool(cfg.reentry_requires_reset)
    enter_first = bool(cfg.enter_on_start)
    cost_side = cfg.cost_side

    for i in range(n):
        px = close[i]

        # 1) salidas
        if side != 0:
            reason = ref = None
            if side > 0:
                if low[i] <= stop:
                    reason, ref = "stop", min(stop, open_[i])
                elif tp is not None and high[i] >= tp:
                    reason, ref = "take_profit", max(tp, open_[i])
            else:
                if high[i] >= stop:
                    reason, ref = "stop", max(stop, open_[i])
                elif tp is not None and low[i] <= tp:
                    reason, ref = "take_profit", min(tp, open_[i])
            if reason is None and want_all[i] != side:
                reason, ref = "signal", px
            if reason is not None and inf(ref) and ref > 0:
                q = qty
                fill = broker.paper_fill(cfg, ref, -side)
                fe = broker.paper_fee(cfg, q, fill)
                if side > 0:
                    cash += q * fill - fe
                else:
                    cash -= q * fill + fe
                pnl = side * (fill - entry) * q - fe - entry_fee
                trades.append({"i": i, "side": side, "qty": q, "entry": entry, "exit": fill,
                               "pnl": float(pnl), "r": float(pnl / risk_amt) if risk_amt > 0 else 0.0,
                               "reason": reason})
                if pnl < 0:
                    cooldown_until = i + cd_bars
                side, qty, entry, stop, tp = 0, 0.0, 0.0, 0.0, None
                entry_fee, risk_amt = 0.0, 0.0

        # 2) entradas
        if side == 0:
            want = want_all[i]
            if reentry_reset:
                fresh = (want != want_all[i - 1]) if i > 0 else enter_first
            else:
                fresh = True
            if want != 0 and fresh and i > cooldown_until:
                a = atr_arr[i] if i < na else float("nan")
                if inf(a) and a > 0 and inf(px) and px > 0:
                    ratio = vr[i] if (vr is not None and i < nvr) else 1.0
                    mult = regime.risk_multiplier(cfg, ratio, dd)
                    q = sizing.entry_qty(cfg, cash, cash, 0.0, px, a, mult, want)
                    if q > 0:
                        fill = broker.paper_fill(cfg, px, want)
                        fe = broker.paper_fee(cfg, q, fill)
                        stop_, tp_ = sizing.stop_and_tp(cfg, fill, want, a)
                        if want > 0:
                            cash -= q * fill + fe
                        else:
                            cash += q * fill - fe
                        side, qty, entry, stop, tp = want, q, fill, stop_, tp_
                        entry_fee = fe
                        risk_amt = q * abs(fill - stop_)

        # 3) equity de la vela
        if side > 0:
            ev = cash + qty * px
        elif side < 0:
            ev = cash - qty * px
        else:
            ev = cash
        eq[i] = ev
        if ev > peak:
            peak = ev
        dd = (peak - ev) / peak * 100.0 if peak > 0 else 0.0

    eq = np.asarray(eq, dtype=float)
    ret = np.zeros(n, dtype=float)
    prev_eq = np.r_[float(cfg.initial_capital), eq[:-1]]
    ok = prev_eq > 0
    ret[ok] = eq[ok] / prev_eq[ok] - 1.0
    ret[~ok] = 0.0

    return {"equity": eq, "ret": ret, "trades": trades,
            "trade_bars": np.array([t["i"] for t in trades], dtype=int),
            "final_equity": float(eq[-1]), "open_at_end": side != 0,
            "cost_side": cost_side}
