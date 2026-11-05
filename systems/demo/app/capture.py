# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
capture.py -- triggered snapshots and short video clips.

The workflow's Snapshot / Record output nodes need a hand on the actual frames,
which outputs.py (GPIO/MQTT/webhook) never has. This module owns that seam:
run.py offers it every annotated frame; a trigger arms it; it saves.

  - snap():  one JPEG into the store's snapshot dir (id cap_<ts>), so the
             Footage page shows it beside event snapshots with no new UI.
  - record(): arms a FORWARD recording -- the next `duration_s` seconds of
             frames become one clip under data/clips/. Capped hard at
             MAX_CLIP_S: a trigger firing every frame must produce bounded
             clips, not an accidental CCTV archive.

Clips are written with cv2.VideoWriter (mp4). No cv2 (bare mock install) =
recording quietly degrades to a snapshot, and says so once in the log.

Both features sit behind the [capture] config switches (System Control
toggles): an operator turns the capability on per system; the workflow decides
WHEN. Everything is best-effort -- capture must never take the loop down.
"""

import collections
import os
import threading
import time

try:
    from . import faults
except ImportError:                     # run as a loose script, not a package
    import faults

log = faults.logger(__name__)

MAX_CLIP_S = 5.0
#: The most frames a second a clip is written at.
#:
#: A clip is evidence for a person to watch, and 12fps is smooth enough to see
#: what happened. The detection loop can run faster than that, and every extra
#: frame costs twice: a full annotated render in the loop, and a software H.264
#: encode of a 920x640 image. Both were being spent per tick, so arming a
#: recording visibly slowed the live feed. Capped here, and the header is
#: written at the same rate, so playback speed is unaffected.
MAX_CLIP_FPS = 12.0
#: How many rendered frames may be waiting for the encoder. Small on purpose:
#: this is the buffer that absorbs an encoder hiccup, not a queue that lets a
#: slow one build a minute of backlog in RAM (each entry is ~1.7 MB).
QUEUE_MAX = 8
#: The most run-up a clip may carry, and so the deepest the rolling buffer
#: ever goes. Same cap as the forward half: a clip is at most 5s + 5s.
MAX_PREROLL_S = 5.0
MAX_CLIPS = 100                       # prune oldest past this, disk is finite

CLIP_DIR = "clips"

#: Captures are for HUMAN review, so they get more pixels than the live
#: pipeline: the working frames are capped at ~640 for the AI's sake
#: (camera.py), and snapshots/clips are scaled back up to fit this box.
CAP_W, CAP_H = 920, 640


class Capture:
    def __init__(self, data_dir, store, fps=10, tag=""):
        self.store = store
        # Which camera these files came from. One system may record several
        # cameras at once, and the clip name is only second-resolution -- two
        # cameras triggered in the same second used to write the SAME path, so
        # one clip silently replaced the other.
        self.tag = "".join(c if c.isalnum() else "_" for c in str(tag or "")).strip("_")[:24]
        self.fps = max(1, int(fps or 10))
        self.dir = os.path.join(data_dir, CLIP_DIR)
        os.makedirs(self.dir, exist_ok=True)
        self._writer = None
        self._record_until = 0.0
        self._clip_path = None
        self._warned_no_cv2 = False
        # THE RUN-UP, and what it costs.
        #
        # A clip that starts at the alarm has already missed how the thing got
        # there, so a Record Clip may ask for seconds BEFORE the trigger. The
        # only way to have them is to have been keeping them: a ring of
        # rendered frames per camera, filled every frame, whether or not
        # anything ever fires.
        #
        # That is real money on a Pi. A capture-size frame (920x640 BGR) is
        # ~1.7 MB, so five seconds at 10fps is ~90 MB of RAM per camera --
        # plus the annotated render itself, which the loop otherwise skips
        # entirely when nothing is recording (run.py checks `wants_frames`).
        # It is off unless an output asks for it: `preroll_s` stays 0, the
        # deque stays empty, and the cost is exactly what it was before.
        self._preroll_s = 0.0
        self._buffer = collections.deque()
        # The id of the object that caused the firing being recorded, if the
        # event named one and the output asked for it. Stamped into the file
        # name so a clip can be found by object later.
        self._oid = ""
        self._page = ""        # the Record Clip node's Footage page, likewise
        # ENCODING RUNS OFF THE DETECTION LOOP.
        #
        # cv2's H.264 writer is a software encoder, and it was being called
        # synchronously from the loop -- so every frame of every clip spent
        # tens of milliseconds of the loop's budget inside libx264. Arming a
        # recording therefore slowed inference, the measured rate fell, and
        # the live feed people were watching went visibly choppy for as long
        # as the clip lasted. Nothing about writing a file needs to happen
        # between two detections.
        #
        # So the loop hands finished frames to this queue and returns. A
        # bounded deque with drop-oldest is deliberate over a blocking queue:
        # if the encoder cannot keep up, the honest outcome is a clip missing
        # a frame here and there, not a detection loop waiting on a disk.
        self._q = collections.deque()
        self._qlock = threading.Condition()
        self._thread = None
        self._writing = False           # a clip is open or opening
        self._dropped = 0
        # The rate frames are TAKEN at -- see MAX_CLIP_FPS.
        self._interval = 1.0 / min(float(self.fps), MAX_CLIP_FPS)
        self._last_taken = 0.0

    # -- triggers (called from the outputs seam) ----------------------------

    @staticmethod
    def _fit(frame):
        """Scale a frame to fit inside CAP_W x CAP_H, aspect preserved."""
        try:
            import cv2                                      # noqa: PLC0415
            h, w = frame.shape[:2]
            scale = min(CAP_W / w, CAP_H / h)
            if abs(scale - 1.0) < 0.01:
                return frame
            interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
            return cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                              interpolation=interp)
        except Exception:                                   # noqa: BLE001
            return frame

    def _rescale_jpeg(self, jpeg):
        """Re-encode a JPEG at capture size; the original on any failure."""
        try:
            import cv2                                      # noqa: PLC0415
            import numpy as np                              # noqa: PLC0415
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return jpeg
            out = self._fit(frame)
            if out is frame:
                return jpeg
            ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            return buf.tobytes() if ok else jpeg
        except Exception:                                   # noqa: BLE001
            return jpeg

    @staticmethod
    def _oid_text(frame, oid):
        """Burn "OID <n>" into the frame's bottom-left corner. Best effort."""
        try:
            import cv2                                      # noqa: PLC0415
            h = frame.shape[0]
            text = f"OID {oid}"
            # Drawn twice: a dark stroke under a light fill, so it stays
            # readable over a bright floor and over a dark one.
            for colour, thick in (((0, 0, 0), 4), ((255, 255, 255), 2)):
                cv2.putText(frame, text, (12, h - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, thick,
                            cv2.LINE_AA)
        except Exception:                                   # noqa: BLE001
            pass
        return frame

    def snap(self, jpeg_bytes, tag="", oid="", kind="", hold=False, page=""):
        """One JPEG into the snapshot store. Returns the snapshot id.

        `kind` marks WHY it was taken in the file name, after the camera's tag
        (cap_<camera>_<kind>_<stamp>), so a page can pick one sort of picture
        out of the store without opening any: "snp" is a frame a Snapshot
        Detection Stream detected (run.py _camsnap). Empty for every other
        capture, whose names are exactly what they always were.

        `oid` is the object that caused this firing, when the event named one
        and the output asked to be told. It goes into the PICTURE (a corner
        stamp) and into the FILENAME, because those answer two different
        questions later: "who is this" while looking at it, and "find me the
        pictures of object 42" without opening any.
        """
        if not jpeg_bytes:
            return None
        oid = "".join(c for c in str(oid or "") if c.isalnum())[:12]
        stamp = int(time.time() * 1000)
        # The Snapshot node's page (its name's short form), so Footage can
        # group a node's photos by file name alone.
        page = "".join(c for c in str(page or "").lower()
                       if c.isascii() and c.isalnum())[:16]
        parts = ["cap"] + ([self.tag] if self.tag else []) \
            + ([f"oid{oid}"] if oid else []) + ([f"pg{page}"] if page else []) \
            + [f"{stamp:x}"]
        kind = "".join(c for c in str(kind or "") if c.isalnum())[:8]
        if kind:
            parts.insert(2 if self.tag else 1, kind)
        sid = "_".join(parts)
        data = self._rescale_jpeg(jpeg_bytes)
        if oid:
            data = self._stamp_jpeg(data, oid)
        if hold:
            self.store.save_snapshot(sid, data, hold=True)   # shown on release
        else:
            self.store.save_snapshot(sid, data)
        log.info("capture: snapshot %s%s", sid, f" ({tag})" if tag else "")
        return sid

    def _stamp_jpeg(self, jpeg, oid):
        """The same corner stamp, applied to an already-encoded JPEG."""
        try:
            import cv2                                      # noqa: PLC0415
            import numpy as np                              # noqa: PLC0415
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return jpeg
            ok, buf = cv2.imencode(".jpg", self._oid_text(frame, oid),
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            return buf.tobytes() if ok else jpeg
        except Exception:                                   # noqa: BLE001
            return jpeg

    def set_fps(self, fps):
        """The rate the CLIP is written at.

        Told, rather than assumed from the camera's configured rate: a clip is
        played back at the rate in its header, so a 44fps header over frames
        that actually arrived at 12 plays a five-second event as a one-second
        blur -- which is exactly what a saved clip looked like. The loop knows
        the measured delivery rate; this is where it says so.
        """
        try:
            rate = float(fps or 0)
        except (TypeError, ValueError):
            return
        if rate >= 1.0:
            # Capped at MAX_CLIP_FPS, and the frames are TAKEN at the same
            # rate below -- the header and the feed have to agree or the clip
            # plays at the wrong speed, which is the bug this method exists
            # for in the first place.
            self.fps = max(1, min(int(MAX_CLIP_FPS), int(round(rate))))
            self._interval = 1.0 / self.fps

    def want_preroll(self, seconds):
        """How much run-up to keep, in seconds. 0 frees the buffer.

        Called as the outputs are (re-)read, so turning the setting off on the
        canvas stops the cost within seconds rather than at the next restart.
        """
        try:
            want = min(MAX_PREROLL_S, max(0.0, float(seconds or 0)))
        except (TypeError, ValueError):
            want = 0.0
        if want == self._preroll_s:
            return
        self._preroll_s = want
        if not want:
            self._buffer.clear()

    def wants_frames(self, now=None):
        """Does this capture need the annotated frame RIGHT NOW?

        True while a clip is open, and true whenever a run-up is being kept --
        the buffer has to be full before the trigger, which is the whole point
        and the whole cost.
        """
        return bool(self._preroll_s) or self.armed(now)

    def due(self, now=None):
        """Is another frame wanted YET?

        Asked before the annotated render, not after: at 25 detections a
        second a 12fps clip wants fewer than half of them, and painting boxes
        onto a 920x640 frame that is about to be thrown away is the more
        expensive half of what recording was costing the loop.
        """
        now = time.time() if now is None else now
        # The 0.9 stops a loop running at almost exactly the clip rate from
        # alternating take/skip and halving the real frame rate.
        return (now - self._last_taken) >= self._interval * 0.9

    def record(self, after_s=MAX_CLIP_S, tag="", before_s=0.0, oid="", page=""):
        """Arm (or extend) a recording: `before_s` of run-up plus `after_s`
        forward. The forward half is clamped to MAX_CLIP_S from NOW, so
        overlapping triggers stretch a clip but never past the cap.

        The run-up is whatever the buffer actually holds -- asking for five
        seconds two seconds after the app started gets two.
        """
        now = time.time()
        until = now + min(MAX_CLIP_S, max(0.5, float(after_s or MAX_CLIP_S)))
        if until > self._record_until:
            self._record_until = until
        if not self._writing:
            # `_writing` rather than `self._writer`: the writer is opened by
            # the encoder thread when the first frame reaches it, so it is
            # still None for a moment after a clip has been armed -- and
            # testing it here would arm the same clip twice.
            self._writing = True
            self._oid = "".join(c for c in str(oid or "") if c.isalnum())[:12]
            # The Record Clip node's Footage page, in the clip's file name.
            self._page = "".join(c for c in str(page or "").lower()
                                 if c.isascii() and c.isalnum())[:16]
            log.info("capture: recording ~%ss clip%s%s", after_s,
                     f" +{before_s}s run-up" if before_s else "",
                     f" ({tag})" if tag else "")
            self._flush_preroll(before_s, now)

    def _flush_preroll(self, before_s, now):
        """Write the buffered run-up into a fresh writer, oldest first."""
        try:
            want = min(MAX_PREROLL_S, max(0.0, float(before_s or 0)))
        except (TypeError, ValueError):
            want = 0.0
        if not want or not self._buffer:
            return
        floor = now - want
        for ts, frame in list(self._buffer):
            if ts >= floor:
                self._submit(frame, burst=True)

    # -- per-frame (called from the loop) -----------------------------------

    def armed(self, now=None):
        """Is a clip window open right now?

        The caller asks BEFORE painting: a clip carries the boxes but not the
        HUD, so it needs its own render, and rendering one every frame in case
        somebody might record is the whole cost of a feature nobody is using.
        """
        return (time.time() if now is None else now) < self._record_until

    def offer(self, frame, now=None):
        """Feed one (annotated) frame. Fills the run-up buffer, and
        starts/extends/closes the clip writer as the armed window dictates.
        Safe with frame=None."""
        now = time.time() if now is None else now
        if now >= self._record_until:
            if self._writing:
                # A marker rather than a close: frames already queued have to
                # reach the file before it is released, or the last half
                # second of every clip is the half second the encoder was
                # still working on.
                self._writing = False
                self._submit(None)
            if self.due(now):
                self._last_taken = now
                self._remember(frame, now)
            return
        if frame is None or not hasattr(frame, "shape"):
            return
        if not self.due(now):
            return
        self._last_taken = now
        self._submit(frame)
        # Kept while recording too: a second trigger during a clip starts its
        # own run-up from here, and a buffer that stopped filling would hand
        # it the frames from before the FIRST one.
        self._remember(frame, now)

    def _submit(self, frame, burst=False):
        """Hand one frame (or None, meaning "close when you get here") to the
        encoder thread. Never blocks the caller.

        `burst` is the run-up flush: those frames are ALREADY held by the
        preroll buffer, so queueing them costs a pointer each rather than
        another 1.7 MB, and dropping them to a cap meant for live backlog
        would throw away most of the run-up the operator asked for.
        """
        with self._qlock:
            if not burst and len(self._q) >= QUEUE_MAX:
                # Drop the OLDEST frame, never the close marker: losing the
                # marker would leave the clip open for ever. An old frame is
                # the right thing to lose -- by the time the encoder is this
                # far behind, that frame is already stale.
                for i, item in enumerate(self._q):
                    if item is not None:
                        del self._q[i]
                        self._dropped += 1
                        break
            self._q.append(frame)
            self._qlock.notify()
        self._spawn()

    def _spawn(self):
        """Start the encoder thread, once. Daemon: a clip is never a reason to
        hold the app open at shutdown."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._encode_loop,
                                        name="clip-encoder", daemon=True)
        self._thread.start()

    def _encode_loop(self):
        while True:
            with self._qlock:
                while not self._q:
                    # Times out so an idle system's encoder thread goes away
                    # instead of sitting on a condition for ever.
                    self._qlock.wait(2.0)
                    if not self._q:
                        self._thread = None
                        return
                item = self._q.popleft()
            try:
                if item is None:
                    if self._writer is not None:
                        self._close()
                    if self._dropped:
                        log.warning("capture: %d frame(s) dropped - the "
                                    "encoder could not keep up", self._dropped)
                        self._dropped = 0
                else:
                    self._write(item)
            except Exception as exc:                        # noqa: BLE001
                # This thread must not die: it would leave every later clip
                # queued and never written, with nothing on screen to say so.
                log.error("capture: encoder error: %s", exc, exc_info=True)

    def _remember(self, frame, now):
        """Keep this frame for a future clip's run-up, and drop what has aged
        out. A no-op with no run-up asked for -- which is the default, and the
        reason this feature costs nothing until somebody wants it."""
        if not self._preroll_s or frame is None or not hasattr(frame, "shape"):
            return
        try:
            self._buffer.append((now, self._fit(frame).copy()))
        except Exception:                                   # noqa: BLE001
            return
        floor = now - self._preroll_s
        while self._buffer and self._buffer[0][0] < floor:
            self._buffer.popleft()

    def _write(self, frame):
        """One frame into the clip, opening the writer on the first."""
        if frame is None or not hasattr(frame, "shape"):
            return
        try:
            import cv2                                      # noqa: PLC0415
        except ImportError:
            if not self._warned_no_cv2:
                self._warned_no_cv2 = True
                log.warning("capture: cv2 not installed - recording "
                            "downgraded to snapshots")
            self._record_until = 0.0
            return
        try:
            frame = self._fit(frame)                        # capture-size, not AI-size
            if self._writer is None:
                h, w = frame.shape[:2]
                stamp = time.strftime("%Y%m%d_%H%M%S")
                parts = ["clip"] + ([self.tag] if self.tag else []) \
                    + ([f"oid{self._oid}"] if self._oid else []) \
                    + ([f"pg{self._page}"] if self._page else []) + [stamp]
                self._clip_path = os.path.join(self.dir, "_".join(parts) + ".mp4")
                # H.264, not MPEG-4 Part 2.
                #
                # `mp4v` writes an mpeg4 stream that no browser plays: the
                # clip downloaded fine, opened in the player, and sat at 0:00
                # for ever -- a recording that exists and cannot be watched,
                # which is the same as no recording at all. avc1 is H.264 and
                # is what every browser decodes; the fallback stays for a box
                # whose OpenCV was built without it, where an unplayable clip
                # still beats no clip.
                writer = cv2.VideoWriter(
                    self._clip_path, cv2.VideoWriter_fourcc(*"avc1"),
                    self.fps, (w, h))
                if not writer.isOpened():
                    writer.release()
                    log.warning("capture: no H.264 encoder - writing mp4v, "
                                "which most browsers cannot play")
                    writer = cv2.VideoWriter(
                        self._clip_path, cv2.VideoWriter_fourcc(*"mp4v"),
                        self.fps, (w, h))
                self._writer = writer
            self._writer.write(frame)
        except Exception as exc:                            # noqa: BLE001
            log.error("capture: recording failed: %s", exc, exc_info=True)
            self._close()
            self._record_until = 0.0

    def _close(self):
        try:
            self._writer.release()
            log.info("capture: clip saved %s",
                     os.path.basename(self._clip_path))
        except Exception:                                   # noqa: BLE001
            pass
        self._writer = None
        self._clip_path = None
        self._oid = ""
        self._page = ""
        self._prune()

    def _prune(self):
        try:
            clips = sorted(f for f in os.listdir(self.dir) if f.endswith(".mp4"))
            for name in clips[:-MAX_CLIPS]:
                os.remove(os.path.join(self.dir, name))
        except OSError:
            pass
