"""CRM tab (crm.use): companies, contacts, their timeline, undo, and the approvals queue.
Every rule (who sees which record, caps, approvals) lives in orchestrator/crm.py and approvals.py."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import approvals, crm, crm_deals, crm_leads, db, permissions
from orchestrator.identity.registry import Person

from .routes import current_person, requires

router = APIRouter(prefix="/api/crm", dependencies=[Depends(requires("crm.use"))])
Entity = Literal["company", "contact", "deal"]
_PLURAL = {"companies": "company", "contacts": "contact", "deals": "deal"}


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
                    "view_all": person.can("crm.view_all"), "settings": person.can("crm.settings")},
            "stages": crm_deals.stages(), "leads": crm_leads.counts(person),
            "deals_open": _run(crm_deals.search, person, status="open", limit=1)["total"],
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
                       stage_id: int | None = Query(default=None, ge=1),
                       mine: bool = False, archived: bool = False,
                       sort: Literal["name", "recent", "close", "value", "title"] = "name",
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200)) -> dict:
    entity = _entity(kind)
    if entity == "deal":
        return _run(crm_deals.search, person, q=q, status=status or "open", stage_id=stage_id, company_id=company_id,
                    mine=mine, archived=archived, sort=sort if sort != "name" else "close", offset=offset, limit=limit)
    return _run(crm.search, person, entity, q=q, status=status, tag=tag, company_id=company_id, mine=mine,
                archived=archived, sort=sort if sort in ("name", "recent") else "name", offset=offset, limit=limit)


@router.post("/{kind}")
async def create(body: RecordBody, kind: str = Path(max_length=12), person: Person = Depends(current_person)) -> dict:
    entity = _entity(kind)
    if entity == "deal":
        return _run(crm_deals.save, person, body.fields)
    return _run(crm.save, person, entity, body.fields, None, force=body.force)


@router.get("/{kind}/{record_id}")
async def get_record(kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                     person: Person = Depends(current_person)) -> dict:
    entity = _entity(kind)
    return _run(crm_deals.get, person, record_id) if entity == "deal" else _run(crm.get, person, entity, record_id)


@router.patch("/{kind}/{record_id}")
async def update(body: RecordBody, kind: str = Path(max_length=12), record_id: int = Path(ge=1),
                 person: Person = Depends(current_person)) -> dict:
    entity = _entity(kind)
    if entity == "deal":
        return _run(crm_deals.save, person, body.fields, record_id)
    return _run(crm.save, person, entity, body.fields, record_id)


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


class Move(Strict):
    stage_id: int = Field(ge=1)
    note: str = Field(default="", max_length=300)


class StageBody(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    probability: int | None = Field(default=None, ge=0, le=100)


class Direction(Strict):
    direction: Literal[-1, 1]


class Ids(Strict):
    ids: list[int] = Field(min_length=1, max_length=200)


class LeadAdd(Strict):
    force: bool = False


class LeadLink(Strict):
    company_id: int = Field(ge=1)


@actions.post("/deals/{deal_id}/move")
async def move_deal(body: Move, deal_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(crm_deals.move, person, deal_id, body.stage_id, body.note)


@actions.get("/pipeline")
async def pipeline(person: Person = Depends(current_person), mine: bool = False,
                   q: str = Query(default="", max_length=80)) -> dict:
    return {**_run(crm_deals.board, person, mine=mine, q=q), "forecast": _run(crm_deals.forecast, person, mine=mine)}


@actions.get("/stages")
async def list_stages(person: Person = Depends(current_person)) -> list[dict]:
    return crm_deals.stages(include_archived=True)


@actions.post("/stages")
async def add_stage(body: StageBody, person: Person = Depends(current_person)) -> list[dict]:
    _run(crm_deals.save_stage, person, name=body.name, probability=body.probability)
    return crm_deals.stages(include_archived=True)


@actions.patch("/stages/{stage_id}")
async def change_stage(body: StageBody, stage_id: int = Path(ge=1),
                       person: Person = Depends(current_person)) -> list[dict]:
    _run(crm_deals.save_stage, person, name=body.name, probability=body.probability, stage_id=stage_id)
    return crm_deals.stages(include_archived=True)


@actions.post("/stages/{stage_id}/move")
async def reorder_stage(body: Direction, stage_id: int = Path(ge=1),
                        person: Person = Depends(current_person)) -> list[dict]:
    _run(crm_deals.move_stage, person, stage_id, body.direction)
    return crm_deals.stages(include_archived=True)


@actions.post("/stages/{stage_id}/archive")
async def archive_stage(body: Flag, stage_id: int = Path(ge=1), person: Person = Depends(current_person)) -> list[dict]:
    _run(crm_deals.archive_stage, person, stage_id, body.archived)
    return crm_deals.stages(include_archived=True)


@actions.get("/leads")
async def leads(person: Person = Depends(current_person), source: Literal["", "market", "mail"] = "",
                status: Literal["open", "added", "linked", "dismissed", "all"] = "open") -> list[dict]:
    return _run(crm_leads.list_for, person, source=source, status=status)


@actions.post("/leads/add")
async def add_leads(body: Ids, person: Person = Depends(current_person)) -> dict:
    return _run(crm_leads.add_many, person, body.ids)


@actions.post("/leads/{lead_id}/add")
async def add_lead(body: LeadAdd, lead_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(crm_leads.add, person, lead_id, force=body.force)


@actions.post("/leads/{lead_id}/link")
async def link_lead(body: LeadLink, lead_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(crm_leads.link, person, lead_id, body.company_id)


@actions.post("/leads/dismiss")
async def dismiss_leads(body: Ids, person: Person = Depends(current_person)) -> dict:
    return {"dismissed": _run(crm_leads.dismiss, person, body.ids)}


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

