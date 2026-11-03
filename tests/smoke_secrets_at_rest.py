# The master password is never kept in clear, and the files that hold secrets
# are owner-only. A box upgraded from before the hash has the cleartext in
# auth.local.toml until somebody signs in; secrets_at_rest.run() removes it at
# every start, so a box nobody signs in to does not keep it for ever.
import os
import re
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                        # noqa: E402
from core.services import auth                                  # noqa: E402
from core.services import secrets_at_rest as sar                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def raw(cfg):
    with open(os.path.join(cfg, "auth.local.toml"), encoding="utf-8") as f:
        return f.read()


# -- an old box: cleartext, nothing else ----------------------------------------
CFG = tempfile.mkdtemp()
config.save_local("auth", {"master": {"username": "boss", "password": "oldpass-1"},
                           "session": {"secret": "x" * 20}}, CFG)
check("the old cleartext is on disk before", "oldpass-1" in raw(CFG))
check("it is migrated at start", sar.migrate_master_password(CFG) is True)
text = raw(CFG)
check("...the cleartext is gone from the file and a hash is in its place",
      "oldpass-1" not in text and "password_hash" in text and "salt" in text)
check("...and the same password still signs in, a wrong one does not",
      auth.verify_login("boss", "oldpass-1", CFG) is True
      and auth.verify_login("boss", "oldpass-2", CFG) is False)
check("running it again changes nothing", sar.migrate_master_password(CFG) is False)

# A short legacy password (under the rule for NEW ones) is hashed as it is.
CFG2 = tempfile.mkdtemp()
config.save_local("auth", {"master": {"username": "boss", "password": "short"}}, CFG2)
sar.migrate_master_password(CFG2)
check("a short password already in use is hashed, not refused or lost",
      "short" not in raw(CFG2) and auth.verify_login("boss", "short", CFG2) is True)

# -- a hash is left alone -----------------------------------------------------------
CFG3 = tempfile.mkdtemp()
auth.set_master_password("brand-new-pass", CFG3)
before = raw(CFG3)
check("a box that already has a hash is untouched", sar.migrate_master_password(CFG3) is False
      and raw(CFG3) == before)

# -- file modes ------------------------------------------------------------------------
if os.name == "posix":
    p = os.path.join(CFG, "auth.local.toml")
    os.chmod(p, 0o644)
    other = os.path.join(CFG, "mail.local.toml")
    open(other, "w").write("x = 1\n")
    os.chmod(other, 0o664)
    shipped = os.path.join(CFG, "auth.toml")
    open(shipped, "w").write("x = 1\n")
    os.chmod(shipped, 0o644)
    changed = sar.tighten_modes(CFG)
    check("a secret file readable by others is put back to owner-only",
          set(changed) == {"auth.local.toml", "mail.local.toml"}
          and stat.S_IMODE(os.stat(p).st_mode) == 0o600
          and stat.S_IMODE(os.stat(other).st_mode) == 0o600)
    check("...and a committed config file is left alone",
          stat.S_IMODE(os.stat(shipped).st_mode) == 0o644)
else:
    print("skip file-mode checks (not posix)")

# -- wired in, and nothing reads the secrets back ------------------------------------
def src(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


check("the hub and every master run it at start",
      "secrets_at_rest.run(config_dir)" in src("hub_server.py")
      and "secrets_at_rest.run(config_dir)" in src("master_server.py"))
check("the legacy reader is the only code that reads a cleartext master password",
      not re.search(r"(?<![A-Za-z_])master_password\(", "".join(
          src("core", "routes", n) for n in os.listdir(os.path.join(ROOT, "core", "routes"))
          if n.endswith(".py"))))
check("the installer writes the shipped password through the hashing setter only",
      "auth.set_master_password(want_password)" in src("scripts", "install.sh"))

print()
if bad:
    print(f"{bad} FAILED")
    sys.exit(1)
print("all good")
