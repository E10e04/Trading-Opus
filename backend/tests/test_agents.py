"""Agent tests - one suite per agent, each verifying its own skill-based logic."""
import sys
import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from app.agents.orchestrator import Orchestrator, Proposal            # noqa: E402
from app.agents.alerts_agent import AlertsAgent, AlertRule            # noqa: E402
from app.agents.news_agent import NewsAgent                           # noqa: E402
from app.agents.analyst_agent import AnalystAgent, q4_derived, yoy    # noqa: E402
from app.agents.pelosi_agent import PelosiAgent, disclosure_delay     # noqa: E402
from app.agents.broker_agent import BrokerAgent                       # noqa: E402
from app.core.paper import PaperEngine                                # noqa: E402
from app.core.risk import check_orders, order_fingerprint            # noqa: E402

DAY = date(2026, 10, 19)


def _bars(day, series):
    out = []
    t = datetime.combine(day, time(9, 30))
    for c, h, l, v in series:
        out.append({"start": t, "open": c, "high": h, "low": l, "close": c, "volume": v})
        t += timedelta(minutes=5)
    return out


REEL = [
    (356.00, 357.50, 354.47, 900), (360.00, 362.18, 355.50, 800),
    (361.50, 361.80, 356.00, 700), (360.50, 361.50, 359.90, 300),
    (361.68, 362.00, 360.20, 310), (362.50, 363.00, 361.90, 280),
    (361.68, 362.00, 361.00, 290), (364.18, 364.50, 363.80, 400),
    (365.00, 366.00, 364.00, 450),
]


def _synthetic_chart(plan_date, n_days=25, seed_val=7):
    import random as _r
    _r.seed(seed_val)
    out = {}
    d = plan_date - timedelta(days=1)
    made = 0
    while made < n_days:
        if d.weekday() < 5 and d != date(2026, 10, 12):
            px = 350.0
            s = []
            for _ in range(78):
                o = px
                c = px * (1 + _r.uniform(-0.02, 0.02))
                s.append((c, max(o, c) * 1.002, min(o, c) * 0.998, 1000))
                px = c
            out[d] = _bars(d, s)
            made += 1
        d -= timedelta(days=1)
    return out


def test_orchestrator_lists_eleven_agents():
    o = Orchestrator()
    listed = o.list_agents()
    assert len(listed) == 11
    keys = {a["key"] for a in listed}
    assert keys == {"scanner", "news", "entry", "exit", "timemachine",
                    "budget", "risk", "broker", "alerts", "analyst", "pelosi"}
    skills = {a["skill"] for a in listed}
    assert all(s.startswith("trader-") for s in skills)   # one skill per agent


def test_disabled_agent_is_skipped():
    o = Orchestrator()
    o.disable("pelosi")
    assert all(a["key"] != "pelosi" for a in o.list_agents())
    res = asyncio.run(o.run_agent("pelosi"))
    assert res["status"] == "skipped"


def test_audit_trail_fields():
    o = Orchestrator()
    asyncio.run(o.run_agent("news", tickers=["TSLA"]))
    entry = o.audit[-1]
    assert entry["skill"] == "trader-news"
    assert entry["skill_ref"].startswith("3d0f4ef")
    assert entry["ts"]
    assert entry["agent"] == "news"


def test_trade_pipeline_end_to_end_reel():
    engine = PaperEngine()
    engine.create_portfolio("main", 5000)
    o = Orchestrator(paper_engine=engine)
    out = asyncio.run(o.run_trade_pipeline(
        "TSLA", daily_closes=[300 + i for i in range(30)],
        intraday_bars=_bars(DAY, REEL), chart_by_day=_synthetic_chart(DAY),
        now=datetime(2026, 10, 19, 10, 15), cash=4500.0, holdings={},
        fresh={"TSLA": {"price": 364.20, "age_min": 1}}, today_entries=0, today_risk=0.0))
    steps = dict(out["steps"])
    assert steps["scanner"] == "success"
    assert steps["entry"] == "success"
    assert steps["risk"] == "success"
    assert out["status"] == "proposed", out
    prop = out["proposal"]
    assert prop["symbol"] == "TSLA"
    assert prop["risk_verdict"]["verdict"] == "PASS"
    # the user refuses -> nothing executed
    r = o.decide(prop["proposal_id"], "refuse")
    assert r["ok"] and r["state"] == "refused"
    assert engine.get("main").orders == []          # paper engine untouched


def test_blocked_proposal_never_reaches_user():
    engine = PaperEngine()
    engine.create_portfolio("main", 5000)
    o = Orchestrator(paper_engine=engine)
    out = asyncio.run(o.run_trade_pipeline(
        "TSLA", daily_closes=[300 + i for i in range(30)],
        intraday_bars=_bars(DAY, REEL), chart_by_day=_synthetic_chart(DAY),
        now=datetime(2026, 10, 19, 9, 40),          # before the entry window
        cash=4500.0, holdings={}, fresh={}, today_entries=3, today_risk=150.0))
    assert out["status"] in ("risk_blocked", "no_entry")
    assert o.proposals == {}                        # nothing proposed when blocked


def test_broker_requires_explicit_yes_and_fingerprint():
    engine = PaperEngine()
    engine.create_portfolio("main", 5000)
    b = BrokerAgent(paper_engine=engine)
    order = {"symbol": "TSLA", "side": "buy", "qty": "8", "type": "market",
             "time_in_force": "day", "order_class": "bracket",
             "stop_loss_stop_price": "358.32", "take_profit_limit_price": "370.04",
             "plan": {"entry": 364.18, "stop": 358.32, "target": 370.04}}
    v = check_orders([order], holdings={}, cash=5000.0,
                     now_ny=datetime(2026, 10, 19, 10, 15))
    ticket = b.ticket(order, v[0].as_dict())
    assert ticket["ready"] and ticket["state"] == "READY"
    # ambiguous replies are never a yes
    assert b.record_yes(order, "sure") is False
    assert b.record_yes(order, "yes to all") is False
    assert b.record_yes(order, "yes") is True
    res = b.execute(order, mid_price=364.20)
    assert res["executed"] is True
    assert engine.get("main").positions["TSLA"].qty == 8
    # one order per yes: the same fingerprint cannot execute twice
    res2 = b.execute(order, mid_price=364.20)
    assert res2["executed"] is False


def test_alerts_agent_ring_once_and_dated():
    a = AlertsAgent()
    a.add_from_plan({"symbol": "TSLA", "stop": 358.32, "target": 370.04,
                     "flat_by": "15:55", "date": DAY})
    now = datetime(2026, 10, 19, 10, 53)
    rang = a.check({"TSLA": 366.48}, now_ny=now)
    assert rang == []                                  # between stop and target
    rang = a.check({"TSLA": 370.50}, now_ny=now)
    assert any("plan-target" in r["rule_id"] for r in rang)
    rang2 = a.check({"TSLA": 371.00}, now_ny=now)
    assert all("plan-target" not in r["rule_id"] for r in rang2)   # rings once
    # yesterday's rule never rings today
    rang3 = a.check({"TSLA": 375.0}, now_ny=datetime(2026, 10, 20, 10, 0))
    assert all("plan" not in r["rule_id"] for r in rang3)


def test_news_agent_tags_and_moving():
    n = NewsAgent()
    items = n.classify_items([
        {"ticker": "TSLA", "title": "Tesla-SpaceX Merger Mania Strikes Wall Street Ahead of Earnings",
         "link": "https://x/1", "published": "2026-10-19T08:04:00-04:00", "source": "yahoo"},
    ])
    assert "earnings" in items[0]["tags"] and "deal" in items[0]["tags"]
    assert items[0]["kind"] == "interpretation"
    again = n.classify_items([
        {"ticker": "TSLA", "title": "Tesla-SpaceX Merger Mania Strikes Wall Street Ahead of Earnings",
         "link": "https://x/1", "published": "2026-10-19T08:04:00-04:00"}])
    assert again[0]["new"] is False                    # never repeats an item
    f = n.classify_filings([{"ticker": "XYZ", "item": "2.02", "link": "https://sec/1",
                             "filed": "2026-10-19"}])
    assert f[0]["important"] and f[0]["kind"] == "fact"
    m = n.moving_flags(["TSLA"], {"TSLA": 354.72}, {"TSLA": 366.48})
    assert m["TSLA"]["moving"] and abs(m["TSLA"]["move_pct"] - 3.31) < 0.1


def test_news_agent_offline_is_honest():
    n = NewsAgent()
    r = n.run(tickers=["TSLA"])
    assert r.status == "success"
    assert any("not fetched" in s for s in r.notes)
    assert r.data["items"] == []                       # nothing invented


def test_analyst_agent_yoy_and_oneoffs():
    a = AnalystAgent()
    r = a.run(facts={"TSLA": {
        "revenue": 28.24e9, "net_income": 1.11e9, "diluted_eps": 0.32,
        "operating": 0.396e9, "period": "Q2-2026",
        "prior_year": {"revenue": 22.50e9, "net_income": 1.166e9, "diluted_eps": 0.33}}})
    row = r.data["rows"][0]
    assert row["revenue_yoy_pct"] == 25.5              # the reel's real number
    assert any("above operating income" in s for s in row["notes"])
    # without EDGAR connected: skipped, honest
    r2 = a.run(ticker="KO")
    assert r2.status == "skipped"
    assert q4_derived(100, 20, 25, 25) == 30


def test_pelosi_agent_ranges_and_delay():
    p = PelosiAgent()
    text = "Purchase (BE) Bloom Energy Corporation Class A Common Stock 100 call options $1,000,001 - $5,000,000 07/24/2026"
    r = p.run(filings=[{"member": "Hon. Nancy Pelosi", "doc_id": "20035143",
                        "filed_date": "2026-08-21", "trade_date": "2026-07-24",
                        "district": "CA11", "text": text}])
    f = r.data["filings"][0]
    assert f["new"] is True and f["trade_to_filed_days"] == 28 and not f["late"]
    row = f["rows"][0]
    assert row["ticker"] == "BE"
    assert row["amount_range"] == "$1,000,001 - $5,000,000"   # a range, never exact
    # late filing flag
    assert disclosure_delay("2026-06-01", "2026-08-21") > 45
    # second run: the doc is not new
    r2 = p.run(filings=[{"member": "x", "doc_id": "20035143", "filed_date": "2026-08-21",
                         "trade_date": "2026-07-24", "text": text}])
    assert r2.data["filings"][0]["new"] is False
    # offline: honest skip
    r3 = p.run()
    assert r3.status == "skipped"


def test_proposal_modify_requires_new_risk_pass():
    o = Orchestrator()
    o.proposals["T1-1"] = Proposal(proposal_id="T1-1", symbol="TSLA",
                                   order={"symbol": "TSLA", "side": "buy", "qty": "8"},
                                   risk_verdict={"verdict": "PASS"})
    r = o.decide("T1-1", "modify")
    assert r["ok"] and "Risk gate" in r["note"]