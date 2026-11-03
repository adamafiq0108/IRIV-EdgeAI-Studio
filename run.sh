#!/usr/bin/env bash
#
# run.sh -- bring the whole stack up on this board, in the foreground.
#
#   hub      :5100   <- open http://<pi-ip>:5100 (the Software port setting; 5100 out of the box)
#   masters  :511x   one per system, spawned BY the hub
#   apps             detection loops (camera + Hailo), spawned by the hub
#
# One command, clean slate: every old process is stopped first, then the hub
# starts and brings up each system flagged Autostart (its master AND its
# detection app). Ctrl-C stops the hub; the systems it started keep running,
# and re-running this script restarts everything.
#
# Stopping the MASTERS here matters more than it looks: they are long-lived
# processes that survive a hub restart, so leaving them alone after a git pull
# means the dashboards run new code while the APIs behind them run yesterday's
# -- the cause of every "I pulled but nothing changed" afternoon.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${HERE}/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

mkdir -p "${HERE}/logs"

# Dependencies added since this board was set up. Installing them here means a
# `git pull` is enough to make a new feature work -- otherwise the first sign
# of a missing package is a workflow node that silently does nothing, hours
# after the pull. Only INSTALLS what is missing, and only into this checkout's
# venv; no venv means the operator manages packages themselves, so we skip it.
if [ -x "${HERE}/.venv/bin/pip" ]; then
  missing=""
  for mod in paho.mqtt; do
    "$PY" -c "import ${mod}" 2>/dev/null || missing="${missing} ${mod}"
  done
  if [ -n "$missing" ]; then
    echo "== installing new dependencies:${missing} =="
    "${HERE}/.venv/bin/pip" install --quiet -r "${HERE}/requirements.txt" \
      || echo "   (install failed -- those features stay off; everything else runs)"
  fi
fi

# The LAN IP this box is reached by -- for the banner below only. It is NOT
# written to config: the servers rewrite every browser-facing link onto the
# host the request arrived on (core/net.py), so cards and back links follow
# whatever address the operator typed. Freezing this IP into
# config/systems.local.toml is what used to break the whole UI the next time
# DHCP moved the board -- every link still pointed at the old address.
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
IP="${IP:-127.0.0.1}"

# The boot service and this script both want the hub port. If the service is
# running, hand the port over for this session -- otherwise the hub started
# below cannot bind, and systemd would restart its own copy underneath us.
if command -v systemctl >/dev/null 2>&1 \
   && systemctl is-active --quiet irivai-hub.service 2>/dev/null; then
  echo "== stopping irivai-hub.service (this script runs the hub itself) =="
  sudo systemctl stop irivai-hub.service || true
  echo "   re-enable it later with: sudo systemctl start irivai-hub.service"
fi

echo "== stopping any running instances =="
pkill -f "hub_server.py"    2>/dev/null || true
pkill -f "master_server.py" 2>/dev/null || true
pkill -f "app.run"          2>/dev/null || true
sleep 2

# The port is a setting (Settings -> Network -> Software port), read here
# only to print the address; the hub reads it again itself.
HUB_PORT="$("${PY}" -c 'from core.services import hubport; print(hubport.port())' 2>/dev/null | tail -n 1)"
HUB_PORT="${HUB_PORT:-5100}"
echo
echo "== hub :${HUB_PORT}  ->  http://${IP}:${HUB_PORT}   (Ctrl-C stops the hub) =="
echo "   systems flagged Autostart come up with it, master + detection."
echo "   Anything else: start it from System Control."
exec "${PY}" "${HERE}/hub_server.py"
