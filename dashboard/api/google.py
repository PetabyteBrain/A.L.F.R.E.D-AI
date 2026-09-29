"""Google Calendar linking endpoints (every dashboard user links their own calendar)."""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from orchestrator import db, google_calendar
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/google")


@router.get("/status")
async def google_status(person: Person = Depends(current_person)) -> dict:
    return google_calendar.status(person.id)


@router.post("/connect")
async def google_connect(person: Person = Depends(current_person)) -> dict:
    """Returns Google's consent URL; the browser navigates there."""
    try:
        return {"url": google_calendar.start_link(person.id)}
    except google_calendar.GoogleError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("")
async def google_disconnect(person: Person = Depends(current_person)) -> dict:
    await google_calendar.disconnect(person.id)
    db.log_activity("system", f"{person.display_name} unlinked their Google Calendar", user_id=person.id)
    return google_calendar.status(person.id)


def _bounce(result: str, message: str = "") -> HTMLResponse:
    """Hop back to the dashboard via a same-site page, so the SameSite=Strict login cookie is sent
    again (it isn't on the redirect coming from accounts.google.com). No script: CSP-safe."""
    target = f"/app?google={result}"
    body = (f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={escape(target)}">'
            f'<title>Alfred</title><p style="font-family:sans-serif">{escape(message or "Returning to Alfred")}… '
            f'<a href="{escape(target)}">continue</a></p>')
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/callback", include_in_schema=False)
async def google_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    # No session cookie arrives here (cross-site redirect); the single-use `state` identifies the user.
    if error:
        return _bounce("denied", "Google access was not granted")
    try:
        user_id = await google_calendar.finish_link(state[:200], code[:2000])
    except google_calendar.GoogleError as e:
        return _bounce("error", str(e))
    db.log_activity("system", "Google Calendar linked", user_id=user_id)
    return _bounce("linked", "Google Calendar linked")
