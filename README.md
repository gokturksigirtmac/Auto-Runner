# GitHub Runner Autoscaler

A lightweight webhook server that **starts self-hosted GitHub Actions runners on demand** and **stops them when idle** — saving RAM and CPU on your server.

- **Zero dependencies** — pure Python 3 stdlib, no pip installs
- **Config-driven** — one JSON file maps repos to runner services
- **Flexible backends** — systemd services, Docker containers, or any custom script
- **Multi-repo** — one instance handles unlimited repos and runners
- **Safe** — HMAC-SHA256 webhook signature verification, graceful shutdown

---

## How it works

```
Push to repo
    └─▶ GitHub sends workflow_job.queued webhook
            └─▶ Autoscaler starts the runner service
                    └─▶ Runner picks up the job
                            └─▶ Job completes
                                    └─▶ GitHub sends workflow_job.completed
                                                └─▶ Autoscaler waits grace period
                                                            └─▶ Runner stops (if idle)
```

---

## Requirements

- Linux server with Python 3.8+
- Self-hosted GitHub Actions runner(s) already registered
- Server reachable from the internet on a port (default: 9000)

---

## Quick install

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/gokturksigirtmac/github-runner-autoscaler/main/install.sh)"
```

Then edit the config and start the service:

```bash
nano /etc/github-runner-autoscaler/config.json
systemctl start runner-autoscaler
```

---

## Manual install

```bash
# 1. Copy files
sudo mkdir -p /opt/github-runner-autoscaler /etc/github-runner-autoscaler
sudo cp autoscaler.py /opt/github-runner-autoscaler/
sudo cp config.example.json /etc/github-runner-autoscaler/config.json
sudo cp runner-autoscaler.service /etc/systemd/system/

# 2. Edit config
sudo nano /etc/github-runner-autoscaler/config.json

# 3. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now runner-autoscaler

# 4. Check logs
journalctl -fu runner-autoscaler
```

---

## Configuration

`/etc/github-runner-autoscaler/config.json`:

```json
{
  "port": 9000,
  "webhook_secret": "your-secret",
  "grace_seconds": 30,

  "runners": [
    {
      "repo": "your-org/your-repo",
      "service": "actions.runner.your-org-your-repo.runner-name"
    }
  ]
}
```

### Full config reference

| Field | Type | Default | Description |
|---|---|---|---|
| `port` | int | `9000` | HTTP port to listen on |
| `webhook_secret` | string | `""` | GitHub webhook secret (leave empty to skip verification) |
| `grace_seconds` | int | `30` | Seconds to wait after last job before stopping a runner |
| `runners` | array | `[]` | List of runner definitions (see below) |

### Runner definition

| Field | Type | Description |
|---|---|---|
| `repo` | string | Single repo (`owner/name`) |
| `repos` | string[] | Multiple repos sharing one runner |
| `service` | string | systemd service name to start/stop |
| `start_command` | string | Custom start command (overrides `service`) |
| `stop_command` | string | Custom stop command (overrides `service`) |
| `grace_seconds` | int | Per-runner override of the global grace period |

### Finding your runner service name

```bash
systemctl list-units 'actions.runner.*' --no-pager
```

### Environment variable overrides

Useful in Docker or CI:

| Variable | Overrides |
|---|---|
| `CONFIG_FILE` | Path to config file |
| `WEBHOOK_SECRET` | `webhook_secret` |
| `PORT` | `port` |
| `GRACE_SECONDS` | `grace_seconds` |

---

## Examples

### Multiple repos, one runner

```json
{
  "runners": [
    {
      "repos": ["org/frontend", "org/backend"],
      "service": "actions.runner.org-frontend.my-runner",
      "grace_seconds": 60
    }
  ]
}
```

### Docker-based runner

```json
{
  "runners": [
    {
      "repo": "org/repo",
      "start_command": "docker start github-runner",
      "stop_command": "docker stop github-runner"
    }
  ]
}
```

### Multiple independent runners

```json
{
  "runners": [
    {
      "repo": "org/frontend",
      "service": "actions.runner.org-frontend.fe-runner"
    },
    {
      "repo": "org/backend",
      "service": "actions.runner.org-backend.be-runner",
      "grace_seconds": 60
    }
  ]
}
```

---

## GitHub webhook setup

For each repository:

1. Go to **Settings → Webhooks → Add webhook**
2. **Payload URL**: `http://YOUR_SERVER_IP:9000`
3. **Content type**: `application/json`
4. **Secret**: same value as `webhook_secret` in config
5. **Events**: select **Workflow jobs** only
6. Click **Add webhook** — GitHub sends a ping that the autoscaler responds to

Generate a secure secret:
```bash
openssl rand -hex 32
```

---

## Firewall

```bash
# UFW
sudo ufw allow 9000/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 9000 -j ACCEPT
```

---

## Logs

```bash
journalctl -fu runner-autoscaler
```

Example output:
```
2026-01-01 12:00:00 INFO     Listening on port 9000 | 2 repo(s): ['org/frontend', 'org/backend']
2026-01-01 12:05:12 INFO     [org/frontend] queued #987654321
2026-01-01 12:05:12 INFO     systemctl start actions.runner.org-frontend.fe-runner
2026-01-01 12:08:44 INFO     [org/frontend] completed #987654321
2026-01-01 12:08:44 INFO     Job 987654321 completed. Active jobs for …fe-runner: 0
2026-01-01 12:09:14 INFO     Stopping runner: …fe-runner
```

---

## Health check

```bash
curl http://localhost:9000/health
# → 200 OK
```

---

## License

MIT

---

## Contributing

Issues and PRs welcome. Keep it dependency-free.
