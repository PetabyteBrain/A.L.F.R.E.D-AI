"""Meeting proposals on the dashboard (list + withdraw); scheduling itself happens through Alfred."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from orchestrator import meetings
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/meetings")


@router.get("")
async def list_meetings(include_done: bool = Query(default=False), person: Person = Depends(current_person)) -> list[dict]:
    return meetings.list_for(person, include_done)


@router.delete("/{meeting_id}")
async def cancel_meeting(meeting_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return await meetings.cancel(meeting_id, person)
    except meetings.MeetingError as e:
        raise HTTPException(status_code=400, detail=str(e))
