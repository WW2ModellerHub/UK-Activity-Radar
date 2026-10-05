from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def london_tz() -> tzinfo:
    """Return Europe/London without letting missing Windows tzdata kill a feed.

    The requirements include the ``tzdata`` wheel, so ZoneInfo should normally
    work on Windows. This fallback keeps incident collectors alive even if a
    broken/old virtual environment is reused before pip refreshes packages.
    """
    try:
        return ZoneInfo("Europe/London")
    except ZoneInfoNotFoundError:
        # A conservative fallback. It is only used while a damaged environment
        # is being repaired; all timestamps are still clearly marked as source
        # times rather than silently crashing an entire live feed.
        return timezone(timedelta(hours=0), name="UK-fallback")


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=london_tz())
    return value.astimezone(timezone.utc)
