# The login card says "Wrong username or password." only when it was.
#
# UAT-118 (Ochobot, first run on board "iriv", 2026-10-04): Login.jsx's catch
# printed that fixed string for ANY failure -- the box not answering, a 429
# rate limit, a 500 -- so the only advice for all of them was to retype.
#
#     py -3 tests/smoke_loginerror.py
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


with open(os.path.join(ROOT, "frontend", "src", "pages", "Login.jsx"),
          encoding="utf-8") as f:
    page = f.read()
catch = page[page.index("} catch (err) {"):]
catch = catch[:catch.index("setBusy(false);")]
check("401 keeps the fixed sentence",
      'err?.status === 401 ? "Wrong username or password."' in catch, catch)
check("429 keeps the server's own wait time",
      "err?.status === 429 && err.message ? err.message" in catch, catch)
check("everything else is worded by friendlyError", "friendlyError(err)" in catch
      and "import { api, friendlyError }" in page, catch)

with open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8") as f:
    routes = f.read()
login = routes[routes.index('@router.post("/api/auth/login"'):]
login = login[:login.index("@router.post", 10)]
check("the server's wrong-credentials answer is a 401",
      'status_code=401, detail="Wrong username or password."' in login)
check("...and its throttle a 429 that says how long",
      "status_code=429" in login and "try again in" in login)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
