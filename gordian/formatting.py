"""Durations, ratios, and the day names typed into the chart's From/to boxes.

A ratio is shown as work per 1 free minute, so 25 work and 10 free is 2.5:1.
parse_ratio also accepts 25:10 and spaces around the colon. parse_day accepts
6 Aug, 6th August, August 6, an ISO date, and day/month with slashes.
"""

from __future__ import annotations

import re
from datetime import date


def format_duration(seconds: float) -> str:
    sign = "-" if seconds < 0 else ""
    total = int(abs(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{sign}{hours}h {minutes:02d}m {secs:02d}s"
    return f"{sign}{minutes}m {secs:02d}s"


def plain_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def format_ratio(work_minutes: float, reward_minutes: float) -> str:
    if reward_minutes <= 0:
        return f"{plain_number(work_minutes)}:0"
    return f"{plain_number(work_minutes / reward_minutes)}:1"


def format_ratio_round(work_per_reward: float, places: int = 2) -> str:
    """W:R with the work side rounded, for a compact weekly summary."""
    if work_per_reward <= 0:
        return "—"
    shown = f"{round(work_per_reward, places):.{places}f}".rstrip("0").rstrip(".")
    return f"{shown}:1"


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTHS.update({name[:3]: number for name, number in list(_MONTHS.items())})


def parse_day(text: str, year: int) -> date | None:
    cleaned = " ".join(text.strip().lower().replace(",", " ").split())
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", cleaned)
    if not cleaned:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", cleaned)
    if match:
        return _safe_date(int(match[1]), int(match[2]), int(match[3]))
    match = re.fullmatch(r"(\d{1,2}) ([a-z]+)(?: (\d{4}))?", cleaned)
    if match and match[2] in _MONTHS:
        return _safe_date(int(match[3]) if match[3] else year, _MONTHS[match[2]], int(match[1]))
    match = re.fullmatch(r"([a-z]+) (\d{1,2})(?: (\d{4}))?", cleaned)
    if match and match[1] in _MONTHS:
        return _safe_date(int(match[3]) if match[3] else year, _MONTHS[match[1]], int(match[2]))
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", cleaned)
    if match:
        day, month = int(match[1]), int(match[2])
        parsed_year = year
        if match[3]:
            parsed_year = int(match[3])
            if parsed_year < 100:
                parsed_year += 2000
        if month > 12 and day <= 12:
            day, month = month, day
        return _safe_date(parsed_year, month, day)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_ratio(text: str) -> tuple[float, float] | None:
    cleaned = text.strip().replace(" ", "")
    if ":" not in cleaned:
        return None
    left, right = cleaned.split(":", 1)
    try:
        work = float(left)
        reward = float(right)
    except ValueError:
        return None
    if work <= 0 or reward < 0:
        return None
    if work < 1:
        if reward == 0:
            return 1.0, 0.0
        scale = 1 / work
        work, reward = 1.0, reward * scale
    return work, reward
