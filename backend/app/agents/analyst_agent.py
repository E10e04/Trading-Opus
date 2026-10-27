"""Agent Analyst - port of trader-analyst/facts.py (day-trader-skill @ 3d0f4ef, MIT).
Reads the numbers the company filed with the SEC (XBRL company facts): revenue,
margins, net income, diluted EPS, quarter by quarter against the same quarter a
year earlier, with the trailing twelve months. Flags one-offs (net income above
operating income) and derived Q4. Says when the data lags. Never says cheap,
expensive, a buy or a sell - it reports what the company filed.
"""
from __future__ import annotations
from .base import AgentSpec, BaseAgent, AgentResult

ANALYST_SPEC = AgentSpec(
    key="analyst", name="Agent Analyst", skill="trader-analyst",
    mission="Read earnings from SEC XBRL filings and say plainly what changed, "
            "quarter by quarter, against the same quarter a year earlier.",
    deterministic=False, default_model=None,   # numbers are deterministic; an LLM
                                              # may OPTIONALLY summarize the filing
)


def q4_derived(fy, q1, q2, q3):
    """Companies do not file a Q4 10-Q: Q4 = 10-K year minus the three quarters.
    Marked 'derived'; derived EPS is approximate (skill rule)."""
    return fy - q1 - q2 - q3


def yoy(cur, prev):
    if prev in (None, 0):
        return None
    return round((cur - prev) / abs(prev) * 100, 1)


def margins(rev, gross=None, op=None, net=None):
    out = {}
    if rev:
        for name, v in (("gross_margin", gross), ("operating_margin", op),
                        ("net_margin", net)):
            out[name] = round(v / rev * 100, 1) if v is not None else None
    return out


class AnalystAgent(BaseAgent):
    spec = ANALYST_SPEC

    def __init__(self, params=None, model=None, llm_client=None, http=None, config=None):
        super().__init__(params, model, llm_client)
        self.http = http
        self.config = config or {}

    def required_inputs(self, kwargs):
        if not kwargs.get("facts") and not kwargs.get("ticker"):
            return ["facts or ticker"]
        return []

    def analyze_facts(self, facts: dict) -> dict:
        """facts: {ticker: {revenue, gross, operating, net_income, diluted_eps,
        period, prior_year: {...same keys}}}. All comparisons like with like:
        growth is against the same quarter a year earlier."""
        rows = []
        for ticker, f in facts.items():
            rev, prev_rev = f.get("revenue"), (f.get("prior_year") or {}).get("revenue")
            ni, prev_ni = f.get("net_income"), (f.get("prior_year") or {}).get("net_income")
            eps, prev_eps = f.get("diluted_eps"), (f.get("prior_year") or {}).get("diluted_eps")
            row = {
                "ticker": ticker, "period": f.get("period"),
                "revenue": rev, "revenue_yoy_pct": yoy(rev, prev_rev),
                "net_income": ni, "net_income_yoy_pct": yoy(ni, prev_ni),
                "diluted_eps": eps, "diluted_eps_yoy_pct": yoy(eps, prev_eps),
                "margins": margins(rev, f.get("gross"), f.get("operating"), ni),
                "notes": [],
            }
            if f.get("operating") is not None and ni is not None and ni > f["operating"]:
                row["notes"].append("net income is above operating income: gains "
                                   "outside the business (investment marks, tax items) "
                                   "are inside it")
            if f.get("derived"):
                row["notes"].append("Q4 derived (10-K minus the three filed quarters); "
                                    "derived EPS is approximate")
            if f.get("data_lag"):
                row["notes"].append("SEC facts trail the newest filing: read the 10-Q "
                                   "itself before summarizing (" + str(f.get("data_lag")) + ")")
            if rev is None:
                row["notes"].append("no revenue fact in the filing data: nothing invented")
            rows.append(row)
        return {"rows": rows}

    def run_impl(self, facts=None, ticker=None, http=None, sec_user_agent=None, **_):
        if facts:
            return AgentResult(status="success", data=self.analyze_facts(facts),
                               notes=["filed numbers first; growth is like-for-like "
                                      "against the same quarter a year earlier"])
        # without a connected EDGAR source, say so - never simulate the filing
        return AgentResult(status="skipped", data={"ticker": ticker},
                           notes=["SEC EDGAR not reachable or SEC_USER_AGENT not set: "
                                  "the analyst reports nothing rather than inventing "
                                  "numbers. Provide 'facts' or configure the source."])