"""Persistencia en SQLite (estado, trades, curva de equity, eventos)."""
import json
import os
import sqlite3
import threading
import time


SCHEMA_VERSION = 5
EVENTS_KEEP_DAYS = 30      # retención de la tabla `events`
EVENTS_MAX_ROWS = 20000    # y tope duro, para que no crezca sin freno
PRUNE_EVERY = 500          # cada cuántos eventos se intenta podar


def _j(o):
    return o.item() if hasattr(o, "item") else str(o)


class Store:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "autotrader.db"), check_same_thread=False)
        self.lock = threading.Lock()
        self._writes = 0
        self._migrate()
        self.prune_events()

    def _migrate(self):
        """Esquema idempotente. `PRAGMA user_version` marca la versión aplicada."""
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, side INTEGER,
                qty REAL, entry REAL, exit REAL, pnl REAL, fees REAL, reason TEXT, strategy TEXT, params TEXT);
            CREATE TABLE IF NOT EXISTS equity(ts REAL, equity REAL, cash REAL, dd REAL);
            CREATE TABLE IF NOT EXISTS events(ts REAL, type TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS learning_reports(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
                symbol TEXT, decision TEXT, report TEXT);
            CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts);
            CREATE INDEX IF NOT EXISTS idx_learning_symbol_ts ON learning_reports(symbol, ts);
            CREATE TABLE IF NOT EXISTS candles(symbol TEXT, timeframe TEXT, ts INTEGER,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY(symbol, timeframe, ts));
            CREATE TABLE IF NOT EXISTS settings(env TEXT PRIMARY KEY, value TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS experiments(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, run_id TEXT,
                symbol TEXT, timeframe TEXT, decision TEXT, strategy TEXT, params TEXT, sharpe REAL, pf REAL,
                trades INTEGER, ret_pct REAL, bh_pct REAL, excess_pct REAL, dd_pct REAL, bars INTEGER,
                candidates INTEGER, seconds REAL, test_bars INTEGER, folds INTEGER, report TEXT);
            CREATE INDEX IF NOT EXISTS idx_exp_run ON experiments(run_id, ts);
            CREATE INDEX IF NOT EXISTS idx_exp_cell ON experiments(symbol, timeframe, ts);
            """)
            # columnas añadidas después: las bases ya creadas necesitan el ALTER
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(experiments)").fetchall()}
            for col in ("test_bars", "folds"):
                if col not in cols:
                    self.db.execute(f"ALTER TABLE experiments ADD COLUMN {col} INTEGER")
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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

    def add_learning_report(self, symbol, report):
        """Histórico de walk-forwards: sin esto solo queda el último y no se
        puede ver si el campeón empeora con el tiempo."""
        with self.lock:
            self.db.execute(
                "INSERT INTO learning_reports(ts,symbol,decision,report) VALUES(?,?,?,?)",
                (time.time(), symbol, report.get("decision") or report.get("status"),
                 json.dumps(report, default=_j)))
            self.db.commit()

    def learning_history(self, symbol=None, limit=50):
        q = "SELECT ts,symbol,decision,report FROM learning_reports"
        args = []
        if symbol:
            q += " WHERE symbol=?"
            args.append(symbol)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        return [{"ts": r[0], "symbol": r[1], "decision": r[2], "report": json.loads(r[3])}
                for r in rows][::-1]

    # --- barridos: cada celda símbolo×temporalidad es un intento guardado -------
    def add_experiment(self, run_id, row):
        """Guarda un intento del barrido. Sirve para contar pruebas y no
        confundir un acierto por azar con una ventaja que se repite."""
        with self.lock:
            self.db.execute(
                "INSERT INTO experiments(ts,run_id,symbol,timeframe,decision,strategy,params,sharpe,pf,"
                "trades,ret_pct,bh_pct,excess_pct,dd_pct,bars,candidates,seconds,test_bars,folds,report) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), run_id, row.get("symbol"), row.get("timeframe"),
                 row.get("decision") or row.get("status"), row.get("strategy"),
                 json.dumps(row.get("params"), default=_j), row.get("sharpe"), row.get("pf"),
                 row.get("trades"), row.get("ret_pct"), row.get("bh_pct"), row.get("excess_pct"),
                 row.get("dd_pct"), row.get("bars"), row.get("candidates"), row.get("seconds"),
                 row.get("test_bars"), row.get("folds"),
                 json.dumps({k: v for k, v in row.items() if k != "params"}, default=_j)))
            self.db.commit()

    def experiments(self, run_id=None, limit=500):
        """Intentos guardados, del más reciente al más antiguo."""
        q = ("SELECT ts,run_id,symbol,timeframe,decision,strategy,params,sharpe,pf,trades,"
             "ret_pct,bh_pct,excess_pct,dd_pct,bars,candidates,seconds,test_bars,folds FROM experiments")
        args = []
        if run_id:
            q += " WHERE run_id=?"
            args.append(run_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        keys = ["ts", "run_id", "symbol", "timeframe", "decision", "strategy", "params", "sharpe",
                "pf", "trades", "ret_pct", "bh_pct", "excess_pct", "dd_pct", "bars", "candidates",
                "seconds", "test_bars", "folds"]
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["params"] = json.loads(d["params"]) if d["params"] else None
            out.append(d)
        return out

    def latest_run_id(self):
        """Identificador del último barrido guardado (o None)."""
        with self.lock:
            r = self.db.execute("SELECT run_id FROM experiments ORDER BY id DESC LIMIT 1").fetchone()
        return r[0] if r else None

    # --- caché de velas: entrenar sin volver a pedir datos al exchange ---------
    def save_candles(self, symbol, timeframe, rows):
        """Guarda velas (ts en ms). Idempotente: repetir una vela la actualiza."""
        rows = list(rows)
        if not rows:
            return 0
        with self.lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO candles(symbol,timeframe,ts,open,high,low,close,volume) "
                "VALUES(?,?,?,?,?,?,?,?)",
                [(symbol, timeframe) + tuple(r) for r in rows])
            self.db.commit()
        return len(rows)

    def candles(self, symbol, timeframe, limit=None):
        """Velas guardadas como DataFrame con el mismo formato que la fuente en vivo."""
        q = ("SELECT ts,open,high,low,close,volume FROM candles "
             "WHERE symbol=? AND timeframe=? ORDER BY ts")
        args = [symbol, timeframe]
        if limit:
            q = "SELECT * FROM (" + q.replace(" ORDER BY ts", " ORDER BY ts DESC") + " LIMIT ?) ORDER BY ts"
            args.append(int(limit))
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        if not rows:
            return None
        import pandas as pd
        idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
        return pd.DataFrame({"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                             "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                             "volume": [r[5] for r in rows]}, index=idx)

    def candle_stats(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT symbol,timeframe,COUNT(*),MIN(ts),MAX(ts) FROM candles "
                "GROUP BY symbol,timeframe ORDER BY symbol,timeframe").fetchall()
        return [{"symbol": r[0], "timeframe": r[1], "bars": int(r[2]), "first": r[3], "last": r[4]}
                for r in rows]

    def candle_stat(self, symbol, timeframe):
        with self.lock:
            r = self.db.execute(
                "SELECT COUNT(*),MIN(ts),MAX(ts) FROM candles WHERE symbol=? AND timeframe=?",
                (symbol, timeframe)).fetchone()
        return {"symbol": symbol, "timeframe": timeframe, "bars": int(r[0] or 0),
                "first": r[1], "last": r[2]}

    def prune_events(self):
        """Poda `events` por antigüedad y por volumen (crecía sin techo)."""
        with self.lock:
            self._prune_locked()

    def _prune_locked(self):
        cutoff = time.time() - EVENTS_KEEP_DAYS * 86400
        self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.db.execute(
            "DELETE FROM events WHERE rowid NOT IN "
            "(SELECT rowid FROM events ORDER BY ts DESC LIMIT ?)", (EVENTS_MAX_ROWS,))
        self.db.commit()

    # --- configuración: la base es la fuente de verdad, .env solo arranque ------
    def load_settings(self):
        """Parámetros guardados (env -> valor en texto). Vacío = aún sin migrar."""
        with self.lock:
            rows = self.db.execute("SELECT env, value FROM settings").fetchall()
        return {k: v for k, v in rows}

    def save_settings(self, values):
        """Guarda/actualiza parámetros. La próxima vez se cargan al arrancar."""
        if not values:
            return 0
        now = time.time()
        with self.lock:
            self.db.executemany(
                "INSERT INTO settings(env, value, updated) VALUES(?,?,?) "
                "ON CONFLICT(env) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                [(k, str(v), now) for k, v in values.items()])
            self.db.commit()
        return len(values)

    def add_event(self, typ, data):
        with self.lock:
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), typ, json.dumps(data, default=_j)))
            self.db.commit()
            self._writes += 1
            if self._writes % PRUNE_EVERY == 0:
                self._prune_locked()

    def events(self, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT ts,type,data FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "type": r[1], "data": json.loads(r[2])} for r in rows]
