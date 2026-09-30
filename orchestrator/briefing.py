"""Daily briefing: a short morning summary per person, by Telegram or phone, at a time they choose.

The facts are gathered here in Python (weather, today's calendar, reminders, calls, tasks and,
for admins, failed n8n runs) and Claude only turns them into a few lines in Alfred's voice, so
the briefing never invents anything and costs one small model call.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db, usage
from .config import settings
from .identity import registry
from .identity.registry import Person

log = logging.getLogger(__name__)

POLL_SECONDS = 60
_running: set[asyncio.Task] = set()
LATE_LIMIT = timedelta(hours=2)   # after downtime, skip a briefing that's more than 2 h overdue
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes (Open-Meteo), condensed.
WEATHER = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
           51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
           61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
           71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
           81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
           95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


class BriefingError(ValueError):
    """Message is safe to show to the user / the model."""


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _now_local() -> datetime:
    return datetime.now(_tz())


# --- settings --------------------------------------------------------------------------------

DEFAULTS = {"enabled": False, "time_local": "07:30", "days": "daily", "channel": "telegram",
            "location": None, "lat": None, "lon": None, "last_sent_on": None}


def get_settings(user_id: int) -> dict:
    row = db.query_one("SELECT enabled, time_local, days, channel, location, lat, lon, last_sent_on "
                       "FROM briefings WHERE user_id = ?", (user_id,))
    out = {**DEFAULTS, **(row or {})}
    out["enabled"] = bool(out["enabled"])
    return out


async def geocode(place: str) -> dict:
    place = " ".join(str(place or "").split())[:80]
    if not place:
        raise BriefingError("Say which town the weather should be for.")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(GEO_URL, params={"name": place, "count": 1, "language": "en", "format": "json"})
            r.raise_for_status()
            hits = r.json().get("results") or []
    except httpx.HTTPError:
        raise BriefingError("The weather service couldn't be reached to look up that town. Try again later.")
    usage.record_request("open-meteo", "geocoding")
    if not hits:
        raise BriefingError(f"Couldn't find a place called '{place}'.")
    h = hits[0]
    label = ", ".join(x for x in (h.get("name"), h.get("admin1"), h.get("country")) if x)
    return {"location": label, "lat": float(h["latitude"]), "lon": float(h["longitude"])}


async def update_settings(user_id: int, *, enabled: bool | None = None, time_local: str | None = None,
                          days: str | None = None, channel: str | None = None,
                          location: str | None = None) -> dict:
    cur = get_settings(user_id)
    new = dict(cur)
    if enabled is not None:
        new["enabled"] = bool(enabled)
    if time_local is not None:
        t = str(time_local).strip()
        if re.fullmatch(r"\d:\d\d", t):
            t = "0" + t
        if not TIME_RE.fullmatch(t):
            raise BriefingError("Time must be HH:MM, e.g. 07:30.")
        new["time_local"] = t
    if days is not None:
        if days not in ("daily", "weekdays"):
            raise BriefingError("Days must be daily or weekdays.")
        new["days"] = days
    if channel is not None:
        if channel not in ("telegram", "call"):
            raise BriefingError("Channel must be telegram or call.")
        new["channel"] = channel
    if location is not None:
        if location.strip():
            new.update(await geocode(location))
        else:
            new.update(location=None, lat=None, lon=None)
    if new["enabled"]:
        if new["channel"] == "telegram" and not registry.telegram_for(user_id):
            raise BriefingError("Add and verify a Telegram connection first, or choose a phone call.")
        if new["channel"] == "call" and not registry.phone_for(user_id):
            raise BriefingError("Add and verify a phone number first, or choose Telegram.")
    # Changing the time today shouldn't swallow today's briefing if the new time is still ahead.
    if new["time_local"] != cur["time_local"] or (new["enabled"] and not cur["enabled"]):
        new["last_sent_on"] = None if _due_time(new["time_local"]) > _now_local() else _now_local().date().isoformat()
    db.execute(
        "INSERT INTO briefings(user_id, enabled, time_local, days, channel, location, lat, lon, last_sent_on, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, "
        "time_local = excluded.time_local, days = excluded.days, channel = excluded.channel, "
        "location = excluded.location, lat = excluded.lat, lon = excluded.lon, "
        "last_sent_on = excluded.last_sent_on, updated_at = excluded.updated_at",
        (user_id, int(new["enabled"]), new["time_local"], new["days"], new["channel"], new["location"],
         new["lat"], new["lon"], new["last_sent_on"], db.now_iso()))
    return get_settings(user_id)


def _due_time(time_local: str, day: datetime | None = None) -> datetime:
    day = day or _now_local()
    h, m = map(int, time_local.split(":"))
    return datetime.combine(day.date(), time(h, m), tzinfo=_tz())


# --- gathering ---------------------------------------------------------------------------------

async def _weather(lat: float, lon: float) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(WEATHER_URL, params={
            "latitude": lat, "longitude": lon, "timezone": settings.timezone, "forecast_days": 1,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum"})
        r.raise_for_status()
        d = r.json()["daily"]
    usage.record_request("open-meteo", "forecast")
    return {"conditions": WEATHER.get(d["weather_code"][0], "mixed"),
            "high_c": round(d["temperature_2m_max"][0]), "low_c": round(d["temperature_2m_min"][0]),
            "rain_chance_pct": d["precipitation_probability_max"][0], "precip_mm": d["precipitation_sum"][0]}


def _uses_tool(role: str, tool: str) -> bool:
    from .tools import registry as tools
    if not tools._TOOLS:
        tools.load()
    spec = tools._TOOLS.get(tool)
    return bool(spec and spec.allowed_for(role))


async def gather(person: Person) -> dict:
    """Everything for today's briefing. Each part is optional and fails soft."""
    now = _now_local()
    start, end = now.replace(hour=0, minute=0, second=0, microsecond=0), now.replace(hour=23, minute=59, second=59)
    utc = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    cfg = get_settings(person.id)
    out: dict = {"date": now.strftime("%A %d %B %Y"), "time": now.strftime("%H:%M"), "person": person.display_name}

    if cfg["lat"] is not None:
        try:
            out["weather"] = {"place": cfg["location"], **await _weather(cfg["lat"], cfg["lon"])}
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing weather failed: %s", e)
            out["weather"] = "unavailable right now"

    from . import google_calendar
    if google_calendar.is_linked(person.id):
        try:
            events = await google_calendar.list_events(person.id, start.isoformat(), end.isoformat(), max_results=20)
            out["calendar_today"] = [{"time": "all day" if e["all_day"] else e["start"][11:16], "title": e["summary"],
                                      **({"where": e["location"]} if e["location"] else {})} for e in events]
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing calendar failed: %s", e)
            out["calendar_today"] = "unavailable right now"

    rem = db.query("SELECT text, due_at FROM reminders WHERE user_id = ? AND status = 'pending' AND due_at <= ? "
                   "ORDER BY due_at", (person.id, utc(end)))
    out["reminders_today"] = [{"time": datetime.fromisoformat(r["due_at"]).astimezone(_tz()).strftime("%H:%M"),
                               "text": r["text"]} for r in rem]

    calls = db.query(
        "SELECT c.run_at, c.purpose, t.display_name AS target FROM scheduled_calls c JOIN users t ON t.id = c.target_user_id "
        "WHERE c.status = 'pending' AND c.run_at <= ? AND (c.requested_by = ? OR c.target_user_id = ?) ORDER BY c.run_at",
        (utc(end), person.id, person.id))
    out["calls_planned_today"] = [{"time": datetime.fromisoformat(c["run_at"]).astimezone(_tz()).strftime("%H:%M"),
                                   "to": c["target"], "purpose": c["purpose"]} for c in calls]
    done = db.query(
        "SELECT c.status, c.result, t.display_name AS target FROM scheduled_calls c JOIN users t ON t.id = c.target_user_id "
        "WHERE c.requested_by = ? AND c.target_user_id != ? AND c.status IN ('done','no_answer','failed') "
        "AND c.updated_at >= ? ORDER BY c.updated_at", (person.id, person.id, utc(now - timedelta(hours=24))))
    if done:
        out["call_outcomes_last_24h"] = [{"to": c["target"], "status": c["status"], "result": c["result"]} for c in done]

    if _uses_tool(person.role, "list_tasks"):
        out["open_tasks"] = [t["title"] for t in db.query("SELECT title FROM tasks WHERE status = 'active' ORDER BY id LIMIT 15")]

    if person.is_admin and settings.n8n_enabled:
        try:
            from .tools import n8n_tool
            runs = await n8n_tool.run_action("list_executions", {"status": "error", "limit": 20})
            since = utc(now - timedelta(hours=24))
            failed = [r for r in runs if (r.get("startedAt") or "") >= since[:19]]
            if failed:
                out["n8n_failed_runs_last_24h"] = len(failed)
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing n8n check failed: %s", e)
    return out


# --- composing & sending -------------------------------------------------------------------------

async def compose(person: Person, data: dict, channel: str) -> str:
    """Turn the gathered facts into the briefing text (one small Claude call)."""
    import json
    from .agent_core import client
    who = "sir" if person.is_admin else person.display_name.split()[0]
    style = ("It will be read aloud on a phone call: 4-7 short spoken sentences, no lists, no symbols, "
             "no emoji, times said naturally." if channel == "call" else
             "It is a Telegram message: a greeting line, then short lines grouped as needed (weather, "
             "calendar, reminders, calls, tasks), plain text with at most a few emoji as markers, no "
             "headings, no Markdown tables. Under 900 characters.")
    system = (f"You are {settings.assistant_name}, a discreet, warm British butler (Alfred Pennyworth's manner) "
              f"writing {who}'s daily briefing. Use ONLY the facts in the JSON; never invent events or details. "
              f"Skip sections that are empty or missing; say 'a clear calendar' if calendar_today is an empty "
              f"list. Mention anything 'unavailable' in a few words. {style} Address them as {who}. End with one "
              f"brief, dry, kind remark, not a question.")
    if not settings.anthropic_api_key:
        return f"Good morning, {who}. (Briefing text needs the language model, which isn't configured.)"
    resp = await client().messages.create(model=settings.claude_model, max_tokens=500, system=system,
                                          messages=[{"role": "user", "content": json.dumps(data, ensure_ascii=False)}])
    usage.record_claude(getattr(resp, "usage", None), kind="briefing", user_id=person.id)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


async def send(person: Person, channel: str | None = None) -> str:
    """Build and deliver a briefing now. Returns the channel used."""
    channel = channel or get_settings(person.id)["channel"]
    data = await gather(person)
    if channel == "call":
        if not registry.phone_for(person.id):
            raise BriefingError("No verified phone number to call.")
        from . import scheduler
        text = await compose(person, data, "call")
        await scheduler.request_call(person.id, requested_by=person,
                                     purpose=f"Read them their daily briefing, then offer help: {text}",
                                     opening_line=f"{_short_greeting(person)} Your daily briefing.")
    else:
        if not (settings.telegram_enabled and registry.telegram_for(person.id)):
            raise BriefingError("No verified Telegram account to send it to.")
        from .telegram_handler import send_to_user
        await send_to_user(person.id, await compose(person, data, "telegram"), note="daily briefing")
    db.log_activity("message", f"Daily briefing sent to {person.display_name} by {channel}", user_id=person.id)
    return channel


def _short_greeting(person: Person) -> str:
    h = _now_local().hour
    part = "morning" if 5 <= h < 12 else "afternoon" if h < 18 else "evening"
    who = "sir" if person.is_admin else person.display_name.split()[0]
    return f"Good {part}, {who}. {settings.assistant_name} here."


async def _send_scheduled(person: Person) -> None:
    try:
        await send(person)
    except Exception as e:  # noqa: BLE001
        log.exception("Scheduled briefing for %s failed", person.display_name)
        db.log_activity("error", f"Daily briefing for {person.display_name} failed: {str(e)[:200]}", user_id=person.id)


async def tick() -> None:
    now = _now_local()
    today = now.date().isoformat()
    for row in db.query("SELECT b.user_id, b.time_local, b.days FROM briefings b JOIN users u ON u.id = b.user_id "
                        "WHERE b.enabled = 1 AND u.active = 1 AND (b.last_sent_on IS NULL OR b.last_sent_on != ?)",
                        (today,)):
        if row["days"] == "weekdays" and now.weekday() >= 5:
            continue
        due = _due_time(row["time_local"], now)
        if not (due <= now < due + LATE_LIMIT):
            continue
        # Claim first, so a slow send can't be started twice.
        if not db.execute_count("UPDATE briefings SET last_sent_on = ? WHERE user_id = ? AND "
                                "(last_sent_on IS NULL OR last_sent_on != ?)", (today, row["user_id"], today)):
            continue
        person = registry.get_person(row["user_id"])
        if person:
            task = asyncio.create_task(_send_scheduled(person))
            _running.add(task)
            task.add_done_callback(_running.discard)


async def background_loop() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("Briefing loop iteration failed")
        await asyncio.sleep(POLL_SECONDS)
