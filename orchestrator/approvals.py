"""Drafts that need a second look before they happen (roadmap #58): deleting or merging CRM records, anything
sent to clients (quotes, invoices, reminders, emails), and every change to employee data (absences included).

A draft belongs to an area ("crm", ...); anyone holding "<area>.approve" can approve or reject it, the
requester included (self-approval is allowed, decided 2026-10-12). Approvers hear about new drafts on
Telegram with Approve / Reject buttons and on the dashboard (CRM → Approvals). On approval the area's
handler runs with the approver as the actor; the requester is told the outcome.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Callable

from . import db
from .identity.registry import Person

log = logging.getLogger(__name__)

# (area, kind) -> handler(payload, actor: Person) -> short result text (or a coroutine giving it, e.g. sending
# an email); raises ValueError with a safe message
HANDLERS: dict[tuple[str, str], Callable] = {}
# (area, kind) -> called with (payload, actor) when a request is rejected or withdrawn (e.g. an absence request)
CLOSE_HANDLERS: dict[tuple[str, str], Callable] = {}
AREA_LABELS = {"crm": "CRM", "billing": "Billing", "employees": "Employees"}


class ApprovalError(ValueError):
    """Message is safe to show."""


def handler(area: str, kind: str):
    def wrap(fn):
        HANDLERS[(area, kind)] = fn
        return fn
    return wrap


def on_close(area: str, kind: str):
    """Runs when a request of this kind is rejected or withdrawn, so the area can tidy up its own state."""
    def wrap(fn):
        CLOSE_HANDLERS[(area, kind)] = fn
        return fn
    return wrap


def _closed(a: dict, actor: Person) -> None:
    fn = CLOSE_HANDLERS.get((a["area"], a["kind"]))
    if fn:
        try:
            fn(a["payload"], actor)
        except Exception:  # noqa: BLE001
            log.exception("Close handler for approval #%s failed", a["id"])


def _view(row: dict | None) -> dict | None:
    if not row:
        return None
    row["payload"] = json.loads(row["payload"] or "{}")
    return row


_SELECT = ("SELECT a.*, r.display_name AS requested_by_name, d.display_name AS decided_by_name FROM approvals a "
           "LEFT JOIN users r ON r.id = a.requested_by LEFT JOIN users d ON d.id = a.decided_by")


def get(approval_id: int) -> dict | None:
    return _view(db.query_one(f"{_SELECT} WHERE a.id = ?", (approval_id,)))


def create(area: str, kind: str, summary: str, payload: dict, requested_by: Person) -> dict:
    if (area, kind) not in HANDLERS:
        raise ApprovalError("Unknown kind of request.")
    dup = db.query_one("SELECT id FROM approvals WHERE status = 'pending' AND area = ? AND kind = ? AND payload = ?",
                       (area, kind, json.dumps(payload, sort_keys=True)))
    if dup:
        return {**get(dup["id"]), "already_pending": True}
    aid = db.execute("INSERT INTO approvals(area, kind, summary, payload, requested_by, created_at) VALUES (?,?,?,?,?,?)",
                     (area, kind, summary[:500], json.dumps(payload, sort_keys=True), requested_by.id, db.now_iso()))
    db.log_activity("approval", f"{requested_by.display_name} asked for approval: {summary[:200]}", user_id=requested_by.id)
    _later(_notify_approvers(aid))
    return get(aid)


def _later(coro) -> None:
    """Fire-and-forget when an event loop runs (the app); otherwise (CLI, tests) just skip the message."""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()


def approvers(area: str) -> list[dict]:
    from . import permissions
    return permissions.people_with(f"{area}.approve")


async def _notify_approvers(aid: int) -> None:
    from . import telegram_handler
    from .config import settings
    a = get(aid)
    if not a or not settings.telegram_enabled:
        return
    text = (f"✋ *Approval needed* ({AREA_LABELS.get(a['area'], a['area'])}) #{aid}\n{a['summary']}\n"
            f"Asked by {a['requested_by_name'] or 'someone'}.")
    for p in approvers(a["area"]):
        try:
            await telegram_handler.send_to_user(p["id"], text, note=f"approval #{aid}", record=False,
                                                buttons=[[("✓ Approve", f"apv:yes:{aid}"), ("✕ Reject", f"apv:no:{aid}")]])
        except Exception:  # noqa: BLE001 — no Telegram for this person: the dashboard still shows it
            log.info("Approval #%s: no Telegram message for user %s", aid, p["id"])


async def _notify_requester(a: dict) -> None:
    from . import telegram_handler
    from .config import settings
    if not settings.telegram_enabled or not a.get("requested_by") or a["requested_by"] == a.get("decided_by"):
        return
    word = {"approved": "approved ✓", "rejected": "rejected", "failed": "approved, but it failed"}[a["status"]]
    text = f"Your request #{a['id']} was {word} by {a['decided_by_name']}: {a['summary']}"
    if a.get("note"):
        text += f"\nNote: {a['note']}"
    if a["status"] == "failed" and a.get("result"):
        text += f"\n{a['result']}"
    try:
        await telegram_handler.send_to_user(a["requested_by"], text, note=f"approval #{a['id']}")
    except Exception:  # noqa: BLE001
        log.info("Approval #%s: requester has no Telegram", a["id"])


def can_decide(person: Person, area: str) -> bool:
    return person.can(f"{area}.approve")


def list_for(person: Person, *, status: str = "pending", limit: int = 100) -> list[dict]:
    """Drafts this person may decide, plus their own requests."""
    areas = [a for a in AREA_LABELS if can_decide(person, a)]
    where = "(a.requested_by = ?" + (f" OR a.area IN ({','.join('?' * len(areas))})" if areas else "") + ")"
    params: list = [person.id, *areas]
    if status == "pending":
        where += " AND a.status = 'pending'"
    elif status == "decided":
        where += " AND a.status != 'pending'"
    rows = db.query(f"{_SELECT} WHERE {where} ORDER BY a.id DESC LIMIT ?", (*params, limit))
    out = []
    for r in rows:
        r = _view(r)
        r["can_decide"] = r["status"] == "pending" and can_decide(person, r["area"])
        r["can_cancel"] = r["status"] == "pending" and r["requested_by"] == person.id
        out.append(r)
    return out


def pending_count(person: Person) -> int:
    areas = [a for a in AREA_LABELS if can_decide(person, a)]
    if not areas:
        return 0
    return db.query_one(f"SELECT COUNT(*) AS n FROM approvals WHERE status = 'pending' AND area IN "
                        f"({','.join('?' * len(areas))})", areas)["n"]


async def decide(approval_id: int, person: Person, approve: bool, note: str = "") -> dict:
    a = get(approval_id)
    if not a:
        raise ApprovalError("No such request.")
    if not can_decide(person, a["area"]):
        raise ApprovalError("You don't have permission to decide this request.")
    # Claim it atomically so two approvers can't both run it.
    if not db.execute_count("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, note = ? "
                            "WHERE id = ? AND status = 'pending'",
                            ("approved" if approve else "rejected", person.id, db.now_iso(), (note or "")[:300],
                             approval_id)):
        raise ApprovalError(f"This request was already {a['status']}.")
    result = ""
    if approve:
        try:
            result = HANDLERS[(a["area"], a["kind"])](a["payload"], person)
            if inspect.isawaitable(result):
                result = await result
        except ValueError as e:
            db.execute("UPDATE approvals SET status = 'failed', result = ? WHERE id = ?", (str(e)[:500], approval_id))
        except Exception as e:  # noqa: BLE001
            log.exception("Approval #%s failed", approval_id)
            db.execute("UPDATE approvals SET status = 'failed', result = ? WHERE id = ?",
                       (f"{type(e).__name__}"[:500], approval_id))
        else:
            db.execute("UPDATE approvals SET result = ? WHERE id = ?", (result[:500], approval_id))
    else:
        _closed(a, person)
    done = get(approval_id)
    db.log_activity("approval", f"{person.display_name} {done['status']} #{approval_id}: {a['summary'][:200]}",
                    user_id=person.id)
    _later(_notify_requester(done))
    return done


def cancel(approval_id: int, person: Person) -> dict:
    a = get(approval_id)
    if not a or a["requested_by"] != person.id:
        raise ApprovalError("No such request of yours.")
    if not db.execute_count("UPDATE approvals SET status = 'cancelled', decided_by = ?, decided_at = ? "
                            "WHERE id = ? AND status = 'pending'", (person.id, db.now_iso(), approval_id)):
        raise ApprovalError(f"This request was already {a['status']}.")
    db.log_activity("approval", f"{person.display_name} withdrew request #{approval_id}", user_id=person.id)
    _closed(a, person)
    return get(approval_id)
