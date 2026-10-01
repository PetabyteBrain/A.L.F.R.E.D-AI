"""Offline smoke tests — no API keys needed.  Run: pytest -q"""
from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="alfred-test-"))
os.environ.update({
    "DB_PATH": str(_tmp / "alfred.db"),
    "STORAGE_ROOT": str(_tmp / "storage"),
    "ADMIN_DISPLAY_NAME": "Test Admin",
    "ADMIN_TELEGRAM_ID": "111",
    "ADMIN_PHONE": "079 000 01 00",
    "DASHBOARD_ADMIN_USERNAME": "admin",
    "DASHBOARD_SESSION_SECRET": "test-secret",
    "DASHBOARD_COOKIE_SECURE": "false",
    "TWILIO_VALIDATE_SIGNATURE": "false",
    "PUBLIC_BASE_URL": "https://alfred.example.test",
    "ANTHROPIC_API_KEY": "",
    "TELEGRAM_BOT_TOKEN": "",
    "QUIET_HOURS": "off",          # tests must not depend on the time of day; quiet hours have their own test
})

from orchestrator import db, sessions  # noqa: E402
from orchestrator.identity import registry  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    db.init_db()
    registry.bootstrap_admin()
    admin = db.query_one("SELECT id FROM users WHERE role='admin'")["id"]
    registry.upsert_identifier(admin, "dashboard", "admin", registry.hash_password("correct horse battery"))
    friend = registry.add_user("Friend Person", "friend")
    registry.add_identifier(friend, "telegram", "22222222", verified=True)
    registry.add_identifier(friend, "phone", "+41 78 000 00 00", verified=True)
    yield


def test_resolution_and_normalisation():
    assert registry.normalize_phone("079 000 01 00") == "+41790000100"
    assert registry.normalize_phone("0041790000100") == "+41790000100"
    p = registry.resolve("call", "phone", "+41790000100")
    assert p and p.role == "admin"
    assert registry.resolve("telegram", "telegram", "999") is None
    assert db.query_one("SELECT COUNT(*) n FROM access_attempts WHERE resolved=0")["n"] >= 1


def test_friend_has_no_dashboard():
    friend = registry.lookup("telegram", "22222222")
    assert friend and not registry.role_allows_channel(friend.role, "dashboard")


def test_tool_role_gating():
    from orchestrator.tools import registry as tools
    tools.load()
    admin_names = {t["name"] for t in tools.tools_for("admin")}
    friend_names = {t["name"] for t in tools.tools_for("friend")}
    assert "current_time" in friend_names
    assert "recent_activity" in admin_names and "recent_activity" not in friend_names
    assert "add_task" in friend_names                     # own tasks: everyone; others: admin/cofounder
    assert not any(n.startswith("n8n_") for n in friend_names)


@pytest.mark.asyncio
async def test_tool_execute_blocks_unpermitted():
    from orchestrator.tools import registry as tools
    friend = registry.lookup("telegram", "22222222")
    out, err = await tools.execute("recent_activity", {}, tools.ToolContext(friend, None, "telegram"))
    assert err and "not available" in out


def test_history_scoped_per_channel_and_user():
    admin = registry.lookup("telegram", "111")
    s1 = sessions.start_session(admin.id, "telegram")
    sessions.add_turn(s1, "assistant", "orphan assistant line")  # must be trimmed from the front
    sessions.add_turn(s1, "user", "hello")
    sessions.add_turn(s1, "assistant", "hi there")
    s2 = sessions.start_session(admin.id, "call")
    sessions.add_turn(s2, "user", "call only")
    h = sessions.history(admin.id, "telegram")
    assert h[0] == {"role": "user", "content": "hello"}
    assert all("call only" not in m["content"] for m in h)
    sessions.end_session(s1)
    sessions.end_session(s2)
    from orchestrator.storage import recordings
    assert (recordings.session_dir(s1) / "transcript.json").exists()


def test_call_recorder_stereo_wav():
    from orchestrator.storage.recordings import CallRecorder, ULAW_TO_PCM16
    assert ULAW_TO_PCM16[0xFF] == 0 and ULAW_TO_PCM16[0x7F] == 0
    admin = registry.lookup("telegram", "111")
    sid = sessions.start_session(admin.id, "call")
    rec = CallRecorder(sid)
    rec.agent_audio(bytes([0x00]) * 240)
    for _ in range(10):
        rec.caller_audio(bytes([0x80]) * 160)
    dur = rec.close()
    assert abs(dur - 0.2) < 1e-6
    with wave.open(str(rec.path)) as w:
        assert w.getnchannels() == 2 and w.getframerate() == 8000 and w.getnframes() == 1600


CSRF = {"X-Requested-With": "alfred"}


def test_http_surface():
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    with TestClient(app, base_url="http://testserver") as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/api/me").status_code == 401
        # CSRF: state-changing calls without the custom header are refused outright
        assert c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).status_code == 403
        assert c.post("/api/login", json={"username": "admin", "password": "nope"}, headers=CSRF).status_code == 401
        r = c.post("/api/login", json={"username": "ADMIN", "password": "correct horse battery"}, headers=CSRF)
        assert r.status_code == 200, r.text
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert c.get("/api/me").json()["role"] == "admin"
        for path in ("/api/overview", "/api/sessions/active", "/api/sessions", "/api/recordings/tree",
                     "/api/tasks", "/api/activity", "/api/health", "/api/people", "/api/access-attempts"):
            assert c.get(path).status_code == 200, path
        # input validation
        assert c.get("/api/sessions?limit=-1").status_code == 422
        assert c.get("/api/sessions?date=2026-01-01' OR 1=1").status_code == 422
        assert c.post("/api/tasks", json={"title": "x", "evil": 1}, headers=CSRF).status_code == 422
        assert c.post("/api/tasks", json={"title": "Water plants", "schedule": "daily"}, headers=CSRF).status_code == 200
        # cross-origin write blocked even with the header
        bad = dict(CSRF, Origin="https://evil.example")
        assert c.post("/api/tasks", json={"title": "pwn"}, headers=bad).status_code == 403
        rec = db.query_one("SELECT id FROM recordings LIMIT 1")
        a = c.get(f"/api/recordings/{rec['id']}/audio")
        assert a.status_code == 200 and a.content[:4] == b"RIFF"
        # security headers
        h = c.get("/api/me").headers
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"

        # Revoking sessions (password change / disable) logs the browser out immediately.
        admin_id = c.get("/api/me").json()["id"]
        db.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (admin_id,))
        assert c.get("/api/me").status_code == 401

        # Logout invalidates the token server-side, not just the cookie.
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        from dashboard.api.security import COOKIE_NAME
        token = c.cookies.get(COOKIE_NAME)
        assert c.post("/api/logout", headers=CSRF).status_code == 200
        c.cookies.set(COOKIE_NAME, token)
        assert c.get("/api/me").status_code == 401

        # Unknown caller -> rejected silently; ElevenLabs missing -> known caller also rejected.
        r = c.post("/twilio/voice", data={"From": "+15550000000", "CallSid": "CA1"})
        assert "<Reject" in r.text
        # n8n callback is invisible without the secret
        assert c.post("/n8n/callback", json={}).status_code == 404


def test_friend_cannot_use_dashboard_and_login_is_throttled():
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    friend = registry.lookup("telegram", "22222222")
    registry.add_identifier(friend.id, "dashboard", "friend", registry.hash_password("friendpassword1"))
    with TestClient(app) as c:
        # correct password, but the friend role has no dashboard access
        assert c.post("/api/login", json={"username": "friend", "password": "friendpassword1"}, headers=CSRF).status_code == 401
        for _ in range(5):
            c.post("/api/login", json={"username": "victim", "password": "guess"}, headers=CSRF)
        assert c.post("/api/login", json={"username": "victim", "password": "guess"}, headers=CSRF).status_code == 429


def test_proxy_headers_only_trusted_from_proxy():
    from types import SimpleNamespace as NS
    from dashboard.api.security import client_ip
    spoof = NS(client=NS(host="192.0.2.50"), headers={"cf-connecting-ip": "1.2.3.4"})
    assert client_ip(spoof) == "192.0.2.50"
    proxied = NS(client=NS(host="100.64.0.2"), headers={"cf-connecting-ip": "1.2.3.4"})
    assert client_ip(proxied) == "1.2.3.4"


@pytest.mark.asyncio
async def test_agent_tool_loop_with_fake_claude(monkeypatch):
    """Tool round-trip through agent_core with a stubbed Claude client."""
    from types import SimpleNamespace as NS
    from orchestrator import agent_core
    from orchestrator.config import settings

    calls = []

    class FakeMessages:
        async def create(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                return NS(stop_reason="tool_use", content=[
                    NS(type="text", text="Let me check."),
                    NS(type="tool_use", id="tu_1", name="current_time", input={}),
                ])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="It is tea time.")])

    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    monkeypatch.setattr(type(settings), "anthropic_api_key", property(lambda self: "sk-test"), raising=False)

    friend = registry.lookup("telegram", "22222222")
    sid = sessions.telegram_session_for(friend.id, "22222222")
    reply = await agent_core.respond(friend, "telegram", sid, "what time is it?")
    assert reply == "It is tea time."
    assert calls[0]["model"] == "claude-haiku-4-5"
    assert {t["name"] for t in calls[0]["tools"]} >= {"current_time"}
    assert not any(t["name"].startswith("n8n_") for t in calls[0]["tools"])
    second = calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and second[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert db.query_one("SELECT tool_name FROM tool_calls WHERE session_id=?", (sid,))["tool_name"] == "current_time"
    turns = sessions.session_turns(sid)
    assert [t["speaker"] for t in turns][-2:] == ["user", "assistant"]


def test_call_media_stream_end_to_end(monkeypatch):
    """Twilio webhook -> media stream WS -> fake STT -> fake agent -> fake TTS -> back to Twilio."""
    import base64, json, dataclasses
    from fastapi.testclient import TestClient
    from orchestrator import call_handler, agent_core
    from orchestrator.config import settings
    from orchestrator.stt_tts.base import Provider, STTStream, TTS

    class FakeSTT(STTStream):
        async def start(self): self.n = 0
        async def send_audio(self, b):
            self.n += len(b)
            if self.n == 1600:
                await self.on_final("what's up")
        async def close(self): pass

    class FakeTTS(TTS):
        async def stream(self, text):
            yield bytes([0x10]) * 320

    fake_settings = dataclasses.replace(settings, elevenlabs_api_key="x", elevenlabs_voice_id="v")
    monkeypatch.setattr(call_handler, "settings", fake_settings)
    monkeypatch.setattr(call_handler, "get_provider", lambda: Provider("fake", FakeSTT, FakeTTS()))

    async def fake_respond(person, channel, sid, text, **kw):
        sessions.add_turn(sid, "user", text)
        sessions.add_turn(sid, "assistant", "all good")
        return "all good"
    monkeypatch.setattr(agent_core, "respond", fake_respond)

    from orchestrator.main import app
    with TestClient(app) as c:
        r = c.post("/twilio/voice", data={"From": "+41790000100", "CallSid": "CA42"})
        assert "<Stream url=\"wss://alfred.example.test/twilio/stream\"" in r.text, r.text
        import re
        sid = re.search(r'name="session_id" value="([^"]+)"', r.text).group(1)
        token = re.search(r'name="token" value="([^"]+)"', r.text).group(1)

        with c.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({"event": "connected"}))
            ws.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA42",
                                     "customParameters": {"session_id": sid, "token": token}}}))
            got = [json.loads(ws.receive_text()) for _ in range(2)]  # greeting media + mark
            assert got[0]["event"] == "media" and got[1]["event"] == "mark"
            ws.send_text(json.dumps({"event": "mark", "mark": {"name": got[1]["mark"]["name"]}}))
            frame = base64.b64encode(bytes([0xFF]) * 160).decode()
            for _ in range(10):
                ws.send_text(json.dumps({"event": "media", "media": {"track": "inbound", "payload": frame}}))
            reply = [json.loads(ws.receive_text()) for _ in range(2)]
            assert reply[0]["event"] == "media" and reply[1]["event"] == "mark"
            ws.send_text(json.dumps({"event": "stop"}))

    s = db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))
    assert s["status"] == "ended"
    texts = [t["text"] for t in sessions.session_turns(sid)]
    assert texts[0].startswith("Good ") and ", sir. Alfred here." in texts[0] and "what's up" in texts and "all good" in texts
    assert db.query_one("SELECT duration FROM recordings WHERE session_id=?", (sid,))["duration"] == 0.2

    # A replayed token must not open a second stream.
    with TestClient(app) as c, c.websocket_connect("/twilio/stream") as ws:
        ws.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ2",
                                 "customParameters": {"session_id": sid, "token": token}}}))
        with pytest.raises(Exception):
            ws.receive_text()


def test_plain_http_visits_are_upgraded(monkeypatch):
    import dataclasses
    from types import SimpleNamespace as NS
    from dashboard.api import security
    fake = dataclasses.replace(security.settings, cookie_secure=True)
    monkeypatch.setattr(security, "settings", fake)
    via_cf = lambda scheme, peer="100.64.0.2": NS(client=NS(host=peer), headers={"cf-visitor": f'{{"scheme":"{scheme}"}}'})
    assert security._visitor_used_plain_http(via_cf("http"))
    assert not security._visitor_used_plain_http(via_cf("https"))
    assert not security._visitor_used_plain_http(via_cf("http", peer="10.0.0.9"))  # untrusted peer can't trigger it


def test_dashboard_can_terminate_live_call(monkeypatch):
    import json, re, dataclasses
    from fastapi.testclient import TestClient
    from orchestrator import call_handler
    from orchestrator.config import settings
    from orchestrator.stt_tts.base import Provider, STTStream, TTS

    class SilentSTT(STTStream):
        async def start(self): pass
        async def send_audio(self, b): pass
        async def close(self): pass

    class FakeTTS(TTS):
        async def stream(self, text):
            yield bytes([0x10]) * 160

    monkeypatch.setattr(call_handler, "settings", dataclasses.replace(settings, elevenlabs_api_key="x", elevenlabs_voice_id="v"))
    monkeypatch.setattr(call_handler, "get_provider", lambda: Provider("fake", SilentSTT, FakeTTS()))

    from orchestrator.main import app
    with TestClient(app) as c:
        r = c.post("/twilio/voice", data={"From": "+41790000100", "CallSid": "CA99"})
        sid = re.search(r'name="session_id" value="([^"]+)"', r.text).group(1)
        token = re.search(r'name="token" value="([^"]+)"', r.text).group(1)
        with c.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ9",
                                     "customParameters": {"session_id": sid, "token": token}}}))
            ws.receive_text()  # greeting audio -> call is live
            assert sid in call_handler.ACTIVE_CALLS
            assert c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF).status_code == 200
            # friend-level or missing CSRF can't do it
            assert c.post(f"/api/sessions/{sid}/end").status_code == 403
            res = c.post(f"/api/sessions/{sid}/end", headers=CSRF)
            assert res.status_code == 200, res.text
            assert res.json()["local"] == "stream closed"
        assert sid not in call_handler.ACTIVE_CALLS
        assert db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))["status"] == "terminated"
        assert c.post(f"/api/sessions/{sid}/end", headers=CSRF).status_code == 409

        # a stuck session with no live stream can be cleared too
        stuck = sessions.start_session(registry.lookup("telegram", "111").id, "call")
        assert c.post(f"/api/sessions/{stuck}/end", headers=CSRF).json()["local"] == "session closed"


def test_place_call_endpoint_is_admin_only_and_reports_config_errors():
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    with TestClient(app) as c:
        assert c.post("/api/calls", json={"user_id": 1}, headers=CSRF).status_code == 401
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        assert c.post("/api/calls", json={"user_id": 9999}, headers=CSRF).status_code == 404
        r = c.post("/api/calls", json={"user_id": 1}, headers=CSRF)  # no Twilio in tests
        assert r.status_code == 400 and "configured" in r.json()["detail"]


def test_user_management_permissions():
    """Admin manages everyone; other roles only their own profile; guard rails hold."""
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    admin = TestClient(app)
    other_admin_session = TestClient(app)
    co = TestClient(app)
    co_second_device = TestClient(app)
    with admin, other_admin_session, co, co_second_device:
        assert admin.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF).status_code == 200
        admin_id = admin.get("/api/me").json()["id"]

        # --- create + validate
        assert admin.post("/api/people", json={"display_name": "Moritz", "role": "nope"}, headers=CSRF).status_code == 400
        moritz = admin.post("/api/people", json={"display_name": "  Max   Tester ", "role": "cofounder"}, headers=CSRF).json()
        assert moritz["display_name"] == "Max Tester" and moritz["role"] == "cofounder"
        jid = moritz["id"]
        r = admin.post(f"/api/people/{jid}/connections", json={"type": "phone", "value": "12"}, headers=CSRF)
        assert r.status_code == 400 and "Phone" in r.json()["detail"]
        moritz = admin.post(f"/api/people/{jid}/connections", json={"type": "phone", "value": "077 000 01 00"}, headers=CSRF).json()
        assert moritz["connections"][0]["value"] == "+41770000100"
        # someone else's number can't be claimed
        r = admin.post(f"/api/people/{jid}/connections", json={"type": "phone", "value": "079 000 01 00"}, headers=CSRF)
        assert r.status_code == 400 and "already linked" in r.json()["detail"]
        # dashboard type isn't a "connection"
        assert admin.post(f"/api/people/{jid}/connections", json={"type": "dashboard", "value": "x"}, headers=CSRF).status_code == 422

        # --- dashboard login for Max
        assert admin.put(f"/api/people/{jid}/login", json={"username": "max", "password": "short"}, headers=CSRF).status_code == 400
        assert admin.put(f"/api/people/{jid}/login", json={"username": "max", "password": "cofounder-pass-1"}, headers=CSRF).status_code == 200
        assert co.post("/api/login", json={"username": "max", "password": "cofounder-pass-1"}, headers=CSRF).status_code == 200
        assert co_second_device.post("/api/login", json={"username": "max", "password": "cofounder-pass-1"}, headers=CSRF).status_code == 200

        # --- a cofounder can't touch the registry
        assert co.get("/api/people").status_code == 403
        assert co.patch(f"/api/people/{admin_id}", json={"role": "friend"}, headers=CSRF).status_code == 403
        assert co.post("/api/people", json={"display_name": "X", "role": "admin"}, headers=CSRF).status_code == 403
        admin_phone = next(c for c in admin.get("/api/people").json() if c["id"] == admin_id)["connections"][0]["id"]
        assert co.patch(f"/api/profile/connections/{admin_phone}", json={"value": "0790000000"}, headers=CSRF).status_code == 404
        assert co.delete(f"/api/profile/connections/{admin_phone}", headers=CSRF).status_code == 404
        # ...and can't escalate via extra fields
        assert co.patch("/api/profile", json={"display_name": "J", "role": "admin"}, headers=CSRF).status_code == 422

        # --- but can manage their own profile
        prof = co.patch("/api/profile", json={"display_name": "Max"}, headers=CSRF).json()
        assert prof["display_name"] == "Max" and "notes" not in prof
        prof = co.post("/api/profile/connections", json={"type": "email", "value": "Moritz@Example.com"}, headers=CSRF).json()
        email = next(c for c in prof["connections"] if c["type"] == "email")
        assert email["value"] == "moritz@example.com"
        prof = co.patch(f"/api/profile/connections/{email['id']}", json={"value": "max@example.com"}, headers=CSRF).json()
        assert any(c["value"] == "max@example.com" for c in prof["connections"])
        assert co.delete(f"/api/profile/connections/{email['id']}", headers=CSRF).status_code == 200

        # --- password change: needs the current one; signs out other devices, keeps this one
        assert co.post("/api/profile/password", json={"current_password": "wrong", "new_password": "another-pass-22"}, headers=CSRF).status_code == 400
        assert co.post("/api/profile/password", json={"current_password": "cofounder-pass-1", "new_password": "another-pass-22"}, headers=CSRF).status_code == 200
        assert co.get("/api/me").status_code == 200
        assert co_second_device.get("/api/me").status_code == 401

        # --- role change by admin revokes the person's sessions immediately
        assert admin.patch(f"/api/people/{jid}", json={"role": "friend"}, headers=CSRF).status_code == 200
        assert co.get("/api/me").status_code == 401

        # --- last-admin guard rails
        r = admin.patch(f"/api/people/{admin_id}", json={"role": "friend"}, headers=CSRF)
        assert r.status_code == 400 and "last active admin" in r.json()["detail"]
        assert admin.patch(f"/api/people/{admin_id}", json={"active": False}, headers=CSRF).status_code == 400
        assert admin.delete(f"/api/people/{admin_id}", headers=CSRF).status_code == 400
        assert admin.delete(f"/api/people/{admin_id}/login", headers=CSRF).status_code == 400

        # --- deleting: people with history must be disabled instead; clean ones can go
        assert admin.delete(f"/api/people/{jid}", headers=CSRF).status_code == 200
        with_history = registry.lookup("telegram", "22222222").id
        r = admin.delete(f"/api/people/{with_history}", headers=CSRF)
        assert r.status_code == 400 and "Disable" in r.json()["detail"]
        assert admin.get("/api/me").status_code == 200  # admin session untouched throughout


@pytest.mark.asyncio
async def test_call_requests_schedule_retry_and_report(monkeypatch):
    import dataclasses
    from types import SimpleNamespace as NS
    from orchestrator import scheduler, call_handler, agent_core
    from orchestrator.config import settings

    monkeypatch.setattr(scheduler, "settings", dataclasses.replace(
        settings, elevenlabs_api_key="x", elevenlabs_voice_id="v", twilio_account_sid="a",
        twilio_auth_token="b", twilio_phone_number="+41310000000"))
    placed = []

    async def fake_place(user_id, *, opening_line, reason="", requested_by=None, request_id=None, extra_system=None,
                         voicemail=None):
        sid = sessions.start_session(user_id, "call", "outbound")
        db.execute("UPDATE scheduled_calls SET session_id = ? WHERE id = ?", (sid, request_id))
        placed.append({"sid": sid, "opening": opening_line, "brief": extra_system})
        return "CA-fake"
    monkeypatch.setattr(call_handler, "place_outbound_call", fake_place)

    class FakeMessages:
        async def create(self, **kw):
            return NS(content=[NS(type="text", text="He can send the invoice on Friday.")],
                      usage=NS(input_tokens=120, output_tokens=15, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))

    admin = registry.lookup("telegram", "111")
    # the friend from setup is the call target
    registry.add_identifier(registry.lookup("telegram", "22222222").id, "phone", "078 000 01 00") \
        if not registry.phone_for(registry.lookup("telegram", "22222222").id) else None

    # --- validation
    with pytest.raises(scheduler.ScheduleError):
        scheduler.parse_when("2001-01-01T10:00")
    with pytest.raises(scheduler.ScheduleError):
        scheduler.resolve_person("Nobody Here")
    assert scheduler.resolve_person("friend").display_name == "Friend Person"  # first-name match

    # --- future call stays pending, can be cancelled only by requester/admin
    from datetime import datetime, timedelta
    later = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    row = await scheduler.request_call("Friend", requested_by=admin, purpose="Ask about Friday", when=later)
    assert row["status"] == "pending" and not placed
    friend = registry.lookup("telegram", "22222222")
    with pytest.raises(scheduler.ScheduleError):
        scheduler.cancel(row["id"], friend)
    assert scheduler.cancel(row["id"], admin)["status"] == "cancelled"

    # --- immediate call dials right away with a brief on the requester's behalf
    row = await scheduler.request_call("Friend Person", requested_by=admin, purpose="Ask if he can send the invoice")
    assert row["status"] == "dialing" and len(placed) == 1
    assert "on behalf of Test Admin" in placed[0]["brief"] and "end_call" in placed[0]["brief"]
    assert "calling on behalf of Test" in placed[0]["opening"]

    # --- no answer -> retried later, then given up after MAX_ATTEMPTS
    sessions.end_session(placed[0]["sid"], status="failed")  # what the Twilio status webhook does
    await scheduler.on_call_finished(placed[0]["sid"], "no_answer")
    r = scheduler.get(row["id"])
    assert r["status"] == "pending" and r["attempts"] == 1
    for attempt in range(2, scheduler.MAX_ATTEMPTS + 1):
        db.execute("UPDATE scheduled_calls SET run_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", row["id"]))
        await scheduler.dispatch(row["id"])
        assert len(placed) == attempt
        sessions.end_session(placed[-1]["sid"], status="failed")
        await scheduler.on_call_finished(placed[-1]["sid"], "no_answer")
    r = scheduler.get(row["id"])
    assert r["status"] == "no_answer" and "after 3 attempt" in r["result"]

    # --- answered call -> summarised and reported to the requester
    row = await scheduler.request_call(friend.id, requested_by=admin, purpose="Ask about the invoice")
    sid = placed[-1]["sid"]
    sessions.add_turn(sid, "assistant", "Hello, calling on behalf of Test about the invoice.")
    sessions.add_turn(sid, "user", "Sure, I'll send it on Friday.")
    await scheduler.on_call_finished(sid, "ended")
    r = scheduler.get(row["id"])
    assert r["status"] == "done" and "Friday" in r["result"]
    assert db.query_one("SELECT 1 FROM activity WHERE summary LIKE 'Report to Test Admin:%Friday%'")
    assert db.query_one("SELECT SUM(output_tokens) AS o FROM api_usage WHERE kind = 'summary'")["o"] >= 15


def test_end_call_tool_only_exists_on_calls():
    from orchestrator.tools import registry as tools
    tools.load()
    assert "end_call" not in {t["name"] for t in tools.tools_for("friend", "telegram")}
    assert "end_call" in {t["name"] for t in tools.tools_for("friend", "call")}


def test_google_calendar_link_flow(monkeypatch):
    import base64, dataclasses, json as _json
    from urllib.parse import parse_qs, urlparse
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from orchestrator import google_calendar
    from orchestrator.config import settings
    from orchestrator.tools import registry as tools

    fake = dataclasses.replace(settings, google_client_id="cid.apps.googleusercontent.com", google_client_secret="sec",
                               token_encryption_key=Fernet.generate_key().decode())
    monkeypatch.setattr(google_calendar, "settings", fake)
    monkeypatch.setattr(tools, "settings", fake)
    calls = []

    class Resp:
        def __init__(self, status, data):
            self.status_code, self._d = status, data
            self.text = _json.dumps(data)
            self.content = self.text.encode()
        def json(self):
            return self._d

    id_token = "x." + base64.urlsafe_b64encode(_json.dumps({"email": "boss@example.com"}).encode()).decode().rstrip("=") + ".y"

    async def fake_http(method, url, **kw):
        calls.append((method, url, kw))
        if url == google_calendar.TOKEN_URL and kw["data"]["grant_type"] == "authorization_code":
            assert kw["data"]["code_verifier"]  # PKCE verifier sent
            return Resp(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                              "scope": f"openid email {google_calendar.EVENTS_SCOPE}", "id_token": id_token})
        if url == google_calendar.TOKEN_URL:
            return Resp(200, {"access_token": "at-2", "expires_in": 3600})
        if url.endswith("/events") and method == "GET":
            assert kw["headers"]["Authorization"] == "Bearer at-2"
            return Resp(200, {"items": [{"id": "ev1", "summary": "Dentist", "start": {"dateTime": "2026-10-01T09:00:00+02:00"},
                                         "end": {"dateTime": "2026-10-01T10:00:00+02:00"}}]})
        if url == google_calendar.REVOKE_URL:
            return Resp(200, {})
        raise AssertionError(url)
    monkeypatch.setattr(google_calendar, "_http", fake_http)

    admin = registry.lookup("telegram", "111")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        assert c.get("/api/google/status").json() == {"available": True, "linked": False}
        url = c.post("/api/google/connect", headers=CSRF).json()["url"]
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
        assert q["scope"][0] == google_calendar.SCOPES
        assert q["redirect_uri"] == ["https://alfred.example.test/api/google/callback"]
        state = q["state"][0]
        # wrong / forged state is rejected and links nothing
        r = c.get("/api/google/callback", params={"state": "forged", "code": "abc"})
        assert "google=error" in r.text and not google_calendar.is_linked(admin.id)
        # the real callback links the calendar and bounces back via a same-site page
        r = c.get("/api/google/callback", params={"state": state, "code": "abc"})
        assert "google=linked" in r.text and 'http-equiv="refresh"' in r.text
        st = c.get("/api/google/status").json()
        assert st["linked"] and st["email"] == "boss@example.com"
        # tokens are encrypted at rest
        row = db.query_one("SELECT refresh_token_enc, access_token_enc FROM google_accounts WHERE user_id = ?", (admin.id,))
        assert "rt-1" not in row["refresh_token_enc"] and "at-1" not in row["access_token_enc"]
        # state is single-use
        r = c.get("/api/google/callback", params={"state": state, "code": "abc"})
        assert "google=error" in r.text

    # calendar tools only for the linked person
    tools.load()
    assert "calendar_list_events" in {t["name"] for t in tools.tools_for("admin", "telegram", admin.id)}
    friend = registry.lookup("telegram", "22222222")
    assert "calendar_list_events" not in {t["name"] for t in tools.tools_for("friend", "telegram", friend.id)}

    # expired access token is refreshed transparently
    db.execute("UPDATE google_accounts SET access_expires_at = '2000-01-01T00:00:00+00:00' WHERE user_id = ?", (admin.id,))
    import asyncio
    events = asyncio.run(google_calendar.list_events(admin.id, "2026-10-01", "2026-10-02"))
    assert events[0]["summary"] == "Dentist"
    with pytest.raises(google_calendar.GoogleError):
        asyncio.run(google_calendar.delete_event(admin.id, "../../other-calendar"))

    # unlink revokes at Google and removes the tokens
    asyncio.run(google_calendar.disconnect(admin.id))
    assert not google_calendar.is_linked(admin.id)
    assert any(u == google_calendar.REVOKE_URL for _, u, _ in calls)


def test_usage_report_endpoint():
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    from orchestrator import usage
    from types import SimpleNamespace as NS
    usage.record_claude(NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=5000,
                           cache_creation_input_tokens=0), kind="telegram", user_id=None)
    usage.record_tts(320, user_id=None, session_id=None)
    with TestClient(app) as c:
        assert c.get("/api/usage").status_code == 401
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        r = c.get("/api/usage?days=7").json()
        tg = next(x for x in r["anthropic"] if x["kind"] == "telegram")
        assert tg["input"] >= 1000 and tg["cache_read"] >= 5000
        # 1000*1 + 200*5 + 5000*0.1 per MTok = $0.0025 for that request
        assert r["estimated_cost_usd"] >= 0.0025
        assert any(e["kind"] == "tts" and e["units"] >= 320 for e in r["elevenlabs"])
        assert r["twilio_month"] is None  # not configured in tests
        assert c.get("/api/usage?days=0").status_code == 422


@pytest.mark.asyncio
async def test_call_outcome_processed_once_when_signals_race(monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from orchestrator import scheduler, agent_core
    summaries = []

    class FakeMessages:
        async def create(self, **kw):
            summaries.append(1)
            await asyncio.sleep(0.05)
            return NS(content=[NS(type="text", text="Done.")], usage=None)
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    admin = registry.lookup("telegram", "111")
    friend = registry.lookup("telegram", "22222222")
    sid = sessions.start_session(friend.id, "call", "outbound")
    sessions.add_turn(sid, "user", "yes")
    rid = db.execute("INSERT INTO scheduled_calls(target_user_id, requested_by, run_at, purpose, status, attempts, "
                     "session_id, created_at, updated_at) VALUES (?,?,?,?, 'dialing', 1, ?, ?, ?)",
                     (friend.id, admin.id, db.now_iso(), "Ask something", sid, db.now_iso(), db.now_iso()))
    await asyncio.gather(scheduler.on_call_finished(sid, "ended"), scheduler.on_call_finished(sid, "ended"))
    assert len(summaries) == 1 and scheduler.get(rid)["status"] == "done"


def test_legal_pages_are_public():
    from fastapi.testclient import TestClient
    from orchestrator.main import app, WEB_DIR
    if not (WEB_DIR / "legal").is_dir():
        pytest.skip("dashboard not built")
    with TestClient(app) as c:
        r = c.get("/privacy-policy")
        assert r.status_code == 200 and "Limited Use" in r.text and "text/html" in r.headers["content-type"]
        r = c.get("/terms-of-service")
        assert r.status_code == 200 and "Terms of Service" in r.text
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert c.get("/legal/legal.css").status_code == 200


def test_public_homepage_and_app_route():
    from fastapi.testclient import TestClient
    from orchestrator.main import app, WEB_DIR
    if not (WEB_DIR / "home.html").is_file():
        pytest.skip("dashboard not built")
    with TestClient(app) as c:
        home = c.get("/")
        assert home.status_code == 200 and "private AI butler" in home.text and "/privacy-policy" in home.text
        assert "Limited Use" in home.text  # Google's review reads the homepage without logging in
        dash = c.get("/app")
        assert dash.status_code == 200 and '<div id="root">' in dash.text
        assert dash.headers["cache-control"] == "no-cache"


def test_google_site_verification(monkeypatch):
    import dataclasses
    from fastapi.testclient import TestClient
    from orchestrator import main
    from orchestrator.main import app, WEB_DIR
    if not (WEB_DIR / "home.html").is_file():
        pytest.skip("dashboard not built")
    fake = dataclasses.replace(main.settings, google_site_verification="AbC123_xyz-TokenValue",
                               google_site_verification_file="google0123456789abcdef.html")
    monkeypatch.setattr(main, "settings", fake)
    with TestClient(app) as c:
        assert '<meta name="google-site-verification" content="AbC123_xyz-TokenValue">' in c.get("/").text
        r = c.get("/google0123456789abcdef.html")
        assert r.status_code == 200 and r.text == "google-site-verification: google0123456789abcdef.html"
        assert c.get("/googledeadbeefdeadbeef.html").status_code == 404


@pytest.mark.asyncio
async def test_google_health_reports_per_person(monkeypatch):
    import dataclasses, json as _json
    from cryptography.fernet import Fernet
    from orchestrator import google_calendar
    from orchestrator.config import settings
    monkeypatch.setattr(google_calendar, "settings", dataclasses.replace(
        settings, google_client_id="cid", google_client_secret="sec", token_encryption_key=Fernet.generate_key().decode()))
    state = {"scope": f"openid email {google_calendar.EVENTS_SCOPE}", "refresh": 200}

    class Resp:
        def __init__(self, status, data):
            self.status_code, self._d, self.text = status, data, _json.dumps(data)
            self.content = self.text.encode()
        def json(self):
            return self._d

    async def fake_http(method, url, **kw):
        if url == google_calendar.TOKEN_URL:
            return Resp(state["refresh"], {"access_token": "at", "expires_in": 3600} if state["refresh"] == 200
                        else {"error": "invalid_grant"})
        if url == google_calendar.TOKENINFO_URL:
            return Resp(200, {"scope": state["scope"]})
        if url.endswith("/events"):
            return Resp(200, {"items": []})
        if url == google_calendar.DISCOVERY_URL:
            return Resp(200, {})
        raise AssertionError(url)
    monkeypatch.setattr(google_calendar, "_http", fake_http)

    db.execute("DELETE FROM google_accounts")
    assert await google_calendar.health() == (True, "configured · no calendars linked yet")
    admin = registry.lookup("telegram", "111")
    f = google_calendar._fernet()
    db.execute("INSERT INTO google_accounts(user_id, email, refresh_token_enc, scopes, linked_at) VALUES (?,?,?,?,?)",
               (admin.id, "a@example.com", f.encrypt(b"rt").decode(), "", db.now_iso()))
    before = db.query_one("SELECT COUNT(*) AS n FROM api_usage WHERE provider = 'google'")["n"]
    ok, detail = await google_calendar.health()
    assert ok and detail == "1/1 calendar OK"
    assert db.query_one("SELECT COUNT(*) AS n FROM api_usage WHERE provider = 'google'")["n"] == before  # not metered

    state["scope"] = "openid email"
    db.execute("UPDATE google_accounts SET access_expires_at = NULL")
    ok, detail = await google_calendar.health()
    assert not ok and "Test Admin: calendar permission was removed" in detail

    state["refresh"] = 400
    db.execute("UPDATE google_accounts SET access_expires_at = NULL")
    ok, detail = await google_calendar.health()
    assert not ok and "revoked" in detail


def test_session_state_endpoint():
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    with TestClient(app) as c:
        assert c.get("/api/session").json() == {"authenticated": False}
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        assert c.get("/api/session").json() == {"authenticated": True, "first_name": "Test"}


def test_sessions_table_migrates_to_allow_web(monkeypatch, tmp_path):
    import dataclasses, sqlite3
    from orchestrator import db as dbmod
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, display_name TEXT);
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            channel TEXT NOT NULL CHECK (channel IN ('call','telegram')),
            direction TEXT NOT NULL DEFAULT 'inbound', external_id TEXT, started_at TEXT NOT NULL,
            last_activity_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE transcripts (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            turn_index INTEGER, speaker TEXT, text TEXT, ts TEXT);
        INSERT INTO users VALUES (1, 'Old User');
        INSERT INTO sessions VALUES ('tg-1', 1, 'telegram', 'inbound', NULL, '2026-01-01', '2026-01-01', NULL, 'ended');
        INSERT INTO transcripts VALUES (1, 'tg-1', 0, 'user', 'hello', '2026-01-01');
    """)
    c.commit(); c.close()
    monkeypatch.setattr(dbmod, "settings", dataclasses.replace(dbmod.settings, db_path=old))
    dbmod.init_db()
    c = sqlite3.connect(old)
    assert "'web'" in c.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0]
    assert c.execute("SELECT channel FROM sessions WHERE id='tg-1'").fetchone()[0] == "telegram"   # data kept
    c.execute("INSERT INTO sessions VALUES ('web-1', 1, 'web', 'inbound', NULL, 'x', 'x', NULL, 'active')")
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 1
    c.close()
    dbmod.init_db()  # idempotent


def test_web_chat(monkeypatch):
    from types import SimpleNamespace as NS
    from fastapi.testclient import TestClient
    from orchestrator import agent_core
    from orchestrator.config import settings
    from orchestrator.main import app
    from dashboard.api import chat as chatmod
    seen = []

    class FakeMessages:
        async def create(self, **kw):
            seen.append(kw)
            return NS(stop_reason="end_turn", content=[NS(type="text", text=f"Reply #{len(seen)}")], usage=None)
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    monkeypatch.setattr(type(settings), "anthropic_api_key", property(lambda self: "sk-test"), raising=False)

    with TestClient(app) as c:
        assert c.post("/api/chat", json={"message": "hi"}, headers=CSRF).status_code == 401
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        assert c.post("/api/chat", json={"message": "hi"}).status_code == 403            # CSRF header required
        assert c.post("/api/chat", json={"message": ""}, headers=CSRF).status_code == 422
        assert c.post("/api/chat", json={"message": "x" * 4001}, headers=CSRF).status_code == 422
        assert c.post("/api/chat/reset", headers=CSRF).status_code == 200
        assert c.get("/api/chat/history").json() == []

        r = c.post("/api/chat", json={"message": "What can you do?"}, headers=CSRF).json()
        assert r["reply"] == "Reply #1" and r["session_id"].startswith("web-")
        # web channel: no phone-only tools, calendar-free admin still gets its normal tools
        names = {t["name"] for t in seen[0]["tools"]}
        assert "end_call" not in names and "add_task" in names
        assert "Channel: web." in seen[0]["system"][1]["text"]

        c.post("/api/chat", json={"message": "And then?"}, headers=CSRF)
        assert [m["role"] for m in seen[1]["messages"]] == ["user", "assistant", "user"]   # remembers the thread
        hist = c.get("/api/chat/history").json()
        assert [h["speaker"] for h in hist] == ["user", "assistant", "user", "assistant"]

        # new conversation: history cleared, earlier turns no longer sent to the model
        c.post("/api/chat/reset", headers=CSRF)
        assert c.get("/api/chat/history").json() == []
        c.post("/api/chat", json={"message": "Fresh start"}, headers=CSRF)
        assert [m["role"] for m in seen[-1]["messages"]] == ["user"]

        # rate limit
        monkeypatch.setattr(chatmod, "chat_throttle", chatmod.security.Throttle(window_s=300, max_failures=1))
        assert c.post("/api/chat", json={"message": "one"}, headers=CSRF).status_code == 200
        assert c.post("/api/chat", json={"message": "two"}, headers=CSRF).status_code == 429


def test_web_chat_stream(monkeypatch):
    import json
    from types import SimpleNamespace as NS
    from fastapi.testclient import TestClient
    from orchestrator import agent_core
    from orchestrator.config import settings
    from orchestrator.main import app
    rounds = []

    class Stream:
        def __init__(self, parts, final):
            self.parts, self.final = parts, final
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        def __aiter__(self):            # the SDK stream yields events; text arrives as type "text"
            return self._events()
        async def _events(self):
            for p in self.parts:
                yield NS(type="text", text=p)
        async def get_final_message(self):
            return self.final

    class FakeMessages:
        def stream(self, **kw):
            rounds.append(kw)
            if len(rounds) == 1:   # preamble, then a tool call
                return Stream(["Let me ", "check."], NS(stop_reason="tool_use", usage=None, content=[
                    NS(type="text", text="Let me check."), NS(type="tool_use", id="t1", name="current_time", input={})]))
            return Stream(["It is ", "**noon**."], NS(stop_reason="end_turn", usage=None,
                                                         content=[NS(type="text", text="It is **noon**.")]))
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    monkeypatch.setattr(type(settings), "anthropic_api_key", property(lambda self: "sk-test"), raising=False)

    def events(c, message):
        with c.stream("POST", "/api/chat/stream", json={"message": message}, headers=CSRF) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
        return [json.loads(f[6:]) for f in body.split("\n\n") if f.startswith("data: ")]

    with TestClient(app) as c:
        assert c.post("/api/chat/stream", json={"message": "hi"}, headers=CSRF).status_code == 401
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        assert c.post("/api/chat/stream", json={"message": "hi"}).status_code == 403       # CSRF header required
        assert c.post("/api/chat/stream", json={"message": ""}, headers=CSRF).status_code == 422
        c.post("/api/chat/reset", headers=CSRF)

        ev = events(c, "What time is it?")
        assert [e["type"] for e in ev] == ["delta", "delta", "tool", "delta", "delta", "done"]
        assert ev[2]["name"] == "current_time"
        assert "".join(e["text"] for e in ev[3:5]) == "It is **noon**."
        assert ev[-1]["reply"] == "It is **noon**." and ev[-1]["session_id"].startswith("web-")
        assert rounds[1]["messages"][-1]["content"][0]["type"] == "tool_result"   # tool actually ran
        hist = c.get("/api/chat/history").json()
        assert [(h["speaker"], h["text"]) for h in hist] == [("user", "What time is it?"), ("assistant", "It is **noon**.")]

        # a failing model call ends the stream with an error event, not a hung connection
        async def boom(*a, **kw):
            raise RuntimeError("kaputt")
        monkeypatch.setattr(agent_core, "respond", boom)
        assert [e["type"] for e in events(c, "again")] == ["error"]


def test_web_chat_requires_dashboard_role():
    from orchestrator.identity import registry as reg
    assert reg.role_allows_channel("admin", "web")
    assert not reg.role_allows_channel("friend", "web")


def test_every_call_opens_with_recording_notice():
    from types import SimpleNamespace as NS
    from orchestrator import call_handler as ch
    greeting = ch._greeting(NS(is_admin=False, display_name="Max Mustermann"))
    assert ch.RECORDING_NOTICE in greeting and greeting.endswith("How may I be of service?")
    assert ch.with_recording_notice("Good morning, Max. Alfred here.") == \
        f"Good morning, Max. Alfred here. {ch.RECORDING_NOTICE}"
    assert ch.with_recording_notice(greeting) == greeting            # never said twice


def test_connection_verification(monkeypatch):
    from fastapi.testclient import TestClient
    from orchestrator.identity import verification
    from orchestrator.main import app
    from orchestrator.telegram_handler import on_message  # noqa: F401  (import check)
    calls = []

    async def fake_call(number, code):
        calls.append((number, code))
    monkeypatch.setattr(verification, "_call_with_code", fake_call)

    with TestClient(app) as c:
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        me = c.post("/api/profile/connections", json={"type": "phone", "value": "079 000 01 02"}, headers=CSRF).json()
        phone = next(x for x in me["connections"] if x["value"] == "+41790000102")
        assert phone["needs_verification"] and not phone["verified"] and phone["pending_until"] is None
        # unverified: can't call in, isn't called
        assert registry.lookup("phone", "+41790000102") is None
        assert registry.resolve("call", "phone", "+41790000102") is None
        assert db.query_one("SELECT detail FROM access_attempts ORDER BY id DESC LIMIT 1")["detail"] == "not yet verified"

        r = c.post(f"/api/profile/connections/{phone['id']}/verify", headers=CSRF).json()
        assert r["method"] == "call" and "code" not in r            # a phone code is never shown on screen
        number, code = calls[-1]
        assert number == "+41790000102" and len(code) == 6
        assert c.post(f"/api/profile/connections/{phone['id']}/verify", headers=CSRF).status_code == 400  # cooldown
        wrong = "000000" if code != "000000" else "111111"
        r = c.post(f"/api/profile/connections/{phone['id']}/verify/check", json={"code": wrong}, headers=CSRF)
        assert r.status_code == 400 and "4 tries left" in r.json()["detail"]
        assert c.post(f"/api/profile/connections/{phone['id']}/verify/check", json={"code": code},
                      headers=CSRF).status_code == 200
        assert registry.lookup("phone", "+41790000102") is not None

        # changing the number makes it unverified again
        me = c.patch(f"/api/profile/connections/{phone['id']}", json={"value": "079 000 01 03"}, headers=CSRF).json()
        assert not next(x for x in me["connections"] if x["id"] == phone["id"])["verified"]
        assert registry.lookup("phone", "+41790000103") is None

        # Telegram: code shown on the dashboard, proven by sending it from that account
        me = c.post("/api/profile/connections", json={"type": "telegram", "value": "987654321"}, headers=CSRF).json()
        tg = next(x for x in me["connections"] if x["value"] == "987654321")
        r = c.post(f"/api/profile/connections/{tg['id']}/verify", headers=CSRF).json()
        assert r["method"] == "telegram" and len(r["code"]) == 6
        assert verification.claim_telegram("55555", r["code"]) == ("none", None)      # stranger: stay silent
        assert verification.claim_telegram("987654321", "hello")[0] == "wrong"
        status, who = verification.claim_telegram("987654321", f"/start {r['code']}")
        assert status == "verified" and who.is_admin
        assert registry.telegram_for(who.id) in ("111", "987654321") and registry.lookup("telegram", "987654321")
        assert verification.claim_telegram("987654321", r["code"]) == ("none", None)   # nothing pending any more

        # admin override, admin only
        me = c.post("/api/profile/connections", json={"type": "phone", "value": "079 000 01 04"}, headers=CSRF).json()
        pid = next(x for x in me["connections"] if x["value"] == "+41790000104")["id"]
        view = c.post(f"/api/connections/{pid}/mark-verified", headers=CSRF).json()
        assert next(x for x in view["connections"] if x["id"] == pid)["verified"]
        # email needs no code
        me = c.post("/api/profile/connections", json={"type": "email", "value": "max@example.com"}, headers=CSRF).json()
        assert next(x for x in me["connections"] if x["type"] == "email")["verified"]


def test_verification_rate_limit_per_connection(monkeypatch):
    from orchestrator.identity import verification
    async def fake_call(number, code):
        pass
    monkeypatch.setattr(verification, "_call_with_code", fake_call)
    monkeypatch.setattr(verification, "RESEND_AFTER", verification.timedelta(0))
    admin = registry.lookup("telegram", "111")
    iid = registry.add_identifier(admin.id, "phone", "079 000 01 05")
    import asyncio
    for _ in range(verification.MAX_SENDS_PER_CONNECTION):
        asyncio.run(verification.start(iid, admin))
    try:
        asyncio.run(verification.start(iid, admin))
        raise AssertionError("expected the daily limit")
    except registry.RegistryError as e:
        assert "Too many codes" in str(e)


def test_reminder_recurrence_keeps_local_time_across_dst():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from orchestrator import reminders
    tz = ZoneInfo("Europe/Zurich")
    # Saturday 24 Oct 2026 08:00 (CEST) -> Sunday 25 Oct 08:00 (CET, after the clocks go back)
    sat = datetime(2026, 10, 24, 8, 0, tzinfo=tz)
    nxt = reminders.next_occurrence(sat, "daily").astimezone(tz)
    assert (nxt.day, nxt.hour, nxt.minute) == (25, 8, 0)
    fri = datetime(2026, 10, 23, 8, 0, tzinfo=tz)
    assert reminders.next_occurrence(fri, "weekdays").astimezone(tz).day == 26          # skips the weekend
    jan31 = datetime(2027, 1, 31, 9, 0, tzinfo=tz)
    assert reminders.next_occurrence(jan31, "monthly").astimezone(tz).day == 28           # clamps to Feb


def test_reminders_deliver_ack_escalate_and_repeat(monkeypatch):
    import asyncio
    from datetime import timedelta
    from orchestrator import reminders, scheduler, telegram_handler
    from orchestrator.config import settings
    admin = registry.lookup("telegram", "111")
    friend = registry.lookup("telegram", "22222222")
    sent, calls = [], []

    async def fake_send(user_id, text, note=None, record=True, buttons=None, silent=None):
        sent.append((user_id, text, buttons))

    async def fake_request_call(target, *, requested_by, purpose="", when=None, opening_line=""):
        calls.append((target, purpose))
        return {}
    monkeypatch.setattr(telegram_handler, "send_to_user", fake_send)
    monkeypatch.setattr(scheduler, "request_call", fake_request_call)
    monkeypatch.setattr(type(settings), "telegram_enabled", property(lambda s: True))
    monkeypatch.setattr(type(settings), "twilio_enabled", property(lambda s: True))
    monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda s: True))
    monkeypatch.setattr(type(settings), "public_base_url", property(lambda s: "https://x.test"), raising=False)

    # friends can't set reminders for other people; admins can
    try:
        reminders.create(created_by=friend, text="x", when="2026-12-01T10:00", target_ref=admin.id)
        raise AssertionError("friend reminded someone else")
    except reminders.ReminderError:
        pass
    try:
        reminders.create(created_by=admin, text="x", when="2020-01-01T10:00")
        raise AssertionError("past time accepted")
    except reminders.ReminderError:
        pass

    soon = (reminders._now() + timedelta(minutes=5)).astimezone(reminders._tz()).strftime("%Y-%m-%dT%H:%M")
    r = reminders.create(created_by=admin, text="Water the plants", when=soon, target_ref="Friend",
                         recurrence="daily", escalate=True)
    assert r["user_id"] == friend.id and r["status"] == "pending"
    db.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (reminders._iso(reminders._now()), r["id"]))

    asyncio.run(reminders.tick())
    assert sent and sent[-1][0] == friend.id and "Water the plants" in sent[-1][1] and "(from Test)" in sent[-1][1]
    assert sent[-1][2][0][0][1] == f"rem:done:{r['id']}"
    assert reminders.get(r["id"])["status"] == "sent"
    nxt = db.query_one("SELECT * FROM reminders WHERE id != ? AND text = 'Water the plants' AND status = 'pending'",
                       (r["id"],))
    assert nxt and nxt["recurrence"] == "daily"                                    # series continues

    asyncio.run(reminders.tick())
    assert len(sent) == 1                                                          # not delivered twice
    # ignored for 10 minutes -> escalated to a phone call
    db.execute("UPDATE reminders SET sent_at = ? WHERE id = ?",
               (reminders._iso(reminders._now() - timedelta(minutes=11)), r["id"]))
    asyncio.run(reminders.tick())
    assert calls and calls[-1][0] == friend.id and "not acknowledged" in calls[-1][1]
    assert reminders.get(r["id"])["status"] == "done"

    # snooze / done from the buttons (only the recipient)
    try:
        reminders.acknowledge(nxt["id"], admin.__class__(id=999, display_name="x", role="friend"))
        raise AssertionError("stranger acknowledged")
    except reminders.ReminderError:
        pass
    s = reminders.acknowledge(nxt["id"], friend, snooze_minutes=10)
    assert s["status"] == "pending"
    reminders.cancel(nxt["id"], admin)
    assert reminders.get(nxt["id"])["status"] == "cancelled"

    # nobody reachable -> failed, not silently lost
    lonely = registry.add_user("No Channels", "friend")
    lp = registry.get_person(lonely)
    r2 = reminders.create(created_by=lp, text="Nothing", when=soon)
    db.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (reminders._iso(reminders._now()), r2["id"]))
    asyncio.run(reminders.tick())
    assert reminders.get(r2["id"])["status"] == "failed"

    # a Telegram error doesn't escalate into a phone call
    async def broken_send(*a, **kw):
        raise RuntimeError("Telegram bot is not running.")
    monkeypatch.setattr(telegram_handler, "send_to_user", broken_send)
    n_calls = len(calls)
    r3 = reminders.create(created_by=friend, text="Quiet one", when=soon)
    db.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (reminders._iso(reminders._now()), r3["id"]))
    asyncio.run(reminders.tick())
    assert reminders.get(r3["id"])["status"] == "failed" and len(calls) == n_calls


def test_reminders_api():
    from datetime import datetime, timedelta
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    soon = (datetime.now() + timedelta(days=3)).replace(hour=9, minute=30).strftime("%Y-%m-%dT%H:%M")
    with TestClient(app) as c:
        assert c.get("/api/reminders").status_code == 401
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        r = c.post("/api/reminders", json={"text": "Call the bank", "when": soon, "recurrence": "weekly"},
                   headers=CSRF)
        assert r.status_code == 200 and r.json()["due_local"].endswith("09:30")
        rid = r.json()["id"]
        assert any(x["id"] == rid for x in c.get("/api/reminders").json())
        assert c.post("/api/reminders", json={"text": "x", "when": "yesterday"}, headers=CSRF).status_code in (400, 422)
        assert c.delete(f"/api/reminders/{rid}", headers=CSRF).json()["status"] == "cancelled"


def test_daily_briefing(monkeypatch):
    import asyncio, json
    from datetime import timedelta
    from types import SimpleNamespace as NS
    from orchestrator import agent_core, briefing, reminders, telegram_handler
    from orchestrator.config import settings
    friend = registry.lookup("telegram", "22222222")
    sent, prompts = [], []

    async def fake_geo(place):
        return {"location": "Lucerne, Switzerland", "lat": 47.14, "lon": 7.25}

    async def fake_weather(lat, lon):
        return {"conditions": "light rain", "high_c": 14, "low_c": 8, "rain_chance_pct": 70, "precip_mm": 3.1}

    async def fake_send(user_id, text, note=None, record=True, buttons=None, silent=None):
        sent.append((user_id, text, note))

    class FakeMessages:
        async def create(self, **kw):
            prompts.append(kw)
            return NS(content=[NS(type="text", text="Good morning, Friend. Light rain today.")], usage=None)
    monkeypatch.setattr(briefing, "geocode", fake_geo)
    monkeypatch.setattr(briefing, "_weather", fake_weather)
    monkeypatch.setattr(telegram_handler, "send_to_user", fake_send)
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    monkeypatch.setattr(type(settings), "anthropic_api_key", property(lambda s: "sk-test"), raising=False)
    monkeypatch.setattr(type(settings), "telegram_enabled", property(lambda s: True))

    try:
        asyncio.run(briefing.update_settings(friend.id, time_local="7.30"))
        raise AssertionError("bad time accepted")
    except briefing.BriefingError:
        pass
    now = briefing._now_local()
    due = (now - timedelta(minutes=1)).strftime("%H:%M") if now.hour or now.minute else "00:00"
    s = asyncio.run(briefing.update_settings(friend.id, enabled=True, time_local=due, location="Lucerne"))
    assert s["enabled"] and s["location"].startswith("Lucerne") and s["last_sent_on"] == now.date().isoformat()
    db.execute("UPDATE briefings SET last_sent_on = NULL WHERE user_id = ?", (friend.id,))   # pretend it's due

    soon = (reminders._now() + timedelta(minutes=30)).astimezone(reminders._tz())
    if soon.date() == now.date():
        reminders.create(created_by=friend, text="Buy milk", when=soon.strftime("%Y-%m-%dT%H:%M"))
    data = asyncio.run(briefing.gather(friend))
    assert data["weather"]["conditions"] == "light rain" and "calendar_today" not in data  # no calendar linked
    assert "n8n_failed_runs_last_24h" not in data                                         # admins only

    async def run_tick():
        await briefing.tick()
        await briefing.tick()                     # claimed once: not sent twice
        await asyncio.sleep(0.05)
        await asyncio.gather(*briefing._running)
    asyncio.run(run_tick())
    assert len(sent) == 1 and sent[0][0] == friend.id and sent[0][2] == "daily briefing"
    facts = json.loads(prompts[-1]["messages"][0]["content"])
    assert facts["weather"]["place"].startswith("Lucerne") and "never invent" in prompts[-1]["system"]


def test_briefing_api():
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    with TestClient(app) as c:
        assert c.get("/api/briefing").status_code == 401
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        assert c.get("/api/briefing").json()["time_local"] == "07:30"
        r = c.put("/api/briefing", json={"time_local": "06:45", "days": "weekdays"}, headers=CSRF).json()
        assert r["time_local"] == "06:45" and r["days"] == "weekdays" and not r["enabled"]
        assert c.put("/api/briefing", json={"time_local": "25:00"}, headers=CSRF).status_code == 400
        assert c.put("/api/briefing", json={"channel": "fax"}, headers=CSRF).status_code == 422


def test_telegram_voice_note_round_trip(monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from orchestrator import agent_core, telegram_handler as th
    from orchestrator.config import settings
    from orchestrator.stt_tts import elevenlabs
    replies, seen = [], {}

    async def fake_transcribe(data, filename="voice.ogg", mime="audio/ogg"):
        assert data == b"OggS-fake"
        return "remind me to call the bank", "eng"

    async def fake_tts(text):
        seen["tts"] = text
        return b"OggS-reply"

    async def fake_respond(person, channel, session_id, text, *, extra_system=None, **kw):
        seen.update(text=text, extra=extra_system)
        return "Very good, sir. I'll remind you."
    monkeypatch.setattr(elevenlabs, "transcribe_file", fake_transcribe)
    monkeypatch.setattr(elevenlabs, "synthesize_voice_note", fake_tts)
    monkeypatch.setattr(agent_core, "respond", fake_respond)
    monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda s: True))

    class File:
        async def download_as_bytearray(self):
            return bytearray(b"OggS-fake")

    class Voice:
        duration, file_size, mime_type = 4, 9000, "audio/ogg"
        async def get_file(self):
            return File()

    class Msg:
        chat_id, text, caption, audio, video_note = 111, None, None, None, None
        voice = Voice()
        async def reply_voice(self, voice, caption=None, **kw):
            replies.append(("voice", voice, caption))
            seen["parse_mode"] = kw.get("parse_mode")
        async def reply_text(self, text, **kw):
            replies.append(("text", text))

    upd = NS(effective_message=Msg(), effective_user=NS(id=111, username="t"), effective_chat=NS(id=111, type="private"))
    asyncio.run(th.on_message(upd, None))
    assert seen["text"] == "(voice message) remind me to call the bank" and "spoken" in seen["extra"]
    assert replies == [("voice", b"OggS-reply", "Very good, sir. I'll remind you.")] and seen["parse_mode"] == "HTML"

    # a long recording is refused before anything is downloaded or transcribed
    replies.clear()
    Voice.duration = 900
    asyncio.run(th.on_message(upd, None))
    assert replies and replies[0][0] == "text" and "under 5 minutes" in replies[0][1]


def test_long_term_memory_is_private_and_reaches_the_prompt(monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from fastapi.testclient import TestClient
    from orchestrator import agent_core, memory
    from orchestrator.config import settings
    from orchestrator.main import app
    admin = registry.lookup("telegram", "111")
    friend = registry.lookup("telegram", "22222222")

    m = memory.add(admin, "Max prefers calls after 9:00.")
    for secret in ("My password is hunter22", "Card 4111 1111 1111 1111", "IBAN CH93 0076 2011 6238 5295 7"):
        try:
            memory.add(admin, secret)
            raise AssertionError(f"stored a secret: {secret}")
        except memory.MemoryNoteError:
            pass
    try:
        memory.delete(m["id"], friend)                     # can't touch someone else's notes
        raise AssertionError("friend deleted admin's note")
    except memory.MemoryNoteError:
        pass

    seen = []

    class FakeMessages:
        async def create(self, **kw):
            seen.append(kw["system"][1]["text"])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="ok")], usage=None)
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    monkeypatch.setattr(type(settings), "anthropic_api_key", property(lambda s: "sk-test"), raising=False)
    asyncio.run(agent_core._run(admin, "telegram", "s1", "hi", None))
    asyncio.run(agent_core._run(friend, "telegram", "s2", "hi", None))
    assert "Max prefers calls after 9:00." in seen[0] and f"[{m['id']}]" in seen[0]
    assert "Max" not in seen[1]                           # never in anyone else's conversation

    with TestClient(app) as c:
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        notes = c.post("/api/memory", json={"text": "Likes his coffee black."}, headers=CSRF).json()
        assert [n["text"] for n in notes][:2] == ["Likes his coffee black.", "Max prefers calls after 9:00."]
        nid = notes[0]["id"]
        assert c.patch(f"/api/memory/{nid}", json={"text": "Likes coffee black, no sugar."}, headers=CSRF).json()[0]["text"] \
            == "Likes coffee black, no sugar."
        assert c.post("/api/memory", json={"text": "pin: 1234"}, headers=CSRF).status_code == 400
        assert all(n["id"] != nid for n in c.delete(f"/api/memory/{nid}", headers=CSRF).json())


def test_split_speakable():
    from orchestrator.call_handler import split_speakable
    ready, rest = split_speakable("Certainly, sir. Mr. Smith rang at nine. And the")
    assert ready == ["Certainly, sir.", "Mr. Smith rang at nine."] and rest == "And the"
    ready, rest = split_speakable("OK. Fine")                  # too short to split off on its own
    assert ready == [] and rest == "OK. Fine"
    long = "word " * 60
    ready, rest = split_speakable(long)
    assert ready and len(ready[0]) <= 221 and (ready[0] + rest).split() == long.split()


def test_call_reply_is_spoken_sentence_by_sentence(monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from orchestrator import agent_core, call_handler
    from orchestrator.stt_tts.base import Provider, STTStream, TTS

    spoken, sent = [], []

    class FakeTTS(TTS):
        async def stream(self, text):
            spoken.append(text)
            yield b"\x10" * 160

    class WS:
        async def send_text(self, t):
            sent.append(t)

    class NoSTT(STTStream):
        async def start(self): pass
        async def send_audio(self, b): pass
        async def close(self): pass

    async def fake_respond(person, channel, sid, text, *, extra_system=None, on_event=None, **kw):
        for d in ["Let me ", "check that for you, sir. "]:
            await on_event("delta", d)
        await on_event("tool", "calendar_list_events")
        for d in ["You have two meetings", " today. The first is at ten", "."]:
            await on_event("delta", d)
        return "You have two meetings today. The first is at ten."
    monkeypatch.setattr(agent_core, "respond", fake_respond)

    person = registry.lookup("telegram", "111")
    sid = sessions.start_session(person.id, "call")
    pending = call_handler.PendingCall(sid, person.id, "tok", "Hello.")

    async def run():
        call = call_handler.CallSession(WS(), "MZ", pending, person, Provider("fake", NoSTT, FakeTTS()))
        await call._turn("what's on today")
        await asyncio.sleep(0.05)
        assert spoken == ["Let me check that for you, sir.", "You have two meetings today.", "The first is at ten."]
        assert call.speaking                                   # waiting for Twilio's last mark
        # nothing spoken before a tool -> a short filler; a barge-in drops the rest of the turn
        spoken.clear()

        async def slow_respond(person, channel, sid, text, *, on_event=None, **kw):
            await on_event("tool", "list_tasks")
            await asyncio.sleep(0.05)
            await on_event("delta", "This sentence is stale by now. ")
            return "This sentence is stale by now."
        monkeypatch.setattr(agent_core, "respond", slow_respond)
        t = asyncio.create_task(call._turn("tasks?"))
        await asyncio.sleep(0.01)
        await call.interrupt()
        await t
        await asyncio.sleep(0.05)
        assert spoken == [call_handler.FILLER]
        call.recorder.close()
    asyncio.run(run())


def test_quiet_hours_windows_and_held_calls(monkeypatch):
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace as NS
    from orchestrator import quiet, scheduler, telegram_handler as th
    from orchestrator.config import settings
    tz = ZoneInfo("Europe/Zurich")
    admin = registry.lookup("telegram", "111")
    friend = registry.lookup("telegram", "22222222")
    at = lambda h, m=0: datetime(2026, 10, 5, h, m, tzinfo=tz)  # noqa: E731
    try:
        quiet.set_default(enabled=True, start="22:00", end="07:00")
        assert quiet.quiet_until(friend.id, at(23, 30)).astimezone(tz) == datetime(2026, 10, 6, 7, 0, tzinfo=tz)
        assert quiet.quiet_until(friend.id, at(6, 59)).astimezone(tz) == at(7)
        assert quiet.quiet_until(friend.id, at(7)) is None and not quiet.is_quiet(friend.id, at(12))
        quiet.set_person(friend.id, mode="custom", start="12:00", end="14:00")        # same-day window
        assert quiet.is_quiet(friend.id, at(13)) and not quiet.is_quiet(friend.id, at(23))
        quiet.set_person(friend.id, mode="off")
        assert not quiet.is_quiet(friend.id, at(3))
        try:
            quiet.set_person(friend.id, mode="custom", start="25:00", end="07:00")
            raise AssertionError("bad time accepted")
        except quiet.QuietError:
            pass

        # calls someone else asked for wait; urgent and self-requested calls don't
        quiet.set_person(friend.id, mode="custom", start="00:00", end="23:59")        # quiet (almost) all day
        monkeypatch.setattr(type(settings), "twilio_enabled", property(lambda s: True))
        monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda s: True))
        dialed = []

        async def fake_dispatch(rid):
            dialed.append(rid)
        monkeypatch.setattr(scheduler, "dispatch", fake_dispatch)
        if quiet.is_quiet(friend.id):
            held = asyncio.run(scheduler.request_call(friend.id, requested_by=admin, purpose="Ask about the invoice"))
            assert held.get("held_for_quiet_hours") and held["status"] == "pending" and not dialed
            urgent = asyncio.run(scheduler.request_call(friend.id, requested_by=admin, purpose="x", urgent=True))
            assert not urgent.get("held_for_quiet_hours") and dialed == [urgent["id"]]
            own = asyncio.run(scheduler.request_call(friend.id, requested_by=friend))
            assert not own.get("held_for_quiet_hours")
            for r in (held, urgent, own):
                db.execute("UPDATE scheduled_calls SET status = 'cancelled' WHERE id = ?", (r["id"],))

            # automatic Telegram messages go out silently during quiet hours
            sent = []

            async def send_message(**kw):
                sent.append(kw)
            monkeypatch.setattr(th, "_app", NS(bot=NS(send_message=send_message)))
            asyncio.run(th.send_to_user(friend.id, "call report", record=False))
            asyncio.run(th.send_to_user(friend.id, "your own reminder", record=False, silent=False))
            assert sent[0]["disable_notification"] is True and sent[1]["disable_notification"] is False
    finally:
        quiet.set_default(enabled=False, start="22:00", end="07:00")
        quiet.set_person(friend.id, mode="default")


def test_task_follow_ups(monkeypatch):
    import asyncio
    from datetime import timedelta
    from orchestrator import scheduler, tasks, telegram_handler
    from orchestrator.config import settings
    admin = registry.lookup("telegram", "111")
    friend = registry.lookup("telegram", "22222222")
    sent, calls = [], []

    async def fake_send(user_id, text, note=None, record=True, buttons=None, silent=None):
        sent.append({"to": user_id, "text": text, "buttons": buttons})

    async def fake_request_call(target, *, requested_by, purpose="", when=None, opening_line="", urgent=False):
        calls.append((target, requested_by.id, purpose))
        return {}
    monkeypatch.setattr(telegram_handler, "send_to_user", fake_send)
    monkeypatch.setattr(scheduler, "request_call", fake_request_call)
    monkeypatch.setattr(type(settings), "telegram_enabled", property(lambda s: True))
    soon = (tasks._now() + timedelta(hours=1)).astimezone(tasks._tz()).strftime("%Y-%m-%dT%H:%M")

    try:
        tasks.create(created_by=friend, title="x", owner_ref=admin.id)
        raise AssertionError("friend assigned a task to someone else")
    except tasks.TaskError:
        pass
    try:
        tasks.create(created_by=admin, title="x", follow_up="telegram")
        raise AssertionError("follow-up without a due time")
    except tasks.TaskError:
        pass

    async def run():
        async def settle():
            await asyncio.sleep(0)
            await asyncio.gather(*tasks._running)

        t = tasks.create(created_by=admin, title="Send the invoice", owner_ref="Friend", due=soon, follow_up="telegram")
        assert t["owner_user_id"] == friend.id and t["follow_up_at"] == t["due_at"]
        await tasks.tick()
        assert not sent                                               # not due yet
        db.execute("UPDATE tasks SET follow_up_at = ? WHERE id = ?", (tasks._iso(tasks._now()), t["id"]))
        await tasks.tick()
        await tasks.tick()                                            # claimed: asked once
        assert len(sent) == 1 and sent[0]["to"] == friend.id and "Send the invoice" in sent[0]["text"]
        assert sent[0]["buttons"][0][1][1] == f"task:notyet:{t['id']}"
        assert tasks.get(t["id"])["follow_ups"] == 1

        # "Not yet" -> requester hears it; asked again a day later
        tasks.not_yet(t["id"], friend)
        await settle()
        assert sent[-1]["to"] == admin.id and "isn't done yet" in sent[-1]["text"]
        # done -> requester hears it, follow-ups stop
        tasks.complete(t["id"], friend, "Sent at 11:40")
        await settle()
        assert sent[-1]["to"] == admin.id and "finished" in sent[-1]["text"] and "11:40" in sent[-1]["text"]
        assert tasks.get(t["id"])["status"] == "done" and tasks.get(t["id"])["follow_up_at"] is None

        # phone follow-up goes through the call scheduler with a brief that closes the task
        c = tasks.create(created_by=admin, title="Book the venue", owner_ref=friend.id, due=soon, follow_up="call")
        db.execute("UPDATE tasks SET follow_up_at = ? WHERE id = ?", (tasks._iso(tasks._now()), c["id"]))
        await tasks.tick()
        assert calls and calls[-1][0] == friend.id and calls[-1][1] == admin.id
        assert f"complete_task with task_id {c['id']}" in calls[-1][2]

        # three follow-ups with no answer -> requester told once, then silence
        db.execute("UPDATE tasks SET follow_ups = 3, follow_up_at = NULL, last_follow_up_at = ? WHERE id = ?",
                   (tasks._iso(tasks._now() - timedelta(hours=25)), c["id"]))
        n = len(sent)
        await tasks.tick()
        await settle()
        await tasks.tick()
        await settle()
        assert len(sent) == n + 1 and "No word from" in sent[-1]["text"]
        tasks.cancel(c["id"], admin)
    asyncio.run(run())


def test_tasks_api():
    from datetime import datetime, timedelta
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    friend = registry.lookup("telegram", "22222222")
    due = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    with TestClient(app) as c:
        c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=CSRF)
        r = c.post("/api/tasks", json={"title": "Order toner", "owner_id": friend.id, "due": due, "follow_up": "telegram"},
                   headers=CSRF)
        assert r.status_code == 200 and r.json()["owner_name"] == "Friend Person"
        tid = r.json()["id"]
        assert any(t["id"] == tid for t in c.get("/api/tasks").json()["local"])
        assert c.post("/api/tasks", json={"title": "x", "follow_up": "call"}, headers=CSRF).status_code == 400
        assert c.post(f"/api/tasks/{tid}/done", json={"result": "Ordered"}, headers=CSRF).json()["status"] == "done"
        assert c.delete(f"/api/tasks/{tid}", headers=CSRF).status_code == 400          # already done


def test_jetson_speech_provider_and_fallback(monkeypatch):
    import asyncio, json
    import websockets
    from orchestrator.config import settings
    from orchestrator.stt_tts import base, jetson

    monkeypatch.setattr(type(settings), "stt_tts_provider", property(lambda s: "jetson"), raising=False)
    monkeypatch.setattr(type(settings), "speech_token", property(lambda s: "tok"), raising=False)

    async def run():
        got_audio, seen_auth = [], []

        async def handler(ws):
            seen_auth.append(ws.request.headers.get("Authorization"))
            async for msg in ws:
                got_audio.append(msg)
                if sum(len(m) for m in got_audio) >= 1600:
                    await ws.send(json.dumps({"type": "speech_start"}))
                    await ws.send(json.dumps({"type": "final", "text": "What's on today?"}))
                    break

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(type(settings), "speech_url", property(lambda s: f"http://127.0.0.1:{port}"),
                                raising=False)
            jetson.mark(True)
            assert base.active_name() == "jetson" and base.get_provider().name == "jetson"
            partials, finals = [], []

            async def on_partial(t):
                partials.append(t)

            async def on_final(t):
                finals.append(t)
            stt = jetson.JetsonSTT(on_partial, on_final)
            await stt.start()
            for _ in range(20):
                await stt.send_audio(b"\xff" * 160)          # 20 ms frames, batched to 100 ms
            await asyncio.sleep(0.2)
            await stt.close()
            assert seen_auth == ["Bearer tok"] and all(len(m) == 800 for m in got_audio)
            assert partials == [jetson.SPEAKING_PARTIAL] and finals == ["What's on today?"]
            assert len(jetson.SPEAKING_PARTIAL) >= 6                   # long enough to trigger barge-in

        # Jetson down: ElevenLabs takes over if it's configured, otherwise we keep trying the Jetson
        jetson.mark(False)
        monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda s: True))
        assert base.active_name() == "elevenlabs" and base.get_provider().name == "elevenlabs"
        monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda s: False))
        assert base.active_name() == "jetson" and settings.speech_enabled
        jetson.mark(True)
    asyncio.run(run())


def test_web_search_server_tool_pause_turn_and_metering(monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from orchestrator import agent_core
    from orchestrator.config import settings
    from orchestrator.tools import registry as tools
    tools.load()
    ws = [t for t in tools.tools_for("friend", "telegram") if t["name"] == "web_search"]
    assert ws == [{"name": "web_search", "type": "web_search_20250305", "max_uses": 3,
                   "user_location": {"type": "approximate", "country": "CH", "timezone": "Europe/Zurich"}}]

    calls = []
    usage1 = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                server_tool_use=NS(web_search_requests=2))
    usage2 = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                server_tool_use=None)

    class Stream:
        def __init__(self, events, final):
            self.events, self.final = events, final
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        def __aiter__(self):
            return self._gen()
        async def _gen(self):
            for e in self.events:
                yield e
        async def get_final_message(self):
            return self.final

    class FakeMessages:
        def stream(self, **kw):
            calls.append([m["role"] for m in kw["messages"]])
            if len(calls) == 1:   # searches, then the API pauses the turn
                return Stream([NS(type="text", text="Let me look. "),
                               NS(type="content_block_start", content_block=NS(type="server_tool_use", name="web_search"))],
                              NS(stop_reason="pause_turn", usage=usage1,
                                 content=[NS(type="text", text="Let me look."), NS(type="server_tool_use", name="web_search")]))
            return Stream([NS(type="text", text="The museum opens at 10.")],
                          NS(stop_reason="end_turn", usage=usage2, content=[NS(type="text", text="The museum opens at 10.")]))
    monkeypatch.setattr(agent_core, "_client", NS(messages=FakeMessages()))
    monkeypatch.setattr(type(settings), "anthropic_api_key", property(lambda s: "sk-test"), raising=False)
    events = []

    async def on_event(kind, data):
        events.append((kind, data))
    friend = registry.lookup("telegram", "22222222")
    before = db.query_one("SELECT COALESCE(SUM(units), 0) AS n FROM api_usage WHERE kind = 'web_search'")["n"]
    reply = asyncio.run(agent_core._run(friend, "web", "s-ws", "When does the museum open?", None, on_event))
    assert reply == "Let me look. The museum opens at 10."
    assert calls == [["user"], ["user", "assistant"]]              # resumed with no extra user message
    assert ("tool", "web_search") in events
    after = db.query_one("SELECT COALESCE(SUM(units), 0) AS n FROM api_usage WHERE kind = 'web_search'")["n"]
    assert after - before == 2


def test_voicemail_detection(monkeypatch):
    import asyncio
    from orchestrator import agent_core, call_handler, scheduler
    from orchestrator.stt_tts.base import Provider, STTStream, TTS

    spoken, turns = [], []

    class FakeTTS(TTS):
        async def stream(self, text):
            spoken.append(text)
            yield b"\x10" * 160

    class NoSTT(STTStream):
        async def start(self): pass
        async def send_audio(self, b): pass
        async def close(self): pass

    class WS:
        async def send_text(self, t):
            pass

    async def fake_respond(person, channel, sid, text, **kw):
        turns.append(text)
        return "Lovely to hear from you."

    async def fake_vm(person, info):
        return f"Hello Friend, this is Alfred calling on behalf of {info['requester']}. Goodbye."
    monkeypatch.setattr(agent_core, "respond", fake_respond)
    monkeypatch.setattr(call_handler, "voicemail_message", fake_vm)
    hung_up = []

    async def fake_hangup(self):
        hung_up.append(self.session_id)
    monkeypatch.setattr(call_handler.CallSession, "_hangup", fake_hangup)
    friend = registry.lookup("telegram", "22222222")

    def make_call():
        sid = sessions.start_session(friend.id, "call", "outbound")
        pending = call_handler.PendingCall(sid, friend.id, "tok", "Good day.", amd=True,
                                           voicemail={"target": friend.display_name, "requester": "Test Admin",
                                                      "purpose": "Ask about the invoice"})
        return call_handler.CallSession(WS(), "MZ", pending, friend, Provider("fake", NoSTT, FakeTTS()))

    async def run():
        # a mailbox: its greeting is NOT answered; after the beep we leave a message and hang up
        call = make_call()
        await call.start()
        await call._on_final("Hi, you've reached Friend, please leave a message after the tone.")
        await asyncio.sleep(0.05)
        assert turns == []                                    # held while Twilio decides
        await call.on_amd("machine_end_beep")
        await asyncio.sleep(0.05)
        assert spoken[-1].startswith("Hello Friend, this is Alfred calling on behalf of Test Admin")
        assert call.outcome == "voicemail" and call.hangup_requested
        call.on_mark(call._pending_mark)                      # message played -> hang up
        await asyncio.sleep(0.05)
        assert hung_up == [call.session_id] and turns == []
        await call.close()

        # a person: what they said while AMD ran is answered once the verdict is "human"
        spoken.clear()
        call = make_call()
        await call.start()
        await call._on_final("Hello?")
        await asyncio.sleep(0.05)
        assert turns == []
        await call.on_amd("human")
        await asyncio.sleep(0.05)
        assert turns == ["Hello?"] and call.outcome is None
        await call.close()
    asyncio.run(run())

    # scheduler: a voicemail ends the request (no retries) and tells the requester
    notes = []

    async def fake_notify(requester, target, text):
        notes.append(text)
    monkeypatch.setattr(scheduler, "_notify", fake_notify)
    admin = registry.lookup("telegram", "111")
    sid = sessions.start_session(friend.id, "call", "outbound")
    now = db.now_iso()
    rid = db.execute("INSERT INTO scheduled_calls(target_user_id, requested_by, run_at, purpose, status, attempts, "
                     "session_id, created_at, updated_at) VALUES (?,?,?,?, 'dialing', 1, ?, ?, ?)",
                     (friend.id, admin.id, now, "Ask about the invoice", sid, now, now))
    asyncio.run(scheduler.on_call_finished(sid, "voicemail"))
    row = scheduler.get(rid)
    assert row["status"] == "voicemail" and "left a message" in row["result"]
    assert notes and "voicemail" in notes[-1]



def test_telegram_markdown_to_html():
    from orchestrator.tg_format import md_to_html
    out = md_to_html("## Plan\n**Bold** and *it* with `a<b>` and ANTHROPIC_API_KEY\n- one\n"
                     "[link](https://x.ch/?a=1&b=2)\n<script>x</script>\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<b>Plan</b>" in out and "<b>Bold</b>" in out and "<i>it</i>" in out
    assert "<code>a&lt;b&gt;</code>" in out and "ANTHROPIC_API_KEY" in out and "\u2022 one" in out
    assert '<a href="https://x.ch/?a=1&amp;b=2">link</a>' in out
    assert "<script>" not in out and "&lt;script&gt;" in out                 # model text can't inject markup
    assert "<pre>| a | b |\n| 1 | 2 |</pre>" in out                          # tables -> monospaced block



def test_telegram_markdown_survives_odd_constructs():
    from orchestrator.tg_format import md_to_html, well_formed
    text = ("# Testing Markdown Support\n\n## Text Formatting\n\nYou can write *italic text*, **bold text**, "
            "or ***both***. For strikethrough, use ~~this syntax~~. Obsidian also supports ==highlighting==.\n\n"
            "- Item one\n- Item **two**\n\n1. First\n2. Second\n\n```python\nprint('hi')\n```\n\n"
            "> A quote with **bold** inside\n\nOdd: **unclosed bold and *star* mix and 5 * 3 * 2.")
    out = md_to_html(text)
    assert well_formed(out)
    assert "<b><i>both</i></b>" in out and "<i>italic text</i>" in out and "<s>this syntax</s>" in out
    assert "<b>Testing Markdown Support</b>" in out and "<pre>print('hi')</pre>" in out
    assert "<blockquote>A quote with <b>bold</b> inside</blockquote>" in out
    # a malformed paragraph is escaped on its own; the rest keeps its formatting
    bad = md_to_html("**fine**\n\n<b>*x**y*</b> **a *b** c*")
    assert well_formed(bad) and bad.startswith("<b>fine</b>")


def test_conversation_search_scoping_and_matching():
    import asyncio
    from orchestrator import search
    from orchestrator.tools import registry as tools
    admin = registry.lookup("telegram", "111")
    friend = registry.lookup("telegram", "22222222")

    # admin's own Telegram chat
    s1 = sessions.start_session(admin.id, "telegram")
    sessions.add_turn(s1, "user", "Please remember the Zermatt chalet booking for February.")
    sessions.add_turn(s1, "assistant", "Noted, sir: the chalet in Zermatt, February.")
    # a call Alfred made to the friend on the admin's behalf
    s2 = sessions.start_session(friend.id, "call", "outbound")
    sessions.add_turn(s2, "assistant", "Hello, calling on behalf of Test about the invoices.")
    sessions.add_turn(s2, "user", "The invoice goes out on Friday at noon.")
    sessions.add_turn(s2, "assistant", "Thank you, I'll pass that on.")
    now = db.now_iso()
    db.execute("INSERT INTO scheduled_calls(target_user_id, requested_by, run_at, purpose, status, session_id, "
               "created_at, updated_at) VALUES (?,?,?,?, 'done', ?, ?, ?)",
               (friend.id, admin.id, now, "Ask about the invoice", s2, now, now))
    # the friend's private web chat
    s3 = sessions.start_session(friend.id, "web")
    sessions.add_turn(s3, "user", "My secret zermatt plan is a surprise party.")

    hits = search.search(admin, "invoice friday")                       # stemming: invoices ~ invoice
    assert hits and hits[0]["conversation_with"] == "Friend Person" and hits[0]["call_requested_by"]
    assert any("Friday" in c["text"] for c in hits[0]["context"]) and "«" in hits[0]["match"]
    # admin sees everyone; the friend only their own + nothing of the admin's
    assert {h["session_id"] for h in search.search(admin, "zermatt")} >= {s1, s3}
    friend_hits = {h["session_id"] for h in search.search(friend, "zermatt")}
    assert s3 in friend_hits and s1 not in friend_hits
    # requested calls are found by the requester even though the call was with someone else
    assert s2 in {h["session_id"] for h in search.search(admin, "noon", about=friend)}
    # any-word fallback, and FTS syntax in the query can't break or widen the search
    assert search.search(admin, "chalet nonexistentword")
    assert search.search(friend, 'zermatt" OR "chalet') and \
        s1 not in {h["session_id"] for h in search.search(friend, 'zermatt" OR "chalet')}
    try:
        search.search(admin, "?! *")
        raise AssertionError("empty query accepted")
    except search.SearchError:
        pass
    # through the tool, as the model calls it
    out, err = asyncio.run(tools.execute("search_conversations", {"query": "chalet", "channel": "telegram"},
                                         tools.ToolContext(admin, None, "web")))
    assert not err and "Zermatt" in out
    # index stays in sync with transcript edits
    db.execute("UPDATE transcripts SET text = 'Booking moved to Verbier.' WHERE session_id = ? AND turn_index = 1", (s1,))
    assert s1 in {h["session_id"] for h in search.search(admin, "verbier")}
