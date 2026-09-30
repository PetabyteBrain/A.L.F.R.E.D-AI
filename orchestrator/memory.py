"""Long-term memory: short notes Alfred keeps per person ("prefers morning calls", "Max's
birthday is 3 May") and sees in every conversation with that person, on every channel.

A note belongs to the person who owns it and is only ever shown in that person's own
conversations; it is never used while talking to anyone else. Notes are editable on the
dashboard (Profile). Obvious secrets (passwords, card or account numbers) are refused.
"""
from __future__ import annotations

import re

from . import db
from .identity.registry import Person

MAX_NOTES = 200
MAX_CHARS = 500
PROMPT_BUDGET = 4000   # characters of notes put into the system prompt (newest first)

_SECRETS = [
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                                       # card-like numbers
    re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}\b"),                   # IBAN-like
    re.compile(r"\b(pass(word|wort|code)?|pin|passcode|mot de passe|kennwort)\b\s*(is|ist|est|:|=)", re.I),
]


class MemoryNoteError(ValueError):
    """Message is safe to show to the user / the model."""


def _clean(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        raise MemoryNoteError("There's nothing to remember.")
    if len(text) > MAX_CHARS:
        raise MemoryNoteError(f"Keep a note under {MAX_CHARS} characters.")
    if any(p.search(text) for p in _SECRETS):
        raise MemoryNoteError("That looks like a password or card/account number. I don't store those.")
    return text


def add(owner: Person, text: str, *, created_by: Person | None = None) -> dict:
    text = _clean(text)
    n = db.query_one("SELECT COUNT(*) AS n FROM memories WHERE user_id = ?", (owner.id,))["n"]
    if n >= MAX_NOTES:
        raise MemoryNoteError("The notebook is full. Delete some older notes first.")
    if db.query_one("SELECT 1 FROM memories WHERE user_id = ? AND lower(text) = lower(?)", (owner.id, text)):
        raise MemoryNoteError("I already have exactly that noted.")
    now = db.now_iso()
    mid = db.execute("INSERT INTO memories(user_id, created_by, text, created_at, updated_at) VALUES (?,?,?,?,?)",
                     (owner.id, (created_by or owner).id, text, now, now))
    db.log_activity("system", f"Memory: noted for {owner.display_name}", user_id=owner.id)
    return get(mid)


def get(memory_id: int) -> dict | None:
    return db.query_one("SELECT id, user_id, text, created_at, updated_at FROM memories WHERE id = ?", (memory_id,))


def list_for(owner_id: int) -> list[dict]:
    return db.query("SELECT id, text, created_at, updated_at FROM memories WHERE user_id = ? ORDER BY id DESC",
                    (owner_id,))


def _own(memory_id: int, owner: Person) -> dict:
    row = get(memory_id)
    if not row or row["user_id"] != owner.id:
        raise MemoryNoteError("No such note.")
    return row


def update(memory_id: int, owner: Person, text: str) -> dict:
    _own(memory_id, owner)
    db.execute("UPDATE memories SET text = ?, updated_at = ? WHERE id = ?", (_clean(text), db.now_iso(), memory_id))
    return get(memory_id)


def delete(memory_id: int, owner: Person) -> None:
    _own(memory_id, owner)
    db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


def prompt_block(owner_id: int) -> str | None:
    """Notes for the system prompt, newest first within a size budget."""
    lines, used = [], 0
    for m in list_for(owner_id):
        line = f"- [{m['id']}] {m['text']}"
        if used + len(line) > PROMPT_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return None
    return ("Your notes for this person (their long-term memory; use them naturally, don't recite them; "
            "ids are for update_memory/forget_memory):\n" + "\n".join(lines))
