"""Agent framework - one agent per skill of day-trader-skill (mandatory rule).
Each agent has: an identity, a mission, a skill-based implementation, structured
inputs and outputs, configurable parameters, an execution state, an activity
history, error handling, an optional AI (OpenRouter) configuration, and its own
tests. The orchestrator runs them and keeps the audit trail.
"""
from __future__ import annotations
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

SKILL_REF_COMMIT = "3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd"
SKILL_REPO = "Jakeschincariol/day-trader-skill"


@dataclass(frozen=True)
class AgentSpec:
    key: str                    # scanner, news, entry, ...
    name: str
    skill: str                  # trader-scanner, ...
    mission: str
    deterministic: bool          # does its skill need no AI model?
    default_model: Optional[str] = None   # OpenRouter model when AI is used


@dataclass
class AgentResult:
    status: str                 # success | error | partial | skipped
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    started: Optional[float] = None
    duration_ms: Optional[int] = None

    def as_dict(self):
        return {"status": self.status, "data": self.data, "errors": self.errors,
                "notes": self.notes, "duration_ms": self.duration_ms}


class BaseAgent:
    """Common execution wrapper: state, history, audit fields, error capture.
    An agent's failure never blocks the others (orchestrator isolates runs)."""

    spec: AgentSpec = None

    def __init__(self, params: Optional[dict] = None, model: Optional[str] = None,
                 llm_client=None):
        self.params = dict(params or {})
        self.model = model or (self.spec.default_model if self.spec else None)
        self.llm = llm_client          # None for deterministic agents
        self.state = "idle"           # idle | running | error
        self.history: list[dict] = []

    # ---- to implement per agent ----
    def run_impl(self, **kwargs) -> AgentResult:
        raise NotImplementedError

    def required_inputs(self, kwargs) -> list:
        """Names of missing required inputs; [] when everything is there."""
        return []

    # ---- execution ----
    def run(self, **kwargs) -> AgentResult:
        missing = self.required_inputs(kwargs)
        if missing:
            r = AgentResult(status="skipped",
                            notes=["missing required inputs: " + ", ".join(missing)])
            self._record(r, kwargs)
            return r
        started = time.monotonic()
        self.state = "running"
        try:
            res = self.run_impl(**kwargs)
        except Exception as e:                     # an error is captured, never faked
            res = AgentResult(status="error", errors=[str(e),
                                                      traceback.format_exc(limit=2)])
        finally:
            self.state = "idle"
        res.started = started
        res.duration_ms = int((time.monotonic() - started) * 1000)
        self._record(res, kwargs)
        return res

    def _record(self, res: AgentResult, kwargs):
        self.history.append({
            "agent": self.spec.key if self.spec else type(self).__name__,
            "skill": self.spec.skill if self.spec else "?",
            "skill_ref": SKILL_REF_COMMIT,
            "model": self.model,
            "instrument": kwargs.get("symbol") or kwargs.get("symbols"),
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": res.status,
            "duration_ms": res.duration_ms,
            "result": res.as_dict(),
            "errors": res.errors,
        })
        self.history = self.history[-100:]         # bounded history

    def status(self) -> dict:
        return {
            "key": self.spec.key if self.spec else "?",
            "name": self.spec.name if self.spec else "?",
            "skill": self.spec.skill if self.spec else "?",
            "skill_ref": SKILL_REF_COMMIT,
            "mission": self.spec.mission if self.spec else "",
            "deterministic": self.spec.deterministic if self.spec else True,
            "model": self.model,
            "state": self.state,
            "last_result": self.history[-1]["result"] if self.history else None,
            "runs": len(self.history),
        }

    def spec_dict(self) -> dict:
        s = self.spec
        return {"key": s.key, "name": s.name, "skill": s.skill,
                "skill_ref": SKILL_REF_COMMIT, "mission": s.mission,
                "deterministic": s.deterministic, "default_model": s.default_model}