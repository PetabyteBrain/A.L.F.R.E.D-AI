"""CRM tab (crm.use): companies, contacts, their timeline, undo, and the approvals queue.
Every rule (who sees which record, caps, approvals) lives in orchestrator/crm.py and approvals.py."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import approvals, crm, db, permissions
from orchestrator.identity.registry import Person

from .routes import current_person, requires

router = APIRouter(prefix="/api/crm", dependencies=[Depends(requires("crm.use"))])
Entity = Literal["company", "contact"]
_PLURAL = {"companies": "company", "contacts": "contact"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RecordBody(Strict):
    fields: dict = Field(default_factory=dict)
    force: bool = False


class Flag(Strict):
    archived: bool


class ActivityBody(Strict):
    kind: Literal["note", "call", "meeting", "task", "document"]
    summary: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=5000)
    when: str | None = Field(default=None, max_length=40)


class UndoBody(Strict):
    change_id: int | None = Field(default=None, ge=1)


class RequestBody(Strict):
    action: Literal["delete", "merge"]
    entity: Entity
    id: int = Field(ge=1)
    merge_into: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=200)


class Decision(Strict):
    approve: bool
    note: str = Field(default="", max_length=300)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except crm.DuplicateError as e:
        raise HTTPException(status_code=409, detail={"message": str(e), "duplicates": e.matches})
    except (crm.CrmError, approvals.ApprovalError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _entity(kind: str) -> str:
    if kind not in _PLURAL:
        raise HTTPException(status_code=404)
    return _PLURAL[kind]


@router.get("/status")
async def status(person: Person = Depends(current_person)) -> dict:
    gids = None if person.can("crm.view_all") else {g["id"] for g in permissions.groups_of(person.id)}
    return {"counts": _run(crm.counts, person), "statuses": crm.STATUSES, "activity_kinds": crm.ACTIVITY_KINDS,
            "can": {"edit": person.can("crm.edit"), "approve": person.can("crm.approve"),
                    "view_all": person.can("crm.view_all")},
            "approvals_pending": approvals.pending_count(person),
            "my_requests_pending": db.query_one("SELECT COUNT(*) AS n FROM approvals WHERE status = 'pending' "
                                                "AND requested_by = ?", (person.id,))["n"],
            "people": [{"id": p["id"], "display_name": p["display_name"]} for p in permissions.people_with("crm.use")],
            "groups": [{"id": g["id"], "name": g["name"]} for g in permissions.groups()
                       if gids is None or g["id"] in gids],
            "tags": _run(crm.tags_in_use, person)}


@router.get("/duplicates")
async def duplicates(person: Person = Depends(current_person), entity: Entity = Query(...),
                     name: str = Query(default="", max_length=120), first_name: str = Query(default="", max_length=120),
                     last_name: str = Query(default="", max_length=120), email: str = Query(default="", max_length=254),
                     phone: str = Query(default="", max_length=40), website: str = Query(default="", max_length=200),
                     exclude: int | None = Query(default=None, ge=1)) -> list[dict]:
    data = {k: v for k, v in dict(name=name, first_name=first_name, last_name=last_name, email=email, phone=phone,
                                  website=website).items() if v}
    return _run(crm.duplicates, person, entity, data, exclude) if data else []


@router.get("/{kind}")
async def list_records(kind: str = Path(max_length=12), person: Person = Depends(current_person),
                       q: str = Query(default="", max_length=80), status: str = Query(default="", max_length=20),
                       tag: str = Query(default="", max_length=30), company_id: int | None = Query(default=None, ge=1),
                       mine: bool = False, archived: bool = False,
                       sort: Literal["name", "recent"] = "name",
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return _run(crm.search, person, _entity(kind), q=q, status=status, tag=tag, company_id=company_id, mine=mine,
                archived=archived, sort=sort, offset=offset, limit=limit)


@router.post("/{kind}")
async def create(body: RecordBody, kind: str = Path(max_length=12), person: Person = Depends(current_person)) -> dict:
    return _run(crm.save, person, _entity(kind), body.fields, None, force=body.force)


@router.get("/{kind}/{record_id}")
async def get_record(kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                     person: Person = Depends(current_person)) -> dict:
    return _run(crm.get, person, _entity(kind), record_id)


@router.patch("/{kind}/{record_id}")
async def update(body: RecordBody, kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                 person: Person = Depends(current_person)) -> dict:
    return _run(crm.save, person, _entity(kind), body.fields, record_id)


@router.post("/{kind}/{record_id}/archive")
async def archive(body: Flag, kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                  person: Person = Depends(current_person)) -> dict:
    return _run(crm.set_archived, person, _entity(kind), record_id, body.archived)


@router.get("/{kind}/{record_id}/timeline")
async def timeline(kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                   person: Person = Depends(current_person),
                   type: str = Query(default="", max_length=12)) -> list[dict]:
    return _run(crm.timeline, person, _entity(kind), record_id, kind=type, limit=150)


@router.post("/{kind}/{record_id}/activities")
async def add_activity(body: ActivityBody, kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                       person: Person = Depends(current_person)) -> dict:
    return _run(crm.log, person, _entity(kind), record_id, body.kind, body.summary, body.body, body.when)


@router.get("/{kind}/{record_id}/history")
async def history(kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                  person: Person = Depends(current_person)) -> list[dict]:
    return _run(crm.history, person, _entity(kind), record_id)


# Fixed paths below are registered on their own router so they never collide with /{kind}/{id}.
actions = APIRouter(prefix="/api/crm-actions", dependencies=[Depends(requires("crm.use"))])


@actions.delete("/activities/{activity_id}")
async def delete_activity(activity_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(crm.delete_activity, person, activity_id)


@actions.post("/undo")
async def undo(body: UndoBody, person: Person = Depends(current_person)) -> dict:
    return _run(crm.undo, person, body.change_id)


@actions.post("/requests")
async def make_request(body: RequestBody, person: Person = Depends(current_person)) -> dict:
    return _run(crm.request, person, body.action, body.entity, body.id, merge_into=body.merge_into, reason=body.reason)


@actions.get("/approvals")
async def list_approvals(person: Person = Depends(current_person),
                         status: Literal["pending", "decided", "all"] = "pending") -> list[dict]:
    return approvals.list_for(person, status=status)


@actions.post("/approvals/{approval_id}")
async def decide(body: Decision, approval_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(approvals.decide, approval_id, person, body.approve, body.note)


@actions.delete("/approvals/{approval_id}")
async def cancel(approval_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(approvals.cancel, approval_id, person)

