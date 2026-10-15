# Deployment (GitHub Actions + Tailscale)

`.github/workflows/deploy.yml`: every push to `main` (or a manual run) runs the tests, joins the
tailnet as an ephemeral node tagged `tag:ci`, copies `git archive HEAD` to `~/jarvis` on the server over
Tailscale SSH, runs `docker compose up -d --build` and waits for `/healthz`. Pushes that only touch Markdown
or `docs/` don't deploy; start a run by hand (Actions > Deploy > Run workflow) if one should.

Files that only exist on the server are never touched (the archive is extracted over the folder, nothing
is deleted): `.env`, `data/`, `orchestrator/homelab.yaml`, `company_profile.md`, the company banner.
A file deleted in git therefore stays on the server until removed by hand. Paths with `export-ignore` in
`.gitattributes` (the README's demo media) are never sent.

## One-time setup

1. **Server**: `~/jarvis` holds the `.env` (and `data/`); the deploy user must be allowed to run `docker`.
   Tailscale SSH is on (`sudo tailscale set --ssh`).
2. **Tailscale admin console** (login.tailscale.com/admin):
   - **Access controls**: own both tags and let `tag:ci` in over Tailscale SSH. Tailscale only lets tagged
     devices SSH into tagged devices, so the server gets a tag too, and because a tagged device no longer
     belongs to a person, your own access to it needs its own rule:

     ```json
     "tagOwners": {
       "tag:ci":     ["autogroup:admin"],
       "tag:server": ["autogroup:admin"]
     },
     "ssh": [
       {"action": "accept", "src": ["tag:ci"],           "dst": ["tag:server"], "users": ["<deploy user>"]},
       {"action": "accept", "src": ["autogroup:member"], "dst": ["tag:server"], "users": ["<deploy user>", "autogroup:nonroot"]}
     ]
     ```

     Use `accept`, not `check` (CI can't confirm in a browser). If the policy has `grants`/`acls` instead of
     "allow all", `tag:ci` also needs network access to `tag:server` on port 22.
   - **Machines**: on the server, ... > **Edit ACL tags** > add `tag:server`.
   - **Settings > OAuth clients** (Trust credentials): a client with the **Auth Keys: write** scope and the tag
     `tag:ci`. Without that scope `tailscale up` fails with "403 calling actor does not have enough permissions".
3. **GitHub** (repo > Settings > Environments > `production`, optionally with required reviewers):

   | Secret | Value |
   |---|---|
   | `TS_OAUTH_CLIENT_ID` | OAuth client id |
   | `TS_OAUTH_SECRET` | OAuth client secret |
   | `DEPLOY_HOST` | server's MagicDNS name or tailnet IP |
   | `DEPLOY_USER` | user that owns `~/jarvis` |

The first run is best started by hand (Actions > Deploy > Run workflow) to watch it.
