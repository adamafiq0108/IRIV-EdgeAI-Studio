# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
tracker.py -- persistent object identity across frames, for the whole app.

The zones engine carries a tiny nearest-centroid tracker (zones._Tracker) that
exists only to count line crossings. Dwell time ("how long has THIS person been
in the zone") and travel time ("when did THIS forklift cross line A, and when
line B") need more: a track id that survives across frames, with wall-clock
timestamps attached. That is what lives here.

Deliberately dependency-free (no numpy, no scipy, no ByteTrack): the app must
run on a bare Pi alongside a Hailo accelerator, and at the object counts a
single camera produces (tens, not thousands) plain-Python greedy matching is
both fast enough and debuggable.

Matching, per frame:

1. Greedy IoU on normalized boxes (best overlap first, gate at IOU_MIN). Boxes
   are far more stable than centroids when objects are large or slow.
2. Centroid distance fallback for what IoU left unmatched -- catches fast
   movers whose boxes no longer overlap between frames.
3. Everything still unmatched becomes a new track.

Tracks are confirmed and expired by FRAME COUNT (keep_new_frames /
keep_tracked_frames / keep_lost_frames), matching the Hailo tracker's knob
names the settings dialog exposes. The trade is known: the loop's frame
interval changes as the chip budget is shared, so a frame budget is shorter
wall-clock time under load -- but a NEW track needing keep_new_frames matched
frames before it gets a public id is what keeps one-frame detector ghosts
from minting PIDs, and that guarantee only makes sense in frames.

Known limit, documented not hidden: two objects that cross paths or occlude
each other can swap ids. Downstream consumers guard with sanity caps (e.g. a
travel time above a cap is discarded) rather than pretending ids are perfect.
"""

import time

# The knobs carry the HAILO TRACKER's names and default values, because that
# is the vocabulary the operators tuning them already know. Semantics here:
# the *_thr values are DISTANCE thresholds like Hailo's (higher = more
# permissive; the IoU gate is 1 - thr), and the keep_*_frames values are
# frame counts.

#: Match gate for a CONFIRMED track: accept when (1 - IoU) <= iou_thr.
IOU_THR = 0.9

#: Match gate while a track is still NEW (unconfirmed): stricter on purpose --
#: a ghost must earn its identity before it is allowed to coast.
INIT_IOU_THR = 0.7

#: Centre-distance fallback gate for fast movers whose boxes no longer
#: overlap: allowed distance = kalman_dist_thr * KALMAN_BASE of frame width.
KALMAN_DIST_THR = 0.8
KALMAN_BASE = 0.15                      # 0.8 * 0.15 = the old 0.12 default

#: A new track must be matched on this many frames before it is CONFIRMED and
#: gets a public id. One-frame detector ghosts therefore never mint a PID --
#: which is what inflated every by-PID count.
KEEP_NEW_FRAMES = 2

#: Missed frames an UNCONFIRMED track survives while it is still proving
#: itself. Zero looks safer and is not: a detector misses a real person for a
#: frame all the time -- motion blur, a moment behind something, confidence
#: dipping under the threshold -- and with no grace at all the track is
#: destroyed and rebuilt with a fresh id every time it happens. A person
#: standing still then never confirms at all, so nothing is tracked AND the id
#: counter climbs on every blink, which is the worst of both.
#:
#: One frame is deliberate rather than generous. A track still has to be
#: matched KEEP_NEW_FRAMES times to earn a public id, so a one-frame ghost
#: gains only one extra frame of memory and still mints nothing.
KEEP_NEW_GRACE = 1

#: A confirmed track survives this many unseen frames...
KEEP_TRACKED_FRAMES = 15
#: ...plus this many more in the LOST state before it is gone for good.
KEEP_LOST_FRAMES = 2

#: Hard bound on live tracks -- a detector gone haywire (or a mock flood) must
#: not grow memory without limit.
MAX_TRACKS = 256


def _iou(a, b):
    """Intersection-over-union of two [x1, y1, x2, y2] boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


class Track:
    """One tracked object. Plain state, no behaviour -- consumers read it."""

    __slots__ = ("id", "box", "label", "first_seen", "last_seen",
                 "sides", "zone_entered", "line_times", "hits", "misses")

    def __init__(self, tid, box, label, now):
        self.id = tid
        self.box = box                  # normalized [x1, y1, x2, y2]
        self.label = label
        self.first_seen = now
        self.last_seen = now
        self.sides = {}                 # zone_id -> last side of line (+1/-1)
        self.zone_entered = {}          # zone_id -> entry wall-clock ts (dwell)
        self.line_times = {}            # line_id -> last crossing wall-clock ts
        self.hits = 1                   # frames matched, drives confirmation
        self.misses = 0                 # consecutive unseen frames


class Tracker:
    """Attach a persistent `track_id` (and `age_s`) to each detection dict.

    Usage, once per frame, right after the detector:

        tracker.update(detections, frame_w, frame_h)

    Mutates the dicts in place (the same convention ZoneEngine uses for
    det["violation"]) and returns the live {track_id: Track} map so engines
    can read per-track state (entry timestamps, line sides).
    """

    def __init__(self, iou_thr=IOU_THR, init_iou_thr=INIT_IOU_THR,
                 kalman_dist_thr=KALMAN_DIST_THR,
                 keep_new_frames=KEEP_NEW_FRAMES,
                 keep_tracked_frames=KEEP_TRACKED_FRAMES,
                 keep_lost_frames=KEEP_LOST_FRAMES,
                 keep_new_grace=KEEP_NEW_GRACE):
        # Hailo-style DISTANCE thresholds -> internal IoU gates.
        self.iou_min = max(0.02, 1.0 - float(iou_thr))
        self.init_iou_min = max(0.02, 1.0 - float(init_iou_thr))
        self.radius = float(kalman_dist_thr) * KALMAN_BASE
        self.keep_new = max(1, int(keep_new_frames))
        self.keep_new_grace = max(0, int(keep_new_grace))
        self.keep_frames = max(1, int(keep_tracked_frames)) \
            + max(0, int(keep_lost_frames))
        self.tracks = {}                # id -> Track
        self._next_id = 1

    def retune(self, **kwargs):
        """Apply new thresholds IN PLACE: same maths as construction, but the
        live tracks (and every dwell/travel/cycle timer keyed on their ids)
        survive an operator nudging a slider mid-run."""
        tracks, next_id = self.tracks, self._next_id
        self.__init__(**kwargs)
        self.tracks, self._next_id = tracks, next_id

    def _gate(self, track):
        """The IoU a match with this track needs: confirmed tracks may coast
        on the loose gate, a still-new one must keep matching tightly."""
        return self.iou_min if track.hits >= self.keep_new else self.init_iou_min

    def update(self, detections, frame_w, frame_h, now=None):
        now = time.time() if now is None else now
        dets = [d for d in (detections or ())
                if d.get("box") and len(d["box"]) == 4]
        if not frame_w or not frame_h:
            return self.tracks

        # Normalize boxes once; matching in 0..1 space survives a resolution
        # change mid-run (the camera hot-reload path changes frame size live).
        w, h = float(frame_w), float(frame_h)
        boxes = [[d["box"][0] / w, d["box"][1] / h,
                  d["box"][2] / w, d["box"][3] / h] for d in dets]

        unmatched_dets = set(range(len(dets)))
        unmatched_tracks = set(self.tracks)
        assigned = {}                   # det index -> track id

        # Pass 1: greedy IoU, same-label preferred. Score every candidate pair
        # then take best-first -- keeps a close pair from being stolen.
        pairs = []
        for tid in unmatched_tracks:
            tr = self.tracks[tid]
            for i in unmatched_dets:
                iou = _iou(tr.box, boxes[i])
                if iou >= self._gate(tr):
                    # A label mismatch is usually the model flickering between
                    # classes; allow it, but prefer the same-label candidate.
                    bonus = 0.1 if dets[i].get("label", "") == tr.label else 0.0
                    pairs.append((-(iou + bonus), tid, i))
        pairs.sort()
        for _, tid, i in pairs:
            if tid not in unmatched_tracks or i not in unmatched_dets:
                continue
            unmatched_tracks.discard(tid)
            unmatched_dets.discard(i)
            assigned[i] = tid

        # Pass 2: centroid fallback for fast movers.
        if unmatched_tracks and unmatched_dets:
            pairs = []
            for tid in unmatched_tracks:
                cx, cy = _center(self.tracks[tid].box)
                for i in unmatched_dets:
                    dx, dy = _center(boxes[i])
                    d = ((cx - dx) ** 2 + (cy - dy) ** 2) ** 0.5
                    if d <= self.radius:
                        pairs.append((d, tid, i))
            pairs.sort()
            for _, tid, i in pairs:
                if tid not in unmatched_tracks or i not in unmatched_dets:
                    continue
                unmatched_tracks.discard(tid)
                unmatched_dets.discard(i)
                assigned[i] = tid

        # Update matched tracks.
        for i, tid in assigned.items():
            tr = self.tracks[tid]
            tr.box = boxes[i]
            tr.label = dets[i].get("label", tr.label)
            tr.last_seen = now
            tr.hits += 1
            tr.misses = 0

        # New tracks for the rest (bounded).
        for i in unmatched_dets:
            if len(self.tracks) >= MAX_TRACKS:
                break
            tid = self._next_id
            self._next_id += 1
            self.tracks[tid] = Track(tid, boxes[i], dets[i].get("label", ""), now)
            assigned[i] = tid

        # Age the unmatched. FRAME-count expiry, matching the knob names:
        # a still-NEW track gets a frame of grace, a confirmed one survives
        # keep_tracked + keep_lost frames unseen.
        for tid in list(self.tracks):
            tr = self.tracks[tid]
            if tid in unmatched_tracks:
                tr.misses += 1
                # A still-new track exists to prove itself, but "prove it with
                # no misses at all" asks more of the DETECTOR than of the
                # object: a real person is missed for a frame constantly, and
                # killing the track each time rebuilds it under a new id
                # for ever -- so a stationary person both fails to confirm and
                # burns an id per blink. The grace buys the track past one
                # blink; earning an id still takes keep_new real matches.
                budget = (self.keep_new_grace if tr.hits < self.keep_new
                          else self.keep_frames)
                if tr.misses > budget:
                    del self.tracks[tid]

        # Stamp the detections (the loop passes these on to every engine).
        # ONLY CONFIRMED tracks get a public id: a one-frame detector ghost
        # never mints a PID, so nothing built on identity -- unique visitors,
        # violation totals, dwell -- can be inflated by flicker.
        for i, det in enumerate(dets):
            tid = assigned.get(i)
            if tid is not None and tid in self.tracks \
                    and self.tracks[tid].hits >= self.keep_new:
                det["track_id"] = tid
                det["age_s"] = round(now - self.tracks[tid].first_seen, 3)

        return self.tracks
