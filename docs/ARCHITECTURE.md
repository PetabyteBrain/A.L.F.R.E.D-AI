# Alfred: architecture

How the pieces fit together. Features are listed in the [README](../README.md); conventions for
working on the code are in [CLAUDE.md](../CLAUDE.md).

## Components

| Part | What it is | Where |
|---|---|---|
| Orchestrator | FastAPI app: webhooks, WebSocket media stream, dashboard API, background loops | `orchestrator/main.py` |
| Agent Core | Claude conversation loop with tools, prompt caching, date checks | `orchestrator/agent_core.py` |
| Identity registry | People, roles, connections (phone, Telegram, dashboard, email), verification | `orchestrator/identity/` |
| Tools | Declared in YAML, role-gated twice (offered + executed); built-ins, n8n webhooks, n8n API | `orchestrator/tools/` |
| Telephony | Twilio Media Streams (8 kHz mu-law), streamed sentence-by-sentence speech | `orchestrator/call_handler.py` |
| Speech | STT/TTS swap point; self-hosted Whisper + Kokoro on a GPU box, ElevenLabs as fallback | `orchestrator/stt_tts/`, `speech_server/` |
| Telegram | Long polling; text, voice notes, files, photos; validated HTML formatting | `orchestrator/telegram_handler.py` |
| Dashboard | React 19 + Vite, served by FastAPI; no router, tabs in `App.jsx` | `dashboard/web/`, `dashboard/api/` |
| Storage | SQLite (WAL) + recordings on disk; files/notes on a NAS over SMB | `orchestrator/db.py`, `orchestrator/nas*.py` |
| Market analysis | Businesses in a radius (OpenStreetMap), website checks, web-search lookups, report in the company files | `orchestrator/market_analysis.py` |
| Homelab operator | Status/logs/restarts of other machines through a forced-command SSH key | `orchestrator/homelab.py`, `infra/ops/` |

## Request flow

```
Phone ─► Twilio ─► /twilio/voice ─► identity ─► /twilio/stream (WS) ─► STT ─► Agent Core ─► TTS ─► caller
Telegram ─► long polling ─► identity ─► Agent Core ─► reply (text or voice note)
Dashboard ─► /api/* (session cookie + CSRF header) ─► Agent Core (web chat, SSE) / data endpoints
n8n ─► /n8n/callback (shared secret) ─► Agent Core ─► Telegram
```

1. Every inbound event is resolved to a person first. Unknown or unpermitted senders get no reply
   (an access attempt is recorded); unknown callers may leave a message with no tools involved.
2. Agent Core builds the prompt: a byte-stable static system prompt (cached), then per-request
   context (person, channel, local time, upcoming dates, memory notes, recent turns on other channels).
3. Tools are filtered by role, per-role switches, channel and what the person has linked (e.g. Google
   Calendar). Acting tools pass a weekday/date check before they run; risky actions (outside calls,
   restarts, notes tidy-ups) are prepared first and run only after the person's yes in a later message.
4. Every API call is metered (tokens, characters, minutes) for the Usage tab and budget warnings.

## Data

- `users`, `user_identifiers` (connections, verified flag), `roles`
- `sessions`, `transcripts` (+ FTS5 index for conversation search), `recordings`
- `reminders`, `tasks`, `scheduled_calls`, `external_calls`, `memories`, `meetings`, `api_usage`, `activity`, `health`, `app_settings`
- Recordings: `data/storage/{user_id}/{date}/{session_id}/audio.wav`, moved to the NAS after 14 days.
- New columns go in `db.ADDED_COLUMNS` and are applied on start.

## Security model

- Registry-only access; roles decide channels and tools; per-role permission switches for admins.
- Dashboard: Argon2id passwords, server-side sessions (HMAC'd tokens), idle/absolute timeouts,
  2FA (TOTP or Telegram codes, backup codes), throttled logins, strict CSP (no inline scripts),
  CSRF header + origin check on every state-changing request.
- New phone/Telegram connections count only after a verification code.
- Untrusted text (caller messages, files, web pages) is relayed as data, never as instructions.
- File previews: documents are converted to PDF (LibreOffice) or to plain data; files are never
  rendered as HTML, and only a short list of media types is served inline.
- Secrets live only in `.env` on the server; the repo holds `.env.example` with empty values.

## Operations

- One Docker container (`docker compose up -d --build`); the image builds the dashboard itself.
- Nightly backup: verified SQLite snapshot + recordings + `.env` to an off-device host (append-only
  key) and to the NAS; a monthly restore test opens both copies in a throwaway container.
- Health loop every few minutes: Alfred's services, the homelab hosts, budgets, retention.
- External watchdog on another machine alerts on Telegram if Alfred himself stops answering.
- Monthly update report for the homelab (report only; nothing is installed automatically).

## Machine-specific files (not in git)

| File | Holds |
|---|---|
| `.env` | API keys, tokens, URLs, prices (template: `.env.example`) |
| `orchestrator/homelab.yaml` | the hosts Alfred may check (template: `homelab.example.yaml`) |
| `infra/backup.env` | `BACKUP_TARGET=user@backup-host` for the backup scripts |
| `data/` | database, recordings, backups |
