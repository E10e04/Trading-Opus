"""Agent Entry - port of trader-entry/entry.py (day-trader-skill @ 3d0f4ef, MIT).
Watches a setup bar by bar and tells the moment to get in: the first COMPLETED
bar that CLOSES above the level. It never places an order.
"""
from __future__ import annotations
from .base import AgentSpec, BaseAgent, AgentResult
from ..core.entry import check_entry

ENTRY_SPEC = AgentSpec(
    key="entry", name="Agent Entry", skill="trader-entry",
    mission="Watch a setup bar by bar and call the entry on the first completed "
            "bar that closes above the opening range or a given level.",
    deterministic=True,
)


class EntryAgent(BaseAgent):
    spec = ENTRY_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.params.setdefault("or_minutes", 15)
        self.params.setdefault("interval", 5)
        self.params.setdefault("min_volume_x", None)

    def required_inputs(self, kwargs):
        missing = []
        if not kwargs.get("symbol"):
            missing.append("symbol")
        if not kwargs.get("bars"):
            missing.append("bars")
        return missing

    def run_impl(self, symbol, bars, now, level=None, **_):
        sig = check_entry(bars, now, level=level,
                          or_minutes=self.params["or_minutes"],
                          interval=self.params["interval"],
                          min_volume_x=self.params["min_volume_x"])
        data = {"symbol": symbol, "status": sig.status, "detail": sig.detail,
                "level": sig.level}
        if sig.price is not None:
            data["price"] = sig.price
        if sig.time is not None:
            data["time"] = sig.time.isoformat()
        notes = []
        if sig.status == "triggered":
            notes.append("this is the moment to get in, if you take the trade - "
                         "nothing was placed")
            notes.append("next: set the stop and target BEFORE buying (Agent Exit)")
        return AgentResult(status="success", data=data, notes=notes)