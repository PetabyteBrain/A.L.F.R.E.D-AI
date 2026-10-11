"""Mail tab: Alfred's mailbox (orchestrator/mail.py), escalations (orchestrator/escalation.py) and the admin
switch for how much Alfred answers mail from outside (mail_inbound.mode).

Everyone with dashboard access sees their own mail; admins and cofounders also the company's (inquiries,
mail sent in the company's name). Mail text is returned as plain text, never as HTML.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, escalation, mail, mail_inbound
from orchestrator.config import settings
from orchestrator.identity.registry import Person

from .routes import admin_only, current_person

router = APIRouter(prefix="/api/mail")
TEAM = ("admin", "cofounder")
BOXES = ("inbox", "inquiries", "sent", "all")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CloseBody(Strict):
    note: str = Field(default="", max_length=500)


class ModeBody(Strict):
    mode: str = Field(pattern="^(answer|acknowledge|silent)$")


def _row_view(r: dict) -> dict:
    return {"id": r["id"], "direction": r["direction"], "kind": r["kind"], "status": r["status"], "scope": r["scope"],
            "from_addr": r["from_addr"], "from_name": r["from_name"], "to": r["to_addrs"], "cc": r["cc_addrs"],
            "subject": r["subject"], "summary": r["summary"], "created_at": r["created_at"],
            "attachments": [a.get("name") for a in r["attachments"]], "archived": bool(r["nas_path"])}


@router.get("/status")
async def status(person: Person = Depends(current_person)) -> dict:
    out = {"enabled": settings.mail_enabled, "address": settings.mail_address or None,
           "receiving": settings.mail_inbound_enabled, "team": person.role in TEAM}
    if person.is_admin:
        out["stranger_mode"] = mail_inbound.mode()
        out["retention_days"] = {"personal": settings.mail_retention_days,
                                 "company": settings.mail_company_retention_days}
    if person.role in TEAM:
        out["open_escalations"] = db.query_one("SELECT COUNT(*) n FROM escalations WHERE status = 'open'")["n"]
    return out


@router.get("")
async def list_mail(person: Person = Depends(current_person), box: str = Query(default="inbox"),
                    q: str = Query(default="", max_length=80), limit: int = Query(default=50, ge=1, le=200)) -> dict:
    if box not in BOXES:
        raise HTTPException(status_code=400, detail="Unknown box.")
    team = person.role in TEAM
    if box == "inquiries" and not team:
        raise HTTPException(status_code=403)
    own = "(scope = 'personal' AND user_id = ?)"
    where, params = [], []
    if box == "inbox":
        where.append(f"direction = 'in' AND ({own} OR (kind = 'reply' AND user_id = ?))")
        params += [person.id, person.id]
    elif box == "inquiries":
        where.append("direction = 'in' AND scope = 'company' AND kind NOT IN ('spam','automated')")
    elif box == "sent":
        where.append(f"direction = 'out' AND ({own} OR (scope = 'company' AND ?))")
        params += [person.id, int(team)]
    else:
        where.append(f"({own} OR (scope = 'company' AND ?))")
        params += [person.id, int(team)]
    if q:
        where.append("(subject LIKE ? OR from_addr LIKE ? OR from_name LIKE ? OR to_addrs LIKE ? OR summary LIKE ?)")
        params += [f"%{q}%"] * 5
    rows = db.query(f"SELECT id FROM emails WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*params, limit))
    return {"box": box, "emails": [_row_view(mail.get(r["id"])) for r in rows]}


def _readable(email_id: int, person: Person) -> dict:
    row = mail.get(email_id)
    if not row or not mail.may_read(row, person):
        raise HTTPException(status_code=404, detail="Unknown email.")
    return row


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
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/stranger-mode")
async def set_stranger_mode(body: ModeBody, person: Person = Depends(admin_only)) -> dict:
    mail_inbound.set_mode(body.mode, person)
    return {"stranger_mode": mail_inbound.mode()}


@router.get("/{email_id}")
async def get_mail(email_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    row = _readable(email_id, person)
    thread = db.query("SELECT id FROM emails WHERE thread_id = ? AND id != ? ORDER BY id", (row["thread_id"], row["id"]))
    others = [mail.get(t["id"]) for t in thread]
    esc = db.query_one("SELECT id, status, urgency, topic FROM escalations WHERE email_id IN (?, ?) ORDER BY id DESC "
                       "LIMIT 1", (row["id"], row["thread_id"] or row["id"]))
    return {**_row_view(row), "text": row["body_text"], "own_text": row["own_text"],
            "auth": {k: row["auth"].get(k) for k in ("spf", "dkim", "dmarc", "spam")} if row["direction"] == "in" else None,
            "error": row["error"], "escalation": esc,
            "thread": [_row_view(o) for o in others if o and mail.may_read(o, person)]}


@router.get("/{email_id}/raw")
async def raw_mail(email_id: int = Path(ge=1), person: Person = Depends(current_person)) -> Response:
    row = _readable(email_id, person)
    try:
        import asyncio
        data = await asyncio.to_thread(mail.read_raw, row)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="The stored copy can't be read right now.")
    if not data:
        raise HTTPException(status_code=404, detail="No stored copy.")
    return Response(data, media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="email-{row["id"]}.eml"',
                             "X-Content-Type-Options": "nosniff"})
