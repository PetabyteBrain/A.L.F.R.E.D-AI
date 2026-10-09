# Homelab operator access (alfred-ops)

Alfred (on the Alfred server) checks the homelab with the key `~/.ssh/alfred_ops`. On every host that
key is pinned to one script, so it can never get a shell:

```
command="/home/user/bin/alfred-ops",restrict ssh-ed25519 AAAA… alfred-ops@alfred-server
```

`alfred-ops` offers `status`, `logs <container> [lines]`, `restart <container>`, `mc`
(Minecraft players) and `updates` (monthly report: pending packages, reboot needed, newer container
images upstream; reads only, installs nothing). Logs and restarts only work for containers listed in
`~/.config/alfred-ops/allow` (one name per line) on that host; that list is also what the
monitor watches. Hosts are listed in `orchestrator/homelab.yaml`.

## Update the script on every host

After changing `alfred-ops`, copy it to each host in `orchestrator/homelab.yaml` (same `scp` +
`install` line as below). A host with the old script answers "operation not allowed" and Systems
says so.

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

## Allowlists

Each host's `~/.config/alfred-ops/allow` lists the containers Alfred may read logs of and restart.
Keep databases and Alfred's own container off the list. Example:

| Host | Restart/logs allowed for |
|---|---|
| proxy | caddy, n8n (not the databases) |
| gpu | alfred-speech |
| alfred-server | nothing (Alfred doesn't restart himself) |
