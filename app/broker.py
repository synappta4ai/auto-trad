"""Ejecución. `execute(symbol, side, qty, ref_price)` -> (precio_fill, comision_en_quote, qty_real).
side: +1 compra, -1 venta."""
import logging

log = logging.getLogger("broker")


class NoBalance(RuntimeError):
    pass


class PaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg

    def execute(self, symbol, side, qty, ref_price, **_):
        slip = (self.cfg.slippage_pct + self.cfg.spread_pct / 2.0) / 100.0
        fill = ref_price * (1 + slip * side)
        fee = qty * fill * self.cfg.fee_pct / 100.0
        return fill, fee, qty

    def quote_free(self):
        return None


class CcxtBroker:
    """Spot, órdenes de mercado. Úsalo primero con EXCHANGE_SANDBOX=true."""

    def __init__(self, cfg, feed):
        self.cfg = cfg
        self.ex = feed.ex
        self.quote = cfg.symbols[0].split("/")[-1]

    def execute(self, symbol, side, qty, ref_price, **_):
        if side < 0:  # la comisión puede cobrarse en la moneda base: no vender más de lo disponible
            base = symbol.split("/")[0]
            qty = min(qty, float(self.ex.fetch_balance().get(base, {}).get("free", qty)))
        q = float(self.ex.amount_to_precision(symbol, qty))
        if q <= 0:
            raise NoBalance(f"cantidad {qty} inválida/sin saldo tras redondeo de precisión en {symbol}")
        order = self.ex.create_order(symbol, "market", "buy" if side > 0 else "sell", q)
        fill = order.get("average") or order.get("price") or ref_price
        filled = order.get("filled") or q
        fee = 0.0
        fee_info = order.get("fee") or {}
        if fee_info.get("cost") and fee_info.get("currency") == self.quote:
            fee = float(fee_info["cost"])
        else:
            fee = float(filled) * float(fill) * self.cfg.fee_pct / 100.0
        log.info("ORDEN LIVE %s %s qty=%s fill=%s fee=%.6f id=%s", symbol, "BUY" if side > 0 else "SELL", filled, fill, fee, order.get("id"))
        return float(fill), fee, float(filled)

    def quote_free(self):
        return float(self.ex.fetch_balance().get(self.quote, {}).get("free", 0.0))
