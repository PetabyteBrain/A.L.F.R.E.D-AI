"""Files and photos sent to Alfred on Telegram -> the NAS (#36), and receipts (#15).

A file sent on Telegram is downloaded into a short-lived inbox on the Pi (data/inbox, 1 hour)
and Alfred is told about it ("upload #3: offer.pdf, 220 KB"). It then saves it where the person
says (`nas_save_upload`, same scopes as notes: personal = own folder, company = admins/cofounders),
or asks where. Photos (and the text of PDFs) are shown to the model too, so it can read a
receipt or describe a picture. Receipts: `log_receipt` files the image under
Receipts/<year>/ and appends a line to Receipts/receipts-<year>.csv (opens in Excel).
Nothing is ever overwritten: an existing name gets " (2)", " (3)", ...
"""
from __future__ import annotations

import csv
import io
import itertools
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

TTL_S = 3600
MAX_BYTES = 20_000_000              # Telegram bot downloads stop at 20 MB anyway
MAX_IMAGE_FOR_MODEL = 4_500_000     # the API takes images up to 5 MB
ALLOWED = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".ppt", ".pptx", ".odt", ".ods",
           ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".zip")
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
               ".gif": "image/gif"}
_ids = itertools.count(1)


@dataclass
class Upload:
    id: int
    user_id: int
    name: str
    path: Path
    size: int
    created: float = field(default_factory=time.time)


_inbox: dict[int, Upload] = {}


class UploadError(ValueError):
    """Message is safe to show to the user / the model."""


def _dir() -> Path:
    d = settings.db_path.parent / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    stem, dot, ext = (name or "file").rpartition(".")
    if not dot:
        stem, ext = ext, ""
    stem = re.sub(r"[^\w .,()&'+-]", "", stem, flags=re.UNICODE).strip(" .")[:80] or "file"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8].lower()
    return f"{stem}.{ext}" if ext else stem


def add(person: Person, name: str, data: bytes) -> Upload:
    prune()
    name = safe_name(name)
    if not name.lower().endswith(ALLOWED):
        raise UploadError("I can file documents, spreadsheets, PDFs, text and pictures; not that type of file.")
    if len(data) > MAX_BYTES:
        raise UploadError("That file is too big (max 20 MB).")
    uid = next(_ids)
    path = _dir() / f"{uid}-{name}"
    path.write_bytes(data)
    up = Upload(uid, person.id, name, path, len(data))
    _inbox[uid] = up
    return up


def get(person: Person, upload_id: int) -> Upload:
    up = _inbox.get(int(upload_id))
    if not up or up.user_id != person.id or not up.path.exists():
        raise UploadError("I don't have that file any more (files wait 1 hour); please send it again.")
    return up


def done(up: Upload) -> None:
    _inbox.pop(up.id, None)
    up.path.unlink(missing_ok=True)


def prune() -> None:
    now = time.time()
    for uid, up in list(_inbox.items()):
        if now - up.created > TTL_S:
            done(up)
    for f in _dir().glob("*"):                       # leftovers from before a restart
        if now - f.stat().st_mtime > TTL_S:
            f.unlink(missing_ok=True)


def describe(up: Upload, caption: str = "", via: str = "Telegram") -> str:
    """What the model is told about an upload (as part of the person's message)."""
    how = "attached a file to the email" if via == "email" else f"sent a file on {via}"
    return (f"[The person {how}: upload #{up.id}, \"{up.name}\", {up.size // 1024 or 1} KB."
            + (f" Caption: {caption}" if caption else " No caption.")
            + " To keep it, use nas_save_upload (ask where if they didn't say); for a receipt, log_receipt.]")


def model_attachments(up: Upload) -> tuple[list[dict], str]:
    """Image blocks for photos; extracted text for PDFs/Word/Excel (first part)."""
    ext = "." + up.name.rsplit(".", 1)[-1].lower() if "." in up.name else ""
    if ext in IMAGE_TYPES and up.size <= MAX_IMAGE_FOR_MODEL:
        import base64
        return [{"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[ext],
                                             "data": base64.b64encode(up.path.read_bytes()).decode()}}], ""
    if ext in (".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv"):
        from .nas import extract_text
        try:
            text = extract_text(up.name, up.path.read_bytes())
        except Exception:  # noqa: BLE001
            return [], ""
        return [], f"\n[Text of the file (start):]\n{text[:4000]}"
    return [], ""


# --- saving to the NAS -------------------------------------------------------------------------

def _free_name(smb, folder_full: str, name: str) -> str:
    from .nas import _exists
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 1
    while _exists(smb, folder_full + "\\" + candidate):
        n += 1
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
    return candidate


def save(person: Person, upload_id: int, scope: str, folder: str, filename: str = "") -> dict:
    from . import nas
    up = get(person, upload_id)
    name = safe_name(filename) if filename else up.name
    if "." not in name and "." in up.name:
        name += "." + up.name.rsplit(".", 1)[-1]
    if not name.lower().endswith(ALLOWED):
        raise UploadError("Keep the file's type (e.g. .pdf) in the name.")
    smb = nas._smb()
    nas._ensure_root(person, scope)
    root = nas._root(person, scope)
    rel_folder = nas.clean_path(folder)

    def go():
        target_dir = root + ("\\" + rel_folder if rel_folder else "")
        nas._ensure_dir(smb, target_dir)
        final = _free_name(smb, target_dir, name)
        with smb.open_file(target_dir + "\\" + final, mode="wb") as f:
            f.write(up.path.read_bytes())
        return final
    final = nas._call(go)
    done(up)
    where = (rel_folder.replace("\\", "/") + "/" if rel_folder else "") + final
    db.log_activity("system", f"Files ({scope}): {person.display_name} saved {where}", user_id=person.id)
    return {"saved": where, "scope": scope}


def log_receipt(person: Person, scope: str, date: str, shop: str, amount: float, currency: str = "CHF",
                category: str = "", note: str = "", upload_id: int | None = None) -> dict:
    """Appends to Receipts/receipts-<year>.csv and files the receipt image/PDF under Receipts/<year>/."""
    from . import nas
    try:
        day = datetime.fromisoformat(str(date)[:10]).date()
    except ValueError:
        raise UploadError("Give the receipt date as YYYY-MM-DD.")
    shop = " ".join(str(shop or "").split())[:80]
    if not shop:
        raise UploadError("Which shop or company is it from?")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise UploadError("The amount must be a number.")
    currency = re.sub(r"[^A-Z]", "", str(currency or "CHF").upper())[:3] or "CHF"
    up = get(person, upload_id) if upload_id else None
    smb = nas._smb()
    nas._ensure_root(person, scope)
    root = nas._root(person, scope)
    year_dir = f"{root}\\Receipts\\{day.year}"
    csv_path = f"{root}\\Receipts\\receipts-{day.year}.csv"

    def go():
        nas._ensure_dir(smb, year_dir)
        file_name = ""
        if up:
            ext = up.name.rsplit(".", 1)[-1] if "." in up.name else "jpg"
            file_name = _free_name(smb, year_dir, safe_name(f"{day.isoformat()} {shop} {amount:.2f}.{ext}"))
            with smb.open_file(year_dir + "\\" + file_name, mode="wb") as f:
                f.write(up.path.read_bytes())
        exists = nas._exists(smb, csv_path)
        old = b""
        if exists:
            with smb.open_file(csv_path, mode="rb") as f:
                old = f.read()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")              # ; = what Excel expects in Switzerland
        if not exists:
            w.writerow(["date", "shop", "amount", "currency", "category", "note", "file", "added_by", "added_at"])
        w.writerow([day.isoformat(), shop, f"{amount:.2f}", currency, category[:40], note[:200],
                    f"{day.year}/{file_name}" if file_name else "", person.display_name,
                    datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")])
        new = buf.getvalue().encode("utf-8")
        with smb.open_file(csv_path, mode="wb") as f:
            f.write((old if exists else b"\xef\xbb\xbf") + new)
        return file_name
    file_name = nas._call(go)
    if up:
        done(up)
    db.log_activity("system", f"Receipts ({scope}): {person.display_name} logged {shop} {amount:.2f} {currency}",
                    user_id=person.id)
    return {"logged": f"{day.isoformat()} · {shop} · {amount:.2f} {currency}", "scope": scope,
            "csv": f"Receipts/receipts-{day.year}.csv", **({"file": f"Receipts/{day.year}/{file_name}"} if file_name else {})}
