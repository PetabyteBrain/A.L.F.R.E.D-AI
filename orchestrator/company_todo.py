"""The Company to-do workbook on the NAS (NAS_COMPANY_TODO, default
Admin/To-Do.xlsx in the Company share). Admins and cofounders only.

Layout (sheet 1, data rows 6–30, header in row 5):
  AKTUELLES (current): A Position · B wichtigkeit (1–5) · C Status · D beschreibung
  ZUKUNFT   (future):  H Position · I wichtigkeit · J Status · K beschreibung
  FERTIG    (done):    O Position · P wichtigkeit · Q beschreibung
A row is free when its description is empty. Status values: not started, in progress, done.

Editing safety: openpyxl would drop the workbook's conditional-formatting extensions on save, so
writes change only the touched cells in the sheet XML and copy every other part of the file byte
for byte. Before each write the current file is copied to .alfred-backups/ in the Company share
(last 20 kept). If someone has it open in Excel (~$ lock file), nothing is written.
"""
from __future__ import annotations

import io
import re
import threading
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

from . import db
from .config import settings
from .identity.registry import Person
from .nas import NasError, _call, _smb, check_scope

FIRST, LAST = 6, 30
SECTIONS = {
    "current": {"label": "AKTUELLES", "pos": "A", "imp": "B", "status": "C", "desc": "D"},
    "future": {"label": "ZUKUNFT", "pos": "H", "imp": "I", "status": "J", "desc": "K"},
    "done": {"label": "FERTIG", "pos": "O", "imp": "P", "status": None, "desc": "Q"},
}
STATUSES = ("not started", "in progress", "done")
BACKUPS = ".alfred-backups"
KEEP_BACKUPS = 20
_lock = threading.Lock()


def _paths() -> tuple[str, str]:
    rel = settings.nas_company_todo.replace("/", "\\").strip("\\")
    base = rf"\\{settings.nas_host}\{settings.nas_share_company}"
    return base, base + "\\" + rel


def _load(smb) -> tuple[bytes, dict[str, object]]:
    import openpyxl
    _, full = _paths()
    with smb.open_file(full, mode="rb") as f:
        data = f.read()
    ws = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    cells = {}
    for sec in SECTIONS.values():
        for col in (sec["imp"], sec["status"], sec["desc"]):
            if col:
                for r in range(FIRST, LAST + 1):
                    cells[f"{col}{r}"] = ws[f"{col}{r}"].value
    return data, cells


def _items(cells: dict, key: str) -> list[dict]:
    sec, out = SECTIONS[key], []
    for r in range(FIRST, LAST + 1):
        desc = cells.get(f"{sec['desc']}{r}")
        if desc not in (None, ""):
            out.append({"row": r, "description": str(desc).strip(),
                        "importance": cells.get(f"{sec['imp']}{r}"),
                        **({"status": cells.get(f"{sec['status']}{r}")} if sec["status"] else {})})
    return out


def show(person: Person) -> dict:
    check_scope(person, "company")
    smb = _smb()
    _, cells = _call(_load, smb)
    return {k: _items(cells, k) for k in SECTIONS} | {"file": settings.nas_company_todo}


# --- XML cell editing --------------------------------------------------------------------------

def _sheet_name(z: zipfile.ZipFile) -> str:
    wb = z.read("xl/workbook.xml").decode()
    rid = re.search(r'<sheet\b[^>]*\br:id="([^"]+)"', wb).group(1)
    rels = z.read("xl/_rels/workbook.xml.rels").decode()
    target = re.search(rf'<Relationship\b[^>]*\bId="{rid}"[^>]*\bTarget="([^"]+)"', rels) or \
        re.search(rf'<Relationship\b[^>]*\bTarget="([^"]+)"[^>]*\bId="{rid}"', rels)
    t = target.group(1).lstrip("/")
    return t if t.startswith("xl/") else "xl/" + t


def _col_num(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def _cell_xml(ref: str, style: str, value) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def set_cell(xml: str, ref: str, value) -> str:
    """Replace (or insert, in column order) one cell; keeps its style."""
    m = re.search(rf'<c r="{ref}"(?:\s[^>]*?)?(?:/>|>.*?</c>)', xml, flags=re.S)
    if m:
        style = re.search(r'\ss="(\d+)"', m.group(0).split(">", 1)[0])
        return xml[:m.start()] + _cell_xml(ref, style.group(1) if style else "", value) + xml[m.end():]
    col, row = re.match(r"([A-Z]+)(\d+)", ref).groups()
    rm = re.search(rf'<row r="{row}"[^>]*?(?:/>|>(.*?)</row>)', xml, flags=re.S)
    if not rm:
        raise NasError(f"Row {row} isn't in the sheet as expected; edit this one in Excel.")
    if rm.group(0).endswith("/>"):
        new_row = rm.group(0)[:-2] + ">" + _cell_xml(ref, "", value) + "</row>"
        return xml[:rm.start()] + new_row + xml[rm.end():]
    body, start = rm.group(1), rm.start(1)
    pos = len(body)
    for c in re.finditer(r'<c r="([A-Z]+)\d+"', body):
        if _col_num(c.group(1)) > _col_num(col):
            pos = c.start()
            break
    return xml[:start + pos] + _cell_xml(ref, "", value) + xml[start + pos:]


def _rewrite(data: bytes, changes: dict[str, object]) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    name = _sheet_name(src)
    xml = src.read(name).decode("utf-8")
    for ref, value in changes.items():
        xml = set_cell(xml, ref, value)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            payload = xml.encode("utf-8") if info.filename == name else src.read(info.filename)
            dst.writestr(info, payload, compress_type=info.compress_type)
    return out.getvalue()


# --- operations --------------------------------------------------------------------------------

def _write(person: Person, what: str, plan) -> dict:
    """plan(cells) -> (changes, summary). Backs up, writes only the changed cells."""
    check_scope(person, "company")
    smb = _smb()
    base, full = _paths()
    folder, fname = full.rsplit("\\", 1)

    def go():
        with _lock:
            try:
                smb.stat(folder + "\\~$" + fname[2:])
                locked = True
            except (FileNotFoundError, OSError):
                locked = False
            if locked:
                raise NasError("The to-do list is open in Excel right now; close it and ask me again.")
            data, cells = _load(smb)
            changes, summary = plan(cells)
            new = _rewrite(data, changes)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            try:
                smb.makedirs(base + "\\" + BACKUPS, exist_ok=True)
            except FileExistsError:
                pass
            with smb.open_file(f"{base}\\{BACKUPS}\\{stamp} {fname}", mode="wb") as f:
                f.write(data)
            with smb.open_file(full, mode="wb") as f:
                f.write(new)
            old = sorted(e.name for e in smb.scandir(base + "\\" + BACKUPS) if e.name.endswith(fname))
            for n in old[:-KEEP_BACKUPS]:
                smb.remove(f"{base}\\{BACKUPS}\\{n}")
            return summary
    summary = _call(go)
    db.log_activity("system", f"Company to-do: {person.display_name} {what}: {summary}", user_id=person.id)
    return {"ok": True, "done": summary}


def _free_row(cells: dict, key: str) -> int:
    sec = SECTIONS[key]
    for r in range(FIRST, LAST + 1):
        if cells.get(f"{sec['desc']}{r}") in (None, ""):
            return r
    raise NasError(f"The {sec['label']} section is full (rows {FIRST}–{LAST}); add rows in Excel first.")


def _find(cells: dict, text: str, keys=("current", "future")) -> tuple[str, int]:
    t = " ".join(str(text or "").lower().split())
    if not t:
        raise NasError("Which to-do?")
    hits = []
    for k in keys:
        for it in _items(cells, k):
            d = it["description"].lower()
            if d == t:
                return k, it["row"]
            if t in d:
                hits.append((k, it["row"], it["description"]))
    if len(hits) == 1:
        return hits[0][0], hits[0][1]
    if hits:
        raise NasError("More than one to-do matches: " + "; ".join(h[2] for h in hits))
    raise NasError(f"No open to-do matches '{text}'.")


def _imp(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise NasError("Importance is a number from 1 to 5.")
    if not 1 <= v <= 5:
        raise NasError("Importance is a number from 1 to 5.")
    return v


def _remove_and_compact(cells: dict, key: str, row: int) -> dict:
    """Changes that delete `row` from a section and move the rows below it up (no gaps)."""
    sec = SECTIONS[key]
    cols = [c for c in (sec["imp"], sec["status"], sec["desc"]) if c]
    changes = {}
    last = max([it["row"] for it in _items(cells, key)] + [row])
    for r in range(row, last):
        for c in cols:
            changes[f"{c}{r}"] = cells.get(f"{c}{r + 1}")
    changes[f"{sec['desc']}{last}"] = None
    changes[f"{sec['imp']}{last}"] = 1
    if sec["status"]:
        changes[f"{sec['status']}{last}"] = "not started"
    return changes


def add(person: Person, description: str, importance: int = 3, section: str = "current",
        status: str = "not started") -> dict:
    description = " ".join(str(description or "").split())[:300]
    if not description:
        raise NasError("What's the to-do?")
    if section not in ("current", "future"):
        raise NasError("Section is current or future.")
    if status not in STATUSES[:2]:
        raise NasError("Status is 'not started' or 'in progress'.")
    imp = _imp(importance)

    def plan(cells):
        r = _free_row(cells, section)
        sec = SECTIONS[section]
        return ({f"{sec['desc']}{r}": description, f"{sec['imp']}{r}": imp, f"{sec['status']}{r}": status},
                f"added '{description}' to {sec['label']} (importance {imp})")
    return _write(person, "add", plan)


def update(person: Person, todo: str, status: str | None = None, importance: int | None = None) -> dict:
    if status is not None and status not in STATUSES:
        raise NasError("Status is not started, in progress or done.")

    def plan(cells):
        key, row = _find(cells, todo)
        sec = SECTIONS[key]
        desc = cells.get(f"{sec['desc']}{row}")
        imp = _imp(importance) if importance is not None else cells.get(f"{sec['imp']}{row}")
        if status == "done":                       # finished: move to FERTIG
            r = _free_row(cells, "done")
            d = SECTIONS["done"]
            changes = _remove_and_compact(cells, key, row)
            changes.update({f"{d['desc']}{r}": desc, f"{d['imp']}{r}": imp})
            return changes, f"moved '{desc}' to FERTIG"
        changes = {}
        if importance is not None:
            changes[f"{sec['imp']}{row}"] = imp
        if status:
            changes[f"{sec['status']}{row}"] = status
        if not changes:
            raise NasError("Nothing to change: give a status or an importance.")
        return changes, f"'{desc}': " + ", ".join(f"{k}={v}" for k, v in
                                                  (("status", status), ("importance", importance)) if v is not None)
    return _write(person, "update", plan)


def move(person: Person, todo: str, to: str) -> dict:
    """Between current and future (e.g. start a future to-do now)."""
    if to not in ("current", "future"):
        raise NasError("Move to current or future.")

    def plan(cells):
        key, row = _find(cells, todo)
        if key == to:
            raise NasError(f"It's already in {SECTIONS[to]['label']}.")
        src, dst = SECTIONS[key], SECTIONS[to]
        r = _free_row(cells, to)
        changes = _remove_and_compact(cells, key, row)
        changes.update({f"{dst['desc']}{r}": cells.get(f"{src['desc']}{row}"),
                        f"{dst['imp']}{r}": cells.get(f"{src['imp']}{row}"),
                        f"{dst['status']}{r}": cells.get(f"{src['status']}{row}") or "not started"})
        return changes, f"moved '{cells.get(src['desc'] + str(row))}' to {dst['label']}"
    return _write(person, "move", plan)
