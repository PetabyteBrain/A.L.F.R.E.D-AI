"""Meeting scheduling across people: find common free slots, ask each person, book the winner.

1. find_slots: the organizer's calendar plus the calendars of participants who opted in to share
   free/busy (Profile; off by default). Only busy *times* are read, never titles. Candidates are
   working-hours slots on a 30-minute grid, at most 2 per day, earliest first.
2. propose: each participant is asked which of the proposed slots work for them, on Telegram
   (tick buttons, then Send / None work) or by phone (the call brief uses answer_meeting).
   Quiet hours apply as for any contact on someone else's behalf.
   If an invitee is in their quiet hours, nothing is sent yet: the organizer is told who and until
   when, and chooses (in a later reply, enforced) to send now anyway, to send to the others now
   and queue the quiet ones until their quiet hours end, or to delay the whole request. Queued
   invites are picked up by the background loop (send_due) and the organizer is told when they go.
3. When everyone has answered, the earliest slot that works for all is booked in the organizer's
   Google Calendar with the participants as invitees (Google sends real invitations) and everyone
   is told. No common slot: the organizer gets each person's answer.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity import registry
from .identity.registry import Person

log = logging.getLogger(__name__)

GRID = timedelta(minutes=30)
MAX_SLOTS = 5
MAX_PER_DAY = 2
MAX_DAYS = 21
MAX_PEOPLE = 6
QUIET_MODES = ("send_now", "split", "delay")
DECISION_TTL_S = 1800
_pending_decisions: dict[tuple, dict] = {}     # (organizer, people) -> when the question was asked


class MeetingError(ValueError):
    """Message is safe to show to the user / the model."""


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local(dt: datetime) -> str:
    return dt.astimezone(_tz()).strftime("%a %d %b %H:%M")


def _parse(value: str, what: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise MeetingError(f"Couldn't read the {what}. Use ISO format, e.g. 2026-10-06T09:00.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt.astimezone(timezone.utc)


def _may_organize(person: Person) -> bool:
    from .tools import registry as tools
    if not tools._TOOLS:
        tools.load()
    spec = tools._TOOLS.get("place_call")       # same people who may have Alfred contact others
    return bool(spec and spec.allowed_for(person.role))


def _people(organizer: Person, refs: list) -> list[Person]:
    from .scheduler import ScheduleError, resolve_person
    out: list[Person] = []
    for ref in refs or []:
        try:
            p = resolve_person(ref)
        except ScheduleError as e:
            raise MeetingError(str(e))
        if p.id != organizer.id and all(p.id != q.id for q in out):
            out.append(p)
    if not out:
        raise MeetingError("Who should be in the meeting (besides you)?")
    if len(out) > MAX_PEOPLE:
        raise MeetingError(f"At most {MAX_PEOPLE} other people per meeting.")
    return out


def shares_availability(user_id: int) -> bool:
    row = db.query_one("SELECT share_availability FROM google_accounts WHERE user_id = ?", (user_id,))
    return bool(row and row["share_availability"])


def set_sharing(user_id: int, share: bool) -> None:
    if not db.query_one("SELECT 1 FROM google_accounts WHERE user_id = ?", (user_id,)):
        raise MeetingError("Link your Google Calendar first.")
    db.execute("UPDATE google_accounts SET share_availability = ? WHERE user_id = ?", (int(bool(share)), user_id))


# --- finding slots -----------------------------------------------------------------------------

def _candidates(start: datetime, end: datetime, duration: timedelta, earliest: time, latest: time,
                weekends: bool) -> list[datetime]:
    tz = _tz()
    out = []
    day: date = start.astimezone(tz).date()
    while day <= end.astimezone(tz).date():
        if weekends or day.weekday() < 5:
            t = datetime.combine(day, earliest, tzinfo=tz)
            last = datetime.combine(day, latest, tzinfo=tz) - duration
            while t <= last:
                u = t.astimezone(timezone.utc)
                if start <= u and u + duration <= end:
                    out.append(u)
                t += GRID
        day += timedelta(days=1)
    return out


def _free(slot: datetime, duration: timedelta, busy: list[tuple[datetime, datetime]]) -> bool:
    return all(slot + duration <= b0 or slot >= b1 for b0, b1 in busy)


async def find_slots(organizer: Person, people_refs: list, *, duration_minutes: int = 60, window_start: str = "",
                     window_end: str = "", earliest: str = "09:00", latest: str = "18:00",
                     weekends: bool = False) -> dict:
    if not _may_organize(organizer):
        raise MeetingError("You can't schedule meetings with other people.")
    from . import google_calendar
    people = _people(organizer, people_refs)
    duration = timedelta(minutes=max(15, min(int(duration_minutes or 60), 8 * 60)))
    start = _parse(window_start, "start") if window_start else _now() + timedelta(hours=2)
    start = max(start, _now() + timedelta(hours=1))
    end = _parse(window_end, "end") if window_end else start + timedelta(days=7)
    if end <= start:
        raise MeetingError("The window ends before it starts.")
    end = min(end, start + timedelta(days=MAX_DAYS))
    try:
        e_t, l_t = time.fromisoformat(earliest or "09:00"), time.fromisoformat(latest or "18:00")
    except ValueError:
        raise MeetingError("Working hours must be HH:MM, e.g. 09:00 and 18:00.")

    busy: list[tuple[datetime, datetime]] = []
    checked, unknown = [], []
    for p in [organizer, *people]:
        own = p.id == organizer.id
        if google_calendar.is_linked(p.id) and (own or shares_availability(p.id)):
            try:
                busy += await google_calendar.busy_intervals(p.id, start, end)
                checked.append(p.display_name)
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("Free/busy for %s failed: %s", p.display_name, e)
        unknown.append(p.display_name)

    picked, per_day = [], {}
    for c in _candidates(start, end, duration, e_t, l_t, weekends):
        d = c.astimezone(_tz()).date()
        if per_day.get(d, 0) >= MAX_PER_DAY or not _free(c, duration, busy):
            continue
        if picked and c - picked[-1] < timedelta(hours=2) and d == picked[-1].astimezone(_tz()).date():
            continue                                    # spread the options a little
        picked.append(c)
        per_day[d] = per_day.get(d, 0) + 1
        if len(picked) >= MAX_SLOTS:
            break
    return {"duration_minutes": int(duration.total_seconds() // 60),
            "slots": [{"start": s.astimezone(_tz()).isoformat(timespec="minutes"), "label": _local(s)} for s in picked],
            "calendars_checked": checked, "availability_unknown": unknown,
            "note": ("Calendars of " + ", ".join(unknown) + " weren't checked (not linked or not shared); "
                     "they'll be asked.") if unknown else ""}


# --- proposing ---------------------------------------------------------------------------------

def get(meeting_id: int) -> dict | None:
    m = db.query_one("SELECT m.*, u.display_name AS organizer_name FROM meetings m JOIN users u ON u.id = m.organizer_id "
                     "WHERE m.id = ?", (meeting_id,))
    if not m:
        return None
    m["slots"] = [datetime.fromisoformat(s) for s in json.loads(m["slots_json"])]
    m["invites"] = db.query("SELECT i.*, u.display_name AS name FROM meeting_invites i JOIN users u ON u.id = i.user_id "
                            "WHERE i.meeting_id = ? ORDER BY i.id", (meeting_id,))
    for i in m["invites"]:
        i["accepted"] = json.loads(i["accepted_json"] or "[]")
    return m


def view(m: dict) -> dict:
    return {"meeting_id": m["id"], "title": m["title"], "status": m["status"], "organizer": m["organizer_name"],
            "organizer_id": m["organizer_id"],
            "duration_minutes": m["duration_min"],
            "slots": [f"{n + 1}. {_local(s)}" for n, s in enumerate(m["slots"])],
            "booked": _local(datetime.fromisoformat(m["booked_start"])) if m["booked_start"] else None,
            "answers": [{"name": i["name"], "status": i["status"],
                         "works": [n + 1 for n in i["accepted"]] if i["status"] == "answered" else None,
                         **({"asked_at": _local(datetime.fromisoformat(i["send_after"]))}
                            if i["status"] == "queued" and i.get("send_after") else {})}
                        for i in m["invites"]]}


def _last_user_turn(session_id: str | None) -> int:
    if not session_id:
        return 0
    row = db.query_one("SELECT MAX(id) AS id FROM transcripts WHERE session_id = ? AND speaker = 'user'", (session_id,))
    return (row or {}).get("id") or 0


async def propose(organizer: Person, people_refs: list, title: str, slot_starts: list, *, duration_minutes: int = 60,
                  notes: str = "", channel: str = "auto", quiet_mode: str | None = None,
                  session_id: str | None = None) -> dict:
    if not _may_organize(organizer):
        raise MeetingError("You can't schedule meetings with other people.")
    people = _people(organizer, people_refs)
    title = " ".join(str(title or "").split())[:200] or "Meeting"
    slots = sorted({_parse(s, "slot time") for s in (slot_starts or [])})
    if not slots:
        raise MeetingError("Give one or more times to propose (use find_meeting_slots first).")
    if len(slots) > MAX_SLOTS:
        raise MeetingError(f"Propose at most {MAX_SLOTS} times.")
    if slots[0] < _now():
        raise MeetingError("One of the proposed times is in the past.")
    for p in people:
        if not (registry.telegram_for(p.id) or registry.phone_for(p.id)):
            raise MeetingError(f"{p.display_name} has no verified Telegram or phone to ask.")

    # Quiet hours: ask the organizer first, then act on their choice.
    from . import quiet
    quiet_until = {p.id: quiet.quiet_until(p.id) for p in people}
    quiet_until = {k: v for k, v in quiet_until.items() if v}
    send_after: dict[int, datetime | None] = {p.id: None for p in people}
    urgent = False
    if quiet_until:
        key = (organizer.id, tuple(sorted(p.id for p in people)))
        asked = _pending_decisions.get(key)
        if asked and (datetime.now(timezone.utc) - asked["at"]).total_seconds() > DECISION_TTL_S:
            asked = None
        decided = quiet_mode in QUIET_MODES and asked and (
            session_id is None or _last_user_turn(session_id) > asked["user_turn"])
        if not decided:
            _pending_decisions[key] = {"at": datetime.now(timezone.utc), "user_turn": _last_user_turn(session_id)}
            names = {p.id: p.display_name for p in people}
            return {"needs_decision": True,
                    "in_quiet_hours": [{"name": names[uid], "until": _local(t)} for uid, t in quiet_until.items()],
                    "options": {"send_now": "send all invites now anyway (ignores their quiet hours)",
                                "split": "send to the others now; the ones in quiet hours get theirs when it ends",
                                "delay": "send nothing now; send everyone's invites when the last quiet hours end"},
                    "note": "Nothing was sent. Tell the requester and ask which option; then call again with "
                            "quiet_hours set to their choice."}
        _pending_decisions.pop(key, None)
        if quiet_mode == "send_now":
            urgent = True
        elif quiet_mode == "split":
            send_after.update(quiet_until)
        else:
            last = max(quiet_until.values())
            send_after = {p.id: last for p in people}
        # Options that start before (or within an hour of) an invite going out make no sense.
        first_send = min((t for t in send_after.values() if t), default=None)
        if first_send:
            slots = [s for s in slots if s >= first_send + timedelta(hours=1)]
            if not slots:
                raise MeetingError("All proposed times are before the quiet hours end. Pick later times.")
    now = db.now_iso()
    mid = db.execute("INSERT INTO meetings(organizer_id, title, notes, duration_min, slots_json, status, created_at, "
                     "updated_at) VALUES (?,?,?,?,?, 'proposed', ?, ?)",
                     (organizer.id, title, (notes or "")[:500], max(15, min(int(duration_minutes or 60), 480)),
                      json.dumps([s.isoformat(timespec="seconds") for s in slots]), now, now))
    for p in people:
        ch = channel if channel in ("telegram", "call") else ("telegram" if registry.telegram_for(p.id) else "call")
        if ch == "telegram" and not registry.telegram_for(p.id):
            ch = "call"
        if ch == "call" and not registry.phone_for(p.id):
            ch = "telegram"
        later = send_after.get(p.id)
        db.execute("INSERT INTO meeting_invites(meeting_id, user_id, channel, status, accepted_json, send_after, "
                   "created_at) VALUES (?,?,?,?, '[]', ?, ?)",
                   (mid, p.id, ch, "queued" if later else "pending",
                    later.isoformat(timespec="seconds") if later else None, now))
    m = get(mid)
    for inv in m["invites"]:
        if inv["status"] == "pending":
            await _ask(m, inv, urgent=urgent)
    queued = [i["name"] for i in m["invites"] if i["status"] == "queued"]
    db.log_activity("task", f"{organizer.display_name} proposed '{title}' to "
                    f"{', '.join(i['name'] for i in m['invites'])} ({len(m['slots'])} options"
                    + (f"; queued for {', '.join(queued)} until their quiet hours end" if queued else "")
                    + (", sent despite quiet hours" if urgent else "") + ")", user_id=organizer.id)
    return view(get(mid))


async def send_due() -> None:
    """Queued invites whose quiet hours are over: send them (re-checking quiet hours first)."""
    from . import quiet
    for r in db.query("SELECT i.id, i.meeting_id, i.user_id FROM meeting_invites i JOIN meetings m ON m.id = i.meeting_id "
                      "WHERE i.status = 'queued' AND i.send_after <= ? AND m.status = 'proposed'",
                      (datetime.now(timezone.utc).isoformat(timespec="seconds"),)):
        still = quiet.quiet_until(r["user_id"])
        if still:      # their quiet hours changed meanwhile: wait for the new end
            db.execute("UPDATE meeting_invites SET send_after = ? WHERE id = ?",
                       (still.isoformat(timespec="seconds"), r["id"]))
            continue
        if not db.execute_count("UPDATE meeting_invites SET status = 'pending', send_after = NULL WHERE id = ? "
                                "AND status = 'queued'", (r["id"],)):
            continue
        m = get(r["meeting_id"])
        inv = next(i for i in m["invites"] if i["id"] == r["id"])
        if not any(s > _now() for s in m["slots"]):
            db.execute("UPDATE meeting_invites SET status = 'closed' WHERE id = ?", (r["id"],))
            continue
        await _ask(m, inv)
        await _tell(m, [m["organizer_id"]], f"Quiet hours are over, so I've now asked {inv['name']} about "
                                            f"**{m['title']}**.")


def _question(m: dict, inv: dict) -> str:
    first = m["organizer_name"].split()[0]
    lines = [f"📅 {first} would like to meet: **{m['title']}** ({m['duration_min']} min)."]
    if m["notes"]:
        lines.append(m["notes"])
    lines.append("Which of these work for you? Tick all that do, then Send.")
    lines += [f"{n + 1}. {_local(s)}" for n, s in enumerate(m["slots"])]
    return "\n".join(lines) + f"\n(meeting #{m['id']})"


def _buttons(m: dict, inv: dict) -> list[list[tuple[str, str]]]:
    rows = [[(("✓ " if n in inv["accepted"] else "") + _local(s), f"mtg:{inv['id']}:t{n}")]
            for n, s in enumerate(m["slots"]) if s > _now()]
    rows.append([("Send", f"mtg:{inv['id']}:send"), ("None work", f"mtg:{inv['id']}:none")])
    return rows


async def _ask(m: dict, inv: dict, urgent: bool = False) -> None:
    """urgent: the organizer chose to send despite the person's quiet hours."""
    person = registry.get_person(inv["user_id"])
    organizer = registry.get_person(m["organizer_id"])
    if inv["channel"] == "telegram" and settings.telegram_enabled and registry.telegram_for(person.id):
        from .telegram_handler import send_to_user
        await send_to_user(person.id, _question(m, inv), note="meeting invite", buttons=_buttons(m, inv),
                           silent=False if urgent else None)
    else:
        from . import scheduler
        options = "; ".join(f"option {n + 1}: {_local(s)}" for n, s in enumerate(m["slots"]) if s > _now())
        await scheduler.request_call(
            person.id, requested_by=organizer, urgent=urgent,
            purpose=(f"Ask which of these times work for the meeting '{m['title']}' ({m['duration_min']} minutes) "
                     f"with {organizer.display_name}: {options}. They may pick several. Then call answer_meeting "
                     f"with meeting_id {m['id']} and the option numbers that work (or none_work=true)."))


# --- answers and booking --------------------------------------------------------------------------

def _invite_for(meeting_id: int, person: Person) -> dict:
    inv = db.query_one("SELECT * FROM meeting_invites WHERE meeting_id = ? AND user_id = ?", (meeting_id, person.id))
    if not inv:
        raise MeetingError("You're not invited to that meeting.")
    return inv


def toggle(invite_id: int, person: Person, slot: int) -> dict:
    """Telegram tick button: flip one slot in the person's pending answer."""
    inv = db.query_one("SELECT * FROM meeting_invites WHERE id = ? AND user_id = ?", (invite_id, person.id))
    if not inv or inv["status"] != "pending":
        raise MeetingError("This invitation is closed.")
    accepted = set(json.loads(inv["accepted_json"] or "[]"))
    accepted ^= {slot}
    db.execute("UPDATE meeting_invites SET accepted_json = ? WHERE id = ?", (json.dumps(sorted(accepted)), invite_id))
    m = get(inv["meeting_id"])
    return {"meeting": m, "invite": next(i for i in m["invites"] if i["id"] == invite_id)}


async def answer(meeting_id: int, person: Person, works: list[int] | None, none_work: bool = False) -> dict:
    """Record a participant's final answer (slot numbers are 1-based, as shown to them)."""
    m = get(meeting_id)
    if not m:
        raise MeetingError("No such meeting.")
    inv = _invite_for(meeting_id, person)
    if m["status"] != "proposed":
        raise MeetingError(f"That meeting is already {m['status']}.")
    if none_work:
        picked: list[int] = []
    elif works is None:
        picked = json.loads(inv["accepted_json"] or "[]")
    else:
        picked = sorted({int(n) - 1 for n in works if 1 <= int(n) <= len(m["slots"])})
    db.execute("UPDATE meeting_invites SET status = 'answered', accepted_json = ?, answered_at = ? WHERE id = ?",
               (json.dumps(picked), db.now_iso(), inv["id"]))
    db.log_activity("task", f"{person.display_name} answered meeting #{meeting_id}: "
                    + (", ".join(_local(m["slots"][n]) for n in picked) or "none work"), user_id=person.id)
    return await _settle(meeting_id)


async def _settle(meeting_id: int) -> dict:
    m = get(meeting_id)
    if m["status"] != "proposed" or any(i["status"] in ("pending", "queued") for i in m["invites"]):
        return view(m)
    common = {n for n, s in enumerate(m["slots"]) if s > _now()}       # only times still ahead
    for i in m["invites"]:
        common &= set(i["accepted"])
    # Claim before booking, so two last answers arriving together can't book twice.
    status = "booking" if common else "no_match"
    if not db.execute_count("UPDATE meetings SET status = ?, updated_at = ? WHERE id = ? AND status = 'proposed'",
                            (status, db.now_iso(), meeting_id)):
        return view(get(meeting_id))
    if not common:
        await _tell(m, [m["organizer_id"]],
                    f"No time works for everyone for **{m['title']}**:\n"
                    + "\n".join(f"- {i['name']}: " + (", ".join(_local(m['slots'][n]) for n in i['accepted']) or "none")
                                for i in m["invites"])
                    + "\nAsk me to find other times if you like.")
        return view(get(meeting_id))
    slot = m["slots"][min(common)]
    event_id = await _book(m, slot)
    db.execute("UPDATE meetings SET status = 'booked', booked_start = ?, event_id = ?, updated_at = ? WHERE id = ?",
               (slot.isoformat(timespec="seconds"), event_id, db.now_iso(), meeting_id))
    end = slot + timedelta(minutes=m["duration_min"])
    where = " It's in the calendar, with invitations sent." if event_id else ""
    await _tell(m, [m["organizer_id"], *[i["user_id"] for i in m["invites"]]],
                f"✅ **{m['title']}** is set for {_local(slot)}–{end.astimezone(_tz()):%H:%M}.{where}")
    db.log_activity("task", f"Meeting #{meeting_id} '{m['title']}' booked for {_local(slot)}", user_id=m["organizer_id"])
    return view(get(meeting_id))


async def _book(m: dict, slot: datetime) -> str | None:
    """Organizer's calendar, participants as invitees (Google sends the invitations)."""
    from . import google_calendar
    if not google_calendar.is_linked(m["organizer_id"]):
        return None
    attendees = []
    for i in m["invites"]:
        g = db.query_one("SELECT email FROM google_accounts WHERE user_id = ?", (i["user_id"],))
        e = (g or {}).get("email") or (db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND "
                                                     "type = 'email' LIMIT 1", (i["user_id"],)) or {}).get("value")
        if e:
            attendees.append(e)
    try:
        ev = await google_calendar.create_event(
            m["organizer_id"], m["title"], slot.astimezone(_tz()).isoformat(),
            (slot + timedelta(minutes=m["duration_min"])).astimezone(_tz()).isoformat(),
            description=(m["notes"] or "") + f"\nScheduled by {settings.assistant_name}.", attendees=attendees)
        return ev.get("id")
    except Exception:  # noqa: BLE001
        log.exception("Booking meeting %s in the calendar failed", m["id"])
        return None


async def _tell(m: dict, user_ids: list[int], text: str) -> None:
    from .telegram_handler import send_to_user
    for uid in dict.fromkeys(user_ids):
        db.log_activity("task", f"Meeting #{m['id']}: {text[:150]}", user_id=uid)
        if settings.telegram_enabled and registry.telegram_for(uid):
            try:
                await send_to_user(uid, text, note="meeting")
            except Exception:  # noqa: BLE001
                log.exception("Meeting message failed")


def list_for(person: Person, include_done: bool = False) -> list[dict]:
    status = "" if include_done else " AND m.status IN ('proposed', 'booking')"
    rows = db.query("SELECT DISTINCT m.id FROM meetings m LEFT JOIN meeting_invites i ON i.meeting_id = m.id "
                    f"WHERE (m.organizer_id = ? OR i.user_id = ?){status} ORDER BY m.id DESC LIMIT 30",
                    (person.id, person.id))
    return [view(get(r["id"])) for r in rows]


async def cancel(meeting_id: int, person: Person) -> dict:
    m = get(meeting_id)
    if not m or not (person.is_admin or m["organizer_id"] == person.id):
        raise MeetingError("No such meeting.")
    if m["status"] not in ("proposed", "no_match"):
        raise MeetingError(f"That meeting is already {m['status']}; change it in the calendar.")
    db.execute("UPDATE meetings SET status = 'cancelled', updated_at = ? WHERE id = ?", (db.now_iso(), meeting_id))
    db.execute("UPDATE meeting_invites SET status = 'closed' WHERE meeting_id = ? AND status IN ('pending', 'queued')",
               (meeting_id,))
    await _tell(m, [i["user_id"] for i in m["invites"] if i["status"] == "pending"],
                f"The meeting request **{m['title']}** from {m['organizer_name'].split()[0]} was withdrawn.")
    return view(get(meeting_id))


async def expire_stale() -> None:
    """A proposal whose last option has passed with answers missing: tell the organizer once."""
    for r in db.query("SELECT id FROM meetings WHERE status = 'proposed'"):
        m = get(r["id"])
        if m["slots"][-1] > _now():
            continue
        if not db.execute_count("UPDATE meetings SET status = 'expired', updated_at = ? WHERE id = ? AND status = 'proposed'",
                                (db.now_iso(), m["id"])):
            continue
        missing = [i["name"] for i in m["invites"] if i["status"] in ("pending", "queued")]
        db.execute("UPDATE meeting_invites SET status = 'closed' WHERE meeting_id = ? AND status IN ('pending', 'queued')",
                   (m["id"],))
        await _tell(m, [m["organizer_id"]], f"The proposal **{m['title']}** ran out of time; no answer from "
                                            f"{', '.join(missing) or 'everyone'}. Ask me for new times if you like.")
