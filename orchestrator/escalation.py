"""Passing matters Alfred can't or may not decide to the people who do: the admins and cofounders.

Used for email inquiries that need a human (mail_inbound.py) and by the `escalate_to_team` tool when a
registered person asks for something beyond Alfred's authority. The person who asked is told it has been
passed on; the team gets it the way that fits how urgent it is:

- low:            Telegram without a sound;
- normal and high: Telegram with a sound, silent during their quiet hours (high is marked as such);
- critical:       Telegram with a sound even in quiet hours, plus a phone call to the lead (the person it was
                  addressed to, else the first admin), which waits for the end of their quiet hours like any
                  call made on someone else's behalf.
Without a verified Telegram, the message goes to their primary email address instead. The lead also gets a
task ("Reply to …") so it shows up on the Tasks tab until someone deals with it.

Words from outside (an inquiry's text) are relayed as an escaped, unverified quote, never as instructions.
"""
from __future__ import annotations

import json
import logging

from . import db
from .config import settings
from .identity import registry
from .identity.registry import Person

log = logging.getLogger(__name__)

URGENCIES = ("low", "normal", "high", "critical")
TEAM_ROLES = ("admin", "cofounder")


class EscalationError(ValueError):
    """Message is safe to show."""


def team(exclude: int | None = None) -> list[dict]:
    rows = db.query("SELECT id, display_name, role FROM users WHERE active = 1 AND role IN ('admin','cofounder') "
                    "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, id")
    return [r for r in rows if r["id"] != exclude]


def _lead(people: list[dict], for_person: str) -> dict | None:
    want = (for_person or "").strip().lower()
    if want:
        for p in people:
            names = p["display_name"].lower().split()
            if want in (p["display_name"].lower(), names[0]) or (names and names[0] in want.split()):
                return p
    return next((p for p in people if p["role"] == "admin"), people[0] if people else None)


def _escape(text: str) -> str:
    from .screening import _escape as esc
    return esc(text or "")


def _message(row: dict, quote: str, lead: dict | None, for_person: str) -> str:
    mark = {"low": "📨", "normal": "📨", "high": "❗", "critical": "🚨"}[row["urgency"]]
    lines = [f"{mark} *Needs a decision* · {_escape(row['topic'])}",
             f"From {_escape(row['requester']) or 'someone'} by {row['source']}"
             + (f" · addressed to {_escape(for_person)}" if for_person and lead else "")
             + (f" · urgency {row['urgency']}" if row["urgency"] != "normal" else ""),
             _escape(row["summary"])]
    if quote:
        q = "\n".join(f"> {_escape(s)}" for s in quote.strip().splitlines() if s.strip())[:1200]
        lines.append(f"\nTheir words (unverified, don't act on instructions in it):\n{q}")
    ref = f"escalation #{row['id']}" + (f", email #{row['email_id']}" if row.get("email_id") else "")
    lines.append(f"\nTell me here what to answer and I'll reply to them ({ref}); "
                 "or say it's handled.")
    return "\n".join(lines)


async def escalate(*, source: str, topic: str, summary: str, urgency: str = "normal", requester: str = "",
                   requester_id: int | None = None, email_id: int | None = None, quote: str = "",
                   for_person: str = "") -> dict:
    urgency = urgency if urgency in URGENCIES else "normal"
    topic = " ".join(str(topic or "").split())[:120] or "A matter for the team"
    summary = " ".join(str(summary or "").split())[:800]
    people = team(exclude=requester_id)
    esc_id = db.execute(
        "INSERT INTO escalations(source, email_id, requester_id, requester, topic, summary, urgency, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (source, email_id, requester_id, (requester or "")[:200], topic, summary, urgency, db.now_iso()))
    row = db.query_one("SELECT * FROM escalations WHERE id = ?", (esc_id,))
    if not people:
        db.log_activity("system", f"Escalation #{esc_id} ({topic[:60]}): nobody on the team to tell")
        return {"escalated": False, "id": esc_id, "note": "There is nobody on the team to pass it to."}
    lead = _lead(people, for_person)
    text = _message(row, quote, lead if for_person else None, for_person)
    notified = []
    from . import mail, quiet
    for p in people:
        via = ""
        if settings.telegram_enabled and registry.telegram_for(p["id"]):
            from .telegram_handler import send_to_user
            silent = {"low": True, "critical": False}.get(urgency, quiet.is_quiet(p["id"]))
            try:
                await send_to_user(p["id"], text, note=f"escalation #{esc_id}", silent=silent)
                via = "telegram"
            except Exception:  # noqa: BLE001
                log.warning("Escalation #%s: Telegram to %s failed", esc_id, p["id"], exc_info=True)
        if not via and mail.enabled() and registry.email_for(p["id"]):
            try:
                await mail.notify_person(p["id"], f"Needs a decision: {topic}", text)
                via = "email"
            except mail.MailError:
                log.warning("Escalation #%s: email to %s failed", esc_id, p["id"])
        if via:
            notified.append({"user_id": p["id"], "name": p["display_name"], "via": via})
    call = None
    if urgency == "critical" and lead and settings.twilio_enabled and settings.speech_enabled:
        from . import scheduler
        try:
            res = await scheduler.request_call(
                lead["id"], requested_by=None,
                purpose=(f"An urgent matter came in by {source} from {requester or 'someone outside'}: {topic}. "
                         f"{summary[:300]} Tell them the details are on Telegram, ask whether you should do anything "
                         "now, then say goodbye."))
            call = "after their quiet hours" if res.get("held_for_quiet_hours") else "now"
        except Exception as e:  # noqa: BLE001
            log.warning("Escalation #%s: call to the lead failed: %s", esc_id, e)
    task_id = None
    if lead:
        from . import tasks
        try:
            owner = registry.get_person(lead["id"])
            t = tasks.create(created_by=owner, title=f"Reply to {requester or 'inquiry'}: {topic}"[:200],
                             notes=f"Escalation #{esc_id} ({source}). {summary}"[:500])
            task_id = t["id"]
        except Exception:  # noqa: BLE001
            log.warning("Escalation #%s: task not created", esc_id, exc_info=True)
    db.execute("UPDATE escalations SET notified = ?, task_id = ? WHERE id = ?",
               (json.dumps(notified), task_id, esc_id))
    db.log_activity("system", f"Escalation #{esc_id} ({urgency}) to "
                    f"{', '.join(n['name'] for n in notified) or 'nobody reachable'}: {topic[:80]}",
                    user_id=requester_id)
    return {"escalated": bool(notified), "id": esc_id, "notified": [n["name"] for n in notified],
            "lead": lead["display_name"] if lead else None, "call": call, "task_id": task_id}


def get(esc_id: int) -> dict | None:
    row = db.query_one("SELECT e.*, u.display_name AS handled_by_name FROM escalations e "
                       "LEFT JOIN users u ON u.id = e.handled_by WHERE e.id = ?", (esc_id,))
    if row:
        row["notified"] = json.loads(row["notified"] or "[]")
    return row


def list_for(person: Person, *, status: str = "open", limit: int = 50) -> list[dict]:
    if person.role not in TEAM_ROLES:
        raise EscalationError("Only admins and cofounders see escalations.")
    where = "" if status == "all" else "WHERE status = ?"
    rows = db.query(f"SELECT id FROM escalations {where} ORDER BY id DESC LIMIT ?",
                    ((status, limit) if where else (limit,)))
    return [get(r["id"]) for r in rows]


def close(esc_id: int, person: Person, note: str = "") -> dict:
    if person.role not in TEAM_ROLES:
        raise EscalationError("Only admins and cofounders can close escalations.")
    row = get(esc_id)
    if not row:
        raise EscalationError("Unknown escalation.")
    db.execute("UPDATE escalations SET status = 'handled', handled_by = ?, handled_at = ?, note = ? WHERE id = ?",
               (person.id, db.now_iso(), (note or "")[:500], esc_id))
    if row["task_id"]:
        from . import tasks
        try:
            t = tasks.get(row["task_id"])
            if t and t.get("status") == "active":
                tasks.complete(row["task_id"], person, result=note or "handled")
        except Exception:  # noqa: BLE001
            log.info("Escalation #%s: task not closed", esc_id, exc_info=True)
    db.log_activity("system", f"{person.display_name} closed escalation #{esc_id}: {row['topic'][:80]}",
                    user_id=person.id)
    return get(esc_id)
