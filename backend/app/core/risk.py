"""Risk Manager - port of trader-risk/check.py (day-trader-skill @ 3d0f4ef, MIT).
Deterministic backend gate. A BLOCK is final for that order: it never goes to
the user for a yes. Getting out is never blocked by the day limits or the
entry window (an exit only reduces what is already held).
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from datetime import time

from .calendar import in_entry_window, market_state_at, now_new_york

DEFAULT_RISK_CONFIG = {
    "require_stop": True,
    "max_loss_per_trade_usd": 50.0,
    "max_daily_loss_usd": 150.0,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "min_order_usd": 5.0,
    "max_order_usd": 4000.0,
    "max_position_pct": 75.0,
    "cash_buffer_pct": 10.0,
    "budget": 5000.0,
    "no_entries_before": "09:45",
    "no_entries_after": "15:30",
    "blocked": [],
    "allow_options": False,
    "allow_short": False,
    "pdt_guard": False,
}


class RiskBlock(Exception):
    pass


def order_fingerprint(order: dict) -> str:
    """Stable fingerprint of the exact order - the broker matches verdicts by it.
    An edited order is NOT ready until it passes the check again (skill rule)."""
    keys = ("symbol", "side", "qty", "type", "time_in_force", "order_class",
            "take_profit_limit_price", "stop_loss_stop_price", "limit_price")
    core = {k: order.get(k) for k in keys if k in order}
    payload = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class OrderVerdict:
    index: int
    fingerprint: str
    verdict: str            # "PASS" | "BLOCK"
    is_entry: bool
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return {"index": self.index, "fingerprint": self.fingerprint,
                "verdict": self.verdict, "is_entry": self.is_entry,
                "reasons": self.reasons, "warnings": self.warnings}


def _parse_hhmm(s):
    h, m = s.split(":")
    return time(int(h), int(m))


def _is_entry(order, holdings) -> bool:
    """An entry opens or adds; an exit only reduces what is held.
    A sell beyond held would go short, so it counts as an entry (skill rule)."""
    side = order.get("side")
    if side == "buy":
        return True
    if side == "sell":
        try:
            q = int(order.get("qty") or 0)
        except (TypeError, ValueError):
            return True
        held = holdings.get(order.get("symbol"), 0)
        return q > held
    return True


def check_orders(orders, config=None, holdings=None, cash=None, fresh=None,
                 today_entries=0, today_risk=0.0, orders_placed_today=0,
                 now_ny=None, day_trades_5d=None):
    """Check every order in order, as if each passed order had filled.
    fresh: {symbol: {"price": float, "age_min": float}} - a fresh price (under
    15 min) higher than the plan entry raises the entry risk is measured from.
    today_risk: the sum of the max losses at the stops of today's entries.
    Returns verdicts, one per order. Never raises for a blocked order: the
    verdict carries the reason.
    """
    cfg = dict(DEFAULT_RISK_CONFIG)
    cfg.update(config or {})
    holdings = holdings or {}
    fresh = fresh or {}
    now_ny = now_ny or now_new_york()
    before = _parse_hhmm(cfg["no_entries_before"])
    after = _parse_hhmm(cfg["no_entries_after"])
    verdicts = []
    seen = set()
    entries_count = today_entries
    risk_used = float(today_risk)

    for i, o in enumerate(orders, 1):
        reasons, warnings = [], []
        is_entry = _is_entry(o, holdings)

        # --- format ---
        if not o.get("symbol"):
            reasons.append("format: missing symbol")
        if o.get("side") not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell")
        qty = None
        if "qty" in o and o.get("qty") not in (None, ""):
            try:
                qty = int(o["qty"])
            except (TypeError, ValueError):
                reasons.append("format: qty is not an integer")
        if qty is None and "notional" in o and o.get("notional") is None:
            reasons.append("format: notional order without an amount")
        if o.get("type") == "limit" and not o.get("limit_price"):
            reasons.append("format: limit order without a limit price")
        if qty is not None and qty <= 0:
            reasons.append("format: qty must be positive")

        # --- asset ---
        sym = o.get("symbol") or ""
        if not cfg["allow_options"] and (len(sym) > 5 or "(" in sym):
            reasons.append("asset: options or non-US-stock symbol")

        # --- short ---
        if o.get("side") == "sell" and qty is not None:
            if qty > holdings.get(sym, 0) and not cfg["allow_short"]:
                reasons.append("short: sell of more than held and allow_short is false")

        if is_entry:
            # --- blocked list ---
            if sym in cfg["blocked"]:
                reasons.append("blocked: ticker on the blocked list")

            # --- size ---
            plan = o.get("plan") or {}
            entry_ref = float(o.get("limit_price") or plan.get("entry") or 0)
            if entry_ref > 0 and qty:
                usd = qty * entry_ref
                if usd < cfg["min_order_usd"]:
                    reasons.append("size: under min_order_usd")
                if usd > cfg["max_order_usd"]:
                    reasons.append("size: over max_order_usd")
                # --- position cap ---
                held_value = holdings.get(sym, 0) * entry_ref
                if (held_value + usd) > cfg["budget"] * cfg["max_position_pct"] / 100.0:
                    reasons.append("position: would be over max_position_pct")
                # --- cash ---
                if cash is not None and o.get("side") == "buy":
                    buffer = cfg["budget"] * cfg["cash_buffer_pct"] / 100.0
                    if float(cash) - usd < buffer - 1e-9:
                        reasons.append("cash: would leave less than the cash buffer")

            # --- stop required ---
            if cfg["require_stop"] and not o.get("stop_loss_stop_price") \
                    and not plan.get("stop"):
                reasons.append("stop: no stop and require_stop is true")

            # --- loss / reward:risk / daily loss (stop measured from max(plan, fresh)) ---
            stop = float(o.get("stop_loss_stop_price") or plan.get("stop") or 0)
            if stop and entry_ref:
                eff = entry_ref
                f = fresh.get(sym)
                if f and f.get("age_min", 999) < 15 and f.get("price", 0) > eff:
                    eff = float(f["price"])
                risk = round(eff - stop, 2)
                if risk <= 0:
                    reasons.append("stop: on the wrong side of the entry")
                else:
                    max_loss = round(qty * risk, 2) if qty else 0.0
                    if max_loss > cfg["max_loss_per_trade_usd"]:
                        reasons.append(
                            "loss: " + str(qty) + " x " + format(risk, ".2f")
                            + " = $" + format(max_loss, ".2f") + " at the stop, over $"
                            + format(cfg["max_loss_per_trade_usd"], ".2f"))
                    target = plan.get("target")
                    if target:
                        rr = (float(target) - eff) / risk
                        if rr < cfg["min_reward_risk"]:
                            reasons.append("reward:risk under the minimum")
                    if risk_used + max_loss > cfg["max_daily_loss_usd"]:
                        reasons.append(
                            "daily loss: $" + format(risk_used + max_loss, ".2f")
                            + " would exceed $" + format(cfg["max_daily_loss_usd"], ".2f"))
                    risk_used += max_loss

            # --- trades per day ---
            if entries_count >= cfg["max_trades_per_day"]:
                reasons.append("trades: max_trades_per_day already reached")

            # --- entry window (New York) ---
            state = market_state_at(now_ny)
            if state != "open":
                reasons.append("window: the market is not open (" + state + ")")
            elif not in_entry_window(now_ny, before, after):
                reasons.append("window: outside the entry window "
                               + cfg["no_entries_before"] + "-" + cfg["no_entries_after"] + " ET")

            # --- PDT guard (optional, off by default) ---
            if cfg["pdt_guard"] and (day_trades_5d or 0) >= 3:
                reasons.append("pdt: 3 day trades in the last 5 business days")

            # --- duplicates ---
            key = (sym, o.get("side"))
            if key in seen:
                reasons.append("duplicate: same symbol and side twice in one batch")
            seen.add(key)

            # --- daily order limit ---
            if orders_placed_today >= cfg["max_orders_per_day"]:
                reasons.append("daily limit: max_orders_per_day reached")

        # warnings (never block)
        if is_entry and risk_used > 0.8 * cfg["max_daily_loss_usd"]:
            warnings.append("most of the daily loss limit is used")

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and is_entry:
            entries_count += 1
        verdicts.append(OrderVerdict(index=i, fingerprint=order_fingerprint(o),
                                     verdict=verdict, is_entry=is_entry,
                                     reasons=reasons, warnings=warnings))
    return verdicts


def must_pass(orders, **kwargs) -> list:
    """Hard gate for the paper broker: raises if any order is blocked."""
    verdicts = check_orders(orders, **kwargs)
    blocked = [v for v in verdicts if v.verdict == "BLOCK"]
    if blocked:
        lines = ["order #" + str(v.index) + ": " + "; ".join(v.reasons) for v in blocked]
        raise RiskBlock("risk check BLOCK: " + " | ".join(lines))
    return verdicts