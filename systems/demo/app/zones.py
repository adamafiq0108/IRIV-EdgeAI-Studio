# -*- coding: utf-8 -*-
"""
zones.py -- named zones, and the counting rules that turn detections into numbers.

Two things live here, and nothing else in the app duplicates them:

1. **Zone geometry, in NORMALIZED (0..1) coordinates.** A zone drawn on a 640x480
   preview must mean the same region when the camera is later reconfigured to
   1280x720, or when the operator draws it on a phone. Storing pixels is the
   classic alignment bug: the rectangle silently shifts the day the resolution
   changes, and nothing in the UI says so. Detections arrive in FRAME pixels
   (the detector's own boxes), so they are converted to
   normalized space here -- once, at the boundary.

2. **The counting rules.** "How many are in frame", "how many are inside this
   zone", "how many crossed the line inward" and "is this a violation" are the
   real domain logic of a vision system, and they must be unit-testable with no
   camera, no model and no accelerator. Everything below is pure arithmetic on
   plain dicts for exactly that reason.

Zone shape (config.toml, or a saved zones.json):

    [[zones]]
    id      = "cell_a"
    name    = "Work cell A"
    kind    = "area"            # "area" | "line"
    points  = [[0.34,0.29],[0.66,0.29],[0.66,0.71],[0.34,0.71]]
    violation = true            # an object inside is a violation, not just a count
    labels  = ["person"]        # empty = any label counts

An "area" is a polygon of >= 3 points. A "line" is exactly 2 points and counts
crossings rather than occupancy -- which is why entry/exit tallies need object
identity, and why the tiny tracker below exists.
"""

import math
import time

#: Where on a box we consider the object to "be". Feet (bottom-centre) is the
#: right anchor for people standing on a floor plane -- using the box centre
#: makes a tall person count as inside a floor zone while they are still
#: outside it, leaning in.
ANCHORS = ("bottom_center", "center")

#: Per-zone containment test ("count when ..."):
#:   feet   - the box's bottom-centre point is inside (floor-plane default)
#:   center - the box's centre point is inside (seated/elevated cameras)
#:   full   - the WHOLE box is inside (strict: partially-in does not count)
#:   touch  - the box overlaps the zone at all (loose: brushing the edge counts)
#: A zone without one falls back to the app-wide anchor, which keeps every
#: existing deployment counting exactly as before.
MATCH_MODES = ("feet", "center", "full", "touch")

#: How a LINE reads the SIDE an object crossed from -- "does this line have a
#: face". A directed line answers "did it come in or go out"; a plain one only
#: answers "did something cross", which is the honest report for a line drawn
#: across a corridor nobody agreed a direction for.
#:
#:   forward - the +side of the drawn direction is IN (the historic behaviour,
#:             and what a line without the field keeps doing)
#:   reverse - the same geometry with in and out swapped, so the operator can
#:             flip the face without redrawing the line backwards
#:   none    - no face: every crossing is one crossing, counted as "cross"
LINE_FACINGS = ("forward", "reverse", "none")

#: How far from a line a point must be before it counts as being on one
#: SIDE of it, in normalized frame units (the points are 0..1, so 0.012 is
#: a little over 1% of the frame width -- about 23px across 1920).
#:
#: A tracked box does not sit still: its centre shivers by a few pixels
#: from frame to frame even when the person it belongs to is standing
#: perfectly still. With the line as an infinitely thin boundary, a person
#: pausing ON it flipped sides with the shiver, and every flip was counted
#: as another crossing -- measured on a live door: one person, three
#: crossings 9 MILLISECONDS apart, and 15 of 27 repeat crossings in a day
#: came within 300ms of the one before. The band gives the line a
#: thickness: inside it an object has no side at all, so it neither counts
#: nor forgets which side it came from, and the crossing is counted when
#: it comes out the far edge.
#:
#: The band is applied as a fraction of the picture WIDTH whatever the
#: line's angle: ZoneEngine scales the y axis by frame_h/frame_w before the
#: side test. In raw normalised units the same 0.012 was 23 px on an
#: upright line and 13 px on a flat one at 1920x1080 -- half the band the
#: constant was tuned for, so a horizontal counting line shivered twice as
#: easily as this comment promised. This is also the DEFAULT a line's own
#: `band_pct` overrides (Zone.band()): the live door camera's shiver is
#: wider than 0.012 and no single number suits every lens.
LINE_DEADBAND = 0.012

#: The range an operator may set a line's band_pct to, in percent of the
#: picture width. Below 0.2 (about 4 px at 1920) the slab is thinner than the
#: measured shiver and the triple-count-in-9-ms fault returns. Above 5.0 a
#: 96 px half-thickness is a 192 px slab: a person who steps through and
#: stops a metre past the line is not counted until they walk on, and the
#: hold frames and the end slack already cover what a wider band would be
#: reached for. The default is LINE_DEADBAND * 100.
LINE_BAND_PCT_MIN = 0.2
LINE_BAND_PCT_MAX = 5.0

#: Consecutive frames the NEW side must hold before a crossing counts. The
#: band handles the shiver of a stationary box; this handles the one-frame
#: excursion of a detection that jumps (a box briefly snapping to a
#: reflection or a second person). Two, not more: a real crossing must not
#: be lost because the walker left the frame straight after it.
LINE_CONFIRM_FRAMES = 2

#: A track is dropped after this many consecutive frames without a match. Short
#: enough that a re-entering object is a NEW crossing, long enough to survive a
#: frame or two of missed detection.
_TRACK_TTL = 12

#: Nearest-centroid matching gate, in normalized units. Beyond this a candidate
#: is a different object, not the same one having moved -- without a gate, two
#: objects passing each other swap identities and invent crossings.
_MATCH_RADIUS = 0.12


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def _slug(label):
    """A label as a stats-key fragment: lowercase, spaces/dashes -> underscore.
    "traffic light" and "traffic-light" both become traffic_light."""
    return "".join(c if c.isalnum() else "_" for c in str(label).strip().lower()).strip("_")


def point_in_polygon(px, py, polygon):
    """Ray-casting point-in-polygon test. Points are (x, y) pairs.

    Counts how many polygon edges a ray to the right of the point crosses; odd
    means inside. Handles concave shapes, which a bounding-box test does not --
    an L-shaped keep-out area is common on a real factory floor.
    """
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and \
                (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def anchor_point(box, frame_w, frame_h, mode="bottom_center"):
    """Normalized (x, y) for a detection box given in FRAME pixels.

    Returns None for a degenerate frame size rather than dividing by zero -- a
    camera that has not produced a frame yet must not take the loop down.
    """
    if not box or len(box) != 4 or not frame_w or not frame_h:
        return None
    x1, y1, x2, y2 = [float(v) for v in box]
    cx = (x1 + x2) / 2.0
    cy = y2 if mode == "bottom_center" else (y1 + y2) / 2.0
    return (cx / float(frame_w), cy / float(frame_h))


def to_pixels(points, frame_w, frame_h):
    """Normalized points -> integer frame pixels, for drawing overlays."""
    return [[int(round(x * frame_w)), int(round(y * frame_h))] for x, y in points]


def norm_rect(box, frame_w, frame_h):
    """A FRAME-pixel box -> normalized (x1, y1, x2, y2), clamped to 0..1.
    None for a degenerate box or frame, same contract as anchor_point()."""
    if not box or len(box) != 4 or not frame_w or not frame_h:
        return None
    x1, y1, x2, y2 = [float(v) for v in box]
    nx1, nx2 = sorted((x1 / frame_w, x2 / frame_w))
    ny1, ny2 = sorted((y1 / frame_h, y2 / frame_h))
    clamp = lambda v: min(1.0, max(0.0, v))                 # noqa: E731
    return (clamp(nx1), clamp(ny1), clamp(nx2), clamp(ny2))


def _segments_intersect(a, b, c, d):
    """Whether segment a-b crosses segment c-d (proper crossing)."""
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])
    return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)


def _rect_touches_polygon(rect, polygon):
    """Whether an axis-aligned rect overlaps a polygon at all: a rect corner
    inside, a polygon vertex inside the rect, or any edge crossing."""
    x1, y1, x2, y2 = rect
    for cx, cy in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
        if point_in_polygon(cx, cy, polygon):
            return True
    for px, py in polygon:
        if x1 <= px <= x2 and y1 <= py <= y2:
            return True
    rect_edges = (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                  ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1)))
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        for c, d in rect_edges:
            if _segments_intersect(a, b, c, d):
                return True
    return False


def _side_of_line(point, a, b, band=0.0, ky=1.0):
    """Which side of the directed line a->b the point falls on: +1, -1 or 0.

    The sign of the 2D cross product. "Inward" is defined as the negative->
    positive transition, so reversing the two stored points reverses in/out --
    that is the operator's control over direction, and it needs no extra field.

    `band` widens the zero: a point closer than that to the line (measured
    perpendicular, in the same normalized units as the points) is ON the
    line rather than either side of it. See LINE_DEADBAND for why a line
    with no thickness counts a standing person over and over.

    `ky` scales every y difference before the arithmetic. Points are
    normalised per axis (x/W, y/H), so on a 16:9 frame one unit of y is
    fewer pixels than one unit of x and a band measured in raw normalised
    units is thinner on a flat line than on an upright one. With ky =
    frame_h/frame_w the geometry is in pixel proportions and |cross|/length
    is a distance in units of frame WIDTH, the same for any angle. The sign
    is unchanged by any ky > 0 (cross scales by ky), so every caller that
    reads only the side -- way_of, the face arrow -- sees what it saw.
    """
    dx = b[0] - a[0]
    dy = (b[1] - a[1]) * ky
    px = point[0] - a[0]
    py = (point[1] - a[1]) * ky
    cross = dx * py - dy * px
    if band > 0:
        # |cross| is the perpendicular distance times the line's length.
        length = math.hypot(dx, dy)
        if length > 0 and abs(cross) < band * length:
            return 0
    if cross > 1e-9:
        return 1
    if cross < -1e-9:
        return -1
    return 0


#: How far past either END of a drawn line a crossing still counts, as a
#: fraction of the line's length. A line is drawn a little short of the
#: doorway it stands for, and a person brushing its end is walking through
#: the door; 10% each side is that, and no more.
LINE_END_SLACK = 0.10


def _crosses_within(p, q, a, b, slack=LINE_END_SLACK):
    """Whether the step p -> q crosses the line a -> b BETWEEN its ends.

    The side test alone (_side_of_line) answers for the INFINITE line through
    a and b: a person passing its extension three metres past either end
    flips sides just the same. That is what made a short line drawn across
    one doorway count everyone who crossed its x anywhere in the frame -- a
    car park's whole width for a line drawn over one bay ("Detection" on the
    developer box: line 0.25 tall, counting from every side).

    So the step is intersected with the line and the hit is located along it:
    t in [0, 1] is between the drawn ends, and `slack` on each side is the
    leniency for a line drawn a little short of what it stands for. A step
    parallel to the line (denominator zero) crossed nothing.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx, sy = q[0] - p[0], q[1] - p[1]
    denom = dx * sy - dy * sx
    if abs(denom) < 1e-12:
        return False
    t = ((p[0] - a[0]) * sy - (p[1] - a[1]) * sx) / denom
    return -slack <= t <= 1.0 + slack


# ---------------------------------------------------------------------------
# zone model
# ---------------------------------------------------------------------------

class Zone:
    """One named region. Validates on construction so a typo in config fails at
    startup with a readable message, not as silently-zero counts at 3am."""

    __slots__ = ("id", "name", "camera", "kind", "points", "violation", "labels",
                 "anchor", "color", "enabled", "match", "facing",
                 # A depth range in millimetres, for a zone drawn over a
                 # depth camera: an object counts only between depth_min_mm
                 # and depth_max_mm from the camera. 0 = no bound. This is
                 # what makes a flat polygon a 3D zone -- the picture gives
                 # two axes, the camera's depth gives the third.
                 "depth_min_mm", "depth_max_mm",
                 # A line's own crossing threshold, percent of the picture
                 # width; None = LINE_DEADBAND. Lives on the LINE, not on the
                 # counter: the side decision runs here before any function
                 # sees a crossing, and one line feeds several counters that
                 # must agree on what a crossing is.
                 "band_pct")

    def __init__(self, spec):
        self.id = str(spec.get("id") or spec.get("name") or "zone").strip()
        self.name = str(spec.get("name") or self.id).strip()
        # Which camera's picture these normalised points were drawn over. A
        # system with several cameras runs one zone engine per camera and gives
        # each only its own zones -- without this the same shape would count on
        # every camera at once, since normalised points fit any frame.
        self.camera = str(spec.get("camera") or "").strip()
        self.kind = (spec.get("kind") or "area").strip().lower()
        self.violation = bool(spec.get("violation", False))
        # Disabled = geometry kept, zone skipped (no overlay, no stats). The
        # filter happens in run.py so as_dict()/config round-trips stay whole.
        self.enabled = bool(spec.get("enabled", True))
        # Containment mode (MATCH_MODES). "" = fall back to the app anchor.
        m = str(spec.get("match") or "").strip().lower()
        self.match = m if m in MATCH_MODES else ""
        # Line face (LINE_FACINGS). Absent = "forward": every line drawn before
        # this field existed counted in/out by its point order, and must keep
        # doing exactly that.
        f = str(spec.get("facing") or "").strip().lower()
        self.facing = f if f in LINE_FACINGS else "forward"
        # Crossing threshold (percent of picture width). Absent = the module
        # constant, so every line drawn before the field existed decides
        # exactly as it did; clamped rather than dropped, because a typed 15
        # means "wider" and turning it into the default would invert the ask.
        self.band_pct = self._clean_pct(spec.get("band_pct"))
        self.labels = {str(l) for l in (spec.get("labels") or ())}
        self.anchor = spec.get("anchor") or "bottom_center"
        self.depth_min_mm = self._clean_mm(spec.get("depth_min_mm"))
        self.depth_max_mm = self._clean_mm(spec.get("depth_max_mm"))
        if self.depth_min_mm and self.depth_max_mm and self.depth_min_mm > self.depth_max_mm:
            self.depth_min_mm, self.depth_max_mm = self.depth_max_mm, self.depth_min_mm
        # Per-zone draw colour (#rrggbb). Falls back at draw time: red for a
        # violation zone, cyan otherwise -- so an operator sees intent even
        # without setting a colour.
        self.color = self._clean_color(spec.get("color"))

        pts = [[float(x), float(y)] for x, y in (spec.get("points") or ())]
        if self.kind == "line":
            if len(pts) != 2:
                raise ValueError(f"zone {self.id!r}: a line needs exactly 2 points, got {len(pts)}")
        elif self.kind == "area":
            if len(pts) < 3:
                raise ValueError(f"zone {self.id!r}: an area needs >= 3 points, got {len(pts)}")
        else:
            raise ValueError(f"zone {self.id!r}: unknown kind {self.kind!r} (area|line)")
        # Out-of-range points are almost always pixels pasted into a normalized
        # field. Say that, instead of counting nothing forever.
        for x, y in pts:
            if not (-0.001 <= x <= 1.001 and -0.001 <= y <= 1.001):
                raise ValueError(
                    f"zone {self.id!r}: point ({x}, {y}) is outside 0..1 - zone "
                    "points are NORMALIZED coordinates, not pixels")
        self.points = pts

    @staticmethod
    def _clean_mm(value):
        try:
            return max(0, min(20000, int(float(value or 0))))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _clean_pct(value):
        """band_pct as stored: None for "no override" (absent, blank, zero,
        negative or not a number -- the same reading depth gives 0), else
        clamped into LINE_BAND_PCT_MIN..MAX and kept to one decimal, which
        is what the Zones page shows and what the hub writes."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if not v > 0:
            return None
        return round(min(LINE_BAND_PCT_MAX, max(LINE_BAND_PCT_MIN, v)), 1)

    def band(self):
        """The half-thickness of this line's no-side slab, as a fraction of
        the picture width -- what ZoneEngine hands _side_of_line. Read at
        frame time, so a saved change applies from the next frame."""
        return self.band_pct / 100.0 if self.band_pct else LINE_DEADBAND

    def in_range(self, depth_mm):
        """Whether an object this far away (mm) is within the zone's depth
        range. No range = always. NO READING (None) = yes: a person whose box
        reads no depth -- a window behind them, a dark coat -- must still trip
        a keep-out zone. The range narrows what counts; it never turns a zone
        with no reading into a zone that sees nobody."""
        if not self.depth_min_mm and not self.depth_max_mm:
            return True
        if depth_mm is None:
            return True
        if self.depth_min_mm and depth_mm < self.depth_min_mm:
            return False
        if self.depth_max_mm and depth_mm > self.depth_max_mm:
            return False
        return True

    def accepts_det(self, det):
        """Label AND depth range, for a whole detection."""
        return (self.accepts(det.get("label", ""))
                and self.in_range(det.get("depth_mm")))

    @staticmethod
    def _clean_color(value):
        value = str(value or "").strip()
        if len(value) == 7 and value[0] == "#":
            try:
                int(value[1:], 16)
                return value
            except ValueError:
                pass
        return ""

    def accepts(self, label):
        """Whether this zone counts a detection with this label."""
        return not self.labels or label in self.labels

    def way_of(self, side):
        """What a crossing onto `side` (+1/-1) is called on this line.

        One place decides it, because the tally, the per-class tally and the
        crossing event must never disagree about which way an object went.
        """
        if self.facing == "none":
            return "cross"
        if self.facing == "reverse":
            return "out" if side > 0 else "in"
        return "in" if side > 0 else "out"

    def contains(self, point):
        """Area zones only; a line contains nothing."""
        return self.kind == "area" and point_in_polygon(point[0], point[1], self.points)

    #: How close "at the line" is, as a fraction of the frame diagonal: a
    #: LINE used where an area is expected (dwell at a line, count at a
    #: line) means "within this band of it".
    LINE_BAND = 0.03

    def _near_line(self, px, py):
        """Distance from (px, py) to the line segment <= LINE_BAND."""
        (ax, ay), (bx, by) = self.points
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        qx, qy = ax + t * dx, ay + t * dy
        return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5 <= self.LINE_BAND

    def hits(self, rect, fallback_anchor="bottom_center"):
        """Whether a normalized detection rect counts as inside this zone,
        honouring the zone's `match` mode. `fallback_anchor` is the app-wide
        anchor, used when the zone sets no mode of its own.

        A LINE hits when the detection's anchor point stands within a narrow
        band of the segment -- which is what lets every area rule (dwell,
        count, countdown, occupancy, the object watch) run AT a line too:
        "inside" reads as "at the line".

        "full" tests all four corners (an approximation for a concave polygon,
        exact for convex ones -- and zones are drawn convex in practice)."""
        if rect is None:
            return False
        if self.kind == "line":
            x1, y1, x2, y2 = rect
            mode = self.match or ("feet" if fallback_anchor == "bottom_center"
                                  else "center")
            if mode == "touch":
                return self.touches(rect)
            cx = (x1 + x2) / 2.0
            cy = y2 if mode != "center" else (y1 + y2) / 2.0
            return self._near_line(cx, cy)
        if self.kind != "area":
            return False
        x1, y1, x2, y2 = rect
        mode = self.match or ("feet" if fallback_anchor == "bottom_center" else "center")
        if mode == "full":
            return all(point_in_polygon(cx, cy, self.points)
                       for cx, cy in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)))
        if mode == "touch":
            return _rect_touches_polygon(rect, self.points)
        cx = (x1 + x2) / 2.0
        cy = y2 if mode == "feet" else (y1 + y2) / 2.0
        return point_in_polygon(cx, cy, self.points)

    def touches(self, rect):
        """Does any part of the BOX reach the line, or its band? The "touch"
        test, whatever match mode the line is drawn with -- for a rule that
        asks "is this object ON the line right now" (the live Line Counter),
        where the line's own crossing mode says how a crossing is decided
        and has nothing to say about how long the object stays over it."""
        if rect is None or self.kind != "line":
            return False
        x1, y1, x2, y2 = rect
        # The whole BOX against the segment: an object counts the
        # moment any part of it reaches the line, which is what
        # "touching" means to the person drawing it -- and it catches
        # a tall object whose feet are still short of the line.
        a, b = self.points
        if (min(a[0], b[0]) <= x2 and max(a[0], b[0]) >= x1
                and min(a[1], b[1]) <= y2 and max(a[1], b[1]) >= y1):
            if (x1 <= a[0] <= x2 and y1 <= a[1] <= y2)                             or (x1 <= b[0] <= x2 and y1 <= b[1] <= y2):
                return True
            edges = (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                     ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1)))
            for c, d in edges:
                if _segments_intersect(a, b, c, d):
                    return True
        # ...still "at the line" when the box merely comes within the
        # band of it, so touch is never STRICTER than the anchor
        # modes on the same line.
        return any(self._near_line(px, py)
                   for px, py in ((x1, y1), (x2, y1), (x2, y2), (x1, y2),
                                  ((x1 + x2) / 2.0, (y1 + y2) / 2.0)))

    def as_dict(self, frame_w=None, frame_h=None):
        """Serializable form. With a frame size, also carries pixel points so a
        UI can draw the overlay without re-implementing the mapping."""
        d = {"id": self.id, "name": self.name, "camera": self.camera,
             "kind": self.kind,
             "points": self.points, "violation": self.violation,
             "labels": sorted(self.labels), "color": self.color,
             "enabled": self.enabled, "match": self.match,
             # Carried for lines so the overlay can draw the face arrow the
             # same way the editor does -- one source for which side is IN.
             "facing": self.facing}
        if self.depth_min_mm or self.depth_max_mm:
            d["depth_min_mm"] = self.depth_min_mm
            d["depth_max_mm"] = self.depth_max_mm
        # Only when set, so an untouched line's dict is byte-identical to
        # what it was; the overlay and the Zones canvas draw the slab from it.
        if self.kind == "line" and self.band_pct is not None:
            d["band_pct"] = self.band_pct
        if frame_w and frame_h:
            d["points_px"] = to_pixels(self.points, frame_w, frame_h)
        return d


def parse_zones(specs):
    """Build Zone objects from config, tolerating the legacy single rectangle.

    Older demo config carried `[detector].zone = [x1,y1,x2,y2]` in PIXELS. That
    form is still accepted by `zone_from_rect()` so an existing deployment keeps
    working, but new zones are normalized and named.
    """
    return [Zone(s) for s in (specs or ())]


def zone_from_rect(rect, frame_w, frame_h, name="Zone"):
    """Legacy bridge: a pixel rectangle -> one normalized area zone.

    Kept so upgrading the package does not silently drop the violation rule a
    deployment already relies on.
    """
    if not rect or len(rect) != 4 or not frame_w or not frame_h:
        return None
    x1, y1, x2, y2 = [float(v) for v in rect]
    nx1, nx2 = sorted((x1 / frame_w, x2 / frame_w))
    ny1, ny2 = sorted((y1 / frame_h, y2 / frame_h))
    return Zone({"id": "zone", "name": name, "kind": "area", "violation": True,
                 "points": [[nx1, ny1], [nx2, ny1], [nx2, ny2], [nx1, ny2]]})


# ---------------------------------------------------------------------------
# tracking (only as much as crossing counts require)
# ---------------------------------------------------------------------------

class _Tracker:
    """Nearest-centroid tracker in normalized space.

    Deliberately minimal. A crossing count needs one thing a per-frame detector
    cannot give: "is this the same object as last frame?". Anything more
    (appearance embeddings, Kalman) belongs in a detector that supplies
    track_id itself.
    """

    def __init__(self):
        self._tracks = {}       # id -> {"point", "missed", "sides"}
        self._next_id = 1

    def update(self, points):
        """Match this frame's points to existing tracks. Returns [(track_id, point)]."""
        unmatched = dict(enumerate(points))
        assigned = {}

        # Greedy nearest match. Sorting by distance first keeps a close pair
        # from being stolen by a far one that happened to be checked earlier.
        pairs = []
        for tid, tr in self._tracks.items():
            for idx, pt in unmatched.items():
                d = math.dist(tr["point"], pt)
                if d <= _MATCH_RADIUS:
                    pairs.append((d, tid, idx))
        pairs.sort()
        used_tracks, used_points = set(), set()
        for _, tid, idx in pairs:
            if tid in used_tracks or idx in used_points:
                continue
            used_tracks.add(tid)
            used_points.add(idx)
            assigned[tid] = unmatched[idx]

        # Age or drop everything that did not match this frame.
        for tid in list(self._tracks):
            if tid in assigned:
                self._tracks[tid]["point"] = assigned[tid]
                self._tracks[tid]["missed"] = 0
            else:
                self._tracks[tid]["missed"] += 1
                if self._tracks[tid]["missed"] > _TRACK_TTL:
                    del self._tracks[tid]

        # Whatever is left is new.
        for idx, pt in unmatched.items():
            if idx in used_points:
                continue
            tid = self._next_id
            self._next_id += 1
            self._tracks[tid] = {"point": pt, "missed": 0, "sides": {}}
            assigned[tid] = pt

        return [(tid, pt) for tid, pt in assigned.items()]

    def sides(self, track_id):
        tr = self._tracks.get(track_id)
        return tr["sides"] if tr else {}


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

class ZoneEngine:
    """Turns per-frame detections into the numbers the dashboard shows.

    Stateful only where it must be: line crossings need the previous side of
    each tracked object. Occupancy and in-frame counts are pure functions of the
    current frame, so a restart never invents or loses one.

    Usage:

        engine = ZoneEngine(zones)
        result = engine.update(detections, frame_w, frame_h)
        result["in_frame"]      -> 3
        result["zones"]["cell_a"]["count"] -> 1
        result["crossings"]["door"]        -> {"in": 4, "out": 2}
        result["violations"]   -> [detection, ...]
    """

    def __init__(self, zones, anchor="bottom_center"):
        self.zones = list(zones or ())
        self.anchor = anchor if anchor in ANCHORS else "bottom_center"
        self._tracker = _Tracker()
        # Line-side memory for EXTERNAL track ids (detections that already
        # carry det["track_id"] from app/tracker.py). Keyed separately from the
        # internal fallback tracker so the two id spaces never collide.
        #   track_id -> {"sides": {zone_id: +1/-1}, "age": frames unseen}
        self._ext = {}
        # Cumulative since start; the adapter publishes these as stats and the
        # master aggregates. Per-frame deltas are in the update() result.
        # "by_label" carries per-class tallies so a counting line can report
        # cars vs forklifts separately.
        # "cross" is the faceless tally: a plain line (facing "none") counts
        # every crossing here and leaves in/out at zero, because it was told
        # not to claim a direction it cannot know.
        self.totals = {z.id: {"in": 0, "out": 0, "cross": 0, "by_label": {}}
                       for z in self.zones if z.kind == "line" and z.enabled}
        # Per-zone entry clocks for AREA zones: zone_id -> {track_id: entered
        # epoch}. What "how long has THIS person been inside" is answered
        # from -- each member detection is stamped with its elapsed seconds
        # (det["zone_in"]), which a zone-scoped Data Table timer column reads.
        self._entered = {}

    def update(self, detections, frame_w, frame_h):
        dets = list(detections or ())
        by_label = {}
        points = []          # (detection, normalized anchor) -- line crossings
        rects = []           # (detection, normalized rect)   -- area tests

        for det in dets:
            label = det.get("label", "")
            by_label[label] = by_label.get(label, 0) + 1
            pt = anchor_point(det.get("box"), frame_w, frame_h, self.anchor)
            if pt is not None:
                points.append((det, pt))
            rect = norm_rect(det.get("box"), frame_w, frame_h)
            if rect is not None:
                rects.append((det, rect))

        result = {
            "in_frame": len(dets),
            "in_frame_by_label": by_label,
            "zones": {},
            "crossings": {},
            "crossing_events": [],
            "violations": [],
        }

        # --- area zones: occupancy, and violations -------------------------
        for zone in self.zones:
            # A disabled zone is carried but silent -- no counts, no
            # violations -- so a workflow Zone Switch can flip it live.
            if not zone.enabled:
                continue
            if zone.kind != "area":
                continue
            inside = [d for d, rect in rects
                      if zone.accepts_det(d) and zone.hits(rect, self.anchor)]
            result["zones"][zone.id] = {
                "name": zone.name, "count": len(inside), "violation": zone.violation,
            }
            # EVERY area zone stamps membership, not only violation zones:
            # a zone-scoped Data Table column ("who is in the zone") has to
            # know, and det["zone"] below is only written for violations.
            # Alongside membership, the PER-PERSON CLOCK: seconds since this
            # track entered, reset the moment it leaves (the clock dict is
            # rebuilt from current members only).
            now_clock = time.time()
            prev = self._entered.get(zone.id) or {}
            clocks = {}
            for det in inside:
                det.setdefault("zones", []).append(zone.id)
                tid = det.get("track_id")
                if tid is not None:
                    clocks[tid] = prev.get(tid, now_clock)
                    det.setdefault("zone_in", {})[zone.id] = \
                        round(now_clock - clocks[tid], 1)
            self._entered[zone.id] = clocks
            if zone.violation:
                for det in inside:
                    # Mark in place so the snapshot/annotator and the event
                    # writer agree on what was a violation, without re-testing.
                    det["violation"] = True
                    det["zone"] = zone.id
                    result["violations"].append(det)

        # --- line zones: crossings ----------------------------------------
        line_zones = [z for z in self.zones if z.kind == "line" and z.enabled]
        if line_zones:
            # Prefer EXTERNAL track ids (app/tracker.py stamps det["track_id"])
            # -- they persist across frames on wall-clock TTL and are shared
            # with dwell/travel functions, so a crossing here and a travel
            # timer there agree on object identity. Fall back to the internal
            # centroid tracker for track-less callers (tests, older loops).
            ext_mode = any("track_id" in d for d, _ in points)
            if ext_mode:
                tracked = []            # (sides_dict, det, pt)
                seen = set()
                for det, pt in points:
                    tid = det.get("track_id")
                    if tid is None:
                        continue
                    ent = self._ext.setdefault(tid, {"sides": {}, "age": 0})
                    ent["age"] = 0
                    seen.add(tid)
                    tracked.append((ent["sides"], det, pt))
                # Age out ids the tracker dropped, so memory stays bounded.
                for tid in list(self._ext):
                    if tid not in seen:
                        self._ext[tid]["age"] += 1
                        if self._ext[tid]["age"] > 300:
                            del self._ext[tid]
            else:
                matched = self._tracker.update([pt for _, pt in points])
                det_for_point = {tuple(pt): d for d, pt in points}
                tracked = [(self._tracker.sides(tid),
                            det_for_point.get(tuple(pt)), pt)
                           for tid, pt in matched]

            # The y axis in units of x, so the band below is the same number
            # of PIXELS on a flat line as on an upright one (see LINE_DEADBAND
            # and _side_of_line's `ky`). anchor_point already returned None
            # for a degenerate frame, so `points` is empty when this is 1.0
            # by fallback and nothing below runs.
            ky = (float(frame_h) / float(frame_w)) if frame_w and frame_h else 1.0
            for zone in line_zones:
                a, b = zone.points
                frame_in = frame_out = frame_cross = 0
                totals = self.totals[zone.id]
                for sides, det, pt in tracked:
                    label = det.get("label", "") if det is not None else ""
                    if det is not None and not zone.accepts_det(det):
                        continue
                    side = _side_of_line(pt, a, b, zone.band(), ky)
                    prev = sides.get(zone.id)
                    # A flip has to HOLD. The deadband above stops the
                    # shiver of a box standing on the line; this stops the
                    # single-frame jump of a detection that briefly lands
                    # somewhere else. Counted on the frame the new side is
                    # confirmed, so a crossing is still reported within a
                    # tenth of a second of happening.
                    held = (zone.id, "held")
                    if side != 0 and prev is not None and side != prev:
                        seen = sides.get(held, 0) + 1
                        sides[held] = seen
                        if seen < LINE_CONFIRM_FRAMES:
                            continue
                    sides.pop(held, None)
                    # Only a genuine sign flip counts. Sitting on the line
                    # (side 0) is not a crossing and must not latch.
                    #
                    # ...and only a flip BETWEEN THE LINE'S ENDS. The side is
                    # a fact about the infinite line; the step from where
                    # this track last stood (on its old side) to here has to
                    # cut the drawn segment, or the person walked round it.
                    # The new side is still remembered either way -- they
                    # ARE on the other side now, and walking back round the
                    # end must not read as a crossing either.
                    at = (zone.id, "pt")
                    was_at = sides.get(at)
                    if prev is not None and side != 0 and prev != 0 and side != prev \
                            and was_at is not None \
                            and not _crosses_within(was_at, pt, a, b):
                        sides[zone.id] = side
                        sides[at] = pt
                        continue
                    if prev is not None and side != 0 and prev != 0 and side != prev:
                        way = zone.way_of(side)
                        if way == "in":
                            frame_in += 1
                        elif way == "out":
                            frame_out += 1
                        else:
                            frame_cross += 1
                        totals[way] += 1
                        per = totals["by_label"].setdefault(
                            label, {"in": 0, "out": 0, "cross": 0})
                        per[way] += 1
                        # Per-crossing record: who crossed which line, which
                        # way. The FunctionEngine turns these into travel
                        # times (line A -> line B per track) and per-class
                        # counters; the plain tallies above stay authoritative
                        # for the dashboard totals.
                        result["crossing_events"].append({
                            "zone": zone.id,
                            "direction": way,
                            "label": label,
                            "track_id": (det or {}).get("track_id"),
                            # Which model saw the crossing (multi-model runs);
                            # lets a function bind to one model's crossings.
                            "model": (det or {}).get("model"),
                        })
                    if side != 0:
                        sides[zone.id] = side
                        # Where the track last stood on a definite side: the
                        # start of the step the next flip is judged by.
                        sides[at] = pt
                result["crossings"][zone.id] = {
                    "name": zone.name,
                    "facing": zone.facing,
                    "in": frame_in, "out": frame_out, "cross": frame_cross,
                    "total_in": totals["in"],
                    "total_out": totals["out"],
                    "total_cross": totals["cross"],
                    "occupancy": totals["in"] - totals["out"],
                    "by_label": {k: dict(v) for k, v in totals["by_label"].items()},
                }

        return result

    def stats(self, result):
        """Flatten one update() result into contract stats keys.

        Flat scalar keys, because that is what a Builder widget can bind to and
        what the master aggregates across devices (docs/contract-v1.md sec 4).
        """
        out = {
            "objects_in_frame": result["in_frame"],
            "violations_now": len(result["violations"]),
        }
        for zid, z in result["zones"].items():
            out[f"zone_{zid}_count"] = z["count"]
        for zid, c in result["crossings"].items():
            out[f"line_{zid}_in"] = c["total_in"]
            out[f"line_{zid}_out"] = c["total_out"]
            out[f"line_{zid}_occupancy"] = c["occupancy"]
            # Published for EVERY line, faced or not: on a plain line it is the
            # only tally there is, and on a faced one it is in + out -- the
            # answer to "how many times was this line crossed at all", which is
            # a fair question to ask of a door that does know its sides.
            out[f"line_{zid}_cross"] = (c.get("total_cross", 0)
                                        + c["total_in"] + c["total_out"])
            # Per-class tallies, e.g. line_gate_car_in -- what a counting line
            # configured for several vehicle classes reports separately.
            for label, per in (c.get("by_label") or {}).items():
                key = _slug(label)
                if key:
                    out[f"line_{zid}_{key}_in"] = per["in"]
                    out[f"line_{zid}_{key}_out"] = per["out"]
                    out[f"line_{zid}_{key}_cross"] = (per.get("cross", 0)
                                                      + per["in"] + per["out"])
        return out
