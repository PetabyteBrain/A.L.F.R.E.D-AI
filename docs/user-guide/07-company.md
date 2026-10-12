---
title: Company work
description: Company files and to-do list, market analysis, inquiries from outside and matters for the team.
sidebar_position: 7
---

# Company work

**Who:** admins and cofounders, unless a section says otherwise. Clients, contacts and deals are in the
[CRM](12-crm.md); a market analysis' shortlist shows up there as suggested leads.

## Company files

The company's shared files are available to admins and cofounders, in chat ("look in the company files…") and on
the dashboard (**Files**, the button with your company's name). Alfred reads existing company documents to answer questions and saves notes,
lists, reports and filed attachments there; on the Files tab you can edit and organise them yourself.

## The company to-do list

The company's to-do list is an Excel workbook in the company files. Alfred works with it directly:

> "What's on the company to-do list?"
> "Add 'renew the domain' to the to-do list, importance 4."
> "Set 'new website texts' to in progress." / "Mark the domain renewal as done."
> "Move 'office party' to the future section."

Only the cells concerned change; the rest of the workbook (formatting included) stays exactly as it was, and a
backup copy is made before each change. If the workbook is open in Excel, Alfred waits and tells you. The top
items also appear in the morning briefing.

## Market analysis: finding new clients

> "Find hairdressers within 10 km of Lucerne that have no or a weak website."
> "Do a thorough market analysis of flooring businesses within 10 km of Bern."

**What Alfred does**

1. Finds businesses of the chosen trades within the radius (0.5 to 30 km) from public business listings
   (OpenStreetMap). About 150 trades are built in, in English and German; for anything else he can search by
   listing tags or words in the business names.
2. Checks each business's website: does it exist and load, HTTPS, mobile layout, speed, signs of being outdated,
   online booking.
3. Looks up a website for businesses that don't list one.
4. Scores how well each business fits ("no website" scores highest) and writes a report into the company files
   under **Market analysis/**: a Markdown summary and/or an Excel list.

It runs in the background; you get a Telegram message with the top results and where the report is.

**How thorough**: *quick* (no website look-ups), *standard*, *thorough* or *complete* (every business without a
listed website). Look-ups beyond the free ones cost about one cent each; if a run would use more than 50 paid
look-ups, Alfred tells you the estimate and asks first.

**Options you can mention**: focus on businesses with *no website* or a *weak website*, a stricter or looser fit
threshold, leave out chains, a shortlist of the best *n*, sort order, Markdown and/or Excel, your own title and
notes. Repeat runs mark businesses that are new since last time.

Only public business information is used; contacting the businesses stays your decision.

## Inquiries from outside

When someone who isn't registered (a client, a lead, a supplier) emails Alfred:

- **If public information answers the question** (what the company offers, starting prices, how projects
  start), Alfred answers politely in the company's name.
- **Everything else** (quotes, projects, complaints, anything personal or needing a decision) goes to the team
  (below), and the sender gets a reply that the team will look into it and get back to them.
- **Spam, newsletters, sales pitches, auto-replies** are filed and never answered.
- Alfred answers only senders whose address the mail provider could verify, at most a few times a day per sender,
  and never answers automated mail, so there are no mail loops.
- Alfred reads safe attachments of an inquiry (as information only) and lists them in the message to the team.

The answers come only from the company's public information, which your admin maintains. Alfred never makes
commitments, promises dates or invents prices. He writes in English.

**Settings (admins): Settings → Email**: choose whether Alfred may *answer what public information covers*
(default), only *acknowledge* every message, or send *no replies* at all (the team still hears about everything
that needs a person). You can also switch on **File inquiry attachments automatically** (into the company files
under Mail/Attachments/&lt;year&gt;/&lt;sender&gt;; off by default).

## Matters for the team

**Who:** anyone can have something passed to the team; admins and cofounders receive and handle them.

When something is beyond Alfred's authority or needs a human decision (an approval, money, an exception, a
client's quote), he passes it to the admins and cofounders and tells the person who asked that someone will get
back to them. This works on every channel: "Can I get an advance on my pay?" from a friend on Telegram ends up
with you just like an inquiry by email.

**How you're told**, depending on urgency:

| Urgency | How |
|---|---|
| Low | Telegram message without sound |
| Normal, high | Telegram message with sound (silent during your quiet hours) |
| Critical | Telegram message with sound even in quiet hours, plus a phone call to the person it was addressed to (or the first admin), which waits for the end of their quiet hours |

Without Telegram, the message comes by email. The person it was addressed to also gets a task ("Reply to …").

**Handling it**

- Tell Alfred what to answer: "Reply to email #12: we can start in May, I'll send a quote by Friday." He shows you
  the reply and sends it after your yes; answering the email closes the matter.
- Or close it yourself: "Close escalation #4, I called them" or **Mail → For the team → Mark handled**.
- "Which matters are open?" lists them.

Next: [The dashboard, tab by tab](08-dashboard.md).
