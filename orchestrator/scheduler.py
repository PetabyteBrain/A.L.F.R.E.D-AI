"""Call requests: "call Max now / at 18:00 and ask him X", with the outcome reported back.

Lifecycle of a row in `scheduled_calls`:
  pending --(due)--> dialing --(call ends)--> done | no_answer | failed
                 \\-> cancelled (by requester or admin)
No-answer / busy is retried (up to MAX_ATTEMPTS, RETRY_MINUTES apart) before giving up.
When a call with a purpose finishes, Claude summarises the transcript and the requester gets
the answer on Telegram (and it's stored on the row for the dashboard).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, sessions, usage
from .config import settings
from .identity import registry
from .identity.registry import Person

log = logging.getLogger(__name__)

POLL_SECONDS = 20
MAX_ATTEMPTS = 3
RETRY_MINUTES = 10
MAX_DAYS_AHEAD = 60


class ScheduleError(ValueError):
    """Message is safe to show to the user / the model."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo(settings.timezone)).strftime("%a %d %b %Y %H:%M")


def parse_when(when: str | None) -> datetime:
    """ISO 8601 (with or without offset; naive = local timezone) -> aware UTC. Empty = now."""
    if not when or when.strip().lower() in ("now", "immediately"):
        return _now()
    try:
        dt = datetime.fromisoformat(when.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError("Couldn't read the time. Use ISO format like 2026-09-30T18:30.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
    dt = dt.astimezone(timezone.utc)
    if dt < _now() - timedelta(minutes=2):
        raise ScheduleError(f"That time ({_local(dt)}) is in the past.")
    if dt > _now() + timedelta(days=MAX_DAYS_AHEAD):
        raise ScheduleError(f"Calls can be scheduled at most {MAX_DAYS_AHEAD} days ahead.")
    return dt


def resolve_person(ref) -> Person:
    """A registered person by id or (unique, case-insensitive) name / first name."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        p = registry.get_person(int(ref))
        if p:
            return p
        raise ScheduleError(f"No active person with id {ref}.")
    name = str(ref or "").strip().lower()
    if not name:
        raise ScheduleError("Say who to call.")
    people = db.query("SELECT id, display_name FROM users WHERE active = 1")
    exact = [p for p in people if p["display_name"].lower() == name]
    first = [p for p in people if p["display_name"].lower().split()[0] == name]
    partial = [p for p in people if name in p["display_name"].lower()]
    for group in (exact, first, partial):
        if len(group) == 1:
            return registry.get_person(group[0]["id"])
        if len(group) > 1:
            raise ScheduleError("More than one person matches: " + ", ".join(p["display_name"] for p in group))
    raise ScheduleError(f"Nobody called '{ref}' is registered.")


def _check_callable(target: Person) -> None:
    if not registry.phone_for(target.id):
        raise ScheduleError(f"{target.display_name} has no verified phone number in the registry.")
    if not registry.role_allows_channel(target.role, "call"):
        raise ScheduleError(f"{target.display_name}'s role ({target.role}) has no phone access.")


def _part_of_day(dt: datetime) -> str:
    h = dt.astimezone(ZoneInfo(settings.timezone)).hour
    return "morning" if 5 <= h < 12 else "afternoon" if h < 18 else "evening"


def _opening_line(target: Person, requester: Person | None, purpose: str) -> str:
    part = _part_of_day(_now())
    who = "sir" if target.is_admin else target.display_name.split()[0]
    name = settings.assistant_name
    if requester is None or requester.id == target.id:
        return (f"Good {part}, {who}. {name} here, with the call you asked for."
                if purpose else f"Good {part}, {who}. {name} here.")
    return f"Good {part}, {who}. {name} here, calling on behalf of {requester.display_name.split()[0]}."


def _call_brief(target: Person, requester: Person | None, purpose: str) -> str:
    name = settings.assistant_name
    if requester is None or requester.id == target.id:
        return (f"This is an outbound call {target.display_name} scheduled for themselves. "
                f"Purpose: {purpose or 'none given, just check in'}. Deliver it, help with any follow-up, "
                f"then confirm the outcome in one sentence and say goodbye in that same reply, while "
                f"calling the end_call tool.")
    return (
        f"This is an outbound call you are making on behalf of {requester.display_name} "
        f"(their {requester.role}), to {target.display_name}. Purpose: {purpose or 'a quick check-in'}.\n"
        f"Deliver the message or ask the question clearly and naturally, one thing at a time. Get a clear "
        f"answer; if they need time, ask when they'll get back to {requester.display_name.split()[0]}. "
        f"Once you have the answer, confirm it back in one sentence and say goodbye in that same reply, "
        f"while calling the end_call tool. Stay on this topic "
        f"and don't share anything else about {requester.display_name}. If asked who you are: {name}, "
        f"{requester.display_name.split()[0]}'s assistant."
    )


# --- create / list / cancel ------------------------------------------------------------------

async def request_call(target_ref, *, requested_by: Person | None, purpose: str = "", when: str | None = None,
                       opening_line: str = "") -> dict:
    """Create a call request; dials right away if it's due now. Returns the row."""
    if not (settings.twilio_enabled and settings.elevenlabs_enabled and settings.public_base_url):
        raise ScheduleError("Telephony isn't fully configured.")
    target = resolve_person(target_ref)
    _check_callable(target)
    run_at = parse_when(when)
    purpose = (purpose or "").strip()[:600]
    now = db.now_iso()
    rid = db.execute(
        "INSERT INTO scheduled_calls(target_user_id, requested_by, run_at, purpose, opening_line, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?, 'pending', ?, ?)",
        (target.id, requested_by.id if requested_by else None, run_at.isoformat(timespec="seconds"),
         purpose, (opening_line or "").strip()[:300], now, now),
    )
    who = requested_by.display_name if requested_by else "system"
    if run_at <= _now() + timedelta(seconds=30):
        db.log_activity("call", f"{who} asked to call {target.display_name} now", user_id=target.id)
        await dispatch(rid)
    else:
        db.log_activity("call", f"{who} scheduled a call to {target.display_name} for {_local(run_at)}",
                        user_id=target.id)
    return get(rid)


def get(request_id: int) -> dict:
    row = db.query_one(
        "SELECT c.*, t.display_name AS target_name, r.display_name AS requester_name FROM scheduled_calls c "
        "JOIN users t ON t.id = c.target_user_id LEFT JOIN users r ON r.id = c.requested_by WHERE c.id = ?",
        (request_id,))
    if row:
        row["run_at_local"] = _local(datetime.fromisoformat(row["run_at"]))
    return row


def list_for(person: Person, *, include_done: bool = True, limit: int = 50) -> list[dict]:
    """Admins see all; everyone else sees calls they requested or that target them."""
    where = "" if person.is_admin else " AND (c.requested_by = ? OR c.target_user_id = ?)"
    params: list = [] if person.is_admin else [person.id, person.id]
    status = "" if include_done else " AND c.status IN ('pending', 'dialing')"
    rows = db.query(
        "SELECT c.*, t.display_name AS target_name, r.display_name AS requester_name FROM scheduled_calls c "
        "JOIN users t ON t.id = c.target_user_id LEFT JOIN users r ON r.id = c.requested_by WHERE 1=1"
        + where + status + " ORDER BY CASE WHEN c.status IN ('pending','dialing') THEN 0 ELSE 1 END, "
        "c.run_at DESC LIMIT ?", (*params, limit))
    for r in rows:
        r["run_at_local"] = _local(datetime.fromisoformat(r["run_at"]))
    return rows


def cancel(request_id: int, person: Person) -> dict:
    row = get(request_id)
    if not row or not (person.is_admin or row["requested_by"] == person.id):
        raise ScheduleError("No such scheduled call.")
    if row["status"] != "pending":
        raise ScheduleError(f"That call is already {row['status']} and can't be cancelled.")
    _set(request_id, status="cancelled")
    db.log_activity("call", f"{person.display_name} cancelled the call to {row['target_name']} "
                            f"({row['run_at_local']})", user_id=row["target_user_id"])
    return get(request_id)


def _set(request_id: int, **fields) -> None:
    fields["updated_at"] = db.now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE scheduled_calls SET {cols} WHERE id = ?", (*fields.values(), request_id))


# --- dialing ---------------------------------------------------------------------------------

async def dispatch(request_id: int) -> None:
    from .call_handler import place_outbound_call

    row = get(request_id)
    if not row or row["status"] != "pending":
        return
    target = registry.get_person(row["target_user_id"])
    requester = registry.get_person(row["requested_by"]) if row["requested_by"] else None
    if target is None:
        _set(request_id, status="failed", result="The person is no longer active.")
        return
    busy = db.query_one("SELECT 1 FROM sessions WHERE user_id = ? AND channel = 'call' AND status = 'active'",
                        (target.id,))
    if busy:  # already on a call with us; try again shortly
        _set(request_id, run_at=(_now() + timedelta(minutes=3)).isoformat(timespec="seconds"))
        return
    _set(request_id, status="dialing", attempts=row["attempts"] + 1)
    opening = row["opening_line"] or _opening_line(target, requester, row["purpose"])
    try:
        await place_outbound_call(target.id, opening_line=opening, reason=row["purpose"],
                                  requested_by=requester.id if requester else None, request_id=request_id,
                                  extra_system=_call_brief(target, requester, row["purpose"]))
    except Exception as e:  # noqa: BLE001
        log.exception("Dispatch of call request %s failed", request_id)
        _set(request_id, status="failed", result=f"Couldn't place the call: {str(e)[:200]}")
        await _notify(requester, target, f"I couldn't place the call to {target.display_name}: {str(e)[:150]}")


async def on_call_finished(session_id: str, outcome: str) -> None:
    """Hook from the call handler. outcome: ended | terminated | no_answer | failed."""
    row = db.query_one("SELECT id FROM scheduled_calls WHERE session_id = ? AND status = 'dialing'", (session_id,))
    if not row:
        return
    # Atomic claim: the stream closing and Twilio's status webhook can both report the same call's
    # end within milliseconds; only the first one gets to process (and pay for) the outcome.
    if not db.execute_count("UPDATE scheduled_calls SET status = 'finishing', updated_at = ? "
                            "WHERE id = ? AND status = 'dialing'", (db.now_iso(), row["id"])):
        return
    req = get(row["id"])
    target = registry.get_person(req["target_user_id"])
    requester = registry.get_person(req["requested_by"]) if req["requested_by"] else None
    name = target.display_name if target else req["target_name"]

    if outcome in ("no_answer", "failed"):
        if req["attempts"] < MAX_ATTEMPTS and outcome == "no_answer":
            retry = _now() + timedelta(minutes=RETRY_MINUTES)
            _set(req["id"], status="pending", run_at=retry.isoformat(timespec="seconds"))
            db.log_activity("call", f"{name} didn't answer; retrying at {_local(retry)}", user_id=req["target_user_id"])
            return
        _set(req["id"], status="no_answer" if outcome == "no_answer" else "failed",
             result=f"Couldn't reach {name} after {req['attempts']} attempt(s).")
        await _notify(requester, target, f"I couldn't reach {name}, sir: no answer after {req['attempts']} attempt(s)."
                      if requester and requester.is_admin else
                      f"I couldn't reach {name}: no answer after {req['attempts']} attempt(s).")
        return

    turns = sessions.session_turns(session_id)
    spoke = any(t["speaker"] == "user" for t in turns)
    if not spoke:
        _set(req["id"], status="no_answer", result=f"{name} picked up but didn't say anything.")
        await _notify(requester, target, f"The call to {name} connected, but nobody spoke.")
        return
    summary = await _summarise(req, turns, requester, target) if req["purpose"] else "Call completed."
    _set(req["id"], status="done", result=summary)
    await _notify(requester, target, f"Call with {name} done. {summary}")


async def _summarise(req: dict, turns: list[dict], requester: Person | None, target: Person | None) -> str:
    from .agent_core import client
    transcript = "\n".join(f"{'Assistant' if t['speaker'] == 'assistant' else req['target_name']}: {t['text']}"
                           for t in turns)[-12000:]
    who = requester.display_name if requester else "the requester"
    try:
        resp = await client().messages.create(
            model=settings.claude_model,
            max_tokens=400,
            system=("You summarise phone calls an assistant made on someone's behalf. Reply with 1-3 plain "
                    "sentences for the person who asked for the call: the answer or agreement obtained, any "
                    "commitments or times mentioned, and anything left open. No preamble, no greeting."),
            messages=[{"role": "user", "content": f"{who} asked for this call. Purpose: {req['purpose']}\n\n"
                                                  f"Transcript:\n{transcript}"}],
        )
        usage.record_claude(resp.usage, kind="summary", user_id=req["requested_by"], session_id=req["session_id"])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or "The call finished, but I couldn't summarise it."
    except Exception:  # noqa: BLE001
        log.exception("Call summary failed")
        return "The call finished; the transcript is on the dashboard."


async def _notify(requester: Person | None, target: Person | None, text: str) -> None:
    """Tell the requester (Telegram if linked; always visible on the dashboard)."""
    if requester is None or (target is not None and requester.id == target.id):
        return
    db.log_activity("call", f"Report to {requester.display_name}: {text[:300]}", user_id=requester.id)
    if not settings.telegram_enabled:
        return
    if not registry.telegram_for(requester.id):
        return
    try:
        from .telegram_handler import send_to_user
        await send_to_user(requester.id, text, note="call report")
    except Exception:  # noqa: BLE001
        log.exception("Couldn't deliver call report")


async def background_loop() -> None:
    while True:
        try:
            due = db.query("SELECT id FROM scheduled_calls WHERE status = 'pending' AND run_at <= ? ORDER BY run_at",
                           (_now().isoformat(timespec="seconds"),))
            for r in due:
                await dispatch(r["id"])
            # A request stuck in 'dialing' with no live session (crash, restart) is closed out.
            for r in db.query("SELECT c.id, c.session_id FROM scheduled_calls c LEFT JOIN sessions s ON s.id = c.session_id "
                              "WHERE c.status IN ('dialing', 'finishing') AND c.updated_at < ? AND (s.id IS NULL OR s.status != 'active')",
                              ((_now() - timedelta(minutes=10)).isoformat(timespec="seconds"),)):
                _set(r["id"], status="failed", result="The call didn't complete (interrupted).")
        except Exception:
            log.exception("Scheduler iteration failed")
        await asyncio.sleep(POLL_SECONDS)
