# One page per process, and a source on every row.
#
# WHAT WAS WRONG. Each process answered /api/logs with its OWN ring and
# nothing else. The hub showed hub lines, a master showed master lines, and
# the detection app -- which owns the cameras, the accelerator and the alert
# sending -- showed nothing anywhere, because it has no page and no ring to
# serve. An operator chasing "the camera on line 1 dropped at 3am" had three
# places to look, one of which did not exist.
#
# WHAT IS PINNED HERE.
#   * a master's page carries its own rows AND its app's, each labelled;
#   * the hub's page is the hub's rows only, and never asks a master
#     (Tok Aba, 2026-10-14); a master's page is its own and its app's, never
#     the hub's or another system's; its start-up rows are its own;
#   * the merged page is newest-first and bounded.
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control                    # noqa: E402
from core.services import log_collect                         # noqa: E402
from core.services import masters as masters_service          # noqa: E402

DATA = tempfile.mkdtemp()
NOW = time.time()


class State:
    """The slice of app.state the collector reads. Duck-typed on purpose:
    core services take state and config, never a framework object."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class System:
    def __init__(self, slug):
        self.slug = slug


def spool(*records):
    path = os.path.join(DATA, log_collect.SPOOL_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def local(message, ts):
    """A row as this process's own ring produces it."""
    return {"ts": ts, "level": "WARNING", "logger": "core.services.masters",
            "message": message, "module": "masters", "line": 31}


# The app's data dir is the app's own answer; the collector asks for it rather
# than guessing, so the test answers where the spool actually is.
detector_control.data_dir = lambda systems_dir, slug: DATA

spool({"ts": NOW - 5, "level": "ERROR", "logger": "app.camera",
       "message": "camera '0' gave no frames after 30 reads",
       "source": "camera:1309", "event_code": "CAM_DISCONNECTED"},
      {"ts": NOW - 9, "level": "WARNING", "logger": "app.hailo",
       "message": "another system holds the chip",
       "source": "hailo:702", "event_code": "AI_HAILO_BUSY"})

# -- on a master: its own rows, plus its app's --------------------------------
master_state = State(system=System("line1"), systems_dir="systems")
rows = log_collect.merge([local("master line", NOW - 1)], master_state)

by_src = {}
for row in rows:
    by_src.setdefault(row["source"], []).append(row)
assert set(by_src) == {"master:line1", "app:line1"}, sorted(by_src)
assert len(by_src["app:line1"]) == 2, by_src["app:line1"]
assert rows == sorted(rows, key=lambda r: r["ts"], reverse=True), \
    "the merged page is not newest-first, so the newest failure is not on top"
print("== a master's page carries its own rows and its app's, each labelled")

# The app's rows arrived with the catalog's wording and their own detail.
camera = next(r for r in rows if r.get("event_code") == "CAM_DISCONNECTED")
assert camera["category"] == "Camera", camera
assert "30 reads" in camera["detail"], camera
print("== an app row arrives catalogued, with its technical text intact")

# -- each page is its own process (Tok Aba, 2026-10-14) -----------------------
# "each system must show logs for itself only ... the main (hub) log should
# show hub logs only". The hub used to ask every master and merge them in.
asked = []


def fake_fetch(url, path, cookie=None, timeout=None):
    asked.append((url, path))
    return {"ok": True, "entries": [
        dict(local("line1 master row", NOW - 2), source="master:line1"),
        {"ts": NOW - 5, "level": "ERROR", "logger": "app.camera",
         "message": "camera gone", "event_code": "CAM_DISCONNECTED",
         "source": "app:line1"}]}


masters_service.fetch_json = fake_fetch
hub_state = State(masters={"line1": {"url": "http://127.0.0.1:5000"},
                           "line2": {"url": "http://127.0.0.1:5001"}})
rows = log_collect.merge([local("hub line", NOW)], hub_state)
assert asked == [], f"the hub still asked the masters for their logs: {asked}"
assert log_collect.sources(rows) == ["hub"], log_collect.sources(rows)
print("== the hub page holds the hub's rows only, even with masters answering")

# A row that somehow reached the hub's list carrying another process's source
# (a stale import, a future caller) is dropped, never shown as the hub's.
rows = log_collect.merge([local("hub line", NOW),
                          dict(local("smuggled", NOW - 1), source="master:line1"),
                          dict(local("smuggled app", NOW - 2), source="app:line1")],
                         hub_state)
assert [r["source"] for r in rows] == ["hub"], rows
assert log_collect.own_sources(hub_state) == {"hub"}
print("== a master: or app: row never appears on the hub's page")

# A master never returns the hub's rows or another system's.
rows = log_collect.merge([dict(local("other system", NOW - 1), source="master:line2"),
                          dict(local("other app", NOW - 2), source="app:line2"),
                          local("own", NOW - 3)], master_state)
assert {r["source"] for r in rows} == {"master:line1", "app:line1"}, \
    sorted({r["source"] for r in rows})
assert log_collect.own_sources(master_state) == {"master:line1", "app:line1"}
print("== a master's page never carries another system's rows")

# The start-up mislabel: records a master wrote before it named itself
# (module import, the start-up checks) went out as "hub". They are its own.
rows = log_collect.merge([dict(local("starting up", NOW - 1), source="hub")],
                         master_state)
early = next(r for r in rows if r["message"] == "starting up")
assert early["source"] == "master:line1", early
master_py = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "master_server.py"), encoding="utf-8").read()
main = master_py.split("def main(")[1]
assert main.index("logs_service.set_origin(logs_service.source_of_master(args.system))") \
    < main.index("create_app(args.system"), \
    "the master names itself only inside create_app, after its first records"
print("== a master's start-up records are its own, never the hub's")

# -- bounded, newest first ----------------------------------------------------
rows = log_collect.merge([local(f"flood {i}", NOW - i) for i in range(500)],
                         master_state, limit=25)
assert len(rows) == 25, len(rows)
assert rows == sorted(rows, key=lambda r: r["ts"], reverse=True)
print("== the page stays bounded and newest-first under a flood")

# -- the fan-out is gone, not just unused --------------------------------------
for name in ("master_records", "forget_masters", "purge_masters"):
    assert not hasattr(log_collect, name), f"log_collect.{name} is still there"

print("ALL OK")
