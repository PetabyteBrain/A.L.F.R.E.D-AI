"""Tasks with an owner, a due time and follow-ups.

"Max: send the invoice by Friday 12:00, follow up" -> at the due time Alfred asks Max
(Telegram with Done / Not yet buttons, or a phone call) whether it's done. "Not yet" or no
answer -> ask again a day later, up to MAX_FOLLOW_UPS times. Whoever asked for the task hears
when it's done, when the owner says not yet, and when there's still no answer after the last
follow-up. Follow-ups respect the owner's quiet hours; follow-up calls go through the call
scheduler (retries, quiet hours, outcome report to the requester).

Status: active -> done | cancelled. Tasks without an owner are the old shared list.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, quiet
from .config import settings
from .identity import registry
from .identity.registry import Person

log = logging.getLogger(__name__)

POLL_SECONDS = 30
FOLLOW_UP_EVERY = timedelta(hours=24)
MAX_FOLLOW_UPS = 3
FOLLOW_UP_KINDS = ("none", "telegram", "call")
_COLS = ("t.id, t.title, t.notes, t.status, t.schedule, t.next_run_at, t.owner_user_id, t.requested_by, t.due_at, "
         "t.follow_up, t.follow_up_at, t.follow_ups, t.completed_at, t.completed_by, t.result, t.created_at, "
         "o.display_name AS owner_name, r.display_name AS requester_name")
_FROM = "FROM tasks t LEFT JOIN users o ON o.id = t.owner_user_id LEFT JOIN users r ON r.id = t.requested_by"


class TaskError(ValueError):
    """Message is safe to show to the user / the model."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local(iso: str | None) -> str | None:
    return datetime.fromisoformat(iso).astimezone(_tz()).strftime("%a %d %b %H:%M") if iso else None


def parse_due(when: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(when or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise TaskError("Couldn't read the due time. Use ISO format like 2026-10-03T12:00.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    if dt > _now() + timedelta(days=366):
        raise TaskError("Due dates can be at most a year ahead.")
    return dt.astimezone(timezone.utc)


def _view(row: dict | None) -> dict | None:
    if row:
        row["due_local"] = _local(row["due_at"])
        row["follow_up_local"] = _local(row["follow_up_at"]) if row["status"] == "active" else None
    return row


def get(task_id: int) -> dict | None:
    return _view(db.query_one(f"SELECT {_COLS} {_FROM} WHERE t.id = ?", (task_id,)))


def _may_assign_others(person: Person) -> bool:
    from .tools import registry as tools
    if not tools._TOOLS:
        tools.load()
    spec = tools._TOOLS.get("place_call")  # same people who may have Alfred call someone
    return bool(spec and spec.allowed_for(person.role))


def _may_edit(task: dict, person: Person) -> bool:
    return person.is_admin or person.id in (task["owner_user_id"], task["requested_by"]) or (
        task["owner_user_id"] is None and _may_assign_others(person))


# --- create / list / change ------------------------------------------------------------------

def create(*, created_by: Person, title: str, owner_ref=None, due: str | None = None, follow_up: str = "none",
           notes: str = "", schedule: str = "") -> dict:
    title = " ".join(str(title or "").split())[:200]
    if not title:
        raise TaskError("What's the task?")
    if follow_up not in FOLLOW_UP_KINDS:
        raise TaskError("Follow-up must be none, telegram or call.")
    owner = created_by
    if owner_ref not in (None, "", "me", "myself"):
        from .scheduler import ScheduleError, resolve_person
        try:
            owner = resolve_person(owner_ref)
        except ScheduleError as e:
            raise TaskError(str(e))
        if owner.id != created_by.id and not _may_assign_others(created_by):
            raise TaskError("You can only create tasks for yourself.")
    due_at = parse_due(due) if due else None
    if follow_up != "none" and not due_at:
        raise TaskError("A follow-up needs a due time.")
    if follow_up == "telegram" and not registry.telegram_for(owner.id) and registry.phone_for(owner.id):
        follow_up = "call"                                  # no Telegram: ask by phone instead
    if follow_up == "call" and not registry.phone_for(owner.id) and registry.telegram_for(owner.id):
        follow_up = "telegram"
    if follow_up != "none" and not (registry.telegram_for(owner.id) or registry.phone_for(owner.id)):
        raise TaskError(f"{owner.display_name} has no verified Telegram or phone to follow up on.")
    tid = db.execute(
        "INSERT INTO tasks(title, schedule, source, status, owner_user_id, requested_by, due_at, notes, follow_up, "
        "follow_up_at, created_at) VALUES (?,?, 'local', 'active', ?,?,?,?,?,?,?)",
        (title, (schedule or "")[:200], owner.id, created_by.id, _iso(due_at) if due_at else None, (notes or "")[:500],
         follow_up, _iso(due_at) if due_at and follow_up != "none" else None, db.now_iso()))
    who = "themselves" if owner.id == created_by.id else owner.display_name
    db.log_activity("task", f"{created_by.display_name} added a task for {who}: {title[:80]}"
                    + (f" (due {_local(_iso(due_at))})" if due_at else ""), user_id=owner.id)
    return get(tid)


def list_for(person: Person, *, include_done: bool = False, limit: int = 100) -> list[dict]:
    """Admins see everything; others see tasks they own or asked for, plus the shared (unowned) list."""
    scope = "" if person.is_admin else " AND (t.owner_user_id = ? OR t.requested_by = ? OR t.owner_user_id IS NULL)"
    params: list = [] if person.is_admin else [person.id, person.id]
    status = "" if include_done else " AND t.status = 'active'"
    rows = db.query(f"SELECT {_COLS} {_FROM} WHERE 1=1{scope}{status} ORDER BY CASE WHEN t.status = 'active' "
                    "THEN 0 ELSE 1 END, COALESCE(t.due_at, t.next_run_at, t.created_at) LIMIT ?", (*params, limit))
    return [_view(r) for r in rows]


def _editable(task_id: int, person: Person) -> dict:
    task = get(task_id)
    if not task or not _may_edit(task, person):
        raise TaskError("No such task.")
    return task


def complete(task_id: int, person: Person, result: str = "") -> dict:
    task = _editable(task_id, person)
    if task["status"] != "active":
        raise TaskError(f"That task is already {task['status']}.")
    result = " ".join(str(result or "").split())[:500]
    if not db.execute_count("UPDATE tasks SET status = 'done', completed_at = ?, completed_by = ?, result = ?, "
                            "follow_up_at = NULL WHERE id = ? AND status = 'active'",
                            (db.now_iso(), person.id, result or None, task_id)):
        raise TaskError("That task was just closed.")
    db.log_activity("task", f"{person.display_name} completed: {task['title'][:80]}", user_id=task["owner_user_id"])
    _tell_requester(task, person, f"✓ {person.display_name.split()[0]} finished: {task['title']}"
                    + (f"\n{result}" if result else ""))
    return get(task_id)


def update(task_id: int, person: Person, *, title: str | None = None, due: str | None = None,
           owner_ref=None, follow_up: str | None = None, notes: str | None = None) -> dict:
    task = _editable(task_id, person)
    if task["status"] != "active":
        raise TaskError(f"That task is already {task['status']}.")
    sets: dict = {}
    if title is not None:
        sets["title"] = " ".join(title.split())[:200] or task["title"]
    if notes is not None:
        sets["notes"] = notes[:500]
    if owner_ref not in (None, ""):
        from .scheduler import ScheduleError, resolve_person
        try:
            owner = resolve_person(owner_ref)
        except ScheduleError as e:
            raise TaskError(str(e))
        if owner.id != person.id and not _may_assign_others(person):
            raise TaskError("You can only assign tasks to yourself.")
        sets["owner_user_id"] = owner.id
    if follow_up is not None:
        if follow_up not in FOLLOW_UP_KINDS:
            raise TaskError("Follow-up must be none, telegram or call.")
        sets["follow_up"] = follow_up
    if due is not None:
        sets["due_at"] = _iso(parse_due(due)) if due else None
    kind = sets.get("follow_up", task["follow_up"])
    due_at = sets.get("due_at", task["due_at"])
    if "due_at" in sets or "follow_up" in sets:          # a new date restarts the follow-ups
        if kind != "none" and not due_at:
            raise TaskError("A follow-up needs a due time.")
        sets["follow_up_at"] = due_at if kind != "none" else None
        sets["follow_ups"] = 0
    if sets:
        db.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), task_id))
    new = get(task_id)
    if "due_at" in sets and person.id == task["owner_user_id"] and task["requested_by"] not in (None, person.id):
        _tell_requester(task, person, f"{person.display_name.split()[0]} moved “{task['title']}” to {new['due_local']}.")
    return new


def cancel(task_id: int, person: Person) -> dict:
    task = _editable(task_id, person)
    if task["status"] != "active":
        raise TaskError(f"That task is already {task['status']}.")
    db.execute("UPDATE tasks SET status = 'cancelled', follow_up_at = NULL WHERE id = ?", (task_id,))
    db.log_activity("task", f"{person.display_name} cancelled a task: {task['title'][:80]}", user_id=task["owner_user_id"])
    return get(task_id)


def not_yet(task_id: int, person: Person) -> dict:
    """Owner's "Not yet" on a follow-up: ask again later and let the requester know."""
    task = _editable(task_id, person)
    if task["status"] != "active":
        raise TaskError(f"That task is already {task['status']}.")
    if task["follow_ups"] >= MAX_FOLLOW_UPS:  # they answered, so no "no word from them" report later
        db.execute("UPDATE tasks SET follow_up_at = NULL, follow_ups = ? WHERE id = ?", (MAX_FOLLOW_UPS + 1, task_id))
        note = "I won't ask again automatically."
    else:
        nxt = _now() + FOLLOW_UP_EVERY
        db.execute("UPDATE tasks SET follow_up_at = ? WHERE id = ?", (_iso(nxt), task_id))
        note = f"I'll check again {_local(_iso(nxt))}."
    _tell_requester(task, person, f"{person.display_name.split()[0]} says “{task['title']}” isn't done yet. {note}")
    return get(task_id)


# --- follow-ups ----------------------------------------------------------------------------------

def _tell_requester(task: dict, actor: Person | None, text: str) -> None:
    rid = task["requested_by"]
    if not rid or (actor is not None and actor.id == rid) or rid == task["owner_user_id"]:
        return
    db.log_activity("task", f"Report to {task['requester_name']}: {text[:200]}", user_id=rid)
    if settings.telegram_enabled and registry.telegram_for(rid):
        async def go():
            try:
                from .telegram_handler import send_to_user
                await send_to_user(rid, text, note="task update")
            except Exception:  # noqa: BLE001
                log.exception("Task update to requester failed")
        _spawn(go())


_running: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:            # no loop (sync caller): run it right here
        asyncio.run(coro)
        return
    _running.add(t)
    t.add_done_callback(_running.discard)


def _ask_text(task: dict) -> str:
    by = task["requester_name"].split()[0] if task["requested_by"] not in (None, task["owner_user_id"]) and \
        task["requester_name"] else None
    due = f" (due {task['due_local']})" if task["due_local"] else ""
    return (f"📋 {'Checking in for ' + by if by else 'A gentle check-in'}: is “{task['title']}” done?{due}"
            + (f"\n{task['notes']}" if task["notes"] else "") + f"\n(task #{task['id']})")


async def _follow_up(task: dict) -> None:
    owner = registry.get_person(task["owner_user_id"]) if task["owner_user_id"] else None
    if owner is None:
        db.execute("UPDATE tasks SET follow_up_at = NULL WHERE id = ?", (task["id"],))
        return
    # Someone else's task for this person waits for their quiet hours; their own follow-up doesn't.
    if task["requested_by"] not in (None, owner.id):
        until = quiet.quiet_until(owner.id)
        if until:
            db.execute("UPDATE tasks SET follow_up_at = ? WHERE id = ?", (_iso(until), task["id"]))
            return
    count = task["follow_ups"] + 1
    last = count >= MAX_FOLLOW_UPS
    # Claim: move the next check a day ahead (or clear it after the last one) before contacting anyone.
    if not db.execute_count("UPDATE tasks SET follow_ups = ?, follow_up_at = ?, last_follow_up_at = ? WHERE id = ? "
                            "AND status = 'active' AND follow_up_at = ?",
                            (count, None if last else _iso(_now() + FOLLOW_UP_EVERY), _iso(_now()),
                             task["id"], task["follow_up_at"])):
        return
    try:
        if task["follow_up"] == "call" and registry.phone_for(owner.id):
            from . import scheduler
            requester = registry.get_person(task["requested_by"]) if task["requested_by"] else None
            await scheduler.request_call(
                owner.id, requested_by=requester or owner,
                purpose=(f"Follow up on task #{task['id']}: “{task['title']}”"
                         + (f" (due {task['due_local']})" if task["due_local"] else "")
                         + f". Ask whether it's done. If it is, call complete_task with task_id {task['id']} and any "
                           f"detail they give as result. If not, ask when it will be, and call update_task with "
                           f"task_id {task['id']} and the new due time."))
        elif settings.telegram_enabled and registry.telegram_for(owner.id):
            from .telegram_handler import send_to_user
            await send_to_user(owner.id, _ask_text(task), note="task follow-up",
                               buttons=[[("Done ✓", f"task:done:{task['id']}"), ("Not yet", f"task:notyet:{task['id']}")]],
                               silent=False if task["requested_by"] in (None, owner.id) else None)
        else:
            db.execute("UPDATE tasks SET follow_up_at = NULL WHERE id = ?", (task["id"],))
            _tell_requester(task, None, f"I can't reach {owner.display_name} to follow up on “{task['title']}”.")
            return
    except Exception:  # noqa: BLE001
        log.exception("Task follow-up %s failed", task["id"])
        return
    db.log_activity("task", f"Followed up with {owner.display_name} on “{task['title'][:60]}” "
                    f"({count}/{MAX_FOLLOW_UPS}, {task['follow_up']})", user_id=owner.id)


async def _give_up(task: dict) -> None:
    """A day after the last follow-up with still no answer: tell the requester, once."""
    db.execute("UPDATE tasks SET follow_ups = follow_ups + 1 WHERE id = ?", (task["id"],))
    _tell_requester(task, None, f"No word from {task['owner_name']} on “{task['title']}” after "
                                f"{MAX_FOLLOW_UPS} follow-ups. I've stopped asking.")


async def tick() -> None:
    now = _iso(_now())
    for r in db.query("SELECT id FROM tasks WHERE status = 'active' AND follow_up != 'none' AND follow_up_at IS NOT NULL "
                      "AND follow_up_at <= ? ORDER BY follow_up_at", (now,)):
        await _follow_up(get(r["id"]))
    # Last follow-up sent more than a day ago and still open with no answer: report once.
    for r in db.query("SELECT id FROM tasks WHERE status = 'active' AND follow_up != 'none' AND follow_up_at IS NULL "
                      "AND follow_ups = ? AND last_follow_up_at <= ?", (MAX_FOLLOW_UPS, _iso(_now() - FOLLOW_UP_EVERY))):
        await _give_up(get(r["id"]))


async def background_loop() -> None:
    from . import meetings
    while True:
        try:
            await tick()
            await meetings.expire_stale()
        except Exception:
            log.exception("Task loop iteration failed")
        await asyncio.sleep(POLL_SECONDS)
