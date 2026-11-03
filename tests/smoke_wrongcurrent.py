# Mistyping your OWN current password is not "ask an administrator".
#
# UAT-119 (Ochobot, first run on board "iriv", 2026-10-04): account setup with
# a wrong current password answered 403 AUTH-9020 (AUTH_FORBIDDEN, mapped from
# the bare status) whose action -- shown on the Logs and Events pages -- read
# "Ask an administrator to carry out the action, or to change the account's
# role." to the administrator who had typed their own password wrong.
#
#     py -3 tests/smoke_wrongcurrent.py
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import log_catalog as catalog             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


e = catalog.get("AUTH_WRONG_CURRENT")
check("the catalog has its own entry", e is not None)
check("...with an action the account holder can take",
      e and e.action == "Type the password you signed in with.", e)
check("...and no 'ask an administrator'",
      e and "administrator" not in e.action.lower(), e)
check("...answered as a 403", catalog.status_for("AUTH_WRONG_CURRENT") == 403)

with open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8") as f:
    routes = f.read()
check("no route raises the bare 'current password is wrong' 403 any more",
      "current password is wrong" not in routes)
check("password change, rename and account setup all use it",
      routes.count('catalog.http_error("AUTH_WRONG_CURRENT")') == 3)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
