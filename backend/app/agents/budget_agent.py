"""Agent Budget - port of trader-budget/size.py (day-trader-skill @ 3d0f4ef, MIT).
Sizes the position to the budget and the max loss per trade. Never invents the
balance, fees, prices or missing financial parameters: every number comes from
the config, the plan or the portfolio.
"""
from __future__ import annotations
from .base import AgentSpec, BaseAgent, AgentResult
from ..core.budget import size_position, BudgetRefusal, DEFAULT_CONFIG

BUDGET_SPEC = AgentSpec(
    key="budget", name="Agent Budget", skill="trader-budget",
    mission="Turn the plan into an exact order: whole shares inside the four "
            "limits (max loss, position cap, max order, cash buffer).",
    deterministic=True,
)


class BudgetAgent(BaseAgent):
    spec = BUDGET_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.params.setdefault("config", {})
        self.params.setdefault("use_portfolio_cash", True)

    def required_inputs(self, kwargs):
        missing = []
        if not kwargs.get("plan"):
            missing.append("plan")
        return missing

    def run_impl(self, plan, cash=None, fresh_price=None, fresh_age_min=None, **_):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(self.params.get("config") or {})
        try:
            sized = size_position(plan, config=cfg, cash=cash,
                                  fresh_price=fresh_price, fresh_age_min=fresh_age_min,
                                  symbol=plan.get("symbol"))
        except BudgetRefusal as e:
            return AgentResult(status="skipped", data={"refusal": str(e)},
                               notes=["no order written: the reason is the answer"])
        d = sized.as_dict()
        d["limits_shown"] = cfg
        return AgentResult(status="success", data=d, notes=[
            "the stop decides the size: the further away, the fewer shares",
            "proposal only - Agent Risk checks it, the user decides",
        ])