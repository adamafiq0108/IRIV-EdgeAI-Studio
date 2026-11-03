# -*- coding: utf-8 -*-
"""
dataset_capture.py -- named snapshots and short clips for TRAINING DATA, saved
to the NVMe drive (/mnt/nvme/dataset_image, /mnt/nvme/dataset_rec). Only
offered when the NVMe is actually mounted and writable: dataset recording
fills space fast, and the SD card is not the place for it.

Moved out of detector_control.py whole when that file outgrew its line
ceiling; the clip recorder went on to dataset_record.py. detector_control
re-exports every name, so the routes keep calling detector_control.dataset_*.
DATASET_ROOT is read HERE: whatever must point capture at another folder sets
dataset_capture.DATASET_ROOT, not the re-exported copy.

stream_port is fetched from detector_control at call time, because
detector_control imports this module and a top-level import back would be
circular.
"""

import json
import os
import re
import threading
import time

DATASET_ROOT = os.environ.get("CYTRON_NVME", "/mnt/nvme")
DATASET_IMG_DIR = "dataset_image"
DATASET_REC_DIR = "dataset_rec"
MAX_DATASET_CLIP_S = 10.0

#: The ceiling on a START/STOP recording. Not a feature limit -- the operator
#: says when to stop -- but a backstop: a session left running because someone
#: walked away would fill the NVMe, and a capped clip that exists beats a full
#: disk that stopped everything else on the box. Half an hour of 10fps video is
#: already a large training set.
MAX_DATASET_SESSION_S = 1800.0

#: slug -> {"until", "file", "error", "done", "stop", "manual",
#:          "started_at", "frames"}. `stop` is what a manual recording watches:
#: the writer thread checks it every frame, so Stop closes the clip within one
#: frame rather than at some deadline.
_dataset_jobs = {}


def dataset_state(repair=True, force=False):
    """Can dataset capture write? ``{"ok": bool, "reason": str, "detail": str}``.

    Three failures, and they need three different answers -- the page used to
    give one ("no NVMe drive mounted"), which was simply WRONG for the common
    case and sent somebody off to mount a drive that was already there with
    111 GB free on it.

    The writability probe now happens INSIDE the folder this feature uses
    rather than at the mount root. A stock mount is root-owned and 0755, so a
    service running as `cytron` cannot create anything at the top of it --
    which is exactly what the probe was testing, and exactly what dataset
    capture does not need. What it needs is to write into dataset_image/ and
    dataset_rec/, and those can be created once by setup_nvme.sh and handed to
    the service user.
    """
    root = DATASET_ROOT
    if not os.path.isdir(root):
        return {"ok": False, "reason": "unmounted",
                "detail": f"nothing is mounted at {root}"}
    if os.name != "nt" and not os.path.ismount(root):
        return {"ok": False, "reason": "unmounted",
                "detail": (f"{root} exists but is a plain folder on the boot "
                           "card, not a mounted drive")}
    for sub in (DATASET_IMG_DIR, DATASET_REC_DIR):
        folder = os.path.join(root, sub)
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".cytron_probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as exc:
            err = {"ok": False, "reason": "readonly",
                   "detail": (f"the drive is mounted, but {folder} cannot be "
                              f"written by this service ({exc.strerror or exc})")}
            if not repair:
                return err
            # Mounted, not writable: the box fixes its own folders through
            # the root helper and looks again (storage_job.after_repair);
            # the card is only for a repair that genuinely failed.
            from core.services import storage_job                # noqa: PLC0415
            return storage_job.after_repair(root, err, dataset_state, force)
    return {"ok": True, "reason": "", "detail": ""}


def dataset_available():
    """True when dataset capture can actually write. See dataset_state()."""
    return bool(dataset_state()["ok"])


def _safe_name(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "").strip())[:60].strip("_")
    return s or "capture"


def dataset_dir(kind, slug):
    """The dataset folder for one system: <nvme>/dataset_image/<slug>/.

    Per system, not one shared pile. Several systems run on the same board, and
    training data from the loading-bay camera mixed in with the entrance one is
    not a dataset -- it is a sorting job for whoever comes to use it. The
    system's own name is the only label that survives being copied off the box.
    """
    sub = {"image": DATASET_IMG_DIR, "clip": DATASET_REC_DIR}[kind]
    return os.path.join(DATASET_ROOT, sub, _safe_name(slug))


# ---- which camera ----------------------------------------------------------
#
# A system with several cameras records from the one the operator picks. The
# bare /raw path is the app's FIRST running camera, which is what every
# capture used before the choice existed and what a request naming no camera
# still gets -- a single-camera system, or an old page, behaves exactly as it
# did. A NAMED camera goes to /raw?camera=<id>, and nothing on that path may
# fall back to another camera: the rule_camera bug (1594944d) was a request for
# camera 2 quietly answered from camera 1, and a training set with the wrong
# camera's pictures in it is worse than an error.

#: What the page shows when the chosen camera cannot give a frame. One
#: wording for every cause (switched off, AI off, faulted, gone from the app)
#: because the fix is the same: get that camera running, or pick another.
NOT_RUNNING = "This camera is not running"

#: Which camera took each file, per folder: {filename: {"camera", "camera_name"}}.
#: A side file rather than a renamed capture, so names stay what they always
#: were and a folder written before this existed simply has no tags. Dotted,
#: so it is never listed (the listing filters on .jpg/.mp4) and never served,
#: zipped or deleted as a capture (dataset_path refuses dotted names).
CAMERA_INDEX = ".cameras.json"

_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_index_lock = threading.Lock()


class CameraNotRunning(ValueError):
    """The chosen camera exists but has no frames to give right now."""


def _display_name(cam, index):
    """The name the page shows: the operator's, never an address with a
    password in it (clean_label drops user:pw@), else "Camera N" as the rest
    of the app numbers them."""
    from core.services import detector_control as dc        # noqa: PLC0415
    name = dc.clean_label(cam.get("name") or cam.get("label") or "")
    return name or f"Camera {index + 1}"


def dataset_cameras(systems_dir, slug):
    """[{"id", "name", "enabled"}] for every camera of this system, in the
    system's own order. Ids and names only: a source (and its password) never
    leaves here."""
    from core.services import detector_control as dc        # noqa: PLC0415
    out = []
    for i, cam in enumerate(dc.cameras(systems_dir, slug) or []):
        out.append({"id": str(cam.get("id") or ""), "name": _display_name(cam, i),
                    "enabled": bool(cam.get("enabled", True))})
    return [c for c in out if c["id"]]


def default_camera(cams):
    """The camera a request naming none is served from: the app's first
    RUNNING camera, which is the first enabled one in config order."""
    for c in cams:
        if c["enabled"]:
            return c["id"]
    return cams[0]["id"] if cams else ""


def saved_camera(systems_dir, slug, cams=None):
    """The camera this system's Dataset page last captured from, when it is
    still one of the system's cameras; else the default."""
    from core.services import detector_control as dc        # noqa: PLC0415
    cams = dataset_cameras(systems_dir, slug) if cams is None else cams
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    want = str(((cfg.get("dataset") or {}).get("camera") or "")).strip()
    return want if any(c["id"] == want for c in cams) else default_camera(cams)


def remember_camera(systems_dir, slug, camera_id):
    """Keep the choice in config.local.toml [dataset] camera, beside the
    database's own label ([database] name). Written only when it changes:
    the app watches that file, and a photo should not cost it a reload."""
    from core.services import detector_control as dc        # noqa: PLC0415
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    try:
        # Looked at BEFORE edit_local: leaving its block early still writes.
        have = dc._read_toml(dc._local_path(pkg_dir)).get("dataset") or {}
        if isinstance(have, dict) and have.get("camera") == camera_id:
            return
        with dc.edit_local(pkg_dir) as local:
            block = dict(local.get("dataset") or {})
            block["camera"] = camera_id
            local["dataset"] = block
    except (OSError, ValueError, TypeError):
        pass                    # the capture worked; forgetting is harmless


def resolve_camera(systems_dir, slug, camera):
    """The camera a capture request named, checked against THIS system.

    Returns None when the request named none (the bare /raw path, unchanged
    behaviour), else {"id", "name"}. An id the system does not have is a
    ValueError (a 400); one it has that is not producing is CameraNotRunning
    (also a 400, with NOT_RUNNING as the words). Never a path or a URL:
    anything that is not a plain camera id is refused before config is read.
    """
    if camera is None or camera == "":
        return None
    if not isinstance(camera, str) or not _ID_RE.match(camera.strip()):
        raise ValueError("camera must be one of this system's camera ids")
    cid = camera.strip()
    cams = dataset_cameras(systems_dir, slug)
    found = next((c for c in cams if c["id"] == cid), None)
    if found is None:
        raise ValueError(f"no camera {cid!r} on this system")
    if not found["enabled"]:
        raise CameraNotRunning(NOT_RUNNING)
    from core.services import detector_control as dc        # noqa: PLC0415
    try:
        st = dc.status(systems_dir, slug)
    except Exception:                                       # noqa: BLE001
        st = {}
    if not st.get("running"):
        raise CameraNotRunning(NOT_RUNNING)
    live_all = st.get("cameras") or []
    live = next((c for c in live_all if str(c.get("id")) == cid), None) or {}
    # The app reports each camera it runs (measured_fps is the tell). One that
    # reports cameras but not this one is not running it; one with a fault on
    # it has nothing honest to give. An app too old to report per camera is
    # left to answer for itself: /raw?camera= 404s a camera it is not running.
    reports = any("measured_fps" in c for c in live_all)
    if (reports and "measured_fps" not in live) or live.get("error"):
        raise CameraNotRunning(NOT_RUNNING)
    return {"id": cid, "name": found["name"]}


def raw_url(port, camera=None):
    """The app's raw feed, for one named camera or (None) the first."""
    import urllib.parse
    url = f"http://127.0.0.1:{int(port)}/raw"
    if camera:
        url += "?camera=" + urllib.parse.quote(str(camera), safe="")
    return url


def open_raw(port, camera=None, timeout=6.0):
    """Open the raw MJPEG stream. A refusal for a NAMED camera means the app
    has no such running camera -- reported as such, never retried on the bare
    path (that would be the first camera's picture under the wrong name)."""
    import urllib.error
    import urllib.request
    try:
        return urllib.request.urlopen(raw_url(port, camera), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if camera and exc.code in (404, 503):
            raise CameraNotRunning(NOT_RUNNING) from None
        raise


def _read_index(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_index(path, index):
    """Temp file and one replace, so a reader never sees half an index."""
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(index, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass                    # an untagged capture is still a capture


def tag_camera(kind, slug, fname, cam):
    """Record which camera took `fname` in the folder's side index."""
    if not cam:
        return
    path = os.path.join(dataset_dir(kind, slug), CAMERA_INDEX)
    with _index_lock:
        index = _read_index(path)
        index[fname] = {"camera": cam["id"], "camera_name": cam["name"]}
        _write_index(path, index)


def _grab_frame(port, timeout=6.0, camera=None):
    """One JPEG out of the app's RAW (unannotated) MJPEG stream -- training
    data should not have boxes burned in. `camera` names which one; None is
    the first, as it always was."""
    req = open_raw(port, camera, timeout)
    try:
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = req.read(4096)
            if not chunk:
                break
            buf += chunk
            s = buf.find(b"\xff\xd8")
            e = buf.find(b"\xff\xd9", s + 2) if s != -1 else -1
            if s != -1 and e != -1:
                return buf[s:e + 2]
        return None
    finally:
        req.close()


def dataset_snap(systems_dir, slug, name, camera=None):
    """Save one named raw frame into the dataset image folder, from the
    camera the request named (None: the first, as before)."""
    if not dataset_available():
        raise ValueError("no NVMe storage mounted - dataset capture needs /mnt/nvme")
    cam = resolve_camera(systems_dir, slug, camera)
    from core.services.detector_control import stream_port  # noqa: PLC0415
    jpeg = _grab_frame(stream_port(systems_dir, slug),
                       camera=cam["id"] if cam else None)
    if not jpeg:
        raise (CameraNotRunning(NOT_RUNNING) if cam
               else ValueError("no frame available - is detection running?"))
    folder = dataset_dir("image", slug)
    os.makedirs(folder, exist_ok=True)
    fname = f"{_safe_name(name)}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    with open(os.path.join(folder, fname), "wb") as f:
        f.write(jpeg)
    out = {"file": fname}
    if cam:
        tag_camera("image", slug, fname, cam)
        remember_camera(systems_dir, slug, cam["id"])
        out.update(camera=cam["id"], camera_name=cam["name"])
    return out


def dataset_list(slug=None):
    """{available, images: [...], clips: [...], recording: {...}|None}.

    Scoped to one system's folders: a board running four systems must not show
    the entrance camera's training data on the loading bay's page.
    """
    state = dataset_state()
    out = {"available": state["ok"], "images": [], "clips": [],
           "recording": None, "root": DATASET_ROOT,
           # WHY it is unavailable, so the page can name the actual fix
           # instead of telling somebody to mount a drive that is mounted.
           "reason": state["reason"], "detail": state["detail"]}
    if not out["available"]:
        return out
    for key, kind, ext in (("images", "image", ".jpg"),
                           ("clips", "clip", ".mp4")):
        folder = dataset_dir(kind, slug)
        try:
            names = [n for n in os.listdir(folder) if n.endswith(ext)]
        except OSError:
            names = []
        # Which camera took each file, where it was recorded (multi-camera
        # captures since the choice existed); older files carry no tag.
        tags = _read_index(os.path.join(folder, CAMERA_INDEX)) if names else {}
        rows = []
        for n in sorted(names, reverse=True)[:200]:
            try:
                st = os.stat(os.path.join(folder, n))
                row = {"name": n, "size": st.st_size, "mtime": int(st.st_mtime)}
                tag = tags.get(n)
                if isinstance(tag, dict) and tag.get("camera"):
                    row.update(camera=str(tag["camera"]),
                               camera_name=str(tag.get("camera_name") or tag["camera"]))
                rows.append(row)
            except OSError:
                continue
        out[key] = rows
    job = _dataset_jobs.get(slug) if slug else None
    if job and not job.get("done"):
        # elapsed/manual so the page can show a running timer and offer Stop
        # for the session kind rather than a countdown it cannot influence.
        out["recording"] = {
            "file": job["file"], "manual": bool(job.get("manual")),
            "frames": job.get("frames", 0),
            "elapsed_s": round(time.time() - job.get("started_at", time.time()), 1),
            "stopping": bool(job.get("stop")),
            "max_s": MAX_DATASET_SESSION_S if job.get("manual") else None,
            "camera": job.get("camera"), "camera_name": job.get("camera_name")}
    elif job and job.get("error"):
        out["recording"] = {"file": job["file"], "error": job["error"]}
    return out


def dataset_delete(kind, names, slug=None):
    """Delete the named dataset files. Returns how many went. Each name is
    resolved through dataset_path, so nothing outside THIS system's dataset
    folder can be reached however the request is spelled."""
    removed = 0
    for name in names or []:
        path = dataset_path(kind, str(name), slug)
        if not path:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            continue
        _untag(kind, slug, os.path.basename(path))
    return removed


def _untag(kind, slug, fname):
    """Drop a deleted file's camera tag, so a later capture that happens to
    reuse the name (same name, same second) is not labelled with the old
    camera."""
    path = os.path.join(dataset_dir(kind, slug), CAMERA_INDEX)
    with _index_lock:
        index = _read_index(path)
        if index.pop(fname, None) is not None:
            _write_index(path, index)


def dataset_zip(kind, names, slug=None):
    """The named dataset files as one ZIP, in memory. Datasets are labelled
    off-box, so pulling a session down in a single download is the normal way
    they leave the board."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names or []:
            path = dataset_path(kind, str(name), slug)
            if path:
                zf.write(path, arcname=os.path.basename(path))
    return buf.getvalue()


def dataset_path(kind, name, slug=None):
    """Absolute path of one dataset file, or None. `name` must be a bare
    filename -- the basename check is the security boundary, and scoping to the
    system's own folder is the second one: a request cannot name a file
    belonging to another system on the board."""
    if kind not in ("image", "clip") or os.path.basename(name) != name:
        return None
    if name.startswith("."):
        return None                 # the camera index, a probe: never a capture
    path = os.path.join(dataset_dir(kind, slug), name)
    return path if os.path.isfile(path) else None
