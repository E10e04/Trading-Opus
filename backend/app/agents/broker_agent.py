"""Agent Broker - port of trader-broker/ticket.py (day-trader-skill @ 3d0f4ef, MIT).
The only skill that can lead to an order - so the strictest. Three gates for
every order, every time:

  1. Agent Risk PASSED this exact order today (matched by fingerprint).
  2. The user typed an explicit yes for THIS order (accept/modify/refuse flow).
  3. The destination is the PAPER ENGINE ONLY. There is no real-broker route
     in this application, and none can be enabled.

It renders tickets in plain words, records the verdicts and fills, and keeps
the log for the daily risk limits.
"""
from __future__ import annotations
from .base import AgentSpec, BaseAgent, AgentResult
from ..core.risk import order_fingerprint

BROKER_SPEC = AgentSpec(
    key="broker", name="Agent Broker", skill="trader-broker",
    mission="Render the order ticket and execute it in the paper trading engine, "
            "after Risk passed and the user said yes. Paper only - no real broker.",
    deterministic=True,
)


class BrokerAgent(BaseAgent):
    spec = BROKER_SPEC

    def __init__(self, params=None, model=None, llm_client=None, paper_engine=None):
        super().__init__(params, model, llm_client)
        self.engine = paper_engine       # PaperEngine instance (injected)
        self.acceptances: dict = {}       # fingerprint -> accepted order

    # ---- ticket rendering (never talks to any broker) ----
    def ticket(self, order, risk_verdict) -> dict:
        """Only a PASS from today makes a ticket READY."""
        fp = order_fingerprint(order)
        ready = (risk_verdict is not None and risk_verdict.get("verdict") == "PASS")
        return {
            "order": order,
            "fingerprint": fp,
            "ready": ready,
            "state": "READY" if ready else "NOT READY",
            "plain_words": self._plain_words(order),
        }

    @staticmethod
    def _plain_words(o):
        lines = ["ORDER: " + str(o.get("side", "")).upper() + " " + str(o.get("qty", ""))
                 + " " + str(o.get("symbol", "")) + ", " + str(o.get("type", ""))
                 + ", good for today (PAPER account)"]
        if o.get("stop_loss_stop_price"):
            lines.append("stop-loss " + str(o["stop_loss_stop_price"]))
        if o.get("take_profit_limit_price"):
            lines.append("take-profit " + str(o["take_profit_limit_price"]))
        plan = o.get("plan") or {}
        if plan.get("risk_per_share") and o.get("qty"):
            try:
                lines.append("max loss about $" + format(
                    int(o["qty"]) * float(plan["risk_per_share"]), ".2f")
                    + " if the stop fills at " + str(o.get("stop_loss_stop_price")))
            except (ValueError, TypeError):
                pass
        lines.append("nothing is placed until you say yes")
        return " | ".join(lines)

    # ---- the explicit user yes (recorded per exact order) ----
    def record_yes(self, order, reply: str):
        """A yes must be an explicit yes for THIS order: 'sure', 'yes to all',
        or a yes naming another stock is not recorded."""
        fp = order_fingerprint(order)
        words = (reply or "").strip().lower()
        symbols = str(order.get("symbol", "")).lower()
        if words in ("yes", "y", "oui") or words in ("yes, " + symbols, "yes " + symbols):
            self.acceptances[fp] = True
            return True
        if words in ("no", "n", "non"):
            return False
        return False                    # ambiguous replies are never turned into a yes

    def execute(self, order, mid_price, portfolio_id="main"):
        """Gate check then paper execution. An accepted fingerprint is required.
        market orders need a fresh mid price from the feed."""
        fp = order_fingerprint(order)
        if fp not in self.acceptances:
            return {"executed": False,
                    "reason": "no explicit yes recorded for this exact order"}
        try:
            qty = int(order.get("qty"))
        except (TypeError, ValueError):
            return {"executed": False, "reason": "bad qty"}
        o = self.engine.submit(
            portfolio_id, order.get("symbol"), order.get("side"), qty,
            type_=order.get("type", "market"),
            limit_price=order.get("limit_price"),
            stop_price=order.get("stop_loss_stop_price"),
            mid_price=mid_price, note=order.get("why", ""))
        self.acceptances.pop(fp, None)   # one order per yes
        return {"executed": True, "order": o.as_dict()}

    def fill(self, portfolio_id, symbol, side, qty, price):
        """Record a fill (stop-loss/take-profit hit) on the portfolio."""
        return {"executed": True,
                "order": self.engine.submit(portfolio_id, symbol, side, qty,
                                            type_="market", mid_price=price,
                                            note="bracket leg fill")}

    def flatten(self, portfolio_id, symbol, qty, mid_price):
        """The sell that closes a day trade by flat-by time - checked by Risk as
        an EXIT (never blocked by day limits), then paper-executed."""
        o = self.engine.submit(portfolio_id, symbol, "sell", qty,
                               type_="market", mid_price=mid_price, note="flatten")
        return {"executed": True, "order": o.as_dict()}

    def run_impl(self, **_):
        return AgentResult(status="success", data={
            "note": "the Broker agent renders tickets and executes in the paper "
                    "engine only; no real-broker route exists in this application",
            "acceptances_pending": len(self.acceptances)})