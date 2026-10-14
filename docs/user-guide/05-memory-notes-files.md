---
title: Memory, notes, files and search
description: What Alfred remembers, notes and lists, the file explorer and previews, documents, receipts and searching.
sidebar_position: 5
---

# Memory, notes, files and search

## What Alfred remembers about you

**Who:** everyone.

Tell Alfred once how you like things and he keeps a short note, used only in **your own** conversations, on every
channel.

> "Remember that I prefer calls after 9."
> "Remember my sister's birthday is on 4 March."
> "Forget the note about the gym."
> "What do you remember about me?"

**On the dashboard:** **Profile → Memory** lists every note; edit or delete them there. Alfred never stores
passwords, codes, card or account numbers, even if asked.

## Notes and lists

**Who:** everyone (own notes); admins and cofounders also in the company files.

Your notes and lists live on your own storage server, in a folder only you can reach (admins included).

> "Make a note: the plumber comes on Tuesday, code for the door is in my email."
> "Add milk and eggs to my shopping list." / "What's on my shopping list?" / "Tick off milk."
> "Note for the team: the launch is on the 15th." (company files: admins and cofounders)

- Notes are tidy Markdown files in **Notes/** (with sub-folders if you like); lists are checklists in **Lists/**.
- Alfred adds to an existing note rather than making duplicates.
- "Delete" moves a note to the trash; nothing is erased for good.

### Tidying up your notes

**Who:** everyone (own folder only, `.md` and `.txt` notes).

Ask Alfred to tidy your notes, or on the dashboard: **Files → Tidy up my notes…**. Choose **Folders** (structure:
new folders, moves, renames) or **Contents** (reformat a note, combine notes, split a note by its headings), and
optionally add a wish ("group the recipes"). Alfred shows a plan with a tick box per step; only the ticked steps
run when you press **Apply** (in chat: after your yes). Replaced and combined notes go to the trash, so nothing
is lost.

## The file explorer

**Who:** everyone with a dashboard login.

**Files** is a full explorer for your storage:

- **Mine** (your folder), the company's files (the button carries your company's name; admins and cofounders)
  and **All people** (admins: everyone's folders).
- Breadcrumbs to move around; **New** note, list, text file or folder; **Upload** (or drag files and whole folders from your computer onto the list; drop them on a folder to put them in it; up to 50 MB per file); rename/move; download;
  delete (to the trash).
- Open files as tabs: Markdown with **Edit** and **Preview** and a formatting bar, lists as checklists,
  **Ctrl+S** to save. A dot marks unsaved changes. If someone else changed the file meanwhile, you're warned
  instead of overwriting their change.
- **Previews**: PDFs, pictures, audio and video play in the page; Word, Excel and PowerPoint open in their original
  layout (with a **Quick view** of their text and tables); archived emails (`.eml`) show their text.
- **Page width** hides the explorer so the open file uses the whole width (Esc to go back).
- **Recently viewed** and **Recently edited** list the files you opened or changed lately.

The Files tab keeps open files and unsaved edits when you switch to another tab and back.

## Questions about your documents

**Who:** everyone (own files); admins and cofounders also the company files.

> "What does our offer say about delivery time?"
> "Find the rental contract and tell me the notice period."

Alfred searches your files by meaning (in English and German), reads the right one (Word, Excel, PDF, text) and
tells you which file the answer came from.

## Saving files you send

Send a file on Telegram or attach it to an email and tell Alfred where to put it: "save this under Contracts/2026",
"file it in the company folder Offers". Existing files are never overwritten: a second copy gets " (2)". See also
[Email attachments](06-email.md#attachments).

## Receipts

**Who:** everyone.

Send a photo of a receipt (Telegram or email) and say "log this receipt" (or "company receipt"). Alfred reads the
date, shop and total, files the picture under **Receipts/&lt;year&gt;/** and adds a line to
**Receipts/receipts-&lt;year&gt;.csv**, which opens in Excel. He tells you what he logged; if he can't read a value,
he asks instead of guessing.

## Searching past conversations

**Who:** everyone (own conversations and calls made for them); admins search everyone's.

> "What did Max say about the invoice?"
> "When did we talk about the new office?"

Alfred searches all your past conversations on every channel and answers with the date and what was said. On the
dashboard, the **Archive** tab has a search box and filters (person, channel, dates).

## Searching the web

**Who:** everyone.

> "What are the opening hours of the post office in Thun on Saturday?"
> "Any news about the train strike?"

Alfred searches the web for current information and tells you where it came from (links in Telegram, the web chat
and email; the source named on calls).

Next: [Email](06-email.md).
