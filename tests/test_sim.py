"""Simulador de ejecución: stop, sizing, cooldown y costos."""
import numpy as np
import pandas as pd
import pytest

from app import sim
from app.config import Config


def frame(close, spread=0.002):
    close = np.asarray(close, dtype=float)
    open_ = np.r_[close[0], close[:-1]]
    idx = pd.date_range("2024-01-01", periods=len(close), freq="h", tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) * (1 + spread),
                         "low": np.minimum(open_, close) * (1 - spread),
                         "close": close,
                         "volume": np.full(len(close), 100.0)}, index=idx)


@pytest.fixture
def cfg():
    c = Config()
    c.enter_on_start = True
    c.poll_seconds = 1
    return c


def test_no_signal_keeps_capital(cfg):
    df = frame(np.linspace(100, 120, 80))
    out = sim.simulate(df, np.zeros(80, dtype=int), cfg,
                       atr_series=np.full(80, 1.0), vol_ratio=np.ones(80))
    assert out["trades"] == []
    assert out["final_equity"] == pytest.approx(cfg.initial_capital)
    assert np.allclose(out["ret"], 0.0)


def test_stop_closes_the_position(cfg):
    close = np.r_[np.full(20, 100.0), np.full(40, 80.0)]
    df = frame(close)
    out = sim.simulate(df, np.ones(len(close), dtype=int), cfg,
                       atr_series=np.full(len(close), 0.5), vol_ratio=np.ones(len(close)))
    assert len(out["trades"]) == 1, "tras el stop no debe reentrar sin señal nueva"
    trade = out["trades"][0]
    assert trade["reason"] == "stop"
    assert trade["pnl"] < 0
    assert trade["r"] <= 0


def test_reentry_when_reset_not_required(cfg):
    # tendencia bajista sostenida: tras cada stop la señal sigue en 1 y, sin
    # REENTRY_AFTER_STOP_REQUIRES_RESET, debe volver a entrar
    close = np.r_[np.full(20, 100.0), np.linspace(90.0, 60.0, 40)]
    df = frame(close)
    cfg.reentry_requires_reset = False
    cfg.cooldown_bars = 0
    out = sim.simulate(df, np.ones(len(close), dtype=int), cfg,
                       atr_series=np.full(len(close), 0.5), vol_ratio=np.ones(len(close)))
    assert len(out["trades"]) > 1


def test_position_respects_cap(cfg):
    close = np.exp(np.linspace(np.log(100), np.log(130), 120))
    df = frame(close)
    desired = np.ones(120, dtype=int)
    desired[-10:] = 0  # cierre por señal para poder inspeccionar la operación
    out = sim.simulate(df, desired, cfg,
                       atr_series=np.full(120, 1.0), vol_ratio=np.ones(120))
    t = out["trades"][0]
    notional = t["qty"] * t["entry"]
    assert notional <= cfg.initial_capital * cfg.max_position_pct / 100.0 * 1.01
    assert t["qty"] > 0


def test_costs_reduce_a_flat_roundtrip(cfg):
    # precio constante: la única pérdida posible son los costos de ida y vuelta
    close = np.full(60, 100.0)
    df = frame(close, spread=0.0)
    desired = np.zeros(60, dtype=int)
    desired[10:30] = 1
    out = sim.simulate(df, desired, cfg, atr_series=np.full(60, 5.0), vol_ratio=np.ones(60))
    assert len(out["trades"]) == 1
    assert out["trades"][0]["pnl"] < 0
    assert out["final_equity"] < cfg.initial_capital


def test_equity_series_is_finite(cfg):
    close = 100 * np.exp(np.cumsum(np.random.default_rng(11).standard_normal(200) * 0.01))
    df = frame(close)
    desired = np.repeat([1, 0, 1, 0], 50)
    out = sim.simulate(df, desired, cfg, atr_series=np.full(200, 1.0), vol_ratio=np.ones(200))
    assert np.all(np.isfinite(out["equity"]))
    assert (out["equity"] > 0).all()
    assert len(out["ret"]) == 200


def test_vol_ratio_series_is_bounded_and_finite():
    close = 100 * np.exp(np.cumsum(np.random.default_rng(5).standard_normal(300) * 0.01))
    r = sim.vol_ratio_series(close, 24, 240)
    assert np.all(np.isfinite(r))
    assert (r > 0).all()
