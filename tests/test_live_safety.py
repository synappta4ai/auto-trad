"""Protección en vivo: client order ids, stops nativos, reconciliación y watchdog."""
import threading
import time
import types

import pytest

from app import reconcile as rec
from app.broker import CcxtBroker, PaperBroker, client_id
from app.config import Config
from app.watchdog import Watchdog


@pytest.fixture
def cfg():
    return Config()


# --------------------------------------------------------------------- broker
class FakeEx:
    def __init__(self):
        self.orders, self.cancelled, self.open = [], [], []
        self.balance = {"BTC": {"free": 0.5, "total": 0.5}, "USDT": {"free": 1000.0, "total": 1000.0}}

    def amount_to_precision(self, symbol, qty):
        return f"{float(qty):.6f}"

    def price_to_precision(self, symbol, price):
        return f"{float(price):.2f}"

    def create_order(self, symbol, typ, side, qty, price=None, params=None):
        o = {"id": f"o{len(self.orders) + 1}", "symbol": symbol, "type": typ, "side": side,
             "amount": float(qty), "price": price, "params": dict(params or {})}
        self.orders.append(o)
        return o

    def cancel_order(self, oid, symbol=None):
        self.cancelled.append((oid, symbol))
        return {"id": oid}

    def fetch_open_orders(self, symbol=None):
        return self.open

    def fetch_balance(self):
        return self.balance


def ccxt_broker(cfg):
    b = CcxtBroker.__new__(CcxtBroker)
    b.cfg, b.ex, b.quote = cfg, FakeEx(), "USDT"
    return b


def test_client_id_is_short_and_prefixed(cfg):
    cid = client_id(cfg, "BTC/USDT", "open")
    assert cid.startswith("at-BTCUSDT-open-")
    assert len(cid) <= 36     # límite habitual de los exchanges


def test_execute_passes_client_order_id(cfg):
    b = ccxt_broker(cfg)
    fill, fee, qty = b.execute("BTC/USDT", 1, 0.1, 100.0, cid="at-BTCUSDT-open-1")
    assert b.ex.orders[0]["params"]["clientOrderId"] == "at-BTCUSDT-open-1"
    assert fill == 100.0 and qty == 0.1 and fee > 0


def test_protect_places_stop_market_and_cancels(cfg):
    b = ccxt_broker(cfg)
    oid = b.protect("BTC/USDT", 1, 0.1, 98.457, cid="at-BTCUSDT-stop-2")
    o = b.ex.orders[0]
    assert oid == "o1"
    assert o["type"] == "stop_market" and o["side"] == "sell"
    assert o["params"]["stopPrice"] == 98.46
    assert o["params"]["clientOrderId"] == "at-BTCUSDT-stop-2"
    assert b.cancel("BTC/USDT", oid) is True
    assert b.ex.cancelled == [("o1", "BTC/USDT")]


def test_paper_broker_has_no_exchange_protection(cfg):
    b = PaperBroker(cfg)
    assert b.protect("BTC/USDT", 1, 1.0, 90.0) is None
    assert b.position_qty("BTC/USDT") is None
    assert b.open_orders() == []
    assert b.cancel("BTC/USDT", "x") is False


# ----------------------------------------------------------------- reconciliación
class FakeBroker:
    def __init__(self, qty=None, orders=None, protect_ok=True, orders_fail=False):
        self.qty, self.orders, self.protect_ok = qty, orders or [], protect_ok
        self.orders_fail = orders_fail
        self.protected, self.cancelled = [], []

    def position_qty(self, symbol):
        return self.qty

    def open_orders(self, symbol=None):
        if self.orders_fail:
            raise RuntimeError("sin red")
        return self.orders

    def protect(self, symbol, side, qty, stop, cid=None):
        if not self.protect_ok:
            raise RuntimeError("boom")
        self.protected.append({"symbol": symbol, "side": side, "qty": qty, "stop": stop, "cid": cid})
        return "stop-9"

    def cancel(self, symbol, oid):
        self.cancelled.append((symbol, oid))
        return True


def make_state(qty=10.0, stop=90.0, stop_order_id=None, sym="BTC/USDT"):
    return {"positions": {sym: {"side": 1, "qty": qty, "entry": 100.0, "fee": 0.0, "stop": stop,
                                "tp": None, "stop_order_id": stop_order_id}}}


def run(cfg, S, broker):
    events = []
    rep = rec.reconcile(cfg, S, broker, lambda s: 100.0, lambda t, d: events.append((t, d)))
    return rep, events


def test_closed_position_when_exchange_is_flat(cfg):
    S, broker = make_state(), FakeBroker(qty=0.0)
    rep, events = run(cfg, S, broker)
    assert S["positions"] == {}
    assert rep["closed"] == ["BTC/USDT"]
    assert [t for t, _ in events] == ["reconcile_needed"]


def test_shrunk_position_is_adjusted_down(cfg):
    S, broker = make_state(qty=10.0, stop_order_id="stop-old"), FakeBroker(qty=4.0)
    rep, events = run(cfg, S, broker)
    assert S["positions"]["BTC/USDT"]["qty"] == 4.0
    assert rep["adjusted"] == [{"symbol": "BTC/USDT", "qty": 4.0}]
    assert [t for t, _ in events] == ["reconcile_qty"]
    assert broker.protected == []   # ya tenía stop nativo


def test_never_invents_positions_from_balance(cfg):
    S, broker = {}, FakeBroker(qty=5.0)
    rep, _ = run(cfg, S, broker)
    assert S["positions"] == {}
    assert broker.protected == [] and rep["protected"] == []


def test_missing_native_stop_is_replaced(cfg):
    S, broker = make_state(qty=10.0, stop_order_id=None), FakeBroker(qty=10.0)
    rep, _ = run(cfg, S, broker)
    assert S["positions"]["BTC/USDT"]["stop_order_id"] == "stop-9"
    assert rep["protected"] == ["BTC/USDT"]
    assert broker.protected[0]["cid"].startswith("at-BTCUSDT-stop-")


def test_protect_failure_needs_attention(cfg):
    S, broker = make_state(), FakeBroker(qty=10.0, protect_ok=False)
    rep, events = run(cfg, S, broker)
    assert rep["status"] == "needs_attention"
    assert "BTC/USDT" in S["positions"]      # no se inventa un cierre
    assert "protect_failed" in [t for t, _ in events]


def test_stale_orders_are_cancelled_but_not_foreign_ones(cfg):
    orders = [
        {"id": "x1", "symbol": "BTC/USDT", "clientOrderId": "at-BTCUSDT-open-11"},
        {"id": "x2", "symbol": "BTC/USDT", "clientOrderId": "manual-22"},
    ]
    S, broker = {}, FakeBroker(qty=0.0, orders=orders)
    rep, _ = run(cfg, S, broker)
    assert rep["cancelled"] == ["x1"]
    assert broker.cancelled == [("BTC/USDT", "x1")]


def test_current_stop_order_is_not_cancelled(cfg):
    orders = [{"id": "stop-9", "symbol": "BTC/USDT", "clientOrderId": "at-BTCUSDT-stop-1"}]
    S, broker = make_state(stop_order_id="stop-9"), FakeBroker(qty=10.0, orders=orders)
    rep, _ = run(cfg, S, broker)
    assert rep["cancelled"] == []


def test_paper_mode_skips_reconciliation(cfg):
    cfg.mode = "paper"
    eng = types.SimpleNamespace(cfg=cfg, S={"positions": {}}, broker=FakeBroker(qty=0.0),
                                prices={}, feed=None)
    assert rec.run_reconcile(eng)["status"] == "skipped"


# ----------------------------------------------------------------------- watchdog
class FakeEngine:
    def __init__(self, age=0.0, running=True, alive=True):
        self.running, self.alive = running, alive
        self.lock = threading.RLock()
        self.last_tick_at = time.time() - age
        self.train_state = {}
        self.S = {"positions": {}}
        self.events, self.flattened = [], []
        self._thread = types.SimpleNamespace(is_alive=lambda: self.alive)

    def _event(self, typ, data):
        self.events.append((typ, data))

    def flatten(self, reason="manual"):
        self.flattened.append(reason)


def test_watchdog_detects_stall_once(cfg):
    cfg.watchdog_stall_seconds = 10
    eng = FakeEngine(age=1000)
    wd = Watchdog(eng, cfg)
    assert wd.check() == ["sin_tick"]
    assert [t for t, _ in eng.events] == ["watchdog"]
    wd.check()   # segundo chequeo: no repite la alerta del mismo episodio
    assert len(eng.events) == 1
    assert eng.flattened == []     # WATCHDOG_FLATTEN=false por defecto


def test_watchdog_ignores_healthy_engine(cfg):
    cfg.watchdog_stall_seconds = 10
    eng = FakeEngine(age=1)
    assert Watchdog(eng, cfg).check() == []


def test_watchdog_ignores_long_training(cfg):
    cfg.watchdog_stall_seconds = 10
    eng = FakeEngine(age=1000)
    eng.train_state = {"updated": time.time()}
    assert Watchdog(eng, cfg).check() == []


def test_watchdog_detects_dead_thread(cfg):
    cfg.watchdog_stall_seconds = 10
    eng = FakeEngine(age=0, alive=False)
    assert "hilo_muerto" in Watchdog(eng, cfg).check()


def test_watchdog_does_nothing_when_stopped(cfg):
    cfg.watchdog_stall_seconds = 10
    eng = FakeEngine(age=1000, running=False)
    assert Watchdog(eng, cfg).check() == []


def test_watchdog_flatten_when_configured(cfg):
    cfg.watchdog_stall_seconds = 10
    cfg.watchdog_flatten = True
    eng = FakeEngine(age=1000)
    Watchdog(eng, cfg).check()
    assert eng.flattened == ["watchdog"]
