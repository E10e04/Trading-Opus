"""Daily setups - port of trader-scanner/scan.py (daily scan) rules.
RSI 14 Wilder, breakout_20d, bull_flag, reclaim_ma20, oversold, oversold_bounce.
Computed from daily closes only, as the skill does.
Source: day-trader-skill @ 3d0f4ef (MIT).
"""
from __future__ import annotations


def wilder_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + ag / al)


def daily_setups(closes):
    """Returns the set of matched setup names. A scan is a list of charts that
    match a rule, not a list of stocks to buy."""
    hits = set()
    n = len(closes)
    if n < 16:
        return hits
    last = closes[-1]

    # breakout_20d: the last close above every close of the 20 sessions before it
    if n >= 21 and last > max(closes[-21:-1]):
        hits.add("breakout_20d")

    # bull_flag: up more than 10% over the 10 sessions before the last 5,
    # then the last 5 closes inside a 5% range
    if n >= 20:
        prior_start = closes[-16]
        prior_move = (closes[-11] - prior_start) / prior_start
        last5 = closes[-5:]
        spread = (max(last5) - min(last5)) / max(last5) if max(last5) > 0 else 1
        if prior_move > 0.10 and spread <= 0.05:
            hits.add("bull_flag")

    # reclaim_ma20: the close before was under its 20-day average, the last is above
    if n >= 21:
        ma_prev = sum(closes[-21:-1]) / 20
        ma_now = sum(closes[-20:]) / 20
        if closes[-2] < ma_prev and last > ma_now:
            hits.add("reclaim_ma20")

    # oversold / oversold_bounce: RSI 14 (Wilder) under 30
    rsi_now = wilder_rsi(closes)
    rsi_prev = wilder_rsi(closes[:-1])
    if rsi_now is not None and rsi_now < 30:
        hits.add("oversold")
    if rsi_prev is not None and rsi_prev < 30 and last > closes[-2]:
        hits.add("oversold_bounce")

    return hits