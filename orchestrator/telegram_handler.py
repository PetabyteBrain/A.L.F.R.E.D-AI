"""Telegram bot (long polling — no public endpoint needed).

Identity = Telegram numeric user ID. Messages from anyone not in the registry are
dropped without a reply; only private chats are served.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from . import agent_core, db, sessions
from .config import settings
from .identity import registry, verification

log = logging.getLogger(__name__)

TG_LIMIT = 4000
_app: Application | None = None
_bot_username: str | None = None


def bot_username() -> str | None:
    return _bot_username


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
        # The one exception to silence: an ID with a pending confirmation code (added on the
        # dashboard) sending that code. Everyone else still gets no reply at all.
        status, verified = verification.claim_telegram(str(user.id), msg.text or "")
        if status == "verified":
            first = verified.display_name.split()[0] if verified else ""
            await msg.reply_text(f"Thank you{', ' + first if first else ''}. This Telegram account is now "
                                 f"verified; {settings.assistant_name} at your service.")
        elif status == "wrong":
            await msg.reply_text("That isn't the code shown on the dashboard. Please send the 6-digit code "
                                 "exactly as shown.")
        else:
            log.info("Dropped Telegram message from unregistered id %s (@%s)", user.id, user.username)
        return

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


async def send_to_user(user_id: int, text: str, note: str | None = None, record: bool = True,
                       buttons: list[list[tuple[str, str]]] | None = None) -> None:
    """Proactive message to a registered person (tools, n8n callbacks, reminders).
    `buttons`: rows of (label, callback_data) shown under the last message part."""
    if _app is None:
        raise RuntimeError("Telegram bot is not running.")
    tg = registry.telegram_for(user_id)
    if not tg:
        raise ValueError(f"User {user_id} has no verified Telegram ID in the registry.")
    session_id = sessions.telegram_session_for(user_id, tg)
    parts = _chunks(text)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                   for row in buttons]) if buttons else None
    for i, part in enumerate(parts):
        await _app.bot.send_message(chat_id=int(tg), text=part, disable_web_page_preview=True,
                                    reply_markup=markup if i == len(parts) - 1 else None)
    if record:
        sessions.add_turn(session_id, "assistant", text)
    db.log_activity("message", f"Proactive Telegram message{f' ({note})' if note else ''}", user_id, session_id)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline buttons under reminders: rem:done:<id> / rem:snooze:<id>."""
    from . import reminders
    q = update.callback_query
    if not q or not q.from_user:
        return
    person = registry.resolve("telegram", "telegram", str(q.from_user.id))
    if person is None:
        return  # silent, like messages
    kind, _, rest = (q.data or "").partition(":")
    action, _, rid = rest.partition(":")
    if kind != "rem" or not rid.isdigit() or action not in ("done", "snooze"):
        await q.answer()
        return
    try:
        row = reminders.acknowledge(int(rid), person, snooze_minutes=10 if action == "snooze" else 0)
    except reminders.ReminderError as e:
        await q.answer(str(e), show_alert=False)
        return
    note = "✓ Done" if action == "done" else f"⏳ Snoozed until {row['due_local'][-5:]}"
    await q.answer(note)
    with contextlib.suppress(Exception):
        await q.edit_message_text(f"{q.message.text}\n{note}", reply_markup=None)
    db.log_activity("task", f"{person.display_name}: reminder #{rid} {action}", user_id=person.id)


async def start() -> None:
    global _app, _bot_username
    if not settings.telegram_enabled:
        log.warning("TELEGRAM_BOT_TOKEN not set — Telegram channel disabled.")
        db.set_health("telegram", False, "not configured")
        return
    _app = Application.builder().token(settings.telegram_bot_token).concurrent_updates(True).build()
    _app.add_handler(CallbackQueryHandler(on_button))
    _app.add_handler(MessageHandler(filters.ALL, on_message))
    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(allowed_updates=["message", "callback_query"])
    me = await _app.bot.get_me()
    _bot_username = me.username
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
