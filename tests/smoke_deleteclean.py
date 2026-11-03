"""Deleting a system deletes the system.

It stopped the app, stopped the master, disabled the entry, removed the
package folder -- and left behind every document the system had written. A
dashboard layout and a workflow store are keyed by slug and live OUTSIDE the
package, in the store's own layouts/ and workflows/ directories, so removing
the folder never touched them. Neither did anything else, ever.

That is a leak rather than untidiness, and the difference is visibility:
nothing lists a document whose system does not exist, so an operator cannot
find one to remove. A box in use for months accumulates them silently -- one
had thirty layouts and thirty-one workflow files for fourteen systems, plus
three package folders nothing claimed. The rows in the shared database went
the same way: retention has known how to remove a system's events and Data
Table rows for a long time, and the delete path never called it.

    python tests/smoke_deleteclean.py
"""
import io
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
# The store gets a PRIVATE parent. The data-folder scan walks <store>/.. (on a
# board, the NVMe beside the store), and with the store straight in the system
# temp dir that parent was the whole temp dir: tens of thousands of entries
# (two minutes per run on a dev laptop), other processes' folders counted as
# orphans, and this test's own ghost/parked/live folders left behind in it.
STORE = os.path.join(tempfile.mkdtemp(), "store")
os.makedirs(STORE)
os.environ["CYTRON_STORE"] = STORE

from core import config                                        # noqa: E402
from core.services import provision as provision_service       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def write_docs(slug, backup=False):
    """A layout and a workflow store, where the product puts them."""
    made = []
    for base, name in ((str(config.layouts_dir()), f"{slug}.json"),
                       (str(config.workflows_dir()), f"{slug}.json")):
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, name)
        io.open(path, "w", encoding="utf-8").write(json.dumps({"slug": slug}))
        made.append(path)
    if backup:
        path = os.path.join(str(config.workflows_dir()), f"{slug}.v2.bak.json")
        io.open(path, "w", encoding="utf-8").write("{}")
        made.append(path)
    return made


# -- 1. what belongs to a system, listed once --------------------------------
docs = provision_service.system_documents("alpha")
names = [os.path.basename(p) for p in docs]
check("a system's documents are named in one place",
      "alpha.json" in names and "alpha.v2.bak.json" in names,
      "the delete site and the orphan scan both need this list, and two "
      "copies would disagree the first time a fourth document is added")
check("...including the layout and the workflow store",
      any("layouts" in p for p in docs) and any("workflows" in p for p in docs))

# -- 2. removing them -------------------------------------------------------
made = write_docs("alpha", backup=True)
gone = provision_service._remove_documents("alpha")
check("removing a system removes its documents", len(gone) == 3,
      f"removed {gone}")
check("...and they are actually off the disk",
      not any(os.path.exists(p) for p in made))
check("...and removing a system with none of them is not an error",
      provision_service._remove_documents("never-existed") == [])

# -- 3. what the delete path now does ---------------------------------------
src = read("core", "services", "provision.py")
check("the gate is read BEFORE the folder is deleted",
      "ours = in_store or _is_generated(systems_dir, slug)" in src
      and src.index("ours = in_store") < src.index("shutil.rmtree(pkg_path)"),
      "the marker lives in system.py, so asking afterwards always answers "
      "'not generated' and would spare every document of every system this "
      "tool made")
check("...and the same gate decides the documents and the rows",
      "if ours:\n        documents = _remove_documents(slug, config_dir)" in src,
      "a hand-written package keeps its files so it can be re-enabled, and "
      "re-enabling wants its dashboard to still be there")
check("the shared database is cleared for that system too",
      "delete_for_system" in src and "_remove_rows" in src,
      "retention knew how; nothing called it on a delete")
check("...best effort, so a database that is down does not fail the removal",
      "must not fail a removal whose visible" in src)

# -- 4. the leftovers already on every box -----------------------------------
write_docs("ghost")
write_docs("alpha")
found = provision_service.orphan_documents(os.path.join(ROOT, "systems"))
slugs = [o["slug"] for o in found]
check("what no system claims is findable", "ghost" in slugs and "alpha" in slugs,
      f"found {slugs}")
check("...with what each one left named",
      sorted(f["kind"] for f in
             next(o for o in found if o["slug"] == "ghost")["files"])
      == ["layout", "workflow"])
check("...and a system that EXISTS is never listed as an orphan",
      "demo" not in slugs,
      "demo ships in the repo and is a system, not a leftover")

before = len(provision_service.orphan_documents(os.path.join(ROOT, "systems")))
provision_service.remove_orphan(os.path.join(ROOT, "systems"), "ghost")
after = provision_service.orphan_documents(os.path.join(ROOT, "systems"))
check("clearing one removes it", len(after) == before - 1
      and "ghost" not in [o["slug"] for o in after])

try:
    provision_service.remove_orphan(os.path.join(ROOT, "systems"), "demo")
    check("a live system's slug is refused, not cleaned", False,
          "this is the safety of the whole feature")
except ValueError as e:
    check("a live system's slug is refused, not cleaned",
          "not an orphan" in str(e))

# -- 5. the way in -----------------------------------------------------------
routes = read("core", "routes", "hub.py")
check("the hub lists them", '@router.get("/api/hub/orphans"' in routes)
check("...and removes one", '@router.delete("/api/hub/orphans/{slug}"' in routes)
for name in ("reviewing leftover files is an admin action",
             "removing leftover files is an admin action"):
    check(f"...admin only ({name.split()[0]})", name in routes)

# -- the DATA folder, which is the one an operator can see -------------------
# On a board with an NVMe the app puts events, snapshots, clips and state.json
# at <nvme>/<slug>/data -- outside the package. Nothing ever deleted it, so a
# box that had eight systems still showed eight folders in a file manager long
# after six were gone. That is the leak the operator reported, and it is the
# biggest one: the documents are kilobytes, these are gigabytes.
import shutil                                                     # noqa: E402
from core.services import provision as provision_service          # noqa: E402
from core import config as config_mod                             # noqa: E402

NVME = os.path.dirname(STORE)          # <store>/.. -- where data folders sit
GHOST = os.path.join(NVME, "ghost_system")
os.makedirs(os.path.join(GHOST, "data"), exist_ok=True)
with open(os.path.join(GHOST, "data", "events.jsonl"), "w", encoding="utf-8") as f:
    f.write("x" * 4096)

check("a data folder is found by its slug and its data/ subfolder",
      provision_service.data_dir_for("ghost_system") == GHOST)
check("...and a folder with no data/ inside is NOT a candidate",
      (os.makedirs(os.path.join(NVME, "not_ours"), exist_ok=True)
       or provision_service.data_dir_for("not_ours")) is None,
      "this walks a directory the operator owns too; a folder must never be a "
      "deletion candidate because its NAME happened to match")
check("...nor is the store itself, or a path trying to climb out",
      provision_service.data_dir_for("store") is None
      and provision_service.data_dir_for("../etc") is None
      and provision_service.data_dir_for("") is None)

listed = {o["slug"]: o for o in provision_service.orphan_documents(os.path.join(ROOT, "systems"))}
check("an orphan data folder is listed for tidying",
      "ghost_system" in listed
      and any(f["kind"] == "data" for f in listed["ghost_system"]["files"]))
check("...with its SIZE, because this one is measured in gigabytes",
      listed["ghost_system"]["bytes"] >= 4096,
      "'delete this' and 'delete this 4 GB of recordings' are different questions")
check("...and a folder that is not ours is not listed", "not_ours" not in listed)

provision_service.remove_orphan(os.path.join(ROOT, "systems"), "ghost_system")
check("removing the orphan removes the folder", not os.path.isdir(GHOST))
check("...and leaves the one that is not ours alone",
      os.path.isdir(os.path.join(NVME, "not_ours")))

# -- disabled is not deleted --------------------------------------------------
# Caught on a live box: the repo's own `demo` was listed as an orphan, with its
# master running, because it is not in [systems].enabled. Cleaning by "what is
# registered" would offer a disabled system's dashboard, workflow and whole
# recordings folder for deletion.
DISABLED = os.path.join(NVME, "parked")
os.makedirs(os.path.join(DISABLED, "data"), exist_ok=True)
os.makedirs(os.path.join(str(config_mod.generated_systems_dir()), "parked"),
            exist_ok=True)
listed = {o["slug"] for o in
          provision_service.orphan_documents(os.path.join(ROOT, "systems"))}
check("a package that exists CLAIMS its slug, enabled or not",
      "parked" not in listed,
      "a disabled system is switched back on later and expects its data")
try:
    provision_service.remove_orphan(os.path.join(ROOT, "systems"), "parked")
    refused = False
except ValueError:
    refused = True
check("...and removing it is refused outright", refused)

# -- and a folder a live app is writing is never leftover ----------------------
LIVE = os.path.join(NVME, "live_ghost")
os.makedirs(os.path.join(LIVE, "data"), exist_ok=True)
with open(os.path.join(LIVE, "data", "state.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": os.getpid(), "running": True, "heartbeat": time.time()}, f)
check("a data folder a running app is writing is not a candidate",
      provision_service.data_dir_for("live_ghost") is None,
      "an app running from a package that has gone missing is exactly when "
      "the registry is the least trustworthy witness")
with open(os.path.join(LIVE, "data", "state.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": os.getpid(), "running": True, "heartbeat": 0}, f)
check("...but a stale heartbeat does not protect it for ever",
      provision_service.data_dir_for("live_ghost") == LIVE)

# Trash lives in Settings > Data Retention (2026-09-14), not behind a
# toolbar button on the Systems page: it is housekeeping about stored data.
page = read("frontend", "src", "components", "RetentionPanel.jsx")
check("Settings > Data Retention has an Unused Data section that lists them",
      "function TrashPane" in page and "/api/hub/orphans" in page and "<TrashPane />" in page)
check("...and the Systems page no longer carries the button",
      "Leftover files" not in read("frontend", "src", "pages", "Hub.jsx"))
check("...and removing one is confirmed as the deletion it is",
      "destructive" in page and 'title="Remove unused data?"' in page
      and 'confirmLabel="Remove Unused Data"' in page)
check("...and an empty result says so rather than showing a blank box",
      "No unused data found" in page)
check("...and the row says the recordings go too, with how much",
      "Recorded data (events, snapshots, video clips)" in page
      and "fileSize(o.bytes)" in page)
check("...and no customer-facing text calls it Trash",
      "/> Trash\n" not in page and "read the trash" not in page)

prov = read("core", "services", "provision.py")
check("deleting a system now deletes its data folder as well",
      "data_root = data_dir_for(slug)" in prov
      and '"data_deleted": data_deleted,' in prov,
      "otherwise every delete keeps making the orphans this page cleans up")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
