"""Market data provider interface - any provider (BiQuote SignalR, Yahoo
historical, a fixture for tests) plugs in here without touching the agents
or the paper engine.
"""
from __future__ import annotations
import abc
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass(frozen=True)
class Tick:
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    mid: float
    source_ts: Optional[datetime]      # exchange/provider timestamp
    recv_ts: datetime                  # local reception timestamp
    provider: str

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        ref = self.source_ts or self.recv_ts
        return max(0.0, (now - ref).total_seconds())

    def as_dict(self):
        return {"symbol": self.symbol, "bid": self.bid, "ask": self.ask,
                "mid": self.mid,
                "source_ts": self.source_ts.isoformat() if self.source_ts else None,
                "recv_ts": self.recv_ts.isoformat(), "provider": self.provider}


class MarketDataProvider(abc.ABC):
    """A provider normalizes ticks, validates fields, and can be started and
    stopped cleanly. Agents and the paper engine depend only on this interface."""

    name: str = "base"

    @abc.abstractmethod
    async def start(self, symbols: list[str]) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @abc.abstractmethod
    def latest(self, symbol: str) -> Optional[Tick]: ...

    @abc.abstractmethod
    def subscribe(self, callback: Callable[[Tick], None]) -> None: ...

    def state(self) -> dict:
        return {"provider": self.name, "state": "unknown"}


def validate_tick_fields(symbol: str, price_like: float, bid=None, ask=None) -> bool:
    """Reject ticks with missing or non-positive prices - never let a bad tick
    through silently."""
    if not symbol or not isinstance(symbol, str):
        return False
    try:
        p = float(price_like)
    except (TypeError, ValueError):
        return False
    if p <= 0 or p > 1e12:
        return False
    for v in (bid, ask):
        if v is not None:
            fv = float(v)
            if fv <= 0 or fv > 1e12:
                return False
    return True