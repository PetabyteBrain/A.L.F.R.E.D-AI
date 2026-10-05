"""Switchable permissions per role, set by admins on the dashboard's Settings tab.

Roles stay the coarse gate (registry.yaml `roles`); these switches narrow what a role gets on top.
Admins always have everything; a switch only exists for the roles listed in SWITCHES.

n8n for cofounders (all on by default, decided 2026-10-06):
  n8n_view — see workflows and runs (n8n panel on Tasks, list/get tools, briefing failures)
  n8n_run  — run workflows (trigger webhooks)
  n8n_edit — create, change and switch workflows on/off, "Open n8n" / "Edit" links
Deleting a workflow stays admin-only.
"""
from __future__ import annotations

from . import db

SETTING = "role_permissions"
LABELS = {
    "n8n_view": "See n8n workflows and their runs",
    "n8n_run": "Run n8n workflows",
    "n8n_edit": "Create, edit and switch n8n workflows on/off",
}
SWITCHES: dict[str, dict[str, bool]] = {
    "cofounder": {"n8n_view": True, "n8n_run": True, "n8n_edit": True},
}


class PermissionError_(ValueError):
    """Message is safe to show."""


def all_settings() -> dict[str, dict[str, bool]]:
    stored = db.get_setting(SETTING, {}) or {}
    out = {}
    for role, perms in SWITCHES.items():
        saved = stored.get(role, {}) if isinstance(stored.get(role), dict) else {}
        out[role] = {p: bool(saved.get(p, default)) for p, default in perms.items()}
    return out


def allowed(role: str, permission: str) -> bool:
    if role == "admin":
        return True
    perms = all_settings().get(role)
    return bool(perms and perms.get(permission))


def set_permission(role: str, permission: str, value: bool, *, actor: str) -> dict[str, dict[str, bool]]:
    if role not in SWITCHES:
        raise PermissionError_(f"There are no switches for the {role} role.")
    if permission not in SWITCHES[role]:
        raise PermissionError_("Unknown permission.")
    stored = db.get_setting(SETTING, {}) or {}
    stored.setdefault(role, {})[permission] = bool(value)
    db.set_setting(SETTING, stored)
    db.log_activity("access", f"{actor} turned {'on' if value else 'off'} '{LABELS[permission]}' for {role}s")
    return all_settings()
