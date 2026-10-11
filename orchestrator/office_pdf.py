"""Office documents shown in their real layout: converted to PDF with LibreOffice (headless) and displayed
by the browser's PDF viewer on the Files tab, like any PDF (#51).

    supported(name)                      -> True for Word / Excel / PowerPoint / OpenDocument / RTF
    await pdf_for(person, scope, path)   -> PDF bytes (cached by file, size and modified time)

Conversions run one at a time with a timeout, in a throwaway folder with its own LibreOffice profile;
documents' macros never run (LibreOffice's default for headless conversion). Spreadsheets are exported
one page per sheet, so wide tables aren't cut into pages. The cache lives in /data/preview-cache and
is trimmed to CACHE_MAX_BYTES (oldest first); a changed file gets a new cache entry.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import nas_files
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)

WRITER = (".docx", ".doc", ".docm", ".dotx", ".odt", ".rtf")
CALC = (".xlsx", ".xlsm", ".xls", ".ods")
IMPRESS = (".pptx", ".ppt", ".pptm", ".ppsx", ".odp")
MAX_SOURCE_BYTES = 50_000_000
TIMEOUT_S = 120
CACHE_MAX_BYTES = 500_000_000
# One page per sheet (LibreOffice 7.4+ JSON filter options).
_CALC_FILTER = 'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}'
_lock = asyncio.Lock()


class ConvertError(ValueError):
    """Message is safe to show."""


def supported(name: str) -> bool:
    return name.lower().endswith(WRITER + CALC + IMPRESS)


def available() -> bool:
    return bool(shutil.which("soffice") or shutil.which("libreoffice"))


def _cache_dir() -> Path:
    d = settings.db_path.parent / "preview-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trim_cache() -> None:
    files = sorted(_cache_dir().glob("*.pdf"), key=lambda p: p.stat().st_atime)
    total = sum(p.stat().st_size for p in files)
    while files and total > CACHE_MAX_BYTES:
        old = files.pop(0)
        total -= old.stat().st_size
        old.unlink(missing_ok=True)


def _convert(name: str, data: bytes) -> bytes:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise ConvertError("The document viewer (LibreOffice) isn't installed on the server.")
    import subprocess
    with tempfile.TemporaryDirectory(prefix="alfred-pdf-") as tmp:
        ext = os.path.splitext(name)[1].lower()
        src = Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        target = _CALC_FILTER if ext in CALC else "pdf"
        cmd = [exe, f"-env:UserInstallation=file://{tmp}/profile", "--headless", "--norestore", "--nolockcheck",
               "--nodefault", "--nologo", "--convert-to", target, "--outdir", tmp, str(src)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, check=False,
                           env={**os.environ, "HOME": tmp})
        except subprocess.TimeoutExpired:
            raise ConvertError("The document took too long to prepare. Download it to open it.") from None
        out = Path(tmp) / "source.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise ConvertError("This document couldn't be shown (damaged or not what its name says). Download it instead.")
        return out.read_bytes()


async def pdf_of(name: str, data: bytes) -> bytes:
    """PDF of an office document given as bytes (email attachments), cached by content."""
    if not supported(name):
        raise ConvertError("No document view for this file type.")
    if len(data) > MAX_SOURCE_BYTES:
        raise ConvertError("This document is too big to preview. Download it to open it.")
    key = hashlib.sha256(data).hexdigest()[:32] + "-b"
    cached = _cache_dir() / f"{key}.pdf"
    if cached.exists():
        os.utime(cached)
        return cached.read_bytes()
    async with _lock:
        if cached.exists():
            return cached.read_bytes()
        pdf = await asyncio.to_thread(_convert, name, data)
        tmp = cached.with_suffix(".part")
        tmp.write_bytes(pdf)
        tmp.replace(cached)
        await asyncio.to_thread(_trim_cache)
        return pdf


async def pdf_for(person: Person, scope: str, path: str) -> bytes:
    """PDF of an office document the person may open (access is checked by nas_files)."""
    info = await asyncio.to_thread(nas_files.stat_file, person, scope, path)
    if not supported(info["name"]):
        raise ConvertError("No document view for this file type.")
    if info["size"] > MAX_SOURCE_BYTES:
        raise ConvertError("This document is too big to preview. Download it to open it.")
    key = hashlib.sha256(f"{info['full']}|{info['size']}|{info['mtime']}".encode()).hexdigest()[:32]
    cached = _cache_dir() / f"{key}.pdf"
    if cached.exists():
        os.utime(cached)                                    # keep recently used entries
        return cached.read_bytes()
    async with _lock:                                       # one LibreOffice at a time on the Pi
        if cached.exists():
            return cached.read_bytes()
        name, data = await asyncio.to_thread(nas_files.download, person, scope, path)
        started = time.monotonic()
        pdf = await asyncio.to_thread(_convert, name, data)
        log.info("Converted %s to PDF in %.1fs", os.path.splitext(name)[1], time.monotonic() - started)
        tmp = cached.with_suffix(".part")
        tmp.write_bytes(pdf)
        tmp.replace(cached)
        await asyncio.to_thread(_trim_cache)
        return pdf
