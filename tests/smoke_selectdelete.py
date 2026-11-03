# Data Retention: two buttons per system, and the Trash tells the truth.
#
# The row used to carry four buttons (hold to delete events / footage / reset
# database, Delete All Data). It is now Delete All Data plus ONE "Selected
# deletion" menu with five picks -- the three above and the two Diagnostics
# pages every system carries, Events Logs and System Logs -- each still behind
# a three-second hold. So two new purge scopes exist server side:
#
#   eventlog  what the Events Logs page shows: the system's event-store rows
#             and the event log they are projected from. Footage, tallies and
#             the operator's own tables stay; nothing restarts.
#   logs      what the System Logs page shows: app.log and rotations, the
#             fault spool, HailoRT's log beside the package, the master's log
#             file (truncated -- it is held open). No recorded data.
#
# And the Trash bug that came with the report: Delete on an orphan answered
# ok while the folder stayed on the disk, because rmtree's OSError was
# swallowed. On the box the cause was a .thumbs folder owned by root (a master
# once run as root); pi cannot empty it. remove_orphan now reports what would
# not go and says what to do, and the panel shows that instead of "removed".
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import retention as rt                   # noqa: E402
from core.services import provision as provision_service    # noqa: E402
from core.repos import events as events_repo                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- a system with everything it accumulates ---------------------------------
SAND = tempfile.mkdtemp()
SYSTEMS = os.path.join(SAND, "systems")
SLUG = "select_demo"
PKG = os.path.join(SYSTEMS, SLUG)
DATA = os.path.join(PKG, "data")
LOGS = os.path.join(SAND, "logs")
for d in (os.path.join(DATA, "snapshots", ".thumbs"),
          os.path.join(DATA, "clips", ".thumbs"), LOGS):
    os.makedirs(d, exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\ndata_dir = "{DATA.replace(os.sep, "/")}"\n')

FILES = {
    "tallies": os.path.join(DATA, "fn_daily.json"),
    "events": os.path.join(DATA, "events.jsonl"),
    "snapshot": os.path.join(DATA, "snapshots", "a.jpg"),
    "thumb": os.path.join(DATA, "snapshots", ".thumbs", "a.jpg"),
    "clip": os.path.join(DATA, "clips", "a.mp4"),
    "app_log": os.path.join(DATA, "app.log"),
    "app_log_1": os.path.join(DATA, "app.log.1"),
    "spool": os.path.join(DATA, "faults.jsonl"),
    "hailort": os.path.join(PKG, "hailort.log"),
    "hailort_1": os.path.join(PKG, "hailort.1.log"),
    "master_log": os.path.join(LOGS, f"master-{SLUG}.log"),
}


def seed():
    for path in FILES.values():
        with open(path, "wb") as f:
            f.write(b"x" * 8)


def present():
    return {k: os.path.exists(p) for k, p in FILES.items()}


# The event store is a database this test does not have; count the calls.
deleted_rows = []
real_delete = events_repo.delete_for_system
events_repo.delete_for_system = lambda slug, config_dir=None: (deleted_rows.append(slug), 7)[1]

# -- 1. the scopes exist ------------------------------------------------------
check("eventlog and logs are purge scopes",
      "eventlog" in rt.PURGE_SCOPES and "logs" in rt.PURGE_SCOPES, rt.PURGE_SCOPES)
check("...and the old ones are still there, in their old order",
      rt.PURGE_SCOPES[:4] == ("all", "events", "footage", "counters"))

# -- 2. logs: the System Logs page's files, and nothing recorded --------------
seed()
removed = rt.purge_system(SYSTEMS, SLUG, "logs")
st = present()
check("logs removes app.log's rotation, the spool and HailoRT's logs",
      not st["app_log_1"] and not st["spool"]
      and not st["hailort"] and not st["hailort_1"], st)
check("...and empties app.log in place: a running app holds it open as stdout",
      st["app_log"] and os.path.getsize(FILES["app_log"]) == 0, st)
check("...truncates the master's log rather than unlinking a file it holds open",
      st["master_log"] and os.path.getsize(FILES["master_log"]) == 0)
check("...counts them", removed["log_files"] == 6, removed)
check("...and touches no recorded data",
      st["tallies"] and st["events"] and st["snapshot"] and st["clip"], st)
check("...nor the event store", deleted_rows == [], deleted_rows)

# -- 3. eventlog: the Events Logs page's rows AND the log behind them ---------
seed()
deleted_rows.clear()
removed = rt.purge_system(SYSTEMS, SLUG, "eventlog")
st = present()
check("eventlog deletes this system's rows in the event store",
      deleted_rows == [SLUG] and removed["db_rows"] == 7, (deleted_rows, removed))
check("...and the event log they are projected from, so they do not come back",
      not st["events"] and removed["events"] == 1, st)
check("...but keeps footage, the tallies and the logs",
      st["snapshot"] and st["clip"] and st["tallies"] and st["app_log"], st)

# -- 4. footage now takes the thumbnails with it ------------------------------
seed()
rt.purge_system(SYSTEMS, SLUG, "footage")
st = present()
check("footage removes the .thumbs poster beside the snapshot",
      not st["snapshot"] and not st["thumb"], st)
check("...and leaves events and logs alone", st["events"] and st["app_log"], st)

# -- 5. the route knows the scopes it forwards --------------------------------
route = read("core", "routes", "hub.py")
check("the delete route clears the master's log ring for scope logs, as the operator",
      '"/api/logs/clear"' in route and "masters_service.post_json" in route
      and 'request.headers.get("cookie")' in route.split('"/api/logs/clear"')[1][:200])
masters = read("core", "services", "masters.py")
check("post_json exists and never raises",
      "def post_json(" in masters and "return None" in masters.split("def post_json(")[1][:900])

# -- 6. the panel: two buttons, five picks, a hold in a dialog ----------------
page = read("frontend", "src", "components", "RetentionPanel.jsx")
check("the row has Choose Data to Delete and Delete All System Data, and no hold "
      "buttons on it (customer copy 2026-10-11)",
      "/> Choose Data to Delete\n" in page
      and "/> Delete All System Data\n" in page
      and "Selected deletion" not in page and "Delete All Data" not in page
      and "Hold to delete events" not in page and "Hold to reset database" not in page)
check("Delete All takes the same three-second hold as a pick, no typed name",
      'scope: "all" })' in page and "requireText" not in page
      and 'scope: "all", label: "All Recorded Data"' in page)
for label in ("Events", "Footage", "Database", "Events log", "Logs"):
    check(f"...the menu offers {label!r}", f'label: "{label}"' in page)
for scope in rt.PURGE_SCOPES[1:]:
    check(f"...and each pick names the API scope {scope!r}", f'scope: "{scope}"' in page)
check("...the menu's label sits inside a group (Base UI throws #31 without one, "
      "and that took the whole Settings page down on first click)",
      page.index("<DropdownMenuGroup>") < page.index("<DropdownMenuLabel")
      < page.index("</DropdownMenuGroup>"))
check("...a pick is still held for three seconds before it acts",
      "<HoldToDelete" in page and "pick.scope" in page
      and page.count("Keep holding…") >= 1)
hold = read("frontend", "src", "components", "ui", "HoldToDelete.jsx")
check("...and the warning's 3 seconds is the hold's own 3000 ms, never overridden",
      "const HOLD_SECONDS = 3;" in page and "holdMs = 3000," in hold
      and "holdMs=" not in page
      and "for {HOLD_SECONDS} seconds" in page)
check("...and the toast for logs reports files and the ring",
      "log file(s)" in page and "master_ring" in page)
check("the reset progress still takes the menu's place while it runs",
      "Resetting: {resets[s.slug].step" in page)

# -- 7. the Trash tells the truth when a folder will not go -------------------
store_systems = str(__import__("core.config", fromlist=["x"]).generated_systems_dir())
NVME = os.path.dirname(os.path.dirname(store_systems))
GHOST = os.path.join(NVME, "stuck_ghost")
os.makedirs(os.path.join(GHOST, "data", "snapshots", ".thumbs"), exist_ok=True)
with open(os.path.join(GHOST, "data", "snapshots", ".thumbs", "a.jpg"), "wb") as f:
    f.write(b"x")

real_rmtree = shutil.rmtree


def stubborn_rmtree(path, ignore_errors=False, onerror=None, **kw):
    """rmtree as pi meets a root-owned .thumbs folder: everything else goes,
    that folder and its file report EACCES through onerror."""
    for d, dirs, files in list(os.walk(path, topdown=False)):
        for name in files:
            p = os.path.join(d, name)
            if ".thumbs" in p:
                onerror(os.remove, p, (PermissionError, PermissionError(13, "Permission denied"), None))
            else:
                os.remove(p)
        for name in dirs:
            p = os.path.join(d, name)
            if os.listdir(p):
                onerror(os.rmdir, p, (PermissionError, PermissionError(13, "Permission denied"), None))
            else:
                os.rmdir(p)
    if os.listdir(path):
        onerror(os.rmdir, path, (PermissionError, PermissionError(13, "Permission denied"), None))
    else:
        os.rmdir(path)


shutil.rmtree = stubborn_rmtree
try:
    out = provision_service.remove_orphan(os.path.join(ROOT, "systems"), "stuck_ghost")
finally:
    shutil.rmtree = real_rmtree
check("remove_orphan reports the files that would not go instead of answering ok",
      out["failed"] and any(".thumbs" in p for p in out["failed"]), out)
check("...and says what to do about them",
      "could not be removed" in (out.get("error") or "")
      and "sudo rm -rf" in out["error"] and "stuck_ghost" in out["error"], out)
check("...while a folder that does go is not reported",
      "stuck_ghost/data" not in out["removed"], out)
real_rmtree(GHOST, ignore_errors=True)

out = provision_service.remove_orphan(os.path.join(ROOT, "systems"), "stuck_ghost")
check("a clean removal carries an empty failed list and no error",
      out["failed"] == [] and "error" not in out, out)

for evil in ("..", ".", "../store", "a/b", "", "Store"):
    try:
        provision_service.remove_orphan(os.path.join(ROOT, "systems"), evil)
        refused = False
    except ValueError:
        refused = True
    check(f"remove_orphan refuses {evil!r} before any path is built", refused)
check("the store is still there after those",
      os.path.isdir(store_systems))

check("the panel warns and re-reads the list when the server reports leftovers",
      "d?.error" in page and "toast.warn" in page
      and "not everything could be removed" in page)

events_repo.delete_for_system = real_delete
if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
