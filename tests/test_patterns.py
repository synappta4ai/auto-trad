"""Patrones de vela y tendencia: detección, marcador, selección y anti look-ahead."""
import numpy as np
import pytest

from app import patterns


def _ohlc(rows):
    a = np.asarray(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]      # open, high, low, close


def _random_close(n=12000, seed=5, vol=0.004):
    rng = np.random.default_rng(seed)
    return 100.0 * np.exp(np.cumsum(rng.standard_normal(n) * vol))


# ------------------------------------------------------------- detección
def test_envuelve_bajista_marca_la_vela_que_cierra():
    o, h, l, c = _ohlc([(100, 103, 99, 102),        # verde
                        (103, 104, 98, 99),         # roja que la engulle
                        (99, 101, 97, 100)])
    assert patterns.engulfing_bearish(o, h, l, c).tolist() == [0, -1, 0]
    assert patterns.engulfing_bullish(o, h, l, c).tolist() == [0, 0, 0]


def test_envuelve_alcista_marca_la_vela_que_cierra():
    o, h, l, c = _ohlc([(102, 104, 101, 100),       # roja
                        (99, 104, 98, 103),         # verde que la engulle
                        (103, 105, 102, 104)])
    assert patterns.engulfing_bullish(o, h, l, c).tolist() == [0, 1, 0]
    assert patterns.engulfing_bearish(o, h, l, c).tolist() == [0, 0, 0]


def test_pin_bar_se_distingue_de_una_vela_normal():
    neutra = (100, 103, 99, 102.5)                  # cuerpo grande, sombras cortas
    o, h, l, c = _ohlc([neutra, (100, 104, 99.5, 101), neutra])   # mecha superior
    assert patterns.pin_bar_sell(o, h, l, c).tolist() == [0, -1, 0]
    assert patterns.pin_bar_buy(o, h, l, c).tolist() == [0, 0, 0]

    o, h, l, c = _ohlc([neutra, (101, 101.5, 98, 100), neutra])    # mecha inferior
    assert patterns.pin_bar_buy(o, h, l, c).tolist() == [0, 1, 0]
    assert patterns.pin_bar_sell(o, h, l, c).tolist() == [0, 0, 0]


def test_tres_velas_se_acuerdan_en_la_tercera():
    verdes = [(100, 101, 99, 101), (101, 102, 100, 102), (102, 103, 101, 103)]
    rojas = [(103, 104, 101, 102), (102, 103, 100, 101), (101, 102, 99, 100)]
    assert patterns.three_green(*_ohlc(verdes)).tolist() == [0, 0, -1]
    assert patterns.three_red(*_ohlc(rojas)).tolist() == [0, 0, +1]
    # una racha rota no vale
    mixta = [(100, 101, 99, 101), (101, 102, 100, 101), (101, 102, 100, 103)]
    assert patterns.three_green(*_ohlc(mixta)).tolist() == [0, 0, 0]


def test_registry_cubre_seis_patrones_y_alineados():
    o, h, l, c = _ohlc([(100, 103, 99, 102), (103, 104, 98, 99), (99, 101, 97, 100)] * 40)
    sigs = patterns.pattern_signals(o, h, l, c)
    assert set(sigs) == set(patterns.PATTERNS)
    assert all(len(s) == len(c) for s in sigs.values())
    assert set(np.unique(np.concatenate(list(sigs.values())))) <= {-1, 0, 1}


def test_pattern_signals_rechaza_longitudes_distintas():
    o, h, l, c = _ohlc([(100, 103, 99, 102)] * 5)
    with pytest.raises(ValueError, match="misma longitud"):
        patterns.pattern_signals(o, h, l[:-1], c)


# ------------------------------------------------------------- tendencia
def test_sma_es_la_media_que_dice():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    s = patterns.sma(x, 3)
    assert np.isnan(s[0]) and np.isnan(s[1])
    assert s[2] == pytest.approx(2.0)
    assert s[3] == pytest.approx(3.0)
    assert s[4] == pytest.approx(4.0)


def test_trend_direction_sigue_el_cruce():
    up = np.linspace(100, 200, 200)
    down = np.linspace(200, 100, 200)
    assert patterns.trend_direction(up, fast=5, slow=20)[-1] == 1
    assert patterns.trend_direction(down, fast=5, slow=20)[-1] == -1
    # sin velas suficientes no inventa dirección
    assert patterns.trend_direction(np.linspace(100, 200, 10), 5, 20)[-1] == 0


def test_trend_direction_sin_datos_es_cero_en_toda_la_serie():
    out = patterns.trend_direction(np.linspace(100, 200, 30), fast=10, slow=60)
    assert set(np.unique(out)) == {0}


def test_gate_with_trend_solo_deja_coincidir():
    sig = np.array([1, -1, 1, -1], dtype=np.int8)
    trend = np.array([1, 1, -1, -1], dtype=np.int8)
    out = patterns.gate_with_trend({"x": sig}, trend)["x@trend"]
    assert out.tolist() == [1, 0, 0, -1]


# ------------------------------------------------------- múltiples pruebas
def test_bh_ordena_y_acota():
    p = [0.001, 0.01, 0.02, 0.5, 0.8]
    reject, q = patterns.benjamini_hochberg(p, alpha=0.05)
    assert reject == [True, True, True, False, False]
    assert q[0] == pytest.approx(0.005)
    assert q[1] == pytest.approx(0.025)
    assert q[2] == pytest.approx(0.033333, abs=1e-4)
    # los q ajustados nunca bajan de su p-valor y quedan entre 0 y 1
    assert all(qi >= pi for qi, pi in zip(q, p))
    assert all(0.0 <= qi <= 1.0 for qi in q)
    assert patterns.benjamini_hochberg([]) == ([], [])


def test_bh_no_rechaza_si_todo_es_ruido():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.2, 0.9, size=30).tolist()
    reject, q = patterns.benjamini_hochberg(p, alpha=0.05)
    assert not any(reject)
    assert all(qi >= 0.2 for qi in q)


def test_p_value_cae_con_mas_aciertos():
    assert patterns._p_above(700, 1000, 0.5495) < 0.001
    assert patterns._p_above(550, 1000, 0.5495) == pytest.approx(0.5, abs=0.01)
    assert patterns._p_above(0, 100, 0.50) == pytest.approx(1.0, abs=1e-6)
    assert patterns._p_above(10, 0, 0.5) == 1.0


# -------------------------------------------------------------- marcador
def test_wins_no_mira_mas_alla_del_tramo():
    close = np.array([100.0, 99.0, 98.0, 97.0, 96.0])
    sig = np.full(5, -1, dtype=np.int8)
    # tramo [0, 3) con horizon 1: entran 0, 1 y 2 (vencen en 1, 2 y 3)
    w = patterns._wins(sig, 0, 3, close, 1)
    assert w.size == 3 and w.all()
    # horizon 2: entran 0 y 1 (vencen en 2 y 3); el 2 vencería en 4 -> fuera
    w2 = patterns._wins(sig, 0, 3, close, 2)
    assert w2.size == 2 and w2.all()
    # empate cuenta como pérdida, como en un bróker real
    tie = np.array([100.0, 100.0])
    assert patterns._wins(np.array([1], dtype=np.int8), 0, 1, tie, 1).tolist() == [False]


def test_evaluate_rules_detecta_senal_plantada():
    """El marcador acierta lo que le dicen: sirve para validar el recuento."""
    close = _random_close(12000, seed=5)
    sig = np.zeros(len(close), dtype=np.int8)
    sig[:-1] = np.where(close[1:] < close[:-1], -1, 0).astype(np.int8)
    res = patterns.evaluate_rules(close, {"plantada": sig}, train_bars=3000,
                                  test_bars=1500, folds=4, payout=0.82)
    rule = res["rules"][0]
    assert res["folds_used"] == 4
    assert rule["bets"] > 500
    assert rule["hit_pct"] > 99.0
    assert rule["verdict"] == "ventaja"
    assert rule["ev_per_bet"] > 0
    assert len(rule["folds"]) == 4


def test_serie_aleatoria_no_inventa_ventaja():
    close = _random_close(15000, seed=11)
    rng = np.random.default_rng(3)
    sig = np.where(rng.random(len(close)) < 0.5, 1, -1).astype(np.int8)
    res = patterns.evaluate_rules(close, {"ruido": sig}, train_bars=4000,
                                  test_bars=2500, folds=3, payout=0.82)
    rule = res["rules"][0]
    assert rule["bets"] > 1000
    assert 45.0 < rule["hit_pct"] < 55.0
    assert rule["verdict"] == "sin_ventaja"
    assert res["verdict"] == "sin_ventaja"
    assert res["passed"] == []


def test_con_la_moneda_no_supera_el_umbral():
    """Con acierto real 50 % el IC entero queda por debajo de 54,95 %.

    Aquí no hace falta que el intervalo contenga el umbral: con ~7.000 apuestas
    una moneda se descarta de forma rotunda, y ese es el resultado correcto.
    """
    close = _random_close(15000, seed=21)
    rng = np.random.default_rng(7)
    sig = np.where(rng.random(len(close)) < 0.5, 1, -1).astype(np.int8)
    res = patterns.evaluate_rules(close, {"moneda": sig}, train_bars=4000,
                                  test_bars=2500, folds=3, payout=0.82)
    rule = res["rules"][0]
    assert rule["hit_pct"] == pytest.approx(50.0, abs=3.0)
    assert rule["ci_high_pct"] < res["breakeven_pct"]
    assert rule["verdict"] == "sin_ventaja"


def test_seleccion_en_train_y_test_honesto():
    """La regla elegida es la del train; su test es el que se informa."""
    close = _random_close(16000, seed=9)
    perfecta = np.zeros(len(close), dtype=np.int8)
    perfecta[:-1] = np.where(close[1:] < close[:-1], -1, 0).astype(np.int8)
    rng = np.random.default_rng(4)
    ruido = np.where(rng.random(len(close)) < 0.5, 1, -1).astype(np.int8)
    res = patterns.evaluate_rules(close, {"buena": perfecta, "mala": ruido},
                                  train_bars=4000, test_bars=2000, folds=4, payout=0.82)
    assert res["selected"]["name"] == "buena"
    assert res["selected"]["test_hit_pct"] > 99.0
    # el orden de presentación es el de entrada, no el de los resultados
    assert [r["name"] for r in res["rules"]] == ["buena", "mala"]


def test_fdr_cubre_todas_las_celdas():
    """Todas las celdas con apuestas llevan q-valor y bandera de rechazo."""
    close = _random_close(14000, seed=13)
    rng = np.random.default_rng(2)
    sigs = {f"r{i}": np.where(rng.random(len(close)) < 0.5, 1, -1).astype(np.int8)
            for i in range(6)}
    res = patterns.evaluate_rules(close, sigs, train_bars=3500, test_bars=2000,
                                  folds=4, payout=0.82)
    assert res["tests"] == 6
    for r in res["rules"]:
        assert "q_value" in r and "fdr_reject" in r
        assert 0.0 <= r["q_value"] <= 1.0
    assert res["verdict"] == "sin_ventaja"


def test_ventana_mas_larga_pierde_apuestas_del_borde():
    close = _random_close(12000, seed=3)
    sig = np.ones(len(close), dtype=np.int8)
    kw = dict(train_bars=3000, test_bars=2000, folds=3, payout=0.82)
    h1 = patterns.evaluate_rules(close, {"x": sig}, horizon=1, **kw)
    h4 = patterns.evaluate_rules(close, {"x": sig}, horizon=4, **kw)
    assert h4["rules"][0]["bets"] < h1["rules"][0]["bets"]
    assert h4["rules"][0]["bets"] > 0


def test_falla_si_no_hay_velas():
    with pytest.raises(ValueError, match="hacen falta"):
        patterns.evaluate_rules(_random_close(400), {"x": np.ones(400, dtype=np.int8)},
                                train_bars=2000, test_bars=1000, folds=3)


def test_falla_si_faltan_reglas_o_no_coinciden():
    close = _random_close(1000, seed=1)
    with pytest.raises(ValueError, match="sin reglas"):
        patterns.evaluate_rules(close, {}, train_bars=300, test_bars=200, folds=2)
    with pytest.raises(ValueError, match="tiene"):
        patterns.evaluate_rules(close, {"x": np.ones(10, dtype=np.int8)},
                                train_bars=300, test_bars=200, folds=2)


# -------------------------------------------------------------- resumen
def test_resumen_exige_repeticion_entre_simbolos():
    def res(sym, ok):
        return {"symbol": sym, "breakeven_pct": 54.95,
                "rules": [{"name": "p", "bets": 500, "fdr_reject": ok,
                           "ci_low_pct": 56.0 if ok else 50.0}]}

    dos = patterns.summarize_rules([res("BTC/USDT", True), res("ETH/USDT", True),
                                    res("SOL/USDT", False)])
    assert dos["verdict"] == "repetible"
    assert dos["rules"]["p"]["passed_in"] == ["BTC/USDT", "ETH/USDT"]
    assert dos["rules"]["p"]["replicable"] is True

    uno = patterns.summarize_rules([res("BTC/USDT", True), res("ETH/USDT", False)])
    assert uno["verdict"] == "aislado"
    assert uno["rules"]["p"]["replicable"] is False

    ninguno = patterns.summarize_rules([res("BTC/USDT", False)])
    assert ninguno["verdict"] == "sin_ventaja"
    assert patterns.summarize_rules([])["verdict"] == "sin_ventaja"
