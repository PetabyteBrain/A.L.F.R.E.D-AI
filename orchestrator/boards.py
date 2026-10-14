"""Work boards (roadmap #59): Azure-Boards-style boards that are part of Alfred, not a tool beside it.

- A board has its own item types (Epic, Feature, Task, Bug, ... editable) and states (columns, each in a category
  todo / doing / done, editable), and is shared per everyone / group (team) / role / person with a level:
  view < request < contribute < manage. "Request" is for people who may not create items but can ask: their request
  goes to the team's manager (a group's `manager_id`) or the board's managers, who accept (it becomes an item) or
  decline it (Telegram buttons `brq:yes|no:<id>` or the dashboard).
- Items (`board_items`) have a type, state, priority, tags, start and due date, estimate, a parent item, an assignee
  (a person) and/or a team (a people group), links to a CRM company or deal, attached company files, comments
  (@mentions notify), and a history of every change.
- Teams are the existing people groups (permissions.groups), with a manager and a colour; creating or changing them
  needs teams.manage (or roles.manage).
- The sales pipeline is a built-in board ("sales"): its columns are the CRM stages and its items the deals
  (orchestrator/crm_deals.py), so moving a card there moves the deal everywhere; the CRM's own Pipeline view stays.
- Woven into Alfred: due items are in the agenda (Alfred's calendar), the morning briefing and a morning reminder,
  assignments and mentions notify on Telegram, items show on the Team page and on CRM companies and deals, and Alfred
  creates, moves and reports on items by chat (tools board_*).

Permissions: boards.use (boards shared with them), boards.create, boards.admin (manage every board), teams.manage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)
LEVELS = ("view", "request", "contribute", "manage")
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}
SALES = "sales"
PRIORITIES = {1: "Urgent", 2: "Normal", 3: "Low"}
CATEGORIES = ("todo", "doing", "done")
TEMPLATES = {
    "agile": {"label": "Agile: epics, features, tasks and bugs",
              "types": [("Epic", "#b58cff"), ("Feature", "#7aa7ff"), ("Task", "#5ad0e6"), ("Bug", "#ff6b6b")],
              "states": [("New", "todo"), ("Active", "doing"), ("Review", "doing"), ("Done", "done")]},
    "simple": {"label": "Simple: to do, doing, done",
               "types": [("Task", "#5ad0e6")],
               "states": [("To do", "todo"), ("Doing", "doing"), ("Done", "done")]},
    "projects": {"label": "Client projects: briefing to delivery",
                 "types": [("Project", "#b58cff"), ("Task", "#5ad0e6"), ("Feedback", "#ffb040")],
                 "states": [("Briefing", "todo"), ("In progress", "doing"), ("With the client", "doing"),
                            ("Delivered", "done")]},
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class BoardError(ValueError):
    """Message is safe to show."""


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _text(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _date(v, label: str) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip()[:10]
    try:
        if not _DATE.match(s):
            raise ValueError
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise BoardError(f"The {label} must be a date like 2026-11-02.")


def _names() -> dict[int, str]:
    return {u["id"]: u["display_name"] for u in db.query("SELECT id, display_name FROM users")}


def _later(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()


async def _tell(user_id: int, text: str, note: str, buttons=None) -> None:
    from . import telegram_handler
    from .identity import registry
    if not (settings.telegram_enabled and registry.telegram_for(user_id)):
        return
    try:
        await telegram_handler.send_to_user(user_id, text, note=note, record=False, buttons=buttons)
    except Exception:  # noqa: BLE001
        log.info("Board message to user %s failed", user_id)


# --- access ------------------------------------------------------------------------------------------

def _board_row(board_id) -> dict:
    row = db.query_one("SELECT * FROM boards WHERE id = ?", (board_id,))
    if not row:
        raise BoardError("No such board.")
    return row


def _team_ids(person: Person) -> list[int]:
    from . import permissions
    return [g["id"] for g in permissions.groups_of(person.id)]


def level_of(person: Person, board: dict) -> str | None:
    """The person's level on a board: the highest of what's shared with everyone, their groups, roles and them."""
    if not person.can("boards.use"):
        return None
    if person.can("boards.admin") or board.get("created_by") == person.id:
        return "manage"
    roles = list(person.roles or (person.role,))
    teams = _team_ids(person)
    best = None
    for r in db.query("SELECT subject_kind, subject, level FROM board_access WHERE board_id = ?", (board["id"],)):
        hit = (r["subject_kind"] == "everyone"
               or (r["subject_kind"] == "user" and r["subject"] == str(person.id))
               or (r["subject_kind"] == "role" and r["subject"] in roles)
               or (r["subject_kind"] == "group" and r["subject"].isdigit() and int(r["subject"]) in teams))
        if hit and (best is None or RANK[r["level"]] > RANK[best]):
            best = r["level"]
    # A team's manager can always work on (and decide requests for) a board their team is on.
    if best in ("view", "request") and db.query_one(
            "SELECT 1 FROM board_access a JOIN people_groups g ON a.subject_kind = 'group' AND a.subject = CAST(g.id AS TEXT) "
            "WHERE a.board_id = ? AND g.manager_id = ?", (board["id"], person.id)):
        best = "contribute"
    return best


def _require(person: Person, board_id, need: str) -> dict:
    board = _board_row(board_id)
    lvl = level_of(person, board)
    if lvl is None or board["archived_at"] and need != "manage" and RANK[lvl] < RANK["manage"]:
        raise BoardError("No such board.")
    if RANK[lvl] < RANK[need]:
        raise BoardError({"view": "You can't see this board.", "request": "You can't ask for work on this board.",
                          "contribute": "You can only view this board (or ask the team for something).",
                          "manage": "Only the board's managers can change that."}[need])
    return {**board, "level": lvl}


def can_see_item(person: Person, item: dict) -> bool:
    try:
        _require(person, item["board_id"], "view")
        return True
    except BoardError:
        return False


# --- boards ------------------------------------------------------------------------------------------

def _clean_key(key: str, exclude: int | None = None) -> str:
    key = re.sub(r"[^A-Z0-9]", "", str(key or "").upper())[:8]
    if len(key) < 2:
        raise BoardError("The short key needs 2 to 8 letters or digits (e.g. WEB).")
    if key == "DEAL" or db.query_one("SELECT 1 FROM boards WHERE key = ? AND id != ?", (key, exclude or 0)):
        raise BoardError(f"The key {key} is already taken.")
    return key


def _suggest_key(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name.upper())
    base = ("".join(w[0] for w in words) if len(words) > 1 else (words[0] if words else "BRD"))[:4] or "BRD"
    base = base if len(base) >= 2 else (base + "B")
    key, n = base, 2
    while db.query_one("SELECT 1 FROM boards WHERE key = ?", (key,)) or key == "DEAL":
        key, n = f"{base}{n}", n + 1
    return key


def _clean_access(rows: list) -> list[tuple[str, str, str]]:
    out = {}
    for r in rows or []:
        kind, subject, level = r.get("kind") or r.get("subject_kind"), str(r.get("subject") or ""), r.get("level")
        if kind not in ("everyone", "group", "role", "user") or level not in LEVELS:
            raise BoardError("Sharing: who (everyone, a team, a role or a person) and a level (view, request, "
                             "contribute, manage).")
        if kind == "everyone":
            subject = ""
        elif kind == "group" and not (subject.isdigit() and db.query_one("SELECT 1 FROM people_groups WHERE id = ?",
                                                                          (int(subject),))):
            raise BoardError("Unknown team.")
        elif kind == "role" and not db.query_one("SELECT 1 FROM roles WHERE name = ?", (subject,)):
            raise BoardError("Unknown role.")
        elif kind == "user" and not (subject.isdigit() and db.query_one("SELECT 1 FROM users WHERE id = ?", (int(subject),))):
            raise BoardError("Unknown person.")
        out[(kind, subject)] = level
    if len(out) > 60:
        raise BoardError("At most 60 sharing rules per board.")
    return [(k, s, lvl) for (k, s), lvl in out.items()]


def create_board(person: Person, *, name: str, key: str = "", description: str = "", template: str = "agile",
                 sharing: list | None = None) -> dict:
    if not (person.can("boards.use") and person.can("boards.create")):
        raise BoardError("You don't have permission to create boards.")
    name = _text(name, 60)
    if not name:
        raise BoardError("What's the board called?")
    key = _clean_key(key) if key else _suggest_key(name)
    tpl = TEMPLATES.get(template) or TEMPLATES["agile"]
    access = _clean_access(sharing if sharing is not None else [{"kind": "everyone", "level": "contribute"}])
    now = db.now_iso()
    with db.tx() as conn:
        bid = conn.execute("INSERT INTO boards(name, key, description, created_by, created_at, updated_at) VALUES "
                           "(?,?,?,?,?,?)", (name, key, _text(description, 300), person.id, now, now)).lastrowid
        conn.executemany("INSERT INTO board_types(board_id, name, color, position) VALUES (?,?,?,?)",
                         [(bid, n, c, i) for i, (n, c) in enumerate(tpl["types"])])
        conn.executemany("INSERT INTO board_states(board_id, name, category, position) VALUES (?,?,?,?)",
                         [(bid, n, c, i) for i, (n, c) in enumerate(tpl["states"])])
        conn.executemany("INSERT INTO board_access(board_id, subject_kind, subject, level) VALUES (?,?,?,?)",
                         [(bid, *a) for a in access])
    db.log_activity("boards", f"{person.display_name} created the board {name} ({key})", user_id=person.id)
    return get_board(person, bid)


def update_board(person: Person, board_id: int, *, name: str | None = None, key: str | None = None,
                 description: str | None = None, archived: bool | None = None) -> dict:
    b = _require(person, board_id, "manage")
    changes = {}
    if name is not None:
        changes["name"] = _text(name, 60)
        if not changes["name"]:
            raise BoardError("A board needs a name.")
    if key is not None and key.upper() != b["key"]:
        changes["key"] = _clean_key(key, board_id)
    if description is not None:
        changes["description"] = _text(description, 300)
    if archived is not None:
        changes["archived_at"] = db.now_iso() if archived else None
    if changes:
        db.execute(f"UPDATE boards SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ? WHERE id = ?",
                   (*changes.values(), db.now_iso(), board_id))
        db.log_activity("boards", f"{person.display_name} changed the board {b['name']} ({', '.join(changes)})",
                        user_id=person.id)
    return get_board(person, board_id)


def set_access(person: Person, board_id: int, rows: list) -> dict:
    b = _require(person, board_id, "manage")
    access = _clean_access(rows)
    with db.tx() as conn:
        conn.execute("DELETE FROM board_access WHERE board_id = ?", (board_id,))
        conn.executemany("INSERT INTO board_access(board_id, subject_kind, subject, level) VALUES (?,?,?,?)",
                         [(board_id, *a) for a in access])
    db.log_activity("boards", f"{person.display_name} changed who can use the board {b['name']}", user_id=person.id)
    return get_board(person, board_id)


def _types(board_id: int, include_archived: bool = False) -> list[dict]:
    return db.query("SELECT * FROM board_types WHERE board_id = ?" + ("" if include_archived else " AND archived = 0")
                    + " ORDER BY position, id", (board_id,))


def _states(board_id: int, include_archived: bool = False) -> list[dict]:
    return db.query("SELECT s.*, (SELECT COUNT(*) FROM board_items i WHERE i.state_id = s.id AND i.archived_at IS NULL) "
                    "AS items FROM board_states s WHERE s.board_id = ?" + ("" if include_archived else " AND s.archived = 0")
                    + " ORDER BY s.position, s.id", (board_id,))


def save_type(person: Person, board_id: int, *, type_id: int | None = None, name: str | None = None,
              color: str | None = None, archived: bool | None = None) -> list[dict]:
    _require(person, board_id, "manage")
    if type_id:
        if not db.query_one("SELECT 1 FROM board_types WHERE id = ? AND board_id = ?", (type_id, board_id)):
            raise BoardError("No such item type.")
    if name is not None and not _text(name, 30):
        raise BoardError("Give the type a name.")
    if color is not None and not _COLOR.match(color):
        raise BoardError("Colours look like #5ad0e6.")
    if type_id is None:
        pos = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM board_types WHERE board_id = ?", (board_id,))["p"]
        db.execute("INSERT INTO board_types(board_id, name, color, position) VALUES (?,?,?,?)",
                   (board_id, _text(name or "Item", 30), color or "#5ad0e6", pos))
    else:
        if archived and len(_types(board_id)) <= 1:
            raise BoardError("A board keeps at least one item type.")
        for col, v in (("name", _text(name, 30) if name is not None else None), ("color", color),
                       ("archived", int(archived) if archived is not None else None)):
            if v is not None:
                db.execute(f"UPDATE board_types SET {col} = ? WHERE id = ?", (v, type_id))
    return _types(board_id, include_archived=True)


def save_state(person: Person, board_id: int, *, state_id: int | None = None, name: str | None = None,
               category: str | None = None, wip_limit: int | None = None, archived: bool | None = None) -> list[dict]:
    _require(person, board_id, "manage")
    if category is not None and category not in CATEGORIES:
        raise BoardError("A column is a to-do, in-progress or done column.")
    if name is not None and not _text(name, 30):
        raise BoardError("Give the column a name.")
    if wip_limit is not None and not (0 <= int(wip_limit) <= 999):
        raise BoardError("The work-in-progress limit is 0 (none) to 999.")
    if state_id is None:
        pos = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM board_states WHERE board_id = ?",
                           (board_id,))["p"]
        db.execute("INSERT INTO board_states(board_id, name, category, position, wip_limit) VALUES (?,?,?,?,?)",
                   (board_id, _text(name or "New column", 30), category or "doing", pos, wip_limit or None))
        return _states(board_id, include_archived=True)
    s = db.query_one("SELECT * FROM board_states WHERE id = ? AND board_id = ?", (state_id, board_id))
    if not s:
        raise BoardError("No such column.")
    if archived:
        if db.query_one("SELECT 1 FROM board_items WHERE state_id = ? AND archived_at IS NULL", (state_id,)):
            raise BoardError("Move the items out of this column first.")
        live = [x for x in _states(board_id) if x["id"] != state_id]
        if not live or not any(x["category"] == "done" for x in live) or not any(x["category"] != "done" for x in live):
            raise BoardError("A board keeps at least one open and one done column.")
    if category and category != s["category"]:
        live = [x for x in _states(board_id) if x["id"] != state_id]
        if not any((x["category"] == "done") for x in live) and category != "done":
            raise BoardError("A board keeps at least one done column.")
    for col, v in (("name", _text(name, 30) if name is not None else None), ("category", category),
                   ("wip_limit", (int(wip_limit) or None) if wip_limit is not None else None),
                   ("archived", int(archived) if archived is not None else None)):
        if v is not None or col == "wip_limit" and wip_limit is not None:
            db.execute(f"UPDATE board_states SET {col} = ? WHERE id = ?", (v, state_id))
    if category:          # items there are closed or reopened with their column
        db.execute("UPDATE board_items SET closed_at = CASE WHEN ? = 'done' THEN COALESCE(closed_at, ?) ELSE NULL END "
                   "WHERE state_id = ?", (category, db.now_iso(), state_id))
    return _states(board_id, include_archived=True)


def reorder(person: Person, board_id: int, what: str, ids: list[int]) -> None:
    _require(person, board_id, "manage")
    table = {"types": "board_types", "states": "board_states"}.get(what)
    if not table:
        raise BoardError("Reorder types or states.")
    for i, x in enumerate(ids):
        db.execute(f"UPDATE {table} SET position = ? WHERE id = ? AND board_id = ?", (i, int(x), board_id))


def _people_for(board: dict) -> list[dict]:
    """People who can see the board (they can be assigned)."""
    from .identity import registry
    out = []
    for u in db.query("SELECT id, display_name FROM users WHERE active = 1 ORDER BY display_name"):
        p = registry.get_person(u["id"])
        if p and level_of(p, board):
            out.append({"id": u["id"], "display_name": u["display_name"]})
    return out


def _teams() -> list[dict]:
    from . import permissions
    return [{"id": g["id"], "name": g["name"], "color": g.get("color") or "", "manager_id": g.get("manager_id"),
             "manager_name": g.get("manager_name"), "members": [m["id"] for m in g["members"]]}
            for g in permissions.groups()]


def boards_for(person: Person, include_archived: bool = False) -> list[dict]:
    out = []
    if person.can("boards.use"):
        for b in db.query("SELECT * FROM boards" + ("" if include_archived else " WHERE archived_at IS NULL")
                          + " ORDER BY lower(name)"):
            lvl = level_of(person, b)
            if not lvl:
                continue
            c = db.query_one("SELECT COUNT(*) AS open, SUM(CASE WHEN assignee_id = ? THEN 1 ELSE 0 END) AS mine FROM "
                             "board_items i JOIN board_states s ON s.id = i.state_id WHERE i.board_id = ? AND "
                             "i.archived_at IS NULL AND s.category != 'done'", (person.id, b["id"]))
            out.append({"id": b["id"], "kind": "board", "name": b["name"], "key": b["key"], "level": lvl,
                        "description": b["description"], "archived": bool(b["archived_at"]),
                        "open": c["open"] or 0, "mine": c["mine"] or 0})
    if person.can("crm.use") and person.can("boards.use"):
        from . import crm_deals
        out.append({"id": SALES, "kind": "pipeline", "name": "Sales pipeline", "key": "DEAL",
                    "level": "contribute" if person.can("crm.edit") else "view", "description": "Deals from the CRM",
                    "archived": False, "open": crm_deals.search(person, status="open", limit=1)["total"],
                    "mine": crm_deals.search(person, status="open", mine=True, limit=1)["total"]})
    return out


def get_board(person: Person, board_id) -> dict:
    if str(board_id) == SALES:
        return _sales_board(person)
    b = _require(person, int(board_id), "view")
    out = {"id": b["id"], "kind": "board", "name": b["name"], "key": b["key"], "description": b["description"],
           "level": b["level"], "archived": bool(b["archived_at"]), "created_by": b["created_by"],
           "types": _types(b["id"], include_archived=b["level"] == "manage"),
           "states": _states(b["id"], include_archived=b["level"] == "manage"),
           "teams": _teams(), "people": _people_for(b), "priorities": PRIORITIES, "templates": None}
    if b["level"] == "manage":
        names = _names()
        groups = {t["id"]: t["name"] for t in out["teams"]}
        out["access"] = [{"kind": r["subject_kind"], "subject": r["subject"], "level": r["level"],
                          "label": ("Everyone with boards" if r["subject_kind"] == "everyone" else
                                    groups.get(int(r["subject"]), "?") if r["subject_kind"] == "group" else
                                    names.get(int(r["subject"]), "?") if r["subject_kind"] == "user" else r["subject"])}
                         for r in db.query("SELECT * FROM board_access WHERE board_id = ?", (b["id"],))]
        from . import permissions
        out["roles"] = [{"name": r["name"], "label": r["label"] or r["name"]}
                        for r in db.query("SELECT name, label FROM roles WHERE archived = 0 ORDER BY name")]
        out["all_people"] = [{"id": p["id"], "display_name": p["display_name"]}
                             for p in permissions.people_with("boards.use")]
    out["pending_requests"] = len(requests_for(person, board_id=b["id"], to_decide=True))
    return out


# --- items -------------------------------------------------------------------------------------------

_ITEM_SELECT = ("SELECT i.*, b.key AS board_key, b.name AS board_name, t.name AS type_name, t.color AS type_color, "
                "s.name AS state_name, s.category AS state_category, g.name AS team_name, g.color AS team_color, "
                "co.name AS company_name, d.title AS deal_title, "
                "(SELECT COUNT(*) FROM board_items c WHERE c.parent_id = i.id AND c.archived_at IS NULL) AS children, "
                "(SELECT COUNT(*) FROM board_items c JOIN board_states cs ON cs.id = c.state_id WHERE c.parent_id = i.id "
                "AND c.archived_at IS NULL AND cs.category = 'done') AS children_done, "
                "(SELECT COUNT(*) FROM board_comments m WHERE m.item_id = i.id) AS comments, "
                "(SELECT COUNT(*) FROM board_files f WHERE f.item_id = i.id) AS files "
                "FROM board_items i JOIN boards b ON b.id = i.board_id JOIN board_states s ON s.id = i.state_id "
                "LEFT JOIN board_types t ON t.id = i.type_id LEFT JOIN people_groups g ON g.id = i.team_id "
                "LEFT JOIN crm_companies co ON co.id = i.company_id LEFT JOIN crm_deals d ON d.id = i.deal_id")


def _card(r: dict, names: dict, today: str) -> dict:
    c = dict(r)
    c["key"] = f"{r['board_key']}-{r['number']}"
    c["tags"] = json.loads(r["tags"] or "[]")
    c["assignee_name"] = names.get(r["assignee_id"])
    c["created_by_name"] = names.get(r["created_by"])
    c["done"] = r["state_category"] == "done"
    c["overdue"] = bool(not c["done"] and r["due_date"] and r["due_date"] < today)
    c["due_today"] = bool(not c["done"] and r["due_date"] == today)
    c["archived"] = bool(r["archived_at"])
    c["priority_label"] = PRIORITIES.get(r["priority"], "Normal")
    return c


def items(person: Person, board_id, *, q: str = "", type_id: int | None = None, assignee_id: int | None = None,
          team_id: int | None = None, tag: str = "", include_done: bool = True, done_days: int = 30,
          archived: bool = False, limit: int = 1000) -> list[dict]:
    if str(board_id) == SALES:
        return _sales_items(person, q=q, assignee_id=assignee_id, team_id=team_id, include_done=include_done)
    _require(person, int(board_id), "view")
    where, params = ["i.board_id = ?", "i.archived_at IS " + ("NOT NULL" if archived else "NULL")], [int(board_id)]
    if q:
        like = f"%{q.strip().lower()[:80]}%"
        where.append("(lower(i.title) LIKE ? OR lower(i.description) LIKE ? OR lower(i.tags) LIKE ? OR "
                     "CAST(i.number AS TEXT) = ?)")
        params += [like, like, like, re.sub(r"\D", "", q) or "-1"]
    for col, v in (("i.type_id", type_id), ("i.team_id", team_id)):
        if v:
            where.append(f"{col} = ?")
            params.append(v)
    if assignee_id == -1:
        where.append("i.assignee_id IS NULL")
    elif assignee_id:
        where.append("i.assignee_id = ?")
        params.append(assignee_id)
    if tag:
        where.append("i.tags LIKE ?")
        params.append(f'%"{tag.lower()}"%')
    if not include_done:
        where.append("s.category != 'done'")
    elif done_days:
        where.append("(s.category != 'done' OR i.closed_at IS NULL OR i.closed_at >= ?)")
        params.append((datetime.now() - timedelta(days=done_days)).isoformat())
    rows = db.query(f"{_ITEM_SELECT} WHERE {' AND '.join(where)} ORDER BY s.position, i.position, i.id LIMIT ?",
                    (*params, max(1, min(limit, 2000))))
    names, today = _names(), _today().isoformat()
    return [_card(r, names, today) for r in rows]


def mine(person: Person, *, teams: bool = False, include_deals: bool = True) -> list[dict]:
    """Everything assigned to the person (and, with teams, unassigned items of their teams) across boards."""
    names, today = _names(), _today().isoformat()
    tids = _team_ids(person) if teams else []
    cond = "i.assignee_id = ?" + (f" OR (i.assignee_id IS NULL AND i.team_id IN ({','.join('?' * len(tids))}))"
                                  if tids else "")
    rows = db.query(f"{_ITEM_SELECT} WHERE ({cond}) AND i.archived_at IS NULL AND b.archived_at IS NULL AND "
                    f"s.category != 'done' ORDER BY i.due_date IS NULL, i.due_date, i.priority, i.id", (person.id, *tids))
    out = [_card(r, names, today) for r in rows if can_see_item(person, r)]
    if include_deals and person.can("crm.use") and person.can("boards.use"):
        out += [c for c in _sales_items(person, include_done=False) if c["assignee_id"] == person.id]
    return out


def _item_row(item_id: int) -> dict:
    row = db.query_one(f"{_ITEM_SELECT} WHERE i.id = ?", (item_id,))
    if not row:
        raise BoardError("No such item.")
    return row


def find_item(person: Person, ref) -> dict:
    """An item by id, by its key (WEB-12) or by (part of) its title, among the boards the person can see."""
    s = str(ref).strip()
    m = re.fullmatch(r"([A-Za-z0-9]{2,8})-(\d+)", s)
    if m:
        row = db.query_one(f"{_ITEM_SELECT} WHERE b.key = ? AND i.number = ?", (m.group(1).upper(), int(m.group(2))))
    elif s.isdigit():
        row = db.query_one(f"{_ITEM_SELECT} WHERE i.id = ?", (int(s),))
    else:
        rows = [r for r in db.query(f"{_ITEM_SELECT} WHERE lower(i.title) LIKE ? AND i.archived_at IS NULL "
                                    f"ORDER BY i.id DESC LIMIT 20", (f"%{s.lower()}%",)) if can_see_item(person, r)]
        exact = [r for r in rows if r["title"].lower() == s.lower()]
        if len(rows) > 1 and len(exact) != 1:
            raise BoardError("Several items match: " + "; ".join(f"{r['board_key']}-{r['number']} {r['title']}"
                                                                for r in rows[:6]) + ". Which one?")
        row = (exact or rows or [None])[0]
    if not row or not can_see_item(person, row):
        raise BoardError(f"I found no item matching “{ref}”.")
    return row


def get_item(person: Person, item_id: int) -> dict:
    row = _item_row(item_id)
    b = _require(person, row["board_id"], "view")
    names, today = _names(), _today().isoformat()
    it = _card(row, names, today)
    it["level"] = b["level"]
    it["can_edit"] = RANK[b["level"]] >= RANK["contribute"]
    it["parent"] = None
    if row["parent_id"]:
        p = db.query_one(f"{_ITEM_SELECT} WHERE i.id = ?", (row["parent_id"],))
        it["parent"] = _card(p, names, today) if p else None
    it["children_items"] = [_card(r, names, today) for r in db.query(
        f"{_ITEM_SELECT} WHERE i.parent_id = ? AND i.archived_at IS NULL ORDER BY s.position, i.id", (item_id,))]
    it["comment_list"] = [{**c, "user_name": names.get(c["user_id"]), "mine": c["user_id"] == person.id}
                          for c in db.query("SELECT * FROM board_comments WHERE item_id = ? ORDER BY id", (item_id,))]
    it["file_list"] = [{**f, "added_by_name": names.get(f["added_by"]), "name": f["path"].split("/")[-1],
                        "can_open": person.can("files.company") if f["scope"] == "company" else False}
                       for f in db.query("SELECT * FROM board_files WHERE item_id = ? ORDER BY id", (item_id,))]
    it["history"] = _history(item_id, names)
    return it


_LABELS = {"title": "title", "description": "description", "type_id": "type", "state_id": "state",
           "priority": "priority", "tags": "tags", "start_date": "start", "due_date": "due date", "estimate": "estimate",
           "parent_id": "parent", "assignee_id": "assignee", "team_id": "team", "company_id": "company",
           "deal_id": "deal"}
FIELDS = tuple(_LABELS)


def _history(item_id: int, names: dict) -> list[dict]:
    states = {s["id"]: s["name"] for s in db.query("SELECT id, name FROM board_states")}
    types = {t["id"]: t["name"] for t in db.query("SELECT id, name FROM board_types")}
    teams = {g["id"]: g["name"] for g in db.query("SELECT id, name FROM people_groups")}

    def show(field, v):
        if v in (None, "", []):
            return "none"
        return {"state_id": states, "type_id": types, "team_id": teams, "assignee_id": names}.get(field, {}).get(v, v) \
            if field in ("state_id", "type_id", "team_id", "assignee_id") else \
            PRIORITIES.get(v, v) if field == "priority" else v
    out = []
    for h in db.query("SELECT * FROM board_history WHERE item_id = ? ORDER BY id DESC LIMIT 60", (item_id,)):
        ch = json.loads(h["changes"] or "{}")
        parts = []
        for f, (a, b) in ch.items():
            if f == "description":
                parts.append("changed the description")
            else:
                parts.append(f"{_LABELS.get(f, f)} {show(f, a)} → {show(f, b)}")
        out.append({"id": h["id"], "action": h["action"], "ts": h["ts"], "by": names.get(h["user_id"]),
                    "text": "; ".join(parts)})
    return out


def _clean(person: Person, board_id: int, data: dict, item_id: int | None = None) -> dict:
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise BoardError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    out: dict = {}
    for k, v in data.items():
        if k == "title":
            out[k] = _text(v, 200)
            if not out[k]:
                raise BoardError("An item needs a title.")
        elif k == "description":
            out[k] = str(v or "").replace("\r\n", "\n").strip()[:10000]
        elif k == "priority":
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                raise BoardError("Priority is 1 (urgent), 2 (normal) or 3 (low).")
            if out[k] not in PRIORITIES:
                raise BoardError("Priority is 1 (urgent), 2 (normal) or 3 (low).")
        elif k == "tags":
            items_ = v if isinstance(v, list) else str(v or "").split(",")
            tags = []
            for t in items_:
                t = _text(t, 30).lower()
                if t and t not in tags:
                    tags.append(t)
            out[k] = json.dumps(tags[:15], ensure_ascii=False)
        elif k in ("start_date", "due_date"):
            out[k] = _date(v, "start date" if k == "start_date" else "due date")
        elif k == "estimate":
            if v in (None, ""):
                out[k] = None
            else:
                try:
                    out[k] = round(float(v), 2)
                except (TypeError, ValueError):
                    raise BoardError("The estimate is a number of hours.")
                if not 0 <= out[k] <= 10000:
                    raise BoardError("The estimate is between 0 and 10,000 hours.")
        elif k == "type_id":
            if not db.query_one("SELECT 1 FROM board_types WHERE id = ? AND board_id = ? AND archived = 0", (v, board_id)):
                raise BoardError("Unknown item type on this board.")
            out[k] = int(v)
        elif k == "state_id":
            if not db.query_one("SELECT 1 FROM board_states WHERE id = ? AND board_id = ? AND archived = 0", (v, board_id)):
                raise BoardError("Unknown column on this board.")
            out[k] = int(v)
        elif k == "parent_id":
            if v in (None, "", 0):
                out[k] = None
                continue
            pid = int(v)
            if not db.query_one("SELECT 1 FROM board_items WHERE id = ? AND board_id = ?", (pid, board_id)):
                raise BoardError("The parent must be an item on the same board.")
            cur, seen = pid, set()
            while cur:
                if cur == item_id or cur in seen:
                    raise BoardError("An item can't be its own parent (or grandparent).")
                seen.add(cur)
                cur = (db.query_one("SELECT parent_id FROM board_items WHERE id = ?", (cur,)) or {}).get("parent_id")
            out[k] = pid
        elif k == "assignee_id":
            if v in (None, "", 0):
                out[k] = None
                continue
            from .identity import registry
            who = registry.get_person(int(v))
            if not who or not level_of(who, _board_row(board_id)):
                raise BoardError("That person can't see this board, so they can't be assigned.")
            out[k] = who.id
        elif k == "team_id":
            if v in (None, "", 0):
                out[k] = None
                continue
            if not db.query_one("SELECT 1 FROM people_groups WHERE id = ?", (v,)):
                raise BoardError("Unknown team.")
            out[k] = int(v)
        elif k in ("company_id", "deal_id"):
            if v in (None, "", 0):
                out[k] = None
                continue
            from . import crm
            try:
                out[k] = crm._get_visible(person, "company" if k == "company_id" else "deal", int(v))["id"]
            except crm.CrmError:
                raise BoardError("Unknown client or deal (or you can't see it in the CRM).")
    return out


def _end_position(board_id: int, state_id: int) -> float:
    return db.query_one("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM board_items WHERE board_id = ? AND state_id = ?",
                        (board_id, state_id))["p"]


def _notify_assignment(item: dict, actor: Person) -> None:
    if item.get("assignee_id") and item["assignee_id"] != actor.id:
        due = f", due {item['due_date']}" if item.get("due_date") else ""
        _later(_tell(item["assignee_id"], f"🗂 {actor.display_name} assigned you *{item['key']} {item['title']}* "
                                          f"({item['board_name']}{due}).", "board assignment"))


def save_item(person: Person, data: dict, item_id: int | None = None, board_id=None) -> dict:
    """Create (board_id, no item_id) or change an item. Needs contribute on its board."""
    data = dict(data or {})
    if item_id is None:
        if str(board_id) == SALES:
            raise BoardError("Deals are added in the CRM (or ask me to add a deal).")
        b = _require(person, int(board_id), "contribute")
        clean = _clean(person, b["id"], data)
        if not clean.get("title"):
            raise BoardError("What needs doing? Give the item a title.")
        clean.setdefault("type_id", (_types(b["id"])[0]["id"]))
        clean.setdefault("state_id", next(s["id"] for s in _states(b["id"]) if s["category"] != "done"))
        now = db.now_iso()
        cat = db.query_one("SELECT category FROM board_states WHERE id = ?", (clean["state_id"],))["category"]
        with db.tx() as conn:
            num = conn.execute("SELECT next_number FROM boards WHERE id = ?", (b["id"],)).fetchone()[0]
            conn.execute("UPDATE boards SET next_number = next_number + 1, updated_at = ? WHERE id = ?", (now, b["id"]))
            cols = {**clean, "board_id": b["id"], "number": num, "position": _end_position(b["id"], clean["state_id"]),
                    "created_by": person.id, "created_at": now, "updated_at": now,
                    "closed_at": now if cat == "done" else None}
            iid = conn.execute(f"INSERT INTO board_items({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               list(cols.values())).lastrowid
            conn.execute("INSERT INTO board_history(item_id, user_id, action, changes, ts) VALUES (?,?,?,?,?)",
                         (iid, person.id, "create", "{}", now))
        it = get_item(person, iid)
        db.log_activity("boards", f"{person.display_name} added {it['key']} {it['title']}", user_id=person.id)
        _notify_assignment(it, person)
        return it
    row = _item_row(item_id)
    _require(person, row["board_id"], "contribute")
    clean = _clean(person, row["board_id"], data, item_id)
    changes = {k: v for k, v in clean.items() if row.get(k) != v}
    if not changes:
        return get_item(person, item_id)
    now = db.now_iso()
    extra = {}
    if "state_id" in changes:
        cat = db.query_one("SELECT category FROM board_states WHERE id = ?", (changes["state_id"],))["category"]
        extra["closed_at"] = (row["closed_at"] or now) if cat == "done" else None
        extra["position"] = _end_position(row["board_id"], changes["state_id"])
    if "due_date" in changes or "assignee_id" in changes:
        extra["reminded_on"] = None
    sets = {**changes, **extra}
    db.execute(f"UPDATE board_items SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE id = ?",
               (*sets.values(), now, item_id))
    db.execute("INSERT INTO board_history(item_id, user_id, action, changes, ts) VALUES (?,?,?,?,?)",
               (item_id, person.id, "update", json.dumps({k: [row.get(k), v] for k, v in changes.items()}, default=str), now))
    it = get_item(person, item_id)
    if "assignee_id" in changes:
        _notify_assignment(it, person)
    return it


def move(person: Person, board_id, item_id: int, *, state_id: int | None = None, team_id: int | None | str = "keep",
         index: int | None = None, assignee_id: int | None | str = "keep") -> dict:
    """Drag and drop: to another column (and, in the team lanes, another team), at a place in the column."""
    if str(board_id) == SALES:
        return _sales_move(person, item_id, state_id=state_id, team_id=team_id)
    row = _item_row(item_id)
    if row["board_id"] != int(board_id):
        raise BoardError("That item is on another board.")
    data = {}
    if state_id and state_id != row["state_id"]:
        data["state_id"] = state_id
    if team_id != "keep" and (team_id or None) != row["team_id"]:
        data["team_id"] = team_id or None
    if assignee_id != "keep" and (assignee_id or None) != row["assignee_id"]:
        data["assignee_id"] = assignee_id or None
    it = save_item(person, data, item_id) if data else get_item(person, item_id)
    if index is not None:
        col = [r["id"] for r in db.query("SELECT id FROM board_items WHERE board_id = ? AND state_id = ? AND "
                                         "archived_at IS NULL AND id != ? ORDER BY position, id",
                                         (row["board_id"], it["state_id"], item_id))]
        col.insert(max(0, min(int(index), len(col))), item_id)
        for pos, iid in enumerate(col):
            db.execute("UPDATE board_items SET position = ? WHERE id = ?", (pos, iid))
    return get_item(person, item_id)


def set_archived(person: Person, item_id: int, archived: bool = True) -> dict:
    row = _item_row(item_id)
    b = _require(person, row["board_id"], "contribute")
    if archived and b["level"] != "manage" and row["created_by"] != person.id and row["assignee_id"] != person.id:
        raise BoardError("Only its creator, its assignee or the board's managers can remove an item.")
    db.execute("UPDATE board_items SET archived_at = ?, updated_at = ? WHERE id = ?",
               (db.now_iso() if archived else None, db.now_iso(), item_id))
    db.execute("INSERT INTO board_history(item_id, user_id, action, changes, ts) VALUES (?,?,?,?,?)",
               (item_id, person.id, "archive" if archived else "restore", "{}", db.now_iso()))
    return get_item(person, item_id)


# --- comments and files ---------------------------------------------------------------------------------

def _mentions(text: str, board: dict) -> list[int]:
    found = []
    low = text.lower()
    for p in _people_for(board):
        full = p["display_name"].lower()
        first = full.split()[0] if full else ""
        if f"@{full}" in low or (first and re.search(rf"@{re.escape(first)}\b", low)):
            found.append(p["id"])
    return found


def comment(person: Person, item_id: int, body: str) -> dict:
    row = _item_row(item_id)
    b = _require(person, row["board_id"], "view")
    if RANK[b["level"]] < RANK["request"] and row["assignee_id"] != person.id:
        raise BoardError("You can only read this board.")
    body = str(body or "").replace("\r\n", "\n").strip()[:5000]
    if not body:
        raise BoardError("Write something first.")
    db.execute("INSERT INTO board_comments(item_id, user_id, body, created_at) VALUES (?,?,?,?)",
               (item_id, person.id, body, db.now_iso()))
    db.execute("UPDATE board_items SET updated_at = ? WHERE id = ?", (db.now_iso(), item_id))
    key = f"{row['board_key']}-{row['number']}"
    tell = set(_mentions(body, b))
    if row["assignee_id"]:
        tell.add(row["assignee_id"])
    for uid in tell - {person.id}:
        _later(_tell(uid, f"💬 {person.display_name} on *{key} {row['title']}*: {body[:300]}", "board comment"))
    return get_item(person, item_id)


def delete_comment(person: Person, comment_id: int) -> dict:
    c = db.query_one("SELECT * FROM board_comments WHERE id = ?", (comment_id,))
    if not c:
        raise BoardError("No such comment.")
    row = _item_row(c["item_id"])
    b = _require(person, row["board_id"], "view")
    if c["user_id"] != person.id and b["level"] != "manage":
        raise BoardError("You can only remove your own comments.")
    db.execute("DELETE FROM board_comments WHERE id = ?", (comment_id,))
    return get_item(person, c["item_id"])


def attach_file(person: Person, item_id: int, path: str) -> dict:
    """A file from the company folders (it opens with the viewer's own access, nothing is copied)."""
    from . import nas, nas_files
    row = _item_row(item_id)
    _require(person, row["board_id"], "contribute")
    if not person.can("files.company"):
        raise BoardError("You don't have access to the company files.")
    try:
        rel = nas.clean_path(path).replace("\\", "/")
        nas_files.stat_file(person, "company", rel)
    except nas.NasError as e:
        raise BoardError(str(e))
    except Exception:  # noqa: BLE001 — missing file or the storage server is away
        raise BoardError("I couldn't find that file in the company folders.")
    if not rel:
        raise BoardError("Pick a file.")
    db.execute("INSERT OR IGNORE INTO board_files(item_id, scope, path, added_by, added_at) VALUES (?,?,?,?,?)",
               (item_id, "company", rel, person.id, db.now_iso()))
    return get_item(person, item_id)


def detach_file(person: Person, file_id: int) -> dict:
    f = db.query_one("SELECT * FROM board_files WHERE id = ?", (file_id,))
    if not f:
        raise BoardError("No such attachment.")
    _require(person, _item_row(f["item_id"])["board_id"], "contribute")
    db.execute("DELETE FROM board_files WHERE id = ?", (file_id,))
    return get_item(person, f["item_id"])


# --- requests (for people who may only ask) ---------------------------------------------------------------

def deciders(board_id: int, team_id: int | None) -> list[int]:
    """Who decides a request: the team's manager, else the board's managers (creator, manage level, boards.admin)."""
    if team_id:
        m = db.query_one("SELECT manager_id FROM people_groups WHERE id = ?", (team_id,))
        if m and m["manager_id"]:
            return [m["manager_id"]]
    from .identity import registry
    people = [p for p in (registry.get_person(u["id"]) for u in db.query("SELECT id FROM users WHERE active = 1")) if p]
    out = [p.id for p in people if p.can("boards.use") and _explicit_manage(board_id, p)]
    return out or [p.id for p in people if p.can("boards.admin")]   # nobody manages it: whoever manages every board


def _explicit_manage(board_id: int, p: Person) -> bool:
    b = _board_row(board_id)
    if b["created_by"] == p.id:
        return True
    teams = [str(t) for t in _team_ids(p)]
    for r in db.query("SELECT * FROM board_access WHERE board_id = ? AND level = 'manage'", (board_id,)):
        if r["subject_kind"] == "everyone" or (r["subject_kind"] == "user" and r["subject"] == str(p.id)) or \
                (r["subject_kind"] == "group" and r["subject"] in teams) or \
                (r["subject_kind"] == "role" and r["subject"] in (p.roles or (p.role,))):
            return True
    return False


def request_item(person: Person, board_id: int, data: dict) -> dict:
    b = _require(person, int(board_id), "request")
    title = _text(data.get("title"), 200)
    if not title:
        raise BoardError("What would you like the team to do?")
    team_id = data.get("team_id") or None
    if team_id and not db.query_one("SELECT 1 FROM people_groups WHERE id = ?", (team_id,)):
        raise BoardError("Unknown team.")
    type_id = data.get("type_id") or None
    if type_id and not db.query_one("SELECT 1 FROM board_types WHERE id = ? AND board_id = ?", (type_id, b["id"])):
        raise BoardError("Unknown item type on this board.")
    pr = int(data.get("priority") or 2)
    if pr not in PRIORITIES:
        pr = 2
    rid = db.execute("INSERT INTO board_requests(board_id, team_id, title, description, type_id, priority, due_date, "
                     "requested_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (b["id"], team_id, title, str(data.get("description") or "").strip()[:5000], type_id, pr,
                      _date(data.get("due_date"), "due date"), person.id, db.now_iso()))
    who = deciders(b["id"], team_id)
    team = (db.query_one("SELECT name FROM people_groups WHERE id = ?", (team_id,)) or {}).get("name") if team_id else None
    text = (f"🙋 *Task request* #{rid} on {b['name']}{f' for {team}' if team else ''} from {person.display_name}:\n"
            f"{title}" + (f"\nDue {data.get('due_date')}" if data.get("due_date") else ""))
    for uid in who:
        if uid != person.id:
            _later(_tell(uid, text, "board request",
                         buttons=[[("✓ Accept", f"brq:yes:{rid}"), ("✕ Decline", f"brq:no:{rid}")]]))
    db.log_activity("boards", f"{person.display_name} asked for “{title}” on {b['name']}", user_id=person.id)
    return _request_view(db.query_one("SELECT * FROM board_requests WHERE id = ?", (rid,)), _names(), person)


def _request_view(r: dict, names: dict, person: Person) -> dict:
    out = dict(r)
    out["requested_by_name"] = names.get(r["requested_by"])
    out["decided_by_name"] = names.get(r["decided_by"])
    b = db.query_one("SELECT name, key FROM boards WHERE id = ?", (r["board_id"],))
    out["board_name"], out["board_key"] = (b["name"], b["key"]) if b else ("?", "?")
    out["team_name"] = (db.query_one("SELECT name FROM people_groups WHERE id = ?", (r["team_id"],)) or {}).get("name") \
        if r["team_id"] else None
    out["can_decide"] = r["status"] == "pending" and person.id in deciders(r["board_id"], r["team_id"])
    out["can_withdraw"] = r["status"] == "pending" and r["requested_by"] == person.id
    if r["item_id"]:
        it = db.query_one("SELECT number FROM board_items WHERE id = ?", (r["item_id"],))
        out["item_key"] = f"{out['board_key']}-{it['number']}" if it else None
    return out


def requests_for(person: Person, *, board_id: int | None = None, to_decide: bool = False,
                 status: str = "pending") -> list[dict]:
    where, params = [], []
    if board_id:
        where.append("board_id = ?")
        params.append(board_id)
    if status != "all":
        where.append("status = ?")
        params.append(status)
    rows = db.query("SELECT * FROM board_requests" + (f" WHERE {' AND '.join(where)}" if where else "")
                    + " ORDER BY id DESC LIMIT 200", params)
    names = _names()
    out = []
    for r in rows:
        v = _request_view(r, names, person)
        if v["can_decide"] or (not to_decide and r["requested_by"] == person.id) or \
                (not to_decide and r["decided_by"] == person.id):
            out.append(v)
    return out


async def decide_request(person: Person, request_id: int, accept: bool, note: str = "", **fields) -> dict:
    r = db.query_one("SELECT * FROM board_requests WHERE id = ?", (request_id,))
    if not r:
        raise BoardError("No such request.")
    if person.id not in deciders(r["board_id"], r["team_id"]):
        raise BoardError("This request is for the team's manager (or the board's managers).")
    if not db.execute_count("UPDATE board_requests SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ? "
                            "AND status = 'pending'", ("accepted" if accept else "declined", person.id, db.now_iso(),
                                                       _text(note, 300), request_id)):
        raise BoardError("This request was already decided.")
    item = None
    if accept:
        data = {"title": r["title"], "description": r["description"], "priority": r["priority"],
                "due_date": r["due_date"], "team_id": r["team_id"]}
        if r["type_id"]:
            data["type_id"] = r["type_id"]
        data.update({k: v for k, v in fields.items() if k in ("assignee_id", "state_id", "due_date") and v})
        from .identity import registry
        requester = registry.get_person(r["requested_by"]) if r["requested_by"] else None
        item = save_item(person, data, board_id=r["board_id"])
        db.execute("UPDATE board_requests SET item_id = ? WHERE id = ?", (item["id"], request_id))
        db.execute("UPDATE board_items SET created_by = COALESCE(?, created_by) WHERE id = ?",
                   (requester.id if requester else None, item["id"]))
    if r["requested_by"] and r["requested_by"] != person.id:
        text = (f"✓ {person.display_name} accepted your request “{r['title']}”: it's {item['key']} now." if accept else
                f"{person.display_name} declined your request “{r['title']}”." + (f" Note: {note}" if note else ""))
        _later(_tell(r["requested_by"], text, "board request"))
    return _request_view(db.query_one("SELECT * FROM board_requests WHERE id = ?", (request_id,)), _names(), person)


def withdraw_request(person: Person, request_id: int) -> dict:
    if not db.execute_count("UPDATE board_requests SET status = 'withdrawn', decided_at = ? WHERE id = ? AND "
                            "requested_by = ? AND status = 'pending'", (db.now_iso(), request_id, person.id)):
        raise BoardError("No open request of yours.")
    return _request_view(db.query_one("SELECT * FROM board_requests WHERE id = ?", (request_id,)), _names(), person)


# --- the sales pipeline as a board ----------------------------------------------------------------------

def _sales_states() -> list[dict]:
    from . import crm_deals
    out = []
    first_open = True
    for s in crm_deals.stages():
        cat = "done" if s["kind"] != "open" else ("todo" if first_open else "doing")
        first_open = first_open and s["kind"] != "open"
        out.append({"id": s["id"], "name": s["name"], "category": cat, "position": s["position"], "wip_limit": None,
                    "archived": 0, "items": s.get("deals", 0), "probability": s["probability"], "kind": s["kind"]})
    return out


def _sales_board(person: Person) -> dict:
    if not (person.can("crm.use") and person.can("boards.use")):
        raise BoardError("No such board.")
    from . import permissions
    return {"id": SALES, "kind": "pipeline", "name": "Sales pipeline", "key": "DEAL",
            "description": "The CRM's deals: columns are the pipeline stages (set them up in the CRM).",
            "level": "contribute" if person.can("crm.edit") else "view", "archived": False,
            "types": [{"id": 0, "name": "Deal", "color": "#ffb040", "archived": 0}], "states": _sales_states(),
            "teams": _teams(), "priorities": PRIORITIES,
            "people": [{"id": p["id"], "display_name": p["display_name"]} for p in permissions.people_with("crm.use")],
            "pending_requests": 0}


def _sales_items(person: Person, *, q: str = "", assignee_id: int | None = None, team_id: int | None = None,
                 include_done: bool = True) -> list[dict]:
    from . import crm_deals
    deals = crm_deals.search(person, status="all" if include_done else "open", q=q, limit=1000)["items"]
    today = _today().isoformat()
    since = (_today() - timedelta(days=30)).isoformat()
    out = []
    for d in deals:
        if not d["open"] and (d["closed_at"] or "")[:10] < since:
            continue
        if assignee_id == -1 and d["owner_id"] or assignee_id and assignee_id != -1 and d["owner_id"] != assignee_id:
            continue
        if team_id and d["group_id"] != team_id:
            continue
        out.append({"id": d["id"], "kind": "deal", "board_id": SALES, "key": f"DEAL-{d['id']}", "number": d["id"],
                    "title": d["title"], "description": d["notes"] or "", "type_id": 0, "type_name": "Deal",
                    "type_color": "#ffb040", "state_id": d["stage_id"], "state_name": d["stage_name"],
                    "state_category": "done" if not d["open"] else "doing", "priority": 2, "priority_label": "Normal",
                    "tags": [], "due_date": d["expected_close"], "assignee_id": d["owner_id"],
                    "assignee_name": d["owner_name"], "team_id": d["group_id"], "company_id": d["company_id"],
                    "company_name": d["company_name"], "deal_id": d["id"], "value": d["value"], "done": not d["open"],
                    "overdue": bool(d["overdue_close"]), "due_today": bool(d["open"] and d["expected_close"] == today),
                    "next_step": d["next_step"], "children": 0, "children_done": 0, "comments": 0, "files": 0,
                    "board_key": "DEAL", "board_name": "Sales pipeline", "position": d["id"], "archived": False})
    return out


def _sales_move(person: Person, deal_id: int, *, state_id: int | None, team_id) -> dict:
    from . import crm, crm_deals
    data = {}
    if state_id:
        data["stage_id"] = state_id
    if team_id != "keep":
        data["group_id"] = team_id or None
    try:
        if data:
            crm_deals.save(person, data, deal_id)
    except crm.CrmError as e:
        raise BoardError(str(e))
    return next((c for c in _sales_items(person) if c["id"] == deal_id), {"id": deal_id})


# --- Alfred's day: agenda, briefing, morning reminder ------------------------------------------------------

def agenda_items(person: Person, start: str, end: str) -> list[dict]:
    """Due dates of the person's open items, for the agenda (Alfred's calendar)."""
    rows = db.query(f"{_ITEM_SELECT} WHERE i.assignee_id = ? AND i.archived_at IS NULL AND b.archived_at IS NULL AND "
                    f"s.category != 'done' AND i.due_date BETWEEN ? AND ?", (person.id, start[:10], end[:10]))
    return [{"key": f"work:{r['id']}", "kind": "work", "id": r["id"], "title": f"{r['board_key']}-{r['number']} {r['title']}",
             "start": r["due_date"], "all_day": True, "category": "work", "board": r["board_name"], "board_id": r["board_id"],
             "editable": False}
            for r in rows if can_see_item(person, r)]


def due_for(person: Person, day: date | None = None) -> list[dict]:
    day = (day or _today()).isoformat()
    return [c for c in mine(person, include_deals=False) if c["due_date"] and c["due_date"] <= day]


def briefing_block(person: Person) -> dict | None:
    if not person.can("boards.use"):
        return None
    due = due_for(person)
    open_ = mine(person, include_deals=False)
    if not open_:
        return None
    out = {"open_items_assigned": len(open_)}
    if due:
        out["due_today_or_overdue"] = [f"{c['key']} {c['title']}" + (" (overdue)" if c["overdue"] else "") for c in due[:6]]
    pending = len(requests_for(person, to_decide=True))
    if pending:
        out["task_requests_to_decide"] = pending
    return out


async def send_due_reminders(day: date | None = None) -> int:
    """Morning: each person's items due today or overdue, unless their briefing carries them (once a day per item)."""
    from . import crm_proactive
    from .identity import registry
    day = day or _today()
    n = 0
    for u in db.query("SELECT DISTINCT i.assignee_id AS id FROM board_items i JOIN board_states s ON s.id = i.state_id "
                      "WHERE i.assignee_id IS NOT NULL AND i.archived_at IS NULL AND s.category != 'done' AND "
                      "i.due_date <= ? AND (i.reminded_on IS NULL OR i.reminded_on != ?)", (day.isoformat(), day.isoformat())):
        person = registry.get_person(u["id"])
        if not person:
            continue
        due = [c for c in due_for(person, day) if c.get("kind") != "deal" and c.get("reminded_on") != day.isoformat()]
        if not due:
            continue
        if not crm_proactive._gets_briefing_today(person.id, day):
            lines = [f"🗂 *Work due* ({len(due)})"] + [
                f"• {c['key']} {c['title']} ({'overdue since ' + c['due_date'] if c['overdue'] else 'today'})"
                for c in due[:10]]
            await _tell(person.id, "\n".join(lines), "board items due")
            n += 1
        db.execute(f"UPDATE board_items SET reminded_on = ? WHERE id IN ({','.join('?' * len(due))})",
                   (day.isoformat(), *[c["id"] for c in due]))
    return n


def linked_items(person: Person, *, company_id: int | None = None, deal_id: int | None = None,
                 user_id: int | None = None) -> list[dict]:
    """Open items on a CRM company or deal, or assigned to someone (Team page)."""
    cond, params = [], []
    for col, v in (("i.company_id", company_id), ("i.deal_id", deal_id), ("i.assignee_id", user_id)):
        if v:
            cond.append(f"{col} = ?")
            params.append(v)
    if not cond or not person.can("boards.use"):
        return []
    rows = db.query(f"{_ITEM_SELECT} WHERE {' AND '.join(cond)} AND i.archived_at IS NULL AND b.archived_at IS NULL "
                    f"ORDER BY s.category = 'done', i.due_date IS NULL, i.due_date LIMIT 50", params)
    names, today = _names(), _today().isoformat()
    return [_card(r, names, today) for r in rows if can_see_item(person, r)]


def boards_summary(person: Person) -> list[dict]:
    """Short list for Alfred: boards with counts."""
    return [{k: b[k] for k in ("id", "name", "key", "level", "open", "mine")} for b in boards_for(person)]
