"""Deadline arithmetic: calendar days or business days.

A deadline policy says whether its day counts are calendar days or business
days (``DSRDeadlinePolicy.calendar_days``). Business days skip Saturdays,
Sundays and the dates in ``settings.DSR_PUBLIC_HOLIDAYS``. Days are counted
in the project time zone (Africa/Nairobi) and keep the time of day.

The holiday list must be maintained by whoever owns the policy; the one in
settings is an example only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

from django.conf import settings
from django.utils import timezone


@lru_cache(maxsize=8)
def _holidays(configured: tuple[str, ...]) -> frozenset[date]:
    return frozenset(date.fromisoformat(item) for item in configured)


def public_holidays() -> frozenset[date]:
    return _holidays(tuple(getattr(settings, "DSR_PUBLIC_HOLIDAYS", ())))


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in public_holidays()


def add_days(start: datetime, days: int, *, calendar_days: bool = True) -> datetime:
    """``start`` plus ``days`` calendar days, or plus ``days`` business days."""
    if calendar_days or days <= 0:
        return start + timedelta(days=days)
    local = timezone.localtime(start).replace(tzinfo=None)
    remaining = days
    while remaining:
        local += timedelta(days=1)
        if is_business_day(local.date()):
            remaining -= 1
    return timezone.make_aware(local)
