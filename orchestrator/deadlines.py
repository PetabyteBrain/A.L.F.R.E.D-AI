"""Deadlines from the CRM and the boards in the agenda, and their sync to Google Calendar.

Sources (each person sees only what they may see anyway):
- "board:<id>": due dates of board items assigned to the person or, unassigned, to one of their teams
  ("all" scope: every item with a due date on the boards they can see);
- "pipeline": deals they own (all scope: every deal they can see): expected close and the next step's date;
- "billing": their open invoices (due date) and sent quotes (valid until).

In the agenda they are kind "deadline" items with a `source`, drawn differently from the person's own calendar so
it's clear they come from Alfred's records. One deadline or a whole source can be synced to the person's Google
Calendar as all-day events (`calendar_links`: deadline key -> event id, with the title and date written). Following
a source (`calendar_follows`) keeps it synced: an hourly job adds new deadlines, moves changed ones and removes the
events of deadlines that are done or gone. Google events that are such copies are flagged in the agenda.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)
KINDS = {"work": "Board item due", "deal_close": "Deal expected to close", "deal_step": "Deal next step",
         "invoice": "Invoice due", "quote": "Quote valid until"}
HORIZON_DAYS = 365


class DeadlineError(ValueError):
    """Message is safe to show."""


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _url() -> str:
    return f"{settings.public_base_url.rstrip('/')}/app" if settings.public_base_url else ""


# --- collecting ------------------------------------------------------------------------------------------

def _board_items(person: Person, start: str, end: str, scope: str) -> list[dict]:
    from . import boards
    if not person.can("boards.use"):
        return []
    out = []
    if scope == "all":
        rows = []
        for b in boards.boards_for(person):
            if b["kind"] == "board":
                rows += [c for c in boards.items(person, b["id"], include_done=False) if c["due_date"]]
    else:
        rows = [c for c in boards.mine(person, teams=True, include_deals=False) if c["due_date"]]
    for c in rows:
        if not (start <= c["due_date"] <= end):
            continue
        out.append({"key": f"work:{c['id']}", "kind": "deadline", "deadline": "work", "id": c["id"],
                    "title": f"{c['key']} {c['title']}", "start": c["due_date"], "all_day": True, "category": "work",
                    "source": f"board:{c['board_id']}", "source_label": c["board_name"], "badge": c["board_key"],
                    "overdue": c["overdue"], "who": c.get("assignee_name"), "editable": False,
                    "open": {"tab": "boards", "item": c["id"], "board": c["board_id"]}})
    return out


def _deals(person: Person, start: str, end: str, scope: str) -> list[dict]:
    from . import crm_deals
    if not person.can("crm.use"):
        return []
    out = []
    for d in crm_deals.search(person, status="open", mine=scope != "all", limit=2000)["items"]:
        who = d["company_name"] or ""
        if d["expected_close"] and start <= d["expected_close"] <= end:
            out.append({"key": f"deal_close:{d['id']}", "kind": "deadline", "deadline": "deal_close", "id": d["id"],
                        "title": f"Close: {d['title']}" + (f" ({who})" if who else ""), "start": d["expected_close"],
                        "all_day": True, "category": "work", "source": "pipeline", "source_label": "Sales pipeline",
                        "badge": "DEAL", "overdue": bool(d["overdue_close"]), "who": d["owner_name"], "editable": False,
                        "open": {"tab": "crm", "deal": d["id"]}})
        if d["next_step"] and d["next_step_at"] and start <= d["next_step_at"] <= end:
            out.append({"key": f"deal_step:{d['id']}", "kind": "deadline", "deadline": "deal_step", "id": d["id"],
                        "title": f"{d['next_step']}: {d['title']}", "start": d["next_step_at"], "all_day": True,
                        "category": "work", "source": "pipeline", "source_label": "Sales pipeline", "badge": "DEAL",
                        "overdue": bool(d["next_step_due"] and d["next_step_at"] < _today().isoformat()),
                        "who": d["owner_name"], "editable": False, "open": {"tab": "crm", "deal": d["id"]}})
    return out


def _billing(person: Person, start: str, end: str, scope: str) -> list[dict]:
    from . import invoicing
    if not person.can("billing.use"):
        return []
    out = []
    for kind, states, deadline, label in (("invoice", ("open", "overdue", "partly_paid"), "invoice", "Due"),
                                          ("quote", ("sent",), "quote", "Quote valid until")):
        for doc in invoicing.search(person, kind=kind, limit=5000)["items"]:
            if doc["state"] not in states or not doc["due_date"] or not (start <= doc["due_date"] <= end):
                continue
            if scope != "all" and person.id not in (doc["owner_id"], doc["created_by"]):
                continue
            who = doc["company_name"] or doc["recipient"].get("name") or ""
            title = (f"{label}: {doc['number']} {who} ({invoicing.fmt_chf(doc['open_amount'])} open)" if kind == "invoice"
                     else f"{label}: {doc['number']} {who}")
            out.append({"key": f"{deadline}:{doc['id']}", "kind": "deadline", "deadline": deadline, "id": doc["id"],
                        "title": title.replace("  ", " "), "start": doc["due_date"], "all_day": True, "category": "work",
                        "source": "billing", "source_label": "Invoices & quotes", "badge": "INV" if kind == "invoice" else "QUO",
                        "overdue": doc["state"] == "overdue", "who": doc.get("owner_name"), "editable": False,
                        "open": {"tab": "crm", "billing": doc["id"]}})
    return out


def items(person: Person, start: str, end: str, scope: str = "mine") -> list[dict]:
    """Deadlines between two local dates (YYYY-MM-DD), with their Google sync state."""
    out = _board_items(person, start, end, scope) + _deals(person, start, end, scope) + _billing(person, start, end, scope)
    links = {r["source_key"]: r for r in db.query("SELECT * FROM calendar_links WHERE user_id = ?", (person.id,))}
    follows = {r["source"] for r in db.query("SELECT source FROM calendar_follows WHERE user_id = ?", (person.id,))}
    for it in out:
        link = links.get(it["key"])
        it["synced"] = bool(link)
        it["sync_stale"] = bool(link and (link["title"] != it["title"] or link["date"] != it["start"]))
        it["source_followed"] = it["source"] in follows
    return out


def sources(person: Person) -> list[dict]:
    """Every source the person has deadlines in (next year), with counts and whether it's followed."""
    t = _today()
    upcoming = items(person, (t - timedelta(days=30)).isoformat(), (t + timedelta(days=HORIZON_DAYS)).isoformat())
    follows = {r["source"] for r in db.query("SELECT source FROM calendar_follows WHERE user_id = ?", (person.id,))}
    seen: dict[str, dict] = {}
    for it in upcoming:
        s = seen.setdefault(it["source"], {"source": it["source"], "label": it["source_label"], "badge": it["badge"],
                                           "count": 0, "synced": 0, "followed": it["source"] in follows})
        s["count"] += 1
        s["synced"] += int(it["synced"])
    for f in follows - set(seen):                  # followed, nothing upcoming right now
        seen[f] = {"source": f, "label": _source_label(f), "badge": "", "count": 0, "synced": 0, "followed": True}
    return sorted(seen.values(), key=lambda s: (not s["source"].startswith("board:"), s["label"].lower()))


def _source_label(source: str) -> str:
    if source == "pipeline":
        return "Sales pipeline"
    if source == "billing":
        return "Invoices & quotes"
    if source.startswith("board:"):
        row = db.query_one("SELECT name FROM boards WHERE id = ?", (int(source.split(":")[1]),))
        return row["name"] if row else "A board"
    return source


def copies(user_id: int) -> dict[str, str]:
    """Google event id -> deadline key, for events that are synced copies."""
    return {r["event_id"]: r["source_key"] for r in db.query("SELECT event_id, source_key FROM calendar_links "
                                                             "WHERE user_id = ?", (user_id,))}


# --- syncing to Google Calendar ---------------------------------------------------------------------------

def _need_calendar(person: Person) -> None:
    from . import google_calendar
    if not google_calendar.is_linked(person.id):
        raise DeadlineError("Link your Google Calendar on your Profile first.")


def _current(person: Person, keys: set[str] | None = None, source: str | None = None,
             scope: str = "all") -> dict[str, dict]:
    """The person's deadlines right now (from 30 days back to a year ahead), by key."""
    t = _today()
    found = items(person, (t - timedelta(days=30)).isoformat(), (t + timedelta(days=HORIZON_DAYS)).isoformat(), scope=scope)
    return {it["key"]: it for it in found if (keys is None or it["key"] in keys) and (source is None or it["source"] == source)}


async def _write(person: Person, it: dict) -> str:
    """Create or update the all-day event for one deadline; returns 'created' / 'updated' / 'unchanged'."""
    from . import google_calendar
    link = db.query_one("SELECT * FROM calendar_links WHERE user_id = ? AND source_key = ?", (person.id, it["key"]))
    end = (date.fromisoformat(it["start"]) + timedelta(days=1)).isoformat()
    desc = (f"{KINDS.get(it['deadline'], 'Deadline')} · {it['source_label']} (from {settings.assistant_name}; it keeps "
            f"this event up to date)" + (f"\n{_url()}" if _url() else ""))
    if link and link["title"] == it["title"] and link["date"] == it["start"]:
        return "unchanged"
    if link:
        try:
            await google_calendar.update_event(person.id, link["event_id"], summary=it["title"], start=it["start"], end=end,
                                               description=desc)
            db.execute("UPDATE calendar_links SET title = ?, date = ?, synced_at = ? WHERE user_id = ? AND source_key = ?",
                       (it["title"], it["start"], db.now_iso(), person.id, it["key"]))
            return "updated"
        except Exception as e:  # noqa: BLE001 — deleted in Google meanwhile: make a new one
            log.info("Deadline event %s gone (%s); recreating", link["event_id"], type(e).__name__)
    ev = await google_calendar.create_event(person.id, it["title"], it["start"], end, description=desc)
    db.execute("INSERT INTO calendar_links(user_id, source_key, event_id, title, date, synced_at) VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(user_id, source_key) DO UPDATE SET event_id = excluded.event_id, title = excluded.title, "
               "date = excluded.date, synced_at = excluded.synced_at",
               (person.id, it["key"], ev["id"], it["title"], it["start"], db.now_iso()))
    return "created" if not link else "updated"


async def sync(person: Person, keys: list[str]) -> dict:
    _need_calendar(person)
    current = _current(person, set(keys))
    missing = [k for k in keys if k not in current]
    if missing and len(keys) == 1:
        raise DeadlineError("That deadline isn't there any more (done, moved more than a year out, or not yours to see).")
    done = {"created": 0, "updated": 0, "unchanged": 0}
    for k in keys:
        if k in current:
            done[await _write(person, current[k])] += 1
    return done


async def unsync(person: Person, key: str) -> None:
    from . import google_calendar
    link = db.query_one("SELECT * FROM calendar_links WHERE user_id = ? AND source_key = ?", (person.id, key))
    if not link:
        return
    try:
        await google_calendar.delete_event(person.id, link["event_id"])
    except Exception:  # noqa: BLE001 — already gone in Google
        pass
    db.execute("DELETE FROM calendar_links WHERE user_id = ? AND source_key = ?", (person.id, key))


async def sync_source(person: Person, source: str, *, remove_gone: bool = True) -> dict:
    """Bring a whole source in line: new deadlines added, changed ones moved, done/gone ones removed."""
    _need_calendar(person)
    mine = _current(person, source=source, scope="mine")          # what following adds: one's own deadlines
    visible = _current(person, source=source, scope="all")       # what still exists (single syncs of others' too)
    done = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0}
    linked = {r["source_key"] for r in db.query("SELECT source_key FROM calendar_links WHERE user_id = ?", (person.id,))}
    for key, it in visible.items():
        if key in mine or key in linked:
            done[await _write(person, it)] += 1
    if remove_gone:
        prefix = {"pipeline": ("deal_close:", "deal_step:"), "billing": ("invoice:", "quote:")}.get(source, ("work:",))
        for link in db.query("SELECT * FROM calendar_links WHERE user_id = ?", (person.id,)):
            k = link["source_key"]
            if k.startswith(prefix) and k not in visible and _belongs(k, source):
                await unsync(person, k)
                done["removed"] += 1
    return done


def _belongs(key: str, source: str) -> bool:
    if not source.startswith("board:"):
        return True
    row = db.query_one("SELECT board_id FROM board_items WHERE id = ?", (int(key.split(":")[1]),))
    return not row or f"board:{row['board_id']}" == source       # a deleted item counts for any board


async def follow(person: Person, source: str, on: bool = True) -> dict:
    if not (source in ("pipeline", "billing") or (source.startswith("board:") and source.split(":")[1].isdigit())):
        raise DeadlineError("Unknown source.")
    if on:
        _need_calendar(person)
        db.execute("INSERT OR IGNORE INTO calendar_follows(user_id, source, created_at) VALUES (?,?,?)",
                   (person.id, source, db.now_iso()))
        return await sync_source(person, source)
    db.execute("DELETE FROM calendar_follows WHERE user_id = ? AND source = ?", (person.id, source))
    return {"followed": False}


async def keep_synced() -> int:
    """Hourly: every followed source of every person is brought in line (new, moved and finished deadlines)."""
    from . import google_calendar
    from .identity import registry
    n = 0
    for f in db.query("SELECT user_id, source FROM calendar_follows"):
        person = registry.get_person(f["user_id"])
        if not person or not google_calendar.is_linked(person.id):
            continue
        try:
            r = await sync_source(person, f["source"])
            n += r["created"] + r["updated"] + r["removed"]
        except Exception:  # noqa: BLE001 — one person's calendar trouble must not stop the others
            log.exception("Keeping %s synced for user %s failed", f["source"], f["user_id"])
    return n
