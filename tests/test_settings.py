"""Configuración editable desde el panel: registro, validación, .env y aplicación."""
import threading

import pytest

from app import settings
from app.config import Config


@pytest.fixture
def cfg():
    return Config()


class FakeFeed:
    def __init__(self):
        self.added = []

    def add_symbol(self, symbol):
        self.added.append(symbol)


class FakeEngine:
    def __init__(self, positions=None):
        self.lock = threading.RLock()
        self.S = {"positions": positions or {}, "prev_desired": {}, "cooldown": {}}
        self.feed = FakeFeed()
        self.wake_ev = threading.Event()
        self.events = []
        self.train_mode = "scheduled"
        self.retrains = 0

    def _event(self, typ, data):
        self.events.append((typ, data))

    def request_retrain(self):
        self.retrains += 1

    def set_train_mode(self, mode):
        self.train_mode = mode


# --------------------------------------------------------------------- registro
def test_los_secretos_no_son_editables():
    envs = {f["env"] for f in settings.FIELDS}
    assert not envs & set(settings.SECRET_ENV)
    assert "API_TOKEN" not in envs and "LIVE_CONFIRM" not in envs


def test_cada_parametro_mapea_un_atributo_real(cfg):
    for f in settings.FIELDS:
        assert hasattr(cfg, f["attr"]), f["env"]
        assert f["kind"] in ("bool", "int", "float", "text", "choice", "csv_list", "csv_grid")


def test_ida_y_vuelta_de_todos_los_tipos(cfg):
    """format_value -> parse debe devolver exactamente el valor de la Config."""
    for f in settings.FIELDS:
        value = getattr(cfg, f["attr"])
        assert settings.parse(f, settings.format_value(f, value)) == value, f["env"]


def test_fields_devuelve_texto_legible(cfg):
    by_env = {f["env"]: f for f in settings.fields(cfg)}
    assert by_env["SYMBOLS"]["value"] == "BTC/USDT,ETH/USDT"
    assert by_env["NATIVE_STOPS"]["value"] == "true"
    assert by_env["MODE"]["kind"] == "choice"
    assert by_env["MODE"]["hot"] is False
    assert by_env["RISK_PER_TRADE_PCT"]["hot"] is True


def test_bool_acepta_varias_formas():
    f = settings.REGISTRY["NATIVE_STOPS"]
    assert settings.parse(f, "si") is True
    assert settings.parse(f, "0") is False
    with pytest.raises(ValueError):
        settings.parse(f, "quizas")


# -------------------------------------------------------------------- validación
def test_parametro_desconocido(cfg):
    with pytest.raises(ValueError, match="desconocido"):
        settings.preview(cfg, {"NO_EXISTE": "1"})


def test_numero_invalido(cfg):
    with pytest.raises(ValueError, match="entero"):
        settings.preview(cfg, {"MAX_OPEN_POSITIONS": "dos"})


def test_choice_invalida(cfg):
    with pytest.raises(ValueError, match="opciones"):
        settings.preview(cfg, {"DATA_SOURCE": "magia"})


def test_cruce_de_ventanas_se_valida(cfg):
    # bajar la historia por debajo de lo que necesita el walk-forward debe fallar
    with pytest.raises(ValueError, match="HISTORY_BARS"):
        settings.preview(cfg, {"HISTORY_BARS": "500"})


def test_modo_continuo_exige_espera_minima(cfg):
    # con cooldown 0 reentrenaba en cada ciclo (1.279 entrenos, mediana de 4 s)
    with pytest.raises(ValueError, match="TRAIN_COOLDOWN_SECONDS"):
        settings.preview(cfg, {"TRAIN_MODE": "continuous", "TRAIN_COOLDOWN_SECONDS": "0"})
    settings.preview(cfg, {"TRAIN_MODE": "continuous", "TRAIN_COOLDOWN_SECONDS": "120"})


def test_varios_errores_a_la_vez(cfg):
    with pytest.raises(ValueError) as ex:
        settings.preview(cfg, {"STOP_ATR_MULT": "0", "WF_FOLDS": "muchos"})
    msg = str(ex.value)
    assert "Folds del walk-forward" in msg and "STOP_ATR_MULT" in msg


# --------------------------------------------------------------------------- .env
def test_guarda_y_aplica_en_caliente(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = FakeEngine()
    res = settings.update(eng, cfg, {"RISK_PER_TRADE_PCT": "1.5", "MAX_OPEN_POSITIONS": "2"})
    assert cfg.risk_per_trade_pct == 1.5 and cfg.max_open_positions == 2
    assert set(res["applied"]) == {"RISK_PER_TRADE_PCT", "MAX_OPEN_POSITIONS"}
    assert res["restart_required"] == []
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RISK_PER_TRADE_PCT=1.5" in text and "MAX_OPEN_POSITIONS=2" in text
    assert eng.events and eng.events[-1][0] == "config"


def test_conserva_comentarios_y_secretos(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "# config real\nEXCHANGE_API_KEY=secreto123\nRISK_PER_TRADE_PCT=0.50\n", encoding="utf-8")
    settings.update(FakeEngine(), cfg, {"RISK_PER_TRADE_PCT": "0.75"})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "# config real" in text
    assert "EXCHANGE_API_KEY=secreto123" in text          # el secreto no se toca
    assert "RISK_PER_TRADE_PCT=0.75" in text
    assert "RISK_PER_TRADE_PCT=0.50" in (tmp_path / ".env.bak").read_text(encoding="utf-8")


def test_clave_ausente_se_anade_al_final(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("# vacia\n", encoding="utf-8")
    settings.update(FakeEngine(), cfg, {"POLL_SECONDS": "30"})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "# vacia" in text and "POLL_SECONDS=30" in text


def test_un_solo_bloque_del_panel(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings.update(FakeEngine(), cfg, {"POLL_SECONDS": "30"})
    settings.update(FakeEngine(), cfg, {"MAX_OPEN_POSITIONS": "2"})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text.count("# --- editado desde el panel web") == 1
    assert "POLL_SECONDS=30" in text and "MAX_OPEN_POSITIONS=2" in text


def test_lo_que_requiere_reinicio_solo_se_guarda(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = settings.update(FakeEngine(), cfg, {"API_PORT": "9999", "POLL_SECONDS": "30"})
    assert res["restart_required"] == ["API_PORT"]
    assert cfg.api_port == 8080           # no se aplica en caliente
    assert cfg.poll_seconds == 30
    assert "API_PORT=9999" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_un_valor_invalido_no_escribe_nada(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        settings.update(FakeEngine(), cfg, {"STOP_ATR_MULT": "0"})
    assert not (tmp_path / ".env").exists()
    assert cfg.stop_atr_mult == 2.0


def test_sin_cambios_es_error(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="cambios"):
        settings.update(FakeEngine(), cfg, {})


# ----------------------------------------------------------------------- símbolos
def test_anadir_simbolo_en_caliente(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = FakeEngine()
    res = settings.update(eng, cfg, {"SYMBOLS": "BTC/USDT,ETH/USDT,SOL/USDT"})
    assert "SOL/USDT" in cfg.symbols
    assert eng.feed.added == ["SOL/USDT"]
    assert eng.retrains == 1               # el símbolo nuevo necesita campeón
    assert "SYMBOLS" in res["applied"]


def test_simbolo_nuevo_sin_mercado_no_se_aplica(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class NoMarket(FakeFeed):
        def add_symbol(self, symbol):
            raise ValueError(f"{symbol} no existe en binance")

    eng = FakeEngine()
    eng.feed = NoMarket()
    with pytest.raises(ValueError, match="no existe"):
        settings.update(eng, cfg, {"SYMBOLS": "BTC/USDT,ETH/USDT,XX/USDT"})
    assert cfg.symbols == ["BTC/USDT", "ETH/USDT"]
    assert not (tmp_path / ".env").exists()


def test_no_se_puede_quitar_un_simbolo_con_posicion(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = FakeEngine(positions={"ETH/USDT": {"side": 1, "qty": 1.0, "entry": 100.0}})
    with pytest.raises(ValueError, match="posición abierta"):
        settings.update(eng, cfg, {"SYMBOLS": "BTC/USDT"})
    assert cfg.symbols == ["BTC/USDT", "ETH/USDT"]
    assert not (tmp_path / ".env").exists()


def test_quitar_simbolo_sin_posicion_limpia_estado(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = FakeEngine()
    eng.S["prev_desired"]["ETH/USDT"] = 1
    eng.S["cooldown"]["ETH/USDT"] = 123
    settings.update(eng, cfg, {"SYMBOLS": "BTC/USDT"})
    assert cfg.symbols == ["BTC/USDT"]
    assert "ETH/USDT" not in eng.S["prev_desired"] and "ETH/USDT" not in eng.S["cooldown"]


def test_simbolo_mal_escrito(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="símbolo inválido"):
        settings.update(FakeEngine(), cfg, {"SYMBOLS": "BTCUSDT"})


# -------------------------------------------------------------------- caliente
def test_train_mode_cambia_el_motor(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = FakeEngine()
    settings.update(eng, cfg, {"TRAIN_MODE": "continuous"})
    assert eng.train_mode == "continuous" and cfg.train_mode == "continuous"


def test_watchdog_es_caliente_pero_su_activacion_no(cfg):
    assert settings.REGISTRY["WATCHDOG_STALL_SECONDS"]["hot"] is True
    assert settings.REGISTRY["WATCHDOG_ENABLED"]["hot"] is False


def test_modo_es_peligroso_y_exige_reinicio(cfg):
    mod = settings.REGISTRY["MODE"]
    assert mod["hot"] is False and mod["danger"] is True
    # sin LIVE_CONFIRM en la configuración actual, pasar a live se rechaza
    with pytest.raises(ValueError, match="LIVE_CONFIRM"):
        settings.preview(cfg, {"MODE": "live"})
