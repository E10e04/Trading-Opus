"""Agent Alerts - port of trader-alerts/alerts.py (day-trader-skill @ 3d0f4ef, MIT).
Rings once per rule: above/below, stop, target, pct_move, time, opening_bell.
Knows the NYSE clock (holidays and early closes 2026-2027, DST computed).
A rule with a date is watched on that date only, so yesterday's stop never
rings today. Prices come from BiQuote (live) instead of delayed Yahoo quotes.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, time

from .base import AgentSpec, BaseAgent, AgentResult
from ..core.calendar import market_day, market_state_at, now_new_york, is_trading_day

ALERTS_SPEC = AgentSpec(
    key="alerts", name="Agent Alerts", skill="trader-alerts",
    mission="Ring when a price, a stop or the clock hits. Each alert rings once. "
            "An alert is information, never an order.",
    deterministic=True,
)


@dataclass
class AlertRule:
    rule_id: str
    type: str            # above|below|stop|target|pct_move|time|opening_bell
    symbol: str = None
    price: float = None
    pct: float = None
    at: str = None       # "15:55"
    label: str = ""
    when: date = None    # watched on that New York date only
    rearm: bool = False
    before_close: bool = False
    ref_close: float = None

    def as_dict(self):
        return {"rule_id": self.rule_id, "type": self.type, "symbol": self.symbol,
                "price": self.price, "pct": self.pct, "at": self.at,
                "label": self.label, "when": str(self.when), "rearm": self.rearm}


class AlertsAgent(BaseAgent):
    spec = ALERTS_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.rules: list[AlertRule] = []
        self.rung: dict = {}          # rule_id -> date last rung

    def add_from_plan(self, plan):
        """The three plan alerts in one go: the stop, the target, flat by close."""
        sym = plan.get("symbol", "")
        day = plan.get("date")
        for t, key, label in (("stop", "stop", "plan stop"),
                              ("target", "target", "plan target")):
            if plan.get(key) is not None:
                self.rules.append(AlertRule(
                    rule_id=sym.lower() + "-plan-" + t, type=t, symbol=sym,
                    price=float(plan[key]), label=label, when=day))
        if plan.get("flat_by"):
            self.rules.append(AlertRule(
                rule_id=sym.lower() + "-flat-by", type="time", symbol=sym,
                at=str(plan["flat_by"]), label="flat by close", when=day,
                before_close=True))

    def check(self, prices: dict, now_ny: datetime = None) -> list[dict]:
        """prices: {symbol: mid}. Rings each rule at most once per day.
        Stale data never rings: a price without a tick this session is skipped."""
        now_ny = now_ny or now_new_york()
        today = now_ny.date()
        rang = []
        for r in self.rules:
            if r.when is not None and str(r.when) != str(today):
                continue                        # yesterday's rule never rings today
            last = self.rung.get(r.rule_id)
            if last == today and not r.rearm:
                continue
            hit, detail = False, ""
            if r.type in ("above", "target"):
                p = prices.get(r.symbol)
                hit = p is not None and p >= r.price
                detail = (r.symbol + " at " + format(p, ".2f") + " >= "
                          + format(r.price, ".2f")) if hit else ""
            elif r.type in ("below", "stop"):
                p = prices.get(r.symbol)
                hit = p is not None and p <= r.price
                detail = (r.symbol + " at " + format(p, ".2f") + " <= "
                          + format(r.price, ".2f")) if hit else ""
            elif r.type == "pct_move":
                p = prices.get(r.symbol)
                if p is not None and r.ref_close:
                    move = (p - r.ref_close) / r.ref_close * 100
                    hit = abs(move) >= r.pct
                    detail = r.symbol + " moved " + format(move, ".2f") + "%"
            elif r.type == "time":
                at = r.at
                if r.before_close and market_day(today).early_close:
                    at = "12:55"               # 3 hours earlier on early closes
                hit = now_ny.strftime("%H:%M") >= at and is_trading_day(today)
                detail = "it is " + now_ny.strftime("%H:%M") + " New York"
            elif r.type == "opening_bell":
                hit = market_state_at(now_ny) == "open"
                detail = "the NYSE is open"
            if hit:
                self.rung[r.rule_id] = today
                rang.append({"rule_id": r.rule_id, "label": r.label, "detail": detail,
                             "ts": now_ny.isoformat()})
        return rang

    def run_impl(self, prices=None, now_ny=None, **_):
        rang = self.check(prices or {}, now_ny=now_ny)
        return AgentResult(status="success", data={
            "rang": rang,
            "rules": [r.as_dict() for r in self.rules],
            "note": "an alert is a fact about a price or the clock, never an order",
        })