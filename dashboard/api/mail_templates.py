"""Template manager on the Mail tab (roadmap #55): email layouts and reusable texts (orchestrator/email_templates.py).

Admins and cofounders only, like the rest of the Mail tab. The preview is the real email HTML, rendered on the
server from the same escaped Markdown subset as sent mail, served from its own URL with a CSP that allows no
scripts at all (only inline styles and the data: banner), and shown in a frame on the Mail tab.
"""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, email_templates, mail, mail_templates
from orchestrator.identity import registry
from orchestrator.identity.registry import Person

from .mail import team_only

router = APIRouter(prefix="/api/mail/templates", dependencies=[Depends(team_only)])
_previews: dict[str, tuple[float, int, str]] = {}          # token -> (created, person id, html)
PREVIEW_TTL_S = 600


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TemplateBody(Strict):
    kind: str | None = Field(default=None, pattern="^(layout|text)$")
    name: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    active: bool | None = None
    company: bool | None = None
    style: dict | None = None
    from_name: str | None = Field(default=None, max_length=80)
    signature_md: str | None = Field(default=None, max_length=1000)
    footer_md: str | None = Field(default=None, max_length=1500)
    subject: str | None = Field(default=None, max_length=200)
    body_md: str | None = Field(default=None, max_length=20_000)
    layout_id: int | None = None


class PreviewBody(TemplateBody):
    template_id: int | None = None
    sample: dict = Field(default_factory=dict)


class Flag(Strict):
    value: bool


def _fail(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _data(body: BaseModel) -> dict:
    return {k: v for k, v in body.model_dump().items() if v is not None and k not in ("template_id", "sample")}


@router.get("")
async def list_templates(kind: str = Query(default="", pattern="^(|layout|text)$"), active: bool = False,
                         archived: bool = False, q: str = Query(default="", max_length=80),
                         person: Person = Depends(team_only)) -> list[dict]:
    return email_templates.list_all(kind=kind, active_only=active, archived=archived, query=q)


@router.post("")
async def create_template(body: TemplateBody, person: Person = Depends(team_only)) -> dict:
    try:
        return email_templates.save(person, _data(body))
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.post("/preview")
async def preview(body: PreviewBody, person: Person = Depends(team_only)) -> dict:
    """Render unsaved editor fields: a layout with a sample text, or a text in its layout with sample values.
    Returns the frame URL and the plain-text version."""
    data = _data(body)
    base = email_templates.get(body.template_id) if body.template_id else None
    kind = (base or {}).get("kind") or data.get("kind") or "text"
    sample = {"name": "Erika Muster", "first_name": "Erika", **{k: str(v)[:200] for k, v in body.sample.items()}}
    if kind == "layout":
        st = {**((base or {}).get("style") or email_templates.BUILTIN_STYLE["alfred"]), **(data.get("style") or {})}
        try:
            st = email_templates._clean_style(st, email_templates.BUILTIN_STYLE["alfred"])
        except email_templates.TemplateError as e:
            raise _fail(e)
        sans = st.get("font") == "sans"
        theme = mail_templates.Theme(**{k: st[k] for k in email_templates.STYLE_KEYS},
                                     head_font=mail_templates.SANS if sans else mail_templates.SERIF_HEAD,
                                     body_font=mail_templates.SANS if sans else mail_templates.SERIF_BODY)
        lay = mail_templates.Layout(
            base["id"] if base else None, (base or {}).get("builtin") or "preview", theme,
            data.get("signature_md", (base or {}).get("signature_md", "")),
            data.get("footer_md", (base or {}).get("footer_md", "")),
            company=bool(data.get("company", (base or {}).get("company", False))),
            banner=email_templates.banner_bytes(base["id"]) if base else None)
        subject = "Preview of the layout"
        text = ("Dear Erika,\n\nThis is how an email in this layout looks: a short paragraph, **bold** and *italic* "
                "words, a [link](https://example.com) and a list:\n\n- first point\n- second point\n\n> A quoted line.")
    else:
        tpl = {"subject": data.get("subject", (base or {}).get("subject", "")),
               "body_md": data.get("body_md", (base or {}).get("body_md", ""))}
        subject, text, missing = email_templates.apply_text(tpl, {"sender": person.display_name, **sample})
        for m in missing:                                      # show unfilled fields visibly
            subject, text = subject.replace("{" + m + "}", f"[{m}]"), text.replace("{" + m + "}", f"[{m}]")
        layout_id = data.get("layout_id", (base or {}).get("layout_id"))
        lay = mail_templates.resolve(layout_id or "alfred")
    r = mail_templates.render(lay, text, subject=subject)
    now = time.time()
    for k, (t, _, _) in list(_previews.items()):
        if now - t > PREVIEW_TTL_S:
            _previews.pop(k, None)
    token = secrets.token_urlsafe(16)
    _previews[token] = (now, person.id, mail_templates.preview_html(r))
    return {"url": f"/api/mail/templates/preview/{token}", "subject": subject, "text": r.text}


@router.get("/preview/{token}")
async def preview_page(token: str = Path(max_length=40), person: Person = Depends(team_only)) -> Response:
    item = _previews.get(token)
    if not item or item[1] != person.id or time.time() - item[0] > PREVIEW_TTL_S:
        raise HTTPException(status_code=404)
    return Response(item[2], media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                                   "frame-ancestors 'self'; base-uri 'none'; form-action 'none'",
        "X-Frame-Options": "SAMEORIGIN", "Cache-Control": "private, no-store"})


@router.get("/{template_id}")
async def get_template(template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    t = email_templates.get(template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Unknown template.")
    return t


@router.patch("/{template_id}")
async def update_template(body: TemplateBody, template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    try:
        return email_templates.save(person, _data(body), template_id)
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.post("/{template_id}/active")
async def set_active(body: Flag, template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    try:
        return email_templates.set_active(person, template_id, body.value)
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.post("/{template_id}/archive")
async def set_archived(body: Flag, template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    try:
        return email_templates.set_archived(person, template_id, body.value)
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.post("/{template_id}/duplicate")
async def duplicate(template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    try:
        return email_templates.duplicate(person, template_id)
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.post("/{template_id}/banner")
async def upload_banner(template_id: int = Path(ge=1), file: UploadFile = File(...),
                        person: Person = Depends(team_only)) -> dict:
    data = await file.read(email_templates.MAX_BANNER_BYTES + 1)
    try:
        return email_templates.set_banner(person, template_id, data)
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.delete("/{template_id}/banner")
async def remove_banner(template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    try:
        return email_templates.set_banner(person, template_id, None)
    except email_templates.TemplateError as e:
        raise _fail(e)


@router.get("/{template_id}/banner")
async def banner(template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> Response:
    b = email_templates.banner_bytes(template_id)
    if not b:
        raise HTTPException(status_code=404)
    return Response(b[0], media_type=f"image/{b[1]}", headers={"Cache-Control": "private, no-store"})


@router.post("/{template_id}/test")
async def send_test(body: PreviewBody, template_id: int = Path(ge=1), person: Person = Depends(team_only)) -> dict:
    """The template (as saved) to the person's own address, with sample values for its fields."""
    t = email_templates.get(template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Unknown template.")
    to = registry.email_for(person.id)
    if not to:
        raise HTTPException(status_code=400, detail="Add and verify an email address on your profile first.")
    sample = {"name": person.display_name, **{k: str(v)[:200] for k, v in body.sample.items()}}
    if t["kind"] == "layout":
        layout, subject, text, tid = t["id"], f"Test of the layout “{t['name']}”", \
            "This is a test of the layout, sent from the Template manager.", None
    else:
        subject, text, missing = email_templates.apply_text(t, {"sender": person.display_name, **sample})
        for m in missing:
            subject, text = subject.replace("{" + m + "}", f"[{m}]"), text.replace("{" + m + "}", f"[{m}]")
        layout, subject, tid = t["layout_id"] or "alfred", f"[Test] {subject or t['name']}", t["id"]
    try:
        row = await mail.send(to=[to], subject=subject, body_md=text, template=layout, user_id=person.id,
                              scope="personal", kind="notice", template_id=tid)
    except mail.MailError as e:
        raise _fail(e)
    db.log_activity("system", f"{person.display_name} sent a test of the email {t['kind']} “{t['name']}”",
                    user_id=person.id)
    return {"sent": True, "to": to, "email_id": row["id"]}
