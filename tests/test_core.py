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
    registry.add_identifier(friend, "telegram", "22222222")
    registry.add_identifier(friend, "phone", "+41 78 000 00 00")
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
    assert "add_task" in admin_names and "add_task" not in friend_names
    assert not any(n.startswith("n8n_") for n in friend_names)


@pytest.mark.asyncio
async def test_tool_execute_blocks_unpermitted():
    from orchestrator.tools import registry as tools
    friend = registry.lookup("telegram", "22222222")
    out, err = await tools.execute("add_task", {"title": "x"}, tools.ToolContext(friend, None, "telegram"))
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

    async def fake_place(user_id, *, opening_line, reason="", requested_by=None, request_id=None, extra_system=None):
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
