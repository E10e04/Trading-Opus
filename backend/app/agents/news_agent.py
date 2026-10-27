"""Agent News - port of trader-news/news.py (day-trader-skill @ 3d0f4ef, MIT).
Reads Yahoo Finance headlines per ticker and 8-K filings on SEC EDGAR, tags
each item, flags MOVING stocks (>=3% from yesterday's close), never repeats
an item, and separates facts / interpretations / uncertainties.

It NEVER simulates access to a source that is not connected: without network
or without SEC_USER_AGENT the filings are skipped with an explicit note.
Headlines are leads, not facts. Filings beat headlines.
"""
from __future__ import annotations
from datetime import datetime, timezone

from .base import AgentSpec, BaseAgent, AgentResult

NEWS_SPEC = AgentSpec(
    key="news", name="Agent News", skill="trader-news",
    mission="Track breaking news on every watched stock and flag what moves it.",
    deterministic=False, default_model=None,
)

IMPORTANT_8K_ITEMS = {          # marked "!" in the skill
    "2.02": "earnings results", "5.02": "officer change", "2.06": "impairment",
    "3.01": "delisting notice", "4.02": "past financials no longer reliable",
    "1.03": "bankruptcy", "1.05": "cyber incident",
}

TAG_RULES = [
    ("earnings", ("earnings", "results", "quarterly", "eps", "revenue beat")),
    ("analyst", ("analyst", "rating", "upgrade", "downgrade", "price target")),
    ("deal", ("merger", "acquisition", "acquire", "deal", "buyout")),
    ("legal", ("lawsuit", "court", "sue", "litigation")),
    ("regulatory", ("regulator", "fda", "sec", "doj", "antitrust")),
    ("halt", ("halt", "halted", "trading suspended")),
    ("people", ("ceo", "cfo", "resign", "appoint")),
    ("capital", ("offering", "shares sale", "dilution", "convertible")),
    ("risk", ("fraud", "probe", "investigation", "recall")),
    ("macro", ("fed", "inflation", "cpi", "rates", "gdp", "futures")),
]


class NewsAgent(BaseAgent):
    spec = NEWS_SPEC

    def __init__(self, params=None, model=None, llm_client=None, http=None, config=None):
        super().__init__(params, model, llm_client)
        self.http = http                # httpx.AsyncClient or None (tests inject stubs)
        self.config = config or {}
        self.seen: set = set()          # item fingerprints already shown

    def required_inputs(self, kwargs):
        return [] if kwargs.get("tickers") or kwargs.get("headlines") else ["tickers or headlines"]

    # ---- tagging (deterministic, from the skill's rules) ----
    @staticmethod
    def tag_title(title: str) -> list:
        t = (title or "").lower()
        tags = [name for name, keys in TAG_RULES if any(k in t for k in keys)]
        return tags or ["general"]

    def classify_items(self, headlines: list) -> list:
        """headline dicts: {title, link, published, source}.
        Distinguishes facts (filings), interpretations (headlines) and the
        missing (stories that could not be read)."""
        items = []
        for h in headlines:
            fp = (h.get("link") or h.get("title") or "")[:200]
            fresh = fp not in self.seen
            self.seen.add(fp)
            items.append({
                "ticker": h.get("ticker"),
                "title": h.get("title"),
                "link": h.get("link"),
                "published": h.get("published"),
                "kind": "interpretation",      # a headline is a lead, not a fact
                "tags": self.tag_title(h.get("title") or ""),
                "source": h.get("source", "yahoo-finance"),
                "new": fresh,
            })
        return items

    def classify_filings(self, filings: list) -> list:
        items = []
        for f in filings:
            important = f.get("item") in IMPORTANT_8K_ITEMS
            items.append({
                "ticker": f.get("ticker"),
                "form": f.get("form", "8-K"),
                "item": f.get("item"),
                "important": important,
                "what": IMPORTANT_8K_ITEMS.get(f.get("item"), f.get("title", "")),
                "link": f.get("link"),
                "filed": f.get("filed"),
                "kind": "fact",                # the filing is what the company told the SEC
                "source": "sec-edgar",
            })
        return items

    def moving_flags(self, tickers, prev_closes, last_prices):
        """MOVING = 3% or more from yesterday's close."""
        out = {}
        for t in tickers:
            prev = prev_closes.get(t) if prev_closes else None
            last = last_prices.get(t) if last_prices else None
            if prev and last:
                move = (last - prev) / prev * 100
                out[t] = {"move_pct": round(move, 2),
                          "moving": abs(move) >= self.params.get("move_pct", 3.0)}
            else:
                out[t] = {"move_pct": None, "moving": False,
                          "note": "no fresh price or no previous close"}
        return out

    def run_impl(self, tickers=None, headlines=None, filings=None,
                 prev_closes=None, last_prices=None, edgar_available=None, **_):
        notes, data = [], {"items": [], "moving": {}}
        if headlines:
            data["items"].extend(self.classify_items(headlines))
        else:
            notes.append("no headlines provided: the Yahoo feed was not fetched "
                         "(source not connected or offline) - nothing is invented")
        if filings:
            data["items"].extend(self.classify_filings(filings))
        if edgar_available is False:
            notes.append("SEC EDGAR skipped: SEC_USER_AGENT is not configured "
                         "(required by the SEC fair-access policy)")
        if tickers:
            data["moving"] = self.moving_flags(tickers, prev_closes, last_prices)
            moved = [t for t, v in data["moving"].items() if v.get("moving")]
            if moved:
                notes.append("MOVING (>=3% from yesterday's close): " + ", ".join(moved)
                             + " - a move with no headline is worth saying out loud too")
        notes.append("headlines are leads, not facts; filings beat headlines")
        return AgentResult(status="success", data=data, notes=notes)