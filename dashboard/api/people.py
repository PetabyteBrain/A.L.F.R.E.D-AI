"""User management (admin) and self-service profile (everyone with dashboard access).

Rules enforced here, not in the UI:
- Only people with people.manage (admins by default) can create/edit/disable/delete people, change
  their roles, or manage other people's connections and dashboard logins. Someone who isn't an admin
  can't change an admin, and can only give roles that grant nothing they lack (permissions.py).
- Everyone can edit their own name and phone/email/Telegram connections and change their own
  password (current password required).
- The last active admin can't be demoted, disabled or deleted; nobody can delete themselves or
  remove their own dashboard login.
- Any change to someone's roles, status, password or login revokes their dashboard sessions.
- New or changed phone/Telegram/email connections stay inactive until verified with a code
  (orchestrator/identity/verification.py); only admins can mark one verified by hand.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, permissions
from orchestrator.identity import mfa, registry, verification
from orchestrator.identity.registry import Person, RegistryError

from . import security
from .routes import current_person, requires

router = APIRouter(prefix="/api")

ConnectionType = Literal["phone", "email", "telegram"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _fail(e: RegistryError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _mask(id_type: str, value: str) -> str:
    """Values in the activity log are partially hidden."""
    if id_type == "email" and "@" in value:
        name, dom = value.split("@", 1)
        return f"{name[:2]}…@{dom}"
    return value[:5] + "…" + value[-2:] if len(value) > 8 else value


def _person_view(user_id: int) -> dict:
    row = db.query_one("SELECT id, display_name, role, active, notes, created_at FROM users WHERE id = ?", (user_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown person.")
    idents = db.query("SELECT id, type, value, verified_at, preferred FROM user_identifiers WHERE user_id = ? "
                      "ORDER BY type, id", (user_id,))
    primary = registry.email_for(user_id)
    row["active"] = bool(row["active"])
    row["connections"] = []
    for i in idents:
        if i["type"] == "dashboard":
            continue
        needs = i["type"] in registry.VERIFY_TYPES
        pend = verification.pending(i["id"]) if needs and not i["verified_at"] else None
        row["connections"].append({
            "id": i["id"], "type": i["type"], "value": i["value"],
            "needs_verification": needs, "verified": bool(i["verified_at"]) or not needs,
            "pending_until": pend["expires_at"] if pend else None,
            **({"primary": i["value"] == primary} if i["type"] == "email" else {}),
        })
    login = next((i for i in idents if i["type"] == "dashboard"), None)
    row["dashboard_username"] = login["value"] if login else None
    last = db.query_one("SELECT MAX(last_seen_at) AS t FROM dashboard_sessions WHERE user_id = ?", (user_id,))
    row["last_dashboard_seen"] = last["t"] if last else None
    row["mfa"] = mfa.status(user_id)["enabled"]
    row["roles"] = permissions.roles_of(user_id) or [row["role"]]
    row["group_roles"] = [r for r in permissions.group_roles_of(user_id) if r not in row["roles"]]
    row["groups"] = permissions.groups_of(user_id)
    perms = permissions.resolve(row["roles"] + row["group_roles"])
    row["channels"] = [c for c in ("telegram", "call", "email", "dashboard") if f"channel.{c}" in perms]
    row["is_admin"] = permissions.ADMIN in row["roles"]
    return row


def _guard_target(actor: Person, user_id: int) -> None:
    """Only an admin may change an admin."""
    if not actor.is_admin and permissions.is_admin_user(user_id):
        raise HTTPException(status_code=403, detail="Only an admin can change an admin.")


def _perm_fail(e: permissions.PermissionError_) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ============================================================================ admin

class PersonCreate(Strict):
    display_name: str = Field(min_length=1, max_length=80)
    role: str | None = Field(default=None, min_length=1, max_length=40)
    roles: list[str] | None = Field(default=None, max_length=20)          # several roles; first = primary
    notes: str = Field(default="", max_length=500)


class PersonUpdate(Strict):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, min_length=1, max_length=40)
    roles: list[str] | None = Field(default=None, max_length=20)
    active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


class ConnectionCreate(Strict):
    type: ConnectionType
    value: str = Field(min_length=1, max_length=254)


class ConnectionUpdate(Strict):
    value: str = Field(min_length=1, max_length=254)


class CodeCheck(Strict):
    code: str = Field(min_length=1, max_length=12)


class LoginSet(Strict):
    username: str = Field(min_length=3, max_length=32)
    password: str | None = Field(default=None, max_length=200)


@router.get("/roles")
async def roles(person: Person = Depends(requires("people.manage"))) -> list[dict]:
    """Roles to choose from on Personnel (active ones), with what each one opens up."""
    mine = permissions.for_person(person)
    out = []
    for r in permissions.role_rows():
        eff = set(r["effective"])
        out.append({"name": r["name"], "label": r["label"], "description": r["description"],
                    "channels": [c for c in ("telegram", "call", "email", "dashboard") if f"channel.{c}" in eff],
                    "assignable": person.is_admin or (r["name"] != permissions.ADMIN and eff <= mine)})
    return out


@router.get("/people")
async def list_people(person: Person = Depends(requires("people.manage"))) -> list[dict]:
    return [_person_view(r["id"]) for r in db.query("SELECT id FROM users ORDER BY active DESC, id")]


@router.post("/people")
async def create_person(body: PersonCreate, person: Person = Depends(requires("people.manage"))) -> dict:
    roles = [r for r in (body.roles or [body.role]) if r]
    if not roles:
        raise HTTPException(status_code=400, detail="Give the person at least one role.")
    actor = permissions.actor_for(person)
    try:
        for r in roles:
            if not registry.role_exists(r):
                raise RegistryError(f"Unknown role '{r}'.")
            if not actor.admin:
                permissions._check_role_cap(actor, r)
        uid = registry.add_user(body.display_name, roles[0], body.notes)
        if len(roles) > 1:
            permissions.set_person_roles(actor, uid, roles)
    except RegistryError as e:
        raise _fail(e)
    except permissions.PermissionError_ as e:
        raise _perm_fail(e)
    db.log_activity("system", f"Registry: {person.display_name} added {body.display_name} "
                              f"({', '.join(permissions.label_of(r) for r in roles)})", user_id=uid)
    return _person_view(uid)


@router.patch("/people/{user_id}")
async def update_person(body: PersonUpdate, request: Request, user_id: int = Path(ge=1),
                        person: Person = Depends(requires("people.manage"))) -> dict:
    _guard_target(person, user_id)
    roles = body.roles if body.roles is not None else ([body.role] if body.role else None)
    roles_changed = False
    try:
        if roles is not None:
            roles_changed = permissions.set_person_roles(permissions.actor_for(person), user_id, roles)
        result = registry.update_user(user_id, display_name=body.display_name, active=body.active, notes=body.notes)
    except RegistryError as e:
        raise _fail(e)
    except permissions.PermissionError_ as e:
        raise _perm_fail(e)
    if result["access_changed"] or roles_changed:
        security.revoke_user_sessions(user_id, keep=request if user_id == person.id else None)
    changes = ", ".join(k for k, v in body.model_dump(exclude_none=True).items())
    db.log_activity("system", f"Registry: {person.display_name} updated #{user_id} ({changes})", user_id=user_id)
    return _person_view(user_id)


@router.delete("/people/{user_id}")
async def delete_person(user_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    _guard_target(person, user_id)
    if user_id == person.id:
        raise HTTPException(status_code=400, detail="You can't delete yourself.")
    name = (db.query_one("SELECT display_name FROM users WHERE id = ?", (user_id,)) or {}).get("display_name", "?")
    try:
        registry.delete_user(user_id)
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"Registry: {person.display_name} deleted {name} (#{user_id})")
    return {"ok": True}


@router.post("/people/{user_id}/connections")
async def admin_add_connection(body: ConnectionCreate, user_id: int = Path(ge=1),
                               person: Person = Depends(requires("people.manage"))) -> dict:
    _person_view(user_id)  # 404 if unknown
    _guard_target(person, user_id)
    try:
        registry.add_identifier(user_id, body.type, body.value)
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"Registry: {person.display_name} linked {body.type} to #{user_id}", user_id=user_id)
    return _person_view(user_id)


def _editable_connection(identifier_id: int, owner: int | None, actor: Person | None = None) -> dict:
    ident = registry.get_identifier(identifier_id)
    if not ident or ident["type"] == "dashboard" or (owner is not None and ident["user_id"] != owner):
        raise HTTPException(status_code=404, detail="Unknown connection.")
    if actor is not None:
        _guard_target(actor, ident["user_id"])
    return ident


@router.patch("/connections/{identifier_id}")
async def admin_update_connection(body: ConnectionUpdate, identifier_id: int = Path(ge=1),
                                  person: Person = Depends(requires("people.manage"))) -> dict:
    ident = _editable_connection(identifier_id, None, person)
    try:
        registry.update_identifier(identifier_id, body.value)
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"Registry: {person.display_name} changed a {ident['type']} of #{ident['user_id']}",
                    user_id=ident["user_id"])
    return _person_view(ident["user_id"])


@router.delete("/connections/{identifier_id}")
async def admin_delete_connection(identifier_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    ident = _editable_connection(identifier_id, None, person)
    registry.delete_identifier(identifier_id)
    db.log_activity("system", f"Registry: {person.display_name} removed a {ident['type']} from #{ident['user_id']}",
                    user_id=ident["user_id"])
    return _person_view(ident["user_id"])


async def _start_verification(ident: dict, person: Person) -> dict:
    try:
        return await verification.start(ident["id"], person)
    except RegistryError as e:
        raise _fail(e)


def _check_code(ident: dict, code: str) -> dict:
    try:
        verification.check(ident["id"], code)
    except RegistryError as e:
        raise _fail(e)
    return {"ok": True}


@router.post("/connections/{identifier_id}/verify")
async def admin_start_verification(identifier_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    return await _start_verification(_editable_connection(identifier_id, None, person), person)


@router.post("/connections/{identifier_id}/verify/check")
async def admin_check_code(body: CodeCheck, identifier_id: int = Path(ge=1),
                           person: Person = Depends(requires("people.manage"))) -> dict:
    return _check_code(_editable_connection(identifier_id, None, person), body.code)


def _make_primary(ident: dict, person: Person) -> None:
    try:
        registry.set_preferred_email(ident["id"])
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"{person.display_name} made {_mask('email', ident['value'])} the primary email of "
                    f"#{ident['user_id']}", user_id=ident["user_id"])


@router.post("/connections/{identifier_id}/primary")
async def admin_make_primary(identifier_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    ident = _editable_connection(identifier_id, None, person)
    _make_primary(ident, person)
    return _person_view(ident["user_id"])


@router.post("/connections/{identifier_id}/mark-verified")
async def admin_mark_verified(identifier_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    """Admin override, e.g. for a number the admin knows personally. Logged."""
    ident = _editable_connection(identifier_id, None, person)
    registry.mark_verified(identifier_id)
    db.log_activity("access", f"Registry: {person.display_name} marked a {ident['type']} of #{ident['user_id']} "
                    "verified without a code", user_id=ident["user_id"])
    return _person_view(ident["user_id"])


@router.put("/people/{user_id}/login")
async def admin_set_login(body: LoginSet, request: Request, user_id: int = Path(ge=1),
                          person: Person = Depends(requires("people.manage"))) -> dict:
    _guard_target(person, user_id)
    target = _person_view(user_id)
    if "dashboard" not in target["channels"]:
        raise HTTPException(status_code=400, detail="This person's roles have no dashboard access.")
    try:
        registry.set_dashboard_login(user_id, body.username, body.password or None)
    except RegistryError as e:
        raise _fail(e)
    if body.password:
        # Keep the admin's own current session if they reset their own password here.
        security.revoke_user_sessions(user_id, keep=request if user_id == person.id else None)
    db.log_activity("access", f"Registry: {person.display_name} set the dashboard login of #{user_id}"
                    + (" (new password)" if body.password else ""), user_id=user_id)
    return _person_view(user_id)


@router.delete("/people/{user_id}/login")
async def admin_remove_login(user_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    _guard_target(person, user_id)
    if user_id == person.id:
        raise HTTPException(status_code=400, detail="You can't remove your own dashboard login.")
    db.execute("DELETE FROM user_identifiers WHERE user_id = ? AND type = 'dashboard'", (user_id,))
    security.revoke_user_sessions(user_id)
    db.log_activity("access", f"Registry: {person.display_name} removed the dashboard login of #{user_id}",
                    user_id=user_id)
    return _person_view(user_id)


# ============================================================================ self-service

class ProfileUpdate(Strict):
    display_name: str = Field(min_length=1, max_length=80)


class PasswordChange(Strict):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


@router.get("/profile")
async def get_profile(person: Person = Depends(current_person)) -> dict:
    view = _person_view(person.id)
    view.pop("notes", None)  # admin-only notes
    return view


@router.patch("/profile")
async def update_profile(body: ProfileUpdate, person: Person = Depends(current_person)) -> dict:
    try:
        registry.update_user(person.id, display_name=body.display_name)
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"Profile: {person.display_name} renamed themselves to {body.display_name}",
                    user_id=person.id)
    return await get_profile(person)


@router.post("/profile/connections")
async def add_own_connection(body: ConnectionCreate, person: Person = Depends(current_person)) -> dict:
    try:
        registry.add_identifier(person.id, body.type, body.value)
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"Profile: {person.display_name} added {body.type} "
                    f"{_mask(body.type, registry.normalize(body.type, body.value))}", user_id=person.id)
    return await get_profile(person)


@router.patch("/profile/connections/{identifier_id}")
async def update_own_connection(body: ConnectionUpdate, identifier_id: int = Path(ge=1),
                                person: Person = Depends(current_person)) -> dict:
    ident = _editable_connection(identifier_id, person.id)
    try:
        new = registry.update_identifier(identifier_id, body.value)
    except RegistryError as e:
        raise _fail(e)
    db.log_activity("system", f"Profile: {person.display_name} changed {ident['type']} to "
                    f"{_mask(ident['type'], new['value'])}", user_id=person.id)
    return await get_profile(person)


@router.delete("/profile/connections/{identifier_id}")
async def delete_own_connection(identifier_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    ident = _editable_connection(identifier_id, person.id)
    registry.delete_identifier(identifier_id)
    db.log_activity("system", f"Profile: {person.display_name} removed a {ident['type']}", user_id=person.id)
    return await get_profile(person)


@router.post("/profile/connections/{identifier_id}/verify")
async def start_own_verification(identifier_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return await _start_verification(_editable_connection(identifier_id, person.id), person)


@router.post("/profile/connections/{identifier_id}/verify/check")
async def check_own_code(body: CodeCheck, identifier_id: int = Path(ge=1),
                         person: Person = Depends(current_person)) -> dict:
    return _check_code(_editable_connection(identifier_id, person.id), body.code)


@router.post("/profile/connections/{identifier_id}/primary")
async def make_own_primary(identifier_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    _make_primary(_editable_connection(identifier_id, person.id), person)
    return await get_profile(person)


@router.post("/profile/password")
async def change_own_password(body: PasswordChange, request: Request,
                              person: Person = Depends(current_person)) -> dict:
    key = f"pw:{person.id}"
    if security.user_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    if not registry.check_password(person.id, body.current_password):
        security.user_throttle.fail(key)
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    security.user_throttle.reset(key)
    try:
        registry.validate_password(body.new_password)
        if body.new_password == body.current_password:
            raise RegistryError("The new password must be different.")
        login = db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND type = 'dashboard'", (person.id,))
        registry.set_dashboard_login(person.id, login["value"], body.new_password)
    except RegistryError as e:
        raise _fail(e)
    security.revoke_user_sessions(person.id, keep=request)  # sign out everywhere else
    db.log_activity("access", f"Profile: {person.display_name} changed their password", user_id=person.id)
    return {"ok": True}
