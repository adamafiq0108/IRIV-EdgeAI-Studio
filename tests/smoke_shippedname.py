# The name the box ships as counts as the shipped name.
#
# UAT-106 (Ochobot, first run on board "iriv", 2026-10-04): GET
# /api/auth/account-setup answered is_default_username: false on a fresh box,
# because the check compared against ADMIN_DEFAULT ("admin") while
# scripts/install.sh names the account "iriv". The setup page's nudge to pick
# a name of your own never showed on a real box.
#
#     py -3 tests/smoke_shippedname.py
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import auth as auth_service               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


install = read("scripts", "install.sh")
m = re.search(r'^DEFAULT_ADMIN_USER="([^"]+)"', install, re.M)
check("install.sh names the account", m is not None)
shipped = m.group(1) if m else "iriv"
check("the installer's name is a shipped name",
      shipped in auth_service.SHIPPED_USERNAMES, (shipped, auth_service.SHIPPED_USERNAMES))

CFG = tempfile.mkdtemp()
check("an unnamed box (admin) is on a shipped name",
      auth_service.is_default_username(CFG) is True)
auth_service.set_admin_username(shipped, CFG)
check(f"a box named {shipped!r} by the installer is on a shipped name",
      auth_service.is_default_username(CFG) is True)
auth_service.set_admin_username("siteowner", CFG)
check("an owner's own name is not", auth_service.is_default_username(CFG) is False)

routes = read("core", "routes", "auth.py")
check("the setup context answers from the predicate",
      '"is_default_username": auth_service.is_default_username(cfg)' in routes)
page = read("frontend", "src", "pages", "AccountSetup.jsx")
check("the page quotes the shipped name from the server, not a literal",
      "still called “${ctx.username}”" in page and "still called “admin”" not in page)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
