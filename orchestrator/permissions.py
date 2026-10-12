"""Roles and permissions (roadmap #57): the one place that decides who may do what.

Every ability is a named permission in CATALOGUE. A role is an editable set of permissions: each
one either allowed or denied (or not set). A person can hold several roles, directly or through
their groups; a role can inherit from a parent role. A person's permissions are everything their
roles (and the roles' ancestors) allow, minus everything any of them deny: deny wins.

The admin role is fixed: it has every permission and can't be edited, inherited or given to a
group, so "who is an admin" is always the people holding it directly (last-admin protection).

Editing is capped: nobody can change a permission they don't hold themselves, give a role they
couldn't build themselves, or touch the admin role or admins unless they are an admin.

Tools declare the permission they need in tools/registry.yaml (`permission:`; none = everyone),
dashboard routes use `routes.requires(...)`, and the dashboard tabs read `/api/me`'s list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import db

ADMIN = "admin"

# key, group, label, held by default by (besides admin): c = cofounder, f = friend
_CATALOGUE = [
    ("channel.telegram", "Channels", "Write to Alfred on Telegram", "cf"),
    ("channel.call", "Channels", "Call Alfred and get calls from him", "cf"),
    ("channel.email", "Channels", "Write to Alfred by email", "cf"),
    ("channel.dashboard", "Channels", "Sign in to the dashboard and the web chat", "c"),

    ("tasks.use", "Everyday", "Own tasks (add, list, complete)", "cf"),
    ("reminders.use", "Everyday", "Own reminders and the agenda", "cf"),
    ("calendar.use", "Everyday", "Own Google Calendar", "cf"),
    ("email.use", "Everyday", "Email through Alfred (own mail, drafts to send)", "cf"),
    ("files.own", "Everyday", "Own notes and files on the storage server", "cf"),
    ("meetings.answer", "Everyday", "Answer meeting invitations", "cf"),
    ("conversations.search", "Everyday", "Search own past conversations", "cf"),
    ("web.search", "Everyday", "Web search", "cf"),

    ("people.view", "Team", "See the people Alfred knows, live sessions and recent activity", "c"),
    ("others.contact", "Team", "Have Alfred call or message other people", "c"),
    ("others.assign", "Team", "Reminders and tasks for other people", "c"),
    ("meetings.organize", "Team", "Organise meetings with other people", "c"),
    ("email.others", "Team", "Have Alfred email other people", "c"),
    ("calls.outside", "Team", "Calls to businesses and numbers outside the registry", "c"),
    ("screening.relay", "Team", "Get messages from unknown callers and answer them", "c"),
    ("files.company", "Team", "Company files and the company to-do list", "c"),
    ("mail.tab", "Team", "Mail tab: Alfred's mailbox and company mail", "c"),
    ("mail.templates", "Team", "Edit email layouts and texts (Template manager)", "c"),
    ("escalations.handle", "Team", "Receive and close matters Alfred passes to the team", "c"),
    ("market.analysis", "Team", "Market analysis", "c"),

    ("automations.view", "Automations", "See n8n workflows and their runs", "c"),
    ("automations.run", "Automations", "Run n8n workflows", "c"),
    ("automations.edit", "Automations", "Create, edit and switch n8n workflows on/off", "c"),
    ("automations.delete", "Automations", "Delete n8n workflows", ""),

    ("everyone.view", "Administration", "See everyone's conversations, calls, reminders, tasks and the access log", ""),
    ("files.people", "Administration", "See everyone's personal folders", ""),
    ("homelab.manage", "Administration", "Home servers: status, logs, updates, restarts", ""),
    ("people.manage", "Administration", "Personnel: add and edit people, their connections and roles", ""),
    ("roles.manage", "Administration", "Roles and groups", "c"),
    ("settings.manage", "Administration", "Settings: AI, quiet hours, call screening, budgets", ""),
    ("usage.view", "Administration", "API usage and costs", ""),
]

CATALOGUE = [{"key": k, "group": g, "label": lbl} for k, g, lbl, _ in _CATALOGUE]
KEYS = frozenset(k for k, *_ in _CATALOGUE)
LABELS = {k: lbl for k, _, lbl, _ in _CATALOGUE}
_DEFAULTS = {"cofounder": {k for k, *_, d in _CATALOGUE if "c" in d},
             "friend": {k for k, *_, d in _CATALOGUE if "f" in d}}
CHANNELS = {"telegram": "channel.telegram", "call": "channel.call", "email": "channel.email",
            "dashboard": "channel.dashboard", "web": "channel.dashboard"}   # the web chat lives in the dashboard
_OLD_N8N = {"n8n_view": "automations.view", "n8n_run": "automations.run", "n8n_edit": "automations.edit"}
# Built-in roles: display names, and descriptions replacing the pre-#57 defaults (kept if someone changed them).
_BUILTIN = {
    "admin": ("Admin", "Full access to every tool and channel.", "Fixed: every permission."),
    "cofounder": ("Cofounder", "Just under admin: all tools except n8n workflow management; own dashboard.",
                  "Just under admin: the team's tools, company files and mail, automations, roles."),
    "friend": ("Friend", "Chat and call; only tools explicitly granted to 'friend'.",
               "Chat, call and email Alfred for their own things."),
}


class PermissionError_(ValueError):
    """Message is safe to show."""


# --- resolution ----------------------------------------------------------------------------

def _parents() -> dict[str, str | None]:
    return {r["name"]: r["parent"] for r in db.query("SELECT name, parent FROM roles WHERE archived = 0")}


def expand(roles) -> list[str]:
    """The roles plus every ancestor (archived roles count for nothing), cycle-safe."""
    parents = _parents()
    out: list[str] = []
    for r in roles:
        seen = 0
        while r and r in parents and r not in out and seen < 50:
            out.append(r)
            r, seen = parents[r], seen + 1
    return out


def resolve(roles) -> frozenset[str]:
    """Permissions for a set of roles: allows add up, any deny wins; admin has everything."""
    names = expand(roles)
    if ADMIN in names:
        return KEYS
    if not names:
        return frozenset()
    rows = db.query(f"SELECT permission, effect FROM role_permissions WHERE role IN ({','.join('?' * len(names))})",
                    names)
    allow = {r["permission"] for r in rows if r["effect"] == "allow"}
    deny = {r["permission"] for r in rows if r["effect"] == "deny"}
    return frozenset((allow - deny) & KEYS)


def roles_of(user_id: int) -> list[str]:
    """Roles held directly, in the order they were given (the first one is the primary role)."""
    return [r["role"] for r in db.query("SELECT role FROM user_roles WHERE user_id = ? ORDER BY rowid", (user_id,))]


def group_roles_of(user_id: int) -> list[str]:
    return [r["role"] for r in db.query(
        "SELECT DISTINCT gr.role FROM group_members m JOIN group_roles gr ON gr.group_id = m.group_id "
        "WHERE m.user_id = ? ORDER BY gr.role", (user_id,))]


def all_roles_of(user_id: int) -> list[str]:
    return list(dict.fromkeys(roles_of(user_id) + group_roles_of(user_id)))


def for_person(person) -> frozenset[str]:
    if person is None:
        return frozenset()
    if getattr(person, "perms", None) is not None:      # "view as": a fixed, already-capped set
        return person.perms
    roles = all_roles_of(person.id) or [person.role]
    return resolve(roles)


def can(person, permission: str) -> bool:
    return permission in for_person(person)


def can_use_channel(person, channel: str) -> bool:
    return can(person, CHANNELS[channel])


def people_with(permission: str) -> list[dict]:
    """Active people holding a permission: [{id, display_name, role}] (who gets escalations, relays...)."""
    out = []
    for u in db.query("SELECT id, display_name, role FROM users WHERE active = 1 ORDER BY id"):
        if permission in resolve(all_roles_of(u["id"]) or [u["role"]]):
            out.append(u)
    return out


def is_admin_user(user_id: int) -> bool:
    return ADMIN in roles_of(user_id)


# --- reading for the dashboard -------------------------------------------------------------

def role_rows(include_archived: bool = False) -> list[dict]:
    where = "" if include_archived else " WHERE archived = 0"
    rows = db.query(f"SELECT name, label, description, parent, archived FROM roles{where}")
    perms: dict[str, dict[str, str]] = {}
    for p in db.query("SELECT role, permission, effect FROM role_permissions"):
        perms.setdefault(p["role"], {})[p["permission"]] = p["effect"]
    counts = {r["role"]: r["n"] for r in db.query(
        "SELECT ur.role, COUNT(*) AS n FROM user_roles ur JOIN users u ON u.id = ur.user_id AND u.active = 1 "
        "GROUP BY ur.role")}
    order = {ADMIN: 0, "cofounder": 1, "friend": 9}
    for r in rows:
        r["label"] = r["label"] or r["name"]
        r["fixed"] = r["name"] == ADMIN
        r["rules"] = {k: v for k, v in perms.get(r["name"], {}).items() if k in KEYS}
        r["effective"] = sorted(resolve([r["name"]])) if not r["archived"] else []
        r["people"] = counts.get(r["name"], 0)
    return sorted(rows, key=lambda r: (r["archived"], order.get(r["name"], 5), r["label"].lower()))


def label_of(role: str) -> str:
    row = db.query_one("SELECT label FROM roles WHERE name = ?", (role,))
    return (row and row["label"]) or role


def explain_person(user_id: int) -> dict:
    """What can this person do, and through which role."""
    direct = roles_of(user_id)
    via_groups = [r for r in group_roles_of(user_id) if r not in direct]
    roles = direct + via_groups
    names = expand(roles)
    admin = ADMIN in names
    rules: dict[str, list[tuple[str, str]]] = {}
    if names and not admin:
        for p in db.query(f"SELECT role, permission, effect FROM role_permissions WHERE role IN "
                          f"({','.join('?' * len(names))})", names):
            rules.setdefault(p["permission"], []).append((p["role"], p["effect"]))
    out = []
    for item in CATALOGUE:
        k = item["key"]
        if admin:
            out.append({**item, "allowed": True, "because": ["admin"]})
            continue
        got = rules.get(k, [])
        denied = [r for r, e in got if e == "deny"]
        allowed = [r for r, e in got if e == "allow"]
        out.append({**item, "allowed": bool(allowed) and not denied, "because": denied or allowed,
                    "denied": bool(denied)})
    return {"roles": direct, "group_roles": via_groups, "inherited": [r for r in names if r not in roles],
            "permissions": out}


def explain_permission(permission: str) -> list[dict]:
    """Who can do X (active people)."""
    if permission not in KEYS:
        raise PermissionError_("Unknown permission.")
    return people_with(permission)


# --- editing (capped) ----------------------------------------------------------------------

@dataclass(frozen=True)
class Actor:
    id: int
    name: str
    perms: frozenset
    admin: bool


def actor_for(person) -> Actor:
    perms = for_person(person)
    return Actor(person.id, person.display_name, perms, perms == KEYS and is_admin_user(person.id))


def _need(actor: Actor, permission: str) -> None:
    if permission not in actor.perms:
        raise PermissionError_("You don't have permission to do that.")


def _role(name: str) -> dict:
    row = db.query_one("SELECT name, label, parent, archived FROM roles WHERE name = ?", (name,))
    if not row:
        raise PermissionError_("Unknown role.")
    return row


def _check_role_cap(actor: Actor, role: str) -> None:
    """A role can only be given (to a person, a group or as a parent) by someone who holds all it grants."""
    if role == ADMIN and not actor.admin:
        raise PermissionError_("Only an admin can give the admin role.")
    missing = resolve([role]) - actor.perms
    if missing:
        raise PermissionError_(f"The role '{label_of(role)}' grants things you don't have yourself "
                               f"({', '.join(LABELS[m] for m in sorted(missing)[:3])}).")


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30] or "role"
    base, n = s, 2
    while db.query_one("SELECT 1 FROM roles WHERE name = ?", (s,)):
        s, n = f"{base}-{n}", n + 1
    return s


def _clean_label(label: str) -> str:
    label = " ".join(str(label or "").split())[:40]
    if not label:
        raise PermissionError_("Give the role a name.")
    return label


def create_role(actor: Actor, label: str, *, description: str = "", copy_from: str | None = None,
                parent: str | None = None) -> str:
    _need(actor, "roles.manage")
    label = _clean_label(label)
    if db.query_one("SELECT 1 FROM roles WHERE lower(label) = lower(?) OR name = ?", (label, label.lower())):
        raise PermissionError_("A role with that name already exists.")
    rules: dict[str, str] = {}
    if copy_from:
        _role(copy_from)
        if copy_from == ADMIN:
            raise PermissionError_("The admin role can't be copied; start from another role.")
        rules = {r["permission"]: r["effect"] for r in
                 db.query("SELECT permission, effect FROM role_permissions WHERE role = ?", (copy_from,))}
        for k, eff in rules.items():
            if eff == "allow" and k not in actor.perms:
                raise PermissionError_(f"That role allows something you don't have yourself ({LABELS.get(k, k)}).")
    if parent:
        _check_parent(actor, None, parent)
    name = _slug(label)
    with db.tx() as conn:
        conn.execute("INSERT INTO roles(name, label, description, parent, can_telegram, can_call, can_dashboard) "
                     "VALUES (?,?,?,?,0,0,0)", (name, label, (description or "")[:200], parent))
        conn.executemany("INSERT INTO role_permissions(role, permission, effect) VALUES (?,?,?)",
                         [(name, k, e) for k, e in rules.items() if k in KEYS])
    db.log_activity("access", f"{actor.name} created the role '{label}'" + (f" (copy of {label_of(copy_from)})"
                                                                            if copy_from else ""), user_id=actor.id)
    return name


def _check_parent(actor: Actor, role: str | None, parent: str) -> None:
    p = _role(parent)
    if p["archived"]:
        raise PermissionError_("That parent role is archived.")
    if parent == ADMIN:
        raise PermissionError_("A role can't inherit from admin; give people the admin role directly.")
    if role and role in expand([parent]):
        raise PermissionError_("That would make the role inherit from itself.")
    _check_role_cap(actor, parent)


def update_role(actor: Actor, role: str, *, label: str | None = None, description: str | None = None,
                parent: str | None = "", archived: bool | None = None) -> list[int]:
    """parent: "" = leave as is, None = no parent, name = inherit from that role.
    Returns the people whose access may have changed."""
    _need(actor, "roles.manage")
    row = _role(role)
    if role == ADMIN:
        raise PermissionError_("The admin role is fixed: it always has everything.")
    if not actor.admin and resolve([role]) - actor.perms:
        raise PermissionError_("This role has permissions you don't have yourself; ask an admin.")
    sets, params, what = [], [], []
    if label is not None:
        label = _clean_label(label)
        if db.query_one("SELECT 1 FROM roles WHERE lower(label) = lower(?) AND name != ?", (label, role)):
            raise PermissionError_("A role with that name already exists.")
        sets.append("label = ?"), params.append(label), what.append(f"renamed to '{label}'")
    if description is not None:
        sets.append("description = ?"), params.append(description[:200]), what.append("description")
    if parent != "":
        if parent:
            _check_parent(actor, role, parent)
        sets.append("parent = ?"), params.append(parent or None)
        what.append(f"inherits from {label_of(parent)}" if parent else "no parent")
    if archived is not None:
        if archived and db.query_one("SELECT 1 FROM user_roles ur JOIN users u ON u.id = ur.user_id "
                                     "WHERE ur.role = ? AND u.active = 1", (role,)):
            raise PermissionError_("People still have this role. Give them another role first.")
        if archived and db.query_one("SELECT 1 FROM roles WHERE parent = ? AND archived = 0", (role,)):
            raise PermissionError_("Other roles inherit from this one. Change them first.")
        sets.append("archived = ?"), params.append(int(archived)), what.append("archived" if archived else "restored")
    if not sets:
        return []
    before = _affected_users([role])
    snap = db.query_one("SELECT label, description, parent, archived FROM roles WHERE name = ?", (role,))
    db.execute(f"UPDATE roles SET {', '.join(sets)} WHERE name = ?", (*params, role))
    try:
        _guard_actor(actor)
    except PermissionError_:
        db.execute("UPDATE roles SET label = ?, description = ?, parent = ?, archived = ? WHERE name = ?",
                   (snap["label"], snap["description"], snap["parent"], snap["archived"], role))
        raise
    db.log_activity("access", f"{actor.name} changed the role '{row['label'] or role}': {', '.join(what)}",
                    user_id=actor.id)
    return before


def set_rule(actor: Actor, role: str, permission: str, effect: str | None) -> None:
    """effect: 'allow', 'deny' or None (not set)."""
    _need(actor, "roles.manage")
    _role(role)
    if role == ADMIN:
        raise PermissionError_("The admin role is fixed: it always has everything.")
    if permission not in KEYS:
        raise PermissionError_("Unknown permission.")
    if effect not in ("allow", "deny", None):
        raise PermissionError_("Choose allow, deny or not set.")
    if not actor.admin and permission not in actor.perms:
        raise PermissionError_("You can only change permissions you have yourself.")
    old = db.query_one("SELECT effect FROM role_permissions WHERE role = ? AND permission = ?", (role, permission))
    if (old and old["effect"]) == effect:
        return
    if effect is None:
        db.execute("DELETE FROM role_permissions WHERE role = ? AND permission = ?", (role, permission))
    else:
        db.execute("INSERT INTO role_permissions(role, permission, effect) VALUES (?,?,?) "
                   "ON CONFLICT(role, permission) DO UPDATE SET effect = excluded.effect", (role, permission, effect))
    _guard_actor(actor, undo=(role, permission, old and old["effect"]))
    word = {"allow": "allowed", "deny": "denied", None: "not set"}[effect]
    db.log_activity("access", f"{actor.name} set '{LABELS[permission]}' to {word} for the role '{label_of(role)}'",
                    user_id=actor.id)


def _guard_actor(actor: Actor, undo: tuple | None = None) -> None:
    """Nobody locks themselves out of the role editor by accident."""
    if actor.admin or "roles.manage" in resolve(all_roles_of(actor.id)):
        return
    if undo:
        role, perm, old = undo
        if old is None:
            db.execute("DELETE FROM role_permissions WHERE role = ? AND permission = ?", (role, perm))
        else:
            db.execute("INSERT OR REPLACE INTO role_permissions(role, permission, effect) VALUES (?,?,?)",
                       (role, perm, old))
    raise PermissionError_("That would take away your own access to roles. Ask an admin to make this change.")


def _affected_users(roles: list[str]) -> list[int]:
    """People whose access depends on these roles (directly, through groups or inheritance)."""
    out = []
    for u in db.query("SELECT id FROM users WHERE active = 1"):
        if set(expand(all_roles_of(u["id"]))) & set(roles):
            out.append(u["id"])
    return out


def set_person_roles(actor: Actor, user_id: int, roles: list[str]) -> bool:
    """Replace a person's directly held roles (first = primary). Returns True if anything changed."""
    _need(actor, "people.manage")
    user = db.query_one("SELECT id, display_name, active FROM users WHERE id = ?", (user_id,))
    if not user:
        raise PermissionError_("Unknown person.")
    roles = list(dict.fromkeys(r for r in roles if r))
    if not roles:
        raise PermissionError_("Give the person at least one role.")
    for r in roles:
        if _role(r)["archived"]:
            raise PermissionError_(f"The role '{label_of(r)}' is archived.")
    old = roles_of(user_id)
    if old == roles:
        return False
    if not actor.admin:
        if ADMIN in old:
            raise PermissionError_("Only an admin can change an admin's roles.")
        for r in set(roles) - set(old):
            _check_role_cap(actor, r)
    if ADMIN in old and ADMIN not in roles and user["active"] and admin_count(excluding=user_id) == 0:
        raise PermissionError_("This is the last active admin. Make someone else admin first.")
    primary = ADMIN if ADMIN in roles else roles[0]
    ordered = [primary] + [r for r in roles if r != primary]
    with db.tx() as conn:
        conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        conn.executemany("INSERT INTO user_roles(user_id, role) VALUES (?,?)", [(user_id, r) for r in ordered])
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (primary, user_id))
    db.log_activity("access", f"{actor.name} set {user['display_name']}'s roles to "
                              f"{', '.join(label_of(r) for r in ordered)}", user_id=user_id)
    return True


def admin_count(excluding: int | None = None) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM user_roles ur JOIN users u ON u.id = ur.user_id "
                       "WHERE ur.role = 'admin' AND u.active = 1 AND (? IS NULL OR u.id != ?)", (excluding, excluding))
    return row["n"] if row else 0


# --- groups ----------------------------------------------------------------------------------

def groups() -> list[dict]:
    rows = db.query("SELECT id, name, description FROM people_groups ORDER BY lower(name)")
    members = db.query("SELECT m.group_id, u.id, u.display_name FROM group_members m JOIN users u ON u.id = m.user_id "
                       "ORDER BY u.display_name")
    roles = db.query("SELECT group_id, role FROM group_roles ORDER BY role")
    for g in rows:
        g["members"] = [{"id": m["id"], "display_name": m["display_name"]} for m in members if m["group_id"] == g["id"]]
        g["roles"] = [r["role"] for r in roles if r["group_id"] == g["id"]]
    return rows


def groups_of(user_id: int) -> list[dict]:
    return db.query("SELECT g.id, g.name FROM group_members m JOIN people_groups g ON g.id = m.group_id "
                    "WHERE m.user_id = ? ORDER BY lower(g.name)", (user_id,))


def _group(group_id: int) -> dict:
    row = db.query_one("SELECT id, name FROM people_groups WHERE id = ?", (group_id,))
    if not row:
        raise PermissionError_("Unknown group.")
    return row


def _clean_group_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise PermissionError_("Give the group a name.")
    return name


def create_group(actor: Actor, name: str, description: str = "") -> int:
    _need(actor, "roles.manage")
    name = _clean_group_name(name)
    if db.query_one("SELECT 1 FROM people_groups WHERE lower(name) = lower(?)", (name,)):
        raise PermissionError_("A group with that name already exists.")
    gid = db.execute("INSERT INTO people_groups(name, description, created_at) VALUES (?,?,?)",
                     (name, (description or "")[:200], db.now_iso()))
    db.log_activity("access", f"{actor.name} created the group '{name}'", user_id=actor.id)
    return gid


def update_group(actor: Actor, group_id: int, *, name: str | None = None, description: str | None = None) -> None:
    _need(actor, "roles.manage")
    g = _group(group_id)
    if name is not None:
        name = _clean_group_name(name)
        if db.query_one("SELECT 1 FROM people_groups WHERE lower(name) = lower(?) AND id != ?", (name, group_id)):
            raise PermissionError_("A group with that name already exists.")
        db.execute("UPDATE people_groups SET name = ? WHERE id = ?", (name, group_id))
    if description is not None:
        db.execute("UPDATE people_groups SET description = ? WHERE id = ?", (description[:200], group_id))
    db.log_activity("access", f"{actor.name} changed the group '{g['name']}'", user_id=actor.id)


def delete_group(actor: Actor, group_id: int) -> list[int]:
    """Returns the members (their access may have changed)."""
    _need(actor, "roles.manage")
    g = _group(group_id)
    members = [m["user_id"] for m in db.query("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,))]
    if not actor.admin and any(is_admin_user(m) for m in members) and _group_has_roles(group_id):
        raise PermissionError_("Only an admin can change a group with admins in it.")
    db.execute("DELETE FROM people_groups WHERE id = ?", (group_id,))
    db.log_activity("access", f"{actor.name} deleted the group '{g['name']}'", user_id=actor.id)
    return members


def _group_has_roles(group_id: int) -> bool:
    return bool(db.query_one("SELECT 1 FROM group_roles WHERE group_id = ?", (group_id,)))


def set_group_members(actor: Actor, group_id: int, user_ids: list[int]) -> list[int]:
    """Replace the members. Returns the people added or removed."""
    _need(actor, "roles.manage")
    g = _group(group_id)
    old = {m["user_id"] for m in db.query("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,))}
    new = set(user_ids)
    unknown = new - {u["id"] for u in db.query("SELECT id FROM users")}
    if unknown:
        raise PermissionError_("Unknown person.")
    changed = old ^ new
    if not changed:
        return []
    if not actor.admin and _group_has_roles(group_id):
        for r in db.query("SELECT role FROM group_roles WHERE group_id = ?", (group_id,)):
            if new - old:
                _check_role_cap(actor, r["role"])
        if any(is_admin_user(u) for u in changed):
            raise PermissionError_("Only an admin can change an admin's groups.")
    with db.tx() as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT INTO group_members(group_id, user_id) VALUES (?,?)", [(group_id, u) for u in new])
    names = {u["id"]: u["display_name"] for u in db.query("SELECT id, display_name FROM users")}
    added = [names[u] for u in new - old]
    removed = [names[u] for u in old - new]
    db.log_activity("access", f"{actor.name} changed the group '{g['name']}'"
                    + (f": added {', '.join(added)}" if added else "")
                    + (f"{';' if added else ':'} removed {', '.join(removed)}" if removed else ""), user_id=actor.id)
    return sorted(changed)


def set_group_roles(actor: Actor, group_id: int, roles: list[str]) -> list[int]:
    """Roles every member gets. Returns the members (their access changed)."""
    _need(actor, "roles.manage")
    g = _group(group_id)
    roles = list(dict.fromkeys(r for r in roles if r))
    old = {r["role"] for r in db.query("SELECT role FROM group_roles WHERE group_id = ?", (group_id,))}
    if set(roles) == old:
        return []
    for r in roles:
        if r == ADMIN:
            raise PermissionError_("The admin role can only be given to people directly.")
        if _role(r)["archived"]:
            raise PermissionError_(f"The role '{label_of(r)}' is archived.")
    members = [m["user_id"] for m in db.query("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,))]
    if not actor.admin:
        for r in set(roles) - old:
            _check_role_cap(actor, r)
        if any(is_admin_user(m) for m in members):
            raise PermissionError_("Only an admin can change a group with admins in it.")
    with db.tx() as conn:
        conn.execute("DELETE FROM group_roles WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT INTO group_roles(group_id, role) VALUES (?,?)", [(group_id, r) for r in roles])
    db.log_activity("access", f"{actor.name} set the roles of the group '{g['name']}' to "
                              f"{', '.join(label_of(r) for r in roles) or 'none'}", user_id=actor.id)
    return members


# --- migration from fixed roles (run once by db.init_db) ----------------------------------

def migrate(conn) -> None:
    """Today's roles become editable roles with exactly today's permissions; users get user_roles rows.
    Permissions added to the catalogue later go to the built-in roles that hold them by default."""
    import json
    done = conn.execute("SELECT value FROM app_settings WHERE key = 'permissions_migrated'").fetchone()
    known = conn.execute("SELECT value FROM app_settings WHERE key = 'permissions_known'").fetchone()
    if done and known:
        new = KEYS - set(json.loads(known[0]))
        for role, keys in _DEFAULTS.items():
            conn.executemany("INSERT OR IGNORE INTO role_permissions(role, permission, effect) "
                             "SELECT name, ?, 'allow' FROM roles WHERE name = ?", [(k, role) for k in sorted(new & keys)])
    conn.execute("INSERT INTO app_settings(key, value, updated_at) VALUES ('permissions_known', ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (json.dumps(sorted(KEYS)), db.now_iso()))
    for name, (label, old_desc, desc) in _BUILTIN.items():
        conn.execute("UPDATE roles SET label = ? WHERE name = ? AND label IS NULL", (label, name))
        conn.execute("UPDATE roles SET description = ? WHERE name = ? AND description = ?", (desc, name, old_desc))
    if not done:
        old = conn.execute("SELECT value FROM app_settings WHERE key = 'role_permissions'").fetchone()
        switches = json.loads(old[0]) if old else {}
        for r in conn.execute("SELECT name, can_telegram, can_call, can_dashboard, can_email FROM roles").fetchall():
            name = r[0]
            if name == ADMIN:
                continue
            grant = set(_DEFAULTS.get(name, _DEFAULTS["friend"])) - set(CHANNELS.values())
            grant |= {p for p, col in zip(("channel.telegram", "channel.call", "channel.dashboard", "channel.email"),
                                          r[1:]) if col}
            for oldkey, newkey in _OLD_N8N.items():
                saved = (switches.get(name) or {}) if isinstance(switches.get(name), dict) else {}
                if name == "cofounder" and saved.get(oldkey) is False:
                    grant.discard(newkey)
            conn.executemany("INSERT OR IGNORE INTO role_permissions(role, permission, effect) VALUES (?,?,'allow')",
                             [(name, k) for k in sorted(grant)])
        conn.execute("INSERT INTO app_settings(key, value, updated_at) VALUES ('permissions_migrated', '1', ?)",
                     (db.now_iso(),))
    if "role" in {r[1] for r in conn.execute("PRAGMA table_info(users)")}:
        conn.execute("INSERT OR IGNORE INTO user_roles(user_id, role) SELECT id, role FROM users "
                     "WHERE id NOT IN (SELECT user_id FROM user_roles)")
