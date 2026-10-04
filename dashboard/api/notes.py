"""Notes, lists and the Company to-do on the dashboard (#37). Same rules as the chat tools: the
scope checks live in orchestrator/nas.py (personal = own folder, company = admins/cofounders)."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import nas, company_todo
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/nas")
Scope = Literal["personal", "company"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteBody(Strict):
    scope: Scope
    title: str = Field(min_length=1, max_length=200)
    folder: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=nas.MAX_WRITE_CHARS)
    mode: Literal["create", "replace", "append"] = "create"


class MoveBody(Strict):
    scope: Scope
    path: str = Field(min_length=1, max_length=300)
    new_path: str = Field(min_length=1, max_length=300)


class ListBody(Strict):
    scope: Scope
    name: str = Field(min_length=1, max_length=80)
    op: Literal["create", "add", "check", "uncheck", "remove", "clear_done"]
    items: list[str] = Field(default_factory=list, max_length=50)


class TodoBody(Strict):
    action: Literal["add", "update", "move"]
    description: str = Field(default="", max_length=300)
    todo: str = Field(default="", max_length=300)
    importance: int | None = Field(default=None, ge=1, le=5)
    section: Literal["current", "future"] = "current"
    status: Literal["not started", "in progress", "done"] | None = None
    to: Literal["current", "future"] | None = None


async def _run(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except nas.NasError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def status(person: Person = Depends(current_person)) -> dict:
    return {"enabled": nas.enabled(),
            "scopes": ["personal"] + (["company"] if person.role in nas.COMPANY_ROLES else []),
            "company_todo": person.role in nas.COMPANY_ROLES}


@router.get("/notes")
async def notes(scope: Scope, folder: str = Query(default="", max_length=200),
                person: Person = Depends(current_person)) -> dict:
    return await _run(nas.list_notes, person, scope, folder)


@router.get("/note")
async def note(scope: Scope, path: str = Query(max_length=300), person: Person = Depends(current_person)) -> dict:
    return await _run(nas.read_file, person, scope, nas._note_path(path).replace("\\", "/"))


@router.post("/note")
async def save(body: NoteBody, person: Person = Depends(current_person)) -> dict:
    return await _run(nas.save_note, person, body.scope, body.title, body.content, body.folder, body.mode)


@router.post("/note/move")
async def move(body: MoveBody, person: Person = Depends(current_person)) -> dict:
    return await _run(nas.move_note, person, body.scope, body.path, body.new_path)


@router.delete("/note")
async def trash(scope: Scope, path: str = Query(max_length=300), person: Person = Depends(current_person)) -> dict:
    return await _run(nas.trash_note, person, scope, path)


@router.get("/lists")
async def lists(scope: Scope, person: Person = Depends(current_person)) -> dict:
    return await _run(nas.all_lists, person, scope)


@router.get("/list")
async def show_list(scope: Scope, name: str = Query(max_length=80), person: Person = Depends(current_person)) -> dict:
    return await _run(nas.list_op, person, scope, name, "show", None)


@router.post("/list")
async def change_list(body: ListBody, person: Person = Depends(current_person)) -> dict:
    return await _run(nas.list_op, person, body.scope, body.name, body.op, body.items)


@router.get("/company-todo")
async def todo(person: Person = Depends(current_person)) -> dict:
    return await _run(company_todo.show, person)


@router.post("/company-todo")
async def change_todo(body: TodoBody, person: Person = Depends(current_person)) -> dict:
    if body.action == "add":
        return await _run(company_todo.add, person, body.description, body.importance or 3, body.section)
    if body.action == "update":
        return await _run(company_todo.update, person, body.todo, body.status, body.importance)
    return await _run(company_todo.move, person, body.todo, body.to or "current")
