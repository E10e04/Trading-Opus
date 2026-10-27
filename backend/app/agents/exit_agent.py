"""Agent Exit - port of trader-exit/exit.py (day-trader-skill @ 3d0f4ef, MIT).
Sets the stop and the target BEFORE entering, from the real entry trigger or
the entry.json the Entry agent produced. A stop is a plan, not a guarantee.
Also evaluates open positions for exit conditions (stop, target, flat-by).
"""
from __future__ import annotations
from datetime import time

from .base import AgentSpec, BaseAgent, AgentResult
from ..core.exit import plan_exit, ExitRefusal

EXIT_SPEC = AgentSpec(
    key="exit", name="Agent Exit", skill="trader-exit",
    mission="Decide how a trade ends before it starts: stop, target, reward:risk, "
            "flat-by. Evaluates live positions against the plan.",
    deterministic=True,
)


class ExitAgent(BaseAgent):
    spec = EXIT_SPEC

    def __init__(self, params=None, model=None, llm_client=None):
        super().__init__(params, model, llm_client)
        self.params.setdefault("stop_rule", "or-mid")
        self.params.setdefault("target_rule", "2R")
        self.params.setdefault("flat_by", "15:55")

    def required_inputs(self, kwargs):
        if kwargs.get("mode") == "evaluate":
            return []
        missing = []
        if not kwargs.get("entry"):
            missing.append("entry")
        return missing

    def run_impl(self, mode="plan", entry=None, or_high=None, or_low=None,
                 stop_price=None, target_price=None, bars=None,
                 position=None, last_price=None, now=None, **_):
        if mode == "evaluate":
            return self._evaluate(position or {}, last_price, now)

        # ---- plan mode: stop and target BEFORE the entry ----
        hh, mm = (int(x) for x in self.params["flat_by"].split(":"))
        try:
            p = plan_exit(
                float(entry),
                stop_rule=self.params.get("stop_rule", "or-mid"),
                or_high=or_high, or_low=or_low, stop_price=stop_price,
                target_rule=self.params.get("target_rule", "2R"),
                target_price=target_price, flat_by=time(hh, mm), bars=bars)
        except ExitRefusal as e:
            return AgentResult(status="skipped", data={"refusal": str(e)},
                               notes=["refused with the reason; the fix is a wider "
                                      "stop or a farther target, never loosening rules"])
        d = p.as_dict()
        d["note"] = "set before the entry; a stop is a plan, not a guarantee"
        return AgentResult(status="success", data=d)

    def _evaluate(self, position, last_price, now):
        """Evaluate an OPEN position of the virtual portfolio against its plan.
        Uses only real portfolio data, never invented positions."""
        if not position:
            return AgentResult(status="skipped", data={"reason": "no open position"})
        sym = position.get("symbol")
        qty = position.get("qty", 0)
        stop = position.get("stop")
        target = position.get("target")
        notes = []
        proposal = None
        if last_price is None:
            notes.append("no fresh price: cannot evaluate the exit conditions")
        else:
            if stop and last_price <= float(stop):
                proposal = {"action": "close", "symbol": sym, "qty": qty,
                            "reason": "price at or under the stop: the setup failed"}
            elif target and last_price >= float(target):
                proposal = {"action": "close", "symbol": sym, "qty": qty,
                            "reason": "price at or over the target"}
            else:
                d = float(target) - last_price if target else None
                notes.append("between stop and target"
                             + ("; " + format(d, ".2f") + " to the target" if d else ""))
        if position.get("flat_by") and now is not None and str(position.get("flat_by")) <= now.strftime("%H:%M"):
            proposal = {"action": "flatten", "symbol": sym, "qty": qty,
                        "reason": "flat-by time reached: out by the close, win or lose"}
        return AgentResult(status="success",
                           data={"symbol": sym, "proposal": proposal}, notes=notes)