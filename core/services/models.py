# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
models.py -- the master's model store: bring your own HEF (or ONNX, or .pt).

The template ships a mock detector and a Hailo backend, but the point of an
edge AI platform is somebody else's model. This is where an operator uploads
one and describes it, in a form, without touching Python:

    <config_dir>/models/<name>.hef        the weights
    <config_dir>/models/<name>.json       the manifest describing them
    <config_dir>/models/<name>.labels     class names, one per line (optional)

A device pulls both over HTTP from its master and hands the manifest to its
detector. That is why the manifest is strict and small: every field is
something a detector genuinely cannot guess -- the input size it was compiled
for, whether NMS runs on-chip, which architecture the HEF targets. Get any of
those wrong and the model loads and returns nonsense, which is far worse than
failing to load.

Uploads are admin-only, size-capped, and the filename is rebuilt from the
sanitised model name -- never trusted from the client, because this writes to
disk.
"""

import json
import os
import re
import time

from core import config as config_mod

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ARCHS = ("hailo10h", "hailo8", "hailo8l", "cpu", "gpu", "other")


#: A board that can be asked for MORE than its own architecture.
#:
#: The narrowing exists because a HEF for the wrong accelerator installs
#: cleanly and then fails at load. That is true of a Hailo-8 board, which runs
#: Hailo-8 HEFs and nothing else. It is not true of a Hailo-10 module: boards
#: built around it have run the 8 and 8L builds here in practice, and hiding
#: those left an operator with weights they could not install on hardware that
#: loads them. So the wider list is granted where it is earned, per chip,
#: rather than by dropping the check that keeps the other boards honest.
ALSO_RUNS = {
    "hailo10h": ("hailo8", "hailo8l"),
}


def offered_archs(detected, current=""):
    """The architectures worth OFFERING on this box, as against the ones that
    validate.

    A HEF is compiled for one accelerator and will not load on another, so on
    a board whose chip is known there is exactly one right answer and five
    wrong ones -- and every wrong one is a model that saves cleanly, sits in
    the list looking installed, and fails at load time with an arch mismatch
    the operator has no reason to connect to a dropdown they picked from a
    week earlier.

    ARCHS stays the full vocabulary because it is what VALIDATES: a model
    imported from another box, or one saved before its chip was detectable,
    must still be editable without its arch being silently rewritten -- hence
    `current`, which keeps a value already on the model in the list.
    """
    if not detected or detected not in ARCHS:
        return list(ARCHS)                  # no chip detected: no basis to narrow
    allowed = set(ALSO_RUNS.get(detected, ())) | {detected}
    if current:
        allowed.add(current)
    return [a for a in ARCHS if a in allowed]
#: One task, because one task is what this platform runs. Tracking is not
#: listed: it is not a model you upload but track ids layered onto a detection
#: model's boxes, so offering it here would ask for a file that does not exist.
TASKS = ("detection",)
#: This platform runs models on the Hailo accelerator and nowhere else, so a
#: model is a COMPILED HEF with its post-processing baked onto the chip. ONNX,
#: TFLite and Torch weights would need a compile step the board cannot do, and
#: CPU post-processing would move work off the accelerator that the whole
#: design exists to keep on it -- both were accepted here and then failed at
#: load time, which is a worse answer than refusing at upload.
POSTPROCESS = ("nms_on_chip",)
EXTENSIONS = (".hef",)
MAX_BYTES = 512 * 1024 * 1024          # a compiled HEF is tens of MB; 512MB is slack

#: The 80 COCO classes, in the exact order the Model Zoo YOLO HEFs emit them.
#: Line order IS the class index, so this order is load-bearing -- reordering it
#: silently relabels every detection. The UI offers these as the labels for any
#: COCO-trained detector, and as a checklist so an operator can keep just
#: "person" without hand-typing 80 names or guessing the spelling.
COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)

#: One-click detection defaults. Each prefills every field the runtime cannot
#: guess with a known-good value for a Hailo Model Zoo COCO YOLO HEF, so an
#: operator picks a preset, sets the arch to match their accelerator, uploads
#: the .hef, and is done -- no manifest to hand-fill. `arch` is left None so the
#: form asks (a HEF is compiled for one architecture). Labels come from
#: COCO_CLASSES, so the preset never ships without them.
#:
#: Two of them, and no more: nano for speed, small for accuracy. A third
#: preset in the same speed class (yolov6n was) only asks the operator to
#: research a choice that does not change their result.
#:
#: Nothing heavier than "small" is offered. A multi-context HEF (yolov8m and
#: up) streams part of its weights over PCIe for every inference: measured on
#: a Hailo-8, yolov8m runs 14.7 frames/s where yolov8s runs 102, and four
#: cameras sharing the chip get under 10 each -- which an operator reads as
#: the product being slow rather than as the model they picked. A heavier HEF
#: can still be uploaded with a hand-filled manifest.
PRESETS = (
    {"name": "yolov8n", "label": "YOLOv8-nano (fastest)",
     "description": "YOLOv8-nano, 80 COCO classes. Fastest; best for Hailo-8L or several cameras."},
    {"name": "yolov8s", "label": "YOLOv8-small",
     "description": "YOLOv8-small. Higher FPS for multi-camera setups."},
)


def preset(name):
    """A full manifest draft for a detection preset, or None. The UI loads this
    into the form; arch stays empty so the operator confirms it for their HEF."""
    match = next((p for p in PRESETS if p["name"] == name), None)
    if not match:
        return None
    return {
        "name": match["name"],
        "task": "detection",
        "arch": "",                       # operator sets it: one arch per HEF
        "file": f"{match['name']}.hef",
        "labels": f"{match['name']}.labels",
        "input": {"width": 640, "height": 640},
        "postprocess": "nms_on_chip",
        "confidence": 0.45,
        "classes": [],                    # empty = every class; UI offers COCO
        "description": match["description"],
        "labels_text": "\n".join(COCO_CLASSES),
    }


def _dir(config_dir, pkg_dir=None):
    """Where a system's installed models live.

    Per SYSTEM (`<pkg_dir>/models`), not per box: two systems on one board run
    different jobs and should not inherit each other's models -- installing a
    model for the counting line must not put it in the safety system's list.
    `pkg_dir=None` still resolves to the shared config/models directory, which
    is where models installed before this rule live; _legacy_dir() below lets
    those keep working.
    """
    root = pkg_dir if pkg_dir else config_mod.root(config_dir)
    path = os.path.join(str(root), "models")
    os.makedirs(path, exist_ok=True)
    return path


def _legacy_dir(config_dir):
    """The old shared location, from before models were per-system. Read-only:
    listings still surface what is there so an upgrade loses nothing, but
    nothing new is ever written to it. A legacy entry is marked shared=True so
    the page can say where it came from."""
    return os.path.join(config_mod.root(config_dir), "models")


def manifest_template(name="my_model"):
    """What the UI form fills in. Also the documentation of the format."""
    return {
        "name": name,
        "task": "detection",
        "arch": "hailo8",
        "file": f"{name}.hef",
        "labels": f"{name}.labels",
        "input": {"width": 640, "height": 640},
        "postprocess": "nms_on_chip",
        "confidence": 0.45,
        "classes": [],
        "description": "",
    }


def validate(manifest):
    """Return a cleaned manifest, or raise ValueError naming the field.

    Every message says what to do, because the person reading it is filling in
    a form about a model someone else compiled.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    name = str(manifest.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("name: lowercase letters, digits, - and _, max 40 chars")
    task = manifest.get("task")
    if task not in TASKS:
        raise ValueError(f"task must be one of {', '.join(TASKS)}")
    arch = manifest.get("arch")
    if arch not in ARCHS:
        raise ValueError(f"arch must be one of {', '.join(ARCHS)} "
                         "(a HEF compiled for one Hailo architecture will not "
                         "load on another)")
    # Default to the only supported value rather than the old "raw", so an
    # older manifest (or one posted without the field) validates instead of
    # failing on a choice the operator was never offered.
    post = manifest.get("postprocess") or "nms_on_chip"
    if post not in POSTPROCESS:
        raise ValueError(
            "post-processing runs on the Hailo accelerator, so it must be "
            "nms_on_chip - a HEF compiled for CPU post-processing will not "
            "produce detections the app can read")

    size = manifest.get("input") or {}
    try:
        width, height = int(size.get("width")), int(size.get("height"))
    except (TypeError, ValueError):
        raise ValueError("input.width and input.height are required - the size "
                         "the model was compiled for, not your camera's")
    if not (16 <= width <= 8192 and 16 <= height <= 8192):
        raise ValueError("input.width/height look wrong (16..8192)")

    confidence = float(manifest.get("confidence") or 0.45)
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")

    classes = manifest.get("classes") or []
    if not isinstance(classes, list):
        raise ValueError("classes must be a list of label names (empty = all)")

    return {
        "name": name,
        "task": task,
        "arch": arch,
        "file": str(manifest.get("file") or f"{name}.hef"),
        "labels": str(manifest.get("labels") or ""),
        "input": {"width": width, "height": height},
        "postprocess": post,
        "confidence": confidence,
        "classes": [str(c)[:60] for c in classes[:1000]],
        "description": str(manifest.get("description") or "")[:400],
        "updated_at": time.time(),
    }


def save_manifest(config_dir, manifest, pkg_dir=None):
    clean = validate(manifest)
    path = os.path.join(_dir(config_dir, pkg_dir), f"{clean['name']}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2)
    os.replace(tmp, path)
    return clean


def save_weights(config_dir, name, filename, blob, pkg_dir=None):
    """Write uploaded weights. Returns the stored filename.

    The extension is taken from the upload (a HEF and an ONNX are not
    interchangeable) but the stem is always the sanitised model name, so a
    crafted filename cannot escape the models directory.
    """
    name = str(name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("invalid model name")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in EXTENSIONS:
        raise ValueError(
            f"unsupported weights file {ext or '(none)'} - this platform runs "
            "compiled Hailo models only, so the weights must be a .hef built "
            "for this board's accelerator")
    if len(blob) > MAX_BYTES:
        raise ValueError(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not blob:
        raise ValueError("empty file")

    stored = f"{name}{ext}"
    with open(os.path.join(_dir(config_dir, pkg_dir), stored), "wb") as f:
        f.write(blob)
    return stored


def save_labels(config_dir, name, text, pkg_dir=None):
    """One class name per line. Order IS the class index -- a reordered labels
    file silently relabels every detection."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    stored = f"{name}.labels"
    with open(os.path.join(_dir(config_dir, pkg_dir), stored), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return stored, len(lines)


def _shared_weights_path(filename, arch=""):
    """Where this HEF already sits on the box, or None.

    Searches EVERY model root (config.model_search_roots), not just today's
    write target. The write target is one directory -- the NVMe when it is
    present -- while a HEF may have been fetched into any root a past release
    used. Looking only at the write target reported a downloaded, runnable
    model as "no weights", which also disabled "Use on this system" for it:
    the file was on disk the whole time, one directory over. Existence on
    disk, not preference, decides.

    `arch` is the manifest's own accelerator when it names one: a HEF is
    compiled for ONE chip, so a manifest for hailo8l is only "present" when a
    hailo8l build is -- a hailo8 file of the same name would load and fail.
    Without one, any arch folder holding the file answers, as before.
    """
    filename = os.path.basename(str(filename or ""))
    if not filename.lower().endswith(".hef"):
        return None
    from core import config as config_mod                    # noqa: PLC0415
    arch = str(arch or "").strip()
    if arch:
        # The arch build in ANY root beats a flat file in an earlier one: a
        # flat HEF says nothing about which chip it was compiled for, and the
        # manifest does.
        roots = list(config_mod.model_search_roots())
        for root in roots:
            candidate = os.path.join(root, arch, filename)
            if os.path.exists(candidate):
                return candidate
        for root in roots:
            flat = os.path.join(root, filename)
            if os.path.exists(flat):
                return flat
        return None
    for root in config_mod.model_search_roots():
        # Per-arch subfolder first (<root>/<arch>/<file>.hef, how the fetcher
        # and the app's resolver lay them out), then flat.
        try:
            for entry in sorted(os.listdir(root)):
                candidate = os.path.join(root, entry, filename)
                if os.path.isdir(os.path.join(root, entry)) \
                        and os.path.exists(candidate):
                    return candidate
        except OSError:
            pass
        flat = os.path.join(root, filename)
        if os.path.exists(flat):
            return flat
    return None


def listing(config_dir, pkg_dir=None):
    """Every model this system has, with whether its weights are on disk.

    Reads the system's own models directory, then folds in anything left in
    the old shared location that the system does not already define -- an
    upgrade must not make an operator's existing models vanish. Shared entries
    are flagged so the page can distinguish them.
    """
    out = []
    seen = set()
    directories = [(_dir(config_dir, pkg_dir), False)]
    if pkg_dir:
        directories.append((_legacy_dir(config_dir), True))
    for directory, shared in directories:
        out.extend(_listing_in(directory, shared, seen))
    return out


def _listing_in(directory, shared, seen):
    out = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return out
    for entry in entries:
        if not entry.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, entry), "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        if manifest.get("name") in seen:
            continue
        seen.add(manifest.get("name"))
        manifest["shared"] = shared
        weights = os.path.join(directory, manifest.get("file") or "")
        labels = os.path.join(directory, manifest.get("labels") or "")
        present = os.path.exists(weights)
        if not present:
            # A HEF the catalogue already fetched lives in the SHARED models
            # store, not the upload folder. A manifest naming that file has
            # its weights -- reporting "no weights" here disabled the "Use on
            # this system" button for a model that was sitting on disk.
            fetched = _shared_weights_path(manifest.get("file") or "",
                                           manifest.get("arch") or "")
            if fetched:
                weights, present = fetched, True
        manifest["_dir"] = directory
        manifest["weights_present"] = present
        manifest["weights_bytes"] = os.path.getsize(weights) if present else 0
        manifest["labels_count"] = (
            sum(1 for _ in open(labels, encoding="utf-8")) if manifest.get("labels")
            and os.path.exists(labels) else 0)
        out.append(manifest)
    return out


def get(config_dir, name, pkg_dir=None):
    for manifest in listing(config_dir, pkg_dir):
        if manifest["name"] == name:
            return manifest
    return None


def file_path(config_dir, name, what="weights", pkg_dir=None):
    """Absolute path to a model's weights or labels, or None."""
    manifest = get(config_dir, name, pkg_dir)
    if not manifest:
        return None
    filename = manifest.get("file") if what == "weights" else manifest.get("labels")
    if not filename:
        return None
    # A model may live in this system's folder or, for one installed before
    # models were per-system, the shared one -- listing() records which.
    home = manifest.get("_dir") or _dir(config_dir, pkg_dir)
    path = os.path.join(home, os.path.basename(filename))
    if os.path.exists(path):
        return path
    if what == "weights":
        return _shared_weights_path(filename, manifest.get("arch") or "")
    return None


def delete(config_dir, name, pkg_dir=None):
    """Remove manifest, weights and labels. Returns how many files went."""
    manifest = get(config_dir, name, pkg_dir)
    if not manifest:
        return 0
    # Delete from wherever the manifest actually lives. A model still in the
    # shared folder belongs to every system that has not overridden it, so
    # removing it from one system removes it from all -- the honest outcome
    # while it remains shared, and the page labels it as such.
    directory = manifest.get("_dir") or _dir(config_dir, pkg_dir)
    removed = 0
    for filename in (f"{name}.json", manifest.get("file"), manifest.get("labels")):
        if not filename:
            continue
        try:
            os.remove(os.path.join(directory, os.path.basename(filename)))
            removed += 1
        except OSError:
            pass
    return removed
