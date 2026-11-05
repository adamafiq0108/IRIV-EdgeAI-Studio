# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""publisher.py -- push one camera's annotated video to the WebRTC gateway.

The MJPEG path encodes a JPEG per frame in Python and serves it over HTTP.
This is the other half of [app] stream = "webrtc": the frames are handed to
a small GStreamer pipeline that encodes H.264 once and publishes it to
MediaMTX over loopback RTSP, and the browser plays that with <video>.

WHY A PIPELINE AND NOT A LIBRARY CALL. H.264 is a temporal codec: every frame
depends on the ones before it, so the encoder has to be a long-lived object
being fed a continuous stream -- exactly what a GStreamer pipeline is, and
exactly what a per-frame "encode this" function cannot be.

WHERE THE BOXES ARE DRAWN. Two ways in, and both end here:

  * gstreamer detector -- the boxes are drawn INSIDE the detection pipeline
    by hailooverlay (C, on the frame the chip already produced), and this
    module is handed the finished picture. Nothing is drawn in Python.
  * every other detector -- the app annotates with OpenCV as it always has
    and pushes the result. The transport changes; the drawing does not.

Self-contained and OPTIONAL: no GStreamer, no gateway, or a pipeline that
refuses to start leaves `ok` False and an `error` to show, and the app keeps
serving MJPEG. Video must degrade to a worse picture, never to none.
"""

import logging
import shutil
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

#: Bitrate, in bits per second, by frame area. A 640x480 stream that looks
#: fine at 1.5 Mbit/s is a smeared mess at 4K, and a 4K bitrate on a small
#: stream is bandwidth spent on nothing. Interpolated from the area rather
#: than matched to named resolutions, because cameras deliver odd sizes
#: (960x600 is a real mode on hardware this ships with).
_BITRATE_PER_PIXEL = 5.0
_MIN_BITRATE = 600_000
_MAX_BITRATE = 8_000_000

#: How long to wait for the pipeline to reach PLAYING before calling it a
#: failure. Publishing sets up a TCP session with the gateway, so this is
#: not instant; it is also not slow enough to be worth blocking a camera on.
_START_TIMEOUT_S = 6.0


def bitrate_for(width, height, fps):
    """Bits per second for a stream of this size and rate."""
    area = max(1, int(width or 640) * int(height or 480))
    rate = max(1.0, float(fps or 15))
    bits = area * _BITRATE_PER_PIXEL * (rate / 30.0)
    return int(max(_MIN_BITRATE, min(_MAX_BITRATE, bits)))


_ELEMENTS = ("appsrc", "videoconvert", "openh264enc", "h264parse",
             "rtspclientsink")

#: Longest the plugin cache may take to rebuild before the box is treated as
#: unable to publish (MJPEG instead). A rebuild is a few seconds when it works.
_WARM_TIMEOUT_S = 40.0


def warm_registry(timeout_s=_WARM_TIMEOUT_S):
    """Ask a CHILD process whether the publish elements exist, so GStreamer
    rebuilds its plugin cache there and not in this process.

    Gst.init() in this process forks gst-plugin-scanner whenever the cache is
    stale (a boot, a plugin update). By the time this runs, the Hailo gRPC
    client already exists, and forking after it leaves the scanner and this
    process waiting on each other for ever (HailoRT logs "Using the same
    VDeviceClient instance after fork ..." and "epoll_ctl failed: Bad file
    descriptor"): the app never opened its port and the page sat on "Detection
    is busy". A child that has no gRPC state rebuilds the cache, the in-process
    Gst.init() then finds it valid and forks nothing.

    True: every element is there. False: one is missing, or the child did not
    finish in time (killed). None: no gst-inspect-1.0 to ask; the caller carries
    on as before."""
    exe = shutil.which("gst-inspect-1.0")
    if not exe:
        return None
    deadline = time.monotonic() + float(timeout_s)
    for name in _ELEMENTS:
        left = deadline - time.monotonic()
        if left <= 0:
            logger.warning("GStreamer plugin cache did not rebuild in %.0f s; "
                           "not publishing", timeout_s)
            return False
        try:
            r = subprocess.run([exe, "--exists", name], capture_output=True,
                               timeout=left, check=False)
        except subprocess.TimeoutExpired:
            logger.warning("GStreamer plugin cache did not rebuild in %.0f s; "
                           "not publishing", timeout_s)
            return False
        except OSError:
            return None
        if r.returncode != 0:
            return False
    return True


def available():
    """Can this box publish at all? (GStreamer present with the elements.)"""
    if not shutil.which("gst-launch-1.0"):
        return False
    if warm_registry() is False:
        return False
    try:
        import gi                                            # noqa: PLC0415
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst                        # noqa: PLC0415
    except (ImportError, ValueError):
        return False
    Gst.init(None)
    # openh264enc is the encoder this hardware has (a CM5 has no hardware
    # H.264 encoder -- measured, see the deploy notes); rtspclientsink comes
    # from gstreamer1.0-rtsp, which is not installed by default.
    return all(Gst.ElementFactory.find(n) is not None for n in _ELEMENTS)


class Publisher:
    """A live H.264 publish of one camera to the gateway.

    Fed frame by frame with push(); drops frames rather than blocking, so a
    stalled gateway can never slow the detection loop down. Thread-safe.
    """

    def __init__(self, url, width, height, fps, name="", gop=None, bitrate=None):
        self.url = str(url)
        self.width = int(width or 640)
        self.height = int(height or 480)
        # What the ENCODER is told: rows must be a multiple of 4 bytes (BGR is 3
        # per pixel) and I420 needs an even height, or the picture is read from
        # the wrong offsets and comes out solid green. width/height stay the
        # frame's own size, which is what run.py compares against.
        self._ew = (self.width + 3) // 4 * 4
        self._eh = (self.height + 1) // 2 * 2
        self.fps = max(1, int(fps or 15))
        self.name = name or url
        # A Snapshot camera publishes a held picture about once a second: the
        # GOP and the bitrate are then chosen by the caller (every frame a
        # key-frame, a still-picture quality), because a one-second-GOP at the
        # camera's rate would be a key-frame only every ~50 seconds.
        self._gop = int(gop) if gop else None
        self._bitrate = int(bitrate) if bitrate else None
        self.snapshot = bool(gop)
        self.ok = False
        self.error = None
        self.pushed = 0
        self._pipeline = None
        self._src = None
        self._Gst = None
        self._lock = threading.Lock()
        self._t0 = time.time()
        self._start()

    # -- lifecycle ----------------------------------------------------------

    def _describe(self):
        """The publish pipeline, as a parse_launch string.

        appsrc is-live + do-timestamp: the app pushes frames when the camera
        delivers them, at whatever rate that turns out to be, and GStreamer
        stamps arrival time rather than assuming a perfect clock. A camera
        that drops to 12fps then recovers must not accumulate a growing
        offset between its timestamps and reality -- that is what turns a
        live stream into a stream running minutes behind.

        The queue is leaky=downstream and shallow: when the encoder cannot
        keep up, the NEWEST frame is what a live viewer wants, so the old
        ones are dropped here rather than queued into latency.

        openh264enc: complexity=low and a small GOP. This encoder shares four
        cores with inference, so it is tuned for cheap-and-timely over small
        -- and key-frames every second are what let a viewer joining mid-
        stream see a picture immediately instead of waiting for the next one.
        """
        gop = self._gop or max(1, int(self.fps))
        bitrate = self._bitrate or bitrate_for(self.width, self.height, self.fps)
        return (
            f"appsrc name=src is-live=true do-timestamp=true format=time "
            f"block=false max-bytes=4000000 "
            f"caps=video/x-raw,format=BGR,width={self._ew},"
            f"height={self._eh},framerate={self.fps}/1 "
            f"! queue leaky=downstream max-size-buffers=3 "
            f"! videoconvert "
            f"! video/x-raw,format=I420 "
            f"! openh264enc name=enc "
            f"bitrate={bitrate} "
            f"complexity=low gop-size={gop} "
            f"! h264parse config-interval=1 "
            f"! rtspclientsink name=sink location={self.url} "
            f"protocols=tcp latency=0 timeout=5"
        )

    def _start(self):
        try:
            import gi                                        # noqa: PLC0415
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst                    # noqa: PLC0415
        except (ImportError, ValueError) as e:
            self.error = f"GStreamer python bindings unavailable ({e})"
            return
        Gst.init(None)
        self._Gst = Gst
        desc = self._describe()
        logger.info("[PUB] %s: %s", self.name, desc)
        try:
            self._pipeline = Gst.parse_launch(desc)
        except Exception as e:                               # noqa: BLE001
            self.error = f"cannot build publish pipeline: {e}"
            return
        self._src = self._pipeline.get_by_name("src")
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::eos", self._on_eos)
        self._pipeline.set_state(Gst.State.PLAYING)
        # Wait for PLAYING rather than assuming it: the gateway may be down,
        # and a publisher that silently never connects looks exactly like a
        # camera with no picture. ASYNC means "still negotiating", which for
        # an RTSP publish is normal for a beat.
        state = self._pipeline.get_state(int(_START_TIMEOUT_S * Gst.SECOND))
        if state[0] == Gst.StateChangeReturn.FAILURE:
            self.error = self.error or f"publish to {self.url} failed to start"
            self.close()
            return
        self.ok = True
        logger.info("[PUB] %s: publishing to %s", self.name, self.url)

    def _on_error(self, _bus, message):
        err, dbg = message.parse_error()
        self.error = f"{err}"
        self.ok = False
        logger.error("[PUB] %s: %s (%s)", self.name, err, dbg)

    def _on_eos(self, _bus, _message):
        self.ok = False
        self.error = "publish ended"

    # -- feeding ------------------------------------------------------------

    def push(self, frame):
        """Publish one annotated BGR frame. False if the stream is not up.

        Never raises and never blocks: this runs on the detection loop, and
        a publish problem must cost a frame, not the pipeline.
        """
        if not self.ok or self._src is None or frame is None:
            return False
        try:
            h, w = frame.shape[:2]
        except Exception:                                    # noqa: BLE001
            return False
        if w != self.width or h != self.height:
            # The negotiated caps are fixed for the life of the pipeline, so
            # a camera that changes size (a reconnect landing on a different
            # mode) needs a new publisher rather than a reinterpreted buffer.
            self.error = (f"frame is {w}x{h}, stream negotiated "
                          f"{self.width}x{self.height}")
            self.ok = False
            return False
        if (w, h) != (self._ew, self._eh):
            # Pad to the size the encoder was told, repeating the edge so
            # there is no black bar.
            try:
                import cv2                                   # noqa: PLC0415
                frame = cv2.copyMakeBorder(frame, 0, self._eh - h, 0,
                                           self._ew - w, cv2.BORDER_REPLICATE)
            except Exception as e:                           # noqa: BLE001
                self.error = f"cannot pad frame: {e}"
                self.ok = False
                return False
        try:
            data = frame.tobytes()
            buf = self._Gst.Buffer.new_wrapped(data)
            with self._lock:
                res = self._src.emit("push-buffer", buf)
            if res != self._Gst.FlowReturn.OK:
                # FLUSHING is what a pipeline being torn down returns; it is
                # not worth an error line per frame.
                if res != self._Gst.FlowReturn.FLUSHING:
                    self.error = f"push returned {res}"
                self.ok = False
                return False
        except Exception as e:                               # noqa: BLE001
            self.error = f"push failed: {e}"
            self.ok = False
            return False
        self.pushed += 1
        return True

    def force_keyframe(self):
        """Ask the encoder for a key-frame on the next frame it encodes.

        Called when a paused publish wakes ([video] on_demand, run.py
        _publish_loop): the last key-frame went out a GOP of keepalive frames
        ago, and a viewer that just joined can decode nothing before the next
        one. The upstream force-key-unit event is the standard GstVideoEncoder
        request. Best-effort -- False if it could not be sent, and then the
        GOP (one second at full rate) delivers one anyway.
        """
        if not self.ok or self._pipeline is None or self._Gst is None:
            return False
        try:
            import gi                                        # noqa: PLC0415
            gi.require_version("GstVideo", "1.0")
            from gi.repository import GstVideo               # noqa: PLC0415
            enc = self._pipeline.get_by_name("enc")
            pad = enc.get_static_pad("src") if enc is not None else None
            if pad is None:
                return False
            ev = GstVideo.video_event_new_upstream_force_key_unit(
                self._Gst.CLOCK_TIME_NONE, True, 0)
            return bool(pad.send_event(ev))
        except Exception as e:                               # noqa: BLE001
            logger.debug("[PUB] %s: key-frame request failed: %s", self.name, e)
            return False

    def close(self):
        pipeline, self._pipeline = self._pipeline, None
        self._src = None
        self.ok = False
        if pipeline is not None and self._Gst is not None:
            try:
                pipeline.set_state(self._Gst.State.NULL)
            except Exception:                                # noqa: BLE001
                pass


def ensure_gateway(timeout=2.0):
    """Is the local gateway accepting publishes? Cheap TCP probe.

    Checked before building a pipeline: rtspclientsink against a dead
    gateway spends the whole start timeout failing, once per camera, on
    every retry -- and the answer was knowable in a millisecond.
    """
    import socket                                            # noqa: PLC0415
    try:
        with socket.create_connection(("127.0.0.1", 8554), timeout=timeout):
            return True
    except OSError:
        return False
