"""Fuentes de datos: sintética (demo/pruebas) y exchange real vía ccxt."""
import logging
import time

import numpy as np
import pandas as pd

log = logging.getLogger("data")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def tf_seconds(tf):
    return int(tf[:-1]) * _UNITS[tf[-1]]


def index_ms(idx):
    """Índice temporal -> epoch en milisegundos.

    pandas guarda los índices con la resolución con la que se crearon (ms, us o
    ns), así que `astype("int64")` no siempre son nanosegundos: se calcula por
    diferencia contra la epoch y no hay sorpresas al guardar la caché.
    """
    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return ((idx - _EPOCH) // pd.Timedelta(milliseconds=1)).to_numpy(dtype="int64")


def _frame(ts_ms, o, h, l, c, v):
    idx = pd.to_datetime(np.asarray(ts_ms, dtype="int64"), unit="ms", utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)


class SyntheticFeed:
    """Precios con regímenes (tendencia / reversión / ruido) para probar el sistema sin red.
    Avanza `synthetic_bars_per_poll` velas en cada ciclo para ver el autoajuste en vivo."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tf_ms = tf_seconds(cfg.timeframe) * 1000
        self.cursor = cfg.history_bars + 50
        self.full = {}
        self.total = self.cursor + 30000
        end = (int(time.time() * 1000) // self.tf_ms) * self.tf_ms
        self.start = end - self.cursor * self.tf_ms
        for k, s in enumerate(cfg.symbols):
            self.full[s] = self._gen(self.total, self.start, cfg.synthetic_seed + 101 * k, 100.0 * (k + 1))

    def add_symbol(self, symbol):
        """Añade un símbolo en caliente (el motor recorre cfg.symbols en cada ciclo)."""
        if symbol in self.full:
            return
        k = len(self.full)
        self.full[symbol] = self._gen(self.total, self.start, self.cfg.synthetic_seed + 101 * k, 100.0 * (k + 1))

    def _gen(self, n, start_ms, seed, p0):
        rng = np.random.default_rng(seed)
        phi = np.empty(n)
        i = 0
        while i < n:
            L = int(rng.integers(250, 700))
            phi[i:i + L] = rng.choice([0.22, -0.22, 0.0], p=[0.4, 0.3, 0.3])
            i += L
        sig = 0.006
        r = np.zeros(n)
        e = rng.standard_normal(n) * sig
        for t in range(1, n):
            r[t] = phi[t] * r[t - 1] + e[t]
        c = p0 * np.exp(np.cumsum(r))
        o = np.r_[p0, c[:-1]]
        spread = np.abs(rng.standard_normal(n)) * sig * 0.5
        h = np.maximum(o, c) * (1 + spread)
        l = np.minimum(o, c) * (1 - spread)
        v = rng.uniform(50, 150, n)
        ts = start_ms + np.arange(n) * self.tf_ms
        return _frame(ts, o, h, l, c, v)

    def advance(self):
        self.cursor += self.cfg.synthetic_bars_per_poll

    def candles(self, symbol):
        df = self.full[symbol]
        lo = max(0, self.cursor - self.cfg.history_bars)
        return df.iloc[lo:self.cursor]

    def last_price(self, symbol):
        return float(self.full[symbol]["close"].iloc[self.cursor - 1])


class ExchangeFeed:
    def __init__(self, cfg, store=None):
        import ccxt
        self.cfg = cfg
        self.store = store        # caché local de velas (opcional)
        self._saved = {}          # última vela guardada por símbolo (ms)
        self.tf_ms = tf_seconds(cfg.timeframe) * 1000
        params = {"enableRateLimit": True}
        if cfg.api_key:
            params.update(apiKey=cfg.api_key, secret=cfg.api_secret)
            if cfg.api_password:
                params["password"] = cfg.api_password
        self.ex = getattr(ccxt, cfg.exchange)(params)
        if cfg.exchange_sandbox:
            self.ex.set_sandbox_mode(True)
        self.ex.load_markets()
        self.data = {}

    def add_symbol(self, symbol):
        """Verifica que el mercado exista; las velas se piden de forma perezosa."""
        if symbol in self.data or not self.ex.markets:
            return
        if symbol not in self.ex.markets:
            raise ValueError(f"{symbol} no existe en {self.cfg.exchange}")

    def advance(self):
        pass

    def _fetch(self, symbol, since):
        out = []
        while True:
            rows = self.ex.fetch_ohlcv(symbol, self.cfg.timeframe, since=since, limit=1000)
            if not rows:
                break
            out += rows
            nxt = rows[-1][0] + self.tf_ms
            if len(rows) < 2 or nxt > time.time() * 1000:
                break
            since = nxt
        return out

    def candles(self, symbol):
        now_ms = int(time.time() * 1000)
        cur = self.data.get(symbol)
        since = (now_ms - (self.cfg.history_bars + 5) * self.tf_ms) if cur is None else int(cur.index[-1].timestamp() * 1000)
        rows = self._fetch(symbol, since)
        if rows:
            a = np.array(rows, dtype=float)
            a = a[a[:, 0] + self.tf_ms <= now_ms]  # solo velas cerradas
            if len(a):
                new = _frame(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])
                cur = new if cur is None else pd.concat([cur, new])
                cur = cur[~cur.index.duplicated(keep="last")].sort_index().iloc[-(self.cfg.history_bars + 500):]
                self.data[symbol] = cur
                self._save(symbol, cur)
        return self.data[symbol]

    def _save(self, symbol, df):
        """Guarda en SQLite lo que aún no estaba. Así el laboratorio puede
        entrenar con estas velas sin volver a pedirlas (y sin esperar al mercado)."""
        if self.store is None or df is None or not len(df):
            return
        try:
            ts_ms = index_ms(df.index)
            keep = ts_ms > self._saved.get(symbol, 0)
            if not keep.any():
                return
            pms = ts_ms[keep]
            part = df[keep]
            self.store.save_candles(symbol, self.cfg.timeframe,
                                    [(int(t), float(r.open), float(r.high), float(r.low),
                                      float(r.close), float(r.volume))
                                     for t, r in zip(pms, part.itertuples())])
            self._saved[symbol] = int(pms.max())
        except Exception:
            log.exception("no se pudo guardar el histórico de %s", symbol)

    def last_price(self, symbol):
        return float(self.ex.fetch_ticker(symbol)["last"])


def make_feed(cfg, store=None):
    """Sintética para probar sin red; exchange con caché en disco si se pasa `store`."""
    return SyntheticFeed(cfg) if cfg.data_source == "synthetic" else ExchangeFeed(cfg, store)
