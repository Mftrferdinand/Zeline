#!/usr/bin/env bash
# Watchdog for the muse reasoning-engine stack.
# Runs every 2 minutes via muse-stack-watchdog.timer.
# Closes the hole systemd Restart=always cannot cover: VM/container
# replacement wipes /etc/systemd/system, so units can vanish entirely.
# If units are missing or any service is down/unhealthy, reinstall the
# whole stack from /opt/muse-stack and start it again.
set -uo pipefail

SRC="${MUSE_STACK_SRC:-/opt/muse-stack}"
LOG="${MUSE_STACK_LOG:-/var/log/muse-stack-watchdog.log}"
BRIDGE_URL="${MUSE_BRIDGE_URL:-http://127.0.0.1:8765}"
ROUTER_URL="${MUSE_ROUTER_URL:-http://localhost:20128}"

need=0

# 1. our unit files must exist
for svc in muse-bridge muse-poller; do
  [ -f "/etc/systemd/system/$svc.service" ] || need=1
done

# 2. our services must be active
for svc in muse-bridge muse-poller; do
  systemctl is-active --quiet "$svc" 2>/dev/null || need=1
done

# 3. bridge must answer healthy
curl -s -m 5 "$BRIDGE_URL/health" | grep -q '"ok": *true' || need=1

# 4. if 9Router / zeline gateway units exist on this machine, they must be
#    active too (installed separately; we only restart/reinstall our stack,
#    but a dead router or gateway is worth a reinstall attempt as well)
if [ -f /etc/systemd/system/9router.service ]; then
  systemctl is-active --quiet 9router 2>/dev/null || need=1
  [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$ROUTER_URL/v1/models")" = "200" ] || need=1
fi
if [ -f /etc/systemd/system/zeline.service ]; then
  systemctl is-active --quiet zeline 2>/dev/null || need=1
fi

if [ "$need" = "1" ]; then
  echo "$(date '+%F %T %Z') watchdog: failure detected, reinstalling stack" >> "$LOG"
  "$SRC/install.sh" >> "$LOG" 2>&1
  echo "$(date '+%F %T %Z') watchdog: reinstall done" >> "$LOG"
fi
