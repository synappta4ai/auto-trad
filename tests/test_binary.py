"""Laboratorio binario: umbrales, intervalos y ausencia de look-ahead."""
import math

import numpy as np
import pandas as pd
import pytest

from app import binary


def _series(n=8000, seed=7):
    rng = np.random.default_rng(seed)
    return 100.0 * np.exp(np.cumsum(rng.standard_normal(n) * 0.004))


def _planted(n=8000, step=0.02):
    """Sube y baja alternando: la reversión tras movimiento extremo es segura."""
    close = [100.0]
    for i in range(1, n):
        close.append(close[-1] * math.exp(step if i % 2 else -step))
    return np.array(close)


def test_breakeven_y_valor_esperado():
    assert binary.breakeven(0.85) == pytest.approx(0.5405, abs=1e-4)
    assert binary.breakeven(0.90) == pytest.approx(0.5263, abs=1e-4)
    assert binary.ev_per_bet(0.5582, 0.85) == pytest.approx(0.5582 * 0.85 - 0.4418, abs=1e-9)
    assert binary.ev_per_bet(0.5582, 0.70) < 0
    assert binary.ev_per_bet(0.50, 0.90) == pytest.approx(-0.05, abs=1e-9)


def test_kelly_solo_positivo_con_ventaja():
    assert binary.kelly(0.50, 0.85) == 0.0
    f = binary.kelly(0.60, 0.85)
    assert 0.0 < f < 1.0
    # con p=0.60 y payout 0.85: (0.6*1.85-1)/0.85
    assert f == pytest.approx((0.6 * 1.85 - 1) / 0.85, abs=1e-9)


def test_wilson_contiene_la_proporcion():
    lo, hi = binary.wilson(560, 1000)
    assert lo < 0.56 < hi
    assert lo < hi
    assert binary.wilson(0, 0) == (0.0, 0.0)


def test_apuestas_para_demostrar_crece_al_adelgazar_la_ventaja():
    assert binary.bets_to_prove(0.50, 0.85) is None
    assert binary.bets_to_prove(0.56, 0.85) > binary.bets_to_prove(0.60, 0.85)


def test_walk_forward_detecta_reversion_plantada():
    close = _planted()
    r = binary.walk_forward(close, horizon=1, window=1, quantile=0.90, payout=0.85,
                            train_bars=2000, test_bars=1000, folds=4)
    assert r["hit_pct"] > 99.0
    assert r["verdict"] == "ventaja"
    assert r["ev_per_bet"] > 0
    assert r["max_drawdown_stakes"] == 0.0
    assert len(r["folds"]) == 4


def test_momentum_se_invierte_en_la_misma_serie():
    close = _planted()
    r = binary.walk_forward(close, horizon=1, quantile=0.90, payout=0.85,
                            train_bars=2000, test_bars=1000, folds=4, side="momentum")
    assert r["hit_pct"] < 1.0
    assert r["verdict"] == "sin_ventaja"


def test_sin_look_ahead_la_apuesta_vence_dentro_del_test():
    """Ninguna apuesta puede resolverse con precios de la ventana siguiente."""
    close = _series(12000)
    h = 4
    r = binary.walk_forward(close, horizon=h, quantile=0.80, payout=0.85,
                            train_bars=3000, test_bars=1500, folds=4)
    for f in r["folds"]:
        assert f["last_index"] + h <= f["test_last"]
    # los tramos de test no se solapan y cada train acaba donde empezó el test
    for prev, nxt in zip(r["folds"], r["folds"][1:]):
        assert nxt["test_first"] >= prev["test_last"]
        assert nxt["train_last"] == nxt["test_first"]


def test_serie_aleatoria_no_inventa_ventaja():
    r = binary.walk_forward(_series(12000, seed=11), horizon=1, quantile=0.90,
                            payout=0.85, train_bars=4000, test_bars=2000, folds=3)
    assert 42.0 < r["hit_pct"] < 58.0
    assert r["bets"] > 100
    assert r["verdict"] in {"sin_ventaja", "indeterminado"}


def test_falla_si_no_hay_velas_suficientes():
    with pytest.raises(ValueError, match="hacen falta"):
        binary.walk_forward(_series(500), train_bars=1000, test_bars=500, folds=3)


def test_resumen_exige_repeticion():
    rows = [{"symbol": "BTC/USDT", "bets": 100, "hit_pct": 55.0, "verdict": "ventaja"},
            {"symbol": "ETH/USDT", "bets": 100, "hit_pct": 54.5, "verdict": "ventaja"},
            {"symbol": "SOL/USDT", "bets": 100, "hit_pct": 50.0, "verdict": "sin_ventaja"}]
    assert binary.summarize(rows)["verdict"] == "repetible"
    solo = rows[:1]
    assert binary.summarize(solo)["verdict"] == "aislado"
    assert binary.summarize([])["verdict"] == "sin_datos"


def test_senal_actual_marca_extremo_y_direccion():
    close = _planted(4000)
    sig = binary.latest_signal(close, horizon=1, window=1, quantile=0.50,
                               side="reversion", train_bars=1000)
    assert sig["extreme"] is True
    assert sig["direction"] in (-1, 1)
    assert sig["threshold_pct"] > 0
    mom = binary.latest_signal(close, horizon=1, window=1, quantile=0.50,
                               side="momentum", train_bars=1000)
    assert mom["direction"] == -sig["direction"]


def test_senal_actual_sin_datos():
    assert binary.latest_signal(np.array([100.0, 101.0])) is None


def test_fit_windows_encoge_sin_perder_folds():
    assert binary.fit_windows(44000, 1, 20000, 4000, 5) == (20000, 4000)
    tr, te = binary.fit_windows(20000, 1, 20000, 4000, 5)
    assert tr >= 500 and te >= 200
    assert tr + te * 5 + 1 + 100 <= 20000
    with pytest.raises(ValueError, match="hacen falta"):
        binary.fit_windows(800, 1, 20000, 4000, 5)


def _seeded_store(tmp_path, symbol="BTC/USDT", tf="1h", n=3000):
    from app.store import Store
    from app.data import index_ms

    store = Store(str(tmp_path))
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(np.random.default_rng(3).standard_normal(n) * 0.005))
    rows = [(int(t), float(c), float(c) * 1.002, float(c) * 0.998, float(c), 10.0)
            for t, c in zip(index_ms(idx), close)]
    store.save_candles(symbol, tf, rows)
    return store


def test_laboratorio_por_simbolo(tmp_path):
    from app.config import Config

    cfg = Config()
    cfg.data_source = "synthetic"      # sin red: solo caché
    lab = binary.BinaryLab(cfg, _seeded_store(tmp_path))
    out = lab.run({"symbols": ["BTC/USDT"], "timeframe": "1h", "horizon": 1,
                   "quantile": 0.9, "payout": 0.85, "train": 1000, "test": 500,
                   "folds": 2})
    assert out["status"] == "done"
    row = out["rows"][0]
    assert row["symbol"] == "BTC/USDT"
    assert row["bets"] > 0
    assert row["signal"] is not None
    assert out["summary"]["symbols"] == 1


def test_rechaza_temporalidad_no_soportada(tmp_path):
    """El mínimo es 15m: cualquier otra cosa debe fallar con un mensaje claro."""
    from app.config import Config

    cfg = Config()
    cfg.data_source = "synthetic"
    lab = binary.BinaryLab(cfg, _seeded_store(tmp_path))
    with pytest.raises(ValueError, match="temporalidad no soportada"):
        lab.run({"symbols": ["BTC/USDT"], "timeframe": "1m"})
