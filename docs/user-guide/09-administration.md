---
title: Administration
description: People and roles, settings, usage and budgets, data requests, logs, systems, backups and automations.
sidebar_position: 9
---

# Administration

**Who:** admins only.

## People and roles

**Personnel** lists everyone in the registry.

- **Add a person**: name, role, optional notes. Then add their connections (phone, Telegram, email). New
  connections must be confirmed with a code (see
  [Connecting](01-getting-started.md#connecting-your-phone-telegram-and-email)); you can start the code for them,
  or **Mark verified** if you know the connection is theirs (logged).
- **Dashboard login**: give someone a username and password (only roles with dashboard access can use it).
  **Reset 2FA** helps someone who lost their phone: they sign in with the password only and set it up again.
- **Change role, disable, delete**: a person with history can only be disabled, not deleted. The last active admin
  can't be demoted, disabled or deleted. Changing someone's role, password or status signs them out everywhere.
- **Call** next to a person rings them now (with a purpose, if you like).
- **Access attempts**: who tried to reach Alfred without being registered (kept 90 days).

**Roles**: *admin*, *cofounder* and *friend* come built in. Each role decides which channels work (Telegram, calls,
dashboard, email) and which of Alfred's abilities it gets. New roles and their abilities are set up by whoever runs
the server (see the README).

## Settings

**Settings** holds everything that applies to the whole household.

| Card | What it does |
|---|---|
| **AI model & billing** | Which API key pays, which Claude model answers chats (Haiku, Sonnet, Opus, Fable), how hard the bigger models think, and a separate model for phone calls (fast by default). |
| **Role permissions** | Switches that narrow a role, e.g. whether cofounders may see, run or edit automations. Admins always have everything. |
| **View as role** | See the dashboard and the chat with another role's permissions, to check what they can do. A banner shows it's a preview; **Back to my view** ends it. |
| **Household quiet hours** | The default quiet hours everyone follows unless they set their own (see [Quiet hours](03-your-day.md#quiet-hours)). |
| **Email** | How Alfred answers mail from outside (*answer what public information covers*, *acknowledge only*, *no replies*), automatic filing of inquiry attachments, and how long mail is kept. See [Inquiries from outside](07-company.md#inquiries-from-outside). |
| **Call screening** | Whether unknown callers can leave a message, recent screened calls, and **blocked numbers** (block / unblock). |

The public information Alfred uses to answer outside inquiries (what the company offers, starting prices, how to
get in touch) is a text file maintained on the server; keep it to things you'd publish on your website.

## Usage and budgets

**Usage** shows what Alfred costs: Claude tokens and cost per day, per channel, per person and per API key (cache
savings included), speech minutes and characters, web searches, the phone provider's bill so far this month and, if
configured, what Anthropic actually billed.

**Monthly budgets** (in USD, per calendar month): set a limit per service (Claude incl. web search, phone, speech)
and in total. At 80 % and 100 % every admin gets one Telegram message per month and level. Budgets only warn;
nothing is switched off.

## Data requests

When someone asks on their profile for their data to be deleted, every admin is told on Telegram. On
**Personnel → data requests** you can **Export first** (a ZIP of their data), then **Confirm** or **Reject**.
Deleting either removes their history (conversations, recordings, notes, reminders, tasks, personal mail and
files, settings) or the whole account. You can also export or delete someone's data directly from the person
editor (for requests by email), with your password. Your own request needs another admin, unless you're the only
one. See [Privacy and safety](10-privacy-and-safety.md).

## The event log

**Log** shows everything that happened. **Export**: choose the period and the kind of events and download a CSV
that opens in Excel. Sign-in and security events are kept a year. Each export is itself logged.

## Systems, home servers and backups

- **Systems** shows the health of every service and machine. Problems with a home server (unreachable, disk almost
  full, too hot, under-voltage, a watched container stopped) are reported to admins on Telegram once, plus a
  "recovered" message. A separate watchdog on another machine tells you if Alfred himself stops answering.
- **Ask Alfred** about the home servers: "How are the servers?", "Show me the logs of the n8n container on host-b",
  "Restart the n8n container" (he asks before restarting, and only containers that are allowed). "Is anyone on
  the Minecraft server?" works for everyone.
- **Monthly update report** (1st of the month, or **Check now** on Systems): pending updates and security fixes per
  machine, newer container images, reboots needed. Report only; installing stays your job.
- **Backups** run every night to two other devices (a backup machine and the storage server), and once a month an
  automatic restore test opens both copies to prove they work. Their status is on Systems; failures alert admins.

## Automations (n8n)

Alfred is connected to your automation server. Ask him to list workflows, show their runs, run one, or even build
or change one ("make a workflow that emails me the weather every Monday"); he asks before deleting or overwriting
anything. Cofounders get these abilities as far as the **Role permissions** allow; deleting workflows stays
admin-only. The Tasks tab shows the workflows with their last run and failures.

Next: [Privacy and safety](10-privacy-and-safety.md).
