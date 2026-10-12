---
title: Troubleshooting and FAQ
description: Common questions and what to do when something doesn't work.
sidebar_position: 11
---

# Troubleshooting and FAQ

## Alfred doesn't answer me

- **On Telegram**: is your Telegram account linked *and verified* (Profile → Connections shows **Verified**)? Make
  sure you write to the bot in a private chat, not a group.
- **On the phone**: is your number verified? A withheld number (caller ID hidden) gets a busy tone.
- **By email**: write from an address on your profile that shows **Verified**. If your mail provider's sender check
  fails, Alfred doesn't act on the mail and warns you on Telegram; try again from that address directly (not via a
  forwarding service).
- **Still nothing?** Check **Systems** on the dashboard or ask your admin; the status at the top of the dashboard
  shows if a system needs attention.

## I didn't get my verification code

- **Email code**: look in your spam folder; wait a minute and press **Send again**. Codes expire after 10 minutes.
- **Phone code**: Alfred calls from his own number; answer and listen for the 6 digits. If you're in quiet hours and
  someone else started the verification, it's refused until your quiet hours end.
- **Telegram code**: send the code *to the bot* from the Telegram account you're adding.
- After several codes in a day, new ones are blocked until the next day. An admin can **Mark verified** instead.

## I lost my phone (two-factor sign-in)

Use one of your backup codes. Without them, ask another admin to **Reset 2FA** on Personnel; then sign in with your
password and set up two-factor sign-in again.

## A reminder or call came too late

- Check your **quiet hours** (Profile → How Alfred serves you): calls others asked for wait until they end.
- Alfred retries unanswered calls twice, 10 minutes apart.

## Alfred understood the wrong date

Say the date explicitly ("Friday the 9th") or correct him ("I meant next week's Friday"). He always tells you which
date he understood when a phrase could mean two things.

## I don't see the Mail tab, the company files or the Settings tab

They depend on your roles: by default Mail and company files are for admins and cofounders, Personnel and Usage for
admins, and Settings shows what your roles allow there. If you think you should have access, ask your admin; on
**Settings → Roles and groups → Who can what** they can see exactly which role gives what.

## Email: Alfred's sign-off appeared twice / looks wrong

The layout adds the signature; Alfred removes a closing line he wrote himself. If an email still looks wrong, open
**Mail → Template manager**, check the layout's signature and footer, and use **Send a test to me**.

## Email: a reply from a client didn't reach me

Replies to mail Alfred sent for you come back to Alfred and are passed on to you on Telegram (or by email if you
have no Telegram). They're also in **Mail → Inbox**. If someone replied to your own address instead, Alfred never
sees it.

## An attachment shows as "risky"

Programs, scripts, Office files with macros and files whose content doesn't match their name are flagged and never
opened by Alfred. Download it only if you trust the sender and know what it is.

## A file preview doesn't work

Old formats (.doc, .xls, .ppt) and large files may take a few seconds the first time. Some videos (for example iPhone
HEVC) can't play in the browser; download them. Unknown file types can only be downloaded.

## The market analysis found few or no businesses

Try a larger radius, another word for the trade (English or German), or name some words that appear in the business
names. Very small places may have few public listings.

## Something else

Ask Alfred: "How do I…?" (on the dashboard via his portrait), or contact your admin. Admins: the **Log** and
**Systems** tabs show what happened and what's down.
