"""Global settings on the dashboard's Settings tab (settings.manage): the AI model choice. Roles and
permissions are in access.py; household quiet hours and call screening keep their own endpoints."""
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
