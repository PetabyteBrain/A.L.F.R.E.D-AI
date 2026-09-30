"""Quiet hours: when Alfred shouldn't disturb someone.

A global default (QUIET_HOURS in .env, e.g. 22:00-07:00; editable by an admin on the dashboard)
applies to everyone who hasn't chosen otherwise; each person can pick their own window or turn
quiet hours off (Profile).

What they do, for the person being contacted:
- calls that *someone else* asked for wait until the quiet hours end (an urgent call placed by
  an admin/cofounder is the exception);
- proactive Telegram messages arrive silently (no sound/vibration).
Anything the person set up for themselves (own reminders, briefing, "call me") is not held back:
they chose that time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings

HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MODES = ("default", "custom", "off")
_DEFAULT_KEY = "quiet_hours_default"


class QuietError(ValueError):
    """Message is safe to show to the user / the model."""


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def clean_hhmm(value: str) -> str:
    v = str(value or "").strip().replace(".", ":")
    if re.fullmatch(r"\d:\d\d", v):
        v = "0" + v
    if not HHMM.fullmatch(v):
        raise QuietError("Times must be HH:MM, e.g. 22:00.")
    return v


def _env_default() -> dict:
    raw = (settings.quiet_hours or "").strip().lower()
    if raw in ("", "off", "none", "0"):
        return {"enabled": False, "start": "22:00", "end": "07:00"}
    try:
        start, end = (clean_hhmm(x) for x in raw.split("-", 1))
    except (ValueError, QuietError):
        return {"enabled": True, "start": "22:00", "end": "07:00"}
    return {"enabled": True, "start": start, "end": end}


def get_default() -> dict:
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (_DEFAULT_KEY,))
    if row:
        try:
            return {**_env_default(), **json.loads(row["value"])}
        except ValueError:
            pass
    return _env_default()


def set_default(*, enabled: bool, start: str, end: str) -> dict:
    value = {"enabled": bool(enabled), "start": clean_hhmm(start), "end": clean_hhmm(end)}
    if value["start"] == value["end"]:
        raise QuietError("Start and end can't be the same time.")
    db.execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
               "value = excluded.value, updated_at = excluded.updated_at",
               (_DEFAULT_KEY, json.dumps(value), db.now_iso()))
    return value


def get_person(user_id: int) -> dict:
    row = db.query_one("SELECT mode, start, end FROM quiet_hours WHERE user_id = ?", (user_id,))
    return dict(row) if row else {"mode": "default", "start": None, "end": None}


def set_person(user_id: int, *, mode: str, start: str | None = None, end: str | None = None) -> dict:
    if mode not in MODES:
        raise QuietError("Mode must be default, custom or off.")
    if mode == "custom":
        start, end = clean_hhmm(start), clean_hhmm(end)
        if start == end:
            raise QuietError("Start and end can't be the same time.")
    else:
        start = end = None
    db.execute("INSERT INTO quiet_hours(user_id, mode, start, end, updated_at) VALUES (?,?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode, start = excluded.start, end = excluded.end, "
               "updated_at = excluded.updated_at", (user_id, mode, start, end, db.now_iso()))
    return get_person(user_id)


def window_for(user_id: int) -> tuple[str, str] | None:
    """The person's effective quiet window as (start, end) HH:MM local, or None."""
    p = get_person(user_id)
    if p["mode"] == "off":
        return None
    if p["mode"] == "custom" and p["start"] and p["end"]:
        return p["start"], p["end"]
    d = get_default()
    return (d["start"], d["end"]) if d["enabled"] else None


def _t(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m)


def quiet_until(user_id: int, at: datetime | None = None) -> datetime | None:
    """If `at` (default now) falls in the person's quiet hours, when they end (aware UTC); else None."""
    w = window_for(user_id)
    if not w:
        return None
    local = (at or datetime.now(timezone.utc)).astimezone(_tz())
    start, end = _t(w[0]), _t(w[1])
    now_t = local.time().replace(second=0, microsecond=0)
    if start < end:                       # same-day window, e.g. 12:00-14:00
        quiet = start <= now_t < end
        end_day = local.date()
    else:                                 # overnight, e.g. 22:00-07:00
        quiet = now_t >= start or now_t < end
        end_day = local.date() + timedelta(days=1) if now_t >= start else local.date()
    if not quiet:
        return None
    return datetime.combine(end_day, end, tzinfo=_tz()).astimezone(timezone.utc)


def is_quiet(user_id: int, at: datetime | None = None) -> bool:
    return quiet_until(user_id, at) is not None


def describe(user_id: int) -> dict:
    """For the dashboard / tools: settings, effective window and whether it's quiet right now."""
    p, w, until = get_person(user_id), window_for(user_id), quiet_until(user_id)
    return {**p, "default": get_default(), "effective": {"start": w[0], "end": w[1]} if w else None,
            "quiet_now": until is not None,
            "until_local": until.astimezone(_tz()).strftime("%H:%M") if until else None}
