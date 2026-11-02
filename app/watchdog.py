"""Watchdog: detecta que el bucle del motor dejó de avanzar.

No sustituye al stop nativo (ese vive en el exchange y funciona aunque el
proceso esté muerto). Su trabajo es avisar y, si se pide explícitamente, aplanar
cuando el proceso sigue vivo pero el ciclo está atascado.
"""
import logging
import threading
import time

log = logging.getLogger("watchdog")


class Watchdog(threading.Thread):
    def __init__(self, engine, cfg):
        super().__init__(name="watchdog", daemon=True)
        self.engine, self.cfg = engine, cfg
        self._stop_ev = threading.Event()
        self._alerted = False
        self.last_check = 0.0

    def stop(self):
        self._stop_ev.set()

    def run(self):
        while not self._stop_ev.wait(self.cfg.watchdog_interval_seconds):
            try:
                self.check()
            except Exception:
                log.exception("error en el watchdog")

    def _age(self):
        """Segundos desde la última señal de vida (tick o progreso de entrenamiento).

        Contar el progreso del entrenamiento evita falsas alarmas: un
        walk-forward con rejilla grande puede tardar minutos dentro de un tick.
        """
        st = self.engine.train_state or {}
        beat = max(float(self.engine.last_tick_at or 0.0),
                   float(st.get("updated") or 0.0), float(st.get("started") or 0.0))
        return time.time() - beat if beat else 0.0

    def check(self):
        self.last_check = time.time()
        eng, cfg = self.engine, self.cfg
        if not eng.running:
            self._alerted = False
            return []
        problems = []
        thread = getattr(eng, "_thread", None)
        if thread is not None and not thread.is_alive():
            problems.append("hilo_muerto")
        age = self._age()
        if age > cfg.watchdog_stall_seconds:
            problems.append("sin_tick")
        if not problems:
            self._alerted = False
            return []
        if not self._alerted:   # una sola alerta por episodio, no spam cada intervalo
            self._alerted = True
            log.error("watchdog: %s (última señal hace %.0fs, posiciones=%s)",
                      problems, age, list(eng.S["positions"]))
            eng._event("watchdog", {"problems": problems, "age_seconds": round(age, 1),
                                    "positions": list(eng.S["positions"]),
                                    "msg": "el ciclo no avanza; revisa el log del motor"})
        if cfg.watchdog_flatten:
            self._flatten()
        return problems

    def _flatten(self):
        got = self.engine.lock.acquire(timeout=5)
        try:
            if not got:
                log.error("watchdog: el motor no libera el lock; no se puede aplanar")
                return
            self.engine.flatten("watchdog")
        finally:
            if got:
                self.engine.lock.release()
