"""Telegram bot (long polling — no public endpoint needed).

Identity = Telegram numeric user ID. Messages from anyone not in the registry are
dropped without a reply; only private chats are served.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from . import agent_core, db, sessions
from .config import settings
from .identity import registry

log = logging.getLogger(__name__)

TG_LIMIT = 4000
_app: Application | None = None


def _chunks(text: str) -> list[str]:
    out, buf = [], ""
    for para in text.split("\n"):
        while len(para) > TG_LIMIT:
            out.append(para[:TG_LIMIT])
            para = para[TG_LIMIT:]
        if len(buf) + len(para) + 1 > TG_LIMIT:
            out.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        out.append(buf)
    return out or ["…"]


async def _keep_typing(chat_id: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(Exception):
            await _app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=4.5)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return
    if chat.type != ChatType.PRIVATE:
        # Groups/channels are never served; log once per message and stay silent.
        registry._record_attempt("telegram", f"telegram:{user.id}", False, f"non-private chat {chat.type}")
        return

    person = registry.resolve("telegram", "telegram", str(user.id))
    if person is None:
        log.info("Dropped Telegram message from unregistered id %s (@%s)", user.id, user.username)
        return  # silent by design

    text = msg.text or msg.caption
    if not text:
        await msg.reply_text("I can only read text messages for now.")
        return

    session_id = sessions.telegram_session_for(person.id, str(chat.id))
    db.log_activity("message", f"Telegram from {person.display_name}: {text[:80]}", person.id, session_id)

    stop = asyncio.Event()
    typing = asyncio.create_task(_keep_typing(chat.id, stop))
    try:
        reply = await agent_core.respond(person, "telegram", session_id, text)
    except Exception:
        log.exception("Agent failure for Telegram message")
        reply = "Something broke on my side. It's been logged."
    finally:
        stop.set()
        await typing

    for part in _chunks(reply):
        await msg.reply_text(part, disable_web_page_preview=True)


async def send_to_user(user_id: int, text: str, note: str | None = None, record: bool = True) -> None:
    """Proactive message to a registered person (tools, n8n callbacks)."""
    if _app is None:
        raise RuntimeError("Telegram bot is not running.")
    row = db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND type = 'telegram' LIMIT 1", (user_id,))
    if not row:
        raise ValueError(f"User {user_id} has no Telegram ID in the registry.")
    session_id = sessions.telegram_session_for(user_id, row["value"])
    for part in _chunks(text):
        await _app.bot.send_message(chat_id=int(row["value"]), text=part, disable_web_page_preview=True)
    if record:
        sessions.add_turn(session_id, "assistant", text)
    db.log_activity("message", f"Proactive Telegram message{f' ({note})' if note else ''}", user_id, session_id)


async def start() -> None:
    global _app
    if not settings.telegram_enabled:
        log.warning("TELEGRAM_BOT_TOKEN not set — Telegram channel disabled.")
        db.set_health("telegram", False, "not configured")
        return
    _app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(True).build()
    _app.add_handler(MessageHandler(filters.ALL, on_message))
    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(allowed_updates=["message"])
    me = await _app.bot.get_me()
    db.set_health("telegram", True, f"polling as @{me.username}")
    log.info("Telegram bot @%s polling", me.username)


async def stop() -> None:
    global _app
    if _app is None:
        return
    with contextlib.suppress(Exception):
        await _app.updater.stop()
        await _app.stop()
        await _app.shutdown()
    _app = None


async def health() -> tuple[bool, str]:
    if _app is None:
        return False, "not running" if settings.telegram_enabled else "not configured"
    try:
        me = await _app.bot.get_me()
        return True, f"polling as @{me.username}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
