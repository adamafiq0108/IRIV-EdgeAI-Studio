# scripts/upgrade_steps.sh: the one-time root steps an OTA update cannot do,
# safe to re-run, shipped in every release, secrets never on argv.
#
# An over-the-air update swaps the release tree as the service account; the
# root-owned pieces this release changed (sudoers, helpers, nftables units,
# the hub unit's baked --port, the gateway config, the image's listeners)
# stay as they were until somebody runs this once. Static checks, like
# smoke_hardenbox.py: the script parses, ships, guards every step with a
# check, says what it did and skipped, leaves helpers, sudoers and nftables to
# the list every update uses too (scripts/root_items.json, pinned against
# install.sh by smoke_rootsteps.py), and with --harden never reads, sets or
# expires the pi account's OS password (Tok Aba, 2026-10-07).
#
#     python tests/smoke_upgradesteps.py
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


path = os.path.join(ROOT, "scripts", "upgrade_steps.sh")
with open(path, encoding="utf-8", newline="") as fh:
    up = fh.read()
with open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8") as fh:
    inst = fh.read()

bash = shutil.which("bash")
if bash:
    r = subprocess.run([bash, "-n", path.replace("\\", "/")], capture_output=True, text=True)
    check("bash -n parses it", r.returncode == 0, r.stderr)
check("LF line endings (the kernel reads its shebang)", "\r" not in up)
check("it ships: not a bench script, not under scripts/dev",
      "scripts/upgrade_steps.sh" not in inst.split("BENCH_ONLY=(", 1)[1].split(")", 1)[0])
check("root only, from an installed release",
      '[[ $EUID -eq 0 ]] || die' in up and "scripts/wifi_profile.py" in up)
check("set -euo pipefail, and no condition pipes into grep -q",
      "set -euo pipefail" in up
      and not [ln for ln in up.splitlines()
               if ln.strip().startswith(("if ", "elif ")) and "| grep -q" in ln])

# -- every step checks before it acts ------------------------------------------
guards = {
    "hub unit only when it bakes --port": "grep -qE ' --port [0-9]+' \"$UNIT\"",
    "NetworkManager 1.52+ for the link-local drop-in": "sort -V | head -1)\" != \"1.52\"",
    "files installed only when they differ (cmp)": 'cmp -s <(sed \'s/\\r$//\' "$src") "$dest"',
    "packages only when missing": "dpkg-query -W -f='${Status}'",
    "units disabled only when present and on": 'systemctl cat "$unit" >/dev/null 2>&1 || continue',
    "cmdline edited only when the console is still on the UART": "grep -qE 'console=(serial0|ttyAMA10)' \"$cmdline\"",
}
for label, needle in guards.items():
    check(label, needle in up, needle)
check("helpers, sudoers and nftables come from the shared list, through root_steps.py",
      'python3 -I "${HERE}/root_steps.py" "${ROOT_ARGS[@]}"' in up
      and '[[ $DRY -eq 1 ]] && ROOT_ARGS+=(--dry-run)' in up)
check("...whose lines land in the summary (done, skipped, refused)",
      '"done: "*)    did' in up and '"skipped: "*) skip' in up and '"refused: "*) warn' in up)
check("a summary of what it did and what it skipped, and a dry run",
      'say "Summary"' in up and "did()" in up and "skip()" in up and "--dry-run" in up)
for flag in ("--keep-vnc", "--keep-rpcbind", "--keep-serial-console", "--allow-ap-forwarding",
             "--harden"):
    check(f"{flag} is parsed", f"{flag})" in up)
check("hardening is off unless asked", "HARDEN=0" in up and 'if [[ $HARDEN -eq 0 ]]; then' in up)

# -- the same rules install.sh writes ----------------------------------------------
# One list now (scripts/root_items.json): smoke_rootsteps.py holds every helper,
# sudoers rule and nftables unit in it to install.sh's text, and this script
# keeps no copy of its own to drift.
check("no rule, helper list or unit text of its own",
      "BOX_RULE=" not in up and "HOST_RULE=" not in up and "for pair in" not in up
      and "nft_unit" not in up and "put_sudoers" not in up)

# -- the pi OS password is left alone (Tok Aba, 2026-10-07) ------------------------
# --harden used to read a NEW pi password from stdin and chpasswd it. The box
# now keeps the OS password it shipped with; the owner runs passwd if they
# want another. Nothing reads, sets, expires or asks for one.
up_code = "\n".join(ln for ln in up.splitlines() if not ln.strip().startswith("#"))
check("--harden reads no password (no read of a secret, no prompt)",
      "PI_PW" not in up_code and "read -r" not in up_code.split('say "1. hub unit', 1)[0])
check("...and never sets, changes or expires pi's password",
      "chpasswd" not in up_code and "chage" not in up_code
      and not re.search(r"\bpasswd\s+(-\S+\s+)*pi\b", up_code.replace("sudo passwd pi)", "")))
check("...and says so in its summary",
      'skip "pi: OS password not changed (change it with passwd if you want)"' in up)
check("--harden still refuses with nothing touched when there is no pi account",
      up.index('id -u pi >/dev/null 2>&1 || die "--harden: there is no pi account')
      < up.index('say "1. hub unit'))
check("no secret is a command-line option", "--password" not in up and "--pi-password" not in up)

# -- the rest of --harden is unchanged ------------------------------------------------
h9 = up.split('say "9. hardening"', 1)[1].split('say "10. hotspot key"', 1)[0]
check("pi's NOPASSWD: ALL becomes a password grant (visudo-checked copy, renamed)",
      'sudoers_edit "$f" "s/${PI_NOPASSWD}/\\\\1\\\\3/"' in h9
      and 'visudo -c -q -f "$tmp"' in h9 and 'mv -f "$tmp" "$file"' in h9)
check("...but not when pi has no usable password (sudo could not ask; said instead)",
      '"$pi_hash" == [\\!\\*]*' in h9 and "no password sudo could ask for" in h9)
check("nodered's NOPASSWD: ALL goes only while Node-RED is off",
      "! systemctl is-enabled nodered" in h9 and "/^nodered[[:space:]]+ALL" in h9)
check("sshd: PermitRootLogin no always, password login off only when pi has a key",
      'echo "PermitRootLogin no"' in h9
      and re.search(r'if \[\[ \$keys -eq 1 \]\]; then\s+echo "PasswordAuthentication no"', h9)
      is not None
      and 'rm -f "$DROPIN"' in h9)

# -- step 10: the hotspot key is left exactly as stored --------------------------
# From 1.0.14 this step replaced a stored "iriv1234" with a random key (Pipi's
# gate F1) and the hub nagged in the Inbox. iriv1234 is the default again
# (Tok Aba, 2026-10-07): the step now changes nothing, so a box on iriv1234
# stays on it and a box with any other key keeps it here. A key the 10-05..07
# generator wrote goes back to iriv1234 at the hub's start instead
# (apdefault.py, smoke_apdefault), never in this root step.
step10 = up.split('say "10. hotspot key"', 1)
check("step 10 still exists (the numbering other steps are pinned by)", len(step10) == 2)
if len(step10) == 2:
    body = step10[1].split('say "11.', 1)[0]
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    check("...and leaves a stored iriv1234 alone: no Python, no write, no re-key, no restart",
          "<<'PY'" not in code and "save_local" not in code and "ensure_ap_password" not in code
          and "network.local.toml" not in code and "RESTART_HUB=1" not in code
          and "runuser" not in code, code)
    check("...and says so in the summary", 'skip "hotspot key: kept as stored' in code)
check("nothing else in upgrade_steps.sh touches the hotspot key",
      "ap_password" not in up and "network.local.toml" not in up
      and "is_shipped" not in up and "user_set" not in up)

# The unit fix, RUN: the sed takes the baked port and nothing else.
if bash:
    line = "ExecStart=/opt/irivai-systor/.venv/bin/python hub_server.py --port 5100\n"
    r = subprocess.run([bash, "-c", "sed -E 's/ --port [0-9]+//'"], input=line,
                       capture_output=True, text=True)
    check("the --port fix leaves the rest of ExecStart alone",
          r.stdout == "ExecStart=/opt/irivai-systor/.venv/bin/python hub_server.py\n", r.stdout)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
