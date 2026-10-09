# External watchdog for Alfred

Alfred monitors everything else, so something outside Alfred has to watch Alfred. Every 2 minutes
`alfred_watchdog.py` (on a second machine, cron) checks over the tailnet that Alfred answers, that
his health loop is still running, and that Claude, Telegram, Twilio and the Jetson speech server
are up. Problems go straight to Telegram through the Bot API (works while Alfred is down): after 2
runs in a row for Alfred down/hung, 3 for a component; a reminder every 6 hours; a message when it
clears. Standard-library Python only.

## Install (on the watchdog machine)

```bash
mkdir -p ~/alfred-watchdog && chmod 700 ~/alfred-watchdog
# copy infra/watchdog/alfred_watchdog.py to ~/alfred-watchdog/
cat > ~/alfred-watchdog/watchdog.env <<'CONF'
ALFRED_URL=http://100.64.0.1:8000
WATCHDOG_TOKEN=<same as WATCHDOG_TOKEN in Alfred's .env>
TELEGRAM_BOT_TOKEN=<Alfred's bot token>
TELEGRAM_CHAT_IDS=<admin Telegram ids, comma-separated>
CONF
chmod 600 ~/alfred-watchdog/watchdog.env
python3 ~/alfred-watchdog/alfred_watchdog.py --test        # sends a test message
(crontab -l; echo '*/2 * * * * /usr/bin/python3 /home/user/alfred-watchdog/alfred_watchdog.py') | crontab -
```

Alfred side: `WATCHDOG_TOKEN` in `.env` enables `GET /internal/watchdog` (header
`X-Watchdog-Token`; answers 404 without it). It only returns up/down flags and the age of the
last health check, no details. Log: `~/alfred-watchdog/watchdog.log`. Alfred's homelab monitor in
turn watches the watchdog machine itself.
