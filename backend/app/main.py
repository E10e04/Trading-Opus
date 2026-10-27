"""Trading Opus - FastAPI application.
REST + WebSocket. Mounts the BiQuote provider, the paper engine, the 11-agent
orchestrator and the OpenRouter client. The API key never leaves this process.
"""
from __future__ import annotations
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import date

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .ai.openrouter import OpenRouterClient
from .agents.orchestrator import Orchestrator
from .core.paper import PaperEngine, PaperTradingError
from .db.models import make_engine, make_session, AgentRunRow, Portfolio, OrderRow
from .market.biquote import BiQuoteProvider, BiQuoteConfig


def load_env_file(path=".env"):
    """Minimal .env loader (python-dotenv independent, no dependency drift)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


load_env_file(".env")


class AppState:
    def __init__(self):
        self.engine = PaperEngine(
            commission_pct=float(os.environ.get("PAPER_COMMISSION_PCT", 0) or 0),
            slippage_bps=float(os.environ.get("PAPER_SLIPPAGE_BPS", 5) or 5),
            spread_pct=float(os.environ.get("PAPER_MARKUP_SPREAD_PCT", 0.02) or 0.02))
        default_cash = float(os.environ.get("PAPER_DEFAULT_CASH", 100000) or 100000)
        self.engine.create_portfolio("main", default_cash)
        self.http = httpx.AsyncClient(timeout=15)
        self.biquote = BiQuoteProvider(BiQuoteConfig(
            url=os.environ.get("BIQUOTE_URL", "https://biquote.io/hubs/tick"),
            symbols=tuple((os.environ.get("BIQUOTE_SYMBOLS", "EURUSD,XAUUSD,BTCUSD")
                           .replace(" ", "").split(",")))))
        self.openrouter = OpenRouterClient(
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            monthly_budget_usd=float(os.environ.get("OPENROUTER_MONTHLY_BUDGET_USD", 10) or 10),
            http=self.http)
        self.orch = Orchestrator(paper_engine=self.engine, http=self.http)
        self.db_engine = make_engine(os.environ.get("DATABASE_URL",
                                                    "sqlite:///./trading_opus.db"))
        self.ws_clients: set[WebSocket] = set()
        self._biquote_task = None

    async def start(self):
        def on_tick(tick):
            asyncio.get_event_loop().call_soon(self._fan_out, {
                "type": "tick", "tick": tick.as_dict()})
        self.biquote.subscribe(on_tick)
        self._biquote_task = asyncio.create_task(self.biquote.start())

    async def stop(self):
        await self.biquote.stop()
        await self.http.aclose()

    def _fan_out(self, payload: dict):
        dead = []
        for ws in self.ws_clients:
            try:
                asyncio.get_event_loop().create_task(ws.send_text(json.dumps(payload)))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.start()
    yield
    await state.stop()


app = FastAPI(title="Trading Opus", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


# ------------------------------- system -------------------------------
@app.get("/api/v1/system/status")
async def system_status():
    b = state.biquote.state()
    return {"system": "ok", "biquote": b,
            "agents": state.orch.list_agents(),
            "paper": {"portfolios": list(state.engine.portfolios)}}


@app.get("/api/v1/system/diagnostic")
async def diagnostic():
    """Honest diagnostic: what is connected, what is stale, what is disabled."""
    return {
        "biquote": state.biquote.state(),
        "openrouter": await state.openrouter.status(),
        "agents": [{"key": k, "disabled": k in state.orch.disabled,
                    "state": a.state} for k, a in state.orch.agents.items()],
        "data_freshness": "ticks older than " + str(state.biquote.cfg.stale_seconds)
                          + "s are flagged STALE and never trigger actions",
    }


# ------------------------------- agents -------------------------------
@app.get("/api/v1/agents")
async def agents():
    return state.orch.list_agents()


@app.get("/api/v1/agents/{key}")
async def agent_detail(key: str):
    a = state.orch.agents.get(key)
    if a is None:
        return {"error": "unknown agent"}
    return {**a.status(), "params": a.params, "history": a.history[-20:]}


@app.post("/api/v1/agents/{key}/run")
async def run_agent(key: str, payload: dict = None):
    return await state.orch.run_agent(key, **(payload or {}))


@app.post("/api/v1/agents/{key}/enable")
async def enable_agent(key: str):
    state.orch.enable(key)
    return {"ok": True, "key": key}


@app.post("/api/v1/agents/{key}/disable")
async def disable_agent(key: str):
    state.orch.disable(key)
    return {"ok": True, "key": key}


# ------------------------------- market -------------------------------
@app.get("/api/v1/market/ticks")
async def ticks():
    out = {}
    for sym, t in state.biquote._latest.items():
        d = t.as_dict()
        d["stale"] = t.age_seconds() > state.biquote.cfg.stale_seconds
        out[sym] = d
    return {"ticks": out, "feed_state": state.biquote.state()}


@app.websocket("/ws/market")
async def ws_market(ws: WebSocket):
    await ws.accept()
    state.ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()      # keepalive; ignore content
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(ws)


# ------------------------------- paper trading -------------------------------
@app.get("/api/v1/paper/portfolios")
async def portfolios():
    return {"portfolios": {pid: p.as_dict(prices={
        s: state.biquote._latest[s].mid for s in state.biquote._latest})
        for pid, p in state.engine.portfolios.items()}}


@app.post("/api/v1/paper/portfolios/{pid}/orders")
async def submit_order(pid: str, order: dict):
    """Manual order entry: still goes through validation + the Risk gate.
    Paper engine only; no real broker route exists."""
    try:
        v = state.orch.run_agent("risk", orders=[order],
                                 holdings={s: p.qty for s, p in
                                           state.engine.get(pid).positions.items()},
                                 cash=state.engine.get(pid).cash)
    except Exception as e:
        return {"error": str(e)}
    verdicts = (v.get("data") or {}).get("verdicts") if isinstance(v, dict) else None
    if not verdicts or verdicts[0]["verdict"] != "PASS":
        return {"risk": "BLOCK", "verdicts": verdicts}
    mid = order.get("mid_price") or (
        state.biquote._latest.get(order.get("symbol", "").upper()).mid
        if order.get("symbol", "").upper() in state.biquote._latest else None)
    if mid is None:
        return {"error": "no fresh price for this symbol; nothing executed"}
    try:
        o = state.engine.submit(pid, order.get("symbol"), order.get("side"),
                                float(order.get("qty")), type_=order.get("type", "market"),
                                limit_price=order.get("limit_price"),
                                stop_price=order.get("stop_price"), mid_price=mid)
    except PaperTradingError as e:
        return {"error": str(e)}
    return {"risk": "PASS", "order": o.as_dict()}


# ------------------------------- proposals -------------------------------
@app.get("/api/v1/proposals")
async def proposals():
    return {"proposals": [p.as_dict() for p in state.orch.proposals.values()
                          if p.state == "pending"]}


@app.post("/api/v1/proposals/{pid}/decision")
async def decide(pid: str, body: dict):
    return state.orch.decide(pid, body.get("decision"), body.get("order"))


# ------------------------------- OpenRouter -------------------------------
@app.get("/api/v1/openrouter/status")
async def openrouter_status():
    return await state.openrouter.status()      # never echoes the key


@app.post("/api/v1/openrouter/models/assign")
async def assign_model(body: dict):
    """Assign a model to an agent that actually uses AI. Deterministic agents
    keep running deterministically - the model is ignored there."""
    key, model = body.get("agent"), body.get("model")
    a = state.orch.agents.get(key)
    if a is None:
        return {"error": "unknown agent"}
    if a.spec.deterministic:
        return {"ok": False,
                "reason": "this agent is deterministic: its skill needs no AI model"}
    a.model = model
    return {"ok": True, "agent": key, "model": model}


# ------------------------------- history -------------------------------
@app.get("/api/v1/history/agent-runs")
async def agent_runs(limit: int = 100):
    s = make_session(state.db_engine)
    try:
        rows = s.query(AgentRunRow).order_by(AgentRunRow.ts.desc()).limit(limit).all()
        return {"runs": [{"agent": r.agent, "skill": r.skill, "status": r.status,
                         "ts": r.ts.isoformat()} for r in rows]}
    finally:
        s.close()