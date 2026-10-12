"""Preview the dashboard (and the web chat) as a role or as a person, to check permissions.

The session gets `view_as` (a role name, or "user:<id>"); `routes.current_person` then applies that
role's or person's permissions to the previewer's own account, capped to the previewer's own
permissions, so it can only ever lower them. Open to everyone with roles.manage; switching back
always uses the real identity.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, permissions
from orchestrator.identity import registry
from orchestrator.identity.registry import Person
from orchestrator.tools import registry as tools

from . import security
from .routes import preview, real_person

router = APIRouter(prefix="/api/view-as")


class ViewAs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: str | None = Field(default=None, min_length=1, max_length=40)
    user_id: int | None = Field(default=None, ge=1)


def real_manager(person: Person = Depends(real_person)) -> Person:
    if not person.can("roles.manage"):
        raise HTTPException(status_code=403)
    return person


def _summary(perms: frozenset) -> dict:
    return {"channels": [c for c in ("telegram", "call", "email", "dashboard") if f"channel.{c}" in perms],
            "tools": sorted(t["name"] for t in tools.tools_for(_probe(perms), "telegram"))}


def _probe(perms: frozenset) -> Person:
    return Person(id=0, display_name="", role="", perms=perms)


@router.get("")
async def options(request: Request, person: Person = Depends(real_manager)) -> dict:
    mine = permissions.for_person(person)
    roles = [{"name": r["name"], "label": r["label"], "description": r["description"],
              **_summary(frozenset(r["effective"]) & mine)}
             for r in permissions.role_rows() if r["name"] != permissions.ADMIN]
    people = [{"id": u["id"], "display_name": u["display_name"]}
              for u in db.query("SELECT id, display_name FROM users WHERE active = 1 AND id != ? ORDER BY display_name",
                                (person.id,))]
    return {"current": security.session_view_as(request), "roles": roles, "people": people}


@router.post("")
async def start(body: ViewAs, request: Request, person: Person = Depends(real_manager)) -> dict:
    if (body.role is None) == (body.user_id is None):
        raise HTTPException(status_code=400, detail="Pick a role or a person.")
    if body.role is not None and (body.role == permissions.ADMIN or not registry.role_exists(body.role)):
        raise HTTPException(status_code=400, detail="Pick a role other than admin.")
    key = body.role if body.role is not None else f"user:{body.user_id}"
    shown = preview(person, key)
    if shown is None:
        raise HTTPException(status_code=400, detail="That can't be previewed.")
    security.set_view_as(request, key)
    what = f"role '{permissions.label_of(body.role)}'" if body.role else f"{registry.get_person(body.user_id).display_name}"
    db.log_activity("access", f"{person.display_name} is viewing the dashboard as {what}", user_id=person.id)
    return {"viewing_as": key}


@router.delete("")
async def stop(request: Request, person: Person = Depends(real_manager)) -> dict:
    security.set_view_as(request, None)
    db.log_activity("access", f"{person.display_name} is back to their own view", user_id=person.id)
    return {"viewing_as": None}
