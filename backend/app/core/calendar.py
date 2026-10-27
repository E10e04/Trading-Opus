"""NYSE calendar - ported from day-trader-skill (trader-scanner, trader-entry,
trader-risk, trader-alerts calendars: 2026-2027 holidays, early closes, DST).

Source: github.com/Jakeschincariol/day-trader-skill @ 3d0f4ef (MIT) - SKILL.md
trader-alerts ("from nyse.com, read 2026-10-13").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NEW_YORK: ZoneInfo = ZoneInfo("America/New_York")

NYSE_HOLIDAYS: frozenset[date] = frozenset({
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

NYSE_EARLY_CLOSES: frozenset[date] = frozenset({
    date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26),
})

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


@dataclass(frozen=True)
class MarketDay:
    day: date
    holiday: bool = False
    early_close: bool = False

    @property
    def is_open(self) -> bool:
        return not self.holiday and self.day.weekday() < 5

    @property
    def close_time(self) -> time:
        return EARLY_CLOSE if self.early_close else REGULAR_CLOSE


def market_day(d: date) -> MarketDay:
    return MarketDay(
        day=d,
        holiday=d in NYSE_HOLIDAYS,
        early_close=d in NYSE_EARLY_CLOSES,
    )


def is_trading_day(d: date) -> bool:
    return market_day(d).is_open


def close_time_for(d: date) -> time:
    return market_day(d).close_time


def market_state_at(dt: datetime) -> str:
    d = dt.date()
    md = market_day(d)
    if not md.is_open:
        return "weekend" if d.weekday() >= 5 else "holiday"
    if dt.time() < REGULAR_OPEN:
        return "pre_open"
    if dt.time() >= md.close_time:
        return "closed"
    return "open"


def now_new_york() -> datetime:
    return datetime.now(NEW_YORK)


def in_entry_window(now_ny: datetime, before: time, after: time) -> bool:
    md = market_day(now_ny.date())
    if not md.is_open:
        return False
    close = md.close_time
    eff_after = after if after <= close else close
    if md.early_close:
        eff_after = after if after <= time(12, 30) else time(12, 30)
    t = now_ny.time()
    return before <= t <= eff_after