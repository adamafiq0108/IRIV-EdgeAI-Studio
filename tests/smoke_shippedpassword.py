# The board may not finish first-run setup on the password it shipped with.
#
# Setup was gated on the RECOVERY EMAIL alone, so the owner who typed an
# address in was never shown this screen again -- with iriv1234 still live.
# That string is printed in README.md and is the Wi-Fi access point key too,
# so the board was open to anyone within radio range of it, no network access
# needed first.
#
# What this pins:
#
#   * the shipped password is recognised through the normal verify path, for a
#     stored hash and for the cleartext an upgraded box still carries;
#   * an owner who chose their own password answers False -- including after an
#     install.sh re-run, which never replaces a password that already exists,
#     so they are not marched through the setup screen again;
#   * a recovery email on its own no longer satisfies the gate;
#   * the gate demands a CHANGE and is not a lock-out: the route refuses the
#     shipped string as the NEW password, or the same screen would be waiting
#     at the next sign-in.
#
#     python tests/smoke_shippedpassword.py
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                      # noqa: E402
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


SHIPPED = auth_service.SHIPPED_PASSWORD

# -- it is the same string the installer writes --------------------------------
# Two files, one password. If the installer's default ever moves, this check is
# what says the predicate went blind rather than the board going secure.
install = read("scripts", "install.sh")
check("the predicate knows the password install.sh actually writes",
      f'DEFAULT_ADMIN_PASSWORD="{SHIPPED}"' in install, SHIPPED)

# -- a box as it leaves the factory --------------------------------------------
CFG = tempfile.mkdtemp()
auth_service.set_master_password(SHIPPED, CFG)
check("a board on the shipped password says so",
      auth_service.master_password_is_default(CFG) is True)

# ...and a recovery address does NOT buy it off. This is the exact bug: the
# owner filled the email in, setup was declared finished, iriv1234 stayed live.
auth_service.set_admin_email("owner@example.com", CFG)
check("a recovery email alone does not satisfy the gate",
      auth_service.admin_email(CFG) == "owner@example.com"
      and auth_service.master_password_is_default(CFG) is True)

# -- the owner chooses their own -------------------------------------------------
auth_service.set_master_password("a-real-password", CFG)
check("a chosen password is not the shipped one",
      auth_service.master_password_is_default(CFG) is False)
check("...and it still signs in",
      auth_service.verify_login(auth_service.admin_username(CFG),
                                "a-real-password", CFG) is True)

# -- the re-install case ---------------------------------------------------------
# install.sh writes the default only when no master password exists, so a
# re-run leaves a chosen password alone. Re-running it must not put an owner
# who finished setup months ago back on the first-run screen.
check("install.sh only writes a password when there is none",
      re.search(r"if auth\.master_password\(\).*password_hash", install)
      is not None,
      "install.sh's guard moved -- a re-run may now overwrite a chosen password")
check("a re-install does not re-open the gate",
      auth_service.master_password_is_default(CFG) is False)

# -- the upgraded box ------------------------------------------------------------
# A deployment that predates hashing still has the password in clear next to
# the session secret. It is the same default, and the gate has to see it:
# reading it through the normal verify path is what makes that free.
LEGACY = tempfile.mkdtemp()
local = config.load_local("auth", LEGACY)
local.setdefault("master", {})["password"] = SHIPPED
auth_service._save_local("auth", local, LEGACY)
check("a legacy cleartext default is seen too",
      auth_service.master_password_is_default(LEGACY) is True)

# -- the login gate --------------------------------------------------------------
routes = read("core", "routes", "auth.py")
gate = routes.split("needs_setup = ")[1].split("token = ")[0]
check("login demands setup while the board is on the shipped password",
      "setup_pending(username, cfg)" in gate, gate.strip()[:200])

# -- the form ---------------------------------------------------------------------
check("the setup context tells the form which of the two screens it is",
      '"password_is_default"' in routes)

# ...and the server refuses to let the screen be finished on the default, or
# the browser would be the only thing holding the gate.
setup = routes.split("def setup_json")[1].split("# -- session management")[0]
check("the route refuses a setup that leaves the shipped password in place",
      "first_run_pending" in setup and "if not password" in setup,
      setup[:400])
check("...and refuses the shipped password as the NEW one",
      "auth_service.SHIPPED_PASSWORD" in setup,
      "otherwise the same screen is waiting at the next sign-in")

page = read("frontend", "src", "pages", "AccountSetup.jsx")
check("the password field is required exactly when the board is on the default",
      "required={!!ctx?.password_is_default}" in page)
check("an owner who already chose a password still sees 'blank to keep'",
      "- blank to keep" in page)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
