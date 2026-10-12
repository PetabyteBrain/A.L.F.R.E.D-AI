"""Roles, permissions and groups (Settings → Roles), for people with roles.manage (admins and cofounders by
default). Every rule lives in orchestrator/permissions.py: editing is capped to what the editor holds
themselves, the admin role is fixed, and every change is logged. Changing a person's roles or groups ends
their dashboard sessions, so the new permissions apply from their next sign-in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, permissions
from orchestrator.identity.registry import Person

from . import security
from .routes import requires

router = APIRouter(prefix="/api/access")
_manage = requires("roles.manage")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoleCreate(Strict):
    label: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=200)
    copy_from: str | None = Field(default=None, max_length=40)
    parent: str | None = Field(default=None, max_length=40)


class RoleUpdate(Strict):
    label: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=200)
    parent: str | None = Field(default="", max_length=40)        # "" = unchanged, null = no parent
    archived: bool | None = None


class Rule(Strict):
    permission: str = Field(min_length=1, max_length=40)
    effect: str | None = Field(default=None, pattern="^(allow|deny)$")   # null = not set


class GroupBody(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=200)


class Members(Strict):
    user_ids: list[int] = Field(max_length=500)


class GroupRoles(Strict):
    roles: list[str] = Field(max_length=20)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except permissions.PermissionError_ as e:
        raise HTTPException(status_code=400, detail=str(e))


def _revoke(user_ids, request: Request, actor: Person) -> None:
    for uid in user_ids:
        security.revoke_user_sessions(uid, keep=request if uid == actor.id else None)


def _people() -> list[dict]:
    out = []
    for u in db.query("SELECT id, display_name, active FROM users ORDER BY active DESC, display_name"):
        out.append({"id": u["id"], "display_name": u["display_name"], "active": bool(u["active"]),
                    "roles": permissions.roles_of(u["id"]), "group_roles": permissions.group_roles_of(u["id"]),
                    "groups": [g["id"] for g in permissions.groups_of(u["id"])]})
    return out


def _state(person: Person) -> dict:
    return {"catalogue": permissions.CATALOGUE, "roles": permissions.role_rows(include_archived=True),
            "groups": permissions.groups(), "people": _people(),
            "mine": sorted(permissions.for_person(person)), "admin": person.is_admin}


@router.get("")
async def state(person: Person = Depends(_manage)) -> dict:
    return _state(person)


@router.post("/roles")
async def create_role(body: RoleCreate, person: Person = Depends(_manage)) -> dict:
    name = _run(permissions.create_role, permissions.actor_for(person), body.label, description=body.description,
                copy_from=body.copy_from or None, parent=body.parent or None)
    return {**_state(person), "created": name}


@router.patch("/roles/{name}")
async def update_role(body: RoleUpdate, name: str = Path(max_length=40), person: Person = Depends(_manage)) -> dict:
    _run(permissions.update_role, permissions.actor_for(person), name, label=body.label,
         description=body.description, parent=body.parent, archived=body.archived)
    return _state(person)


@router.put("/roles/{name}/rules")
async def set_rule(body: Rule, name: str = Path(max_length=40), person: Person = Depends(_manage)) -> dict:
    _run(permissions.set_rule, permissions.actor_for(person), name, body.permission, body.effect)
    return _state(person)


@router.post("/groups")
async def create_group(body: GroupBody, person: Person = Depends(_manage)) -> dict:
    _run(permissions.create_group, permissions.actor_for(person), body.name or "", body.description or "")
    return _state(person)


@router.patch("/groups/{group_id}")
async def update_group(body: GroupBody, group_id: int = Path(ge=1), person: Person = Depends(_manage)) -> dict:
    _run(permissions.update_group, permissions.actor_for(person), group_id, name=body.name,
         description=body.description)
    return _state(person)


@router.delete("/groups/{group_id}")
async def delete_group(request: Request, group_id: int = Path(ge=1), person: Person = Depends(_manage)) -> dict:
    had_roles = bool(db.query_one("SELECT 1 FROM group_roles WHERE group_id = ?", (group_id,)))
    members = _run(permissions.delete_group, permissions.actor_for(person), group_id)
    if had_roles:
        _revoke(members, request, person)
    return _state(person)


@router.put("/groups/{group_id}/members")
async def set_members(body: Members, request: Request, group_id: int = Path(ge=1),
                      person: Person = Depends(_manage)) -> dict:
    changed = _run(permissions.set_group_members, permissions.actor_for(person), group_id, body.user_ids)
    _revoke(changed, request, person)
    return _state(person)


@router.put("/groups/{group_id}/roles")
async def set_group_roles(body: GroupRoles, request: Request, group_id: int = Path(ge=1),
                          person: Person = Depends(_manage)) -> dict:
    members = _run(permissions.set_group_roles, permissions.actor_for(person), group_id, body.roles)
    _revoke(members, request, person)
    return _state(person)


@router.get("/explain/person/{user_id}")
async def explain_person(user_id: int = Path(ge=1), person: Person = Depends(_manage)) -> dict:
    if not db.query_one("SELECT 1 FROM users WHERE id = ?", (user_id,)):
        raise HTTPException(status_code=404)
    return permissions.explain_person(user_id)


@router.get("/explain/permission/{key}")
async def explain_permission(key: str = Path(max_length=40), person: Person = Depends(_manage)) -> list[dict]:
    return [{"id": p["id"], "display_name": p["display_name"]}
            for p in _run(permissions.explain_permission, key)]
