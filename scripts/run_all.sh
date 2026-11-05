#!/usr/bin/env bash
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
#
# run_all.sh -- Stand up the hub and every enabled system's master on this host.
#
#   sudo ./scripts/run_all.sh
#
# One command for the whole master-side deployment. Wraps setup_pi.sh, which
# still does the actual installing -- this only decides what to install and on
# which port, so there is one place where a unit gets written, not two.
#
# Ports are NOT arguments. They come from config/systems.toml, because the hub
# probes and links each master at the url configured there: a master started on
# a port that disagrees with its config is invisible to the hub, and the card
# says "master unreachable" while the process is up and healthy. Change the
# port in systems.local.toml and re-run; never here.
#
# Slaves are not touched. A slave Pi runs the agent and nothing else:
#   sudo ./scripts/setup_pi.sh --role agent --adapter <module>:<Class>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_USER="cytron"
DRY_RUN=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: run_all.sh [--user <name>] [--dry-run]

Installs and starts, on THIS host:
  - the hub, on the port in [hub].url
  - one master per enabled system, on the port in [masters.<slug>].url

  --user <name>   service account (default: cytron)
  --dry-run       print what would be installed, change nothing
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)    SERVICE_USER="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *)         usage >&2; die "unknown argument: $1" ;;
    esac
done

if [[ $DRY_RUN -eq 0 && $EUID -ne 0 ]]; then
    die "installing units needs root. Re-run with sudo, or pass --dry-run."
fi

command -v python3 >/dev/null || die "python3 not found"
[[ -x "${SCRIPT_DIR}/setup_pi.sh" || -f "${SCRIPT_DIR}/setup_pi.sh" ]] \
    || die "setup_pi.sh not found next to this script"

# ---------------------------------------------------------------------------
# What to run, read from the config the hub itself reads
# ---------------------------------------------------------------------------

say "Reading config"

# The product's own loader, so *.local.toml overrides land exactly as they do at
# runtime. Stdlib only (tomllib), so this works before the venv exists. Emits
# "hub <port> <host>" and one "master <slug> <port> <host>" per enabled system.
#
# tr -d '\r' because a Python writing to a text-mode stdout (a developer running
# this under Git Bash or WSL) ends every line with CRLF. The CR then rides along
# on the last field, and a host compares unequal to itself for reasons nothing
# on screen shows. pipefail keeps python's exit status, not tr's.
PLAN="$(cd "$REPO_DIR" && python3 - <<'PY' | tr -d '\r'
import sys, urllib.parse
sys.path.insert(0, ".")
from core import config
from core.services import registry

cfg = config.load("systems")

def port_of(url, what):
    parsed = urllib.parse.urlparse(url or "")
    if not parsed.port:
        raise SystemExit(f"ERR:{what} has no port in its url: {url!r}")
    return parsed.port, parsed.hostname

lines = []

hub_url = (cfg.get("hub") or {}).get("url")
if hub_url:
    port, host = port_of(hub_url, "[hub].url")
    lines.append(f"hub {port} {host}")
else:
    print("WARN:no [hub].url -- masters will render no back link", file=sys.stderr)

# Discover rather than trust the config list: a slug in [masters.*] with no
# package on disk is a typo, and starting a master for it fails at boot anyway.
try:
    specs = registry.discover(cfg.get("dir") or "systems", cfg.get("enabled"))
except Exception as e:
    raise SystemExit(f"ERR:{type(e).__name__}: {e}")

masters = cfg.get("masters") or {}
for spec in specs:
    url = (masters.get(spec.slug) or {}).get("url")
    if not url:
        # Deliberate in the product: the card says "no master configured" rather
        # than linking nowhere. Nothing to start here, so say so and move on.
        print(f"WARN:{spec.slug} has no [masters.{spec.slug}].url -- not started",
              file=sys.stderr)
        continue
    port, host = port_of(url, f"[masters.{spec.slug}].url")
    lines.append(f"master {spec.slug} {port} {host}")

print("\n".join(lines))
PY
)" || die "could not read config/systems.toml -- see the error above"

grep -q '^ERR:' <<<"$PLAN" && die "${PLAN#ERR:}"
[[ -n "$PLAN" ]] || die "nothing to run: no [hub].url and no system has a [masters.<slug>].url"

# ---------------------------------------------------------------------------
# Local-host sanity
# ---------------------------------------------------------------------------

# A url may legitimately point at another machine (docs/setup.md sec 1: the hub
# and the masters need not share a host). Starting it HERE would then be wrong:
# the process would run on a port nobody probes.
LOCAL_IPS="$(hostname -I 2>/dev/null || true) 127.0.0.1 localhost"
non_local=0
while read -r kind a b c; do
    case "$kind" in
        hub)    host="$b" ;;
        master) host="$c" ;;
        *)      continue ;;
    esac
    [[ -z "$host" ]] && continue
    if ! grep -qw -- "$host" <<<"$LOCAL_IPS"; then
        warn "${kind} ${a} is configured at ${host}, which is not this host."
        non_local=1
    fi
done <<<"$PLAN"

if [[ $non_local -eq 1 ]]; then
    warn "Those are configured to run elsewhere. Starting them here anyway would"
    warn "give you a process no one probes. Fix systems.local.toml, or run this"
    warn "script on that host instead."
fi

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

say "Plan"
while read -r kind a b _; do
    case "$kind" in
        hub)    printf '  hub                  port %s\n' "$a" ;;
        master) printf '  master %-14s port %s\n' "$a" "$b" ;;
    esac
done <<<"$PLAN"

if [[ $DRY_RUN -eq 1 ]]; then
    say "Dry run -- nothing changed"
    exit 0
fi

# ---------------------------------------------------------------------------
# Install, one role at a time
# ---------------------------------------------------------------------------

setup() {
    bash "${SCRIPT_DIR}/setup_pi.sh" --user "$SERVICE_USER" "$@"
}

failed=()

while read -r kind a b _; do
    case "$kind" in
        hub)
            say "Hub (port ${a})"
            setup --role hub --port "$a" || failed+=("hub")
            ;;
        master)
            say "Master ${a} (port ${b})"
            # Keep going on failure: one bad system package must not leave the
            # other systems and the hub uninstalled. Report at the end.
            setup --role master --system "$a" --port "$b" || failed+=("master ${a}")
            ;;
    esac
done <<<"$PLAN"

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

say "Result"

while read -r kind a b _; do
    case "$kind" in
        hub)    unit="cytron-hub";              port="$a" ;;
        master) unit="cytron-master@${a}";      port="$b" ;;
        *)      continue ;;
    esac
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        health="$(curl -fsS -m 3 "http://127.0.0.1:${port}/api/health" 2>/dev/null || echo "no answer")"
        printf '  %-28s port %-5s %s\n' "$unit" "$port" "$health"
    else
        printf '  %-28s port %-5s NOT RUNNING\n' "$unit" "$port"
    fi
done <<<"$PLAN"

if [[ ${#failed[@]} -gt 0 ]]; then
    printf '\n'
    die "failed: ${failed[*]}
       journalctl -u cytron-hub -n 30
       journalctl -u 'cytron-master@*' -n 30"
fi

HUB_PORT="$(awk '/^hub /{print $2}' <<<"$PLAN")"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<EOF

  Hub: http://${IP:-<this-host>}:${HUB_PORT:-5100}

Next:
  - Log in as 'admin'. There is no default password: set [master].password in
    gitignored config/auth.local.toml (chmod 600, owned by ${SERVICE_USER}), or
    nobody logs in.
  - Cards show "master unreachable" until each master is up, and "no master
    configured" for a system with no [masters.<slug>].url. Those are different
    problems on purpose.
  - Devices are separate: run setup_pi.sh --role agent on each Pi, then pair
    (docs/setup.md sec 5). A master with no slaves reports 0/0 devices, which
    is honest, not broken.

EOF
