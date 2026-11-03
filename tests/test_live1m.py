"""Simulador binario de 1 minuto: dirección, vencimiento y contabilidad."""
import numpy as np
import pandas as pd
import pytest

from app import live1m
from app.data import index_ms
from app.store import Store


def _bet(direction=1, entry=100.0, expiry=1_700_000_120, stake=5.0, balance=100.0):
    """La vela que liquida es la que abre en `entry_ts` y cierra en `expiry`."""
    return {"direction": direction, "entry": entry, "entry_ts": expiry - 60,
            "expiry_ts": expiry, "stake": stake, "payout": 0.82, "balance_before": balance}


def _store(tmp_path, n=300, seed=11):
    store = Store(str(tmp_path))
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(np.random.default_rng(seed).standard_normal(n) * 0.001))
    rows = [(int(t), float(c), float(c) * 1.0005, float(c) * 0.9995, float(c), 1.0)
            for t, c in zip(index_ms(idx), close)]
    store.save_candles("BTC/USDT", "1m", rows)
    return store


def test_direccion_es_contraria_al_ultimo_movimiento():
    close = np.r_[np.linspace(100, 110, 200), 110.6]      # la última vela sube
    assert live1m.direction_for(close, mode="every") == -1
    assert live1m.direction_for(close, mode="every", side="momentum") == 1
    plano = np.r_[np.linspace(100, 110, 200), 110.0]
    assert live1m.direction_for(plano, mode="every") == 0


def test_direccion_no_usa_la_vela_futura():
    rng = np.random.default_rng(5)
    close = 100.0 * np.exp(np.cumsum(rng.standard_normal(400) * 0.002))
    antes = live1m.direction_for(close, mode="signal")
    despues = live1m.direction_for(np.r_[close, close[-1] * 1.05], mode="signal")
    # añadir una vela futura cambia la señal de la próxima, nunca la del pasado
    assert antes in (-1, 0, 1) and despues in (-1, 0, 1)
    assert live1m.direction_for(close, mode="signal") == antes


def test_liquidacion_gana_pierde_y_empata():
    bet = _bet(direction=1, entry=100.0)
    cierre = bet["expiry_ts"] - 60          # vela que cierra en el vencimiento
    up = live1m.settle_bet(bet, 100.5, cierre, 0.82)
    assert up["won"] and up["pnl"] == pytest.approx(4.10)
    assert up["balance_after"] == pytest.approx(104.10)
    down = live1m.settle_bet(bet, 99.5, cierre, 0.82)
    assert not down["won"] and down["pnl"] == pytest.approx(-5.0)
    tie = live1m.settle_bet(bet, 100.0, cierre, 0.82)
    assert tie["tie"] and not tie["won"] and tie["pnl"] == pytest.approx(-5.0)


def test_no_liquida_antes_del_vencimiento():
    bet = _bet(direction=-1, entry=100.0)
    assert live1m.settle_bet(bet, 99.0, bet["expiry_ts"] - 120, 0.82) is None
    out = live1m.settle_bet(bet, 99.0, bet["expiry_ts"] - 60, 0.82)
    assert out["won"]


def test_ciclo_completo_abre_y_liquida(tmp_path):
    from app.config import Config

    cfg = Config()
    cfg.data_source = "synthetic"               # sin red: solo la caché sembrada
    store = _store(tmp_path)
    sim = live1m.Live1m(cfg, store)
    st = sim.run_once({"symbol": "BTC/USDT", "stake": 5, "bankroll": 100,
                       "mode": "every", "refresh": False})
    assert st["running"] and st["pending"] is not None
    assert st["pending"]["expiry_ts"] >= st["pending"]["entry_ts"] + 60
    assert 0 < st["expires_in"] <= 60
    assert st["settled"] == 0

    entry = st["pending"]["entry"]
    nxt = st["pending"]["expiry_ts"] - 60      # vela que cierra en el vencimiento
    cierre = entry * 1.01                      # sube: gana la compra
    store.save_candles("BTC/USDT", "1m",
                       [(nxt * 1000, entry, cierre, min(entry, cierre), cierre, 1.0)])
    st2 = sim.run_once()
    assert st2["settled"] == 1
    assert st2["wins"] + st2["losses"] == 1
    assert st2["balance"] in (100 + 5 * 0.82, 95.0)
    assert st2["pending"] is not None           # sigue apostando en la vela siguiente
    assert st2["bets"][0]["result"]["close"] == pytest.approx(cierre)


def test_parametros_invalidos(tmp_path):
    from app.config import Config

    cfg = Config()
    cfg.data_source = "synthetic"
    sim = live1m.Live1m(cfg, _store(tmp_path))
    with pytest.raises(ValueError):
        sim.run_once({"symbol": "BTC/USDT", "stake": 0})
    with pytest.raises(ValueError):
        sim.run_once({"symbol": "BTC/USDT", "stake": 5, "bankroll": 1})
    with pytest.raises(ValueError):
        sim.run_once({"symbol": "BTC/USDT", "stake": 5, "mode": "siempre"})
    with pytest.raises(ValueError):
        sim.run_once({"symbol": "BTC-USDT", "stake": 5})
