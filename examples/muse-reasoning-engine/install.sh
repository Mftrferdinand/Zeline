#!/usr/bin/env bash
# muse-stack installer: one-command setup of the Muse reasoning-engine stack.
#
# Installs: bridge.py + poller.py (OpenAI-compatible reasoning backend),
# systemd units (bridge, poller), and a watchdog timer that reinstalls +
# restarts everything if units vanish (VM replacement) or services die.
#
# Run: sudo ./install.sh
#
# After install:
#   1. Edit /etc/muse-stack/poller.env — set MUSE_POLLER_API_KEY (and BASE_URL/MODEL).
#   2. systemctl restart muse-poller && journalctl -u muse-poller -f
#   3. In 9Router: add OpenAI-compatible provider 'Muse' -> http://127.0.0.1:8765/v1
#   4. In Zeline: use model 'muse/muse'.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST="/opt/muse-stack"
CONF="/etc/muse-stack"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./install.sh" >&2
  exit 1
fi

# Copy the whole example dir so the watchdog can reinstall from $DST later.
mkdir -p "$DST" "$CONF"
cp -r "$SRC/." "$DST/"
chmod 755 "$DST/bridge.py" "$DST/poller.py" "$DST/watchdog.sh" "$DST/install.sh"

# env files (never overwrite existing ones)
[ -f "$CONF/poller.env" ] || { cp "$SRC/poller.env.example" "$CONF/poller.env"; chmod 600 "$CONF/poller.env"; }
[ -f "$CONF/bridge.env" ] || { printf '# Bridge env (optional)\n# MUSE_BRIDGE_HOST=127.0.0.1\n# MUSE_BRIDGE_PORT=8765\n# MUSE_BRIDGE_DIR=/var/lib/muse-bridge/queue\n' > "$CONF/bridge.env"; chmod 600 "$CONF/bridge.env"; }

cp "$DST/systemd/muse-bridge.service" "$DST/systemd/muse-poller.service" \
   "$DST/systemd/muse-stack-watchdog.service" "$DST/systemd/muse-stack-watchdog.timer" \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now muse-bridge.service
systemctl enable --now muse-poller.service
systemctl enable --now muse-stack-watchdog.timer

echo
echo "Installed to $DST. Next steps:"
echo "  1. Edit $CONF/poller.env — set MUSE_POLLER_API_KEY (and BASE_URL/MODEL)."
echo "  2. systemctl restart muse-poller && journalctl -u muse-poller -f"
echo "  3. In 9Router: add OpenAI-compatible provider 'Muse' -> http://127.0.0.1:8765/v1"
echo "  4. In Zeline: model 'muse/muse'. Health: curl -s http://127.0.0.1:8765/health"
echo "  Watchdog: every 2 min checks bridge/poller (+ 9router/zeline if present),"
echo "  reinstalls + restarts on failure. Log: /var/log/muse-stack-watchdog.log"
