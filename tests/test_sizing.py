"""Sizing: riesgo por operación, topes y MIN_NOTIONAL."""
import pytest

from app.config import Config
from app.sizing import entry_qty, stop_and_tp


@pytest.fixture
def cfg():
    return Config()


def test_qty_respects_risk_per_trade(cfg):
    # equity 1000, riesgo 0.5%, ATR 10, STOP_ATR_MULT 2 -> stop a 20
    # qty = 1000 * 0.005 / 20 = 0.25 -> nocional 25 al precio 100
    q = entry_qty(cfg, equity=1000, cash=1000, exposure=0.0, price=100.0, atr_value=10.0)
    assert q == pytest.approx(0.25)


def test_qty_capped_by_max_position(cfg):
    # ATR 1 -> qty teórica 2.5 (nocional 250); el tope es 25% de 1000 = 250
    q = entry_qty(cfg, equity=1000, cash=1000, exposure=0.0, price=100.0, atr_value=1.0)
    assert q * 100.0 == pytest.approx(250.0)


def test_qty_capped_by_total_exposure(cfg):
    cfg.max_position_pct = 100
    # exposición ya al 60% -> no queda margen
    q = entry_qty(cfg, equity=1000, cash=1000, exposure=600.0, price=100.0, atr_value=1.0)
    assert q == 0.0


def test_qty_respects_min_notional(cfg):
    # ATR enorme -> qty diminuta -> nocional bajo MIN_NOTIONAL
    q = entry_qty(cfg, equity=1000, cash=1000, exposure=0.0, price=100.0, atr_value=5000.0)
    assert q == 0.0


def test_risk_multiplier_scales_size(cfg):
    full = entry_qty(cfg, 1000, 1000, 0.0, 100.0, 10.0, mult=1.0)
    half = entry_qty(cfg, 1000, 1000, 0.0, 100.0, 10.0, mult=0.5)
    assert half == pytest.approx(full / 2)


def test_invalid_inputs_return_zero(cfg):
    assert entry_qty(cfg, 0, 0, 0.0, 100.0, 10.0) == 0.0
    assert entry_qty(cfg, 1000, 1000, 0.0, 0.0, 10.0) == 0.0
    assert entry_qty(cfg, 1000, 1000, 0.0, 100.0, 0.0) == 0.0


def test_stop_and_tp(cfg):
    stop, tp = stop_and_tp(cfg, fill=100.0, side=1, atr_value=10.0)
    assert stop == pytest.approx(100.0 - 10.0 * cfg.stop_atr_mult)
    assert tp is None  # TAKE_PROFIT_RR=0 por defecto
    cfg.take_profit_rr = 2.0
    stop, tp = stop_and_tp(cfg, fill=100.0, side=1, atr_value=10.0)
    assert tp == pytest.approx(100.0 + 2 * 10.0 * cfg.stop_atr_mult)
