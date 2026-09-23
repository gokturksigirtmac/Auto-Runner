#!/usr/bin/env python3
"""
GitHub Runner Autoscaler
========================
Starts self-hosted runners on workflow_job.queued,
stops them after a grace period once the job completes.

Config: /etc/github-runner-autoscaler/config.json (or CONFIG_FILE env var)
"""

import hashlib
import hmac
import http.server
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("autoscaler")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "port": 9000,
    "webhook_secret": "",
    "grace_seconds": 30,
    "runners": [],
}


def load_config() -> dict:
    path = os.environ.get(
        "CONFIG_FILE", "/etc/github-runner-autoscaler/config.json"
    )
    try:
        with open(path) as f:
            cfg = json.load(f)
        log.info("Config loaded from %s", path)
    except FileNotFoundError:
        log.warning("Config file not found at %s, using env vars only", path)
        cfg = {}
    except json.JSONDecodeError as e:
        log.error("Config parse error: %s", e)
        sys.exit(1)

    merged = {**DEFAULT_CONFIG, **cfg}

    # Env var overrides (useful in Docker / CI)
    if os.environ.get("WEBHOOK_SECRET"):
        merged["webhook_secret"] = os.environ["WEBHOOK_SECRET"]
    if os.environ.get("PORT"):
        merged["port"] = int(os.environ["PORT"])
    if os.environ.get("GRACE_SECONDS"):
        merged["grace_seconds"] = int(os.environ["GRACE_SECONDS"])

    # Build lookup: repo → runner config
    merged["_by_repo"] = {}
    for r in merged["runners"]:
        for repo in (r.get("repos") or [r.get("repo")]):
            if repo:
                merged["_by_repo"][repo] = r

    if not merged["_by_repo"]:
        log.warning("No runners configured. Add entries to config.json.")

    return merged


# ---------------------------------------------------------------------------
# Runner backend
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 30) -> bool:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            log.warning("Command %s exited %d: %s", cmd, r.returncode, r.stderr.strip())
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("Command %s timed out after %ds", cmd, timeout)
        return False
    except Exception as e:
        log.error("Command %s failed: %s", cmd, e)
        return False


def _resolve_cmd(runner: dict, action: str) -> list[str] | None:
    """
    Return the shell command for start/stop.

    Priority:
      1. Explicit start_command / stop_command strings in config
      2. service → systemctl start/stop <service>
    """
    key = f"{action}_command"
    if runner.get(key):
        return runner[key].split()

    svc = runner.get("service")
    if svc:
        return ["systemctl", action, svc]

    log.error("Runner has neither 'service' nor '%s' defined: %s", key, runner)
    return None


def is_running(runner: dict) -> bool:
    svc = runner.get("service")
    if svc:
        r = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True, text=True,
        )
        return r.stdout.strip() == "active"

    # Custom command: optimistically assume not running so we always start
    return False


def start_runner(runner: dict) -> None:
    label = runner.get("service") or runner.get("start_command", "?")
    if is_running(runner):
        log.info("Runner already active: %s", label)
        return
    cmd = _resolve_cmd(runner, "start")
    if cmd:
        log.info("Starting runner: %s", label)
        _run(cmd)


def stop_runner(runner: dict) -> None:
    label = runner.get("service") or runner.get("stop_command", "?")
    cmd = _resolve_cmd(runner, "stop")
    if cmd:
        log.info("Stopping runner: %s", label)
        _run(cmd)


# ---------------------------------------------------------------------------
# In-flight job tracking
# ---------------------------------------------------------------------------
_active: dict[str, set] = {}   # service/label → set of job_ids
_timers: dict[str, threading.Timer] = {}
_lock = threading.Lock()


def _key(runner: dict) -> str:
    return runner.get("service") or runner.get("start_command", "unknown")


def _schedule_stop(runner: dict, grace: int) -> None:
    key = _key(runner)

    def _do_stop():
        with _lock:
            if _active.get(key):
                log.info("Grace elapsed but jobs still active for %s, skipping stop", key)
                return
            _timers.pop(key, None)
        stop_runner(runner)

    with _lock:
        # Cancel any pending stop timer (new job arrived)
        existing = _timers.pop(key, None)
        if existing:
            existing.cancel()
        t = threading.Timer(grace, _do_stop)
        _timers[key] = t
        t.daemon = True
        t.start()
        log.info("Stop scheduled in %ds for %s", grace, key)


def on_queued(runner: dict, job_id: str) -> None:
    key = _key(runner)
    with _lock:
        _active.setdefault(key, set()).add(job_id)
        # Cancel any pending stop (new work arrived)
        t = _timers.pop(key, None)
        if t:
            t.cancel()
            log.info("Pending stop cancelled — new job queued for %s", key)
    start_runner(runner)


def on_completed(runner: dict, job_id: str, grace: int) -> None:
    key = _key(runner)
    with _lock:
        _active.setdefault(key, set()).discard(job_id)
        remaining = len(_active[key])

    log.info("Job %s completed. Active jobs for %s: %d", job_id, key, remaining)

    if remaining == 0:
        _schedule_stop(runner, grace)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class WebhookHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, b"OK")
        else:
            self._reply(404, b"Not Found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if not self._verify_signature(body):
            self._reply(403, b"Forbidden")
            return

        event = self.headers.get("X-GitHub-Event", "")
        if event != "workflow_job":
            self._reply(200, b"OK")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._reply(400, b"Bad Request")
            return

        self._reply(200, b"OK")
        self._handle_event(payload)

    # ------------------------------------------------------------------

    def _verify_signature(self, body: bytes) -> bool:
        secret = self.server.config.get("webhook_secret", "")
        if not secret:
            return True  # No secret configured — allow all
        sig_header = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig_header, expected)

    def _handle_event(self, payload: dict) -> None:
        repo   = payload.get("repository", {}).get("full_name", "")
        action = payload.get("action", "")
        job    = payload.get("workflow_job", {})
        job_id = str(job.get("id", ""))

        runner = self.server.config["_by_repo"].get(repo)
        if not runner:
            log.debug("No runner configured for repo: %s", repo)
            return

        grace = runner.get("grace_seconds", self.server.config["grace_seconds"])
        log.info("[%s] %s #%s", repo, action, job_id)

        if action == "queued":
            threading.Thread(
                target=on_queued, args=(runner, job_id), daemon=True
            ).start()
        elif action in ("completed", "cancelled"):
            threading.Thread(
                target=on_completed, args=(runner, job_id, grace), daemon=True
            ).start()

    def _reply(self, code: int, body: bytes = b"") -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.debug(fmt, *args)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
class AutoscalerServer(http.server.HTTPServer):
    def __init__(self, config: dict) -> None:
        self.config = config
        addr = ("", config["port"])
        super().__init__(addr, WebhookHandler)


def main() -> None:
    config = load_config()
    port   = config["port"]

    server = AutoscalerServer(config)

    # Graceful shutdown on SIGTERM / SIGINT
    def _shutdown(signum, _frame):
        log.info("Signal %d received, shutting down…", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    repos = list(config["_by_repo"].keys())
    log.info("Listening on port %d | %d repo(s): %s", port, len(repos), repos)
    server.serve_forever()


if __name__ == "__main__":
    main()
