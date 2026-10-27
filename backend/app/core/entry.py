"""Opening range and ORB detection - port of trader-scanner/scan.py (orb)
and trader-entry/entry.py (day-trader-skill @ 3d0f4ef, MIT).

Only COMPLETED bars count. A bar is closed when the clock passes its start
plus its interval. A wick above is not a trigger. A close exactly at the
level is not above it.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float
    bars: int

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2


def build_opening_range(bars, or_minutes=15, interval=5, regular_open=time(9, 30)):
    """bars: [{start: datetime, high, low, close, volume}] sorted by start.
    Returns None while the range is incomplete (the 9:30, 9:35, 9:40 bars)."""
    need = or_minutes // interval
    window_end = time(regular_open.hour, regular_open.minute + or_minutes)
    in_range = [b for b in bars if b["start"].time() < window_end]
    complete = [b for b in in_range
                if b["start"] + timedelta(minutes=interval) <= (b["start"] + timedelta(minutes=10**6))]
    rng = [b for b in in_range][-need:]
    if len(rng) < need:
        return None
    return OpeningRange(high=max(b["high"] for b in rng),
                        low=min(b["low"] for b in rng),
                        bars=len(rng))


@dataclass(frozen=True)
class EntrySignal:
    status: str          # not_open_yet|range_forming|waiting|triggered|expired|market_closed|no_data
    detail: str = ""
    price: float = None
    time: datetime = None
    level: float = None


def check_entry(bars, now, level=None, or_minutes=15, interval=5,
                min_volume_x=None, no_before=time(9, 45), no_after=time(15, 30)):
    """The trigger is the first COMPLETED bar after the range that CLOSES above
    the level. Volume gate: the trigger bar's volume at min_volume_x times the
    range bars' average. One trigger per setup per day: the first close above.
    """
    from .calendar import market_state_at

    if not bars:
        return EntrySignal(status="no_data", detail="no bars")
    day = bars[0]["start"].date()
    state = market_state_at(datetime.combine(day, now.time()) if isinstance(now, datetime) else now)

    rng = build_opening_range(bars, or_minutes, interval)
    if rng is None:
        return EntrySignal(status="no_data" if now.time() < time(9, 45) else "range_forming",
                           detail="opening range incomplete")
    lvl = float(level) if level is not None else rng.high
    range_end = time(9, 30 + or_minutes)
    avg_vol = sum(b["volume"] for b in bars if b["start"].time() < range_end) / max(rng.bars, 1)

    done = [b for b in bars
            if isinstance(now, datetime) and b["start"] + timedelta(minutes=interval) <= now]
    after_range = [b for b in done if b["start"].time() >= range_end]

    for b in after_range:
        vol_ok = min_volume_x is None or (avg_vol > 0 and b["volume"] >= min_volume_x * avg_vol)
        if b["close"] > lvl and vol_ok:
            trig_time = b["start"] + timedelta(minutes=interval)
            if trig_time.time() > no_after:
                return EntrySignal(status="expired", detail="trigger after the entry window")
            return EntrySignal(status="triggered",
                               detail="bar closed " + format(b["close"], ".2f") + " > level "
                                      + format(lvl, ".2f"),
                               price=b["close"], time=trig_time, level=lvl)

    last = done[-1]["close"] if done else None
    if last is not None and last <= lvl:
        return EntrySignal(status="waiting",
                           detail="last close " + format(last, ".2f") + ", "
                                  + format(lvl - last, ".2f") + " under the level " + format(lvl, ".2f"),
                           price=last, level=lvl)
    return EntrySignal(status="waiting", detail="no close above the level yet", level=lvl)