"""Homelab operator: status of the hosts in homelab.yaml, logs/restarts of allowlisted containers,
Minecraft players, and a monitor that alerts admins when something goes wrong.

Alfred reaches each host with a dedicated SSH key that the host pins to ~/bin/alfred-ops
(infra/ops/alfred-ops): it can only run that script's operations, never a shell. Restarts are
additionally limited to each host's allowlist, to admins, and to after the admin has confirmed
in the conversation (enforced here, not just asked for in the prompt).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import yaml

from . import db
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)

HOSTS_FILE = Path(__file__).with_name("homelab.yaml")
POLL_SECONDS = 300
DISK_ALERT_PCT = 90
TEMP_ALERT_C = 80
FAILS_BEFORE_DOWN = 2          # consecutive unreachable polls before alerting
CONFIRM_TTL_S = 600

_state: dict = {"fails": {}, "alerts": set(), "running": {}}
_pending_restarts: dict[tuple[int, str, str], dict] = {}


class HomelabError(ValueError):
    """Message is safe to show to the user / the model."""


def hosts() -> list[dict]:
    return (yaml.safe_load(HOSTS_FILE.read_text(encoding="utf-8")) or {}).get("hosts", [])


def _host(name: str) -> dict:
    name = (name or "").strip().lower()
    for h in hosts():
        if h["name"] == name or h["ssh"].split("@")[-1].lower() == name:
            return h
    raise HomelabError(f"Unknown host '{name}'. Known: {', '.join(h['name'] for h in hosts())}.")


def enabled() -> bool:
    return Path(settings.ops_key_path).is_file()


async def _ops(host: dict, *args: str, timeout: float = 25) -> dict:
    cmd = ["ssh", "-i", settings.ops_key_path, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
           "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={settings.ops_known_hosts}",
           host["ssh"], *args]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
    except OSError as e:                      # no ssh client in the image
        return {"error": f"cannot run ssh: {e}", "unreachable": True}
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": "timed out", "unreachable": True}
    text = out.decode(errors="replace").strip()
    try:
        return json.loads(text.splitlines()[-1]) if text else {"error": err.decode(errors="replace").strip()[:200],
                                                                 "unreachable": proc.returncode == 255}
    except (ValueError, IndexError):
        return {"error": (text or err.decode(errors="replace"))[:200], "unreachable": proc.returncode == 255}


def _safe_error(st: dict) -> str:
    """Error category only: raw ssh/stderr text (user@ip, key details) never leaves the server log."""
    err = st.get("error") or ""
    if "Permission denied" in err:
        return "access not set up"
    if "timed out" in err.lower():
        return "timed out"
    if st.get("unreachable"):
        return "unreachable"
    if "allowlist" in err or "not allowed" in err or "bad container" in err:
        return err[:120]                       # our own alfred-ops messages are safe to show
    return "check failed"


def summarize(name: str, st: dict) -> str:
    if st.get("error"):
        log.warning("Homelab %s: %s", name, st["error"][:300])
        return f"{name}: {_safe_error(st)}"
    cs = st.get("containers") or []
    running = [c for c in cs if c.get("state") == "running"]
    bad = [c["name"] for c in cs if c.get("health") == "unhealthy"]
    disks = " ".join(f"{d['path']} {d['used_pct']:.0f}%" for d in st.get("disks", []))
    return (f"{name}: load {st['load'][0]:.2f}/{st.get('cpus')} cpu · mem {st['mem_used_pct']:.0f}% · disk {disks}"
            + (f" · {st['temp_c']:.0f}°C" if st.get("temp_c") is not None else "")
            + f" · {len(running)}/{len(cs)} containers up" + (f" · unhealthy: {', '.join(bad)}" if bad else "")
            + f" · up {st['uptime_h'] / 24:.1f} d")


async def status(name: str | None = None) -> list[dict]:
    targets = [_host(name)] if name else hosts()
    results = await asyncio.gather(*(_ops(h, "status") for h in targets))
    # The configured short name wins over the machine's own hostname (kept as "hostname").
    return [{**r, "hostname": r.get("host"), "host": h["name"], "role": h.get("role", "")}
            for h, r in zip(targets, results)]


async def logs(person: Person, host: str, container: str, lines: int = 50) -> dict:
    if not person.is_admin:
        raise HomelabError("Only an admin can read service logs.")
    return await _ops(_host(host), "logs", container, str(max(1, min(int(lines or 50), 200))))


def _last_user_turn(session_id: str | None) -> int:
    if not session_id:
        return 0
    row = db.query_one("SELECT MAX(id) AS id FROM transcripts WHERE session_id = ? AND speaker = 'user'", (session_id,))
    return (row or {}).get("id") or 0


async def restart(person: Person, session_id: str | None, host: str, container: str, confirmed: bool) -> dict:
    """Two-step: the first call only asks; the restart runs on a later call with confirmed=true,
    after the admin has replied in the conversation (a new user turn since the question)."""
    if not person.is_admin:
        raise HomelabError("Only an admin can restart services.")
    h = _host(host)
    key = (person.id, h["name"], container)
    pending = _pending_restarts.get(key)
    if pending and time.monotonic() - pending["at"] > CONFIRM_TTL_S:
        _pending_restarts.pop(key, None)
        pending = None
    if not confirmed or not pending:
        _pending_restarts[key] = {"at": time.monotonic(), "user_turn": _last_user_turn(session_id)}
        return {"needs_confirmation": True,
                "ask": f"Restart '{container}' on {h['name']} ({h.get('role', '')})? It will be briefly unavailable."}
    if _last_user_turn(session_id) <= pending["user_turn"]:
        return {"needs_confirmation": True, "note": "Wait for the person to confirm in their own reply first."}
    _pending_restarts.pop(key, None)
    result = await _ops(h, "restart", container, timeout=150)
    db.log_activity("system", f"{person.display_name} restarted {container} on {h['name']}: "
                    f"{'ok' if result.get('ok') else result.get('error') or result.get('detail')}", user_id=person.id)
    return result


async def minecraft() -> dict:
    h = next((x for x in hosts() if x.get("minecraft")), None)     # explicit flag in homelab.yaml
    if not h:
        raise HomelabError("No Minecraft host configured.")
    return {"host": h["name"], **await _ops(h, "mc")}


# --- monitor -----------------------------------------------------------------------------------

def _conditions(name: str, st: dict) -> dict[str, str]:
    """Problems on a host right now, keyed for de-duplication."""
    found = {}
    for d in st.get("disks", []):
        if d["used_pct"] >= DISK_ALERT_PCT:
            found[f"{name}:disk:{d['path']}"] = f"{name}: disk {d['path']} is {d['used_pct']:.0f}% full"
    if st.get("temp_c") is not None and st["temp_c"] >= TEMP_ALERT_C:
        found[f"{name}:temp"] = f"{name}: running hot at {st['temp_c']:.0f}°C"
    if (st.get("power") or {}).get("under_voltage_now"):
        found[f"{name}:power"] = f"{name}: under-voltage (power supply too weak)"
    watched = set(st.get("manageable") or [])
    for c in st.get("containers") or []:
        if c["name"] in watched and (c.get("state") != "running" or c.get("health") == "unhealthy"):
            found[f"{name}:ctr:{c['name']}"] = f"{name}: {c['name']} is {c.get('health') or c.get('state')} ({c['status']})"
    return found


async def check_all() -> list[str]:
    """Poll every host, update Systems, and return alert texts (new problems and recoveries)."""
    if not enabled():
        return []
    messages, active = [], set()
    for r in await status():
        name = r["host"]
        if r.get("error") and "Permission denied" in r["error"]:
            # Not down, just not set up yet (alfred-ops key not installed there): show, don't alert.
            log.warning("Homelab %s: %s", name, r["error"][:300])
            db.set_health(f"host {name}", False, "access not set up (install alfred-ops, infra/ops/README.md)")
            continue
        if r.get("error"):
            fails = _state["fails"][name] = _state["fails"].get(name, 0) + 1
            db.set_health(f"host {name}", False, summarize(name, r))
            if fails >= FAILS_BEFORE_DOWN:
                active.add(f"{name}:down")
                if f"{name}:down" not in _state["alerts"]:
                    messages.append(f"🔴 {name} ({r['role']}) is not reachable.")
            else:
                active |= {k for k in _state["alerts"] if k.startswith(f"{name}:")}  # one miss: keep state
            continue
        _state["fails"][name] = 0
        problems = _conditions(name, r)
        db.set_health(f"host {name}", not problems, summarize(name, r))
        for k, text in problems.items():
            active.add(k)
            if k not in _state["alerts"]:
                messages.append(f"🟠 {text}")
    for k in sorted(_state["alerts"] - active):
        messages.append(f"🟢 Recovered: {k.replace(':ctr:', ': ').replace(':', ' ')}")
    _state["alerts"] = active
    return messages


async def _notify_admins(text: str) -> None:
    from .identity import registry
    if not settings.telegram_enabled:
        return
    from .telegram_handler import send_to_user
    for admin in db.query("SELECT id FROM users WHERE role = 'admin' AND active = 1"):
        if registry.telegram_for(admin["id"]):
            try:
                await send_to_user(admin["id"], text, note="homelab alert")   # silent in quiet hours
            except Exception:  # noqa: BLE001
                log.exception("Homelab alert to admin failed")


_poll_lock = asyncio.Lock()


async def poll_and_alert() -> None:
    """One monitor pass; used by the loop and by "Run diagnostics", so alerts are never swallowed."""
    async with _poll_lock:
        msgs = await check_all()
        if msgs:
            db.log_activity("system", "Homelab: " + " | ".join(msgs)[:300])
            await _notify_admins("Homelab\n" + "\n".join(msgs))


def host_rows_visible(rows: list[dict], is_admin: bool) -> list[dict]:
    """Systems rows for the dashboard: grouped, host rows only for admins and only configured hosts."""
    configured = hosts() if HOSTS_FILE.exists() else []
    roles = {f"host {h['name']}": h.get("role", "") for h in configured}
    rank = {f"host {h['name']}": i for i, h in enumerate(configured)}
    out = []
    for r in rows:
        if r["service"].startswith("host "):
            if not is_admin or r["service"] not in roles:
                continue
            out.append({**r, "group": "homelab", "label": r["service"][5:], "role": roles[r["service"]],
                        "order": rank[r["service"]]})
        else:
            out.append({**r, "group": "alfred", "label": r["service"], "role": ""})
    return sorted(out, key=lambda r: (r["group"] != "alfred", r.get("order", 0)))


async def background_loop() -> None:
    await asyncio.sleep(60)          # let everything start first
    while True:
        try:
            await poll_and_alert()
        except Exception:
            log.exception("Homelab monitor iteration failed")
        await asyncio.sleep(POLL_SECONDS)
