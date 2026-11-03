# Switching a camera off must actually stop it -- and keep it stopped.
#
# Deactivating a camera released its device and then took it straight back,
# because two mechanisms disagreed about what "no frames" means:
#
#   1. The config reload closed the pipe but refused to COMMIT an empty camera
#      list (`if kept:`), so the closed pipe stayed in the live list and
#      cam_list never caught up -- the reload then re-ran every tick for ever.
#   2. The recovery ladder, whose whole job is resurrecting a camera that
#      DIED, could not tell that from one the operator switched off. It
#      reopened the device within seconds.
#
#   3. And on the next start the app fell back to the legacy [camera] table,
#      because _camera_list answers [] both for "no cameras" and for "every
#      camera off" -- so a system with everything deactivated opened a camera
#      nobody had asked for.
#
# The fix is a PARKED pipe: closed, skipped by recovery, never opened at
# start, and rebuilt the moment somebody switches it back on. Verified on
# hardware -- the device is released within 5s and stays released.
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)

from app.run import _camera_list                              # noqa: E402

RUN = os.path.join(ROOT, "systems", "demo", "app", "run.py")
with open(RUN, encoding="utf-8") as f:
    src = f.read()

# 1. A switched-off camera is not captured.
assert _camera_list({"cameras": [{"id": "a", "source": "0", "enabled": False}]}) == [], \
    "a camera switched off must not be captured"
assert [c["id"] for c in _camera_list(
    {"cameras": [{"id": "a", "source": "0", "enabled": False},
                 {"id": "b", "source": "1"}]})] == ["b"], \
    "switching one camera off must leave the others running"

# 2. The ambiguity the startup path has to resolve: [] means BOTH "no cameras
#    configured" and "every camera switched off". Anything keying on the
#    filtered list alone cannot tell them apart -- which is why the fix reads
#    the RAW table.
assert _camera_list({"cameras": []}) == []
assert _camera_list({"cameras": [{"id": "a", "source": "0", "enabled": False}]}) == []

# 3. Recovery must leave a parked camera alone. The check has to come BEFORE
#    the reconnect ladder, or the ladder reopens it first and the flag is
#    decoration.
read_frame = src.split("def _read_frame(", 1)[1].split("\n    def ", 1)[0]
assert "if pipe.parked:" in read_frame, \
    "_read_frame must skip a parked camera"
park_at = read_frame.index("if pipe.parked:")
ladder_at = read_frame.index("cam_fails")
assert park_at < ladder_at, \
    "the parked check must come before the reconnect ladder, not after it"

# 4. The reload must COMMIT an empty camera list. The old `if kept:` guard
#    refused to, so cam_list never caught up and the block re-ran every tick.
assert "if not new_cam_list:" in src, \
    "the reload must handle 'every camera switched off' explicitly"
empty_branch = src.split("if not new_cam_list:", 1)[1].split("elif kept:", 1)[0]
assert "p.parked = True" in empty_branch, "all-off must park the pipes"
assert "p.close()" in empty_branch, "all-off must release the devices"
assert "cam_list = new_cam_list" in empty_branch, \
    "all-off must commit cam_list, or the reload re-runs every tick for ever"

# 5. Switching one back on must reopen it. A parked pipe's settings are
#    unchanged, so the reload's "nothing changed, leave the video alone"
#    branch would skip it -- Active would light up over a dead camera.
assert re.search(r"if have\.parked:\s*\n\s*same = False\s*\n\s*have\.parked = False",
                 src), \
    "unparking must force a reopen however equal the settings look"

# 6. A start with every camera off must not open the legacy [camera] row.
assert "all_off" in src, "startup must detect 'configured, but all switched off'"
assert 'cfg.get("cameras")' in src, \
    "all-off must be read from the RAW table -- the filtered list cannot say"
assert re.search(r"if not p\.parked:\s*\n\s*p\.cam\.open\(\)", src), \
    "a parked pipe must never be opened at startup"

print("smoke_camswitch OK")
