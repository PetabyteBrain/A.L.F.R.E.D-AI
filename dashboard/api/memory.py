"""The signed-in person's long-term memory notes: view, add, edit, delete (own notes only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import memory
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/memory")


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=memory.MAX_CHARS)


def _fail(e: memory.MemoryNoteError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_notes(person: Person = Depends(current_person)) -> list[dict]:
    return memory.list_for(person.id)


@router.post("")
async def add_note(body: Note, person: Person = Depends(current_person)) -> list[dict]:
    try:
        memory.add(person, body.text)
    except memory.MemoryNoteError as e:
        raise _fail(e)
    return memory.list_for(person.id)


@router.patch("/{memory_id}")
async def edit_note(body: Note, memory_id: int = Path(ge=1), person: Person = Depends(current_person)) -> list[dict]:
    try:
        memory.update(memory_id, person, body.text)
    except memory.MemoryNoteError as e:
        raise _fail(e)
    return memory.list_for(person.id)


@router.delete("/{memory_id}")
async def delete_note(memory_id: int = Path(ge=1), person: Person = Depends(current_person)) -> list[dict]:
    try:
        memory.delete(memory_id, person)
    except memory.MemoryNoteError as e:
        raise _fail(e)
    return memory.list_for(person.id)
