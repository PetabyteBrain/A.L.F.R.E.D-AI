# Alfred (repo/service name: alfred) — notes for Claude

Personal AI butler "Alfred" (phone + Telegram + dashboard). Renamed from Alfred on 2026-09-28:
user-facing name comes from `ASSISTANT_NAME`; infra names (folder, container, domain, cookie,
CSRF value, localStorage keys) intentionally stay `alfred`. Dashboard skins: `alfred` (Stark HUD)
and `batcave` (see `dashboard/web/src/theme.js`). Full spec lives in the README; the
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

## Dashboard
- React 19 + Vite in `dashboard/web` (no router; tabs in `App.jsx`), built to `dashboard/web/dist`,
  which FastAPI serves. Keep it CSP-clean: no inline scripts, no `dangerouslySetInnerHTML`.
- All API calls go through `src/api.js` (it adds the CSRF header). Security primitives live in
  `dashboard/api/security.py`; anything that changes a person's credentials, role or status must
  revoke their `dashboard_sessions`.

## People & permissions
- Admin-only user management + everyone's self-service profile: `dashboard/api/people.py`
  (UI: `pages/People.jsx`, `pages/Profile.jsx`, `components/Connections.jsx`). All validation and
  guard rails (last admin, no self-delete, conflict checks, session revocation) live server-side in
  `orchestrator/identity/registry.py`; the CLI uses the same functions.
- `.env` ADMIN_* only seeds the very first admin; the DB is the source of truth afterwards.

## Testing
- `.venv/Scripts/python -m pytest -q` — offline; includes a simulated Twilio call through the
  media-stream WebSocket with fake STT/TTS/agent.
- `python -m tests.devserver` — built dashboard with demo data on :8765 (admin / devpassword123);
  `npm run dev` in dashboard/web for hot reload on :5180.
