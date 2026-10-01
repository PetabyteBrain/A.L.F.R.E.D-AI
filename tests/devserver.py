"""Local dashboard preview with demo data and no API keys.

    python -m tests.devserver      -> http://127.0.0.1:8765  (login: admin / devpassword123)
"""
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "data-dev"
os.environ.setdefault("DB_PATH", str(root / "alfred.db"))
os.environ.setdefault("STORAGE_ROOT", str(root / "storage"))
os.environ.setdefault("DASHBOARD_COOKIE_SECURE", "false")
os.environ.setdefault("DASHBOARD_SESSION_SECRET", "dev-only-secret")
os.environ.setdefault("ADMIN_DISPLAY_NAME", "Erika Mustermann")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "100000001")

import uvicorn  # noqa: E402

from orchestrator import db, sessions  # noqa: E402
from orchestrator.identity import registry  # noqa: E402
from orchestrator.storage.recordings import CallRecorder  # noqa: E402


def seed() -> None:
    db.init_db()
    registry.bootstrap_admin()
    # demo Systems rows (the real ones come from health/homelab checks on the Pi)
    for svc, ok, detail in [
        ("claude", True, "claude-haiku-4-5 reachable"), ("telegram", True, "polling as @Demo_Bot"),
        ("jetson_speech", True, "cuda · whisper-large-v3-turbo · voice bm_george · stt 750 ms · tts 346 ms"),
        ("backup", True, "last 3 h ago · db 408K + 30 recording files -> Jetson"),
        ("host host-b", True, "host-b: load 0.28/4 cpu · mem 19% · disk / 25% · 58°C · 11/12 containers up · up 12.2 d"),
        ("host host-h", False, "host-h: disk / 93% full"),
        ("host jetson", True, "jetson: load 0.02/6 cpu · mem 69% · disk / 47% /ssd 6% · 50°C · 1/1 containers up"),
    ]:
        db.set_health(svc, ok, detail)
    if db.query_one("SELECT 1 FROM sessions LIMIT 1"):
        return
    admin = db.query_one("SELECT id FROM users WHERE role='admin'")["id"]
    registry.upsert_identifier(admin, "dashboard", "admin", registry.hash_password("devpassword123"))
    friend = registry.add_user("Alex Friend", "friend")
    registry.add_identifier(friend, "telegram", "200000002", verified=True)

    s = sessions.start_session(admin, "call", external_id="CAdemo")
    sessions.add_turn(s, "assistant", "Good evening, sir. Alfred here. How may I be of service?")
    sessions.add_turn(s, "user", "Remind me what's on the task list.")
    sessions.add_turn(s, "assistant", "Just one thing: water the plants, every evening.")
    rec = CallRecorder(s)
    for _ in range(250):
        rec.caller_audio(bytes([0xFF]) * 160)
    rec.close()
    sessions.end_session(s)

    live = sessions.start_session(friend, "telegram", external_id="200000002")
    sessions.add_turn(live, "user", "Hey, is the Minecraft server up?")
    sessions.add_turn(live, "assistant", "I can't check that one for you, but I'll let Erika know you asked.")
    db.execute("INSERT INTO tasks(title, schedule, created_at) VALUES ('Water the plants','every evening',?)",
               (db.now_iso(),))
    db.execute("INSERT INTO access_attempts(channel, raw_identifier, resolved, detail, ts) VALUES "
               "('telegram','telegram:987654',0,'unregistered',?)", (db.now_iso(),))
    db.set_health("claude", False, "not configured")

    # demo usage for the Usage tab + a couple of call requests for the call queue
    import random
    from datetime import datetime, timedelta, timezone
    rnd = random.Random(7)
    for i in range(24):
        day = (datetime.now(timezone.utc) - timedelta(days=i)).replace(hour=12).isoformat(timespec="seconds")
        for kind in ("telegram", "call"):
            inp, out, cr = rnd.randint(2000, 30000), rnd.randint(200, 3000), rnd.randint(0, 60000)
            cost = (inp * 1 + out * 5 + cr * 0.1) / 1e6
            db.execute("INSERT INTO api_usage(ts, provider, kind, model, input_tokens, output_tokens, cache_read_tokens, "
                       "cost_usd, user_id) VALUES (?,?,?,?,?,?,?,?,?)", (day, "anthropic", kind, "claude-haiku-4-5",
                                                                          inp, out, cr, cost, admin))
        db.execute("INSERT INTO api_usage(ts, provider, kind, units, unit, user_id) VALUES (?,?,?,?,?,?)",
                   (day, "elevenlabs", "tts", rnd.randint(200, 3000), "chars", admin))
        db.execute("INSERT INTO api_usage(ts, provider, kind, units, unit, user_id) VALUES (?,?,?,?,?,?)",
                   (day, "twilio", "voice-outbound", rnd.randint(30, 400), "seconds", admin))
    now = datetime.now(timezone.utc)
    db.execute("INSERT INTO scheduled_calls(target_user_id, requested_by, run_at, purpose, status, created_at, updated_at) "
               "VALUES (?,?,?,?,?,?,?)", (friend, admin, (now + timedelta(days=1)).isoformat(timespec="seconds"),
                                          "Ask Alex whether he can bring the projector on Friday.", "pending",
                                          db.now_iso(), db.now_iso()))
    db.execute("INSERT INTO scheduled_calls(target_user_id, requested_by, run_at, purpose, status, attempts, result, "
               "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (friend, admin, (now - timedelta(hours=3)).isoformat(timespec="seconds"),
                "Ask Alex if the invoice is ready.", "done", 1,
                "Alex says the invoice will be sent by Thursday noon; he needs the project number first.",
                db.now_iso(), db.now_iso()))
    db.set_health("telegram", False, "not configured")


def seed_live_call() -> None:
    """Runs after startup (startup closes orphaned calls): a fake in-progress call for the Live view."""
    import threading, time

    def go():
        time.sleep(8)
        admin = db.query_one("SELECT id FROM users WHERE role='admin'")["id"]
        sid = sessions.start_session(admin, "call", external_id=None)
        sessions.add_turn(sid, "assistant", "Good evening, sir. Alfred here. How may I be of service?")
        sessions.add_turn(sid, "user", "Is the Minecraft server still running?")
        sessions.LIVE[sid].speaking = True
    threading.Thread(target=go, daemon=True).start()


def fake_claude() -> None:
    """A pretend model so the web chat streams without an API key: calendar-ish questions go
    through a tool round first, everything else streams a canned butler reply word by word."""
    import asyncio
    from types import SimpleNamespace as NS
    from orchestrator import agent_core
    from orchestrator.config import settings

    object.__setattr__(settings, "anthropic_api_key", "dev-fake")
    reply = ("Certainly, sir. Here is what I can do from here:\n\n"
             "- **Calls**: ring anyone in the registry, now or at a set time, and report back\n"
             "- **Calendar**: read and arrange your Google Calendar\n"
             "- **Tasks**: keep the list in order\n\n"
             "### This week\n\n"
             "| Day | What |\n|---|---|\n| Mon | Call Max about the ~~old~~ new invoice |\n"
             "| Fri | Briefing at 07:30 |\n\n"
             "> A butler's work is never quite done.\n\n"
             "Just say the word. `ANTHROPIC_API_KEY` is a fake one in this preview, naturally.")

    class Stream:
        def __init__(self, text, tool, delay=0.0):
            self.text, self.tool, self.delay = text, tool, delay

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            return self._events()

        async def _events(self):
            await asyncio.sleep(self.delay)  # time to first token
            for i, word in enumerate(self.text.split(" ")):
                await asyncio.sleep(0.05)
                yield NS(type="text", text=(" " if i else "") + word)

        async def get_final_message(self):
            content = [NS(type="text", text=self.text)]
            if self.tool:
                await asyncio.sleep(1.5)
                content.append(NS(type="tool_use", id="tu1", name="current_time", input={}))
            return NS(stop_reason="tool_use" if self.tool else "end_turn", content=content, usage=None)

    class Messages:
        def stream(self, **kw):
            last = kw["messages"][-1]["content"]
            if isinstance(last, str) and "calendar" in last.lower():
                return Stream("Let me have a look.", tool=True)
            if isinstance(last, list):
                return Stream("Nothing on the calendar tomorrow, sir. A rare quiet day.", tool=False, delay=2.0)
            return Stream(reply, tool=False)

        async def create(self, **kw):
            async with self.stream(**kw) as st:
                return await st.get_final_message()

    class Models:
        async def retrieve(self, model):
            return NS(id=model)

    agent_core._client = NS(messages=Messages(), models=Models())


if __name__ == "__main__":
    seed()
    fake_claude()
    seed_live_call()
    uvicorn.run("orchestrator.main:app", host="127.0.0.1", port=8765)
