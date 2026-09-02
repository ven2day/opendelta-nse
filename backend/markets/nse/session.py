"""NSE cash-market session rules (Asia/Kolkata)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
SESSION_BARS_5M = 75


def nse_session_is_open(moment: datetime, *, holidays: frozenset | None = None) -> bool:
    local = moment.astimezone(IST) if moment.tzinfo else moment.replace(tzinfo=IST)
    if local.weekday() >= 5:
        return False
    if holidays and local.date() in holidays:
        return False
    return MARKET_OPEN <= local.time() <= MARKET_CLOSE
