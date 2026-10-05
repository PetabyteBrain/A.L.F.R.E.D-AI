"""Admins: global settings on the dashboard's Settings tab. Switchable role permissions live here
(orchestrator/permissions.py); household quiet hours and call screening keep their own endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import permissions
from orchestrator.identity.registry import Person

from .routes import admin_only

router = APIRouter(prefix="/api/settings")


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: str = Field(min_length=1, max_length=40)
    permission: str = Field(min_length=1, max_length=40)
    allowed: bool


@router.get("/role-permissions")
async def get_permissions(person: Person = Depends(admin_only)) -> dict:
    return {"roles": permissions.all_settings(), "labels": permissions.LABELS}


@router.put("/role-permissions")
async def put_permission(body: Switch, person: Person = Depends(admin_only)) -> dict:
    try:
        roles = permissions.set_permission(body.role, body.permission, body.allowed, actor=person.display_name)
    except permissions.PermissionError_ as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"roles": roles, "labels": permissions.LABELS}
