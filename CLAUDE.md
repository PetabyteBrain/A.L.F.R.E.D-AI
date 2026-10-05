# Alfred (repo/service name: alfred) — notes for Claude

Personal AI butler "Alfred" ("A Loyal Family Retainer, Exceedingly Discreet"; phone + Telegram + dashboard). Renamed from Alfred on 2026-09-28:
user-facing name comes from `ASSISTANT_NAME`; infra names (folder, container, domain, cookie,
CSRF value, localStorage keys) intentionally stay `alfred`. Skins: `alfred` (Stark HUD),
`batcave`, `matrix` and `alfred`, listed in `dashboard/web/src/themes.js` (+ `public/theme-boot.js`,
which applies the saved skin on every page); animated backgrounds in `src/backdrops.js` (shared with
the homepage). Full spec lives in the README; the
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
  unpermitted → drop silently (no reply) and record an access attempt. Only exception: unknown
  *phone* numbers may be screened (`orchestrator/screening.py`, CALL_SCREENING): Alfred takes a
  message with no tools and no Agent Core, then reports to admins; withheld/blocked/over-limit
  numbers still get `<Reject reason="busy"/>`. Calls to numbers outside the registry only via
  `orchestrator/external_calls.py` (task-driven, confirmed, no tools on the call). Both relay
  untrusted caller words: keep them inert (`screening._escape`) and never feed them to Agent Core.
- Conversation memory is per (channel, user), rebuilt from `transcripts` (`sessions.history`).
- Tools are declared in `orchestrator/tools/registry.yaml` and role-gated twice (list + execute).
  New n8n-backed features = n8n workflow + one yaml entry, no Python changes.
- `orchestrator/stt_tts/base.py` is the STT/TTS swap point; audio is 8 kHz mu-law end to end.
  Production uses the Jetson (`speech_server/`, deployed to `user@100.64.0.6:~/alfred-speech`,
  `docker compose up -d --build` there) with automatic ElevenLabs fallback. English only.
  Gate telephony on `settings.speech_enabled`, never on `elevenlabs_enabled`.
  Voice: Kokoro `bm_george` for now; another voice may come later (roadmap #24): change
  `KOKORO_VOICE` in the Jetson's `~/alfred-speech/.env` and `docker compose up -d`.
- NAS (TrueNAS, SMB from inside the container): `orchestrator/nas.py` (scopes personal/company, notes,
  lists, files) and `orchestrator/company_todo.py` (cell-level xlsx edits + backups; never save the
  workbook with openpyxl). Telegram files: `orchestrator/uploads.py`; search by meaning:
  `orchestrator/nas_search.py`. Recordings are written to local `STORAGE_ROOT` and moved to the NAS
  after 14 days (`orchestrator/recording_archive.py`); always read/delete them through that module.
- Model: `claude-haiku-4-5` (user's choice) via the official `anthropic` SDK (1.x, async client).
  Haiku 4.5 does not take adaptive thinking/effort; don't add them.
- Keep the static system prompt in `agent_core.STATIC_SYSTEM` byte-stable (prompt caching); put
  per-request details in `_dynamic_system`. Tool list is sorted for the same reason.

## Dashboard
- React 19 + Vite in `dashboard/web` (no router; tabs in `App.jsx`), built to `dashboard/web/dist`,
  which FastAPI serves. Keep it CSP-clean: no inline scripts, no `dangerouslySetInnerHTML`.
- Admin Settings tab (`pages/Settings.jsx`) holds household-wide settings; Profile only personal ones.
- Per-role switches: `orchestrator/permissions.py` (+ `permission:` on tools in registry.yaml). Check
  `permissions.allowed(role, ...)` server-side for anything a switch covers; admins always pass.
- Unknown non-API pages get `public/not-found.html` (`main._http_errors`); API paths keep JSON 404.
- Permissions: always use `routes.current_person` (respects an admin's "view as" role preview);
  `routes.real_person` only for the preview switch itself (`dashboard/api/viewas.py`).
- Tasks and Files stay mounted in the background (`App.jsx` KEEP_ALIVE) so state survives tab switches;
  polling there must use `usePoll` (pauses via `PageActive`) or check `usePageActive()`.
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
- Agenda (`orchestrator/agenda.py`): reminders tied to calendar events + categories; any code that
  changes or deletes a calendar event should call `agenda.on_event_changed`.

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
- Voice notes on request / voice mode: `orchestrator/voice_replies.py`; `send_to_user(voice=...)`
  defaults to the person's voice mode. Pass `voice=False` for anything secret (sign-in codes).

## Ops
- Backups: `infra/backup.sh` (Pi crontab 03:30) + `orchestrator/backup.py`; restore in
  `infra/README-backup.md`. Shell scripts must stay LF (`.gitattributes`; this PC has autocrlf).
- Monthly restore test: `infra/restore-drill.sh` (Pi crontab, 1st 04:30) -> `orchestrator/restore_drill.py`
  in a throwaway container. Recording retention: `orchestrator/retention.py` (+ Jetson crontab).
- Budgets: `orchestrator/budgets.py` (warnings to admins only). Data export/deletion:
  `orchestrator/privacy.py`; a new table holding personal data must be added to `collect` and `erase`
  (the personal NAS folder is already included).
- Homelab: hosts in `orchestrator/homelab.yaml`; per-host access via `infra/ops/alfred-ops`
  (forced command) + `~/.config/alfred-ops/allow`. Never widen the key to a shell.

## Testing
- Behaviour checks against the real model (costs cents): `python -m orchestrator.evals` (in the
  container on the Pi, which has the API key). Add a case there when changing prompts or tool rules.
- `.venv/Scripts/python -m pytest -q` — offline; includes a simulated Twilio call through the
  media-stream WebSocket with fake STT/TTS/agent.
- `python -m tests.devserver` — built dashboard with demo data on :8765 (admin / devpassword123);
  `npm run dev` in dashboard/web for hot reload on :5180.
