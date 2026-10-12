"""Audit-log export (admin): the event log or the blocked-access log as CSV.

Times are local (TIMEZONE). Cells that a spreadsheet would run as a formula (=, +, -, @, tab, CR)
get a leading apostrophe, because summaries contain text people sent.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from orchestrator import db
from orchestrator.config import settings
from orchestrator.identity.registry import Person

from .routes import requires

router = APIRouter(prefix="/api")

_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_FORMULA) else s


def _local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return "﻿" + buf.getvalue()       # BOM so Excel reads UTF-8


@router.get("/audit/export")
async def export(person: Person = Depends(requires("everyone.view")),
                 source: Literal["activity", "access"] = "activity",
                 kind: str | None = Query(default=None, pattern="^[a-z_]{1,20}$"),
                 days: int = Query(default=90, ge=0, le=3660)) -> Response:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
    if source == "activity":
        sql = ("SELECT a.ts, a.kind, u.display_name, a.summary, a.session_id FROM activity a "
               "LEFT JOIN users u ON u.id = a.user_id WHERE a.ts >= ?")
        params: list = [since]
        if kind:
            sql += " AND a.kind = ?"
            params.append(kind)
        rows = db.query(sql + " ORDER BY a.id", params)
        body = to_csv(["time", "kind", "person", "event", "session"],
                      [[_local(r["ts"]), r["kind"], r["display_name"], r["summary"], r["session_id"]] for r in rows])
    else:
        rows = db.query("SELECT ts, channel, raw_identifier, resolved, detail FROM access_attempts "
                        "WHERE ts >= ? ORDER BY id", (since,))
        body = to_csv(["time", "channel", "from", "recognised", "detail"],
                      [[_local(r["ts"]), r["channel"], r["raw_identifier"], "yes" if r["resolved"] else "no",
                        r["detail"]] for r in rows])
    db.log_activity("access", f"Audit log exported by {person.display_name} ({source}, "
                    f"{f'{days} days' if days else 'everything'}, {len(rows)} rows)", user_id=person.id)
    name = f"alfred-{source}-{datetime.now(ZoneInfo(settings.timezone)).strftime('%Y-%m-%d')}.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
