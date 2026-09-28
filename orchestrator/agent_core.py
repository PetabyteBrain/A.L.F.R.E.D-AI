"""Agent Core: Claude (Haiku 4.5) with role-gated tool calling and per-(channel,user) memory.

Agent Core only ever receives resolved, registered people — identity resolution happens
in the channel handlers before anything gets here.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from . import db, sessions
from .config import settings
from .identity.registry import Person
from .tools import registry as tool_registry
from .tools.registry import ToolContext

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

_client: anthropic.AsyncAnthropic | None = None
_locks: dict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None, max_retries=3, timeout=60.0)
    return _client


# Stable part of the system prompt — keep it byte-identical between requests so it caches.
STATIC_SYSTEM = f"""You are {settings.assistant_name}, a personal AI assistant running on a private home server.
You work for a small, fixed set of registered people. Everyone you talk to has already been
identified by the system; you are told who they are and what role they have in each request.

Channels:
- telegram: text chat. Reply in plain text; short paragraphs; light Markdown only when it
  genuinely helps (lists, code). No headings.
- call: a live phone call. Your words are converted to speech. Speak naturally in short
  sentences, one idea at a time. Never use Markdown, bullet points, emoji, URLs or symbols
  that don't read aloud. Keep answers to one to three sentences unless asked for detail.
  If a tool will take a moment, say so briefly first.

Roles:
- admin: the owner. Full access to every tool, including n8n workflow management.
- cofounder: the owner's co-founder and closest partner. Near-admin: operational tools,
  calls and messages, n8n read/trigger access. Cannot create, change, (de)activate or delete
  n8n workflows.
- friend: a trusted person with limited access. Only the tools you are given are available
  to them; don't reveal details about other people, the owner's data, system internals,
  or tools they can't use.
Other roles may exist; the tool list you receive is always exactly what the current person
is allowed to use. Never claim to have done something you have no tool for.

Tools:
- Use tools when they are the reliable way to answer; don't guess system state.
- n8n: the owner's automation server. Workflows may not exist yet. Before any destructive
  n8n action (deleting, overwriting a workflow), state exactly what will happen and get an
  explicit yes in the conversation first.
- If a tool fails, say so plainly and suggest a next step. Don't retry the same failing
  call more than once.

Style: warm, concise, capable, a little dry wit. Match the language the person writes or
speaks in (you'll often hear English, German, Swiss German, French or Greek). Never invent
facts about people, schedules or system state.
"""


def _dynamic_system(person: Person, channel: str, extra: str | None) -> str:
    now = datetime.now(ZoneInfo(settings.timezone)).strftime("%A %d %B %Y, %H:%M")
    lines = [
        f"Current person: {person.display_name} (user id {person.id}, role: {person.role}).",
        f"Channel: {channel}.",
        f"Local time: {now} ({settings.timezone}).",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _merge_user(messages: list[dict], text: str) -> list[dict]:
    if messages and messages[-1]["role"] == "user":
        messages[-1] = {"role": "user", "content": f"{messages[-1]['content']}\n{text}"}
    else:
        messages.append({"role": "user", "content": text})
    return messages


def _text_of(content) -> str:
    return "".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


async def respond(
    person: Person,
    channel: str,
    session_id: str,
    user_text: str,
    *,
    extra_system: str | None = None,
    record_user_turn: bool = True,
) -> str:
    """Run one exchange: history + new message -> (tools)* -> reply text. Persists both turns."""
    async with _locks[(person.id, channel)]:
        live = sessions.LIVE.get(session_id)
        if live:
            live.busy = True
        try:
            reply = await _run(person, channel, session_id, user_text, extra_system)
        finally:
            if live:
                live.busy = False
        if record_user_turn:
            sessions.add_turn(session_id, "user", user_text)
        sessions.add_turn(session_id, "assistant", reply)
        return reply


async def _run(person: Person, channel: str, session_id: str, user_text: str, extra_system: str | None) -> str:
    if not settings.anthropic_api_key:
        return "My language model isn't configured yet (ANTHROPIC_API_KEY is empty)."

    messages = _merge_user(sessions.history(person.id, channel), user_text)
    tools = tool_registry.tools_for(person.role)
    system = [
        {"type": "text", "text": STATIC_SYSTEM, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _dynamic_system(person, channel, extra_system)},
    ]
    ctx = ToolContext(person=person, session_id=session_id, channel=channel)
    max_tokens = 400 if channel == "call" else settings.claude_max_tokens

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            kwargs = dict(model=settings.claude_model, max_tokens=max_tokens, system=system, messages=messages)
            if tools:
                kwargs["tools"] = tools
            response = await client().messages.create(**kwargs)
        except anthropic.AuthenticationError:
            db.set_health("claude", False, "authentication failed")
            return "I can't reach my language model right now (authentication failed)."
        except anthropic.RateLimitError:
            db.set_health("claude", False, "rate limited")
            return "I'm being rate-limited right now. Try again in a minute."
        except anthropic.APIStatusError as e:
            db.set_health("claude", False, f"HTTP {e.status_code}")
            log.exception("Claude API error")
            return "Something went wrong talking to my language model. Try again shortly."
        except anthropic.APIConnectionError:
            db.set_health("claude", False, "connection error")
            return "I can't reach my language model right now (network)."

        db.set_health("claude", True, f"ok ({settings.claude_model})")

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                out, is_error = await tool_registry.execute(block.name, dict(block.input or {}), ctx)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
            messages.append({"role": "user", "content": results})
            continue

        text = _text_of(response.content)
        if response.stop_reason == "refusal":
            return text or "I can't help with that one."
        if response.stop_reason == "max_tokens" and text:
            return text + " …"
        return text or "(no response)"

    return "I got stuck in a loop of tool calls, so I stopped. Could you rephrase what you need?"
