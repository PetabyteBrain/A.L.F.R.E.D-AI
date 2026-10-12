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
                       "calendar-only entry) and List view (tasks, meetings, reminders, scheduled and archived "
                       "calls, n8n automations if allowed)."),
    "notes": ("Files", "notes, lists and files on the NAS: own folder, company for admins/cofounders, every "
                       "folder for admins; editor with Markdown preview and checklists, read-only previews of PDF, Word, Excel, "
                       "PowerPoint, pictures, audio and video, recent files; "
                       "Tidy up asks Alfred to propose a cleaner layout for your notes, applied only after you approve it."),
    "crm": ("CRM", "companies (clients, leads, partners: status, industry, website, contacts, tags, extra fields, "
                   "owner, group) and the people at them; open one for its facts, people and timeline (log a note, "
                   "call, meeting or document; emails to and from their addresses and Alfred's outside calls appear "
                   "by themselves), History with Undo, More: archive, merge into, delete (merging and deleting wait "
                   "for approval). New company / New person (duplicate warning while typing). Sidebar: status and "
                   "tag filters, only mine, archive; Approvals (approve/reject with a note, withdraw own). Keys: / "
                   "search, n new, Esc back."),
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
    "people": ("Personnel", "people, their roles (several each) and groups, connections, logins, access attempts, "
                            "data requests."),
    "usage": ("Usage", "API usage, costs and monthly budgets."),
    "settings": ("Settings", "Roles: create, copy and edit roles (each permission allowed, denied or not set; a role "
                             "can inherit from a parent), groups of people that get roles, 'who can do X / what can "
                             "Y do', view as a role or a person; plus household quiet hours, how Alfred answers "
                             "email from outside, call screening and blocked numbers."),
    "profile": ("Profile", "own name, connections (several email addresses, each verified with an emailed "
                           "code, one primary), password, two-factor sign-in, Google Calendar, quiet hours, "
                           "voice replies, morning briefing, memory notes, download or delete own data."),
}
# Tabs that need a permission (any of them); the dashboard's App.jsx uses the same rules.
TAB_PERMISSIONS = {"people": ("people.manage",), "usage": ("usage.view",), "mail": ("mail.tab",), "crm": ("crm.use",),
                   "settings": ("settings.manage", "roles.manage")}


def context(person: Person, page: str) -> str:
    tabs = [f"{label}: {desc}" for pid, (label, desc) in PAGES.items()
            if any(person.can(p) for p in TAB_PERMISSIONS.get(pid, ())) or pid not in TAB_PERMISSIONS]
    label = PAGES.get(page, ("the dashboard", ""))[0]
    return ("This message comes from the helper on the web dashboard (a small popup with Alfred's portrait). "
            f"The person is currently on the {label} page. If they ask how to do something on the dashboard, "
            "explain where to click in one or two short sentences; if you can simply do it with a tool, offer "
            "that. Keep replies short: the popup shows a speech bubble. Dashboard tabs: " + " | ".join(tabs))
