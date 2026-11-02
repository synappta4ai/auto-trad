"""Invariante central: ninguna estrategia mira el futuro (no look-ahead)."""
import numpy as np
import pandas as pd
import pytest

from app import strategies
from app.config import Config


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def df():
    rng = np.random.default_rng(3)
    n = 420
    r = rng.standard_normal(n) * 0.01
    close = 100.0 * np.exp(np.cumsum(r))
    open_ = np.r_[100.0, close[:-1]]
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.full(n, 100.0)}, index=idx)


@pytest.mark.parametrize("name", sorted(strategies.REGISTRY))
def test_positions_are_causal(cfg, df, name):
    """La posición de la vela t solo puede depender de datos hasta t."""
    for params in strategies.REGISTRY[name][0](cfg)[:3]:
        full = strategies.positions(name, df, params, cfg)
        cut = 350
        part = strategies.positions(name, df.iloc[:cut], params, cfg)
        assert len(part) == cut
        np.testing.assert_array_equal(part, full[:cut])


def test_positions_domain(cfg, df):
    for name, (grid, _) in strategies.REGISTRY.items():
        for params in grid(cfg)[:2]:
            pos = strategies.positions(name, df, params, cfg)
            assert set(np.unique(pos)).issubset({-1, 0, 1})


def test_candidates_are_deterministic(cfg):
    assert strategies.candidates(cfg) == strategies.candidates(cfg)
