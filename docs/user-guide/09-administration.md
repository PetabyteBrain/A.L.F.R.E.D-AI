---
title: Administration
description: People, roles and permissions, groups, settings, usage and budgets, data requests, logs, systems, backups and automations.
sidebar_position: 9
---

# Administration

**Who:** admins by default. Admins can hand single parts to other roles (see
[Roles and permissions](#roles-and-permissions)); roles and groups are also open to cofounders.

## People and roles

**Team → Accounts** lists everyone in the registry (it used to be the Personnel tab).

- **Add a person**: name, one or more roles, optional notes. Then add their connections (phone, Telegram, email). New
  connections must be confirmed with a code (see
  [Connecting](01-getting-started.md#connecting-your-phone-telegram-and-email)); you can start the code for them,
  or **Mark verified** if you know the connection is theirs (logged).
- **Dashboard login**: give someone a username and password (only if one of their roles has dashboard access).
  **Reset 2FA** helps someone who lost their phone: they sign in with the password only and set it up again.
- **Change roles, disable, delete**: tap the roles to switch them on or off (the first one is shown as their main
  role). A person with history can only be disabled, not deleted. The last active admin can't lose the admin role,
  be disabled or deleted. Changing someone's roles, password or status signs them out everywhere.
- **Call** next to a person rings them now (with a purpose, if you like).
- **Team → Access log**: who tried to reach Alfred without being registered (kept 90 days).

## Roles and permissions

**Settings → Roles and groups** (admins and cofounders by default).

Everything Alfred can do is a named **permission**, sorted into groups: channels (Telegram, calls, email,
dashboard), everyday things (own tasks, reminders, calendar, email, files…), team abilities (calling or messaging
other people, calls to businesses, company files, the mailbox, templates, escalations, market analysis…),
automations (see, run, edit, delete), boards and teams, and administration (accounts, roles, settings, usage, home servers, seeing
everyone's records). A **role** is a set of these permissions.

- **Roles tab**: pick a role on the left. Each permission can be **Allow**, **—** (not set: left to the person's
  other roles) or **Deny** (taken away, even if another role allows it). Changes apply immediately, in the chat and
  on the dashboard.
- **New role**: give it a name and start from nothing or from a copy of another role, then adjust. Example: an
  *Intern* role copied from Cofounder with **Deny** on "Calls to businesses".
- **Inherits from**: a role can build on a parent role; it gets everything the parent allows and denies, plus its
  own rules. The list on the left shows the family tree. A role can't inherit from itself, directly or in a circle.
- **Rename** a role any time; **Archive** one nobody uses any more (and no other role builds on).
- **Several roles per person** (on Team → Accounts): their permissions add up, and any Deny wins.
- **Groups tab**: a group is a set of people (a team, a client project); groups are also the teams on the boards
  (made on Team → Teams, with a manager and a colour). Tap roles to give them to the group and
  tap people to make them members; every member gets the group's roles on top of their own.
- **Who can what**: pick a person to see every permission they have and which role gives it ("What can Ivo do?"),
  or pick a permission to see everyone who has it ("Who can call businesses?").

**Rules that always apply:**

- The **admin** role is fixed: it has every permission and can't be edited, copied, inherited or given to a group.
- Nobody can change a permission they don't have themselves, or give a role that grants more than they have.
  Only admins can change admins.
- Nobody can take away their own access to the role editor by accident.
- Changing someone's roles or groups signs them out of the dashboard; every change is in the **Log**.

**View as** (on the same page): preview the dashboard, and talk to Alfred in the Chat tab, as a **role** or as a
**person** (all their roles together), using your own account's data. You never see more than you're allowed to
yourself. A banner shows it's a preview; **Back to my view** ends it.

## Settings

**Settings** holds everything that applies to the whole household.

| Card | What it does |
|---|---|
| **AI model & billing** | Which API key pays, which Claude model answers chats (Haiku, Sonnet, Opus, Fable), how hard the bigger models think, and a separate model for phone calls (fast by default). |
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
**Team → Accounts → data requests** you can **Export first** (a ZIP of their data), then **Confirm** or **Reject**.
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
anything. Each part (see, run, edit, delete) is its own permission; by default cofounders may see, run and edit, and
deleting stays with admins. The Tasks tab shows the workflows with their last run and failures.

Next: [Privacy and safety](10-privacy-and-safety.md).
