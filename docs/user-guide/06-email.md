---
title: Email
description: Alfred's own address, sending and receiving, attachments, the Mail tab, groups and tags, and templates.
sidebar_position: 6
---

# Email

Alfred has his own email address. You can write to him, have him send mail for you, and (admins and cofounders)
manage everything that arrives in the **Mail** tab.

## Writing to Alfred

**Who:** everyone with a verified email address on their profile.

Write from one of **your verified addresses** (Profile → Connections; you can add several). Alfred treats your
email like any other message from you, with exactly what your role allows, and answers **in the same thread**.

> "Summarise the forwarded mail and tell me what I need to do."
> "Put the appointment in this mail in my calendar."
> "Remind me on Friday to answer this."
> "File the attached contract under Contracts/2026."

**Good to know**

- Forwarded or quoted text counts as information only: Alfred won't follow instructions written in someone else's
  mail unless you ask him to.
- For security, Alfred only accepts mail as coming from you if your mail provider's sender check passes. A mail
  that pretends to be from your address but fails the check is not acted on, and you get a warning on Telegram.
- Attachments are listed for Alfred with numbers; he reads the first few and can file any of them, now or later.

## Having Alfred send email

**Who:** everyone can have Alfred email **themselves**; admins and cofounders can email anyone.

> "Email me the shopping list."
> "Email max@example.com that the offer is attached and valid until Friday, and attach Offers/Offer-12.pdf."
> "Send Ms Muster the offer follow-up; the offer went out on 3 October."

**How it works**

- **To yourself**: sent straight away, to your primary address.
- **To anyone else**: Alfred shows you the complete email (recipients, subject, layout, attachments, text) and
  sends it only after your **yes**.
- Mail for you goes out from Alfred's address as **"Your Name via Alfred"**, with you in copy. **Replies come back
  to Alfred**, who files them and tells you on Telegram; just tell him what to answer.
- Attachments can come from your files or the company files.
- Each email gets a layout with a banner, signature and footer; Alfred writes the text, the layout adds the rest.
  "Company" mail (formal, in the company's name) is for admins and cofounders.
- **Text templates**: reusable emails such as "Offer follow-up" (see [Templates](#the-template-manager)). Alfred
  fills in the fields and asks you for anything he doesn't know.
- At most 30 emails a day per person.

## The Mail tab

**Who:** admins and cofounders.

The Mail tab shows Alfred's mailbox. Everyone sees their own mail plus the company's; nobody sees anyone else's
personal mail.

**Boxes (left)**

| Box | What's in it |
|---|---|
| **Inbox** | Mail you sent to Alfred, and replies to mail he sent for you |
| **Unread** | Everything you haven't opened yet |
| **Starred** | What you starred |
| **Inquiries** | Mail from outside to the company (unknown senders, clients) |
| **Sent** | Everything Alfred sent for you or the company |
| **Archived** | What you archived |
| **All mail** | Everything you may see |
| **For the team** | Matters passed to the team (see [Company work](07-company.md#matters-for-the-team)) |

Unread mail is bold and marked with a bar; counts show what's new.

**Working with the list**

- **Search** at the top; **Unread only** to filter.
- Tick emails (or the box at the top for the whole page) for **bulk actions**: Read, Unread, Star, Archive, move to
  a group, add a tag.
- Click the star on a row to star it. 50 emails per page with ‹ › to page.

**Reading an email**

- The whole conversation is shown, with each message collapsible.
- **↑ / ↓** go to the newer or older email without going back.
- **Star**, **Archive**, **Mark unread**, download the original (**.eml**).
- **Sender check** shows whether the sender's address was verified by the mail provider.
- **Alfred:** summarises what he did with it (answered, passed to the team, filed…).
- Group and tag pickers, and tag chips you can remove with ×.

**Keyboard:** `j` / `k` next and previous, `s` star, `e` archive, `Esc` back to the list.

Read, starred and archived are **personal**: archiving an inquiry doesn't hide it for your colleague.

## Groups and tags

**Who:** admins and cofounders.

- A **group** works like a folder: an email is in at most one group (e.g. *Clients*, *Suppliers*).
- **Tags** are coloured labels; an email can have as many as you like (e.g. *Lead*, *Urgent*, *Invoice*).
- **My groups / My tags** are yours, for your own mail. **Team groups / Team tags** are shared by admins and
  cofounders, for company mail.
- **+** next to a heading creates one; the **pencil** renames it, changes its colour or deletes it (the emails
  stay). Click a group or tag to see its emails.
- Alfred can do it too: "tag the shop inquiry as Lead", "put Max's mails in the Clients group", "archive the
  newsletters".

## Attachments

**Who:** admins and cofounders on the Mail tab; everyone can have Alfred file attachments of their own mail.

Under each message, every attachment shows its name and size with:

- **Preview**: PDFs and pictures directly, Word/Excel/PowerPoint in their original layout, CSV and text as data.
- **Download**.
- **Save to Files**: into your own folder or the company files, any folder, optional new name. Existing files
  are never overwritten.

**Risky files** (programs, scripts, Office files with macros, files whose content doesn't match their name,
archives containing such files) are marked in red. They can only be downloaded, after a warning, and are never
previewed, filed or shown to Alfred.

**Ask Alfred:** "File the PDF from Max's email under Clients/Acme" works days later too. When you email documents
to Alfred, write where they should go ("file under Admin/Insurance").

Attachments are kept as long as their email.

## The Template manager

**Who:** admins and cofounders.

**Mail → Template manager** holds the email **layouts** and reusable **text templates**.

**The list**: cards with an **Active** switch, how often and when each was last used, and **Edit**,
**Duplicate**, **Archive**. Filter by layouts or texts, active only, archived; search.

**Layouts** decide how an email looks:

- banner (upload a PNG or JPEG, up to 1 MB; 1200 × 300 pixels works well),
- eight colours and a classic (serif) or modern (sans-serif) font,
- sender name, signature and footer lines (fields: `{assistant}`, `{company}`, `{website}`, `{tagline}`),
- **In the company's name**: formal mail for clients and outside contacts (counts as company mail).

**Text templates** are reusable emails: a subject and a text with fields such as `{name}`, `{first_name}`,
`{company}`, `{date}`, `{sender}`, or your own like `{offer_date}`, each with a layout. Start the text with the
greeting and leave out the signature; the layout adds it.

**Editing**: a live preview on the right shows the email exactly as the recipient sees it (plus the plain-text
version); type sample values for your fields to see them filled in. **Send a test to me** emails the saved
template to your own address.

**Rules**

- Only **active** templates are offered to Alfred and in the pickers. At least one layout always stays active.
- **Archive** instead of delete: archived templates can be restored; sent emails keep their own copy.
- The built-in templates (*Company*, *Alfred*, *Inquiry acknowledgement*) can be edited like the others. *Inquiry
  acknowledgement* is the reply people outside get when their mail goes to the team.

## Mail from people Alfred doesn't know

See [Inquiries from outside](07-company.md#inquiries-from-outside).

## How long mail is kept

Every email in and out is archived as a file on your storage server (your own mail in your folder under **Mail/**,
company mail in the company files under **Mail/**). Personal mail is kept 2 years, company mail 10 years (Swiss
rules for business correspondence), spam and automated mail 30 days. Your admin can change these periods.

Next: [Company work](07-company.md).
