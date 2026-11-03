"""Barridos: matriz símbolo×temporalidad, persistencia y veredicto del conjunto."""
import threading

import numpy as np
import pandas as pd
import pytest

from app import sweep
from app.config import Config
from app.data import index_ms
from app.store import Store


def _cfg():
    c = Config()
    c.data_source = "synthetic"
    c.train_window_bars = 150
    c.test_window_bars = 50
    c.wf_folds = 2
    c.history_bars = 500
    c.strategies = ["ema_cross"]
    c.ema_fast = [2, 5]
    c.ema_slow = [10, 20]
    c.min_train_trades = 1
    c.max_candidates = 20
    c.min_oos_trades = 1
    c.min_oos_sharpe = -1e9      # el veredicto se prueba aparte; aquí solo el flujo
    c.require_beat_benchmark = False
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


def _seed(store, symbols, timeframes, n=500):
    for sym in symbols:
        for tf in timeframes:
            df = _df(n, seed=sum(ord(c) for c in sym + tf) % 997 + 1)
            rows = [(int(t), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume))
                    for t, r in zip(index_ms(df.index), df.itertuples())]
            store.save_candles(sym, tf, rows)


def test_barrido_guarda_cada_celda(tmp_path):
    cfg, store = _cfg(), Store(str(tmp_path))
    _seed(store, ["BTC/USDT", "ETH/USDT"], ["1h", "4h"])
    sw = sweep.Sweep(cfg, store)

    out = sw.run({"symbols": ["BTC/USDT", "ETH/USDT"], "timeframes": ["1h", "4h"], "bars": 500})

    assert out["status"] == "done"
    assert len(out["rows"]) == 4
    assert {(r["symbol"], r["timeframe"]) for r in out["rows"]} == {
        ("BTC/USDT", "1h"), ("BTC/USDT", "4h"), ("ETH/USDT", "1h"), ("ETH/USDT", "4h")}
    saved = store.experiments(out["run_id"])
    assert len(saved) == 4
    assert store.latest_run_id() == out["run_id"]
    assert out["summary"]["cells"] == 4
    assert all(r["sharpe"] is not None for r in saved)


def test_celda_sin_velas_no_rompe_el_barrido(tmp_path):
    cfg, store = _cfg(), Store(str(tmp_path))
    _seed(store, ["BTC/USDT"], ["1h"])
    out = sweep.Sweep(cfg, store).run({"symbols": ["BTC/USDT", "SOL/USDT"],
                                       "timeframes": ["1h"], "bars": 500})
    assert out["status"] == "done"
    assert len(out["rows"]) == 2
    missing = [r for r in out["rows"] if r["symbol"] == "SOL/USDT"][0]
    assert missing["decision"] == "datos_insuficientes"
    assert missing["needed"] == 150 + 2 * 50 + 200
    assert out["summary"]["evaluated"] == 1


def test_valida_simbolos_temporalidades_y_tamano(tmp_path):
    sw = sweep.Sweep(_cfg(), Store(str(tmp_path)))
    with pytest.raises(ValueError, match="símbolo inválido"):
        sw._prepare({"symbols": "BTCUSDT", "timeframes": ["1h"]})
    with pytest.raises(ValueError, match="temporalidad inválida"):
        sw._prepare({"symbols": ["BTC/USDT"], "timeframes": ["3x"]})
    with pytest.raises(ValueError, match="misma moneda cotizada"):
        sw._prepare({"symbols": ["BTC/USDT", "ETH/BTC"], "timeframes": ["1h"]})
    with pytest.raises(ValueError, match="al menos 300 velas"):
        sw._prepare({"symbols": ["BTC/USDT"], "timeframes": ["1h"], "bars": 200})


def test_no_lanza_dos_barridos_a_la_vez(tmp_path, monkeypatch):
    sw = sweep.Sweep(_cfg(), Store(str(tmp_path)))
    started, release = threading.Event(), threading.Event()

    def fake_run(self, *a, **k):
        started.set()
        release.wait(5)

    monkeypatch.setattr(sweep.Sweep, "_run", fake_run)
    sw.start({"symbols": ["BTC/USDT"], "timeframes": ["1h"], "bars": 500})
    assert started.wait(5)
    with pytest.raises(RuntimeError, match="ya hay un barrido"):
        sw.start({"symbols": ["BTC/USDT"], "timeframes": ["1h"], "bars": 500})
    release.set()
    sw._thread.join(5)


def test_veredicto_exige_repeticion():
    one = {"symbol": "BTC/USDT", "timeframe": "1h", "sharpe": 1.0, "excess_pct": 5.0,
           "candidates": 10, "gate_fails": []}
    assert sweep.summarize([one])["verdict"] == "aislado"
    assert sweep.summarize([one])["trials"] == 10

    two = dict(one, symbol="ETH/USDT", excess_pct=3.0)
    assert sweep.summarize([one, two])["verdict"] == "repetible"

    fail = dict(one, sharpe=-0.2, excess_pct=-4.0, gate_fails=["sharpe -0.2<0.6"])
    assert sweep.summarize([fail])["verdict"] == "sin_ventaja"
    assert sweep.summarize([fail])["edge"] == 0
    assert sweep.summarize([dict(one, sharpe=None, excess_pct=None)])["verdict"] == "sin_datos"
