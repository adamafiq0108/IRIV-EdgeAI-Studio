# -*- coding: utf-8 -*-
# scripts/dev/harden_box.sh must not be able to lock the laptop out of a box.
#
# The first version (2026-10-04) had four ways to do exactly that, found in
# review before it ran on a second board:
#
#   H2  the documented invocation piped the SCRIPT and the SECRETS down the
#       same stdin (`bash -s` after `cat harden_box.sh`), so the two `read`s
#       took whatever script line came next as the password and the key;
#   M5  "pi is in the sudo group" was checked AFTER NOPASSWD had been removed
#       from sudoers -- a pi outside the group lost sudo before the check ran;
#   M1  pi's cached sudo ticket (/run/sudo/ts, global timestamp_type on this
#       image) survived, so "sudo asks for a password now" was not true for
#       15 minutes and nothing checked that it was;
#   L4  a drop-in that failed `sshd -t` was LEFT in place (sshd would refuse
#       to start at the next boot), sudoers was replaced non-atomically, and
#       the hotspot key was interpolated into a Python heredoc.
#
# Found while finishing the fix (2026-10-05): a secret line without its
# trailing newline made `read` return 1, and under `set -e` the script
# stopped with NO message; and the "is the app installed" check sat in step
# 4, AFTER the password, sudoers and sshd had already been changed. The first
# is pinned by actually running the secret-reading block in bash.
#
# The script only runs as root on a Pi, so this test is static: it reads the
# script and pins the ORDER and the SHAPE of the fixes -- the pre-checks and
# the length checks before the first change, the secrets never on an argv
# line, the header never feeding the script through its own stdin, the
# drop-in removed on failure, the sudoers rename, the ticket wipe and its
# post-check -- plus `bash -n` where a bash is installed.
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "dev", "harden_box.sh")

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


raw = open(SCRIPT, "rb").read()
check("no CR in the script (bash on the box would choke on CRLF)", b"\r" not in raw)
text = raw.decode("utf-8")
lines = text.split("\n")

# The header is every leading comment line; the code is the rest.
header_end = next(i for i, l in enumerate(lines) if l.strip() and not l.startswith("#"))
header = lines[:header_end]
code = lines[header_end:]


def first(pattern, where=None, start=0):
    """Index (into `lines`) of the first code line matching, or None."""
    rx = re.compile(pattern)
    for i in range(max(start, header_end), len(lines)):
        if rx.search(lines[i]):
            return i
    return None


# -- bash -n ------------------------------------------------------------------
bash = None
for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe",
             r"C:\Program Files\Git\usr\bin\bash.exe", "/usr/bin/bash", "/bin/bash"):
    if cand and os.path.exists(cand):
        try:
            # WSL's bash.exe stub exits non-zero with no distro; skip it.
            if subprocess.run([cand, "-c", "true"], capture_output=True,
                              timeout=20).returncode == 0:
                bash = cand
                break
        except Exception:
            continue
if bash:
    proc = subprocess.run([bash, "-n", SCRIPT.replace("\\", "/")],
                          capture_output=True, text=True, timeout=60)
    check("bash -n parses the script", proc.returncode == 0,
          (proc.stdout + proc.stderr).strip())
else:
    print("note bash -n: no usable bash on this machine, syntax check not run")

# -- H2: the header never pipes the script into its own stdin -------------------
hdr = "\n".join(header)
# The documented commands are the indented header lines ("#     ssh ...");
# the prose may NAME `bash -s` to say why it is gone, a command may not.
hdr_cmds = [l for l in header if re.match(r"#\s{4,}\S", l)]
check("header commands exist (scp, ssh, fd-3 form)", len(hdr_cmds) >= 3, "\n".join(hdr_cmds))
check("no header command uses `bash -s` (script on stdin)",
      not any(re.search(r"bash\s+-s\b", l) for l in hdr_cmds), "\n".join(hdr_cmds))
check("the ssh command runs the copied FILE under sudo -k -S",
      any("ssh pi@" in l and "sudo -k -S" in l and "bash /tmp/harden_box.sh" in l for l in hdr_cmds),
      "\n".join(hdr_cmds))
check("header does not `cat` the script into the ssh stdin",
      not re.search(r"cat\s+\S*harden_box\.sh", hdr))
check("header documents scp to /tmp and running the FILE",
      "scp scripts/dev/harden_box.sh" in hdr and "bash /tmp/harden_box.sh" in hdr)
check("header documents the stdin order: current pw, new pw, hotspot key",
      re.search(r"1\. the CURRENT pi password.*?2\. the NEW pi password.*?3\. the new hotspot key",
                hdr, re.S) is not None)
check("header says the new pi password is OPTIONAL (an empty line keeps it)",
      "OPTIONAL: an EMPTY line keeps" in hdr)

# -- order: pre-checks < reads < length checks < first side effect -------------
i_keys = first(r"authorized_keys\s*\]\s*\|\|\s*die")
i_group = first(r"id -nG pi \| grep -qw sudo \|\| die")
i_session = first(r"^AUTH=\$\(session_auth\)")
# A SECRET read: session_auth's `read -r ppid comm` (from ps) is not one.
i_read = first(r"^\s*(IFS= )?read -r.*\b(PI_PW|AP_KEY)\b")
i_app = first(r"\.venv/bin/python\" \] \|\| die")
i_cytron = first(r"^id cytron .*\|\| die")
i_len_pw = first(r"\$\{#PI_PW\} -ge 16")
i_len_key = first(r"\$\{#AP_KEY\} -ge 8")
side_effects = [
    first(r"\| chpasswd"),
    first(r"rm -f /etc/sudoers\.d/010_pi-nopasswd"),
    first(r"cat > \"?\$?\{?DROPIN|cat > /etc/ssh/sshd_config\.d"),
    first(r"^\s*nmcli connection modify"),
    first(r"systemctl disable"),
    first(r"rm -f /etc/ssh/ssh_host_"),
    first(r"journalctl --rotate"),
]
check("every anchor line exists",
      None not in (i_keys, i_group, i_session, i_read, i_len_pw, i_len_key) and None not in side_effects,
      f"keys={i_keys} group={i_group} session={i_session} read={i_read} "
      f"len_pw={i_len_pw} len_key={i_len_key} side_effects={side_effects}")
if None not in (i_keys, i_group, i_session, i_read, i_len_pw, i_len_key) and None not in side_effects:
    first_change = min(side_effects)
    check("authorized_keys check precedes the first secret read", i_keys < i_read)
    check("M5: sudo-group check precedes the first secret read", i_group < i_read)
    check("session-auth (key vs password) check precedes the first secret read", i_session < i_read)
    check("installed-app and cytron-user checks precede the first change (they sat in step 4)",
          None not in (i_app, i_cytron) and max(i_app, i_cytron) < min(side_effects),
          f"app={i_app} cytron={i_cytron} first_change={min(side_effects)}")
    check("the secret reads precede the length checks", i_read < i_len_pw < i_len_key)
    check("the length checks precede every side effect (first change at line %d)" % (first_change + 1),
          i_len_key < first_change)
    i_sudoers_edit = first(r"/etc/sudoers > |> \"\$tmp\"")
    check("M5: sudo-group check precedes the sudoers edit",
          i_sudoers_edit is not None and i_group < i_sudoers_edit)
    check("no `read` of a secret before the sudo-group check (the review's H2 ordering)",
          first(r"read -r.*(PI_PW|AP_KEY)") > i_group)

# -- the pi password floor matches the prompt --------------------------------
check("pi password floor is 16 (the prompt says 16+)", first(r"\$\{#PI_PW\} -ge 12") is None)

# -- the new pi password is optional (Tok Aba, 2026-10-07) ---------------------
# The box keeps the OS password it shipped with unless somebody chooses to
# change it. An empty line 2 skips chpasswd; step 2 still makes pi's sudo ask
# for a password, so keeping it is refused (before any change) when pi has
# no usable password for sudo to ask for.
i_floor = first(r"\$\{#PI_PW\} -ge 16")
check("an empty pi password passes the floor (only a NON-empty one must be 16+)",
      i_floor is not None
      and lines[i_floor].startswith('[ -z "$PI_PW" ] || [ ${#PI_PW} -ge 16 ]'))
i_keep = first(r'die "pi has no usable password and no new one was given')
check("keeping pi's password is refused when pi has none sudo could ask for, before any change",
      i_keep is not None and None not in side_effects and i_keep < min(side_effects)
      and "getent shadow pi" in "\n".join(lines[i_keep - 3:i_keep]))
i_chp = first(r"\| chpasswd")
check("chpasswd runs only when a new password was given",
      i_chp is not None and lines[i_chp - 3].strip() == 'if [ -z "$PI_PW" ]; then'
      and "skipped: no new pi password given" in lines[i_chp - 2])

# -- secrets never on an argv line ----------------------------------------------
check("chpasswd reads from a pipe, never argv",
      all("| chpasswd" in l for l in code if "chpasswd" in l and not l.strip().startswith("#")))
# Commands are joined across `\` continuations first: the nmcli modify puts
# the key on the NEXT physical line, which a line-by-line scan never sees.
cmds, buf = [], ""
for l in code:
    if not buf and l.strip().startswith("#"):
        continue
    if l.rstrip().endswith("\\"):
        buf += l.rstrip()[:-1] + " "
        continue
    cmds.append((buf + l).strip())
    buf = ""
SECRET = r"\$\{?(PI_PW|AP_KEY)\b"
# The ONE accepted exposure: the live Hotspot re-key runs from this root
# shell (not sudo-logged, not journaled -- the header says so). It must stay
# the psk property form, never nmcli's `password <key>` (the form the hub's
# boot path journals, M-5), and never carry the pi password.
nmcli_secret = [c for c in cmds if re.search(r"\bnmcli\b", c) and re.search(SECRET, c)
                and not c.startswith("live=")]
check("the only secret on an nmcli argv is the documented `802-11-wireless-security.psk \"$AP_KEY\"` modify",
      len(nmcli_secret) == 1 and "nmcli connection modify Hotspot" in nmcli_secret[0]
      and re.search(r'802-11-wireless-security\.psk "\$AP_KEY"', nmcli_secret[0]) is not None,
      "\n      ".join(nmcli_secret))
check("no `password $SECRET` on any nmcli/chpasswd command",
      not any(re.search(r"\bpassword\s+[\"']?" + SECRET, c) for c in cmds
              if "nmcli" in c or "chpasswd" in c))
pw_cmds = [c for c in cmds if re.search(r"\$\{?PI_PW\b", c)
           and not re.match(r"(\[|case |IFS= read|read )", c)
           and c != 'if [ -z "$PI_PW" ]; then']
check("the pi password is on no argv: its only use is printf (builtin) into chpasswd's stdin",
      len(pw_cmds) == 1 and pw_cmds[0].startswith("printf '%s\\n' \"pi:$PI_PW\" | chpasswd"),
      "\n      ".join(pw_cmds))

# -- L4: AP_KEY reaches Python through the environment, not the heredoc --------
i_py = first(r"runuser -u cytron -- \.venv/bin/python - <<")
check("python heredoc delimiter is quoted (no interpolation)",
      i_py is not None and re.search(r"<<\s*'EOF'", lines[i_py]) is not None)
check("AP_KEY is passed in the environment of the python step",
      i_py is not None and re.search(r"AP_KEY=\"\$AP_KEY\"\s+runuser", lines[i_py]) is not None)
if i_py is not None:
    body = []
    for l in lines[i_py + 1:]:
        if l == "EOF":
            break
        body.append(l)
    body_txt = "\n".join(body)
    check("heredoc body reads os.environ[\"AP_KEY\"] and never $AP_KEY",
          'os.environ["AP_KEY"]' in body_txt and "$AP_KEY" not in body_txt)
    # Save's own writer, so the box records that a person chose this key
    # ([ap].user_set); an install older than apconf (2026-10-06) as before.
    check("the key is stored through apconf.save_ap (records [ap].user_set), "
          "with the old set_wifi_mode path only where apconf is missing",
          "apconf.save_ap(apconf.ap_ssid(), key)" in body_txt
          and re.search(r'except ImportError:\n\s*netconf\.set_wifi_mode\("ap", key\)',
                        body_txt) is not None)
    check("heredoc warns when the panel's own ap_password differs from the key",
          '"panel"' in body_txt and "WARNING" in body_txt and "ap_password" in body_txt)
    # H1 is fixed in the code (e00fa68a): a fixed install ignores any panel
    # copy, so warning there would be a false alarm. Only old installs warn.
    check("the H1 warning fires only on installs WITHOUT the fix (no `def ap_password(` in panel.py)",
          '"def ap_password(" in open("core/services/panel.py"' in body_txt
          and re.search(r"if fixed:\n[^\n]*\nelif panel and panel != key:", body_txt) is not None)
    check("the warning never prints the old key itself",
          "%r" not in body_txt and "% panel" not in body_txt and "{panel" not in body_txt)

# -- L4: a bad drop-in is removed, never left behind ---------------------------
i_t = first(r"^\s*if ! sshd -t")
check("sshd -t failure branch removes the drop-in before dying",
      i_t is not None and re.search(r"rm -f \"?\$?\{?DROPIN|rm -f /etc/ssh/sshd_config\.d/99-irivai\.conf",
                                    "\n".join(lines[i_t:i_t + 4])) is not None
      and "die" in "\n".join(lines[i_t:i_t + 4]))
check("no `drop-in left in place` wording survives", "left in place" not in text)

# -- L4: sudoers replaced atomically after visudo -c ---------------------------
i_visudo = first(r"visudo -c -q -f \"\$tmp\"")
i_mv = first(r"^\s*mv -f \"\$tmp\" /etc/sudoers")
check("sudoers: visudo -c on the temp file, then an atomic mv (same filesystem)",
      None not in (i_visudo, i_mv) and i_visudo < i_mv
      and first(r"mktemp /etc/sudoers") is not None)
check("sudoers: no `install ... /etc/sudoers` (non-atomic write) remains",
      first(r"install .*\s/etc/sudoers\s*$") is None)

# -- M1: ticket wipe and post-check ----------------------------------------------
i_ts = first(r"rm -rf /run/sudo/ts/\*")
i_post = first(r"runuser -u pi -- sudo -n true")
check("M1: /run/sudo/ts is wiped", i_ts is not None)
check("M1: `runuser -u pi -- sudo -n true` post-check comes after the wipe and dies when it SUCCEEDS",
      None not in (i_ts, i_post) and i_ts < i_post
      and lines[i_post].strip().startswith("if runuser")
      and "die" in lines[i_post + 1])
check("M1: prints the `sudo -K` advice", "sudo -K" in text)
check("M1: the ticket wipe comes after the last change (end of script)",
      i_ts is not None and i_ts > max(x for x in side_effects if x is not None))

# -- a short or newline-less input reaches the length checks, not set -e -------
read_lines = [l for l in code if re.search(r"read -r.*\b(PI_PW|AP_KEY)\b", l)]
check("every secret `read` ends in `|| true` (EOF must reach the length checks)",
      len(read_lines) == 6 and all("|| true" in l for l in read_lines),
      "\n      ".join(read_lines))
i_block = first(r"^if \{ true >&3; \}")
i_cr = first(r"^case \"\$PI_PW\$AP_KEY\"")
if bash and None not in (i_block, i_cr):
    # Run the script's OWN secret block (fd-3 test, reads, length and CR
    # checks) under set -euo pipefail with stdin as a pipe, the ssh case.
    prog = ("set -euo pipefail\n"
            "die() { printf 'STOP: %s\\n' \"$*\"; exit 1; }\n"
            + "\n".join(lines[i_block:i_cr + 1])
            + "\necho \"PASSED pw=${#PI_PW} key=${#AP_KEY}\"\n")

    def run_block(stdin):
        r = subprocess.run([bash, "-c", prog], input=stdin.encode(),
                           capture_output=True, timeout=30)
        return r.returncode, (r.stdout + r.stderr).decode(errors="replace")

    pw, key = "p" * 20, "k" * 12
    cases = [
        ("two lines with newlines", f"{pw}\n{key}\n", 0, "PASSED pw=20 key=12"),
        ("key without its newline (was a SILENT set -e exit)", f"{pw}\n{key}", 0, "PASSED pw=20 key=12"),
        ("no input at all", "", 1, "STOP: hotspot key must be 8-63"),
        ("empty password line keeps pi's password", f"\n{key}\n", 0, "PASSED pw=0 key=12"),
        ("only the password", f"{pw}\n", 1, "STOP: hotspot key must be 8-63"),
        ("15-char password", "p" * 15 + f"\n{key}\n", 1, "STOP: pi password too short"),
        ("7-char key", f"{pw}\n" + "k" * 7 + "\n", 1, "STOP: hotspot key must be 8-63"),
        ("64-char key", f"{pw}\n" + "k" * 64 + "\n", 1, "STOP: hotspot key must be 8-63"),
        ("CRLF input", f"{pw}\r\n{key}\r\n", 1, "STOP: a secret ends in CR"),
        ("spaces, quotes, backslash kept verbatim", "  a 'b\" c\\d  xyz1234\n" + key + "\n", 0, "PASSED pw=20 key=12"),
    ]
    for label, stdin, want_rc, want_out in cases:
        rc, out = run_block(stdin)
        check(f"secret block, {label}: exit {want_rc}, '{want_out}'",
              rc == want_rc and want_out in out, f"rc={rc} out={out.strip()!r}")
elif not bash:
    print("note secret block: no usable bash, behaviour cases not run")
else:
    check("secret block anchors found", False, f"block={i_block} cr={i_cr}")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
