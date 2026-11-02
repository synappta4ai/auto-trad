"""Persistencia: poda de la tabla de eventos (retención por antigüedad y tope)."""
import time

from app.store import EVENTS_KEEP_DAYS, Store


def test_poda_eventos_antiguos(tmp_path):
    s = Store(str(tmp_path))
    old = time.time() - (EVENTS_KEEP_DAYS + 1) * 86400
    with s.lock:
        s.db.execute("INSERT INTO events VALUES(?,?,?)", (old, "train", "{}"))
        s.db.commit()
    s.add_event("train", {"x": 1})

    s.prune_events()

    rows = s.events(10)
    assert len(rows) == 1 and rows[0]["data"] == {"x": 1}


def test_tope_de_filas(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.EVENTS_MAX_ROWS", 3)
    s = Store(str(tmp_path))
    for i in range(6):
        s.add_event("t", {"i": i})

    s.prune_events()

    assert len(s.events(10)) == 3


def test_add_event_poda_cada_tanto(tmp_path, monkeypatch):
    """Sin el disparador periódico, la tabla volvería a crecer sin freno."""
    monkeypatch.setattr("app.store.PRUNE_EVERY", 2)
    s = Store(str(tmp_path))
    old = time.time() - (EVENTS_KEEP_DAYS + 1) * 86400
    with s.lock:
        s.db.execute("INSERT INTO events VALUES(?,?,?)", (old, "viejo", "{}"))
        s.db.commit()

    s.add_event("t", {"i": 0})
    s.add_event("t", {"i": 1})

    assert [r["type"] for r in s.events(10)] == ["t", "t"]
