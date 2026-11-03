# Opening "New system" brings the templates up to date first -- quietly.
#
# Tok Aba, 2026-10-14: when somebody clicks New system, the box fetches the
# latest published templates so the new system starts from the newest one.
# No internet, no token, GitHub refusing, a download that does not verify:
# all fine, all silent, the wizard carries on with what the device holds.
# Pinned here against the real fetch, signature check and merge
# (core/services/template_updates.py auto_refresh), GitHub stubbed:
#
#   1. no token -> "skipped", nothing sent; the source hub -> "skipped"
#   2. no default route -> "offline", nothing sent, and not counted as an
#      attempt (the next open checks)
#   3. a newer published template -> "updated", installed, count and key
#   4. a second open inside the window -> the cached answer, no network
#   5. the window over and nothing new -> "current"
#   6. a bad signature -> "skipped", nothing installed, one warning, no token
#   7. GitHub unreachable -> "offline"
#   8. concurrent opens share ONE fetch (single flight)
#   9. a hung fetch costs the caller the budget, not more
#  10. it never raises, whatever breaks underneath
#  11. the token appears in no answer and no log line
#  12. the button and the refresh share one lock; the button counts as the
#      attempt; create waits for a refresh in flight; the route is admin
#
#   py -3 tests/smoke_templateautorefresh.py
import base64
import io
import logging
import os
import sys
import tempfile
import threading
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                          # noqa: E402
from core.services import ghnet                                  # noqa: E402
from core.services import provision                              # noqa: E402
from core.services import template_updates as tu                 # noqa: E402
from core.services import update_auto                            # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey)
from cryptography.hazmat.primitives import serialization          # noqa: E402

bad = 0
TOKEN = "ghp_SECRET_auto_refresh_test"


def check(msg, passed, detail=""):
    global bad
    if passed:
        print("ok   " + msg)
    else:
        print("FAIL " + msg + ("\n      " + str(detail) if detail else ""))
        bad += 1


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# Every log line the module writes, to look for the token in.
logged = []


class Grab(logging.Handler):
    def emit(self, record):
        logged.append(record.getMessage())


logging.getLogger(tu.__name__).addHandler(Grab())
logging.getLogger(tu.__name__).setLevel(logging.DEBUG)

_priv = Ed25519PrivateKey.generate()
PUB = base64.b64encode(_priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

CFG = tempfile.mkdtemp()
SYSTEMS = tempfile.mkdtemp()
io.open(os.path.join(CFG, "store.toml"), "w", encoding="utf-8").write(
    '[signing]\npubkey = "%s"\n' % PUB)
config.load.cache_clear()

provision.save_format("alpha", {"label": "Alpha", "widgets": [],
                                "draft": False}, CFG)


def published(label):
    entry = provision.format_entry("alpha", {"label": label, "widgets": []})[1]
    return (config._toml_dump({"formats": {"alpha": entry}}) + "\n").encode()


calls = []


def serving(body, sig=None, delay=0.0):
    """open_url answering the formats file and its signature."""
    good = base64.b64encode(_priv.sign(body))

    def _open(url, token=None, **kw):
        calls.append((url, token, kw.get("timeout")))
        if delay:
            time.sleep(delay)
        path = url.split("/contents/", 1)[1].split("?", 1)[0]
        if path.endswith(".sig"):
            return io.BytesIO(sig if sig is not None else good)
        return io.BytesIO(body)
    return _open


def unreachable(url, token=None, **kw):
    calls.append((url, token, kw.get("timeout")))
    raise urllib.error.URLError("no route to host")


def fresh():
    """Forget the last attempt: as if the window had run out."""
    with tu._auto_lock:
        tu._auto["at"] = None
        tu._auto["result"] = None
    calls.clear()


def label():
    return provision.builtin_formats(CFG)["alpha"]["label"]


real_route = update_auto.has_route
update_auto.has_route = lambda: True
answers = []

# ---- 1. no token ----------------------------------------------------------
ghnet.open_url = serving(published("Alpha v2"))
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("no update token -> skipped, nothing sent",
      r["state"] == "skipped" and r["count_changed"] == 0 and not calls, (r, calls))
config.save_local("update", {"token": TOKEN}, CFG)

# ---- 2. no default route ----------------------------------------------------
update_auto.has_route = lambda: False
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("no default route -> offline, nothing sent", r["state"] == "offline" and not calls,
      (r, calls))
check("...and not counted as an attempt", tu._auto["at"] is None, tu._auto)
update_auto.has_route = lambda: True

# ---- 3. updated -------------------------------------------------------------
fresh()
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("a newer published template -> updated, one change, its key",
      r["state"] == "updated" and r["count_changed"] == 1 and r["changed"] == ["alpha"],
      r)
check("...installed on the box", label() == "Alpha v2", label())
# Four reads since 2026-10-22: the templates and their signature, then the
# preview media list and its signature (template_media_sync.py).
check("...the box's token rode on every read, under the budget",
      len(calls) == 4 and "formats.toml?" in calls[0][0] and "formats.toml.sig" in calls[1][0]
      and "media.json?" in calls[2][0] and "media.json.sig" in calls[3][0]
      and all(c[1] == TOKEN for c in calls)
      and all(c[2] is not None and c[2] <= tu.AUTO_BUDGET_S for c in calls), calls)

# ---- 4. rate limit ----------------------------------------------------------
calls.clear()
ghnet.open_url = serving(published("Alpha v3"))
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("a second open inside the window: the cached answer, no network",
      not calls and r["cached"] is True and r["state"] == "updated", (r, calls))
check("...and nothing installed meanwhile", label() == "Alpha v2", label())

# ---- 5. current -------------------------------------------------------------
fresh()
ghnet.open_url = serving(published("Alpha v2"))
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("window over, nothing new -> current", r["state"] == "current"
      and r["count_changed"] == 0 and len(calls) == 4, (r, calls))

# ---- 6. bad signature -------------------------------------------------------
fresh()
logged.clear()
ghnet.open_url = serving(published("Alpha EVIL"), sig=base64.b64encode(b"x" * 64))
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("a bad signature -> skipped, kept current", r["state"] == "skipped"
      and label() == "Alpha v2", (r, label()))
fresh()
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("...logged once at warning, not on every open",
      len([m for m in logged if "did not verify" in m]) == 1, logged)
tampered = published("Alpha v2") + b"# x\n"
fresh()
good_sig = base64.b64encode(_priv.sign(published("Alpha v2")))
ghnet.open_url = serving(tampered, sig=good_sig)
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("...a file changed after signing is kept out too",
      r["state"] == "skipped" and label() == "Alpha v2", r)

# ---- 7. unreachable ---------------------------------------------------------
fresh()
ghnet.open_url = unreachable
r = tu.auto_refresh(CFG, SYSTEMS)
answers.append(r)
check("GitHub unreachable -> offline", r["state"] == "offline", r)
check("...and the failure counts: the next open is cached, no retry storm",
      tu.auto_refresh(CFG, SYSTEMS)["cached"] is True and len(calls) == 1, calls)

# ---- 8. single flight -------------------------------------------------------
fresh()
ghnet.open_url = serving(published("Alpha v4"), delay=0.3)
got = []
threads = [threading.Thread(target=lambda: got.append(tu.auto_refresh(CFG, SYSTEMS)))
           for _ in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
answers.extend(got)
check("six concurrent opens: ONE fetch (file + signature, media list + signature)",
      len(calls) == 4, calls)
check("...all six told the same: updated",
      len(got) == 6 and all(g["state"] == "updated" for g in got), got)
check("...installed once", label() == "Alpha v4", label())

# ---- 9. a hung fetch ----------------------------------------------------------
fresh()
ghnet.open_url = serving(published("Alpha v5"), delay=1.5)
t0 = time.monotonic()
r = tu.auto_refresh(CFG, SYSTEMS, budget=0.3)
spent = time.monotonic() - t0
answers.append(r)
check("a hung fetch costs the caller the budget, then offline",
      r["state"] == "offline" and spent < 1.0, (r, spent))
t0 = time.monotonic()
tu.wait_auto(5)
check("...create's wait lets the late worker land", time.monotonic() - t0 < 4.0
      and tu._auto["done"] is None)
check("...and the next open in the window gets what it found",
      tu.auto_refresh(CFG, SYSTEMS)["cached"] is True)
tu.wait_auto(0.1)
check("wait_auto with nothing in flight returns at once", True)

# ---- 10. never raises ---------------------------------------------------------
real_check = tu._check
tu._check = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom " + TOKEN))
fresh()
try:
    r = tu.auto_refresh(CFG, SYSTEMS)
    answers.append(r)
    check("an unexpected error underneath -> skipped, no exception", r["state"] == "skipped", r)
except Exception as exc:                                         # noqa: BLE001
    check("an unexpected error underneath -> skipped, no exception", False, exc)
tu._check = real_check
real_token = tu.update_service.token
tu.update_service.token = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
fresh()
try:
    r = tu.auto_refresh(CFG, SYSTEMS)
    check("...even the token read failing", r["state"] == "skipped", r)
except Exception as exc:                                         # noqa: BLE001
    check("...even the token read failing", False, exc)
tu.update_service.token = real_token

# ---- 11. the token --------------------------------------------------------------
check("the token is in no answer", all(TOKEN not in repr(a) for a in answers))
check("...and in no log line", all(TOKEN not in m for m in logged), logged)

# ---- 12. the button, the lock, the source hub, the route --------------------------
fresh()
ghnet.open_url = serving(published("Alpha v6"))
tu._check_lock.acquire()
try:
    r = tu.auto_refresh(CFG, SYSTEMS, budget=0.3)
    tu.wait_auto(2)                    # the worker gives up while still held
    after = tu._auto["result"]
finally:
    tu._check_lock.release()
check("a button check holding the lock: the refresh steps aside, installs nothing",
      r["state"] in ("skipped", "offline") and after["state"] == "skipped"
      and label() != "Alpha v6", (r, after, label()))
fresh()
res = tu.check(CFG, SYSTEMS)
tu.note_checked(res)
calls.clear()
r = tu.auto_refresh(CFG, SYSTEMS)
check("the button's check counts as the attempt: the next open is cached",
      r["cached"] is True and r["state"] == "updated" and not calls, (r, calls))

fresh()
io.open(os.path.join(CFG, "deploy.toml"), "w", encoding="utf-8").write(
    'mode = "dev"\n')
config.load.cache_clear()
r = tu.auto_refresh(CFG, SYSTEMS)
check("the source hub installs nothing: skipped, nothing sent",
      tu.authoring(CFG) and r["state"] == "skipped" and not calls, (r, calls))
os.remove(os.path.join(CFG, "deploy.toml"))
config.load.cache_clear()

hub = read("core", "routes", "hub.py")
route = hub[hub.index('name="hub.refresh_templates"'):]
route = route[:route.index("def _template_listing")]
check("the refresh route is gated like create (admin) and calls auto_refresh",
      "_require_admin(" in route and "template_updates.auto_refresh(" in route)
create = hub[hub.index('name="hub.create_system"'):]
create = create[:create.index("create_vision_system(")]
check("create waits for a refresh in flight, off the event loop",
      "asyncio.to_thread(template_updates.wait_auto)" in create)
button = hub[hub.index('name="hub.update_templates"'):]
button = button[:button.index("def refresh_templates")]
check("the button records its check for the refresh's window",
      "template_updates.note_checked(result)" in button)

update_auto.has_route = real_route
print()
if bad:
    print("%d check(s) failed" % bad)
    raise SystemExit(1)
print("ALL OK")
