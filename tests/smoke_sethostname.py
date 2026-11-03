# The box-name helper and what install.sh does for it (Kokoci's hostname
# plan, docs/team/kokoci/2026-10-05-hostname.md, sections 1 and 2).
#
# Pinned: scripts/set_hostname.py takes the name on stdin only and
# re-validates it as an RFC 1123 label (no digits-only, no localhost) before
# touching anything; install.sh installs it root-owned outside the release
# tree with its own no-argument sudoers rule (visudo-checked), normalises its
# line endings, names a NEW box iriv-<serial8>, keeps a name an owner chose,
# and installs avahi-utils for the mDNS advertisement.
#
#     python tests/smoke_sethostname.py
import importlib.util
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


path = os.path.join(ROOT, "scripts", "set_hostname.py")
spec = importlib.util.spec_from_file_location("set_hostname", path)
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)

for good in ("iriv-2b35eb4f", "line-a", "a", "box1"):
    check(f"accepts {good!r}", bool(sh.LABEL.match(good)))
for badname in ("-x", "x-", "UPPER", "a.b", "a b", "x" * 64, "a;rm", "", "a\nb"):
    check(f"refuses {badname!r}", not sh.LABEL.match(badname))
src = open(path, encoding="utf-8").read()
check("digits-only and localhost are refused too",
      "name.isdigit()" in src and 'name == "localhost"' in src)
check("the name comes from stdin, never argv", "sys.stdin.readline(80)" in src
      and "sys.argv" not in src)
check("only the 127.0.1.1 line of /etc/hosts is rewritten, by rename",
      'r"127\\.0\\.1\\.1\\s"' in src and 'os.replace(tmp, "/etc/hosts")' in src)
if hasattr(os, "geteuid") and os.geteuid() != 0:
    r = subprocess.run([sys.executable, path], input="iriv-test\n", capture_output=True, text=True)
    check("refuses to run as anyone but root", r.returncode == 2 and "root" in r.stderr)

inst = open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8").read()
check("installed root-owned outside the release tree",
      'install -m 755 "${SRC_DIR}/scripts/set_hostname.py" /usr/local/lib/irivai/set-hostname.py'
      in inst and "chown root:root /usr/local/lib/irivai/set-hostname.py" in inst)
check("...line endings normalised with the other helpers",
      "/usr/local/lib/irivai/set-hostname.py \\\n" in inst)
rule = re.search(r"cat > /etc/sudoers.d/irivai-hostname.tmp <<SUDOERS\n(.*?)\nSUDOERS", inst, re.S)
check("its own sudoers rule names exactly the helper, no arguments",
      rule is not None and rule.group(1).strip()
      == "${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/set-hostname.py")
check("...visudo-checked before it goes live",
      "visudo -cf /etc/sudoers.d/irivai-hostname.tmp" in inst)
check("avahi-utils is in the package list",
      re.search(r"APT_PACKAGES=\([^)]*\bavahi-utils\b", inst, re.S) is not None)
hostname_svc = open(os.path.join(ROOT, "core", "services", "hostname.py"), encoding="utf-8").read()
check("the hub calls exactly what the sudoers rule names",
      'HELPER = "/usr/local/lib/irivai/set-hostname.py"' in hostname_svc
      and 'HELPER_ARGV = ("sudo", "-n", "/usr/bin/python3", "-I", HELPER)' in hostname_svc)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
