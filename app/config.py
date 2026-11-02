"""Configuración de arranque.

`.env` solo trae lo imprescindible para abrir la base (ruta de datos, credenciales
del panel, claves del exchange). El resto de parámetros vive en la tabla
`settings` de SQLite: `settings.load()` los aplica al arrancar y la base manda
sobre el entorno cuando hay conflicto.
"""
import os

LIVE_PHRASE = "I_UNDERSTAND_THE_RISK"


def _load_dotenv():
    """Carga .env del directorio actual sin dependencias externas.

    No sobreescribe variables ya definidas en el entorno (Docker manda).
    """
    if os.getenv("AUTOTRADER_SKIP_DOTENV"):
        return
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv()


def _raw(name):
    v = os.getenv(name)
    return None if v is None or v.strip() == "" else v.strip()


def _s(name, d):
    v = _raw(name)
    return d if v is None else v


def _f(name, d):
    v = _raw(name)
    return float(d) if v is None else float(v)


def _i(name, d):
    v = _raw(name)
    return int(d) if v is None else int(float(v))


def _b(name, d):
    v = _raw(name)
    return bool(d) if v is None else v.lower() in ("1", "true", "yes", "on", "si", "sí")


def _list(name, d):
    return [x.strip() for x in _s(name, d).split(",") if x.strip()]


def _grid(name, d):
    """'8,12,16' -> [8, 12, 16]. Un solo valor = parámetro fijo; varios = el optimizador los prueba."""
    out = []
    for x in _s(name, d).split(","):
        x = x.strip()
        if not x:
            continue
        v = float(x)
        out.append(int(v) if v == int(v) else v)
    return out


class Config:
    def __init__(self):
        # --- Modo y conexión ---
        self.mode = _s("MODE", "paper").lower()  # paper | live
        self.live_confirm = _s("LIVE_CONFIRM", "")
        self.log_level = _s("LOG_LEVEL", "INFO").upper()
        self.data_dir = _s("DATA_DIR", "/data")
        self.api_token = _s("API_TOKEN", "")
        self.panel_user = _s("PANEL_USER", "")          # HTTP Basic del panel (arranque)
        self.panel_password = _s("PANEL_PASSWORD", "")  # contraseña del panel (arranque)
        self.api_bind = _s("API_BIND", "127.0.0.1")
        self.api_port = _i("API_PORT", 8080)
        self.auto_start = _b("AUTO_START", True)   # arranca el motor al iniciar el proceso
        self.retrain_on_start = _b("RETRAIN_ON_START", True)  # walk-forward al pulsar Iniciar/arrancar
        self.data_source = _s("DATA_SOURCE", "synthetic").lower()  # synthetic | exchange
        self.exchange = _s("EXCHANGE", "binance")
        self.exchange_sandbox = _b("EXCHANGE_SANDBOX", True)
        self.api_key = _s("EXCHANGE_API_KEY", "")
        self.api_secret = _s("EXCHANGE_API_SECRET", "")
        self.api_password = _s("EXCHANGE_API_PASSWORD", "")
        # --- Mercado ---
        self.symbols = _list("SYMBOLS", "BTC/USDT,ETH/USDT")
        self.timeframe = _s("TIMEFRAME", "1h")
        self.history_bars = _i("HISTORY_BARS", 3000)
        self.poll_seconds = _i("POLL_SECONDS", 20)
        self.bars_per_year = _f("BARS_PER_YEAR", 0)  # 0 = automático (mercado 24/7)
        self.synthetic_seed = _i("SYNTHETIC_SEED", 7)
        self.synthetic_bars_per_poll = _i("SYNTHETIC_BARS_PER_POLL", 1)
        # --- Capital y costos ---
        self.initial_capital = _f("INITIAL_CAPITAL", 1000)
        self.fee_pct = _f("FEE_PCT", 0.10)
        self.slippage_pct = _f("SLIPPAGE_PCT", 0.05)
        self.spread_pct = _f("SPREAD_PCT", 0.02)
        self.allow_short = _b("ALLOW_SHORT", False)
        # --- Estrategias (listas = el optimizador prueba todas las combinaciones) ---
        self.strategies = _list("STRATEGIES", "ema_cross,rsi_reversion,breakout,ml_logit")
        self.fixed_strategy = _s("FIXED_STRATEGY", "")  # solo si LEARNING_ENABLED=false
        self.ema_fast = _grid("EMA_FAST", "8,12,20")
        self.ema_slow = _grid("EMA_SLOW", "30,50,100")
        self.rsi_period = _grid("RSI_PERIOD", "14")
        self.rsi_low = _grid("RSI_LOW", "25,30")
        self.rsi_high = _grid("RSI_HIGH", "70,75")
        self.rsi_exit = _grid("RSI_EXIT", "50")
        self.breakout_lookback = _grid("BREAKOUT_LOOKBACK", "20,40,80")
        self.breakout_exit_ratio = _grid("BREAKOUT_EXIT_RATIO", "0.5")
        self.ml_train_bars = _grid("ML_TRAIN_BARS", "600,1000")
        self.ml_threshold = _grid("ML_THRESHOLD", "0.03,0.06")
        self.ml_horizon = _i("ML_HORIZON", 4)
        self.ml_refit_bars = _i("ML_REFIT_BARS", 50)
        self.ml_l2 = _f("ML_L2", 0.05)
        # --- Riesgo / topes ---
        self.risk_per_trade_pct = _f("RISK_PER_TRADE_PCT", 0.5)
        self.max_position_pct = _f("MAX_POSITION_PCT", 25)
        self.max_total_exposure_pct = _f("MAX_TOTAL_EXPOSURE_PCT", 60)
        self.max_open_positions = _i("MAX_OPEN_POSITIONS", 3)
        self.atr_period = _i("ATR_PERIOD", 14)
        self.stop_atr_mult = _f("STOP_ATR_MULT", 2.0)
        self.take_profit_rr = _f("TAKE_PROFIT_RR", 0)  # 0 = sin take profit (sale por señal)
        self.max_daily_loss_pct = _f("MAX_DAILY_LOSS_PCT", 3)
        self.max_weekly_loss_pct = _f("MAX_WEEKLY_LOSS_PCT", 6)
        self.max_drawdown_pct = _f("MAX_DRAWDOWN_PCT", 15)
        self.max_trades_per_day = _i("MAX_TRADES_PER_DAY", 10)
        self.cooldown_bars = _i("COOLDOWN_BARS", 2)
        self.reentry_requires_reset = _b("REENTRY_AFTER_STOP_REQUIRES_RESET", True)
        self.enter_on_start = _b("ENTER_ON_START", False)
        self.min_notional = _f("MIN_NOTIONAL", 10)
        self.kill_switch_close_positions = _b("KILL_SWITCH_CLOSE_POSITIONS", True)
        # --- Protección en vivo y vigilancia ---
        self.native_stops = _b("NATIVE_STOPS", True)          # stop en el exchange, no solo en el proceso
        self.reconcile_on_start = _b("RECONCILE_ON_START", True)
        self.client_id_prefix = _s("CLIENT_ID_PREFIX", "at")
        self.watchdog_enabled = _b("WATCHDOG_ENABLED", True)
        self.watchdog_interval_seconds = _f("WATCHDOG_INTERVAL_SECONDS", 30)
        self.watchdog_stall_seconds = _f("WATCHDOG_STALL_SECONDS", 600)
        self.watchdog_flatten = _b("WATCHDOG_FLATTEN", False)   # false = solo avisa
        # --- Aprendizaje (walk-forward + champion/challenger) ---
        self.learning_enabled = _b("LEARNING_ENABLED", True)
        self.retrain_interval_hours = _f("RETRAIN_INTERVAL_HOURS", 168)
        self.train_mode = _s("TRAIN_MODE", "scheduled").lower()   # scheduled | continuous
        self.train_cooldown_seconds = _f("TRAIN_COOLDOWN_SECONDS", 300)
        self.train_min_new_bars = _i("TRAIN_MIN_NEW_BARS", 1)
        self.train_window_bars = _i("TRAIN_WINDOW_BARS", 1500)
        self.test_window_bars = _i("TEST_WINDOW_BARS", 300)
        self.wf_folds = _i("WF_FOLDS", 3)
        self.min_oos_trades = _i("MIN_OOS_TRADES", 8)
        self.min_oos_sharpe = _f("MIN_OOS_SHARPE", 0.5)
        self.min_oos_profit_factor = _f("MIN_OOS_PROFIT_FACTOR", 1.1)
        self.max_oos_drawdown_pct = _f("MAX_OOS_DRAWDOWN_PCT", 20)
        self.promote_margin_pct = _f("PROMOTE_MARGIN_PCT", 10)
        self.demote_on_fail = _b("DEMOTE_ON_FAIL", True)
        self.max_candidates = _i("MAX_CANDIDATES", 400)
        # núcleos para repartir los candidatos del walk-forward (0 = automático, 1 = serie)
        self.parallel_workers = _i("PARALLEL_WORKERS", 0)
        # combinaciones mínimas por fold en la ventana de entrenamiento
        self.min_train_trades = _i("MIN_TRAIN_TRADES", 5)
        # separación entre train y test para evitar etiquetas solapadas (0 = ML_HORIZON)
        self.embargo_bars = _i("EMBARGO_BARS", 0)
        # exige además superar a comprar y mantener el activo
        self.require_beat_benchmark = _b("REQUIRE_BEAT_BENCHMARK", False)
        # --- Autoajuste: deriva y riesgo ---
        self.retrain_on_drift = _b("RETRAIN_ON_DRIFT", True)
        self.drift_vol_ratio = _f("DRIFT_VOL_RATIO", 2.0)
        self.drift_short_bars = _i("DRIFT_SHORT_BARS", 24)
        self.drift_long_bars = _i("DRIFT_LONG_BARS", 240)
        self.drift_loss_streak = _i("DRIFT_LOSS_STREAK", 5)
        self.drift_min_hours = _f("DRIFT_MIN_HOURS", 12)
        self.vol_scaling_enabled = _b("VOL_SCALING_ENABLED", True)
        self.derisk_enabled = _b("DERISK_ENABLED", True)
        self.derisk_start_dd_pct = _f("DERISK_START_DD_PCT", 5)
        self.derisk_min_mult = _f("DERISK_MIN_MULT", 0.25)
        self.risk_mult_min = _f("RISK_MULT_MIN", 0.25)
        self.risk_mult_max = _f("RISK_MULT_MAX", 1.0)
        self.equity_log_seconds = _i("EQUITY_LOG_SECONDS", 300)
        self.validate()

    # costo por lado (fracción) usado en backtest y paper
    @property
    def cost_side(self):
        return (self.fee_pct + self.slippage_pct + self.spread_pct / 2.0) / 100.0

    @property
    def tf_seconds(self):
        from .data import tf_seconds
        return tf_seconds(self.timeframe)

    @property
    def bpy(self):
        return self.bars_per_year if self.bars_per_year > 0 else 365 * 24 * 3600 / self.tf_seconds

    @property
    def embargo(self):
        """Velas de separación entre la ventana de train y la de test."""
        return self.embargo_bars if self.embargo_bars > 0 else int(self.ml_horizon)

    def validate(self):
        e = []
        if self.mode not in ("paper", "live"):
            e.append("MODE debe ser paper o live")
        if self.data_source not in ("synthetic", "exchange"):
            e.append("DATA_SOURCE debe ser synthetic o exchange")
        if self.train_mode not in ("scheduled", "continuous"):
            e.append("TRAIN_MODE debe ser scheduled o continuous")
        if self.train_cooldown_seconds < 0:
            e.append("TRAIN_COOLDOWN_SECONDS debe ser >= 0")
        if self.train_min_new_bars < 0:
            e.append("TRAIN_MIN_NEW_BARS debe ser >= 0")
        if self.train_mode == "continuous" and self.train_cooldown_seconds < 60:
            e.append("TRAIN_MODE=continuous exige TRAIN_COOLDOWN_SECONDS >= 60 "
                     "(con menos, reentrena en cada ciclo y se come la CPU)")
        if not self.symbols:
            e.append("SYMBOLS vacío")
        if len({s.split("/")[-1] for s in self.symbols}) > 1:
            e.append("Todos los SYMBOLS deben compartir la misma moneda cotizada (ej. /USDT)")
        known = {"ema_cross", "rsi_reversion", "breakout", "ml_logit"}
        bad = [s for s in self.strategies if s not in known]
        if bad or not self.strategies:
            e.append(f"STRATEGIES inválido {bad}; disponibles: {sorted(known)}")
        if self.min_train_trades < 1:
            e.append("MIN_TRAIN_TRADES debe ser >= 1")
        if self.parallel_workers < 0:
            e.append("PARALLEL_WORKERS debe ser >= 0 (0 = automático, 1 = en serie)")
        if self.embargo_bars < 0:
            e.append("EMBARGO_BARS debe ser >= 0")
        if not self.client_id_prefix or len(self.client_id_prefix) > 8:
            e.append("CLIENT_ID_PREFIX debe tener 1-8 caracteres")
        elif not all(c.isalnum() or c in "-_." for c in self.client_id_prefix):
            e.append("CLIENT_ID_PREFIX solo admite letras, números, '-', '_' y '.'")
        if self.watchdog_interval_seconds <= 0:
            e.append("WATCHDOG_INTERVAL_SECONDS debe ser > 0")
        if self.watchdog_stall_seconds <= self.poll_seconds:
            e.append("WATCHDOG_STALL_SECONDS debe ser mayor que POLL_SECONDS")
        if self.train_window_bars - self.embargo < 100:
            e.append("TRAIN_WINDOW_BARS - EMBARGO_BARS debe ser >= 100")
        need = self.train_window_bars + self.wf_folds * self.test_window_bars + 200
        if self.learning_enabled and self.history_bars < need:
            e.append(f"HISTORY_BARS ({self.history_bars}) debe ser >= TRAIN_WINDOW_BARS + WF_FOLDS*TEST_WINDOW_BARS + 200 = {need}")
        for n in ("risk_per_trade_pct", "max_position_pct", "max_total_exposure_pct", "stop_atr_mult"):
            if getattr(self, n) <= 0:
                e.append(f"{n.upper()} debe ser > 0")
        if self.max_position_pct > 100:
            e.append("MAX_POSITION_PCT no puede superar 100")
        if self.mode == "live":
            if self.live_confirm != LIVE_PHRASE:
                e.append(f"MODE=live exige LIVE_CONFIRM={LIVE_PHRASE}")
            if self.data_source != "exchange":
                e.append("MODE=live exige DATA_SOURCE=exchange")
            if not (self.api_key and self.api_secret):
                e.append("MODE=live exige EXCHANGE_API_KEY y EXCHANGE_API_SECRET")
            if self.allow_short:
                e.append("ALLOW_SHORT=true no está soportado en live (solo spot long-only)")
        if e:
            raise ValueError("Configuración inválida:\n - " + "\n - ".join(e))

    def public(self):
        d = {k: v for k, v in self.__dict__.items()}
        for k in ("api_key", "api_secret", "api_password", "api_token", "live_confirm",
                  "panel_password"):
            d[k] = "***" if d.get(k) else ""
        return d
