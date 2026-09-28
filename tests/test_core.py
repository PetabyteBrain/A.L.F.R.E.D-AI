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
    registry.add_identifier(friend, "telegram", "222")
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
    friend = registry.lookup("telegram", "222")
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
    friend = registry.lookup("telegram", "222")
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
    friend = registry.lookup("telegram", "222")
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

    friend = registry.lookup("telegram", "222")
    sid = sessions.telegram_session_for(friend.id, "222")
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
    assert texts[0].startswith("Hi Test") and "what's up" in texts and "all good" in texts
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
