"""Attachments of Alfred's email: index, risk check, content, filing.

- Indexed when a message is stored (`index`): 1-based order, name, type, size, sha256 and a risk flag. The bytes
  stay inside the archived .eml, so an attachment lives exactly as long as its email (same retention, same
  deletion) and is read back from it on demand (`content`).
- Risky files (programs, scripts, macro-enabled Office files, archives containing those, files whose content
  doesn't match a harmless name) are flagged: listed and downloadable on the Mail tab with a warning, never
  previewed, never shown to the model, never filed on the NAS.
- `save` files one on the NAS (own folder or, for admins/cofounders, the company files), never overwriting.
  Used by the Mail tab ("Save to Files"), the tool file_email_attachment and the optional rule that files
  inquiry attachments automatically (setting mail_inquiry_autofile).
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, mail
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)

MAX_ATTACHMENTS = 25
AUTOFILE_SETTING = "mail_inquiry_autofile"
DANGEROUS = (".exe", ".com", ".bat", ".cmd", ".scr", ".pif", ".msi", ".msp", ".dll", ".cpl", ".js", ".jse", ".vbs",
             ".vbe", ".wsf", ".wsh", ".ps1", ".psm1", ".hta", ".jar", ".sh", ".app", ".lnk", ".reg", ".iso", ".img",
             ".vhd", ".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm", ".ppam", ".apk", ".dmg", ".pkg",
             ".svg", ".html", ".htm", ".xhtml", ".mht", ".swf")
ARCHIVES = (".zip", ".7z", ".rar", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".cab")
_MAGIC = {".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
          ".gif": (b"GIF8",), ".docx": (b"PK",), ".xlsx": (b"PK",), ".pptx": (b"PK",), ".zip": (b"PK",)}
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"#!")
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
               ".gif": "image/gif"}
MAX_IMAGE_FOR_MODEL = 4_500_000


class AttachmentError(ValueError):
    """Message is safe to show."""


def ext(name: str) -> str:
    low = (name or "").lower()
    return low[low.rfind("."):] if "." in low else ""


def safe_name(name: str) -> str:
    from .uploads import safe_name as clean
    return clean(name or "attachment")


def flag_reason(name: str, data: bytes) -> str:
    e = ext(name)
    if e in DANGEROUS:
        return "program, script or macro file"
    if re.search(r"\.(exe|scr|js|vbs|bat|cmd|com)\.\w{1,5}$", name.lower()):
        return "disguised file name"
    if data[:4].startswith(_EXECUTABLE_MAGIC) and e not in (".sh",):
        return "program inside a harmless-looking file"
    magic = _MAGIC.get(e)
    if magic and data and not data.startswith(magic):
        return "content doesn't match its file type"
    if e == ".zip" or (e in (".docx", ".xlsx", ".pptx") and data.startswith(b"PK")):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()[:2000]
        except zipfile.BadZipFile:
            return "damaged archive" if e == ".zip" else "damaged document"
        if any(ext(n) in DANGEROUS for n in names) or (e != ".zip" and any("vbaProject" in n for n in names)):
            return "contains programs or macros"
        if e == ".zip" and any(ext(n) in ARCHIVES for n in names):
            return "archive inside an archive"
    elif e in ARCHIVES:
        return "archive that can't be checked"
    return ""


def index(email_id: int, attachments: list[tuple[str, bytes, str]]) -> list[dict]:
    """Record the attachments of a stored email (replaces an earlier index)."""
    rows = []
    with db.tx() as conn:
        conn.execute("DELETE FROM email_attachments WHERE email_id = ?", (email_id,))
        for i, (name, data, mime) in enumerate(attachments[:MAX_ATTACHMENTS], start=1):
            nm = safe_name(name)
            reason = flag_reason(nm, data)
            conn.execute("INSERT INTO email_attachments(email_id, idx, name, mime, size, sha256, flagged) "
                         "VALUES (?,?,?,?,?,?,?)", (email_id, i, nm, (mime or "")[:100], len(data),
                                                    hashlib.sha256(data).hexdigest(), reason))
            rows.append({"idx": i, "name": nm, "mime": mime, "size": len(data), "flagged": reason})
    return rows


def list_for(email_id: int) -> list[dict]:
    return db.query("SELECT idx, name, mime, size, flagged, saved_to FROM email_attachments WHERE email_id = ? "
                    "ORDER BY idx", (email_id,))


def lists_for(email_ids: list[int]) -> dict[int, list[dict]]:
    if not email_ids:
        return {}
    out: dict[int, list[dict]] = {}
    for r in db.query(f"SELECT email_id, idx, name, mime, size, flagged, saved_to FROM email_attachments WHERE "
                      f"email_id IN ({','.join('?' * len(email_ids))}) ORDER BY email_id, idx", email_ids):
        out.setdefault(r.pop("email_id"), []).append(r)
    return out


def _row(person: Person, email_id: int) -> dict:
    row = mail.get(int(email_id))
    if not row or not mail.may_read(row, person):
        raise AttachmentError("I can't find that email.")
    return row


def pick(email_id: int, ref) -> dict:
    """An attachment by number (1-based) or by (part of its) file name."""
    items = list_for(email_id)
    if not items:
        raise AttachmentError("That email has no attachments.")
    s = str(ref or "").strip()
    if s.isdigit() and any(a["idx"] == int(s) for a in items):
        return next(a for a in items if a["idx"] == int(s))
    if not s and len(items) == 1:
        return items[0]
    hits = [a for a in items if s.lower() and s.lower() in a["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{a['idx']}. {a['name']}" for a in items)
    raise AttachmentError(f"Which attachment? {names}")


def content(person: Person, email_id: int, idx: int) -> tuple[dict, bytes]:
    """(index row, bytes) read back from the stored .eml (blocking: NAS)."""
    row = _row(person, email_id)
    meta = next((a for a in list_for(row["id"]) if a["idx"] == int(idx)), None)
    if not meta:
        raise AttachmentError("Unknown attachment.")
    raw = mail.read_raw(row, quiet=True)
    if not raw:
        raise AttachmentError("The stored copy of this email can't be read right now.")
    from .mail_inbound import parse
    parts = parse(raw, max_attachments=MAX_ATTACHMENTS).attachments
    if int(idx) > len(parts):
        raise AttachmentError("That attachment isn't in the stored copy.")
    data = parts[int(idx) - 1][1]
    if meta["size"] and hashlib.sha256(data).hexdigest() != db.query_one(
            "SELECT sha256 FROM email_attachments WHERE email_id = ? AND idx = ?", (row["id"], idx))["sha256"]:
        raise AttachmentError("The stored copy doesn't match the attachment list.")
    return meta, data


def _free_name(smb, folder_full: str, name: str) -> str:
    from .uploads import _free_name as free
    return free(smb, folder_full, name)


def save(person: Person, email_id: int, idx: int, scope: str, folder: str, filename: str = "") -> dict:
    """File one attachment on the NAS (blocking). Returns where it went."""
    from . import nas
    meta, data = content(person, email_id, idx)
    if meta["flagged"]:
        raise AttachmentError(f"“{meta['name']}” looks risky ({meta['flagged']}), so I won't file it. Download it "
                              "from the Mail tab only if you trust the sender.")
    if scope not in ("personal", "company"):
        raise AttachmentError("Scope must be personal or company.")
    try:
        nas.check_scope(person, scope)
    except nas.NasError as e:
        raise AttachmentError(str(e))
    name = safe_name(filename) if filename else meta["name"]
    if ext(meta["name"]) and not ext(name):
        name += ext(meta["name"])
    if ext(name) != ext(meta["name"]):
        raise AttachmentError("Keep the file's type (e.g. .pdf) in the name.")
    smb = nas._smb()
    nas._ensure_root(person, scope)
    root = nas._root(person, scope)
    try:
        rel = nas.clean_path(folder)
    except nas.NasError as e:
        raise AttachmentError(str(e))

    def go():
        target = root + ("\\" + rel if rel else "")
        nas._ensure_dir(smb, target)
        final = _free_name(smb, target, name)
        with smb.open_file(target + "\\" + final, mode="wb") as f:
            f.write(data)
        return final
    try:
        final = nas._call(go)
    except nas.NasError as e:
        raise AttachmentError(str(e))
    where = (rel.replace("\\", "/") + "/" if rel else "") + final
    db.execute("UPDATE email_attachments SET saved_to = ? WHERE email_id = ? AND idx = ?",
               (f"{scope}:{where}", int(email_id), int(idx)))
    db.log_activity("system", f"Files ({scope}): {person.display_name} filed {where} from email #{email_id}",
                    user_id=person.id)
    return {"saved": where, "scope": scope, "email_id": int(email_id), "attachment": meta["name"]}


def model_blocks(name: str, data: bytes, limit_chars: int = 4000) -> tuple[list[dict], str]:
    """What the model may see of a (non-flagged) attachment: an image block, or the start of its text."""
    e = ext(name)
    if e in IMAGE_TYPES and len(data) <= MAX_IMAGE_FOR_MODEL:
        import base64
        return [{"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[e],
                                             "data": base64.b64encode(data).decode()}}], ""
    if e in (".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv"):
        from .nas import extract_text
        try:
            text = extract_text(name, data)
        except Exception:  # noqa: BLE001
            return [], ""
        return [], text[:limit_chars]
    return [], ""


def describe(email_id: int, items: list[dict]) -> str:
    """The attachment list as the model sees it."""
    if not items:
        return ""
    lines = [f"{a['idx']}. {a['name']} ({max(1, a['size'] // 1024)} KB)"
             + (f" - RISKY: {a['flagged']}, not opened" if a["flagged"] else "") for a in items]
    return (f"\n[Attachments of email #{email_id}: " + "; ".join(lines) + ". To file one on the NAS (now or any "
            "time later), use file_email_attachment with this email id and the number.]")


# --- optional rule: file inquiry attachments automatically ----------------------------------------------

def autofile_on() -> bool:
    return bool(db.get_setting(AUTOFILE_SETTING, False))


def set_autofile(on: bool, actor: Person) -> bool:
    db.set_setting(AUTOFILE_SETTING, bool(on))
    db.log_activity("system", f"{actor.display_name} turned {'on' if on else 'off'} filing inquiry attachments "
                    "automatically", user_id=actor.id)
    return bool(on)


def autofile_inquiry(row: dict, attachments: list[tuple[str, bytes, str]]) -> list[str]:
    """Company Mail/Attachments/<year>/<sender> for safe inquiry attachments (blocking, best effort)."""
    from . import nas
    if not nas.enabled():
        return []
    when = datetime.fromisoformat(row["created_at"]).astimezone(ZoneInfo(settings.timezone))
    sender = re.sub(r"[^a-z0-9@._-]+", "-", (row["from_addr"] or "unknown").lower())[:60] or "unknown"
    folder = rf"\\{settings.nas_host}\{settings.nas_share_company}\{settings.mail_archive_folder}\Attachments" \
             rf"\{when:%Y}\{sender}"
    smb = nas._smb()
    saved = []
    for meta in list_for(row["id"]):
        if meta["flagged"] or meta["idx"] > len(attachments):
            continue
        data = attachments[meta["idx"] - 1][1]

        def go(meta=meta, data=data):
            nas._ensure_dir(smb, folder)
            final = _free_name(smb, folder, meta["name"])
            with smb.open_file(folder + "\\" + final, mode="wb") as f:
                f.write(data)
            return final
        try:
            final = nas._call(go)
        except nas.NasError as e:
            log.warning("Inquiry attachment not filed: %s", e)
            break
        where = f"{settings.mail_archive_folder}/Attachments/{when:%Y}/{sender}/{final}"
        db.execute("UPDATE email_attachments SET saved_to = ? WHERE email_id = ? AND idx = ?",
                   (f"company:{where}", row["id"], meta["idx"]))
        saved.append(where)
    return saved
