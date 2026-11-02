"""Fórmulas de sizing y de niveles de stop/take-profit.

Las comparten el motor (ejecución real) y el simulador del walk-forward
(`app/sim.py`), para que lo que se mide fuera de muestra sea exactamente lo que
se ejecuta en vivo. Si cambia el sizing, cambia en un solo sitio.
"""
import math


def stop_distance(cfg, atr_value):
    """Distancia al stop en precio: ATR * STOP_ATR_MULT."""
    return float(atr_value) * cfg.stop_atr_mult


def entry_qty(cfg, equity, cash, exposure, price, atr_value, mult=1.0, side=1):
    """Cantidad a operar según riesgo, tope de posición y caja.

    Devuelve 0.0 cuando la operación no debe hacerse (ATR inválido, tope de
    exposición ya alcanzado o nocional por debajo de MIN_NOTIONAL).
    """
    if not math.isfinite(price) or price <= 0 or not math.isfinite(equity) or equity <= 0:
        return 0.0
    dist = stop_distance(cfg, atr_value)
    if not math.isfinite(dist) or dist <= 0:
        return 0.0
    qty = equity * cfg.risk_per_trade_pct / 100.0 * mult / dist
    max_notional = min(equity * cfg.max_position_pct / 100.0,
                       equity * cfg.max_total_exposure_pct / 100.0 - exposure)
    if side > 0:
        max_notional = min(max_notional, cash * 0.995)
    qty = min(qty, max(0.0, max_notional) / price)
    if qty <= 0 or qty * price < cfg.min_notional:
        return 0.0
    return float(qty)


def stop_and_tp(cfg, fill, side, atr_value):
    """Niveles de stop (siempre) y take profit (None si TAKE_PROFIT_RR <= 0)."""
    dist = stop_distance(cfg, atr_value)
    stop = fill - side * dist
    tp = fill + side * dist * cfg.take_profit_rr if cfg.take_profit_rr > 0 else None
    return stop, tp
