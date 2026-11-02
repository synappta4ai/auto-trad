"""Recetas de configuración: paquetes de ajustes con un objetivo concreto.

Cada receta responde a una pregunta distinta ("¿funciona el circuito?", "¿hay
algo aprovechable?", "¿opero lo suficiente para medirlo?"). Se aplican con
`settings.update`, así que pasan por la misma validación y el mismo guardado en
`.env` que un cambio hecho a mano; nada de estados paralelos.
"""
import logging

from . import settings

log = logging.getLogger("presets")


PRESETS = [
    {
        "id": "rapido",
        "name": "Iterar en segundos",
        "goal": "Probar la rejilla en muestras cortas para ver rápido si una idea tiene forma.",
        "eta": "~3 s por símbolo",
        "values": {"HISTORY_BARS": "1500", "TRAIN_WINDOW_BARS": "700", "TEST_WINDOW_BARS": "180",
                   "WF_FOLDS": "3", "MAX_CANDIDATES": "200", "RETRAIN_INTERVAL_HOURS": "0.5",
                   "TRAIN_MODE": "scheduled"},
        "warn": "Muestra pequeña: decide rápido, pero manda el ruido. Sirve para descartar, no para confiar.",
        "next": "Descarga historial y pulsa «Entrenar con estos datos».",
    },
    {
        "id": "mas_operaciones",
        "name": "Que la rejilla opere",
        "goal": "Atacar el bloqueo «trades 6<30»: tramos de test más largos para que haya operaciones que medir.",
        "eta": "~1-2 s por símbolo",
        "values": {"HISTORY_BARS": "3000", "TRAIN_WINDOW_BARS": "1200", "TEST_WINDOW_BARS": "500",
                   "WF_FOLDS": "3", "MIN_OOS_TRADES": "30"},
        "warn": "1500 velas de test en vez de 900: las métricas OOS dejan de ser una anécdota.",
        "next": "Entrena con los datos guardados y mira si los trades suben de 30.",
    },
    {
        "id": "explorar",
        "name": "Ampliar la búsqueda",
        "goal": "Buscar fuera de la rejilla actual: más combinaciones de EMA, rupturas y RSI.",
        "eta": "~5-15 s por símbolo",
        "values": {"MAX_CANDIDATES": "400", "STRATEGIES": "ema_cross,breakout,rsi_reversion",
                   "EMA_FAST": "5,8,12,20", "EMA_SLOW": "21,30,50,100",
                   "BREAKOUT_LOOKBACK": "10,20,40,80",
                   "RSI_LOW": "20,25,30,35", "RSI_HIGH": "65,70,75,80"},
        "warn": "Más candidatos = más tiempo y más probabilidad de encontrar algo por azar.",
    },
    {
        "id": "veredicto",
        "name": "Veredicto inmediato",
        "goal": "Puertas flojas para destapar qué hay; si algo se acerca, después se exige en serio.",
        "eta": "~1 s por símbolo",
        "values": {"MIN_OOS_TRADES": "5", "MIN_OOS_SHARPE": "0.1", "MIN_OOS_PROFIT_FACTOR": "1.02",
                   "MAX_OOS_DRAWDOWN_PCT": "25", "REQUIRE_BEAT_BENCHMARK": "false",
                   "RETRAIN_INTERVAL_HOURS": "0.5"},
        "warn": "Con estas puertas un campeón puede ser casualidad: explora, no operes con él a ciegas.",
    },
    {
        "id": "profundo",
        "name": "Histórico profundo",
        "goal": "Aprovechar años de velas reales: ventanas grandes para medir con 3600 velas OOS.",
        "eta": "~20-40 s por símbolo",
        "values": {"HISTORY_BARS": "10000", "TRAIN_WINDOW_BARS": "6000",
                   "TEST_WINDOW_BARS": "1200", "WF_FOLDS": "3", "MIN_OOS_TRADES": "30"},
        "warn": "Ventanas de meses: mezclan regímenes muy distintos. Ojo, el motor también pedirá "
                "10000 velas al arrancar.",
        "next": "Descarga con 10000 velas y entrena: tarda más, pero el veredicto pesa mucho más.",
    },
    {
        "id": "cinco_anios",
        "name": "Cinco años",
        "goal": "36.000 velas OOS en 12 folds: la primera muestra con peso estadístico de verdad.",
        "eta": "1-2 min de descarga + ~30 s por símbolo",
        "values": {"HISTORY_BARS": "44000", "TRAIN_WINDOW_BARS": "6000",
                   "TEST_WINDOW_BARS": "3000", "WF_FOLDS": "12", "MIN_OOS_TRADES": "100"},
        "warn": "Si aquí sale sin_ventaja, no es mala suerte: es que no hay ventaja. Ojo, el motor pedirá "
                "44.000 velas al arrancar.",
        "next": "Descarga con 44000 velas y entrena: cada pasada tarda más, pero atraviesa varios regímenes.",
    },
    {
        "id": "estricto",
        "name": "Solo lo que aguanta",
        "goal": "Filtrar en serio: exige ventaja clara y además batir a comprar y mantener.",
        "eta": "~1 s por símbolo",
        "values": {"MIN_OOS_TRADES": "40", "MIN_OOS_SHARPE": "1.0", "MIN_OOS_PROFIT_FACTOR": "1.3",
                   "MAX_OOS_DRAWDOWN_PCT": "10", "PROMOTE_MARGIN_PCT": "20",
                   "REQUIRE_BEAT_BENCHMARK": "true"},
        "warn": "Con 900-1500 velas OOS es difícil pasar: si nada pasa, es que no hay ventaja demostrable.",
    },
    {
        "id": "desatendido",
        "name": "Dejarlo corriendo",
        "goal": "Día a día: un walk-forward cada 24 h, cooldown y puertas de producción.",
        "eta": "una vez al día",
        "values": {"TRAIN_MODE": "scheduled", "RETRAIN_INTERVAL_HOURS": "24",
                   "TRAIN_COOLDOWN_SECONDS": "600", "TRAIN_MIN_NEW_BARS": "24",
                   "POLL_SECONDS": "30", "MIN_OOS_TRADES": "30", "MIN_OOS_SHARPE": "0.6",
                   "MIN_OOS_PROFIT_FACTOR": "1.15", "MAX_OOS_DRAWDOWN_PCT": "15",
                   "REQUIRE_BEAT_BENCHMARK": "true"},
        "warn": "",
        "next": "Es la que quieres al final; las demás son experimentos.",
    },
]

_BY_ID = {p["id"]: p for p in PRESETS}


def get(pid):
    p = _BY_ID.get((pid or "").strip())
    if p is None:
        raise ValueError(f"receta desconocida: {pid}")
    return p


def _expected(env, raw):
    """Valor de la receta tal y como lo mostraría la página de configuración."""
    field = settings.REGISTRY[env]
    return settings.format_value(field, settings.parse(field, raw))


def listing(cfg):
    """Recetas con sus valores, si ya están aplicadas y cuántos parámetros tocan."""
    current = {f["env"]: f["value"] for f in settings.fields(cfg)}
    out = []
    for p in PRESETS:
        item = {k: p[k] for k in ("id", "name", "goal", "eta", "next") if k in p}
        item["warn"] = p.get("warn", "")
        item["values"] = {env: str(v) for env, v in p["values"].items()}
        item["active"] = all(current.get(env) == _expected(env, v) for env, v in p["values"].items())
        item["matches"] = sum(1 for env, v in p["values"].items() if current.get(env) == _expected(env, v))
        item["changes"] = len(p["values"])
        out.append(item)
    return {"presets": out, "timeframe": cfg.timeframe}


def apply(engine, cfg, pid):
    """Aplica la receta: valida, guarda en `.env` y entra en caliente donde se pueda."""
    p = get(pid)
    res = settings.update(engine, cfg, dict(p["values"]))
    log.info("receta aplicada: %s (%s parámetros)", p["name"], len(p["values"]))
    return dict(res, preset={"id": p["id"], "name": p["name"], "warn": p.get("warn", ""),
                             "next": p.get("next", "")})
