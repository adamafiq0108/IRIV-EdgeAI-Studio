# What one dashboard poll costs the master (perf audit item L4, 2026-10-02).
#
# Main.jsx polls /api/dashboard every 600 ms -- deliberately, measured on the
# board (see the comment there), so the tiles keep up with the live picture
# beside them. So the poll keeps its rate and gets cheaper instead:
#   * the USB camera scan the dashboard triggers (cameras() ->
#     _refresh_usb_names -> list_cameras, which opens every /dev/video*) is
#     reused for LIST_CAMERAS_TTL_S; Camera Setup's list still scans fresh;
#   * the config reads behind it no longer re-parse unchanged files (L3).
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


from core import tomlcache                                           # noqa: E402
from core.services import detector_control as dc                     # noqa: E402

scans = []
real_scan = dc._scan_cameras
dc._scan_cameras = lambda: scans.append(1) or [{"index": 0, "path": "/dev/video0",
                                                "name": "USB cam", "kind": "usb",
                                                "slot": None}]
dc.forget_camera_scan()
for _ in range(10):
    dc.list_cameras()
check("ten dashboard polls inside the TTL scan the cameras once", len(scans) == 1)
dc._cam_scan["at"] -= dc.LIST_CAMERAS_TTL_S + 1
dc.list_cameras()
check("...and again once it has aged out", len(scans) == 2)
got = dc.list_cameras()
got[0]["name"] = "changed by a caller"
check("each caller gets its own copy", dc.list_cameras()[0]["name"] == "USB cam")
dc.discover_cameras()
check("Camera Setup's list always scans fresh", len(scans) == 3)
check("...and the TTL is short enough that a replug shows on the dashboard soon",
      dc.LIST_CAMERAS_TTL_S <= 5)
dc._scan_cameras = real_scan

systems = tempfile.mkdtemp()
pkg = os.path.join(systems, "s")
os.makedirs(pkg)
with open(os.path.join(pkg, "config.toml"), "w") as f:
    f.write("[app]\ntitle = 'S'\n[camera]\nsource = '0'\n")
dc._write_local(pkg, {"detector": {"kind": "none"},
                      "cameras": [{"id": "cam1", "source": "0", "kind": "usb"}]})
dc.cameras(systems, "s")
tomlcache.stats.update(parses=0, hits=0)
for _ in range(10):
    dc.cameras(systems, "s")
    dc.system_config(systems, "s")
check("repeated dashboard reads of an unchanged system parse nothing",
      tomlcache.stats["parses"] == 0 and tomlcache.stats["hits"] > 0, tomlcache.stats)
main = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Main.jsx"),
               encoding="utf-8").read()
check("the dashboard keeps its measured 600 ms (no visible change for users)",
      "const REFRESH_MS = 600;" in main)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
