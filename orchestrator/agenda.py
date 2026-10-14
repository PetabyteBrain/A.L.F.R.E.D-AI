"""One agenda: Google Calendar events, reminders, tasks and scheduled calls together, with
categories, and reminders linked to calendar events so the two can't drift apart.

Linking: a one-off reminder can belong to a calendar event ("15 minutes before"):
`reminders.event_id` + `event_offset_min`. `sync_person` re-reads those events: moved -> the
reminder moves with it (link_state 'linked', note "moved with the event"); deleted or cancelled ->
link_state 'event_deleted' ("not needed") and it is never sent. Right before delivery the link is
checked once more (`check_before_delivery`).
Duplicates: an unlinked one-off reminder within 30 minutes of an event with a similar title is
marked link_state 'duplicate' (event_id = the matching event) so the UI can offer "link" or
"not needed".
Categories: meeting, reminder, task, call, work, personal, other. Reminders and tasks store their
own; calendar events get one in `event_categories` (default: meeting if it has attendees, else other).
"""
from __future__ import annotations

import difflib
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)

CATEGORIES = ("meeting", "reminder", "task", "call", "work", "personal", "other")
DUP_WINDOW = timedelta(minutes=30)
MAX_OCCURRENCES = 200


class AgendaError(ValueError):
    """Message is safe to show to the user / the model."""


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def check_category(category: str) -> str:
    c = str(category or "").strip().lower()
    if c not in CATEGORIES:
        raise AgendaError("Category must be one of: " + ", ".join(CATEGORIES) + ".")
    return c


def event_category(user_id: int, event: dict) -> str:
    row = db.query_one("SELECT category FROM event_categories WHERE user_id = ? AND event_id = ?", (user_id, event["id"]))
    if row:
        return row["category"]
    return "meeting" if event.get("attendees") else "other"


# --- linking ---------------------------------------------------------------------------------

async def _event(user_id: int, event_id: str) -> dict | None:
    """The event, or None if it was deleted/cancelled."""
    from . import google_calendar
    try:
        data = await google_calendar._api(user_id, "GET", f"/events/{google_calendar._event_id(event_id)}", meter=False)
    except google_calendar.GoogleError as e:
        if "wasn't found" in str(e):
            return None
        raise
    if data.get("status") == "cancelled":
        return None
    return google_calendar._compact(data)


async def link(person: Person, reminder_id: int, event_id: str, minutes_before: int = 15) -> dict:
    from . import google_calendar, reminders
    row = reminders._owned(reminder_id, person)
    if row["recurrence"]:
        raise AgendaError("Repeating reminders can't be tied to one calendar event.")
    if not google_calendar.is_linked(row["user_id"]):
        raise AgendaError("That person has no Google Calendar linked.")
    minutes_before = max(0, min(int(minutes_before), 7 * 24 * 60))
    ev = await _event(row["user_id"], event_id)
    if ev is None:
        raise AgendaError("That calendar event doesn't exist (any more).")
    due = _utc(ev["start"]) - timedelta(minutes=minutes_before)
    db.execute("UPDATE reminders SET event_id = ?, event_offset_min = ?, due_at = ?, link_state = 'linked', "
               "link_note = ?, status = CASE WHEN status = 'sent' THEN status ELSE 'pending' END WHERE id = ?",
               (ev["id"], minutes_before, _iso(due), ev["summary"][:200], reminder_id))
    db.log_activity("task", f"{person.display_name} tied reminder #{reminder_id} to the event '{ev['summary'][:60]}'",
                    user_id=row["user_id"])
    return reminders.get(reminder_id)


def unlink(person: Person, reminder_id: int) -> dict:
    from . import reminders
    reminders._owned(reminder_id, person)
    db.execute("UPDATE reminders SET event_id = NULL, event_offset_min = NULL, link_state = 'none', link_note = NULL "
               "WHERE id = ?", (reminder_id,))
    return reminders.get(reminder_id)


async def remind_for_event(person: Person, event_id: str, minutes_before: int = 15, channel: str = "telegram") -> dict:
    """New reminder tied to an event (from the calendar view or the model)."""
    from . import reminders
    ev = await _event(person.id, event_id)
    if ev is None:
        raise AgendaError("That calendar event doesn't exist (any more).")
    due = _utc(ev["start"]) - timedelta(minutes=max(0, int(minutes_before)))
    if due <= datetime.now(timezone.utc):
        raise AgendaError("That would be in the past.")
    r = reminders.create(created_by=person, text=ev["summary"], when=due.astimezone(_tz()).isoformat(timespec="minutes"),
                         channel=channel)
    db.execute("UPDATE reminders SET category = ? WHERE id = ?", (event_category(person.id, ev), r["id"]))
    return await link(person, r["id"], ev["id"], minutes_before)


async def sync_person(user_id: int) -> dict:
    """Re-check linked reminders against the calendar; mark likely duplicates. Returns counts."""
    from . import google_calendar
    if not google_calendar.is_linked(user_id):
        return {}
    out = {"moved": 0, "deleted": 0, "duplicates": 0}
    for r in db.query("SELECT id, event_id, event_offset_min, due_at FROM reminders WHERE user_id = ? AND event_id "
                      "IS NOT NULL AND link_state IN ('linked', 'event_moved') AND status = 'pending'", (user_id,)):
        try:
            ev = await _event(user_id, r["event_id"])
        except Exception:  # noqa: BLE001 — Google unreachable: try again next time
            log.warning("Agenda sync: couldn't read event for reminder %s", r["id"], exc_info=True)
            continue
        if ev is None:
            db.execute("UPDATE reminders SET link_state = 'event_deleted', link_note = 'The calendar event was "
                       "deleted, so this reminder is not needed' WHERE id = ?", (r["id"],))
            out["deleted"] += 1
            continue
        due = _utc(ev["start"]) - timedelta(minutes=r["event_offset_min"] or 0)
        if _iso(due) != r["due_at"]:
            db.execute("UPDATE reminders SET due_at = ?, link_state = 'linked', link_note = ? WHERE id = ?",
                       (_iso(due), f"Moved with the event '{ev['summary'][:80]}'", r["id"]))
            db.log_activity("task", f"Reminder #{r['id']} moved with its calendar event", user_id=user_id)
            out["moved"] += 1
    # Likely duplicates of calendar events
    loose = db.query("SELECT id, text, due_at FROM reminders WHERE user_id = ? AND status = 'pending' AND "
                     "recurrence IS NULL AND (link_state IS NULL OR link_state IN ('none', 'duplicate'))", (user_id,))
    if loose:
        times = [_utc(r["due_at"]) for r in loose]
        try:
            events = await google_calendar.list_events(user_id, (min(times) - DUP_WINDOW).isoformat(),
                                                       (max(times) + DUP_WINDOW).isoformat(), max_results=100)
        except Exception:  # noqa: BLE001
            events = []
        for r, t in zip(loose, times):
            match = None
            for ev in events:
                if ev["all_day"]:
                    continue
                close = abs(_utc(ev["start"]) - t) <= DUP_WINDOW
                similar = difflib.SequenceMatcher(None, r["text"].lower(), ev["summary"].lower()).ratio() >= 0.5
                if close and similar:
                    match = ev
                    break
            if match:
                db.execute("UPDATE reminders SET link_state = 'duplicate', event_id = ?, link_note = ? WHERE id = ?",
                           (match["id"], f"Looks like the calendar event '{match['summary'][:80]}'", r["id"]))
                out["duplicates"] += 1
            else:
                db.execute("UPDATE reminders SET link_state = 'none', event_id = NULL, link_note = NULL "
                           "WHERE id = ? AND link_state = 'duplicate'", (r["id"],))
    return out


async def sync_all() -> None:
    """From the health loop: everyone with a calendar and open reminders."""
    for row in db.query("SELECT DISTINCT r.user_id FROM reminders r JOIN google_accounts g ON g.user_id = r.user_id "
                        "WHERE r.status = 'pending'"):
        try:
            await sync_person(row["user_id"])
        except Exception:  # noqa: BLE001
            log.warning("Agenda sync failed for user %s", row["user_id"], exc_info=True)


async def check_before_delivery(row: dict) -> str:
    """'send', or 'skip' if the reminder's event is gone (marked not needed). Moves it if the event moved."""
    if not row.get("event_id") or row.get("link_state") not in ("linked", "event_moved"):
        return "skip" if row.get("link_state") == "event_deleted" else "send"
    try:
        ev = await _event(row["user_id"], row["event_id"])
    except Exception:  # noqa: BLE001 — can't check: deliver as planned rather than miss it
        return "send"
    if ev is None:
        db.execute("UPDATE reminders SET link_state = 'event_deleted', link_note = 'The calendar event was deleted, "
                   "so this reminder is not needed' WHERE id = ?", (row["id"],))
        return "skip"
    due = _utc(ev["start"]) - timedelta(minutes=row.get("event_offset_min") or 0)
    if due - datetime.now(timezone.utc) > timedelta(minutes=2):      # the event moved later
        db.execute("UPDATE reminders SET due_at = ?, link_note = ? WHERE id = ?",
                   (_iso(due), f"Moved with the event '{ev['summary'][:80]}'", row["id"]))
        return "later"
    return "send"


async def on_event_changed(user_id: int, event_id: str) -> None:
    """After Alfred changed or deleted an event: bring its reminders in line right away."""
    if db.query_one("SELECT 1 FROM reminders WHERE user_id = ? AND event_id = ? AND status = 'pending'",
                    (user_id, event_id)):
        await sync_person(user_id)


# --- the agenda --------------------------------------------------------------------------------

def _expand(r: dict, start: datetime, end: datetime) -> list[datetime]:
    from .reminders import next_occurrence
    t = _utc(r["due_at"])
    if not r["recurrence"]:
        return [t] if start <= t <= end else []
    out = []
    while t <= end and len(out) < MAX_OCCURRENCES:
        if t >= start:
            out.append(t)
        t = next_occurrence(t, r["recurrence"])
    return out


async def items(person: Person, start: str, end: str) -> dict:
    s, e = _utc(start), _utc(end)
    if e <= s or e - s > timedelta(days=62):
        raise AgendaError("Pick a range of up to two months.")
    out: list[dict] = []
    calendar = {"linked": False}
    from . import google_calendar
    if google_calendar.is_linked(person.id):
        calendar["linked"] = True
        try:
            for ev in await google_calendar.list_events(person.id, s.isoformat(), e.isoformat(), max_results=100):
                out.append({"key": f"event:{ev['id']}", "kind": "event", "id": ev["id"], "title": ev["summary"],
                            "start": ev["start"], "end": ev["end"], "all_day": ev["all_day"],
                            "category": event_category(person.id, ev), "location": ev["location"],
                            "attendees": len(ev["attendees"]), "editable": True})
        except Exception as ex:  # noqa: BLE001
            calendar["error"] = str(ex)[:200]
    for r in db.query("SELECT * FROM reminders WHERE user_id = ? AND status IN ('pending', 'sent')", (person.id,)):
        for t in _expand(r, s, e):
            out.append({"key": f"reminder:{r['id']}:{t.isoformat()}", "kind": "reminder", "id": r["id"],
                        "title": r["text"], "start": t.isoformat(), "all_day": False, "category": r["category"],
                        "recurrence": r["recurrence"], "channel": r["channel"], "link_state": r["link_state"],
                        "linked_event": r["event_id"] if r["link_state"] == "linked" else None,
                        "duplicate_of": r["event_id"] if r["link_state"] == "duplicate" else None,
                        "link_note": r["link_note"], "status": r["status"], "editable": True})
    for t in db.query("SELECT id, title, due_at, category, status FROM tasks WHERE owner_user_id = ? AND status = 'active' "
                      "AND due_at IS NOT NULL AND due_at BETWEEN ? AND ?", (person.id, _iso(s), _iso(e))):
        out.append({"key": f"task:{t['id']}", "kind": "task", "id": t["id"], "title": t["title"], "start": t["due_at"],
                    "all_day": False, "category": t["category"], "editable": True})
    for c in db.query("SELECT c.id, c.run_at, c.purpose, u.display_name AS target FROM scheduled_calls c JOIN users u "
                      "ON u.id = c.target_user_id WHERE c.status = 'pending' AND (c.requested_by = ? OR c.target_user_id = ?) "
                      "AND c.run_at BETWEEN ? AND ?", (person.id, person.id, _iso(s), _iso(e))):
        out.append({"key": f"call:{c['id']}", "kind": "call", "id": c["id"], "title": f"Call {c['target']}"
                    + (f": {c['purpose'][:60]}" if c["purpose"] else ""), "start": c["run_at"], "all_day": False,
                    "category": "call", "editable": False})
    if person.can("boards.use"):                 # due dates of the person's board items (#59)
        from . import boards
        out += boards.agenda_items(person, s.astimezone(_tz()).date().isoformat(), e.astimezone(_tz()).date().isoformat())
    out.sort(key=lambda i: (i["start"][:10], not i["all_day"], i["start"]))
    return {"items": out, "calendar": calendar, "categories": list(CATEGORIES)}


async def update(person: Person, kind: str, item_id, *, title: str | None = None, start: str | None = None,
                 category: str | None = None) -> dict:
    """Change an agenda item's title, time and/or category (from the calendar or the list)."""
    from . import google_calendar, reminders, tasks
    if category is not None:
        category = check_category(category)
    if kind == "event":
        if category:
            _set_event_category(person.id, str(item_id), category)
        if title or start:
            fields = {"summary": title}
            if start:
                ev = await _event(person.id, str(item_id))
                if ev is None:
                    raise AgendaError("That event doesn't exist any more.")
                if ev["all_day"]:
                    raise AgendaError("All-day events can't be moved here; change them in Google Calendar.")
                length = _utc(ev["end"]) - _utc(ev["start"])
                ns = _utc(start)
                fields.update(start=ns.astimezone(_tz()).isoformat(timespec="minutes"),
                              end=(ns + length).astimezone(_tz()).isoformat(timespec="minutes"))
            try:
                await google_calendar.update_event(person.id, str(item_id), **fields)
            except google_calendar.GoogleError as e:
                raise AgendaError(str(e))
            await on_event_changed(person.id, str(item_id))
        return {"ok": True}
    if kind == "reminder":
        row = reminders._owned(int(item_id), person)
        sets, params = [], []
        if title:
            sets.append("text = ?")
            params.append(" ".join(title.split())[:500])
        if category:
            sets.append("category = ?")
            params.append(category)
        if start:
            sets += ["due_at = ?", "status = 'pending'"]
            params.append(_iso(_utc(start)))
            if row["event_id"] and row["link_state"] == "linked":       # moved by hand: no longer follows the event
                sets += ["link_state = 'none'", "event_id = NULL", "event_offset_min = NULL", "link_note = NULL"]
        if sets:
            db.execute(f"UPDATE reminders SET {', '.join(sets)} WHERE id = ?", (*params, int(item_id)))
        return reminders.get(int(item_id))
    if kind == "task":
        try:
            if title or start:
                tasks.update(int(item_id), person, title=title, due=start)
        except tasks.TaskError as e:
            raise AgendaError(str(e))
        if category:
            tasks._editable(int(item_id), person)
            db.execute("UPDATE tasks SET category = ? WHERE id = ?", (category, int(item_id)))
        return tasks.get(int(item_id))
    raise AgendaError("That item can't be changed here.")


def _set_event_category(user_id: int, event_id: str, category: str) -> None:
    db.execute("INSERT INTO event_categories(user_id, event_id, category) VALUES (?,?,?) ON CONFLICT(user_id, "
               "event_id) DO UPDATE SET category = excluded.category", (user_id, event_id, category))


async def create(person: Person, mode: str, *, title: str, start: str, category: str = "", duration_min: int = 60,
                 channel: str = "telegram", recurrence: str | None = None, also_calendar: bool = False,
                 minutes_before: int = 15, follow_up: str = "none") -> dict:
    """New item from the calendar page. `mode` says what Alfred does with it:
    'reminder' — Alfred reminds you (optionally also as a calendar event, the reminder tied to it);
    'task' — a task due then, optionally followed up by Telegram or a call;
    'calendar' — only a Google Calendar entry; Alfred does nothing with it.
    `start` is local ISO; a bare date (YYYY-MM-DD) is an all-day calendar entry."""
    from . import google_calendar, reminders, tasks
    title = " ".join(str(title or "").split())
    if not title:
        raise AgendaError("Give it a title.")
    all_day = len(start.strip()) == 10
    if all_day and mode != "calendar":
        raise AgendaError("Reminders and tasks need a time.")
    duration_min = max(5, min(int(duration_min or 60), 24 * 60))
    cat = check_category(category) if category else None

    async def new_event(summary: str) -> dict:
        if not google_calendar.is_linked(person.id):
            raise AgendaError("Link your Google Calendar on your Profile first.")
        if all_day:
            d = datetime.strptime(start.strip(), "%Y-%m-%d")
            end = (d + timedelta(days=1)).strftime("%Y-%m-%d")
            s = start.strip()
        else:
            t = _utc(start).astimezone(_tz())
            s, end = t.isoformat(timespec="minutes"), (t + timedelta(minutes=duration_min)).isoformat(timespec="minutes")
        try:
            return await google_calendar.create_event(person.id, summary[:300], s, end)
        except google_calendar.GoogleError as e:
            raise AgendaError(str(e))

    if mode == "calendar":
        ev = await new_event(title)
        _set_event_category(person.id, ev["id"], cat or "other")
        db.log_activity("task", f"{person.display_name} added '{title[:60]}' to their calendar", user_id=person.id)
        return {"kind": "event", "id": ev["id"]}
    if mode == "reminder":
        if also_calendar:
            if recurrence not in (None, "", "none"):
                raise AgendaError("A repeating reminder can't be tied to one calendar event; turn off the calendar entry.")
            minutes_before = max(0, min(int(minutes_before), 7 * 24 * 60))
            if _utc(start) - timedelta(minutes=minutes_before) <= datetime.now(timezone.utc):
                raise AgendaError("That reminder would be in the past.")
            ev = await new_event(title)
            _set_event_category(person.id, ev["id"], cat or "reminder")
            r = await remind_for_event(person, ev["id"], minutes_before, channel)
        else:
            try:
                r = reminders.create(created_by=person, text=title, when=start, recurrence=recurrence or None,
                                     channel=channel, category=cat or "reminder")
            except reminders.ReminderError as e:
                raise AgendaError(str(e))
        if cat:
            db.execute("UPDATE reminders SET category = ? WHERE id = ?", (cat, r["id"]))
        return {"kind": "reminder", "id": r["id"]}
    if mode == "task":
        try:
            t = tasks.create(created_by=person, title=title, due=start, follow_up=follow_up or "none")
        except tasks.TaskError as e:
            raise AgendaError(str(e))
        db.execute("UPDATE tasks SET category = ? WHERE id = ?", (cat or "task", t["id"]))
        return {"kind": "task", "id": t["id"]}
    raise AgendaError("Choose reminder, task or calendar only.")


def dismiss(person: Person, reminder_id: int) -> dict:
    """'Not needed': cancel a reminder whose event is gone or that duplicates an event."""
    from . import reminders
    row = reminders._owned(reminder_id, person)
    if row["status"] not in ("pending", "sent"):
        raise AgendaError(f"That reminder is already {row['status']}.")
    db.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
    db.log_activity("task", f"{person.display_name} dismissed reminder #{reminder_id} as not needed", user_id=row["user_id"])
    return reminders.get(reminder_id)
