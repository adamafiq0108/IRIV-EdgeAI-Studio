#!/usr/bin/env bash
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
#
# install.sh -- Turn a board into a customer appliance. One command, one box.
#
#   sudo ./scripts/install.sh
#   sudo ./scripts/install.sh --adapter systems.demo.adapter:DemoAdapter
#
# This is NOT scripts/setup_pi.sh with defaults. setup_pi.sh installs one role
# from the checkout it runs in; here the install is a COPY, and what does not
# get copied is the point:
#
#   frontend/       source; only the built bundle is kept
#   the bench scripts that build a box (see BENCH_ONLY below)
#
# What ships is the product plus the built-in systems named in
# config/builtin.toml.
#
# Re-running is safe: every step checks before it writes.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="/opt/irivai-systor"
SERVICE_USER="cytron"
ADAPTER=""
HUB_PORT=5100
MASTER_PORT=5110
AGENT_PORT=5101
KEEP_NODE_MODULES=0
SKIP_HARDENING=0
INSTALL_SERVICE=1
SKIP_HARDWARE_CHECK=0
SKIP_NODE_UPDATE=0
#: Install Node-RED when the board does not have it (1) or leave it out (0).
#: Installed OFF either way -- the Board I/O page turns it on (8b).
INSTALL_NODERED=1
#: The row store. A board that cannot reach Postgres still runs -- tables can
#: be designed, nothing is stored -- which is right for a deployment pointing
#: several boards at one server, and wrong as the default for an appliance
#: that advertises a Database page.
INSTALL_DB=1
DB_HOST=""
#: The MQTT broker. The workflow's MQTT nodes point at 127.0.0.1:1883 out of
#: the box, and a box with nothing listening there turns every one of them
#: into "connection refused". Local only (see 7e).
INSTALL_MQTT=1
#: What the board answers to, and therefore what <name>.local resolves to.
#: Empty = IRIV-EdgeAI-Studio, the same as
#: core/services/hostinfo.default_hostname(). Every box called "iriv" made
#: iriv.local a race that avahi settled in boot order (iriv-2, iriv-3)
#: (docs/team/kokoci/2026-10-05-hostname.md).
SET_HOSTNAME=1
WANT_HOSTNAME=""
HOSTNAME_EXPLICIT=0
#: The hotspot is a way onto THE BOX, not through it onto the site's wired
#: LAN. 1 = leave NetworkManager's shared-mode forwarding open (8b).
AP_FORWARD=0
#: Services the OS image ships that an appliance does not use (8b). Each one
#: off by default, each with a flag to keep it.
KEEP_VNC=0
KEEP_RPCBIND=0
KEEP_SERIAL_CONSOLE=0
REAL_TARGET=""

VENV=""      # set once TARGET is final
PY=""

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '\033[33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/install.sh [options]

Installs IRIV EdgeAI Studio as a customer appliance: hub + one master per
built-in system, and optionally the device agent, all on this box.

Options:
  --target <dir>     where to install (default: /opt/irivai-systor)
  --adapter <m:C>    also install the device agent with this adapter.
                     Omit to install the dashboard only.
  --user <name>      service account (default: cytron)
  --hub-port <n>     default 5100. Written as a setting the box keeps
                     (Settings -> Network -> Software port can change it
                     later); a box that already has one keeps it.
  --master-port <n>  first master's port, default 5110 (each system gets +1)
  --agent-port <n>   default 5101
  --no-mqtt          do not install the MQTT broker (Mosquitto). The MQTT
                     nodes then need a broker elsewhere, named by address.
  --keep-node-modules
                     leave frontend/node_modules in the SOURCE checkout after
                     the build (default: pruned -- it is hundreds of MB that
                     nothing on an appliance ever reads again)
  --allow-ap-forwarding
                     let devices on the box's hotspot be ROUTED onto the
                     wired LAN (NetworkManager's shared-mode default). Off
                     by default: the hotspot reaches the box itself (the
                     dashboard, the systems, live video) and nothing behind
                     it. Only for a site that wants the hotspot as a way
                     into its plant network, knowing anyone with the key
                     gets one.
  --keep-vnc         leave the VNC desktop server (wayvnc) running. Off by
                     default: a remote desktop on 5900 is a second way in
                     that this product does not need.
  --keep-rpcbind     leave rpcbind (NFS's port mapper, 111) running.
  --keep-serial-console
                     leave the Linux console on the UART (ttyAMA10). Off by
                     default: that UART is the RS485 port Modbus RTU uses,
                     and a login prompt on it answers the PLC. Takes
                     effect at the next reboot.
  --skip-hardening   do not harden or check this box (passwordless sudo,
                     sshd, root's password, the hotspot isolation), and
                     print "Done" even with problems. A bench box only.
  --skip-node-update
                     leave Node.js and Node-RED at the versions already on the
                     box. By default the installer updates Node.js to the
                     latest LTS and Node-RED (if the board has it) to its
                     latest release; Node-RED stays on or off as it was.
  --no-nodered       do not install Node-RED on a board that does not have
                     it. By default it is installed (and left OFF: Settings ->
                     Board I/O turns it on).
  --skip-hardware-check
                     install even when the hardware check fails: not an IRIV
                     Edge AI device (no Compute Module 5, no ATECC), or no AI
                     HAT. A bench or dev box only.
  --no-service       build and copy, but write no systemd unit and start
                     nothing. Use to rehearse an install, or to check on a
                     running box what WOULD ship, without touching it.
  -h, --help

EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)       TARGET="${2:-}"; shift 2 ;;
        --adapter)      ADAPTER="${2:-}"; shift 2 ;;
        --user)         SERVICE_USER="${2:-}"; shift 2 ;;
        --hub-port)     HUB_PORT="${2:-}"; shift 2 ;;
        --master-port)  MASTER_PORT="${2:-}"; shift 2 ;;
        --agent-port)   AGENT_PORT="${2:-}"; shift 2 ;;
        --db-host)      DB_HOST="${2:-}"; shift 2 ;;
        --no-database)  INSTALL_DB=0; shift ;;
        --no-mqtt)      INSTALL_MQTT=0; shift ;;
        --hostname)     WANT_HOSTNAME="${2:-}"; HOSTNAME_EXPLICIT=1; shift 2 ;;
        --no-hostname)  SET_HOSTNAME=0; shift ;;
        --allow-ap-forwarding) AP_FORWARD=1; shift ;;
        --keep-vnc)     KEEP_VNC=1; shift ;;
        --keep-rpcbind) KEEP_RPCBIND=1; shift ;;
        --keep-serial-console) KEEP_SERIAL_CONSOLE=1; shift ;;
        --keep-node-modules) KEEP_NODE_MODULES=1; shift ;;
        --skip-hardening)    SKIP_HARDENING=1; shift ;;
        --skip-hardware-check) SKIP_HARDWARE_CHECK=1; shift ;;
        --skip-node-update)  SKIP_NODE_UPDATE=1; shift ;;
        --no-nodered)        INSTALL_NODERED=0; shift ;;
        --no-service)        INSTALL_SERVICE=0; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              usage >&2; die "unknown argument: $1" ;;
    esac
done

[[ -n "$ADAPTER" && "$ADAPTER" != *:* ]] && die "--adapter must be module:Class, got '${ADAPTER}'"
for p in "$HUB_PORT" "$MASTER_PORT" "$AGENT_PORT"; do
    [[ "$p" =~ ^[0-9]+$ ]] || die "ports must be numbers, got '${p}'"
done
if [[ $INSTALL_SERVICE -eq 1 && $EUID -ne 0 ]]; then
    die "this installs systemd units and a service account -- re-run with sudo, or pass --no-service"
fi
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "no requirements.txt in ${SRC_DIR} -- run this from inside the checkout"

VENV="${TARGET}/.venv"
PY="${VENV}/bin/python"

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------

say "Checking prerequisites"

command -v python3 >/dev/null || die "python3 not found"
# 3.11 is the floor twice over: core/config.py uses stdlib tomllib, and the
# routes use `str | None` annotations FastAPI resolves at import.
python3 - <<'PY' || die "Python 3.11+ required (tomllib + PEP 604 annotations)"
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
note "python3: $(python3 --version)"

if ! python3 -c "import venv" 2>/dev/null; then
    command -v apt-get >/dev/null || die "python3-venv missing and this is not an apt host"
    apt-get update && apt-get install -y python3-venv git curl rsync
fi
command -v rsync >/dev/null || die "rsync not found -- apt-get install rsync"

# ---------------------------------------------------------------------------
# 1a. Hardware gate -- Hailo accelerator
# ---------------------------------------------------------------------------
#
# Two things are checked HERE, before anything is downloaded or written, so a
# board that does not qualify gets a plain answer instead of a half-finished
# install. Nothing is sent anywhere -- it reads the board in front of it.
#
#   1. RUN ONLY IN IRIV EDGE AI DEVICES: the box must be an IRIV Edge AI device -- a Raspberry
#      Pi Compute Module 5, OR an ATECC secure element answering on I2C
#      (core/services/device_gate.py, the same check sign-in and Start use).
#   2. An AI HAT (a Hailo accelerator: 8, 8L or 10H) must be fitted. To run
#      this software, you need an AI HAT.
#
# --skip-hardware-check installs anyway, for a bench or dev box. The running
# software still refuses sign-in on a non-IRIV device unless that box also
# opts out (IRIV_HW_CHECK=off, or [hardware] enforce = false).
say "Hardware check"
# The ATECC is asked over /dev/i2c-*, which needs the i2c-dev module; a fresh
# image may not have loaded it yet. Harmless where it is already loaded.
modprobe i2c-dev 2>/dev/null || true
DEV_RC=0
python3 - "${SRC_DIR}" <<'PY' || DEV_RC=$?
import os
import sys

sys.path.insert(0, sys.argv[1])
# The installer decides on its own flag (--skip-hardware-check), so the
# runtime opt-out must not short-circuit the answer printed here.
os.environ.pop("IRIV_HW_CHECK", None)
from core.services import device_gate                           # noqa: E402

cm5 = device_gate.is_cm5()
atecc = False if cm5 else device_gate.has_atecc()
print("   Compute Module 5  : %s" % ("found" if cm5 else "not found"))
if not cm5:
    print("   ATECC secure elem.: %s" % ("found" if atecc else "not found"))
sys.exit(0 if (cm5 or atecc) else 1)
PY
if [[ $DEV_RC -ne 0 ]]; then
    if [[ $SKIP_HARDWARE_CHECK -eq 1 ]]; then
        warn "RUN ONLY IN IRIV EDGE AI DEVICES: this is not an IRIV Edge AI device -- continuing because --skip-hardware-check was given."
    else
        die "RUN ONLY IN IRIV EDGE AI DEVICES: this box has no Compute Module 5 and no ATECC secure element, so it is not an IRIV Edge AI device and the studio will not install here."
    fi
fi
HW_RC=0
python3 - "${SRC_DIR}" <<'PY' || HW_RC=$?
import sys

sys.path.insert(0, sys.argv[1])
from core.services import builtin_models                        # noqa: E402

hailo = bool(builtin_models.detect_arch_uncached())
print("   AI HAT (Hailo)    : %s" % ("found" if hailo else "NOT FOUND"))
sys.exit(0 if hailo else 1)
PY
if [[ $HW_RC -ne 0 ]]; then
    if [[ $SKIP_HARDWARE_CHECK -eq 1 ]]; then
        warn "AI HAT REQUIRED: no AI HAT found -- continuing because --skip-hardware-check was given."
    else
        die "AI HAT REQUIRED: to run this software, you need an AI HAT. No AI HAT (Hailo accelerator) was found on this box. Fit one and run the installer again (or pass --skip-hardware-check on a bench/dev box)."
    fi
fi

# ---------------------------------------------------------------------------
# 1b. System packages
# ---------------------------------------------------------------------------
#
# Everything the product SHELLS OUT TO or imports from the system Python. None
# of it can come from pip: v4l2-ctl and nmcli are programs, the GStreamer
# elements are plugins, and OpenCV/PyGObject/hailo_platform are the distro's
# builds against the distro's libraries.
#
# This list exists because it was previously nobody's: the packages were
# installed BY HAND on each board, so a box installed with this script came up
# with no camera mode list (no v4l2-ctl), no WebRTC publish (no rtspclientsink,
# no gi), no GigE camera (no aravissrc) and no Network page (no nmcli) -- each
# one degrading quietly, which is the worst way for a missing dependency to
# report itself.
#
#   python3-opencv      the detection loop's frames (systems/*/app/camera.py)
#   python3-numpy       what OpenCV hands back
#   v4l-utils           v4l2-ctl -- the resolutions and frame rates a USB
#                       camera actually offers, on the Camera Setup page
#   ffmpeg              ffprobe -- the same two questions for an RTSP camera,
#                       which has no v4l2 device to ask
#   python3-gi
#   gir1.2-gstreamer-1.0
#   gstreamer1.0-tools
#   gstreamer1.0-plugins-base
#   gstreamer1.0-plugins-good
#   gstreamer1.0-plugins-bad    openh264enc: a CM5 has no hardware encoder
#   gstreamer1.0-libav
#   gstreamer1.0-rtsp   rtspclientsink -- the element that PUBLISHES to the
#                       gateway. Not a dependency of any of the above, and its
#                       absence is why a board can look fully installed and
#                       still never produce a WebRTC stream.
#   libaravis-0.8-0     aravissrc: the element that opens a GigE Vision camera
#   gir1.2-aravis-0.8
#   aravis-tools-cli    arv-tool -- names the cameras Aravis can open. Split
#                       from the plugin on Debian 13, so it is asked for by
#                       name or it is not there.
#   python3-smbus2, i2c-tools     the OLED panel and the board's I2C header
#   avahi-utils         avahi-publish-service / avahi-browse: the box
#                       advertises itself as _irivai._tcp (discovery_mdns.py)
#                       and learns the .local name avahi actually gave it
#                       (hostname.local_name); without it a box is found by
#                       the /24 sweep only
#   network-manager     nmcli -- the Network page configures the box's own
#                       interfaces through it
#   nftables            nft -- the box's own fixed rules (8b): no routing
#                       from the hotspot onto the wired LAN. Not on every
#                       Pi OS Lite image.
#   libpango…/libgdk-pixbuf       WeasyPrint's system libraries (the styled
#                       PDF report; it falls back to matplotlib without them)
APT_PACKAGES=(
    python3-venv python3-pip git curl rsync avahi-daemon avahi-utils
    python3-opencv python3-numpy v4l-utils ffmpeg
    python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-tools
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good
    gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-rtsp
    libaravis-0.8-0 gir1.2-aravis-0.8 aravis-tools-cli
    python3-smbus2 i2c-tools network-manager nftables
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0
    # lsof: the fast path for "who holds this port" when a master is
    # stopped (core/services/portpids.py); the code walks /proc without it,
    # this is belt and braces on a trimmed image.
    lsof
    # arping -D: the Network page's address conflict test and the OLED's
    # IP CONFLICT line (core/services/ipconflict.py, panel_ipcheck.py); the
    # iputils one, whose flags the code uses.
    iputils-arping
    # The rest of what the code runs, named rather than assumed from the OS
    # image (tests/smoke_deps.py reads the code for them):
    #   iputils-ping  ping -- gateway and uplink checks (routeheal.py,
    #                 uplinkguard.py), the conflict test's fallback
    #   iw            the radio's own view (`iw dev wlan0 ...`) when a Wi-Fi
    #                 problem is diagnosed over ssh
    #   gpiod         gpioinfo/gpioget -- the board's terminals (board_io.py)
    #   pciutils      lspci -- which Hailo chip, when hailortcli cannot say
    #   openssl       a certificate fallback when the system has no
    #                 python3-cryptography
    #   fdisk         sfdisk -- the New storage popup's format (storage_setup.py);
    #                 its own package since Debian 12
    iputils-ping iw gpiod pciutils openssl fdisk
)

if command -v apt-get >/dev/null && [[ $EUID -eq 0 ]]; then
    say "System packages"
    MISSING=()
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q '^install ok installed$' \
            || MISSING+=("$pkg")
    done
    if [[ ${#MISSING[@]} -eq 0 ]]; then
        note "all present"
    else
        note "installing: ${MISSING[*]}"
        apt-get update -qq || warn "apt-get update failed -- trying the install anyway"
        # Not fatal one by one: a package name differs between Debian releases
        # (aravis-tools-cli is aravis-tools on 12), and losing the WHOLE install
        # over one renamed package is worse than losing the feature it carries.
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING[@]}" \
            || for pkg in "${MISSING[@]}"; do
                   DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" \
                       || warn "could not install ${pkg} -- the feature it carries will be unavailable"
               done
    fi

    # The NPU's runtime. Vendor-supplied, from the Raspberry Pi archive, so it
    # is asked for only where it EXISTS: on any other Debian this is not a
    # missing package, it is a different way of installing Hailo, and failing
    # the appliance install over it would be wrong.
    if ! python3 -c "import hailo_platform" 2>/dev/null; then
        # Captured first, matched second. Under `set -o pipefail` a
        # `... | grep -q ...` condition reports FAILURE when grep
        # matches early and the writer dies of SIGPIPE -- the test then
        # reads false precisely because it succeeded. apt-cache prints
        # four lines and usually finishes before grep closes the pipe,
        # so this escaped by luck rather than by design; the same shape
        # against systemctl's 374 lines silently skipped a whole step.
        hailo_policy="$(apt-cache policy hailo-all 2>/dev/null || true)"
        if [[ "$hailo_policy" == *"Candidate:"* && "$hailo_policy" != *"Candidate: (none)"* ]]; then
            note "installing hailo-all (HailoRT + driver)"
            DEBIAN_FRONTEND=noninteractive apt-get install -y hailo-all \
                || warn "hailo-all failed to install -- detection will run on CPU"
        else
            warn "no hailo_platform and no hailo-all package -- install HailoRT for this board by hand, or detection runs on CPU"
        fi
    fi
else
    note "not root or not an apt host -- skipping system packages"
    for tool in v4l2-ctl nmcli gst-launch-1.0; do
        command -v "$tool" >/dev/null || warn "${tool} not found -- install the system packages listed in scripts/install.sh"
    done
fi

# ---------------------------------------------------------------------------
# 2. Which systems are built in
# ---------------------------------------------------------------------------

say "Built-in systems (config/builtin.toml)"

BUILTIN_SLUGS="$(python3 - "$SRC_DIR" <<'PY'
import pathlib, sys, tomllib
root = pathlib.Path(sys.argv[1])
path = root / "config" / "builtin.toml"
if not path.is_file():
    sys.exit("config/builtin.toml is missing -- it decides what ships")
data = tomllib.loads(path.read_text(encoding="utf-8"))
slugs = [str(s).strip() for s in data.get("systems", []) if str(s).strip()]
if not slugs:
    sys.exit("config/builtin.toml lists no systems -- an appliance with no "
             "system would boot to an empty hub")
missing = [s for s in slugs if not (root / "systems" / s / "system.py").is_file()]
if missing:
    sys.exit(f"config/builtin.toml names systems that are not in this checkout: "
             f"{', '.join(missing)}")
print(" ".join(slugs))
PY
)" || die "$BUILTIN_SLUGS"

for slug in $BUILTIN_SLUGS; do note "bundling ${slug}"; done

# ---------------------------------------------------------------------------
# 3. Service account
# ---------------------------------------------------------------------------

if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "Service account: ${SERVICE_USER}"
    if id -u "$SERVICE_USER" >/dev/null 2>&1; then
        note "exists"
    else
        # No login shell, no home: this account runs the product and owns its
        # tokens.
        useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
        note "created"
    fi

    # HailoRT writes a cache and a log into the account's home and errors on
    # every device open without one. Made here rather than by useradd so a
    # pre-existing account gets it too.
    install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" "/home/${SERVICE_USER}"

    # The hardware this product drives is group-owned, and a --system account
    # starts in none of those groups. Skipping this does not fail loudly: the
    # camera picker listed EVERY /dev/video* node (19 of them on a CM5),
    # because a node that cannot be opened cannot be asked what it is, and an
    # unanswerable node is listed rather than hidden -- so "no permission"
    # read as "lots of cameras", and no real camera could be opened either.
    #
    #   video    /dev/video* -- USB cameras
    #   render   the GPU nodes
    #   gpio     the board's terminals, the USER button, the buzzer
    #   i2c      the OLED front panel
    #   spi      the board's SPI header
    #   dialout  serial ports (Modbus RTU)
    #   plugdev  hot-plugged devices
    for grp in video render gpio i2c spi dialout plugdev; do
        getent group "$grp" >/dev/null 2>&1 || continue
        usermod -aG "$grp" "$SERVICE_USER"
    done
    note "groups: $(id -nG "$SERVICE_USER" | tr ' ' ',')"
fi

# ---------------------------------------------------------------------------
# 4. Build the frontend -- customer mode -- BEFORE copying
# ---------------------------------------------------------------------------
#
# Built in the source checkout, then the built bundle is copied and the source
# is not. An appliance has no reason to carry a Vite project or a node_modules
# tree, and every megabyte of eMMC it does not use is one the event store can.

say "Frontend (customer build)"

# NODE.JS AND NODE-RED, KEPT CURRENT. Node.js comes from NodeSource's LTS repo:
# setup_lts.x points apt at the CURRENT long-term-support line, and installing
# nodejs then moves the box onto it (a patch release within the line is picked
# up the same way). Node-RED, where the board has it, is updated to its latest
# release with npm. Neither failing stops the install -- the version already
# on the box is kept and said -- except that a box with no Node.js at all
# cannot build the frontend. --skip-node-update leaves both as they are.
if ! command -v node >/dev/null 2>&1 || [[ $SKIP_NODE_UPDATE -eq 0 ]]; then
    if command -v apt-get >/dev/null; then
        if command -v node >/dev/null 2>&1; then
            note "node $(node --version) found -- updating to the latest LTS (NodeSource)"
        else
            note "node not found -- installing Node.js LTS (NodeSource)"
        fi
        if curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
           && apt-get install -y nodejs; then
            :
        else
            command -v node >/dev/null 2>&1 \
                || die "node/npm not found and could not be installed (is this box online?)"
            warn "could not update Node.js -- keeping $(node --version)"
        fi
    else
        command -v node >/dev/null 2>&1 || die "node/npm not found and this is not an apt host"
    fi
fi
note "node: $(node --version)   npm: $(npm --version)"

if [[ $SKIP_NODE_UPDATE -eq 0 ]]; then
    if command -v node-red >/dev/null 2>&1; then
        nr_before="$(node-red --version 2>/dev/null | head -n1 || true)"
        note "${nr_before:-Node-RED} found -- updating to the latest release"
        if npm install -g --unsafe-perm node-red@latest >/dev/null 2>&1; then
            # A flow's own nodes can carry native modules, built for one Node
            # major: rebuilt in each user's Node-RED folder after the update.
            for nr_dir in /home/*/.node-red; do
                [[ -f "${nr_dir}/package.json" ]] || continue
                nr_user="$(stat -c %U "${nr_dir}" 2>/dev/null || true)"
                [[ -n "$nr_user" ]] || continue
                ( cd "${nr_dir}" && sudo -u "$nr_user" npm rebuild >/dev/null 2>&1 ) \
                    || warn "could not rebuild the Node-RED nodes in ${nr_dir}"
            done
            note "Node-RED: now $(node-red --version 2>/dev/null | head -n1 || echo unknown) (left as it was: on or off)"
        else
            warn "could not update Node-RED -- keeping ${nr_before:-the installed version}"
        fi
    elif [[ $INSTALL_NODERED -eq 1 ]]; then
        # NODE-RED, INSTALLED WHERE THE BOARD HAS NONE (an OS image without
        # it). The board's Node-RED is a feature the Board I/O page turns on,
        # and that page has nothing to turn on without it. Installed the way
        # Raspberry Pi OS ships it: its own `nodered` account, a nodered
        # systemd unit, flows in /home/nodered/.node-red -- so the update step
        # above, the sudoers rule (enable/disable --now nodered) and the
        # Board I/O switch all find it where they look. Left OFF: the editor
        # on 1880 runs code and has no password until one is set (8b).
        note "Node-RED: not installed -- installing the latest release"
        if npm install -g --unsafe-perm node-red@latest >/dev/null 2>&1; then
            id -u nodered >/dev/null 2>&1 \
                || useradd --system --create-home --home-dir /home/nodered \
                           --shell /usr/sbin/nologin nodered
            # GPIO / serial access for flows that drive the board's I/O.
            for g in gpio dialout i2c spi; do
                getent group "$g" >/dev/null && usermod -aG "$g" nodered
            done
            install -d -o nodered -g nodered -m 750 /home/nodered/.node-red
            nr_bin="$(command -v node-red)"
            cat > /etc/systemd/system/nodered.service <<UNIT
[Unit]
Description=Node-RED
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nodered
Group=nodered
WorkingDirectory=/home/nodered
Environment="NODE_OPTIONS=--max_old_space_size=512"
ExecStart=${nr_bin} --userDir /home/nodered/.node-red
Restart=on-failure
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
UNIT
            systemctl daemon-reload
            note "Node-RED: $(node-red --version 2>/dev/null | head -n1 || echo installed) (off; turn it on in Settings -> Board I/O)"
        else
            warn "could not install Node-RED (is this box online?) -- the rest of the install carries on"
        fi
    else
        note "Node-RED: not installed (--no-nodered)"
    fi
fi

( cd "${SRC_DIR}/frontend" \
    && { [[ -d node_modules ]] && npm ci >/dev/null 2>&1 || npm install; } \
    && npm run build ) \
  || die "frontend build failed -- see the npm output above"

[[ -f "${SRC_DIR}/core/static/app/index.html" ]] \
    || die "the build produced no core/static/app/index.html"

note "built core/static/app"

# ---------------------------------------------------------------------------
# 5. Copy the product
# ---------------------------------------------------------------------------

say "Installing into ${TARGET}"

# Nothing at ${TARGET} yet = a first install, decided HERE, before anything is
# put there. Only a first install starts without the reference "demo" system on
# its hub (see "No demo card" below); a re-install over a box that has it keeps
# it.
FRESH_INSTALL=0
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] && FRESH_INSTALL=1

REAL_TARGET="$TARGET"
install -d -m 755 "$TARGET"

# The scripts that BUILD a box, as against the ones that RUN on it.
#
# These six are how a board is made and re-made, which is bench work here: a
# customer with a broken box opens a ticket and the board comes back, it is
# never rebuilt in the field. Left on the appliance they are a one-command way
# to stand this software up on any other hardware, so they stop at the workshop
# door. The checkout this script runs from keeps its copies -- that is the
# point, the next board is built from the same checkout.
#
# What stays, and why each one has to: db_console_role.py and the other root
# helpers, installed to /usr/local/lib/irivai and run through sudo; and the
# udev, modprobe and mediamtx CONFIG the running box reads. Removing any of
# those breaks a box that is already in service.
BENCH_ONLY=(
    scripts/install.sh
    scripts/setup_pi.sh
    scripts/run_all.sh
    scripts/install_service.sh
    scripts/setup_nvme.sh
    scripts/mediamtx/install.sh
    scripts/publish_update.sh
)

# Installing a checkout ONTO ITSELF would now delete this very file while bash
# is still reading it, and bash reads a script as it goes -- the tail would run
# as whatever landed at that offset, or not at all. Harmless before the list
# above existed, fatal after it, so it is refused outright.
if [[ "$(cd "$SRC_DIR" && pwd -P)" == "$(cd "$REAL_TARGET" 2>/dev/null && pwd -P || echo "")" ]]; then
    die "the source checkout and the install target are the same directory (${SRC_DIR}) -- install from a checkout that is not the target"
fi

# docs/ is the project's own documentation (design, planning, test reports):
# for the repository, never for a customer's box, and nothing at runtime reads
# it. The release package (make_release.py) has never shipped it either.
#
# The owner's tooling, removed OUTRIGHT before the copy -- not left to
# --exclude and --delete together, which do NOT do this: rsync's delete
# pass only removes files it considered for transfer, and an --exclude'd
# path is invisible to that pass, so it is never a candidate for deletion
# in the first place. On a fresh /opt install there was nothing here to
# leave behind, which is why this went unnoticed -- CONVERTING an existing
# developer-style checkout in place (this box's own history, before it was
# ever installed with this script) is exactly the case where core/dev/ and
# scripts/dev/ were already sitting in the target, and would have stayed.
rm -rf "${TARGET}/core/dev" "${TARGET}/scripts/dev" "${TARGET}/frontend/node_modules" \
       "${TARGET}/config.json" "${TARGET}/docs"
# Same reasoning for the bench scripts: a box installed by an earlier
# version of this installer already has them, and an --exclude alone would
# never take them away.
for bench in "${BENCH_ONLY[@]}"; do
    rm -f "${TARGET}/${bench}"
done

# Each bench script as its own --exclude. Every pattern holds a slash, so
# rsync anchors it at the transfer root instead of matching the basename
# anywhere in the tree -- a bare "install.sh" would also catch any future
# file of that name under a subdirectory nobody thought about here.
BENCH_EXCLUDE=()
for bench in "${BENCH_ONLY[@]}"; do
    BENCH_EXCLUDE+=(--exclude "$bench")
done

# --delete so a re-install cannot leave behind anything else an earlier
# version had.
rsync -a --delete \
    --exclude '.git' \
    --exclude '.github' \
    --exclude '.claude' \
    --exclude '.venv' \
    --exclude 'core/dev' \
    --exclude 'scripts/dev' \
    --exclude 'frontend' \
    --exclude 'systems' \
    --exclude 'var' \
    --exclude 'logs' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'config/*.local.toml' \
    --exclude 'config.json' \
    --exclude '/docs' \
    "${BENCH_EXCLUDE[@]}" \
    "${SRC_DIR}/" "${TARGET}/"

# The .env file: install-time defaults (hostname, hotspot address and key,
# store paths, the hardware check -- core/envfile.py). Kept OUTSIDE the
# release folder at /etc/irivai/studio.env so an update keeps it; written
# once, from the checkout's .env if it has one, else from .env.example, and
# never overwritten afterwards (edit it there, then restart irivai-hub).
install -d -m 755 /etc/irivai
if [[ ! -f /etc/irivai/studio.env ]]; then
    if [[ -f "${SRC_DIR}/.env" ]]; then
        install -m 600 "${SRC_DIR}/.env" /etc/irivai/studio.env
        note "settings: /etc/irivai/studio.env written from .env"
    elif [[ -f "${SRC_DIR}/.env.example" ]]; then
        install -m 600 "${SRC_DIR}/.env.example" /etc/irivai/studio.env
        note "settings: /etc/irivai/studio.env written from .env.example"
    fi
    chown "${SERVICE_USER}:${SERVICE_USER}" /etc/irivai/studio.env 2>/dev/null || true
fi
rm -f "${TARGET}/.env"          # the box reads /etc/irivai/studio.env

# Only the built-in systems, named one by one -- never the whole systems/ dir,
# which on a developer box also holds workbenches and unpublished work.
install -d -m 755 "${TARGET}/systems"
for slug in $BUILTIN_SLUGS; do
    rsync -a --delete \
        --exclude '__pycache__' --exclude '*.pyc' \
        --exclude 'data' --exclude 'models' \
        --exclude 'config.local.toml' \
        "${SRC_DIR}/systems/${slug}/" "${TARGET}/systems/${slug}/"
    note "copied systems/${slug}"
done

# Belt and braces: assert the exclusions actually held. A typo in an --exclude
# is silent, and the whole point of this script is what is NOT here.
for forbidden in core/dev scripts/dev frontend/node_modules config.json docs \
                 "${BENCH_ONLY[@]}"; do
    [[ -e "${TARGET}/${forbidden}" ]] \
        && die "${forbidden} reached the install -- refusing to continue"
done
note "core/dev, scripts/dev, frontend source, config.json, docs: absent"
note "installer and bench scripts: absent (${#BENCH_ONLY[@]} of them)"

# ---------------------------------------------------------------------------
# 6. Virtualenv
# ---------------------------------------------------------------------------

say "Python environment"

# --system-site-packages, deliberately. OpenCV, PyGObject and hailo_platform
# are the DISTRO's builds against the distro's libraries -- there is no pip
# wheel for hailo_platform at all, and an isolated venv simply cannot see any
# of them. Without this the app imports none of them and degrades: no frames,
# no WebRTC publish, no NPU. Everything the product owns is still pinned in
# requirements.txt and installed into the venv, which keeps precedence.
[[ -x "$PY" ]] || python3 -m venv --system-site-packages "$VENV"
# An older install made an ISOLATED venv; it stays isolated for ever, because
# the flag is only read at creation. Flipped in place rather than rebuilt: it
# is one line of pyvenv.cfg, and rebuilding would re-download every dependency
# on a box that may be updating over a phone connection.
if [[ -f "${VENV}/pyvenv.cfg" ]] \
   && grep -qi '^include-system-site-packages *= *false' "${VENV}/pyvenv.cfg"; then
    sed -i 's/^include-system-site-packages *= *false/include-system-site-packages = true/I' \
        "${VENV}/pyvenv.cfg"
    note "venv now sees the system packages (OpenCV, PyGObject, HailoRT)"
fi
# --no-cache-dir: pip's cache is written once and read never on an appliance.
"${VENV}/bin/pip" install --quiet --no-cache-dir --upgrade pip
"${VENV}/bin/pip" install --quiet --no-cache-dir -r "${TARGET}/requirements.txt"
note "dependencies installed"

# The store's preferred home is /mnt/nvme/cytron/store (core/config.py
# STORE_CANDIDATES), and "first writable candidate wins" on a fresh box. A
# freshly set-up NVMe is mounted root-owned, so the service account could
# not create that folder and the store fell back to its home directory on
# the 29 GB system disk -- recordings and clips beside the OS, which is
# exactly what the drive was fitted to avoid. Seen on the first two customer
# boards. Made writable HERE, before the store root is first resolved below,
# so the very first resolution lands on the drive.
if [[ $INSTALL_SERVICE -eq 1 && -d /mnt/nvme && $(findmnt -no TARGET /mnt/nvme 2>/dev/null) == /mnt/nvme ]]; then
    install -d -m 2775 -o "$SERVICE_USER" -g "$SERVICE_USER" /mnt/nvme/cytron /mnt/nvme/cytron/store
    note "store root: /mnt/nvme/cytron/store (the NVMe)"
elif [[ $INSTALL_SERVICE -eq 1 ]] && grep -qsE '^[^#]\S*\s+/mnt/nvme\s' /etc/fstab; then
    # Promised by fstab, not delivered by the kernel (a UUID that no longer
    # matches the drive is the usual cause). Said in the drive's own words --
    # the store line below would otherwise call the NVMe "empty".
    warn "/mnt/nvme is listed in /etc/fstab but NOT mounted -- the store falls back to the home directory on the system disk. Check the UUID (blkid) against the fstab line and run: sudo mount -a"
fi
# Resolve the store root once now, as the service user, so the first resolution
# (and its directories) happens with the right ownership.
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    STORE_ROOT="$(cd "$TARGET" && sudo -u "$SERVICE_USER" "$PY" -c 'from core import config; print(config.store_dir())' 2>/dev/null | tail -n 1 || true)"
    [[ -n "$STORE_ROOT" ]] && note "store root: ${STORE_ROOT}"
fi

# No demo card. systems/demo is the reference app copied into every system the
# operator creates, so it stays in the install; but a box that has just been
# set up should open on an empty hub, not on a "demo" system nobody made.
# `enabled` here layers over config/systems.toml (lists replace, core/config.py
# _merge) and only governs the built-in folder: systems the operator creates
# live elsewhere and are never filtered by it. Local, so an update never undoes
# it, and written only on a first install and only if nothing is there already,
# so a box that already shows demo keeps it.
if [[ $FRESH_INSTALL -eq 1 && ! -e "${TARGET}/config/systems.local.toml" ]]; then
    # Also on a first install: reach the box at one address with no port (the
    # hub at /, each system at /s/<slug>), the reverse-proxy access mode. The
    # hub makes the port 80 redirect at its start and, if it cannot, falls
    # back to port addresses and says so (webaccess.ensure_redirect).
    printf '# First install: the reference "demo" system is not listed on the hub.\nenabled = []\n\n[hub]\naccess = "proxy"\n' \
        > "${TARGET}/config/systems.local.toml"
    note "no demo card on the hub (config/systems.local.toml: enabled = [])"
    note "systems open at /s/<name> on port 80 (config/systems.local.toml [hub].access = proxy)"
fi

if [[ $KEEP_NODE_MODULES -eq 0 && -d "${SRC_DIR}/frontend/node_modules" ]]; then
    rm -rf "${SRC_DIR}/frontend/node_modules"
    note "pruned frontend/node_modules from the source checkout"
fi

# ---------------------------------------------------------------------------
# 7. Ownership and secrets
# ---------------------------------------------------------------------------

say "Permissions"

if [[ $INSTALL_SERVICE -eq 1 ]]; then
    # The real directory, not the link: chown -R does not follow a symlink
    # argument, so recursing through ${TARGET} would change the link's own
    # ownership and nothing underneath it.
    chown -h "${SERVICE_USER}:${SERVICE_USER}" "$TARGET" 2>/dev/null || true
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$REAL_TARGET"
fi
# config/ must be WRITABLE, not just readable: pairing a device and the first
# login both create *.local.toml through config.save_local().
chmod 755 "${TARGET}/config"
shopt -s nullglob
for f in "${TARGET}"/config/*.local.toml; do
    chmod 600 "$f"
    [[ $INSTALL_SERVICE -eq 1 ]] && chown "${SERVICE_USER}:${SERVICE_USER}" "$f"
done
shopt -u nullglob

# Camera slot pinning. The kernel numbers /dev/video* by ENUMERATION ORDER, so
# which camera is "video0" changes with plug order and with every
# re-enumeration of a flaky link. These rules key on the soldered port path and
# publish /dev/cam-usb<slot>, which is what makes "camera 0" mean "the camera
# in socket 0" across replugs -- and what the Board I/O and Camera Setup
# socket maps read to say which socket a camera is in. Without them the map
# draws two empty sockets on a board with a camera plugged into one.
if [[ $INSTALL_SERVICE -eq 1 && -f "${SRC_DIR}/scripts/udev/83-cytron-cam-slots.rules" ]]; then
    install -d -m 755 /etc/udev/rules.d
    install -m 644 "${SRC_DIR}/scripts/udev/83-cytron-cam-slots.rules" \
            /etc/udev/rules.d/83-cytron-cam-slots.rules
    udevadm control --reload >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=video4linux >/dev/null 2>&1 || true
    note "camera slot rules: /dev/cam-usb0, /dev/cam-usb1"
fi

# UVC driver settings. A big uncompressed camera (an Arducam B0499 does YUYV
# 1456x1088 at 44fps and nothing else) stalls under load, and the stock
# five-second driver timeout turned every stall into a ten-second frozen
# picture -- the app cannot recover while its capture thread is blocked inside
# that wait. The file says what was measured; applied on the next module load,
# and reloaded here when nothing has the camera open.
if [[ $INSTALL_SERVICE -eq 1 && -f "${SRC_DIR}/scripts/modprobe/irivai-uvcvideo.conf" ]]; then
    install -d -m 755 /etc/modprobe.d
    install -m 644 "${SRC_DIR}/scripts/modprobe/irivai-uvcvideo.conf" \
            /etc/modprobe.d/irivai-uvcvideo.conf
    # Best effort: with a camera streaming, the module is busy and the
    # settings wait for the next boot. Never fatal -- a failed reload must not
    # fail an install.
    if modprobe -r uvcvideo >/dev/null 2>&1; then
        modprobe uvcvideo >/dev/null 2>&1 || true
        note "uvcvideo settings applied (timeout=1500 nodrop=0)"
    else
        note "uvcvideo settings installed -- they apply on the next reboot"
    fi
fi

# The product's own model folder (core/config.py BUILTIN_MODEL_DIR). Beside
# the releases, never inside one: an update replaces the release directory and
# prunes old ones, and models kept in there would be downloaded again after
# every update. Kept apart from the operator's /mnt/nvme/model drop folder so a
# catalogue model does not also list itself as a custom one.
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" /opt/irivai-models
    note "built-in models: /opt/irivai-models"
fi

# The operator's drop folder (core/config.py _nvme_model_dir). The hub already
# writes here fine as SERVICE_USER, which is all core/config.py checks -- but
# an operator moving a model folder onto the box directly (scp, a USB stick
# mounted and copied over) logs in as whoever is running this installer, not
# as SERVICE_USER, and a plain 755 directory shut that out with a permission
# denied that had nothing to do with the product. Reported on a box where the
# login account (pi) and the service account (cytron) are different people.
#
# Fixed the same way the hardware groups above are: SERVICE_USER's own group
# gets write + setgid (so a file dropped in keeps a group the service can
# read), and whoever actually ran `sudo ./install.sh` -- the real operator,
# not root -- is added to that group so a direct file transfer works too.
if [[ $INSTALL_SERVICE -eq 1 && -d /mnt/nvme && $(findmnt -no TARGET /mnt/nvme 2>/dev/null) == /mnt/nvme ]]; then
    install -d -m 2775 -o "$SERVICE_USER" -g "$SERVICE_USER" /mnt/nvme/model
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "$SERVICE_USER" ]] \
            && getent passwd "$SUDO_USER" >/dev/null 2>&1; then
        usermod -aG "$SERVICE_USER" "$SUDO_USER"
        note "model drop folder: /mnt/nvme/model (writable by ${SERVICE_USER} and ${SUDO_USER} -- ${SUDO_USER} may need to log out and back in for the group to apply)"
    else
        note "model drop folder: /mnt/nvme/model (writable by ${SERVICE_USER})"
    fi
fi

# The video gateway. WebRTC is how the pages show a live feed with less than a
# second of delay; the detection app publishes H.264 into this and the browser
# plays it. Its own installer, called from here rather than left as a step in a
# document -- a box installed without it silently falls back to MJPEG, which
# looks like "the stream is slow" rather than "a component was never
# installed". Never fatal: it downloads a release binary, and a box being
# installed offline must still finish.
if [[ $INSTALL_SERVICE -eq 1 && -f "${SRC_DIR}/scripts/mediamtx/install.sh" ]]; then
    say "Video gateway (MediaMTX)"
    # Run through sh: the execute bit does not survive every checkout (a repo
    # cloned on Windows and copied over has none), and a missing +x would turn
    # a working install into a "Permission denied" nobody expects here.
    sh "${SRC_DIR}/scripts/mediamtx/install.sh" \
        || warn "the video gateway did not install -- live views fall back to MJPEG. Re-run scripts/mediamtx/install.sh FROM THE INSTALLER CHECKOUT once this box is online; it is not copied onto the box"
fi

# ---------------------------------------------------------------------------
# 7b. (removed -- the board-identity record needed the ATECC secure element)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 7c. The sign-in a board arrives with
# ---------------------------------------------------------------------------
#
# The platform administrator -- the MASTER account, which owns everything on
# the box. The product on its own never invents a password ("an operator sets
# it deliberately or nobody logs in", core/services/auth.py), which is the
# right default for a developer checkout and leaves a customer appliance with
# no way in until somebody talks them through first setup.
#
# So an appliance ships with one, and the decision is recorded here rather
# than buried: this password is the SAME on every board, and it is written
# down in the README. It is a starting credential, not a secret. What keeps a
# board safe is the customer changing it, and the product should keep asking
# until they do.
#
# It is stored the product's own way -- salted hash through
# auth.set_master_password, never in clear -- so the salt differs on every
# board even though the password does not. That matters for the golden-image
# plan: cloning a disk clones the hash, and a hash of a known password is
# worth no more than the password itself, which is already public.
#
# ONLY when there is no master password yet. Re-running the installer on a
# board whose owner has chosen their own must never put this one back.
DEFAULT_ADMIN_USER="iriv"
DEFAULT_ADMIN_PASSWORD="iriv1234"

if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "Default sign-in"
    if "${TARGET}/.venv/bin/python" - "$TARGET" "$DEFAULT_ADMIN_USER" \
         "$DEFAULT_ADMIN_PASSWORD" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from core.services import auth

target, want_user, want_password = sys.argv[1], sys.argv[2], sys.argv[3]
if auth.master_password() or (auth._auth_local().get("master") or {}).get("password_hash"):
    print("   a master password is already set -- left alone")
    sys.exit(0)
auth.set_admin_username(want_user)
auth.set_master_password(want_password)
print("   ready for first setup: the first visit to the dashboard creates the account")
PY
    then
        for f in "${TARGET}/config/auth.local.toml"; do
            [[ -f "$f" ]] || continue
            chmod 600 "$f"
            chown "${SERVICE_USER}:${SERVICE_USER}" "$f"
        done
    else
        warn "could not set the default sign-in -- first setup will have to create one"
    fi
fi

# THE HOTSPOT KEY. A new box broadcasts the default, iriv1234
# (netconf.DEFAULT_AP_PASSWORD, Tok Aba's decision 2026-10-07: it is in the
# manual, so the person at the box can always join; the dashboard password
# still guards the box and the hotspot cannot reach the wired LAN, see the
# nftables step). Written by the product's own function into
# config/network.local.toml, 0600 and the service account's.
#
# A key already there is kept -- a re-install must not re-key a radio an
# operator chose. Applied by the hub at its next start; printed below and on
# the front panel's Wi-Fi page.
AP_KEY=""
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "Hotspot key"
    if AP_KEY="$("${TARGET}/.venv/bin/python" - "$TARGET" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from core.services import netconf

cfg = sys.argv[1] + "/config"
had = bool(netconf.ap_password(cfg))
key = netconf.ensure_ap_password(cfg)
print("   kept the key this box already has" if had
      else "   set the default key", file=sys.stderr)
print(key)
PY
)"; then
        chmod 600 "${TARGET}/config/network.local.toml"
        chown "${SERVICE_USER}:${SERVICE_USER}" "${TARGET}/config/network.local.toml"
        note "hotspot key: ${AP_KEY}  (config/network.local.toml, 600; also on the front panel)"
    else
        AP_KEY=""
        warn "could not write the hotspot key -- the hub sets the default at its first start"
    fi
fi

if [[ -n "$ADAPTER" && $INSTALL_SERVICE -eq 1 ]]; then
    install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" /var/lib/cytron-agent
    [[ -f /var/lib/cytron-agent/pairing.json ]] && chmod 600 /var/lib/cytron-agent/pairing.json
    note "/var/lib/cytron-agent (700, ${SERVICE_USER})"
fi

# ---------------------------------------------------------------------------
# 7d. The row store
# ---------------------------------------------------------------------------
#
# A table has two halves. Its DESIGN -- name and columns -- lives in config, so
# tables can be built with the database down and nothing is lost. Its ROWS live
# in Postgres. With no Postgres, a Data Table node writes nowhere, Analytics
# has nothing to chart and the query console has nothing to query, and the only
# sign of it is a banner most people will not connect to "the board records
# nothing".
#
# This used to be left to the operator, because the database MAY live on
# another host and several boards may share it. That is a real deployment and
# --db-host still serves it. It is the wrong default for an appliance: a box
# that ships a Database page should arrive able to store a row.
#
# Nothing here creates a schema. The master runs core/db/migrations on startup
# (master_server.py), so the schema arrives with the code that expects it --
# an installer that wrote tables would be a second place for the schema to be
# defined, and the two would drift.
if [[ $INSTALL_DB -eq 1 && $INSTALL_SERVICE -eq 1 && -n "$DB_HOST" ]]; then
    say "Row store"
    note "using the database at ${DB_HOST} -- not installing one here"
    note "  set its password in ${TARGET}/config/storage.local.toml"
elif [[ $INSTALL_DB -eq 1 && $INSTALL_SERVICE -eq 1 ]]; then
    say "Row store"

    if [[ $EUID -eq 0 ]] && command -v apt-get >/dev/null; then
        if ! command -v psql >/dev/null; then
            note "installing postgresql"
            DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql \
                || warn "postgresql failed to install -- tables can be designed but no rows will be stored"
        fi
        systemctl enable --now postgresql >/dev/null 2>&1 || true
    fi

    if command -v psql >/dev/null && systemctl is-active postgresql >/dev/null 2>&1; then
        # The password is generated ONCE and then read back on every re-run.
        # Rolling it would leave the role and the config disagreeing, and the
        # box would come up unable to reach a database that is running fine.
        DB_PASSWORD="$("${TARGET}/.venv/bin/python" - "$TARGET" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import secrets
from core import config

block = (config.load_local("storage", sys.argv[1] + "/config") or {}).get("postgres") or {}
print(str(block.get("password") or "") or secrets.token_urlsafe(24))
PY
)"
        # Role and database, both idempotent: CREATE would fail the second
        # time, and this script is expected to be re-runnable.
        sudo -u postgres psql -tAc \
            "SELECT 1 FROM pg_roles WHERE rolname='${SERVICE_USER}'" 2>/dev/null \
            | grep -q 1 && DB_ROLE_EXISTS=1 || DB_ROLE_EXISTS=0
        if [[ $DB_ROLE_EXISTS -eq 0 ]]; then
            sudo -u postgres psql -q -c \
                "CREATE ROLE ${SERVICE_USER} LOGIN PASSWORD '${DB_PASSWORD}'" \
                >/dev/null 2>&1 && note "role ${SERVICE_USER} created" \
                || warn "could not create the database role"
        else
            # The role predates this run -- make its password the one we are
            # about to write down, or the two would disagree.
            sudo -u postgres psql -q -c \
                "ALTER ROLE ${SERVICE_USER} LOGIN PASSWORD '${DB_PASSWORD}'" \
                >/dev/null 2>&1 || warn "could not set the database role's password"
            note "role ${SERVICE_USER}: already there"
        fi

        sudo -u postgres psql -tAc \
            "SELECT 1 FROM pg_database WHERE datname='cytron_dashboard'" 2>/dev/null \
            | grep -q 1 && DB_EXISTS=1 || DB_EXISTS=0
        if [[ $DB_EXISTS -eq 0 ]]; then
            sudo -u postgres createdb -O "${SERVICE_USER}" cytron_dashboard \
                >/dev/null 2>&1 && note "database cytron_dashboard created" \
                || warn "could not create cytron_dashboard"
        else
            note "database cytron_dashboard: already there"
        fi

        # Written through the product's own config writer, merged onto
        # whatever else storage.local.toml holds.
        if "${TARGET}/.venv/bin/python" - "$TARGET" "$DB_PASSWORD" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from core import config

cfg_dir = sys.argv[1] + "/config"
local = config.load_local("storage", cfg_dir) or {}
block = dict(local.get("postgres") or {})
block["password"] = sys.argv[2]
local["postgres"] = block
config.save_local("storage", local, cfg_dir)
PY
        then
            chmod 600 "${TARGET}/config/storage.local.toml"
            chown "${SERVICE_USER}:${SERVICE_USER}" "${TARGET}/config/storage.local.toml"
            note "credentials: config/storage.local.toml (600)"
        else
            warn "could not write the database password -- the board will not reach Postgres"
        fi

        # Proved, not assumed: connect the way the platform does.
        if "${TARGET}/.venv/bin/python" - "$TARGET" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
from core import db

with db.connect(sys.argv[1] + "/config") as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
PY
        then
            note "the platform can reach the row store"
        else
            warn "the row store did not answer -- tables can be designed, but no rows will be stored"
        fi
    else
        warn "no running postgresql -- tables can be designed, but no rows will be stored"
        warn "  install it and re-run, or point this box at one with --db-host"
    fi
fi

# ---------------------------------------------------------------------------
# 7e. The MQTT broker
# ---------------------------------------------------------------------------
#
# Publish to MQTT and MQTT Value default to 127.0.0.1:1883, so a box needs a
# broker of its own for them to work as placed. Mosquitto, from the distro,
# enabled at boot. It listens on LOOPBACK only and takes anonymous clients
# there: the app on this box is the client, and a broker open to the LAN with
# no password is a door anyone on the plant network can write through. A site
# that wants PLCs or SCADA to reach it sets a username and password under
# Settings > MQTT Broker, which opens the listener with a hashed password file
# (scripts/mqtt_apply.py, below) -- a decision for that site, not a default.
#
# Node-RED holding 1883 (its Aedes broker, on a stock IRIV image) is not a
# reason to skip: "Appliance defaults" below turns Node-RED off on every
# appliance anyway, so skipping here left a box with NO broker once it had
# (found installing 192.168.1.47 on 2026-09-29). It is turned off here
# instead, the same way and with the same reason, and the step below finds
# it already off. Anything else on 1883 is left alone: two brokers cannot
# share the port, and the one there is already doing the job.
#
# An irivai.conf already on the box is kept: it may be a secured listener
# set from Settings > MQTT Broker, and re-running the installer must not
# quietly reopen it as anonymous or close it to the LAN.
if [[ $INSTALL_MQTT -eq 1 && $INSTALL_SERVICE -eq 1 ]]; then
    say "MQTT broker"
    mqtt_port_owner="$(ss -ltnpH 'sport = :1883' 2>/dev/null || true)"
    if [[ "$mqtt_port_owner" == *node-red* ]] && systemctl cat nodered.service >/dev/null 2>&1; then
        systemctl disable --now nodered >/dev/null 2>&1 || true
        note "Node-RED: stopped and disabled (its broker held 1883, and it was open to the network)"
        note "  turn it back on from Settings -> Board I/O when it is wanted"
        for _ in 1 2 3 4 5; do
            mqtt_port_owner="$(ss -ltnpH 'sport = :1883' 2>/dev/null || true)"
            [[ -z "$mqtt_port_owner" ]] && break
            sleep 1
        done
    fi
    if [[ -n "$mqtt_port_owner" && "$mqtt_port_owner" != *mosquitto* ]]; then
        note "port 1883 is already served here -- leaving that broker in place"
    elif [[ $EUID -eq 0 ]] && command -v apt-get >/dev/null; then
        if ! command -v mosquitto >/dev/null; then
            note "installing mosquitto"
            DEBIAN_FRONTEND=noninteractive apt-get install -y mosquitto mosquitto-clients                 || warn "mosquitto failed to install -- MQTT nodes need a broker named by address"
        fi
        if command -v mosquitto >/dev/null; then
            install -d -m 755 /etc/mosquitto/conf.d
            if [[ -f /etc/mosquitto/conf.d/irivai.conf ]]; then
                note "keeping the broker settings already on this box"
            else
            cat > /etc/mosquitto/conf.d/irivai.conf <<'MQTTCONF'
# Written by the IRIV installer. Loopback only: the app on this box is the
# client. Settings > MQTT Broker replaces this file; change it there.
listener 1883 127.0.0.1
allow_anonymous true
MQTTCONF
            chmod 644 /etc/mosquitto/conf.d/irivai.conf
            fi
            systemctl enable mosquitto >/dev/null 2>&1 || true
            systemctl restart mosquitto >/dev/null 2>&1 || true
            if systemctl is-active mosquitto >/dev/null 2>&1; then
                note "broker on 127.0.0.1:1883 (local only)"
            else
                warn "mosquitto is installed but not running -- check: journalctl -u mosquitto"
            fi
        fi
    else
        warn "not root or not an apt host -- no MQTT broker installed"
    fi
fi
# --no-mqtt is REMEMBERED. The hub installs a missing broker by itself when
# it starts (core/services/mqtt_broker.py auto_install_step), which is how a
# box updated over the air gets one -- and without this marker it would do
# exactly that on the first start after an install that said no. Written
# only when the flag is given: a plain re-run must not undo an admin who
# turned the automatic install off on the Settings page.
if [[ $INSTALL_MQTT -eq 0 && $INSTALL_SERVICE -eq 1 ]]; then
    printf 'auto_install = false\n' > "${TARGET}/config/mqtt_autoinstall.local.toml"
    chmod 600 "${TARGET}/config/mqtt_autoinstall.local.toml"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${TARGET}/config/mqtt_autoinstall.local.toml"
    note "no MQTT broker, and the platform will not install one by itself"
fi

# ---------------------------------------------------------------------------
# 7f. Detection models
# ---------------------------------------------------------------------------
#
# A box with no model cannot detect anything, and the catalogue's models used
# to be fetched only from a button on the AI Models page -- so every fresh
# install showed "Needs its .hef" (found on 192.168.1.47, 2026-09-29). Here,
# as the service account, into the shared models dir (/opt/irivai-models on
# an appliance): yolov8n and yolov8s for THIS box's accelerator, and coco.txt,
# the class names, written from the product's own list rather than downloaded
# (core/services/model_bootstrap.py). Files already there are kept.
#
# Never fatal. Offline, or no accelerator plugged in, the install finishes and
# says how to fetch later; the hub also retries by itself at start, which is
# how a box updated over the air gets its models.
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "Detection models"
    # `|| models_rc=$?`, the same idiom as the hardening probe: under set -e a
    # bare call that exits non-zero would end the install right here.
    models_rc=0
    (cd "$TARGET" && sudo -u "$SERVICE_USER" "$PY" -m core.services.model_bootstrap) \
        || models_rc=$?
    case "$models_rc" in
        0) note "detection models ready" ;;
        2) warn "no Hailo chip identified yet (normal on a new box before its first reboot) -- no models fetched; the hub fetches them at its next start" ;;
        *) warn "some detection models could not be downloaded (offline?) -- the hub retries at start, or run: cd ${TARGET} && sudo -u ${SERVICE_USER} ${PY} -m core.services.model_bootstrap" ;;
    esac
fi

# ---------------------------------------------------------------------------
# 7g. The accelerator's sharing service
# ---------------------------------------------------------------------------
#
# On a Hailo-8 / 8L every system opens the chip THROUGH hailort.service
# (multi_process is their default, systems/demo/app/hailo.py
# _default_multi_process), which is how several systems share one chip. With
# the service off the first system takes the chip for itself after its grace
# and the second one is refused. hailo-all ships the unit; nothing enabled it,
# so it was switched on by hand per board. Asked of the product's own
# detection (builtin_models.detect_arch_uncached: hailortcli, then lspci, so
# a chip whose driver loads only after the first reboot is still seen), run
# as the service user like every other in-tree Python call here: the tree and
# its venv are that account's by now, so running them as root would hand it
# root on the next re-run (Pipi S1-L2). Neither probe needs root.
# Hailo-10H boxes open the chip themselves and are left as they are. Never
# fatal: a service that cannot start yet is enabled for the next boot.
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    say "HailoRT service"
    HAILO_ARCH="$(cd "$TARGET" && sudo -u "$SERVICE_USER" "$PY" -c 'from core.services import builtin_models as b; print(b.detect_arch_uncached() or "")' 2>/dev/null | tail -n 1 || true)"
    case "$HAILO_ARCH" in
        hailo8|hailo8l)
            if ! systemctl cat hailort.service >/dev/null 2>&1; then
                warn "${HAILO_ARCH}: no hailort.service on this box (HailoRT not from hailo-all?) -- one system at a time can use the chip"
            elif systemctl enable --now hailort.service >/dev/null 2>&1; then
                note "${HAILO_ARCH}: hailort.service on (the systems share the chip through it)"
            else
                systemctl enable hailort.service >/dev/null 2>&1 || true
                warn "${HAILO_ARCH}: hailort.service did not start yet -- enabled, it starts at the next boot"
            fi ;;
        hailo10h) note "Hailo-10H: each system opens the chip itself; hailort.service left as it is" ;;
        *)        note "no Hailo chip seen -- hailort.service left as it is" ;;
    esac
fi

# ---------------------------------------------------------------------------
# 8. systemd units
# ---------------------------------------------------------------------------

if [[ $INSTALL_SERVICE -eq 0 ]]; then
    say "systemd units"
    note "skipped (--no-service): nothing written, nothing started"
else

say "systemd units"

write_unit() { printf '%s\n' "$2" > "$1"; note "wrote $1"; }

# NODE-RED WHEN THE SOFTWARE IS OFF -- ONE OR THE OTHER, NEVER BOTH. Both
# drive the board's GPIO (relays, PWM, inputs), and the front panel's display,
# buzzer and USER button; two programs driving the same pins fight. So:
#   * switching IRIV EdgeAI Studio off (`systemctl disable --now irivai-hub`,
#     the README's "Off") starts Node-RED at once, and at every boot while the
#     software stays disabled (irivai-nodered-fallback.service);
#   * whenever the software STARTS (switched on, at boot, restarted) Node-RED
#     is stopped and its own start-at-boot is turned off -- whoever turned it on.
# The test is the hub unit's enablement: a restart, a crash or an update of
# an enabled hub is still "the software is on".
install -d -m 755 /usr/local/lib/irivai
cat > /usr/local/lib/irivai/nodered-fallback.sh <<'SH'
#!/bin/sh
# Called by irivai-hub.service (hub-starting / hub-stopped) and at boot by
# irivai-nodered-fallback.service (boot). See scripts/install.sh.
systemctl cat nodered.service >/dev/null 2>&1 || exit 0     # no Node-RED here
case "$1" in
    boot|hub-stopped)
        systemctl is-enabled --quiet irivai-hub && exit 0     # the software is on
        systemctl is-active --quiet nodered && exit 0         # already running
        systemctl start nodered \
            && echo "IRIV EdgeAI Studio is off - Node-RED started"
        ;;
    hub-starting)
        if systemctl is-active --quiet nodered || systemctl is-enabled --quiet nodered; then
            systemctl disable --now nodered >/dev/null 2>&1
            echo "IRIV EdgeAI Studio is starting - Node-RED stopped (they share the GPIO)"
        fi
        ;;
esac
exit 0
SH
chmod 755 /usr/local/lib/irivai/nodered-fallback.sh
chown root:root /usr/local/lib/irivai/nodered-fallback.sh

write_unit /etc/systemd/system/irivai-hub.service "[Unit]
Description=IRIV EdgeAI Studio hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${TARGET}
# No --port here. The port is a SETTING (config/systems.local.toml [hub].port,
# Settings -> Network -> Software port) that the hub reads at start
# (core/services/hubport.py), so master can change it from the page with no
# root step beyond the restart rule below. A --port on this line would win
# over the setting and lock it; the page says so if it finds one.
ExecStart=${PY} hub_server.py
# Node-RED while the software is off (nodered-fallback.sh, above). '+' = as
# root: starting and stopping another unit is not the service account's job.
ExecStartPre=+/usr/local/lib/irivai/nodered-fallback.sh hub-starting
ExecStopPost=+/usr/local/lib/irivai/nodered-fallback.sh hub-stopped
# Restarting the dashboard must never take a live detection down with it.
# Without this, systemd's default (control-group) kill takes every master
# and app the hub ever spawned down along with it -- meaning a hub crash,
# or an admin bouncing the service for any reason at all, stops every
# camera on the box. KillMode=process kills only the hub's own PID.
KillMode=process
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"

write_unit /etc/systemd/system/irivai-master@.service "[Unit]
Description=IRIV EdgeAI Studio master (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${TARGET}
EnvironmentFile=/etc/irivai/%i.env
ExecStart=${PY} master_server.py --system %i --port \${PORT}
Restart=always
RestartSec=5
# A system removed on the Hub: its master exits 78 (master_server.stand_down)
# and stays down, instead of a restart every 5 s for ever.
Environment=IRIV_REMOVED_EXIT=78
RestartPreventExitStatus=78

[Install]
WantedBy=multi-user.target"

install -d -m 755 /etc/irivai
# The unit's port and the hub's [masters.<slug>].url must be ONE number. The
# env file used to get 5110 while systems.local.toml got nothing, so the hub's
# boot reconcile invented a url of its own (5101, the agent's port) and the
# Demo card sat red "down" beside a master that was running fine. Decided once
# here and written to both: a slug's existing url is kept when it is in the
# master band and nobody else holds it (a re-install leaves a working box
# alone), otherwise the next free port from --master-port that no other
# master, stream port or box service uses (provision.stream_ports_in_use).
MASTER_PORTS="$(cd "$TARGET" && sudo -u "$SERVICE_USER" "$PY" - "$MASTER_PORT" $BUILTIN_SLUGS <<'PY'
import sys
import urllib.parse
from core import config
from core.services import hubport, provision
first, slugs = int(sys.argv[1]), sys.argv[2:]
used = {}
for slug, entry in (config.systems().get("masters") or {}).items():
    port = urllib.parse.urlparse((entry or {}).get("url") or "").port
    if port:
        used[slug] = port
fixed = provision.stream_ports_in_use() | set(hubport.RESERVED) | {hubport.port()}
local = config.load_local("systems")
masters = local.setdefault("masters", {})
for slug in slugs:
    others = fixed | {p for s, p in used.items() if s != slug}
    port = used.get(slug)
    if not port or port < first or port in others:
        port = first
        while port in others:
            port += 1
    used[slug] = port
    masters.setdefault(slug, {})["url"] = "http://127.0.0.1:%d" % port
    print(slug, port)
config.save_local("systems", local)
PY
)" || MASTER_PORTS=""
if [[ -z "$MASTER_PORTS" ]]; then
    warn "could not read the systems config -- numbering masters from ${MASTER_PORT}"
    port=$MASTER_PORT
    for slug in $BUILTIN_SLUGS; do
        MASTER_PORTS+="${slug} ${port}"$'\n'
        port=$((port + 1))
    done
fi
while read -r slug port; do
    [[ -n "$slug" && "$port" =~ ^[0-9]+$ ]] || continue
    printf 'PORT=%s\n' "$port" > "/etc/irivai/${slug}.env"
    note "systems/${slug} -> port ${port}"
done <<< "$MASTER_PORTS"
for f in "${TARGET}/config/systems.local.toml"; do
    [[ -f "$f" ]] && chmod 600 "$f" && chown "${SERVICE_USER}:${SERVICE_USER}" "$f"
done

# The hub's port, written where the hub reads it. Only when asked for a
# non-default one and the box has no setting yet: a re-install must not put
# the installer's default back over a port master chose on the page. The
# EFFECTIVE port -- whatever the file now says -- is what the health poll
# and the summary below use, because that is where the hub will be.
if [[ "$HUB_PORT" != "5100" ]]; then
    (cd "$TARGET" && sudo -u "$SERVICE_USER" "$PY" - "$HUB_PORT" <<'PY'
import sys
from core import config
from core.services import hubport
if "port" in (config.load_local("systems").get("hub") or {}):
    print("   software port: already set on this box - left alone")
else:
    hubport.save(int(sys.argv[1]), validate=False)
    print("   software port: %s (config/systems.local.toml [hub].port)" % sys.argv[1])
PY
    ) || warn "could not write the software port -- the hub starts on its configured port"
    for f in "${TARGET}/config/systems.local.toml"; do
        [[ -f "$f" ]] && chmod 600 "$f" && chown "${SERVICE_USER}:${SERVICE_USER}" "$f"
    done
fi
HUB_PORT="$(cd "$TARGET" && sudo -u "$SERVICE_USER" "$PY" -c 'from core.services import hubport; print(hubport.port())' 2>/dev/null | tail -n 1 || echo "$HUB_PORT")"
[[ "$HUB_PORT" =~ ^[0-9]+$ ]] || HUB_PORT=5100

install -d -m 755 /usr/local/lib/irivai
# `sudo iriv-ctl disable|enable|stop|start|status`: switch the software off or on.
install -m 755 "${SRC_DIR}/scripts/iriv-ctl.sh" /usr/local/bin/iriv-ctl
# The query console's one-shot privilege helper. Root-owned and outside every
# tree the service account can write: the platform may ASK for it to run, and
# must not be able to change what it does.
install -m 755 "${SRC_DIR}/scripts/db_console_role.py" /usr/local/lib/irivai/db-console-role.py
chown root:root /usr/local/lib/irivai/db-console-role.py

# The MQTT broker's settings (Settings > MQTT Broker), on the same terms: the
# page may ASK for a port, a WebSocket port and a login, and this root-owned
# script decides. It re-validates every field, writes only irivai.conf and
# the hashed password file under /etc/mosquitto, and rolls back a restart
# that does not come up. Input on stdin only, so the sudoers rule below can
# name it with no arguments at all.
install -m 755 "${SRC_DIR}/scripts/mqtt_apply.py" /usr/local/lib/irivai/mqtt-apply.py
chown root:root /usr/local/lib/irivai/mqtt-apply.py

# Wi-Fi profiles, on the same terms: the hotspot key and the site's Wi-Fi
# passphrase used to reach NetworkManager on nmcli's argv through sudo, and
# sudo journals every argv it authorises (Yaya M-5, Pipi 2b). This helper
# takes the request on stdin, re-validates it, writes a 0600 keyfile and
# brings it up; the sudoers rule below names it with no arguments.
install -m 755 "${SRC_DIR}/scripts/wifi_profile.py" /usr/local/lib/irivai/wifi-profile.py
chown root:root /usr/local/lib/irivai/wifi-profile.py

# The box's hostname (Settings -> Network -> Hostname), on the same terms: a
# rename needs hostnamectl, /etc/hosts' 127.0.1.1 line and an avahi restart,
# all root. Name on stdin, re-validated there; no arguments in sudoers.
install -m 755 "${SRC_DIR}/scripts/set_hostname.py" /usr/local/lib/irivai/set-hostname.py
chown root:root /usr/local/lib/irivai/set-hostname.py

# Software update by hand (About -> Software update): switching the live
# release link in /opt needs root, so it is a root-owned script with the request
# on stdin and nothing on argv. It refuses a box that is not set up for updates
# (the live folder is not a link), and keeps only the live and previous release.
install -m 755 "${SRC_DIR}/scripts/apply_release.py" /usr/local/lib/irivai/apply-release.py
chown root:root /usr/local/lib/irivai/apply-release.py

# A drive fitted after install (Settings -> "New storage detected"), on the
# same terms: the popup may ASK to format a drive or mount what is on it, by
# the drive's id from the hub's own list, and this root-owned script decides
# -- it asks lsblk again, refuses the system's own disk, the eMMC/SD and
# anything in use, checks the drive is the one the admin saw, and only then
# partitions, mounts at /mnt/nvme and adds the fstab line (nofail). The same
# helper makes the box's own folders on a drive already mounted there and
# hands them to the service account ("repair": those folders only), so no
# customer ever types sudo for "Storage is not writable". Request on stdin;
# no arguments in sudoers.
install -m 755 "${SRC_DIR}/scripts/storage_setup.py" /usr/local/lib/irivai/storage-setup.py
chown root:root /usr/local/lib/irivai/storage-setup.py

# Port 80 for the reverse-proxy access mode (Settings -> Network -> Access),
# on the same terms: the hub may ASK for "80 -> my port" or "no redirect",
# and this root-owned script decides -- it re-checks the port, refuses while
# another program listens on 80, and writes only its own nftables table
# (inet irivai_web) and /etc/irivai/web-redirect.nft. Request on stdin; no
# arguments in sudoers. Nothing is redirected until the mode is chosen.
install -m 755 "${SRC_DIR}/scripts/web_redirect.py" /usr/local/lib/irivai/web-redirect.py
chown root:root /usr/local/lib/irivai/web-redirect.py

# The eth1 port, which is a USB adapter pretending to be a soldered-on port.
#
# eth0 is a real Raspberry Pi port. eth1 is a Realtek RTL8153 on USB, and it
# ships with two rough edges eth0 does not have: its jack LEDs stay dark on a
# GIGABIT link, because the chip drives one LED per speed and the 1G one is
# not the LED the jack wired up; and its address EEPROM is blank, so the
# kernel invents a new MAC every boot and DHCP moves the box to a new address
# each time. That is how a board at .173 became .10 with nobody touching its
# configuration -- somebody moved the cable from one port to the other.
#
# The helper fixes both, from udev, once per appearance of the interface.
install -m 755 "${SRC_DIR}/scripts/usbnet_setup.py" /usr/local/lib/irivai/usbnet-setup.py
chown root:root /usr/local/lib/irivai/usbnet-setup.py
if [[ -f "${SRC_DIR}/scripts/udev/84-irivai-usbnet.rules" ]]; then
    install -d -m 755 /etc/udev/rules.d
    install -m 644 "${SRC_DIR}/scripts/udev/84-irivai-usbnet.rules" \
            /etc/udev/rules.d/84-irivai-usbnet.rules
    udevadm control --reload >/dev/null 2>&1 || true
    # Not triggered here on purpose. The address can only be set while the
    # interface is DOWN, and re-triggering a live eth1 would either be
    # ignored or bounce the link this script may be running over. It takes
    # effect at the next boot, which is when the address matters anyway.
    note "eth1 port: LEDs on at every speed, address pinned from the next boot"
fi

# A WIRED PORT WITH NOBODY HANDING OUT ADDRESSES (direct cable to another
# iriv, or to a laptop) gets no address from NetworkManager by default --
# DHCP is retried for ever. The drop-in gives such a port a 169.254 address
# the way a laptop would (scripts/networkmanager/80-irivai-linklocal.conf
# says why). Only NetworkManager 1.52 and later know the value; an older one
# is told, not fed a setting it would warn about on every start. Reloading
# the configuration touches no live connection.
if [[ -f "${SRC_DIR}/scripts/networkmanager/80-irivai-linklocal.conf" ]] && command -v nmcli >/dev/null; then
    nm_ver="$(nmcli -v 2>/dev/null | awk '{print $NF}')"
    if [[ "$(printf '%s\n' 1.52 "${nm_ver:-0}" | sort -V | head -1)" == "1.52" ]]; then
        install -d -m 755 /etc/NetworkManager/conf.d
        install -m 644 "${SRC_DIR}/scripts/networkmanager/80-irivai-linklocal.conf" \
                /etc/NetworkManager/conf.d/80-irivai-linklocal.conf
        nmcli general reload conf >/dev/null 2>&1 || true
        note "wired ports: a jack that hears no DHCP server falls back to a 169.254 address"
    else
        note "NetworkManager ${nm_ver:-?} is older than 1.52: a direct cable with no DHCP server gets no address"
    fi
fi

# LINE ENDINGS, on the three files the kernel reads a shebang from.
#
# A CRLF here makes the kernel look for an interpreter literally named
# "python3\r", and it says so in a way that blames the interpreter:
#
#   env: 'python3\r': No such file or directory
#
# which reads as a broken Python install on the board.
#
# .gitattributes now pins *.py to LF, which fixes `git archive`. This is the
# belt to that brace: a tree can reach a board by scp from a Windows machine,
# by unzipping a download, or through a share that rewrites endings, and none
# of those consult .gitattributes. Cheap, and it only touches files we
# install ourselves.
for helper in /usr/local/lib/irivai/db-console-role.py \
              /usr/local/lib/irivai/mqtt-apply.py \
              /usr/local/lib/irivai/wifi-profile.py \
              /usr/local/lib/irivai/set-hostname.py \
              /usr/local/lib/irivai/storage-setup.py \
              /usr/local/lib/irivai/web-redirect.py \
              /usr/local/lib/irivai/usbnet-setup.py; do
    [[ -f "$helper" ]] || continue
    python3 - "$helper" <<'PY' || true
import sys
path = sys.argv[1]
with open(path, "rb") as f:
    data = f.read()
if b"\r\n" in data:
    with open(path, "wb") as f:
        f.write(data.replace(b"\r\n", b"\n"))
    print("   normalised line endings in " + path)
PY
done
# Leftovers of the removed over-the-air updater, on a box installed by an
# earlier version of this script: its units, helpers and sudoers rules would
# otherwise stay behind pointing at scripts that no longer exist.
systemctl disable irivai-recover.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/irivai-recover.service \
      /etc/systemd/system/irivai-apply.service \
      /usr/local/lib/irivai/recover.py /usr/local/lib/irivai/flip-link.py \
      /usr/local/lib/irivai/root-steps.py \
      /etc/sudoers.d/irivai-update /etc/sudoers.d/irivai-rootsteps \
      /etc/irivai/update.conf

# Restarting the platform's own units: the hub restarts itself and each
# system's master (a port or hostname change, a system's Restart button).
# Exact commands, no shell, no wildcards -- and the units themselves run as
# ${SERVICE_USER}, so restarting one grants nothing that account did not
# already have.
#
# The master instance is a sudo REGEX (1.9.10+), not `irivai-master@*`: a
# sudoers `*` also matches spaces, so the wildcard let the account restart
# ANY unit as root by listing it after a master (Pipi, 2026-10-07). On an
# older sudo, which would read ^...$ literally, the rule names each master
# this installer set up instead, one exact command each.
umask 077
MASTER_RESTART='/usr/bin/systemctl ^restart irivai-master@[a-z0-9_-]+[.]service$, '
SUDO_VER="$(sudo -V 2>/dev/null | sed -n 's/^Sudo version \([0-9.]*\).*/\1/p' | head -1)"
if [[ "$(printf '%s\n' 1.9.10 "${SUDO_VER:-0}" | sort -V | head -1)" != "1.9.10" ]]; then
    MASTER_RESTART=""
    for env_file in /etc/irivai/*.env; do
        slug="$(basename "$env_file" .env)"
        [[ -f "$env_file" && "$slug" =~ ^[a-z0-9_-]+$ ]] || continue
        MASTER_RESTART+="/usr/bin/systemctl restart irivai-master@${slug}.service, "
    done
    warn "sudo ${SUDO_VER:-?} is older than 1.9.10: the restart rule names each installed system's unit; re-run this installer after adding one"
fi
cat > /etc/sudoers.d/irivai-restart.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart irivai-hub.service${MASTER_RESTART:+, ${MASTER_RESTART%, }}
SUDOERS
chmod 440 /etc/sudoers.d/irivai-restart.tmp
if visudo -cf /etc/sudoers.d/irivai-restart.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-restart.tmp /etc/sudoers.d/irivai-restart
    note "sudoers: ${SERVICE_USER} may restart the platform's own units"
else
    rm -f /etc/sudoers.d/irivai-restart.tmp
    warn "the restart sudoers rule did not validate -- the platform will not be able to restart its own units"
fi

# The update helper, as its own file. NOT irivai-update: this script removes
# that name above, as a leftover of the old over-the-air updater.
cat > /etc/sudoers.d/irivai-release.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/apply-release.py
SUDOERS
chmod 440 /etc/sudoers.d/irivai-release.tmp
if visudo -cf /etc/sudoers.d/irivai-release.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-release.tmp /etc/sudoers.d/irivai-release
    note "sudoers: ${SERVICE_USER} may switch the live release (software update)"
else
    rm -f /etc/sudoers.d/irivai-release.tmp
    warn "the update sudoers rule did not validate -- software update will not be able to switch versions"
fi

# The box's own hardware and network, for the front panel and the Network
# settings page. Separate file from the update rule because these are a
# different decision an installer may want to refuse: shutdown and reboot are
# what the USER button does and what the dashboard's power buttons ask for;
# nmcli is how the appliance is put on a network by whoever is standing next
# to it; and the Node-RED unit is the factory flow, which cannot share the
# OLED, the buzzer and the USER button with this platform.
#
# Each is a full path with no shell, and every one of them is something the
# person at the machine can already do by pressing a button or pulling a plug.
#
# nmcli is NOT `nmcli *` any more. That grant let the account run any nmcli
# as root -- `nmcli -s connection show` prints every stored Wi-Fi key -- and
# it is why the keys travelled on argv. Now: the Wi-Fi helper (keys on its
# stdin) and the verbs the Network page uses (netconf.py: radio on/off,
# connection up/down/delete/modify, adding a wired profile, reconnecting
# wlan0), with --show-secrets / -s refused wherever it could be appended.
# Reading needs no sudo at all.
umask 077
cat > /etc/sudoers.d/irivai-box.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/sbin/shutdown now, /usr/sbin/reboot now, /sbin/shutdown now, /sbin/reboot now, /usr/bin/systemctl enable --now nodered, /usr/bin/systemctl disable --now nodered, /usr/bin/python3 -I /usr/local/lib/irivai/wifi-profile.py, /usr/bin/nmcli radio wifi on, /usr/bin/nmcli radio wifi off, /usr/bin/nmcli connection up *, /usr/bin/nmcli connection down *, /usr/bin/nmcli connection delete *, /usr/bin/nmcli connection modify *, /usr/bin/nmcli connection add type ethernet *, /usr/bin/nmcli device connect wlan0, !/usr/bin/nmcli -s *, !/usr/bin/nmcli * -s, !/usr/bin/nmcli * -s *, !/usr/bin/nmcli --show-secrets *, !/usr/bin/nmcli * --show-secrets, !/usr/bin/nmcli * --show-secrets *
SUDOERS
chmod 440 /etc/sudoers.d/irivai-box.tmp
if visudo -cf /etc/sudoers.d/irivai-box.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-box.tmp /etc/sudoers.d/irivai-box
    note "sudoers: ${SERVICE_USER} may power the box down and configure its network"
else
    rm -f /etc/sudoers.d/irivai-box.tmp
    warn "the box sudoers rule did not validate -- the front panel's power button and the Network page will not work"
fi
# The SQL console's read-only roles. The console runs every query as an
# unprivileged per-system login role with no rights on any base table, and
# creating that role needs CREATEROLE -- which the platform's database user
# does not get when the database is created by hand. Without this the console
# tells the operator to run a psql command that a dashboard cannot run.
#
# One fixed command, no arguments, so there is nowhere for a second statement
# to hide; the script it names is root-owned (installed above). The helper
# itself refuses on PostgreSQL before 16, where CREATEROLE could administer
# every other non-superuser role -- from 16 it may only administer the roles
# it created, and can never grant SUPERUSER.
umask 077
cat > /etc/sudoers.d/irivai-db.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/db-console-role.py
SUDOERS
chmod 440 /etc/sudoers.d/irivai-db.tmp
if visudo -cf /etc/sudoers.d/irivai-db.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-db.tmp /etc/sudoers.d/irivai-db
    note "sudoers: ${SERVICE_USER} may ask for the query console's database role"
else
    rm -f /etc/sudoers.d/irivai-db.tmp
    warn "the database sudoers rule did not validate -- the query console will ask for a psql command to be run by hand"
fi

# The MQTT broker helper. One exact command: python3 in isolated mode (-I, no
# PYTHON* environment or user site-packages) on the root-owned script above,
# with no further arguments permitted -- the request travels on stdin, and
# the script trusts none of it.
cat > /etc/sudoers.d/irivai-mqtt.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/mqtt-apply.py
SUDOERS
chmod 440 /etc/sudoers.d/irivai-mqtt.tmp
if visudo -cf /etc/sudoers.d/irivai-mqtt.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-mqtt.tmp /etc/sudoers.d/irivai-mqtt
    note "sudoers: ${SERVICE_USER} may apply the MQTT broker's settings"
else
    rm -f /etc/sudoers.d/irivai-mqtt.tmp
    warn "the MQTT sudoers rule did not validate -- Settings > MQTT Broker will not be able to change the broker"
fi

# The hostname helper, on the mqtt-apply terms: one exact command, no
# arguments, the name on stdin.
cat > /etc/sudoers.d/irivai-hostname.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/set-hostname.py
SUDOERS
chmod 440 /etc/sudoers.d/irivai-hostname.tmp
if visudo -cf /etc/sudoers.d/irivai-hostname.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-hostname.tmp /etc/sudoers.d/irivai-hostname
    note "sudoers: ${SERVICE_USER} may change the hostname (Settings -> Network -> Hostname)"
else
    rm -f /etc/sudoers.d/irivai-hostname.tmp
    warn "the hostname sudoers rule did not validate -- the Hostname card stays read-only"
fi

# The new-drive helper, on the same terms: one exact command, no arguments,
# the request on stdin.
cat > /etc/sudoers.d/irivai-storage.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/storage-setup.py
SUDOERS
chmod 440 /etc/sudoers.d/irivai-storage.tmp
if visudo -cf /etc/sudoers.d/irivai-storage.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-storage.tmp /etc/sudoers.d/irivai-storage
    note "sudoers: ${SERVICE_USER} may set up a newly fitted drive (the New storage popup)"
else
    rm -f /etc/sudoers.d/irivai-storage.tmp
    warn "the storage sudoers rule did not validate -- a new drive must be set up by hand (scripts/setup_nvme.sh)"
fi

# The port 80 helper, on the same terms: one exact command, no arguments,
# the request on stdin.
cat > /etc/sudoers.d/irivai-web.tmp <<SUDOERS
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/web-redirect.py
SUDOERS
chmod 440 /etc/sudoers.d/irivai-web.tmp
if visudo -cf /etc/sudoers.d/irivai-web.tmp >/dev/null; then
    mv /etc/sudoers.d/irivai-web.tmp /etc/sudoers.d/irivai-web
    note "sudoers: ${SERVICE_USER} may point port 80 at the dashboard (Settings -> Network -> Access)"
else
    rm -f /etc/sudoers.d/irivai-web.tmp
    warn "the port 80 sudoers rule did not validate -- the box stays on addresses with a port"
fi
umask 022

# ...and do it now, so a fresh box never meets that error at all. Best effort:
# a box whose database is not up yet, or is on another host, is not a failed
# install -- the console asks again the first time it is used.
if /usr/bin/python3 -I /usr/local/lib/irivai/db-console-role.py >/dev/null 2>&1; then
    note "database: the query console's read-only roles can be created"
fi

if [[ -n "$ADAPTER" ]]; then
    # --unlock is deliberately absent. A device that reopens its pairing window
    # on every reboot can be seized by any host on the LAN.
    write_unit /etc/systemd/system/irivai-agent.service "[Unit]
Description=IRIV EdgeAI Studio device agent (Contract v1)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${TARGET}
ExecStart=${PY} -m agent.agent --adapter ${ADAPTER} --port ${AGENT_PORT} --state /var/lib/cytron-agent/pairing.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"
fi

# At boot, while the software is disabled: start Node-RED (nodered-fallback.sh).
write_unit /etc/systemd/system/irivai-nodered-fallback.service "[Unit]
Description=Start Node-RED while IRIV EdgeAI Studio is switched off
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/lib/irivai/nodered-fallback.sh boot

[Install]
WantedBy=multi-user.target"

systemctl daemon-reload
systemctl enable irivai-nodered-fallback.service >/dev/null
systemctl enable irivai-hub.service >/dev/null
# restart, not `enable --now`: --now only starts a STOPPED unit, so a re-install
# with a different port would leave the old one serving where nobody probes.
systemctl restart irivai-hub.service
for slug in $BUILTIN_SLUGS; do
    # A built-in the box does not list (demo, on every box: it ships only as
    # the template new systems are built from) gets no service. Started, its
    # master would see it is not listed, stand down, and leave a failed unit.
    if grep -qE '^enabled = \[\]' "${TARGET}/config/systems.local.toml" 2>/dev/null        || ! grep -qE "^enabled = .*\"${slug}\"" "${TARGET}/config/systems.local.toml" "${TARGET}/config/systems.toml" 2>/dev/null; then
        systemctl disable "irivai-master@${slug}.service" >/dev/null 2>&1 || true
        note "${slug}: not listed on this box, so its service is not started"
        continue
    fi
    systemctl enable "irivai-master@${slug}.service" >/dev/null
    systemctl restart "irivai-master@${slug}.service"
done
if [[ -n "$ADAPTER" ]]; then
    systemctl enable irivai-agent.service >/dev/null
    systemctl restart irivai-agent.service
fi

# Poll the port rather than trust is-active: Restart=always means a unit that
# crashes on boot still reads "activating" for its first seconds.
wait_for_port() {
    local port="$1" unit="$2"
    for _ in $(seq 1 20); do
        curl -fsS -m 2 "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1 && return 0
        systemctl is-active --quiet "$unit" || break
        sleep 1
    done
    systemctl status "$unit" --no-pager --lines 20 || true
    journalctl -u "$unit" --no-pager --lines 20 || true
    return 1
}

wait_for_port "$HUB_PORT" irivai-hub.service || die "the hub is not answering on ${HUB_PORT}"
note "hub answering on ${HUB_PORT}"

fi   # INSTALL_SERVICE

# ---------------------------------------------------------------------------
# 8b. Appliance defaults
# ---------------------------------------------------------------------------
#
# What a board ARRIVES like, as against what is merely reported about it.
# These used to sit inside the --skip-hardening block, which meant that
# flag -- "do not lecture me about passwords" -- also silently left
# Node-RED open and the front panel off. Configuring the appliance and
# auditing it are two different jobs.
say "Appliance defaults"

# NODE-RED OFF. Raspberry Pi OS ships it, and it comes up listening on
# 0.0.0.0:1880 with adminAuth commented out -- on a board installed on
# 2026-09-19 an anonymous request to /flows returned 95 KB of flows over
# the LAN, with an MQTT broker open on 1883 beside it. That is a larger
# hole than any password on the dashboard: the editor runs code.
#
# Disabled rather than removed. It is a real feature of the board, the
# Board I/O page turns it back on deliberately (there is a sudoers rule
# for exactly that), and uninstalling something the customer may have
# bought the board for is not the installer's decision to make.
#
# `systemctl cat`, not `list-unit-files | grep`. This script runs
# under `set -o pipefail`, and `grep -q` exits the instant it
# matches -- so systemctl dies of SIGPIPE with 141, pipefail hands
# that to the `if`, and the condition reads FALSE because the match
# SUCCEEDED. The step then silently did nothing. Run on its own the
# same pipeline matches perfectly, which is what makes this worth a
# comment rather than a quiet fix.
if systemctl cat nodered.service >/dev/null 2>&1; then
    if systemctl is-enabled nodered >/dev/null 2>&1 \
       || systemctl is-active nodered >/dev/null 2>&1; then
        systemctl disable --now nodered >/dev/null 2>&1 || true
        note "Node-RED: stopped and disabled (it was open to the network)"
        note "  turn it back on from Settings -> Board I/O when it is wanted"
    else
        note "Node-RED: already off"
    fi
fi

# THE IMAGE'S OTHER LISTENERS. Raspberry Pi OS ships a desktop image, and
# three of its services are doors an appliance has no use for:
#
#   wayvnc (+ wayvnc-control)  a remote desktop on 5900. The dashboard is the
#                              product's remote interface; VNC is a second,
#                              separately-authenticated one.
#   rpcbind (.socket, .service) NFS's port mapper on 111, tcp and udp, to
#                              the whole LAN. Nothing here mounts or serves NFS.
#   serial-getty@ttyAMA10      a login prompt on the board's UART -- which is
#                              muxed with the RS485 port. A Modbus RTU master
#                              on that bus talks to a getty. Its kernel half,
#                              console=serial0/ttyAMA10 in cmdline.txt, goes
#                              too (a backup is kept beside it), effective at
#                              the next reboot.
#
# Disabled, not removed, and each has a --keep-* flag: a site that uses one
# of them deliberately says so. Only touched when the unit exists.
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    off_units() {
        local label="$1"; shift
        local unit found=0
        for unit in "$@"; do
            systemctl cat "$unit" >/dev/null 2>&1 || continue
            found=1
            systemctl disable --now "$unit" >/dev/null 2>&1 || true
        done
        [[ $found -eq 1 ]] && note "${label}: stopped and disabled"
        return 0
    }
    if [[ $KEEP_VNC -eq 0 ]]; then
        off_units "VNC desktop (wayvnc, 5900)" wayvnc.service wayvnc-control.service
    else
        note "VNC desktop: kept (--keep-vnc)"
    fi
    if [[ $KEEP_RPCBIND -eq 0 ]]; then
        # The socket first: disabling only the service leaves the socket to
        # start it again on the next connection to 111.
        off_units "rpcbind (111)" rpcbind.socket rpcbind.service
    else
        note "rpcbind: kept (--keep-rpcbind)"
    fi
    if [[ $KEEP_SERIAL_CONSOLE -eq 0 ]]; then
        off_units "serial console login (ttyAMA10)" serial-getty@ttyAMA10.service
        for cmdline in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
            [[ -f "$cmdline" ]] || continue
            if grep -qE 'console=(serial0|ttyAMA10)' "$cmdline"; then
                [[ -f "${cmdline}.irivai-bak" ]] || cp -p "$cmdline" "${cmdline}.irivai-bak"
                sed -i -E 's/console=(serial0|ttyAMA10)(,[0-9]+)?[[:space:]]*//g; s/[[:space:]]+$//' "$cmdline"
                # One line, still naming the root filesystem, or it goes
                # back: a cmdline.txt the firmware cannot boot is a board
                # in a box on its way back to the workshop.
                if [[ "$(wc -l < "$cmdline")" -le 1 ]] && grep -q 'root=' "$cmdline"; then
                    note "kernel console off the UART (${cmdline}; was saved as ${cmdline}.irivai-bak) -- from the next reboot"
                else
                    cp -p "${cmdline}.irivai-bak" "$cmdline"
                    warn "${cmdline} did not look right after the edit -- put back as it was"
                fi
            fi
            break
        done
    else
        note "serial console: kept (--keep-serial-console) -- the RS485 port shares that UART"
    fi
fi

# FRONT PANEL ON. Holding the USER button for five seconds shows a
# six-digit code that resets the master password -- the way back in for an
# owner who is standing at the box with no email on file. It only works
# with the panel running, and the panel ships OFF (panel.py: enabled
# defaults to False, because not every board that runs this software has
# one). A customer appliance does have one, so the appliance installer
# turns it on; a developer checkout is untouched.
#
# Written as a .local file, which no update overwrites, and only if the
# operator has not already decided for themselves.
if [[ ! -f "${TARGET}/config/panel.local.toml" ]]; then
    printf '[panel]\nenabled = true\n' > "${TARGET}/config/panel.local.toml"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${TARGET}/config/panel.local.toml"
    note "front panel: on (hold USER 5s for the password-reset code)"
else
    note "front panel: left as configured"
fi


# WHAT THE BOARD CALLS ITSELF.
#
# The front panel tells whoever is standing at the box to open
# `iriv.local:5100` (core/services/panel.py). Nothing made that true: the
# installer never set a hostname, so a board published `raspberrypi.local`
# over mDNS and the name on the screen resolved to nothing. Verified on a
# board on 2026-09-19 -- raspberrypi.local answered, iriv.local did not
# exist.
#
# So the name is set here, and avahi-daemon is in the package list above
# rather than inherited from whatever the OS image happened to include.
#
# /etc/hosts matters as much as the hostname: without the 127.0.1.1 line
# every sudo prints "unable to resolve host" and waits on a DNS timeout
# first, which turns a working box into a slow one.
if [[ $SET_HOSTNAME -eq 1 && $EUID -eq 0 ]]; then
    if [[ -z "$WANT_HOSTNAME" ]]; then
        # .env IRIV_DEFAULT_HOSTNAME, else the same default as
        # core/services/hostinfo.DEFAULT_HOSTNAME.
        env_host=""
        for envf in /etc/irivai/studio.env "${SRC_DIR}/.env"; do
            if [[ -f "$envf" ]]; then
                env_host="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?IRIV_DEFAULT_HOSTNAME[[:space:]]*=[[:space:]]*["'"'"']\?\([A-Za-z0-9-]*\).*/\2/p' "$envf" | tail -1)"
                break
            fi
        done
        WANT_HOSTNAME="${env_host:-IRIV-EdgeAI-Studio}"
    fi
    current_host="$(hostname)"
    if [[ "$current_host" == "$WANT_HOSTNAME" ]]; then
        note "hostname: already ${WANT_HOSTNAME} (${WANT_HOSTNAME}.local)"
    elif [[ $HOSTNAME_EXPLICIT -eq 0 && "$current_host" != "raspberrypi" \
            && "$current_host" != "iriv" ]]; then
        # Named by its owner (the Network page, or by hand): a re-install
        # keeps it. --hostname still renames.
        note "hostname: keeping ${current_host} (${current_host}.local)"
    elif command -v hostnamectl >/dev/null; then
        hostnamectl set-hostname "$WANT_HOSTNAME" 2>/dev/null \
            || warn "could not set the hostname"
        if grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
            sed -i "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t${WANT_HOSTNAME}/" /etc/hosts
        else
            printf '127.0.1.1\t%s\n' "$WANT_HOSTNAME" >> /etc/hosts
        fi
        systemctl restart avahi-daemon >/dev/null 2>&1 || true
        note "hostname: ${current_host} -> ${WANT_HOSTNAME} (reachable as ${WANT_HOSTNAME}.local)"

        # And the browser's opinion about the old name.
        #
        # Chromium locks its profile with a symlink naming the machine and
        # process that holds it -- SingletonLock -> raspberrypi-2780. It
        # compares the HOSTNAME in that name against the current one, so
        # renaming the board convinces it that the profile belongs to a
        # different computer, and it refuses to start:
        #
        #   The profile appears to be in use by another Chromium process
        #   (2780) on another computer (raspberrypi).
        #
        # Which is what happened on a board on 2026-09-20: the desktop over
        # VNC came up fine and no browser would open, with the reason only
        # visible on a terminal. The lock is stale by definition here --
        # the machine it names no longer exists -- and Chromium rebuilds
        # all three files on its next start. Only touched when nothing is
        # running, and only when the link really does name the old host.
        if ! pgrep -x chromium >/dev/null 2>&1; then
            for profile in /root/.config/chromium /home/*/.config/chromium; do
                [[ -L "${profile}/SingletonLock" ]] || continue
                target="$(readlink "${profile}/SingletonLock")"
                [[ "$target" == "${current_host}-"* ]] || continue
                rm -f "${profile}/SingletonLock" \
                      "${profile}/SingletonCookie" \
                      "${profile}/SingletonSocket"
                note "cleared a Chromium profile lock left by ${current_host}"
            done
        fi
    fi
fi

# THE HOTSPOT REACHES THE BOX, NOT THE LAN BEHIND IT.
#
# NetworkManager's hotspot is `ipv4.method shared`: it switches on IP
# forwarding and masquerades the hotspot's clients out of every other
# interface. So anyone holding the hotspot key had a route through the box
# into the site's wired network -- with the key the same on every box, that
# was the second link of Pipi's CRITICAL chain (2026-10-04). A fixed nftables
# table drops forwarded traffic from wlan0 to a wired port
# (scripts/nftables/ap-isolate.nft says why each line is there). The box's
# own services are input, not forwarding, so the dashboard, the systems'
# pages and live video still answer on the hotspot.
#
# Its own oneshot unit, ordered before NetworkManager so the hotspot is
# never up without it. --allow-ap-forwarding removes it, for a site that
# wants the hotspot as a door into its plant network.
AP_ISOLATED=0
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    # Remembered across re-installs: with this file the isolation stays off,
    # without it it is kept loaded.
    rm -f /etc/irivai/ap-forwarding-allowed
    if [[ $AP_FORWARD -eq 1 ]]; then
        install -d -m 755 /etc/irivai && touch /etc/irivai/ap-forwarding-allowed
        systemctl disable --now irivai-ap-isolate.service >/dev/null 2>&1 || true
        rm -f /etc/systemd/system/irivai-ap-isolate.service /usr/local/lib/irivai/ap-isolate.nft
        command -v nft >/dev/null && nft delete table inet irivai_ap_isolate >/dev/null 2>&1 || true
        systemctl daemon-reload
        warn "hotspot clients can be routed onto the wired LAN (--allow-ap-forwarding)"
    elif ! command -v nft >/dev/null; then
        warn "nft not found (package nftables) -- hotspot clients can still be routed onto the wired LAN"
    else
        install -d -m 755 /usr/local/lib/irivai
        install -m 644 -o root -g root "${SRC_DIR}/scripts/nftables/ap-isolate.nft" \
                /usr/local/lib/irivai/ap-isolate.nft
        sed -i 's/\r$//' /usr/local/lib/irivai/ap-isolate.nft
        write_unit /etc/systemd/system/irivai-ap-isolate.service "[Unit]
Description=IRIV EdgeAI Studio: no forwarding from the hotspot to the wired LAN
DefaultDependencies=no
After=local-fs.target
Before=network-pre.target NetworkManager.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/nft delete table inet irivai_ap_isolate
ExecStart=/usr/sbin/nft -f /usr/local/lib/irivai/ap-isolate.nft
ExecStop=/usr/sbin/nft delete table inet irivai_ap_isolate

[Install]
WantedBy=multi-user.target"
        systemctl daemon-reload
        systemctl enable irivai-ap-isolate.service >/dev/null 2>&1 || true
        systemctl restart irivai-ap-isolate.service >/dev/null 2>&1 || true
        if nft list table inet irivai_ap_isolate >/dev/null 2>&1; then
            AP_ISOLATED=1
            note "hotspot: reaches this box only, nothing is routed onto the wired LAN"
        else
            warn "the hotspot isolation table did not load -- check: journalctl -u irivai-ap-isolate"
        fi
    fi
fi

# MODBUS ON 502. Every PLC defaults to 502; a non-root app cannot bind it.
# A fixed nftables redirect sends 502 to 5020, where a system's Modbus
# server listens (scripts/nftables/modbus-redirect.nft says why this and not
# a capability). The marker tells the Modbus panel the redirect is there --
# the service account cannot ask nftables itself.
if [[ $INSTALL_SERVICE -eq 1 ]]; then
    if command -v nft >/dev/null; then
        install -d -m 755 /usr/local/lib/irivai /etc/irivai
        install -m 644 -o root -g root "${SRC_DIR}/scripts/nftables/modbus-redirect.nft" \
                /usr/local/lib/irivai/modbus-redirect.nft
        sed -i 's/\r$//' /usr/local/lib/irivai/modbus-redirect.nft
        write_unit /etc/systemd/system/irivai-modbus-redirect.service "[Unit]
Description=IRIV EdgeAI Studio: Modbus 502 -> 5020 redirect
After=network-pre.target
Before=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/nft delete table inet irivai_modbus
ExecStart=/usr/sbin/nft -f /usr/local/lib/irivai/modbus-redirect.nft
ExecStop=/usr/sbin/nft delete table inet irivai_modbus

[Install]
WantedBy=multi-user.target"
        systemctl daemon-reload
        systemctl enable irivai-modbus-redirect.service >/dev/null 2>&1 || true
        systemctl restart irivai-modbus-redirect.service >/dev/null 2>&1 || true
        if nft list table inet irivai_modbus >/dev/null 2>&1; then
            printf '502 -> 5020\n' > /etc/irivai/modbus-502-redirect
            chmod 644 /etc/irivai/modbus-502-redirect
            note "Modbus: PLCs polling 502 reach the Modbus server on 5020"
        else
            rm -f /etc/irivai/modbus-502-redirect
            warn "the Modbus 502 redirect did not load -- PLCs must poll 5020 (journalctl -u irivai-modbus-redirect)"
        fi
    else
        rm -f /etc/irivai/modbus-502-redirect
        warn "nft not found (package nftables) -- PLCs must poll the Modbus server on 5020, not 502"
    fi
fi

# ---------------------------------------------------------------------------
# 9. Hardening
# ---------------------------------------------------------------------------
#
# An appliance ships to a site nobody from here will ever log into.
#
# This used to REPORT and change nothing, on the theory that editing someone's
# password or SSH config is how an installer locks an operator out. The image
# made that theory expensive: pi/raspberry with `pi ALL=(ALL) NOPASSWD: ALL`
# (inline in /etc/sudoers on this image, not only in 010_pi-nopasswd) and
# password SSH on, behind a hotspot whose key was the same on every box, is
# root from radio range (Pipi's CRITICAL chain, 2026-10-04). So the fixes that
# cannot lock anybody out are now MADE, each with the guard that keeps that
# true:
#
#   * pi's OS password is NOT touched, not expired, not checked (Tok Aba,
#     2026-10-07): the box keeps the password it shipped with and the owner
#     changes it with passwd if they want. The Done summary says so in one
#     line. Only root is still probed for the factory password below.
#   * NOPASSWD: ALL for pi -> the same grant WITH a password (the tag is
#     removed, the line kept, so pi keeps sudo whatever groups it is in);
#     visudo-checked on a copy, renamed into place. Not done when pi has no
#     usable password -- sudo would then have no way to ask -- which is
#     reported instead.
#   * nodered's NOPASSWD: ALL -> removed while Node-RED is disabled (8b).
#   * sshd: PermitRootLogin no always; PasswordAuthentication no ONLY when
#     the account running this install has an authorized_keys to get back in
#     with. Without one it is said, not done.
#
# And while anything is still wrong the install does NOT print "Done": it
# exits non-zero with the list above it. --skip-hardening skips this whole
# step (and that refusal) for a bench box that knows what it is.

if [[ $SKIP_HARDENING -eq 0 ]]; then
    say "Hardening check"
    problems=0

    # A CHECK THAT CANNOT RUN MUST NOT PRINT A PASS. This is counted
    # separately from a problem for exactly that reason: the first version of
    # this block read /etc/shadow through Python's `crypt` and `spwd`, both
    # REMOVED in Python 3.13 (PEP 594). On a Debian 13 board the import
    # failed, 2>/dev/null ate the error, the `if` went false, and the
    # installer announced "no default passwords" on a box whose pi account
    # still had `raspberry`. Silence read as safety.
    unchecked=0

    # root only: pi's password is the owner's business (see above), so a
    # pi on `raspberry` is neither changed nor a reason to refuse "Done".
    for u in root; do
        id -u "$u" >/dev/null 2>&1 || continue
        # libcrypt through ctypes, and /etc/shadow read directly. Both are
        # there on any Linux that has a shadow file at all, and neither is a
        # Python standard-library module that can be removed from under this.
        # Exit codes are three-way on purpose: 0 default, 1 not default,
        # 2 could not tell.
        # `|| rc=$?`, not a bare call then `case $?`: under `set -e` the probe's
        # exit 1 -- "no default password", the GOOD answer -- ended the whole
        # install here, as a failure, before the SSH check and the summary
        # (root on 192.168.1.47, 2026-09-29).
        rc=0
        python3 - "$u" <<'PY' || rc=$?
import ctypes, ctypes.util, sys

def crypt_r(word, salt):
    name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
    lib = ctypes.CDLL(name)
    lib.crypt.restype = ctypes.c_char_p
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    out = lib.crypt(word.encode(), salt.encode())
    return out.decode() if out else ""

try:
    want = sys.argv[1]
    hashed = ""
    with open("/etc/shadow", encoding="utf-8") as f:
        for line in f:
            parts = line.split(":")
            if parts and parts[0] == want:
                hashed = parts[1] if len(parts) > 1 else ""
                break
    # "!" or "*" is a locked account with no password to be default.
    if not hashed or hashed[0] in "!*":
        sys.exit(1)
    sys.exit(0 if crypt_r("raspberry", hashed) == hashed else 1)
except Exception:
    sys.exit(2)
PY
        case $rc in
            0)  warn "user '${u}' still has the default password 'raspberry'."
                warn "  Fix before this box leaves: sudo passwd ${u}"
                problems=$((problems + 1)) ;;
            1)  ;;
            *)  warn "could not check whether '${u}' has the default password."
                unchecked=$((unchecked + 1)) ;;
        esac
    done

    # NOPASSWD: ALL GRANTS. A grant line is edited on a COPY of its file,
    # the copy is checked with visudo, and only then renamed over the
    # original (same directory, so the rename is atomic). A copy that does
    # not parse is thrown away and the original stays: a broken sudoers file
    # is a box nobody can administer. Files sudo itself ignores (a name with
    # a dot or ending in ~) are left alone.
    #   sudoers_edit FILE SED-EXPR -> 0 changed, 1 nothing to change, 2 refused
    sudoers_edit() {
        local file="$1" expr="$2" tmp
        [[ -f "$file" ]] || return 1
        tmp="$(mktemp "$(dirname "$file")/.irivai-sudoers.XXXXXX")" || return 2
        sed -E "$expr" "$file" > "$tmp"
        if cmp -s "$file" "$tmp"; then rm -f "$tmp"; return 1; fi
        if ! visudo -c -q -f "$tmp" >/dev/null 2>&1; then rm -f "$tmp"; return 2; fi
        chown root:root "$tmp" && chmod 0440 "$tmp" && mv -f "$tmp" "$file"             || { rm -f "$tmp"; return 2; }
        return 0
    }
    sudoers_files() {
        local f
        [[ -f /etc/sudoers ]] && echo /etc/sudoers
        for f in /etc/sudoers.d/*; do
            [[ -f "$f" ]] || continue
            case "$(basename "$f")" in *.*|*~) continue ;; esac
            echo "$f"
        done
    }
    if [[ $EUID -eq 0 ]] && command -v visudo >/dev/null; then
        PI_NOPASSWD='^(pi[[:space:]]+ALL[[:space:]]*=[[:space:]]*\(ALL(:ALL)?\)[[:space:]]+)NOPASSWD:[[:space:]]*(ALL[[:space:]]*)$'
        if id -u pi >/dev/null 2>&1; then
            pi_hash="$(getent shadow pi 2>/dev/null | cut -d: -f2 || true)"
            pi_grants=0
            for f in $(sudoers_files); do
                grep -qE "$PI_NOPASSWD" "$f" && pi_grants=$((pi_grants + 1))
            done
            if [[ $pi_grants -gt 0 && ( -z "$pi_hash" || "$pi_hash" == [\!\*]* ) ]]; then
                warn "pi has passwordless sudo and no password sudo could ask for -- left as it is."
                warn "  Set one (sudo passwd pi) and re-run, so the grant can ask for it."
                problems=$((problems + 1))
            elif [[ $pi_grants -gt 0 ]]; then
                for f in $(sudoers_files); do
                    rc=0; sudoers_edit "$f" "s/${PI_NOPASSWD}/\\1\\3/" || rc=$?
                    case $rc in
                        0) note "sudo: pi's grant in ${f} now asks for pi's password" ;;
                        2) warn "could not edit ${f} safely (visudo refused the result) -- left as it was"
                           problems=$((problems + 1)) ;;
                    esac
                done
                # A ticket pi already holds would keep the old grant alive
                # until it times out.
                rm -f /run/sudo/ts/pi 2>/dev/null || true
            fi
            # Proved, not assumed: sudo's own list of what pi may do.
            # Captured first, matched second (pipefail + grep -q, see 8b).
            pi_rules="$(sudo -l -U pi 2>/dev/null || true)"
            if [[ "$pi_rules" == *"NOPASSWD: ALL"* ]]; then
                warn "pi can still run anything as root without a password (sudo -l -U pi)."
                warn "  A grant this installer does not recognise -- remove it with visudo."
                problems=$((problems + 1))
            fi
        fi
        # Node-RED's own account. It runs the flow editor, which runs code;
        # with NOPASSWD: ALL that code is root. Only while Node-RED is OFF
        # (8b turns it off): a site that turned it back on decided to run it
        # as it ships, and taking sudo from under its flows is not this
        # script's call.
        if ! systemctl is-enabled nodered >/dev/null 2>&1 \
           && ! systemctl is-active nodered >/dev/null 2>&1; then
            for f in $(sudoers_files); do
                rc=0
                sudoers_edit "$f" '/^nodered[[:space:]]+ALL[[:space:]]*=.*NOPASSWD:[[:space:]]*ALL[[:space:]]*$/d' || rc=$?
                case $rc in
                    0) note "sudo: removed nodered's passwordless grant from ${f} (Node-RED is off)" ;;
                    2) warn "could not edit ${f} safely (visudo refused the result) -- left as it was"
                       problems=$((problems + 1)) ;;
                esac
            done
        fi
    elif [[ $EUID -eq 0 ]]; then
        warn "visudo not found -- sudo grants were not checked."
        unchecked=$((unchecked + 1))
    fi

    # SSHD. A drop-in of our own, named to sort FIRST: sshd keeps the first
    # value it reads for a keyword, and Debian's sshd_config includes
    # sshd_config.d/*.conf at its top, so a later file (cloud-init's 50-...)
    # cannot quietly turn passwords back on. Rewritten on every run from
    # what the box is NOW, so a re-run on a box whose keys are gone gives
    # password login back rather than locking it.
    #
    # Whose keys: the account that ran `sudo ./install.sh` -- that is who
    # has to get back in. From a root shell (no SUDO_USER), any login
    # account's.
    if [[ $EUID -eq 0 ]] && command -v sshd >/dev/null && [[ -d /etc/ssh ]]; then
        key_user=""
        has_keys() {
            local home
            home="$(getent passwd "$1" | cut -d: -f6)"
            [[ -n "$home" && -s "${home}/.ssh/authorized_keys" ]] \
                && grep -qE '^[[:space:]]*(ssh-|ecdsa-|sk-)' "${home}/.ssh/authorized_keys"
        }
        if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
            has_keys "$SUDO_USER" && key_user="$SUDO_USER"
            who_needs="${SUDO_USER}"
        else
            for home in /home/*; do
                u="$(basename "$home")"
                id -u "$u" >/dev/null 2>&1 && has_keys "$u" && { key_user="$u"; break; }
            done
            who_needs="a login account"
        fi
        DROPIN=/etc/ssh/sshd_config.d/01-irivai-hardening.conf
        install -d -m 755 /etc/ssh/sshd_config.d
        {
            echo "# Written by the IRIV installer (scripts/install.sh, Hardening). Re-written"
            echo "# on every run: password login goes off only while ${who_needs} has an"
            echo "# authorized_keys to log in with."
            echo "PermitRootLogin no"
            if [[ -n "$key_user" ]]; then
                echo "PasswordAuthentication no"
                echo "KbdInteractiveAuthentication no"
            fi
        } > "$DROPIN"
        chmod 644 "$DROPIN"
        if sshd -t >/dev/null 2>&1; then
            systemctl reload ssh >/dev/null 2>&1 || systemctl reload sshd >/dev/null 2>&1 || true
            if [[ -n "$key_user" ]]; then
                note "sshd: key login only (${key_user} has an authorized_keys), no root login"
            else
                note "sshd: no root login"
                warn "password SSH is left ON: ${who_needs} has no ~/.ssh/authorized_keys yet."
                warn "  Add a key (ssh-copy-id from your laptop), check you can log in with it, re-run."
            fi
        else
            rm -f "$DROPIN"
            warn "sshd -t rejected the hardening drop-in -- removed again, sshd left as it was"
            problems=$((problems + 1))
        fi
    fi

    # sshd's OWN answer, not a guess from the files. Password authentication
    # is ON BY DEFAULT: a board with no PasswordAuthentication line anywhere
    # accepts passwords, and grepping for the directive found nothing and
    # called that hardened. `sshd -T` prints the configuration actually in
    # force, defaults included, which is the only thing worth asserting.
    ssh_pw="$(sshd -T 2>/dev/null | awk '/^passwordauthentication /{print $2}')"
    if [[ -z "$ssh_pw" ]]; then
        ssh_pw="$(grep -REhs '^[[:space:]]*PasswordAuthentication[[:space:]]+' \
                    /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null \
                  | tail -1 | awk '{print tolower($2)}')"
        [[ -z "$ssh_pw" ]] && ssh_pw="unknown"
    fi
    case "$ssh_pw" in
        yes)
            warn "SSH accepts password authentication."
            warn "  Prefer keys: set PasswordAuthentication no in /etc/ssh/sshd_config.d/,"
            warn "  after installing an authorized_keys you can still log in with."
            problems=$((problems + 1)) ;;
        no) ;;
        *)  warn "could not read sshd's password-authentication setting."
            unchecked=$((unchecked + 1)) ;;
    esac
    ssh_root="$(sshd -T 2>/dev/null | awk '/^permitrootlogin /{print $2}')"
    case "$ssh_root" in
        no) ;;
        "") unchecked=$((unchecked + 1)) ;;
        *)  warn "SSH lets root log in (PermitRootLogin ${ssh_root})."
            problems=$((problems + 1)) ;;
    esac

    # The hotspot isolation from 8b. Not loaded (no nft, or a table that
    # would not parse) is a problem unless the operator asked for
    # forwarding: the hotspot is then a way into the site's wired LAN.
    if [[ $INSTALL_SERVICE -eq 1 && $AP_FORWARD -eq 0 && $AP_ISOLATED -eq 0 ]]; then
        warn "the hotspot can route its clients onto the wired LAN (irivai-ap-isolate is not loaded)."
        warn "  Install nftables and re-run, or pass --allow-ap-forwarding if that is wanted."
        problems=$((problems + 1))
    fi

    if [[ $problems -eq 0 && $unchecked -eq 0 ]]; then
        note "no default root password, no passwordless sudo, SSH not accepting passwords"
    elif [[ $problems -eq 0 ]]; then
        warn "${unchecked} check(s) could not run -- this box is NOT confirmed"
        warn "hardened. Verify by hand before it leaves."
    else
        warn "${problems} issue(s) above -- each one is a way into this box that"
        warn "does not go through the dashboard's sign-in."
        # No "Done" for a box that is not. The product is installed and
        # running; what is refused is the banner that would send it out of
        # the door. Fix and re-run (every step above is re-runnable), or
        # --skip-hardening for a bench box.
        die "not done: ${problems} hardening problem(s) above. Fix them and re-run, or pass --skip-hardening."
    fi
fi

# ---------------------------------------------------------------------------
# 10. Done
# ---------------------------------------------------------------------------

say "Done"

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

  Dashboard      : http://${IP:-<this-box>}  (port 80; also http://${IP:-<this-box>}:${HUB_PORT})
  First visit    : create your own username and password on the setup screen
  Hotspot key    : ${AP_KEY:-iriv1234 (set by the hub at its first start)}  (on the front panel; change it on Settings -> Network)
  OS account     : the pi account password was not changed; change it with passwd if you want
  Installed at   : ${TARGET}
  Runs as        : ${SERVICE_USER}
  Built-in       : ${BUILTIN_SLUGS}

$(if [[ $INSTALL_SERVICE -eq 1 ]]; then
echo "  Units:"
echo "    systemctl status irivai-hub"
for slug in $BUILTIN_SLUGS; do echo "    systemctl status irivai-master@${slug}"; done
[[ -n "$ADAPTER" ]] && echo "    systemctl status irivai-agent"
else
echo "  No services installed (--no-service). The tree above is what WOULD ship;"
echo "  re-run without the flag, as root, to write the units and start them."
fi)

  Logs are on the dashboard: Administration -> Logs (admin account), and each
  system's master has its own at /logs. Every request is one line with its
  status code and a request id; an error carries the same id, so a report of
  "it failed" is one search.

EOF
