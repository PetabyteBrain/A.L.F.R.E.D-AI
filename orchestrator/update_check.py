"""Monthly update check across the homelab: report only, nothing is installed or pulled.

On the 1st of each month (after 09:00 local) Alfred asks every host in homelab.yaml for
`alfred-ops updates` (pending OS packages from apt's existing lists, reboot needed, containers whose
image tag has a newer build upstream), stores the result, shows it on Systems ("updates") and sends
admins one short Telegram summary. Admins can also ask Alfred (tool homelab_updates) or press
"Check now" on Systems. Updating stays a human decision.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, homelab
from .config import settings

log = logging.getLogger(__name__)

_KEY = "update_check"
MAX_AGE = timedelta(days=40)
RUN_DAY, RUN_HOUR = 1, 9
_lock = asyncio.Lock()


def last() -> dict:
    return db.get_setting(_KEY) or {}


def _host_line(h: dict) -> str:
    if h.get("error"):
        return f"{h['host']}: couldn't check ({h['error']})"
    bits = []
    pk = h.get("packages")
    if pk:
        bits.append(f"{pk['count']} packages" + (f" ({pk['security']} security)" if pk.get("security") else ""))
    newer = [i["container"] for i in h.get("images") or [] if i["state"] == "newer upstream"]
    if newer:
        bits.append(f"newer images: {', '.join(newer)}")
    if h.get("reboot_required"):
        bits.append("reboot needed")
    return f"{h['host']}: " + ("; ".join(bits) if bits else "up to date")


def _summary(hosts: list[dict]) -> str:
    ok = [h for h in hosts if not h.get("error")]
    pk = sum((h.get("packages") or {}).get("count", 0) for h in ok)
    sec = sum((h.get("packages") or {}).get("security", 0) for h in ok)
    imgs = sum(1 for h in ok for i in h.get("images") or [] if i["state"] == "newer upstream")
    reboot = [h["host"] for h in ok if h.get("reboot_required")]
    failed = [h["host"] for h in hosts if h.get("error")]
    parts = [f"{pk} package updates ({sec} security)", f"{imgs} newer images"]
    if reboot:
        parts.append(f"reboot needed: {', '.join(reboot)}")
    if failed:
        parts.append(f"not checked: {', '.join(failed)}")
    return " · ".join(parts)


async def run(notify: bool = True) -> dict:
    """Check every host now (read-only) and store the report."""
    async with _lock:
        targets = homelab.hosts()
        results = await asyncio.gather(*(homelab._ops(h, "updates", timeout=150) for h in targets))
        hosts = []
        for h, r in zip(targets, results):
            if r.get("error"):
                log.warning("Update check %s: %s", h["name"], str(r["error"])[:300])
                err = "update check not installed there yet" if "not allowed" in r["error"] else homelab._safe_error(r)
                hosts.append({"host": h["name"], "role": h.get("role", ""), "error": err})
                continue
            pk = r.get("packages")
            hosts.append({"host": h["name"], "role": h.get("role", ""), "os": r.get("os"), "kernel": r.get("kernel"),
                          "docker": r.get("docker"), "reboot_required": bool(r.get("reboot_required")),
                          "packages": pk and {"count": pk.get("count", 0), "security": pk.get("security", 0),
                                              "names": (pk.get("names") or [])[:40], "lists_age_d": pk.get("lists_age_d")},
                          "images": r.get("images") or []})
        report = {"at": db.now_iso(), "summary": _summary(hosts), "hosts": hosts}
        db.set_setting(_KEY, report)
        db.set_health("updates", *health_now())
        db.log_activity("system", f"Update check: {report['summary']}"[:300])
        if notify:
            text = "🧰 Monthly update check (nothing was installed)\n" + "\n".join(_host_line(h) for h in hosts)
            await homelab._notify_admins(text[:3500])
        return report


def health_now() -> tuple[bool, str]:
    st = last()
    if not st.get("at"):
        return True, "not run yet (1st of the month, 09:00)"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(st["at"])
    when = f"{age.days} d ago" if age.days else "today"
    if age > MAX_AGE:
        return False, f"overdue: last checked {when}"
    return True, f"checked {when} · {st.get('summary', '')}"


async def monthly() -> None:
    """Called from the health loop; runs once on the 1st of the month after 09:00 local time."""
    if not homelab.enabled():
        return
    now = datetime.now(ZoneInfo(settings.timezone))
    if now.day != RUN_DAY or now.hour < RUN_HOUR:
        return
    st = last()
    if st.get("at") and datetime.fromisoformat(st["at"]).astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m") == now.strftime("%Y-%m"):
        return
    await run()
