"""Every camera's frame is in flight on the accelerator before any is waited for.

Measured on 192.168.1.234 (Hailo-8, HailoRT 4.23, yolov8m): one system alone
on the chip ran 7.2 fps per camera exclusive and 7.1 through the service, so
the service costs nothing -- and the zoo's number for this model on this chip
is 25-30. The gap was the loop: letterbox, submit, WAIT ~65 ms, parse, next
camera. One frame in flight, ever, so DMA-in, compute and DMA-out never
overlapped and the box idled on each round trip.

detect_many() submits every frame first and only then waits for each. Two
cameras become two frames in flight. Results come back in order, per frame,
in frame pixels, exactly as before; a None frame answers [] in its slot.

Exercised with a FAKE ConfiguredInferModel, because the real one needs the
chip: what is pinned is the ORDER of calls (every submit before the first
wait), that buffers stay alive until their job is waited on, that results map
back to the right frame, and that the parse still puts boxes in frame pixels.

    python tests/smoke_hailoasync.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. the shape, in source ---------------------------------------------------
hailo_src = read("systems", "demo", "app", "hailo.py")
run_src = read("systems", "demo", "app", "run.py")
det_src = read("systems", "demo", "app", "detector.py")

check("every detector has detect_many; the base one loops",
      "def detect_many(self, frames):" in det_src
      and "return [self.detect(f) for f in frames]" in det_src)
check("the Hailo detector overlaps: every chunk submitted before any wait",
      "def detect_many(self, frames):" in hailo_src
      and hailo_src.index("job = self._infer.run_async(bindings)")
        < hailo_src.index("job.wait(budget)")
      and hailo_src.index("pending.append((chunk, preps, job))")
        < hailo_src.index("for chunk, preps, job in pending:"))
check("...gated on the pipeline's queue, never blind",
      "self._infer.wait_for_async_ready(timeout_ms=budget, frames_count=size)" in hailo_src)
check("...and detect() is the batch of one", "return self.detect_many([frame])[0]" in hailo_src)
# The read is wrapped in _pre() -- this camera's software pre-processing,
# which sits between the grab and the detector by design (app/preprocess.py).
# What is pinned is unchanged: every camera is READ first, then ONE detector
# call takes the lot.
check("the run loop reads every camera, THEN makes one detector call",
      "frames = [(pipe, _pre(pipe, _read_frame(pipe, now)))" in run_src
      and "for pipe in pipes]" in run_src
      and "_infer_batch([f for _p, f in frames])" in run_src)
check("...through the failure ladder, once per loop not once per camera",
      "results = det.detect_many([f for _, f in good])" in run_src)
check("...with bad inputs answered [] and never counted against the chip",
      "if f is not None and hasattr(f, \"shape\")]" in run_src)
check("the async path is opt-in, and the reason is the measurement",
      'self.config.get("async", False) is True' in hailo_src,
      "2 and 4 in flight measured the same 14.7 fps as sync on a saturated "
      "chip, and through the service the async path raised RPC errors")
check("the scheduler priority knob exists", "set_scheduler_priority(int(prio))" in hailo_src)
check("the input buffer is held until its job is waited on",
      '"input": resized' in hailo_src,
      "HailoRT DMAs straight out of it; a collected array is a corrupted frame")

# -- 2. the behaviour, against a fake accelerator ----------------------------
try:
    import numpy as np
    import cv2                                                  # noqa: F401
except ImportError:
    print("skip  numpy/cv2 missing here -- fake-chip round trip not exercised")
    np = None

if np is not None:
    from app.hailo import HailoDetector

    log = []

    class FakeSlot:
        def __init__(self, store, key):
            self.store, self.key = store, key
        def set_buffer(self, buf):
            self.store[self.key] = buf

    class FakeBindings:
        def __init__(self):
            self.bufs = {}
        def input(self):
            return FakeSlot(self.bufs, "in")
        def output(self, name):
            return FakeSlot(self.bufs, name)

    class FakeJob:
        def __init__(self, bindings, n):
            self.bindings, self.n = bindings, n
        def wait(self, timeout_ms):
            log.append(("wait", self.n))
            # The chip "writes" one class-0 box, normalised to the letterbox:
            # [count, ymin, xmin, ymax, xmax, score]
            out = self.bindings.bufs["out"]
            out[:] = 0
            out[:6] = [1, 0.10, 0.20, 0.50, 0.60, 0.90]

    class FakeOut:
        shape = (64,)

    class FakeModel:
        def output(self, name):
            return FakeOut()

    class FakeInfer:
        def __init__(self):
            self.n = 0
        def create_bindings(self):
            return FakeBindings()
        def wait_for_async_ready(self, timeout_ms=1000, frames_count=1):
            log.append(("ready", self.n))
        def run_async(self, bindings, callback=None):
            self.n += 1
            log.append(("submit", self.n))
            return FakeJob(bindings[0], self.n)
        def run(self, bindings, timeout):
            raise AssertionError("the synchronous path must not run when async is on")

    d = HailoDetector.__new__(HailoDetector)
    d._infer = FakeInfer()
    d._model = FakeModel()
    d._output_names = ["out"]
    d.input_size = (640, 640)
    d.labels = ["person"]
    d.classes = set()
    d.min_confidence = 0.3
    d._async = True

    a = np.zeros((480, 640, 3), dtype=np.uint8)       # 4:3, letterboxed at bottom
    b = np.zeros((360, 1280, 3), dtype=np.uint8)      # wide, scale 0.5
    res = d.detect_many([a, None, b])

    kinds = [k for k, _ in log]
    check("both frames are submitted before the first wait",
          kinds == ["ready", "submit", "ready", "submit", "wait", "wait"], str(log))
    check("a None frame answers [] in its own slot", res[1] == [] and len(res) == 3)
    check("each frame gets its own box back", len(res[0]) == 1 and len(res[2]) == 1)
    # Frame a: 640x480 into 640x640 -> scale 1.0; xmin .2 -> 128, ymin .1 -> 64,
    # xmax .6 -> 384, ymax .5 -> 320.
    check("...in that frame's own pixels (4:3 frame, scale 1)",
          res[0][0]["box"] == [128, 64, 384, 320] and res[0][0]["label"] == "person",
          str(res[0]))
    # Frame b: 1280x360 -> scale 0.5; the same normalised box lands at double.
    check("...and the wide frame's box is scaled back by ITS letterbox (scale .5)",
          res[2][0]["box"] == [256, 128, 768, 360], str(res[2]))
    check("...with confidence carried through", res[0][0]["confidence"] == 0.9)

    # The synchronous path, when asked for.
    log.clear()
    class SyncInfer(FakeInfer):
        def run(self, bindings, timeout):
            log.append(("run", 0))
            FakeJob(bindings[0], 0).wait(timeout)
        def run_async(self, *a, **k):
            raise AssertionError("async must not be used when switched off")
    d._infer = SyncInfer()
    d._async = False
    res2 = d.detect_many([a, b])
    kinds = [k for k, _ in log]
    check("async = false takes the synchronous path, one frame at a time",
          kinds.count("run") == 2 and "submit" not in kinds and "ready" not in kinds, str(log))
    check("...and answers the same boxes", len(res2) == 2 and res2[0] == res[0] and res2[1] == res[2])

    # detect() is the batch of one.
    check("detect(frame) still answers a plain list", d.detect(a) == res[0])

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
