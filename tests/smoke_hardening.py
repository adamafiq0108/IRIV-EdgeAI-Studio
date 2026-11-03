# The installer's hardening check cannot pass by failing.
#
# WHAT HAPPENED. Installing onto a Debian 13 board printed "no default
# passwords, SSH not accepting passwords". The board's pi account still had
# `raspberry`, and sshd was accepting passwords. Both halves were wrong, for
# two different reasons, and both reported as safety.
#
#   1. The password test ran `import crypt, spwd` in python3. Both modules
#      were REMOVED in Python 3.13 (PEP 594). The import raised, 2>/dev/null
#      ate it, the `if` went false, and "no default password" was inferred
#      from a check that never ran.
#
#   2. The SSH test grepped the config files for `PasswordAuthentication yes`.
#      Password authentication is ON BY DEFAULT, so a board with no such line
#      anywhere accepts passwords -- and the grep, finding nothing, called
#      that hardened.
#
# WHAT IS PINNED. That neither removed module comes back; that the question is
# put to sshd rather than to the files; and -- the one that actually matters
# -- that a check which cannot run reports that it could not run, instead of
# printing a pass. A silent skip is indistinguishable from a clean board,
# which is how an appliance ships with its documented default password.
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = io.open(os.path.join(ROOT, "scripts", "install.sh"),
                  encoding="utf-8").read()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


block = INSTALL.split('say "Hardening check"', 1)
check("install.sh still has a hardening check", len(block) == 2)
if len(block) != 2:
    sys.exit(1)
block = block[1]

# -- the two removed modules stay removed ----------------------------------------
check("the probe's exit code is caught, so set -e does not end the install on \"not default\"",
      "<<'PY' || rc=$?" in block and "case $rc in" in block and "case $? in" not in block)
check("it does not import Python's crypt, which no longer exists",
      not re.search(r"\bimport\s+crypt\b", block)
      and not re.search(r"\bcrypt\.crypt\(", block))
# A COMMENT may name them -- the paragraph explaining what went wrong is
# worth more than the grep is strict, and the same allowance is made in
# smoke_dialogs.py for window.confirm.
code = chr(10).join(ln for ln in block.splitlines()
                    if not ln.strip().startswith("#"))
check("...nor spwd, removed in the same release", "spwd" not in code)
check("it asks libcrypt directly instead, which cannot be removed from under it",
      "ctypes" in block and "libcrypt" in block)
check("...and reads /etc/shadow itself, rather than through a stdlib module",
      "/etc/shadow" in block)

# -- the SSH question goes to sshd ------------------------------------------------
check("it asks sshd what is actually in force",
      "sshd -T" in block and "passwordauthentication" in block)

# -- and the whole point: silence is not safety -----------------------------------
check("a check that could not run is counted apart from a check that passed",
      "unchecked=0" in block and "unchecked=$((unchecked + 1))" in block)
check("...and the pass line requires BOTH no problems and nothing unchecked",
      re.search(r"if \[\[ \$problems -eq 0 && \$unchecked -eq 0 \]\]", block)
      is not None)
check("...with its own sentence, so an operator is told to verify by hand",
      "NOT confirmed" in block)
check("the password test reports three outcomes, not two",
      "sys.exit(2)" in block and re.search(r"case \$rc in", block) is not None)

# -- a locked account has no password to be default -------------------------------
check("a locked or passwordless account is not reported as defaulted",
      '"!*"' in block or "'!*'" in block)

# -- the appliance defaults the installer sets --------------------------------------
#
# Raspberry Pi OS ships Node-RED, and it comes up on 0.0.0.0:1880 with
# adminAuth commented out. On the board installed 2026-09-19 an anonymous
# request to /flows returned 95 KB of flows over the LAN, with an MQTT broker
# open on 1883 beside it. That is a bigger hole than any dashboard password:
# the editor runs code.
defaults = INSTALL.split('say "Appliance defaults"', 1)
check("install.sh has an appliance-defaults step", len(defaults) == 2)
defaults = defaults[1].split('say "Hardening check"')[0] if len(defaults) == 2 else ""

check("it switches Node-RED off", "systemctl disable --now nodered" in defaults)
check("...and says where to turn it back on, rather than removing it",
      "Board I/O" in defaults)
check("it switches the front panel ON, so holding USER for five seconds can "
      "show the password-reset code the docs promise",
      "panel.local.toml" in defaults and "enabled = true" in defaults)
check("...as a .local file, which no update overwrites",
      "panel.local.toml" in defaults)
check("...and does not overrule an operator who already chose",
      "-f " in defaults and "left as configured" in defaults)

# THE TRAP. install.sh runs under `set -o pipefail`. `grep -q` exits the
# instant it matches, so the command feeding it dies of SIGPIPE (141) and
# pipefail hands that to the `if` -- the condition reads FALSE because the
# match SUCCEEDED. The Node-RED step silently did nothing on a real board for
# exactly this reason, while the same pipeline run on its own matched fine.
piped = [ln.strip() for ln in INSTALL.splitlines()
         if ln.strip().startswith(("if ", "elif ")) and "| grep -q" in ln]
check("no condition in install.sh pipes into `grep -q`, which pipefail turns "
      "into a false negative precisely when it matches",
      not piped, repr(piped))

# -- the sign-in a board arrives with -----------------------------------------------
#
# A deliberate decision, recorded rather than buried. The product itself never
# invents a master password ("an operator sets it deliberately or nobody logs
# in"), which is right for a developer checkout and leaves a customer
# appliance with no way in. So the APPLIANCE installer ships one, it is the
# same on every board, and the README prints it. It is a starting credential,
# not a secret.
signin = INSTALL.split("7c. The sign-in a board arrives with", 1)
check("install.sh sets a default sign-in", len(signin) == 2)
signin = signin[1].split("# 8.")[0] if len(signin) == 2 else ""

check("the account is iriv", 'DEFAULT_ADMIN_USER="iriv"' in signin)
check("...with the documented password",
      'DEFAULT_ADMIN_PASSWORD="iriv1234"' in signin)
check("it is stored through the product's own hashing, never as a literal "
      "hash or in clear",
      "auth.set_master_password" in signin
      and "password_hash =" not in signin)
check("...and the file is 600, owned by the service account",
      'chmod 600 "$f"' in signin and "chown" in signin)
check("a board whose owner already chose a password keeps theirs",
      "already set -- left alone" in signin)
check("...which is what makes re-running the installer safe",
      "auth.master_password()" in signin)
check("the closing banner says the credential out loud, and says to change it",
      "Sign in as" in INSTALL and "(change it)" in INSTALL)

README = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
check("the README publishes it, because a credential nobody can find is a "
      "support call, not a secret",
      "iriv1234" in README)
check("...and says to change it on the board",
      "iriv1234" in README and "change" in README.lower())

# -- the row store a board arrives with ---------------------------------------------
#
# A table has two halves: its DESIGN lives in config, its ROWS live in
# Postgres. A board installed on 2026-09-19 had no Postgres at all -- not
# stopped, not installed -- so every Data Table wrote nowhere, Analytics had
# nothing to chart and the only sign was a banner most people would not
# connect to "the board records nothing".
#
# It had been left to the operator because the database MAY live on another
# host and several boards may share one. That deployment is real and --db-host
# still serves it. It was the wrong default for an appliance that ships a
# Database page.
store = INSTALL.split("7d. The row store", 1)
check("install.sh sets up a row store", len(store) == 2)
store = store[1].split("# 8. systemd units")[0] if len(store) == 2 else ""

check("it installs postgresql", "apt-get install -y postgresql" in store)
check("...and starts it", "systemctl enable --now postgresql" in store)
check("the role and the database are created only if absent, because this "
      "script is expected to be re-runnable",
      "FROM pg_roles WHERE rolname=" in store
      and "FROM pg_database WHERE datname=" in store)
check("...and an existing role has its password aligned rather than left to "
      "disagree with the config",
      "ALTER ROLE" in store)
check("the password is generated ONCE and read back afterwards",
      "secrets.token_urlsafe" in store
      and 'block.get("password")' in store,
      "rolling it on every run would leave a board unable to reach a database "
      "that is running perfectly well")
check("it is written through the product's own config writer, merged",
      "config.save_local" in store and "config.load_local" in store)
check("...into a file only the service account can read",
      'chmod 600 "${TARGET}/config/storage.local.toml"' in store)
check("the result is PROVED by connecting the way the platform does",
      "from core import db" in store and "db.connect" in store)

# The installer must not define a schema. The master runs the migrations on
# startup, so the schema arrives with the code that expects it; an installer
# that created tables would be a second definition, and the two would drift.
check("it creates no tables -- migrations own the schema",
      "CREATE TABLE" not in store.upper())

check("a deployment sharing one server can say so", "--db-host" in INSTALL)
check("...and one that wants no database at all can too",
      "--no-database" in INSTALL and "INSTALL_DB=0" in INSTALL)
check("a board pointed elsewhere does not get a local postgres",
      'if [[ $INSTALL_DB -eq 1 && $INSTALL_SERVICE -eq 1 && -n "$DB_HOST" ]]'
      in store or '-n "$DB_HOST"' in store)

# -- what a board already knew about itself, across a version change ----------------
#
# A release directory is named after VERSION, so installing a NEW version lands
# in a NEW empty one -- and config/*.local.toml is excluded from the copy,
# correctly, because that is what stops an install trampling a board's own
# settings. Two right decisions making a wrong one: installing 1.0.5 over a
# 1.0.4 board produced a box with no accounts, no database password, no device
# identity and no update token, serving a login page nobody could pass.
#
# The over-the-air updater always carried them (apply_update.py CARRY_CONFIG).
# The installer did not, and the asymmetry was invisible until somebody
# re-installed at a different version.
check("the installer carries local config into a new release directory",
      "PREVIOUS_RELEASE" in INSTALL and "*.local.toml" in INSTALL)
check("...including deploy.toml, the same set the OTA updater carries",
      "config/deploy.toml" in INSTALL)
check("...only where the new release does not already have one, so a second "
      "run of the SAME version cannot copy yesterday's settings over today's",
      '[[ -e "${REAL_TARGET}/config/$(basename "$f")" ]] && continue' in INSTALL)
check("...and it says how many it carried, because a silent migration is one "
      "nobody checks",
      "carried ${carried} local config file" in INSTALL)

# -- the name on the front panel is a name that exists -----------------------------
#
# The panel tells whoever is standing at the box to open iriv.local:5100. The
# installer never set a hostname, so a board published raspberrypi.local and
# the name on the screen resolved to nothing. Checked on a real board: before
# this, raspberrypi.local answered and iriv.local did not exist.
check("the installer names the board iriv-<serial8> (one name per box, not one per fleet)",
      'WANT_HOSTNAME=""' in INSTALL
      and 'WANT_HOSTNAME="iriv${serial:+-${serial: -8}}"' in INSTALL)
check("...and keeps a name its owner chose, unless --hostname is given",
      "HOSTNAME_EXPLICIT=1" in INSTALL and "hostname: keeping ${current_host}" in INSTALL)
# The panel prints THIS box's name (hostname.local_name: the name avahi won,
# else <hostname>.local) and the Software port (hubport) -- not a literal
# "iriv.local", which on a bench of boxes is somebody else's (2026-10-05).
# ...through webaccess.panel_address: name:port, or the name alone when the
# box is reached through its reverse proxy on port 80.
check("...matching what core/services/panel.py advertises",
      "webaccess.panel_address(hostname.local_name(), config_dir)" in io.open(
          os.path.join(ROOT, "core", "services", "panel.py"),
          encoding="utf-8").read()
      and '"%s:%d" % (local_name, hubport.port(config_dir))' in io.open(
          os.path.join(ROOT, "core", "services", "webaccess.py"),
          encoding="utf-8").read())
check("...and installs the mDNS responder rather than hoping the OS image "
      "had one", "avahi-daemon" in INSTALL)

# Renaming the board takes the browser's profile lock with it. Chromium
# locks a profile with a symlink naming the machine that holds it
# (SingletonLock -> raspberrypi-2780) and compares that hostname with the
# current one, so after a rename it decides the profile belongs to another
# computer and will not start:
#
#   The profile appears to be in use by another Chromium process (2780)
#   on another computer (raspberrypi).
#
# Found on a board over VNC on 2026-09-20: the desktop was fine and no
# browser would open, with the reason visible only on a terminal.
check("renaming the board clears the Chromium profile lock the old name left",
      "SingletonLock" in INSTALL and "SingletonCookie" in INSTALL)
check("...only when the lock really does name the OLD host, not any lock it finds",
      '"$target" == "${current_host}-"*' in INSTALL)
check("...and only when no browser is running, since a live lock is doing its job",
      "pgrep -x chromium" in INSTALL)
check("/etc/hosts gets the matching 127.0.1.1 line, or every sudo waits on a "
      "DNS timeout first", "127.0.1.1" in INSTALL)
check("a deployment that wants its own name can say so",
      "--hostname" in INSTALL and "--no-hostname" in INSTALL)

# The appliance defaults are not part of the audit.
check("configuring the appliance is not gated on --skip-hardening",
      INSTALL.index('say "Appliance defaults"')
      < INSTALL.index("if [[ $SKIP_HARDENING -eq 0 ]]"),
      "that flag means 'do not lecture me about passwords', and it used to "
      "also leave Node-RED open and the front panel off")

# -- line endings on the files the kernel reads a shebang from ----------------
#
# A CRLF makes the kernel look for an interpreter literally named python3
# followed by a carriage return, and it blames the interpreter:
#
#   env: 'python3\r': No such file or directory
#
# which reads as a broken Python install on the board. It happened on
# 2026-09-19: flip-link.py arrived with CRLF from a git archive taken on
# Windows, and the next over-the-air update failed at the flip.
#
# .gitattributes fixes git archive. The installer normalising is the belt to
# that brace, because a tree can reach a board by scp from Windows, by
# unzipping a download, or through a share that rewrites endings, and none
# of those consult .gitattributes.
attrs = io.open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8").read()
check("*.py is pinned to LF, not only *.sh",
      "*.py text eol=lf" in attrs,
      "flip_link.py is invoked through its shebang; recover.py and "
      "db-console-role.py escaped it only by being run with an explicit "
      "python3 in front")
check("...and the installer normalises them anyway, whatever the tree "
      "arrived as",
      "normalised line endings in" in INSTALL)

# And the files themselves are clean in the repo, whatever a checkout did.
import subprocess                                             # noqa: E402
for rel in ("scripts/flip_link.py", "scripts/recover.py",
            "scripts/db_console_role.py", "scripts/apply_update.py"):
    blob = subprocess.run(["git", "show", "HEAD:" + rel], cwd=ROOT,
                          capture_output=True)
    first = (blob.stdout or b"").split(bytes([10]), 1)[0]
    check(rel + " is stored with a clean shebang",
          blob.returncode != 0 or not first.endswith(bytes([13])),
          repr(first[:40]))

# -- the step FIXES what cannot lock anybody out (Pipi's CRITICAL chain) -----------
#
# The check used to report and change nothing. On this image that left
# pi/raspberry, `pi ALL=(ALL) NOPASSWD: ALL` (inline in /etc/sudoers, not only
# in 010_pi-nopasswd) and password SSH -- root for anyone who reached the box.
# Each fix is pinned with the guard that keeps it from locking the operator
# out, and the install refuses "Done" while a problem is left.
hard = INSTALL.split('say "Hardening check"', 1)[1].split("# 10. Done", 1)[0]
# pi's OS password is the owner's (Tok Aba, 2026-10-07): the installer used to
# `chage -d 0 pi` on the factory password and refuse "Done" when it could
# not. Now it never sets, expires or probes it, and the summary says so.
hard_code = "\n".join(ln for ln in hard.splitlines() if not ln.strip().startswith("#"))
install_code = "\n".join(ln for ln in INSTALL.splitlines() if not ln.strip().startswith("#"))
check("pi's OS password is never expired or set by the installer",
      "chage" not in install_code and "chpasswd" not in install_code
      and not re.search(r"\bpasswd\s+(-\S+\s+)*pi\b",
                        install_code.replace("(sudo passwd pi)", "")))
check("...and is not probed, so a factory pi password is no hardening problem",
      "for u in root; do" in hard_code and "for u in pi" not in hard_code
      and '"$u" == "pi"' not in hard_code)
check("root on the factory password is still a problem",
      "warn \"user '${u}' still has the default password 'raspberry'.\"" in hard_code)
check("the Done summary says, in one line, that pi's password was not changed",
      "the pi account password was not changed; change it with passwd if you want"
      in INSTALL.split('say "Done"', 1)[1])
check("sudoers is edited on a copy, visudo-checked, renamed into place, else left alone",
      "visudo -c -q -f \"$tmp\"" in hard and 'mv -f "$tmp" "$file"' in hard
      and 'rm -f "$tmp"; return 2' in hard)
check("...both /etc/sudoers and the drop-ins, skipping files sudo itself ignores",
      "echo /etc/sudoers" in hard and "/etc/sudoers.d/*" in hard and "*.*|*~) continue" in hard)
check("...not when pi has no usable password (sudo could not ask; reported instead)",
      '"$pi_hash" == [\\!\\*]*' in hard and "no password sudo could ask for" in hard)
check("...and proved afterwards from sudo's own list, without a piped grep -q",
      'pi_rules="$(sudo -l -U pi 2>/dev/null || true)"' in hard
      and '"$pi_rules" == *"NOPASSWD: ALL"*' in hard)
check("nodered's NOPASSWD: ALL goes only while Node-RED is off",
      "! systemctl is-enabled nodered" in hard and "/^nodered[[:space:]]+ALL" in hard)
ssh = hard.split("DROPIN=/etc/ssh/sshd_config.d/01-irivai-hardening.conf", 1)
check("sshd gets a drop-in that sorts first (sshd keeps the first value it reads)",
      len(ssh) == 2)
if len(ssh) == 2:
    body = ssh[1].split("\n    fi\n", 1)[0]
    check("...PermitRootLogin no, always",
          body.index('echo "PermitRootLogin no"') < body.index('if [[ -n "$key_user" ]]; then'))
    check("...PasswordAuthentication no ONLY when the installing account has keys",
          re.search(r'if \[\[ -n "\$key_user" \]\]; then\s+echo "PasswordAuthentication no"', body)
          is not None)
    check("...and a drop-in sshd -t rejects is removed again",
          'if sshd -t' in body and 'rm -f "$DROPIN"' in body)
check("whose keys: the account that ran sudo, else any login account",
      'has_keys "$SUDO_USER"' in hard and "for home in /home/*" in hard)
check("root login over SSH is a problem when sshd still allows it",
      "/^permitrootlogin /" in hard and "PermitRootLogin ${ssh_root}" in hard)
check("problems > 0 refuses the Done banner (die), unless --skip-hardening skipped the step",
      re.search(r'die "not done: \$\{problems\} hardening problem', hard) is not None
      and "if [[ $SKIP_HARDENING -eq 0 ]]; then" in INSTALL
      and INSTALL.index('die "not done:') < INSTALL.index('say "Done"'))
check("--skip-hardening documents what it now skips",
      "print \"Done\" even with problems" in INSTALL)

# The two sed expressions, RUN: the grant keeps pi's sudo and loses only the
# tag; nodered's line goes; nothing else in the file moves. And the edit
# function itself, with a stand-in visudo: a result visudo refuses leaves the
# original untouched.
import shutil                                                 # noqa: E402
import tempfile                                               # noqa: E402
bash = shutil.which("bash")
if bash:
    fn = hard.split("    sudoers_edit() {", 1)[1].split("\n    }\n", 1)[0]
    pat = re.search(r"PI_NOPASSWD='([^']+)'", hard).group(1)
    tmp = tempfile.mkdtemp()
    stub = os.path.join(tmp, "bin")
    os.makedirs(stub)
    for name, body in (("visudo", '#!/bin/sh\nfor a; do f="$a"; done\n'
                                  'grep -q BROKEN "$f" && exit 1\nexit 0\n'),
                       ("chown", "#!/bin/sh\nexit 0\n")):
        with open(os.path.join(stub, name), "w", newline="\n") as fh:
            fh.write(body)
        os.chmod(os.path.join(stub, name), 0o755)
    sud = os.path.join(tmp, "sudoers")
    original = ("root\tALL=(ALL:ALL) ALL\npi ALL=(ALL) NOPASSWD: ALL\n"
                "pi ALL=(ALL:ALL) NOPASSWD:ALL\npi ALL=(ALL) NOPASSWD: /usr/bin/x\n"
                "nodered ALL=(ALL) NOPASSWD: ALL\n%sudo ALL=(ALL:ALL) ALL\n")
    with open(sud, "w", newline="\n") as fh:
        fh.write(original)
    script = ("set -euo pipefail\nsudoers_edit() {\n" + fn + "\n}\n"
              "PI_NOPASSWD='" + pat + "'\n"
              'rc=0; sudoers_edit "$1" "s/${PI_NOPASSWD}/\\\\1\\\\3/" || rc=$?; echo "pi:$rc"\n'
              "rc=0; sudoers_edit \"$1\" '/^nodered[[:space:]]+ALL[[:space:]]*=.*NOPASSWD:[[:space:]]*ALL[[:space:]]*$/d' || rc=$?; echo \"nr:$rc\"\n"
              'rc=0; sudoers_edit "$1" "s/^root/BROKEN/" || rc=$?; echo "bad:$rc"\n'
              'rc=0; sudoers_edit "$1" "s/^zzz/yyy/" || rc=$?; echo "none:$rc"\n')
    env = dict(os.environ, PATH=stub.replace("\\", "/") + os.pathsep + os.environ.get("PATH", ""))
    run = subprocess.run([bash, "-c", script, "x", sud.replace("\\", "/")],
                         capture_output=True, text=True, env=env)
    after = open(sud, encoding="utf-8").read()
    check("sudoers_edit reports changed / changed / refused / nothing (0 0 2 1)",
          run.stdout.split() == ["pi:0", "nr:0", "bad:2", "none:1"],
          repr((run.stdout, run.stderr[-300:])))
    check("pi's NOPASSWD: ALL grants become password grants, specific ones are left",
          "pi ALL=(ALL) ALL\n" in after and "pi ALL=(ALL:ALL) ALL\n" in after
          and "pi ALL=(ALL) NOPASSWD: /usr/bin/x\n" in after, after)
    check("nodered's grant is gone, every other line is where it was",
          "nodered" not in after and after.startswith("root\tALL=(ALL:ALL) ALL\n")
          and after.endswith("%sudo ALL=(ALL:ALL) ALL\n"), after)
    check("a result visudo refuses never replaces the file", "BROKEN" not in after, after)
    check("...and leaves no temp copy behind",
          sorted(os.listdir(tmp)) == ["bin", "sudoers"], repr(os.listdir(tmp)))
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print("skip the sed run (no bash on PATH)")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
