# Deployment (GitHub Actions + Tailscale)

`.github/workflows/deploy.yml`: every push to `main` (or a manual run) runs the tests, joins the
tailnet as an ephemeral node, copies `git archive HEAD` to `~/jarvis` on the server over Tailscale SSH
and runs `docker compose up -d --build`, then waits for `/healthz`.

Files that only exist on the server are never touched (the archive is extracted over the folder, nothing
is deleted): `.env`, `data/`, `orchestrator/homelab.yaml`, `company_profile.md`, the company banner.
A file deleted in git therefore stays on the server until removed by hand.

## One-time setup

1. **Server**: `~/jarvis` holds the `.env` (and `data/`). Move the existing install if needed:
   `mv ~/alfred ~/jarvis` after `docker compose down`. The user must be allowed to run `docker`.
2. **Tailscale ACL** (admin console > Access controls):
   - `"tagOwners": {"tag:ci": ["autogroup:admin"]}`
   - an ssh rule letting the CI node in as the deploy user:
     `{"action": "accept", "src": ["tag:ci"], "dst": ["tag:server"], "users": ["<deploy user>"]}`
     (tag the server, e.g. `tag:server`, and enable Tailscale SSH there: `tailscale up --ssh`).
3. **OAuth client** (Settings > OAuth clients): scope `auth_keys` (write), tag `tag:ci`.
4. **GitHub** (repo > Settings > Environments > `production`, optionally with required reviewers),
   add these secrets:

   | Secret | Value |
   |---|---|
   | `TS_OAUTH_CLIENT_ID` | OAuth client id |
   | `TS_OAUTH_SECRET` | OAuth client secret |
   | `DEPLOY_HOST` | server's MagicDNS name or tailnet IP |
   | `DEPLOY_USER` | user that owns `~/jarvis` |

The first run is best started by hand (Actions > Deploy > Run workflow) to watch it.
