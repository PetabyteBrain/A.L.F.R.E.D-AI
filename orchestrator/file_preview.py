"""Read-only previews for the Files tab (#51): office documents become plain structured data that the
dashboard renders itself (never HTML from the file), and a short list of safe media types is shown
inline by the browser.

    inline_type(name)       -> content type for PDFs, raster images, audio and video, else None
    preview(name, data)     -> {"kind": "doc" | "sheets" | "slides" | "text", ...}

Word (.docx) and OpenDocument text (.odt): headings, paragraphs, list items and tables.
Excel (.xlsx/.xlsm) and CSV: the first rows and columns of every sheet. PowerPoint (.pptx): each
slide's title, text and speaker notes. Code and config files: plain text. Archived email (.eml): the
headers, attachment names and the text (never its HTML). Old binary Office formats
(.doc/.xls/.ppt) can't be read here: download them.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

INLINE = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".mp3": "audio/mpeg",
          ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg", ".mp4": "video/mp4", ".webm": "video/webm",
          ".mov": "video/quicktime"}
TEXT = (".xml", ".html", ".htm", ".ini", ".conf", ".cfg", ".toml", ".sh", ".ps1", ".bat", ".py", ".js", ".jsx",
        ".ts", ".tsx", ".css", ".sql", ".env.example", ".srt", ".vtt", ".tex", ".rtf")
LEGACY = (".doc", ".xls", ".ppt")
MAX_ROWS, MAX_COLS, MAX_BLOCKS, MAX_TEXT = 300, 40, 2000, 200_000

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
OT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
OTAB = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


class PreviewError(ValueError):
    """Message is safe to show."""


MAX_PART = 40_000_000          # unpacked size of one part inside a document (zip bombs)


def _read(z: zipfile.ZipFile, name: str) -> bytes:
    if z.getinfo(name).file_size > MAX_PART:
        raise PreviewError("This document is too big to preview. Download it to open it.")
    return z.read(name)


def _ext(name: str) -> str:
    low = name.lower()
    return low[low.rfind("."):] if "." in low else ""


def inline_type(name: str) -> str | None:
    return INLINE.get(_ext(name))


def can_preview(name: str) -> bool:
    return _ext(name) in (".docx", ".odt", ".xlsx", ".xlsm", ".csv", ".tsv", ".pptx", ".eml") + TEXT \
        or bool(inline_type(name))


def preview(name: str, data: bytes) -> dict:
    ext = _ext(name)
    try:
        if ext == ".docx":
            return {"kind": "doc", "blocks": _docx(data)}
        if ext == ".odt":
            return {"kind": "doc", "blocks": _odt(data)}
        if ext in (".xlsx", ".xlsm"):
            return {"kind": "sheets", "sheets": _xlsx(data)}
        if ext in (".csv", ".tsv"):
            return {"kind": "sheets", "sheets": [_csv(data, "\t" if ext == ".tsv" else None)]}
        if ext == ".pptx":
            return {"kind": "slides", "slides": _pptx(data)}
        if ext == ".eml":
            text = _eml(data)
            return {"kind": "text", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
        if ext in TEXT:
            text = data.decode("utf-8", errors="replace")
            return {"kind": "text", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as e:
        raise PreviewError("This file couldn't be read for a preview (damaged or not what its name says).") from e
    if ext in LEGACY:
        raise PreviewError("Older Office format: no preview here. Download it to open it.")
    raise PreviewError("No preview for this file type. Download it to open it.")


# --- Word / OpenDocument -------------------------------------------------------------------------

def _eml(data: bytes) -> str:
    from email import message_from_bytes, policy
    from .mail_inbound import parse
    msg = message_from_bytes(data, policy=policy.default)
    p = parse(data)
    head = [f"{k}: {' '.join(str(msg.get(k, '')).split())}" for k in ("From", "To", "Cc", "Date", "Subject") if msg.get(k)]
    if p.attachments:
        head.append("Attachments: " + ", ".join(f"{n} ({len(d) // 1024 or 1} KB)" for n, d, _ in p.attachments))
    return "\n".join(head) + "\n\n" + (p.text or "(no text)")


def _w_text(el) -> str:
    out = []
    for node in el.iter():
        if node.tag == W + "t" and node.text:
            out.append(node.text)
        elif node.tag == W + "tab":
            out.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out)


def _docx(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(_read(z, "word/document.xml"))
    body = root.find(W + "body")
    blocks: list[dict] = []
    for el in list(body if body is not None else []):
        if len(blocks) >= MAX_BLOCKS:
            blocks.append({"type": "p", "text": "… (the rest is in the file)"})
            break
        if el.tag == W + "p":
            text = _w_text(el)
            style = el.find(f"{W}pPr/{W}pStyle")
            sid = (style.get(W + "val") if style is not None else "") or ""
            is_list = el.find(f"{W}pPr/{W}numPr") is not None
            m = re.match(r"(?i)heading\s*(\d)|title", sid.replace("berschrift", "heading"))
            if m and text.strip():
                blocks.append({"type": "h", "level": int(m.group(1)) if m.group(1) else 1, "text": text})
            elif is_list and text.strip():
                blocks.append({"type": "li", "text": text})
            elif text.strip():
                blocks.append({"type": "p", "text": text})
        elif el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                rows.append([_w_text(tc).strip() for tc in tr.findall(W + "tc")][:MAX_COLS])
                if len(rows) >= MAX_ROWS:
                    break
            if rows:
                blocks.append({"type": "table", "rows": rows})
    return blocks


def _o_text(el) -> str:
    return "".join(el.itertext())


def _odt(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(_read(z, "content.xml"))
    blocks: list[dict] = []

    def walk(el):
        for ch in list(el):
            if len(blocks) >= MAX_BLOCKS:
                return
            if ch.tag == OT + "h":
                blocks.append({"type": "h", "level": int(ch.get(OT + "outline-level") or 1), "text": _o_text(ch)})
            elif ch.tag == OT + "p" and _o_text(ch).strip():
                blocks.append({"type": "p", "text": _o_text(ch)})
            elif ch.tag == OT + "list":
                for item in ch.iter(OT + "list-item"):
                    text = " ".join(_o_text(p) for p in item.findall(OT + "p"))
                    if text.strip():
                        blocks.append({"type": "li", "text": text})
            elif ch.tag == OTAB + "table":
                rows = [[_o_text(c).strip() for c in r.findall(OTAB + "table-cell")][:MAX_COLS]
                        for r in ch.iter(OTAB + "table-row")][:MAX_ROWS]
                blocks.append({"type": "table", "rows": rows})
            else:
                walk(ch)
    walk(root)
    return blocks


# --- Excel / CSV ---------------------------------------------------------------------------------

def _cell(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M") if getattr(v, "hour", 0) or getattr(v, "minute", 0) else v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _trim(rows: list[list[str]]) -> list[list[str]]:
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((max((i + 1 for i, v in enumerate(r) if v), default=0) for r in rows), default=0)
    return [r[:width] + [""] * (width - len(r[:width])) for r in rows]


def _xlsx(data: bytes) -> list[dict]:
    import openpyxl
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 5 * MAX_PART:
            raise PreviewError("This workbook is too big to preview. Download it to open it.")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = []
    try:
        for ws in wb.worksheets[:20]:
            rows, more = [], False
            for i, row in enumerate(ws.iter_rows(values_only=True, max_col=MAX_COLS)):
                if i >= MAX_ROWS:
                    more = True
                    break
                rows.append([_cell(v) for v in row])
            sheets.append({"name": ws.title, "rows": _trim(rows), "more": more})
    finally:
        wb.close()
    return sheets


def _csv(data: bytes, delimiter: str | None) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows, more = [], False
    for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
        if i >= MAX_ROWS:
            more = True
            break
        rows.append(row[:MAX_COLS])
    return {"name": "", "rows": _trim(rows), "more": more}


# --- PowerPoint ----------------------------------------------------------------------------------

def _paras(el) -> list[str]:
    out = []
    for p in el.iter(A + "p"):
        t = "".join(r.text or "" for r in p.iter(A + "t")).strip()
        if t:
            out.append(t)
    return out


def _pptx(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        order = []
        try:                                   # slide order from the presentation, not the file names
            pres = ET.fromstring(_read(z, "ppt/presentation.xml"))
            rels = ET.fromstring(_read(z, "ppt/_rels/presentation.xml.rels"))
            target = {r.get("Id"): r.get("Target") for r in rels}
            for sid in pres.iter(P + "sldId"):
                t = target.get(sid.get(R + "id"), "")
                order.append("ppt/" + t.lstrip("/").removeprefix("ppt/"))
        except KeyError:
            pass
        if not order:
            order = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        slides = []
        for i, path in enumerate(order[:200], 1):
            if path not in names:
                continue
            root = ET.fromstring(_read(z, path))
            title, body = "", []
            for sp in root.iter(P + "sp"):
                ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                texts = _paras(sp)
                if ph is not None and ph.get("type") in ("title", "ctrTitle") and not title:
                    title = " ".join(texts)
                else:
                    body.extend(texts)
            notes = []
            rel_path = path.replace("slides/", "slides/_rels/") + ".rels"
            if rel_path in names:
                for r in ET.fromstring(_read(z, rel_path)):
                    if (r.get("Type") or "").endswith("/notesSlide"):
                        npath = "ppt/" + (r.get("Target") or "").replace("../", "")
                        if npath in names:
                            nroot = ET.fromstring(_read(z, npath))
                            for sp in nroot.iter(P + "sp"):
                                ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                                if ph is not None and ph.get("type") == "body":
                                    notes.extend(_paras(sp))
            slides.append({"n": i, "title": title, "text": body[:80], "notes": notes[:40]})
    return slides
