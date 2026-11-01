"""Global settings on the dashboard's Settings tab (settings.manage): the AI model choice and the business features
switch (admins only). Roles and permissions are in access.py; household quiet hours and call screening keep their
own endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.identity.registry import Person

from .routes import requires

router = APIRouter(prefix="/api/settings")


class AiChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str | None = Field(default=None, max_length=20)
    model: str | None = Field(default=None, max_length=40)
    effort: str | None = Field(default=None, max_length=10)
    call_model: str | None = Field(default=None, max_length=40)


@router.get("/ai")
async def get_ai(person: Person = Depends(requires("settings.manage"))) -> dict:
    from orchestrator import ai_config
    return ai_config.describe()


@router.put("/ai")
async def put_ai(body: AiChoice, person: Person = Depends(requires("settings.manage"))) -> dict:
    from orchestrator import ai_config
    try:
        ai_config.update(body.model_dump(exclude_none=True), actor=person.display_name)
    except ai_config.AiConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ai_config.describe()


class Features(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business: bool


@router.get("/features")
async def get_features(person: Person = Depends(requires("settings.manage"))) -> dict:
    from orchestrator import permissions
    return {"business": permissions.business_enabled(), "can_change": person.is_admin}


@router.put("/features")
async def put_features(body: Features, person: Person = Depends(requires("settings.manage"))) -> dict:
    """Business features on/off for everyone: admins only (not a role permission, so nobody can be given it)."""
    from orchestrator import db, permissions
    if not person.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can switch the business features on or off.")
    if body.business != permissions.business_enabled():
        permissions.set_business_enabled(body.business)
        db.log_activity("access", f"{person.display_name} turned the business features "
                        f"(CRM, pipeline, boards, team) {'on' if body.business else 'off'}", user_id=person.id)
    return {"business": permissions.business_enabled(), "can_change": True}
