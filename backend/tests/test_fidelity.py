"""Fidelity tests - every expected value comes from the day-trader-skill
repository's own worked example (the reel: TSLA, Monday 2026-10-19) and from
each SKILL.md's stated rules. Source: @ 3d0f4ef (MIT).
"""
from __future__ import annotations
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from app.core.exit import plan_exit, ExitRefusal, atr          # noqa: E402
from app.core.budget import size_position, BudgetRefusal       # noqa: E402
from app.core.risk import check_orders, order_fingerprint     # noqa: E402
from app.core.entry import check_entry, build_opening_range    # noqa: E402
from app.core.rsi import daily_setups, wilder_rsi              # noqa: E402
from app.core.timemachine import timemachine_run, timemachine_history, TimeMachineRefusal  # noqa: E402
from app.core.calendar import market_day, market_state_at, in_entry_window  # noqa: E402

DAY = date(2026, 10, 19)
OR_LOW, OR_HIGH, ENTRY = 354.47, 362.18, 364.18


def _bars(day, series):
    """series: (close, high, low, volume), 5-minute bars from 9:30."""
    out = []
    t = datetime.combine(day, time(9, 30))
    for c, h, l, v in series:
        out.append({"start": t, "open": c, "high": h, "low": l,
                    "close": c, "volume": v})
        t += timedelta(minutes=5)
    return out


REEL_SERIES = [
    (356.00, 357.50, 354.47, 900),   # 9:30
    (360.00, 362.18, 355.50, 800),   # 9:35
    (361.50, 361.80, 356.00, 700),   # 9:40
    (360.50, 361.50, 359.90, 300),   # 9:45
    (361.68, 362.00, 360.20, 310),   # 9:50
    (362.50, 363.00, 361.90, 280),   # 9:55
    (361.68, 362.00, 361.00, 290),   # 10:00
    (364.18, 364.50, 363.80, 400),   # 10:05 -> closes 10:10 (bar 8 in the reel counts from 9:30)
    (365.00, 366.00, 364.00, 450),   # 10:10 -> closes 10:15
]


def test_reel_exit_or_mid_1r():
    p = plan_exit(ENTRY, or_high=OR_HIGH, or_low=OR_LOW, stop_rule="or-mid", target_rule="1R")
    assert p.stop == 358.32            # 358.325 rounds DOWN
    assert p.risk_per_share == 5.86
    assert p.target == 370.04
    assert p.reward_risk == 1.0
    assert p.flat_by == time(15, 55)


def test_reel_exit_default_2r():
    p = plan_exit(ENTRY, or_high=OR_HIGH, or_low=OR_LOW, stop_rule="or-mid")
    assert p.target == 375.90         # 364.18 + 2 x 5.86


def test_exit_refusals():
    # stop at or above the entry
    try:
        plan_exit(ENTRY, stop_rule="price", stop_price=ENTRY)
        assert False, "must refuse"
    except ExitRefusal:
        pass
    # too tight: risk under 0.05% of the price
    try:
        plan_exit(ENTRY, stop_rule="pct", stop_price=0.01)
        assert False, "must refuse"
    except ExitRefusal:
        pass
    # target at or below the entry
    try:
        plan_exit(ENTRY, or_high=OR_HIGH, or_low=OR_LOW, target_rule="price", target_price=ENTRY - 1)
        assert False, "must refuse"
    except ExitRefusal:
        pass


def test_atr_fewer_than_14_bars_averages_all():
    bars = [{"high": 10 + i * 0.5, "low": 9 + i * 0.5, "close": 9.5 + i * 0.5} for i in range(10)]
    assert atr(bars) is not None


REEL_PLAN = {"symbol": "TSLA", "entry": ENTRY, "stop": 358.32, "target": 370.04,
             "risk_per_share": 5.86, "target_r": 1, "date": DAY}


def test_reel_budget_eight_shares():
    o = size_position(REEL_PLAN)
    assert o.qty == 8                                       # 50 / 5.86
    assert o.deciding_limit == "max_loss"
    assert o.limits == {"max_loss": 8, "position_cap": 10, "max_order": 10, "cash": 12}
    assert o.notional == 2913.44
    assert o.max_loss_at_stop == 46.88
    assert o.order["order_class"] == "bracket"
    assert o.order["take_profit_limit_price"] == "370.04"
    assert o.order["stop_loss_stop_price"] == "358.32"


def test_budget_stop_too_far_refused():
    try:
        size_position(dict(REEL_PLAN, stop=300.00))
        assert False, "must refuse"
    except BudgetRefusal as e:
        assert "zero shares" in str(e)


def test_budget_fresh_price_rr_refused():
    # at 10:52 the real quote was 366.48: risk 8.16 -> reward:risk 0.44 < 1.0 -> refused
    try:
        size_position(REEL_PLAN, fresh_price=366.48, fresh_age_min=3)
        assert False, "must refuse"
    except BudgetRefusal as e:
        assert "reward:risk" in str(e)


def _reel_order(qty):
    return {"symbol": "TSLA", "side": "buy", "qty": str(qty), "type": "market",
            "time_in_force": "day", "order_class": "bracket",
            "take_profit_limit_price": "370.04", "stop_loss_stop_price": "358.32",
            "plan": {"entry": ENTRY, "stop": 358.32, "target": 370.04}}


def test_reel_risk_pass_8():
    v = check_orders([_reel_order(8)], holdings={}, cash=5000.0,
                     today_entries=0, today_risk=0.0,
                     now_ny=datetime(2026, 10, 19, 10, 15))
    assert v[0].verdict == "PASS", v[0].reasons
    assert v[0].is_entry


def test_reel_risk_block_9():
    v = check_orders([_reel_order(9)], holdings={}, cash=5000.0,
                     today_entries=0, today_risk=0.0,
                     now_ny=datetime(2026, 10, 19, 10, 15))
    assert v[0].verdict == "BLOCK"
    assert any("52.74" in r for r in v[0].reasons)      # 9 x 5.86 = 52.74 > 50


def test_reel_risk_window_block_0940():
    v = check_orders([_reel_order(8)], holdings={}, cash=5000.0,
                     today_entries=0, today_risk=0.0,
                     now_ny=datetime(2026, 10, 19, 9, 40))
    assert v[0].verdict == "BLOCK"
    assert any("window" in r for r in v[0].reasons)


def test_risk_exit_never_blocked():
    # the 3:55 PM sell that closes the position passes even after the entry cutoff
    o = {"symbol": "TSLA", "side": "sell", "qty": "8", "type": "market",
         "time_in_force": "day"}
    v = check_orders([o], holdings={"TSLA": 8}, cash=100.0, today_entries=3,
                     today_risk=150.0, now_ny=datetime(2026, 10, 19, 15, 55))
    assert v[0].verdict == "PASS", v[0].reasons
    assert not v[0].is_entry


def test_fingerprint_stable_and_exact():
    a = order_fingerprint(_reel_order(8))
    b = order_fingerprint(_reel_order(8))
    assert a == b
    assert a != order_fingerprint(_reel_order(9))        # an edited order is a new order


def test_entry_triggered_at_reel_time():
    bars = _bars(DAY, REEL_SERIES[:8])
    r = check_entry(bars, datetime(2026, 10, 19, 10, 15))
    assert r.status == "triggered"
    assert r.price == 364.18
    assert r.time == datetime(2026, 10, 19, 10, 10)      # bar 8 (10:05 bar) closes 10:10


def test_entry_waiting_before_close():
    bars = _bars(DAY, REEL_SERIES[:7])
    r = check_entry(bars, datetime(2026, 10, 19, 10, 12))
    assert r.status == "waiting"
    assert r.price == 361.68


def test_entry_volume_gate():
    series = REEL_SERIES[:11] + [(365.75, 366.50, 365.20, 3000)]  # 10:30 bar closes 10:35
    bars = _bars(DAY, series)
    r = check_entry(bars, datetime(2026, 10, 19, 10, 36), min_volume_x=1.0)
    assert r.status == "triggered"
    assert r.price == 365.75


def test_scanner_breakout_and_rsi():
    closes = [float(i) for i in range(20, 40)] + [40.5]
    assert "breakout_20d" in daily_setups(closes)
    falling = [100.0 - 0.9 * i for i in range(30)]
    assert "oversold" in daily_setups(falling)
    assert wilder_rsi(falling) is not None and wilder_rsi(falling) < 30


def test_calendar_reel_day():
    md = market_day(DAY)
    assert md.is_open and not md.early_close
    assert market_state_at(datetime(2026, 10, 19, 10, 15)) == "open"
    assert market_state_at(datetime(2026, 10, 19, 8, 0)) == "pre_open"
    assert not market_day(date(2026, 12, 25)).is_open          # Christmas holiday
    assert market_day(date(2026, 11, 27)).early_close           # 1:00 p.m. close


def test_entry_window_reel():
    assert in_entry_window(datetime(2026, 10, 19, 10, 15), time(9, 45), time(15, 30))
    assert not in_entry_window(datetime(2026, 10, 19, 9, 40), time(9, 45), time(15, 30))
    assert not in_entry_window(datetime(2026, 10, 19, 15, 55), time(9, 45), time(15, 30))


def _synthetic_chart(plan_date, n_days=25, seed_val=7):
    """Deterministic synthetic 5m charts: full sessions, random-walk bars."""
    import random as _r
    _r.seed(seed_val)
    out = {}
    d = plan_date - timedelta(days=1)
    made = 0
    while made < n_days:
        if d.weekday() < 5 and d != date(2026, 10, 12):
            px = 350.0
            series = []
            for _ in range(78):
                o = px
                c = px * (1 + _r.uniform(-0.02, 0.02))
                h = max(o, c) * (1 + _r.uniform(0, 0.004))
                l = min(o, c) * (1 - _r.uniform(0, 0.004))
                series.append((c, h, l, 1000))
                px = c
            out[d] = _bars(d, series)
            made += 1
        d -= timedelta(days=1)
    return out


def test_timemachine_reproducible_seed():
    chart = _synthetic_chart(DAY)
    plan = {"date": DAY, "entry": ENTRY, "stop": 358.32, "target": 370.04,
            "entry_bar": 8, "target_r": 1}
    a = timemachine_run(plan, chart, replays=500, seed=19)
    b = timemachine_run(plan, chart, replays=500, seed=19)
    assert a == b                                     # same seed -> same numbers
    mc = a["mc"]
    total = mc["target_first_pct"] + mc["stop_first_pct"] + mc["neither_pct"]
    assert abs(total - 100.0) < 0.5
    assert 0 <= mc["green_pct"] <= 100


def test_timemachine_refuses_too_few_sessions():
    try:
        timemachine_run({"date": DAY, "entry": ENTRY, "stop": 358.32,
                         "target": 370.04, "entry_bar": 8}, {})
        assert False, "must refuse"
    except TimeMachineRefusal:
        pass


def test_timemachine_history_counts_add_up():
    chart = _synthetic_chart(DAY)
    plan = {"date": DAY, "target_r": 1}
    h = timemachine_history(plan, chart)
    assert h["fired"] == h["target"] + h["stop"] + h["neither"]