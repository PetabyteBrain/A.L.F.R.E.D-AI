"""Employees, CRM stage 4 (roadmap #58): the team directory, absences and holidays, onboarding and offboarding.

Who may do what (orchestrator/permissions.py):
- employees.self: one's own employee record, balance and absences; ask for holidays and other absences;
- employees.view: the directory, everyone's absences (the team calendar) and the checklists;
- employees.manage: propose changes to employee data, record absences for others, run checklists;
- employees.approve: decide those requests (area "employees" in orchestrator/approvals.py, self-approval allowed).

Every change to employee data is an approval request (decided 2026-10-12): adding someone, changing their details,
an absence and cancelling an approved absence. Checklist progress (ticking off steps) is work, not employee data,
and applies directly. Approved absences show on the team calendar, in the briefing, and Alfred doesn't place calls
others asked for to someone who is away (orchestrator/scheduler.py).

An employee can be linked to their Alfred account (`user_id`), which is how "me", the calls rule, the checklist
assignees and responsibilities in the CRM (records they own) find them; people without an account can be listed too.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import approvals, db
from .config import settings
from .identity.registry import Person

EMPLOYMENT = {"full_time": "Full-time", "part_time": "Part-time", "contractor": "Contractor", "intern": "Intern",
              "apprentice": "Apprentice"}
ABSENCE_KINDS = {"holiday": "Holiday", "sick": "Sick leave", "unpaid": "Unpaid leave", "training": "Training",
                 "parental": "Parental leave", "other": "Other absence"}
FIELDS = ("name", "user_id", "job_title", "department", "manager_id", "email", "phone", "employment", "workload",
          "start_date", "end_date", "holiday_days", "holiday_adjust", "timetrack_user", "notes")
FIELD_LABELS = {"name": "name", "user_id": "Alfred account", "job_title": "job title", "department": "department",
                "manager_id": "manager", "email": "email", "phone": "phone", "employment": "employment",
                "workload": "workload", "start_date": "first day", "end_date": "last day",
                "holiday_days": "holiday days a year", "holiday_adjust": "holiday corrections",
                "timetrack_user": "time-tracking name", "notes": "notes"}
WHO = {"starter": "whoever starts the checklist", "manager": "their manager", "employee": "the employee"}
DEFAULT_ONBOARDING = [
    {"title": "Prepare the employment contract and get it signed", "days": -14, "who": "starter"},
    {"title": "Set up the laptop, accounts and email address", "days": -3, "who": "starter"},
    {"title": "Add them to Alfred with the right roles and groups", "days": -1, "who": "starter"},
    {"title": "Welcome meeting and introduction to the team", "days": 0, "who": "manager"},
    {"title": "Walk through the company tools, files and processes", "days": 1, "who": "manager"},
    {"title": "Agree on goals for the first three months", "days": 7, "who": "manager"},
    {"title": "Check-in after the first month", "days": 30, "who": "manager"},
    {"title": "End-of-probation review", "days": 90, "who": "manager"},
]
DEFAULT_OFFBOARDING = [
    {"title": "Confirm the last working day and the remaining holidays in writing", "days": -30, "who": "starter"},
    {"title": "Plan the handover of clients, deals and tasks", "days": -14, "who": "manager"},
    {"title": "Exit conversation", "days": -3, "who": "manager"},
    {"title": "Collect the laptop, keys and cards", "days": 0, "who": "starter"},
    {"title": "Switch off their accounts and remove them from Alfred", "days": 0, "who": "starter"},
    {"title": "Write the reference letter", "days": 7, "who": "manager"},
    {"title": "Final payroll: holidays and overtime settled", "days": 14, "who": "starter"},
]
DEFAULT_SETTINGS = {"holiday_days": 25, "public_holidays": [], "onboarding": DEFAULT_ONBOARDING,
                    "offboarding": DEFAULT_OFFBOARDING}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EmployeeError(ValueError):
    """Message is safe to show."""


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _need(person: Person, permission: str) -> None:
    if not person.can(permission):
        raise EmployeeError("You don't have permission to do that.")


def _text(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _date(v, label: str) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip()[:10]
    try:
        if not _DATE.match(s):
            raise ValueError
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise EmployeeError(f"The {label} must be a date like 2026-11-02.")


def _human(d: str | date | None) -> str:
    if not d:
        return ""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d.strftime("%a %d %b %Y").replace(" 0", " ")


def _half(x: float) -> float:
    return round(x * 2) / 2


# --- settings ---------------------------------------------------------------------------------------

def get_settings() -> dict:
    stored = db.get_setting("employees", {}) or {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}


def _steps(items, label: str) -> list[dict]:
    if not isinstance(items, list) or len(items) > 40:
        raise EmployeeError(f"The {label} checklist needs a list of at most 40 steps.")
    out = []
    for it in items:
        it = it if isinstance(it, dict) else {}
        title = _text(it.get("title"), 160)
        if not title:
            continue
        try:
            days = int(it.get("days") or 0)
        except (TypeError, ValueError):
            raise EmployeeError(f"“{title}”: days must be a whole number (negative = before the day).")
        if not -365 <= days <= 365:
            raise EmployeeError(f"“{title}”: at most a year before or after.")
        who = it.get("who") if it.get("who") in WHO else "starter"
        out.append({"title": title, "days": days, "who": who})
    return out


def save_settings(person: Person, data: dict) -> dict:
    _need(person, "employees.manage")
    s = get_settings()
    unknown = set(data) - set(DEFAULT_SETTINGS)
    if unknown:
        raise EmployeeError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    if "holiday_days" in data:
        try:
            hd = float(data["holiday_days"])
        except (TypeError, ValueError):
            raise EmployeeError("Holiday days must be a number.")
        if not 0 <= hd <= 60:
            raise EmployeeError("Holiday days must be between 0 and 60.")
        s["holiday_days"] = _half(hd)
    if "public_holidays" in data:
        days = data["public_holidays"]
        if isinstance(days, str):
            days = re.split(r"[\s,;]+", days)
        if not isinstance(days, list) or len(days) > 80:
            raise EmployeeError("Public holidays: a list of at most 80 dates.")
        s["public_holidays"] = sorted({_date(d, "public holiday") for d in days if str(d).strip()})
    for kind in ("onboarding", "offboarding"):
        if kind in data:
            s[kind] = _steps(data[kind], kind)
    db.set_setting("employees", s)
    db.log_activity("employees", f"{person.display_name} changed the employee settings", user_id=person.id)
    return s


# --- working days and balances ---------------------------------------------------------------------

def working_days(start: date, end: date, *, half_day: bool = False, holidays: set[str] | None = None) -> float:
    """Monday to Friday between two dates (both included), without public holidays."""
    holidays = holidays if holidays is not None else set(get_settings()["public_holidays"])
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            n += 1
        d += timedelta(days=1)
    return 0.5 if half_day and n else float(n)


def _clip_days(a: dict, year: int, holidays: set[str], before: date | None = None, after: date | None = None) -> float:
    """Working days of an absence that fall in `year` (optionally only before / from a date)."""
    s, e = date.fromisoformat(a["start_date"]), date.fromisoformat(a["end_date"])
    s, e = max(s, date(year, 1, 1)), min(e, date(year, 12, 31))
    if before:
        e = min(e, before - timedelta(days=1))
    if after:
        s = max(s, after)
    if s > e:
        return 0.0
    return working_days(s, e, half_day=bool(a["half_day"]), holidays=holidays)


def allowance(emp: dict, year: int) -> float:
    """The year's holiday days, pro rata for a start or end within the year, to the half day."""
    y0, y1 = date(year, 1, 1), date(year, 12, 31)
    s = max(date.fromisoformat(emp["start_date"]), y0) if emp.get("start_date") else y0
    e = min(date.fromisoformat(emp["end_date"]), y1) if emp.get("end_date") else y1
    if s > e:
        return 0.0
    return _half(float(emp["holiday_days"] or 0) * ((e - s).days + 1) / ((y1 - y0).days + 1))


def balance(emp: dict, year: int | None = None) -> dict:
    today = _today()
    year = year or today.year
    holidays = set(get_settings()["public_holidays"])
    adjust = json.loads(emp.get("holiday_adjust") or "{}") if isinstance(emp.get("holiday_adjust"), str) \
        else (emp.get("holiday_adjust") or {})
    rows = db.query("SELECT * FROM employee_absences WHERE employee_id = ? AND kind = 'holiday' AND status IN "
                    "('approved','pending') AND start_date <= ? AND end_date >= ?",
                    (emp["id"], f"{year}-12-31", f"{year}-01-01"))
    taken = sum(_clip_days(a, year, holidays, before=today) for a in rows if a["status"] == "approved")
    planned = sum(_clip_days(a, year, holidays, after=today) for a in rows if a["status"] == "approved")
    pending = sum(_clip_days(a, year, holidays) for a in rows if a["status"] == "pending")
    total = allowance(emp, year) + float(adjust.get(str(year), 0) or 0)
    return {"year": year, "allowance": allowance(emp, year), "adjust": float(adjust.get(str(year), 0) or 0),
            "total": total, "taken": taken, "planned": planned, "pending": pending,
            "remaining": total - taken - planned}


# --- records ---------------------------------------------------------------------------------------

def _names() -> dict[int, str]:
    return {u["id"]: u["display_name"] for u in db.query("SELECT id, display_name FROM users")}


def _row(employee_id: int) -> dict:
    row = db.query_one("SELECT * FROM employees WHERE id = ?", (employee_id,))
    if not row:
        raise EmployeeError("No such employee.")
    return row


def own_record(person: Person) -> dict | None:
    return db.query_one("SELECT * FROM employees WHERE user_id = ?", (person.id,))


def _may_see(person: Person, row: dict) -> bool:
    return person.can("employees.view") or (row.get("user_id") == person.id and person.can("employees.self"))


def _open_kinds() -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for r in db.query("SELECT employee_id, kind FROM employee_checklists WHERE closed_at IS NULL"):
        out.setdefault(r["employee_id"], set()).add(r["kind"])
    return out


def status_of(row: dict, today: date, open_kinds: set[str]) -> str:
    t = today.isoformat()
    if row.get("end_date") and row["end_date"] < t:
        return "former"
    if "offboarding" in open_kinds or row.get("end_date"):
        return "leaving"
    if row.get("start_date") and row["start_date"] > t:
        return "starting"
    if "onboarding" in open_kinds:
        return "onboarding"
    return "active"


def _away_map(day: date) -> dict[int, dict]:
    d = day.isoformat()
    return {a["employee_id"]: a for a in db.query(
        "SELECT * FROM employee_absences WHERE status = 'approved' AND start_date <= ? AND end_date >= ?", (d, d))}


def _view(row: dict, names: dict, today: date, open_kinds: dict, away: dict, managers: dict) -> dict:
    r = dict(row)
    r["holiday_adjust"] = json.loads(r.get("holiday_adjust") or "{}")
    r["user_name"] = names.get(r.get("user_id"))
    r["manager_name"] = managers.get(r.get("manager_id"))
    r["status"] = status_of(row, today, open_kinds.get(row["id"], set()))
    a = away.get(row["id"])
    r["away"] = ({"kind": a["kind"], "label": ABSENCE_KINDS.get(a["kind"], a["kind"]), "until": a["end_date"],
                  "half_day": bool(a["half_day"])} if a else None)
    r["employment_label"] = EMPLOYMENT.get(r["employment"], r["employment"])
    return r


def _context() -> tuple[dict, date, dict, dict, dict]:
    today = _today()
    managers = {e["id"]: e["name"] for e in db.query("SELECT id, name FROM employees")}
    return _names(), today, _open_kinds(), _away_map(today), managers


def directory(person: Person, *, q: str = "", include_former: bool = False) -> list[dict]:
    _need(person, "employees.view")
    names, today, kinds, away, managers = _context()
    rows = db.query("SELECT * FROM employees ORDER BY lower(name)")
    out = [_view(r, names, today, kinds, away, managers) for r in rows]
    if not include_former:
        out = [r for r in out if r["status"] != "former"]
    if q:
        ql = q.strip().lower()
        out = [r for r in out if any(ql in (r.get(k) or "").lower()
                                     for k in ("name", "job_title", "department", "email", "user_name"))]
    return out


def find(person: Person, ref) -> dict:
    """An employee by id, by (part of) their name, or "me"."""
    if str(ref).strip().lower() in ("me", "myself", "i"):
        row = own_record(person)
        if not row:
            raise EmployeeError("You're not in the employee directory.")
        return row
    if isinstance(ref, int) or str(ref).strip().isdigit():
        row = _row(int(ref))
    else:
        ql = str(ref).strip().lower()
        rows = [r for r in db.query("SELECT * FROM employees") if ql in r["name"].lower()]
        exact = [r for r in rows if r["name"].lower() == ql]
        if not rows:
            raise EmployeeError(f"There's nobody called “{ref}” in the employee directory.")
        if len(rows) > 1 and len(exact) != 1:
            raise EmployeeError("Several employees match: " + "; ".join(f"#{r['id']} {r['name']}" for r in rows)
                                + ". Which one?")
        row = (exact or rows)[0]
    if not _may_see(person, row):
        raise EmployeeError("No such employee.")
    return row


def _history(employee_id: int, names: dict) -> list[dict]:
    out = []
    for h in db.query("SELECT * FROM employee_history WHERE employee_id = ? ORDER BY id DESC LIMIT 50", (employee_id,)):
        after = json.loads(h["after"] or "{}")
        out.append({"id": h["id"], "action": h["action"], "ts": h["ts"], "by": names.get(h["user_id"]),
                    "approval_id": h["approval_id"], "fields": [FIELD_LABELS.get(k, k) for k in after]})
    return out


def get(person: Person, employee_id: int) -> dict:
    row = _row(employee_id)
    if not _may_see(person, row):
        raise EmployeeError("No such employee.")
    names, today, kinds, away, managers = _context()
    r = _view(row, names, today, kinds, away, managers)
    r["balance"] = balance(row)
    r["absences"] = absences(person, employee_id=employee_id, start=f"{today.year - 1}-12-01",
                             end=f"{today.year + 1}-12-31", statuses=("pending", "approved", "rejected", "cancelled"))
    r["checklists"] = checklists(person, employee_id=employee_id, open_only=False)
    r["reports"] = [{"id": e["id"], "name": e["name"]} for e in
                    db.query("SELECT id, name FROM employees WHERE manager_id = ? ORDER BY name", (employee_id,))]
    r["pending_changes"] = [{"id": a["id"], "summary": a["summary"]} for a in db.query(
        "SELECT id, summary FROM approvals WHERE area = 'employees' AND kind = 'save' AND status = 'pending' "
        "AND payload LIKE ?", (f'%"employee_id": {employee_id},%',))]
    if row.get("user_id"):
        uid = row["user_id"]
        r["work"] = {"open_tasks": db.query_one("SELECT COUNT(*) AS n FROM tasks WHERE status = 'active' AND "
                                                "owner_user_id = ?", (uid,))["n"]}
        if person.can("crm.use"):
            r["work"]["companies"] = db.query_one("SELECT COUNT(*) AS n FROM crm_companies WHERE owner_id = ? AND "
                                                  "archived_at IS NULL", (uid,))["n"]
            deals = db.query("SELECT d.value FROM crm_deals d JOIN crm_stages s ON s.id = d.stage_id WHERE "
                             "d.owner_id = ? AND d.archived_at IS NULL AND s.kind = 'open'", (uid,))
            r["work"]["open_deals"] = len(deals)
            r["work"]["open_deal_value"] = round(sum(d["value"] or 0 for d in deals), 2)
    r["history"] = _history(employee_id, names) if person.can("employees.view") else []
    mine = row.get("user_id") == person.id
    r["can"] = {"manage": person.can("employees.manage"), "approve": person.can("employees.approve"),
                "request_absence": person.can("employees.manage") or (mine and person.can("employees.self"))}
    r["is_me"] = mine
    return r


# --- changes (always through an approval) ------------------------------------------------------------

def _clean(data: dict, existing: dict | None) -> dict:
    from .crm import _email, _phone, CrmError
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise EmployeeError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    out: dict = {}
    for k, v in data.items():
        if k == "name":
            out[k] = _text(v, 120)
            if not out[k]:
                raise EmployeeError("An employee needs a name.")
        elif k in ("job_title", "department"):
            out[k] = _text(v, 80)
        elif k == "timetrack_user":
            out[k] = _text(v, 60)
        elif k == "notes":
            out[k] = str(v or "").replace("\r\n", "\n").strip()[:3000]
        elif k == "email":
            try:
                out[k] = _email(v)
            except CrmError as e:
                raise EmployeeError(str(e))
        elif k == "phone":
            out[k] = _phone(v)
        elif k == "employment":
            if v not in EMPLOYMENT:
                raise EmployeeError(f"Employment must be one of: {', '.join(EMPLOYMENT)}.")
            out[k] = v
        elif k == "workload":
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                raise EmployeeError("Workload is a percentage, e.g. 80.")
            if not 1 <= out[k] <= 100:
                raise EmployeeError("Workload must be between 1 and 100 %.")
        elif k == "holiday_days":
            try:
                out[k] = _half(float(v))
            except (TypeError, ValueError):
                raise EmployeeError("Holiday days must be a number.")
            if not 0 <= out[k] <= 60:
                raise EmployeeError("Holiday days must be between 0 and 60.")
        elif k == "holiday_adjust":
            if not isinstance(v, dict) or len(v) > 10:
                raise EmployeeError("Holiday corrections are year: days pairs.")
            adj = {}
            for y, n in v.items():
                if not re.fullmatch(r"20\d\d", str(y)):
                    raise EmployeeError("Holiday corrections: years like 2026.")
                try:
                    n = _half(float(n))
                except (TypeError, ValueError):
                    raise EmployeeError("Holiday corrections must be numbers of days.")
                if n:
                    if not -60 <= n <= 60:
                        raise EmployeeError("A holiday correction is at most 60 days.")
                    adj[str(y)] = n
            out[k] = json.dumps(dict(sorted(adj.items())))
        elif k in ("start_date", "end_date"):
            out[k] = _date(v, FIELD_LABELS[k])
        elif k == "user_id":
            if v in (None, "", 0):
                out[k] = None
                continue
            try:
                uid = int(v)
            except (TypeError, ValueError):
                raise EmployeeError("Unknown Alfred account.")
            if not db.query_one("SELECT 1 FROM users WHERE id = ? AND active = 1", (uid,)):
                raise EmployeeError("Unknown Alfred account.")
            other = db.query_one("SELECT id, name FROM employees WHERE user_id = ?", (uid,))
            if other and (not existing or other["id"] != existing["id"]):
                raise EmployeeError(f"That account already belongs to {other['name']}.")
            out[k] = uid
        elif k == "manager_id":
            if v in (None, "", 0):
                out[k] = None
                continue
            try:
                mid = int(v)
            except (TypeError, ValueError):
                raise EmployeeError("Unknown manager.")
            _row(mid)
            # No one manages themselves, directly or around a loop.
            seen, cur = set(), mid
            while cur:
                if existing and cur == existing["id"]:
                    raise EmployeeError("That would make them their own manager.")
                if cur in seen:
                    break
                seen.add(cur)
                cur = (db.query_one("SELECT manager_id FROM employees WHERE id = ?", (cur,)) or {}).get("manager_id")
            out[k] = mid
    start = out.get("start_date", (existing or {}).get("start_date"))
    end = out.get("end_date", (existing or {}).get("end_date"))
    if start and end and end < start:
        raise EmployeeError("The last day can't be before the first day.")
    return out


def _describe(k: str, v) -> str:
    if v in (None, ""):
        return "none"
    if k == "user_id":
        return (db.query_one("SELECT display_name FROM users WHERE id = ?", (v,)) or {}).get("display_name", "?")
    if k == "manager_id":
        return (db.query_one("SELECT name FROM employees WHERE id = ?", (v,)) or {}).get("name", "?")
    if k == "employment":
        return EMPLOYMENT.get(v, v)
    if k in ("start_date", "end_date"):
        return _human(v)
    if k == "workload":
        return f"{v} %"
    if k == "holiday_adjust":
        return ", ".join(f"{y}: {n:+g}" for y, n in json.loads(v).items()) or "none"
    if k == "notes":
        return "(changed)"
    return str(v)


def request_change(person: Person, data: dict, employee_id: int | None = None, *, onboarding: bool = False,
                   offboarding: bool = False, reason: str = "") -> dict:
    """Add someone (no id) or change their details; also starts onboarding / offboarding once approved."""
    _need(person, "employees.manage")
    existing = _row(employee_id) if employee_id else None
    clean = _clean(data, existing)
    if existing:
        clean = {k: v for k, v in clean.items() if existing.get(k) != v}
    elif not clean.get("name"):
        raise EmployeeError("What's the new employee's name?")
    open_kinds = _open_kinds().get(employee_id, set()) if employee_id else set()
    if onboarding and "onboarding" in open_kinds:
        onboarding = False
    if offboarding and "offboarding" in open_kinds:
        offboarding = False
    if offboarding and not (clean.get("end_date") or (existing or {}).get("end_date")):
        raise EmployeeError("Offboarding needs their last working day (end date).")
    if not clean and not onboarding and not offboarding:
        raise EmployeeError("That's what's on record already; nothing to change.")
    if existing:
        parts = [f"{FIELD_LABELS[k]} {_describe(k, existing.get(k))} → {_describe(k, v)}" for k, v in clean.items()]
        summary = f"Change {existing['name']}: " + "; ".join(parts) if parts else f"{existing['name']}:"
    else:
        extras = [x for x in (clean.get("job_title"), f"starts {_human(clean['start_date'])}"
                              if clean.get("start_date") else "") if x]
        summary = f"Add the employee {clean['name']}" + (f" ({', '.join(extras)})" if extras else "")
    if onboarding:
        summary += ", with the onboarding checklist"
    if offboarding:
        summary += ", and start offboarding"
    summary += "." + (f" Reason: {_text(reason, 200)}" if reason else "")
    payload = {"employee_id": employee_id, "changes": clean, "onboarding": bool(onboarding),
               "offboarding": bool(offboarding), "starter": person.id}
    return approvals.create("employees", "save", summary, payload, person)


@approvals.handler("employees", "save")
def _approved_save(payload: dict, actor: Person) -> str:
    eid = payload.get("employee_id")
    existing = _row(int(eid)) if eid else None
    changes = _clean(payload.get("changes") or {}, existing)     # checked again: things may have moved on
    now = db.now_iso()
    if existing:
        changes = {k: v for k, v in changes.items() if existing.get(k) != v}
        if changes:
            db.execute(f"UPDATE employees SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ? WHERE id = ?",
                       (*changes.values(), now, eid))
            db.execute("INSERT INTO employee_history(employee_id, action, before, after, user_id, ts) "
                       "VALUES (?,?,?,?,?,?)", (eid, "update", json.dumps({k: existing.get(k) for k in changes}),
                                                json.dumps(changes), actor.id, now))
        name = changes.get("name") or existing["name"]
        text = f"Updated {name}" if changes else name
    else:
        if not changes.get("name"):
            raise ValueError("The new employee has no name.")
        changes.setdefault("holiday_days", get_settings()["holiday_days"])
        cols = {**changes, "created_by": payload.get("starter") or actor.id, "created_at": now, "updated_at": now}
        eid = db.execute(f"INSERT INTO employees({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         list(cols.values()))
        db.execute("INSERT INTO employee_history(employee_id, action, before, after, user_id, ts) VALUES (?,?,?,?,?,?)",
                   (eid, "create", None, json.dumps(changes), actor.id, now))
        name = changes["name"]
        text = f"Added {name}"
    extra = []
    starter = int(payload.get("starter") or actor.id)
    for kind in ("onboarding", "offboarding"):
        if payload.get(kind):
            _create_checklist(int(eid), kind, starter)
            extra.append(f"{kind} checklist started")
    db.log_activity("employees", f"{actor.display_name} approved: {text}" + (f" ({', '.join(extra)})" if extra else ""),
                    user_id=actor.id)
    return text + (f"; {', '.join(extra)}" if extra else "") + "."


# --- absences ----------------------------------------------------------------------------------------

def _absence_view(a: dict, names: dict | None = None) -> dict:
    r = dict(a)
    r["kind_label"] = ABSENCE_KINDS.get(r["kind"], r["kind"])
    r["half_day"] = bool(r["half_day"])
    r["requested_by_name"] = (names or {}).get(r.get("requested_by"))
    return r


def _absence_text(a: dict, name: str) -> str:
    when = (_human(a["start_date"]) if a["start_date"] == a["end_date"]
            else f"{_human(a['start_date'])} – {_human(a['end_date'])}")
    days = f"{a['days']:g} working day{'' if a['days'] == 1 else 's'}"
    return f"{ABSENCE_KINDS.get(a['kind'], a['kind'])} for {name}: {when}" + (" (half day)" if a["half_day"] else "") \
        + f", {days}"


def request_absence(person: Person, *, kind: str, start: str, end: str | None = None, half_day: bool = False,
                    note: str = "", employee_id: int | None = None) -> dict:
    own = own_record(person)
    if employee_id and (not own or own["id"] != int(employee_id)):
        _need(person, "employees.manage")
        emp = _row(int(employee_id))
    else:
        if not own:
            raise EmployeeError("You're not in the employee directory yet; ask whoever runs HR to add you.")
        if not (person.can("employees.self") or person.can("employees.manage")):
            raise EmployeeError("You don't have permission to do that.")
        emp = own
    if kind not in ABSENCE_KINDS:
        raise EmployeeError(f"What kind of absence? One of: {', '.join(ABSENCE_KINDS)}.")
    s = _date(start, "first day")
    e = _date(end, "last day") or s
    if not s:
        raise EmployeeError("From which day?")
    if e < s:
        raise EmployeeError("The last day can't be before the first day.")
    sd, ed, today = date.fromisoformat(s), date.fromisoformat(e), _today()
    if (ed - sd).days > 366:
        raise EmployeeError("At most a year at a time.")
    if sd < today - timedelta(days=366) or sd > today + timedelta(days=730):
        raise EmployeeError("That date is too far away.")
    half_day = bool(half_day) and s == e
    clash = db.query_one("SELECT * FROM employee_absences WHERE employee_id = ? AND status IN ('pending','approved') "
                         "AND start_date <= ? AND end_date >= ?", (emp["id"], e, s))
    if clash:
        raise EmployeeError(f"That overlaps with {_absence_text(clash, emp['name'])} ({clash['status']}).")
    days = working_days(sd, ed, half_day=half_day)
    if not days:
        raise EmployeeError("Those are all weekends or public holidays; nothing to take off.")
    now = db.now_iso()
    cols = {"employee_id": emp["id"], "kind": kind, "start_date": s, "end_date": e, "half_day": int(half_day),
            "days": days, "note": _text(note, 300), "status": "pending", "requested_by": person.id, "created_at": now}
    aid = db.execute(f"INSERT INTO employee_absences({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     list(cols.values()))
    row = db.query_one("SELECT * FROM employee_absences WHERE id = ?", (aid,))
    summary = _absence_text(row, emp["name"])
    if kind == "holiday":
        b = balance(emp, sd.year)
        left = b["remaining"] - b["pending"] + days      # before this request
        summary += f" ({left:g} left in {sd.year}" + (", so this goes over" if days > left else "") + ")"
    if cols["note"]:
        summary += f". Note: {cols['note']}"
    summary += "."
    try:
        a = approvals.create("employees", "absence", summary, {"absence_id": aid}, person)
    except Exception:
        db.execute("DELETE FROM employee_absences WHERE id = ?", (aid,))
        raise
    db.execute("UPDATE employee_absences SET approval_id = ? WHERE id = ?", (a["id"], aid))
    return {"absence": _absence_view(db.query_one("SELECT * FROM employee_absences WHERE id = ?", (aid,))),
            "approval": a}


@approvals.handler("employees", "absence")
def _approved_absence(payload: dict, actor: Person) -> str:
    a = db.query_one("SELECT a.*, e.name FROM employee_absences a JOIN employees e ON e.id = a.employee_id "
                     "WHERE a.id = ?", (int(payload["absence_id"]),))
    if not a or a["status"] != "pending":
        raise ValueError("That absence request isn't open any more.")
    db.execute("UPDATE employee_absences SET status = 'approved', decided_at = ? WHERE id = ?", (db.now_iso(), a["id"]))
    db.log_activity("employees", f"{actor.display_name} approved: {_absence_text(a, a['name'])}", user_id=actor.id)
    return f"{_absence_text(a, a['name'])}: approved."


@approvals.on_close("employees", "absence")
def _closed_absence(payload: dict, actor: Person) -> None:
    a = db.query_one("SELECT x.status FROM employee_absences e JOIN approvals x ON x.id = e.approval_id WHERE e.id = ?",
                     (int(payload["absence_id"]),))
    status = "cancelled" if a and a["status"] == "cancelled" else "rejected"
    db.execute("UPDATE employee_absences SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
               (status, db.now_iso(), int(payload["absence_id"])))


def cancel_absence(person: Person, absence_id: int) -> dict:
    """Withdraw a request, or ask to cancel an approved absence that hasn't passed (that needs approval too)."""
    a = db.query_one("SELECT a.*, e.name, e.user_id FROM employee_absences a JOIN employees e ON e.id = a.employee_id "
                     "WHERE a.id = ?", (absence_id,))
    mine = a and a["user_id"] == person.id
    if not a or not (mine or person.can("employees.manage")):
        raise EmployeeError("No such absence.")
    if a["status"] == "pending":
        db.execute("UPDATE employee_absences SET status = 'cancelled', decided_at = ? WHERE id = ?",
                   (db.now_iso(), absence_id))
        if a["approval_id"]:
            db.execute("UPDATE approvals SET status = 'cancelled', decided_by = ?, decided_at = ? WHERE id = ? AND "
                       "status = 'pending'", (person.id, db.now_iso(), a["approval_id"]))
        db.log_activity("employees", f"{person.display_name} withdrew the request: {_absence_text(a, a['name'])}",
                        user_id=person.id)
        return {"cancelled": True, "absence_id": absence_id}
    if a["status"] != "approved":
        raise EmployeeError(f"It's already {a['status']}.")
    if a["end_date"] < _today().isoformat():
        raise EmployeeError("That absence is over; ask whoever manages employees to correct it.")
    summary = f"Cancel an approved absence: {_absence_text(a, a['name'])}."
    return {"approval": approvals.create("employees", "absence_cancel", summary, {"absence_id": absence_id}, person)}


@approvals.handler("employees", "absence_cancel")
def _approved_absence_cancel(payload: dict, actor: Person) -> str:
    a = db.query_one("SELECT a.*, e.name FROM employee_absences a JOIN employees e ON e.id = a.employee_id "
                     "WHERE a.id = ?", (int(payload["absence_id"]),))
    if not a or a["status"] != "approved":
        raise ValueError("That absence isn't approved any more.")
    db.execute("UPDATE employee_absences SET status = 'cancelled', decided_at = ? WHERE id = ?", (db.now_iso(), a["id"]))
    db.log_activity("employees", f"{actor.display_name} cancelled: {_absence_text(a, a['name'])} (approved)",
                    user_id=actor.id)
    return f"Cancelled: {_absence_text(a, a['name'])}."


def absences(person: Person, *, start: str, end: str, employee_id: int | None = None,
             statuses: tuple = ("pending", "approved")) -> list[dict]:
    """Absences between two dates: everyone's with employees.view, otherwise only one's own."""
    s, e = _date(start, "start") or _today().isoformat(), _date(end, "end") or _today().isoformat()
    where, params = "a.start_date <= ? AND a.end_date >= ?", [e, s]
    if not person.can("employees.view"):
        own = own_record(person)
        if not own or not person.can("employees.self") or (employee_id and employee_id != own["id"]):
            return []
        employee_id = own["id"]
    if employee_id:
        where += " AND a.employee_id = ?"
        params.append(employee_id)
    where += f" AND a.status IN ({','.join('?' * len(statuses))})"
    params += list(statuses)
    names = _names()
    rows = db.query(f"SELECT a.*, e.name AS employee_name, e.user_id FROM employee_absences a JOIN employees e "
                    f"ON e.id = a.employee_id WHERE {where} ORDER BY a.start_date, e.name", params)
    return [_absence_view(r, names) for r in rows]


def calendar(person: Person, month: str) -> dict:
    """One month of the team calendar: people as rows, their absences, weekends and public holidays."""
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        month = _today().strftime("%Y-%m")
    y, m = int(month[:4]), int(month[5:])
    first = date(y, m, 1)
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    holidays = set(get_settings()["public_holidays"])
    items = absences(person, start=first.isoformat(), end=last.isoformat())
    if person.can("employees.view"):
        people = [{"id": r["id"], "name": r["name"], "department": r["department"]}
                  for r in directory(person) if not r["start_date"] or r["start_date"] <= last.isoformat()]
    else:
        own = own_record(person)
        people = [{"id": own["id"], "name": own["name"], "department": own["department"]}] if own else []
    return {"month": month, "days": [{"date": (first + timedelta(days=i)).isoformat(),
                                      "weekend": (first + timedelta(days=i)).weekday() >= 5,
                                      "holiday": (first + timedelta(days=i)).isoformat() in holidays}
                                     for i in range((last - first).days + 1)],
            "people": people, "absences": items, "today": _today().isoformat()}


def away(user_id: int, day: date | None = None) -> dict | None:
    """A full-day approved absence of the person with this Alfred account on that day, with the day they're back."""
    day = day or _today()
    emp = db.query_one("SELECT id, name FROM employees WHERE user_id = ?", (user_id,))
    if not emp:
        return None
    holidays = set(get_settings()["public_holidays"])
    a = db.query_one("SELECT * FROM employee_absences WHERE employee_id = ? AND status = 'approved' AND half_day = 0 "
                     "AND start_date <= ? AND end_date >= ?", (emp["id"], day.isoformat(), day.isoformat()))
    if not a:
        return None
    back = date.fromisoformat(a["end_date"]) + timedelta(days=1)
    for _ in range(120):                       # skip weekends, public holidays and absences that follow on
        nxt = db.query_one("SELECT end_date FROM employee_absences WHERE employee_id = ? AND status = 'approved' AND "
                           "half_day = 0 AND start_date <= ? AND end_date >= ?", (emp["id"], back.isoformat(),
                                                                                 back.isoformat()))
        if nxt:
            back = date.fromisoformat(nxt["end_date"]) + timedelta(days=1)
        elif back.weekday() >= 5 or back.isoformat() in holidays:
            back += timedelta(days=1)
        else:
            break
    return {"name": emp["name"], "kind": a["kind"], "label": ABSENCE_KINDS.get(a["kind"], a["kind"]).lower(),
            "until": a["end_date"], "back": back.isoformat(), "back_human": _human(back)}


def away_today(day: date | None = None) -> list[dict]:
    day = day or _today()
    return [{"name": a["name"], "absence": ABSENCE_KINDS.get(a["kind"], a["kind"]).lower(), "until": _human(a["end_date"]),
             **({"half_day": True} if a["half_day"] else {})}
            for a in db.query("SELECT a.*, e.name FROM employee_absences a JOIN employees e ON e.id = a.employee_id "
                              "WHERE a.status = 'approved' AND a.start_date <= ? AND a.end_date >= ? ORDER BY e.name",
                              (day.isoformat(), day.isoformat()))]


# --- onboarding / offboarding checklists ---------------------------------------------------------------

def _assignee(who: str, emp: dict, starter: int | None) -> int | None:
    if who == "employee" and emp.get("user_id"):
        return emp["user_id"]
    if who == "manager" and emp.get("manager_id"):
        m = db.query_one("SELECT user_id FROM employees WHERE id = ?", (emp["manager_id"],))
        if m and m["user_id"]:
            return m["user_id"]
    return starter


def _create_checklist(employee_id: int, kind: str, starter: int | None, anchor: str | None = None) -> int:
    emp = _row(employee_id)
    if db.query_one("SELECT 1 FROM employee_checklists WHERE employee_id = ? AND kind = ? AND closed_at IS NULL",
                    (employee_id, kind)):
        raise EmployeeError(f"{emp['name']} already has an open {kind} checklist.")
    anchor = anchor or (emp["start_date"] if kind == "onboarding" else emp["end_date"]) or _today().isoformat()
    base = date.fromisoformat(anchor)
    now = db.now_iso()
    with db.tx() as conn:
        cid = conn.execute("INSERT INTO employee_checklists(employee_id, kind, anchor_date, created_by, created_at) "
                           "VALUES (?,?,?,?,?)", (employee_id, kind, anchor, starter, now)).lastrowid
        for i, step in enumerate(get_settings()[kind]):
            conn.execute("INSERT INTO employee_tasks(checklist_id, position, title, due_date, assignee_id) "
                         "VALUES (?,?,?,?,?)", (cid, i, step["title"], (base + timedelta(days=step["days"])).isoformat(),
                                                _assignee(step["who"], emp, starter)))
    return cid


def start_checklist(person: Person, employee_id: int, kind: str, anchor: str | None = None) -> dict:
    """Starting a checklist is work on the process, not a change to the person's data, so it applies directly."""
    _need(person, "employees.manage")
    if kind not in ("onboarding", "offboarding"):
        raise EmployeeError("Choose onboarding or offboarding.")
    cid = _create_checklist(employee_id, kind, person.id, _date(anchor, "date"))
    emp = _row(employee_id)
    db.log_activity("employees", f"{person.display_name} started the {kind} of {emp['name']}", user_id=person.id)
    return next(c for c in checklists(person, employee_id=employee_id, open_only=False) if c["id"] == cid)


def _task(task_id: int) -> dict:
    t = db.query_one("SELECT t.*, c.employee_id, c.kind, c.closed_at FROM employee_tasks t JOIN employee_checklists c "
                     "ON c.id = t.checklist_id WHERE t.id = ?", (task_id,))
    if not t:
        raise EmployeeError("No such checklist step.")
    return t


def _sync_closed(checklist_id: int) -> None:
    left = db.query_one("SELECT COUNT(*) AS n FROM employee_tasks WHERE checklist_id = ? AND done_at IS NULL",
                        (checklist_id,))["n"]
    db.execute("UPDATE employee_checklists SET closed_at = CASE WHEN ? = 0 THEN COALESCE(closed_at, ?) ELSE NULL END "
               "WHERE id = ?", (left, db.now_iso(), checklist_id))


def tick(person: Person, task_id: int, done: bool = True) -> dict:
    t = _task(task_id)
    if not (person.can("employees.manage") or t["assignee_id"] == person.id):
        raise EmployeeError("Only the person responsible, or someone who manages employees, can tick this off.")
    db.execute("UPDATE employee_tasks SET done_at = ?, done_by = ? WHERE id = ?",
               (db.now_iso() if done else None, person.id if done else None, task_id))
    _sync_closed(t["checklist_id"])
    return _task(task_id)


def add_task(person: Person, checklist_id: int, title: str, due_date: str | None = None,
             assignee_id: int | None = None) -> dict:
    _need(person, "employees.manage")
    c = db.query_one("SELECT * FROM employee_checklists WHERE id = ?", (checklist_id,))
    if not c:
        raise EmployeeError("No such checklist.")
    title = _text(title, 160)
    if not title:
        raise EmployeeError("What needs doing?")
    if assignee_id and not db.query_one("SELECT 1 FROM users WHERE id = ? AND active = 1", (assignee_id,)):
        raise EmployeeError("Unknown person.")
    pos = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM employee_tasks WHERE checklist_id = ?",
                       (checklist_id,))["p"]
    tid = db.execute("INSERT INTO employee_tasks(checklist_id, position, title, due_date, assignee_id) VALUES (?,?,?,?,?)",
                     (checklist_id, pos, title, _date(due_date, "due date"), assignee_id or person.id))
    _sync_closed(checklist_id)
    return _task(tid)


def update_task(person: Person, task_id: int, *, due_date: str | None = None, assignee_id: int | None = None) -> dict:
    _need(person, "employees.manage")
    _task(task_id)
    if assignee_id is not None:
        if assignee_id and not db.query_one("SELECT 1 FROM users WHERE id = ? AND active = 1", (assignee_id,)):
            raise EmployeeError("Unknown person.")
        db.execute("UPDATE employee_tasks SET assignee_id = ?, reminded_on = NULL WHERE id = ?",
                   (assignee_id or None, task_id))
    if due_date is not None:
        db.execute("UPDATE employee_tasks SET due_date = ?, reminded_on = NULL WHERE id = ?",
                   (_date(due_date, "due date"), task_id))
    return _task(task_id)


def remove_task(person: Person, task_id: int) -> None:
    _need(person, "employees.manage")
    t = _task(task_id)
    db.execute("DELETE FROM employee_tasks WHERE id = ?", (task_id,))
    _sync_closed(t["checklist_id"])


def close_checklist(person: Person, checklist_id: int) -> None:
    """Close a checklist early (e.g. the person didn't start after all); the open steps stay as they are."""
    _need(person, "employees.manage")
    db.execute("UPDATE employee_checklists SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
               (db.now_iso(), checklist_id))


def checklists(person: Person, *, employee_id: int | None = None, open_only: bool = True,
               mine: bool = False) -> list[dict]:
    """Checklists with their steps. Without employees.view one only sees steps assigned to oneself."""
    see_all = person.can("employees.view")
    where, params = "1=1", []
    if employee_id:
        where += " AND c.employee_id = ?"
        params.append(employee_id)
    if open_only:
        where += " AND c.closed_at IS NULL"
    names = _names()
    today = _today().isoformat()
    out = []
    for c in db.query(f"SELECT c.*, e.name AS employee_name, e.user_id AS employee_user FROM employee_checklists c "
                      f"JOIN employees e ON e.id = c.employee_id WHERE {where} ORDER BY c.closed_at IS NOT NULL, "
                      f"c.anchor_date, c.id", params):
        tasks = db.query("SELECT * FROM employee_tasks WHERE checklist_id = ? ORDER BY position, id", (c["id"],))
        if mine or (not see_all and c["employee_user"] != person.id):   # one's own onboarding is shown in full
            tasks = [t for t in tasks if t["assignee_id"] == person.id]
            if not tasks:
                continue
        for t in tasks:
            t["assignee_name"] = names.get(t["assignee_id"])
            t["done_by_name"] = names.get(t["done_by"])
            t["overdue"] = bool(not t["done_at"] and t["due_date"] and t["due_date"] < today)
            t["mine"] = t["assignee_id"] == person.id
        done = sum(1 for t in tasks if t["done_at"])
        out.append({**c, "created_by_name": names.get(c["created_by"]), "tasks": tasks, "done": done,
                    "total": len(tasks)})
    return out


def due_tasks(day: date | None = None, *, unreminded_only: bool = True) -> list[dict]:
    """Open checklist steps due by `day` (by default only those whose person hasn't been reminded that day)."""
    d = (day or _today()).isoformat()
    return db.query("SELECT t.*, e.name AS employee_name, c.kind FROM employee_tasks t JOIN employee_checklists c "
                    "ON c.id = t.checklist_id JOIN employees e ON e.id = c.employee_id WHERE c.closed_at IS NULL "
                    "AND t.done_at IS NULL AND t.assignee_id IS NOT NULL AND t.due_date <= ?"
                    + (" AND (t.reminded_on IS NULL OR t.reminded_on != ?)" if unreminded_only else "")
                    + " ORDER BY t.due_date", (d, d) if unreminded_only else (d,))


def mark_reminded(task_ids: list[int], day: date | None = None) -> None:
    if task_ids:
        db.execute(f"UPDATE employee_tasks SET reminded_on = ? WHERE id IN ({','.join('?' * len(task_ids))})",
                   ((day or _today()).isoformat(), *task_ids))


# --- privacy -----------------------------------------------------------------------------------------

def collect(user_id: int) -> dict:
    """A person's own employee record, absences and checklist steps (for the data export)."""
    emp = db.query_one("SELECT * FROM employees WHERE user_id = ?", (user_id,))
    out: dict = {"checklist_steps_assigned": db.query(
        "SELECT title, due_date, done_at FROM employee_tasks WHERE assignee_id = ?", (user_id,))}
    if emp:
        out["employee_record"] = emp
        out["absences"] = db.query("SELECT kind, start_date, end_date, half_day, days, note, status, created_at "
                                   "FROM employee_absences WHERE employee_id = ?", (emp["id"],))
    return out
