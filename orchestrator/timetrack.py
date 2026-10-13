"""Time tracking link, CRM stage 4 (roadmap #58): hours from the existing time-tracking app (Chrono).

The app offers a read-only export (`GET /api/export/sessions?from=&to=`, bearer EXPORT_TOKEN there,
TIMETRACK_URL / TIMETRACK_TOKEN here). Each work session has a person, start and end, and a tag (in a tag group).
A session counts for a client when its tag or tag group is the company's time-tracking tag (set on the company;
several separated by commas) or, without one, has the company's name (legal form ignored: "Acme" = "Acme AG").
A session belongs to an employee through their time-tracking name (username or display name in the app).

Nothing is written back; results are cached for a few minutes. Without the two settings, hours simply aren't shown.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)
CACHE_SECONDS = 300
_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}


class TimetrackError(RuntimeError):
    """Message is safe to show."""


def configured() -> bool:
    return bool(settings.timetrack_url and settings.timetrack_token)


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _today() -> date:
    return datetime.now(_tz()).date()


def _utc(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=_tz()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_tz()).date().isoformat()
    except (TypeError, ValueError):
        return ""


async def fetch(start: date, end: date) -> list[dict]:
    """Finished sessions that started from `start` up to and including `end` (local days)."""
    if not configured():
        return []
    key = (start.isoformat(), end.isoformat())
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.timetrack_url}/api/export/sessions",
                            params={"from": _utc(start), "to": _utc(end + timedelta(days=1))},
                            headers={"Authorization": f"Bearer {settings.timetrack_token}"})
        r.raise_for_status()
        raw = r.json().get("sessions", [])
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        log.warning("Time tracking export failed: %s", type(e).__name__)
        raise TimetrackError("The time-tracking app isn't answering just now.")
    out = []
    for s in raw if isinstance(raw, list) else []:
        try:
            minutes = float(s.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
        if minutes <= 0:
            continue
        out.append({"day": _local_day(str(s.get("start") or "")), "user": str(s.get("user") or "")[:60],
                    "user_name": str(s.get("user_name") or s.get("user") or "")[:80], "minutes": minutes,
                    "tag": str(s.get("tag") or "")[:80], "group": str(s.get("group") or "")[:80],
                    "note": str(s.get("note") or "")[:200]})
    _cache[key] = (time.monotonic(), out)
    if len(_cache) > 64:
        _cache.pop(next(iter(_cache)))
    return out


def _hours(minutes: float) -> float:
    return round(minutes / 60, 2)


def _keys_for(company: dict) -> set[str]:
    from .crm import name_key
    tags = [t for t in (company.get("timetrack_tag") or "").split(",") if t.strip()]
    return {name_key(t) for t in tags} if tags else {name_key(company["name"])}


def matches(session: dict, keys: set[str]) -> bool:
    from .crm import name_key
    return bool(keys & {name_key(session["tag"]), name_key(session["group"])} - {""})


def _for_employee(session: dict, emp: dict) -> bool:
    want = {x.strip().lower() for x in ((emp.get("timetrack_user") or "") or emp["name"]).split(",") if x.strip()}
    return session["user"].lower() in want or session["user_name"].lower() in want


async def company_hours(person: Person, company_id: int) -> dict:
    from . import crm
    co = crm._get_visible(person, "company", company_id)
    if not configured():
        return {"configured": False}
    today = _today()
    sessions = [s for s in await fetch(date(today.year, 1, 1), today) if matches(s, _keys_for(co))]
    month, d30 = today.strftime("%Y-%m"), (today - timedelta(days=30)).isoformat()
    people: dict[str, float] = {}
    for s in sessions:
        people[s["user_name"]] = people.get(s["user_name"], 0) + s["minutes"]
    return {"configured": True, "year": _hours(sum(s["minutes"] for s in sessions)),
            "month": _hours(sum(s["minutes"] for s in sessions if s["day"][:7] == month)),
            "last_30_days": _hours(sum(s["minutes"] for s in sessions if s["day"] >= d30)),
            "by_person": sorted(({"name": k, "hours": _hours(v)} for k, v in people.items()), key=lambda x: -x["hours"]),
            "recent": [{"day": s["day"], "who": s["user_name"], "hours": _hours(s["minutes"]), "tag": s["tag"],
                        "note": s["note"]} for s in sorted(sessions, key=lambda s: s["day"], reverse=True)[:8]],
            "matched_by": co.get("timetrack_tag") or co["name"]}


def _companies() -> list[dict]:
    return db.query("SELECT id, name, timetrack_tag FROM crm_companies WHERE archived_at IS NULL")


async def employee_hours(employee: dict) -> dict:
    if not configured():
        return {"configured": False}
    today = _today()
    week0 = today - timedelta(days=today.weekday())
    month0 = today.replace(day=1)
    sessions = [s for s in await fetch(min(week0, month0), today) if _for_employee(s, employee)]
    clients: dict[str, float] = {}
    keyed = [(c, _keys_for(c)) for c in _companies()]
    for s in sessions:
        if s["day"] < month0.isoformat():
            continue
        name = next((c["name"] for c, keys in keyed if matches(s, keys)), None) or s["tag"] or "Untagged"
        clients[name] = clients.get(name, 0) + s["minutes"]
    return {"configured": True, "matched_by": employee.get("timetrack_user") or employee["name"],
            "week": _hours(sum(s["minutes"] for s in sessions if s["day"] >= week0.isoformat())),
            "month": _hours(sum(s["minutes"] for s in sessions if s["day"] >= month0.isoformat())),
            "by_client": sorted(({"name": k, "hours": _hours(v)} for k, v in clients.items()), key=lambda x: -x["hours"])}


async def hours_by_company(person: Person, start: date, end: date) -> dict:
    """Hours per client the person can see in a period (for reports); the rest summed as other work."""
    from . import crm
    if not configured():
        return {"configured": False}
    sessions = await fetch(start, end)
    where, params = crm._scope(person, "x", "company")
    visible = db.query(f"SELECT x.id, x.name, x.timetrack_tag FROM crm_companies x WHERE x.archived_at IS NULL{where}",
                       params)
    keyed = [(c, _keys_for(c)) for c in visible]
    per: dict[int, dict] = {}
    other = 0.0
    for s in sessions:
        c = next((c for c, keys in keyed if matches(s, keys)), None)
        if c:
            per.setdefault(c["id"], {"id": c["id"], "name": c["name"], "minutes": 0.0})["minutes"] += s["minutes"]
        else:
            other += s["minutes"]
    rows = sorted(per.values(), key=lambda x: -x["minutes"])
    return {"configured": True, "clients": [{"id": r["id"], "name": r["name"], "hours": _hours(r["minutes"])} for r in rows],
            "client_hours": _hours(sum(r["minutes"] for r in rows)), "other_hours": _hours(other),
            "total_hours": _hours(sum(s["minutes"] for s in sessions))}
