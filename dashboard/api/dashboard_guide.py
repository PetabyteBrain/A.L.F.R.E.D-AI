"""What Alfred knows about the dashboard when someone asks him through the avatar helper.

Added to the per-request (dynamic) system prompt only for helper messages, so the static prompt
stays byte-stable for caching. Admin-only tabs are described only to admins.
"""
from __future__ import annotations

from orchestrator.identity.registry import Person

# tab id -> (label, what it's for). Keep in sync with TABS in dashboard/web/src/App.jsx.
PAGES: dict[str, tuple[str, str]] = {
    "live": ("Live", "calls and chats in progress, with live transcripts; admins can end them."),
    "chat": ("Chat", "the full conversation with Alfred (the same one as this helper)."),
    "history": ("Archive", "past calls and chats with recordings and transcripts; search and filters."),
    "tasks": ("Tasks", "Calendar view (month/week/day, click a day or slot to add a reminder, task or "
                       "calendar-only entry; Show filters for your own entries and the deadlines from each board, the "
                       "sales pipeline and invoices, Mine / Everything I can see; deadlines are dashed with a source "
                       "tag; click one to open it, Sync this event to Google Calendar or Sync all events from that "
                       "board/pipeline, kept up to date hourly; ⟳ Sync to Google… for all sources) and List view (tasks, meetings, reminders, scheduled and archived "
                       "calls, n8n automations if allowed)."),
    "notes": ("Files", "notes, lists and files on the NAS: own folder, company for admins/cofounders, every "
                       "folder for admins; editor with Markdown preview and checklists, read-only previews of PDF, Word, Excel, "
                       "PowerPoint, pictures, audio and video, recent files; Upload, or drag files and folders onto the "
                       "list (onto a folder row to put them in it); "
                       "Tidy up asks Alfred to propose a cleaner layout for your notes, applied only after you approve it."),
    "crm": ("CRM", "Pipeline (default view): forecast cards (open, weighted, won this month, win rate, needs "
                   "attention) and bars by month; Board (drag a deal card to another stage or use Move; Won/Lost ask "
                   "why) or List; Stages button (rename, probability, order, add, remove empty). Deal view: stage "
                   "track and switch, value and weighted value, next step, company, contact, close date, timeline, "
                   "history, archive/delete. Leads: suggestions from market analyses and inquiries (tick and Add as "
                   "leads, Add, Link to an existing company, Dismiss). Quotes & invoices: totals (receivable, overdue, "
                   "paid this month, open quotes), Invoices/Quotes with state filters, Export CSV, Settings (address, "
                   "IBAN, VAT, numbering, texts, products); a draft has client, address, title, intro, due date, lines "
                   "with products and live totals, Preview PDF (QR-bill on invoices), Send (needs approval), Issue "
                   "without sending, Discard; issued ones: payments, payment reminder, accept/decline and Create "
                   "invoice for quotes, Copy as new draft, Cancel (needs approval). Also "
                   "companies (clients, leads, partners: status, industry, website, contacts, tags, extra fields, "
                   "owner, group) and the people at them; open one for its facts, people and timeline (log a note, "
                   "call, meeting or document; emails to and from their addresses and Alfred's outside calls appear "
                   "by themselves), History with Undo, More: archive, merge into, delete (merging and deleting wait "
                   "for approval). New company / New person (duplicate warning while typing). Sidebar: status and "
                   "tag filters, only mine, archive; Approvals (approve/reject with a note, withdraw own). "
                   "Follow-ups: stale leads, deals without a next step or past their close date, overdue invoices, "
                   "expiring quotes (mine/everyone's; Email drafts a follow-up that waits for approval; Put off for a "
                   "while; Settings: nudge time, days without contact, weekly report, meeting logging). Reports: "
                   "pipeline, won/lost with reasons, new business, invoices, hours per client, who's away, for a "
                   "chosen period (Print). Email button on a company, person or deal: Alfred drafts it, you edit, "
                   "it's sent after approval. Time logged on companies when time tracking is linked. Keys: / "
                   "search, n new, Esc back."),
    "boards": ("Boards", "My work: everything assigned to you across boards and your deals, by due date (also your "
                         "teams' unassigned items). Requests: task requests to accept (assign someone) or decline, and "
                         "your own. A board: views Board (columns; drag cards), Team lanes (one horizontal lane per team, "
                         "drag between teams), List (sortable, tree of sub-items, change state in place) and Calendar "
                         "(drag to change the due date); search, filter by type, person, team, done; + New item (n) or "
                         "+ Add in a column. An item opens beside the board: type, title, state, assignee, team, "
                         "priority, due and start date, estimate, parent, tags, description, client and deal links, "
                         "sub-items, company files, comments (@Name notifies), history, remove. Board settings "
                         "(managers): name and key, item types, columns (to do / in progress / done, limits), who can "
                         "do what (see, ask, work on it, manage) per everyone, team, role or person. The Sales pipeline "
                         "is a board too. New board: from a template, shared with everyone, chosen teams or only you."),
    "team": ("Team", "Teams: create teams (groups) with a manager and a colour, choose members. Accounts (people.manage): "
                     "everyone Alfred knows, their roles, groups, connections, logins, 2FA, data requests, Add person; "
                     "Access log: allowed and blocked contact attempts. Me: own holiday balance (taken, planned, waiting, left) and time off; Request time off (kind, "
                     "dates, half day, note; waits for approval). Directory: employees with title, department, "
                     "manager, status (starting, onboarding, leaving, former) and who's away; open one for details, "
                     "balance, time off (withdraw or cancel), onboarding/offboarding checklist (tick steps, add, "
                     "assign, close), responsibilities, hours tracked, history. New employee / Edit send the change "
                     "for approval (optionally starting onboarding or offboarding). Absences: month calendar of the "
                     "team (striped = waiting for approval) and the month's list. Checklists: all running ones or "
                     "My steps. Approvals: employee changes and time off. Holidays & checklists: default holiday "
                     "days, public holidays, the checklist templates."),
    "mail": ("Mail", "admins/cofounders: Alfred's own mailbox: inbox, unread, starred, sent, archived, all, "
                     "inquiries and matters passed to the team (mark handled). Groups (like folders) and coloured "
                     "tags in the sidebar (+ to add, pencil to rename/recolour/delete; own ones, plus shared team ones "
                     "for company mail); tick emails for bulk read/unread, star, archive, group and tag; the reader "
                     "shows the whole conversation with attachments (preview, download, Save to Files; risky ones download-only); "
                     "keys j/k, s, e, Esc. The Template manager button switches to the email layouts and reusable "
                     "texts: create, edit, duplicate, archive, switch on/off, live preview, send a test to yourself. "
                     "Emails are sent by asking Alfred."),
    "activity": ("Log", "the event log of everything that happened; admins can export it."),
    "health": ("Systems", "health of the services, Pis, Jetson and backups; admins also see the monthly "
                         "update report (Check now; report only, nothing is installed)."),
    "usage": ("Usage", "API usage, costs and monthly budgets."),
    "settings": ("Settings", "One section at a time (menu on the left): AI model, household quiet hours, email, "
                             "unknown callers (call screening), Roles & groups, View as. Roles: create, copy and edit roles (each permission allowed, denied or not set; a role "
                             "can inherit from a parent), groups of people that get roles, 'who can do X / what can "
                             "Y do', view as a role or a person; plus household quiet hours, how Alfred answers "
                             "email from outside, call screening and blocked numbers."),
    "profile": ("Profile", "own name, connections (several email addresses, each verified with an emailed "
                           "code, one primary), password, two-factor sign-in, Google Calendar, quiet hours, "
                           "voice replies, morning briefing, memory notes, download or delete own data."),
}
# Tabs that need a permission (any of them); the dashboard's App.jsx uses the same rules.
TAB_PERMISSIONS = {"usage": ("usage.view",), "mail": ("mail.tab",), "crm": ("crm.use",),
                   "team": ("employees.self", "employees.view", "employees.manage", "employees.approve", "teams.manage",
                            "people.manage"),
                   "boards": ("boards.use",),
                   "settings": ("settings.manage", "roles.manage")}


def context(person: Person, page: str) -> str:
    tabs = [f"{label}: {desc}" for pid, (label, desc) in PAGES.items()
            if any(person.can(p) for p in TAB_PERMISSIONS.get(pid, ())) or pid not in TAB_PERMISSIONS]
    label = PAGES.get(page, ("the dashboard", ""))[0]
    return ("This message comes from the helper on the web dashboard (a small popup with Alfred's portrait). "
            f"The person is currently on the {label} page. If they ask how to do something on the dashboard, "
            "explain where to click in one or two short sentences; if you can simply do it with a tool, offer "
            "that. Keep replies short: the popup shows a speech bubble. Dashboard tabs: " + " | ".join(tabs))
