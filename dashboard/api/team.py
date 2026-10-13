"""Team tab (CRM stage 4): the employee directory, absences, onboarding/offboarding and the generic approvals queue.
Every rule lives in orchestrator/employees.py (who sees what, approvals for every change to employee data)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import approvals, db, employees, timetrack
from orchestrator.identity.registry import Person

from .routes import current_person

_TEAM = ("employees.self", "employees.view", "employees.manage", "employees.approve")


def _team_member(person: Person = Depends(current_person)) -> Person:
    if not any(person.can(p) for p in _TEAM):
        raise HTTPException(status_code=403)
    return person


router = APIRouter(prefix="/api/team", dependencies=[Depends(_team_member)])
approvals_router = APIRouter(prefix="/api/approvals")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChangeBody(Strict):
    fields: dict = Field(default_factory=dict)
    onboarding: bool = False
    offboarding: bool = False
    reason: str = Field(default="", max_length=200)


class AbsenceBody(Strict):
    kind: Literal["holiday", "sick", "unpaid", "training", "parental", "other"]
    start: str = Field(max_length=10)
    end: str | None = Field(default=None, max_length=10)
    half_day: bool = False
    note: str = Field(default="", max_length=300)
    employee_id: int | None = Field(default=None, ge=1)


class ChecklistBody(Strict):
    kind: Literal["onboarding", "offboarding"]
    anchor: str | None = Field(default=None, max_length=10)


class TaskBody(Strict):
    title: str = Field(min_length=1, max_length=160)
    due_date: str | None = Field(default=None, max_length=10)
    assignee_id: int | None = Field(default=None, ge=1)


class TaskPatch(Strict):
    done: bool | None = None
    due_date: str | None = Field(default=None, max_length=10)
    assignee_id: int | None = Field(default=None, ge=0)


class SettingsBody(Strict):
    settings: dict


class Decision(Strict):
    approve: bool
    note: str = Field(default="", max_length=300)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (employees.EmployeeError, approvals.ApprovalError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def status(person: Person = Depends(current_person)) -> dict:
    own = employees.own_record(person)
    see_all = person.can("employees.view")
    open_steps = [t for t in employees.due_tasks(unreminded_only=False) if t["assignee_id"] == person.id]
    pending = (db.query_one("SELECT COUNT(*) AS n FROM employee_absences WHERE status = 'pending'")["n"]
               if see_all else 0)
    return {"can": {"view": see_all, "manage": person.can("employees.manage"),
                    "approve": person.can("employees.approve"), "self": person.can("employees.self")},
            "me": {"id": own["id"], "name": own["name"], "balance": employees.balance(own)} if own else None,
            "count": len(employees.directory(person)) if see_all else None,
            "away_today": employees.away_today() if see_all else [],
            "absences_pending": pending, "my_steps_due": len(open_steps),
            "approvals_pending": len([a for a in approvals.list_for(person) if a["area"] == "employees"
                                      and a["can_decide"]]),
            "kinds": employees.ABSENCE_KINDS, "employment": employees.EMPLOYMENT, "who": employees.WHO,
            "timetrack": timetrack.configured(),
            "people": [{"id": u["id"], "display_name": u["display_name"]}
                       for u in db.query("SELECT id, display_name FROM users WHERE active = 1 ORDER BY display_name")]
            if person.can("employees.manage") else [],
            "managers": [{"id": e["id"], "name": e["name"]} for e in employees.directory(person)] if see_all else []}


@router.get("/employees")
async def directory(person: Person = Depends(current_person), q: str = Query(default="", max_length=80),
                    former: bool = False) -> list[dict]:
    return _run(employees.directory, person, q=q, include_former=former)


@router.post("/employees")
async def add(body: ChangeBody, person: Person = Depends(current_person)) -> dict:
    return _run(employees.request_change, person, body.fields, None, onboarding=body.onboarding,
                offboarding=False, reason=body.reason)


@router.get("/employees/{employee_id}")
async def get(employee_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(employees.get, person, employee_id)


@router.patch("/employees/{employee_id}")
async def change(body: ChangeBody, employee_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(employees.request_change, person, body.fields, employee_id, onboarding=body.onboarding,
                offboarding=body.offboarding, reason=body.reason)


@router.get("/employees/{employee_id}/time")
async def time(employee_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    row = _run(employees.find, person, employee_id)
    try:
        return await timetrack.employee_hours(row)
    except timetrack.TimetrackError as e:
        return {"configured": True, "error": str(e)}


@router.post("/employees/{employee_id}/checklists")
async def start_checklist(body: ChecklistBody, employee_id: int = Path(ge=1),
                          person: Person = Depends(current_person)) -> dict:
    return _run(employees.start_checklist, person, employee_id, body.kind, body.anchor)


@router.get("/calendar")
async def calendar(person: Person = Depends(current_person),
                   month: str = Query(default="", max_length=7)) -> dict:
    return employees.calendar(person, month)


@router.get("/absences")
async def absences(person: Person = Depends(current_person), start: str = Query(max_length=10),
                   end: str = Query(max_length=10), status: Literal["open", "pending", "all"] = "open") -> list[dict]:
    statuses = {"open": ("pending", "approved"), "pending": ("pending",),
                "all": ("pending", "approved", "rejected", "cancelled")}[status]
    return _run(employees.absences, person, start=start, end=end, statuses=statuses)


@router.post("/absences")
async def request_absence(body: AbsenceBody, person: Person = Depends(current_person)) -> dict:
    return _run(employees.request_absence, person, kind=body.kind, start=body.start, end=body.end,
                half_day=body.half_day, note=body.note, employee_id=body.employee_id)


@router.delete("/absences/{absence_id}")
async def cancel_absence(absence_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(employees.cancel_absence, person, absence_id)


@router.get("/checklists")
async def checklists(person: Person = Depends(current_person), mine: bool = False, closed: bool = False) -> list[dict]:
    return employees.checklists(person, open_only=not closed, mine=mine)


@router.post("/checklists/{checklist_id}/tasks")
async def add_task(body: TaskBody, checklist_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(employees.add_task, person, checklist_id, body.title, body.due_date, body.assignee_id)


@router.post("/checklists/{checklist_id}/close")
async def close_checklist(checklist_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    _run(employees.close_checklist, person, checklist_id)
    return {"closed": True}


@router.patch("/tasks/{task_id}")
async def update_task(body: TaskPatch, task_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    out = None
    if body.due_date is not None or body.assignee_id is not None:
        out = _run(employees.update_task, person, task_id, due_date=body.due_date, assignee_id=body.assignee_id)
    if body.done is not None:
        out = _run(employees.tick, person, task_id, body.done)
    if out is None:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    return out


@router.delete("/tasks/{task_id}")
async def remove_task(task_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    _run(employees.remove_task, person, task_id)
    return {"removed": True}


@router.get("/settings")
async def get_settings(person: Person = Depends(current_person)) -> dict:
    return employees.get_settings()


@router.put("/settings")
async def save_settings(body: SettingsBody, person: Person = Depends(current_person)) -> dict:
    return _run(employees.save_settings, person, body.settings)


# --- the approvals queue, for every area (CRM, billing, employees) ----------------------------------

@approvals_router.get("")
async def list_approvals(person: Person = Depends(current_person), area: str = Query(default="", max_length=20),
                         status: Literal["pending", "decided", "all"] = "pending") -> list[dict]:
    rows = approvals.list_for(person, status=status)
    return [a for a in rows if not area or a["area"] == area]


@approvals_router.post("/{approval_id}")
async def decide(body: Decision, approval_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return await approvals.decide(approval_id, person, body.approve, body.note)
    except approvals.ApprovalError as e:
        raise HTTPException(status_code=400, detail=str(e))


@approvals_router.delete("/{approval_id}")
async def cancel(approval_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(approvals.cancel, approval_id, person)

