"""Agent Risk - port of trader-risk/check.py (day-trader-skill @ 3d0f4ef, MIT).
The deterministic gate between a proposal and the paper broker. A BLOCK is
final for that order. The checks are code, not AI: no model can disable them.
"""
from __future__ import annotations
from .base import AgentSpec, BaseAgent, AgentResult
from ..core.risk import check_orders, order_fingerprint, DEFAULT_RISK_CONFIG

RISK_SPEC = AgentSpec(
    key="risk", name="Agent Risk", skill="trader-risk",
    mission="Check every trade against the limits and stop anything over them, "
            "before it can reach the paper broker. PASS means 'allowed to be shown', "
            "never a buy signal.",
    deterministic=True,
)


class RiskAgent(BaseAgent):
    spec = RISK_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.params.setdefault("config", {})

    def required_inputs(self, kwargs):
        if not kwargs.get("orders"):
            return ["orders"]
        return []

    def run_impl(self, orders, holdings=None, cash=None, fresh=None,
                 today_entries=0, today_risk=0.0, orders_placed_today=0,
                 now_ny=None, day_trades_5d=None, **_):
        cfg = dict(DEFAULT_RISK_CONFIG)
        cfg.update(self.params.get("config") or {})
        verdicts = check_orders(orders, config=cfg, holdings=holdings or {},
                                 cash=cash, fresh=fresh,
                                 today_entries=today_entries, today_risk=today_risk,
                                 orders_placed_today=orders_placed_today,
                                 now_ny=now_ny, day_trades_5d=day_trades_5d)
        passed = sum(1 for v in verdicts if v.verdict == "PASS")
        blocked = sum(1 for v in verdicts if v.verdict == "BLOCK")
        return AgentResult(status="success", data={
            "verdicts": [v.as_dict() for v in verdicts],
            "passed": passed, "blocked": blocked,
        }, notes=[
            "a PASS is a rules check, not a recommendation",
            "a blocked order never goes to the user for a yes",
        ] if blocked == 0 else [
            "blocked orders stay blocked: the fix is fewer shares, a closer stop, "
            "the window, or skipping the trade - never a looser limit",
        ])