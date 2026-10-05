"""File management for the dashboard explorer (Notes & files tab): browse, open, edit, upload,
download, new folder, rename/move and delete, plus "recently viewed / edited" per person.

Scopes as in nas.py: personal (own folder), company (admins + cofounders), people (admins: every
personal folder). These are the person's own actions in the dashboard, so unlike Alfred's chat
tools they may change any file in the scope. Safety:
- paths are normalised and confined to the scope (no "..", no hidden files);
- "delete" moves files and folders to .trash/ in the scope (recoverable on the NAS);
- saving a text file sends the modified time it was opened with; if someone changed it since,
  the save is refused (no silent overwrites); uploads never overwrite unless asked;
- text files up to 2 MB can be edited in the browser; everything else is download/upload.
"""
from __future__ import annotations

from datetime import datetime

from . import db
from .identity.registry import Person
from .nas import (NasError, TRASH, _call, _ensure_dir, _ensure_root, _exists, _local, _root, _smb, clean_path,
                  check_scope)

EDITABLE = (".md", ".txt", ".csv", ".json", ".log", ".yaml", ".yml")
MAX_EDIT_BYTES = 2_000_000
MAX_UPLOAD_BYTES = 50_000_000
RECENT_KEEP = 15


def _paths(person: Person, scope: str, path: str) -> tuple[str, str]:
    rel = clean_path(path)
    if scope == "people" and not rel:
        pass                                  # the share root: everyone's folders
    return _root(person, scope) + ("\\" + rel if rel else ""), rel


def _info(e) -> dict:
    st = e.stat()
    return {"name": e.name, "dir": e.is_dir(), "size": st.st_size if not e.is_dir() else None,
            "modified": _local(st.st_mtime), "mtime": st.st_mtime,
            "editable": (not e.is_dir()) and e.name.lower().endswith(EDITABLE) and st.st_size <= MAX_EDIT_BYTES}


def browse(person: Person, scope: str, path: str = "") -> dict:
    smb = _smb()
    _ensure_root(person, scope)
    full, rel = _paths(person, scope, path)

    def go():
        items = [_info(e) for e in smb.scandir(full) if not e.name.startswith((".", "~$"))]
        items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
        return items
    return {"scope": scope, "path": rel.replace("\\", "/"), "items": _call(go)}


def open_text(person: Person, scope: str, path: str) -> dict:
    smb = _smb()
    full, rel = _paths(person, scope, path)
    if not rel.lower().endswith(EDITABLE):
        raise NasError("Only text files (Markdown, text, CSV, JSON) open in the editor; download the others.")

    def go():
        st = smb.stat(full)
        if st.st_size > MAX_EDIT_BYTES:
            raise NasError("That file is too big for the editor; download it instead.")
        with smb.open_file(full, mode="rb") as f:
            data = f.read()
        return data.decode("utf-8", errors="replace"), st.st_mtime
    text, mtime = _call(go)
    touch(person, scope, rel, "viewed")
    return {"scope": scope, "path": rel.replace("\\", "/"), "text": text, "mtime": mtime}


def save_text(person: Person, scope: str, path: str, text: str, mtime: float | None, create: bool = False) -> dict:
    smb = _smb()
    _ensure_root(person, scope)
    full, rel = _paths(person, scope, path)
    if not rel or not rel.lower().endswith(EDITABLE):
        raise NasError("Give the file a name ending in .md, .txt, .csv or .json.")
    data = text.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        raise NasError("That's too long for one file.")

    def go():
        exists = _exists(smb, full)
        if create and exists:
            raise NasError("A file with that name already exists there.")
        if not create:
            if not exists:
                raise NasError("That file doesn't exist any more (moved or deleted).")
            if mtime is not None and abs(smb.stat(full).st_mtime - float(mtime)) > 1:
                raise NasError("Someone changed this file since you opened it. Reload it, then make your change again.")
        _ensure_dir(smb, full.rsplit("\\", 1)[0])
        with smb.open_file(full, mode="wb") as f:
            f.write(data)
        return smb.stat(full).st_mtime
    new_mtime = _call(go)
    touch(person, scope, rel, "edited")
    db.log_activity("system", f"Files ({scope}): {person.display_name} {'created' if create else 'saved'} "
                    f"{rel.replace(chr(92), '/')}", user_id=person.id)
    return {"saved": rel.replace("\\", "/"), "mtime": new_mtime}


def download(person: Person, scope: str, path: str) -> tuple[str, bytes]:
    smb = _smb()
    full, rel = _paths(person, scope, path)

    def go():
        if smb.stat(full).st_size > MAX_UPLOAD_BYTES:
            raise NasError("That file is too big to download here.")
        with smb.open_file(full, mode="rb") as f:
            return f.read()
    data = _call(go)
    touch(person, scope, rel, "viewed")
    return rel.rsplit("\\", 1)[-1], data


def upload(person: Person, scope: str, folder: str, filename: str, data: bytes, replace: bool = False) -> dict:
    from .uploads import safe_name
    if len(data) > MAX_UPLOAD_BYTES:
        raise NasError("That file is too big (max 50 MB).")
    smb = _smb()
    _ensure_root(person, scope)
    dir_full, dir_rel = _paths(person, scope, folder)
    name = safe_name(filename)
    if name.startswith("."):
        raise NasError("Hidden file names aren't allowed.")

    def go():
        from .uploads import _free_name
        _ensure_dir(smb, dir_full)
        final = name if replace else _free_name(smb, dir_full, name)
        with smb.open_file(dir_full + "\\" + final, mode="wb") as f:
            f.write(data)
        return final
    final = _call(go)
    rel = (dir_rel + "\\" if dir_rel else "") + final
    touch(person, scope, rel, "edited")
    db.log_activity("system", f"Files ({scope}): {person.display_name} uploaded {rel.replace(chr(92), '/')}",
                    user_id=person.id)
    return {"saved": rel.replace("\\", "/")}


def make_folder(person: Person, scope: str, path: str) -> dict:
    smb = _smb()
    _ensure_root(person, scope)
    full, rel = _paths(person, scope, path)
    if not rel:
        raise NasError("Give the folder a name.")

    def go():
        if _exists(smb, full):
            raise NasError("That already exists.")
        smb.makedirs(full)
    _call(go)
    return {"created": rel.replace("\\", "/")}


def move(person: Person, scope: str, path: str, new_path: str) -> dict:
    """Rename or move a file/folder within the scope. Never overwrites."""
    smb = _smb()
    src, src_rel = _paths(person, scope, path)
    dst, dst_rel = _paths(person, scope, new_path)
    if not src_rel or not dst_rel:
        raise NasError("Pick what to move and where to.")
    if (dst_rel + "\\").lower().startswith(src_rel.lower() + "\\"):
        raise NasError("A folder can't be moved into itself.")

    def go():
        if not _exists(smb, src):
            raise NasError("That doesn't exist any more.")
        if _exists(smb, dst):
            raise NasError("Something with that name is already there.")
        _ensure_dir(smb, dst.rsplit("\\", 1)[0])
        smb.rename(src, dst)
    _call(go)
    db.execute("UPDATE nas_recent SET path = ? || substr(path, ?) WHERE user_id = ? AND scope = ? AND "
               "(path = ? OR path LIKE ?)", (dst_rel.replace("\\", "/"), len(src_rel) + 1, person.id, scope,
                                              src_rel.replace("\\", "/"), src_rel.replace("\\", "/") + "/%"))
    db.log_activity("system", f"Files ({scope}): {person.display_name} moved {src_rel.replace(chr(92), '/')} to "
                    f"{dst_rel.replace(chr(92), '/')}", user_id=person.id)
    return {"moved": src_rel.replace("\\", "/"), "to": dst_rel.replace("\\", "/")}


def delete(person: Person, scope: str, path: str) -> dict:
    """Moves a file or folder to .trash/ in the scope (recoverable), never deletes."""
    smb = _smb()
    src, rel = _paths(person, scope, path)
    if not rel:
        raise NasError("Pick a file or folder.")
    if scope == "people" and "\\" not in rel:
        raise NasError("A person's whole folder can't be deleted here (use data deletion on the People tab).")
    root = _root(person, scope)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    def go():
        if not _exists(smb, src):
            raise NasError("That doesn't exist any more.")
        _ensure_dir(smb, f"{root}\\{TRASH}")
        smb.rename(src, f"{root}\\{TRASH}\\{stamp} {rel.rsplit(chr(92), 1)[-1]}")
    _call(go)
    db.execute("DELETE FROM nas_recent WHERE user_id = ? AND scope = ? AND (path = ? OR path LIKE ?)",
               (person.id, scope, rel.replace("\\", "/"), rel.replace("\\", "/") + "/%"))
    db.log_activity("system", f"Files ({scope}): {person.display_name} moved {rel.replace(chr(92), '/')} to the trash",
                    user_id=person.id)
    return {"trashed": rel.replace("\\", "/"), "note": "Moved to .trash on the NAS; it can be restored there."}


# --- recently viewed / edited --------------------------------------------------------------------

def touch(person: Person, scope: str, rel: str, action: str) -> None:
    path = rel.replace("\\", "/")
    db.execute("DELETE FROM nas_recent WHERE user_id = ? AND scope = ? AND path = ? AND action = ?",
               (person.id, scope, path, action))
    db.execute("INSERT INTO nas_recent(user_id, scope, path, action, at) VALUES (?,?,?,?,?)",
               (person.id, scope, path, action, db.now_iso()))
    db.execute("DELETE FROM nas_recent WHERE user_id = ? AND action = ? AND id NOT IN (SELECT id FROM nas_recent "
               "WHERE user_id = ? AND action = ? ORDER BY id DESC LIMIT ?)",
               (person.id, action, person.id, action, RECENT_KEEP))


def recent(person: Person) -> dict:
    out = {}
    for action in ("viewed", "edited"):
        rows = db.query("SELECT scope, path, at FROM nas_recent WHERE user_id = ? AND action = ? ORDER BY id DESC LIMIT ?",
                        (person.id, action, RECENT_KEEP))
        visible = []
        for r in rows:
            try:
                check_scope(person, r["scope"])         # role changed since: don't show what's no longer allowed
            except NasError:
                continue
            visible.append(r)
        out[action] = visible
    return out
