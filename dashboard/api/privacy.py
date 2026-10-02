"""Data export & deletion (orchestrator/privacy.py) and cost budgets (orchestrator/budgets.py).

- Everyone: download their own data; ask for their history or account to be deleted (password
  required; an admin confirms on People and is told on Telegram).
- Admins: confirm/reject requests, export or delete a person's data directly (for requests
  that arrive by email; password required), and set the monthly budgets.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import budgets, db, privacy
from orchestrator.config import settings
from orchestrator.identity import registry
from orchestrator.identity.registry import Person

from . import security
from .routes import admin_only, current_person

router = APIRouter(prefix="/api")

export_throttle = security.Throttle(window_s=3600, max_failures=5)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeletionRequest(Strict):
    scope: Literal["history", "account"]
    note: str = Field(default="", max_length=300)
    password: str = Field(min_length=1, max_length=200)


class Decision(Strict):
    approve: bool


class Erase(Strict):
    scope: Literal["history", "account"]
    password: str = Field(min_length=1, max_length=200)


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anthropic: float | None = Field(default=None, gt=0, le=100_000)
    twilio: float | None = Field(default=None, gt=0, le=100_000)
    elevenlabs: float | None = Field(default=None, gt=0, le=100_000)
    total: float | None = Field(default=None, gt=0, le=100_000)


def _password(person: Person, password: str) -> None:
    key = f"pw:{person.id}"
    if security.user_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    if not registry.check_password(person.id, password):
        security.user_throttle.fail(key)
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    security.user_throttle.reset(key)


def _zip(user_id: int, who: Person) -> Response:
    key = f"export:{who.id}"
    if export_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many exports in the last hour. Try again later.")
    export_throttle.fail(key)
    try:
        data = privacy.export_zip(user_id)
    except privacy.PrivacyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    name = f"alfred-data-{user_id}-{db.now_iso()[:10]}.zip"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def _tell_admins(text: str) -> None:
    from orchestrator import telegram_handler
    for admin in db.query("SELECT id FROM users WHERE role = 'admin' AND active = 1"):
        try:
            await telegram_handler.send_to_user(admin["id"], text, note="privacy request")
        except Exception:  # noqa: BLE001 — the request is on the People tab either way
            pass


# --- self-service ------------------------------------------------------------------------

@router.get("/profile/data/export")
async def export_own(person: Person = Depends(current_person)) -> Response:
    return _zip(person.id, person)


@router.get("/profile/data/request")
async def own_request(person: Person = Depends(current_person)) -> dict:
    return {"pending": privacy.pending_for(person.id), "retention_days": settings.recording_retention_days}


@router.post("/profile/data/request")
async def request_deletion(body: DeletionRequest, person: Person = Depends(current_person)) -> dict:
    _password(person, body.password)
    try:
        pending = privacy.request_deletion(person, body.scope, body.note)
    except privacy.PrivacyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    what = "their whole account" if body.scope == "account" else "their conversation history"
    await _tell_admins(f"🗑️ {person.display_name} asked to delete {what}. Confirm or reject on the dashboard, "
                       "People tab.")
    return {"pending": pending}


@router.delete("/profile/data/request")
async def cancel_request(person: Person = Depends(current_person)) -> dict:
    privacy.cancel(person)
    return {"pending": None}


# --- admin -------------------------------------------------------------------------------

@router.get("/data-requests")
async def list_requests(person: Person = Depends(admin_only)) -> list[dict]:
    return privacy.pending_all()


@router.post("/data-requests/{request_id}")
async def decide(body: Decision, request_id: int = Path(ge=1), person: Person = Depends(admin_only)) -> dict:
    try:
        return await privacy.decide(request_id, person, body.approve)
    except privacy.PrivacyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/people/{user_id}/export")
async def export_person(user_id: int = Path(ge=1), person: Person = Depends(admin_only)) -> Response:
    return _zip(user_id, person)


@router.post("/people/{user_id}/erase")
async def erase_person(body: Erase, user_id: int = Path(ge=1),
                       person: Person = Depends(admin_only)) -> dict:
    _password(person, body.password)
    if user_id == person.id and body.scope == "account":
        raise HTTPException(status_code=400, detail="Delete your own account through a request on Profile.")
    try:
        summary = await privacy.erase(user_id, body.scope, by=person)
    except privacy.PrivacyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.execute("DELETE FROM data_requests WHERE user_id = ?", (user_id,))
    return {"ok": True, "summary": summary}


@router.get("/budgets")
async def get_budgets(person: Person = Depends(admin_only)) -> dict:
    return await budgets.status()


@router.put("/budgets")
async def put_budgets(body: Budgets, person: Person = Depends(admin_only)) -> dict:
    budgets.set_budgets(body.model_dump())
    db.log_activity("system", f"Usage: {person.display_name} changed the monthly budgets", user_id=person.id)
    await budgets.check(force=True)
    return await budgets.status()
