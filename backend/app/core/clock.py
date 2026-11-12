"""What day it is. Pinned by BI_FAKE_TODAY in tests so answers are repeatable."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings


def today(settings: Settings, timezone: str = "Asia/Kolkata") -> date:
    """Today's date in the given time zone (a company's), or the pinned date."""
    if settings.fake_today is not None:
        return settings.fake_today
    return datetime.now(ZoneInfo(timezone)).date()
