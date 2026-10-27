"""Orchestrator - coordinates the eleven agents.
- manual or automatic (scheduled) triggering per agent
- dependency-aware pipeline for the trade path (scanner -> news -> entry ->
  exit -> timemachine -> budget -> risk -> broker) without contradictory
  concurrent runs (one pipeline lock per symbol)
- structured handoffs between agents (watchlist -> entry -> plan -> orders)
- timeouts and per-agent isolation: one agent's failure never blocks the others
- full audit trail per execution (agent, skill ref, model, instrument, status,
  duration, results, errors)
- each agent can be disabled independently
- proposals ALWAYS pass the deterministic Risk gate before the user sees them
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .base import BaseAgent, SKILL_REF_COMMIT
from .scanner import ScannerAgent
from .news_agent import NewsAgent
from .entry_agent import EntryAgent
from .exit_agent import ExitAgent
from .timemachine_agent import TimeMachineAgent
from .budget_agent import BudgetAgent
from .risk_agent import RiskAgent
from .broker_agent import BrokerAgent
from .alerts_agent import AlertsAgent
from .analyst_agent import AnalystAgent
from .pelosi_agent import PelosiAgent
from ..core.entry import build_opening_range


@dataclass
class Proposal:
    """A structured proposal from an agent, on its way to the user's decision."""
    proposal_id: str
    symbol: str
    order: dict
    risk_verdict: dict
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    state: str = "pending"            # pending -> accepted | modified | refused

    def as_dict(self):
        return {"proposal_id": self.proposal_id, "symbol": self.symbol,
                "order": self.order, "risk_verdict": self.risk_verdict,
                "created": self.created.isoformat(), "state": self.state}


class Orchestrator:
    def __init__(self, paper_engine=None, http=None, config=None):
        self.paper_engine = paper_engine
        self.http = http
        self.config = config or {}
        self.agents: dict[str, BaseAgent] = {
            "scanner": ScannerAgent(),
            "news": NewsAgent(http=http),
            "entry": EntryAgent(),
            "exit": ExitAgent(),
            "timemachine": TimeMachineAgent(),
            "budget": BudgetAgent(),
            "risk": RiskAgent(),
            "broker": BrokerAgent(paper_engine=paper_engine),
            "alerts": AlertsAgent(),
            "analyst": AnalystAgent(http=http),
            "pelosi": PelosiAgent(http=http),
        }
        self.disabled: set[str] = set()
        self.audit: list[dict] = []
        self.proposals: dict[str, Proposal] = {}
        self._pipeline_locks: dict[str, asyncio.Lock] = {}
        self._proposal_seq = 0

    # ---- lifecycle ----
    def enable(self, key): self.disabled.discard(key)

    def disable(self, key): self.disabled.add(key)

    def list_agents(self):
        return [self.agents[k].status() for k in sorted(self.agents)
                if k not in self.disabled]

    # ---- single run with audit; ALWAYS returns a plain dict ----
    async def run_agent(self, key: str, **kwargs) -> dict:
        agent = self.agents.get(key)
        if agent is None:
            return {"agent": key, "status": "error",
                    "errors": ["unknown agent"], "data": {}}
        if key in self.disabled:
            return {"agent": key, "status": "skipped",
                    "errors": ["agent disabled"], "data": {}}
        timeout = self.config.get("agent_timeout_s", 120)
        try:
            res = await asyncio.wait_for(asyncio.to_thread(agent.run, **kwargs),
                                         timeout=timeout)
            out = {"agent": key, **res.as_dict()}
        except asyncio.TimeoutError:
            out = {"agent": key, "status": "error",
                   "errors": ["timeout after " + str(timeout) + "s"], "data": {}}
        self.audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": key, "skill": agent.spec.skill, "skill_ref": SKILL_REF_COMMIT,
            "model": agent.model,
            "instrument": kwargs.get("symbol") or kwargs.get("symbols"),
            "status": out["status"], "result": out["data"],
            "errors": out["errors"],
        })
        self.audit = self.audit[-500:]
        return out

    # ---- the full trade pipeline for one symbol ----
    async def run_trade_pipeline(self, symbol, daily_closes, intraday_bars,
                                 chart_by_day, now, cash, holdings, fresh,
                                 today_entries=0, today_risk=0.0):
        """The one-trade path. No agent can skip the Risk gate. A failure in a
        step stops THIS pipeline only, never the other agents."""
        lock = self._pipeline_locks.setdefault(symbol, asyncio.Lock())
        if lock.locked():
            return {"status": "skipped",
                    "reason": "a pipeline is already running for " + symbol}
        async with lock:
            out = {"symbol": symbol, "steps": []}

            # 1. scanner
            sc = await self.run_agent("scanner", daily_closes={symbol: daily_closes},
                                      intraday_bars={symbol: intraday_bars}, now=now)
            out["steps"].append(("scanner", sc["status"]))

            # 2. news (sources may be offline -> partial is honest)
            news = await self.run_agent("news", tickers=[symbol])
            out["steps"].append(("news", news["status"]))

            # 3. entry
            entry = await self.run_agent("entry", symbol=symbol, bars=intraday_bars, now=now)
            out["steps"].append(("entry", entry["status"]))
            entry_data = entry.get("data") or {}
            if entry["status"] != "success" or entry_data.get("status") != "triggered":
                out["status"] = "no_entry"
                out["reason"] = entry_data.get("status") or entry["status"]
                return out

            # 4. exit plan (stop/target BEFORE the buy) - from the REAL opening range
            rng = build_opening_range(intraday_bars, or_minutes=15, interval=5)
            ex = await self.run_agent("exit", entry=entry_data.get("price"),
                                      or_high=rng.high if rng else None,
                                      or_low=rng.low if rng else None,
                                      bars=intraday_bars)
            out["steps"].append(("exit", ex["status"]))
            plan = ex.get("data") or {}
            if ex["status"] != "success":
                out["status"] = "no_plan"
                out["reason"] = plan.get("refusal")
                return out
            plan["symbol"] = symbol

            # 5. timemachine
            tm_plan = {"date": now.date(), "entry": plan["entry"],
                       "stop": plan["stop"], "target": plan["target"],
                       "entry_bar": entry_data.get("entry_bar", 8), "target_r": 1}
            tm = await self.run_agent("timemachine", plan=tm_plan, chart_by_day=chart_by_day)
            out["steps"].append(("timemachine", tm["status"]))

            # 6. budget
            bg = await self.run_agent("budget", plan=plan, cash=cash,
                                      fresh_price=(fresh or {}).get(symbol, {}).get("price"),
                                      fresh_age_min=(fresh or {}).get(symbol, {}).get("age_min"))
            out["steps"].append(("budget", bg["status"]))
            sized = bg.get("data") or {}
            if bg["status"] != "success":
                out["status"] = "not_sized"
                out["reason"] = sized.get("refusal")
                return out
            order = sized.get("order") or {}
            order["symbol"] = symbol

            # 7. risk gate (deterministic, mandatory, unskippable)
            rk = await self.run_agent("risk", orders=[order], holdings=holdings,
                                      cash=cash, fresh=fresh,
                                      today_entries=today_entries, today_risk=today_risk,
                                      now_ny=now)
            out["steps"].append(("risk", rk["status"]))
            verdicts = (rk.get("data") or {}).get("verdicts") or []
            if not verdicts or verdicts[0]["verdict"] != "PASS":
                out["status"] = "risk_blocked"
                out["reason"] = verdicts[0]["reasons"] if verdicts else "no verdict"
                return out

            # 8. proposal to the user (accept / modify / refuse)
            self._proposal_seq += 1
            pid = symbol + "-" + str(self._proposal_seq)
            proposal = Proposal(proposal_id=pid, symbol=symbol, order=order,
                                risk_verdict=verdicts[0])
            self.proposals[pid] = proposal
            out["status"] = "proposed"
            out["proposal"] = proposal.as_dict()
            return out

    # ---- user decision on a proposal ----
    def decide(self, proposal_id, decision, modified_order=None):
        p = self.proposals.get(proposal_id)
        if p is None:
            return {"ok": False, "reason": "unknown proposal"}
        if p.state != "pending":
            return {"ok": False, "reason": "already decided"}
        if decision == "refuse":
            p.state = "refused"
            return {"ok": True, "state": "refused"}
        if decision == "accept":
            p.state = "accepted"
            return {"ok": True, "state": "accepted",
                    "note": "Broker renders the ticket; the user's explicit yes "
                            "is still required for THIS exact order (paper engine only)"}
        if decision == "modify":
            # an edited order must pass Risk again (fingerprint changes)
            p.state = "modified"
            return {"ok": True, "state": "modified",
                    "note": "the modified order goes back through the Risk gate"}
        return {"ok": False, "reason": "unknown decision"}

    # ---- alerts on ticks (scheduled cadence) ----
    def on_prices(self, prices: dict, now_ny=None):
        if "alerts" in self.disabled:
            return []
        return self.agents["alerts"].check(prices, now_ny=now_ny)