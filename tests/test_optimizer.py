"""Walk-forward: folds, puertas, champion/challenger y benchmark."""
import numpy as np
import pandas as pd
import pytest

from app import optimizer
from app.config import Config


def _cfg():
    c = Config()
    c.train_window_bars = 150
    c.test_window_bars = 50
    c.wf_folds = 2
    c.strategies = ["ema_cross"]
    c.ema_fast = [2, 5]
    c.ema_slow = [10, 20]
    c.min_train_trades = 1
    c.max_candidates = 50
    return c


def _df(n=500, seed=7):
    rng = np.random.default_rng(seed)
    drift = np.linspace(0, 0.25, n)
    close = 100.0 * np.exp(drift + np.cumsum(rng.standard_normal(n) * 0.008))
    open_ = np.r_[close[0], close[:-1]]
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) * 1.003,
                         "low": np.minimum(open_, close) * 0.997,
                         "close": close,
                         "volume": np.full(n, 100.0)}, index=idx)


def test_report_shape_and_exec_model():
    cfg, df = _cfg(), _df()
    champ, rep = optimizer.train_symbol(df, cfg, None)
    assert rep["status"] == "ok"
    assert rep["exec_model"] == "sim"
    assert len(rep["folds"]) == cfg.wf_folds
    for key in ("sharpe", "pf", "trades", "max_dd_pct", "return_pct", "win_rate", "avg_r"):
        assert key in rep["wf_oos"]
    assert "benchmark_oos_pct" in rep
    assert "excess_oos_pct" in rep
    assert rep["embargo_bars"] == cfg.ml_horizon
    assert rep["decision"] in {"promovido_sin_campeon", "sin_ventaja",
                               "sin_ventaja_campeon_retirado"}


def test_gates_block_promotion():
    cfg, df = _cfg(), _df()
    cfg.min_oos_sharpe = 1e9
    champ, rep = optimizer.train_symbol(df, cfg, None)
    assert rep["decision"] == "sin_ventaja"
    assert champ is None
    assert any("sharpe" in f for f in rep["gate_fails"])


def test_failed_gate_demotes_existing_champion():
    cfg, df = _cfg(), _df()
    old = {"strategy": "ema_cross", "params": {"fast": 2, "slow": 10}}
    cfg.min_oos_trades = 10_000
    new, rep = optimizer.train_symbol(df, cfg, old)
    assert rep["decision"] == "sin_ventaja_campeon_retirado"
    assert new is None


def test_failed_gate_can_keep_champion():
    cfg, df = _cfg(), _df()
    old = {"strategy": "ema_cross", "params": {"fast": 2, "slow": 10}}
    cfg.min_oos_trades = 10_000
    cfg.demote_on_fail = False
    new, rep = optimizer.train_symbol(df, cfg, old)
    assert rep["decision"] == "sin_ventaja"
    assert new is old


def test_insufficient_data_returns_champion_untouched():
    cfg = _cfg()
    old = {"strategy": "ema_cross", "params": {"fast": 2, "slow": 10}}
    new, rep = optimizer.train_symbol(_df(200), cfg, old)
    assert rep["status"] == "datos_insuficientes"
    assert new is old


def test_benchmark_gate_optional():
    cfg, df = _cfg(), _df()
    cfg.require_beat_benchmark = True
    # Nadie bate a un activo que sube 25% sin riesgo: la puerta debe rechazar.
    _, rep = optimizer.train_symbol(df, cfg, None)
    assert rep["decision"] in {"sin_ventaja", "sin_ventaja_campeon_retirado"}


def test_metrics_are_per_trade():
    trades = [{"pnl": 10.0, "r": 2.0}, {"pnl": -5.0, "r": -1.0}]
    m = optimizer.metrics(np.array([1.0, 1.05, 1.05]), np.array([0.05, 0.0]),
                          trades, bpy=8760)
    assert m["trades"] == 2
    assert m["pf"] == pytest.approx(2.0)
    assert m["win_rate"] == pytest.approx(50.0)
    assert m["avg_r"] == pytest.approx(0.5)


def test_workers_heuristic():
    cfg = Config()
    cfg.parallel_workers = 1
    assert optimizer._workers(cfg, 400, 40000) == 1
    cfg.parallel_workers = 0
    assert optimizer._workers(cfg, 4, 100) == 1          # poco trabajo: en serie
    assert 1 <= optimizer._workers(cfg, 400, 40000) <= 8
    cfg.parallel_workers = 3
    assert optimizer._workers(cfg, 4, 100) == 3          # pedido explícito


def test_score_chunk_matches_inline_simulation():
    """Repartir candidatos entre procesos no cambia ni un puntaje."""
    from app import sim, strategies
    cfg, df = _cfg(), _df(700)
    bounds = [(300, 400, 400, 450), (350, 450, 450, 500)]
    atr = strategies.atr(df, cfg.atr_period).to_numpy(dtype=float)
    vr = sim.vol_ratio_series(df["close"].to_numpy(dtype=float),
                              cfg.drift_short_bars, cfg.drift_long_bars)
    cands = strategies.candidates(cfg)
    items = [(i, n, p) for i, (n, p) in enumerate(cands)]
    got = dict(optimizer._score_chunk((df, cfg, atr, vr, bounds,
                                       cfg.min_train_trades, cfg.bpy, items)))
    for i, (name, p) in enumerate(cands):
        run = sim.simulate(df, strategies.positions(name, df, p, cfg), cfg, atr, vr)
        want = [optimizer._score(run["ret"], run["trade_bars"], a, b,
                                 cfg.min_train_trades, cfg.bpy)
                for (a, b, _te_a, _te_b) in bounds]
        assert got[i] == pytest.approx(want)


def test_long_history_is_trimmed_to_the_scored_window():
    cfg = _cfg()
    _, rep = optimizer.train_symbol(_df(4000), cfg, None)
    assert rep["warmup_bars"] > 0
    assert rep["bars"] == 4000
    assert rep["sim_bars"] < 4000


def test_trimmed_window_equals_the_same_short_history():
    """Recortar no cambia nada: la simulación ve lo mismo que con la serie corta."""
    long_rep = optimizer.train_symbol(_df(4000), _cfg(), None)[1]
    short_df = _df(4000).iloc[long_rep["warmup_bars"]:]
    short_rep = optimizer.train_symbol(short_df, _cfg(), None)[1]
    assert short_rep["sim_bars"] == long_rep["sim_bars"]
    assert short_rep["wf_oos"] == long_rep["wf_oos"]
