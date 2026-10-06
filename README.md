# Alfred

*A.L.F.R.E.D.: A Loyal Family Retainer, Exceedingly Discreet.*

Personal AI butler (persona modelled on Alfred Pennyworth) reachable by **phone** (Twilio, Swiss
number), **Telegram** (text, voice notes, files and photos) and a **web chat**, backed by
**Claude Haiku 4.5**, with self-hosted speech on a **Jetson Orin**, notes/lists/files on a
**TrueNAS** box, wired into **n8n**, and a **dashboard** with two-factor sign-in. Only people in
the registry get answers; unknown callers can leave a message, everyone else is ignored silently.

Runs as one Docker container on **alfred-server** (`/home/user/alfred`). The project, service,
container and hostname keep their original `alfred` names (alfred.example.com, `docker compose exec alfred`);
only the assistant's persona and branding are "Alfred" (`ASSISTANT_NAME`). The site has four skins, chosen on the
homepage, the sign-in page or the dashboard and applied everywhere (homepage, dashboard, legal pages,
404 page): **Stark** (holographic HUD), **Batcave** (bat-signal yellow on gunmetal, Gotham skyline), **Matrix**
(green-screen terminal with digital rain) and **Alfred** (the butler's outfit: black, white and silver).

## What Alfred can do

| Area | What it does | Where |
|---|---|---|
| Phone | Answers registered callers, speaks sentence by sentence as the reply streams in, says "one moment" while a tool runs, announces that calls are recorded, hangs up by itself when done. Unknown numbers: Alfred takes a message without naming anyone and relays it to the right admin/cofounder (block button); withheld/blocked numbers get "busy" | call |
| Calls for you | "Call Max tomorrow at 9 and ask about the invoice": dials now or later, retries no-answer, leaves a short voicemail if a mailbox answers, summarises the call and reports back. Admins/cofounders can also have Alfred ring a business for one concrete task ("book a table for 4 on Friday"), only after a yes, Mon–Sat 08–19, as a declared AI assistant | any channel, Tasks tab |
| Web search | Current information (news, opening hours, prices) with sources; links in chat, source named on calls | any channel |
| Chat | Telegram (text + voice notes answered with voice; voice-note replies on request or in "voice mode"; files and photos, which Alfred can read and file on the NAS) and the dashboard Chat tab with live-streamed replies and Alfred's portrait | Telegram, web |
| Reminders | One-off or recurring (daily/weekdays/weekly/monthly), Telegram with Done/Snooze or a call, optional escalation to a call if ignored; can be tied to a calendar event (follows it, not sent if it's deleted; duplicates flagged) | any channel, Tasks tab |
| Agenda | Month / week / day calendar (jump to any date) of events, reminders, tasks and calls with categories (meeting, reminder, task, call, work, personal, other), editable from the calendar and the list; create reminders, tasks or calendar-only entries straight from a day or time slot; Alfred sees the same merged view (`agenda` tool), so \"what do I have on Saturday?\" includes reminders and repeats | Tasks tab |
| Daily briefing | Weather (Open-Meteo), today's calendar, reminders, planned calls, overnight call outcomes, tasks, the top Company to-dos (admins/cofounders; Monday nudge for items stuck in progress); by Telegram or call at a set time | Profile |
| Tasks & follow-ups | Tasks with owner, due time and follow-up: Alfred checks in with the owner (Telegram buttons or a call), up to 3 times, and tells the requester | any channel, Tasks tab |
| Memory | Short per-person notes ("prefers calls after 9") used in that person's conversations only; editable. Plus full-text search over all past conversations ("what did Max say about the invoice?") | any channel, Profile |
| Notes & files | On request Alfred tidies your own notes (folder structure or contents: reformat, combine, separate; .md/.txt only; shows the plan and waits for your yes; nothing lost). Markdown notes and checklists plus a full file explorer on the NAS (create, edit, upload, move, trash, download; tabs, Markdown preview, recent files): own space per person, Company for admins/cofounders, every folder for admins | any channel, Files tab |
| Files & documents | Browse, search (by meaning, English + German) and read Word, Excel, PDF and text files on the NAS and answer from them with the file named; files and photos sent on Telegram are filed where you say (never overwritten) | any channel |
| Receipts | Send a photo of a receipt: Alfred reads date, shop and amount, files the photo and adds a line to an Excel-ready CSV | Telegram |
| Company to-do | The company Excel to-do list: show, add, change status/importance, move between sections; only the touched cells change, with a backup before each edit | any channel (admin/cofounder), Notes tab |
| Calendar | Per-user Google Calendar: list, create, move, delete events (Limited Use) | any channel, Profile |
| Meetings | "Find an hour with Max next week": common free times (opt-in free/busy sharing, times only), each person asked by Telegram buttons or call, booked with Google invitations | any channel (admin/cofounder), Tasks |
| Automations | Full n8n API for the admin, webhook tools, async results via callback | any channel |
| Quiet hours | Household default (22:00-07:00) + per person; calls others asked for wait, messages go silent, "urgent" overrides | Profile |
| Homelab | Status of all Pis, the Jetson and their containers, logs, confirmed restarts (admin), Minecraft players; alerts when something breaks. An external watchdog on host-d alerts if Alfred himself is down | any channel (admin), Systems |
| Backups | Nightly verified database snapshot + recordings + `.env` to the Jetson (no-delete key) and to the NAS (snapshot-protected); monthly automatic restore test (`infra/README-backup.md`) | automatic, Systems |
| Safety & privacy | Weekday/date check before Alfred acts on anything, registry-only access, roles, code verification of new connections, dashboard 2FA (authenticator app or Telegram code), call audio deleted after 90 days, "download / delete my data", monthly cost budgets with admin alerts, behaviour checks against the real model | everywhere |
| Continuity | Optional: Alfred sees your last few messages from your other channels (12 h), so a call can pick up a Telegram thread | Profile |
| Dashboard | Alfred's avatar helper on every tab (portrait that reacts to what he's doing, speech bubble, knows the page you're on); live sessions (terminate), archive with audio and search/filters, calendar + tasks/reminders/calls (archived calls searchable), notes & files, event log (CSV export), health, people, API usage, budgets; admin Settings tab (role permissions such as n8n for cofounders, household quiet hours, call screening, view as role) | `/app` |

```
Phone ──► Twilio ──► /twilio/voice ─► identity ─┬─► <Stream> ─► /twilio/stream (WS), mu-law 8k
                                    unknown no. ─┘     ├─► Jetson Whisper (STT)   ┐ ElevenLabs as
                                    (screening:        ├─► Agent Core (Claude)    │ automatic
                                     no tools)         └─◄ Jetson Kokoro (TTS)    ┘ fallback
Telegram ◄─► long polling ─► identity ─► Agent Core   (voice notes, files, photos)
Dashboard (React) ─► /api/* (Argon2 login + 2FA, server-side sessions) ─► SQLite + /data/storage
                     /api/chat/stream (SSE)
Agent Core ─► tools ─► n8n · Google Calendar · homelab (alfred-ops) · NAS over SMB (notes, lists, files, to-do)
n8n ─► /n8n/callback (X-Alfred-Secret) ─► Agent Core ─► Telegram
Background loops: calls · reminders · task follow-ups · briefings · health · retention · budgets · homelab
Nightly: backup → Jetson + NAS · monthly restore test · watchdog on host-d every 2 min
```

The full, current project plan (architecture, data model, phases, roadmap) is in
[docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md).

## Status by phase

| Phase | What | State |
|---|---|---|
| 1 | Telegram + Agent Core + identity registry + SQLite logging | live |
| 2 | n8n full REST API tools + role-gated registry + `echo_test` | live |
| 3 | Twilio Media Streams + ElevenLabs STT/TTS + stereo recording | live |
| 4 | Dashboard + additions (people, calls for others, calendar, usage, web chat, reminders, briefing, voice notes, memory, verification, quiet hours, task follow-ups) | live |
| 5 | Self-hosted speech on the Jetson Orin (Whisper large-v3-turbo + Kokoro `bm_george`) | live; `speech_server/`, ElevenLabs as fallback |
| 6 | NAS (TrueNAS over SMB): notes, lists, files, Company to-do, receipts, second backup, recordings after 14 days | live |

## First start on the Pi

```bash
cd ~/alfred
cp .env.example .env      # fill in keys as you get them
mkdir -p data             # owned by you (uid 1000) so the container can write
docker compose up -d --build
docker compose exec alfred python -m orchestrator.identity.admin_cli set-password 1 admin
```

Dashboard: `http://alfred-server:8000` (LAN/Tailscale). For plain-HTTP LAN testing set
`DASHBOARD_COOKIE_SECURE=false`, otherwise the login cookie is only sent over HTTPS.

## Managing people

```bash
alias alfred-admin='docker compose exec alfred python -m orchestrator.identity.admin_cli'
alfred-admin list
alfred-admin add "Alex Muster" --role friend --telegram 123456789 --phone "079 000 01 00"
alfred-admin link 2 email alex@example.com
alfred-admin add-role family --desc "Family" --dashboard
alfred-admin disable 2
alfred-admin attempts          # who tried to reach Alfred without being registered
```

Usually you'll do this on the dashboard instead: **Personnel** (admin) and **Profile** (everyone).

- Telegram identity is the **numeric user ID** (e.g. via @userinfobot), never the @username.
- Phone numbers are normalised to E.164; Swiss local format (`079…`) becomes `+4179…`.
- Phone numbers and Telegram IDs added on the dashboard stay inactive until confirmed with a
  code (Alfred calls with it, or the code is sent to the bot from that account); admins can mark
  one verified by hand. CLI-added connections count as verified.
- A role's channels (`telegram` / `call` / `dashboard`) are set per role; its tools are set in
  `orchestrator/tools/registry.yaml` (`roles: [admin, friend]`, or `"*"` for everyone).
- `.env` `ADMIN_*` only seeds the very first admin; after that the database is the source of truth.

## Adding a capability

1. Build an n8n workflow with a **Webhook** trigger (path e.g. `alfred-weather`).
2. Add an entry to `orchestrator/tools/registry.yaml` (`kind: n8n_webhook`, `webhook_path`,
   `roles`, `input_schema`). For long jobs set `async: true`; n8n then POSTs its result to
   `{PUBLIC_BASE_URL}/n8n/callback` with header `X-Alfred-Secret` and a JSON body containing the
   `session_id`/`correlation_id` it received, and Alfred tells the person on Telegram.
3. `docker compose restart alfred` (the registry file is bind-mounted, no rebuild needed).

The admin also has direct n8n tools (list/get/create/update/activate/delete workflows, list
executions, trigger any webhook), so you can ask Alfred to build workflows for you.

## Going public (needed for phone calls)

Telegram needs no public URL. Twilio does. Following the existing homelab pattern
(the proxy host Caddy + `the tunnel`):

1. Caddyfile on the proxy host: `alfred.example.com { tls internal; reverse_proxy <pi5-tailnet-ip>:8000 }`
   (Caddy handles WebSocket upgrades automatically).
2. `/etc/cloudflared/config.yml`: add `alfred.example.com → https://localhost:443` with
   `originServerName`, before the 404 catch-all; `cloudflared tunnel route dns … alfred.example.com`.
3. `docker exec caddy caddy reload && sudo systemctl restart cloudflared`.
4. Twilio console → your number → *A call comes in*: **Webhook** `POST https://alfred.example.com/twilio/voice`.
5. Optional extra layer: Cloudflare Access on the dashboard path, leaving `/twilio/*` and
   `/n8n/callback` open (they're protected by Twilio signatures / shared secret).

## Storage layout

```
data/alfred.db
data/storage/{user_id}/{YYYY-MM-DD}/{session_id}/audio.wav        # calls: stereo, L=caller R=Alfred
data/storage/{user_id}/{YYYY-MM-DD}/{session_id}/transcript.json  # calls, Telegram and web sessions
data/backups/                                                     # nightly verified DB snapshots (last 3)
data/inbox/                                                       # Telegram files waiting to be filed (1 h)

NAS, share Alfred_Personal:  <person>/Notes/ · <person>/Lists/ · <person>/Receipts/   (one folder per person)
                             .alfred-backups/{db,storage,config}   (nightly copy)
                             .alfred-recordings/{user_id}/...      (recordings older than 14 days)
NAS, share Company:           company files (read-only for Alfred) · Notes/ · Lists/ · Receipts/
                             the to-do workbook · .alfred-backups/ (copies before each to-do edit)
```

## Dashboard security

- Argon2id password hashes; the login throttle trips after 10 failures per IP or 5 per username in 15 min.
- Two-factor sign-in (authenticator app or a code on Telegram, backup codes), required for admins
  (`DASHBOARD_2FA_REQUIRED_ROLES`); lost phone: another admin resets it, or
  `python -m orchestrator.identity.mfa reset <username>` on the server.
- Server-side sessions: the cookie (`__Host-alfred`, HttpOnly, Secure, SameSite=Strict) holds a
  random token; only its HMAC is stored. Idle timeout 60 min, hard cap 12 h. Logout, password
  change, role change, disabling a person, or `admin_cli logout-all` revoke sessions immediately.
- CSRF: every POST/PUT/PATCH/DELETE under `/api` needs `X-Requested-With: alfred`, and a
  foreign `Origin` is rejected.
- Strict CSP (`script-src 'self'`, no inline code, `frame-ancestors 'none'`), nosniff,
  no-referrer, HSTS over HTTPS, `no-store` on API responses and recordings.
- Validated inputs (bounded query params, strict JSON bodies) and parameterised SQL.
  Recordings are served only by numeric ID after an ownership check, and paths are confined to `STORAGE_ROOT`.
- Admins see everything; other dashboard roles see only their own sessions and recordings.
- The client IP (used for throttling) is taken from `CF-Connecting-IP` only when the request
  comes from `TRUSTED_PROXY_IPS` (the reverse proxy); uvicorn runs without proxy-header trust.
- Tip: set `ALFRED_BIND` to the Pi 5 tailnet IP so port 8000 isn't reachable from the LAN.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt pytest pytest-asyncio
cd dashboard/web && npm install && npm run build && cd ../..
.venv/Scripts/python -m pytest -q        # offline tests incl. a simulated phone call + security checks
# behaviour checks against the real model (a few cents), on the Pi:
#   docker compose exec alfred python -m orchestrator.evals
.venv/Scripts/python -m tests.devserver  # built dashboard with demo data: http://127.0.0.1:8765 (admin / devpassword123)
cd dashboard/web && npm run dev          # hot-reload React on :5180, proxies /api to the devserver
```

The Docker build compiles the React app in a Node stage, so the Pi doesn't need Node installed.

Deploy from Windows (local copy is the master):

```bash
git archive HEAD | ssh user@alfred-server 'tar -xf - -C ~/alfred'
ssh user@alfred-server 'cd ~/alfred && docker compose up -d --build'
```

Schema changes migrate themselves on start (`db.init_db`); take a DB snapshot into
`data/backups/` first. Never pipe the archive into an ssh call that also runs
`docker compose exec` (exec swallows stdin).

## Open questions

- First real n8n workflow beyond the echo test and the daily weather alert.
- Decided 2026-10-07: threads stay per channel, plus an optional excerpt of the person's recent turns on
  their other channels (#30, Profile switch "Continue across channels").
- Alfred's own email address (#7): which address and provider.

The feature roadmap is in [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md#10-roadmap--feature-expansions).
