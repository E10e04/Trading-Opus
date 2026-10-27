"""OpenRouter client - the AI provider. The API key stays backend-only:
never in the frontend, the logs, the Git repo or the build files. Deterministic
agents never call it; only configured agents do, and the numbers stay in code.
"""
from __future__ import annotations
import time
from typing import Optional


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    def __init__(self, api_key: str = "", base_url: str = "https://openrouter.ai/api/v1",
                 timeout_s: float = 60.0, monthly_budget_usd: float = 10.0, http=None):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.monthly_budget_usd = float(monthly_budget_usd)
        self.http = http                      # injected httpx.AsyncClient (tests)
        self.spend_usd = 0.0
        self.calls = 0
        self.errors = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def status(self) -> dict:
        """Connection test + model list. Never echoes the key."""
        out = {"configured": self.configured, "base_url": self.base_url,
               "spend_usd": round(self.spend_usd, 4),
               "monthly_budget_usd": self.monthly_budget_usd,
               "calls": self.calls, "errors": self.errors,
               "models": []}
        if not self.configured:
            out["note"] = "OPENROUTER_API_KEY is not set; AI-assisted agents run in "
                          "their deterministic mode only"
            return out
        try:
            r = await self._get("/models")
            out["models"] = [m.get("id") for m in r.get("data", [])][:100]
            out["connection"] = "ok"
        except Exception as e:
            out["connection"] = "error"
            out["error"] = str(e)
            self.errors += 1
        return out

    async def complete(self, model: str, messages: list, max_tokens: int = 800) -> dict:
        """A chat completion. Budget-checked: over the monthly limit the call is
        refused, never silently allowed."""
        if not self.configured:
            raise OpenRouterError("OpenRouter is not configured (missing API key)")
        if self.spend_usd > self.monthly_budget_usd:
            raise OpenRouterError("monthly budget exceeded: AI calls are refused")
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        headers = {"Authorization": "Bearer " + self.api_key,
                   "Content-Type": "application/json"}
        t0 = time.monotonic()
        self.calls += 1
        try:
            r = await self.http.post(self.base_url + "/chat/completions",
                                     json=payload, headers=headers)
            r.raise_for_status()
        except Exception as e:
            self.errors += 1
            raise OpenRouterError("OpenRouter call failed: " + str(e))
        data = r.json()
        usage = data.get("usage") or {}
        cost = usage.get("cost") or 0.0
        self.spend_usd += float(cost or 0.0)
        return {"text": (data.get("choices") or [{}])[0].get("message", {}).get("content"),
                "usage": usage, "cost_usd": float(cost or 0.0),
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "model": model}