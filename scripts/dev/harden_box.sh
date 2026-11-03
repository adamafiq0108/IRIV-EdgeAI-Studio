#!/usr/bin/env bash
# harden_box.sh -- per-box hardening of a customer board (the Pipi list,
# first applied to the v1.0.13 board b2ca63532b35eb4f on 2026-10-04).
#
# Run it ON the box, as root, from a KEY-authenticated ssh session. Copy the
# file over first; the script is NEVER fed through the stdin that carries the
# secrets (a `bash -s` reading the script from the same pipe as the secrets
# made the two `read`s consume script lines, or the shell execute a password):
#
#     scp scripts/dev/harden_box.sh pi@<box>:/tmp/harden_box.sh
#     ssh pi@<box> 'sudo -k -S -p "" bash /tmp/harden_box.sh --serial <serial> [--serial-console-off]' \
#         < <(printf '%s\n%s\n%s\n' "$PI_PASSWORD_NOW" "$PI_PASSWORD_NEW" "$AP_KEY")
#
# stdin order, one per line: 1. the CURRENT pi password (consumed by sudo -S;
# `-k` makes sudo ask even with a fresh timestamp, so the line count is
# fixed), 2. the NEW pi password (16+ chars) -- OPTIONAL: an EMPTY line keeps
# pi's password exactly as it is (Tok Aba, 2026-10-07: the box keeps the OS
# password it shipped with unless somebody chooses to change it), and
# 3. the new hotspot key (8-63). With $PI_PASSWORD_NEW unset the printf
# above sends that empty line. From a shell that is ALREADY root (sudo closes
# fd 3 before it execs, so this does not work through sudo) the two secrets
# can come on fd 3 instead:
#
#     bash /tmp/harden_box.sh --serial <serial> 3< <(printf '%s\n%s\n' "$PI_PASSWORD_NEW" "$AP_KEY")
#
# or run `sudo bash /tmp/harden_box.sh --serial <serial>` from a terminal and
# answer the two prompts (Enter at the first keeps pi's password). Secrets are
# NEVER taken on argv: argv is in `ps`, in shell history and in the sudo
# journal.
#
# What it does, in order. EVERY check that could leave the laptop locked out
# runs before the first change is made:
#   0. refuses to run unless the serial matches, pi has an authorized_key, pi
#      is in the sudo group (step 2 removes NOPASSWD; without the group pi
#      would lose sudo entirely), and -- when it can be told from the sshd
#      journal -- this very session logged in with a key, not a password
#      (step 3 turns password login off); then reads both secrets and checks
#      their lengths, and when no new pi password was given, that pi HAS a
#      usable one (step 2 makes sudo ask for it; with none, pi would lose
#      sudo). Nothing has been touched if any of that fails;
#   1. (the authorized_keys count is printed);
#   2. new pi password via chpasswd on stdin, only when one was given
#      (skipped otherwise: pi keeps the password it has); NOPASSWD removed from
#      /etc/sudoers (the Raspberry Pi image puts `pi ALL=(ALL) NOPASSWD: ALL`
#      in the main file, not only in sudoers.d/010_pi-nopasswd) -- the edited
#      file is parsed with visudo -c and swapped in with a rename, so sudoers
#      is never half-written;
#   3. sshd: key-only login, no root over ssh (drop-in 99-irivai.conf). A
#      drop-in that fails `sshd -t` is REMOVED again, never left behind to
#      stop sshd at the next boot;
#   4. hotspot key: staged the way the product does it (apconf.save_ap
#      -> config/network.local.toml, read live by the hub, the Network page
#      and the front panel after its next reload), applied to the live
#      Hotspot profile from THIS root shell (nmcli argv is not sudo-logged
#      here), then the journal is rotated and vacuumed so the plaintext
#      `nmcli ... password` line the hub wrote at boot (Yaya M-5) is gone.
#      The key reaches the Python staging step through the environment, not
#      interpolated into code. On an install OLDER than the H1 fix (no
#      panel.ap_password() in core/services/panel.py) whose panel config
#      carries its own ap_password, prints a WARNING: that code's OLED keeps
#      printing the panel copy, i.e. the old key, until the box is updated;
#   5. wayvnc (5900) and rpcbind (111) disabled;
#   6. cloned-image fix: new ssh host keys and machine-id; the new
#      fingerprints are printed so the laptop can replace its known_hosts
#      entry (ssh-keygen -R <old addresses>; ssh-keyscan -t ed25519 <box>);
#   7. --serial-console-off: serial-getty@ttyAMA10 disabled and console=
#      serial0/ttyAMA10 dropped from cmdline.txt (effective at reboot);
#   8. the sudo timestamps under /run/sudo/ts are cleared (this image uses a
#      global timestamp_type, so pi's cached ticket would otherwise keep
#      NOPASSWD-like sudo alive for 15 minutes) and `sudo -n true` as pi is
#      checked to FAIL: sudo now asks pi for pi's password (the new one, or
#      the one pi already had when line 2 was empty).
#
# NOT here, deliberately: the NVMe mount and the store move (a per-box
# storage decision, see core/config.py store_dir), and the dashboard
# accounts (hub-managed). The irivai services are never restarted.
#
# Caveats that stay open until fixed in the hub: ap_enable() still passes
# the key on nmcli's argv at boot (M-5), so the journal line comes back the
# next time the hub has to raise the AP itself -- re-run step 4's vacuum
# then; and on installs older than the H1 fix the panel's own ap_password
# wins over the network key (step 4 warns).

set -euo pipefail

EXPECT_SERIAL=""
SERIAL_CONSOLE_OFF=0
while [ $# -gt 0 ]; do
    case "$1" in
        --serial) EXPECT_SERIAL="$2"; shift 2 ;;
        --serial-console-off) SERIAL_CONSOLE_OFF=1; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf 'STOP: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo)"
SERIAL=$(awk '/^Serial/ {print $3}' /proc/cpuinfo)
[ -n "$EXPECT_SERIAL" ] || die "--serial <expected serial> is required (this box: $SERIAL)"
[ "$SERIAL" = "$EXPECT_SERIAL" ] || die "serial mismatch: box is $SERIAL, expected $EXPECT_SERIAL"

# -- 0. never lock the laptop out: every pre-check before any change ---------
say "0. pre-checks (nothing is changed until all pass)"
[ -s /home/pi/.ssh/authorized_keys ] || die "pi has no authorized_keys; add the laptop key first"
echo "ok: $(grep -c . /home/pi/.ssh/authorized_keys) key(s) in /home/pi/.ssh/authorized_keys"
id -nG pi | grep -qw sudo || die "pi is not in the sudo group; removing NOPASSWD would take sudo away entirely"
echo "ok: pi is in the sudo group"
# Step 4 needs the installed app and its service user; find out NOW, not
# after the password, sudoers and sshd have already been changed.
APP=/opt/irivai-systor
[ -x "$APP/.venv/bin/python" ] || die "$APP/.venv missing; is this an installed box?"
id cytron >/dev/null 2>&1 || die "no 'cytron' user; step 4 stages the key as that user"
echo "ok: $APP installed, user cytron exists"

# How did THIS session log in? Walk up from this shell to the per-session
# sshd ("sshd: pi@notty"); its parent is the privileged monitor, and that
# pid is on the "Accepted publickey|password for pi" line in the journal.
session_auth() {
    local pid=$$ comm ppid
    while [ "$pid" -gt 1 ]; do
        read -r ppid comm < <(ps -o ppid=,comm= -p "$pid" 2>/dev/null) || break
        [ -n "${ppid:-}" ] || break
        if [ "$comm" = sshd ] || [ "$comm" = "sshd-session" ]; then
            for p in "$pid" "$ppid"; do
                journalctl -b -q --no-pager -o cat _PID="$p" 2>/dev/null \
                    | grep -oE 'Accepted (publickey|password|keyboard-interactive/pam)' \
                    | tail -1 | awk '{print $2}' | grep . && return 0
            done
            echo unknown; return 0
        fi
        pid=$ppid
    done
    echo unknown
}
AUTH=$(session_auth)
case "$AUTH" in
    publickey) echo "ok: this session authenticated with a key" ;;
    password|keyboard-interactive/pam)
        die "this session logged in with a PASSWORD; step 3 would lock it out. Log in with the key first" ;;
    *) echo "note: could not tell how this session authenticated (not an ssh session, or no journal); relying on the authorized_keys check" ;;
esac

# -- secrets: fd 3, prompt, or the stdin lines after sudo's password ---------
# Read AFTER the pre-checks: a box that fails them never sees the secrets.
if { true >&3; } 2>/dev/null; then
    IFS= read -r -u 3 PI_PW || true
    IFS= read -r -u 3 AP_KEY || true
elif [ -t 0 ]; then
    IFS= read -r -s -p "new pi password (16+ chars, Enter keeps the current one): " PI_PW || true; echo
    IFS= read -r -s -p "new hotspot key (8-63 chars): " AP_KEY || true; echo
else
    IFS= read -r PI_PW || true
    IFS= read -r AP_KEY || true
fi
# `|| true`: a last line without its newline, or a short input, makes `read`
# return 1 -- under set -e that was a SILENT exit. Whatever arrived (maybe
# nothing) goes to the length checks, which say what is wrong. An EMPTY pi
# password is not short: it means "keep pi's password" (step 2 skips chpasswd).
[ -z "$PI_PW" ] || [ ${#PI_PW} -ge 16 ] || die "pi password too short (16+ chars, or an empty line to keep it); nothing changed"
[ ${#AP_KEY} -ge 8 ] && [ ${#AP_KEY} -le 63 ] || die "hotspot key must be 8-63 chars; nothing changed"
case "$PI_PW$AP_KEY" in *$'\r'*) die "a secret ends in CR (CRLF input?); nothing changed" ;; esac
# Keeping pi's password is only safe when there IS one: step 2 turns pi's
# sudo into sudo WITH a password, and a locked or empty pi password leaves
# sudo nothing to ask for -- pi would lose sudo. Refused before any change.
if [ -z "$PI_PW" ]; then
    pi_hash=$(getent shadow pi 2>/dev/null | cut -d: -f2 || true)
    case "$pi_hash" in
        ''|'!'*|'*'*) die "pi has no usable password and no new one was given; sudo would have nothing to ask for. Give one on line 2; nothing changed" ;;
    esac
fi

# -- 1. ----------------------------------------------------------------------
say "1. key login possible for pi: yes (checked above)"

# -- 2. pi password + sudo with password ------------------------------------
say "2. pi password and sudo"
if [ -z "$PI_PW" ]; then
    echo "skipped: no new pi password given; pi keeps the password it has"
else
    printf '%s\n' "pi:$PI_PW" | chpasswd
    echo "ok: pi password changed"
fi
rm -f /etc/sudoers.d/010_pi-nopasswd
if grep -qE '^pi[[:space:]]+ALL=\(ALL(:ALL)?\)[[:space:]]+NOPASSWD:' /etc/sudoers; then
    cp -p /etc/sudoers "/root/sudoers.bak-$(date +%F)"
    tmp=$(mktemp /etc/sudoers.new.XXXXXX)
    sed -E '/^pi[[:space:]]+ALL=\(ALL(:ALL)?\)[[:space:]]+NOPASSWD:/d' /etc/sudoers > "$tmp"
    if ! visudo -c -q -f "$tmp"; then
        rm -f "$tmp"
        die "edited sudoers does not parse; /etc/sudoers untouched"
    fi
    chown root:root "$tmp" && chmod 0440 "$tmp"
    mv -f "$tmp" /etc/sudoers        # same filesystem: an atomic rename
    visudo -c -q || die "/etc/sudoers does not parse after the swap; restore /root/sudoers.bak-$(date +%F)"
fi
echo "ok: NOPASSWD lines for pi left: $(grep -cE '^pi[[:space:]].*NOPASSWD' /etc/sudoers /etc/sudoers.d/* 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')"
grep -nE 'NOPASSWD:[[:space:]]*ALL' /etc/sudoers /etc/sudoers.d/* 2>/dev/null \
    | grep -v '^#' && echo "note: other NOPASSWD: ALL grants above are left as found (decide separately)" || true

# -- 3. sshd ----------------------------------------------------------------
say "3. sshd: key-only, no root"
DROPIN=/etc/ssh/sshd_config.d/99-irivai.conf
cat > "$DROPIN" <<'EOF'
# IRIV Edge AI per-box hardening: key-only login for every account, no root
# over ssh. The pi password is for sudo at the console only.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
chmod 0644 "$DROPIN"
if ! sshd -t; then
    rm -f "$DROPIN"
    die "sshd -t failed; the drop-in was removed again (sshd must start at boot), ssh NOT reloaded"
fi
systemctl reload ssh
sshd -T | grep -iE '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication) '

# -- 4. hotspot key ---------------------------------------------------------
say "4. hotspot key"
# The key goes in through the environment (quoted heredoc: nothing is
# interpolated into the code; a key with a quote or a backslash cannot break
# out of a string literal).
( cd "$APP" && AP_KEY="$AP_KEY" runuser -u cytron -- .venv/bin/python - <<'EOF'
import os
from core.services import netconf
from core import config
key = os.environ["AP_KEY"]
# Through Save's own writer where the install has it (apconf, 2026-10-06), so
# [ap].user_set records that a person chose this key; older installs as before.
try:
    from core.services import apconf
except ImportError:
    netconf.set_wifi_mode("ap", key)
else:
    netconf.set_wifi_mode("ap")
    apconf.save_ap(apconf.ap_ssid(), key)
ap = config.load("network").get("ap") or {}
print("staged in config/network.local.toml:", ap.get("password") == key)
# H1: code before the fix let a panel copy of the key win on the OLED. The
# fixed code has panel.ap_password() reading the network key and ignores any
# stored copy, so only an OLD install with a differing copy is warned about.
# The source is read, not imported (the panel module drives hardware).
fixed = "def ap_password(" in open("core/services/panel.py", encoding="utf-8").read()
panel = (config.load("panel").get("panel") or {}).get("ap_password")
if fixed:
    print("front panel: prints the network key (H1 fix present)")
elif panel and panel != key:
    print("WARNING: this install predates the H1 fix and the front panel has its"
          " own ap_password in config/panel.toml that differs from the new key;"
          " the OLED keeps printing THAT until the box is updated, or the field"
          " is set to the new key in Settings -> Front Panel.")
EOF
)
if nmcli -t -f NAME connection show | grep -qx Hotspot; then
    nmcli connection modify Hotspot 802-11-wireless-security.key-mgmt wpa-psk \
        802-11-wireless-security.psk "$AP_KEY"
    if nmcli -t -f NAME connection show --active | grep -qx Hotspot; then
        nmcli connection up Hotspot >/dev/null && echo "Hotspot re-keyed and up"
    fi
    live=$(nmcli -s -g 802-11-wireless-security.psk connection show Hotspot)
    [ "$live" = "$AP_KEY" ] && echo "live psk matches" || die "live psk does not match the staged key"
else
    echo "no Hotspot profile yet; the hub raises it from the staged key at boot"
fi
before=$(journalctl --no-pager -q 2>/dev/null | grep -c 'nmcli.*password' || true)
journalctl --rotate && journalctl --vacuum-time=1s >/dev/null 2>&1
after=$(journalctl --no-pager -q 2>/dev/null | grep -c 'nmcli.*password' || true)
echo "journal plaintext nmcli lines: $before -> $after"
iw dev wlan0 info 2>/dev/null | grep -E 'ssid|type' || true

# -- 5. wayvnc + rpcbind ----------------------------------------------------
say "5. wayvnc and rpcbind off"
systemctl disable --now wayvnc.service wayvnc-control.service 2>/dev/null || true
systemctl disable --now rpcbind.socket rpcbind.service 2>/dev/null || true
sleep 1
ss -lntup | grep -E ':5900 |:111 ' && die "something still listens on 5900/111" || echo "ok: nothing on 5900 or 111"

# -- 6. cloned image: host keys + machine-id --------------------------------
say "6. new ssh host keys and machine-id"
rm -f /etc/ssh/ssh_host_*
ssh-keygen -A >/dev/null
rm -f /etc/machine-id
systemd-machine-id-setup >/dev/null
[ -L /var/lib/dbus/machine-id ] || cp /etc/machine-id /var/lib/dbus/machine-id
sshd -t && systemctl reload ssh
echo "machine-id: $(cat /etc/machine-id)"
for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done
echo "laptop: ssh-keygen -R <every old address of this box>; ssh-keyscan -t ed25519 <box> >> ~/.ssh/known_hosts; compare the fingerprint with the ED25519 line above"

# -- 7. serial console (opt-in) ---------------------------------------------
if [ "$SERIAL_CONSOLE_OFF" = 1 ]; then
    say "7. serial console off (effective at reboot)"
    systemctl disable --now serial-getty@ttyAMA10.service 2>/dev/null || true
    cp -p /boot/firmware/cmdline.txt "/boot/firmware/cmdline.txt.bak-$(date +%F)"
    sed -i -E 's/console=(serial0|ttyAMA10)(,[0-9]+)? ?//g' /boot/firmware/cmdline.txt
    grep -q 'console=tty1' /boot/firmware/cmdline.txt || die "console=tty1 missing from cmdline.txt after edit; restore the .bak"
    echo "cmdline: $(cat /boot/firmware/cmdline.txt)"
fi

# -- 8. no cached sudo ticket survives ---------------------------------------
say "8. sudo timestamps cleared"
rm -rf /run/sudo/ts/* 2>/dev/null || true
if runuser -u pi -- sudo -n true >/dev/null 2>&1; then
    die "pi can still sudo WITHOUT a password (a NOPASSWD grant or a live ticket remains); check the notes under step 2"
fi
echo "ok: 'sudo -n true' as pi is refused; sudo now asks pi for pi's password"
echo "in any other open pi shell on this box run: sudo -K"

say "done. Not touched: irivai services, NVMe/store, dashboard accounts."
