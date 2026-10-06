"""Alfred tidies a person's own notes on request: folder structure and note contents, as two separate
kinds of plan. Only the person's personal folder, only .md / .txt files.

Flow (like outside calls): Alfred looks (`overview`, reading notes), proposes a plan (`preview`), shows it
and stops; only after the person's yes, in a later message, does `apply` run it. Nothing is ever lost:
moved files keep their content, replaced and removed notes go to the NAS trash (.trash/), and merging
and splitting copy the text exactly (done here, not retyped by the model).

structure ops: {"op": "folder", "path"} · {"op": "move", "from", "to"} · {"op": "remove_empty", "path"}
content ops:   {"op": "write", "path", "text"} (reformat or create) · {"op": "merge", "into", "sources": [..]}
               · {"op": "split", "path", "level": 1|2, "folder"?} · {"op": "trash", "path"}
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import db, nas, nas_files
from .identity.registry import Person

SCOPE = "personal"
NOTE_EXT = (".md", ".txt")
KINDS = ("structure", "content")
CONFIRM_TTL_S = 30 * 60
MAX_OPS = 60
MAX_ENTRIES = 400
_pending: dict[tuple[int, str], dict] = {}


class TidyError(ValueError):
    """Message is safe to show to the user / the model."""


def _norm(path: str) -> str:
    return nas.clean_path(path).replace("\\", "/")


def _is_note(path: str) -> bool:
    return path.lower().endswith(NOTE_EXT)


def _exists(person: Person, path: str) -> bool:
    smb = nas._smb()
    full, _ = nas_files._paths(person, SCOPE, path)
    return nas._call(lambda: nas._exists(smb, full))


def overview(person: Person) -> dict:
    """The whole personal folder: folders with counts, notes with their first heading, other files."""
    smb = nas._smb()
    nas._ensure_root(person, SCOPE)
    root, _ = nas_files._paths(person, SCOPE, "")
    folders, notes, other = [], [], []

    def walk(full: str, rel: str, depth: int):
        if depth > 6 or len(notes) + len(other) + len(folders) > MAX_ENTRIES:
            return
        for e in smb.scandir(full):
            if e.name.startswith((".", "~$")):
                continue
            path = f"{rel}/{e.name}" if rel else e.name
            if e.is_dir():
                folders.append(path)
                walk(full + "\\" + e.name, path, depth + 1)
            elif e.name.lower().endswith(NOTE_EXT):
                head = ""
                try:
                    with smb.open_file(full + "\\" + e.name, mode="rb") as f:
                        first = f.read(400).decode("utf-8", errors="replace")
                    head = next((ln.strip("# ").strip() for ln in first.splitlines() if ln.strip()), "")[:80]
                except Exception:  # noqa: BLE001 — a title is optional
                    pass
                notes.append({"path": path, "size": e.stat().st_size, "starts_with": head})
            else:
                other.append(path)
    nas._call(lambda: walk(root, "", 0))
    counts = {f: sum(1 for n in notes if n["path"].startswith(f + "/")) for f in folders}
    return {"folders": [{"path": f, "notes": counts[f]} for f in sorted(folders)], "notes": notes,
            "other_files": other[:80],
            "rules": "Only .md/.txt notes can be moved, merged, split or rewritten; other files stay where they are.",
            "next": "If they asked for a tidy-up, now call notes_reorganize (folders) or notes_rewrite (contents) with "
                    "your proposed steps. That changes nothing yet; the person sees the steps and says yes or no. "
                    "Don't list suggestions in your own words instead."}


# --- validation --------------------------------------------------------------------------------

def _validate(person: Person, kind: str, ops: list) -> tuple[list[dict], list[str]]:
    if kind not in KINDS:
        raise TidyError("Plan kind must be structure or content.")
    if not isinstance(ops, list) or not ops:
        raise TidyError("The plan is empty.")
    if len(ops) > MAX_OPS:
        raise TidyError(f"That's more than {MAX_OPS} steps; split it into smaller plans.")
    allowed = {"structure": {"folder", "move", "remove_empty"}, "content": {"write", "merge", "split", "trash"}}[kind]
    clean, lines, made, gone = [], [], set(), set()
    for i, raw in enumerate(ops, 1):
        if not isinstance(raw, dict) or raw.get("op") not in allowed:
            other = "content" if kind == "structure" else "structure"
            raise TidyError(f"Step {i}: '{(raw or {}).get('op')}' isn't a {kind} step (that belongs in a {other} plan).")
        op = raw["op"]
        if op == "folder":
            p = _norm(raw.get("path", ""))
            if not p:
                raise TidyError(f"Step {i}: give the folder a name.")
            clean.append({"op": op, "path": p}); made.add(p.lower()); lines.append(f"New folder: {p}")
        elif op == "move":
            a, b = _norm(raw.get("from", "")), _norm(raw.get("to", ""))
            if not (_is_note(a) and _is_note(b)):
                raise TidyError(f"Step {i}: only .md/.txt notes can be moved here ({a} -> {b}).")
            if not _exists(person, a) or a.lower() in gone:
                raise TidyError(f"Step {i}: {a} doesn't exist.")
            if _exists(person, b) or b.lower() in made:
                raise TidyError(f"Step {i}: {b} already exists; pick another name.")
            clean.append({"op": op, "from": a, "to": b}); gone.add(a.lower()); made.add(b.lower())
            lines.append(f"Move {a} -> {b}")
        elif op == "remove_empty":
            p = _norm(raw.get("path", ""))
            clean.append({"op": op, "path": p}); lines.append(f"Remove the folder {p} if it's empty")
        elif op == "write":
            p, text = _norm(raw.get("path", "")), str(raw.get("text") or "")
            if not _is_note(p):
                raise TidyError(f"Step {i}: notes must end in .md or .txt ({p}).")
            if not text.strip():
                raise TidyError(f"Step {i}: no text for {p}.")
            exists = _exists(person, p)
            clean.append({"op": op, "path": p, "text": text})
            lines.append(f"{'Rewrite' if exists else 'Create'} {p} ({len(text.split())} words"
                         + ("; the old version goes to the trash" if exists else "") + ")")
        elif op == "merge":
            into, sources = _norm(raw.get("into", "")), [_norm(s) for s in (raw.get("sources") or [])]
            if not _is_note(into) or len(sources) < 2 or not all(_is_note(s) for s in sources):
                raise TidyError(f"Step {i}: merge needs a .md/.txt target and at least two .md/.txt notes.")
            missing = [s for s in sources if not _exists(person, s)]
            if missing:
                raise TidyError(f"Step {i}: not found: {', '.join(missing)}.")
            clean.append({"op": op, "into": into, "sources": sources})
            lines.append(f"Combine {', '.join(sources)} into {into} (word for word, each under its own heading; "
                         "the originals go to the trash)")
        elif op == "split":
            p, level = _norm(raw.get("path", "")), int(raw.get("level") or 2)
            folder = _norm(raw.get("folder", "")) if raw.get("folder") else p.rsplit("/", 1)[0] if "/" in p else ""
            if not _is_note(p) or not _exists(person, p) or level not in (1, 2):
                raise TidyError(f"Step {i}: split needs an existing .md/.txt note and level 1 or 2.")
            clean.append({"op": op, "path": p, "level": level, "folder": folder})
            lines.append(f"Split {p} into one note per {'#' * level} heading in {folder or 'the top folder'} "
                         "(word for word; the original goes to the trash)")
        elif op == "trash":
            p = _norm(raw.get("path", ""))
            if not _is_note(p) or not _exists(person, p):
                raise TidyError(f"Step {i}: {p} isn't an existing .md/.txt note.")
            clean.append({"op": op, "path": p}); lines.append(f"Move {p} to the trash")
    return clean, lines


# --- preview / apply ---------------------------------------------------------------------------

def _last_user_turn(session_id: str | None) -> int:
    from .external_calls import _last_user_turn as last
    return last(session_id)


def preview(person: Person, session_id: str | None, kind: str, ops: list) -> dict:
    clean, lines = _validate(person, kind, ops)
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:16]
    _pending[(person.id, kind)] = {"ops": clean, "digest": digest, "at": time.monotonic(),
                                   "user_turn": _last_user_turn(session_id)}
    return {"prepared": True, "applied": False, "kind": kind, "steps": lines,
            "next": "Show these steps to the person in a short list and stop. Only after they say yes, in their own "
                    f"message, call notes_tidy_confirm with kind={kind}. If they want changes, prepare the plan again."}


def _split_sections(text: str, level: int) -> tuple[str, list[tuple[str, str]]]:
    mark = "#" * level + " "
    intro, sections, cur_title, cur = [], [], None, []
    for line in text.splitlines():
        if line.startswith(mark) and not line.startswith(mark + "#"):
            if cur_title is not None:
                sections.append((cur_title, "\n".join(cur).strip() + "\n"))
            cur_title, cur = line[len(mark):].strip(), [line]
        elif cur_title is None:
            intro.append(line)
        else:
            cur.append(line)
    if cur_title is not None:
        sections.append((cur_title, "\n".join(cur).strip() + "\n"))
    return "\n".join(intro).strip(), sections


def _read(person: Person, path: str) -> str:
    return nas_files.open_text(person, SCOPE, path)["text"]


def _write(person: Person, path: str, text: str) -> None:
    if _exists(person, path):
        nas_files.delete(person, SCOPE, path)                      # old version to the trash
    nas_files.save_text(person, SCOPE, path, text, None, create=True)


def _free(person: Person, path: str) -> str:
    stem, ext = path.rsplit(".", 1)
    n, cand = 2, path
    while _exists(person, cand):
        cand, n = f"{stem} ({n}).{ext}", n + 1
    return cand


def apply(person: Person, session_id: str | None, kind: str) -> dict:
    pending = _pending.get((person.id, kind))
    if not pending or time.monotonic() - pending["at"] > CONFIRM_TTL_S:
        _pending.pop((person.id, kind), None)
        raise TidyError("There's no prepared plan to carry out (or it's older than 30 minutes). Prepare it again.")
    if _last_user_turn(session_id) <= pending["user_turn"]:
        return {"applied": False, "note": "The person hasn't answered yet: wait for their yes in their own message."}
    _pending.pop((person.id, kind), None)
    done = []
    try:
        for op in pending["ops"]:
            o = op["op"]
            if o == "folder":
                if not _exists(person, op["path"]):
                    nas_files.make_folder(person, SCOPE, op["path"])
                done.append(f"folder {op['path']}")
            elif o == "move":
                nas_files.move(person, SCOPE, op["from"], op["to"]); done.append(f"moved {op['from']} -> {op['to']}")
            elif o == "remove_empty":
                if _exists(person, op["path"]) and not nas_files.browse(person, SCOPE, op["path"])["items"]:
                    nas_files.delete(person, SCOPE, op["path"]); done.append(f"removed empty folder {op['path']}")
                else:
                    done.append(f"kept {op['path']} (not empty)")
            elif o == "write":
                _write(person, op["path"], op["text"]); done.append(f"wrote {op['path']}")
            elif o == "merge":
                parts = []
                into_is_source = op["into"].lower() in {x.lower() for x in op["sources"]}
                existing = _read(person, op["into"]).rstrip() + "\n\n" \
                    if _exists(person, op["into"]) and not into_is_source else ""
                for src in op["sources"]:
                    body = _read(person, src).strip()
                    title = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    has_heading = re.match(r"#{1,6} ", body)
                    parts.append(body if has_heading else f"## {title}\n\n{body}")
                _write(person, op["into"], existing + "\n\n".join(parts) + "\n")
                for src in op["sources"]:
                    if src.lower() != op["into"].lower():
                        nas_files.delete(person, SCOPE, src)
                done.append(f"combined {len(op['sources'])} notes into {op['into']}")
            elif o == "split":
                text = _read(person, op["path"])
                intro, sections = _split_sections(text, op["level"])
                if len(sections) < 2:
                    done.append(f"kept {op['path']} (fewer than two {'#' * op['level']} headings to split by)")
                    continue
                ext = op["path"].rsplit(".", 1)[1]
                made = []
                for title, body in sections:
                    name = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()[:80] or "Untitled"
                    target = _free(person, (op["folder"] + "/" if op["folder"] else "") + f"{name}.{ext}")
                    nas_files.save_text(person, SCOPE, target, body, None, create=True); made.append(target)
                if intro:
                    _write(person, op["path"], intro + "\n")
                else:
                    nas_files.delete(person, SCOPE, op["path"])
                done.append(f"split {op['path']} into {len(made)} notes")
            elif o == "trash":
                nas_files.delete(person, SCOPE, op["path"]); done.append(f"trashed {op['path']}")
    except (nas.NasError, TidyError) as e:
        db.log_activity("system", f"Notes tidy-up ({kind}) for {person.display_name} stopped: {e}", user_id=person.id)
        return {"applied": "partly", "done": done, "stopped_at": str(e),
                "note": "Everything before the problem was done; nothing was lost (old versions are in the trash)."}
    db.log_activity("system", f"Notes tidy-up ({kind}) for {person.display_name}: {len(done)} steps", user_id=person.id)
    return {"applied": True, "done": done, "note": "Old versions and removed notes are in the NAS trash (.trash)."}
