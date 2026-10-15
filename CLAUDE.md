# Alfred — notes for Claude

Personal AI butler "Alfred" ("A Loyal Family Retainer, Exceedingly Discreet"; phone + Telegram + dashboard).
The user-facing name comes from `ASSISTANT_NAME`; service, container, cookie, CSRF value and localStorage
keys are `alfred`. Operator details (name, location, contact email, site URL) and the company workspace's
name come from `.env` (`OPERATOR_*`, `CONTACT_EMAIL`, `PUBLIC_BASE_URL`, `COMPANY_NAME`): public pages hold
`{{PLACEHOLDERS}}` filled in by `main._fill`, so no personal details live in the repo. Skins: `stark` (Stark HUD),
`batcave`, `matrix` and `alfred`, listed in `dashboard/web/src/themes.js` (+ `public/theme-boot.js`,
which applies the saved skin on every page); animated backgrounds in `src/backdrops.js` (shared with
the homepage). Features are in the README, the design in `docs/ARCHITECTURE.md`, and how to use them in the user
guide `docs/user-guide/` (one Markdown page per topic, front matter for a docs site; update it with every
user-visible change, keeping it free of personal details). Personal planning
docs and the real homelab map are git-ignored (`docs/PROJECT_PLAN.md`, `CLAUDE.local.md`,
`orchestrator/homelab.yaml`); keep real phone numbers, IPs, names and hostnames out of tracked files.

## Where things run
- The local checkout is the master; pushes to `main` deploy through GitHub Actions: the runner joins the tailnet
  as `tag:ci` and uses Tailscale SSH into the server (tagged `tag:server`) to unpack `git archive HEAD` over
  `~/jarvis`, rebuild and health-check (`.github/workflows/deploy.yml`, `infra/README-deploy.md`).
  Markdown/docs-only pushes don't deploy. By hand it is `git archive HEAD` there + `docker compose up -d --build`
  (details per machine in `CLAUDE.local.md`, if present).
- One container `alfred`, port 8000, data in `./data` (SQLite + recordings). `.env` only on the server.
- Public exposure (when needed for Twilio) goes through a reverse proxy on another machine + a
  Cloudflare Tunnel; never add a Caddy/proxy service to this compose file.

## Architecture rules
- Every inbound event resolves through `orchestrator/identity/registry.py` first. Unknown or
  unpermitted → drop silently (no reply) and record an access attempt. Only exception: unknown
  *phone* numbers may be screened (`orchestrator/screening.py`, CALL_SCREENING): Alfred takes a
  message with no tools and no Agent Core, then reports to admins; withheld/blocked/over-limit
  numbers still get `<Reject reason="busy"/>`. Calls to numbers outside the registry only via
  `orchestrator/external_calls.py` (task-driven, confirmed, no tools on the call). Both relay
  untrusted caller words: keep them inert (`screening._escape`) and never feed them to Agent Core.
- Conversation memory is per (channel, user), rebuilt from `transcripts` (`sessions.history`).
- Tools are declared in `orchestrator/tools/registry.yaml` with the `permission:` they need and are
  checked twice (list + execute).
  New n8n-backed features = n8n workflow + one yaml entry, no Python changes.
- `orchestrator/stt_tts/base.py` is the STT/TTS swap point; audio is 8 kHz mu-law end to end.
  Production uses a Jetson (`speech_server/`, deployed to `~/alfred-speech` there, `SPEECH_BIND` in its
  `.env`, `docker compose up -d --build`) with automatic ElevenLabs fallback. English only.
  Gate telephony on `settings.speech_enabled`, never on `elevenlabs_enabled`.
  Voice: Kokoro `bm_george` for now; another voice may come later (roadmap #24): change
  `KOKORO_VOICE` in the Jetson's `~/alfred-speech/.env` and `docker compose up -d`.
- NAS (TrueNAS, SMB from inside the container): `orchestrator/nas.py` (scopes personal/company, notes,
  lists, files) and `orchestrator/company_todo.py` (cell-level xlsx edits + backups; never save the
  workbook with openpyxl). Telegram files: `orchestrator/uploads.py`; search by meaning:
  `orchestrator/nas_search.py`. Recordings are written to local `STORAGE_ROOT` and moved to the NAS
  after 14 days (`orchestrator/recording_archive.py`); always read/delete them through that module.
- Model and billing key are chosen by admins on the Settings tab (`orchestrator/ai_config.py`, setting `ai`):
  ANTHROPIC_API_KEY / ANTHROPIC_API_KEY_2, conversations on Haiku 4.5 (default) / Sonnet 5.5 / Opus 5.5 /
  Fable 5.1, calls separately (Haiku default). Haiku takes no thinking/effort; the others always think
  (effort via `output_config`, larger max_tokens) - use `ai_config.request_params`, never hard-code. Keep
  history append-only within a turn (preserved thinking). Helper jobs stay on CLAUDE_MODEL (Haiku).
  `agent_core._client`, when set, overrides the key (tests, dev server).
- Notes tidy-up: `orchestrator/notes_tidy.py` (personal scope, .md/.txt only, preview -> confirm after a new user turn;
  Files tab: `propose` -> ticked steps -> `dashboard_apply`, the Apply click is the confirmation).
- Every acting tool call passes a weekday/date check first (`orchestrator/dates.py`, `registry.date_problems`);
  `resolve_date` (`dates.resolve`) turns spoken days into dates so the model never counts.
- Keep the static system prompt in `agent_core.STATIC_SYSTEM` byte-stable (prompt caching); put
  per-request details in `_dynamic_system`. Tool list is sorted for the same reason.

## Dashboard
- React 19 + Vite in `dashboard/web` (no router; tabs in `App.jsx`), built to `dashboard/web/dist`,
  which FastAPI serves. Keep it CSP-clean: no inline scripts, no `dangerouslySetInnerHTML`.
- Settings tab (`pages/Settings.jsx`) holds household-wide settings (settings.manage) and the role editor
  (`components/RolesManager.jsx`, roles.manage); Profile only personal ones.
- Avatar helper: `components/AlfredAvatar.jsx` (states = portrait file names in `src/assets/avatar/`, from
  `scripts/crop_avatar.py`); page help text in `dashboard/api/dashboard_guide.py` (keep in sync with App.jsx TABS).
- Tabs and UI parts follow `me.permissions` via `can(me, "…")` (`components/common.jsx`); TABS `perms` and
  `dashboard_guide.TAB_PERMISSIONS` must match. Hiding UI is cosmetic: the server checks every permission.
- Unknown non-API pages get `public/not-found.html` (`main._http_errors`); API paths keep JSON 404.
- Files in `public/` have fixed names and Cloudflare caches .css/.js/.svg: link shared ones through
  `main._FIXED_NAME_ASSETS` (served pages get `?v=<content hash>`), and check theme work on the live site.
- Permissions: always use `routes.current_person` (applies a "view as" preview of a role or a person,
  capped to the previewer's own permissions) and `routes.requires("perm")` for routes that need one;
  `routes.real_person` only for the preview switch itself (`dashboard/api/viewas.py`).
- Tasks and Files stay mounted in the background (`App.jsx` KEEP_ALIVE) so state survives tab switches;
  polling there must use `usePoll` (pauses via `PageActive`) or check `usePageActive()`.
- All API calls go through `src/api.js` (it adds the CSRF header). Security primitives live in
  `dashboard/api/security.py`; anything that changes a person's credentials, roles, groups or status must
  revoke their `dashboard_sessions`.

## Two-factor sign-in
- `orchestrator/identity/mfa.py` (TOTP, Telegram codes, backup codes, challenges) +
  `dashboard/api/twofa.py`. `routes.current_person` blocks roles in `DASHBOARD_2FA_REQUIRED_ROLES`
  (default admin) until a method is on, except `/api/me` and `/api/profile*`. Tests run with it
  empty; the dev server requires it for admin (reset: `DB_PATH=data-dev/alfred.db python -m
  orchestrator.identity.mfa reset admin`).

## People & permissions
- Roles and permissions (roadmap #57): `orchestrator/permissions.py` is the one place that decides.
  Every ability is a named permission in `CATALOGUE`; roles (`role_permissions`: allow/deny per
  permission, `roles.parent` inheritance, `roles.archived`, `roles.label` as display name) are editable
  sets of them; people hold several roles (`user_roles`, first = `users.role`, the primary) and get
  group roles (`people_groups`, `group_members`, `group_roles`). Allows add up, any deny wins; admin is
  fixed (everything, never inherited or given to a group). Editing is capped (`Actor`, `_check_role_cap`).
  In code: `person.can("files.company")`, `permissions.people_with(...)` for "who gets this",
  `registry.allows_channel(person, channel)`. Never compare role names for access (the "sir" greeting
  via `is_admin` is the exception). A new permission: add it to `_CATALOGUE` with its defaults; `migrate`
  grants it to the built-in roles that hold it by default. API: `dashboard/api/access.py`.
- User management (people.manage) + everyone's self-service profile: `dashboard/api/people.py`
  (UI: `pages/People.jsx`, `pages/Profile.jsx`, `components/Connections.jsx`). All validation and
  guard rails (last admin, no self-delete, conflict checks, session revocation) live server-side in
  `orchestrator/identity/registry.py`; the CLI uses the same functions.
- `.env` ADMIN_* only seeds the very first admin; the DB is the source of truth afterwards.

## CRM and approvals (roadmap #58)
- `orchestrator/crm.py`: companies/contacts (`crm_companies`, `crm_contacts`; tags JSON list, custom JSON object),
  timeline `crm_activities` (emails and outside calls are matched by address at read time, mail filtered by
  `mail.may_read`), every direct change in `crm_changes` (history + `undo`, own latest change only). Visibility:
  crm.view_all, else owner / creator / group (contacts also via their company): always go through `_scope` /
  `_get_visible`. New records are duplicate-checked (`DuplicateError`, `force`).
- Deleting and merging are never direct: `crm.request` -> `orchestrator/approvals.py` (generic: area + kind,
  handlers registered with `@approvals.handler`, decided by holders of `<area>.approve`, self-approval allowed,
  Telegram buttons `apv:yes|no:<id>`). Later stages (quotes, invoices, employee data) add their own kinds.
- Pipeline (stage 2): `orchestrator/crm_deals.py` (`crm_stages`: open stages in order with a win probability, plus
  one Won and one Lost; `crm_deals`: value CHF, probability override, expected_close, next step, outcome note;
  visibility = `crm._scope(..., "deal")`, also through the company; changes/undo/approvals reuse crm.py with
  entity "deal"; won/lost sets `closed_at`, `board()` and `forecast()`). Stage moves are shown on timelines from
  `crm_changes`. Leads: `orchestrator/crm_leads.py` (`crm_suggestions`, from `market_analysis.analyse` shortlists
  and inquiry senders; add/link/dismiss only by a person). Permission crm.settings for the stages.
- Quotes and invoices (stage 3): `orchestrator/invoicing.py` (NOT billing.py: that one fetches Anthropic's billed
  cost for the Usage tab). Settings in app_settings "billing" (address, IBAN, VAT, terms, formats, texts),
  `billing_products`, `billing_docs` (draft -> pending -> issued -> paid/accepted/declined/cancelled; `display_status`
  adds open/overdue/partly_paid/expired), `billing_lines`, `billing_payments`. Issuing = next number per kind and year
  (`_next_number` in a transaction), freeze, PDF (`orchestrator/invoicing_pdf.py`: reportlab + qrbill/svglib, the QR
  part drawn at the page bottom), filed to the company share. Sending, reminders and cancelling are approvals (area
  "billing", async handlers send the email with the PDF). Permissions billing.use / billing.approve /
  billing.settings. API `dashboard/api/billing.py` (`/api/billing/...`), UI `components/crm/Billing*.jsx`.
- API `dashboard/api/crm.py` (`/api/crm/{companies|contacts|deals}...` and fixed paths under `/api/crm-actions`:
  pipeline, stages, leads, approvals, undo); UI `pages/CRM.jsx` + `components/crm/*` (same layout classes as Mail).
  Tools crm_*, crm_deals, crm_deal_save, crm_leads, crm_lead_decide and list/decide_approval.
- Employees (stage 4): `orchestrator/employees.py` (`employees` linked to a user by `user_id`, `employee_history`,
  `employee_absences`, `employee_checklists` + `employee_tasks`; settings "employees": holiday days, public holidays,
  checklist templates). Permissions employees.self / view / manage / approve. EVERY change to employee data is an
  approval (area "employees": save, absence, absence_cancel); checklist ticking is direct. `approvals.on_close` tidies
  up rejected/withdrawn requests. `employees.away(user_id, day)` is what `scheduler` checks before calls others ask
  for (refused at request time, postponed to the day they're back at dispatch). API `dashboard/api/team.py`
  (`/api/team/...`, plus the generic `/api/approvals` used by both tabs); UI `pages/Team.jsx` + `components/team/*`.
- Time tracking: `orchestrator/timetrack.py` reads the time-tracking app's read-only export (TIMETRACK_URL /
  TIMETRACK_TOKEN; Chrono `GET /api/export/sessions`), cached 5 min; client = tag or tag group matching the
  company's `timetrack_tag` or name (`crm.name_key`), employee = `timetrack_user` or name. Never writes there.
- Proactive (stage 5): `orchestrator/crm_proactive.py` (follow_ups + `crm_snoozes`, weekday nudges unless the
  briefing carries them, weekly report to crm.reports, emails to clients as approval kind crm/email, meetings from
  linked calendars logged once per event via `crm_activities.source_ref`, checklist reminders); its loop starts in
  `main.lifespan`, jobs claim their day/hour in app_settings "crm_proactive_state". Tools crm_follow_ups, crm_email,
  crm_report, team_directory, absence, employee_change, employee_checklist. A tool `permission:` may be "a|b".

## Boards and teams (roadmap #59)
- `orchestrator/boards.py`: boards with their own `board_types` and `board_states` (category todo/doing/done, WIP
  limit), `board_items` (numbered per board, KEY-n; assignee must see the board; team = a people group; company/deal
  links checked through crm._get_visible), comments (@mentions and the assignee notified), `board_files` (company
  share only, opened with the viewer's own access), `board_history`. Access: `level_of` = highest of the
  `board_access` rows for everyone/group/role/user (view < request < contribute < manage); creator and boards.admin
  manage; a team manager gets contribute on boards their team is on. "request" level -> `board_requests`, decided by
  the team's manager or the board's managers (`deciders`; Telegram `brq:yes|no:<id>`).
- The sales pipeline is the virtual board "sales" (columns = crm_stages, cards = deals; moves go through
  crm_deals.save). Woven in: agenda (`agenda_items`, kind "work"), briefing (`briefing_block`), morning reminder in
  the crm_proactive loop (`send_due_reminders`, setting board_reminders), Team page / CRM views (`linked_items`).
- Teams are people groups with `manager_id` and `color`; teams.manage (or roles.manage) changes them, but a group
  that gives roles stays with roles.manage (`permissions._need_any`). Membership changes revoke dashboard sessions.
- API `dashboard/api/boards.py` (`/api/boards/...`, `/api/teams`), UI `pages/Boards.jsx` + `components/boards/*`,
  teams in `components/team/Teams.jsx`. Tools board_items, board_item_save, board_comment, board_request.
  `hooks.navigate(tab, target)` + `useNavTarget` open another tab at a record (boards item, CRM deal, a file).
- Deadlines in the agenda: `orchestrator/deadlines.py` (board items, deals' close / next step, invoices and quotes as
  agenda kind "deadline" with a `source` board:<id> | pipeline | billing; `calendar_links` = synced Google copies,
  `calendar_follows` = sources kept in sync hourly by the crm_proactive loop; agenda flags Google events that are
  copies, `copy_of`). API under `/api/agenda/deadline*`, UI `components/DeadlinePanels.jsx` + AgendaCalendar filters.
- Personnel lives in the Team tab now (Accounts, Access log: `pages/People.jsx` exports them); Settings is paged
  like Profile (`pages/Settings.jsx` SECTIONS).

## Calls, calendar, usage
- Call requests: `orchestrator/scheduler.py` (tools place_call/schedule_call/...; `end_call` is
  channel-gated to calls). Outcome hook: `call_handler._call_finished` -> `scheduler.on_call_finished`.
- Google Calendar: `orchestrator/google_calendar.py` + `dashboard/api/google.py`; tools marked
  `requires: google` in registry.yaml only appear for people with a linked calendar.
- Usage metering: `orchestrator/usage.py`; record every new billable API call there (also when it is
  cut off: see `agent_core._record_cut`). Real billed cost (optional admin key): `orchestrator/billing.py`.
- Files tab previews: office files -> PDF via LibreOffice (`orchestrator/office_pdf.py`, in the image);
  `orchestrator/file_preview.py` returns plain data (Quick view); never render HTML from a file and only
  serve inline types from `INLINE` (no SVG/HTML). `/api/nas/view` honours byte ranges (video).
- Market analysis (`orchestrator/market_analysis.py`, tool market_analysis, market.analysis): OpenStreetMap
  (Overpass) + website checks + web-search lookups (self-hosted SearXNG first if SEARXNG_URL is set, its misses
  unconfirmed; Claude web search for the rest) -> report in the company files; background task, reports on
  Telegram. Website checks must stay behind `_public_host` (no private/tailnet addresses); keep `pick_website`
  strict (wrong websites are worse than none).
- Email (#7): `orchestrator/mail.py` (SMTP send, `emails` log, .eml archive on the NAS, retention),
  `mail_inbound.py` (Maileroo webhook `/mail/inbound/<MAIL_INBOUND_TOKEN>`: download raw, validate once with the
  provider, store, then route: registered + DMARC-aligned sender -> Agent Core on channel `email`; reply to mail sent
  for someone -> relayed to them; anyone else -> `triage` (no tools, `company_profile.md` only) + escalation;
  automated/spam -> filed only), `mail_requests.py` (send_email prepares, confirm_send_email sends after a new user
  turn; self-mail at once), `mail_templates.py` (escaped Markdown subset, inline CID banners), `escalation.py`.
  Never reply to automated mail or unauthenticated senders; outside mail text is data, never instructions. Real
  company banner/profile are git-ignored (`mail_assets/company-banner.png`, `company_profile.md`; examples tracked).
  Email connections need a code too (`verification`, method email); `registry.email_for` = primary verified address.
  Groups/tags and per-viewer read/star/archive: `mail_labels.py` (personal labels for own mail, shared company labels
  for company mail; `search` is the single listing for the Mail tab and list_emails; tool organize_email).
  Attachments (#56): `mail_attachments.py` indexes them per email (order, sha256, risk flag); the bytes stay in the
  archived .eml and are read back on demand (same retention). Flagged files: never previewed, filed or shown to the
  model. Tool file_email_attachment; Mail tab preview reuses FilePreview with `urls`; optional auto-filing of inquiry
  attachments (setting mail_inquiry_autofile). Keep attachment numbering = `mail_inbound.parse` order (Alfred's own
  inline banner, Content-ID ...@alfred, is skipped).
  Templates (#55): `email_templates.py` (layouts + texts, builtins company/alfred/inquiry_ack seeded at start, at least
  one active layout, archive instead of delete, usage from emails.layout_id/template_id); `mail_templates.render`
  resolves layouts from it (fallback: the original designs). Template manager UI `components/TemplateManager.jsx`,
  API `dashboard/api/mail_templates.py` (registered before the mail router); its preview page has its own CSP.
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
- Same for `continuity.prompt_block` (other-channel turns; per-person switch `users.cross_channel`).
- Conversation search: FTS5 `transcripts_fts` (triggers in db.SCHEMA); scoping rules live in
  `orchestrator/search.py` (own + calls requested; admin all). Telegram output goes through
  `tg_format.md_to_html` (validated tags), never raw parse_mode Markdown.
- Meetings: `orchestrator/meetings.py`; another person's calendar is only read if they turned on
  free/busy sharing, and only busy times ever leave google_calendar.busy_intervals.
- Calls speak streamed text sentence by sentence (`call_handler.split_speakable`, `_turn`).
- Voice notes on request / voice mode: `orchestrator/voice_replies.py`; `send_to_user(voice=...)`
  defaults to the person's voice mode. Pass `voice=False` for anything secret (sign-in codes).

## Ops
- Backups: `infra/backup.sh` (Pi crontab 03:30; target in git-ignored `infra/backup.env`) + `orchestrator/backup.py`; restore in
  `infra/README-backup.md`. Shell scripts must stay LF (`.gitattributes`; this PC has autocrlf).
- Monthly restore test: `infra/restore-drill.sh` (Pi crontab, 1st 04:30) -> `orchestrator/restore_drill.py`
  in a throwaway container, for the Jetson copy and the NAS copy (`backup_nas fetch`). Recording retention: `orchestrator/retention.py` (+ Jetson crontab).
- Budgets: `orchestrator/budgets.py` (warnings to admins only). Data export/deletion:
  `orchestrator/privacy.py`; a new table holding personal data must be added to `collect` and `erase`
  (the personal NAS folder is already included).
- Homelab: hosts in `orchestrator/homelab.yaml` (git-ignored; template `homelab.example.yaml`); per-host access via `infra/ops/alfred-ops`
  (forced command) + `~/.config/alfred-ops/allow`. Never widen the key to a shell. After changing the
  script, install it on every host (infra/ops/README.md). Monthly update report: `orchestrator/update_check.py`
  (`alfred-ops updates`; report only, never install anything from Alfred).

## Testing
- Behaviour checks against the real model (costs cents): `python -m orchestrator.evals` (in the
  container on the Pi, which has the API key). Add a case there when changing prompts or tool rules.
- `.venv/Scripts/python -m pytest -q` — offline; includes a simulated Twilio call through the
  media-stream WebSocket with fake STT/TTS/agent.
- `python -m tests.devserver` — built dashboard with demo data on :8765 (admin / devpassword123);
  `npm run dev` in dashboard/web for hot reload on :5180.
