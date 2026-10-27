"""Agent TimeMachine - port of trader-timemachine/timemachine.py
(day-trader-skill @ 3d0f4ef, MIT). Replays the rest of the session N times from
the stock's own 5-minute bars and re-runs the rule on every past session.
Built from the last 60 days - NOT a prediction. Earnings, news and halts can
do things those 60 days never did.
"""
from __future__ import annotations
from .base import AgentSpec, BaseAgent, AgentResult
from ..core.timemachine import timemachine_run, timemachine_history, TimeMachineRefusal

TIMEMACHINE_SPEC = AgentSpec(
    key="timemachine", name="Agent TimeMachine", skill="trader-timemachine",
    mission="Replay every possible outcome of a trade before risking a dollar: "
            "N seeded simulations plus the real history of the same rule.",
    deterministic=True,
)


class TimeMachineAgent(BaseAgent):
    spec = TIMEMACHINE_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.params.setdefault("replays", 10000)
        self.params.setdefault("seed", 19)
        self.params.setdefault("window", 2)

    def required_inputs(self, kwargs):
        missing = []
        if not kwargs.get("plan"):
            missing.append("plan")
        if not kwargs.get("chart_by_day"):
            missing.append("chart_by_day")
        return missing

    def run_impl(self, plan, chart_by_day, **_):
        try:
            out = timemachine_run(plan, chart_by_day,
                                  replays=int(self.params["replays"]),
                                  seed=int(self.params["seed"]),
                                  window=int(self.params["window"]))
        except TimeMachineRefusal as e:
            return AgentResult(status="skipped", data={"refusal": str(e)},
                               notes=["the honest answer is that the pool is too "
                                      "small to say anything"])
        mc = out["mc"]
        return AgentResult(status="success", data=out, notes=[
            "seed " + str(mc["seed"]) + " - same plan, same data, same seed, "
            "the same numbers on any machine",
            "a simulation built from the stock's own recent bars, not a prediction",
            "verdict: " + out["verdict"],
        ])