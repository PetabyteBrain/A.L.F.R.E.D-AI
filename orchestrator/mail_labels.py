"""Organising Alfred's mailbox: groups, tags and each viewer's read / starred / archived state.

- Groups work like folders: an email is in at most one group. Tags are labels: any number per email.
- Personal mail is organised with the owner's own groups and tags; company mail (inquiries, mail sent in the
  company's name) with the team's shared ones, which admins and cofounders manage together.
- Read, starred and archived are per viewer (two cofounders reading the same inquiry each have their own).
  Incoming mail is unread until it's opened; mail Alfred sent counts as read.
- `search` is the one listing used by the Mail tab and the list_emails tool.
"""
from __future__ import annotations

import re

from . import db, mail
from .identity.registry import Person

KINDS = ("group", "tag")
COLORS = ("slate", "blue", "teal", "green", "amber", "orange", "red", "pink", "purple")
TEAM = ("admin", "cofounder")
BOXES = ("inbox", "unread", "starred", "inquiries", "sent", "archived", "all")
MAX_LABELS = 60


class LabelError(ValueError):
    """Message is safe to show."""


def _team(person: Person) -> bool:
    return person.role in TEAM


def _clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:40]
    if not name or re.search(r"[<>]", name):
        raise LabelError("Give it a short name (no < or >).")
    return name


# --- labels ----------------------------------------------------------------------------------------

def _visible(person: Person) -> tuple[str, list]:
    if _team(person):
        return "((scope = 'personal' AND user_id = ?) OR scope = 'company')", [person.id]
    return "(scope = 'personal' AND user_id = ?)", [person.id]


def get_label(person: Person, label_id: int) -> dict:
    where, params = _visible(person)
    row = db.query_one(f"SELECT * FROM mail_labels WHERE id = ? AND {where}", (int(label_id), *params))
    if not row:
        raise LabelError("Unknown group or tag.")
    return row


def list_labels(person: Person) -> list[dict]:
    where, params = _visible(person)
    rows = db.query(f"SELECT id, kind, scope, name, color FROM mail_labels WHERE {where} ORDER BY kind, scope, "
                    "name COLLATE NOCASE", params)
    counts = {r["label_id"]: r for r in db.query(
        "SELECT l.label_id, COUNT(*) AS total, SUM(CASE WHEN e.direction = 'in' AND s.read_at IS NULL THEN 1 ELSE 0 END) "
        "AS unread FROM email_labels l JOIN emails e ON e.id = l.email_id "
        "LEFT JOIN email_state s ON s.email_id = e.id AND s.user_id = ? GROUP BY l.label_id", (person.id,))}
    for r in rows:
        c = counts.get(r["id"]) or {}
        r["total"], r["unread"] = c.get("total") or 0, c.get("unread") or 0
    return rows


def find_label(person: Person, kind: str, name: str, scope: str | None = None) -> dict | None:
    where, params = _visible(person)
    sql = f"SELECT * FROM mail_labels WHERE kind = ? AND name = ? COLLATE NOCASE AND {where}"
    args = [kind, " ".join(str(name or "").split()), *params]
    if scope:
        sql += " AND scope = ?"
        args.append(scope)
    return db.query_one(sql + " ORDER BY scope DESC LIMIT 1", args)


def create_label(person: Person, *, kind: str, scope: str, name: str, color: str = "slate") -> dict:
    if kind not in KINDS:
        raise LabelError("Choose group or tag.")
    if scope == "company" and not _team(person):
        raise LabelError("Only admins and cofounders manage the company's groups and tags.")
    if scope not in ("personal", "company"):
        raise LabelError("Scope must be personal or company.")
    name = _clean_name(name)
    if find_label(person, kind, name, scope):
        raise LabelError(f"There is already a {kind} called “{name}”.")
    owner = person.id if scope == "personal" else None
    n = db.query_one("SELECT COUNT(*) n FROM mail_labels WHERE scope = ? AND user_id IS ?", (scope, owner))["n"]
    if n >= MAX_LABELS:
        raise LabelError(f"That's the limit of {MAX_LABELS} groups and tags.")
    lid = db.execute("INSERT INTO mail_labels(kind, scope, user_id, name, color, created_at) VALUES (?,?,?,?,?,?)",
                     (kind, scope, owner, name, color if color in COLORS else "slate", db.now_iso()))
    return get_label(person, lid)


def _editable(person: Person, label_id: int) -> dict:
    row = get_label(person, label_id)
    if row["scope"] == "company" and not _team(person):
        raise LabelError("Only admins and cofounders manage the company's groups and tags.")
    return row


def update_label(person: Person, label_id: int, *, name: str | None = None, color: str | None = None) -> dict:
    row = _editable(person, label_id)
    if name is not None:
        name = _clean_name(name)
        clash = find_label(person, row["kind"], name, row["scope"])
        if clash and clash["id"] != row["id"]:
            raise LabelError(f"There is already a {row['kind']} called “{name}”.")
        db.execute("UPDATE mail_labels SET name = ? WHERE id = ?", (name, row["id"]))
    if color is not None:
        if color not in COLORS:
            raise LabelError("Unknown colour.")
        db.execute("UPDATE mail_labels SET color = ? WHERE id = ?", (color, row["id"]))
    return get_label(person, label_id)


def delete_label(person: Person, label_id: int) -> None:
    row = _editable(person, label_id)
    db.execute("DELETE FROM mail_labels WHERE id = ?", (row["id"],))     # its assignments go with it


def labels_for(email_ids: list[int]) -> dict[int, list[dict]]:
    if not email_ids:
        return {}
    rows = db.query(f"SELECT l.email_id, m.id, m.kind, m.name, m.color FROM email_labels l JOIN mail_labels m "
                    f"ON m.id = l.label_id WHERE l.email_id IN ({','.join('?' * len(email_ids))}) "
                    "ORDER BY m.kind, m.name COLLATE NOCASE", email_ids)
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r.pop("email_id"), []).append(r)
    return out


# --- per-viewer state ------------------------------------------------------------------------------

def _readable_rows(person: Person, email_ids: list) -> list[dict]:
    ids = [int(i) for i in dict.fromkeys(email_ids or [])][:500]
    if not ids:
        raise LabelError("No emails chosen.")
    rows = [mail.get(i) for i in ids]
    rows = [r for r in rows if r and mail.may_read(r, person)]
    if not rows:
        raise LabelError("I can't find those emails.")
    return rows


def set_state(person: Person, email_ids: list, *, read: bool | None = None, starred: bool | None = None,
              archived: bool | None = None) -> int:
    rows = _readable_rows(person, email_ids)
    with db.tx() as conn:
        for r in rows:
            conn.execute("INSERT OR IGNORE INTO email_state(email_id, user_id) VALUES (?,?)", (r["id"], person.id))
            if read is not None:
                conn.execute("UPDATE email_state SET read_at = ? WHERE email_id = ? AND user_id = ?",
                             (db.now_iso() if read else None, r["id"], person.id))
            if starred is not None:
                conn.execute("UPDATE email_state SET starred = ? WHERE email_id = ? AND user_id = ?",
                             (int(starred), r["id"], person.id))
            if archived is not None:
                conn.execute("UPDATE email_state SET archived = ? WHERE email_id = ? AND user_id = ?",
                             (int(archived), r["id"], person.id))
    return len(rows)


def mark_read(person: Person, email_id: int) -> None:
    db.execute("INSERT INTO email_state(email_id, user_id, read_at) VALUES (?,?,?) ON CONFLICT(email_id, user_id) "
               "DO UPDATE SET read_at = COALESCE(read_at, excluded.read_at)", (email_id, person.id, db.now_iso()))


def state_for(person: Person, email_ids: list[int]) -> dict[int, dict]:
    if not email_ids:
        return {}
    rows = db.query(f"SELECT email_id, read_at, starred, archived FROM email_state WHERE user_id = ? AND email_id IN "
                    f"({','.join('?' * len(email_ids))})", (person.id, *email_ids))
    return {r["email_id"]: r for r in rows}


# --- assigning ---------------------------------------------------------------------------------------

def assign(person: Person, email_ids: list, *, add_tags: list | None = None, remove_tags: list | None = None,
           group: int | None = None, set_group: bool = False) -> int:
    """Tags/groups by id. `set_group` with group=None takes the emails out of their group."""
    rows = _readable_rows(person, email_ids)
    add = [get_label(person, t) for t in add_tags or []]
    remove = [get_label(person, t) for t in remove_tags or []]
    grp = get_label(person, group) if set_group and group else None
    for lab in add + remove:
        if lab["kind"] != "tag":
            raise LabelError(f"“{lab['name']}” is a group: move emails into it instead.")
    if grp and grp["kind"] != "group":
        raise LabelError(f"“{grp['name']}” is a tag, not a group.")
    with db.tx() as conn:
        for r in rows:
            mine = [lab for lab in add if lab["scope"] == r["scope"]]
            for lab in mine:
                conn.execute("INSERT OR IGNORE INTO email_labels(email_id, label_id) VALUES (?,?)", (r["id"], lab["id"]))
            for lab in remove:
                conn.execute("DELETE FROM email_labels WHERE email_id = ? AND label_id = ?", (r["id"], lab["id"]))
            if set_group:
                conn.execute("DELETE FROM email_labels WHERE email_id = ? AND label_id IN (SELECT id FROM mail_labels "
                             "WHERE kind = 'group' AND scope = ?)", (r["id"], r["scope"]))
                if grp and grp["scope"] == r["scope"]:
                    conn.execute("INSERT INTO email_labels(email_id, label_id) VALUES (?,?)", (r["id"], grp["id"]))
    mismatch = [lab["name"] for lab in add + ([grp] if grp else [])
                if not any(lab["scope"] == r["scope"] for r in rows)]
    if mismatch:
        raise LabelError(f"{', '.join(mismatch)}: personal groups and tags go on your own mail, company ones on "
                         "company mail.")
    return len(rows)


def organize(person: Person, email_ids: list, *, group: str | None = None, add_tags: list | None = None,
             remove_tags: list | None = None, read: bool | None = None, starred: bool | None = None,
             archived: bool | None = None) -> dict:
    """By name, for the tool: missing tags/groups are created in the scope of the emails ("" group = none)."""
    rows = _readable_rows(person, email_ids)
    scopes = {r["scope"] for r in rows}
    if len(scopes) > 1 and (group or add_tags):
        raise LabelError("Organise personal and company mail separately.")
    scope = scopes.pop() if scopes else "personal"

    def label(kind, name, create=True):
        lab = find_label(person, kind, name, scope)
        if not lab and create:
            lab = create_label(person, kind=kind, scope=scope, name=name)
        return lab
    ids = [r["id"] for r in rows]
    add = [label("tag", t)["id"] for t in add_tags or [] if str(t).strip()]
    remove = [lab["id"] for t in remove_tags or [] if (lab := label("tag", t, create=False))]
    if add or remove or group is not None:
        assign(person, ids, add_tags=add, remove_tags=remove,
               group=label("group", group)["id"] if group else None, set_group=group is not None)
    if read is not None or starred is not None or archived is not None:
        set_state(person, ids, read=read, starred=starred, archived=archived)
    return {"organised": len(ids), "labels": {i: [lab["name"] for lab in labs] for i, labs in labels_for(ids).items()}}


# --- listing -------------------------------------------------------------------------------------------

def search(person: Person, *, box: str = "inbox", group: int | None = None, tag: int | None = None, query: str = "",
           unread_only: bool = False, offset: int = 0, limit: int = 50) -> dict:
    if box not in BOXES:
        raise LabelError("Unknown box.")
    team = _team(person)
    if box == "inquiries" and not team:
        raise LabelError("Only admins and cofounders see the company's mail.")
    own = "(e.scope = 'personal' AND e.user_id = ?)"
    seen = f"({own} OR (e.scope = 'company' AND ?))"
    where, params = [], []
    archived = "COALESCE(s.archived, 0)"
    if box == "inbox":
        where.append(f"e.direction = 'in' AND e.kind NOT IN ('spam','automated') AND ({own} OR (e.kind = 'reply' "
                     f"AND e.user_id = ?)) AND {archived} = 0")
        params += [person.id, person.id]
    elif box == "inquiries":
        where.append(f"e.direction = 'in' AND e.scope = 'company' AND e.kind NOT IN ('spam','automated') AND {archived} = 0")
    elif box == "sent":
        where.append(f"e.direction = 'out' AND {seen}")
        params += [person.id, int(team)]
    else:
        where.append(seen)
        params += [person.id, int(team)]
        if box == "unread":
            where.append(f"e.direction = 'in' AND s.read_at IS NULL AND e.kind NOT IN ('spam','automated') AND {archived} = 0")
        elif box == "starred":
            where.append("COALESCE(s.starred, 0) = 1")
        elif box == "archived":
            where.append(f"{archived} = 1")
    for label_id in (group, tag):
        if label_id:
            get_label(person, label_id)
            where.append("EXISTS (SELECT 1 FROM email_labels l WHERE l.email_id = e.id AND l.label_id = ?)")
            params.append(int(label_id))
    if unread_only:
        where.append("e.direction = 'in' AND s.read_at IS NULL")
    if query:
        like = f"%{query.strip()[:80]}%"
        where.append("(e.subject LIKE ? OR e.from_addr LIKE ? OR e.from_name LIKE ? OR e.to_addrs LIKE ? "
                     "OR e.summary LIKE ? OR e.own_text LIKE ?)")
        params += [like] * 6
    base = (f"FROM emails e LEFT JOIN email_state s ON s.email_id = e.id AND s.user_id = {int(person.id)} "
            f"WHERE {' AND '.join(where)}")
    total = db.query_one(f"SELECT COUNT(*) n {base}", params)["n"]
    limit = max(1, min(int(limit or 50), 200))
    rows = db.query(f"SELECT e.id {base} ORDER BY e.id DESC LIMIT ? OFFSET ?", (*params, limit, max(0, int(offset))))
    ids = [r["id"] for r in rows]
    return {"box": box, "total": total, "offset": offset, "emails": [mail.get(i) for i in ids],
            "labels": labels_for(ids), "state": state_for(person, ids)}


def counts(person: Person) -> dict:
    """Unread per box, for the sidebar."""
    out = {}
    for box in ("inbox", "inquiries") if _team(person) else ("inbox",):
        out[box] = search(person, box=box, unread_only=True, limit=1)["total"]
    out["starred"] = search(person, box="starred", limit=1)["total"]
    return out
