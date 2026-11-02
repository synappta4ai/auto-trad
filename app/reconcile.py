"""Reconciliación al arrancar: el exchange es la verdad sobre lo que existe.

Solo actúa en live, y con reglas deliberadamente conservadoras:

- **Nunca inventa posiciones** a partir del saldo: el saldo de BTC de la cuenta
  puede ser tuyo y no del bot. Si el bot no abrió la posición, no la adopta.
- **Solo corrige hacia abajo**: si el exchange tiene menos cantidad que el
  estado local, el stop nativo se ejecutó (o alguien vendió a mano) y se ajusta.
- **Cancela órdenes huérfanas** de ejecuciones anteriores que llevan nuestro
  prefijo de client order id: un stop viejo con otra cantidad puede cerrar la
  posición nueva si se deja vivo.
"""
import logging

log = logging.getLogger("reconcile")

DUST_RATIO = 0.02   # 2% de tolerancia por comisiones y redondeos de precisión


def run_reconcile(engine):
    """Punto de entrada desde el motor. No hace nada en paper."""
    if engine.cfg.mode != "live":
        return {"status": "skipped", "reason": "solo MODE=live"}

    def price_of(sym):
        return engine.prices.get(sym) or engine.feed.last_price(sym)

    def emit(typ, data):
        engine._event(typ, data)

    return reconcile(engine.cfg, engine.S, engine.broker, price_of, emit)


def reconcile(cfg, S, broker, price_of, emit):
    out = {"status": "ok", "closed": [], "adjusted": [], "protected": [],
           "cancelled": [], "errors": []}
    stops = set()
    positions = S.setdefault("positions", {})
    for sym in cfg.symbols:
        pos = positions.get(sym)
        try:
            ex_qty = broker.position_qty(sym)
        except Exception as ex:
            out["errors"].append(f"{sym}: {ex}")
            ex_qty = None
        if not pos:
            continue
        if pos.get("stop_order_id"):
            stops.add(pos["stop_order_id"])
        local = float(pos.get("qty") or 0.0)
        if ex_qty is not None:
            if ex_qty <= max(local * DUST_RATIO, 1e-12):
                del positions[sym]
                out["closed"].append(sym)
                emit("reconcile_needed", {"symbol": sym, "local_qty": local, "exchange_qty": ex_qty,
                                          "msg": "la posición local ya no está en el exchange "
                                                 "(stop ejecutado o cierre manual); revisa el PnL"})
                continue
            if ex_qty < local * (1 - DUST_RATIO):
                pos["qty"] = ex_qty
                out["adjusted"].append({"symbol": sym, "qty": ex_qty})
                emit("reconcile_qty", {"symbol": sym, "local_qty": local, "exchange_qty": ex_qty})
        if cfg.native_stops and not pos.get("stop_order_id"):
            try:
                oid = broker.protect(sym, pos["side"], pos["qty"], pos["stop"],
                                     cid=_client_id(cfg, sym, "stop"))
            except Exception as ex:
                out["errors"].append(f"{sym}: protect {ex}")
                out["status"] = "needs_attention"
                emit("protect_failed", {"symbol": sym, "error": str(ex),
                                        "msg": "posición sin stop nativo: revisa a mano"})
                oid = None
            if oid:
                pos["stop_order_id"] = oid
                stops.add(oid)
                out["protected"].append(sym)
                emit("reconcile_protect", {"symbol": sym, "order_id": oid, "stop": pos["stop"]})

    try:
        orders = broker.open_orders() or []
    except Exception as ex:
        out["errors"].append(f"open_orders: {ex}")
        out["status"] = "needs_attention"   # puede haber stops huérfanos vivos
        orders = []
    prefix = f"{cfg.client_id_prefix}-"
    for o in orders:
        oid = o.get("id")
        cid = o.get("clientOrderId") or (o.get("info") or {}).get("clientOrderId") or ""
        if oid in stops or not str(cid).startswith(prefix):
            continue
        sym = o.get("symbol")
        try:
            broker.cancel(sym, oid)
            out["cancelled"].append(oid)
            emit("reconcile_cancel", {"symbol": sym, "order_id": oid, "client_order_id": cid})
        except Exception as ex:
            out["errors"].append(f"cancel {oid}: {ex}")
    return out


def _client_id(cfg, symbol, kind):
    from .broker import client_id
    return client_id(cfg, symbol, kind)
