"""Notes, lists and the Company to-do on the dashboard (#37). Same rules as the chat tools: the
scope checks live in orchestrator/nas.py (personal = own folder, company = admins/cofounders)."""
from __future__ import annotations

import asyncio
from typing import Literal

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import nas, nas_files, company_todo
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/nas")
Scope = Literal["personal", "company"]
AnyScope = Literal["personal", "company", "people"]       # people = every personal folder (admins)


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
            "scopes": ["personal"] + (["company"] if person.role in nas.COMPANY_ROLES else [])
                      + (["people"] if person.is_admin else []),
            "company_todo": person.role in nas.COMPANY_ROLES}


# --- explorer (orchestrator/nas_files.py) --------------------------------------------------------

class FileSave(Strict):
    scope: AnyScope
    path: str = Field(min_length=1, max_length=400)
    text: str = Field(max_length=nas_files.MAX_EDIT_BYTES)
    mtime: float | None = None
    create: bool = False


class PathBody(Strict):
    scope: AnyScope
    path: str = Field(min_length=1, max_length=400)


class MovePath(PathBody):
    new_path: str = Field(min_length=1, max_length=400)


@router.get("/browse")
async def browse(scope: AnyScope, path: str = Query(default="", max_length=400), person: Person = Depends(current_person)) -> dict:
    return await _run(nas_files.browse, person, scope, path)


@router.get("/file")
async def open_file(scope: AnyScope, path: str = Query(max_length=400), person: Person = Depends(current_person)) -> dict:
    return await _run(nas_files.open_text, person, scope, path)


@router.put("/file")
async def save_file(body: FileSave, person: Person = Depends(current_person)) -> dict:
    return await _run(nas_files.save_text, person, body.scope, body.path, body.text, body.mtime, body.create)


@router.get("/download")
async def download(scope: AnyScope, path: str = Query(max_length=400), person: Person = Depends(current_person)) -> Response:
    name, data = await _run(nas_files.download, person, scope, path)
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "Cache-Control": "private, no-store"})


@router.get("/preview")
async def file_preview_json(scope: AnyScope, path: str = Query(max_length=400), person: Person = Depends(current_person)) -> dict:
    """Read-only preview of an office document or code file as plain data (orchestrator/file_preview.py)."""
    from orchestrator import file_preview
    name = path.rsplit("/", 1)[-1]
    if not file_preview.can_preview(name) and not name.lower().endswith(file_preview.LEGACY):
        raise HTTPException(status_code=400, detail="No preview for this file type. Download it to open it.")
    name, data = await _run(nas_files.download, person, scope, path)
    try:
        return await asyncio.to_thread(file_preview.preview, name, data)
    except file_preview.PreviewError as e:
        raise HTTPException(status_code=400, detail=str(e))


_RANGE_CHUNK = 4 * 1024 * 1024          # an open-ended range ("bytes=0-") is answered in pieces of this size
_STREAM_CHUNK = 1024 * 1024


def _view_headers(name: str, kind: str) -> dict:
    return {"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}", "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes", "X-Frame-Options": "SAMEORIGIN",
            # Chrome's PDF viewer counts as a plugin: object-src 'none' would blank it, so PDFs only limit framing.
            "Content-Security-Policy": "frame-ancestors 'self'" if kind == "application/pdf" else
            "default-src 'none'; img-src 'self' data:; media-src 'self'; frame-ancestors 'self'"}


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end inclusive) for a single 'bytes=a-b' / 'bytes=a-' / 'bytes=-n' range, else None."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    a, _, b = header[6:].strip().partition("-")
    try:
        if a == "":
            n = int(b)
            return (max(0, size - n), size - 1) if n > 0 else None
        start = int(a)
        end = min(int(b), size - 1) if b else min(start + _RANGE_CHUNK - 1, size - 1)
    except ValueError:
        return None
    return (start, end) if start <= end < size else None


def _ranged(request: Request, size: int, read, name: str, kind: str) -> Response:
    """200 (streamed) or 206 for a byte range; `read(start, length)` is a blocking reader."""
    headers = _view_headers(name, kind)
    rng = _parse_range(request.headers.get("range"), size)
    if request.headers.get("range") and rng is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    if rng:
        start, end = rng
        data = read(start, end - start + 1)
        return Response(data, status_code=206, media_type=kind,
                        headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})

    async def body():
        pos = 0
        while pos < size:
            chunk = await asyncio.to_thread(read, pos, min(_STREAM_CHUNK, size - pos))
            if not chunk:
                break
            pos += len(chunk)
            yield chunk
    return StreamingResponse(body(), media_type=kind, headers={**headers, "Content-Length": str(size)})


@router.get("/view")
async def view(request: Request, scope: AnyScope, path: str = Query(max_length=400), as_: str = Query(default="", alias="as"),
               person: Person = Depends(current_person)) -> Response:
    """PDFs, pictures, audio and video shown by the browser itself (byte ranges, so video can seek and
    start before the whole file arrives), and office documents as PDF (as=pdf, orchestrator/office_pdf.py).
    Fixed content types from a short list (+ nosniff), framed only by the dashboard: nothing in a file
    can run as a page."""
    from orchestrator import file_preview, office_pdf
    if as_ == "pdf":
        try:
            pdf = await office_pdf.pdf_for(person, scope, path)
        except office_pdf.ConvertError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except nas.NasError as e:
            raise HTTPException(status_code=400, detail=str(e))
        name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".pdf"
        return _ranged(request, len(pdf), lambda s, n: pdf[s:s + n], name, "application/pdf")
    kind = file_preview.inline_type(path)
    if not kind:
        raise HTTPException(status_code=400, detail="This file type isn't shown inline. Download it instead.")
    info = await _run(nas_files.stat_file, person, scope, path)
    if request.headers.get("range"):
        rng = _parse_range(request.headers.get("range"), info["size"])
        if rng:                                            # read the one piece now, outside the event loop
            data = await _run(nas_files.read_range, person, scope, path, rng[0], rng[1] - rng[0] + 1)
            return _ranged(request, info["size"], lambda s, n: data[s - rng[0]:s - rng[0] + n], info["name"], kind)
    return _ranged(request, info["size"], lambda s, n: nas_files.read_range(person, scope, path, s, n), info["name"], kind)


@router.post("/view/prepare")
async def prepare_view(body: PathBody, person: Person = Depends(current_person)) -> dict:
    """Converts an office document to PDF ahead of showing it (so the page can say "preparing" and show
    a readable error instead of an error page inside the frame). Cached: the frame then loads at once."""
    from orchestrator import office_pdf
    if not office_pdf.supported(body.path):
        raise HTTPException(status_code=400, detail="No document view for this file type.")
    try:
        pdf = await office_pdf.pdf_for(person, body.scope, body.path)
    except office_pdf.ConvertError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except nas.NasError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "bytes": len(pdf)}


@router.post("/upload")
async def upload(scope: AnyScope = Form(...), folder: str = Form(default="", max_length=400), replace: bool = Form(default=False),
                 file: UploadFile = File(...), person: Person = Depends(current_person)) -> dict:
    data = await file.read(nas_files.MAX_UPLOAD_BYTES + 1)
    return await _run(nas_files.upload, person, scope, folder, file.filename or "file", data, replace)


@router.post("/folder")
async def new_folder(body: PathBody, person: Person = Depends(current_person)) -> dict:
    return await _run(nas_files.make_folder, person, body.scope, body.path)


@router.post("/move")
async def move_path(body: MovePath, person: Person = Depends(current_person)) -> dict:
    return await _run(nas_files.move, person, body.scope, body.path, body.new_path)


@router.delete("/path")
async def delete_path(scope: AnyScope, path: str = Query(max_length=400), person: Person = Depends(current_person)) -> dict:
    return await _run(nas_files.delete, person, scope, path)


@router.get("/recent")
async def recent(person: Person = Depends(current_person)) -> dict:
    return nas_files.recent(person)


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


# --- "Tidy up" on the Files tab (orchestrator/notes_tidy.py): own folder, .md/.txt only -------------

class TidyPlanBody(Strict):
    kind: Literal["structure", "content"]
    wishes: str = Field(default="", max_length=1000)


class TidyApplyBody(Strict):
    kind: Literal["structure", "content"]
    digest: str = Field(min_length=1, max_length=40)
    steps: list[int] = Field(min_length=1, max_length=60)


@router.post("/tidy/plan")
async def tidy_plan(body: TidyPlanBody, person: Person = Depends(current_person)) -> dict:
    from orchestrator import notes_tidy
    try:
        return await notes_tidy.propose(person, body.kind, body.wishes)
    except (notes_tidy.TidyError, nas.NasError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tidy/apply")
async def tidy_apply(body: TidyApplyBody, person: Person = Depends(current_person)) -> dict:
    from orchestrator import notes_tidy
    try:
        return await asyncio.to_thread(notes_tidy.dashboard_apply, person, body.kind, body.digest, body.steps)
    except (notes_tidy.TidyError, nas.NasError) as e:
        raise HTTPException(status_code=400, detail=str(e))
