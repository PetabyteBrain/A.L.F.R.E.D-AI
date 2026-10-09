#!/usr/bin/env python3
"""External watchdog for Alfred. Runs on another machine from cron every 2 minutes and
messages the admins straight through the Telegram Bot API, so it works when Alfred is down.

Watches (over the tailnet):
- Alfred answering at all (/healthz);
- Alfred's own health loop still running (/internal/watchdog: last check < 15 min old);
- the components Alfred needs: Claude, Telegram, Twilio (phone), Jetson speech.
A problem is reported after it was seen on 2 runs in a row (Alfred down / hung) or 3 runs
(a component), reminded every 6 hours while it lasts, and "back to normal" is sent when it clears.

Config: ~/alfred-watchdog/watchdog.env (mode 600):
  ALFRED_URL=http://100.64.0.1:8000
  WATCHDOG_TOKEN=...            (same as WATCHDOG_TOKEN in Alfred's .env)
  TELEGRAM_BOT_TOKEN=...        (Alfred's bot)
  TELEGRAM_CHAT_IDS=123,456     (who gets the messages)
Standard library only. State in ~/alfred-watchdog/state.json, log in ~/alfred-watchdog/watchdog.log.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path(__file__).resolve().parent
CONF = HOME / "watchdog.env"
STATE = HOME / "state.json"
LOG = HOME / "watchdog.log"

STALE_S = 15 * 60
REMIND_S = 6 * 3600
RUNS_DOWN = 2            # Alfred unreachable / hung
RUNS_COMPONENT = 3       # a component reported down
LABELS = {"alfred": "Alfred isn't answering", "stale": "Alfred is running but his checks have stopped (hung?)",
          "claude": "the language model (Claude) is failing", "telegram": "his Telegram connection is down",
          "twilio": "the phone line (Twilio) is down", "jetson_speech": "the Jetson speech server is down "
                                                                       "(calls fall back to ElevenLabs)"}
SHORT = {"alfred": "Alfred answers again", "stale": "Alfred's checks are running again", "claude": "Claude",
         "telegram": "Telegram", "twilio": "phone line", "jetson_speech": "Jetson speech"}


def log(msg: str) -> None:
    with LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    if LOG.stat().st_size > 1_000_000:           # keep the log small
        LOG.write_text("\n".join(LOG.read_text().splitlines()[-2000:]) + "\n")


def config() -> dict:
    conf = {}
    for line in CONF.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip("'\"")
    return conf


def get_json(url: str, headers: dict | None = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def problems(conf: dict) -> set[str]:
    base = conf["ALFRED_URL"].rstrip("/")
    try:
        if not get_json(f"{base}/healthz").get("ok"):
            return {"alfred"}
    except (urllib.error.URLError, OSError, ValueError):
        return {"alfred"}
    try:
        d = get_json(f"{base}/internal/watchdog", {"X-Watchdog-Token": conf["WATCHDOG_TOKEN"]})
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"watchdog endpoint failed: {e}")
        return set()                              # Alfred answers; details unavailable (e.g. token mismatch)
    found = {name for name, ok in (d.get("components") or {}).items() if not ok}
    age = d.get("health_age_s")
    if age is None or age > STALE_S:
        found.add("stale")
    return found


def send(conf: dict, text: str) -> None:
    for chat in [c.strip() for c in conf.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{conf['TELEGRAM_BOT_TOKEN']}/sendMessage",
                                   data=data, timeout=15).read()
        except (urllib.error.URLError, OSError) as e:
            log(f"telegram send to {chat} failed: {e}")


def run() -> int:
    conf = config()
    state = json.loads(STATE.read_text()) if STATE.exists() else {"seen": {}, "alerted": {}}
    now = time.time()
    current = problems(conf)
    seen, alerted = state["seen"], state["alerted"]
    for p in list(seen):
        if p not in current:
            seen.pop(p)
    for p in current:
        seen[p] = seen.get(p, 0) + 1

    new, remind = [], []
    for p in current:
        need = RUNS_DOWN if p in ("alfred", "stale") else RUNS_COMPONENT
        if seen[p] >= need:
            if p not in alerted:
                new.append(p)
                alerted[p] = now
            elif now - alerted[p] >= REMIND_S:
                remind.append(p)
                alerted[p] = now
    cleared = [p for p in list(alerted) if p not in current]
    for p in cleared:
        alerted.pop(p)

    if new:
        send(conf, "🚨 Watchdog: " + "; ".join(LABELS.get(p, p) for p in new) + ".")
    if remind:
        send(conf, "🚨 Still a problem: " + "; ".join(LABELS.get(p, p) for p in remind) + ".")
    if cleared:
        send(conf, "✅ Back to normal: " + ", ".join(SHORT.get(p, p) for p in cleared) + ".")
    if new or remind or cleared or current:
        log(f"problems={sorted(current)} new={new} remind={remind} cleared={cleared}")
    STATE.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:                 # send a test message, show what it sees
        c = config()
        print("problems now:", sorted(problems(c)) or "none")
        send(c, "✅ Alfred's watchdog is set up. You'll hear from it only if something breaks.")
        sys.exit(0)
    sys.exit(run())
