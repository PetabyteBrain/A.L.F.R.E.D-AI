"""Mail tab: Alfred's mailbox (orchestrator/mail.py), its groups, tags and read/star/archive state
(orchestrator/mail_labels.py), escalations (orchestrator/escalation.py) and the admin switch for how much
Alfred answers mail from outside (mail_inbound.mode).

The tab is for admins and cofounders only (every route checks it), whatever role has dashboard access. Each sees
their own mail and the company's (inquiries, mail sent in the company's name), never the other's personal mail.
Other roles reach their own mail through Alfred (tools). Mail text is returned as plain text, never as HTML.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, escalation, mail, mail_inbound, mail_labels
from orchestrator.config import settings
from orchestrator.identity.registry import Person

from .routes import admin_only, current_person

TEAM = ("admin", "cofounder")


def team_only(person: Person = Depends(current_person)) -> Person:
    if person.role not in TEAM:
        raise HTTPException(status_code=403, detail="The mailbox is for admins and cofounders.")
    return person


router = APIRouter(prefix="/api/mail", dependencies=[Depends(team_only)])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CloseBody(Strict):
    note: str = Field(default="", max_length=500)


class ModeBody(Strict):
    mode: str = Field(pattern="^(answer|acknowledge|silent)$")


class LabelCreate(Strict):
    kind: str = Field(pattern="^(group|tag)$")
    scope: str = Field(default="personal", pattern="^(personal|company)$")
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(default="slate", max_length=20)


class LabelUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = Field(default=None, max_length=20)


class Bulk(Strict):
    ids: list[int] = Field(min_length=1, max_length=500)
    read: bool | None = None
    starred: bool | None = None
    archived: bool | None = None
    add_tags: list[int] = Field(default_factory=list, max_length=20)
    remove_tags: list[int] = Field(default_factory=list, max_length=20)
    set_group: bool = False
    group: int | None = None


def _fail(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _row_view(r: dict, labels: list | None = None, state: dict | None = None) -> dict:
    state = state or {}
    return {"id": r["id"], "direction": r["direction"], "kind": r["kind"], "status": r["status"], "scope": r["scope"],
            "from_addr": r["from_addr"], "from_name": r["from_name"], "to": r["to_addrs"], "cc": r["cc_addrs"],
            "subject": r["subject"], "summary": r["summary"], "created_at": r["created_at"],
            "attachments": [a.get("name") for a in r["attachments"]], "archived_nas": bool(r["nas_path"]),
            "thread_id": r["thread_id"], "labels": labels or [],
            "read": r["direction"] == "out" or bool(state.get("read_at")),
            "starred": bool(state.get("starred")), "archived": bool(state.get("archived"))}


@router.get("/status")
async def status(person: Person = Depends(current_person)) -> dict:
    out = {"enabled": settings.mail_enabled, "address": settings.mail_address or None,
           "receiving": settings.mail_inbound_enabled, "team": person.role in TEAM,
           "counts": mail_labels.counts(person), "colors": list(mail_labels.COLORS)}
    if person.is_admin:
        out["stranger_mode"] = mail_inbound.mode()
        out["retention_days"] = {"personal": settings.mail_retention_days,
                                 "company": settings.mail_company_retention_days}
    if person.role in TEAM:
        out["open_escalations"] = db.query_one("SELECT COUNT(*) n FROM escalations WHERE status = 'open'")["n"]
    return out


@router.get("")
async def list_mail(person: Person = Depends(current_person), box: str = Query(default="inbox", max_length=20),
                    group: int | None = Query(default=None, ge=1), tag: int | None = Query(default=None, ge=1),
                    q: str = Query(default="", max_length=80), unread: bool = Query(default=False),
                    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200)) -> dict:
    try:
        res = mail_labels.search(person, box=box, group=group, tag=tag, query=q, unread_only=unread, offset=offset,
                                 limit=limit)
    except mail_labels.LabelError as e:
        raise _fail(e)
    return {"box": res["box"], "total": res["total"], "offset": res["offset"],
            "emails": [_row_view(r, res["labels"].get(r["id"]), res["state"].get(r["id"])) for r in res["emails"]]}


# --- groups & tags ---------------------------------------------------------------------------------

@router.get("/labels")
async def labels(person: Person = Depends(current_person)) -> list[dict]:
    return mail_labels.list_labels(person)


@router.post("/labels")
async def create_label(body: LabelCreate, person: Person = Depends(current_person)) -> dict:
    try:
        return mail_labels.create_label(person, kind=body.kind, scope=body.scope, name=body.name, color=body.color)
    except mail_labels.LabelError as e:
        raise _fail(e)


@router.patch("/labels/{label_id}")
async def update_label(body: LabelUpdate, label_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return mail_labels.update_label(person, label_id, name=body.name, color=body.color)
    except mail_labels.LabelError as e:
        raise _fail(e)


@router.delete("/labels/{label_id}")
async def delete_label(label_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        mail_labels.delete_label(person, label_id)
    except mail_labels.LabelError as e:
        raise _fail(e)
    return {"ok": True}


@router.post("/bulk")
async def bulk(body: Bulk, person: Person = Depends(current_person)) -> dict:
    try:
        n = 0
        if body.add_tags or body.remove_tags or body.set_group:
            n = mail_labels.assign(person, body.ids, add_tags=body.add_tags, remove_tags=body.remove_tags,
                                   group=body.group, set_group=body.set_group)
        if body.read is not None or body.starred is not None or body.archived is not None:
            n = mail_labels.set_state(person, body.ids, read=body.read, starred=body.starred, archived=body.archived)
    except mail_labels.LabelError as e:
        raise _fail(e)
    return {"ok": True, "changed": n}


# --- escalations & settings ------------------------------------------------------------------------

@router.get("/escalations")
async def list_escalations(person: Person = Depends(current_person),
                           status: str = Query(default="open", pattern="^(open|handled|all)$")) -> list[dict]:
    try:
        return escalation.list_for(person, status=status, limit=100)
    except escalation.EscalationError:
        raise HTTPException(status_code=403)


@router.post("/escalations/{esc_id}/close")
async def close_escalation(body: CloseBody, esc_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return escalation.close(esc_id, person, body.note)
    except escalation.EscalationError as e:
        raise _fail(e)


@router.put("/stranger-mode")
async def set_stranger_mode(body: ModeBody, person: Person = Depends(admin_only)) -> dict:
    mail_inbound.set_mode(body.mode, person)
    return {"stranger_mode": mail_inbound.mode()}


# --- one email ---------------------------------------------------------------------------------------

def _readable(email_id: int, person: Person) -> dict:
    row = mail.get(email_id)
    if not row or not mail.may_read(row, person):
        raise HTTPException(status_code=404, detail="Unknown email.")
    return row


@router.get("/{email_id}")
async def get_mail(email_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    row = _readable(email_id, person)
    if row["direction"] == "in":
        mail_labels.mark_read(person, row["id"])
    thread = [mail.get(t["id"]) for t in db.query("SELECT id FROM emails WHERE thread_id = ? ORDER BY id",
                                                   (row["thread_id"] or row["id"],))]
    thread = [t for t in thread if t and mail.may_read(t, person)]
    ids = [t["id"] for t in thread] or [row["id"]]
    labs, state = mail_labels.labels_for(ids), mail_labels.state_for(person, ids)
    esc = db.query_one("SELECT id, status, urgency, topic FROM escalations WHERE email_id IN (?, ?) ORDER BY id DESC "
                       "LIMIT 1", (row["id"], row["thread_id"] or row["id"]))
    return {**_row_view(row, labs.get(row["id"]), state.get(row["id"])), "text": row["body_text"],
            "own_text": row["own_text"],
            "auth": {k: row["auth"].get(k) for k in ("spf", "dkim", "dmarc", "spam")} if row["direction"] == "in" else None,
            "error": row["error"], "escalation": esc,
            "thread": [{**_row_view(t, labs.get(t["id"]), state.get(t["id"])),
                        "text": (t["own_text"] or t["body_text"])[:20_000]} for t in thread]}


@router.get("/{email_id}/raw")
async def raw_mail(email_id: int = Path(ge=1), person: Person = Depends(current_person)) -> Response:
    row = _readable(email_id, person)
    try:
        data = await asyncio.to_thread(mail.read_raw, row)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="The stored copy can't be read right now.")
    if not data:
        raise HTTPException(status_code=404, detail="No stored copy.")
    return Response(data, media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="email-{row["id"]}.eml"',
                             "X-Content-Type-Options": "nosniff"})
