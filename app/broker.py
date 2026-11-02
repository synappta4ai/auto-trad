"""Ejecución. `execute(symbol, side, qty, ref_price, cid=None, reduce_only=False)`
-> (precio_fill, comision_en_quote, qty_real). side: +1 compra, -1 venta.

`cid` es el client order id: el exchange lo deduplica, así que reintentar tras
un timeout no abre dos veces la misma posición.

`paper_fill` y `paper_fee` son públicas a propósito: el simulador del
walk-forward (`app/sim.py`) las usa para medir con el mismo modelo de costos.

Los dos brokers exponen la misma interfaz de protección (`protect`, `cancel`,
`open_orders`, `position_qty`). En paper son no-ops: no hay nada que proteger.
"""
import logging
import time

log = logging.getLogger("broker")


def client_id(cfg, symbol, kind):
    """Client order id determinista por intento: el exchange lo deduplica.

    Formato: `at-BTCUSDT-open-1793595506123` (<= 36 caracteres, límite habitual).
    """
    base = symbol.replace("/", "").replace(":", "")
    return f"{cfg.client_id_prefix}-{base}-{kind}-{int(time.time() * 1000)}"


class NoBalance(RuntimeError):
    pass


def paper_fill(cfg, ref, side):
    """Precio de fill simulado: deslizamiento + medio spread, siempre en contra."""
    slip = (cfg.slippage_pct + cfg.spread_pct / 2.0) / 100.0
    return float(ref) * (1.0 + slip * side)


def paper_fee(cfg, qty, price):
    return abs(float(qty) * float(price)) * cfg.fee_pct / 100.0


class PaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg

    def execute(self, symbol, side, qty, ref_price, cid=None, reduce_only=False, **_):
        fill = paper_fill(self.cfg, ref_price, side)
        return fill, paper_fee(self.cfg, qty, fill), qty

    def quote_free(self):
        return None

    # --- protección: en paper no hay exchange, todo es no-op ---
    def protect(self, symbol, side, qty, stop_price, cid=None):
        return None

    def cancel(self, symbol, order_id):
        return False

    def open_orders(self, symbol=None):
        return []

    def position_qty(self, symbol):
        return None


class CcxtBroker:
    """Spot, órdenes de mercado. Úsalo primero con EXCHANGE_SANDBOX=true."""

    def __init__(self, cfg, feed):
        self.cfg = cfg
        self.ex = feed.ex
        self.quote = cfg.symbols[0].split("/")[-1]

    def execute(self, symbol, side, qty, ref_price, cid=None, reduce_only=False, **_):
        if side < 0:  # la comisión puede cobrarse en la moneda base: no vender más de lo disponible
            base = symbol.split("/")[0]
            qty = min(qty, float(self.ex.fetch_balance().get(base, {}).get("free", qty)))
        q = float(self.ex.amount_to_precision(symbol, qty))
        if q <= 0:
            raise NoBalance(f"cantidad {qty} inválida/sin saldo tras redondeo de precisión en {symbol}")
        params = {}
        if cid:
            params["clientOrderId"] = cid
        if reduce_only:
            # spot de Binance rechaza reduceOnly; ahí la protección es que el
            # stop vende exactamente la cantidad de la posición.
            params["reduceOnly"] = True
        order = self.ex.create_order(symbol, "market", "buy" if side > 0 else "sell", q, None, params)
        fill = order.get("average") or order.get("price") or ref_price
        filled = order.get("filled") or q
        fee = 0.0
        fee_info = order.get("fee") or {}
        if fee_info.get("cost") and fee_info.get("currency") == self.quote:
            fee = float(fee_info["cost"])
        else:
            fee = float(filled) * float(fill) * self.cfg.fee_pct / 100.0
        log.info("ORDEN LIVE %s %s qty=%s fill=%s fee=%.6f id=%s cid=%s",
                 symbol, "BUY" if side > 0 else "SELL", filled, fill, fee, order.get("id"), cid)
        return float(fill), fee, float(filled)

    def quote_free(self):
        return float(self.ex.fetch_balance().get(self.quote, {}).get("free", 0.0))

    # ---------------------------------------------------------------- protección
    def protect(self, symbol, side, qty, stop_price, cid=None):
        """Stop en el exchange que cierra la posición si el proceso se cae.

        Devuelve el id de la orden. El lado es el contrario a la posición.
        """
        q = float(self.ex.amount_to_precision(symbol, qty))
        if q <= 0:
            return None
        params = {"stopPrice": float(self.ex.price_to_precision(symbol, stop_price))}
        if cid:
            params["clientOrderId"] = cid
        order = self.ex.create_order(symbol, "stop_market", "sell" if side > 0 else "buy", q, None, params)
        log.info("STOP NATIVO %s %s qty=%s stop=%s id=%s", symbol, "SELL" if side > 0 else "BUY",
                 q, params["stopPrice"], order.get("id"))
        return order.get("id")

    def cancel(self, symbol, order_id):
        if not order_id:
            return False
        try:
            return bool(self.ex.cancel_order(order_id, symbol))
        except Exception as ex:
            # puede haberse ejecutado ya (el stop saltó): el cierre posterior lo dirá
            log.warning("cancel %s en %s falló: %s", order_id, symbol, ex)
            return False

    def open_orders(self, symbol=None):
        try:
            return self.ex.fetch_open_orders(symbol) if symbol else self.ex.fetch_open_orders()
        except Exception as ex:
            log.warning("fetch_open_orders falló: %s", ex)
            return []

    def position_qty(self, symbol):
        """Cantidad de la moneda base en la cuenta, o None si no se puede saber."""
        try:
            base = symbol.split("/")[0]
            return float(self.ex.fetch_balance().get(base, {}).get("total") or 0.0)
        except Exception as ex:
            log.warning("fetch_balance falló para %s: %s", symbol, ex)
            return None
