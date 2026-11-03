# -*- coding: utf-8 -*-
"""
chipshare.py -- the optional fair-share balancer for the box's one AI chip.

Every system on a box is its own app process, and they share one Hailo chip
(through hailort_service on a Hailo-8/8L; one owner on a Hailo-10H). The
runtime's scheduler splits its time per NETWORK -- per process -- not per
camera and not by what each model costs, and run.py's own pacing
(_fps_share) is a fixed table that never looks at the chip at all. So a
four-camera system on yolov8n and a one-camera system on yolov8m get
whatever the scheduler happens to hand them.

This module is the box-wide switch [system] chip_balance (Settings > System,
"Hailo load balancer (testing)"). OFF -- the default -- it changes no cap:
tick() answers None and run.py paces exactly as it always has. ON:

  * every app writes a small heartbeat FILE of its own into one shared
    directory (what it runs, what each camera delivers, what it measured);
  * every app reads ALL of them and computes the same plan from the same
    numbers -- weighted max-min fair shares of the chip's measured capacity,
    counted in chip time -- and applies only its own cameras' part. No
    daemon, no IPC, no lock: the files are the agreement;
  * the caps move slowly (EMA, a dead band, bounded steps, a start-up hold),
    so a busy chip settles instead of see-sawing.

One file per app rather than more keys in the camera registry
(cytron_cameras.json): that registry is one JSON with many writers, and a
heartbeat from one app has already clobbered another's entries there (see
camera.reaffirm). A file only its own app ever writes cannot lose anybody
else's numbers, a crashed app's file simply ages out (STALE_S), and the
camera-ownership registry stays exactly what it was.

Pure where it can be (model_cost, water_fill, plan, smooth take numbers and
return numbers) so tests drive it without a chip; the file I/O is the thin
layer at the bottom. Imports nothing from core/, like the rest of the app.
"""

import json
import os
import tempfile
import threading
import time

try:
    import tomllib as _toml
except ModuleNotFoundError:                                 # pragma: no cover
    import tomli as _toml

# ---------------------------------------------------------------------------
# Knobs. Every one is a module constant so a test (or a box being tuned) can
# change it in one place.
# ---------------------------------------------------------------------------

#: The slowest any camera is paced at. Mirrors run.MIN_CAMERA_FPS (pinned
#: equal by smoke_chipshare): below this the tracker loses identities.
MIN_FPS = 10.0

#: A heartbeat older than this is an app that has gone (crashed, stopped,
#: frozen). Three balance ticks.
STALE_S = 15.0

#: After any app (re)starts or rebuilds its detector, nobody moves a cap for
#: this long: a model load and a hailort_service reset make the chip look
#: saturated for a few seconds, and rebalancing on that is chasing noise.
HOLD_S = 30.0

#: Smoothing of the measured rates across 5 s ticks (new = a*x + (1-a)*old).
EMA_ALPHA = 0.4

#: Dead band: a cap moves only when the new target differs from it by more
#: than this fraction of it, or this many fps, whichever is LARGER.
HYST_PCT = 0.15
HYST_FPS = 2.0

#: Bounded steps per tick. Up is always slow; down is slow too unless the
#: chip is saturated, when a cap goes straight to its target -- a saturated
#: chip is costing every camera on the box, waiting would only spread it.
STEP_UP = 2.0
STEP_DOWN = 2.0

#: Saturation, from BUSY TIME: each app measures the fraction of wall time
#: its infer thread spends inside detect_many (on the chip, or queued for
#: it behind the other apps -- hailort_service's own CPU is not visible to
#: an app, this is). Summed over the box: well under 1 the chip has idle
#: time; at 1 or more somebody is always on it or waiting for it. Enter at
#: BUSY_ENTER, leave below BUSY_EXIT (hysteresis). A learned "unloaded
#: latency" was tried first and dropped: an app that starts on an already
#: busy chip learns the loaded latency as its best and never sees a
#: saturation at all.
BUSY_ENTER = 0.9
BUSY_EXIT = 0.7

#: When caps are set but the chip has idle time, the capacity is probed this
#: much above what it served last tick, so caps that ended up too low climb
#: back (still STEP_UP at a time) instead of ratcheting down forever.
PROBE = 0.2

#: The remembered capacity (the best the chip served while saturated)
#: decays by this per tick when not refreshed, so a box whose load changed
#: forgets an old number in a few minutes (0.98 ** 35 ~ one half).
PEAK_DECAY = 0.98

#: Chip time one frame costs, relative to yolov8s = 1.0. ROUGH on purpose:
#: the allocator needs the ratios in the right neighbourhood, not to the
#: percent. yolov8m is about three times yolov8s on the Hailo-8 (multi-context,
#: see hailo.py's batch notes); n, l and x are scaled from the same family.
#: Matched by PREFIX on the model's file name, longest first, so
#: "yolov8m_coco" is an m. Anything unlisted costs DEFAULT_COST. A system can
#: say exactly what its model costs with [detector] chip_cost = <number>.
COSTS = {
    "yolov8n": 0.5,
    "yolov8s": 1.0,
    "yolov8m": 3.0,
    "yolov8l": 6.0,
    "yolov8x": 9.0,
}
DEFAULT_COST = 1.0

#: Where the heartbeat files live, under the temp dir the camera registry
#: already shares between apps (and that core reads it from).
SHARE_DIRNAME = "cytron_chipshare"

#: The environment variable the hub and every master export (core
#: sysconfig.export_location): the PRODUCT config directory, where
#: system.toml / system.local.toml hold [system] chip_balance. The app is
#: not told that directory any other way. An app started without it reads
#: the switch as off -- today's behaviour.
CONFIG_ENV = "IRIVAI_BOX_CONFIG"
SWITCH_KEY = "chip_balance"

#: Why a camera is where it is, as the Settings card shows it.
REASONS = ("off", "free", "fair", "floor", "demand", "hold", "fallback")


# ---------------------------------------------------------------------------
# Pure parts.
# ---------------------------------------------------------------------------

def model_cost(model, override=None):
    """Relative chip time of one frame of `model` (see COSTS).

    `override` is [detector] chip_cost when a system sets one; a value that
    is not a positive number is ignored rather than trusted."""
    if override is not None:
        try:
            value = float(override)
            if value > 0:
                return min(50.0, max(0.05, value))
        except (TypeError, ValueError):
            pass
    name = os.path.basename(str(model or "")).lower()
    if name.endswith(".hef"):
        name = name[:-4]
    for key in sorted(COSTS, key=len, reverse=True):
        if name.startswith(key):
            return COSTS[key]
    return DEFAULT_COST


def water_fill(cams, capacity, floor=MIN_FPS):
    """Weighted max-min fair shares of `capacity`, in chip time.

    `cams` is [(key, cost, demand_fps)]; returns {key: (fps, reason)}.

    Every camera is offered the SAME chip time per second; it turns that
    into frames at its own cost, so a cheap model gets more frames for the
    same share. A camera that cannot use its share (its camera delivers
    less) takes only its demand and the rest is shared by the others --
    the water level rises until the capacity is used. Nobody goes below the
    floor, even when the floors alone exceed the chip (the floor is a
    promise about tracking, the chip then simply runs late).

    reason: "demand" got everything it can use, "fair" capped at the fair
    share, "floor" held up at MIN_FPS above its share.
    """
    rows = []
    for key, cost, demand in sorted(cams, key=lambda r: str(r[0])):
        c = max(1e-3, float(cost))
        d = max(0.0, float(demand))
        rows.append((key, c, min(floor, d) * c, d * c))
    if not rows:
        return {}
    cap = max(0.0, float(capacity))
    total = lambda level: sum(min(hi, max(lo, level)) for _k, _c, lo, hi in rows)
    if total(0.0) >= cap:
        level = 0.0
    else:
        points = sorted({p for _k, _c, lo, hi in rows for p in (lo, hi)})
        level = points[-1]
        prev = 0.0
        for p in points:
            if total(p) >= cap:
                slope = sum(1 for _k, _c, lo, hi in rows if lo <= prev and hi >= p)
                level = prev + (cap - total(prev)) / slope if slope else p
                break
            prev = p
    out = {}
    for key, c, lo, hi in rows:
        chip = min(hi, max(lo, level))
        if hi <= level or lo >= hi:
            why = "demand"
        elif lo > level and lo < hi:
            why = "floor"
        else:
            why = "fair"
        out[key] = (chip / c, why)
    return out


def _num(value, lo=0.0, hi=1e6):
    value = float(value)
    if value != value or value < lo or value > hi:          # NaN, out of range
        raise ValueError(value)
    return value


def clean_entry(entry):
    """One heartbeat, checked and normalised; ValueError when it is not one.

    Only the fields the plan reads survive -- whatever else a file carries
    is dropped here, never passed on."""
    if not isinstance(entry, dict):
        raise ValueError("not an object")
    cams = []
    for cam in entry.get("cams") or []:
        if not isinstance(cam, dict) or not str(cam.get("id") or ""):
            raise ValueError("bad camera")
        cams.append({"id": str(cam["id"])[:64],
                     "demand": _num(cam.get("demand", 0)),
                     "fps": _num(cam.get("fps", 0))})
    return {"pid": int(entry["pid"]),
            "ts": _num(entry["ts"], 0.0, 1e11),
            "started": _num(entry.get("started", 0), 0.0, 1e11),
            "chip": entry.get("chip") is True,
            "cost": model_cost(None, entry.get("cost", DEFAULT_COST)),
            "busy": _num(entry.get("busy", 0), 0.0, 10.0),
            "saturated": entry.get("mode") == "saturated",
            "capped": entry.get("capped") is True,
            "peak": _num(entry.get("peak", 0)),
            "cams": cams}


def plan(entries, now, me=None, floor=MIN_FPS):
    """The box's plan from one snapshot, or None to fall back to the table.

    Deterministic: the same entries (in any order) give the same plan in
    every app, which is how they agree without talking. Returns

        {"mode": "free" | "probe" | "saturated", "hold": bool,
         "capacity": chip units or None, "used": chip units,
         "targets": {(pid, cam_id): (fps or None, reason)}}

    fps None = no cap from the balancer ("free"). None overall -- fail safe
    -- when the snapshot is missing, holds a fresh entry that is not a
    heartbeat, or does not contain `me` (our own write did not land, so the
    snapshot cannot be the one everybody else sees).
    """
    if entries is None:
        return None
    live = []
    try:
        for raw in entries:
            e = clean_entry(raw)
            if abs(now - e["ts"]) > STALE_S or not e["chip"]:
                continue
            live.append(e)
    except (KeyError, TypeError, ValueError):
        return None
    if me is not None and not any(e["pid"] == me for e in live):
        return None
    if not live:
        return None
    live.sort(key=lambda e: e["pid"])
    hold = any(now - e["started"] < HOLD_S for e in live)
    # Saturated, with hysteresis carried in the snapshot itself: an app
    # that saw "saturated" last tick says so in its mode, and the box stays
    # saturated until the summed busy time falls below BUSY_EXIT.
    busy = sum(e["busy"] for e in live)
    was = any(e["saturated"] for e in live)
    sat = busy >= BUSY_ENTER or (was and busy > BUSY_EXIT)
    capped = any(e["capped"] for e in live)
    cams = [((e["pid"], c["id"]), e["cost"], c["demand"])
            for e in live for c in e["cams"]]
    used = sum(c["fps"] * e["cost"] for e in live for c in e["cams"])
    peak = max(e["peak"] for e in live)
    if sat:
        # The chip served `used`; with idle time left it can do more.
        grow = 1.0 if busy >= BUSY_ENTER else 1.0 + PROBE
        mode, capacity = "saturated", max(used, peak) * grow
    elif capped:
        mode, capacity = "probe", max(used, peak) * (1.0 + PROBE)
    else:
        mode, capacity = "free", None
    if capacity is None:
        targets = {key: (None, "free") for key, _c, _d in cams}
    else:
        targets = water_fill(cams, capacity, floor)
    return {"mode": mode, "hold": hold, "capacity": capacity,
            "used": used, "busy": busy, "targets": targets}


def smooth(prev, target, demand, fast_down, floor=MIN_FPS):
    """Move one camera's cap from `prev` toward `target`, stably.

    prev/target None = no cap. A camera with no cap runs at its demand, so
    that is where a first cap starts from, and where a released one climbs
    back to -- STEP_UP at a time, then the cap is dropped altogether.
    Inside the dead band nothing moves; that is the whole anti-oscillation
    rule for a target that jitters with the measurements.
    """
    release = target is None
    if release and prev is None:
        return None
    base = prev if prev is not None else max(floor, float(demand or 0))
    goal = max(floor, float(demand if release else target) or 0)
    band = max(HYST_FPS, HYST_PCT * base)
    diff = goal - base
    if abs(diff) <= band:
        new = base
    elif diff > 0:
        new = base + min(diff, STEP_UP)
    else:
        new = goal if fast_down else base - min(-diff, STEP_DOWN)
    new = max(floor, new)
    if release and (new >= goal or goal - new <= band):
        return None
    return round(new, 2)


# ---------------------------------------------------------------------------
# One app's side: measurements, its heartbeat, its caps.
# ---------------------------------------------------------------------------

def _ema(old, new):
    return new if old is None else EMA_ALPHA * new + (1 - EMA_ALPHA) * old


class Balancer:
    """Everything one app remembers between balance ticks.

    run.py makes one, feeds it inference timings (note_infer, from the
    infer thread) and calls tick() on the 5 s balance check. tick() answers
    None -- use the fixed rule, exactly as before -- or {camera id: cap or
    None} for this app's cameras.
    """

    def __init__(self, pid=None, now=None, share_dir=None):
        self.pid = int(pid or os.getpid())
        self.started = time.time() if now is None else now
        self.share_dir = share_dir
        self.caps = {}
        self.applied = {}
        self.reasons = {}
        self.fps = {}
        self.demand = {}
        self.infer_ms = None
        self.busy = None
        self.peak = 0.0
        self.mode = "off"
        self.capacity = None
        self._det = None
        self._last = None
        self._lat = [0.0, 0]
        self._lock = threading.Lock()

    def note_infer(self, seconds, frames):
        """One finished detect_many: its wall time and how many frames."""
        if frames > 0 and seconds >= 0:
            with self._lock:
                self._lat[0] += seconds
                self._lat[1] += frames

    def restart(self, now):
        """The detector was (re)built: hold, and forget the old model's
        timings -- a new model has different ones."""
        self.started = now
        self.infer_ms = self.busy = None

    def note_applied(self, targets):
        """The fps run.py actually paced each camera at, for the heartbeat."""
        self.applied = dict(targets)

    def _observe(self, now, cams, det_key):
        if det_key is not None and det_key != self._det:
            if self._det is not None:
                self.restart(now)
            self._det = det_key
        with self._lock:
            spent, frames = self._lat
            self._lat = [0.0, 0]
        if frames:
            self.infer_ms = _ema(self.infer_ms, 1000.0 * spent / frames)
        for cid, demand, fps in cams:
            self.demand[cid] = _ema(self.demand.get(cid), float(demand or 0))
            self.fps[cid] = _ema(self.fps.get(cid), float(fps or 0))
        for gone in set(self.demand) - {c for c, _d, _f in cams}:
            self.demand.pop(gone, None)
            self.fps.pop(gone, None)
        # Busy: the share of the wall time since the last tick that the
        # infer thread spent inside detect_many. A batch straddling the
        # tick lands on the next one; the EMA evens that out.
        span = now - self._last if self._last is not None else 0.0
        self._last = now
        if span > 0:
            frac = min(1.5, spent / span)
            holding = now - self.started < HOLD_S
            self.busy = 0.0 if holding else _ema(self.busy, frac)

    def entry(self, now, slug, model, cost, chip, on):
        """This app's heartbeat. No camera source, no URL, no credential --
        camera ids and numbers only."""
        return {
            "v": 1, "pid": self.pid, "ts": round(now, 3),
            "started": round(self.started, 3),
            "slug": str(slug or "")[:64],
            "model": os.path.basename(str(model or ""))[:64],
            "cost": cost, "chip": bool(chip), "on": bool(on),
            "busy": round(self.busy or 0.0, 3),
            "peak": round(self.peak, 3),
            "capped": any(v is not None for v in self.caps.values()),
            "mode": self.mode,
            "capacity": (round(self.capacity, 2)
                         if self.capacity is not None else None),
            "infer_ms": round(self.infer_ms or 0.0, 2),
            "cams": [{"id": str(cid)[:64],
                      "demand": round(self.demand.get(cid) or 0.0, 2),
                      "fps": round(self.fps.get(cid) or 0.0, 2),
                      "target": round(float(self.applied.get(cid) or 0), 2),
                      "reason": self.reasons.get(cid, "off")}
                     for cid in sorted(self.demand)],
        }

    def tick(self, now, on, cams, slug="", model="", cost=DEFAULT_COST,
             chip=True, det_key=None):
        """One balance check. `cams` is [(camera id, demand fps, measured
        fps)] for this app's cameras. Returns None (the fixed rule) or
        {camera id: cap fps or None}."""
        self._observe(now, cams, det_key)
        active = bool(on and chip)
        wrote = write_beat(self.entry(now, slug, model, cost, chip, active),
                           self.pid, self.share_dir)
        if not active:
            self.caps, self.mode, self.capacity = {}, "off", None
            self.reasons = {cid: "off" for cid, _d, _f in cams}
            return None
        p = plan(read_beats(now, self.share_dir), now, me=self.pid) \
            if wrote else None
        if p is None:
            self.caps, self.mode, self.capacity = {}, "fallback", None
            self.reasons = {cid: "fallback" for cid, _d, _f in cams}
            return None
        self.mode, self.capacity = p["mode"], p["capacity"]
        if p["mode"] == "saturated":
            self.peak = max(self.peak * PEAK_DECAY, p["used"])
        else:
            self.peak *= PEAK_DECAY
        out = {}
        for cid, _demand, _fps in cams:
            target, why = p["targets"].get((self.pid, str(cid)), (None, "free"))
            prev = self.caps.get(cid)
            if p["hold"]:
                out[cid], self.reasons[cid] = prev, "hold"
                continue
            # A camera that cannot use its share needs no cap: capping it
            # at its own demand would only stop it from speeding up, and
            # would keep the box out of "free" for nothing.
            if why == "demand":
                target = None
            out[cid] = smooth(prev, target, self.demand.get(cid) or 0.0,
                              p["mode"] == "saturated")
            self.reasons[cid] = (why if out[cid] is not None or why == "demand"
                                 else "free")
        self.caps = out
        return out


# ---------------------------------------------------------------------------
# The files.
# ---------------------------------------------------------------------------

def share_dir(base=None):
    return base or os.path.join(tempfile.gettempdir(), SHARE_DIRNAME)


def _own_dir(path, create):
    """The share dir, only if it is a real directory this user owns.

    Somebody else's directory (or a symlink planted under the shared temp
    dir) is refused: its files would steer this app's camera rates."""
    try:
        if create and not os.path.lexists(path):
            os.makedirs(path, mode=0o755, exist_ok=True)
        st = os.lstat(path)
    except OSError:
        return False
    import stat                                             # noqa: PLC0415
    if not stat.S_ISDIR(st.st_mode):
        return False
    getuid = getattr(os, "getuid", None)
    return getuid is None or st.st_uid == getuid()


def write_beat(entry, pid, base=None):
    """Write this app's heartbeat (atomic replace, 0644). True when it landed."""
    path = share_dir(base)
    if not _own_dir(path, create=True):
        return False
    final = os.path.join(path, f"{int(pid)}.json")
    tmp = final + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f)
        os.replace(tmp, final)
        return True
    except (OSError, TypeError, ValueError):
        return False


#: How old a file is. One name, so a test replaying hours of ticks in a
#: second can give the files its own clock.
_mtime = os.path.getmtime


def read_beats(now, base=None):
    """Every FRESH heartbeat in the share dir, or None when it cannot be
    read -- or when a fresh file in it is not JSON, which the plan treats
    as an inconsistent snapshot (fail safe). Files older than STALE_S are
    skipped unread, and ones past 20 x that are tidied away."""
    path = share_dir(base)
    if not _own_dir(path, create=False):
        return None
    out = []
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        full = os.path.join(path, name)
        try:
            age = now - _mtime(full)
            if age > STALE_S:
                if age > 20 * STALE_S:
                    os.unlink(full)
                continue
            with open(full, "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
    return out


#: path -> (mtime, value of [system] chip_balance or None). See switch_on.
_switch_cache = {}


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def switch_on(environ=None):
    """Is [system] chip_balance on? Read live (two stats per call; a file is
    parsed only when its mtime moved), local file over committed, exactly
    as core's sysconfig.settings() layers them. Anything unreadable is off."""
    env = os.environ if environ is None else environ
    base = env.get(CONFIG_ENV)
    if not base:
        return False
    value = False
    for name in ("system.toml", "system.local.toml"):
        path = os.path.join(base, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        cached = _switch_cache.get(path)
        if cached is None or cached[0] != mtime:
            try:
                with open(path, "rb") as f:
                    section = (_toml.load(f).get("system") or {})
                found = section.get(SWITCH_KEY) if isinstance(section, dict) else None
            except (OSError, ValueError):
                found = None
            cached = _switch_cache[path] = (mtime, found)
        if cached[1] is not None:
            value = _truthy(cached[1])
    return value
