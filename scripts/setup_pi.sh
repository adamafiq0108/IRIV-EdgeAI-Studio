#!/usr/bin/env bash
#
# setup_pi.sh -- Install and wire one IRIV Edge AI Studio process on a Linux host.
#
# One host runs one role. A slave Pi runs the agent; the hub and the masters can
# share a machine or not (docs/setup.md sec 1). The roles differ only in
# arguments, so they differ only in arguments here too:
#
#   sudo ./scripts/setup_pi.sh --role agent --adapter <module>:<AdapterClass>
#   sudo ./scripts/setup_pi.sh --role hub
#   sudo ./scripts/setup_pi.sh --role master --system <slug> --port 5110
#
# Installs the checkout it is run from -- no clone URL to guess, no second copy
# of the code to drift. Re-running is safe: every step checks before it writes.
#
# Pairing is NOT done here. It needs an operator, a token and a device that is
# already up (docs/setup.md sec 5); this script prints the exact command.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

ROLE=""
SYSTEM=""
ADAPTER=""
PORT=""
SERVICE_USER="cytron"
INSTALL_SERVICE=1

AGENT_STATE_DIR="/var/lib/cytron-agent"
AGENT_STATE="${AGENT_STATE_DIR}/pairing.json"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${REPO_DIR}/.venv"
PY="${VENV}/bin/python"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: setup_pi.sh --role <agent|hub|master> [options]

  --role agent     Contract v1 agent for one device (port 5101)
  --role hub       system cards, one per system (port 5100)
  --role master    one system's dashboard (port 5110, 5111, ...)

Options:
  --system <slug>    master only, required: must match the package folder name
  --adapter <m:C>    agent only, required: module:Class of the adapter
  --port <n>         defaults: agent 5101, hub 5100, master 5110
  --user <name>      service account (default: cytron)
  --no-service       install and verify only; write no systemd unit
  -h, --help

The pairing window is never opened from here: it is an operator action on a
device that is already up (docs/setup.md sec 5). This script prints the steps.
EOF
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)       ROLE="${2:-}"; shift 2 ;;
        --system)     SYSTEM="${2:-}"; shift 2 ;;
        --adapter)    ADAPTER="${2:-}"; shift 2 ;;
        --port)       PORT="${2:-}"; shift 2 ;;
        --user)       SERVICE_USER="${2:-}"; shift 2 ;;
        --no-service) INSTALL_SERVICE=0; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            usage >&2; die "unknown argument: $1" ;;
    esac
done

case "$ROLE" in
    agent)
        [[ -n "$ADAPTER" ]] || die "--role agent needs --adapter <module>:<Class> (write one with the new-slave-adapter skill, or use tools/mock_slave.py to rehearse without hardware)"
        [[ "$ADAPTER" == *:* ]] || die "--adapter must be module:Class, got '${ADAPTER}'"
        PORT="${PORT:-5101}"
        ;;
    hub)
        PORT="${PORT:-5100}"
        ;;
    master)
        [[ -n "$SYSTEM" ]] || die "--role master needs --system <slug>"
        PORT="${PORT:-5110}"
        ;;
    *)
        usage >&2
        die "--role must be agent, hub or master"
        ;;
esac

[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port must be a number, got '${PORT}'"

# The unit this run owns. Named once here so the error paths can name it too.
case "$ROLE" in
    agent)  ROLE_UNIT="cytron-agent" ;;
    hub)    ROLE_UNIT="cytron-hub" ;;
    master) ROLE_UNIT="cytron-master@${SYSTEM}" ;;
esac

if [[ $INSTALL_SERVICE -eq 1 && $EUID -ne 0 ]]; then
    die "installing a systemd unit needs root. Re-run with sudo, or pass --no-service."
fi

# ---------------------------------------------------------------------------
# 1. Prerequisites (docs/setup.md sec 1)
# ---------------------------------------------------------------------------

say "Checking prerequisites"

command -v python3 >/dev/null || die "python3 not found"

# 3.11 is the floor twice over: core/config.py uses stdlib tomllib, and the
# routes use `str | None` annotations FastAPI resolves at import.
python3 - <<'PY' || die "Python 3.11+ required (tomllib + PEP 604 annotations)"
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
echo "  python3: $(python3 --version)"

if ! python3 -c "import venv" 2>/dev/null; then
    if command -v apt-get >/dev/null; then
        say "Installing python3-venv, git, curl"
        apt-get update && apt-get install -y python3-venv git curl
    else
        die "python3-venv missing and this is not an apt host -- install it yourself"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Device identity (agent only, docs/setup.md sec 2)
# ---------------------------------------------------------------------------

if [[ "$ROLE" == "agent" ]]; then
    say "Checking device identity"

    SERIAL=""
    if [[ -r /sys/firmware/devicetree/base/serial-number ]]; then
        # The devicetree string is null-terminated. The trailing \x00 is invisible
        # in a terminal and compares unequal to the same serial read anywhere else,
        # so strip it here exactly as agent/identity.py does.
        SERIAL="$(tr -d '\0' < /sys/firmware/devicetree/base/serial-number | tr 'A-Z' 'a-z')"
    elif grep -qs '^Serial' /proc/cpuinfo; then
        SERIAL="$(awk '/^Serial/ {print tolower($3)}' /proc/cpuinfo)"
    fi

    if [[ -n "$SERIAL" ]]; then
        echo "  host_serial: ${SERIAL}"
        echo "  (this is the device's identity -- never paste it into config; the agent reads it at runtime)"
    else
        warn "no CPU serial readable -- the agent will refuse to start on this host, and it is right to."
        warn "Off-device, run it by hand with --serial-fallback."
    fi

    # A Pi 5 has no RTC. A boot without NTP timestamps events in 1970, the master
    # flags the skew (core/services/sync.py SKEW_LIMIT_S = 60), and the digest is
    # poisoned. Cheaper to catch now than in contract_check.
    if command -v timedatectl >/dev/null; then
        if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
            echo "  clock: NTP synchronised"
        else
            warn "clock NOT NTP-synchronised. Fix this before pairing -- 'timedatectl status'."
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 3. Service account
# ---------------------------------------------------------------------------

if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "Service account: ${SERVICE_USER}"
    if id -u "$SERVICE_USER" >/dev/null 2>&1; then
        echo "  exists"
    else
        # No login shell, no home: this account runs one process and owns a token.
        useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
        echo "  created"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Virtualenv
# ---------------------------------------------------------------------------

say "Installing into ${REPO_DIR}"

[[ -f "${REPO_DIR}/requirements.txt" ]] || die "no requirements.txt in ${REPO_DIR} -- run this script from inside the checkout"

if [[ $INSTALL_SERVICE -eq 1 ]] && command -v su >/dev/null; then
    # systemd chdir's into WorkingDirectory as SERVICE_USER before it execs
    # anything. A checkout under /home/<someone> fails there with 200/CHDIR --
    # home directories are 0750, so the service account cannot traverse them,
    # and the unit restart-loops with an error naming no path. Fail here with
    # one that does. -s overrides the account's nologin shell.
    if ! su -s /bin/sh -c "test -x '${REPO_DIR}'" "$SERVICE_USER" 2>/dev/null; then
        die "user '${SERVICE_USER}' cannot enter ${REPO_DIR}, so systemd could not
       chdir there (status=200/CHDIR). A home directory is 0750 and only its
       owner may traverse it. Move the checkout somewhere the service account
       can reach and re-run:

         sudo systemctl disable --now ${ROLE_UNIT}
         sudo mv ${REPO_DIR} /opt/cytron-dashboard
         cd /opt/cytron-dashboard && sudo rm -rf .venv
         sudo bash scripts/setup_pi.sh <same arguments>

       The venv goes because its scripts hard-code their own absolute paths; a
       moved venv points at where it used to live. Or keep the checkout here and
       pass --user ${SUDO_USER:-pi} to run the service as its owner instead."
    fi
fi

if [[ ! -x "$PY" ]]; then
    python3 -m venv "$VENV"
    echo "  venv created"
fi

"${VENV}/bin/pip" install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet -r "${REPO_DIR}/requirements.txt"
echo "  dependencies installed"

if [[ $INSTALL_SERVICE -eq 1 ]]; then
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$VENV"
fi

# ---------------------------------------------------------------------------
# 4b. Frontend (React SPA) -- hub and master serve it; the agent has no UI
# ---------------------------------------------------------------------------
#
# The dashboard UI is a Vite + React SPA in frontend/, built here on the host
# into core/static/app (served by hub_server.py / master_server.py). Unlike the
# edge apps -- which ship a pre-built dist because a Pi should never need Node --
# the hub/master build on the host so the repo carries no build artifact.

if [[ "$ROLE" != "agent" ]]; then
    say "Frontend (React SPA)"

    if ! command -v node >/dev/null 2>&1; then
        if command -v apt-get >/dev/null; then
            echo "  node not found -- installing Node.js LTS (NodeSource)"
            curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
            apt-get install -y nodejs
        else
            die "node/npm not found and this is not an apt host -- install Node.js 18+ and re-run"
        fi
    fi
    echo "  node: $(node --version)   npm: $(npm --version)"

    ( cd "${REPO_DIR}/frontend" \
        && { [[ -d node_modules ]] && npm ci >/dev/null 2>&1 || npm install; } \
        && npm run build ) \
      || die "frontend build failed -- see the npm output above"
    echo "  built core/static/app"

    if [[ $INSTALL_SERVICE -eq 1 ]]; then
        # The service account only reads the built bundle, but must be able to.
        chown -R "${SERVICE_USER}:${SERVICE_USER}" "${REPO_DIR}/core/static/app" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
# 5. Role-specific state
# ---------------------------------------------------------------------------

if [[ "$ROLE" == "agent" && $INSTALL_SERVICE -eq 1 ]]; then
    say "State directory"
    # Holds the bearer token. 700/600 or it is not a secret.
    install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$AGENT_STATE_DIR"
    [[ -f "$AGENT_STATE" ]] && chmod 600 "$AGENT_STATE"
    echo "  ${AGENT_STATE_DIR} (700, ${SERVICE_USER})"
fi

if [[ "$ROLE" != "agent" ]]; then
    say "Config"

    if [[ $INSTALL_SERVICE -eq 1 ]]; then
        # The service WRITES here, it does not only read: pairing a device from
        # the Settings page appends to slaves.local.toml, and the first login
        # generates the session secret into auth.local.toml. Both go through
        # config.save_local(), which creates the file -- so the directory itself
        # must be writable by the service account, not just the files in it.
        # Without this the master answers 500 on pair and on login, and the
        # traceback (PermissionError, deep in config.py) names nothing an
        # operator would connect back to file ownership.
        chown "${SERVICE_USER}:${SERVICE_USER}" "${REPO_DIR}/config"
        echo "  config/ writable by ${SERVICE_USER}"
    fi

    # Secrets live only in gitignored *.local.toml (docs/conventions.md sec 6).
    # Tighten anything already there rather than inventing config the operator
    # has not written yet.
    shopt -s nullglob
    for f in "${REPO_DIR}"/config/*.local.toml; do
        chmod 600 "$f"
        [[ $INSTALL_SERVICE -eq 1 ]] && chown "${SERVICE_USER}:${SERVICE_USER}" "$f"
        echo "  secured $(basename "$f")"
    done
    shopt -u nullglob
fi

# ---------------------------------------------------------------------------
# 6. systemd unit
# ---------------------------------------------------------------------------

write_unit() {
    local path="$1" body="$2"
    printf '%s\n' "$body" > "$path"
    echo "  wrote ${path}"
}

if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "systemd unit"

    case "$ROLE" in
        agent)
            UNIT="$ROLE_UNIT"
            # --unlock is deliberately absent. A device that reopens its pairing
            # window on every reboot can be seized by any host on the LAN.
            write_unit "/etc/systemd/system/${UNIT}.service" "[Unit]
Description=IRIV Edge AI Studio system agent (Contract v1)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
Environment=CYTRON_AGENT_STATE=${AGENT_STATE}
ExecStart=${PY} -m agent.agent --adapter ${ADAPTER} --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"
            ;;
        hub)
            UNIT="$ROLE_UNIT"
            write_unit "/etc/systemd/system/${UNIT}.service" "[Unit]
Description=IRIV Edge AI Studio hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${PY} hub_server.py --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"
            ;;
        master)
            # Template unit: masters differ only in --system and --port, so one
            # file serves every system (docs/setup.md sec 3).
            UNIT="$ROLE_UNIT"
            install -d -m 755 /etc/cytron
            printf 'PORT=%s\n' "$PORT" > "/etc/cytron/${SYSTEM}.env"
            echo "  wrote /etc/cytron/${SYSTEM}.env (PORT=${PORT})"
            write_unit "/etc/systemd/system/cytron-master@.service" "[Unit]
Description=IRIV Edge AI Studio master (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=/etc/cytron/%i.env
ExecStart=${PY} master_server.py --system %i --port \${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"
            ;;
    esac

    systemctl daemon-reload
    systemctl enable "$UNIT" >/dev/null

    # restart, not `enable --now`: --now only STARTS a stopped unit, so a unit
    # already running keeps its old EnvironmentFile and its old ExecStart. Re-run
    # this script with a different --port and the unit would go on serving the
    # previous one -- healthy, enabled, and answering where nobody probes.
    systemctl restart "$UNIT"

    # Poll for the port rather than trust is-active. Restart=always means a unit
    # that crashes on boot still reads "activating" for its first seconds, so
    # is-active answers "yes" to a question nobody asked. The port answering is
    # the thing we actually want to know.
    ok=0
    for _ in $(seq 1 15); do
        if curl -fsS -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
            ok=1
            break
        fi
        systemctl is-active --quiet "$UNIT" || break
        sleep 1
    done

    if [[ $ok -eq 1 ]]; then
        echo "  ${UNIT} answering on port ${PORT}"
    else
        systemctl status "$UNIT" --no-pager --lines 20 || true
        journalctl -u "$UNIT" --no-pager --lines 20 || true
        die "${UNIT} is not answering on port ${PORT} -- see the log above"
    fi
fi

# ---------------------------------------------------------------------------
# 7. Verify + next steps
# ---------------------------------------------------------------------------

say "Done"

if [[ "$ROLE" == "agent" ]]; then
    if [[ $INSTALL_SERVICE -eq 1 ]]; then
        curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null \
            && echo "  /api/health answering" \
            || warn "/api/health not answering -- journalctl -u cytron-agent -n 50"

        PAIRED="$(curl -fsS "http://127.0.0.1:${PORT}/api/agent/info" 2>/dev/null \
                  | grep -o '"paired": *[a-z]*' | awk '{print $2}')" || PAIRED="?"
        echo "  paired: ${PAIRED:-?}"
    fi

    cat <<EOF

Next -- pairing (docs/setup.md sec 5). An operator does this, not this script:

  1. On the MASTER, generate a token for this device:
       python3 -c "import secrets; print(secrets.token_urlsafe(32))"

  2. Confirm this device is unpaired and is the right system:
       curl -s http://<this-pi-ip>:${PORT}/api/agent/info

     Check "system_slug" matches the master you are pairing to. Nothing
     enforces this yet, and a device paired to the wrong master reports its
     events into the wrong system's dashboard.

  3. Open the window on this device (first install only), then pair:
       sudo systemctl stop cytron-agent
       sudo -u ${SERVICE_USER} ${PY} -m agent.agent \\
         --adapter ${ADAPTER} --state ${AGENT_STATE} --unlock
       # from the master, within 300s:
       curl -s -X POST http://<this-pi-ip>:${PORT}/api/agent/pair \\
         -H 'Content-Type: application/json' \\
         -d '{"master_serial":"<master-serial>","token":"<token>"}'
       # then Ctrl-C the manual run and:
       sudo systemctl start cytron-agent

  4. On the MASTER, record the device -- config/slaves.toml:
       [slaves.${SERIAL:-<host_serial>}]
       system_slug = "<slug>"
       label = "<where it is>"
     and its token in gitignored config/slaves.local.toml (chmod 600):
       [slaves.${SERIAL:-<host_serial>}]
       token = "<token from step 1>"

  5. From the MASTER, the arbiter:
       .venv/bin/python tools/contract_check.py http://<this-pi-ip>:${PORT} --token <token>

Firewall (docs/setup.md sec 8) -- the agent serves video and event history to
anything holding the token. Only its own master should reach this port:

  sudo ufw allow from <master-ip> to any port ${PORT} proto tcp

EOF
else
    cat <<EOF

  ${ROLE} on port ${PORT}: http://\$(hostname -I | awk '{print \$1}'):${PORT}

Next:
  - config/systems.local.toml sets [hub].url and [masters.<slug>].url.
    A system with no [masters.<slug>] block still gets a card -- it just says
    "no master configured". That is deliberate.
  - Secrets go in gitignored *.local.toml only, chmod 600.
  - Logs: journalctl -u ${UNIT:-cytron-${ROLE}} -f

EOF
fi
