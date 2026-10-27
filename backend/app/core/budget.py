"""Budget Manager - port of trader-budget/size.py (day-trader-skill @ 3d0f4ef, MIT).
The stop decides the size: the further away the stop, the fewer shares.
Whole shares only. The size is the smallest of four limits. Explicit refusals.
Writes one bracket order (paper trading only in this application).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field


class BudgetRefusal(Exception):
    pass


DEFAULT_CONFIG = {
    "budget": 5000.0,                # money for this, only money the user can lose
    "cash_buffer_pct": 10.0,         # always stays in cash
    "max_loss_per_trade_usd": 50.0,
    "max_position_pct": 75.0,        # of the budget
    "max_order_usd": 4000.0,
    "min_order_usd": 5.0,
    "min_reward_risk": 1.0,
}


@dataclass(frozen=True)
class SizedOrder:
    order: dict                      # Alpaca-shaped bracket order (paper destination)
    qty: int
    deciding_limit: str
    limits: dict
    notional: float
    max_loss_at_stop: float
    entry_used: float

    def as_dict(self):
        return {
            "order": self.order, "qty": self.qty,
            "deciding_limit": self.deciding_limit, "limits": self.limits,
            "notional": self.notional, "max_loss_at_stop": self.max_loss_at_stop,
            "entry_used": self.entry_used,
        }


def size_position(plan, config=None, fresh_price=None, fresh_age_min=None,
                 cash=None, symbol=None):
    """plan: {entry, stop, target, risk_per_share?, target_r?}.
    fresh_price/fresh_age_min: a quote from the market feed, used ONLY when
    fresh (under 15 min) and HIGHER than the plan entry - a price that ran
    up means a bigger loss at the same stop, so fewer shares (skill rule).
    cash: portfolio cash caps the size, never more than what is held.
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    target = float(plan["target"])

    if stop >= entry:
        raise BudgetRefusal("stop not below the entry")
    if target <= entry:
        raise BudgetRefusal("target not above the entry (the move already happened)")

    eff = entry
    if (fresh_price is not None and fresh_age_min is not None
            and fresh_age_min < 15 and float(fresh_price) > entry):
        eff = float(fresh_price)

    risk = round(eff - stop, 2)
    rr = (target - eff) / risk if risk > 0 else 0.0
    if rr < cfg["min_reward_risk"]:
        raise BudgetRefusal(
            "reward:risk " + format(rr, ".2f") + " under min "
            + str(cfg["min_reward_risk"]) + " (the move mostly happened)")

    limits = {
        "max_loss": math.floor(cfg["max_loss_per_trade_usd"] / risk) if risk > 0 else 0,
        "position_cap": math.floor(cfg["budget"] * cfg["max_position_pct"] / 100.0 / eff),
        "max_order": math.floor(cfg["max_order_usd"] / eff),
        "cash": math.floor(cfg["budget"] * (1 - cfg["cash_buffer_pct"] / 100.0) / eff),
    }
    if cash is not None:
        limits["cash"] = min(limits["cash"], math.floor(float(cash) / eff))

    deciding = min(limits, key=lambda k: limits[k])
    qty = limits[deciding]
    if qty <= 0:
        raise BudgetRefusal("zero shares fit the limits (stop too far for the max loss)")
    notional = round(qty * eff, 2)
    if notional < cfg["min_order_usd"]:
        raise BudgetRefusal("order under the minimum " + str(cfg["min_order_usd"]) + " USD")

    max_loss = round(qty * risk, 2)
    order = {
        "symbol": symbol or plan.get("symbol", ""),
        "side": "buy",
        "qty": str(qty),
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit_limit_price": format(target, ".2f"),
        "stop_loss_stop_price": format(stop, ".2f"),
        "why": "sized by the Budget Manager (paper trading only)",
        "plan": {"entry": entry, "stop": stop, "target": target,
                 "risk_per_share": risk, "reward_risk": round(rr, 2)},
    }
    return SizedOrder(order=order, qty=qty, deciding_limit=deciding,
                      limits=limits, notional=notional, max_loss_at_stop=max_loss,
                      entry_used=eff)