"""Time Machine - port of trader-timemachine/timemachine.py
(day-trader-skill @ 3d0f4ef, MIT).

Replays the rest of the session N times from the stock's own 5-minute bars:
each replayed bar is drawn from a past FULL session's bar at the same time of
day (plus or minus --window bars), scaled to the current price as ratios to
the previous close. Stop first, then target: a bar that touches both counts
as the stop. Re-runs the plan's own rule on every past session (the history).
Seeded RNG: same plan, same data, same seed -> the same numbers.
A simulation, not a prediction.
"""
from __future__ import annotations
import random
from dataclasses import dataclass
from datetime import date, time
from typing import Optional

from .exit import round_down_cents, round_half_up_cents
from .entry import build_opening_range


class TimeMachineRefusal(Exception):
    pass


def _full_sessions(chart_by_day, plan_date, bars_per_session=78):
    """Only FULL sessions before the plan's date. Half days, gaps and today are out."""
    out = {}
    for d, bars in sorted(chart_by_day.items()):
        if d >= plan_date:
            continue
        if len(bars) >= bars_per_session:
            out[d] = bars
    return out


def _ratios(bars):
    """Every bar becomes four ratios to the bar before it (o/h/l/c vs prev close)."""
    r = []
    prev = bars[0]["close"]
    for b in bars[1:]:
        if prev <= 0:
            continue
        r.append({"open": b["open"] / prev, "high": b["high"] / prev,
                  "low": b["low"] / prev, "close": b["close"] / prev})
        prev = b["close"]
    return r


def timemachine_run(plan, chart_by_day, replays=10000, seed=19, window=2,
                    until=time(16, 0), or_minutes=15):
    """plan: {date, entry, stop, target, entry_bar, target_r}. Returns the odds.
    Refuses when there are fewer than 20 full sessions (skill rule)."""
    plan_date = plan["date"] if isinstance(plan["date"], date) else date.fromisoformat(plan["date"])
    sessions = _full_sessions(chart_by_day, plan_date)
    if len(sessions) < 20:
        raise TimeMachineRefusal(
            "fewer than 20 full sessions in the pool (need 60 days of 5m bars)")

    entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
    risk = entry - stop
    if risk <= 0:
        raise TimeMachineRefusal("stop not below the entry")
    entry_bar = int(plan.get("entry_bar") or 0)

    pools = {d: _ratios(b) for d, b in sessions.items()}
    n_bars = len(next(iter(sessions.values())))
    remaining = max(0, n_bars - 1 - entry_bar)
    days = list(sessions.keys())
    rng = random.Random(seed)

    outcome = {"target": 0, "stop": 0, "neither": 0}
    greens = 0
    close_rs = []
    for _ in range(replays):
        price, hit = entry, None
        for j in range(remaining):
            d = days[rng.randrange(len(days))]
            pool = pools[d]
            idx = entry_bar + j
            if idx >= len(pool):
                break
            lo = max(0, idx - window)
            hi = min(len(pool) - 1, idx + window)
            r = pool[rng.randint(lo, hi)]
            low = price * r["low"]
            high = price * r["high"]
            if low <= stop:            # stop first if the bar touches both
                hit = "stop"
                break
            if high >= target:
                hit = "target"
                break
            price = price * r["close"]
        outcome[hit or "neither"] += 1
        if hit == "target" or price > entry:
            greens += 1
        close_rs.append((price - entry) / risk)

    target_pct = round(100.0 * outcome["target"] / replays, 1)
    stop_pct = round(100.0 * outcome["stop"] / replays, 1)
    neither_pct = round(100.0 * outcome["neither"] / replays, 1)
    green_pct = round(100.0 * greens / replays, 1)
    avg_r = round(sum(close_rs) / len(close_rs), 3)

    verdict = _verdict(target_pct, stop_pct, float(plan.get("target_r") or 1))
    return {
        "mc": {
            "target_first_pct": target_pct,
            "stop_first_pct": stop_pct,
            "neither_pct": neither_pct,
            "green_pct": green_pct,
            "avg_r": avg_r,
            "seed": seed, "replays": replays, "window": window,
        },
        "verdict": verdict,
        "history": timemachine_history(plan, chart_by_day, or_minutes=or_minutes),
        "sessions_used": len(sessions),
    }


def _verdict(target_pct, stop_pct, target_r):
    """One honest line from the numbers, as the skill prints it.
    A bigger target needs to win less often to break even: 1 in (1+R)."""
    hit_a_side = target_pct + stop_pct
    if hit_a_side <= 0:
        return "no replay hit a side"
    share = target_pct / hit_a_side
    breakeven = 1.0 / (1.0 + target_r)
    delta = (share - breakeven) * 100.0
    if abs(delta) < 5:
        if abs(target_r - 1.0) < 1e-9:
            return "Close to a coin flip"
        return "Close to break-even"
    if 5 <= delta < 15:
        return "Leans toward the target"
    if -15 < delta <= -5:
        return "Leans toward the stop"
    if delta >= 15:
        return "Strongly toward the target"
    return "Strongly toward the stop"


def timemachine_history(plan, chart_by_day, or_minutes=15):
    """Re-runs the plan's own rule (same opening range, stop rule, target R)
    on every past full session. What really happened, not a simulation."""
    plan_date = plan["date"] if isinstance(plan["date"], date) else date.fromisoformat(plan["date"])
    sessions = _full_sessions(chart_by_day, plan_date)
    fired = target_n = stop_n = neither_n = 0
    target_r = float(plan.get("target_r") or 1)
    for d, bars in sessions.items():
        rng = build_opening_range(bars, or_minutes=or_minutes, interval=5)
        if rng is None:
            continue
        stop = round_down_cents(rng.midpoint)
        trigger = None
        range_end_time = bars[0]["start"].time()
        for i, b in enumerate(bars):
            if i < or_minutes // 5:
                continue
            if b["close"] > rng.high:
                trigger = b
                break
        if trigger is None:
            continue
        fired += 1
        entry = trigger["close"]
        risk = entry - stop
        if risk <= 0:
            continue
        target = round_half_up_cents(entry + target_r * risk)
        res = None
        t_idx = bars.index(trigger)
        for b in bars[t_idx + 1:]:
            if b["low"] <= stop:
                res = "stop"
                break
            if b["high"] >= target:
                res = "target"
                break
        if res == "target":
            target_n += 1
        elif res == "stop":
            stop_n += 1
        else:
            neither_n += 1
    return {"fired": fired, "target": target_n, "stop": stop_n, "neither": neither_n}