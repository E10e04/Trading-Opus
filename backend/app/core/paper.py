"""Paper trading engine - the ONLY execution destination in this application.
No route to a real broker exists or can be enabled. Portfolios are persistent
and independent: initial capital, cash, positions, exposure, fees, realized
and unrealized P&L, full fill history.

Models: market, limit, stop, stop-limit. Bid/ask from a configurable spread
around the mid price; slippage in basis points; commission in percent.
An order is never "filled" merely because it was submitted.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class PaperTradingError(Exception):
    pass


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    ts: datetime
    reason: str = ""

    def as_dict(self):
        return {"order_id": self.order_id, "symbol": self.symbol, "side": self.side,
                "qty": self.qty, "price": self.price, "fee": self.fee,
                "ts": self.ts.isoformat(), "reason": self.reason}


@dataclass
class Position:
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def as_dict(self):
        return {"qty": self.qty, "avg_price": self.avg_price,
                "realized_pnl": round(self.realized_pnl, 2)}


@dataclass
class PaperOrder:
    order_id: str
    symbol: str
    side: str            # buy | sell
    qty: float
    type: str            # market | limit | stop | stop_limit
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: str = "new"  # new -> working -> filled | canceled | rejected
    filled_qty: float = 0.0
    filled_price: Optional[float] = None
    fees: float = 0.0
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fills: list = field(default_factory=list)
    note: str = ""

    def as_dict(self):
        return {"order_id": self.order_id, "symbol": self.symbol, "side": self.side,
                "qty": self.qty, "type": self.type, "limit_price": self.limit_price,
                "stop_price": self.stop_price, "status": self.status,
                "filled_qty": self.filled_qty, "filled_price": self.filled_price,
                "fees": self.fees, "created": self.created.isoformat(),
                "fills": self.fills, "note": self.note}


@dataclass
class PaperPortfolio:
    id: str
    initial_capital: float
    cash: float
    positions: dict = field(default_factory=dict)      # symbol -> Position
    realized_pnl: float = 0.0
    orders: list = field(default_factory=list)
    _seq: int = 0

    # ---- valuation ----
    def market_value(self, prices: dict) -> float:
        return sum(p.qty * float(prices.get(sym, p.avg_price)) for sym, p in self.positions.items())

    def equity(self, prices: dict) -> float:
        return self.cash + self.market_value(prices)

    def unrealized_pnl(self, prices: dict) -> float:
        return sum(p.qty * (float(prices.get(sym, p.avg_price)) - p.avg_price)
                   for sym, p in self.positions.items() if p.qty != 0)

    def exposure(self, prices: dict) -> float:
        eq = self.equity(prices)
        return self.market_value(prices) / eq if eq > 0 else 0.0

    def as_dict(self, prices=None):
        return {"id": self.id, "initial_capital": self.initial_capital,
                "cash": round(self.cash, 2),
                "positions": {s: p.as_dict() for s, p in self.positions.items() if p.qty != 0},
                "realized_pnl": round(self.realized_pnl, 2),
                "orders": [o.as_dict() for o in self.orders[-100:]],
                "valuation": None if prices is None else {
                    "equity": round(self.equity(prices), 2),
                    "market_value": round(self.market_value(prices), 2),
                    "unrealized_pnl": round(self.unrealized_pnl(prices), 2),
                    "exposure_pct": round(self.exposure(prices) * 100, 2)}}


class PaperEngine:
    """Deterministic simulation engine. Prices come from the MarketDataProvider
    (BiQuote live ticks, or replayed/fixture prices in tests)."""

    def __init__(self, commission_pct=0.0, slippage_bps=5, spread_pct=0.02):
        self.commission_pct = float(commission_pct)
        self.slippage_bps = float(slippage_bps)
        self.spread_pct = float(spread_pct)   # half-spread as percent of mid
        self.portfolios: dict[str, PaperPortfolio] = {}

    # ---- portfolios ----
    def create_portfolio(self, pid: str, initial_capital: float) -> PaperPortfolio:
        if pid in self.portfolios:
            raise PaperTradingError("portfolio already exists: " + pid)
        pf = PaperPortfolio(id=pid, initial_capital=float(initial_capital),
                            cash=float(initial_capital))
        self.portfolios[pid] = pf
        return pf

    def get(self, pid: str) -> PaperPortfolio:
        if pid not in self.portfolios:
            raise PaperTradingError("unknown portfolio: " + pid)
        return self.portfolios[pid]

    # ---- pricing ----
    def _bid_ask(self, mid: float):
        half = mid * self.spread_pct / 100.0
        return mid - half, mid + half

    def _exec_price(self, side: str, mid: float) -> float:
        bid, ask = self._bid_ask(mid)
        base = ask if side == "buy" else bid
        slip = base * (self.slippage_bps / 10000.0) * (1 if side == "buy" else -1)
        return round(base + slip, 4)

    def _fee(self, notional: float) -> float:
        return round(notional * self.commission_pct / 100.0, 4)

    # ---- orders ----
    def submit(self, pid: str, symbol: str, side: str, qty: float, type_: str = "market",
               limit_price=None, stop_price=None, mid_price: Optional[float] = None,
               note: str = "") -> PaperOrder:
        pf = self.get(pid)
        if side not in ("buy", "sell"):
            raise PaperTradingError("side must be buy or sell")
        if qty is None or qty <= 0:
            raise PaperTradingError("qty must be positive")
        if type_ not in ("market", "limit", "stop", "stop_limit"):
            raise PaperTradingError("unsupported order type: " + type_)
        if type_ == "market" and mid_price is None:
            raise PaperTradingError("a market order needs a live mid price")
        if type_ in ("limit", "stop_limit") and limit_price is None:
            raise PaperTradingError("limit/stop_limit order needs limit_price")
        if type_ in ("stop", "stop_limit") and stop_price is None:
            raise PaperTradingError("stop/stop_limit order needs stop_price")
        held = pf.positions.get(symbol).qty if symbol in pf.positions else 0.0
        if side == "sell" and qty > held:
            raise PaperTradingError("sell of more than held (short selling is off)")

        pf._seq += 1
        o = PaperOrder(order_id=pid + "-" + str(pf._seq), symbol=symbol.upper(),
                       side=side, qty=float(qty), type=type_, limit_price=limit_price,
                       stop_price=stop_price, note=note)
        pf.orders.append(o)

        if type_ == "market":
            o.status = "filled"
            self._apply_fill(pf, o, self._exec_price(side, float(mid_price)),
                              reason="market fill at bid/ask + slippage")
        else:
            o.status = "working"
        return o

    def on_tick(self, pid: str, symbol: str, mid: float) -> list:
        """Drive working conditional orders with a new mid price. Returns fills."""
        pf = self.get(pid)
        fills = []
        for o in pf.orders:
            if o.status != "working" or o.symbol != symbol.upper():
                continue
            trigger = False
            if o.type == "limit":
                trigger = (o.side == "buy" and mid <= o.limit_price) or \
                          (o.side == "sell" and mid >= o.limit_price)
            elif o.type == "stop":
                trigger = (o.side == "buy" and mid >= o.stop_price) or \
                          (o.side == "sell" and mid <= o.stop_price)
            elif o.type == "stop_limit":
                trigger = (o.side == "buy" and mid >= o.stop_price) or \
                          (o.side == "sell" and mid <= o.stop_price)
            if not trigger:
                continue
            if o.type == "stop_limit":
                px = o.limit_price
            else:
                px = self._exec_price(o.side, mid)   # a stop can fill past its price
            o.status = "filled"
            self._apply_fill(pf, o, px, reason="conditional trigger on tick " + str(mid))
            fills.append(o)
        return fills

    def _apply_fill(self, pf: PaperPortfolio, o: PaperOrder, price: float, reason: str):
        notional = o.qty * price
        fee = self._fee(notional)
        pos = pf.positions.setdefault(o.symbol, Position())
        if o.side == "buy":
            if pf.cash < notional + fee:
                o.status = "rejected"
                o.note = "insufficient cash"
                return
            pf.cash -= notional + fee
            total_qty = pos.qty + o.qty
            pos.avg_price = ((pos.avg_price * pos.qty) + notional) / total_qty if total_qty else 0.0
            pos.qty = total_qty
        else:
            proceeds = notional - fee
            pf.cash += proceeds
            pnl = (price - pos.avg_price) * o.qty - fee
            pos.realized_pnl += pnl
            pf.realized_pnl += pnl
            pos.qty -= o.qty
            if abs(pos.qty) < 1e-9:
                pos.qty = 0.0
                pos.avg_price = 0.0
        o.filled_qty = o.qty
        o.filled_price = price
        o.fees = fee
        o.fills.append(Fill(order_id=o.order_id, symbol=o.symbol, side=o.side,
                            qty=o.qty, price=price, fee=fee,
                            ts=datetime.now(timezone.utc), reason=reason).as_dict())

    def cancel(self, pid: str, order_id: str) -> PaperOrder:
        pf = self.get(pid)
        for o in pf.orders:
            if o.order_id == order_id and o.status == "working":
                o.status = "canceled"
                return o
        raise PaperTradingError("working order not found: " + order_id)