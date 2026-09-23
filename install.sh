#!/usr/bin/env bash
# GitHub Runner Autoscaler — installer
# Usage: sudo bash install.sh
set -euo pipefail

INSTALL_DIR="/opt/github-runner-autoscaler"
CONFIG_DIR="/etc/github-runner-autoscaler"
SERVICE_FILE="/etc/systemd/system/runner-autoscaler.service"
REPO_URL="https://raw.githubusercontent.com/gokturksigirtmac/github-runner-autoscaler/main"

# ── helpers ────────────────────────────────────────────────────────────────
green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }
die()    { red "Error: $*"; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash install.sh"
command -v python3 >/dev/null || die "python3 not found. Install it first."

# ── install ────────────────────────────────────────────────────────────────
green "==> Creating directories"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

green "==> Downloading autoscaler.py"
if command -v curl >/dev/null; then
  curl -fsSL "$REPO_URL/autoscaler.py" -o "$INSTALL_DIR/autoscaler.py"
elif command -v wget >/dev/null; then
  wget -qO "$INSTALL_DIR/autoscaler.py" "$REPO_URL/autoscaler.py"
else
  die "Neither curl nor wget found."
fi
chmod +x "$INSTALL_DIR/autoscaler.py"

green "==> Downloading systemd unit"
if command -v curl >/dev/null; then
  curl -fsSL "$REPO_URL/runner-autoscaler.service" -o "$SERVICE_FILE"
else
  wget -qO "$SERVICE_FILE" "$REPO_URL/runner-autoscaler.service"
fi

green "==> Installing config"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  if command -v curl >/dev/null; then
    curl -fsSL "$REPO_URL/config.example.json" -o "$CONFIG_DIR/config.json"
  else
    wget -qO "$CONFIG_DIR/config.json" "$REPO_URL/config.example.json"
  fi
  yellow "  Config created at $CONFIG_DIR/config.json — edit it before starting the service!"
else
  yellow "  Existing config kept at $CONFIG_DIR/config.json"
fi

green "==> Enabling systemd service (not starting yet)"
systemctl daemon-reload
systemctl enable runner-autoscaler

# ── UFW (optional) ────────────────────────────────────────────────────────
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  PORT=$(python3 -c "import json; c=json.load(open('$CONFIG_DIR/config.json')); print(c.get('port',9000))" 2>/dev/null || echo 9000)
  ufw allow "${PORT}/tcp" >/dev/null
  green "==> UFW: allowed port $PORT"
fi

# ── done ──────────────────────────────────────────────────────────────────
echo ""
green "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/config.json"
echo "     - Set webhook_secret (generate with: openssl rand -hex 32)"
echo "     - Add your repos and runner service names"
echo ""
echo "  2. Start the service:"
echo "     systemctl start runner-autoscaler"
echo "     journalctl -fu runner-autoscaler"
echo ""
echo "  3. Register a GitHub webhook on each repo:"
echo "     URL:          http://YOUR_SERVER_IP:9000"
echo "     Content type: application/json"
echo "     Secret:       (same as webhook_secret in config)"
echo "     Events:       Workflow jobs"
echo ""
echo "  4. Stop your runners — they will auto-start on the next push:"
echo "     systemctl stop <your-runner-service>"
echo ""
echo "  Health check: curl http://YOUR_SERVER_IP:9000/health"
