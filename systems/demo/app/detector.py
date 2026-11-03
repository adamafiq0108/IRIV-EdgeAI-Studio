# -*- coding: utf-8 -*-
"""
detector.py -- the inference interface, its registry, and a mock detector.

One rule: **the loop never imports a model.** It asks the registry for the
detector named in config and calls `detect(frame)`. Swapping YOLO for ONNX for
a vendor SDK is a new class and one config line, and the loop, the store, the
adapter and the dashboard are all unchanged.

A detector returns plain dicts, never framework tensors -- a torch tensor
leaking out of here would put torch in the import path of the adapter, the
agent, and eventually the master.
"""

import random
import time

#: Detection dict: {"label": str, "confidence": float, "box": [x1,y1,x2,y2]}


class Detector:
    """Subclass this. Two methods, both cheap to get right."""

    name = "base"
    #: How many frames this detector wants per call. The run loop collects
    #: that many per camera before asking; 1 is "one frame at a time", which
    #: is right for everything that cannot amortise anything (see hailo.py).
    batch = 1

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.min_confidence = float(self.config.get("confidence", 0.5))

    def detect(self, frame):
        """Return a list of detection dicts for one frame.

        `frame` is whatever camera.py yields -- a numpy array with cv2, a PIL
        image without it. A detector that needs a specific type checks and says
        so; it must never crash the loop.
        """
        raise NotImplementedError

    def detect_many(self, frames):
        """One call for SEVERAL frames -- one per camera -- answered in order.

        The run loop calls this, not detect(): a detector that can keep more
        than one frame in flight (hailo.py) overlaps them, and that is where a
        two-camera system's inference rate doubles. The default is the plain
        loop, so every detector has the method and none has to know why.
        Frames arrive already screened -- the loop drops None and non-arrays
        before asking, so a bad input is never counted as a broken detector.
        """
        return [self.detect(f) for f in frames]

    def close(self):
        """Release the model/session. Called on shutdown."""


class MockDetector(Detector):
    """Synthetic detections at a configured rate.

    Exists so the whole stack -- app, adapter, agent, master, hub -- runs on a
    laptop with no camera, no model and no GPU, and so CI can assert on event
    flow deterministically with a seed.
    """

    name = "mock"

    def __init__(self, config=None):
        super().__init__(config)
        mock = self.config.get("mock") or {}
        self.per_minute = float(mock.get("events_per_minute", 12))
        self.violation_ratio = float(mock.get("violation_ratio", 0.3))
        self.zone = list(self.config.get("zone") or [220, 140, 420, 340])
        self._rng = random.Random(mock.get("seed"))
        self._next_at = 0.0

    def detect(self, frame):
        now = time.time()
        if now < self._next_at:
            return []
        gap = 60.0 / self.per_minute if self.per_minute > 0 else 5.0
        self._next_at = now + self._rng.uniform(gap * 0.5, gap * 1.5)

        inside = self._rng.random() < self.violation_ratio
        if inside:                       # a box that overlaps the zone
            x1, y1, x2, y2 = self.zone
            box = [x1 + 10, y1 + 10, x2 - 10, y2 - 10]
        else:                            # a box safely outside it
            box = [10, 10, 90, 120]
        return [{"label": "object",
                 "confidence": round(self._rng.uniform(0.55, 0.95), 2),
                 "box": [int(v) for v in box]}]


class NullDetector(Detector):
    """Detects nothing, on purpose.

    The safety fallback when a REAL detector cannot run (camera lost, runtime
    error, model missing). Falling back to MockDetector here invents detections
    that look real on every dashboard -- counts, events, snapshots -- which is
    far worse than honestly detecting nothing. Mock stays the fallback only
    when mock was what the operator configured.
    """

    name = "none"

    def detect(self, frame):
        return []


def fallback(config):
    """The right do-no-harm detector when the configured one fails: mock if
    mock was configured (its synthetic events are the point), silence
    otherwise."""
    kind = (config or {}).get("kind") or "mock"
    return MockDetector(config) if kind == "mock" else NullDetector(config)


def _load_yolo(config):
    """Reference slot for a real model. Imported ONLY when configured, so the
    heavy dependency never enters the import path of a laptop clone or CI.

        [detector]
        kind = "yolo"
        weights = "yolov8n.pt"
        classes = ["person"]
    """
    from ultralytics import YOLO       # noqa: PLC0415  (deliberate lazy import)

    class YoloDetector(Detector):
        name = "yolo"

        def __init__(self, cfg):
            super().__init__(cfg)
            self.model = YOLO(cfg.get("weights") or "yolov8n.pt")
            self.classes = set(cfg.get("classes") or [])

        def detect(self, frame):
            out = []
            for r in self.model.predict(frame, verbose=False):
                for b in r.boxes:
                    conf = float(b.conf[0])
                    label = r.names[int(b.cls[0])]
                    if conf < self.min_confidence:
                        continue
                    if self.classes and label not in self.classes:
                        continue
                    out.append({"label": label, "confidence": round(conf, 2),
                                "box": [int(v) for v in b.xyxy[0].tolist()]})
            return out

    return YoloDetector(config)


def _load_hailo(config):
    """Hailo-8 / 8L / 10H via HailoRT. Imported only when configured, so a
    laptop clone never needs the runtime installed (see app/hailo.py).

    A `models` list (detector.[[models]]) runs SEVERAL models on the one camera
    and one shared accelerator, in this process -- the reliable way to run
    multiple models on a single camera."""
    from app.hailo import HailoDetector, MultiHailoDetector  # noqa: PLC0415

    # The Hailo params (model, models_dir, labels, classes, confidence) live in
    # the [detector.hailo] sub-table; lift them to the top level HailoDetector
    # reads from, so setting them under [detector.hailo] actually takes effect.
    # Multi needs the same lift: each [[detector.models]] entry is merged OVER
    # this shared config, so a class filter written only under [detector.hailo]
    # used to apply with one model and silently vanish with two.
    merged = {**config, **(config.get("hailo") or {})}
    if merged.get("models"):
        return MultiHailoDetector(merged)
    return HailoDetector(merged)


def _load_onnx(config):
    """ONNX Runtime slot: CPU, CUDA, or a vendor execution provider.

    Kept as a stub rather than a guess -- output decoding differs per exported
    model, and a wrong decode looks like a working detector that never fires.
    Implement `detect()` against your export, then register it here.
    """
    raise NotImplementedError(
        "onnx detector: implement decoding for your exported model, then "
        "return an instance from this factory")


#: name -> factory(config) -> Detector. Add yours here; nothing else changes --
#: not the loop, not the store, not the adapter, not the dashboard.
REGISTRY = {
    "mock": MockDetector,       # no dependencies; the default
    "none": NullDetector,       # detects nothing; the safe fallback
    "yolo": _load_yolo,         # ultralytics, CPU/GPU
    "hailo": _load_hailo,       # Hailo-8 / 8L / 10H accelerators
    "onnx": _load_onnx,         # stub: bring your own export
}


def build(config):
    """Instantiate the detector named by config['kind'] (default "mock")."""
    kind = (config or {}).get("kind") or "mock"
    factory = REGISTRY.get(kind)
    if factory is None:
        raise ValueError(f"unknown detector kind {kind!r}; have {sorted(REGISTRY)}")
    return factory(config)


def overlaps(box, zone):
    """True if a detection box intersects the zone rectangle.

    The zone rule lives here, not in the loop, because "what counts as a
    violation" is the one piece of real domain logic in a detection app and it
    must be unit-testable without a camera.
    """
    if not zone or len(zone) != 4:
        return False
    ax1, ay1, ax2, ay2 = box
    zx1, zy1, zx2, zy2 = zone
    return not (ax2 < zx1 or ax1 > zx2 or ay2 < zy1 or ay1 > zy2)
