"""Date sanity checks for what the model writes into tool calls.

The model sometimes pairs a weekday with the wrong date ("Friday 11 October" when that Friday is the
9th). `check_args` runs before every tool (tools/registry.execute) and returns a correction instead of
letting the tool act, so the wrong date never reaches a person, a booking or a calendar.

Two checks, English only (Alfred is English-only):
- "<weekday> [the] <day> <month> [<year>]" (also "Fri 09 Oct", "Friday, 9th of October"): the weekday
  must match the date. Without a year, the nearest such date (from 2 months back) is assumed.
- a text argument that names exactly one weekday plus an ISO date/time argument (when/start/due…):
  they must agree.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import settings

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD = r"(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MO = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_PAIR = re.compile(rf"\b{_WD}\.?,?\s+(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\.?\s+(?:of\s+)?{_MO}\.?(?:,?\s+(\d{{4}}))?\b", re.I)
_LONE_WD = re.compile(rf"\b{_WD}\b", re.I)
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ][\d:]{4,8}.*)?$")
_ISO_KEYS = ("when", "start", "due", "date", "run_at", "end")


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _wd_index(token: str) -> int:
    return [w[:3] for w in WEEKDAYS].index(token[:3].lower())


def _resolve(day: int, month: int, year: int | None) -> date | None:
    try:
        if year:
            return date(year, month, day)
        today = _today()
        d = date(today.year, month, day)
        return d if d >= today - timedelta(days=60) else date(today.year + 1, month, day)
    except ValueError:
        return None


def _fmt(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()].title()} {d.day} {d.strftime('%B %Y')}"


def _upcoming(weekday: int) -> date:
    today = _today()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def check_text(text: str) -> list[str]:
    problems = []
    for m in _PAIR.finditer(text or ""):
        wd, day, mon, year = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        d = _resolve(day, _MONTHS.index(mon[:3].lower()) + 1, int(year) if year else None)
        if d and d.weekday() != _wd_index(wd):
            right = _upcoming(_wd_index(wd))
            problems.append(f"'{m.group(0)}' doesn't add up: {d.day} {d.strftime('%B %Y')} is a {WEEKDAYS[d.weekday()].title()}. "
                            f"The coming {WEEKDAYS[_wd_index(wd)].title()} is {_fmt(right)}.")
    return problems


def check_args(args: dict) -> list[str]:
    """Problems found in a tool call's arguments (empty list = fine)."""
    texts = {k: v for k, v in args.items() if isinstance(v, str) and len(v) <= 4000}
    problems = [p for v in texts.values() for p in check_text(v)]
    if problems:
        return problems
    iso = next((m.group(1) for k in _ISO_KEYS if isinstance(args.get(k), str) and (m := _ISO.match(args[k].strip()))), None)
    if iso:
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            return []
        for k, v in texts.items():
            if k in _ISO_KEYS:
                continue
            days = {_wd_index(t) for t in _LONE_WD.findall(v)}
            if len(days) == 1 and d.weekday() not in days:
                wd = days.pop()
                problems.append(f"The text says {WEEKDAYS[wd].title()} but the time is {_fmt(d)}. "
                                f"The coming {WEEKDAYS[wd].title()} is {_fmt(_upcoming(wd))}.")
                break
    return problems
