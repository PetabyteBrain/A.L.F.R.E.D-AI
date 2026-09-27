# Alfred — notes for Claude

Personal AI agent (phone + Telegram + dashboard). Full spec lives in the README; the
original design spec is `C:\Users\user\Downloads\alfred-agent-spec.md`.

## Where things run
- Local copy `C:\Github\Claude_Antropic\alfred` is the **master**; deploy to
  `user@alfred-server:~/alfred` (tailnet 100.64.0.1) and run `docker compose up -d --build`.
- One container `alfred`, port 8000, data in `./data` (SQLite + recordings). `.env` only on the Pi.
- Public exposure (when needed for Twilio) goes through the shared Caddy on the proxy host + `the tunnel`;
  never add a Caddy/proxy service to this compose file.

## Architecture rules
- Every inbound event resolves through `orchestrator/identity/registry.py` first. Unknown or
  unpermitted → drop silently (no reply, `<Reject>` for calls) and record an access attempt.
- Conversation memory is per (channel, user), rebuilt from `transcripts` (`sessions.history`).
- Tools are declared in `orchestrator/tools/registry.yaml` and role-gated twice (list + execute).
  New n8n-backed features = n8n workflow + one yaml entry, no Python changes.
- `orchestrator/stt_tts/base.py` is the STT/TTS swap point; audio is 8 kHz mu-law end to end.
- `STORAGE_ROOT` is the only thing to change for the NAS migration.
- Model: `claude-haiku-4-5` (user's choice) via the official `anthropic` SDK (1.x, async client).
  Haiku 4.5 does not take adaptive thinking/effort; don't add them.
- Keep the static system prompt in `agent_core.STATIC_SYSTEM` byte-stable (prompt caching); put
  per-request details in `_dynamic_system`. Tool list is sorted for the same reason.

## Testing
- `.venv/Scripts/python -m pytest -q` — offline; includes a simulated Twilio call through the
  media-stream WebSocket with fake STT/TTS/agent.
- `python -m tests.devserver` — dashboard with demo data on :8765 (admin / devpassword123).
