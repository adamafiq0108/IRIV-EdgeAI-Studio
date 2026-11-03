#!/usr/bin/env bash
#
# install_service.sh -- make the platform come back by itself after a reboot.
#
# Without this the hub is whatever shell last launched it: a power cut, a
# kernel update or an unplugged board leaves every dashboard dead until someone
# SSHes in. This installs ONE systemd unit for the hub; the hub then starts each
# system flagged Autostart (master + detection) exactly as it does when you flip
# that switch in System Control. So: one unit here, per-system choice there.
#
#   sudo scripts/install_service.sh          # install + enable + start
#   sudo scripts/install_service.sh remove   # stop + disable + delete
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=/etc/systemd/system/irivai-hub.service
# No port by default: the hub reads Settings -> Network -> Software port
# (config/systems.local.toml [hub].port, 5100 out of the box). PORT=<n> in
# the environment bakes an explicit --port into the unit instead, which then
# wins over the setting -- a developer's choice, not an appliance's.
PORT_ARG=""
[[ -n "${PORT:-}" ]] && PORT_ARG=" --port ${PORT}"
RUN_USER="${SUDO_USER:-$(id -un)}"

if [ "${1:-install}" = "remove" ]; then
  systemctl stop irivai-hub.service 2>/dev/null || true
  systemctl disable irivai-hub.service 2>/dev/null || true
  rm -f "$UNIT"
  systemctl daemon-reload
  echo "removed irivai-hub.service"
  exit 0
fi

PY="${HERE}/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

cat > "$UNIT" <<EOF
[Unit]
Description=IRIV EdgeAI Studio hub (systems dashboard)
# Wants= rather than Requires=: a board with no network should still bring the
# hub up on loopback, so a locally attached screen is not left with nothing.
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${HERE}
ExecStart=${PY} ${HERE}/hub_server.py${PORT_ARG}
# The hub spawns each system's master as its own detached process; KillMode
# 'process' stops only the hub on restart, so a dashboard restart does not take
# every running detection down with it.
KillMode=process
Restart=always
RestartSec=5
StandardOutput=append:${HERE}/logs/hub.log
StandardError=append:${HERE}/logs/hub.log

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "${HERE}/logs"
chown "${RUN_USER}" "${HERE}/logs" 2>/dev/null || true
systemctl daemon-reload
systemctl enable irivai-hub.service
systemctl restart irivai-hub.service
sleep 3
systemctl --no-pager --lines=0 status irivai-hub.service || true
echo
echo "Hub installed as irivai-hub.service on port ${PORT}."
echo "Flip Autostart on a system in System Control and it will come back too."
