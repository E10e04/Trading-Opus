"""Agent Pelosi Disclosures - port of trader-pelosi/ptr.py
(day-trader-skill @ 3d0f4ef, MIT).
Reads the House Clerk's public Periodic Transaction Reports. Amounts are
RANGES, never an exact dollar. The owner matters (SP spouse, JT joint, DC
dependent child). Each row shows the days between trade and filing; over 45
is flagged late. A PTR is due within 30 days of the member learning of the
trade and never more than 45 days after: these are PUBLIC disclosures, days
or weeks old, never a real-time signal and never sufficient proof for an order.
The Senate route is manual (the user accepts the site agreement themselves).
"""
from __future__ import annotations
from datetime import date, datetime

from .base import AgentSpec, BaseAgent, AgentResult

PELOSI_SPEC = AgentSpec(
    key="pelosi", name="Agent Pelosi Disclosures", skill="trader-pelosi",
    mission="Read Congress's stock-trade disclosures the minute they are filed - "
            "public filings under the STOCK Act, up to 45 days after the trade.",
    deterministic=True,
)

OWNER_CODES = {"SP": "spouse", "JT": "joint", "DC": "dependent child", "": "the member"}


def disclosure_delay(trade_date, filed_date):
    if not trade_date or not filed_date:
        return None
    d = (date.fromisoformat(str(filed_date)) - date.fromisoformat(str(trade_date))).days
    return max(0, d)


class PelosiAgent(BaseAgent):
    spec = PELOSI_SPEC

    def __init__(self, params=None, model=None, llm_client=None, http=None, config=None):
        super().__init__(params, model, llm_client)
        self.http = http
        self.config = config or {}
        self.seen_doc_ids: set = set()       # each PTR alerts once

    def parse_rows(self, ptr_text: str, filed_date=None) -> list:
        """Parse pdftotext -layout style rows of a PTR. Quote the filing, not
        a tracker. Amounts stay ranges."""
        rows = []
        for line in (ptr_text or "").splitlines():
            line = line.strip()
            if not any(k in line for k in ("Purchase", "Sale", "Exchange")):
                continue
            side = ("Purchase" if "Purchase" in line else
                    "Sale" if "Sale" in line else "Exchange")
            owner = ""
            for code in OWNER_CODES:
                if code and code in line.split("(")[0][-4:]:
                    owner = code
            amount = ""
            import re
            m = re.search(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?", line)
            if m:
                amount = m.group(0)
            ticker = ""
            m2 = re.search(r"\(([A-Z]{1,5})\)", line)
            if m2:
                ticker = m2.group(1)
            rows.append({
                "type": side.lower(), "owner": OWNER_CODES.get(owner, "the member"),
                "ticker": ticker, "amount_range": amount,          # a range, never exact
                "line": line[:200],
            })
        return rows

    def classify_filings(self, filings: list) -> list:
        out = []
        for f in filings:
            doc_id = f.get("doc_id")
            fresh = doc_id not in self.seen_doc_ids
            self.seen_doc_ids.add(doc_id)
            delay = disclosure_delay(f.get("trade_date"), f.get("filed_date") or f.get("filed"))
            rows = self.parse_rows(f.get("text", ""), f.get("filed_date"))
            out.append({
                "member": f.get("member"), "doc_id": doc_id,
                "filed": f.get("filed_date") or f.get("filed"),
                "district": f.get("district"), "filing_type": f.get("filing_type", "PTR"),
                "new": fresh, "rows": rows,
                "trade_to_filed_days": delay,
                "late": (delay or 0) > 45,
            })
        return out

    def run_impl(self, filings=None, member="pelosi", http=None, **_):
        if filings is not None:
            data = {"filings": self.classify_filings(filings), "member": member}
            return AgentResult(status="success", data=data, notes=[
                "these are PUBLIC disclosures filed up to 45 days after the trade",
                "they say what was bought in the past, not what anyone will buy next",
                "a disclosure is a reason to look at a chart, not a setup: Scanner "
                "and Entry decide if there is a trade today",
            ])
        return AgentResult(status="skipped", data={"member": member}, notes=[
            "House Clerk index not reachable (or pdftotext missing): nothing is "
            "invented. The Senate route stays manual - the user accepts the site "
            "agreement themselves, this agent never automates it.",
        ])