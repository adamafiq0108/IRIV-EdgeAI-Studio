# -*- coding: utf-8 -*-
"""
snapgate.py -- a Detection Stream that detects only when it is asked to.

A camera whose Detection Stream is set to Snapshot (Workflow, Capture) takes a
snap input. An Inject, a Trigger or a sensor wired into it says WHEN; the
number of frames to detect is set on that source. In between, the detector is
not run on that camera at all -- the chip and the cores are free for the other
cameras -- and the camera's last result is held, so a zone does not read empty
and no "area empty" alarm fires just because nobody asked for a picture.

What the compiler writes (core/services/workflow.py, dc.set_snapshot) is on the
camera entry as `snapshot = {sources: [{kind, key, fn, frames}]}`:

  kind   "inject"   `key` is the Inject's `fired` pulse, one tick per inject
         "trigger"  `key` is the rule's `active` level (a Trigger has no
                    function of its own; it stands for the rule behind it)
         "level"    `key` is a sensor's value: anything non-zero is true
  frames how many frames to detect for one snap (1..600)

THE RULES
  * One snap at a time. An edge that arrives while frames are still being
    taken is ignored, not queued: a second press mid-snap is the same moment.
  * A snap starts on a RISING edge. A Trigger or sensor that is already true
    when the app starts is the baseline, not a snap; and after its frames are
    taken it must be seen false before it can start another.
  * When the frames are taken the source goes back to false by itself, so the
    snap stops and it is ready for the next true: an Inject's value is reset
    (functions.reset_inject), a Trigger's published level is held low until
    its rule really releases (functions.hold_low). A sensor cannot be reset, so
    it simply has to fall and rise again.

This module owns the DECISIONS (is a frame allowed, did a snap just finish) and
nothing else; run.py owns the detector and the function engine, and carries out
the resets this returns. No imports beyond the standard library, so the rules
are testable without a camera, a chip or a loop.
"""

import json

#: Most frames one snap may take. Mirrors workflow.MAX_SNAP_FRAMES and
#: detector_control.MAX_SNAPSHOT_FRAMES; clamped here once more because this is
#: the side that spends the chip.
MAX_FRAMES = 600


def _truthy(value):
    """A published stat as a level: non-zero numbers and non-empty words are
    true; 0, false, none and blank are not."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return str(value).strip().lower() not in ("", "0", "false", "none", "off")


class Gate:
    """One camera's snapshot state."""

    def __init__(self, cfg):
        cfg = cfg if isinstance(cfg, dict) else {}
        self.sig = json.dumps(cfg, sort_keys=True, default=str)
        self.sources = []
        for src in cfg.get("sources") or []:
            if not isinstance(src, dict) or not src.get("key"):
                continue
            try:
                frames = int(src.get("frames") or 1)
            except (TypeError, ValueError):
                frames = 1
            self.sources.append({"kind": str(src.get("kind") or "level"),
                                 "key": str(src["key"]),
                                 "fn": str(src.get("fn") or ""),
                                 "frames": max(1, min(MAX_FRAMES, frames))})
        self.left = 0                   # frames still to detect for this snap
        self.src = None                 # the source that started it
        self.prev = {}                  # key -> level at the last look
        self.wait_low = {}              # key -> must fall before it may start again
        self.finished = None            # the source whose frames were just taken
        self.idle_now = True            # this pass detected nothing here
        self.hold = False               # camera not ready: frames are not spent yet
        self.cam_state = {}             # the camera's sleep/wake bookkeeping (snapcam)
        self.verdict = {}               # stat key -> 0 GOOD / 1 NG, of the last finished snap
        self._hit = {}                  # stat key -> NG seen in the snap being taken
        self._in_burst = False          # fresh frames are being analysed

    # -- the decision: did something just say "snap"? -------------------------

    def update(self, stats):
        """Look at this pass's published numbers. Starts a snap on a rising
        edge, ignoring edges while one is running."""
        for src in self.sources:
            key = src["key"]
            level = _truthy(stats.get(key))
            if src["kind"] == "inject":
                # `fired` is one tick wide: the pulse IS the edge.
                edge = level
            else:
                if key not in self.prev:
                    # The first look is the baseline, not an edge: a level that
                    # is already true when the app starts must not snap.
                    self.prev[key] = level
                    self.wait_low[key] = level
                    continue
                edge = level and not self.prev[key]
                self.prev[key] = level
                if not level:
                    self.wait_low[key] = False
            if edge and self.left == 0 and not self.wait_low.get(key):
                self.left = src["frames"]
                self.src = src

    # -- the decision: may this frame go to the detector? ---------------------

    def take(self, n=1):
        """Spend up to `n` of the frames still owed. Returns how many were
        actually spent. Sets `finished` when the last one is."""
        used = min(max(0, int(n)), self.left)
        self.left -= used
        if used and self.left == 0 and self.src is not None:
            self.finished = self.src
            if self.src["kind"] != "inject":
                # A Trigger's level or a sensor stays as it is until it really
                # falls; it may not start another snap before that.
                self.wait_low[self.src["key"]] = True
        return used

    # -- the decision: GOOD or NG for the snap that was taken -----------------

    def observe(self, fresh, ng):
        """One analysed pass. `fresh` is True when this pass looked at a newly
        snapped frame (False when the camera's last result was held), `ng`
        maps each verdict stat key to whether it read NG on this pass.

        A snap is NG when ANY of its frames found something, and the answer
        is held until the next snap finishes: between snaps the engine is fed
        the last detections again, which must not move it. Nothing is known
        until the first snap has been analysed, so nothing is returned for it.
        Returns {key: 0 or 1} for every key with an answer."""
        if fresh:
            self._in_burst = True
            for key, hit in (ng or {}).items():
                self._hit[key] = bool(self._hit.get(key)) or bool(hit)
        elif self._in_burst and self.left == 0:
            for key in set(self._hit) | set(ng or {}):
                self.verdict[key] = 1 if self._hit.get(key) else 0
            self._hit = {}
            self._in_burst = False
        return dict(self.verdict)

    def pop_finished(self):
        """The source whose snap just ended (once), or None."""
        done, self.finished = self.finished, None
        return done


def sync(pipe):
    """Make `pipe.snap` match the camera's config: a Gate when the camera is
    set to snapshot, None when it is not. Cheap enough to call every pass, and
    a changed config builds a fresh Gate (a half-taken snap is dropped, which is
    what saving the workflow should do)."""
    cfg = (getattr(pipe, "cfg", None) or {}).get("snapshot")
    if not isinstance(cfg, dict) or not cfg.get("sources"):
        if getattr(pipe, "snap", None) is not None:
            pipe.snap = None
        return None
    sig = json.dumps(cfg, sort_keys=True, default=str)
    gate = getattr(pipe, "snap", None)
    if gate is None or gate.sig != sig:
        gate = pipe.snap = Gate(cfg)
        if not gate.sources:
            pipe.snap = None
            return None
    return gate


def filter_frames(frames):
    """[(pipe, frame)] -> ([(pipe, frame or None)], [finished sources]).

    A camera without a gate keeps every frame. A camera with one keeps only as
    many frames as it is still owed and gets None for the rest -- which the
    inference path already answers with no detections and no call to the
    detector. The frames list is one entry per frame a pass collected (a batch
    detector collects several per camera), so a snap of 3 may be taken in one
    pass or across three."""
    allow = {}
    out = []
    finished = []
    for pipe, frame in frames:
        gate = sync(pipe)
        if gate is None:
            out.append((pipe, frame))
            continue
        pid = id(pipe)
        if pid not in allow:
            allow[pid] = gate.left
        if frame is not None and allow[pid] > 0 and not gate.hold:
            allow[pid] -= 1
            gate.take(1)
            out.append((pipe, frame))
        else:
            out.append((pipe, None))
        done = gate.pop_finished()
        if done is not None:
            finished.append(done)
    return out, finished
