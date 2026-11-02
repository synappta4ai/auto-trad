"""Deriva de mercado y multiplicador de riesgo automático."""
import numpy as np


def vol_ratio(close, short, long):
    """Volatilidad reciente / volatilidad normal. >1 = mercado más agitado que de costumbre."""
    c = np.asarray(close, dtype=float)
    if len(c) < long + 2:
        return 1.0
    r = np.diff(np.log(c))
    lv = r[-long:].std()
    return float(r[-short:].std() / lv) if lv > 0 else 1.0


def risk_multiplier(cfg, ratio, dd_pct):
    """Multiplica el riesgo por operación. Baja con picos de volatilidad y con drawdown."""
    m = 1.0
    if cfg.vol_scaling_enabled and ratio > 1.0:
        m *= 1.0 / ratio
    if cfg.derisk_enabled and dd_pct > cfg.derisk_start_dd_pct:
        span = max(1e-9, cfg.max_drawdown_pct - cfg.derisk_start_dd_pct)
        frac = min(1.0, (dd_pct - cfg.derisk_start_dd_pct) / span)
        m *= max(cfg.derisk_min_mult, 1.0 - frac * (1.0 - cfg.derisk_min_mult))
    return float(min(cfg.risk_mult_max, max(cfg.risk_mult_min, m)))


def drift_detected(cfg, ratio, recent_pnls):
    if ratio >= cfg.drift_vol_ratio or ratio <= 1.0 / cfg.drift_vol_ratio:
        return f"vol_ratio={ratio:.2f}"
    k = cfg.drift_loss_streak
    if k > 0 and len(recent_pnls) >= k and all(p < 0 for p in recent_pnls[:k]):
        return f"racha_de_{k}_perdidas"
    return None
