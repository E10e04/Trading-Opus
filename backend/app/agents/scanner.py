"""Agent Scanner - one agent per skill: trader-scanner/scan.py
(day-trader-skill @ 3d0f4ef, MIT).

Runs the daily scan setups (breakout_20d, bull_flag, reclaim_ma20, oversold,
oversold_bounce) on daily closes and the opening-range breakout scan on
5-minute bars. A scan is a list of charts that match a rule, NOT a list of
stocks to buy. Filters out insufficient or stale data and says why a chart
was kept or dropped.
"""
from __future__ import annotations
from dataclasses import dataclass

from .base import AgentSpec, BaseAgent, AgentResult
from ..core.rsi import daily_setups
from ..core.entry import build_opening_range, check_entry
from ..market.base import Tick

SCANNER_SPEC = AgentSpec(
    key="scanner", name="Agent Scanner", skill="trader-scanner",
    mission="Spot instruments and market situations worth examining: daily setups "
            "and opening-range breakouts. Explains why each chart was selected.",
    deterministic=True,
)


@dataclass
class ScanInput:
    daily_closes: dict            # symbol -> list of daily closes (chronological)
    intraday_bars: dict = None    # symbol -> list of 5m bars (start, high, low, close, volume)
    now = None                    # pinned clock for replays


class ScannerAgent(BaseAgent):
    spec = SCANNER_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.params.setdefault("min_price", 5.0)
        self.params.setdefault("min_volume", 1_000_000)

    def required_inputs(self, kwargs):
        missing = []
        if not kwargs.get("daily_closes") and not kwargs.get("intraday_bars"):
            missing.append("daily_closes or intraday_bars")
        return missing

    def run_impl(self, daily_closes=None, intraday_bars=None, now=None, **_):
        hits, dropped = [], []

        # ---- daily scan (before the open) ----
        for sym, closes in (daily_closes or {}).items():
            if len(closes) < 16:
                dropped.append({"symbol": sym, "reason": "insufficient history (<16 closes)"})
                continue
            last = closes[-1]
            if last < self.params.get("min_price", 5.0):
                dropped.append({"symbol": sym, "reason": "under min_price"})
                continue
            setups = daily_setups(closes)
            if setups:
                hits.append({"symbol": sym, "setups": sorted(setups),
                             "last_close": last, "why": "matched rule(s): "
                             + ", ".join(sorted(setups))})

        # ---- opening-range breakout scan (during the session) ----
        for sym, bars in (intraday_bars or {}).items():
            rng = build_opening_range(bars, or_minutes=15, interval=5)
            if rng is None:
                dropped.append({"symbol": sym, "reason": "opening range incomplete"})
                continue
            sig = check_entry(bars, now) if now is not None else None
            if sig is not None and sig.status == "triggered":
                hits.append({
                    "symbol": sym, "setups": ["orb"],
                    "or_low": rng.low, "or_high": rng.high,
                    "trigger_price": sig.price, "trigger_time": sig.time.isoformat() if sig.time else None,
                    "why": "first completed 5m bar closed above the opening-range high"})

        return AgentResult(status="success", data={
            "hits": hits, "dropped": dropped,
            "scanned": len(daily_closes or {}) + len(intraday_bars or {}),
            "note": "matched a rule, never 'a buy'. A hit is stale once the price moved on.",
        })