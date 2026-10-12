---
title: Getting started
description: Signing in, two-factor sign-in, connecting your phone, Telegram and email, and your profile.
sidebar_position: 1
---

# Getting started

## How you get access

Alfred is invitation-only: there is no public sign-up. Your operator (an admin) adds you, chooses your role and
links at least one way for you to reach Alfred: your phone number, your Telegram account or your email address.

Alfred only answers people he knows. Messages from unknown Telegram accounts are ignored, and email from unknown
addresses is treated as an outside inquiry (see [Email](06-email.md)). Unknown callers may leave a message
(see [Unknown callers](04-tasks-calls-meetings.md#when-someone-unknown-calls)).

## The ways to reach Alfred

| Channel | What you need | Good for |
|---|---|---|
| **Phone** | Your number linked and verified | Talking while driving or walking, quick requests |
| **Telegram** | Your Telegram account linked and verified | Everyday requests, voice notes, files and photos, buttons on reminders |
| **Email** | Your email address linked and verified | Forwarding mail, sending documents to file, longer requests |
| **Dashboard** | A dashboard login (admins and cofounders) | Overview, calendar, files, mailbox, settings, the web chat |

You can use whichever is handiest; Alfred is the same everywhere. See [Talking to Alfred](02-talking-to-alfred.md).

## Signing in to the dashboard

**Who:** everyone with a dashboard login (admins and cofounders by default).

1. Open your Alfred address in a browser and choose **Sign in** (the dashboard is at `/app`).
2. Enter your username and password.
3. Enter your second factor (see below), if you have one.

Sessions end after 60 minutes without activity and after 12 hours at the latest. Signing out, changing your
password or a change to your role signs you out everywhere.

### Two-factor sign-in

**Who:** everyone with a dashboard login. Required for admins; optional for others.

After your password, the dashboard asks for a second proof that it's you. Set it up on **Profile → Sign-in &
security**:

- **Authenticator app** (recommended): scan the QR code with an app such as Google Authenticator, Microsoft
  Authenticator or 1Password, then type the 6-digit code it shows.
- **Code on Telegram**: Alfred sends a 6-digit code to your verified Telegram account when you sign in.
- **Backup codes**: when you switch on your first method you get 10 one-time codes. Keep them somewhere safe;
  each works once if you lose your phone. You can make new ones on the same page (needs your password).

Lost your phone and your backup codes? Ask another admin to **Reset 2FA** for you on the Personnel tab; you then
sign in with your password and set it up again.

## Connecting your phone, Telegram and email

**Who:** everyone. On the dashboard: **Profile → Connections**. Without a dashboard login, ask your admin.

Every new connection has to be confirmed with a code before Alfred uses it, so nobody can link someone else's
number or address:

| Connection | How it's confirmed |
|---|---|
| **Phone** | Click **Call with code**: Alfred calls the number and reads out a 6-digit code; type it in. |
| **Telegram** | Click **Get code**, then send that code to Alfred's bot from the Telegram account you're adding (the **Open in Telegram** button does it in one tap). The page updates by itself. |
| **Email** | Click **Email a code**: Alfred emails a 6-digit code to the address; type it in (check spam if it doesn't arrive). |

Codes are valid for 10 minutes and allow 5 tries. Until a connection is confirmed it shows **Unverified** and
Alfred neither answers it nor contacts it.

**Tips**

- Your Telegram connection is your numeric Telegram ID, not your @username. The bot @userinfobot tells you yours.
- Phone numbers can be typed the local way (`079 000 01 00`); Alfred stores them in the international format.
- **Several email addresses**: add every address you write from (private and work, for example), so Alfred
  recognises your mail from any of them. Mark the one he should write to as **Primary**.
- An admin can confirm a connection by hand (**Mark verified**) if they know it's yours.

## Your profile

**Who:** everyone with a dashboard login.

**Profile** is your personal settings page, in sections:

| Section | What you set there |
|---|---|
| **Account** | Your name, your login, your password |
| **Sign-in & security** | Two-factor sign-in and backup codes |
| **Connections** | Phone, Telegram and email addresses, with verification |
| **Calendar** | Link your Google Calendar; share your free/busy times for meeting scheduling |
| **How Alfred serves you** | Quiet hours, the daily briefing, voice replies, continuing across channels |
| **Memory** | The notes Alfred keeps about you, to read, change or delete |
| **Your data** | Download everything stored about you, or ask for it to be deleted |

Each of these is explained on the page about that feature.

## Choosing a look

The dashboard and the website come in four looks ("skins"): **Stark** (a holographic display), **Batcave**,
**Matrix** (a green terminal) and **Alfred** (black, white and silver, like his suit). Switch at the bottom of the
dashboard's side menu, on the sign-in page or on the homepage. Your choice is remembered in your browser.

Next: [Talking to Alfred](02-talking-to-alfred.md).
