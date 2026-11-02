"""Configuración editable desde el panel web.

`.env` sigue siendo la única fuente de verdad: cada cambio se valida sobre una
copia de la `Config` (incluye las comprobaciones cruzadas de `validate()`), se
guarda en `.env` y, si el motor lo lee en cada ciclo, se aplica al instante.
Lo que se construye una sola vez al arrancar (feed, broker, modo, puerto) se
marca `hot=False`: se guarda y se avisa de que requiere reinicio.

Las claves de API, el token del panel y `LIVE_CONFIRM` no se editan desde la
web: quedan solo en `.env`.
"""
import copy
import datetime as dt
import logging
import os
import shutil
import threading

log = logging.getLogger("settings")

SECRET_ENV = ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD",
              "API_TOKEN", "LIVE_CONFIRM")
BUSY_TIMEOUT_SECONDS = 2.0
_TRUE = ("1", "true", "yes", "on", "si", "sí")
_FALSE = ("0", "false", "no", "off", "")
_LOCK = threading.RLock()   # un guardado a la vez


class Busy(RuntimeError):
    """El motor no suelta el lock (entrenando): mejor reintentar luego."""


def f(env, attr, label, group, kind="text", hot=True, help="", options=None, danger=False):
    return {"env": env, "attr": attr, "label": label, "group": group, "kind": kind,
            "hot": hot, "help": help, "options": list(options or []), "danger": danger}


# --- grupos ------------------------------------------------------------------
G_MARKET = "Mercado y datos"
G_CAPITAL = "Capital y costos"
G_RISK = "Riesgo y topes"
G_LEARN = "Aprendizaje (walk-forward)"
G_GRID = "Rejillas de estrategias"
G_PROTECT = "Protección en vivo"
G_AUTO = "Autoajuste"
G_MODE = "Arranque (requiere reinicio)"

FIELDS = [
    # --- mercado -------------------------------------------------------------
    f("SYMBOLS", "symbols", "Símbolos", G_MARKET, "csv_list",
      help="BASE/QUOTE separados por coma. Los nuevos empiezan en liquidez hasta su primer entrenamiento."),
    f("TIMEFRAME", "timeframe", "Temporalidad", G_MARKET, hot=False,
      help="1m, 5m, 15m, 1h, 4h, 1d..."),
    f("HISTORY_BARS", "history_bars", "Velas de historia", G_MARKET, "int",
      help="Debe cubrir TRAIN_WINDOW_BARS + WF_FOLDS*TEST_WINDOW_BARS + 200."),
    f("POLL_SECONDS", "poll_seconds", "Intervalo del ciclo (s)", G_MARKET, "int",
      help="Cada cuánto se revisan precios, stops y entradas. Menos segundos = más llamadas al exchange."),
    f("DATA_SOURCE", "data_source", "Fuente de datos", G_MARKET, "choice", hot=False,
      options=["synthetic", "exchange"]),
    f("EXCHANGE", "exchange", "Exchange (ccxt)", G_MARKET, hot=False),
    f("EXCHANGE_SANDBOX", "exchange_sandbox", "Sandbox del exchange", G_MARKET, "bool", hot=False),
    f("SYNTHETIC_SEED", "synthetic_seed", "Semilla sintética", G_MARKET, "int", hot=False),
    f("SYNTHETIC_BARS_PER_POLL", "synthetic_bars_per_poll", "Velas nuevas por ciclo (sintético)",
      G_MARKET, "int", help="Velocidad del simulador; 1 = tiempo real."),
    f("BARS_PER_YEAR", "bars_per_year", "Velas por año (0 = auto)", G_MARKET, "float",
      help="Solo afecta a la anualización del Sharpe."),
    # --- capital -------------------------------------------------------------
    f("INITIAL_CAPITAL", "initial_capital", "Capital inicial", G_CAPITAL, "float", hot=False,
      help="En live es el capital asignado al bot. Cambiarlo exige reiniciar."),
    f("FEE_PCT", "fee_pct", "Comisión por lado (%)", G_CAPITAL, "float"),
    f("SLIPPAGE_PCT", "slippage_pct", "Slippage estimado (%)", G_CAPITAL, "float"),
    f("SPREAD_PCT", "spread_pct", "Spread (%)", G_CAPITAL, "float"),
    f("MIN_NOTIONAL", "min_notional", "Nocional mínimo", G_CAPITAL, "float",
      help="Si la posición calculada no llega a esto, no se abre."),
    f("ALLOW_SHORT", "allow_short", "Permitir cortos", G_CAPITAL, "bool",
      help="Solo paper: en live está bloqueado (spot long-only)."),
    # --- riesgo --------------------------------------------------------------
    f("RISK_PER_TRADE_PCT", "risk_per_trade_pct", "Riesgo por operación (%)", G_RISK, "float",
      help="Porcentaje del equity que se arriesga hasta el stop."),
    f("MAX_POSITION_PCT", "max_position_pct", "Peso máximo por posición (%)", G_RISK, "float"),
    f("MAX_TOTAL_EXPOSURE_PCT", "max_total_exposure_pct", "Exposición total máxima (%)", G_RISK, "float"),
    f("MAX_OPEN_POSITIONS", "max_open_positions", "Posiciones simultáneas", G_RISK, "int"),
    f("ATR_PERIOD", "atr_period", "Periodo del ATR", G_RISK, "int"),
    f("STOP_ATR_MULT", "stop_atr_mult", "Stop en múltiplos de ATR", G_RISK, "float"),
    f("TAKE_PROFIT_RR", "take_profit_rr", "Take profit (R, 0 = sin TP)", G_RISK, "float"),
    f("MAX_DAILY_LOSS_PCT", "max_daily_loss_pct", "Pérdida diaria máxima (%)", G_RISK, "float"),
    f("MAX_WEEKLY_LOSS_PCT", "max_weekly_loss_pct", "Pérdida semanal máxima (%)", G_RISK, "float"),
    f("MAX_DRAWDOWN_PCT", "max_drawdown_pct", "Drawdown máximo (%)", G_RISK, "float"),
    f("MAX_TRADES_PER_DAY", "max_trades_per_day", "Operaciones por día", G_RISK, "int"),
    f("COOLDOWN_BARS", "cooldown_bars", "Velas de espera tras pérdida", G_RISK, "int"),
    f("REENTRY_AFTER_STOP_REQUIRES_RESET", "reentry_requires_reset",
      "Reentrar exige reset de señal", G_RISK, "bool"),
    f("ENTER_ON_START", "enter_on_start", "Entrar al arrancar", G_RISK, "bool",
      help="Si la señal ya está activa cuando el bot empieza, abre igualmente."),
    f("KILL_SWITCH_CLOSE_POSITIONS", "kill_switch_close_positions",
      "Kill switch cierra posiciones", G_RISK, "bool"),
    # --- aprendizaje ---------------------------------------------------------
    f("LEARNING_ENABLED", "learning_enabled", "Aprendizaje activo", G_LEARN, "bool",
      help="Si lo apagas, usa FIXED_STRATEGY con el primer juego de parámetros."),
    f("FIXED_STRATEGY", "fixed_strategy", "Estrategia fija", G_LEARN,
      help="Solo aplica con el aprendizaje desactivado."),
    f("STRATEGIES", "strategies", "Estrategias a probar", G_LEARN, "csv_list",
      help="ema_cross, rsi_reversion, breakout, ml_logit"),
    f("TRAIN_MODE", "train_mode", "Modo de entrenamiento", G_LEARN, "choice",
      options=["scheduled", "continuous"],
      help="continuous = reentrena en cuanto hay velas nuevas y pasa el cooldown."),
    f("RETRAIN_INTERVAL_HOURS", "retrain_interval_hours", "Reentrenar cada (h)", G_LEARN, "float"),
    f("TRAIN_COOLDOWN_SECONDS", "train_cooldown_seconds", "Espera mínima entre entrenamientos (s)",
      G_LEARN, "float", help="Modo continuo exige 60 s como mínimo: con menos reentrenaba en cada ciclo."),
    f("TRAIN_MIN_NEW_BARS", "train_min_new_bars", "Velas nuevas mínimas", G_LEARN, "int"),
    f("TRAIN_WINDOW_BARS", "train_window_bars", "Ventana de entrenamiento", G_LEARN, "int"),
    f("TEST_WINDOW_BARS", "test_window_bars", "Ventana de test", G_LEARN, "int"),
    f("WF_FOLDS", "wf_folds", "Folds del walk-forward", G_LEARN, "int"),
    f("EMBARGO_BARS", "embargo_bars", "Embargo entre train y test", G_LEARN, "int",
      help="0 = ML_HORIZON. Evita etiquetas solapadas con el test."),
    f("MIN_TRAIN_TRADES", "min_train_trades", "Trades mínimos en train", G_LEARN, "int"),
    f("MAX_CANDIDATES", "max_candidates", "Candidatos máximos", G_LEARN, "int",
      help="Acota el coste del walk-forward."),
    f("PARALLEL_WORKERS", "parallel_workers", "Núcleos del walk-forward", G_LEARN, "int",
      help="0 = automático (solo si hay trabajo suficiente); 1 = en serie."),
    f("MIN_OOS_TRADES", "min_oos_trades", "Trades mínimos OOS", G_LEARN, "int"),
    f("MIN_OOS_SHARPE", "min_oos_sharpe", "Sharpe mínimo OOS", G_LEARN, "float"),
    f("MIN_OOS_PROFIT_FACTOR", "min_oos_profit_factor", "Profit factor mínimo OOS", G_LEARN, "float"),
    f("MAX_OOS_DRAWDOWN_PCT", "max_oos_drawdown_pct", "Drawdown máximo OOS (%)", G_LEARN, "float"),
    f("PROMOTE_MARGIN_PCT", "promote_margin_pct", "Margen para promocionar (%)", G_LEARN, "float"),
    f("DEMOTE_ON_FAIL", "demote_on_fail", "Degradar si falla las puertas", G_LEARN, "bool"),
    f("REQUIRE_BEAT_BENCHMARK", "require_beat_benchmark", "Exigir superar buy & hold", G_LEARN, "bool"),
    f("RETRAIN_ON_DRIFT", "retrain_on_drift", "Reentrenar si hay deriva", G_LEARN, "bool"),
    # --- rejillas ------------------------------------------------------------
    f("EMA_FAST", "ema_fast", "EMA rápida", G_GRID, "csv_grid",
      help="Varios valores = el optimizador prueba cada combinación."),
    f("EMA_SLOW", "ema_slow", "EMA lenta", G_GRID, "csv_grid"),
    f("RSI_PERIOD", "rsi_period", "Periodo RSI", G_GRID, "csv_grid"),
    f("RSI_LOW", "rsi_low", "RSI sobreventa", G_GRID, "csv_grid"),
    f("RSI_HIGH", "rsi_high", "RSI sobrecompra", G_GRID, "csv_grid"),
    f("RSI_EXIT", "rsi_exit", "RSI salida", G_GRID, "csv_grid"),
    f("BREAKOUT_LOOKBACK", "breakout_lookback", "Lookback de ruptura", G_GRID, "csv_grid"),
    f("BREAKOUT_EXIT_RATIO", "breakout_exit_ratio", "Salida de ruptura", G_GRID, "csv_grid"),
    f("ML_TRAIN_BARS", "ml_train_bars", "Velas de entrenamiento ML", G_GRID, "csv_grid"),
    f("ML_THRESHOLD", "ml_threshold", "Umbral ML", G_GRID, "csv_grid"),
    f("ML_HORIZON", "ml_horizon", "Horizonte ML", G_GRID, "int"),
    f("ML_REFIT_BARS", "ml_refit_bars", "Reajuste ML (velas)", G_GRID, "int"),
    f("ML_L2", "ml_l2", "Regularización L2", G_GRID, "float"),
    # --- protección ----------------------------------------------------------
    f("NATIVE_STOPS", "native_stops", "Stop nativo en el exchange", G_PROTECT, "bool",
      help="Si el exchange lo rechaza, la posición se cierra en el acto."),
    f("RECONCILE_ON_START", "reconcile_on_start", "Reconciliar al arrancar", G_PROTECT, "bool"),
    f("CLIENT_ID_PREFIX", "client_id_prefix", "Prefijo de client order id", G_PROTECT, hot=False,
      help="Cambiarlo con posiciones abiertas dificulta reconocer órdenes huérfanas."),
    f("WATCHDOG_ENABLED", "watchdog_enabled", "Watchdog activo", G_PROTECT, "bool", hot=False,
      help="El hilo se crea al arrancar: activarlo aquí exige reinicio."),
    f("WATCHDOG_INTERVAL_SECONDS", "watchdog_interval_seconds", "Intervalo del watchdog (s)",
      G_PROTECT, "float"),
    f("WATCHDOG_STALL_SECONDS", "watchdog_stall_seconds", "Atasco a partir de (s)", G_PROTECT, "float",
      help="Sin señal de vida durante este tiempo = alerta. Debe superar POLL_SECONDS."),
    f("WATCHDOG_FLATTEN", "watchdog_flatten", "Aplanar si se atasca", G_PROTECT, "bool"),
    # --- autoajuste ----------------------------------------------------------
    f("VOL_SCALING_ENABLED", "vol_scaling_enabled", "Escalado por volatilidad", G_AUTO, "bool"),
    f("DERISK_ENABLED", "derisk_enabled", "Reducir riesgo con drawdown", G_AUTO, "bool"),
    f("DERISK_START_DD_PCT", "derisk_start_dd_pct", "Drawdown donde empieza (s)", G_AUTO, "float"),
    f("DERISK_MIN_MULT", "derisk_min_mult", "Multiplicador mínimo", G_AUTO, "float"),
    f("RISK_MULT_MIN", "risk_mult_min", "Multiplicador de riesgo mínimo", G_AUTO, "float"),
    f("RISK_MULT_MAX", "risk_mult_max", "Multiplicador de riesgo máximo", G_AUTO, "float"),
    f("DRIFT_VOL_RATIO", "drift_vol_ratio", "Deriva: ratio de volatilidad", G_AUTO, "float"),
    f("DRIFT_SHORT_BARS", "drift_short_bars", "Deriva: ventana corta", G_AUTO, "int"),
    f("DRIFT_LONG_BARS", "drift_long_bars", "Deriva: ventana larga", G_AUTO, "int"),
    f("DRIFT_LOSS_STREAK", "drift_loss_streak", "Deriva: racha de pérdidas", G_AUTO, "int"),
    f("DRIFT_MIN_HOURS", "drift_min_hours", "Deriva: horas mínimas", G_AUTO, "float"),
    f("EQUITY_LOG_SECONDS", "equity_log_seconds", "Guardar equity cada (s)", G_AUTO, "int"),
    # --- arranque ------------------------------------------------------------
    f("MODE", "mode", "Modo", G_MODE, "choice", hot=False, options=["paper", "live"], danger=True,
      help="live exige LIVE_CONFIRM y claves ya presentes en .env. Se aplica al reiniciar."),
    f("API_PORT", "api_port", "Puerto del panel", G_MODE, "int", hot=False),
    f("API_BIND", "api_bind", "Interfaz de escucha", G_MODE, hot=False),
    f("LOG_LEVEL", "log_level", "Nivel de log", G_MODE, "choice", hot=False,
      options=["DEBUG", "INFO", "WARNING", "ERROR"]),
]

REGISTRY = {x["env"]: x for x in FIELDS}


# --------------------------------------------------------------------- valores
def parse(field, raw):
    """Texto del formulario -> valor tipado de la Config."""
    kind, label = field["kind"], field["label"]
    s = ("" if raw is None else str(raw)).strip()
    if kind == "bool":
        if s.lower() in _TRUE:
            return True
        if s.lower() in _FALSE:
            return False
        raise ValueError(f"{label}: usa true o false")
    if kind == "int":
        try:
            return int(float(s))
        except ValueError:
            raise ValueError(f"{label}: se espera un número entero") from None
    if kind == "float":
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"{label}: se espera un número") from None
    if kind == "choice":
        if s not in field["options"]:
            raise ValueError(f"{label}: opciones válidas: {', '.join(field['options'])}")
        return s
    if kind in ("csv_list", "csv_grid"):
        items = [x.strip() for x in s.split(",") if x.strip()]
        if not items:
            raise ValueError(f"{label}: no puede estar vacío")
        if kind == "csv_list":
            return items
        out = []
        for x in items:
            try:
                v = float(x)
            except ValueError:
                raise ValueError(f"{label}: '{x}' no es un número") from None
            out.append(int(v) if v == int(v) else v)
        return out
    return s


def format_value(field, value):
    """Valor de la Config -> texto para el formulario y para `.env`."""
    if field["kind"] == "bool":
        return "true" if value else "false"
    if field["kind"] in ("csv_list", "csv_grid"):
        return ",".join(str(v) for v in value)
    return str(value)


def fields(cfg):
    return [dict(REGISTRY[e], value=format_value(REGISTRY[e], getattr(cfg, REGISTRY[e]["attr"])))
            for e in REGISTRY]


# ------------------------------------------------------------------- validación
def preview(cfg, values):
    """Aplica los cambios sobre una copia y valida. Devuelve (copia, tipados)."""
    cand, typed, errors = copy.deepcopy(cfg), {}, []
    for env, raw in (values or {}).items():
        field = REGISTRY.get(env)
        if field is None:
            errors.append(f"parámetro desconocido: {env}")
            continue
        try:
            typed[env] = parse(field, raw)
        except ValueError as exc:
            errors.append(str(exc))
    for env, val in typed.items():
        setattr(cand, REGISTRY[env]["attr"], val)
    try:
        cand.validate()   # comprobaciones cruzadas (ventanas, live, símbolos...)
    except ValueError as exc:
        for line in str(exc).splitlines():
            line = line.strip().lstrip("- ").strip()
            if line and not line.startswith("Configuraci"):
                errors.append(line)
    if errors:
        raise ValueError("\n".join(errors))
    return cand, typed


# ------------------------------------------------------------------------ .env
def env_path():
    return os.path.join(os.getcwd(), ".env")


def save_env(updates, path=None):
    """Reescribe solo las claves indicadas; conserva comentarios y el resto de líneas."""
    path = path or env_path()
    with _LOCK:
        lines = []
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                lines = fh.read().splitlines()
            try:
                shutil.copyfile(path, path + ".bak")
            except OSError:
                log.warning("no se pudo crear la copia de seguridad de %s", path)
        pending = dict(updates)
        out = []
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                key = s.split("=", 1)[0].strip()
                if key in pending:
                    out.append(f"{key}={pending.pop(key)}")
                    continue
            out.append(line)
        if pending:
            marker = "# --- editado desde el panel web"
            if not any(line.startswith(marker) for line in out):
                stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                if out and out[-1].strip():
                    out.append("")
                out.append(f"{marker} ({stamp}) ---")
            out += [f"{k}={v}" for k, v in pending.items()]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out).rstrip("\n") + "\n")
        os.replace(tmp, path)
        return sorted(updates)


# ------------------------------------------------------------------- aplicación
def _check_symbols(engine, cfg, new):
    """Valida el cambio de símbolos. Devuelve (añadidos, quitados)."""
    added = [s for s in new if s not in cfg.symbols]
    removed = [s for s in cfg.symbols if s not in new]
    for sym in added + removed:
        head, _, tail = sym.partition("/")
        if not head or not tail or not (head.isalnum() and tail.isalnum()):
            raise ValueError(f"símbolo inválido: {sym} (usa BASE/QUOTE, ej. BTC/USDT)")
    if not added and not removed:
        return [], []
    if removed:
        if not engine.lock.acquire(timeout=BUSY_TIMEOUT_SECONDS):
            raise Busy("el motor está ocupado (entrenando); reintenta en unos segundos")
        try:
            busy = [s for s in removed if s in engine.S.get("positions", {})]
        finally:
            engine.lock.release()
        if busy:
            raise ValueError("no se puede quitar " + ", ".join(busy) +
                             ": hay una posición abierta (ciérrala antes)")
        for sym in removed:
            for key in ("prev_desired", "cooldown"):
                engine.S.get(key, {}).pop(sym, None)
    return added, removed


def update(engine, cfg, values):
    """Valida, guarda en `.env` y aplica en caliente lo que se puede."""
    if not isinstance(values, dict) or not values:
        raise ValueError("no hay cambios que guardar")
    with _LOCK:
        _, parsed = preview(cfg, values)
        hot = {e: v for e, v in parsed.items() if REGISTRY[e]["hot"]}
        restart = sorted(e for e in parsed if not REGISTRY[e]["hot"])
        added = []
        if "SYMBOLS" in hot:
            added, _ = _check_symbols(engine, cfg, hot["SYMBOLS"])
            for sym in added:
                engine.feed.add_symbol(sym)    # ValueError si el mercado no existe
        try:
            save_env({e: format_value(REGISTRY[e], v) for e, v in parsed.items()})
        except OSError as exc:
            raise ValueError(f"no se pudo escribir .env ({exc}): revisa permisos del archivo") from None
        for env, val in hot.items():
            setattr(cfg, REGISTRY[env]["attr"], val)
        if "TRAIN_MODE" in hot:
            engine.set_train_mode(cfg.train_mode)
        if added and cfg.learning_enabled:
            engine.request_retrain()           # el símbolo nuevo necesita campeón
        wake = getattr(engine, "wake_ev", None)
        if wake is not None:
            wake.set()
        changed = sorted(parsed)
        log.info("configuración actualizada: %s (reinicio: %s)", changed, restart or "-")
        event = getattr(engine, "_event", None)
        if event:
            event("config", {"changed": changed, "restart_required": restart})
        return {"ok": True, "applied": sorted(hot), "restart_required": restart,
                "values": {e: format_value(REGISTRY[e], v) for e, v in parsed.items()}}
