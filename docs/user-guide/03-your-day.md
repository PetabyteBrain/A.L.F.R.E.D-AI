---
title: "Your day: reminders, calendar, briefing, quiet hours"
description: Reminders, the calendar and agenda, Google Calendar, the morning briefing and quiet hours.
sidebar_position: 3
---

# Your day: reminders, calendar, briefing, quiet hours

## Reminders

**Who:** everyone. Admins and cofounders can also remind other people.

**Ask Alfred**

> "Remind me tomorrow at 9 to call the bank."
> "Remind me every weekday at 8:30 to check the post."
> "Call me at 6 to remind me about the parcel."
> "Remind Max on Friday at 10 to send the offer." (admins and cofounders)
> "Remind me 15 minutes before the dentist appointment." (tied to a calendar event)

**How reminders arrive**

- **By Telegram** (default): a message with **Done** and **Snooze 10 min** buttons. If you ignore it, Alfred can
  call you after 10 minutes instead: ask for "and call me if I don't react".
- **By phone call**: say "call me".
- Repeats: daily, weekdays, weekly or monthly; the time stays the same across summer/winter time changes.
- After a reminder, you can also just answer "done" or "later".

**Tied to a calendar event.** A reminder can belong to a Google Calendar event ("x minutes before"). It then moves
when the event moves and is not sent if the event is deleted. If you set a separate reminder that looks like an
existing event, the dashboard marks it **probably a duplicate** so you can tie it to the event or drop it.

**On the dashboard:** **Tasks → Calendar** or **List**. Click a day or time slot and choose **Remind me**, or use
the reminders list to change, snooze or delete them.

## The calendar and agenda

**Who:** everyone with a dashboard login (calendar view); everyone can ask Alfred about their schedule.

Alfred keeps one combined view of your day: Google Calendar events, reminders (including repeats), tasks with a
due date, planned calls, and the due dates of board items assigned to you (🗂; click one to open it on its
[board](14-boards.md)).

**Ask Alfred**

> "What do I have on Saturday?"
> "Anything between 9 and 11 tomorrow?"
> "Which reminders do I have this week?"

**On the dashboard: Tasks → Calendar**

- **Month, week or day** view, with previous/next, **Today** and a date picker. Your choice is remembered.
- Colours by category: meeting, reminder, task, call, work, personal, other. Filter by category.
- **Click an empty spot** (a day, a time slot or the all-day strip) or **+ New** to create something. You choose
  what Alfred should do with it:
  - **Remind me**: by Telegram or call, optionally repeating, optionally also as a calendar event.
  - **Task**: due at that time, optionally with a follow-up.
  - **Calendar only**: an entry in your Google Calendar that Alfred doesn't act on.
- **Click an entry** to change its title, time or category, tie a reminder to an event, or add "remind me before".
- **Show** your own entries (calendar, reminders, tasks, calls) and the **deadlines** from the CRM and the boards,
  each source on its own (every board, the **Sales pipeline** with deals' expected close and next steps, and
  **Invoices & quotes**). *Mine* shows your own deadlines; *Everything I can see* the whole team's.
- **Deadlines look different**: outlined and dashed, with a tag saying where they come from (the board's key, DEAL,
  INV, QUO); your own calendar entries are solid. Click one to see it, **Open it** where it lives, and:
  - **Sync this event**: put it into your Google Calendar as an all-day event (✓ on the entry); if the date or
    title changes, **Update the event**, or **Remove from my calendar**.
  - **Sync all events from …**: the whole board, the pipeline or your invoices, kept up to date by themselves:
    new deadlines appear, moved ones move and finished ones disappear (checked hourly). Only your own deadlines
    are added. **⟳ Sync to Google…** lists every source to switch on or off in one place.
  A synced copy in Google shows only once here, as the deadline.

The Tasks tab keeps your place (view, date, filters) when you switch to another tab and back.

## Google Calendar

**Who:** everyone; each person links their own calendar.

1. Dashboard: **Profile → Calendar → Link Google Calendar** (without a dashboard login, ask your admin how your
   operator handles linking).
2. Sign in with Google and allow access.

Then Alfred can list, add, move and delete events on **your own** calendar when you ask: "Put lunch with Erika on
Thursday at 12:30 in my calendar", "Move the dentist to 3 pm". Deleting always needs your confirmation.
**Unlink** on the same card ends access at once.

**Free/busy sharing** (off by default): turn it on if colleagues may schedule meetings with you. Others then only
learn *when* you're busy, never what the events are. See [Meetings](04-tasks-calls-meetings.md#meetings).

## The morning briefing

**Who:** everyone.

Every morning (or weekdays only) at a time you choose, Alfred sends a short briefing:

- the weather for your town,
- today's calendar, reminders and planned calls,
- what happened overnight (calls made for you and their outcome),
- your open tasks,
- for admins and cofounders: the most important company to-dos (and on Mondays, items that have been "in
  progress" for a long time), and failed automations for admins,
- with the CRM: the clients and deals you should follow up on ([CRM](12-crm.md#follow-ups)),
- with the employee directory: who's away today, and onboarding or offboarding steps that are yours today
  ([Team](13-team.md)),
- with the boards: your items due today or overdue, and task requests waiting for you ([Boards](14-boards.md)).

**Set it up:** **Profile → How Alfred serves you → Daily briefing** (time, every day or weekdays, Telegram or a
call, weather town), or ask: "Send me a briefing every weekday at 7:30 by Telegram, weather for Bern."

**Any time:** "What's my day like?" gives you the briefing on the spot.

## Quiet hours

**Who:** everyone. Admins also set the household default.

Quiet hours are the times Alfred won't disturb you on someone else's behalf (default 22:00 to 07:00).

During your quiet hours:

- calls that **someone else** asked for (a call, a reminder they set for you, a task check-in, a meeting
  invitation) wait until your quiet hours end, unless they marked it **urgent**;
- messages from Alfred arrive **silently** on Telegram (no sound);
- things **you** set up for yourself (your own reminders, your briefing, "call me") still happen.

**Set it:** **Profile → How Alfred serves you → Quiet hours**: follow the household default, set your own window,
or switch quiet hours off. Or ask: "Set my quiet hours to 23:00 to 06:30."

When you ask Alfred to contact someone who is in their quiet hours, he tells you and asks whether to send it now
anyway, later, or only to the others.

Next: [Getting things done: tasks, calls, meetings](04-tasks-calls-meetings.md).
