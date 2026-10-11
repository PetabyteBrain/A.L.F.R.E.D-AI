"""Builtin tool handlers. Signature: async def handler(ctx, **tool_input) -> str | dict | list."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .. import db
from ..config import settings

Handler = Callable[..., Awaitable[Any]]
HANDLERS: dict[str, Handler] = {}


def handler(fn: Handler) -> Handler:
    HANDLERS[fn.__name__] = fn
    return fn


@handler
async def current_time(ctx) -> str:
    now = datetime.now(ZoneInfo(settings.timezone))
    return now.strftime(f"%A %d %B %Y, %H:%M ({settings.timezone})")


@handler
async def resolve_date(ctx, phrases: list[str] | None = None, phrase: str = "") -> dict:
    from .. import dates
    return dates.resolve_many(list(phrases or []) + ([phrase] if phrase else []))


@handler
async def list_tasks(ctx, include_finished: bool = False) -> dict:
    from .. import tasks
    out: dict[str, Any] = {"tasks": [
        {k: t[k] for k in ("id", "title", "status", "owner_name", "requester_name", "due_local", "follow_up",
                           "follow_up_local", "follow_ups", "result", "schedule") if t[k] not in (None, "", "none")}
        for t in tasks.list_for(ctx.person, include_done=include_finished, limit=40)]}
    from .. import permissions
    if settings.n8n_enabled and permissions.allowed(ctx.person.role, "n8n_view"):
        from . import n8n_tool
        try:
            out["active_n8n_workflows"] = [
                {"id": w.get("id"), "name": w.get("name")} for w in await n8n_tool.list_active_workflows()
            ]
        except Exception as e:  # noqa: BLE001
            out["n8n_error"] = str(e)[:200]
    return out


@handler
async def add_task(ctx, title: str, person: str = "", due: str = "", follow_up: str = "", notes: str = "") -> dict:
    from .. import tasks
    try:
        t = tasks.create(created_by=ctx.person, title=title, owner_ref=person or None, due=due or None,
                         follow_up=follow_up or ("telegram" if due else "none"), notes=notes)
    except tasks.TaskError as e:
        return {"error": str(e)}
    return {"task_id": t["id"], "owner": t["owner_name"], "due": t["due_local"], "follow_up": t["follow_up"]}


@handler
async def complete_task(ctx, task_id: int, result: str = "") -> dict:
    from .. import tasks
    try:
        t = tasks.complete(int(task_id), ctx.person, result)
    except tasks.TaskError as e:
        return {"error": str(e)}
    return {"task_id": t["id"], "status": t["status"]}


@handler
async def update_task(ctx, task_id: int, action: str = "update", due: str | None = None, person: str | None = None,
                      follow_up: str | None = None, title: str | None = None) -> dict:
    from .. import tasks
    try:
        if action == "cancel":
            t = tasks.cancel(int(task_id), ctx.person)
        elif action == "not_yet":
            t = tasks.not_yet(int(task_id), ctx.person)
        else:
            t = tasks.update(int(task_id), ctx.person, title=title, due=due, owner_ref=person, follow_up=follow_up)
    except tasks.TaskError as e:
        return {"error": str(e)}
    return {"task_id": t["id"], "status": t["status"], "owner": t["owner_name"], "due": t["due_local"],
            "next_follow_up": t["follow_up_local"]}


@handler
async def list_active_sessions(ctx) -> list[dict]:
    from ..sessions import LIVE
    rows = db.query(
        "SELECT s.id, s.channel, s.direction, s.started_at, u.display_name FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.status = 'active' ORDER BY s.started_at"
    )
    for r in rows:
        live = LIVE.get(r["id"])
        r["busy"] = bool(live and live.busy)
    return rows


@handler
async def recent_activity(ctx, limit: int = 15) -> list[dict]:
    return db.query(
        "SELECT a.ts, a.kind, a.summary, u.display_name AS user FROM activity a "
        "LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT ?",
        (max(1, min(int(limit), 50)),),
    )


@handler
async def list_people(ctx) -> list[dict]:
    people = db.query("SELECT id, display_name, role FROM users WHERE active = 1 ORDER BY id")
    for p in people:
        types = db.query("SELECT DISTINCT type FROM user_identifiers WHERE user_id = ? AND verified_at IS NOT NULL",
                         (p["id"],))
        p["reachable_via"] = sorted(t["type"] for t in types if t["type"] in ("telegram", "phone"))
    return people


@handler
async def send_telegram_message(ctx, user_id: int, text: str) -> str:
    from ..telegram_handler import send_to_user
    await send_to_user(int(user_id), text, note=f"sent on behalf of {ctx.person.display_name}")
    return "Sent."


@handler
async def set_reminder(ctx, text: str = "", when: str = "", recurrence: str = "none", channel: str = "telegram",
                       escalate: bool = False, person: str = "", event_id: str = "", minutes_before: int = 15,
                       category: str = "reminder") -> dict:
    from .. import agenda, reminders
    try:
        if event_id:                                  # tied to a calendar event: follows it if it moves
            r = await agenda.remind_for_event(ctx.person, event_id, minutes_before, channel)
        else:
            r = reminders.create(created_by=ctx.person, text=text, when=when, target_ref=person or None,
                                 recurrence=recurrence, channel=channel, escalate=escalate, category=category)
    except (reminders.ReminderError, agenda.AgendaError) as e:
        return {"error": str(e)}
    return {"reminder_id": r["id"], "for": r["person_name"], "at_local_time": r["due_local"],
            "recurrence": r["recurrence"] or "once", "channel": r["channel"], "escalate": r["escalate"]}


@handler
async def list_reminders(ctx, include_finished: bool = False) -> list[dict]:
    from .. import reminders
    return [{"id": r["id"], "for": r["person_name"], "text": r["text"], "at_local_time": r["due_local"],
             "recurrence": r["recurrence"] or "once", "channel": r["channel"], "status": r["status"]}
            for r in reminders.list_for(ctx.person, include_done=include_finished, limit=30)]


@handler
async def update_reminder(ctx, reminder_id: int, action: str, snooze_minutes: int = 10) -> dict:
    from .. import reminders
    try:
        if action == "cancel":
            r = reminders.cancel(int(reminder_id), ctx.person)
        elif action in ("done", "snooze"):
            r = reminders.acknowledge(int(reminder_id), ctx.person,
                                      snooze_minutes=(snooze_minutes or 10) if action == "snooze" else 0)
        else:
            return {"error": "action must be done, snooze or cancel"}
    except reminders.ReminderError as e:
        return {"error": str(e)}
    return {"reminder_id": r["id"], "status": r["status"], "at_local_time": r["due_local"]}


@handler
async def get_briefing(ctx) -> dict:
    from .. import briefing
    return await briefing.gather(ctx.person)


@handler
async def configure_briefing(ctx, enabled: bool | None = None, time: str | None = None, days: str | None = None,
                             channel: str | None = None, location: str | None = None) -> dict:
    from .. import briefing
    try:
        s = await briefing.update_settings(ctx.person.id, enabled=enabled, time_local=time, days=days,
                                           channel=channel, location=location)
    except briefing.BriefingError as e:
        return {"error": str(e)}
    return {k: s[k] for k in ("enabled", "time_local", "days", "channel", "location")}


@handler
async def configure_quiet_hours(ctx, mode: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    from .. import quiet
    if mode is not None:
        try:
            quiet.set_person(ctx.person.id, mode=mode, start=start, end=end)
        except quiet.QuietError as e:
            return {"error": str(e)}
    return quiet.describe(ctx.person.id)


@handler
async def nas_notes(ctx, action: str, scope: str, title: str = "", content: str = "", folder: str = "",
                    new_path: str = "") -> dict:
    import asyncio
    from .. import nas
    p = ctx.person
    try:
        if action == "list":
            return await asyncio.to_thread(nas.list_notes, p, scope, folder)
        if action == "read":
            return await asyncio.to_thread(nas.read_file, p, scope, nas._note_path(title, folder).replace("\\", "/"))
        if action in ("create", "replace", "append"):
            return await asyncio.to_thread(nas.save_note, p, scope, title, content, folder, action)
        if action == "move":
            return await asyncio.to_thread(nas.move_note, p, scope, title, new_path)
        if action == "trash":
            return await asyncio.to_thread(nas.trash_note, p, scope, title)
        return {"error": "Unknown action."}
    except nas.NasError as e:
        return {"error": str(e)}


@handler
async def nas_list(ctx, action: str, scope: str, name: str = "", items: list | None = None) -> dict:
    import asyncio
    from .. import nas
    try:
        if action == "all":
            return await asyncio.to_thread(nas.all_lists, ctx.person, scope)
        return await asyncio.to_thread(nas.list_op, ctx.person, scope, name, action, items)
    except nas.NasError as e:
        return {"error": str(e)}


@handler
async def nas_files(ctx, action: str, scope: str, path: str = "", query: str = "") -> dict:
    import asyncio
    from .. import nas
    try:
        if action == "list":
            return await asyncio.to_thread(nas.list_dir, ctx.person, scope, path)
        if action == "search":
            return await asyncio.to_thread(nas.search, ctx.person, scope, query, path)
        if action == "find":
            from .. import nas_search
            return await asyncio.to_thread(nas_search.find, ctx.person, scope, query, path)
        if action == "read":
            return await asyncio.to_thread(nas.read_file, ctx.person, scope, path)
        return {"error": "Unknown action."}
    except nas.NasError as e:
        return {"error": str(e)}


@handler
async def nas_save_upload(ctx, upload_id: int, scope: str, folder: str, filename: str = "") -> dict:
    import asyncio
    from .. import nas, uploads
    try:
        return await asyncio.to_thread(uploads.save, ctx.person, upload_id, scope, folder, filename)
    except (nas.NasError, uploads.UploadError) as e:
        return {"error": str(e)}


@handler
async def log_receipt(ctx, scope: str, date: str, shop: str, amount: float, currency: str = "CHF", category: str = "",
                      note: str = "", upload_id: int | None = None) -> dict:
    import asyncio
    from .. import nas, uploads
    try:
        return await asyncio.to_thread(uploads.log_receipt, ctx.person, scope, date, shop, amount, currency, category,
                                       note, upload_id)
    except (nas.NasError, uploads.UploadError) as e:
        return {"error": str(e)}


@handler
async def market_analysis(ctx, area: str, industries: list[str] | None = None, radius_km: float = 5,
                          osm_tags: list[str] | None = None, name_keywords: list[str] | None = None,
                          thoroughness: str = "standard", web_lookups: int | None = None, check_websites: bool = True,
                          focus: str = "both", min_score: int = 50, exclude_chains: bool = True, shortlist: int = 40,
                          sort: str = "score", report_format: str = "both", title: str = "", notes: str = "",
                          lookup_method: str = "auto", claude_fallback: int = 0, confirmed: bool = False) -> dict:
    from .. import market_analysis as ma, nas
    try:
        spec = ma.build_spec(area, industries, radius_km, osm_tags, name_keywords, thoroughness, web_lookups,
                             check_websites, focus, min_score, exclude_chains, shortlist, sort, report_format, title, notes,
                             lookup_method, claude_fallback)
        return await ma.start(ctx.person, spec, confirmed)
    except (ma.MarketError, nas.NasError) as e:
        return {"error": str(e)}


@handler
async def company_todo(ctx, action: str, description: str = "", todo: str = "", importance: int | None = None,
                      section: str = "current", status: str | None = None, to: str = "") -> dict:
    import asyncio
    from .. import nas, company_todo as wt
    try:
        if action == "show":
            return await asyncio.to_thread(wt.show, ctx.person)
        if action == "add":
            return await asyncio.to_thread(wt.add, ctx.person, description, importance or 3, section)
        if action == "update":
            return await asyncio.to_thread(wt.update, ctx.person, todo or description, status, importance)
        if action == "move":
            return await asyncio.to_thread(wt.move, ctx.person, todo or description, to)
        return {"error": "Unknown action."}
    except nas.NasError as e:
        return {"error": str(e)}


@handler
async def reply_by_voice(ctx) -> dict:
    from .. import voice_replies
    from ..identity import registry
    if not settings.speech_enabled:
        return {"error": "Speech isn't available right now; answer in text and say so."}
    if not registry.telegram_for(ctx.person.id):
        return {"error": "This person has no verified Telegram connection."}
    voice_replies.request_for(ctx.session_id)
    return {"ok": True, "note": "This reply goes out as a voice note with the text. Write it to be spoken: "
                                "natural sentences, no Markdown, lists or URLs; brief unless asked for detail."}


@handler
async def voice_mode(ctx, action: str, minutes: int | None = None) -> dict:
    from .. import voice_replies
    if action == "status":
        return voice_replies.describe(ctx.person.id)
    if action == "on" and not voice_replies.describe(ctx.person.id)["telegram_linked"]:
        return {"error": "Voice notes go out on Telegram, and this person has no verified Telegram connection."}
    try:
        return voice_replies.set_mode(ctx.person.id, action == "on", minutes)
    except voice_replies.VoiceError as e:
        return {"error": str(e)}


@handler
async def search_conversations(ctx, query: str, person: str = "", channel: str = "", days: int = 0) -> dict:
    from .. import search
    from ..scheduler import ScheduleError, resolve_person
    about = None
    if person:
        try:
            about = resolve_person(person)
        except ScheduleError as e:
            return {"error": str(e)}
    try:
        hits = search.search(ctx.person, query, about=about, channel=channel or None, days=days or None)
    except search.SearchError as e:
        return {"error": str(e)}
    return {"matches": hits} if hits else {"matches": [], "note": "Nothing found. Try other or fewer words."}


@handler
async def homelab_updates(ctx, check_now: bool = False) -> dict:
    from .. import homelab, update_check
    if not ctx.person.is_admin:
        return {"error": "Only an admin can see the update report."}
    if check_now:
        if not homelab.enabled():
            return {"error": "The homelab key isn't set up."}
        report = await update_check.run(notify=False)
    else:
        report = update_check.last()
    if not report.get("at"):
        return {"report": None, "note": "No update check has run yet; it runs on the 1st of the month (check_now runs it)."}
    return {"checked_at": report["at"], "summary": report["summary"],
            "hosts": [update_check._host_line(h) for h in report["hosts"]],
            "note": "Report only. Alfred doesn't install updates; the person does that on the machines."}


@handler
async def homelab_status(ctx, host: str = "") -> dict:
    from .. import homelab
    try:
        rows = await homelab.status(host or None)
    except homelab.HomelabError as e:
        return {"error": str(e)}
    return {"summary": [homelab.summarize(r["host"], r) for r in rows],
            "details": [{k: r.get(k) for k in ("host", "role", "containers", "disks", "temp_c", "power", "manageable",
                                               "error")} for r in rows]}


@handler
async def homelab_logs(ctx, host: str, container: str, lines: int = 50) -> dict:
    from .. import homelab
    try:
        return await homelab.logs(ctx.person, host, container, lines)
    except homelab.HomelabError as e:
        return {"error": str(e)}


@handler
async def homelab_restart(ctx, host: str, container: str, confirmed: bool = False) -> dict:
    from .. import homelab
    try:
        return await homelab.restart(ctx.person, ctx.session_id, host, container, confirmed)
    except homelab.HomelabError as e:
        return {"error": str(e)}


@handler
async def minecraft_status(ctx) -> dict:
    from .. import homelab
    try:
        return await homelab.minecraft()
    except homelab.HomelabError as e:
        return {"error": str(e)}


@handler
async def find_meeting_slots(ctx, people: list, duration_minutes: int = 60, window_start: str = "", window_end: str = "",
                             earliest: str = "09:00", latest: str = "18:00", weekends: bool = False) -> dict:
    from .. import meetings
    try:
        return await meetings.find_slots(ctx.person, people, duration_minutes=duration_minutes,
                                         window_start=window_start, window_end=window_end, earliest=earliest,
                                         latest=latest, weekends=weekends)
    except meetings.MeetingError as e:
        return {"error": str(e)}


@handler
async def propose_meeting(ctx, people: list, title: str, slots: list, duration_minutes: int = 60, notes: str = "",
                          channel: str = "auto", quiet_hours: str = "") -> dict:
    from .. import meetings
    try:
        return await meetings.propose(ctx.person, people, title, slots, duration_minutes=duration_minutes,
                                      notes=notes, channel=channel, quiet_mode=quiet_hours or None,
                                      session_id=ctx.session_id)
    except meetings.MeetingError as e:
        return {"error": str(e)}


@handler
async def answer_meeting(ctx, meeting_id: int, works: list | None = None, none_work: bool = False) -> dict:
    from .. import meetings
    try:
        return await meetings.answer(int(meeting_id), ctx.person, [int(w) for w in (works or [])], none_work)
    except (meetings.MeetingError, ValueError) as e:
        return {"error": str(e)}


@handler
async def list_meetings(ctx, include_finished: bool = False) -> list[dict]:
    from .. import meetings
    return meetings.list_for(ctx.person, include_finished)


@handler
async def cancel_meeting(ctx, meeting_id: int) -> dict:
    from .. import meetings
    try:
        return await meetings.cancel(int(meeting_id), ctx.person)
    except meetings.MeetingError as e:
        return {"error": str(e)}


@handler
async def remember(ctx, text: str) -> dict:
    from .. import memory
    try:
        m = memory.add(ctx.person, text)
    except memory.MemoryNoteError as e:
        return {"error": str(e)}
    return {"saved": True, "memory_id": m["id"]}


@handler
async def update_memory(ctx, memory_id: int, text: str) -> dict:
    from .. import memory
    try:
        m = memory.update(int(memory_id), ctx.person, text)
    except memory.MemoryNoteError as e:
        return {"error": str(e)}
    return {"updated": True, "memory_id": m["id"]}


@handler
async def forget_memory(ctx, memory_id: int) -> dict:
    from .. import memory
    try:
        memory.delete(int(memory_id), ctx.person)
    except memory.MemoryNoteError as e:
        return {"error": str(e)}
    return {"deleted": True}


@handler
async def place_call(ctx, person: str, purpose: str = "", opening_line: str = "", urgent: bool = False) -> dict:
    from .. import scheduler
    try:
        row = await scheduler.request_call(person, requested_by=ctx.person, purpose=purpose, opening_line=opening_line,
                                           urgent=urgent)
    except scheduler.ScheduleError as e:
        return {"error": str(e)}
    if row.get("held_for_quiet_hours"):
        return {"status": "waiting_for_quiet_hours", "calling": row["target_name"], "request_id": row["id"],
                "will_call_at_local_time": row["run_at_local"],
                "note": "They're in their quiet hours; the call goes out when those end. Say so, and mention "
                        "it can be placed as urgent if it really can't wait."}
    return {"status": row["status"], "calling": row["target_name"], "request_id": row["id"],
            "note": "The outcome will be reported back when the call ends." if purpose and row["target_user_id"] != ctx.person.id
            else ""}


@handler
async def call_external(ctx, number: str, contact: str, task: str, share: str = "", when: str = "") -> dict:
    from .. import external_calls
    try:
        return await external_calls.request(ctx.person, ctx.session_id, number=number, contact=contact, task=task,
                                            share=share, when=when or None)
    except external_calls.ExternalCallError as e:
        return {"error": str(e)}


@handler
async def confirm_external_call(ctx) -> dict:
    from .. import external_calls
    try:
        row = await external_calls.confirm(ctx.person, ctx.session_id)
    except external_calls.ExternalCallError as e:
        return {"error": str(e)}
    if not row.get("id"):
        return row
    return {"dialled": row["status"] != "pending", "call_id": row["id"], "status": row["status"], "calling": row["contact"], "at_local_time": row["run_at_local"],
            "task_id": row["task_id"], "note": "The outcome will be reported back when the call ends."}


@handler
async def list_external_calls(ctx, include_finished: bool = False) -> list[dict]:
    from .. import external_calls
    return [{k: r[k] for k in ("id", "contact", "number", "task", "status", "run_at_local", "attempts", "result",
                               "requester_name")} for r in external_calls.list_for(ctx.person, include_finished)]


@handler
async def cancel_external_call(ctx, call_id: int) -> dict:
    from .. import external_calls
    try:
        row = external_calls.cancel(int(call_id), ctx.person)
    except external_calls.ExternalCallError as e:
        return {"error": str(e)}
    return {"cancelled": True, "contact": row["contact"]}


@handler
async def schedule_call(ctx, person: str, when: str, purpose: str = "", opening_line: str = "") -> dict:
    from .. import scheduler
    try:
        row = await scheduler.request_call(person, requested_by=ctx.person, purpose=purpose, when=when,
                                           opening_line=opening_line)
    except scheduler.ScheduleError as e:
        return {"error": str(e)}
    return {"scheduled": True, "request_id": row["id"], "calling": row["target_name"],
            "at_local_time": row["run_at_local"], "status": row["status"],
            **({"note": "Moved to the end of their quiet hours."} if row.get("held_for_quiet_hours") else {})}


@handler
async def list_scheduled_calls(ctx, include_finished: bool = False) -> list[dict]:
    from .. import scheduler
    rows = scheduler.list_for(ctx.person, include_done=include_finished, limit=25)
    return [{k: r[k] for k in ("id", "target_name", "requester_name", "run_at_local", "purpose", "status",
                               "attempts", "result")} for r in rows]


@handler
async def cancel_scheduled_call(ctx, request_id: int) -> dict:
    from .. import scheduler
    try:
        row = scheduler.cancel(int(request_id), ctx.person)
    except scheduler.ScheduleError as e:
        return {"error": str(e)}
    return {"cancelled": True, "call_to": row["target_name"], "was_at": row["run_at_local"]}


@handler
async def end_call(ctx) -> str:
    from ..call_handler import request_hangup
    if ctx.channel != "call" or not ctx.session_id or not request_hangup(ctx.session_id):
        return "There's no live call to end."
    return ("The line will be closed right after your current reply. In that reply, briefly confirm what "
            "was agreed or answered (one sentence), then say goodbye.")


# --- Google Calendar (the current person's own linked calendar only) -------------------------

async def _cal(fn, *args, **kwargs):
    from .. import google_calendar
    try:
        return await fn(*args, **kwargs)
    except google_calendar.GoogleError as e:
        return {"error": str(e)}


def _tidy_call(fn, *a):
    from .. import nas, notes_tidy
    try:
        return fn(*a)
    except (notes_tidy.TidyError, nas.NasError) as e:
        return {"error": str(e)}


@handler
async def notes_overview(ctx) -> dict:
    from .. import notes_tidy
    return _tidy_call(notes_tidy.overview, ctx.person)


@handler
async def notes_reorganize(ctx, steps: list) -> dict:
    from .. import notes_tidy
    return _tidy_call(notes_tidy.preview, ctx.person, ctx.session_id, "structure", steps)


@handler
async def notes_rewrite(ctx, steps: list) -> dict:
    from .. import notes_tidy
    return _tidy_call(notes_tidy.preview, ctx.person, ctx.session_id, "content", steps)


@handler
async def notes_tidy_confirm(ctx, kind: str) -> dict:
    from .. import notes_tidy
    return _tidy_call(notes_tidy.apply, ctx.person, ctx.session_id, kind)


@handler
async def agenda(ctx, start: str = "", end: str = "") -> dict:
    """Everything scheduled in a period, as on the dashboard calendar: Google events, reminders
    (repeats expanded), tasks with due dates, planned calls. Local ISO dates/times; a bare end date
    counts the whole day. Defaults: today and the next 7 days."""
    from datetime import timedelta
    from .. import agenda as agenda_mod
    tz = ZoneInfo(settings.timezone)

    def parse(value: str, end_of_day: bool) -> datetime:
        v = (value or "").strip()
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        if end_of_day and len(v) == 10:
            dt += timedelta(days=1)
        return dt

    try:
        s = parse(start, False) if start else datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        e = parse(end, True) if end else s + timedelta(days=8)
    except ValueError:
        return {"error": "Use ISO dates or times, e.g. 2026-10-10 or 2026-10-10T09:00."}
    if e <= s:
        e = s + timedelta(days=1)
    try:
        data = await agenda_mod.items(ctx.person, s.isoformat(), e.isoformat())
    except agenda_mod.AgendaError as ex:
        return {"error": str(ex)}

    def local(it: dict) -> str:
        if it.get("all_day"):
            return datetime.fromisoformat(it["start"][:10]).strftime("%a %d %b %Y") + " (all day)"
        return datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(tz).strftime("%a %d %b %Y %H:%M")

    out = []
    for it in data["items"]:
        if it.get("link_state") == "event_deleted":
            continue
        row = {"when": local(it), "kind": it["kind"], "title": it["title"], "category": it.get("category")}
        if it.get("recurrence"):
            row["repeats"] = it["recurrence"]
        if it["kind"] == "reminder":
            row["reminder_id"] = it["id"]
            row["by"] = it.get("channel")
            if it.get("link_state") == "linked":
                row["tied_to_calendar_event"] = True
        out.append(row)
    cal = data.get("calendar", {})
    return {"from": s.strftime("%a %d %b %Y %H:%M"), "until": e.strftime("%a %d %b %Y %H:%M"), "items": out[:80],
            "google_calendar": "included" if cal.get("linked") and not cal.get("error")
            else ("not reachable: " + cal["error"]) if cal.get("error") else "not linked (reminders, tasks and calls only)"}


@handler
async def calendar_list_events(ctx, start: str, end: str, query: str = "", max_results: int = 25):
    from .. import google_calendar
    return await _cal(google_calendar.list_events, ctx.person.id, start, end, query, max_results)


@handler
async def calendar_create_event(ctx, summary: str, start: str, end: str, description: str = "", location: str = ""):
    from .. import google_calendar
    return await _cal(google_calendar.create_event, ctx.person.id, summary, start, end, description, location)


@handler
async def calendar_update_event(ctx, event_id: str, summary: str | None = None, start: str | None = None,
                                end: str | None = None, description: str | None = None, location: str | None = None):
    from .. import google_calendar
    result = await _cal(google_calendar.update_event, ctx.person.id, event_id, summary=summary, start=start, end=end,
                        description=description, location=location)
    from .. import agenda
    await agenda.on_event_changed(ctx.person.id, event_id)      # its reminders move along
    return result


@handler
async def calendar_delete_event(ctx, event_id: str, confirmed_by_user: bool = False):
    from .. import google_calendar
    if not confirmed_by_user:
        return {"error": "Ask the person to confirm deleting this specific event first."}
    result = await _cal(google_calendar.delete_event, ctx.person.id, event_id)
    from .. import agenda
    await agenda.on_event_changed(ctx.person.id, event_id)      # its reminders become "not needed"
    return result or {"deleted": event_id}
