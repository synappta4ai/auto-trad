"""Punto de entrada: motor continuo + API/UI web (y GUI de escritorio opcional)."""
import logging
import secrets
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn

from .api import create_app
from .broker import CcxtBroker, PaperBroker
from .config import Config
from .data import make_feed
from .engine import Engine
from .store import Store


def _force_utf8():
    """Evita UnicodeEncodeError en consolas Windows con cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _force_utf8()
    cfg = Config()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("main")

    token = cfg.api_token
    if not token:
        token = secrets.token_urlsafe(24)
        log.warning("API_TOKEN vacio: token temporal generado -> %s (definelo en .env para que sea fijo)", token)
    if cfg.mode == "live":
        log.warning("*** MODO LIVE: se enviaran ordenes reales a %s (sandbox=%s) ***", cfg.exchange, cfg.exchange_sandbox)

    # --- componentes ---
    store = Store(cfg.data_dir)
    feed = make_feed(cfg)
    broker = CcxtBroker(cfg, feed) if cfg.mode == "live" else PaperBroker(cfg)
    engine = Engine(cfg, store, feed, broker)

    # --- motor: arranca solo si AUTO_START=true (o el boton Iniciar de la UI) ---
    if cfg.auto_start:
        engine.start()

    # --- API ---
    @asynccontextmanager
    async def lifespan(_app):
        yield
        engine.stop()

    app = create_app(engine, cfg, store, token, lifespan=lifespan)

    def _serve():
        try:
            uvicorn.run(app, host=cfg.api_bind, port=cfg.api_port, log_level="warning")
        except Exception:
            log.exception("el servidor API termino con error")
        finally:
            engine.stop()

    server_thread = threading.Thread(target=_serve, name="uvicorn-server", daemon=True)
    server_thread.start()

    url = f"http://{cfg.api_bind}:{cfg.api_port}/ui"
    print("=" * 62)
    print(" AutoTrader iniciado")
    print(f" Panel web : {url}")
    print(f" API docs  : http://{cfg.api_bind}:{cfg.api_port}/docs")
    print(f" Modo      : {cfg.mode} | datos: {cfg.data_source} | tf: {cfg.timeframe}")
    print(f" Simbolos  : {', '.join(cfg.symbols)}")
    print(f" Motor     : {'AUTOSTART' if cfg.auto_start else 'detenido (pulsa Iniciar)'}")
    print("=" * 62)

    # --- GUI de escritorio opcional (UI=desktop) ---
    if cfg.ui == "desktop":
        try:
            from .gui import run_gui
            run_gui(cfg, engine)
            return
        except Exception as exc:
            log.error("GUI de escritorio no disponible (%s). Se continua solo con la web.", exc)

    # --- mantener vivo el proceso mientras el servidor este arriba ---
    try:
        while server_thread.is_alive():
            server_thread.join(0.5)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario (Ctrl+C)")
    finally:
        engine.stop()
        print("AutoTrader detenido")


if __name__ == "__main__":
    main()
