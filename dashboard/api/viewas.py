"""Admins: preview the dashboard (and the web chat) as another role, to check permissions.

The session gets `view_as`; `routes.current_person` then applies that role's permissions to the
admin's own account. It can only ever lower permissions: only a real admin can switch, the
admin role itself can't be "previewed", and switching back always uses the real identity.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db
from orchestrator.identity import registry
from orchestrator.identity.registry import Person
from orchestrator.tools import registry as tools

from . import security
from .routes import real_person

router = APIRouter(prefix="/api/view-as")


class ViewAs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: str = Field(min_length=1, max_length=40)


def real_admin(person: Person = Depends(real_person)) -> Person:
    if not person.is_admin:
        raise HTTPException(status_code=403)
    return person


@router.get("")
async def roles(request: Request, person: Person = Depends(real_admin)) -> dict:
    rows = db.query("SELECT name, description, can_telegram, can_call, can_dashboard FROM roles WHERE name != 'admin' "
                    "ORDER BY name")
    out = []
    for r in rows:
        out.append({"name": r["name"], "description": r["description"],
                    "channels": [c for c, k in (("telegram", "can_telegram"), ("call", "can_call"),
                                                ("dashboard", "can_dashboard")) if r[k]],
                    "tools": sorted(t["name"] for t in tools.tools_for(r["name"], "telegram"))})
    return {"current": security.session_view_as(request), "roles": out}


@router.post("")
async def start(body: ViewAs, request: Request, person: Person = Depends(real_admin)) -> dict:
    if body.role == "admin" or not registry.role_exists(body.role):
        raise HTTPException(status_code=400, detail="Pick a role other than admin.")
    security.set_view_as(request, body.role)
    db.log_activity("access", f"{person.display_name} is viewing the dashboard as role '{body.role}'", user_id=person.id)
    return {"viewing_as": body.role}


@router.delete("")
async def stop(request: Request, person: Person = Depends(real_admin)) -> dict:
    security.set_view_as(request, None)
    db.log_activity("access", f"{person.display_name} is back to their own view", user_id=person.id)
    return {"viewing_as": None}
