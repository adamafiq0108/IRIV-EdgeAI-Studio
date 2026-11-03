# -*- coding: utf-8 -*-
"""
gst_detector.py -- detection via a GStreamer + Hailo pipeline, self-contained.

The pipeline follows the TAPPAS patterns that give the best FPS on this
hardware, combined:

  *parallel networks*  -- ONE capture, tee'd into N inference branches (one per
                          model), all scheduled on the one Hailo by HailoRT.
                          Several models on one camera inside one process: no
                          cross-process chip fighting, no camera contention.
  *resolution preservation* -- the display branch keeps the camera's FULL
                          resolution; each inference branch scales its own copy
                          down to the network's 640x640. The dashboard shows
                          crisp video while the chip sees the small frame it
                          wants.
  *leaky queues*        -- every branch drops stale frames instead of blocking,
                          so a slow consumer never stalls capture or inference.

    <source> -> videoconvert -> RGB full-res -> tee
        t. -> queue(leaky) -> identity(display probe)          -> fakesink
        t. -> queue(leaky) -> videoscale -> 640x640
                           -> hailonet(model_i) -> hailofilter -> probe_i -> fakesink
        ... one inference branch per model ...

`hailonet` infers on-chip, `hailofilter` decodes NMS in C++ (filter_letterbox
maps boxes back out of the letterbox padding, so the normalized coords are
valid on the full-res display frame). Python only reads results and frames.

Self-contained: needs `import hailo` (TAPPAS bindings), the system GStreamer
elements, and the system post-process .so -- NOT the hailo_apps framework.
Construction failure raises a readable error; run.py falls back to mock.
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_SO = "/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so"
DEFAULT_FUNCTION = "filter_letterbox"


def _gst_quoted(value):
    """A value for a property in gst-launch TEXT, double-quoted and escaped.
    Unquoted, a URL holding a space or "!" (a camera password may) ended the
    property and started elements of its own."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _source_pipeline(source, width, height, fps):
    """The GStreamer source chain for a camera source string (same vocabulary
    as camera.py / detector_control): gige/rtsp/http/v4l2/test.

    CSI (libcamera) is refused -- this hardware has no ribbon connector."""
    s = str(source).strip()
    if s in ("", "synthetic", "test"):
        return "videotestsrc is-live=true pattern=ball"
    if s == "libcamera" or s.startswith("libcamera:"):
        raise ValueError(
            "CSI (libcamera) cameras are not supported on this hardware - "
            "re-add this camera as USB, IP (RTSP) or GigE")
    if s == "gige" or s.startswith("gige:"):
        name = s.split(":", 1)[1] if ":" in s else None
        cam = f" camera-name={_gst_quoted(name)}" if name else ""
        return f"aravissrc{cam}"
    if s.startswith("rtsp://"):
        return (f"rtspsrc location={_gst_quoted(s)} protocols=tcp latency=200 "
                "drop-on-latency=true ! rtph264depay ! avdec_h264")
    if s.startswith("http://") or s.startswith("https://"):
        return (f"souphttpsrc location={_gst_quoted(s)} is-live=true "
                "! multipartdemux ! jpegdec")
    dev = f"/dev/video{s}" if s.isdigit() else s
    return f"v4l2src device={_gst_quoted(dev)}"


class GstDetector:
    """A running Hailo GStreamer pipeline: one camera, one or many models.

    Config (systems/<slug>/config.toml):

        [detector]
        kind = "gstreamer"
        model = "yolov8n"            # single model...
        models_dir = "..."
        labels = ".../coco.txt"
        confidence = 0.45
        classes = ["person"]

        [[detector.models]]          # ...or several, tee'd in parallel
        model = "yolov8n"
        classes = ["person"]
        [[detector.models]]
        model = "yolov8s"
        classes = ["car"]

        [detector.gstreamer]
        so_path = ".../libyolo_hailortpp_postprocess.so"
        function = "filter_letterbox"
    """

    name = "gstreamer"

    def __init__(self, config=None):
        self.config = dict(config or {})
        gst_cfg = self.config.get("gstreamer") or {}
        hailo_cfg = self.config.get("hailo") or {}
        self.so_path = gst_cfg.get("so_path") or DEFAULT_SO
        self.function = gst_cfg.get("function") or DEFAULT_FUNCTION
        self.source = self.config.get("source") or "0"
        self.width = int(self.config.get("width") or 1280)
        self.height = int(self.config.get("height") or 720)
        self.fps = int(self.config.get("fps") or 30)

        # One entry per model branch: single-model config collapses to one.
        base = {
            "model": (self.config.get("model") or hailo_cfg.get("model") or "yolov8n"),
            "confidence": float(self.config.get("confidence",
                                hailo_cfg.get("confidence", 0.45))),
            "classes": (self.config.get("classes")
                        or hailo_cfg.get("classes") or []),
        }
        specs = self.config.get("models") or [base]
        self._branches = []
        for spec in specs:
            merged = {**base, **(spec or {})}
            self._branches.append({
                "model": merged.get("model") or "yolov8n",
                "confidence": float(merged.get("confidence") or 0.45),
                "classes": set(merged.get("classes") or ()),
                "dets": [],
                "frames": 0,
                "fps": 0.0,
                "last": 0.0,
            })

        self._lock = threading.Lock()
        self._frame = None
        self._pipeline = None
        self._loop = None
        self._error = None
        self.labels = self._load_labels(
            self.config.get("labels") or hailo_cfg.get("labels"))
        self._start()

    # -- setup ----------------------------------------------------------------

    def _load_labels(self, path):
        if not path or not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def _resolve_hef(self, model):
        from app.hailo import resolve_hef, detect_arch      # noqa: PLC0415
        models_dir = (self.config.get("models_dir")
                      or (self.config.get("hailo") or {}).get("models_dir")
                      or "models")
        return resolve_hef(models_dir, model, detect_arch())

    def _start(self):
        try:
            import gi                                        # noqa: PLC0415
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst, GLib              # noqa: PLC0415
        except (ImportError, ValueError) as e:
            raise RuntimeError(
                "GStreamer Python bindings not available (gi/Gst). Install "
                "python3-gi + gstreamer, or use detector.kind = \"hailo\"."
            ) from e
        import hailo                                         # noqa: PLC0415,F401

        if not os.path.exists(self.so_path):
            raise RuntimeError(f"post-process .so not found: {self.so_path}")

        Gst.init(None)
        self._Gst = Gst

        src = _source_pipeline(self.source, self.width, self.height, self.fps)
        q = "queue leaky=downstream max-size-buffers=2"

        # Full-res display branch + one scaled inference branch per model
        # (TAPPAS parallel-networks + resolution-preservation, via tee).
        parts = [
            f"{src} ! videoconvert ! videoscale "
            f"! video/x-raw,format=RGB,width={self.width},height={self.height} "
            f"! tee name=t",
            f"t. ! {q} ! identity name=display ! fakesink sync=false",
        ]
        for i, br in enumerate(self._branches):
            hef = self._resolve_hef(br["model"])
            parts.append(
                f"t. ! {q} ! videoscale add-borders=true "
                f"! video/x-raw,format=RGB,width=640,height=640 "
                f"! {q} ! hailonet hef-path={hef} "
                f"! {q} ! hailofilter so-path={self.so_path} "
                f"function-name={self.function} qos=false "
                f"! identity name=cb{i} ! fakesink sync=false")
        desc = " ".join(parts)
        logger.info("[GST] pipeline: %s", desc)
        self._pipeline = Gst.parse_launch(desc)

        display = self._pipeline.get_by_name("display")
        display.get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER, self._on_display)
        for i, br in enumerate(self._branches):
            cb = self._pipeline.get_by_name(f"cb{i}")
            cb.get_static_pad("src").add_probe(
                Gst.PadProbeType.BUFFER,
                lambda pad, info, b=br: self._on_infer(pad, info, b))

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)

        self._pipeline.set_state(Gst.State.PLAYING)
        self._loop = GLib.MainLoop()
        threading.Thread(target=self._loop.run, daemon=True).start()
        logger.info("[GST] %d model branch(es) on %s (full-res display %dx%d)",
                    len(self._branches),
                    ", ".join(b["model"] for b in self._branches),
                    self.width, self.height)

    def _on_bus_error(self, _bus, message):
        err, dbg = message.parse_error()
        self._error = f"{err}: {dbg}"
        logger.error("[GST] pipeline error: %s", self._error)

    # -- probes ---------------------------------------------------------------

    def _on_display(self, pad, info):
        buf = info.get_buffer()
        if buf is None:
            return self._Gst.PadProbeReturn.OK
        frame = self._frame_from_buffer(pad, buf)
        if frame is not None:
            with self._lock:
                self._frame = frame
        return self._Gst.PadProbeReturn.OK

    def _on_infer(self, pad, info, branch):
        import hailo                                         # noqa: PLC0415
        buf = info.get_buffer()
        if buf is None:
            return self._Gst.PadProbeReturn.OK
        dets = []
        try:
            roi = hailo.get_roi_from_buffer(buf)
            for det in roi.get_objects_typed(hailo.HAILO_DETECTION):
                score = float(det.get_confidence())
                if score < branch["confidence"]:
                    continue
                label = det.get_label()
                if branch["classes"] and label not in branch["classes"]:
                    continue
                bb = det.get_bbox()
                dets.append({
                    "label": label,
                    "confidence": round(score, 2),
                    "model": branch["model"],
                    "box_norm": [bb.xmin(), bb.ymin(), bb.xmax(), bb.ymax()],
                })
        except Exception as exc:                             # noqa: BLE001
            logger.debug("[GST] roi parse failed: %s", exc)

        now = time.perf_counter()
        with self._lock:
            branch["dets"] = dets
            if branch["frames"] > 0 and now > branch["last"]:
                inst = 1.0 / (now - branch["last"])
                branch["fps"] = inst if branch["fps"] == 0 else \
                    branch["fps"] * 0.9 + inst * 0.1
            branch["last"] = now
            branch["frames"] += 1
        return self._Gst.PadProbeReturn.OK

    def _frame_from_buffer(self, pad, buf):
        caps = pad.get_current_caps()
        if caps is None:
            return None
        st = caps.get_structure(0)
        ok_w, w = st.get_int("width")
        ok_h, h = st.get_int("height")
        if not (ok_w and ok_h):
            return None
        ok, mapinfo = buf.map(self._Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            import numpy as np                               # noqa: PLC0415
            import cv2                                       # noqa: PLC0415
            rgb = np.frombuffer(mapinfo.data, dtype=np.uint8)[: w * h * 3].reshape((h, w, 3))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception:                                    # noqa: BLE001
            return None
        finally:
            buf.unmap(mapinfo)

    # -- consumed by run.py ---------------------------------------------------

    def latest(self):
        """(full-res frame_bgr | None, merged [detections]) -- every branch's
        current detections together, each tagged with its model. box_norm is
        0..1 on the display frame (filter_letterbox already unmapped the
        letterbox), so run.py just scales to pixels."""
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            dets = [d for b in self._branches for d in b["dets"]]
            return frame, dets

    @property
    def infer_fps(self):
        """Measured on-chip inference rate per model branch: {model: fps}."""
        with self._lock:
            return {b["model"]: round(b["fps"]) for b in self._branches}

    @property
    def error(self):
        return self._error

    def close(self):
        try:
            if self._pipeline is not None:
                self._pipeline.set_state(self._Gst.State.NULL)
        except Exception:                                    # noqa: BLE001
            pass
        try:
            if self._loop is not None:
                self._loop.quit()
        except Exception:                                    # noqa: BLE001
            pass
        self._pipeline = self._loop = None
