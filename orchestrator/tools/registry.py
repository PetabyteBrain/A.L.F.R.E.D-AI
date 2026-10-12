"""Tool registry: loads registry.yaml, builds role-filtered tool lists, executes tool calls."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

from .. import db
from ..config import settings
from ..identity.registry import Person
from . import builtin, n8n_tool

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 12_000


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str
    description: str
    input_schema: dict
    permission: str | None = None   # orchestrator/permissions.py; None = everyone
    config: dict = field(default_factory=dict)
    channels: tuple[str, ...] = ()  # empty = every channel

    def allowed_for(self, person: Person) -> bool:
        if not self.permission:
            return True
        from .. import permissions
        return permissions.can(person, self.permission)

    def api_definition(self) -> dict:
        if self.kind == "anthropic_server":   # runs on Anthropic's side (e.g. web search): no schema
            return {"name": self.name, **self.config.get("server_tool", {})}
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolContext:
    person: Person
    session_id: str | None
    channel: str


_TOOLS: dict[str, ToolSpec] = {}


def load() -> dict[str, ToolSpec]:
    raw = yaml.safe_load(settings.tool_registry_path.read_text(encoding="utf-8")) or {}
    tools: dict[str, ToolSpec] = {}
    for entry in raw.get("tools", []):
        entry = dict(entry)
        name = entry.pop("name")
        kind = entry.pop("kind")
        if kind == "builtin" and entry.get("handler") not in builtin.HANDLERS:
            log.error("Tool %s: unknown builtin handler %r — skipped", name, entry.get("handler"))
            continue
        from .. import permissions
        perm = entry.pop("permission", None)
        if perm is not None and perm not in permissions.KEYS:
            log.error("Tool %s: unknown permission %r — skipped", name, perm)
            continue
        tools[name] = ToolSpec(
            name=name,
            kind=kind,
            description=entry.pop("description", "").strip(),
            input_schema=entry.pop("input_schema", {"type": "object", "properties": {}}),
            permission=perm,
            channels=tuple(entry.pop("channels", [])),
            config=entry,
        )
    _TOOLS.clear()
    _TOOLS.update(tools)
    log.info("Loaded %d tools from %s", len(tools), settings.tool_registry_path)
    return tools


def _available(spec: ToolSpec, person_id: int | None = None) -> bool:
    if spec.config.get("requires") == "google":
        if not settings.google_enabled:
            return False
        if person_id is not None:
            from .. import google_calendar
            return google_calendar.is_linked(person_id)
        return True
    if spec.kind in ("n8n_api",):
        return settings.n8n_enabled
    if spec.kind == "n8n_webhook":
        return bool(settings.n8n_base_url)
    if spec.name in ("find_meeting_slots", "propose_meeting"):
        return settings.telegram_enabled or (settings.twilio_enabled and settings.speech_enabled)
    if spec.name in ("place_call", "schedule_call", "list_scheduled_calls", "cancel_scheduled_call"):
        return settings.twilio_enabled and settings.speech_enabled and bool(settings.public_base_url)
    if spec.name == "send_telegram_message":
        return settings.telegram_enabled
    if spec.config.get("requires") == "mail":
        return settings.mail_enabled
    if spec.config.get("requires") == "nas":
        from .. import nas
        return nas.enabled()
    if spec.name.startswith("homelab_") or spec.name == "minecraft_status":
        from .. import homelab
        return homelab.enabled()
    if spec.kind == "anthropic_server" and spec.name == "web_search":
        return settings.web_search_enabled
    return True


def spec(name: str) -> ToolSpec | None:
    if not _TOOLS:
        load()
    return _TOOLS.get(name)


def tools_for(person: Person | str, channel: str | None = None, person_id: int | None = None) -> list[dict]:
    """Tools this person may use on this channel. A role name instead of a person = what that one role
    gets (role editor, tests); then `person_id` optionally checks that person's own Google link.

    Deterministic (sorted) so the prompt-cache prefix stays stable between requests.
    """
    if not _TOOLS:
        load()
    from .. import permissions
    if isinstance(person, str):
        perms, link_id = permissions.resolve([person]), person_id
    else:
        perms, link_id = permissions.for_person(person), person.id
    return [s.api_definition() for s in sorted(_TOOLS.values(), key=lambda s: s.name)
            if (not s.permission or s.permission in perms)
            and (not s.channels or channel is None or channel in s.channels)
            and _available(s, link_id)]


def _truncate(result: Any) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n…[truncated, {len(text) - MAX_RESULT_CHARS} more chars]"
    return text


_READ_ONLY_PREFIXES = ("list_", "get_", "search_", "find_", "calendar_list", "n8n_get", "n8n_list", "homelab_status",
                       "homelab_logs", "recent_", "agenda", "current_time", "resolve_date")


def date_problems(name: str, args: dict) -> list[str]:
    """Weekday/date mismatches in a tool call that acts on something (orchestrator/dates.py)."""
    if name.startswith(_READ_ONLY_PREFIXES):
        return []
    from .. import dates
    try:
        return dates.check_args(args)
    except Exception:  # noqa: BLE001 — a checker bug must never block a tool
        log.warning("Date check failed for %s", name, exc_info=True)
        return []


async def execute(name: str, args: dict, ctx: ToolContext) -> tuple[str, bool]:
    """Run a tool. Returns (result_text, is_error). Never raises."""
    if not _TOOLS:
        load()
    spec = _TOOLS.get(name)
    if spec is None or not spec.allowed_for(ctx.person) or (spec.channels and ctx.channel not in spec.channels):
        # Defence in depth: the model only sees permitted tools, but re-check anyway.
        result, is_error = f"Tool '{name}' is not available to this user.", True
        db.log_tool_call(name, args, result, True, ctx.session_id, ctx.person.id)
        return result, is_error
    try:
        if spec.kind == "builtin":
            out = await builtin.HANDLERS[spec.config["handler"]](ctx, **args)
        elif spec.kind == "n8n_api":
            out = await n8n_tool.run_action(spec.config["action"], args)
        elif spec.kind == "n8n_webhook":
            out = await _run_webhook(spec, args, ctx)
        else:
            raise ValueError(f"unknown tool kind {spec.kind}")
        result, is_error = _truncate(out), False
    except TypeError as e:  # bad/missing arguments from the model
        result, is_error = f"Invalid arguments for {name}: {e}", True
    except Exception as e:  # noqa: BLE001
        log.exception("Tool %s failed", name)
        result, is_error = f"{type(e).__name__}: {e}"[:1000], True

    db.log_tool_call(name, args, result, is_error, ctx.session_id, ctx.person.id)
    db.log_activity("tool", f"{name} {'failed' if is_error else 'ok'}", user_id=ctx.person.id,
                    session_id=ctx.session_id)
    return result, is_error


async def _run_webhook(spec: ToolSpec, args: dict, ctx: ToolContext) -> Any:
    payload = {
        "tool": spec.name,
        "input": args,
        "user": {"id": ctx.person.id, "name": ctx.person.display_name, "role": ctx.person.role,
                 "roles": list(ctx.person.roles or (ctx.person.role,))},
        "session_id": ctx.session_id,
        "channel": ctx.channel,
    }
    if spec.config.get("async"):
        cid = n8n_tool.new_correlation_id()
        payload["correlation_id"] = cid
        payload["callback"] = {
            "url": f"{settings.public_base_url}/n8n/callback",
            "header": "X-Alfred-Secret",
            "secret": settings.n8n_callback_secret,
        }
        await n8n_tool.post_webhook(spec.config["webhook_path"], payload, timeout=15)
        return {"status": "started", "correlation_id": cid,
                "note": "Running in n8n; the result will be delivered to the user when it finishes."}
    return await n8n_tool.post_webhook(spec.config["webhook_path"], payload,
                                       timeout=float(spec.config.get("timeout", 60)))
