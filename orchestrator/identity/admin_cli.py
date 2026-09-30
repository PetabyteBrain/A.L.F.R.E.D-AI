"""Admin CLI for the registry.

Run inside the container:
    docker compose exec alfred python -m orchestrator.identity.admin_cli <command> ...

Commands:
    list                                     people + identifiers
    add "Name" --role friend [--telegram ID] [--phone +41..] [--email x@y] [--dashboard user]
    link USER_ID TYPE VALUE                  attach another identifier
    unlink IDENTIFIER_ID                     remove one identifier
    set-role USER_ID ROLE
    set-password USER_ID USERNAME            prompts; creates/updates a dashboard login
    disable USER_ID / enable USER_ID         soft on/off (history is kept)
    remove USER_ID                           hard delete (only if the person has no sessions)
    roles                                    list roles
    add-role NAME [--desc ..] [--no-telegram] [--no-call] [--dashboard]
    hash-password                            prints an Argon2 hash (for DASHBOARD_ADMIN_PASSWORD_HASH)
    attempts [--limit 20]                    recent blocked access attempts
    logout-all [USER_ID]                     revoke dashboard sessions (one person or everyone)
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .. import db
from . import registry


def _print_people() -> None:
    people = db.query("SELECT id, display_name, role, active FROM users ORDER BY id")
    if not people:
        print("(registry is empty)")
    for p in people:
        flag = "" if p["active"] else "  [disabled]"
        print(f"#{p['id']}  {p['display_name']}  ({p['role']}){flag}")
        for i in registry.identifiers_for(p["id"]):
            print(f"     id={i['id']:<4} {i['type']:<10} {i['value']}")


def _prompt_password() -> str:
    pw = getpass.getpass("Password: ")
    if len(pw) < 10:
        sys.exit("Use at least 10 characters.")
    if pw != getpass.getpass("Repeat:   "):
        sys.exit("Passwords don't match.")
    return pw


def _revoke(user_id: int) -> None:
    """Credentials, role or status changed -> existing dashboard logins must not survive it."""
    db.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user_id,))


def main(argv: list[str] | None = None) -> None:
    db.init_db()
    ap = argparse.ArgumentParser(prog="admin_cli", description="Alfred registry admin")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--role", default="friend")
    a.add_argument("--telegram")
    a.add_argument("--phone")
    a.add_argument("--email")
    a.add_argument("--dashboard", help="dashboard username (you'll be prompted for a password)")
    a.add_argument("--notes", default="")

    l = sub.add_parser("link")
    l.add_argument("user_id", type=int)
    l.add_argument("type", choices=registry.IDENTIFIER_TYPES)
    l.add_argument("value")

    u = sub.add_parser("unlink")
    u.add_argument("identifier_id", type=int)

    r = sub.add_parser("set-role")
    r.add_argument("user_id", type=int)
    r.add_argument("role")

    sp = sub.add_parser("set-password")
    sp.add_argument("user_id", type=int)
    sp.add_argument("username")

    for name in ("disable", "enable", "remove"):
        p = sub.add_parser(name)
        p.add_argument("user_id", type=int)

    sub.add_parser("roles")
    ar = sub.add_parser("add-role")
    ar.add_argument("name")
    ar.add_argument("--desc", default="")
    ar.add_argument("--no-telegram", action="store_true")
    ar.add_argument("--no-call", action="store_true")
    ar.add_argument("--dashboard", action="store_true")

    sub.add_parser("hash-password")
    lo = sub.add_parser("logout-all")
    lo.add_argument("user_id", type=int, nargs="?")
    at = sub.add_parser("attempts")
    at.add_argument("--limit", type=int, default=20)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        _print_people()

    elif args.cmd == "add":
        uid = registry.add_user(args.name, args.role, args.notes)
        if args.telegram:
            registry.add_identifier(uid, "telegram", args.telegram, verified=True)
        if args.phone:
            registry.add_identifier(uid, "phone", args.phone, verified=True)
        if args.email:
            registry.add_identifier(uid, "email", args.email, verified=True)
        if args.dashboard:
            registry.add_identifier(uid, "dashboard", args.dashboard, registry.hash_password(_prompt_password()), verified=True)
        db.log_activity("system", f"Registry: added {args.name} ({args.role})", user_id=uid)
        print(f"Added #{uid}.")
        _print_people()

    elif args.cmd == "link":
        pw_hash = registry.hash_password(_prompt_password()) if args.type == "dashboard" else None
        registry.add_identifier(args.user_id, args.type, args.value, pw_hash, verified=True)
        print("Linked.")

    elif args.cmd == "unlink":
        row = db.query_one("SELECT user_id FROM user_identifiers WHERE id = ?", (args.identifier_id,))
        db.execute("DELETE FROM user_identifiers WHERE id = ?", (args.identifier_id,))
        if row:
            _revoke(row["user_id"])
        print("Unlinked.")

    elif args.cmd == "set-role":
        registry.update_user(args.user_id, role=args.role)  # refuses to demote the last admin
        _revoke(args.user_id)
        print("Role updated.")

    elif args.cmd == "set-password":
        registry.set_dashboard_login(args.user_id, args.username, _prompt_password())
        _revoke(args.user_id)
        print("Dashboard login set.")

    elif args.cmd in ("disable", "enable"):
        registry.update_user(args.user_id, active=args.cmd == "enable")
        _revoke(args.user_id)
        print(f"User {args.cmd}d.")

    elif args.cmd == "remove":
        registry.delete_user(args.user_id)  # same guard rails as the dashboard
        print("Removed.")

    elif args.cmd == "roles":
        for r in db.query("SELECT * FROM roles ORDER BY name"):
            chans = [c for c, col in (("telegram", "can_telegram"), ("call", "can_call"), ("dashboard", "can_dashboard")) if r[col]]
            print(f"{r['name']:<10} channels={','.join(chans) or '-'}  {r['description']}")

    elif args.cmd == "add-role":
        db.execute(
            "INSERT INTO roles(name, description, can_telegram, can_call, can_dashboard) VALUES (?,?,?,?,?)",
            (args.name, args.desc, int(not args.no_telegram), int(not args.no_call), int(args.dashboard)),
        )
        print(f"Role '{args.name}' created. Grant it tools in tools/registry.yaml.")

    elif args.cmd == "hash-password":
        print(registry.hash_password(_prompt_password()))

    elif args.cmd == "logout-all":
        if args.user_id:
            _revoke(args.user_id)
        else:
            db.execute("DELETE FROM dashboard_sessions")
        print("Dashboard sessions revoked.")

    elif args.cmd == "attempts":
        for row in db.query(
            "SELECT ts, channel, raw_identifier, detail FROM access_attempts WHERE resolved = 0 "
            "ORDER BY id DESC LIMIT ?",
            (args.limit,),
        ):
            print(f"{row['ts']}  {row['channel']:<9} {row['raw_identifier']:<30} {row['detail']}")


if __name__ == "__main__":
    try:
        main()
    except registry.RegistryError as e:
        sys.exit(f"Error: {e}")
