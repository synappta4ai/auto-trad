import asyncio
import logging
import secrets
import threading
import sys

from contextlib import asynccontextmanager

import uvicorn

from PyQt6.QtWidgets import QApplication

from .api import create_app
from .broker import CcxtBroker, PaperBroker
from .config import Config
from .data import make_feed
from .engine import Engine
from .store import Store
from .gui import run_gui


def main():
    cfg = Config()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("main")
    token = cfg.api_token
    if not token:
        token = secrets.token_urlsafe(24)
        log.warning("API_TOKEN vacío: token temporal generado -> %s (defínelo en .env para que sea fijo)", token)
    if cfg.mode == "live":
        log.warning("*** MODO LIVE: se enviarán órdenes reales a %s (sandbox=%s) ***", cfg.exchange, cfg.exchange_sandbox)

    # --- INICIALIZAR COMPONENTES ---
    store = Store(cfg.data_dir)
    feed = make_feed(cfg)
    broker = CcxtBroker(cfg, feed) if cfg.mode == "live" else PaperBroker(cfg)
    engine = Engine(cfg, store, feed, broker)

    # --- CREAR QApplication EN HILLO PRINCIPAL (OBLIGATORIO PYQT6 WINDOWS) ---
    # Esto debe estar ANTES que cualquier otro hilo que use Qt
    qt_app = QApplication.instance()
    if qt_app is None:
        qt_app = QApplication(sys.argv)

    # --- INICIAR MOTOR EN HILO DAEMON ---
    engine_thread = threading.Thread(target=engine.run, name="engine", daemon=True)
    engine_thread.start()

    # --- INICIAR SERVER UVICORN EN HILO DAEMON ---
    @asynccontextmanager
    async def lifespan(_app):
        yield
        engine.running = False
        engine.stop_ev.set()

    app = create_app(engine, cfg, store, token, lifespan=lifespan)

    # --- INICIAR SERVER EN HILO SEPARADO ---
    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=cfg.api_bind, port=cfg.api_port, log_level="warning"),
        daemon=True,
        name="uvicorn-server"
    )
    server_thread.start()

    # --- MOSTRAR VENTANA GUI Y BLOQUEAR HILLO PRINCIPAL ---
    # QApplication.exec() debe ejecutarse en el hilo principal para que
    # todos los eventos de GUI (botones, etc.) funcionen correctamente
    print("AutoTrader iniciado - Motor y server en background")
    print("Use la ventana gráfica para controlar: Start/Stop/Pause")
    print("Cerrar la ventana o presionar Ctrl+C para detener todo")

    # Ejecutar event loop de Qt en el hilo principal
    # Esto bloquea y mantiene la ventana abierta, procesando todos los eventos
    # de los botones Start/Stop/Pause que el usuario pulse
    try:
        run_gui(cfg, engine)
    except KeyboardInterrupt:
        print("\n⏹ Detenido por usuario (Ctrl+C)")
    except Exception as e:
        logging.getLogger("main").error(f"Error en GUI: {e}")
        print(f"\nError: {e}")
    finally:
        # Cuando la GUI se cierra, limpiar
        engine.running = False
        engine.stop_ev.set()
        print("✅ AutoTrader detenido correctamente")


if __name__ == "__main__":
    main()