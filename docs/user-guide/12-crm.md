---
title: CRM: clients and contacts
description: The sales pipeline and deals, companies and the people at them, suggested leads, the timeline, undo and approvals.
sidebar_position: 12
---

# CRM: clients and contacts

**Who:** admins and cofounders by default (permissions *CRM: …*). An admin can give other roles the CRM too, for
example limited to the records they own or that belong to their group (see
[Roles and permissions](09-administration.md#roles-and-permissions)).

The CRM keeps track of the companies you work with (clients, leads, prospects, partners, suppliers) and the people at
them, your **deals** as they move through the sales pipeline, and a timeline of everything that happened: notes, calls,
meetings, documents, deal moves, and every email to or from them. It also suggests new leads from your market
analyses and from inquiries.

## By chat, phone or email

You can do most things just by telling Alfred:

> "Add Acme AG as a new lead, they're at acme.example.com, contact Erika Mustermann, erika@acme.example.com."
>
> "What's open with Acme?"
>
> "Log that I called Erika: she wants the offer by Friday."
>
> "Acme signed, make them a client."
>
> "Undo that."
>
> "New deal for Acme: website relaunch, CHF 12'000, should close end of November."
>
> "Acme signed the website relaunch, mark it won." / "We lost the Atelier Nord deal, too expensive."
>
> "How does the pipeline look this month?"
>
> "Any new leads to look at?"

Alfred looks a record up before he changes it and only uses details you give him. If something looks like a record
that already exists, he tells you which and asks before creating a second one. He never deletes or merges anything
himself: that becomes a request that someone with approval rights decides (see below).

## The CRM tab

The tab has the same layout as Mail: views on the left (**Pipeline**, **Companies**, **People**, **Leads**,
**Approvals**), a list or the board, and a record that opens in place.

### Pipeline

- At the top: **Open pipeline** (all open deals), **Weighted** (each deal's value times its chance of winning),
  **Won this month** (and this quarter), the **win rate** of the last 90 days, and **Needs attention** (deals past
  their expected close date, next steps that are due). The bars show the next six months by expected close: the
  full value, the weighted value, and what was won.
- **Board**: one column per stage with the number of deals and their total. **Drag a card** to another stage, or use
  its **Move** menu (handy on a phone). Moving to **Won** or **Lost** asks why (optional; the usual reasons for losing
  are one tap). Won and Lost show the last 30 days.
- **List**: all deals, open / won / lost, sorted by close date, value or recent changes.
- **Only mine** and the search work on both.
- **Stages** (for whoever may set up the pipeline): rename stages, set each one's probability for the forecast,
  change the order, add one (for example *Demo booked*) or remove an empty one. Won and Lost always come last.

### A deal

Open one from the board, the list or its company. The header shows the value and its weighted value; the track
below shows where it is in the pipeline (click a stage to move it there, or use the stage switch for Won and Lost).
The **next step** with its date sits on top; then company, contact person, expected close (marked when it has
passed), probability (from the stage, or your own), source and owner. Deals have their own timeline (log a call
or a note about this deal; stage moves appear by themselves) and history with undo. **New deal** is on the CRM tab
and on every company.

### Leads

Suggestions to review, never added by themselves:

- **From market analyses**: every business on an analysis' shortlist, with its address, public website and contact
  details, the fit score and why it fits.
- **From inquiries**: people who wrote to Alfred with an inquiry but aren't in the CRM yet (one suggestion per
  address; the company is guessed from the address unless it's a free-mail provider).

Tick the ones you want and **Add as leads** (a company with status *Lead*, plus the person for inquiries), or use
**Add**, **Link…** (attach it to a company you already have: fills in its missing details, or adds the person
there) and **✕** (dismiss) on each. If something looks like a record that already exists, you choose: **Link to a
company** or **Add anyway**. The dropdown shows what was added, linked or dismissed before.

- **Companies** and **People**: search by name, email, phone, industry, notes or tag (press **/**); sort A–Z or by
  recently changed. The sidebar filters by **status** and **tag**, **Only mine** (records you own), and **Archive**.
  On a phone, the filters fold away under **Filters**.
- **New company** / **New person** (or press **n**): fill in what you know. While you type, Alfred warns if it looks
  like a record that already exists (same name, email, phone or website) so you can open that one instead. If you
  save anyway, you're asked once more; **It's a different one: create anyway** creates it.
- **More fields**: add your own fields (for example *VAT number* or *Budget*). **Tags** group records across
  companies (for example *vip*, *web*). **Owner** is responsible for the record; **Group** shares it with a team.

### A company or person

- The header shows the **status**; change it right there with one click.
- **Facts**: email, phone (tap to call), website, industry, address, owner, source and your extra fields.
- **People**: everyone at the company; **Add a person** creates one already linked to it.
- **Timeline**: write a **note**, log a **call**, **meeting** or **document** (with an optional date if it happened
  earlier). Emails to and from the company's or person's addresses appear by themselves (only mail you're allowed to
  read), and so do calls Alfred made to their numbers for you. Filter by kind; open an entry to read the details.
  You can remove entries you wrote yourself.
- **History**: every change, who made it and when. **Undo** reverts your own latest change. After saving, a message
  at the bottom of the screen also offers **Undo** for a few seconds.
- **More**: **Archive** hides a record from the lists without deleting it (undoable; find it again with **Archive** in
  the sidebar, then **Restore**). **Merge into…** and **Delete…** send a request for approval.

## Approvals

Deleting a record or a deal for good, and merging two records into one, never happen directly.

1. Someone asks (on the record's **More** menu, or by telling Alfred).
2. Everyone allowed to approve gets a Telegram message with **Approve** / **Reject** buttons, and the request waits
   under **CRM → Approvals** (the number next to it shows how many are waiting for you).
3. One of them decides, optionally with a note. You may approve your own request.
4. The person who asked is told the outcome.

When two records are merged, the one you keep gets the other's timeline, people and any details it was missing; the
other is then deleted. You can withdraw your own request while it's waiting.

## Who sees what

- With *CRM: see every client and contact*, you see all records.
- Otherwise you see the records you own, the ones you added, and those filed under one of your groups (and the people
  at those companies).
- Emails show up on a timeline only if you may read them anyway (company mail needs access to the Mail tab).

Next: back to the [contents](index.md).
