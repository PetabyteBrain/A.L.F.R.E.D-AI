"""CRM, stage 1 (roadmap #58): companies, contacts and their timeline.

Who sees what (orchestrator/permissions.py):
- crm.use: the CRM at all, limited to records the person owns, created, or that belong to one of their
  groups (a contact is also visible through its company);
- crm.view_all: every record;
- crm.edit: create and change the records they can see, log on the timeline, archive, undo;
- crm.approve: approve drafts (orchestrator/approvals.py).

Direct changes apply at once and land in `crm_changes`, so they can be shown and undone. Deleting and
merging are never direct: they become approval drafts. New records are checked for duplicates (same
email, phone, web domain or name) and are only created anyway when the person says so (`force`).

The timeline merges what people log (notes, calls, meetings, documents) with emails to or from the
record's addresses (only mail the viewer may read) and outside calls to its numbers, at read time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import approvals, db
from .config import settings
from .identity.registry import Person, normalize_phone

STATUSES = ("lead", "prospect", "client", "partner", "supplier", "former", "other")
ACTIVITY_KINDS = ("note", "call", "meeting", "task", "document")
COMPANY_FIELDS = ("name", "status", "industry", "website", "email", "phone", "address", "notes", "tags", "custom",
                  "source", "owner_id", "group_id", "timetrack_tag")
CONTACT_FIELDS = ("first_name", "last_name", "company_id", "title", "email", "phone", "notes", "tags", "custom",
                  "source", "owner_id", "group_id")
DEAL_FIELDS = ("title", "company_id", "contact_id", "stage_id", "value", "probability", "expected_close", "source",
               "next_step", "next_step_at", "outcome_note", "notes", "owner_id", "group_id")
TABLE = {"company": "crm_companies", "contact": "crm_contacts", "deal": "crm_deals"}
FIELDS = {"company": COMPANY_FIELDS, "contact": CONTACT_FIELDS, "deal": DEAL_FIELDS}
MAX_TAGS, MAX_CUSTOM = 20, 30
_LEGAL = re.compile(r"\b(ag|gmbh|sa|sàrl|sarl|ltd|limited|inc|llc|kg|co|plc|bv|srl|spa|sagl|ug|oy|ab|as|corp|"
                    r"corporation|company|& co|und co)\b\.?", re.I)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class CrmError(ValueError):
    """Message is safe to show."""


class DuplicateError(CrmError):
    def __init__(self, matches: list[dict]):
        super().__init__("This looks like a record that already exists.")
        self.matches = matches


# --- access -----------------------------------------------------------------------------------

def _need(person: Person, permission: str) -> None:
    if not person.can(permission):
        raise CrmError("You don't have permission to do that in the CRM.")


def _group_ids(person: Person) -> list[int]:
    from . import permissions
    return [g["id"] for g in permissions.groups_of(person.id)]


def _scope(person: Person, alias: str, entity: str) -> tuple[str, list]:
    """SQL condition (starting with AND) limiting rows to what the person may see."""
    _need(person, "crm.use")
    if person.can("crm.view_all"):
        return "", []
    gids = _group_ids(person)
    in_groups = f" OR {{a}}.group_id IN ({','.join('?' * len(gids))})" if gids else ""
    own = f"({{a}}.owner_id = ? OR {{a}}.created_by = ?{in_groups})"
    cond = own.format(a=alias)
    params: list = [person.id, person.id, *gids]
    if entity in ("contact", "deal"):             # also visible through their company
        cond = (f"({cond} OR {alias}.company_id IN (SELECT co.id FROM crm_companies co WHERE "
                f"{own.format(a='co')}))")
        params += [person.id, person.id, *gids]
    return f" AND {cond}", params


def _get_visible(person: Person, entity: str, record_id: int) -> dict:
    where, params = _scope(person, "x", entity)
    row = db.query_one(f"SELECT x.* FROM {TABLE[entity]} x WHERE x.id = ?{where}", (record_id, *params))
    if not row:
        raise CrmError(f"No such {entity}.")
    return row


# --- cleaning ---------------------------------------------------------------------------------

def _text(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _multiline(v, n: int) -> str:
    return str(v or "").replace("\r\n", "\n").strip()[:n]


def _tags(v) -> list[str]:
    items = v if isinstance(v, list) else str(v or "").split(",")
    out = []
    for t in items:
        t = _text(t, 30).lower()
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _custom(v) -> dict:
    if not isinstance(v, dict):
        raise CrmError("Custom fields must be name: value pairs.")
    out = {}
    for k, val in list(v.items())[:MAX_CUSTOM]:
        k = _text(k, 40)
        if k:
            out[k] = _text(val, 300)
    return out


def _email(v) -> str:
    v = _text(v, 254).lower()
    if v and not _EMAIL.match(v):
        raise CrmError(f"“{v}” doesn't look like an email address.")
    return v


def _phone(v) -> str:
    v = _text(v, 40)
    if not v:
        return ""
    p = normalize_phone(v)
    return p if re.fullmatch(r"\+[1-9]\d{6,14}", p) else v


def _website(v) -> str:
    v = _text(v, 200)
    if v and not re.match(r"(?i)^https?://", v):
        v = "https://" + v
    return v


def _domain(url: str) -> str:
    m = re.match(r"(?i)^(?:https?://)?(?:www\.)?([^/:?#]+)", url or "")
    return m.group(1).lower() if m else ""


def name_key(name: str) -> str:
    s = _LEGAL.sub(" ", (name or "").lower())
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def _person_ref(v, label: str) -> int | None:
    if v in (None, "", 0):
        return None
    try:
        uid = int(v)
    except (TypeError, ValueError):
        raise CrmError(f"Unknown {label}.")
    if not db.query_one("SELECT 1 FROM users WHERE id = ? AND active = 1", (uid,)):
        raise CrmError(f"Unknown {label}.")
    return uid


def _group_ref(v) -> int | None:
    if v in (None, "", 0):
        return None
    try:
        gid = int(v)
    except (TypeError, ValueError):
        raise CrmError("Unknown group.")
    if not db.query_one("SELECT 1 FROM people_groups WHERE id = ?", (gid,)):
        raise CrmError("Unknown group.")
    return gid


def _clean(person: Person, entity: str, data: dict) -> dict:
    fields = COMPANY_FIELDS if entity == "company" else CONTACT_FIELDS
    unknown = set(data) - set(fields)
    if unknown:
        raise CrmError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    out: dict = {}
    for k, v in data.items():
        if k in ("name", "industry", "first_name", "last_name", "title", "source", "timetrack_tag"):
            out[k] = _text(v, 120)
        elif k == "status":
            v = _text(v, 20).lower()
            if v not in STATUSES:
                raise CrmError(f"Status must be one of: {', '.join(STATUSES)}.")
            out[k] = v
        elif k in ("address", "notes"):
            out[k] = _multiline(v, 300 if k == "address" else 5000)
        elif k == "email":
            out[k] = _email(v)
        elif k == "phone":
            out[k] = _phone(v)
        elif k == "website":
            out[k] = _website(v)
        elif k == "tags":
            out[k] = json.dumps(_tags(v), ensure_ascii=False)
        elif k == "custom":
            out[k] = json.dumps(_custom(v), ensure_ascii=False)
        elif k == "owner_id":
            out[k] = _person_ref(v, "owner")
        elif k == "group_id":
            gid = _group_ref(v)
            if gid and not person.can("crm.view_all") and gid not in _group_ids(person):
                raise CrmError("You can only file records under your own groups.")
            out[k] = gid
        elif k == "company_id":
            out[k] = None if v in (None, "", 0) else _get_visible(person, "company", int(v))["id"]
    return out


# --- views ------------------------------------------------------------------------------------

def _names() -> dict[int, str]:
    return {u["id"]: u["display_name"] for u in db.query("SELECT id, display_name FROM users")}


def _view(entity: str, row: dict, names: dict | None = None) -> dict:
    names = names if names is not None else _names()
    row = dict(row)
    row["tags"] = json.loads(row.get("tags") or "[]")
    row["custom"] = json.loads(row.get("custom") or "{}")
    row["owner_name"] = names.get(row.get("owner_id"))
    row["created_by_name"] = names.get(row.get("created_by"))
    row["archived"] = bool(row.get("archived_at"))
    if entity == "contact":
        row["name"] = " ".join(x for x in (row["first_name"], row["last_name"]) if x) or "(no name)"
    return row


def contact_label(c: dict) -> str:
    return " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("email") or f"contact #{c['id']}"


def _order(sort: str, entity: str) -> str:
    if sort == "recent":
        return "x.updated_at DESC"
    return "lower(x.name)" if entity == "company" else "lower(x.last_name), lower(x.first_name)"


def search(person: Person, entity: str, *, q: str = "", status: str = "", tag: str = "", company_id: int | None = None,
           mine: bool = False, archived: bool = False, sort: str = "name", limit: int = 50, offset: int = 0) -> dict:
    if entity not in TABLE:
        raise CrmError("Choose companies or contacts.")
    where, params = _scope(person, "x", entity)
    where = " WHERE x.archived_at IS " + ("NOT NULL" if archived else "NULL") + where
    if q:
        like = f"%{q.strip().lower()[:80]}%"
        cols = (["x.name", "x.industry", "x.website"] if entity == "company"
                else ["x.first_name || ' ' || x.last_name", "x.title",
                      "(SELECT co.name FROM crm_companies co WHERE co.id = x.company_id)"])
        cols += ["x.email", "x.phone", "x.notes", "x.tags"]
        conds = [f"lower({c}) LIKE ?" for c in cols]
        cond_params: list = [like] * len(cols)
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 6:                       # "079 000 01 01" finds +41790000101
            conds.append("replace(replace(x.phone, ' ', ''), '+', '') LIKE ?")
            cond_params.append(f"%{digits.lstrip('0')[-9:]}%")
        where += " AND (" + " OR ".join(conds) + ")"
        params += cond_params
    if status and entity == "company":
        where += " AND x.status = ?"
        params.append(status)
    if tag:
        where += " AND x.tags LIKE ?"
        params.append(f'%"{tag.lower()}"%')
    if company_id and entity == "contact":
        where += " AND x.company_id = ?"
        params.append(company_id)
    if mine:
        where += " AND x.owner_id = ?"
        params.append(person.id)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM {TABLE[entity]} x{where}", params)["n"]
    extra_cols = ""
    if entity == "company":
        extra_cols = (", (SELECT COUNT(*) FROM crm_contacts c WHERE c.company_id = x.id AND c.archived_at IS NULL) "
                      "AS contacts, (SELECT MAX(occurred_at) FROM crm_activities a WHERE a.company_id = x.id) AS last_activity")
    else:
        extra_cols = (", (SELECT co.name FROM crm_companies co WHERE co.id = x.company_id) AS company_name, "
                      "(SELECT MAX(occurred_at) FROM crm_activities a WHERE a.contact_id = x.id) AS last_activity")
    rows = db.query(f"SELECT x.*{extra_cols} FROM {TABLE[entity]} x{where} ORDER BY {_order(sort, entity)} "
                    f"LIMIT ? OFFSET ?", (*params, max(1, min(limit, 200)), max(0, offset)))
    names = _names()
    return {"total": total, "items": [_view(entity, r, names) for r in rows]}


def get(person: Person, entity: str, record_id: int) -> dict:
    row = _view(entity, _get_visible(person, entity, record_id))
    if entity == "company":
        from . import crm_deals
        row["contacts"] = search(person, "contact", company_id=record_id, limit=200)["items"]
        row["deals"] = crm_deals.search(person, company_id=record_id, status="all", limit=100)["items"]
    elif row.get("company_id"):
        try:
            co = _get_visible(person, "company", row["company_id"])
            row["company"] = {"id": co["id"], "name": co["name"], "status": co["status"]}
        except CrmError:
            row["company"] = None
    row["can_edit"] = person.can("crm.edit")
    return row


def find_one(person: Person, entity: str, ref) -> dict:
    """A record by id or by (part of its) name, for Alfred. Ambiguous names list the candidates."""
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return get(person, entity, int(ref))
    found = search(person, entity, q=str(ref), limit=6)["items"]
    if not found:
        raise CrmError(f"I found no {entity} matching “{ref}”.")
    exact = [r for r in found if r["name"].lower() == str(ref).strip().lower()]
    if len(found) > 1 and len(exact) != 1:
        raise CrmError(f"Several {'companies' if entity == 'company' else 'contacts'} match “{ref}”: "
                       + "; ".join(f"#{r['id']} {r['name']}" for r in found) + ". Which one?")
    return get(person, entity, (exact or found)[0]["id"])


def tags_in_use(person: Person) -> list[str]:
    out: set[str] = set()
    for entity in ("company", "contact"):
        where, params = _scope(person, "x", entity)
        for r in db.query(f"SELECT x.tags FROM {TABLE[entity]} x WHERE x.archived_at IS NULL{where}", params):
            out.update(json.loads(r["tags"] or "[]"))
    return sorted(out)


def counts(person: Person) -> dict:
    out = {}
    for entity in ("company", "contact"):
        where, params = _scope(person, "x", entity)
        out[entity] = db.query_one(f"SELECT COUNT(*) AS n FROM {TABLE[entity]} x WHERE x.archived_at IS NULL{where}",
                                   params)["n"]
    where, params = _scope(person, "x", "company")
    out["statuses"] = {r["status"]: r["n"] for r in db.query(
        f"SELECT x.status, COUNT(*) AS n FROM crm_companies x WHERE x.archived_at IS NULL{where} GROUP BY x.status",
        params)}
    return out


# --- duplicates -------------------------------------------------------------------------------

def duplicates(person: Person, entity: str, data: dict, exclude_id: int | None = None) -> list[dict]:
    """Visible records that look like the same company or person, with the reason."""
    where, params = _scope(person, "x", entity)
    rows = db.query(f"SELECT x.* FROM {TABLE[entity]} x WHERE x.archived_at IS NULL{where}", params)
    email = (data.get("email") or "").strip().lower()
    phone = _phone(data.get("phone")) if data.get("phone") else ""
    out = []
    for r in rows:
        if r["id"] == exclude_id:
            continue
        why = []
        if email and r["email"] and r["email"].lower() == email:
            why.append("same email")
        if phone and r["phone"] and _phone(r["phone"]) == phone:
            why.append("same phone")
        if entity == "company":
            if data.get("website") and r["website"] and _domain(r["website"]) == _domain(data["website"]):
                why.append("same website")
            if data.get("name") and name_key(r["name"]) and name_key(r["name"]) == name_key(data["name"]):
                why.append("same name")
            if email and r["website"] and email.split("@")[-1] == _domain(r["website"]) \
                    and email.split("@")[-1] not in _FREEMAIL:
                why.append("email on their domain")
        else:
            full = " ".join(x for x in (data.get("first_name"), data.get("last_name")) if x).strip().lower()
            if full and " ".join(x for x in (r["first_name"], r["last_name"]) if x).strip().lower() == full:
                why.append("same name")
        if why:
            label = r["name"] if entity == "company" else contact_label(r)
            out.append({"id": r["id"], "name": label, "reason": ", ".join(why)})
    return out[:5]


_FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com",
             "me.com", "gmx.ch", "gmx.net", "gmx.de", "bluewin.ch", "sunrise.ch", "proton.me", "protonmail.com",
             "hispeed.ch", "web.de", "yahoo.de", "aol.com"}


# --- changes ----------------------------------------------------------------------------------

def _record(entity: str, entity_id: int, action: str, before: dict | None, after: dict | None, person: Person) -> int:
    return db.execute("INSERT INTO crm_changes(entity, entity_id, action, before, after, user_id, ts) VALUES (?,?,?,?,?,?,?)",
                      (entity, entity_id, action, json.dumps(before, default=str) if before is not None else None,
                       json.dumps(after, default=str) if after is not None else None, person.id, db.now_iso()))


def _label(entity: str, row: dict) -> str:
    if entity == "deal":
        return f"deal “{row['title']}”"
    return row["name"] if entity == "company" else contact_label(row)


def save(person: Person, entity: str, data: dict, record_id: int | None = None, *, force: bool = False) -> dict:
    """Create (no id) or change a company or contact. Returns {record, change_id}."""
    _need(person, "crm.edit")
    if entity not in TABLE:
        raise CrmError("Choose a company or a contact.")
    clean = _clean(person, entity, data)
    now = db.now_iso()
    if record_id is None:
        if entity == "company" and not clean.get("name"):
            raise CrmError("What's the company called?")
        if entity == "contact" and not (clean.get("first_name") or clean.get("last_name") or clean.get("email")):
            raise CrmError("Give the contact a name or an email address.")
        if not force:
            dup = duplicates(person, entity, {**data, **{k: clean.get(k) for k in ("email", "phone", "website") if k in clean}})
            if dup:
                raise DuplicateError(dup)
        if not clean.get("owner_id"):                  # new records belong to whoever adds them unless set
            clean["owner_id"] = person.id
        cols = {**clean, "created_by": person.id, "created_at": now, "updated_at": now}
        rid = db.execute(f"INSERT INTO {TABLE[entity]}({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         list(cols.values()))
        row = db.query_one(f"SELECT * FROM {TABLE[entity]} WHERE id = ?", (rid,))
        cid = _record(entity, rid, "create", None, row, person)
        db.log_activity("crm", f"{person.display_name} added the {entity} {_label(entity, row)}", user_id=person.id)
        return {"record": get(person, entity, rid), "change_id": cid}
    before = _get_visible(person, entity, record_id)
    if before["archived_at"]:
        raise CrmError(f"This {entity} is archived. Restore it first.")
    if entity == "company" and "name" in clean and not clean["name"]:
        raise CrmError("A company needs a name.")
    changes = {k: v for k, v in clean.items() if before.get(k) != v}
    if not changes:
        return {"record": get(person, entity, record_id), "change_id": None}
    db.execute(f"UPDATE {TABLE[entity]} SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ? WHERE id = ?",
               (*changes.values(), now, record_id))
    cid = _record(entity, record_id, "update", {k: before[k] for k in changes}, changes, person)
    db.log_activity("crm", f"{person.display_name} changed {_label(entity, before)} ({', '.join(changes)})",
                    user_id=person.id)
    return {"record": get(person, entity, record_id), "change_id": cid}


def set_archived(person: Person, entity: str, record_id: int, archived: bool) -> dict:
    _need(person, "crm.edit")
    row = _get_visible(person, entity, record_id)
    if bool(row["archived_at"]) == archived:
        return {"record": get(person, entity, record_id), "change_id": None}
    db.execute(f"UPDATE {TABLE[entity]} SET archived_at = ?, updated_at = ? WHERE id = ?",
               (db.now_iso() if archived else None, db.now_iso(), record_id))
    cid = _record(entity, record_id, "archive" if archived else "restore", {"archived_at": row["archived_at"]}, None,
                  person)
    db.log_activity("crm", f"{person.display_name} {'archived' if archived else 'restored'} {_label(entity, row)}",
                    user_id=person.id)
    return {"record": get(person, entity, record_id), "change_id": cid}


def history(person: Person, entity: str, record_id: int, limit: int = 50) -> list[dict]:
    _get_visible(person, entity, record_id)
    rows = db.query("SELECT c.*, u.display_name AS user_name FROM crm_changes c LEFT JOIN users u ON u.id = c.user_id "
                    "WHERE c.entity = ? AND c.entity_id = ? ORDER BY c.id DESC LIMIT ?", (entity, record_id, limit))
    last_own = next((r["id"] for r in rows if r["user_id"] == person.id and not r["undone_at"]), None)
    out = []
    for r in rows:
        before = json.loads(r["before"]) if r["before"] else {}
        after = json.loads(r["after"]) if r["after"] else {}
        fields = sorted(set(before) | set(after)) if r["action"] == "update" else []
        out.append({"id": r["id"], "action": r["action"], "ts": r["ts"], "user_name": r["user_name"],
                    "fields": fields, "undone": bool(r["undone_at"]),
                    "can_undo": r["id"] == last_own and person.can("crm.edit")})
    return out


def undo(person: Person, change_id: int | None = None) -> dict:
    """Undo a change of one's own (default: the latest one not undone yet)."""
    _need(person, "crm.edit")
    if change_id is None:
        ch = db.query_one("SELECT * FROM crm_changes WHERE user_id = ? AND undone_at IS NULL ORDER BY id DESC LIMIT 1",
                          (person.id,))
    else:
        ch = db.query_one("SELECT * FROM crm_changes WHERE id = ? AND user_id = ? AND undone_at IS NULL",
                          (change_id, person.id))
    if not ch:
        raise CrmError("There's no change of yours to undo.")
    entity, eid, action = ch["entity"], ch["entity_id"], ch["action"]
    before = json.loads(ch["before"]) if ch["before"] else None
    after = json.loads(ch["after"]) if ch["after"] else None
    later = db.query_one("SELECT 1 FROM crm_changes WHERE entity = ? AND entity_id = ? AND id > ? AND undone_at IS NULL",
                         (entity, eid, ch["id"]))
    if later:
        raise CrmError("Someone changed this record since; undo their change first, or edit it by hand.")
    now = db.now_iso()
    if entity == "activity":
        if action == "create":
            db.execute("DELETE FROM crm_activities WHERE id = ?", (eid,))
            what = "removed the timeline entry again"
        else:  # delete -> put it back
            cols = {k: v for k, v in (before or {}).items() if k != "id"}
            db.execute(f"INSERT INTO crm_activities(id, {', '.join(cols)}) VALUES (?, {','.join('?' * len(cols))})",
                       (eid, *cols.values()))
            what = "restored the timeline entry"
    else:
        row = db.query_one(f"SELECT * FROM {TABLE[entity]} WHERE id = ?", (eid,))
        if not row:
            raise CrmError("That record no longer exists.")
        if action == "create":
            db.execute(f"UPDATE {TABLE[entity]} SET archived_at = ?, updated_at = ? WHERE id = ?", (now, now, eid))
            what = f"archived {_label(entity, row)} again (new records are archived, not deleted)"
        elif action == "update":
            keep = {k: v for k, v in (before or {}).items() if k in FIELDS[entity]}
            db.execute(f"UPDATE {TABLE[entity]} SET {', '.join(f'{k} = ?' for k in keep)}, updated_at = ? WHERE id = ?",
                       (*keep.values(), now, eid))
            if entity == "deal" and "stage_id" in keep:    # back in an open stage = not closed any more (and vice versa)
                db.execute("UPDATE crm_deals SET closed_at = CASE WHEN (SELECT kind FROM crm_stages s WHERE "
                           "s.id = crm_deals.stage_id) = 'open' THEN NULL ELSE COALESCE(closed_at, ?) END WHERE id = ?",
                           (now, eid))
            what = f"put back {', '.join(keep)} of {_label(entity, row)}"
        elif action in ("archive", "restore"):
            db.execute(f"UPDATE {TABLE[entity]} SET archived_at = ?, updated_at = ? WHERE id = ?",
                       ((before or {}).get("archived_at"), now, eid))
            what = f"{'restored' if action == 'archive' else 'archived'} {_label(entity, row)} again"
        else:
            raise CrmError("That change can't be undone.")
    db.execute("UPDATE crm_changes SET undone_at = ? WHERE id = ?", (now, ch["id"]))
    db.log_activity("crm", f"{person.display_name} undid a CRM change: {what}", user_id=person.id)
    return {"undone": ch["id"], "what": what, "entity": entity, "entity_id": eid}


# --- timeline ---------------------------------------------------------------------------------

def _local(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.timezone)).strftime("%a %d %b %Y %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def _when(value: str | None) -> str:
    """An ISO date/time (local if no zone) -> UTC ISO; empty = now."""
    if not value:
        return db.now_iso()
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise CrmError("When? Give a date like 2026-10-12 or 2026-10-12 14:30.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
    if dt > datetime.now(timezone.utc) + timedelta(days=366):
        raise CrmError("That date is too far ahead.")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def log(person: Person, entity: str, record_id: int, kind: str, summary: str, body: str = "",
        when: str | None = None) -> dict:
    _need(person, "crm.edit")
    if kind not in ACTIVITY_KINDS:
        raise CrmError(f"Kind must be one of: {', '.join(ACTIVITY_KINDS)}.")
    row = _get_visible(person, entity, record_id)
    summary = _text(summary, 200)
    if not summary:
        raise CrmError("What happened? Give a short summary.")
    company_id = record_id if entity == "company" else row.get("company_id")
    contact_id = record_id if entity == "contact" else row.get("contact_id") if entity == "deal" else None
    cols = {"company_id": company_id, "contact_id": contact_id, "deal_id": record_id if entity == "deal" else None,
            "kind": kind, "summary": summary,
            "body": _multiline(body, 5000), "occurred_at": _when(when), "user_id": person.id, "created_at": db.now_iso()}
    aid = db.execute(f"INSERT INTO crm_activities({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     list(cols.values()))
    cid = _record("activity", aid, "create", None, {**cols, "id": aid}, person)
    db.execute(f"UPDATE {TABLE[entity]} SET updated_at = ? WHERE id = ?", (db.now_iso(), record_id))
    return {"activity_id": aid, "change_id": cid}


def delete_activity(person: Person, activity_id: int) -> dict:
    """People remove their own timeline entries (undo brings them back); others' entries stay."""
    _need(person, "crm.edit")
    row = db.query_one("SELECT * FROM crm_activities WHERE id = ?", (activity_id,))
    if not row:
        raise CrmError("No such timeline entry.")
    entity, rid = (("deal", row["deal_id"]) if row.get("deal_id") else
                   ("contact", row["contact_id"]) if row["contact_id"] else ("company", row["company_id"]))
    _get_visible(person, entity, rid)
    if row["user_id"] != person.id:
        raise CrmError("You can only remove timeline entries you wrote yourself.")
    db.execute("DELETE FROM crm_activities WHERE id = ?", (activity_id,))
    cid = _record("activity", activity_id, "delete", row, None, person)
    return {"change_id": cid}


def _addresses(entity: str, row: dict) -> tuple[list[str], list[str]]:
    """Email addresses and phone numbers that belong to a record (a company includes its contacts')."""
    emails = {row["email"].lower()} if row.get("email") else set()
    phones = {_phone(row["phone"])} if row.get("phone") else set()
    if entity == "company":
        for c in db.query("SELECT email, phone FROM crm_contacts WHERE company_id = ?", (row["id"],)):
            if c["email"]:
                emails.add(c["email"].lower())
            if c["phone"]:
                phones.add(_phone(c["phone"]))
    return sorted(emails), sorted(p for p in phones if p)


def _deal_events(entity: str, record_id: int, names: dict, limit: int) -> list[dict]:
    """Deals created, moved to another stage, won or lost, from the change log."""
    deals = ({d["id"]: d["title"] for d in db.query("SELECT id, title FROM crm_deals WHERE company_id = ?", (record_id,))}
             if entity == "company" else
             {d["id"]: d["title"] for d in db.query("SELECT id, title FROM crm_deals WHERE id = ?", (record_id,))})
    if not deals:
        return []
    stages = {s["id"]: s["name"] for s in db.query("SELECT id, name FROM crm_stages")}
    rows = db.query(f"SELECT * FROM crm_changes WHERE entity = 'deal' AND undone_at IS NULL AND entity_id IN "
                    f"({','.join('?' * len(deals))}) ORDER BY id DESC LIMIT ?", (*deals, limit))
    out = []
    for c in rows:
        before = json.loads(c["before"]) if c["before"] else {}
        after = json.loads(c["after"]) if c["after"] else {}
        if c["action"] == "create":
            text = f"New deal: {deals[c['entity_id']]}" + (f" ({stages.get(after.get('stage_id'), '')})" if after else "")
        elif c["action"] == "update" and "stage_id" in after:
            text = (f"{deals[c['entity_id']]}: {stages.get(before.get('stage_id'), '?')} → "
                    f"{stages.get(after['stage_id'], '?')}")
        else:
            continue
        out.append({"type": "deal", "id": c["id"], "deal_id": c["entity_id"], "summary": text, "body": "",
                    "at": c["ts"], "at_local": _local(c["ts"]), "by": names.get(c["user_id"]), "mine": False})
    return out


def timeline(person: Person, entity: str, record_id: int, *, kind: str = "", limit: int = 100) -> list[dict]:
    row = _get_visible(person, entity, record_id)
    names = _names()
    items: list[dict] = []
    col = {"company": "company_id", "contact": "contact_id", "deal": "deal_id"}[entity]
    if kind in ("", "note", "call", "meeting", "task", "document"):
        acts = db.query(f"SELECT a.*, c.first_name, c.last_name FROM crm_activities a LEFT JOIN crm_contacts c "
                        f"ON c.id = a.contact_id WHERE a.{col} = ?" + (" AND a.kind = ?" if kind else "")
                        + " ORDER BY a.occurred_at DESC LIMIT ?", (record_id, *([kind] if kind else []), limit))
        for a in acts:
            items.append({"type": a["kind"], "id": a["id"], "summary": a["summary"], "body": a["body"],
                          "at": a["occurred_at"], "at_local": _local(a["occurred_at"]), "by": names.get(a["user_id"]),
                          "contact": contact_label(a) if entity == "company" and a["contact_id"] else None,
                          "mine": a["user_id"] == person.id})
    if kind in ("", "deal") and entity in ("company", "deal"):
        items += _deal_events(entity, record_id, names, limit)
    emails, phones = _addresses(entity, row) if entity != "deal" else ([], [])
    if kind in ("", "email") and emails:
        from . import mail
        rows = db.query(
            "SELECT e.id, e.direction, e.kind, e.scope, e.user_id, e.from_addr, e.from_name, e.to_addrs, e.cc_addrs, "
            "e.subject, e.summary, e.created_at FROM emails e WHERE e.kind NOT IN ('spam','automated') AND ("
            + " OR ".join(["lower(e.from_addr) = ?"] * len(emails) + ["lower(e.to_addrs) LIKE ?"] * len(emails)
                          + ["lower(e.cc_addrs) LIKE ?"] * len(emails))
            + ") ORDER BY e.id DESC LIMIT ?", (*emails, *[f'%"{x}"%' for x in emails], *[f'%"{x}"%' for x in emails], limit))
        for e in rows:
            if not mail.may_read(e, person):
                continue
            items.append({"type": "email", "id": e["id"], "direction": e["direction"],
                          "summary": e["subject"] or "(no subject)", "body": e["summary"], "at": e["created_at"],
                          "at_local": _local(e["created_at"]),
                          "by": (e["from_name"] or e["from_addr"]) if e["direction"] == "in" else "sent",
                          "mine": False})
    if kind in ("", "call") and phones:
        everyone = person.can("everyone.view")
        rows = db.query("SELECT x.id, x.number, x.contact, x.task, x.status, x.result, x.requested_by, x.updated_at "
                        "FROM external_calls x WHERE x.number IN (" + ",".join("?" * len(phones)) + ")"
                        + ("" if everyone else " AND x.requested_by = ?") + " ORDER BY x.id DESC LIMIT ?",
                        (*phones, *([] if everyone else [person.id]), limit))
        for c in rows:
            items.append({"type": "outside_call", "id": c["id"], "summary": f"Alfred called: {c['task']}",
                          "body": c["result"] or c["status"], "at": c["updated_at"], "at_local": _local(c["updated_at"]),
                          "by": names.get(c["requested_by"]), "mine": False})
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items[:limit]


# --- deleting and merging: approval drafts ----------------------------------------------------

def request(person: Person, action: str, entity: str, record_id: int, *, merge_into: int | None = None,
            reason: str = "") -> dict:
    _need(person, "crm.edit")
    row = _get_visible(person, entity, record_id)
    label = _label(entity, row)
    reason = _text(reason, 200)
    if action == "delete":
        extra = ""
        if entity == "company":
            n = db.query_one("SELECT COUNT(*) AS n FROM crm_contacts WHERE company_id = ?", (record_id,))["n"]
            extra = f" Its {n} contact(s) stay, without a company." if n else ""
        summary = f"Delete the {entity} {label} for good, with its timeline.{extra}" + (f" Reason: {reason}" if reason else "")
        payload = {"entity": entity, "id": record_id}
    elif action == "merge":
        if not merge_into or merge_into == record_id:
            raise CrmError(f"Which {entity} should it be merged into?")
        keep = _get_visible(person, entity, merge_into)
        summary = (f"Merge the {entity} {label} (#{record_id}) into {_label(entity, keep)} (#{merge_into}): its "
                   f"timeline{', contacts' if entity == 'company' else ''} and missing details move over, then "
                   f"#{record_id} is deleted." + (f" Reason: {reason}" if reason else ""))
        payload = {"entity": entity, "id": record_id, "into": merge_into}
    else:
        raise CrmError("Action must be delete or merge.")
    return approvals.create("crm", action, summary, payload, person)


@approvals.handler("crm", "delete")
def _approved_delete(payload: dict, actor: Person) -> str:
    entity, rid = payload["entity"], int(payload["id"])
    row = db.query_one(f"SELECT * FROM {TABLE[entity]} WHERE id = ?", (rid,))
    if not row:
        raise ValueError("It no longer exists.")
    db.execute(f"DELETE FROM {TABLE[entity]} WHERE id = ?", (rid,))
    _record(entity, rid, "delete", row, None, actor)
    db.log_activity("crm", f"{actor.display_name} deleted the {entity} {_label(entity, row)} (approved)", user_id=actor.id)
    return f"Deleted {_label(entity, row)}."


@approvals.handler("crm", "merge")
def _approved_merge(payload: dict, actor: Person) -> str:
    entity, rid, into = payload["entity"], int(payload["id"]), int(payload["into"])
    src = db.query_one(f"SELECT * FROM {TABLE[entity]} WHERE id = ?", (rid,))
    dst = db.query_one(f"SELECT * FROM {TABLE[entity]} WHERE id = ?", (into,))
    if not src or not dst:
        raise ValueError("One of the two records no longer exists.")
    fields = COMPANY_FIELDS if entity == "company" else CONTACT_FIELDS
    fill = {k: src[k] for k in fields if k not in ("tags", "custom") and src[k] not in (None, "") and dst[k] in (None, "")}
    tags = list(dict.fromkeys(json.loads(dst["tags"] or "[]") + json.loads(src["tags"] or "[]")))[:MAX_TAGS]
    custom = {**json.loads(src["custom"] or "{}"), **json.loads(dst["custom"] or "{}")}
    if src["notes"] and dst["notes"] and src["notes"] not in dst["notes"]:
        fill["notes"] = (dst["notes"] + "\n\n" + src["notes"])[:5000]
    fill.update(tags=json.dumps(tags, ensure_ascii=False), custom=json.dumps(custom, ensure_ascii=False))
    fill = {k: v for k, v in fill.items() if dst[k] != v}
    col = "company_id" if entity == "company" else "contact_id"
    with db.tx() as conn:
        conn.execute(f"UPDATE {TABLE[entity]} SET {''.join(f'{k} = ?, ' for k in fill)}updated_at = ? WHERE id = ?",
                     (*fill.values(), db.now_iso(), into))
        conn.execute(f"UPDATE crm_activities SET {col} = ? WHERE {col} = ?", (into, rid))
        if entity == "company":
            conn.execute("UPDATE crm_contacts SET company_id = ? WHERE company_id = ?", (into, rid))
        conn.execute(f"DELETE FROM {TABLE[entity]} WHERE id = ?", (rid,))
    _record(entity, rid, "delete", src, {"merged_into": into}, actor)
    if fill:
        _record(entity, into, "update", {k: dst[k] for k in fill}, fill, actor)
    db.log_activity("crm", f"{actor.display_name} merged {_label(entity, src)} into {_label(entity, dst)} (approved)",
                    user_id=actor.id)
    return f"Merged {_label(entity, src)} into {_label(entity, dst)}."
