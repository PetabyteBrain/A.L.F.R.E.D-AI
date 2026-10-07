"""What Anthropic actually billed (optional): the Cost API of the Admin API, next to Alfred's own estimate.

Alfred meters every request itself (usage.py); that is an estimate from token counts and list prices,
and it can't see anything else billed to the same organisation (other apps or keys, the Console
workbench, Claude Code on an API key). With an Admin API key in .env the Usage tab also shows the real
cost for the period, per account:

    ANTHROPIC_ADMIN_KEY     admin key of the organisation behind ANTHROPIC_API_KEY
    ANTHROPIC_ADMIN_KEY_2   admin key of the organisation behind ANTHROPIC_API_KEY_2 (if another one)

Only `GET /v1/organizations/cost_report` is called (read-only). Admin keys can manage the whole
organisation, so they never leave the server; results are cached for 10 minutes (the data itself
lags a few minutes). Not available for individual Console accounts (no Admin API there).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import ai_config
from .config import _env

log = logging.getLogger(__name__)

URL = "https://api.anthropic.com/v1/organizations/cost_report"
CACHE_S = 600
_cache: dict[tuple, tuple[float, dict]] = {}


def _admin_keys() -> dict[str, str]:
    keys = {"primary": _env("ANTHROPIC_ADMIN_KEY"), "secondary": _env("ANTHROPIC_ADMIN_KEY_2")}
    return {k: v for k, v in keys.items() if v}


def configured() -> bool:
    return bool(_admin_keys())


async def _cost(admin_key: str, start: datetime) -> dict:
    """Total USD and per-model USD from `start` (UTC day) until now, following pagination."""
    params = {"starting_at": start.strftime("%Y-%m-%dT00:00:00Z"), "bucket_width": "1d",
              "group_by[]": "description", "limit": 31}
    headers = {"x-api-key": admin_key, "anthropic-version": "2023-06-01", "User-Agent": "Alfred/1.0"}
    total, by_model = 0.0, {}
    async with httpx.AsyncClient(timeout=20) as c:
        for _ in range(20):
            r = await c.get(URL, params=params, headers=headers)
            if r.status_code in (401, 403):
                raise RuntimeError("the admin key was refused")
            r.raise_for_status()
            body = r.json()
            for bucket in body.get("data", []):
                for res in bucket.get("results", []):
                    usd = float(res.get("amount") or 0) / 100          # decimal string in cents
                    total += usd
                    name = res.get("model") or res.get("cost_type") or res.get("description") or "other"
                    by_model[name] = by_model.get(name, 0.0) + usd
            if not body.get("has_more") or not body.get("next_page"):
                break
            params["page"] = body["next_page"]
    return {"usd": round(total, 4), "by_model": {k: round(v, 4) for k, v in sorted(by_model.items(), key=lambda x: -x[1])}}


async def billed(days: int) -> dict:
    """{"configured", "since", "accounts": [{"key", "label", "usd", "by_model"} | {"key", "label", "error"}]}"""
    keys = _admin_keys()
    if not keys:
        return {"configured": False}
    end = datetime.now(timezone.utc).date() + timedelta(days=1)        # today included (the report is by day)
    start = end - timedelta(days=days)
    cache_key = (days, end.isoformat(), tuple(sorted(keys)))
    hit = _cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    labels = {k["id"]: k["label"] for k in ai_config.describe()["keys"]}
    accounts = []
    for name, key in keys.items():
        try:
            accounts.append({"key": name, "label": labels.get(name, name),
                             **await _cost(key, datetime.combine(start, datetime.min.time()))})
        except Exception as e:  # noqa: BLE001 — shown as "couldn't read", never breaks the Usage tab
            log.warning("Anthropic cost report (%s) failed: %s", name, e)
            accounts.append({"key": name, "label": labels.get(name, name),
                             "error": str(e)[:120] if isinstance(e, RuntimeError) else "couldn't read the cost report"})
    out = {"configured": True, "since": start.isoformat(), "accounts": accounts}
    _cache[cache_key] = (time.monotonic(), out)
    return out


async def month_to_date() -> float | None:
    """Billed Claude cost this calendar month (UTC days), for budgets; None without admin keys or on error."""
    if not configured():
        return None
    today = datetime.now(timezone.utc).date()
    data = await billed(today.day)
    vals = [a["usd"] for a in data.get("accounts", []) if "usd" in a]
    return round(sum(vals), 4) if vals else None
