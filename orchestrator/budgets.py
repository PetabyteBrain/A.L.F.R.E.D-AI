"""Monthly cost budgets with Telegram warnings to admins.

Budgets (USD per calendar month, local time) are set by admins on the Usage tab; empty = none.
Spend this month:
- anthropic: estimated from metered tokens + web searches (usage.py prices)
- elevenlabs: estimated from metered characters/seconds (only if prices are configured)
- twilio: the real month-to-date bill from Twilio's Usage API (calls + phone number)
- total: the sum of the above
At 80 % and 100 % of a budget every active admin gets one Telegram message per month and level.
Warnings only: nothing is switched off when a budget is exceeded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db, usage
from .config import settings

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "twilio", "elevenlabs", "total")
LABELS = {"anthropic": "Claude (incl. web search)", "twilio": "Twilio (phone)", "elevenlabs": "ElevenLabs",
          "total": "All providers"}
LEVELS = (80, 100)
_BUDGETS_KEY = "budgets"
_ALERTS_KEY = "budget_alerts"
_CHECK_KEY = "budget_last_check"
CHECK_MINUTES = 60


def _month_start() -> datetime:
    local = datetime.now(ZoneInfo(settings.timezone))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def month_key() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")


def get() -> dict:
    stored = db.get_setting(_BUDGETS_KEY, {}) or {}
    return {p: stored.get(p) for p in PROVIDERS}


def set_budgets(values: dict) -> dict:
    clean = {}
    for p in PROVIDERS:
        v = values.get(p)
        if v is not None:
            v = round(float(v), 2)
            if not 0 < v <= 100_000:
                raise ValueError("A budget must be between 0.01 and 100000 USD, or empty.")
        clean[p] = v
    db.set_setting(_BUDGETS_KEY, clean)
    # New limits: allow fresh warnings this month for levels not reached under the new numbers.
    db.set_setting(_ALERTS_KEY, {})
    return clean


async def spend() -> dict:
    since = _month_start().isoformat(timespec="seconds")
    rows = db.query("SELECT provider, SUM(cost_usd) AS cost FROM api_usage WHERE ts >= ? "
                    "AND provider IN ('anthropic', 'elevenlabs') GROUP BY provider", (since,))
    out = {"anthropic": 0.0, "elevenlabs": 0.0, "twilio": None}
    for r in rows:
        out[r["provider"]] = round(r["cost"] or 0, 4)
    tw = await usage.twilio_month_to_date()
    if tw and tw.get("total") is not None:
        out["twilio"] = round(tw["total"], 4)
    out["total"] = round(sum(v for v in out.values() if v), 4)
    return out


async def status() -> dict:
    budgets, spent = get(), await spend()
    rows = []
    for p in PROVIDERS:
        limit, s = budgets[p], spent.get(p)
        pct = round(s / limit * 100) if limit and s is not None else None
        rows.append({"provider": p, "label": LABELS[p], "budget": limit, "spent": s, "percent": pct})
    return {"month": month_key(), "rows": rows}


async def check(force: bool = False) -> list[str]:
    """Send any due warnings; returns the texts sent. Runs at most hourly unless forced."""
    now = datetime.now(timezone.utc)
    last = db.get_setting(_CHECK_KEY)
    if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < CHECK_MINUTES * 60:
        return []
    db.set_setting(_CHECK_KEY, now.isoformat())
    if not any(get().values()):
        return []
    st = await status()
    alerts = db.get_setting(_ALERTS_KEY, {}) or {}
    sent_this_month = alerts.get(st["month"], {})
    texts = []
    for row in st["rows"]:
        if row["percent"] is None:
            continue
        due = [lvl for lvl in LEVELS if row["percent"] >= lvl and lvl not in sent_this_month.get(row["provider"], [])]
        if not due:
            continue
        lvl = max(due)
        sent_this_month[row["provider"]] = sorted(set(sent_this_month.get(row["provider"], []) + due))
        verb = "is over" if lvl >= 100 else "has reached 80 % of"
        texts.append(f"💸 *Budget warning*: {row['label']} {verb} this month's budget: "
                     f"${row['spent']:.2f} of ${row['budget']:.2f} ({row['percent']} %). "
                     "Nothing is switched off; see the Usage tab.")
    alerts = {st["month"]: sent_this_month}           # older months are forgotten
    db.set_setting(_ALERTS_KEY, alerts)
    for text in texts:
        await _tell_admins(text)
        db.log_activity("system", text.replace("*", ""))
    return texts


async def _tell_admins(text: str) -> None:
    from . import telegram_handler
    for admin in db.query("SELECT id FROM users WHERE role = 'admin' AND active = 1"):
        try:
            await telegram_handler.send_to_user(admin["id"], text, note="budget warning")
        except Exception:
            log.warning("Budget warning to admin %s failed", admin["id"], exc_info=True)
