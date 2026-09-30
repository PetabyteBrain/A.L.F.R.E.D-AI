"""Reminders & nudges: "remind me at 17:00 to …", by Telegram or by phone, optionally recurring.

Lifecycle of a row in `reminders` (one row per occurrence):
  pending --(due)--> sent (Telegram, waiting for "Done") --(Done)--> done
                 |                                       \\-(Snooze)--> pending (+N min)
                 |                                       \\-(escalate, no reaction)--> call --> done
                 \\-> done (delivered by phone) | failed | cancelled
A recurring reminder schedules its next occurrence as soon as the current one is delivered;
cancelling the pending occurrence ends the series.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity import registry
from .identity.registry import Person

log = logging.getLogger(__name__)

POLL_SECONDS = 20
ESCALATE_AFTER = timedelta(minutes=10)
AUTO_CLOSE_AFTER = timedelta(hours=24)       # an unanswered Telegram reminder stops waiting
RECURRENCES = ("daily", "weekdays", "weekly", "monthly")
CHANNELS = ("telegram", "call")
MAX_DAYS_AHEAD = 366
MAX_OPEN_PER_PERSON = 100


class ReminderError(ValueError):
    """Message is safe to show to the user / the model."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _local(dt: datetime) -> str:
    return dt.astimezone(_tz()).strftime("%a %d %b %Y %H:%M")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_due(when: str) -> datetime:
    """ISO 8601 (naive = local timezone) -> aware UTC, in the future and within a year."""
    try:
        dt = datetime.fromisoformat(str(when or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise ReminderError("Couldn't read the time. Use ISO format like 2026-10-01T17:00.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    dt = dt.astimezone(timezone.utc)
    if dt < _now() - timedelta(minutes=2):
        raise ReminderError(f"That time ({_local(dt)}) is in the past.")
    if dt > _now() + timedelta(days=MAX_DAYS_AHEAD):
        raise ReminderError("Reminders can be set at most a year ahead.")
    return dt


def next_occurrence(due: datetime, recurrence: str) -> datetime:
    """Next time for a recurring reminder, keeping the local wall-clock time across DST."""
    local = due.astimezone(_tz())
    if recurrence == "daily":
        nxt = local + timedelta(days=1)
    elif recurrence == "weekdays":
        nxt = local + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
    elif recurrence == "weekly":
        nxt = local + timedelta(days=7)
    elif recurrence == "monthly":
        y, m = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        nxt = local.replace(year=y, month=m, day=min(local.day, calendar.monthrange(y, m)[1]))
    else:
        raise ReminderError(f"Recurrence must be one of: {', '.join(RECURRENCES)}.")
    # Re-attach the zone to the naive wall-clock time so 08:00 stays 08:00 after a DST change.
    return nxt.replace(tzinfo=None).replace(tzinfo=_tz()).astimezone(timezone.utc)


# --- create / list / change ------------------------------------------------------------------

def _may_remind_others(person: Person) -> bool:
    from .tools import registry as tools
    if not tools._TOOLS:
        tools.load()
    spec = tools._TOOLS.get("place_call")  # same people who may have Alfred call someone
    return bool(spec and spec.allowed_for(person.role))


def create(*, created_by: Person, text: str, when: str, target_ref=None, recurrence: str | None = None,
           channel: str = "telegram", escalate: bool = False) -> dict:
    text = " ".join(str(text or "").split())[:500]
    if not text:
        raise ReminderError("What should the reminder say?")
    if channel not in CHANNELS:
        raise ReminderError("Channel must be telegram or call.")
    if recurrence in ("", "none", None):
        recurrence = None
    elif recurrence not in RECURRENCES:
        raise ReminderError(f"Recurrence must be one of: {', '.join(RECURRENCES)}.")
    target = created_by
    if target_ref not in (None, "", "me", "myself"):
        from .scheduler import ScheduleError, resolve_person
        try:
            target = resolve_person(target_ref)
        except ScheduleError as e:
            raise ReminderError(str(e))
        if target.id != created_by.id and not _may_remind_others(created_by):
            raise ReminderError("You can only set reminders for yourself.")
    due = parse_due(when)
    n = db.query_one("SELECT COUNT(*) AS n FROM reminders WHERE user_id = ? AND status IN ('pending','sent')",
                     (target.id,))["n"]
    if n >= MAX_OPEN_PER_PERSON:
        raise ReminderError("That person already has too many open reminders.")
    rid = db.execute(
        "INSERT INTO reminders(user_id, created_by, text, due_at, recurrence, channel, escalate, status, created_at) "
        "VALUES (?,?,?,?,?,?,?, 'pending', ?)",
        (target.id, created_by.id, text, _iso(due), recurrence, channel, int(bool(escalate)), db.now_iso()))
    who = "themselves" if target.id == created_by.id else target.display_name
    db.log_activity("task", f"{created_by.display_name} set a reminder for {who} at {_local(due)}"
                    f"{f' ({recurrence})' if recurrence else ''}: {text[:80]}", user_id=target.id)
    return get(rid)


def get(reminder_id: int) -> dict | None:
    row = db.query_one("SELECT r.*, u.display_name AS person_name FROM reminders r JOIN users u ON u.id = r.user_id "
                       "WHERE r.id = ?", (reminder_id,))
    if row:
        row["due_local"] = _local(datetime.fromisoformat(row["due_at"]))
        row["escalate"] = bool(row["escalate"])
    return row


def list_for(person: Person, *, include_done: bool = False, limit: int = 50, everyone: bool = False) -> list[dict]:
    """Own reminders plus ones this person set for others; admins may ask for everyone's."""
    where = "1=1" if (everyone and person.is_admin) else "(r.user_id = ? OR r.created_by = ?)"
    params: list = [] if (everyone and person.is_admin) else [person.id, person.id]
    status = "" if include_done else " AND r.status IN ('pending','sent')"
    rows = db.query(
        "SELECT r.*, u.display_name AS person_name FROM reminders r JOIN users u ON u.id = r.user_id WHERE "
        + where + status + " ORDER BY CASE WHEN r.status IN ('pending','sent') THEN 0 ELSE 1 END, r.due_at LIMIT ?",
        (*params, limit))
    for r in rows:
        r["due_local"] = _local(datetime.fromisoformat(r["due_at"]))
        r["escalate"] = bool(r["escalate"])
    return rows


def _owned(reminder_id: int, person: Person) -> dict:
    row = get(reminder_id)
    if not row or not (person.is_admin or person.id in (row["user_id"], row["created_by"])):
        raise ReminderError("No such reminder.")
    return row


def cancel(reminder_id: int, person: Person) -> dict:
    row = _owned(reminder_id, person)
    if row["status"] not in ("pending", "sent"):
        raise ReminderError(f"That reminder is already {row['status']}.")
    db.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
    db.log_activity("task", f"{person.display_name} cancelled a reminder: {row['text'][:80]}", user_id=row["user_id"])
    return get(reminder_id)


def acknowledge(reminder_id: int, person: Person, snooze_minutes: int = 0) -> dict:
    """Done (snooze 0) or snooze a delivered reminder. Only its recipient (or an admin)."""
    row = get(reminder_id)
    if not row or not (person.is_admin or person.id == row["user_id"]):
        raise ReminderError("No such reminder.")
    if row["status"] not in ("sent", "pending"):
        raise ReminderError(f"That reminder is already {row['status']}.")
    if snooze_minutes:
        snooze_minutes = max(1, min(int(snooze_minutes), 24 * 60))
        due = _now() + timedelta(minutes=snooze_minutes)
        db.execute("UPDATE reminders SET status = 'pending', due_at = ?, sent_at = NULL WHERE id = ?",
                   (_iso(due), reminder_id))
    else:
        db.execute("UPDATE reminders SET status = 'done', acked_at = ? WHERE id = ?", (db.now_iso(), reminder_id))
    return get(reminder_id)


# --- delivery --------------------------------------------------------------------------------

def _message(row: dict) -> str:
    by = ""
    if row["created_by"] and row["created_by"] != row["user_id"]:
        setter = registry.get_person(row["created_by"])
        by = f" (from {setter.display_name.split()[0]})" if setter else ""
    return f"⏰ Reminder{by}: {row['text']}"


async def _via_telegram(row: dict) -> bool:
    if not (settings.telegram_enabled and registry.telegram_for(row["user_id"])):
        return False
    from .telegram_handler import send_to_user
    buttons = [[("Done ✓", f"rem:done:{row['id']}"), ("Snooze 10 min", f"rem:snooze:{row['id']}")]]
    await send_to_user(row["user_id"], _message(row) + f"\n(reminder #{row['id']})", note="reminder", buttons=buttons)
    return True


async def _via_call(row: dict, *, escalated: bool = False) -> bool:
    if not (settings.twilio_enabled and settings.elevenlabs_enabled and settings.public_base_url):
        return False
    person = registry.get_person(row["user_id"])
    if not person or not registry.phone_for(person.id) or not registry.role_allows_channel(person.role, "call"):
        return False
    from . import scheduler
    setter = registry.get_person(row["created_by"]) if row["created_by"] else None
    purpose = (f"Deliver this reminder{' (it was not acknowledged on Telegram)' if escalated else ''}: "
               f"{row['text']}. Say it clearly, check they got it, then say goodbye and end the call.")
    await scheduler.request_call(person.id, requested_by=setter or person, purpose=purpose,
                                 opening_line=f"{_greeting(person)} A reminder for you.")
    return True


def _greeting(person: Person) -> str:
    h = _now().astimezone(_tz()).hour
    part = "morning" if 5 <= h < 12 else "afternoon" if h < 18 else "evening"
    who = "sir" if person.is_admin else person.display_name.split()[0]
    return f"Good {part}, {who}. {settings.assistant_name} here."


async def deliver(reminder_id: int) -> None:
    # Claim atomically so a slow delivery can't be picked up twice by the next loop pass.
    if not db.execute_count("UPDATE reminders SET status = 'sending' WHERE id = ? AND status = 'pending'",
                            (reminder_id,)):
        return
    row = get(reminder_id)
    order = ("telegram", "call") if row["channel"] == "telegram" else ("call", "telegram")
    used, error = None, ""
    for ch in order:
        # Fall back to the other channel only if this one isn't set up for the person; an error on
        # the way (e.g. Telegram hiccup) must not turn a quiet message into a ringing phone.
        try:
            if await (_via_telegram(row) if ch == "telegram" else _via_call(row)):
                used = ch
                break
        except Exception as e:  # noqa: BLE001
            log.exception("Reminder %s via %s failed", reminder_id, ch)
            error = f"{ch}: {str(e)[:280]}"
            break
    if used is None:
        db.execute("UPDATE reminders SET status = 'failed', error = ? WHERE id = ?",
                   (error or "No verified Telegram or phone to reach them.", reminder_id))
        db.log_activity("error", f"Reminder #{reminder_id} couldn't be delivered: {error or 'no channel'}",
                        user_id=row["user_id"])
    else:
        # Telegram waits for Done/Snooze; a phone call counts as delivered.
        db.execute("UPDATE reminders SET status = ?, sent_at = ? WHERE id = ?",
                   ("sent" if used == "telegram" else "done", db.now_iso(), reminder_id))
        db.log_activity("task", f"Reminder #{reminder_id} delivered by {used}", user_id=row["user_id"])
    if row["recurrence"]:
        nxt = next_occurrence(datetime.fromisoformat(row["due_at"]), row["recurrence"])
        while nxt <= _now():  # after downtime, skip missed occurrences instead of firing them all
            nxt = next_occurrence(nxt, row["recurrence"])
        db.execute(
            "INSERT INTO reminders(user_id, created_by, text, due_at, recurrence, channel, escalate, status, created_at) "
            "VALUES (?,?,?,?,?,?,?, 'pending', ?)",
            (row["user_id"], row["created_by"], row["text"], _iso(nxt), row["recurrence"], row["channel"],
             int(row["escalate"]), db.now_iso()))


async def _escalate(row: dict) -> None:
    if not db.execute_count("UPDATE reminders SET escalated_at = ? WHERE id = ? AND status = 'sent' "
                            "AND escalated_at IS NULL", (db.now_iso(), row["id"])):
        return
    try:
        ok = await _via_call(row, escalated=True)
    except Exception:  # noqa: BLE001
        log.exception("Reminder %s escalation failed", row["id"])
        ok = False
    if ok:
        db.execute("UPDATE reminders SET status = 'done' WHERE id = ?", (row["id"],))
        db.log_activity("task", f"Reminder #{row['id']} not acknowledged, so {settings.assistant_name} phoned",
                        user_id=row["user_id"])


async def tick() -> None:
    now = _now()
    for r in db.query("SELECT id FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at",
                      (_iso(now),)):
        await deliver(r["id"])
    for r in db.query("SELECT id FROM reminders WHERE status = 'sent' AND escalate = 1 AND escalated_at IS NULL "
                      "AND sent_at <= ?", (_iso(now - ESCALATE_AFTER),)):
        await _escalate(get(r["id"]))
    db.execute("UPDATE reminders SET status = 'done' WHERE status = 'sent' AND sent_at <= ?",
               (_iso(now - AUTO_CLOSE_AFTER),))
    # A delivery interrupted by a restart is retried.
    db.execute("UPDATE reminders SET status = 'pending' WHERE status = 'sending' AND due_at <= ?",
               (_iso(now - timedelta(minutes=5)),))


async def background_loop() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("Reminder loop iteration failed")
        await asyncio.sleep(POLL_SECONDS)
