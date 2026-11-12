"""What "last month" and the other period words mean as dates.

Computed by code from today's date in the company's time zone and handed to the
model as plain ranges, so it never has to do date arithmetic.
"""

from __future__ import annotations

from datetime import date, timedelta


def _month_start(day: date, offset: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def period_ranges(today: date) -> dict[str, tuple[date, date]]:
    this_month = _month_start(today)
    last_month = _month_start(today, -1)
    week_start = today - timedelta(days=today.weekday())
    quarter_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    last_quarter_start = _month_start(quarter_start, -3)
    yesterday = today - timedelta(days=1)
    before_last = _month_start(today, -2)
    # the same day of last month, or its last day when last month is shorter
    same_day = min(last_month.replace(day=1) + timedelta(days=today.day - 1),
                   this_month - timedelta(days=1))  # fmt: skip
    return {
        "today": (today, today),
        "yesterday": (yesterday, yesterday),
        "this week": (week_start, today),
        "last week": (week_start - timedelta(days=7), week_start - timedelta(days=1)),
        "this month": (this_month, today),
        "last month": (last_month, this_month - timedelta(days=1)),
        "the month before last": (before_last, last_month - timedelta(days=1)),
        "last two months": (before_last, this_month - timedelta(days=1)),
        "the same days of last month": (last_month, same_day),
        "this quarter": (quarter_start, today),
        "last quarter": (last_quarter_start, quarter_start - timedelta(days=1)),
        "this year": (date(today.year, 1, 1), today),
        "last year": (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
        "last 7 days": (today - timedelta(days=7), yesterday),
        "last 30 days": (today - timedelta(days=30), yesterday),
        "last 90 days": (today - timedelta(days=90), yesterday),
    }


def periods_text(today: date) -> str:
    lines = [f"Today is {today.isoformat()} ({today:%A})."]
    for words, (start, end) in period_ranges(today).items():
        lines.append(f"- {words}: {start.isoformat()} to {end.isoformat()}")
    return "\n".join(lines)
