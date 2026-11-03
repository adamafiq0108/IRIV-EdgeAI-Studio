# Changing a password says, before it happens, that other sign-ins end.
#
# UAT-108 (Ochobot, first run on board "iriv", 2026-10-04): finishing account
# setup took the box from six sessions to two -- correct (a new password is
# how a stolen one is shut out) but silent, so a colleague commissioning from
# another laptop was bounced to the login with no word as to why. The server
# behaviour stays; both forms now say it.
#
#     py -3 tests/smoke_signoutnotice.py
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return " ".join(f.read().split())


routes = read("core", "routes", "auth.py")
check("the server still ends the other sessions on a password change",
      routes.count("revoke_sessions_of(") >= 3)
setup = read("frontend", "src", "pages", "AccountSetup.jsx")
check("Account setup says so under New password",
      "Saving a new password signs out every other browser that is signed in "
      "as this account." in setup)
panel = read("frontend", "src", "components", "AccountsPanel.jsx")
check("the Change password dialog says so before the change",
      "every other browser signed in as you is " in panel)
# ...and an admin reset says the user is out everywhere, never the "sessions
# may remain signed in" a policy-agnostic copy would have promised: both
# routes revoke, whatever the session policy.
check("...and a reset of someone else says they are signed out everywhere",
      "signed out everywhere and must sign in again" in panel
      and "may remain signed in" not in panel)
check("...and the success toast says it happened",
      "Other browsers signed in as you were signed out." in panel)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
