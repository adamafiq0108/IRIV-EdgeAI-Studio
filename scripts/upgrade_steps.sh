#!/usr/bin/env bash
#
# upgrade_steps.sh -- the one-time ROOT steps an over-the-air update cannot do.
#
# An OTA update swaps the release tree and restarts the platform as the
# service account. Everything below lives outside that tree and needs root,
# so a box updated over the air keeps the old version of each until somebody
# runs this once. scripts/install.sh does all of it on a fresh install; this
# is the same set for a box that is already in service, without re-running
# the whole installer (no rebuild, no package churn, no account changes).
#
#   sudo bash /opt/irivai-systor/scripts/upgrade_steps.sh [options]
#
# Every step checks first and acts only when the box differs from what this
# release ships, so it is safe to re-run; the summary at the end says what it
# did and what it skipped, and why.
#
#   1. irivai-hub.service: drop a baked `--port N`, daemon-reload, restart
#      the hub. Tidy only since 2026-10-05: under systemd the setting wins
#      over a baked port (core/services/hubport.py), so the page can move
#      the port on a box that never ran this.
#   2. NetworkManager link-local drop-in (direct cable, no DHCP -> 169.254),
#      NetworkManager 1.52+ only.
#   3. The video gateway's config: the committed mediamtx.yml, which no longer
#      stamps the install-day address into webrtcAdditionalHosts, and (1.0.37,
#      Pipi H1) serves nothing off loopback but WebRTC media.
#   4. Packages: nftables (the fixed rules below), avahi-utils (the box's mDNS
#      advertisement and its real .local name). Skipped offline, said so.
#   5. Root helpers (/usr/local/lib/irivai), and with them root-steps.py,
#      which every over-the-air update from then on runs to keep them,
#   6. the sudoers rules and
#   7. the nftables units current by itself. One list for both:
#      scripts/root_items.json, carried out by scripts/root_steps.py (an
#      update installs only what its signed manifest pins). Each piece only
#      when it differs; a rule only once its helper is there, visudo-checked;
#      an nftables unit re-run only when changed or not loaded. Hotspot
#      isolation stays off on a box run with --allow-ap-forwarding (it leaves
#      /etc/irivai/ap-forwarding-allowed for the updates to see). A box that
#      never ran this has no root-steps.py, and its update log says so.
#   8. Appliance defaults: wayvnc, rpcbind and the UART login getty off, the
#      kernel console off the UART (RS485) -- each kept with a --keep-* flag.
#   9. Hardening (--harden only): pi's passwordless sudo turned into sudo
#      WITH pi's existing password (left as it is when pi has no usable
#      password), nodered's removed while Node-RED is off, sshd with no root
#      login and key-only login when pi has a key. pi's OS password itself is
#      never set, changed or expired (Tok Aba, 2026-10-07): the box keeps the
#      one it shipped with, and the owner runs passwd if they want another.
#  10. The hotspot key: left exactly as stored. iriv1234 is the default
#      again (Tok Aba, 2026-10-07), so a box keyed with it is not re-keyed,
#      and a box with any other key keeps that one.
#  11. irivai-apply.service gets KillMode=process (the installer writes it
#      since 1.0.16): the masters the updater re-spawns at the end of an
#      update no longer die with its oneshot unit. The hub brings them back
#      either way (core/services/update_revive.py); this is the belt.
#
# Options:
#   --keep-vnc --keep-rpcbind --keep-serial-console   as install.sh
#   --allow-ap-forwarding   let hotspot clients be routed onto the wired LAN
#   --harden                step 9; reads nothing, asks for no password
#   --dry-run               say what would change, change nothing
#
#   ssh pi@<box> 'sudo bash /opt/irivai-systor/scripts/upgrade_steps.sh --harden'
#
# Ships in every release (it is not under scripts/dev and not a bench
# script): the box that needs it is the one that was updated over the air.
#
# From 1.0.37 an over-the-air update does steps 1, 2, 3, 4 and 11 by itself,
# once per box (scripts/root_steps.py upgrade(), the "upgrade" table of
# root_items.json, recorded in /var/lib/irivai/upgrade-steps.json), on any box
# that already has root-steps.py. This script is still how a box WITHOUT it
# gets it, and the only place for steps 8 and 9, which are an operator's
# choices and are never done unattended. Its root-step run records the
# one-time steps as done too.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "${HERE}/.." && pwd)"
LIB=/usr/local/lib/irivai
KEEP_VNC=0
KEEP_RPCBIND=0
KEEP_SERIAL_CONSOLE=0
AP_FORWARD=0
HARDEN=0
DRY=0
DONE=()
SKIPPED=()

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '\033[33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
did()  { DONE+=("$*"); note "done: $*"; }
skip() { SKIPPED+=("$*"); note "skipped: $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-vnc)            KEEP_VNC=1 ;;
        --keep-rpcbind)        KEEP_RPCBIND=1 ;;
        --keep-serial-console) KEEP_SERIAL_CONSOLE=1 ;;
        --allow-ap-forwarding) AP_FORWARD=1 ;;
        --harden)              HARDEN=1 ;;
        --dry-run)             DRY=1 ;;
        -h|--help)             sed -n '2,65p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)                     die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0"
[[ -f "${APP}/scripts/wifi_profile.py" ]] || die "run from an installed release (${APP} has no scripts/wifi_profile.py)"

# The service account is whoever the hub unit runs as; cytron when unknown.
SERVICE_USER="$(sed -n 's/^User=//p' /etc/systemd/system/irivai-hub.service 2>/dev/null | head -1 || true)"
SERVICE_USER="${SERVICE_USER:-cytron}"

# --harden needs a pi account to work on; checked FIRST, before any change.
# No password is read: pi keeps the OS password it has (Tok Aba, 2026-10-07).
if [[ $HARDEN -eq 1 ]]; then
    id -u pi >/dev/null 2>&1 || die "--harden: there is no pi account on this box; nothing changed"
fi

# act CMD... -- run it, or only say it under --dry-run.
act() { if [[ $DRY -eq 1 ]]; then note "would run: $*"; else "$@"; fi; }

# put SRC DEST MODE -- install a file root-owned when it differs; 0 = changed.
put() {
    local src="$1" dest="$2" mode="$3"
    [[ -f "$src" ]] || return 2
    if [[ -f "$dest" ]] && cmp -s <(sed 's/\r$//' "$src") "$dest"; then return 1; fi
    if [[ $DRY -eq 1 ]]; then note "would install ${dest}"; return 0; fi
    install -d -m 755 "$(dirname "$dest")"
    sed 's/\r$//' "$src" > "${dest}.irivai-new"
    chmod "$mode" "${dest}.irivai-new"
    chown root:root "${dest}.irivai-new"
    mv -f "${dest}.irivai-new" "$dest"
    return 0
}

# --------------------------------------------------------------------------
say "1. hub unit: the software port is a setting"
UNIT=/etc/systemd/system/irivai-hub.service
RESTART_HUB=0
if [[ ! -f "$UNIT" ]]; then
    skip "hub unit: ${UNIT} not found"
elif grep -qE ' --port [0-9]+' "$UNIT"; then
    act sed -i -E 's/ --port [0-9]+//' "$UNIT"
    act systemctl daemon-reload
    RESTART_HUB=1
    did "hub unit: baked --port removed (the hub restarts at the end)"
else
    skip "hub unit: no baked --port"
fi

# --------------------------------------------------------------------------
say "2. NetworkManager: link-local fallback on a cable with no DHCP"
if ! command -v nmcli >/dev/null; then
    skip "link-local: no NetworkManager"
else
    nm_ver="$(nmcli -v 2>/dev/null | awk '{print $NF}')"
    if [[ "$(printf '%s\n' 1.52 "${nm_ver:-0}" | sort -V | head -1)" != "1.52" ]]; then
        skip "link-local: NetworkManager ${nm_ver:-?} is older than 1.52"
    else
        rc=0; put "${APP}/scripts/networkmanager/80-irivai-linklocal.conf" \
                  /etc/NetworkManager/conf.d/80-irivai-linklocal.conf 644 || rc=$?
        if [[ $rc -eq 0 ]]; then
            act nmcli general reload conf >/dev/null 2>&1 || true
            did "link-local: drop-in installed"
        else
            skip "link-local: drop-in already current"
        fi
    fi
fi

# --------------------------------------------------------------------------
say "3. video gateway: no install-day address"
if [[ ! -d /etc/mediamtx ]]; then
    skip "video gateway: not installed here (/etc/mediamtx missing)"
else
    rc=0; put "${APP}/scripts/mediamtx/mediamtx.yml" /etc/mediamtx/mediamtx.yml 644 || rc=$?
    if [[ $rc -eq 0 ]]; then
        act systemctl restart irivai-mediamtx >/dev/null 2>&1 || true
        did "video gateway: committed mediamtx.yml (addresses from the interfaces, every session)"
    else
        skip "video gateway: config already current"
    fi
fi

# --------------------------------------------------------------------------
say "4. packages"
for pkg in nftables avahi-utils; do
    status="$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)"
    if [[ "$status" == "install ok installed" ]]; then
        skip "package ${pkg}: already installed"
    elif ! command -v apt-get >/dev/null; then
        skip "package ${pkg}: not an apt host"
    elif act env DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" >/dev/null 2>&1; then
        did "package ${pkg}: installed"
    else
        skip "package ${pkg}: could not install (offline?) -- re-run when the box is online"
    fi
done

# --------------------------------------------------------------------------
say "5-7. root helpers, sudoers, nftables"
# ONE list for this script and for every over-the-air update:
# scripts/root_items.json, carried out by scripts/root_steps.py. Run by hand
# as root, the tree is taken as it is (CRLF made LF); an update runs the copy
# installed here (/usr/local/lib/irivai/root-steps.py) through its own
# no-argument sudoers rule and installs only what the release's signed
# manifest pins. Once this has run on a box, updates keep every piece below
# current by themselves.
AP_OPTOUT=/etc/irivai/ap-forwarding-allowed
if [[ $AP_FORWARD -eq 1 ]]; then
    if [[ -f /etc/systemd/system/irivai-ap-isolate.service ]]; then
        act systemctl disable --now irivai-ap-isolate.service >/dev/null 2>&1 || true
        act rm -f /etc/systemd/system/irivai-ap-isolate.service "${LIB}/ap-isolate.nft"
        act nft delete table inet irivai_ap_isolate >/dev/null 2>&1 || true
        did "hotspot isolation: removed (--allow-ap-forwarding)"
    else
        skip "hotspot isolation: not wanted (--allow-ap-forwarding)"
    fi
    # Remembered, so the updates that follow leave it off too.
    act install -d -m 755 /etc/irivai
    act touch "$AP_OPTOUT"
elif [[ -f "$AP_OPTOUT" ]]; then
    act rm -f "$AP_OPTOUT"
    note "hotspot isolation: wanted again (no --allow-ap-forwarding this time)"
fi
ROOT_ARGS=(--tree "$APP")
[[ $DRY -eq 1 ]] && ROOT_ARGS+=(--dry-run)
while IFS= read -r line; do
    case "$line" in
        "done: "*)    did "${line#done: }" ;;
        "skipped: "*) skip "${line#skipped: }" ;;
        "refused: "*) warn "${line#refused: }"; skip "refused: ${line#refused: }" ;;
        *)            if [[ -n "$line" ]]; then note "$line"; fi ;;
    esac
done < <(python3 -I "${HERE}/root_steps.py" "${ROOT_ARGS[@]}" 2>&1 || true)

# --------------------------------------------------------------------------
say "8. appliance defaults"
off_units() {
    local label="$1"; shift
    local unit acted=0
    for unit in "$@"; do
        systemctl cat "$unit" >/dev/null 2>&1 || continue
        if systemctl is-enabled "$unit" >/dev/null 2>&1 \
           || systemctl is-active "$unit" >/dev/null 2>&1; then
            act systemctl disable --now "$unit" >/dev/null 2>&1 || true
            acted=1
        fi
    done
    if [[ $acted -eq 1 ]]; then did "${label}: stopped and disabled"; else skip "${label}: already off or absent"; fi
}
if [[ $KEEP_VNC -eq 0 ]]; then off_units "VNC (wayvnc)" wayvnc.service wayvnc-control.service
else skip "VNC: kept (--keep-vnc)"; fi
if [[ $KEEP_RPCBIND -eq 0 ]]; then off_units "rpcbind" rpcbind.socket rpcbind.service
else skip "rpcbind: kept (--keep-rpcbind)"; fi
if [[ $KEEP_SERIAL_CONSOLE -eq 0 ]]; then
    off_units "UART login (serial-getty@ttyAMA10)" serial-getty@ttyAMA10.service
    cmdline=""
    for c in /boot/firmware/cmdline.txt /boot/cmdline.txt; do [[ -f "$c" ]] && { cmdline="$c"; break; }; done
    if [[ -z "$cmdline" ]]; then
        skip "kernel console: no cmdline.txt"
    elif ! grep -qE 'console=(serial0|ttyAMA10)' "$cmdline"; then
        skip "kernel console: already off the UART"
    elif [[ $DRY -eq 1 ]]; then
        note "would take console=serial0/ttyAMA10 out of ${cmdline}"
    else
        [[ -f "${cmdline}.irivai-bak" ]] || cp -p "$cmdline" "${cmdline}.irivai-bak"
        sed -i -E 's/console=(serial0|ttyAMA10)(,[0-9]+)?[[:space:]]*//g; s/[[:space:]]+$//' "$cmdline"
        if [[ "$(wc -l < "$cmdline")" -le 1 ]] && grep -q 'root=' "$cmdline"; then
            did "kernel console: off the UART from the next reboot (backup ${cmdline}.irivai-bak)"
        else
            cp -p "${cmdline}.irivai-bak" "$cmdline"
            warn "${cmdline} did not look right after the edit -- put back as it was"
            skip "kernel console: edit undone"
        fi
    fi
else
    skip "serial console: kept (--keep-serial-console)"
fi

# --------------------------------------------------------------------------
say "9. hardening"
if [[ $HARDEN -eq 0 ]]; then
    skip "hardening: not asked (--harden)"
elif [[ $DRY -eq 1 ]]; then
    note "would turn pi's NOPASSWD into a password grant, harden sshd (pi's password is not touched)"
else
    # pi's OS password is left exactly as it is: not set, not expired.
    skip "pi: OS password not changed (change it with passwd if you want)"
    # The same edit install.sh makes: on a copy, visudo-checked, renamed.
    sudoers_edit() {
        local file="$1" expr="$2" tmp
        [[ -f "$file" ]] || return 1
        tmp="$(mktemp "$(dirname "$file")/.irivai-sudoers.XXXXXX")" || return 2
        sed -E "$expr" "$file" > "$tmp"
        if cmp -s "$file" "$tmp"; then rm -f "$tmp"; return 1; fi
        if ! visudo -c -q -f "$tmp" >/dev/null 2>&1; then rm -f "$tmp"; return 2; fi
        chown root:root "$tmp" && chmod 0440 "$tmp" && mv -f "$tmp" "$file" \
            || { rm -f "$tmp"; return 2; }
        return 0
    }
    PI_NOPASSWD='^(pi[[:space:]]+ALL[[:space:]]*=[[:space:]]*\(ALL(:ALL)?\)[[:space:]]+)NOPASSWD:[[:space:]]*(ALL[[:space:]]*)$'
    # The same guard install.sh has: with no usable password on pi (empty,
    # "!" or "*") sudo would have nothing to ask for and pi would lose sudo,
    # so the grant is left as it is and said. Moot while this step still set
    # a new password first; it no longer does.
    pi_hash="$(getent shadow pi 2>/dev/null | cut -d: -f2 || true)"
    pi_can_ask=1
    [[ -z "$pi_hash" || "$pi_hash" == [\!\*]* ]] && pi_can_ask=0
    for f in /etc/sudoers /etc/sudoers.d/*; do
        [[ -f "$f" ]] || continue
        case "$(basename "$f")" in *.*|*~) [[ "$f" == /etc/sudoers ]] || continue ;; esac
        if [[ $pi_can_ask -eq 1 ]]; then
            rc=0; sudoers_edit "$f" "s/${PI_NOPASSWD}/\\1\\3/" || rc=$?
            [[ $rc -eq 0 ]] && did "sudo: pi's grant in ${f} now asks for pi's password"
            [[ $rc -eq 2 ]] && warn "could not edit ${f} safely -- left as it was"
        elif grep -qE "$PI_NOPASSWD" "$f"; then
            warn "pi has passwordless sudo in ${f} and no password sudo could ask for -- left as it is"
            warn "  Set one (sudo passwd pi) and re-run with --harden, so the grant can ask for it."
        fi
        if ! systemctl is-enabled nodered >/dev/null 2>&1 \
           && ! systemctl is-active nodered >/dev/null 2>&1; then
            rc=0
            sudoers_edit "$f" '/^nodered[[:space:]]+ALL[[:space:]]*=.*NOPASSWD:[[:space:]]*ALL[[:space:]]*$/d' || rc=$?
            [[ $rc -eq 0 ]] && did "sudo: nodered's passwordless grant removed from ${f}"
        fi
    done
    rm -f /run/sudo/ts/pi 2>/dev/null || true
    pi_rules="$(sudo -l -U pi 2>/dev/null || true)"
    [[ "$pi_rules" == *"NOPASSWD: ALL"* ]] \
        && warn "pi can still run anything as root without a password (sudo -l -U pi) -- a grant this script does not recognise"
    # sshd: no root, and key-only ONLY when pi has a key to get back in with.
    keys=0
    [[ -s /home/pi/.ssh/authorized_keys ]] \
        && grep -qE '^[[:space:]]*(ssh-|ecdsa-|sk-)' /home/pi/.ssh/authorized_keys && keys=1
    DROPIN=/etc/ssh/sshd_config.d/01-irivai-hardening.conf
    {
        echo "# Written by scripts/upgrade_steps.sh --harden (same file as install.sh)."
        echo "PermitRootLogin no"
        if [[ $keys -eq 1 ]]; then
            echo "PasswordAuthentication no"
            echo "KbdInteractiveAuthentication no"
        fi
    } > "$DROPIN"
    chmod 644 "$DROPIN"
    if sshd -t >/dev/null 2>&1; then
        systemctl reload ssh >/dev/null 2>&1 || systemctl reload sshd >/dev/null 2>&1 || true
        if [[ $keys -eq 1 ]]; then did "sshd: key-only login, no root login"
        else did "sshd: no root login"; warn "password SSH left ON: pi has no authorized_keys yet"; fi
    else
        rm -f "$DROPIN"
        warn "sshd -t rejected the drop-in -- removed, sshd left as it was"
    fi
fi

# --------------------------------------------------------------------------
say "10. hotspot key"
# Nothing to change. From 1.0.14 this step replaced a stored "iriv1234" with
# a random per-box key and the hub posted an Inbox notice about it; iriv1234
# is the intended default now (netconf.DEFAULT_AP_PASSWORD), and re-keying a
# radio during an update strands every device that joined it. Whatever key
# the box has is the key it keeps; Settings -> Network changes it.
skip "hotspot key: kept as stored (change it on Settings -> Network)"

# --------------------------------------------------------------------------
say "11. apply unit: re-spawned systems outlive the update"
# A child keeps its parent's cgroup however it detaches, so with systemd's
# default KillMode=control-group everything the applier started was stopped
# when its oneshot exited (boards .21/.22, 2026-10-05). Same setting the hub
# unit has had all along, for the same reason. Never while an update runs:
# a daemon-reload mid-apply is not worth the belt.
APPLY_UNIT=/etc/systemd/system/irivai-apply.service
if [[ ! -f "$APPLY_UNIT" ]]; then
    skip "apply unit: ${APPLY_UNIT} not found (no OTA layout on this box)"
elif grep -qE '^KillMode=process$' "$APPLY_UNIT"; then
    skip "apply unit: KillMode=process already set"
elif [[ "$(systemctl is-active irivai-apply.service 2>/dev/null || true)" == "active" ]]; then
    skip "apply unit: an update is running right now; run this again later"
else
    if grep -qE '^KillMode=' "$APPLY_UNIT"; then
        act sed -i -E 's/^KillMode=.*$/KillMode=process/' "$APPLY_UNIT"
    else
        act sed -i -E 's/^Type=oneshot$/Type=oneshot\nKillMode=process/' "$APPLY_UNIT"
    fi
    act systemctl daemon-reload
    did "apply unit: KillMode=process (re-spawned systems survive the updater's exit)"
fi

# --------------------------------------------------------------------------
if [[ $RESTART_HUB -eq 1 ]]; then
    act systemctl restart irivai-hub.service || warn "the hub did not restart"
fi

say "Summary"
printf '   did     %d step(s)\n' "${#DONE[@]}"
for d in "${DONE[@]+"${DONE[@]}"}"; do printf '     + %s\n' "$d"; done
printf '   skipped %d step(s)\n' "${#SKIPPED[@]}"
for s in "${SKIPPED[@]+"${SKIPPED[@]}"}"; do printf '     - %s\n' "$s"; done
[[ $DRY -eq 1 ]] && note "(dry run: nothing was changed)"
exit 0
