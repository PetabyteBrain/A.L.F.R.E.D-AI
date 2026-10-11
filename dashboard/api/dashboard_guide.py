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
    "mail": ("Mail", "Alfred's own mailbox: inbox, unread, starred, sent, archived, all; for admins/cofounders "
                     "also inquiries and matters passed to the team (mark handled). Groups (like folders) and coloured "
                     "tags in the sidebar (+ to add, pencil to rename/recolour/delete; own ones, plus shared team ones "
                     "for company mail); tick emails for bulk read/unread, star, archive, group and tag; the reader "
                     "shows the whole conversation; keys j/k, s, e, Esc. Emails are sent by asking Alfred."),
    "activity": ("Log", "the event log of everything that happened; admins can export it."),
    "health": ("Systems", "health of the services, Pis, Jetson and backups; admins also see the monthly "
                         "update report (Check now; report only, nothing is installed)."),
    "people": ("Personnel", "admins: people, roles, connections, logins, access attempts, data requests."),
    "usage": ("Usage", "admins: API usage, costs and monthly budgets."),
    "settings": ("Settings", "admins: role permissions (e.g. n8n for cofounders), view as role, household "
                             "quiet hours, how Alfred answers email from outside, call screening and blocked numbers."),
    "profile": ("Profile", "own name, connections (several email addresses, each verified with an emailed "
                           "code, one primary), password, two-factor sign-in, Google Calendar, quiet hours, "
                           "voice replies, morning briefing, memory notes, download or delete own data."),
}
ADMIN_ONLY = {"people", "usage", "settings"}


def context(person: Person, page: str) -> str:
    tabs = [f"{label}: {desc}" for pid, (label, desc) in PAGES.items()
            if person.is_admin or pid not in ADMIN_ONLY]
    label = PAGES.get(page, ("the dashboard", ""))[0]
    return ("This message comes from the helper on the web dashboard (a small popup with Alfred's portrait). "
            f"The person is currently on the {label} page. If they ask how to do something on the dashboard, "
            "explain where to click in one or two short sentences; if you can simply do it with a tool, offer "
            "that. Keep replies short: the popup shows a speech bubble. Dashboard tabs: " + " | ".join(tabs))
