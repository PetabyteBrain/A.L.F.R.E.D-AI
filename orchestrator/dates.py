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


def _resolve(day: int, month: int, year: int | None, today: date | None = None) -> date | None:
    try:
        if year:
            return date(year, month, day)
        today = today or _today()
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


# ---------------------------------------------------------------- resolve_date tool
# Turns what people say ("this Friday", "the 15th", "in three weeks") into exact dates, so the model
# copies a date instead of counting. English only and deliberately small: anything it can't read
# comes back as an error and the model asks the person.

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "thirty": 30}
_N = r"(\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fourteen|thirty)"
_SIMPLE = {"today": 0, "tonight": 0, "tomorrow": 1, "tmrw": 1, "day after tomorrow": 2,
           "the day after tomorrow": 2, "yesterday": -1, "day before yesterday": -2}
_WEEKEND = ("this weekend", "the weekend", "weekend", "at the weekend", "on the weekend", "over the weekend")


def _n(token: str) -> int:
    return int(token) if token.isdigit() else _NUM[token.lower()]


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    first = date(d.year + y, m + 1, 1)
    last = (_next_month_first(first) - timedelta(days=1)).day
    return first.replace(day=min(d.day, last))


def _next_month_first(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def _month_end(d: date) -> date:
    return _next_month_first(d) - timedelta(days=1)


def _entry(d: date, today: date, end: date | None = None, note: str = "") -> dict:
    out = {"date": d.isoformat(), "day": _fmt(d)}
    if end:
        out.update(end=end.isoformat(), end_day=_fmt(end))
    if (end or d) < today:
        note = (note + " " if note else "") + "This is in the past."
    if note:
        out["note"] = note
    return out


def _clean(phrase: str) -> str:
    p = " ".join((phrase or "").lower().replace(",", " ").split())
    p = re.sub(r"\b(?:at|around|by|from|until)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|h)?$", "", p).strip()
    p = re.sub(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(?:am|pm)\b|\b(?:in the\s+)?(?:morning|afternoon|evening|night|noon|midday)\b",
               "", p)
    return " ".join(p.split())


def resolve(phrase: str, today: date | None = None) -> dict:
    """{"date", "day", optional "end"/"end_day" (a period) and "note"}, or {"error"}, for one phrase."""
    today = today or _today()
    p = _clean(phrase)
    if not p:
        return {"error": "There's no day in that."}
    monday = today - timedelta(days=today.weekday())

    if p in _SIMPLE:
        return _entry(today + timedelta(days=_SIMPLE[p]), today)
    if m := re.fullmatch(rf"(in\s+)?{_N}\s+(day|week|month|year)s?(\s+(?:from\s+(?:now|today)|time))?", p):
        if not (m.group(1) or m.group(4)):
            return {"error": "Unclear: is that 'in ...' or '... ago'? Ask the person."}
        n, unit = _n(m.group(2)), m.group(3)
        d = (today + timedelta(days=n) if unit == "day" else today + timedelta(weeks=n) if unit == "week"
             else _add_months(today, n * (12 if unit == "year" else 1)))
        return _entry(d, today)
    if p in ("in a fortnight", "a fortnight", "a fortnight today", "a week today", "a week from today"):
        return _entry(today + timedelta(days=14 if "fortnight" in p else 7), today)

    if p == "this week":
        return _entry(today, today, monday + timedelta(days=6))
    if p == "next week":
        return _entry(monday + timedelta(days=7), today, monday + timedelta(days=13), "The whole week, Monday to Sunday.")
    if p == "last week":
        return _entry(monday - timedelta(days=7), today, monday - timedelta(days=1))
    if p in _WEEKEND:
        if today.weekday() == 6:
            return _entry(today, today, note=f"It's Sunday, so that's today; next weekend starts {_fmt(today + timedelta(days=6))}.")
        return _entry(max(monday + timedelta(days=5), today), today, monday + timedelta(days=6))
    if p == "next weekend":
        return _entry(monday + timedelta(days=12), today, monday + timedelta(days=13),
                      f"Some people mean the coming weekend ({_fmt(monday + timedelta(days=5))}): say which you understood.")
    if p in ("end of the week", "end of this week", "the end of the week", "end of week"):
        return _entry(monday + timedelta(days=4), today, note="Taken as Friday.")
    if p in ("end of the month", "end of this month", "the end of the month", "end of month"):
        return _entry(_month_end(today), today)
    if p in ("end of next month", "the end of next month"):
        return _entry(_month_end(_next_month_first(today)), today)
    if p == "next month":
        first = _next_month_first(today)
        return _entry(first, today, _month_end(first), "The whole month.")
    if p == "this month":
        return _entry(today, today, _month_end(today))

    # "Friday", "this Friday", "next Friday", "Friday next week", "next Friday week", "last Friday"...
    if m := re.fullmatch(rf"(?:on\s+)?(?:(?:the\s+)?(this|next|last|coming|following)\s+)?{_WD}"
                         rf"(?:\s+(this|next|last)\s+week|\s+(week))?", p):
        mod, wd, week, plus_week = m.group(1) or "", _wd_index(m.group(2)), m.group(3), m.group(4)
        if plus_week:                                   # "next Friday week" = a week after the coming one
            return _entry(_upcoming(wd) + timedelta(days=7), today, note="Read as a week after the coming one.")
        if week == "next":
            return _entry(monday + timedelta(days=7 + wd), today)
        if week == "this":
            return _entry(monday + timedelta(days=wd), today)
        if "last" in (week, mod):
            return _entry(today - timedelta(days=(today.weekday() - wd) % 7 or 7), today)
        if mod == "following":
            return _entry(_upcoming(wd) + timedelta(days=7), today, note="Read as the one after the coming one.")
        if mod == "next":
            d = _upcoming(wd)
            return _entry(d, today, note=f"Some people mean the week after ({_fmt(d + timedelta(days=7))}): "
                                         "say which date you understood.")
        if wd == today.weekday() and mod != "coming":
            return _entry(today, today, note=f"That's today; the one next week is {_fmt(today + timedelta(days=7))}.")
        return _entry(_upcoming(wd), today)

    # Explicit dates, optionally after a weekday (which must agree).
    wd_given = None
    if m := re.match(rf"(?:on\s+)?{_WD}\.?\s+(?:the\s+)?(?=\d|{_MO})", p):
        wd_given, p = _wd_index(m.group(1)), p[m.end():]
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p):
        d = _resolve(int(m.group(3)), int(m.group(2)), int(m.group(1)), today)
    elif m := re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?\.?", p):     # day first (Swiss)
        y = m.group(3)
        d = _resolve(int(m.group(1)), int(m.group(2)), (int(y) + 2000 if len(y) == 2 else int(y)) if y else None, today)
    elif m := re.fullmatch(rf"(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\.?\s+(?:of\s+)?{_MO}\.?(?:\s+(\d{{4}}))?", p):
        d = _resolve(int(m.group(1)), _MONTHS.index(m.group(2)[:3]) + 1, int(m.group(3)) if m.group(3) else None, today)
    elif m := re.fullmatch(rf"{_MO}\.?\s+(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+(\d{{4}}))?", p):
        d = _resolve(int(m.group(2)), _MONTHS.index(m.group(1)[:3]) + 1, int(m.group(3)) if m.group(3) else None, today)
    elif m := re.fullmatch(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)", p):            # "the 15th": next one to come
        d, first = None, today.replace(day=1)
        for _ in range(3):
            if int(m.group(1)) <= _month_end(first).day and (cand := first.replace(day=int(m.group(1)))) >= today:
                d = cand
                break
            first = _next_month_first(first)
    else:
        return {"error": "Couldn't read that; ask the person for the date."}
    if not d:
        return {"error": "That date doesn't exist."}
    if wd_given is not None and d.weekday() != wd_given:
        return {"error": f"Doesn't add up: {d.day} {d.strftime('%B %Y')} is a {WEEKDAYS[d.weekday()].title()}, not a "
                         f"{WEEKDAYS[wd_given].title()}. The coming {WEEKDAYS[wd_given].title()} is "
                         f"{_fmt(_upcoming(wd_given))}. Ask the person which they meant."}
    return _entry(d, today)


def resolve_many(phrases: list[str], today: date | None = None) -> dict:
    today = today or _today()
    return {"today": _fmt(today),
            "dates": [{"phrase": ph, **resolve(ph, today)} for ph in phrases[:10] if isinstance(ph, str)]}
