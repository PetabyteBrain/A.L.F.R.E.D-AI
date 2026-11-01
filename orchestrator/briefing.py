"""Daily briefing: a short morning summary per person, by Telegram or phone, at a time they choose.

The facts are gathered here in Python (weather, the calendar, reminders, calls, own tasks, lists and notes, and the
business side: company to-do, clients, boards, team, failed n8n runs) and Claude only turns them into a few lines in
Alfred's voice, so the briefing never invents anything and costs one small model call. A per-weekday plan can make a
day's briefing personal, business or both, a weekly overview instead, with a focus note, or skip quiet days.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import ai_config, db, usage
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

# What a briefing covers and when. "content": personal (weather, own calendar, reminders, calls, tasks, lists and
# notes) | business (company to-do, clients, boards, team) | both. Without "custom", `days` (daily / weekdays) decides
# and every briefing is a daily one with the default content. With "custom", each weekday (Monday first) says
# send: off | day (today's briefing) | week (an overview of the week ahead), its content and an optional focus note.
# "skip_empty": a daily briefing with nothing time-relevant in it isn't sent (weekly overviews always are).
CONTENTS = ("both", "personal", "business")
SENDS = ("off", "day", "week")
NOTE_MAX = 120
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _default_plan() -> dict:
    return {"custom": False, "content": "both", "skip_empty": False,
            "days": [{"send": "day", "content": "both", "note": ""} for _ in WEEKDAYS]}


def _clean_plan(raw) -> dict:
    """A complete, valid plan from whatever was stored or sent (unknown values fall back to the defaults)."""
    import json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    raw = raw if isinstance(raw, dict) else {}
    out = _default_plan()
    out["custom"] = bool(raw.get("custom", False))
    out["skip_empty"] = bool(raw.get("skip_empty", False))
    if raw.get("content") in CONTENTS:
        out["content"] = raw["content"]
    days = raw.get("days") if isinstance(raw.get("days"), list) else []
    for i, d in enumerate(days[:7]):
        if not isinstance(d, dict):
            continue
        if d.get("send") in SENDS:
            out["days"][i]["send"] = d["send"]
        if d.get("content") in CONTENTS:
            out["days"][i]["content"] = d["content"]
        out["days"][i]["note"] = " ".join(str(d.get("note") or "").split())[:NOTE_MAX]
    return out


def get_settings(user_id: int) -> dict:
    row = db.query_one("SELECT enabled, time_local, days, channel, location, lat, lon, last_sent_on, plan "
                       "FROM briefings WHERE user_id = ?", (user_id,))
    out = {**DEFAULTS, **(row or {})}
    out["enabled"] = bool(out["enabled"])
    out["plan"] = _clean_plan(out.get("plan"))
    return out


def has_business(person: Person) -> bool:
    """Whether business content can be part of this person's briefing at all (never while an admin has switched
    the business features off)."""
    from . import permissions
    if not permissions.business_enabled():
        return False
    return person.can("files.company") or bool(permissions.for_person(person) & permissions.BUSINESS)


def day_plan(cfg: dict, day, person: Person | None = None) -> dict | None:
    """What the briefing on that date is: {"kind": day|week, "content", "note"}, or None when there's none that day."""
    plan, wd = cfg["plan"], day.weekday()
    if plan["custom"]:
        d = plan["days"][wd]
        if d["send"] == "off":
            return None
        out = {"kind": d["send"], "content": d["content"], "note": d["note"]}
    else:
        if cfg["days"] == "weekdays" and wd >= 5:
            return None
        out = {"kind": "day", "content": plan["content"], "note": ""}
    if person is not None and not has_business(person):
        out["content"] = "personal"
    return out


def carries_business(user_id: int, day) -> bool:
    """Does today's briefing bring this person's business news (so separate morning nudges can stay quiet)?"""
    cfg = get_settings(user_id)
    p = day_plan(cfg, day) if cfg["enabled"] else None
    return bool(p and p["content"] != "personal")


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
                          location: str | None = None, plan: dict | None = None) -> dict:
    import json
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
    if plan is not None:
        new["plan"] = _clean_plan(plan)
        if new["plan"]["custom"] and all(d["send"] == "off" for d in new["plan"]["days"]):
            raise BriefingError("Pick at least one day for the briefing, or switch it off.")
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
        "INSERT INTO briefings(user_id, enabled, time_local, days, channel, location, lat, lon, last_sent_on, plan, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, "
        "time_local = excluded.time_local, days = excluded.days, channel = excluded.channel, "
        "location = excluded.location, lat = excluded.lat, lon = excluded.lon, "
        "last_sent_on = excluded.last_sent_on, plan = excluded.plan, updated_at = excluded.updated_at",
        (user_id, int(new["enabled"]), new["time_local"], new["days"], new["channel"], new["location"],
         new["lat"], new["lon"], new["last_sent_on"], json.dumps(new["plan"]), db.now_iso()))
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


def _uses_tool(person: Person, tool: str) -> bool:
    from .tools import registry as tools
    spec = tools.spec(tool)
    return bool(spec and spec.allowed_for(person))


async def gather(person: Person, plan: dict | None = None) -> dict:
    """Everything for one briefing. `plan` (from day_plan) picks the content and today vs. the week ahead;
    without one it is today's briefing with everything the person may see. Each part is optional and fails soft."""
    plan = plan or {"kind": "day", "content": "both" if has_business(person) else "personal", "note": ""}
    personal, business, week = plan["content"] != "business", plan["content"] != "personal", plan["kind"] == "week"
    now = _now_local()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=7 if week else 1)) - timedelta(seconds=1)
    utc = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    when = (lambda dt: dt.strftime("%a %H:%M")) if week else (lambda dt: dt.strftime("%H:%M"))  # noqa: E731
    cfg = get_settings(person.id)
    out: dict = {"date": now.strftime("%A %d %B %Y"), "time": now.strftime("%H:%M"), "person": person.display_name,
                 "briefing": "the week ahead (today and the next 6 days)" if week else "today",
                 "covers": {"both": "personal and business", "personal": "personal only",
                            "business": "business only"}[plan["content"]]}
    if plan.get("note"):
        out["focus"] = plan["note"]

    if personal and cfg["lat"] is not None and not week:
        try:
            out["weather"] = {"place": cfg["location"], **await _weather(cfg["lat"], cfg["lon"])}
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing weather failed: %s", e)
            out["weather"] = "unavailable right now"

    from . import google_calendar
    if google_calendar.is_linked(person.id):     # one calendar for both: business-only briefings still show the day
        key = "calendar_week" if week else "calendar_today"
        try:
            events = await google_calendar.list_events(person.id, start.isoformat(), end.isoformat(),
                                                       max_results=40 if week else 20)
            out[key] = [{"time": (e["start"][:10] + " " if week else "") + ("all day" if e["all_day"] else e["start"][11:16]),
                         "title": e["summary"], **({"where": e["location"]} if e["location"] else {})} for e in events]
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing calendar failed: %s", e)
            out[key] = "unavailable right now"

    if personal:
        rem = db.query("SELECT text, due_at FROM reminders WHERE user_id = ? AND status = 'pending' AND due_at <= ? "
                       "ORDER BY due_at", (person.id, utc(end)))
        out["reminders"] = [{"time": when(datetime.fromisoformat(r["due_at"]).astimezone(_tz())), "text": r["text"]}
                            for r in rem]
        calls = db.query(
            "SELECT c.run_at, c.purpose, t.display_name AS target FROM scheduled_calls c JOIN users t ON t.id = c.target_user_id "
            "WHERE c.status = 'pending' AND c.run_at <= ? AND (c.requested_by = ? OR c.target_user_id = ?) ORDER BY c.run_at",
            (utc(end), person.id, person.id))
        out["calls_planned"] = [{"time": when(datetime.fromisoformat(c["run_at"]).astimezone(_tz())),
                                 "to": c["target"], "purpose": c["purpose"]} for c in calls]
        done = db.query(
            "SELECT c.status, c.result, t.display_name AS target FROM scheduled_calls c JOIN users t ON t.id = c.target_user_id "
            "WHERE c.requested_by = ? AND c.target_user_id != ? AND c.status IN ('done','no_answer','failed') "
            "AND c.updated_at >= ? ORDER BY c.updated_at", (person.id, person.id, utc(now - timedelta(hours=24))))
        if done:
            out["call_outcomes_last_24h"] = [{"to": c["target"], "status": c["status"], "result": c["result"]} for c in done]
        if _uses_tool(person, "list_tasks"):      # their own tasks only (ones they hold, or asked for and nobody holds)
            tasks = db.query("SELECT title, due_at FROM tasks WHERE status = 'active' AND (owner_user_id = ? OR "
                             "(owner_user_id IS NULL AND requested_by = ?)) ORDER BY COALESCE(due_at, created_at) LIMIT 15",
                             (person.id, person.id))
            if tasks:
                out["open_tasks"] = [t["title"] + (f" (due {t['due_at'][:10]})" if t["due_at"] else "") for t in tasks]
        if person.can("files.own"):
            from . import nas
            if nas.enabled():
                try:
                    out.update(await asyncio.to_thread(nas.briefing_digest, person))
                except Exception as e:  # noqa: BLE001
                    log.warning("Briefing lists/notes failed: %s", e)

    if business:
        await _business(person, out, now, week)
    return out


async def _business(person: Person, out: dict, now: datetime, week: bool) -> None:
    company = await _company_todo(person, now)
    if company:
        out["company_todo"] = company

    # CRM stage 4/5: the person's client follow-ups, who's away, their onboarding/offboarding steps.
    try:
        if person.can("crm.use"):
            from . import crm_proactive
            items = crm_proactive.follow_ups(person)
            if items:
                out["crm_follow_ups"] = {"count": len(items), "top": [
                    f"{i['title']}" + (f" ({i['company_name']})" if i.get("company_name") and i["entity"] == "deal" else "")
                    + f": {i['detail']}" for i in items[:5]]}
        from . import employees
        if person.can("employees.view"):
            if week:
                away = {}
                for n in range(7):
                    d = now.date() + timedelta(days=n)
                    names = [a["name"] for a in employees.away_today(d)]
                    if names:
                        away[d.strftime("%a %d %b")] = names
            else:
                away = employees.away_today(now.date())
            if away:
                out["team_away_this_week" if week else "team_away_today"] = away
        steps = [t for t in employees.due_tasks(now.date(), unreminded_only=False) if t["assignee_id"] == person.id]
        if steps:
            out["checklist_steps_due"] = [f"{t['employee_name']} ({t['kind']}): {t['title']}" for t in steps[:6]]
            employees.mark_reminded([t["id"] for t in steps], now.date())
    except Exception as e:  # noqa: BLE001
        log.warning("Briefing CRM part failed: %s", e)
    try:                                          # work boards (#59): what's due, requests to decide
        from . import boards
        work = boards.briefing_block(person)
        if work:
            out["work_items"] = work
    except Exception as e:  # noqa: BLE001
        log.warning("Briefing boards part failed: %s", e)
    if week:
        try:                                      # board items, deals and invoices falling due this week
            from . import deadlines
            first, last = now.date().isoformat(), (now.date() + timedelta(days=6)).isoformat()
            due = sorted(deadlines.items(person, first, last), key=lambda d: d["start"])
            if due:
                out["deadlines_this_week"] = [f"{d['start']} {d['title']}" + (" (overdue)" if d["overdue"] else "")
                                              for d in due[:12]]
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing deadlines part failed: %s", e)

    if settings.n8n_enabled and person.can("automations.view"):
        try:
            from .tools import n8n_tool
            runs = await n8n_tool.run_action("list_executions", {"status": "error", "limit": 20})
            since = (now - timedelta(hours=24)).astimezone(timezone.utc).isoformat(timespec="seconds")
            failed = [r for r in runs if (r.get("startedAt") or "") >= since[:19]]
            if failed:
                out["n8n_failed_runs_last_24h"] = len(failed)
        except Exception as e:  # noqa: BLE001
            log.warning("Briefing n8n check failed: %s", e)


# Parts that make a daily briefing worth sending when "skip_empty" is on (weather and plain counts don't).
_TIME_RELEVANT = ("calendar_today", "reminders", "calls_planned", "call_outcomes_last_24h", "checklist_steps_due",
                  "crm_follow_ups", "team_away_today", "n8n_failed_runs_last_24h")


def has_news(data: dict) -> bool:
    """Anything time-relevant for today: events, reminders, calls, things due or overdue."""
    if any(isinstance(data.get(k), (list, dict, int)) and data.get(k) for k in _TIME_RELEVANT):
        return True
    work = data.get("work_items") or {}
    if work.get("due_today_or_overdue") or work.get("task_requests_to_decide"):
        return True
    today = _now_local().date().isoformat()
    return any(t.endswith(")") and "(due " in t and t[-11:-1] <= today for t in data.get("open_tasks") or [])


STALE_DAYS = 14
_PROGRESS_KEY = "company_todo_progress_seen"


async def _company_todo(person: Person, now: datetime) -> dict | None:
    """Admins/cofounders: the top open company to-dos; on Mondays also what's been "in progress"
    for 2+ weeks (tracked here, since the workbook has no dates)."""
    import asyncio
    from . import nas, company_todo
    if not person.can("files.company") or not nas.enabled():
        return None
    try:
        todo = await asyncio.to_thread(company_todo.show, person)
    except Exception as e:  # noqa: BLE001
        log.warning("Briefing company to-do failed: %s", e)
        return {"note": "unavailable right now"}
    current = sorted(todo["current"], key=lambda i: -(i.get("importance") or 0))
    out = {"top_current": [{"todo": i["description"], "importance": i.get("importance"), "status": i.get("status")}
                           for i in current[:5]],
           "open_current": len(current), "future": len(todo["future"])}
    seen = db.get_setting(_PROGRESS_KEY, {}) or {}
    today = now.date().isoformat()
    in_progress = {i["description"] for i in current if (i.get("status") or "").lower() == "in progress"}
    seen = {d: seen.get(d, today) for d in in_progress}          # forget items that moved on
    db.set_setting(_PROGRESS_KEY, seen)
    if now.weekday() == 0:
        stale = [d for d, first in seen.items()
                 if (now.date() - datetime.fromisoformat(first).date()).days >= STALE_DAYS]
        if stale:
            out["in_progress_for_2_weeks_or_more"] = stale
    return out


# --- composing & sending -------------------------------------------------------------------------

async def compose(person: Person, data: dict, channel: str) -> str:
    """Turn the gathered facts into the briefing text (one small Claude call)."""
    import json
    from .agent_core import client
    who = "sir" if person.is_admin else person.display_name.split()[0]
    week = "calendar_week" in data or data.get("briefing", "").startswith("the week")
    what = "weekly briefing (the week ahead)" if week else "daily briefing"
    style = ("It will be read aloud on a phone call: 4-7 short spoken sentences, no lists, no symbols, "
             "no emoji, times said naturally." if channel == "call" else
             "It is a Telegram message: a greeting line, then short lines grouped as needed (weather, "
             "calendar, reminders, calls, tasks, lists and notes, company to-do, clients, team), plain text with at most a "
             f"few emoji as markers, no headings, no Markdown tables. Under {1400 if week else 900} characters.")
    system = (f"You are {settings.assistant_name}, a discreet, warm British butler (Alfred Pennyworth's manner) "
              f"writing {who}'s {what}. Use ONLY the facts in the JSON; never invent events or details. "
              f"Skip sections that are empty or missing; say 'a clear calendar' if calendar_today or calendar_week is an "
              f"empty list. 'covers' says whether this briefing is personal, business or both: stay within it. "
              f"If 'focus' is present, it is what they want this briefing to concentrate on: lead with the facts that "
              f"fit it and keep the rest short (if nothing fits, say so in a few words). For a week ahead, group "
              f"events and deadlines by day. open_list_items are open entries on their own lists; notes_changed_lately "
              f"are their notes worked on this week (titles only). For company_todo give the top few items briefly; if "
              f"in_progress_for_2_weeks_or_more is present, add a gentle nudge about those. crm_follow_ups are clients "
              f"and deals to follow up on: name the top one or two briefly and the count. team_away_today / "
              f"team_away_this_week: who's away. checklist_steps_due: onboarding or offboarding steps that are theirs "
              f"today. work_items: their board items due today or overdue (name them) and task requests waiting for "
              f"their decision. deadlines_this_week: board items, deals and invoices falling due. Mention anything "
              f"'unavailable' in a few words. {style} Address them as {who}. End with one brief, dry, kind remark, "
              f"not a question.")
    if not ai_config.configured():
        return f"Good morning, {who}. (Briefing text needs the language model, which isn't configured.)"
    resp = await client().messages.create(model=settings.claude_model, max_tokens=700 if week else 500, system=system,
                                          messages=[{"role": "user", "content": json.dumps(data, ensure_ascii=False)}])
    usage.record_claude(getattr(resp, "usage", None), kind="briefing", user_id=person.id)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


async def send(person: Person, channel: str | None = None, *, scheduled: bool = False) -> str | None:
    """Build and deliver a briefing now, as today's plan says (a day without one gets the daily briefing when
    asked for by hand). Returns the channel used, or None when a scheduled daily briefing had nothing to say."""
    cfg = get_settings(person.id)
    channel = channel or cfg["channel"]
    plan = day_plan(cfg, _now_local().date(), person)
    data = await gather(person, plan)
    if scheduled and plan and plan["kind"] == "day" and cfg["plan"]["skip_empty"] and not has_news(data):
        db.log_activity("message", f"Daily briefing for {person.display_name} skipped: nothing on today",
                        user_id=person.id)
        return None
    label = "weekly briefing" if plan and plan["kind"] == "week" else "daily briefing"
    if channel == "call":
        if not registry.phone_for(person.id):
            raise BriefingError("No verified phone number to call.")
        from . import scheduler
        text = await compose(person, data, "call")
        await scheduler.request_call(person.id, requested_by=person,
                                     purpose=f"Read them their {label}, then offer help: {text}",
                                     opening_line=f"{_short_greeting(person)} Your {label}.")
    else:
        if not (settings.telegram_enabled and registry.telegram_for(person.id)):
            raise BriefingError("No verified Telegram account to send it to.")
        from .telegram_handler import send_to_user
        await send_to_user(person.id, await compose(person, data, "telegram"), note=label, silent=False)
    db.log_activity("message", f"{label.capitalize()} sent to {person.display_name} by {channel}", user_id=person.id)
    return channel


def _short_greeting(person: Person) -> str:
    h = _now_local().hour
    part = "morning" if 5 <= h < 12 else "afternoon" if h < 18 else "evening"
    who = "sir" if person.is_admin else person.display_name.split()[0]
    return f"Good {part}, {who}. {settings.assistant_name} here."


async def _send_scheduled(person: Person) -> None:
    try:
        await send(person, scheduled=True)
    except Exception as e:  # noqa: BLE001
        log.exception("Scheduled briefing for %s failed", person.display_name)
        db.log_activity("error", f"Daily briefing for {person.display_name} failed: {str(e)[:200]}", user_id=person.id)


async def tick() -> None:
    now = _now_local()
    today = now.date().isoformat()
    for row in db.query("SELECT b.user_id, b.time_local FROM briefings b JOIN users u ON u.id = b.user_id "
                        "WHERE b.enabled = 1 AND u.active = 1 AND (b.last_sent_on IS NULL OR b.last_sent_on != ?)",
                        (today,)):
        if day_plan(get_settings(row["user_id"]), now.date()) is None:
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
