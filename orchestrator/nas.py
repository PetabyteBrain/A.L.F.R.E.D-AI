"""NAS (TrueNAS SCALE) over SMB: notes, lists, files and the Company to-do workbook.

Two shares, two permission scopes (enforced here, before anything touches the NAS):
- "personal": Alfred_Personal/<person's folder>/ — every registered person gets their own folder
  and only ever sees that one (admins included). Notes/ and Lists/ inside it.
- "company":   the Company share — admins and cofounders only. Existing company files are
  read-only for Alfred; it writes only Markdown notes (Notes/), lists (Lists/) and the to-do workbook.

Writes are limited to .md files under Notes/ and Lists/ (and the to-do cells, see company_todo.py).
"Deleting" a note moves it to .trash/ in the same scope. Paths are normalised and can't leave
their scope (no "..", no absolute paths, no hidden files except Alfred's own folders).
The SMB client is blocking, so every public function here is wrapped with asyncio.to_thread
by the callers (tools).
"""
from __future__ import annotations

import io
import logging
import re
import threading
import unicodedata
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)

COMPANY_ROLES = ("admin", "cofounder")
NOTES, LISTS, TRASH = "Notes", "Lists", ".trash"
TEXT_EXT = (".md", ".txt", ".csv", ".json", ".log")
READABLE_EXT = TEXT_EXT + (".docx", ".xlsx", ".pdf")
MAX_READ_CHARS = 20_000
MAX_READ_BYTES = 15_000_000
MAX_WRITE_CHARS = 50_000
SEARCH_MAX_FILES = 400
_FOOTER = re.compile(r"\n*---\n_Last updated [^\n]*_\s*$")
_session_lock = threading.Lock()
_session_ready = False


class NasError(ValueError):
    """Message is safe to show to the user / the model."""


def enabled() -> bool:
    return bool(settings.nas_host and settings.nas_user and settings.nas_password)


# --- connection ------------------------------------------------------------------------------

def _smb():
    global _session_ready
    import smbclient
    if not enabled():
        raise NasError("The NAS isn't configured (NAS_HOST / NAS_USER / NAS_PASSWORD).")
    with _session_lock:
        if not _session_ready:
            smbclient.register_session(settings.nas_host, username=settings.nas_user,
                                       password=settings.nas_password, connection_timeout=10)
            _session_ready = True
    return smbclient


def _reset() -> None:
    global _session_ready
    with _session_lock:
        _session_ready = False
    try:
        import smbclient
        smbclient.reset_connection_cache()
    except Exception:  # noqa: BLE001
        pass


def _call(fn, *a, **kw):
    """One retry after a dropped connection; SMB errors become NasError with a plain message."""
    from smbprotocol.exceptions import SMBException
    for attempt in (1, 2):
        try:
            return fn(*a, **kw)
        except NasError:
            raise
        except FileNotFoundError:
            raise NasError("Not found on the NAS.")
        except (SMBException, OSError) as e:
            text = str(e)
            if "ACCESS_DENIED" in text or "0xc0000022" in text:
                raise NasError("The NAS refused access (check the alfred-ai permissions on that share).")
            if "SHARING_VIOLATION" in text or "0xc0000043" in text:
                raise NasError("That file is open somewhere else (e.g. in Excel). Close it and try again.")
            if attempt == 2:
                log.warning("NAS error: %s", text[:300])
                raise NasError("The NAS isn't reachable right now.")
            _reset()


# --- scopes & paths --------------------------------------------------------------------------

def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "person"


def personal_folder(person: Person) -> str:
    """The person's folder name in the personal share (fixed at first use, so renames don't move it)."""
    row = db.query_one("SELECT folder FROM nas_folders WHERE user_id = ?", (person.id,))
    if row:
        return row["folder"]
    base = _slug(person.display_name.split()[0])
    taken = {r["folder"] for r in db.query("SELECT folder FROM nas_folders")}
    folder = base if base not in taken else f"{base}-{person.id}"
    db.execute("INSERT INTO nas_folders(user_id, folder, created_at) VALUES (?,?,?)", (person.id, folder, db.now_iso()))
    return folder


def check_scope(person: Person, scope: str) -> None:
    if scope == "company":
        if person.role not in COMPANY_ROLES:
            raise NasError("The Company files are only for admins and cofounders.")
    elif scope != "personal":
        raise NasError("Scope must be 'personal' or 'company'.")


def _root(person: Person, scope: str) -> str:
    check_scope(person, scope)
    h = settings.nas_host
    if scope == "company":
        return rf"\\{h}\{settings.nas_share_company}"
    return rf"\\{h}\{settings.nas_share_personal}\{personal_folder(person)}"


def clean_path(path: str, *, allow_trash: bool = False) -> str:
    """'Notes/Clients/acme.md' -> 'Notes\\Clients\\acme.md'; refuses anything that could escape."""
    parts = []
    for p in re.split(r"[\\/]+", str(path or "").strip()):
        p = p.strip()
        if not p or p == ".":
            continue
        if p == ".." or ":" in p or any(ord(c) < 32 for c in p) or re.search(r'[<>"|?*]', p):
            raise NasError("That path isn't allowed.")
        if p.startswith(".") and not (allow_trash and p == TRASH):
            raise NasError("Hidden files and folders aren't available.")
        parts.append(p)
    return "\\".join(parts)


def _full(person: Person, scope: str, path: str, **kw) -> str:
    rel = clean_path(path, **kw)
    return _root(person, scope) + ("\\" + rel if rel else "")


def _ensure_dir(smb, full_dir: str) -> None:
    try:
        smb.makedirs(full_dir, exist_ok=True)
    except FileExistsError:
        pass


def _ensure_root(person: Person, scope: str) -> None:
    if scope == "personal":
        smb = _smb()
        _call(_ensure_dir, smb, _root(person, scope))


# --- files: list / read / search -------------------------------------------------------------

def list_dir(person: Person, scope: str, path: str = "") -> dict:
    smb = _smb()
    _ensure_root(person, scope)
    full = _full(person, scope, path)

    def go():
        out = {"folders": [], "files": []}
        for e in smb.scandir(full):
            if e.name.startswith((".", "~$")):
                continue
            if e.is_dir():
                out["folders"].append(e.name)
            else:
                st = e.stat()
                out["files"].append({"name": e.name, "size_kb": round(st.st_size / 1024, 1),
                                     "modified": _local(st.st_mtime)})
        out["folders"].sort(key=str.lower)
        out["files"].sort(key=lambda f: f["name"].lower())
        return out
    return {"scope": scope, "path": clean_path(path).replace("\\", "/"), **_call(go)}


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(settings.timezone)).strftime("%d %b %Y %H:%M")


def _bytes(smb, full: str) -> bytes:
    st = smb.stat(full)
    if st.st_size > MAX_READ_BYTES:
        raise NasError("That file is too big to read here.")
    with smb.open_file(full, mode="rb") as f:
        return f.read()


def extract_text(name: str, data: bytes) -> str:
    low = name.lower()
    if low.endswith(TEXT_EXT):
        return data.decode("utf-8", errors="replace")
    if low.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    if low.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"## Sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                vals = ["" if v is None else str(v) for v in row]
                if any(vals):
                    lines.append(" | ".join(vals).rstrip(" |"))
        return "\n".join(lines)
    if low.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:60])
    raise NasError("I can read text, Markdown, Word (.docx), Excel (.xlsx) and PDF files; not that type.")


def read_file(person: Person, scope: str, path: str) -> dict:
    smb = _smb()
    full = _full(person, scope, path)
    name = full.rsplit("\\", 1)[-1]
    if not name.lower().endswith(READABLE_EXT):
        raise NasError("I can read text, Markdown, Word (.docx), Excel (.xlsx) and PDF files; not that type.")
    text = extract_text(name, _call(_bytes, smb, full))
    cut = len(text) > MAX_READ_CHARS
    return {"scope": scope, "path": clean_path(path).replace("\\", "/"), "text": text[:MAX_READ_CHARS],
            **({"truncated": f"first {MAX_READ_CHARS} of {len(text)} characters"} if cut else {})}


def search(person: Person, scope: str, query: str, path: str = "") -> dict:
    """File names and the text of readable files (Markdown/text/Word/Excel/PDF) that contain the words."""
    words = [w.lower() for w in re.findall(r"[\w'’-]{2,}", query or "")][:8]
    if not words:
        raise NasError("Give me a word or two to search for.")
    smb = _smb()
    _ensure_root(person, scope)
    root = _full(person, scope, path)
    base = _root(person, scope)

    def go():
        hits, scanned = [], 0
        for dirpath, dirs, files in smb.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.startswith(("~$", ".")):
                    continue
                rel = (dirpath + "\\" + f)[len(base) + 1:].replace("\\", "/")
                name_hit = all(w in rel.lower() for w in words)
                snippet = ""
                if f.lower().endswith(READABLE_EXT) and scanned < SEARCH_MAX_FILES:
                    scanned += 1
                    try:
                        text = extract_text(f, _bytes(smb, dirpath + "\\" + f))
                    except Exception:  # noqa: BLE001 — unreadable file: name match only
                        text = ""
                    low = text.lower()
                    if all(w in low for w in words):
                        i = low.find(words[0])
                        snippet = " ".join(text[max(0, i - 120):i + 200].split())
                if name_hit or snippet:
                    hits.append({"path": rel, **({"match": snippet} if snippet else {"match": "(file name)"})})
                if len(hits) >= 15:
                    return hits
        return hits
    hits = _call(go)
    return {"scope": scope, "matches": hits} if hits else {"scope": scope, "matches": [],
                                                           "note": "Nothing found. Try other words."}


# --- notes (Markdown) ------------------------------------------------------------------------

def _note_path(title_or_path: str, folder: str = "") -> str:
    """A note's path inside Notes/: 'Clients/Acme meeting' -> 'Notes\\Clients\\Acme meeting.md'."""
    raw = str(title_or_path or "").strip()
    if not raw:
        raise NasError("Give the note a title.")
    raw = re.sub(r"^(?:Notes[\\/])", "", raw, flags=re.I)
    if folder:
        raw = f"{folder.strip('/')}/{raw}"
    rel = clean_path(raw)
    name = rel.rsplit("\\", 1)[-1]
    name = re.sub(r"[^\w .,()&'+-]", "", name, flags=re.UNICODE).strip()[:100] or "note"
    rel = (rel.rsplit("\\", 1)[0] + "\\" if "\\" in rel else "") + name
    if not rel.lower().endswith(".md"):
        rel += ".md"
    return f"{NOTES}\\{rel}"


def _write_text(smb, full: str, text: str) -> None:
    _ensure_dir(smb, full.rsplit("\\", 1)[0])
    with smb.open_file(full, mode="w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read_text(smb, full: str) -> str:
    with smb.open_file(full, mode="r", encoding="utf-8") as f:
        return f.read()


def _exists(smb, full: str) -> bool:
    try:
        smb.stat(full)
        return True
    except (FileNotFoundError, OSError):
        return False


def save_note(person: Person, scope: str, title: str, content: str, folder: str = "", mode: str = "create") -> dict:
    """mode: create (fails if it exists), replace, append."""
    content = str(content or "").strip()
    if len(content) > MAX_WRITE_CHARS:
        raise NasError("That note is too long.")
    if mode not in ("create", "replace", "append"):
        raise NasError("mode must be create, replace or append.")
    smb = _smb()
    _ensure_root(person, scope)
    rel = _note_path(title, folder)
    full = _root(person, scope) + "\\" + rel
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")

    def go():
        exists = _exists(smb, full)
        if mode == "create" and exists:
            raise NasError("A note with that name already exists; append to it or choose another title.")
        if mode == "append" and exists:
            text = _read_text(smb, full).rstrip() + f"\n\n## {stamp} ({person.display_name.split()[0]})\n\n{content}\n"
        else:
            name = rel.rsplit("\\", 1)[-1][:-3]
            body = _FOOTER.sub("", content)              # an edited note: don't stack "Last updated" lines
            text = body if body.lstrip().startswith("# ") else f"# {name}\n\n{body}"
            text = text.rstrip() + f"\n\n---\n_Last updated {stamp} by {person.display_name} via {settings.assistant_name}_\n"
        _write_text(smb, full, text)
        return exists
    existed = _call(go)
    db.log_activity("system", f"Notes ({scope}): {person.display_name} {'updated' if existed else 'created'} "
                    f"{rel.replace(chr(92), '/')}", user_id=person.id)
    return {"saved": rel.replace("\\", "/"), "scope": scope, "created": not existed}


def move_note(person: Person, scope: str, path: str, new_path: str) -> dict:
    smb = _smb()
    src_rel = _note_path(path)
    dst_rel = _note_path(new_path)
    root = _root(person, scope)

    def go():
        if _exists(smb, root + "\\" + dst_rel):
            raise NasError("There's already a note at the new place.")
        _ensure_dir(smb, (root + "\\" + dst_rel).rsplit("\\", 1)[0])
        smb.rename(root + "\\" + src_rel, root + "\\" + dst_rel)
    _call(go)
    return {"moved": src_rel.replace("\\", "/"), "to": dst_rel.replace("\\", "/")}


def trash_note(person: Person, scope: str, path: str) -> dict:
    """Moves the note to .trash/ (recoverable on the NAS), never deletes."""
    smb = _smb()
    rel = _note_path(path)
    root = _root(person, scope)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{root}\\{TRASH}\\{stamp} {rel.rsplit(chr(92), 1)[-1]}"

    def go():
        _ensure_dir(smb, f"{root}\\{TRASH}")
        smb.rename(root + "\\" + rel, dst)
    _call(go)
    db.log_activity("system", f"Notes ({scope}): {person.display_name} moved {rel.replace(chr(92), '/')} to the trash",
                    user_id=person.id)
    return {"trashed": rel.replace("\\", "/"), "note": "Moved to .trash on the NAS; it can be restored there."}


def list_notes(person: Person, scope: str, folder: str = "") -> dict:
    smb = _smb()
    _ensure_root(person, scope)
    base = _root(person, scope) + "\\" + NOTES
    start = base + ("\\" + clean_path(folder) if folder else "")

    def go():
        if not _exists(smb, start):
            return []
        out = []
        for dirpath, dirs, files in smb.walk(start):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.lower().endswith(".md") and not f.startswith("."):
                    out.append((dirpath + "\\" + f)[len(base) + 1:].replace("\\", "/"))
        return sorted(out, key=str.lower)
    notes = _call(go)
    return {"scope": scope, "notes": notes[:200], "count": len(notes)}


# --- lists (Markdown checklists) -------------------------------------------------------------

_ITEM = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")


def _list_path(name: str) -> str:
    n = re.sub(r"[^\w .,()&'+-]", "", str(name or ""), flags=re.UNICODE).strip()[:80]
    if not n:
        raise NasError("Which list?")
    return f"{LISTS}\\{n}.md"


def _parse_list(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if m:
            items.append({"done": m.group(1).lower() == "x", "text": m.group(2).strip()})
    return items


def _render_list(name: str, items: list[dict]) -> str:
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")
    body = "\n".join(f"- [{'x' if i['done'] else ' '}] {i['text']}" for i in items)
    return f"# {name}\n\n{body}\n\n---\n_Last updated {stamp} via {settings.assistant_name}_\n"


def _match(items: list[dict], text: str) -> int:
    t = text.strip().lower()
    exact = [i for i, it in enumerate(items) if it["text"].lower() == t]
    part = [i for i, it in enumerate(items) if t in it["text"].lower()]
    for group in (exact, part):
        if len(group) == 1:
            return group[0]
        if len(group) > 1:
            raise NasError("More than one item matches: " + ", ".join(items[i]["text"] for i in group))
    raise NasError(f"'{text}' isn't on that list.")


def list_op(person: Person, scope: str, name: str, op: str, items: list[str] | None = None) -> dict:
    """op: show | create | add | check | uncheck | remove | clear_done."""
    smb = _smb()
    _ensure_root(person, scope)
    rel = _list_path(name)
    full = _root(person, scope) + "\\" + rel
    title = rel.rsplit("\\", 1)[-1][:-3]
    items = [str(i).strip()[:200] for i in (items or []) if str(i).strip()]

    def go():
        cur = _parse_list(_read_text(smb, full)) if _exists(smb, full) else None
        if op == "show":
            if cur is None:
                raise NasError(f"There's no list called '{title}'.")
            return cur
        if op == "create":
            if cur is not None:
                raise NasError(f"A list called '{title}' already exists.")
            cur = []
        cur = cur or []
        if op == "create":
            pass
        elif op == "add":
            if not items:
                raise NasError("What should I add?")
            have = {i["text"].lower() for i in cur}
            for t in items:
                if t.lower() not in have:
                    cur.append({"done": False, "text": t})
                    have.add(t.lower())
        elif op in ("check", "uncheck", "remove"):
            if not items:
                raise NasError("Which item?")
            for t in items:
                i = _match(cur, t)
                if op == "remove":
                    cur.pop(i)
                else:
                    cur[i]["done"] = op == "check"
        elif op == "clear_done":
            cur = [i for i in cur if not i["done"]]
        else:
            raise NasError("Unknown list operation.")
        _write_text(smb, full, _render_list(title, cur))
        return cur
    result = _call(go)
    if op != "show":
        db.log_activity("system", f"Lists ({scope}): {person.display_name} {op} on '{title}'", user_id=person.id)
    return {"list": title, "scope": scope, "open": [i["text"] for i in result if not i["done"]],
            "done": [i["text"] for i in result if i["done"]]}


def all_lists(person: Person, scope: str) -> dict:
    smb = _smb()
    _ensure_root(person, scope)
    base = _root(person, scope) + "\\" + LISTS

    def go():
        if not _exists(smb, base):
            return []
        return sorted((e.name[:-3] for e in smb.scandir(base) if e.name.lower().endswith(".md") and e.is_file()),
                      key=str.lower)
    return {"scope": scope, "lists": _call(go)}


# --- health / privacy --------------------------------------------------------------------------

def health() -> tuple[bool, str]:
    if not enabled():
        return False, "not configured"
    smb = _smb()
    h = settings.nas_host
    ok, parts = True, []
    for label, share in (("Company", settings.nas_share_company), ("personal", settings.nas_share_personal)):
        try:
            _call(smb.listdir, rf"\\{h}\{share}")
            parts.append(f"{label} ok")
        except NasError as e:
            ok = False
            parts.append(f"{label}: {e}")
    return ok, " · ".join(parts)


def export_personal(person: Person, zf: zipfile.ZipFile) -> int:
    """Adds the person's NAS folder (notes, lists, files) to their data export. Returns file count."""
    if not enabled():
        return 0
    smb = _smb()
    root = rf"\\{settings.nas_host}\{settings.nas_share_personal}\{personal_folder(person)}"

    def go():
        n = 0
        if not _exists(smb, root):
            return 0
        for dirpath, dirs, files in smb.walk(root):
            for f in files:
                full = dirpath + "\\" + f
                zf.writestr("nas/" + full[len(root) + 1:].replace("\\", "/"), _bytes(smb, full))
                n += 1
        return n
    return _call(go)


def erase_personal(user_id: int) -> bool:
    """Deletes the person's NAS folder (data deletion). True if something was removed."""
    row = db.query_one("SELECT folder FROM nas_folders WHERE user_id = ?", (user_id,))
    if not row or not enabled():
        return False
    smb = _smb()
    root = rf"\\{settings.nas_host}\{settings.nas_share_personal}\{row['folder']}"

    def go():
        if _exists(smb, root):
            from smbclient import shutil as smb_shutil
            smb_shutil.rmtree(root)
            return True
        return False
    done = _call(go)
    db.execute("DELETE FROM nas_folders WHERE user_id = ?", (user_id,))
    return done
