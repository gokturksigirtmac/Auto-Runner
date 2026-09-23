# AGENTS.md — Instructions for AI Assistants

This file tells AI assistants (Claude, Codex, Copilot, Gemini, etc.) exactly how to install,
configure, and verify **Auto-Runner** on a user's server without the user needing to explain
anything beyond the basics.

When a user says something like:
- *"Install this on my server"*
- *"Set up Auto-Runner for me"*
- *"Bunu sunucuma kur"*
- *"Configure this autoscaler"*

…follow this document top to bottom.

---

## What this project does

Auto-Runner is a lightweight Python webhook server that:
1. Receives `workflow_job` events from GitHub via webhook
2. **Starts** the matching self-hosted runner service when a job is `queued`
3. **Stops** the runner after a grace period once the job `completes`

Result: runners are idle (off) between jobs, saving RAM/CPU on the server.

---

## Step 1 — Gather required information

Ask the user for the following. Do not proceed until you have all required items.
Ask multiple questions in one message to avoid back-and-forth.

### Required

| # | Question to ask the user | Variable name | Example |
|---|---|---|---|
| 1 | What is your server's IP address or hostname? | `SERVER_HOST` | `70.40.138.217` |
| 2 | What SSH username should I connect with? | `SSH_USER` | `root` |
| 3 | What is the path to your SSH private key on this machine? (or say "password" to use a password) | `SSH_KEY` | `~/.ssh/id_rsa` |
| 4 | Which GitHub repos do you want to watch? (list them as `owner/repo`) | `REPOS` | `myorg/frontend`, `myorg/backend` |

### Optional (use defaults if not provided)

| # | Question | Variable | Default |
|---|---|---|---|
| 5 | What port should the autoscaler listen on? | `PORT` | `9000` |
| 6 | How many seconds to wait after a job finishes before stopping the runner? | `GRACE_SECONDS` | `30` |
| 7 | Do you have an existing webhook secret, or should I generate one? | `WEBHOOK_SECRET` | auto-generate |

---

## Step 2 — Discover existing runners on the server

SSH into the server and run:

```bash
systemctl list-units 'actions.runner.*' --no-pager --plain
```

Show the output to the user and ask:
> "These are the runner services I found. Which service should handle which repo?"

Map each repo from Step 1 to a service name. If no runners are found, tell the user to
[register runners](https://docs.github.com/en/actions/hosting-your-own-runners/adding-self-hosted-runners) first, then re-run.

---

## Step 3 — Install

SSH into the server and run the following commands **in order**:

```bash
# 1. Create directories
mkdir -p /opt/Auto-Runner /etc/Auto-Runner

# 2. Download autoscaler
curl -fsSL https://raw.githubusercontent.com/gokturksigirtmac/Auto-Runner/main/autoscaler.py \
  -o /opt/Auto-Runner/autoscaler.py
chmod +x /opt/Auto-Runner/autoscaler.py

# 3. Download systemd unit
curl -fsSL https://raw.githubusercontent.com/gokturksigirtmac/Auto-Runner/main/runner-autoscaler.service \
  -o /etc/systemd/system/runner-autoscaler.service
```

---

## Step 4 — Write config

Generate `WEBHOOK_SECRET` if the user did not provide one:
```bash
openssl rand -hex 32
```

Write `/etc/Auto-Runner/config.json` with the information gathered in Steps 1–2.

Template (fill in the values):
```json
{
  "port": PORT,
  "webhook_secret": "WEBHOOK_SECRET",
  "grace_seconds": GRACE_SECONDS,
  "runners": [
    {
      "repo": "REPO_1",
      "service": "SERVICE_NAME_1",
      "grace_seconds": GRACE_SECONDS
    }
  ]
}
```

For multiple repos → same runner, use the `repos` array:
```json
{
  "repos": ["REPO_1", "REPO_2"],
  "service": "SERVICE_NAME"
}
```

---

## Step 5 — Start the service

```bash
systemctl daemon-reload
systemctl enable runner-autoscaler
systemctl start runner-autoscaler
sleep 2
systemctl status runner-autoscaler --no-pager -l
```

If status shows `Active: active (running)` → proceed.
If it shows `failed` → run `journalctl -u runner-autoscaler -n 30 --no-pager` and show the user.

---

## Step 6 — Open firewall port

```bash
# Check which firewall is active
if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow PORT/tcp
elif command -v firewall-cmd &>/dev/null; then
  firewall-cmd --permanent --add-port=PORT/tcp && firewall-cmd --reload
fi
```

Verify the port is reachable:
```bash
ss -tlnp | grep PORT
```

---

## Step 7 — Register GitHub webhooks

For each repo in the user's list, register a webhook using the GitHub CLI or API.

**Using `gh` CLI** (preferred — ask the user to confirm they are logged in):
```bash
gh api repos/OWNER/REPO/hooks \
  --method POST \
  -f "name=web" \
  -f "config[url]=http://SERVER_HOST:PORT" \
  -f "config[content_type]=json" \
  -f "config[secret]=WEBHOOK_SECRET" \
  -F "active=true" \
  -f "events[]=workflow_job"
```

**Manually** (if `gh` is not available): tell the user to:
1. Go to `https://github.com/OWNER/REPO/settings/hooks/new`
2. Payload URL: `http://SERVER_HOST:PORT`
3. Content type: `application/json`
4. Secret: `WEBHOOK_SECRET`
5. Events: choose **"Workflow jobs"** only

---

## Step 8 — Verify

```bash
curl -s http://SERVER_HOST:PORT/health
```

Expected response: `OK` with HTTP 200.

Also check that GitHub successfully delivered the ping event:
- GitHub → repo Settings → Webhooks → click the webhook → "Recent Deliveries"
- The ping delivery should show HTTP 200

---

## Step 9 — Stop runners (hand-off to autoscaler)

The autoscaler only takes effect when runners are stopped between jobs.
Stop them now so the autoscaler controls the lifecycle going forward:

```bash
# Stop each runner service (they will be auto-started on the next push)
systemctl stop SERVICE_NAME_1
systemctl stop SERVICE_NAME_2   # if applicable
```

Confirm to the user:
> "Done! Your runners are now managed automatically. They will start when a job is
> queued and stop ~GRACE_SECONDS seconds after the last job completes.
> Check logs anytime with: `journalctl -fu runner-autoscaler`"

---

## Troubleshooting reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Webhook delivers 403 | Wrong `webhook_secret` | Check secret in config matches GitHub webhook |
| Webhook delivers connection error | Port not open or wrong IP | Re-run Step 6, confirm `SERVER_HOST` is public IP |
| Runner doesn't start on push | Service name mismatch | Re-run `systemctl list-units 'actions.runner.*'` and update config |
| Runner starts but job stays queued | Runner not registered or offline | `systemctl status SERVICE_NAME` |
| `journalctl` shows `Permission denied` on systemctl | Autoscaler not running as root | Check `User=root` in service file |

---

## Config reference (quick)

```
/etc/Auto-Runner/config.json       ← main config
/opt/Auto-Runner/autoscaler.py     ← server script
/etc/systemd/system/runner-autoscaler.service  ← systemd unit
```

```bash
journalctl -fu runner-autoscaler   # live logs
systemctl restart runner-autoscaler  # apply config changes
curl http://localhost:PORT/health   # health check
```

---

## Notes for the AI

- Always confirm each step succeeded before moving to the next.
- If you can SSH into the server directly, prefer running commands yourself over giving them to the user.
- If the user says "just do it", proceed without asking for optional values — use defaults.
- The config file must be valid JSON. Validate before writing: `python3 -m json.tool config.json`.
- If `curl` is not available on the server, fall back to `wget -qO- URL`.
- `WEBHOOK_SECRET` is sensitive — do not log it or include it in plain-text summaries.
