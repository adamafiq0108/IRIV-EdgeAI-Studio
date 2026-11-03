# -*- coding: utf-8 -*-
"""
builtin_models.py -- the detection models a system package already ships.

Two model stores exist, and they are not the same thing:

  - core/services/models.py owns config/models/ -- weights an operator UPLOADS
    and DEPLOYS to a paired device over the control channel.
  - this module owns systems/<slug>/models/ -- the built-in catalogue a package
    ships (models.toml) and the HEFs fetch_models.py pulls onto the box. These
    are what the LOCAL detection app runs, chosen in config.toml [detector.hailo].

The dashboard could only ever describe the first before. This module lets an
operator SEE the built-in models already on disk and pick which one the local
app runs -- by writing the choice into the package's config.local.toml. The app
watches that file and rebuilds its detector (systems/<slug>/app/run.py), so the
switch takes effect without a restart.

Read-only where it can be: listing never writes. Selecting writes exactly two
keys (detector.kind, detector.hailo.model) into config.local.toml and leaves
everything else -- camera, zones, storage -- untouched, because local wins per
key and sub-tables merge field by field.
"""

import os
import re
import subprocess
import threading
import urllib.request

try:
    import tomllib                     # Python 3.11+
except ModuleNotFoundError:            # pragma: no cover
    import tomli as tomllib

from core import config as config_mod

#: Model Zoo architectures, newest first -- mirrors systems/*/app/hailo.py so a
#: model counts as "downloaded" if its HEF is present for any of them.
_ARCHS = ("hailo10h", "hailo8", "hailo8l")

_PCI_HINTS = (
    (re.compile(r"hailo-?10", re.I), "hailo10h"),
    (re.compile(r"hailo-?8l", re.I), "hailo8l"),
    (re.compile(r"hailo-?8", re.I), "hailo8"),
)


def _run(argv, timeout=5):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


#: detect_arch() runs hailortcli and lspci -- tens of milliseconds of
#: subprocesses -- and its answer cannot change while the box is up. A FOUND
#: chip is kept for the life of the process; "none" is asked again after
#: ARCH_RETRY_S, in case the driver was still loading when we first looked.
ARCH_RETRY_S = 60.0
_arch = {"value": None, "at": 0.0}


def detect_arch():
    """detect_arch_uncached(), cached as ARCH_RETRY_S describes."""
    import time as _time                                         # noqa: PLC0415
    if _arch["value"] is not None:
        return _arch["value"]
    if _arch["at"] and _time.monotonic() - _arch["at"] < ARCH_RETRY_S:
        return None
    value = detect_arch_uncached()
    _arch.update(value=value, at=_time.monotonic())
    return value


def detect_arch_uncached():
    """Which Hailo accelerator is plugged into THIS box: hailo10h | hailo8 |
    hailo8l | None. Same detection order as systems/*/app/hailo.py -- hailortcli
    first (authoritative), then lspci, then the PCIe driver's sysfs entries -- so
    the dashboard and the runtime never disagree about the architecture. The
    master runs on the same board as the accelerator in the single-box setup,
    which is why it can answer this at all."""
    text = _run(["hailortcli", "fw-control", "identify"]) or _run(["hailortcli", "scan"])
    for pattern, arch in _PCI_HINTS:
        if text and pattern.search(text):
            return arch
    for line in _run(["lspci", "-mm"]).splitlines():
        if "hailo" in line.lower():
            for pattern, arch in _PCI_HINTS:
                if pattern.search(line):
                    return arch
            return "hailo8"
    import glob
    for path in glob.glob("/sys/class/hailo_chardev/hailo*/device/device"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read().strip():
                    return "hailo8"
        except OSError:
            continue
    return None


def identify_arch():
    """The accelerator as the CHIP names itself (hailortcli), or None.

    detect_arch() falls back to lspci and sysfs, which cannot tell a Hailo-8L
    from a Hailo-8 -- both enumerate as "Hailo-8" on the PCIe bus. That is
    fine for a page saying which chip is fitted; it is not fine for choosing
    which HEF to DOWNLOAD, because a hailo8 build does not load on a
    Hailo-8L. The model bootstrap asks this instead, and waits (retries at
    the next start) rather than fetch a guessed build.
    """
    text = _run(["hailortcli", "fw-control", "identify"]) or _run(["hailortcli", "scan"])
    for pattern, arch in _PCI_HINTS:
        if text and pattern.search(text):
            return arch
    return None


def _pkg_dir(systems_dir, slug):
    return os.path.join(str(systems_dir), slug)


def _read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _merged_config(pkg_dir):
    """The app's own merged view: config.toml overlaid by config.local.toml,
    the same layering systems/<slug>/app/config.py applies at runtime."""
    cfg = {}
    for name in ("config.toml", "config.local.toml"):
        cfg = _merge(cfg, _read_toml(os.path.join(pkg_dir, name)))
    return cfg


def _merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _catalogue(pkg_dir):
    return _read_toml(os.path.join(pkg_dir, "models.toml")).get("models") or {}


def _models_root(pkg_dir, cfg=None):
    """The directory holding this system's HEFs. Honours
    [detector.hailo].models_dir (which a scaffolded system points at the SHARED
    models dir, so every system reuses one fetch), absolute or relative to the
    package; defaults to <pkg>/models."""
    cfg = cfg if cfg is not None else _merged_config(pkg_dir)
    md = ((cfg.get("detector") or {}).get("hailo") or {}).get("models_dir") or "models"
    return md if os.path.isabs(md) else os.path.join(pkg_dir, md)


def _model_roots(pkg_dir, cfg=None):
    """Every place a HEF for this system might be: its own models dir plus
    EVERY shared/legacy root any release has used (config.model_search_roots).

    The full list, not just today's preferred write target: a box that grew
    an empty /mnt/nvme/model on upgrade must keep seeing the models it
    already fetched into systems/demo/models or /home/pi/model as installed.
    Presence on disk decides "downloaded"; preference only decides where a
    NEW download lands (config.shared_models_dir)."""
    roots = [_models_root(pkg_dir, cfg)]
    try:
        for shared in config_mod.model_search_roots():
            if shared not in roots:
                roots.append(shared)
    except Exception:                                       # noqa: BLE001
        pass
    return roots


def _downloaded_archs(roots, filename):
    """Which arch folders (across `roots`) hold this model's HEF."""
    if isinstance(roots, str):
        roots = [roots]
    have = []
    for arch in _ARCHS:
        if any(os.path.exists(os.path.join(r, arch, filename)) for r in roots):
            have.append(arch)
    return have


#: Detector kinds that actually consume a chosen Hailo model. The gstreamer
#: pipeline reads the same detector.model / detector.hailo.model keys, so a
#: selection must be visible as "current" -- and preserved -- for both.
_MODEL_KINDS = ("hailo", "gstreamer")


def _uploaded_detection_models(config_dir, pkg_dir=None):
    """Operator-UPLOADED models (core/services/models.py store) the LOCAL app
    can run: task=detection, weights present, and a HEF (the local Hailo and
    gstreamer backends load nothing else). Keyed like catalogue entries so the
    UI and select() treat both stores the same."""
    try:
        from core.services import models as models_service  # noqa: PLC0415
        out = {}
        for m in models_service.listing(config_dir, pkg_dir):
            filename = m.get("file") or ""
            if (m.get("task") == "detection" and m.get("weights_present")
                    and filename.lower().endswith(".hef")):
                out[m["name"]] = m
        return out
    except Exception:                                       # noqa: BLE001
        return {}


def _uploaded_dir(config_dir, pkg_dir=None):
    from core.services import models as models_service      # noqa: PLC0415
    return models_service._dir(config_dir, pkg_dir)


def listing(systems_dir, slug, config_dir=None):
    """Every DETECTION model the local app can run -- the package catalogue
    plus operator-uploaded HEFs -- with download status and which one the local
    app is currently set to run.

    Only detection models are offered: the app's Hailo path assumes NMS-on-chip
    output, so a segmentation/pose HEF would load and return nothing useful --
    surfacing it as "runnable" would be a lie.
    """
    pkg_dir = _pkg_dir(systems_dir, slug)
    cfg = _merged_config(pkg_dir)
    det = cfg.get("detector") or {}
    # [detector.hailo].model FIRST: the app merges the hailo sub-table over the
    # top level ({**det, **hailo} in detector.py/gst_detector.py), so the
    # sub-table is what actually runs. Older writers left a stale top-level
    # model behind; reading top-level first showed THAT instead of the running
    # model -- the "I picked a model and it kept saying yolov8n" bug.
    current = ((det.get("hailo") or {}).get("model") or det.get("model")) \
        if det.get("kind") in _MODEL_KINDS else None
    roots = _model_roots(pkg_dir, cfg)

    models = []
    for name, spec in sorted(_catalogue(pkg_dir).items()):
        if spec.get("task") != "detection":
            continue
        filename = spec.get("file") or f"{name}.hef"
        archs = _downloaded_archs(roots, filename)
        models.append({
            "name": name,
            "task": spec.get("task"),
            "description": spec.get("description") or "",
            "downloaded": bool(archs),
            "downloaded_archs": archs,
            "current": name == current,
            "source": "builtin",
        })
    listed = {m["name"] for m in models}
    for name, spec in sorted(_uploaded_detection_models(config_dir, pkg_dir).items()):
        if name in listed:
            continue                    # a catalogue model shadows an upload
        models.append({
            "name": name,
            "task": "detection",
            "description": spec.get("description") or "uploaded model",
            "downloaded": True,         # weights_present was the filter
            "downloaded_archs": [spec.get("arch")] if spec.get("arch") else [],
            "current": name == current,
            "source": "uploaded",
        })
    return {
        "models": models,
        "current": current,
        "kind": det.get("kind") or "mock",
    }


# -- fetching HEFs from the dashboard -----------------------------------------
# The same download scripts/fetch_models.py does, but callable from the AI
# Models page: an operator picks a catalogue model that is "not downloaded" and
# the box pulls the right build for its own accelerator into the SHARED models
# dir. Background thread -- a HEF is tens of MB and the HTTP request must not
# hang on it.

#: (slug, name) -> {"state": downloading|done|failed, "pct": int, "error": str|None}
_FETCHES = {}
_FETCH_LOCK = threading.Lock()


def fetch_status(slug):
    """Every fetch this master started for `slug`, keyed by model name."""
    with _FETCH_LOCK:
        return {name: dict(v) for (s, name), v in _FETCHES.items() if s == slug}


def _download(url, dest, key=None):
    # One writer per file across processes: the hub's start-up fetch
    # (model_bootstrap) may be fetching this very HEF (download_lock).
    from core.services import model_bootstrap                  # noqa: PLC0415
    lock = model_bootstrap.download_lock(dest)
    if lock is None:
        return "this model is already being downloaded - try again shortly"
    try:
        return _download_locked(url, dest, key)
    finally:
        model_bootstrap.release_download_lock(lock)


def _download_locked(url, dest, key=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if key and total:
                    with _FETCH_LOCK:
                        if key in _FETCHES:
                            _FETCHES[key]["pct"] = done * 100 // total
        # A HEF is megabytes. A few hundred bytes is an error page or a cut
        # transfer, and renamed into place it is a "downloaded" model the
        # detector cannot load (model_bootstrap.MIN_HEF_BYTES).
        from core.services import model_bootstrap              # noqa: PLC0415
        if dest.lower().endswith(".hef") \
                and os.path.getsize(tmp) < model_bootstrap.MIN_HEF_BYTES:
            raise ValueError(f"the download was {os.path.getsize(tmp)} bytes "
                             "- not a model file")
        os.replace(tmp, dest)
        return None
    except Exception as e:                                  # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return str(e)


def start_fetch(systems_dir, slug, name, task="detection", on_done=None):
    """Begin downloading catalogue model `name`'s HEF for THIS box's arch into
    the shared models dir. Returns the fetch's current status dict; raises
    ValueError on anything the operator must fix (unknown model, no
    accelerator, no zoo release).

    `task` is what the catalogue entry must declare. It defaults to "detection"
    so every existing caller is unchanged; the depth catalogue passes "depth".
    The download itself is task-agnostic -- a HEF is a HEF, and the zoo URL is
    built from (release, arch, name) either way -- so the only thing that
    differs is which entries this is allowed to reach."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    catalogue_full = _read_toml(os.path.join(pkg_dir, "models.toml"))
    spec = (catalogue_full.get("models") or {}).get(name)
    if not spec or spec.get("task") != task:
        raise ValueError(f"no built-in {task} model named {name!r}")
    arch = detect_arch()
    if not arch:
        raise ValueError("no Hailo accelerator detected on this box - "
                         "stage the HEF with scripts/fetch_models.py --arch instead")
    zoo = catalogue_full.get("zoo") or {}
    ver = (zoo.get("vers") or {}).get(arch)
    base_url = zoo.get("base_url")
    if not ver or not base_url:
        raise ValueError(f"models.toml has no zoo release for arch {arch!r}")

    shared = str(config_mod.shared_models_dir())
    filename = spec.get("file") or f"{name}.hef"
    dest = os.path.join(shared, arch, filename)
    key = (slug, name)
    if os.path.exists(dest):
        with _FETCH_LOCK:
            _FETCHES[key] = {"state": "done", "pct": 100, "error": None}
        if on_done:
            try:
                on_done()
            except Exception:                               # noqa: BLE001
                pass
        with _FETCH_LOCK:
            return dict(_FETCHES[key])
    with _FETCH_LOCK:
        current = _FETCHES.get(key)
        if current and current["state"] == "downloading":
            return dict(current)
        _FETCHES[key] = {"state": "downloading", "pct": 0, "error": None}

    url = base_url.format(ver=ver, arch=arch, name=name)

    def work():
        err = _download(url, dest, key)
        if err is None:
            # The class names are WRITTEN from models.COCO_CLASSES, never
            # downloaded: the Model Zoo URL they came from now answers 404,
            # and a configured labels file that is missing stops the detector.
            try:
                from core.services import model_bootstrap      # noqa: PLC0415
                model_bootstrap.write_labels(shared)
            except OSError:
                pass
        with _FETCH_LOCK:
            _FETCHES[key] = ({"state": "failed", "pct": 0, "error": err} if err
                             else {"state": "done", "pct": 100, "error": None})
        # A caller waiting on the weights (a new system whose template names a
        # model) gets its turn HERE -- selecting before the download lands is
        # what _resolve_selection refuses. Never allowed to kill the thread.
        if err is None and on_done:
            try:
                on_done()
            except Exception:                               # noqa: BLE001
                pass

    threading.Thread(target=work, daemon=True).start()
    with _FETCH_LOCK:
        return dict(_FETCHES[key])


def _resolve_selection(pkg_dir, config_dir, name):
    """Resolve model `name` (catalogue or uploaded) to the paths the app needs.

    Refuses a model neither store lists, or one whose HEF is not on disk --
    selecting a model the app cannot load would swap a working detector for a
    startup error. Returns {"model", "models_dir", "labels", "confidence"}
    (confidence is None when no manifest supplies one).
    """
    catalogue = _catalogue(pkg_dir)
    spec = catalogue.get(name)
    uploaded = None
    if spec is not None:
        if spec.get("task") != "detection":
            raise ValueError(f"no built-in detection model named {name!r}")
        filename = spec.get("file") or f"{name}.hef"
        if not _downloaded_archs(_model_roots(pkg_dir), filename):
            raise ValueError(
                f"{name} is not downloaded yet - fetch it into the shared models dir.")
    else:
        uploaded = _uploaded_detection_models(config_dir, pkg_dir).get(name)
        if uploaded is None:
            # THIRD store: a HEF dropped into the model folder by hand. Every
            # entry in [[detector.models]] carries its own models_dir, so a
            # dropped model runs beside a built-in one perfectly well -- this
            # resolver simply did not know the folder existed, so "run both"
            # failed with "no detection model named ..." about a model the
            # page was listing two cards away.
            from core.services import detector_control as dc      # noqa: PLC0415
            from core.services import local_models as local_service  # noqa: PLC0415

            row = local_service.get(name)
            if row is not None:
                # The app reads labels from a FILE, and the drop folder is not
                # ours to write into -- so they are materialised into this
                # system's own data dir, exactly as local_models.select does
                # for a single selection.
                labels = ""
                if row.get("labels"):
                    data_dir = dc._data_dir(pkg_dir, dc._merged(pkg_dir))
                    path = os.path.join(data_dir, f"labels_{row['name']}.txt")
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write("\n".join(row["labels"]) + "\n")
                        labels = path.replace("\\", "/")
                    except OSError:
                        labels = ""
                try:
                    conf = float(row.get("confidence"))
                    conf = conf if 0 < conf < 1 else None
                except (TypeError, ValueError):
                    conf = None
                return {
                    "model": row["name"],
                    "models_dir": str(row["folder"]).replace("\\", "/"),
                    "labels": labels,
                    "confidence": conf,
                    "classes": [str(c) for c in (row.get("classes") or [])],
                }
            raise ValueError(f"no detection model named {name!r} "
                             "(built-in, uploaded, or in the model folder)")

    if uploaded is not None:
        # Uploaded weights live FLAT in the config models dir; the app's
        # resolve_hef falls back to <models_dir>/<name>.hef when there is no
        # per-arch folder, so pointing models_dir there is enough. When the
        # manifest's HEF was never uploaded but already exists in the SHARED
        # store (fetched by the catalogue), point the app there instead --
        # weights_present said yes because of that store, so the selection
        # must load from it too.
        filename = uploaded.get("file") or f"{name}.hef"
        if os.path.exists(os.path.join(_uploaded_dir(config_dir, pkg_dir), filename)):
            models_root = str(_uploaded_dir(config_dir, pkg_dir)).replace("\\", "/")
        else:
            models_root = str(config_mod.shared_models_dir()).replace("\\", "/")
        labels_file = uploaded.get("labels") or ""
        labels_path = os.path.join(_uploaded_dir(config_dir, pkg_dir),
                                   os.path.basename(labels_file)) if labels_file else ""
        if labels_path and os.path.exists(labels_path):
            labels = labels_path.replace("\\", "/")
        else:
            shared_dir = str(config_mod.shared_models_dir()).replace("\\", "/")
            labels = f"{shared_dir}/coco.txt"
        shared = models_root
    else:
        # Point this system at the root that ACTUALLY HOLDS the HEF -- the
        # roots are searched in preference order, so a model present in
        # several places selects the preferred copy. Falling back to the bare
        # write target here once pointed an upgraded box (empty
        # /mnt/nvme/model, HEF still in systems/demo/models) at a directory
        # with nothing in it. Repairs a system created before the shared dir
        # existed the same way it always did.
        roots = _model_roots(pkg_dir)
        filename = spec.get("file") or f"{name}.hef"
        holder = next((r for r in roots if _downloaded_archs([r], filename)),
                      None)
        shared = str(holder or config_mod.shared_models_dir()).replace("\\", "/")
        coco = next((os.path.join(r, "coco.txt") for r in roots
                     if os.path.exists(os.path.join(r, "coco.txt"))), None)
        if coco is None:
            # No class names anywhere yet (the old download URL is gone).
            # Written now, beside the HEF, so the labels file this selection
            # names exists -- the detector refuses to start without it.
            try:
                from core.services import model_bootstrap      # noqa: PLC0415
                coco = model_bootstrap.write_labels(shared)
            except OSError:
                coco = os.path.join(shared, "coco.txt")
        labels = str(coco).replace("\\", "/")

    # The manifest's confidence threshold travels WITH the model choice --
    # without this the app silently keeps the template default (0.45) and the
    # value typed on the Models page never applies. A hand-written manifest
    # with the same name as a catalogue model still contributes its
    # confidence, even though the catalogue supplies the weights.
    manifest = uploaded or _uploaded_detection_models(config_dir, pkg_dir).get(name) or {}

    # A manifest that ships its OWN labels file wins over the shared coco.txt,
    # even for a catalogue model. The two files disagreed in both naming and
    # ORDER ("motorbike" vs "motorcycle"), so the Models page listed one
    # vocabulary while the detector ran the other -- and since line order is
    # the class index, the wrong file relabels every detection.
    own_labels = manifest.get("labels") or ""
    if own_labels:
        own_path = os.path.join(_uploaded_dir(config_dir, pkg_dir),
                                os.path.basename(own_labels))
        if os.path.exists(own_path):
            labels = own_path.replace("\\", "/")
    conf = manifest.get("confidence") or (spec or {}).get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = None
    if not (conf and 0 < conf < 1):
        conf = None
    # "Classes to keep" ticked on the model form travels WITH the choice. It
    # used to be stored in the manifest and never applied, so an operator who
    # ticked two classes and pressed "Use on this system" got whatever filter
    # the previous model left behind -- and the workflow's class list, which
    # reads the filter, showed that stale set. Empty means "every class", as
    # the form says, so it clears any previous filter.
    picked = manifest.get("classes")
    if picked is None:
        picked = (spec or {}).get("classes")
    classes = [str(c).strip() for c in (picked or []) if str(c).strip()]
    return {"model": name, "models_dir": shared, "labels": labels,
            "confidence": conf, "classes": classes,
            "has_manifest": bool(manifest or spec)}


def _read_labels_file(path):
    """The class names a labels file lists, in class-index order. [] when it
    cannot be read -- an unknown vocabulary must not be treated as an empty
    one."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def _prune_classes(tables, labels_path):
    """Drop keep-filter classes the NEW model cannot emit. Returns the names
    dropped (for the operator's confirmation message).

    A class filter is written against ONE model's vocabulary. Carried onto a
    model that never emits those names it does not filter -- it silences the
    detector completely, because the detector keeps only labels in the set and
    none of them ever appear. That looks exactly like a dead camera, so
    switching models prunes the filter to the names the new model actually has;
    if none survive, the filter is cleared (= every class) rather than left to
    detect nothing.
    """
    labels = _read_labels_file(labels_path)
    if not labels:
        return []                       # unknown vocabulary: leave it alone
    known, dropped = set(labels), []
    for tbl in tables:
        current = [str(c) for c in (tbl.get("classes") or [])]
        if not current:
            continue
        kept = [c for c in current if c in known]
        dropped += [c for c in current if c not in known]
        if kept:
            tbl["classes"] = kept
        else:
            tbl.pop("classes", None)    # empty list would mean "keep nothing"
    return sorted(set(dropped))


def _write_pkg_local(pkg_dir, local):
    """Atomic rewrite of a package's config.local.toml (unique temp name,
    fsync, one replace -- cfg_lock.write)."""
    from core.services import cfg_lock                          # noqa: PLC0415
    cfg_lock.write(os.path.join(pkg_dir, "config.local.toml"),
                   config_mod.toml_text(local))
    try:
        from core.services import detector_control as dc       # noqa: PLC0415
        dc._toml_cache.pop(dc._local_path(pkg_dir), None)
    except Exception:                                          # noqa: BLE001
        pass


def _pkg_locked(fn):
    """Run a (systems_dir, slug, ...) read-modify-write of config.local.toml
    under the system's config lock, in-process AND across processes -- the
    hub selects models while the master saves zones into the same file."""
    import functools                                           # noqa: PLC0415

    @functools.wraps(fn)
    def wrapper(systems_dir, slug, *args, **kwargs):
        from core.services import detector_control as dc       # noqa: PLC0415
        with dc.pkg_config_lock(_pkg_dir(systems_dir, slug)):
            return fn(systems_dir, slug, *args, **kwargs)
    return wrapper


@_pkg_locked
def select(systems_dir, slug, name, config_dir=None):
    """Point the local app at model `name` -- a catalogue model or an uploaded
    one -- by writing detector.kind + detector(.hailo).model into the package's
    config.local.toml. Selecting ONE model also clears any [[detector.models]]
    multi list: "use on this system" means "this model, alone".

    The current detector KIND is preserved when it already runs models
    (gstreamer stays gstreamer); anything else (mock, none) becomes hailo.
    Writing kind="hailo" over a running gstreamer pipeline used to make the
    selection silently not apply. Returns the new {kind, model}.
    """
    pkg_dir = _pkg_dir(systems_dir, slug)
    sel = _resolve_selection(pkg_dir, config_dir, name)

    # Preserve a kind that already consumes models; otherwise switch to hailo.
    current_kind = (_merged_config(pkg_dir).get("detector") or {}).get("kind")
    new_kind = current_kind if current_kind in _MODEL_KINDS else "hailo"
    local = _read_toml(os.path.join(pkg_dir, "config.local.toml"))
    detector = dict(local.get("detector") or {})
    detector["kind"] = new_kind
    # Write the params at BOTH [detector] top-level (where HailoDetector reads
    # them directly) and [detector.hailo] (the documented home), so this works
    # whether the app merges the sub-table or reads only the top level.
    detector["model"] = name
    detector["models_dir"] = sel["models_dir"]
    detector["labels"] = sel["labels"]
    hailo = dict(detector.get("hailo") or {})
    hailo["model"] = name
    hailo["models_dir"] = sel["models_dir"]
    hailo["labels"] = sel["labels"]
    if sel["confidence"]:
        detector["confidence"] = sel["confidence"]
        hailo["confidence"] = sel["confidence"]
    # The manifest's ticked classes ARE the filter for this model. A manifest
    # with none ticked means every class, so the previous model's filter is
    # cleared rather than inherited.
    if sel["has_manifest"]:
        if sel["classes"]:
            detector["classes"] = sel["classes"]
            hailo["classes"] = sel["classes"]
        else:
            detector.pop("classes", None)
            hailo.pop("classes", None)
    # A single selection is exclusive -- leaving a stale [[detector.models]]
    # would keep the app in multi mode and silently ignore this choice.
    detector.pop("models", None)
    dropped = _prune_classes((detector, hailo), sel["labels"])
    detector["hailo"] = hailo
    local["detector"] = detector
    _write_pkg_local(pkg_dir, local)
    return {"kind": new_kind, "model": name, "dropped_classes": dropped}


#: How many models may run at once on one accelerator. The HailoRT scheduler
#: time-slices the chip, so each extra model divides the frame rate; three is
#: already generous for an edge box.
MAX_ACTIVE_MODELS = 3


@_pkg_locked
def select_many(systems_dir, slug, names, config_dir=None):
    """Run SEVERAL models at once on this system's one camera.

    Writes [[detector.models]] -- the multi-model contract the app engine
    already consumes (MultiHailoDetector / per-branch gstreamer): every model
    runs on the same frame and each detection is tagged with the model that
    produced it. The legacy single keys (detector.model/models_dir/labels)
    mirror the FIRST entry so an engine build without multi support degrades
    to one model instead of failing to start.

    One name delegates to select() (exclusive single-model write, clears the
    list). Per-model classes/confidence overrides an operator already set on a
    kept entry survive the rewrite. Returns {kind, models: [names]}.
    """
    clean, seen = [], set()
    for n in names or []:
        n = str(n).strip()
        if n and n not in seen:
            seen.add(n)
            clean.append(n)
    if not clean:
        # Running NOTHING is a state, not a mistake. A system being set up, or
        # one taken out of service, legitimately has no model chosen -- and
        # refusing it here meant a box with a single custom model could never
        # switch that model off, because the only way out of the set was to
        # pick a different member of it.
        #
        # WHETHER it is allowed right now is not this function's business: the
        # route checks that detection is stopped first, because a live
        # detector with no model sees nothing and every rule on the system
        # silently reads zero. Here we only carry it out.
        pkg_dir = _pkg_dir(systems_dir, slug)
        local = _read_toml(os.path.join(pkg_dir, "config.local.toml"))
        detector = dict(local.get("detector") or {})
        hailo = dict(detector.get("hailo") or {})
        # Both the list and the legacy single keys, or an engine build without
        # multi-model support would keep loading whatever the mirror still
        # named and the system would go on detecting after being switched off.
        detector.pop("models", None)
        for tbl in (detector, hailo):
            for key in ("model", "models_dir", "labels"):
                tbl.pop(key, None)
        detector["hailo"] = hailo
        local["detector"] = detector
        _write_pkg_local(pkg_dir, local)
        return {"kind": detector.get("kind") or "hailo", "models": [],
                "dropped_classes": []}
    if len(clean) > MAX_ACTIVE_MODELS:
        raise ValueError(f"at most {MAX_ACTIVE_MODELS} models can run at once")
    if len(clean) == 1:
        return select(systems_dir, slug, clean[0], config_dir=config_dir)

    pkg_dir = _pkg_dir(systems_dir, slug)
    sels = [_resolve_selection(pkg_dir, config_dir, n) for n in clean]

    local = _read_toml(os.path.join(pkg_dir, "config.local.toml"))
    detector = dict(local.get("detector") or {})
    previous = {e.get("model"): e for e in (detector.get("models") or [])
                if isinstance(e, dict)}

    current_kind = (_merged_config(pkg_dir).get("detector") or {}).get("kind")
    detector["kind"] = current_kind if current_kind in _MODEL_KINDS else "hailo"

    first = sels[0]
    hailo = dict(detector.get("hailo") or {})
    for tbl in (detector, hailo):
        tbl["model"] = first["model"]
        tbl["models_dir"] = first["models_dir"]
        tbl["labels"] = first["labels"]
        if first["confidence"]:
            tbl["confidence"] = first["confidence"]

    entries, dropped = [], []
    for sel in sels:
        entry = {"model": sel["model"], "models_dir": sel["models_dir"],
                 "labels": sel["labels"]}
        if sel["confidence"]:
            entry["confidence"] = sel["confidence"]
        was = previous.get(sel["model"]) or {}
        # A filter the operator tuned for THIS entry wins; otherwise the
        # manifest's ticked classes apply, same as a single selection.
        if was.get("classes"):
            entry["classes"] = [str(c) for c in was["classes"]][:200]
        elif sel["classes"]:
            entry["classes"] = sel["classes"][:200]
        if was.get("confidence") and not entry.get("confidence"):
            entry["confidence"] = was["confidence"]
        # Each entry's filter is pruned against ITS OWN labels; a name one
        # model has and another does not is legitimate here.
        dropped += _prune_classes((entry,), sel["labels"])
        entries.append(entry)
    # The shared filter must survive under EVERY model, since it applies to all
    # of them: prune it against the union of their vocabularies.
    union = []
    for sel in sels:
        union += _read_labels_file(sel["labels"])
    if union:
        known = set(union)
        for tbl in (detector, hailo):
            current = [str(c) for c in (tbl.get("classes") or [])]
            if not current:
                continue
            kept = [c for c in current if c in known]
            dropped += [c for c in current if c not in known]
            if kept:
                tbl["classes"] = kept
            else:
                tbl.pop("classes", None)
    detector["models"] = entries
    detector["hailo"] = hailo
    local["detector"] = detector
    _write_pkg_local(pkg_dir, local)
    return {"kind": detector["kind"], "models": [e["model"] for e in entries],
            "dropped_classes": sorted(set(dropped))}
