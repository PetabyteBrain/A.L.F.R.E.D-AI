"""Local dashboard preview with demo data and no API keys.

    python -m tests.devserver      -> http://127.0.0.1:8765  (login: admin / devpassword123, needs 2FA;
                                      cofounder / devpassword123 without)
"""
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "data-dev"
os.environ.setdefault("DB_PATH", str(root / "alfred.db"))
os.environ.setdefault("STORAGE_ROOT", str(root / "storage"))
os.environ.setdefault("DASHBOARD_COOKIE_SECURE", "false")
os.environ.setdefault("DASHBOARD_SESSION_SECRET", "dev-only-secret")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "c9AdNPyDtMmOD7TdD6eFq1tXZNs4myIKk1ZlyADgXdE=")  # dev-only key (2FA secrets, Google tokens)
os.environ.setdefault("ADMIN_DISPLAY_NAME", "Erika Mustermann")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "100000001")
# A fake, local NAS (data-dev/nas) so the Notes tab works without touching the real one.
os.environ.setdefault("NAS_HOST", "dev-nas")
os.environ.setdefault("NAS_USER", "dev")
os.environ.setdefault("NAS_PASSWORD", "dev")
os.environ.setdefault("NAS_COMPANY_TODO", "To-Do.xlsx")
# Email "configured" for the Mail tab; nothing is ever sent (fake_mail swaps the transport).
os.environ.setdefault("MAIL_ADDRESS", "alfred@mail.example.test")
os.environ.setdefault("SMTP_HOST", "smtp.invalid")
os.environ.setdefault("SMTP_USER", "alfred@mail.example.test")
os.environ.setdefault("SMTP_PASSWORD", "dev")
os.environ.setdefault("COMPANY_NAME", "Acme")

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

    class PlanStream(Stream):                  # Files tab "Tidy up": a small demo plan
        def __init__(self, structure):
            super().__init__("", tool=False)
            self.steps = ([{"op": "folder", "path": "Travel"}, {"op": "move", "from": "Lists/Packing.md", "to": "Travel/Packing.md"},
                           {"op": "move", "from": "Notes/Gone.md", "to": "Travel/Gone.md"}] if structure else
                          [{"op": "write", "path": "Notes/Projects/Ideas.md", "text": "# Ideas\n\n- Garden shed\n- Alps trip\n"}])

        async def get_final_message(self):
            await asyncio.sleep(1.0)
            return NS(stop_reason="tool_use", usage=None, content=[NS(type="tool_use", id="tp1", name="propose_plan", input={
                "summary": "Demo plan from the preview server.", "steps": self.steps})])

    class Messages:
        def stream(self, **kw):
            if any(t.get("name") == "propose_plan" for t in kw.get("tools", [])):
                return PlanStream("FOLDER STRUCTURE" in kw.get("system", ""))
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


def fake_nas() -> None:
    from orchestrator import nas, company_todo
    from tests.fake_nas import FakeSMB, todo_workbook
    base = root / "nas"
    for share in ("company", "Alfred_Personal"):
        (base / share).mkdir(parents=True, exist_ok=True)
    if not (base / "company" / "To-Do.xlsx").exists():
        todo_workbook(base / "company" / "To-Do.xlsx")
    fake = FakeSMB(base)
    from orchestrator import nas_files, nas_search
    for mod in (nas, company_todo, nas_files, nas_search):
        mod._smb = lambda: fake


def fake_mail() -> None:
    """Email 'configured' with a transport that only logs, plus a few demo messages and an escalation."""
    import json
    import logging
    from orchestrator import mail
    from orchestrator.identity import registry as reg

    async def health():
        return True, "SMTP login ok as alfred@mail.example.test (demo)"
    mail.transport = lambda msg, rcpts: logging.getLogger("devmail").info("Would send %r to %s", msg["Subject"], rcpts)
    mail.health = health
    co = db.query_one("SELECT user_id FROM user_identifiers WHERE type = 'dashboard' AND value = 'cofounder'")
    if not co:                                       # a second dashboard login without 2FA, for previews
        cid = reg.add_user("Max Cofounder", "cofounder", notes="dev preview")
        reg.upsert_identifier(cid, "dashboard", "cofounder", reg.hash_password("devpassword123"))
    if not db.query_one("SELECT 1 FROM email_attachments LIMIT 1"):   # an inquiry with real attachments
        from email.message import EmailMessage
        from orchestrator import mail_attachments
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "Anna Beispiel <anna@shop.example>", "alfred@mail.example.test", "Plans for our shop"
        m.set_content("Good day,\n\nAttached are the floor plan and our logo. Could you build the shop by May?\n\nAnna")
        m.add_attachment(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
                         b"trailer<</Root 1 0 R>>\n%%EOF", maintype="application", subtype="pdf", filename="floor-plan.pdf")
        m.add_attachment("Item,Price\nShelf,120\nCounter,900\n".encode(), maintype="text", subtype="csv",
                         filename="furniture.csv")
        m.add_attachment(b"MZ\x90\x00fake", maintype="application", subtype="octet-stream", filename="viewer.exe")
        rid = mail.record(direction="in", kind="inquiry", status="done", scope="company", from_addr="anna@shop.example",
                          from_name="Anna Beispiel", to=["alfred@mail.example.test"], subject="Plans for our shop",
                          body_text="Attached are the floor plan and our logo. Could you build the shop by May?",
                          own_text="Attached are the floor plan and our logo. Could you build the shop by May?",
                          auth={"spf": "pass", "dkim": True, "dmarc": True, "spam": False},
                          summary="passed to Erika Mustermann: Wants an online shop by May; sent a floor plan.")
        raw = m.as_bytes()
        mail.spool(rid, raw)
        from orchestrator.mail_inbound import parse
        mail_attachments.index(rid, parse(raw).attachments)
    if not db.query_one("SELECT 1 FROM crm_companies LIMIT 1"):          # CRM demo (roadmap #58)
        from orchestrator import crm
        boss = reg.get_person(db.query_one("SELECT user_id FROM user_identifiers WHERE type = 'dashboard' "
                                           "AND value = 'cofounder'")["user_id"])
        shop = crm.save(boss, "company", {"name": "Beispiel Shop GmbH", "status": "prospect", "industry": "Retail",
                                          "website": "shop.example", "email": "anna@shop.example",
                                          "phone": "044 000 00 10", "address": "Musterstrasse 1\n8000 Zürich",
                                          "tags": ["web", "shop"], "custom": {"Budget": "CHF 15k"},
                                          "source": "email inquiry"}, force=True)["record"]
        anna = crm.save(boss, "contact", {"first_name": "Anna", "last_name": "Beispiel", "title": "Owner",
                                          "email": "anna@shop.example", "phone": "079 000 00 11",
                                          "company_id": shop["id"]}, force=True)["record"]
        crm.log(boss, "contact", anna["id"], "call", "First call: wants an online shop by May",
                "Has a floor plan and logo; budget around 15k. Send an offer next week.")
        crm.log(boss, "company", shop["id"], "meeting", "Visit at the shop", "Measured the shop; photos in Files.")
        for name, status, industry in (("Café Muster", "client", "Restaurant"), ("Atelier Nord", "lead", "Design"),
                                       ("Bäckerei Sonne", "lead", "Bakery"), ("Druckerei Beispiel AG", "supplier", "Printing"),
                                       ("Studio Partner", "partner", "Agency")):
            crm.save(boss, "company", {"name": name, "status": status, "industry": industry}, force=True)
    if not db.query_one("SELECT 1 FROM crm_deals LIMIT 1"):              # pipeline demo (#58 stage 2)
        from datetime import date, timedelta
        from orchestrator import crm_deals, crm_leads
        boss = reg.get_person(db.query_one("SELECT user_id FROM user_identifiers WHERE type = 'dashboard' "
                                           "AND value = 'cofounder'")["user_id"])
        cos = {r["name"]: r["id"] for r in db.query("SELECT id, name FROM crm_companies")}
        st = {s["name"]: s["id"] for s in crm_deals.stages()}
        soon = lambda d: (date.today() + timedelta(days=d)).isoformat()  # noqa: E731
        for title, co, stage, value, close, nxt in (
                ("Online shop", "Beispiel Shop GmbH", "Proposal", 15000, soon(20), "Send offer v2"),
                ("Menu website", "Café Muster", "Negotiation", 4800, soon(9), "Call about the price"),
                ("Brand refresh", "Atelier Nord", "New", 9000, soon(45), ""),
                ("Ordering app", "Bäckerei Sonne", "Qualified", 22000, soon(-3), "Book a workshop"),
                ("Print catalogue", "Druckerei Beispiel AG", "Won", 3500, soon(-10), "")):
            d = crm_deals.save(boss, {"title": title, "company_id": cos.get(co), "stage_id": st[stage], "value": value,
                                      "expected_close": close, "next_step": nxt,
                                      "next_step_at": soon(2) if nxt else None})["record"]
        crm_leads.add_market([{"osm_id": "node/demo1", "name": "Velo Werkstatt Muster", "category": "Bicycle shop",
                               "street": "Seestrasse 4", "postcode": "6000", "town": "Luzern", "score": 8,
                               "reasons": ["no website", "good reviews"]},
                              {"osm_id": "node/demo2", "name": "Coiffeur Beispiel", "category": "Hairdresser",
                               "street": "Bahnhofplatz 2", "postcode": "6003", "town": "Luzern", "score": 6,
                               "website": "http://coiffeur.example", "reasons": ["website not mobile-friendly"]}],
                             "Luzern, Switzerland", date.today().isoformat())
    if db.query_one("SELECT 1 FROM emails LIMIT 1"):
        return
    admin = db.query_one("SELECT id FROM users WHERE role='admin'")["id"]
    if not reg.email_for(admin):
        reg.add_identifier(admin, "email", "erika@home.example", verified=True)
    q = mail.record(direction="in", kind="inquiry", status="done", scope="company", from_addr="max@client.example",
                    from_name="Max Mustermann", to=["alfred@mail.example.test"], subject="Quote for an online shop",
                    body_text="Good day,\n\nWe sell handmade furniture and need an online shop with TWINT by spring. "
                              "Could you send us a quote?\n\nKind regards\nMax Mustermann\n\nOn Mon, someone wrote:\n> old",
                    own_text="Good day,\n\nWe sell handmade furniture and need an online shop with TWINT by spring. "
                             "Could you send us a quote?\n\nKind regards\nMax Mustermann",
                    auth={"spf": "pass", "dkim": True, "dmarc": True, "spam": False},
                    summary="passed to Erika Mustermann: Wants a quote for an online shop with TWINT by spring.")
    mail.record(direction="out", kind="auto_reply", status="sent", scope="company", from_addr="alfred@mail.example.test",
                from_name="Alfred", to=["max@client.example"], subject="Re: Quote for an online shop",
                body_text="Dear Mr Mustermann,\n\nThank you. I've passed this to the team, who will get back to you.",
                thread_id=q)
    db.execute("INSERT INTO escalations(source, email_id, requester, topic, summary, urgency, notified, created_at) "
               "VALUES ('email', ?, 'Max Mustermann <max@client.example>', 'Quote for an online shop', "
               "'Wants a quote for an online shop with TWINT by spring.', 'high', ?, ?)",
               (q, json.dumps([{"user_id": admin, "name": "Erika Mustermann", "via": "telegram"}]), db.now_iso()))
    mail.record(direction="in", kind="request", status="done", scope="personal", user_id=admin,
                from_addr="erika@home.example", from_name="Erika Mustermann", to=["alfred@mail.example.test"],
                subject="Add the dentist to my calendar",
                body_text="Dentist on Thursday at 10:00 please, with a reminder the day before.",
                own_text="Dentist on Thursday at 10:00 please, with a reminder the day before.",
                auth={"spf": "pass", "dkim": True, "dmarc": True, "spam": False},
                summary="Dear Erika, done: the dentist is in your calendar for Thursday 10:00, reminder Wednesday.")
    mail.record(direction="in", kind="spam", status="ignored", scope="company", from_addr="win@prize.example",
                subject="You have won", body_text="Click here", summary="marked as spam by the mail provider",
                auth={"spf": "fail", "dkim": False, "dmarc": False, "spam": True})


if __name__ == "__main__":
    seed()
    fake_nas()
    fake_claude()
    fake_mail()
    seed_live_call()
    uvicorn.run("orchestrator.main:app", host="127.0.0.1", port=8765)
