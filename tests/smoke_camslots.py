# USB camera slot pinning: a numeric camera source resolves through the
# /dev/cam-usb<n> udev symlink (pinned to the PHYSICAL socket) when it
# exists, so "camera 0" follows the socket across replugs and enumeration
# wander -- and behaves exactly as before when no symlink is installed.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from unittest import mock                                     # noqa: E402

from core.services import detector_control as d               # noqa: E402

# 1. no symlink -> bare kernel index, backend unchanged (old behaviour)
with mock.patch.object(d.os.path, "islink", return_value=False):
    arg, backend = d.open_capture_spec("0")
print("no slot   ->", arg, backend)
assert (arg, backend) == (0, None), (arg, backend)

# 2. slot symlink present -> the node behind the SOCKET, as an INTEGER index
# (this OpenCV's V4L2 backend refuses "/dev/videoN" path strings), V4L2 pinned
with mock.patch.object(d.os.path, "islink",
                       side_effect=lambda p: p == "/dev/cam-usb0"), \
     mock.patch.object(d.os.path, "realpath", return_value="/dev/video4"), \
     mock.patch.object(d.os.path, "exists",
                       side_effect=lambda p: p == "/dev/video4"):
    arg, backend = d.open_capture_spec("0")
print("slot 0    ->", arg, backend)
assert (arg, backend) == (4, "CAP_V4L2"), (arg, backend)

# 3. symlink dangling (camera unplugged) -> falls back to the bare index
with mock.patch.object(d.os.path, "islink",
                       side_effect=lambda p: p == "/dev/cam-usb0"), \
     mock.patch.object(d.os.path, "realpath", return_value="/dev/video4"), \
     mock.patch.object(d.os.path, "exists", return_value=False):
    arg, backend = d.open_capture_spec("0")
print("dangling  ->", arg, backend)
assert (arg, backend) == (0, None), (arg, backend)

# 4. non-numeric sources never touch the slot machinery
arg, backend = d.open_capture_spec("rtsp://cam/stream")
assert backend == "CAP_FFMPEG", (arg, backend)

# 5. the app's mirror carries the same slot resolution (it must not import
# core/, so the logic is duplicated on purpose -- keep both in step)
with open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
          encoding="utf-8") as f:
    app_src = f.read()
assert '/dev/cam-usb' in app_src, "app _capture_spec lost slot resolution"
assert 'int(m.group(1))' in app_src, "slot must resolve to an INTEGER index"
# ...and the share alias must not cross two pinned sockets
assert 'not os.path.islink(f"/dev/cam-usb{source}")' in app_src, \
    "numeric share alias must stay off when slots are pinned"

# 6. multi-camera sharing hardening (one box, many apps, one registry file):
#    - registry writers serialise under flock (watched live: an owner's row
#      appeared and vanished in seconds under concurrent heartbeat saves)
#    - /raw serves fresh grabs at delivery rate, not the owner's chip share
assert "def _reg_update" in app_src
assert "fcntl.flock" in app_src, "registry writers must lock"
for fn in ("def _set", "def _drop", "def _beat"):
    assert fn in app_src, f"{fn}: registry writer not under _reg_update"
assert "_grab_seq" in app_src and "_jpeg_seq" in app_src, \
    "last_jpeg lost its freshness tracking"
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()
assert "get_raw_fps" in run_src, "/raw must pace at delivery rate"
assert "measured_fps" in run_src

# 7. the udev rules ship with the repo and cover both hub sides per socket
with open(os.path.join(ROOT, "scripts", "udev",
                       "83-cytron-cam-slots.rules"), encoding="utf-8") as f:
    rules = f.read()
for kernels in ("5-1.2", "4-1.2", "4-1.3", "5-1.3"):
    assert f'KERNELS=="{kernels}"' in rules, kernels
assert 'SYMLINK+="cam-usb0"' in rules
assert 'SYMLINK+="cam-usb1"' in rules
assert 'ttyRS485' not in rules, "cameras only -- no serial rules here"
assert rules.count(':capture:') >= 4, "capture-node filter missing"

# A shared-feed CONSUMER must not negotiate device properties: FOURCC and
# FRAME_WIDTH/HEIGHT describe a device, and forcing them onto the consumed
# /raw MJPEG stream (which is whatever size its owner serves) made ffmpeg
# insert a mismatched swscale -- "Slice parameters 0, 480 are invalid" once
# per frame in every consumer's log, at frame rate.
with open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
          encoding="utf-8") as f:
    cam_src = f.read()
assert "backend != cv2.CAP_GSTREAMER and self._consuming is None" in cam_src,     "shared-feed consumers must skip the device property block"

# A NAMED camera never falls back to the first one -- not even mid-stream.
# The request-time 404 only covers new connections; a viewer whose camera was
# deactivated while streaming used to have _pipe_for's primary fallback swap
# its picture to a different camera. Now: bare path = primary, named-and-gone
# = None, and a stream that serves nothing for 10s ends so the reconnect gets
# the honest 404.
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()

assert "return by_id.get(cid)" in run_src and "return primary" in run_src, \
    "_pipe_for must reserve the primary fallback for the BARE path"
assert "none_since" in run_src, \
    "a stream with no frames must end rather than placeholder forever"

print("smoke_camslots OK")
