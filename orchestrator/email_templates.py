"""Email templates (roadmap #55): layouts and reusable texts, managed on the Mail tab's Template manager.

- Layout: how an email looks: banner (an uploaded PNG/JPEG, or the built-in file), colours and font, the
  sender name, signature and footer (Markdown with {assistant}, {company}, {website}, {tagline}). "Company"
  layouts write in the company's name (admins/cofounders; the mail counts as company mail).
- Text: a reusable email: subject + Markdown body with fields such as {name}, {first_name}, {company}, {date},
  {sender}, plus any others ({offer_date}); each uses a layout.
- Only active, not archived templates are offered to Alfred and in the pickers. At least one layout stays
  active. "Deleting" archives (sent mail keeps its own copy in the archive). Usage (how often, last used) comes
  from the `emails` log.
- Built-ins seeded on first start from the original designs: layouts "Company" and "Alfred", the text
  "Inquiry acknowledgement" (Alfred's answer when an outside inquiry goes to the team) and two examples.
Who: admins and cofounders, both may create, edit and switch templates on or off.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

KINDS = ("layout", "text")
STYLE_KEYS = ("page", "card", "ink", "muted", "accent", "foot_bg", "foot_ink", "foot_muted")
FONTS = ("serif", "sans")
MAX_BANNER_BYTES = 1_000_000
FIELD_RE = re.compile(r"\{([a-z][a-z0-9_]{0,30})\}")
STANDARD_FIELDS = ("name", "first_name", "company", "date", "sender", "assistant", "website")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

BUILTIN_STYLE = {
    "company": {"page": "#e9e6de", "card": "#f8f6f0", "ink": "#1b1b1a", "muted": "#6b6860", "accent": "#1f7a46",
                "foot_bg": "#060606", "foot_ink": "#f4f1ea", "foot_muted": "#8d8a81", "font": "serif"},
    "alfred": {"page": "#e6e5e1", "card": "#f6f5f1", "ink": "#1c1c1e", "muted": "#66666c", "accent": "#4a4c54",
               "foot_bg": "#0c0c0e", "foot_ink": "#e8e6e0", "foot_muted": "#9c9ca2", "font": "serif"},
}
BUILTIN_SIGNATURE = {"company": "Kind regards,\n**{assistant}**\nAI assistant · {company}",
                     "alfred": "At your service,\n**{assistant}**"}
BUILTIN_FOOTER = {
    "company": "{company}\n{tagline}\n{website}\nThis message was written by {assistant}, an AI assistant. Replies "
               "are read by {assistant} and passed to the team where needed.",
    "alfred": "{assistant} · personal assistant\nSent by {assistant}, an AI assistant. Reply to this email to answer "
              "{assistant}.",
}
ACK_BODY = ("Good day,\n\nThank you for your message. I've passed it to the team, who will look into it and get back "
            "to you as soon as possible.")
EXAMPLES = [
    ("Thank you for your inquiry", "Reply to a new client inquiry", "Your inquiry",
     "Dear {name},\n\nThank you for your inquiry about {topic}. We'd be glad to help. To prepare a first proposal, "
     "could you tell us a little more about your timeline and budget?\n\nWe usually reply within one business day."),
    ("Offer follow-up", "Friendly reminder about an offer that was sent", "Our offer of {offer_date}",
     "Dear {name},\n\nI'm following up on the offer we sent on {offer_date}. Do you have any questions, or would you "
     "like to go ahead?\n\nWe're happy to adjust it if anything doesn't fit yet."),
]


class TemplateError(ValueError):
    """Message is safe to show."""


def _team(person: Person) -> None:
    if not person.can("mail.templates"):
        raise TemplateError("You don't have permission to manage email templates.")


def _now() -> str:
    return db.now_iso()


# --- seeding ---------------------------------------------------------------------------------------

def ensure_builtin() -> None:
    """Create the built-in layouts and texts once (idempotent)."""
    have = {r["builtin"] for r in db.query("SELECT builtin FROM mail_templates WHERE builtin IS NOT NULL")}
    now = _now()
    ids = {r["builtin"]: r["id"] for r in db.query("SELECT id, builtin FROM mail_templates WHERE builtin IS NOT NULL")}
    for key, name, desc, company in (("company", "Company", "Formal mail in the company's name", 1),
                                     ("alfred", "Alfred", "Alfred's own, personal mail", 0)):
        if key not in have:
            ids[key] = db.execute(
                "INSERT INTO mail_templates(kind, name, description, builtin, company, style, signature_md, footer_md, "
                "created_at, updated_at) VALUES ('layout',?,?,?,?,?,?,?,?,?)",
                (name, desc, key, company, json.dumps(BUILTIN_STYLE[key]), BUILTIN_SIGNATURE[key], BUILTIN_FOOTER[key],
                 now, now))
    if "inquiry_ack" not in have:
        db.execute("INSERT INTO mail_templates(kind, name, description, builtin, subject, body_md, layout_id, created_at, "
                   "updated_at) VALUES ('text','Inquiry acknowledgement',?, 'inquiry_ack', '', ?, ?, ?, ?)",
                   ("Alfred's reply when an inquiry from outside goes to the team", ACK_BODY, ids.get("company"), now, now))
        for name, desc, subject, body in EXAMPLES:
            db.execute("INSERT INTO mail_templates(kind, name, description, subject, body_md, layout_id, created_at, "
                       "updated_at) VALUES ('text',?,?,?,?,?,?,?)", (name, desc, subject, body, ids.get("company"),
                                                                     now, now))


# --- reading -------------------------------------------------------------------------------------------

def _view(row: dict | None, usage: dict | None = None) -> dict | None:
    if not row:
        return None
    row = dict(row)
    row["style"] = {**BUILTIN_STYLE["alfred"], **json.loads(row.get("style") or "{}")}
    row["has_banner"] = bool(row.pop("banner", None)) or row.get("builtin") in ("company", "alfred")
    row["custom_banner"] = bool(row.get("banner_type"))
    row["active"], row["archived"], row["company"] = bool(row["active"]), bool(row["archived"]), bool(row["company"])
    row["fields"] = sorted(set(FIELD_RE.findall(f"{row.get('subject', '')} {row.get('body_md', '')}")))
    if usage is not None:
        u = usage.get(row["id"]) or {}
        row["used"], row["last_used"] = u.get("n", 0), u.get("last")
    return row


def _usage() -> dict[int, dict]:
    out: dict[int, dict] = {}
    for col in ("template_id", "layout_id"):
        for r in db.query(f"SELECT {col} AS id, COUNT(*) n, MAX(created_at) last FROM emails WHERE direction = 'out' "
                          f"AND {col} IS NOT NULL GROUP BY {col}"):
            out[r["id"]] = {"n": r["n"], "last": r["last"]}
    return out


def get(template_id: int) -> dict | None:
    ensure_builtin()
    return _view(db.query_one("SELECT * FROM mail_templates WHERE id = ?", (int(template_id),)), _usage())


def list_all(*, kind: str = "", active_only: bool = False, archived: bool = False, query: str = "") -> list[dict]:
    ensure_builtin()
    where, params = ["archived = ?"], [int(archived)]
    if kind in KINDS:
        where.append("kind = ?")
        params.append(kind)
    if active_only:
        where.append("active = 1")
    if query:
        where.append("(name LIKE ? OR description LIKE ? OR subject LIKE ? OR body_md LIKE ?)")
        params += [f"%{query.strip()[:80]}%"] * 4
    usage = _usage()
    rows = db.query(f"SELECT * FROM mail_templates WHERE {' AND '.join(where)} ORDER BY kind, builtin IS NULL, "
                    "name COLLATE NOCASE", params)
    return [_view(r, usage) for r in rows]


def find(kind: str, ref, *, active_only: bool = True) -> dict | None:
    """By id, built-in key or name (case-insensitive)."""
    ensure_builtin()
    s = str(ref or "").strip()
    act = " AND active = 1" if active_only else ""
    if s.isdigit():
        row = db.query_one(f"SELECT * FROM mail_templates WHERE id = ? AND kind = ? AND archived = 0{act}", (int(s), kind))
    else:
        row = db.query_one(f"SELECT * FROM mail_templates WHERE kind = ? AND archived = 0{act} AND (builtin = ? OR "
                           "name = ? COLLATE NOCASE) ORDER BY builtin IS NULL LIMIT 1", (kind, s.lower(), s))
    return _view(row) if row else None


def layout_for(key) -> dict:
    """The layout to send with: by id / key / name, falling back for "company" to any active company layout, then
    to any active layout. Includes the banner bytes."""
    ensure_builtin()
    lay = find("layout", key)
    if not lay and str(key) == "company":
        row = db.query_one("SELECT * FROM mail_templates WHERE kind = 'layout' AND active = 1 AND archived = 0 "
                           "AND company = 1 ORDER BY id LIMIT 1")
        lay = _view(row) if row else None
    if not lay:
        row = db.query_one("SELECT * FROM mail_templates WHERE kind = 'layout' AND active = 1 AND archived = 0 "
                           "ORDER BY builtin = 'alfred' DESC, id LIMIT 1")
        lay = _view(row) if row else None
    if not lay:
        raise TemplateError("There is no active email layout.")
    return lay


def banner_bytes(layout_id: int) -> tuple[bytes, str] | None:
    """(bytes, subtype) of a layout's banner: uploaded, else the built-in file."""
    row = db.query_one("SELECT banner, banner_type, builtin FROM mail_templates WHERE id = ?", (int(layout_id),))
    if not row:
        return None
    if row["banner"]:
        return bytes(row["banner"]), row["banner_type"] or "png"
    if row["builtin"] in ("company", "alfred"):
        from .mail_templates import banner
        b = banner(row["builtin"])
        return (b[0], "png") if b else None
    return None


# --- filling a text ----------------------------------------------------------------------------------

def standard_values(sender: str = "") -> dict:
    return {"company": settings.company_name, "assistant": settings.assistant_name,
            "website": settings.company_website, "tagline": settings.company_tagline,
            "date": datetime.now(ZoneInfo(settings.timezone)).strftime("%d %B %Y"), "sender": sender}


def fill(text: str, values: dict) -> tuple[str, list[str]]:
    """Replace {field}s; returns (text, missing fields)."""
    missing: list[str] = []

    def sub(m):
        v = values.get(m.group(1))
        if v in (None, ""):
            if m.group(1) not in missing:
                missing.append(m.group(1))
            return m.group(0)
        return str(v)
    return FIELD_RE.sub(sub, text or ""), missing


def apply_text(tpl: dict, values: dict) -> tuple[str, str, list[str]]:
    """(subject, body, missing) of a text template with these values (standard ones added)."""
    vals = {**standard_values(), **{k: v for k, v in values.items() if v not in (None, "")}}
    if vals.get("name") and not vals.get("first_name"):
        vals["first_name"] = str(vals["name"]).split()[0]
    subject, m1 = fill(tpl["subject"], vals)
    body, m2 = fill(tpl["body_md"], vals)
    return subject, body, list(dict.fromkeys(m1 + m2))


# --- changing ------------------------------------------------------------------------------------------

def _clean_style(style: dict | None, base: dict) -> dict:
    out = dict(base)
    for k, v in (style or {}).items():
        if k in STYLE_KEYS:
            if not isinstance(v, str) or not _COLOR.match(v):
                raise TemplateError(f"Colour '{k}' must look like #1a2b3c.")
            out[k] = v.lower()
        elif k == "font":
            if v not in FONTS:
                raise TemplateError("Font must be serif or sans.")
            out[k] = v
    return out


def _clean_text(value, limit: int, what: str) -> str:
    v = str(value or "").replace("\r\n", "\n").strip()
    if len(v) > limit:
        raise TemplateError(f"The {what} is too long (max {limit} characters).")
    return v


def _active_layouts(excluding: int | None = None) -> int:
    return db.query_one("SELECT COUNT(*) n FROM mail_templates WHERE kind = 'layout' AND active = 1 AND archived = 0 "
                        "AND id IS NOT ?", (excluding,))["n"]


def save(person: Person, data: dict, template_id: int | None = None) -> dict:
    """Create (template_id None) or update a template from the editor's fields."""
    _team(person)
    ensure_builtin()
    cur = db.query_one("SELECT * FROM mail_templates WHERE id = ?", (int(template_id),)) if template_id else None
    if template_id and not cur:
        raise TemplateError("Unknown template.")
    kind = cur["kind"] if cur else data.get("kind")
    if kind not in KINDS:
        raise TemplateError("Choose layout or text.")
    name = " ".join(str(data.get("name", cur["name"] if cur else "")).split())[:60]
    if not name:
        raise TemplateError("Give the template a name.")
    clash = db.query_one("SELECT id FROM mail_templates WHERE kind = ? AND name = ? COLLATE NOCASE AND archived = 0 "
                         "AND id IS NOT ?", (kind, name, cur["id"] if cur else None))
    if clash:
        raise TemplateError(f"There is already a {kind} called “{name}”.")
    f = {"name": name,
         "description": _clean_text(data.get("description", cur["description"] if cur else ""), 200, "description"),
         "active": int(bool(data.get("active", cur["active"] if cur else True)))}
    if kind == "layout":
        base = json.loads(cur["style"]) if cur else BUILTIN_STYLE["alfred"]
        f.update(style=json.dumps(_clean_style(data.get("style"), base)),
                 company=int(bool(data.get("company", cur["company"] if cur else False))),
                 from_name=_clean_text(data.get("from_name", cur["from_name"] if cur else ""), 80, "sender name"),
                 signature_md=_clean_text(data.get("signature_md", cur["signature_md"] if cur else ""), 1000, "signature"),
                 footer_md=_clean_text(data.get("footer_md", cur["footer_md"] if cur else ""), 1500, "footer"))
        if cur and cur["active"] and not f["active"] and _active_layouts(excluding=cur["id"]) == 0:
            raise TemplateError("At least one layout has to stay active.")
    else:
        layout_id = data.get("layout_id", cur["layout_id"] if cur else None)
        if layout_id is not None and not db.query_one("SELECT 1 FROM mail_templates WHERE id = ? AND kind = 'layout' "
                                                      "AND archived = 0", (int(layout_id),)):
            raise TemplateError("Choose an existing layout.")
        f.update(subject=_clean_text(data.get("subject", cur["subject"] if cur else ""), 200, "subject"),
                 body_md=_clean_text(data.get("body_md", cur["body_md"] if cur else ""), 20_000, "text"),
                 layout_id=int(layout_id) if layout_id is not None else None)
        if not f["body_md"]:
            raise TemplateError("Write the email text.")
    f["updated_by"], f["updated_at"] = person.id, _now()
    if cur:
        db.execute(f"UPDATE mail_templates SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), cur["id"]))
        tid = cur["id"]
        what = "changed"
    else:
        f.update(kind=kind, created_by=person.id, created_at=f["updated_at"])
        tid = db.execute(f"INSERT INTO mail_templates({', '.join(f)}) VALUES ({', '.join('?' * len(f))})",
                         tuple(f.values()))
        what = "created"
    db.log_activity("system", f"{person.display_name} {what} the email {kind} “{name}”", user_id=person.id)
    return get(tid)


def set_active(person: Person, template_id: int, active: bool) -> dict:
    _team(person)
    cur = db.query_one("SELECT * FROM mail_templates WHERE id = ? AND archived = 0", (int(template_id),))
    if not cur:
        raise TemplateError("Unknown template.")
    if cur["kind"] == "layout" and cur["active"] and not active and _active_layouts(excluding=cur["id"]) == 0:
        raise TemplateError("At least one layout has to stay active.")
    db.execute("UPDATE mail_templates SET active = ?, updated_by = ?, updated_at = ? WHERE id = ?",
               (int(active), person.id, _now(), cur["id"]))
    return get(cur["id"])


def set_archived(person: Person, template_id: int, archived: bool) -> dict:
    _team(person)
    cur = db.query_one("SELECT * FROM mail_templates WHERE id = ?", (int(template_id),))
    if not cur:
        raise TemplateError("Unknown template.")
    if archived and cur["kind"] == "layout" and cur["active"] and _active_layouts(excluding=cur["id"]) == 0:
        raise TemplateError("At least one layout has to stay active.")
    if archived and cur["kind"] == "layout" and db.query_one(
            "SELECT 1 FROM mail_templates WHERE layout_id = ? AND archived = 0 AND active = 1", (cur["id"],)):
        raise TemplateError("Active text templates still use this layout: give them another layout first.")
    db.execute("UPDATE mail_templates SET archived = ?, active = CASE WHEN ? THEN 0 ELSE active END, updated_by = ?, "
               "updated_at = ? WHERE id = ?", (int(archived), int(archived), person.id, _now(), cur["id"]))
    db.log_activity("system", f"{person.display_name} {'archived' if archived else 'restored'} the email "
                    f"{cur['kind']} “{cur['name']}”", user_id=person.id)
    return get(cur["id"])


def duplicate(person: Person, template_id: int) -> dict:
    _team(person)
    cur = db.query_one("SELECT * FROM mail_templates WHERE id = ?", (int(template_id),))
    if not cur:
        raise TemplateError("Unknown template.")
    base, n = f"{cur['name']} (copy)", 2
    name = base
    while db.query_one("SELECT 1 FROM mail_templates WHERE kind = ? AND name = ? COLLATE NOCASE AND archived = 0",
                       (cur["kind"], name)):
        name, n = f"{base} {n}", n + 1
    cols = ("kind", "description", "company", "style", "from_name", "signature_md", "footer_md", "banner",
            "banner_type", "subject", "body_md", "layout_id")
    now = _now()
    tid = db.execute(f"INSERT INTO mail_templates(name, active, {', '.join(cols)}, created_by, updated_by, created_at, "
                     f"updated_at) VALUES (?, 0, {', '.join('?' * len(cols))}, ?, ?, ?, ?)",
                     (name, *(cur[c] for c in cols), person.id, person.id, now, now))
    if cur["builtin"] in ("company", "alfred") and not cur["banner"]:      # the copy keeps the built-in banner
        b = banner_bytes(cur["id"])
        if b:
            db.execute("UPDATE mail_templates SET banner = ?, banner_type = ? WHERE id = ?", (b[0], b[1], tid))
    return get(tid)


def set_banner(person: Person, template_id: int, data: bytes | None) -> dict:
    """Upload (PNG/JPEG, ≤ 1 MB) or remove (None) a layout's banner."""
    _team(person)
    cur = db.query_one("SELECT kind FROM mail_templates WHERE id = ?", (int(template_id),))
    if not cur or cur["kind"] != "layout":
        raise TemplateError("Banners belong to layouts.")
    if data is None:
        db.execute("UPDATE mail_templates SET banner = NULL, banner_type = NULL, updated_by = ?, updated_at = ? "
                   "WHERE id = ?", (person.id, _now(), int(template_id)))
        return get(template_id)
    if len(data) > MAX_BANNER_BYTES:
        raise TemplateError("The banner is too big (max 1 MB; 1200×300 px works well).")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        subtype = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        subtype = "jpeg"
    else:
        raise TemplateError("The banner must be a PNG or JPEG picture.")
    db.execute("UPDATE mail_templates SET banner = ?, banner_type = ?, updated_by = ?, updated_at = ? WHERE id = ?",
               (data, subtype, person.id, _now(), int(template_id)))
    return get(template_id)


# --- for Alfred ------------------------------------------------------------------------------------------

def catalogue(person: Person) -> dict:
    """Active layouts and texts as the model sees them (list_email_templates)."""
    team = person.can("mail.tab")
    lays = [t for t in list_all(kind="layout", active_only=True) if team or not t["company"]]
    texts = [t for t in list_all(kind="text", active_only=True) if t["builtin"] != "inquiry_ack"]
    names = {t["id"]: t["name"] for t in lays}
    return {"layouts": [{"name": t["name"], "company": t["company"], "description": t["description"]} for t in lays],
            "texts": [{"name": t["name"], "description": t["description"], "subject": t["subject"],
                       "fields": t["fields"], "layout": names.get(t["layout_id"])} for t in texts
                      if team or t["layout_id"] in names]}
