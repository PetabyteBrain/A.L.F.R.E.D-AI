# Homelab operator access (alfred-ops)

Alfred (on alfred-server) checks the homelab with the key `~/.ssh/alfred_ops`. On every host that
key is pinned to one script, so it can never get a shell:

```
command="/home/user/bin/alfred-ops",restrict ssh-ed25519 AAAA… alfred-ops@alfred-server
```

`alfred-ops` offers `status`, `logs <container> [lines]`, `restart <container>` and `mc`
(Minecraft players). Logs and restarts only work for containers listed in
`~/.config/alfred-ops/allow` (one name per line) on that host; that list is also what the
monitor watches. Hosts are listed in `orchestrator/homelab.yaml`.

## Add a host

From a machine that can already SSH to it (as user):

```bash
scp infra/ops/alfred-ops user@HOST:/tmp/ && ssh user@HOST 'mkdir -p ~/bin ~/.config/alfred-ops && install -m 755 /tmp/alfred-ops ~/bin/alfred-ops'
ssh user@HOST 'printf "%s\n" container-a container-b > ~/.config/alfred-ops/allow'
ssh user@HOST "echo 'command=\"/home/user/bin/alfred-ops\",restrict $(ssh user@alfred-server cat ~/.ssh/alfred_ops.pub)' >> ~/.ssh/authorized_keys"
ssh user@alfred-server 'ssh-keyscan -t ed25519 HOST_IP >> ~/.ssh/alfred_ops_known_hosts'
```

Then add it to `orchestrator/homelab.yaml` and deploy. Until the key is installed, Systems shows the
host as "access not set up" (no alert).

## Current allowlists (2026-10-01)

| Host | Restart/logs allowed for |
|---|---|
