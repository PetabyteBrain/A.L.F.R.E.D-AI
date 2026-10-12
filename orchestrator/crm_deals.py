"""CRM stage 2 (roadmap #58): the sales pipeline.

Stages are editable (crm.settings): open stages in order, each with the win probability the forecast uses, plus
one Won and one Lost stage. A deal belongs to a company (and optionally a contact person), has a value in CHF, an
expected close date, a next step, and moves through the stages. Visibility, history, undo and deletion
(approval) work exactly like companies and contacts (orchestrator/crm.py): a deal is visible to its owner, its
creator, its group, and everyone who can see its company.

Value and stage changes apply at once and are logged (decided 2026-10-12); deleting a deal needs approval.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import crm, db
from .config import settings
from .identity.registry import Person

DEFAULT_STAGES = [("New", 10, "open"), ("Qualified", 25, "open"), ("Proposal", 50, "open"), ("Negotiation", 75, "open"),
                  ("Won", 100, "won"), ("Lost", 0, "lost")]
MAX_VALUE = 1_000_000_000
CrmError = crm.CrmError


def seed(conn) -> None:
    if not conn.execute("SELECT 1 FROM crm_stages LIMIT 1").fetchone():
        conn.executemany("INSERT INTO crm_stages(name, position, probability, kind) VALUES (?,?,?,?)",
                         [(n, i, p, k) for i, (n, p, k) in enumerate(DEFAULT_STAGES)])


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


# --- stages -------------------------------------------------------------------------------------

def stages(include_archived: bool = False) -> list[dict]:
    rows = db.query("SELECT s.*, (SELECT COUNT(*) FROM crm_deals d WHERE d.stage_id = s.id AND d.archived_at IS NULL) "
                    "AS deals FROM crm_stages s" + ("" if include_archived else " WHERE s.archived_at IS NULL")
                    + " ORDER BY CASE s.kind WHEN 'open' THEN 0 WHEN 'won' THEN 1 ELSE 2 END, s.position, s.id")
    for r in rows:
        r["archived"] = bool(r["archived_at"])
    return rows


def _stage(stage_id) -> dict:
    try:
        row = db.query_one("SELECT * FROM crm_stages WHERE id = ?", (int(stage_id),))
    except (TypeError, ValueError):
        row = None
    if not row:
        raise CrmError("Unknown stage.")
    return row


def stage_by_name(name: str) -> dict:
    want = " ".join(str(name or "").split()).lower()
    for s in stages():
        if s["name"].lower() == want:
            return s
    matches = [s for s in stages() if want and want in s["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    raise CrmError(f"Unknown stage “{name}”. Stages: {', '.join(s['name'] for s in stages())}.")


def save_stage(person: Person, *, name: str | None = None, probability: int | None = None,
               stage_id: int | None = None) -> dict:
    if not person.can("crm.settings"):
        raise CrmError("You don't have permission to change the pipeline.")
    if name is not None:
        name = " ".join(str(name).split())[:40]
        if not name:
            raise CrmError("Give the stage a name.")
        clash = db.query_one("SELECT 1 FROM crm_stages WHERE lower(name) = lower(?) AND archived_at IS NULL AND id != ?",
                             (name, stage_id or 0))
        if clash:
            raise CrmError("There's already a stage with that name.")
    if probability is not None and not 0 <= int(probability) <= 100:
        raise CrmError("Probability is a percentage from 0 to 100.")
    if stage_id is None:
        if not name:
            raise CrmError("Give the stage a name.")
        pos = (db.query_one("SELECT MAX(position) AS p FROM crm_stages WHERE kind = 'open'")["p"] or 0) + 1
        sid = db.execute("INSERT INTO crm_stages(name, position, probability, kind) VALUES (?,?,?, 'open')",
                         (name, pos, int(probability if probability is not None else 50)))
        db.log_activity("crm", f"{person.display_name} added the pipeline stage {name}", user_id=person.id)
        return _stage(sid)
    row = _stage(stage_id)
    sets = {}
    if name is not None:
        sets["name"] = name
    if probability is not None:
        if row["kind"] != "open":
            raise CrmError("Won and Lost have fixed probabilities (100 % and 0 %).")
        sets["probability"] = int(probability)
    if sets:
        db.execute(f"UPDATE crm_stages SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), stage_id))
        db.log_activity("crm", f"{person.display_name} changed the pipeline stage {row['name']}", user_id=person.id)
    return _stage(stage_id)


def move_stage(person: Person, stage_id: int, direction: int) -> list[dict]:
    if not person.can("crm.settings"):
        raise CrmError("You don't have permission to change the pipeline.")
    row = _stage(stage_id)
    if row["kind"] != "open":
        raise CrmError("Won and Lost always come last.")
    open_ = [s for s in stages() if s["kind"] == "open"]
    i = next(n for n, s in enumerate(open_) if s["id"] == stage_id)
    j = i + (1 if direction > 0 else -1)
    if 0 <= j < len(open_):
        open_[i], open_[j] = open_[j], open_[i]
        with db.tx() as conn:
            conn.executemany("UPDATE crm_stages SET position = ? WHERE id = ?", [(n, s["id"]) for n, s in enumerate(open_)])
    return stages()


def archive_stage(person: Person, stage_id: int, archived: bool = True) -> list[dict]:
    if not person.can("crm.settings"):
        raise CrmError("You don't have permission to change the pipeline.")
    row = _stage(stage_id)
    if row["kind"] != "open":
        raise CrmError("Won and Lost can't be removed.")
    if archived:
        if db.query_one("SELECT 1 FROM crm_deals WHERE stage_id = ? AND archived_at IS NULL", (stage_id,)):
            raise CrmError("Deals are still in this stage. Move them first.")
        if len([s for s in stages() if s["kind"] == "open"]) <= 1:
            raise CrmError("The pipeline needs at least one open stage.")
    db.execute("UPDATE crm_stages SET archived_at = ? WHERE id = ?", (db.now_iso() if archived else None, stage_id))
    db.log_activity("crm", f"{person.display_name} {'removed' if archived else 'restored'} the pipeline stage "
                           f"{row['name']}", user_id=person.id)
    return stages()


# --- deals --------------------------------------------------------------------------------------

def _date(v, label: str) -> str | None:
    v = str(v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise CrmError(f"{label}: give a date like 2026-11-30.")


def _money(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        x = float(re.sub(r"[’'\s]|CHF|Fr\.?", "", str(v), flags=re.I).replace(",", "."))
    except ValueError:
        raise CrmError("The value must be an amount in CHF, e.g. 12500.")
    if not 0 <= x <= MAX_VALUE:
        raise CrmError("That value is out of range.")
    return round(x, 2)


def _clean(person: Person, data: dict) -> dict:
    unknown = set(data) - set(crm.DEAL_FIELDS)
    if unknown:
        raise CrmError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    out: dict = {}
    for k, v in data.items():
        if k == "title":
            out[k] = crm._text(v, 160)
        elif k in ("source", "next_step"):
            out[k] = crm._text(v, 200)
        elif k == "outcome_note":
            out[k] = crm._text(v, 300)
        elif k == "notes":
            out[k] = crm._multiline(v, 5000)
        elif k == "value":
            out[k] = _money(v)
        elif k == "probability":
            if v in (None, ""):
                out[k] = None
            else:
                try:
                    p = int(float(v))
                except (TypeError, ValueError):
                    raise CrmError("Probability is a percentage from 0 to 100.")
                if not 0 <= p <= 100:
                    raise CrmError("Probability is a percentage from 0 to 100.")
                out[k] = p
        elif k in ("expected_close", "next_step_at"):
            out[k] = _date(v, "Expected close" if k == "expected_close" else "Next step date")
        elif k == "stage_id":
            s = _stage(v)
            if s["archived_at"]:
                raise CrmError("That stage was removed.")
            out[k] = s["id"]
        elif k == "company_id":
            out[k] = None if v in (None, "", 0) else crm._get_visible(person, "company", int(v))["id"]
        elif k == "contact_id":
            out[k] = None if v in (None, "", 0) else crm._get_visible(person, "contact", int(v))["id"]
        elif k == "owner_id":
            out[k] = crm._person_ref(v, "owner")
        elif k == "group_id":
            gid = crm._group_ref(v)
            if gid and not person.can("crm.view_all") and gid not in crm._group_ids(person):
                raise CrmError("You can only file deals under your own groups.")
            out[k] = gid
    return out


_SELECT = ("SELECT x.*, s.name AS stage_name, s.kind AS stage_kind, s.probability AS stage_probability, "
           "co.name AS company_name, ct.first_name AS contact_first, ct.last_name AS contact_last "
           "FROM crm_deals x JOIN crm_stages s ON s.id = x.stage_id "
           "LEFT JOIN crm_companies co ON co.id = x.company_id LEFT JOIN crm_contacts ct ON ct.id = x.contact_id")


def _view(row: dict, names: dict | None = None) -> dict:
    names = names if names is not None else crm._names()
    r = dict(row)
    r["probability_effective"] = r["probability"] if r["probability"] is not None else r["stage_probability"]
    r["weighted"] = round((r["value"] or 0) * r["probability_effective"] / 100, 2)
    r["contact_name"] = " ".join(x for x in (r.pop("contact_first", None), r.pop("contact_last", None)) if x) or None
    r["owner_name"] = names.get(r.get("owner_id"))
    r["created_by_name"] = names.get(r.get("created_by"))
    r["archived"] = bool(r.get("archived_at"))
    r["open"] = r["stage_kind"] == "open"
    today = _today().isoformat()
    r["overdue_close"] = bool(r["open"] and r["expected_close"] and r["expected_close"] < today)
    r["next_step_due"] = bool(r["open"] and r["next_step_at"] and r["next_step_at"] <= today)
    r["name"] = r["title"]
    return r


def get(person: Person, deal_id: int) -> dict:
    crm._get_visible(person, "deal", deal_id)
    r = _view(db.query_one(f"{_SELECT} WHERE x.id = ?", (deal_id,)))
    r["can_edit"] = person.can("crm.edit")
    return r


def find_one(person: Person, ref) -> dict:
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return get(person, int(ref))
    found = search(person, q=str(ref), status="all", limit=6)["items"]
    if not found:
        raise CrmError(f"I found no deal matching “{ref}”.")
    exact = [d for d in found if d["title"].lower() == str(ref).strip().lower()]
    if len(found) > 1 and len(exact) != 1:
        raise CrmError(f"Several deals match “{ref}”: " + "; ".join(f"#{d['id']} {d['title']} ({d['company_name'] or '-'})"
                                                                  for d in found) + ". Which one?")
    return get(person, (exact or found)[0]["id"])


def search(person: Person, *, q: str = "", status: str = "open", stage_id: int | None = None,
           company_id: int | None = None, mine: bool = False, archived: bool = False, sort: str = "close",
           limit: int = 100, offset: int = 0) -> dict:
    where, params = crm._scope(person, "x", "deal")
    where = " WHERE x.archived_at IS " + ("NOT NULL" if archived else "NULL") + where
    if status in ("open", "won", "lost"):
        where += " AND s.kind = ?"
        params.append(status)
    if stage_id:
        where += " AND x.stage_id = ?"
        params.append(stage_id)
    if company_id:
        where += " AND x.company_id = ?"
        params.append(company_id)
    if mine:
        where += " AND x.owner_id = ?"
        params.append(person.id)
    if q:
        like = f"%{q.strip().lower()[:80]}%"
        where += " AND (lower(x.title) LIKE ? OR lower(co.name) LIKE ? OR lower(x.notes) LIKE ? OR lower(x.next_step) LIKE ?)"
        params += [like] * 4
    order = {"close": "x.expected_close IS NULL, x.expected_close, x.id", "value": "x.value DESC, x.id",
             "recent": "x.updated_at DESC", "title": "lower(x.title)"}.get(sort, "x.id")
    base = _SELECT.split(" FROM ", 1)[1]
    total = db.query_one(f"SELECT COUNT(*) AS n FROM {base}{where}", params)["n"]
    rows = db.query(f"{_SELECT}{where} ORDER BY {order} LIMIT ? OFFSET ?", (*params, max(1, min(limit, 500)), max(0, offset)))
    names = crm._names()
    return {"total": total, "items": [_view(r, names) for r in rows]}


def save(person: Person, data: dict, deal_id: int | None = None) -> dict:
    if not person.can("crm.edit"):
        raise CrmError("You don't have permission to do that in the CRM.")
    clean = _clean(person, data)
    now = db.now_iso()
    if deal_id is None:
        if not clean.get("title"):
            raise CrmError("What's the deal called? (e.g. “Website relaunch”)")
        if "stage_id" not in clean:
            clean["stage_id"] = next(s["id"] for s in stages() if s["kind"] == "open")
        if not clean.get("owner_id"):
            clean["owner_id"] = person.id
        if clean.get("contact_id") and not clean.get("company_id"):
            clean["company_id"] = db.query_one("SELECT company_id FROM crm_contacts WHERE id = ?",
                                               (clean["contact_id"],))["company_id"]
        closed = _stage(clean["stage_id"])["kind"] != "open"
        cols = {**clean, "created_by": person.id, "created_at": now, "updated_at": now, "closed_at": now if closed else None}
        did = db.execute(f"INSERT INTO crm_deals({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
        row = db.query_one("SELECT * FROM crm_deals WHERE id = ?", (did,))
        cid = crm._record("deal", did, "create", None, row, person)
        db.log_activity("crm", f"{person.display_name} added the deal {row['title']}", user_id=person.id)
        return {"record": get(person, did), "change_id": cid}
    before = crm._get_visible(person, "deal", deal_id)
    if before["archived_at"]:
        raise CrmError("This deal is archived. Restore it first.")
    if "title" in clean and not clean["title"]:
        raise CrmError("A deal needs a title.")
    changes = {k: v for k, v in clean.items() if before.get(k) != v}
    if not changes:
        return {"record": get(person, deal_id), "change_id": None}
    extra = {}
    if "stage_id" in changes:
        was_open = _stage(before["stage_id"])["kind"] == "open"
        now_open = _stage(changes["stage_id"])["kind"] == "open"
        if was_open and not now_open:
            extra["closed_at"] = now
        elif now_open and not was_open:
            extra["closed_at"] = None
    db.execute(f"UPDATE crm_deals SET {', '.join(f'{k} = ?' for k in {**changes, **extra})}, updated_at = ? WHERE id = ?",
               (*changes.values(), *extra.values(), now, deal_id))
    cid = crm._record("deal", deal_id, "update", {k: before[k] for k in changes}, changes, person)
    what = ", ".join(changes)
    if "stage_id" in changes:
        what = f"moved to {_stage(changes['stage_id'])['name']}" + (f"; {', '.join(k for k in changes if k != 'stage_id')}"
                                                                    if len(changes) > 1 else "")
    db.log_activity("crm", f"{person.display_name} changed the deal {before['title']} ({what})", user_id=person.id)
    return {"record": get(person, deal_id), "change_id": cid}


def move(person: Person, deal_id: int, stage_id: int, note: str = "") -> dict:
    """Drag and drop on the board, or "move the Acme deal to negotiation"; a won/lost note is optional."""
    data = {"stage_id": stage_id}
    if note:
        data["outcome_note"] = note
    return save(person, data, deal_id)


def board(person: Person, *, mine: bool = False, q: str = "") -> dict:
    deals = search(person, status="all", mine=mine, q=q, limit=500)["items"]
    since = (_today() - timedelta(days=30)).isoformat()
    cols = []
    for s in stages():
        items = [d for d in deals if d["stage_id"] == s["id"]
                 and (s["kind"] == "open" or (d["closed_at"] or "")[:10] >= since)]
        cols.append({**s, "items": items, "count": len(items), "total": round(sum(d["value"] for d in items), 2),
                     "weighted": round(sum(d["weighted"] for d in items), 2)})
    return {"stages": cols, "closed_window_days": 30}


def forecast(person: Person, *, mine: bool = False) -> dict:
    deals = search(person, status="all", mine=mine, limit=2000)["items"]
    today = _today()
    month = today.strftime("%Y-%m")
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1).isoformat()
    open_ = [d for d in deals if d["open"]]
    won = [d for d in deals if d["stage_kind"] == "won"]
    lost = [d for d in deals if d["stage_kind"] == "lost"]
    recent = (today - timedelta(days=90)).isoformat()
    won90 = [d for d in won if (d["closed_at"] or "")[:10] >= recent]
    lost90 = [d for d in lost if (d["closed_at"] or "")[:10] >= recent]
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        key = f"{y:04d}-{m:02d}"
        items = [d for d in open_ if (d["expected_close"] or "")[:7] == key]
        won_m = [d for d in won if (d["closed_at"] or "")[:7] == key]
        months.append({"month": key, "count": len(items), "value": round(sum(d["value"] for d in items), 2),
                       "weighted": round(sum(d["weighted"] for d in items), 2),
                       "won": round(sum(d["value"] for d in won_m), 2)})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    overdue = [d for d in open_ if d["overdue_close"]]
    return {
        "open_count": len(open_), "open_value": round(sum(d["value"] for d in open_), 2),
        "weighted": round(sum(d["weighted"] for d in open_), 2),
        "won_month": round(sum(d["value"] for d in won if (d["closed_at"] or "")[:7] == month), 2),
        "won_quarter": round(sum(d["value"] for d in won if (d["closed_at"] or "")[:10] >= q_start), 2),
        "win_rate_90d": round(100 * len(won90) / (len(won90) + len(lost90))) if (won90 or lost90) else None,
        "overdue": {"count": len(overdue), "value": round(sum(d["value"] for d in overdue), 2)},
        "no_close_date": len([d for d in open_ if not d["expected_close"]]),
        "next_steps_due": len([d for d in open_ if d["next_step_due"]]),
        "months": months,
    }
