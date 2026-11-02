"""Persistencia en SQLite (estado, trades, curva de equity, eventos)."""
import json
import os
import sqlite3
import threading
import time


def _j(o):
    return o.item() if hasattr(o, "item") else str(o)


class Store:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "autotrader.db"), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, side INTEGER,
                qty REAL, entry REAL, exit REAL, pnl REAL, fees REAL, reason TEXT, strategy TEXT, params TEXT);
            CREATE TABLE IF NOT EXISTS equity(ts REAL, equity REAL, cash REAL, dd REAL);
            CREATE TABLE IF NOT EXISTS events(ts REAL, type TEXT, data TEXT);
            """)
            self.db.commit()

    def get(self, key, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value, default=_j)))
            self.db.commit()

    def add_trade(self, t):
        with self.lock:
            self.db.execute(
                "INSERT INTO trades(ts,symbol,side,qty,entry,exit,pnl,fees,reason,strategy,params) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), t["symbol"], t["side"], t["qty"], t["entry"], t["exit"], t["pnl"], t["fees"],
                 t["reason"], t["strategy"], json.dumps(t["params"], default=_j)))
            self.db.commit()

    def trades(self, limit=100):
        with self.lock:
            rows = self.db.execute(
                "SELECT ts,symbol,side,qty,entry,exit,pnl,fees,reason,strategy,params FROM trades ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        keys = ["ts", "symbol", "side", "qty", "entry", "exit", "pnl", "fees", "reason", "strategy", "params"]
        return [dict(zip(keys, r)) for r in rows]

    def add_equity(self, equity, cash, dd):
        with self.lock:
            self.db.execute("INSERT INTO equity VALUES(?,?,?,?)", (time.time(), equity, cash, dd))
            self.db.commit()

    def equity(self, limit=500):
        with self.lock:
            rows = self.db.execute("SELECT ts,equity,cash,dd FROM equity ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(["ts", "equity", "cash", "dd"], r)) for r in rows][::-1]

    def add_event(self, typ, data):
        with self.lock:
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), typ, json.dumps(data, default=_j)))
            self.db.commit()

    def events(self, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT ts,type,data FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "type": r[1], "data": json.loads(r[2])} for r in rows]
