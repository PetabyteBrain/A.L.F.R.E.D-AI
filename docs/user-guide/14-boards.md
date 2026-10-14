---
title: Boards: team work, deadlines and requests
description: Work boards with their own item types and columns, team lanes, list and calendar views, assigning to people and teams, deadlines in your agenda, company files, comments, task requests and the sales pipeline as a board.
sidebar_position: 14
---

# Boards: team work, deadlines and requests

**Who:** admins and cofounders by default (permissions *Work boards: …* and *Teams: …*). Each board decides who may
see it, ask for work, work on it or manage it, per team, role or person (see below).

Boards hold the team's work: what needs doing, who does it, by when, and how far along it is. They aren't a separate
tool: items you're given show up in your **agenda** (the calendar on the Tasks tab), in the **morning briefing**,
on your **Team** page and on the **CRM** client or deal they belong to, and Alfred can work with them by chat.

## By chat, phone or email

> "Put a task on the website board for Max: logo draft, due Friday."
>
> "What's on my plate?" · "What's open for the design team?" · "Where are we with WEB-12?"
>
> "Move WEB-12 to review." · "WEB-12 is done." · "Give the contact form to Erika, it's urgent."
>
> "Comment on WEB-12: the client wants it in blue." · "Ask the design team for new photos for the about page."

Personal to-dos and reminders stay tasks and reminders; shared work for a team or a project goes on a board.

## The Boards tab

On the left: **My work**, **Requests**, and the boards you can see (with the number of open items).

### My work

Everything assigned to you on every board, and the deals you own, grouped by **Overdue**, **Today**, **Next 7 days**,
**Later** and **No due date**. Tick *Also my teams' unassigned items* to see work your teams haven't handed out yet.

### A board

Pick a view at the top (keys **1** to **4**); the board remembers yours.

- **Board**: one column per state. **Drag a card** to another column, or to another place in the same column. A
  column's number turns orange when it holds more than its limit.
- **Team lanes**: one horizontal lane per team, stacked one above the other, with the same columns running across.
  Drag a card into another lane to hand it to that team. Click a lane's name to fold it.
- **List**: every item in a table you can sort by any column; *Tree* shows sub-items under their parent. Change an
  item's state right in the list.
- **Calendar**: items on their due date, month by month. Drag one to another day to move its deadline; items without
  a date wait on the right and can be dragged onto a day.

Filter by type, person (*Me*, *Nobody yet*, anyone), team and whether to show items finished in the last 30 days;
search with **/**. **+ New item** at the top (key **n**) or **+ Add** in a column adds an item with just a title.

### An item

Click a card to open it beside the board. Everything saves as you change it:

- **Type** and **title**, **state**, **assigned to** (only people who can see the board), **team**, **priority**
  (urgent, normal, low), **due** and **start** date, **estimate** in hours, **parent** item, **tags**.
- **Description**: what exactly, why, and when it's done.
- **Client** and **deal** from the CRM (if you have the CRM).
- **Sub-items**: add them in one line; the card shows how many are done.
- **Files**: attach files from the company folders; they open in the Files tab with your own access.
- **Comments**: write *@Name* to let that person know; the person assigned hears about every comment.
- **History**: every change, by whom and when. **Remove** takes an item off the board (its creator, its assignee or
  the board's managers can; it can be restored).

Whoever is assigned gets a Telegram message, and on the morning an item is due (or overdue) it's in their briefing,
or in a short Telegram message if they don't get one.

### The sales pipeline as a board

The **Sales pipeline** appears among the boards: its columns are the pipeline stages and its cards the deals, with
their value and expected close date. Moving a card there moves the deal everywhere, and the team lanes show the
deals by team. Deals are opened and edited in the CRM, whose own Pipeline view stays as it is.

## Requests

Some people may **ask** for work on a board without adding items themselves. **Ask the team** (or telling Alfred)
sends a request to the team's **manager**, or to the board's managers if the team has none. They get it on Telegram
with **Accept** / **Decline** buttons and under **Requests**, where they can also assign someone right away or add a
note. Accepted requests become items on the board, and the person who asked is told either way. You can withdraw
your own request while it's open.

## Creating and setting up a board

**New board** (with *Work boards: create*): a name, an optional short key (item numbers then look like WEB-12), and
a starting point: *Agile* (epics, features, tasks and bugs; New, Active, Review, Done), *Simple* (to do, doing,
done) or *Client projects*. Choose who can work on it: everyone, chosen teams (everyone else may send requests), or
only you for now.

**Board settings** (the board's managers):

- **General**: name, key, what it's for; archive the board.
- **Item types**: add, rename, recolour, reorder or remove them (items keep their type).
- **Columns**: add, rename, reorder, set whether a column means *to do*, *in progress* or *done*, and an optional
  limit. A column with items in it can't be removed; a board always keeps one open and one done column.
- **Who can do what**: rules for everyone, a team, a role or a person, each with a level: **See it**, **Ask the team**
  (requests), **Work on it** (add, change, move items) or **Manage it** (this page too). The highest level that
  applies to someone counts. Whoever made a board manages it, and a team's manager can always work on boards their
  team is on.

## Teams

Teams are made on the **Team** tab (**Teams**, with *Teams: create teams…*): a name, a colour (the team's lane), a
**manager** (who decides the team's requests) and its members. A team is a group of people, so groups made under
**Settings → Roles & groups** can be used as teams too; groups that also give roles are changed there.

Next: back to the [contents](index.md).
