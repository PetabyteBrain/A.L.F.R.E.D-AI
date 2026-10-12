"""Two-factor sign-in: second login step, self-service set-up on Profile, admin reset.

The logic lives in orchestrator/identity/mfa.py. Switching a method off, or getting new backup
codes, needs the current password; switching one on needs a working code (app) or a verified
Telegram connection. Any change signs the person out on their other devices.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db
from orchestrator.identity import mfa, registry
from orchestrator.identity.registry import Person, RegistryError

from . import security
from .people import _person_view
from .routes import _me_view, current_person, requires

router = APIRouter(prefix="/api")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChallengeBody(Strict):
    challenge: str = Field(min_length=10, max_length=100)


class CodeBody(ChallengeBody):
    code: str = Field(min_length=1, max_length=20)


class PasswordBody(Strict):
    password: str = Field(min_length=1, max_length=200)


class ConfirmBody(Strict):
    code: str = Field(min_length=1, max_length=20)


class TelegramBody(Strict):
    enabled: bool
    password: str | None = Field(default=None, max_length=200)


def _blocked(request: Request, user_key: str) -> None:
    if security.ip_throttle.blocked(security.client_ip(request)) or security.user_throttle.blocked(user_key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")


# --- sign-in, step two -------------------------------------------------------------------

@router.post("/login/2fa")
async def login_second_step(body: CodeBody, request: Request, response: Response) -> dict:
    ip = security.client_ip(request)
    try:
        uid = mfa.challenge_user(body.challenge)
    except RegistryError as e:
        raise HTTPException(status_code=401, detail=str(e))
    key = f"2fa:{uid}"
    _blocked(request, key)
    try:
        mfa.verify(body.challenge, body.code)
    except RegistryError as e:
        security.ip_throttle.fail(ip)
        security.user_throttle.fail(key)
        db.log_activity("access", f"Dashboard sign-in: wrong second-factor code from {ip}", user_id=uid)
        raise HTTPException(status_code=401, detail=str(e))
    person = registry.get_person(uid)
    if person is None or not registry.allows_channel(person, "dashboard"):
        raise HTTPException(status_code=401, detail="Access denied")
    security.ip_throttle.reset(ip)
    security.user_throttle.reset(key)
    security.destroy_session(request)
    security.set_session_cookie(response, security.create_session(person.id, request))
    left = mfa.status(uid)["backup_codes_left"]
    db.log_activity("access", f"Dashboard login from {ip} (two-factor)", user_id=person.id)
    return {**_me_view(person), "backup_codes_left": left}


@router.post("/login/2fa/telegram")
async def login_send_telegram(body: ChallengeBody, request: Request) -> dict:
    _blocked(request, "tg-send")
    try:
        await mfa.send_telegram_code(body.challenge)
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# --- self-service ------------------------------------------------------------------------

def _view(person: Person) -> dict:
    return {**mfa.status(person.id), "required": mfa.required_for(person)}


def _check_password(person: Person, password: str | None) -> None:
    key = f"pw:{person.id}"
    if security.user_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    if not password or not registry.check_password(person.id, password):
        security.user_throttle.fail(key)
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    security.user_throttle.reset(key)


def _guard_last_method(person: Person, removing: str) -> None:
    st = mfa.status(person.id)
    others = (st["totp"] and removing != "totp") or (st["telegram"] and removing != "telegram")
    if mfa.required_for(person) and not others:
        raise HTTPException(status_code=400, detail=f"Your role ({person.role}) requires two-factor sign-in: "
                                                    "switch the other method on first.")


def _changed(person: Person, request: Request, what: str, codes: list[str] | None = None) -> dict:
    mfa.after_change(person.id)
    security.revoke_user_sessions(person.id, keep=request)
    db.log_activity("access", f"Profile: {person.display_name} {what}", user_id=person.id)
    return {**_view(person), "backup_codes": codes}


@router.get("/profile/2fa")
async def get_2fa(person: Person = Depends(current_person)) -> dict:
    return _view(person)


@router.post("/profile/2fa/totp/start")
async def totp_start(person: Person = Depends(current_person)) -> dict:
    if mfa.status(person.id)["totp"]:
        raise HTTPException(status_code=400, detail="The authenticator app is already active.")
    login = db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND type = 'dashboard'", (person.id,))
    try:
        return mfa.start_totp(person.id, login["value"] if login else person.display_name)
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/profile/2fa/totp/confirm")
async def totp_confirm(body: ConfirmBody, request: Request, person: Person = Depends(current_person)) -> dict:
    key = f"2fa-setup:{person.id}"
    if security.user_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    try:
        codes = mfa.confirm_totp(person.id, body.code)
    except RegistryError as e:
        security.user_throttle.fail(key)
        raise HTTPException(status_code=400, detail=str(e))
    return _changed(person, request, "switched on an authenticator app for sign-in", codes)


@router.post("/profile/2fa/totp/disable")
async def totp_disable(body: PasswordBody, request: Request, person: Person = Depends(current_person)) -> dict:
    _check_password(person, body.password)
    _guard_last_method(person, "totp")
    mfa.disable_totp(person.id)
    return _changed(person, request, "removed the authenticator app from sign-in")


@router.post("/profile/2fa/telegram")
async def telegram_toggle(body: TelegramBody, request: Request, person: Person = Depends(current_person)) -> dict:
    if not body.enabled:
        _check_password(person, body.password)
        _guard_last_method(person, "telegram")
    try:
        codes = mfa.set_telegram(person.id, body.enabled)
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _changed(person, request, f"switched Telegram sign-in codes {'on' if body.enabled else 'off'}", codes)


@router.post("/profile/2fa/backup-codes")
async def backup_codes(body: PasswordBody, request: Request, person: Person = Depends(current_person)) -> dict:
    _check_password(person, body.password)
    if not mfa.enabled(person.id):
        raise HTTPException(status_code=400, detail="Switch two-factor sign-in on first.")
    codes = mfa.new_backup_codes(person.id)
    db.log_activity("access", f"Profile: {person.display_name} generated new backup codes", user_id=person.id)
    return {**_view(person), "backup_codes": codes}


# --- admin -------------------------------------------------------------------------------

@router.post("/people/{user_id}/2fa/reset")
async def admin_reset(user_id: int = Path(ge=1), person: Person = Depends(requires("people.manage"))) -> dict:
    if user_id == person.id:
        raise HTTPException(status_code=400, detail="Manage your own two-factor sign-in on your Profile.")
    _person_view(user_id)  # 404 for unknown people
    from orchestrator import permissions
    if not person.is_admin and permissions.is_admin_user(user_id):
        raise HTTPException(status_code=403, detail="Only an admin can change an admin.")
    mfa.reset(user_id)
    security.revoke_user_sessions(user_id)
    db.log_activity("access", f"Registry: {person.display_name} reset the two-factor sign-in of #{user_id}",
                    user_id=user_id)
    return _person_view(user_id)
