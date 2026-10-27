"""Exit Planner - port of trader-exit/exit.py (day-trader-skill @ 3d0f4ef, MIT).
Stops: or-mid, or-low, price, pct, atr:K. Stop rounds DOWN to the cent, target half-up.
Refusals: stop>=entry, target<=entry, risk<0.05% of price. Long only. flat_by 15:55.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import time


class ExitRefusal(Exception):
    pass


def round_down_cents(p):
    return math.floor(p * 100 + 1e-9) / 100.0


def round_half_up_cents(p):
    return math.floor(p * 100 + 0.5 + 1e-9) / 100.0


@dataclass(frozen=True)
class ExitPlan:
    entry: float
    stop: float
    target: float
    stop_rule: str
    target_rule: str
    risk_per_share: float
    reward_risk: float
    flat_by: time

    def as_dict(self):
        return {"entry": self.entry, "stop": self.stop, "target": self.target,
                "stop_rule": self.stop_rule, "target_rule": self.target_rule,
                "risk_per_share": self.risk_per_share,
                "reward_risk": self.reward_risk,
                "flat_by": self.flat_by.strftime("%H:%M")}


def atr(bars, period=14):
    """14-bar ATR - same as the skill (averages all TRs when fewer than 14 bars)."""
    if not bars:
        return None
    trs = []
    prev_close = None
    for b in bars:
        tr = b["high"] - b["low"]
        if prev_close is not None:
            tr = max(tr, abs(b["high"] - prev_close), abs(b["low"] - prev_close))
        trs.append(tr)
        prev_close = b["close"]
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window) if window else None


def plan_exit(entry, stop_rule="or-mid", or_high=None, or_low=None,
              stop_price=None, target_rule="2R", target_price=None,
              flat_by=time(15, 55), bars=None):
    """Sets stop and target BEFORE the entry. Raises ExitRefusal instead of guessing."""
    if entry <= 0:
        raise ExitRefusal("entry must be positive")
    if stop_rule == "or-mid":
        if or_high is None or or_low is None:
            raise ExitRefusal("an opening-range setup needs its range")
        stop = round_down_cents((or_high + or_low) / 2)
    elif stop_rule == "or-low":
        if or_low is None:
            raise ExitRefusal("an opening-range setup needs its range low")
        stop = round_down_cents(or_low)
    elif stop_rule == "price":
        if stop_price is None:
            raise ExitRefusal("price stop needs stop_price")
        stop = round_down_cents(float(stop_price))
    elif stop_rule == "pct":
        if stop_price is None:
            raise ExitRefusal("pct stop needs the percent")
        stop = round_down_cents(entry * (1 - float(stop_price) / 100.0))
    elif stop_rule == "atr":
        if stop_price is None:
            raise ExitRefusal("atr stop needs K")
        a = atr(bars or [])
        if a is None:
            raise ExitRefusal("atr stop needs the session bars")
        stop = round_down_cents(entry - float(stop_price) * a)
    else:
        raise ExitRefusal("unknown stop rule: " + stop_rule)
    if stop >= entry:
        raise ExitRefusal("stop at or above the entry")
    risk = round(entry - stop, 2)
    if risk / entry < 0.0005:
        raise ExitRefusal("risk under 0.05% of the price: too tight")
    if target_rule in ("1R", "2R", "3R", "1.5R"):
        mult = float(target_rule[:-1])
        target = round_half_up_cents(entry + mult * risk)
    elif target_rule == "price":
        if target_price is None:
            raise ExitRefusal("price target needs target_price")
        target = round_half_up_cents(float(target_price))
    else:
        raise ExitRefusal("unknown target rule: " + target_rule)
    if target <= entry:
        raise ExitRefusal("target at or below the entry")
    rr = round((target - entry) / risk, 2)
    return ExitPlan(entry=entry, stop=stop, target=target, stop_rule=stop_rule,
                    target_rule=target_rule, risk_per_share=risk, reward_risk=rr,
                    flat_by=flat_by)