# Alfred (repo/service name: alfred) — notes for Claude

Personal AI butler "Alfred" (phone + Telegram + dashboard). Renamed from Alfred on 2026-09-28:
user-facing name comes from `ASSISTANT_NAME`; infra names (folder, container, domain, cookie,
CSRF value, localStorage keys) intentionally stay `alfred`. Dashboard skins: `alfred` (Stark HUD)
and `batcave` (see `dashboard/web/src/theme.js`). Full spec lives in the README; the
current plan is `docs/PROJECT_PLAN.md` (mirrored to `C:\Users\user\Downloads\alfred-agent-spec.md`);
the original spec is in `docs/archive/original-spec.md`. Keep the plan updated when features land.

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
  Production uses the Jetson (`speech_server/`, deployed to `user@100.64.0.6:~/alfred-speech`,
  `docker compose up -d --build` there) with automatic ElevenLabs fallback. English only.
  Gate telephony on `settings.speech_enabled`, never on `elevenlabs_enabled`.
  Voice: Kokoro `bm_george` for now; another voice may come later (roadmap #24): change
  `KOKORO_VOICE` in the Jetson's `~/alfred-speech/.env` and `docker compose up -d`.
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

## Two-factor sign-in
- `orchestrator/identity/mfa.py` (TOTP, Telegram codes, backup codes, challenges) +
  `dashboard/api/twofa.py`. `routes.current_person` blocks roles in `DASHBOARD_2FA_REQUIRED_ROLES`
  (default admin) until a method is on, except `/api/me` and `/api/profile*`. Tests run with it
  empty; the dev server requires it for admin (reset: `DB_PATH=data-dev/alfred.db python -m
  orchestrator.identity.mfa reset admin`).

## People & permissions
- Admin-only user management + everyone's self-service profile: `dashboard/api/people.py`
  (UI: `pages/People.jsx`, `pages/Profile.jsx`, `components/Connections.jsx`). All validation and
  guard rails (last admin, no self-delete, conflict checks, session revocation) live server-side in
  `orchestrator/identity/registry.py`; the CLI uses the same functions.
- `.env` ADMIN_* only seeds the very first admin; the DB is the source of truth afterwards.

## Calls, calendar, usage
- Call requests: `orchestrator/scheduler.py` (tools place_call/schedule_call/...; `end_call` is
  channel-gated to calls). Outcome hook: `call_handler._call_finished` -> `scheduler.on_call_finished`.
- Google Calendar: `orchestrator/google_calendar.py` + `dashboard/api/google.py`; tools marked
  `requires: google` in registry.yaml only appear for people with a linked calendar.
- Usage metering: `orchestrator/usage.py`; record every new billable API call there.

## Verification, reminders, briefing, memory, voice
- Phone/Telegram connections only count once `verified_at` is set (`identity/verification.py`);
  anything that reaches a person (resolve, phone_for, telegram_for) must use verified ones.
- Background loops in `main.lifespan`: health, scheduler (calls), reminders, briefing, tasks.
- Quiet hours (`orchestrator/quiet.py`): anything contacting a person on someone else's behalf
  must respect them (calls via `scheduler.request_call` do automatically; Telegram via
  `send_to_user` goes silent by default). Pass `silent=False` only for things the person set up.
- New columns on existing tables go in `db.ADDED_COLUMNS` (applied on start).
- `memory.prompt_block` goes into `_dynamic_system` only, never into STATIC_SYSTEM (cache).
- Conversation search: FTS5 `transcripts_fts` (triggers in db.SCHEMA); scoping rules live in
  `orchestrator/search.py` (own + calls requested; admin all). Telegram output goes through
  `tg_format.md_to_html` (validated tags), never raw parse_mode Markdown.
- Meetings: `orchestrator/meetings.py`; another person's calendar is only read if they turned on
  free/busy sharing, and only busy times ever leave google_calendar.busy_intervals.
- Calls speak streamed text sentence by sentence (`call_handler.split_speakable`, `_turn`).

## Ops
- Backups: `infra/backup.sh` (Pi crontab 03:30) + `orchestrator/backup.py`; restore in
  `infra/README-backup.md`. Shell scripts must stay LF (`.gitattributes`; this PC has autocrlf).
- Homelab: hosts in `orchestrator/homelab.yaml`; per-host access via `infra/ops/alfred-ops`
  (forced command) + `~/.config/alfred-ops/allow`. Never widen the key to a shell.

## Testing
- `.venv/Scripts/python -m pytest -q` — offline; includes a simulated Twilio call through the
  media-stream WebSocket with fake STT/TTS/agent.
- `python -m tests.devserver` — built dashboard with demo data on :8765 (admin / devpassword123);
  `npm run dev` in dashboard/web for hot reload on :5180.
