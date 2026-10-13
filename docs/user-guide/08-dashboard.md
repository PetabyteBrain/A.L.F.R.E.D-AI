---
title: The dashboard, tab by tab
description: Every dashboard tab explained, the avatar helper and the looks.
sidebar_position: 8
---

# The dashboard, tab by tab

**Who:** everyone with a dashboard login (admins and cofounders by default). Some tabs only show up if your roles
allow them; the defaults are given in brackets (*admins*, or *team* = admins and cofounders).

Open the dashboard at your Alfred address under `/app`. The side menu (a row at the top on phones) lists the tabs.
The top of every page shows the time and the system status ("All systems nominal" or how many need attention).

## Live

What's happening now: calls and chats in progress with their live transcript, and at the top the **Telemetry**
(active sessions, calls, chats and emails today, and blocked attempts for admins) and **Uplinks** (which channels are
online: Telegram, phone, email, automations, Google). Each session shows its channel (voice, Telegram, web chat or
email). Admins can end a call or close a session; the conversation history is kept. In the *Alfred* look, a calling
card greets you with today's agenda.

## Chat

The web chat with Alfred: the same assistant as on the phone and Telegram, with his portrait beside his replies.
**New conversation** starts fresh. See [Talking to Alfred](02-talking-to-alfred.md#in-the-web-chat).

## Archive

Past calls, chats and email conversations with transcripts, and call recordings to play (audio is kept 90 days).
Search what was said, filter by person, channel (phone, Telegram, web chat, email) and dates, or browse by person
and day. You see your own; admins see everyone's.

## Tasks

- **Calendar**: month, week or day view of events, reminders, tasks and calls; click to create or change things.
  See [The calendar and agenda](03-your-day.md#the-calendar-and-agenda).
- **List**: tasks, reminders (filters: in the calendar, repeating, one-off, not needed), meetings, outside calls,
  and calls (**Upcoming** and **Archived**, searchable), with **Schedule a call**.
- Automations (n8n) for admins and cofounders who may see them: each workflow, its last run and failures, with
  **Open n8n** and **Edit** links.

## Files

Your notes, lists and files on the storage server: **Mine**, the company's files (*team*) and **All people**
(*admins*). Explorer, editor, previews, upload, **Tidy up my notes…**. See
[The file explorer](05-memory-notes-files.md#the-file-explorer).

## Mail (*team*)

Alfred's mailbox: boxes, groups and tags, bulk actions, the conversation reader with attachments, matters for the
team, and the **Template manager**. See [Email](06-email.md#the-mail-tab).

## CRM (*team*)

The sales pipeline (forecast, board with drag and drop, list), companies and the people at them, each with a
timeline of notes, calls, meetings, emails and deal moves; suggested leads from market analyses and inquiries;
quotes and invoices with the Swiss QR-bill (drafts, sending with approval, payments, reminders, CSV export);
duplicate warnings, undo, and **Approvals** for deleting, merging and anything sent to clients; **Follow-ups** (what
needs a nudge, with follow-up emails that wait for approval) and **Reports**. See [CRM](12-crm.md).

## Team (*team*)

The employee directory, everyone's holiday balance and time off on a team calendar, onboarding and offboarding
checklists, hours from time tracking, and **Approvals** for every change to employee data. With only your own
record you see **Me**: your holidays and time off, and **Request time off**. See [Team](13-team.md).

## Log

The event log: everything that happened (sign-ins, calls, messages, tools Alfred used, system events). Admins can
export it, or the blocked-access log, as a spreadsheet file (CSV) for 7, 30, 90 or 365 days or everything,
optionally only sign-ins and security events, calls, messages, system events or errors.

## Systems

The health of everything Alfred depends on: the language model, Telegram, phone, speech, email, storage, backups,
the restore test, the self-hosted search, automations, Google, and each home server (load, memory, disk,
temperature, containers). Admins also see the monthly **update report** (pending updates and security fixes per
machine, newer container images, reboots needed) with **Check now**; nothing is installed automatically.

## Personnel (*admins*)

People and their roles, connections, dashboard logins and two-factor status; access attempts from unknown senders;
data requests. See [Administration](09-administration.md#people-and-roles).

## Usage (*admins*)

What Alfred costs: tokens, minutes and characters per service, cost per day, per channel, per person and per API
key, the phone bill so far this month, what Anthropic actually billed (if set up), and **Monthly budgets** with
warnings. See [Usage and budgets](09-administration.md#usage-and-budgets).

## Settings (*team*)

Household-wide settings (admins by default): the AI model and which key pays, household quiet hours, email (answers
to outside mail, automatic filing of inquiry attachments) and call screening with blocked numbers. See
[Settings](09-administration.md#settings).

**Roles and groups** (admins and cofounders by default): create and edit roles, groups, "who can do what", and
**View as** a role or a person. See [Roles and permissions](09-administration.md#roles-and-permissions).

## Profile

Your personal settings: account, sign-in and security, connections, calendar, how Alfred serves you (quiet hours,
briefing, voice replies, continuing across channels), memory, your data. See
[Your profile](01-getting-started.md#your-profile).

## Alfred's portrait (the helper)

In the bottom corner of every tab except Chat. Click it to ask Alfred anything about the page you're on ("how do I
add a reminder here?") or to have him do it. His portrait shows what he's doing: listening while you type,
thinking, checking his tablet while he looks something up, pleased when done. Now and then he pops up with a
greeting, or a raised finger when a system needs attention; you can switch these pop-ups off in the helper.
During a phone call, the button shows him keeping an eye out.

## Looks

**Stark**, **Batcave**, **Matrix** and **Alfred**, switched at the bottom of the side menu; the homepage, legal pages
and the browser-tab icon follow. See [Choosing a look](01-getting-started.md#choosing-a-look).

Next: [Administration](09-administration.md).
