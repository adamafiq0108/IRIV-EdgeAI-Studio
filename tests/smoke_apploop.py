# The detection loop must actually RUN once, in a test.
#
# Every other test here checks a piece: the engine's numbers, the compiler's
# output, the stream server's clients. Nothing ran main(). So a NameError in
# the draw path -- one undefined variable, on the line that feeds a clip --
# passed the whole suite, shipped, and killed the app on the Pi at the first
# frame. The traceback was invisible too: the failure unwound into the
# shutdown, which segfaults inside libhailort, so the process died before
# Python could print what went wrong.
#
# One cycle, synthetic camera, mock detector. It is not a test of what the
# numbers mean -- it is the test that the loop's own code exists.
import os
import socket
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import run as apprun                                # noqa: E402

DATA = tempfile.mkdtemp()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


CFG = {
    "app": {"stream_port": _free_port(), "stream_bind": "127.0.0.1"},
    # Synthetic: no device to open, and it produces a real BGR frame (see
    # smoke_synthetic.py), which is what the draw path needs.
    "camera": {"source": "synthetic", "width": 320, "height": 240, "fps": 5},
    # Mock: invented detections, so the loop draws BOXES rather than an empty
    # frame -- the annotate path is most of what this covers.
    "detector": {"kind": "mock"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
}

apprun.config_mod.load = lambda *a, **k: CFG
apprun.config_mod.data_dir = lambda *a, **k: DATA

rc = apprun.main(["--once"])
assert rc == 0, f"one cycle of the detection loop returned {rc}"
print("== one full cycle: camera, detector, functions, draw, stream")

# The loop leaves behind exactly what the rest of the app reads: a runtime
# state file, and (on this synthetic path) a frame that encoded.
state = os.path.join(DATA, "state.json")
assert os.path.exists(state), "the loop wrote no runtime state"
print("== and it wrote its runtime state")
print("ALL OK")
