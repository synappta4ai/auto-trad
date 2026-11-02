"""Buffer en memoria de los logs del proceso, para la pagina `/logs`.

El logging ya sale por consola; este handler guarda ademas las ultimas lineas
para que el panel web las pueda mostrar sin depender de un fichero.
"""
import collections
import logging
import threading

MAX_ENTRIES = 4000

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_ENTRIES = collections.deque(maxlen=MAX_ENTRIES)
_LOCK = threading.Lock()


class BufferHandler(logging.Handler):
    """Copia cada registro a un deque compartido (no formatea a disco)."""

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # mensajes con % mal formados
            msg = str(record.msg)
        if record.exc_info:
            try:
                last = logging.Formatter().formatException(record.exc_info).strip().splitlines()[-1]
                msg = f"{msg} | {last}"
            except Exception:
                pass
        with _LOCK:
            _ENTRIES.append({"ts": record.created, "level": record.levelname,
                             "name": record.name, "msg": msg})


def install(level=logging.NOTSET):
    """Engancha el buffer al logger raiz una sola vez."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, BufferHandler):
            return handler
    handler = BufferHandler()
    handler.setLevel(level)
    root.addHandler(handler)
    return handler


def entries(limit=300, level=None, q=None):
    """Ultimas entradas, en orden cronologico, filtradas por nivel minimo y texto."""
    min_level = _LEVELS.get((level or "").upper()) if level else None
    with _LOCK:
        items = list(_ENTRIES)
    if min_level is not None:
        items = [e for e in items if _LEVELS.get(e["level"], 0) >= min_level]
    if q:
        needle = q.lower()
        items = [e for e in items
                 if needle in e["msg"].lower() or needle in e["name"].lower()]
    limit = max(1, int(limit or 300))
    return items[-limit:]


def clear():
    with _LOCK:
        _ENTRIES.clear()
