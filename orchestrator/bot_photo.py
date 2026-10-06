"""Set the Telegram bot's profile picture (Bot API 9.4 setMyProfilePhoto). One-off, run on the Pi:
    docker compose exec alfred python -m orchestrator.bot_photo docs/assets/alfred-telegram.png
Falls back to telling you to use @BotFather /setuserpic if Telegram refuses."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from .config import settings


def main(path: str) -> int:
    if not settings.telegram_bot_token:
        print("No TELEGRAM_BOT_TOKEN configured.")
        return 1
    data = Path(path).read_bytes()
    r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/setMyProfilePhoto",
                   data={"photo": json.dumps({"type": "static", "photo": "attach://pic"})},
                   files={"pic": (Path(path).name, data, "image/png")}, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"description": r.text[:200]}
    if body.get("ok"):
        print("Bot profile picture updated.")
        return 0
    print(f"Telegram said: {body.get('description')}. Set it by hand instead: @BotFather -> /setuserpic -> pick the bot -> send {path}.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/assets/alfred-telegram.png"))
