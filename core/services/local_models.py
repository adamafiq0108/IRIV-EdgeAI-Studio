# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
local_models.py -- models an operator drops onto the board by hand.

There is a third way a model arrives, besides the package catalogue and the
upload form: someone copies it onto the Pi. This module makes that folder a
first-class source, so a model that is already on the disk can be run without
re-uploading it through a browser.

The drop folder (default /home/pi/model, override with CYTRON_MODEL_DIR) holds
one model per entry, either way round:

    /home/pi/model/test/test.hef   +  /home/pi/model/test/test.json
    /home/pi/model/test.hef        +  /home/pi/model/test.json

The .hef is the weights; the .json describes what the runtime cannot infer --
labels, input size, confidence. Both layouts are accepted because both are what
people actually produce when they scp a model across.

Deliberately read-only: this module lists and selects, never writes into the
drop folder. It is the operator's directory, not the dashboard's.
"""

import json
import os

from core.services import detector_control as dc

#: The legacy drop folder, and the preferred one on the NVMe. Models belong on
#: the NVMe (one folder, /mnt/nvme/model, shared by built-in and hand-copied
#: models -- reads and downloads off the eMMC); the legacy folder keeps
#: working, listed right beside it, because nothing is ever moved for the
#: operator. An env override wins over both and keeps this testable.
LEGACY_MODEL_DIR = "/home/pi/model"
NVME_MODEL_DIR = os.path.join(os.environ.get("CYTRON_NVME", "/mnt/nvme"), "model")


def _default_root():
    explicit = os.environ.get("CYTRON_MODEL_DIR")
    if explicit:
        return explicit
    if os.path.isdir(NVME_MODEL_DIR):
        return NVME_MODEL_DIR
    return LEGACY_MODEL_DIR


MODEL_DIR = _default_root()


def _roots():
    """Every drop folder to scan, preferred first, no duplicates."""
    roots = [MODEL_DIR]
    if LEGACY_MODEL_DIR not in roots and os.path.isdir(LEGACY_MODEL_DIR):
        roots.append(LEGACY_MODEL_DIR)
    return roots

_ARCHS = ("hailo10h", "hailo8", "hailo8l")


def _read_json(path):
    """(manifest, error). A file that exists but does not parse returns its
    error rather than {}: "no .json" for a JSON sitting right there is the
    least helpful thing this could say, and a missing comma is by far the most
    common way these files are wrong."""
    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        return {}, f"cannot read {os.path.basename(path)}: {exc}"
    except ValueError as exc:
        return {}, f"{os.path.basename(path)} is not valid JSON - {exc}"
    return (data if isinstance(data, dict) else {}), None


def _find_manifest(folder, stem):
    """The JSON describing this HEF, wherever it reasonably sits.

    Tried in order: named after the HEF, named after the FOLDER (very common --
    HEF filenames carry arch suffixes, folders carry the model's real name),
    the conventional manifest.json / model.json, and finally the only .json in
    the folder when there is exactly one. Returns (manifest, error, path)."""
    names = [f"{stem}.json", f"{os.path.basename(folder)}.json",
             "manifest.json", "model.json"]
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = os.path.join(folder, name)
        manifest, error = _read_json(path)
        if manifest or error:
            return manifest, error, path
    try:
        loose = [n for n in sorted(os.listdir(folder)) if n.lower().endswith(".json")]
    except OSError:
        loose = []
    if len(loose) == 1:
        path = os.path.join(folder, loose[0])
        manifest, error = _read_json(path)
        return manifest, error, path
    return {}, None, None


def _labels_from(manifest, folder, stem):
    """Class names for this model: the manifest's own list, or a labels file
    sitting beside the HEF. Empty when neither exists -- the app then falls
    back to numeric class ids rather than mislabelling."""
    for key in ("labels", "classes_all", "class_names"):
        value = manifest.get(key)
        if isinstance(value, list) and value:
            return [str(v) for v in value]
        # A manifest may point at a labels FILE instead of inlining them.
        if isinstance(value, str) and value.strip():
            path = value if os.path.isabs(value) else os.path.join(folder, value)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return [line.strip() for line in f if line.strip()]
            except OSError:
                pass
    for name in (f"{stem}.txt", "labels.txt", "classes.txt"):
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        except OSError:
            continue
    return []


def _entry(hef_path, root=None):
    """One listing row for a HEF on disk, with whatever its JSON says."""
    root = root or MODEL_DIR
    folder = os.path.dirname(hef_path)
    stem = os.path.splitext(os.path.basename(hef_path))[0]
    manifest, manifest_error, manifest_path = _find_manifest(folder, stem)
    labels = _labels_from(manifest, folder, stem)
    # Two schemas use the key "classes" for opposite things. Our manifests use
    # it as a KEEP-FILTER next to a separate label list; exporter JSONs (the
    # kind that ship beside a compiled HEF, with num_classes/target) use it AS
    # the label list. When it is the only class information present, it is the
    # label list -- treating it as a filter left the model with "no labels".
    classes_filter = [str(c) for c in (manifest.get("classes") or [])]
    if not labels and classes_filter and (
            "num_classes" in manifest
            or not any(k in manifest for k in ("labels", "classes_all", "class_names"))):
        labels = classes_filter
        classes_filter = []
    try:
        size = os.path.getsize(hef_path)
    except OSError:
        size = 0
    # The architecture a HEF targets cannot be read from the file, so trust the
    # manifest and otherwise let the folder name say it (…/hailo8l/x.hef).
    arch = str(manifest.get("arch") or manifest.get("target") or "").strip().lower()
    if arch not in _ARCHS:
        parent = os.path.basename(folder).lower()
        arch = parent if parent in _ARCHS else ""
    if arch not in _ARCHS:
        # Model Zoo HEFs carry the target in the filename
        # (yolox_s_leaky_hailo10h.hef), which beats reporting "arch unknown".
        low = stem.lower()
        arch = next((a for a in _ARCHS if low.endswith("_" + a) or low.endswith(a)), "")
    inp = manifest.get("input") if isinstance(manifest.get("input"), dict) else {}
    try:
        rel = os.path.relpath(hef_path, root).replace("\\", "/")
    except ValueError:
        rel = hef_path.replace("\\", "/")
    return {
        # The HEF filename is NOT unique -- two folders routinely hold the same
        # Model Zoo file. The path under the drop folder is, so that is the id
        # everything selects by; `name` stays the display label.
        "id": rel,
        "name": stem,
        "folder_name": os.path.basename(folder) if folder != root else "",
        "path": hef_path,
        "folder": folder,
        "bytes": size,
        "arch": arch,
        "task": str(manifest.get("task") or "detection"),
        "description": str(manifest.get("description") or ""),
        "labels_count": len(labels),
        "labels": labels,
        "classes": classes_filter,
        # A Hailo postprocess JSON calls it detection_threshold; our own
        # manifests call it confidence. Accept both -- they mean the same
        # number and the operator should not have to translate.
        "confidence": (manifest.get("confidence")
                       if manifest.get("confidence") is not None
                       else manifest.get("detection_threshold")),
        "input": {"width": inp.get("width"), "height": inp.get("height")},
        "has_manifest": bool(manifest),
        "manifest_error": manifest_error,
        "manifest_file": os.path.basename(manifest_path) if manifest_path else "",
        # Hailo postprocess extras, surfaced so the page can show what the
        # model expects even though the app does not consume them yet.
        "iou_threshold": manifest.get("iou_threshold"),
        "label_offset": manifest.get("label_offset"),
        "max_boxes": manifest.get("max_boxes"),
    }


def _scan_root(root):
    """Every HEF under one drop folder (one level of folders deep), or None
    when the folder cannot be read at all."""
    hefs = []
    try:
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(path) and name.lower().endswith(".hef"):
                hefs.append(path)
            elif os.path.isdir(path):
                # One level down only: a model folder, not a whole tree.
                try:
                    for inner in sorted(os.listdir(path)):
                        if inner.lower().endswith(".hef"):
                            hefs.append(os.path.join(path, inner))
                except OSError:
                    continue
    except OSError:
        return None
    return hefs


def listing():
    """Every model across the drop folders (the preferred root plus the legacy
    /home/pi/model, so a box mid-migration shows everything it has), preferred
    root first on an id clash. Never raises: a missing folder is an empty
    list, which is what a board without one should show."""
    rows, seen, available = [], set(), False
    for root in _roots():
        hefs = _scan_root(root)
        if hefs is None:
            continue
        available = True
        for path in hefs:
            row = _entry(path, root)
            if row["id"] in seen:
                continue                 # same layout in both roots: preferred wins
            seen.add(row["id"])
            rows.append(row)
    rows.sort(key=lambda r: r["name"])
    return {"available": available, "root": MODEL_DIR, "models": rows}


def get(ref):
    """One model by id (path under the drop folder) or, failing that, by name.
    Name lookup stays for older callers but is ambiguous when two folders hold
    the same HEF filename -- id is exact."""
    rows = listing()["models"]
    for row in rows:
        if row["id"] == ref:
            return row
    matches = [r for r in rows if r["name"] == ref]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # The same HEF filename in SEVERAL folders (a copied model, a renamed
    # folder kept around). Answering None here told the operator "no
    # detection model named X" about a model the page was listing -- an
    # ambiguity is not an absence. Prefer the folder NAMED like the model
    # (the conventional layout), else the preferred root's first row.
    named = [r for r in matches
             if os.path.basename(str(r.get("folder") or "").rstrip("/\\")) == ref]
    return (named or matches)[0]


def delete(ref, in_use=()):
    """Delete one dropped HEF from disk. Returns the path removed.

    Three guards, because this removes a file the operator put on the box by
    hand rather than something this app created:

      * the model must be one LISTING found -- so the path comes from our own
        scan of the drop folders, never from the request. A caller cannot ask
        for ../../etc/anything, because nothing outside those folders is in
        the listing to begin with.
      * it must still sit inside a drop root when resolved, which catches a
        symlink pointing out of one.
      * a model currently RUNNING is refused: deleting the weights under a
        live detector turns a working system into a broken one at the next
        restart, with nothing on screen explaining why.

    The sidecar manifest and labels are deliberately left alone. They are
    small, they are not what fills a disk, and deleting a description the
    operator wrote by hand is not what "delete this model" asked for.
    """
    row = get(ref)
    if row is None:
        raise ValueError("no such model in the model folder")
    if row["name"] in set(in_use) or row["id"] in set(in_use):
        raise ValueError(f"{row['name']} is running right now - switch to "
                         "another model first, then delete this one")
    path = os.path.realpath(row["path"])
    if not any(path.startswith(os.path.realpath(root) + os.sep)
               for root in _roots()):
        raise ValueError("that model is not inside a model folder")
    try:
        os.remove(path)
    except OSError as exc:
        raise ValueError(f"could not delete it: {exc}")
    return path


@dc.locked
def select(systems_dir, slug, name):
    """Point this system's detection app at a model from the drop folder.

    Writes the same three keys the catalogue path writes -- kind, model,
    models_dir -- so the app's existing resolve_hef finds it with no new
    runtime code. Labels are materialised into the system's own data dir,
    because the app reads them from a file and the drop folder is not ours to
    write into.
    """
    row = get(name)
    if row is None:
        rows = listing()["models"]
        if any(r["name"] == name for r in rows):
            raise ValueError(
                f"{name!r} matches more than one model in {MODEL_DIR}; "
                "select it by its folder")
        raise ValueError(f"no model named {name!r} in {MODEL_DIR}")

    pkg_dir = dc._pkg_dir(systems_dir, slug)
    local = dc._read_toml(dc._local_path(pkg_dir))
    detector = dict(local.get("detector") or {})
    current_kind = (dc._merged(pkg_dir).get("detector") or {}).get("kind")
    detector["kind"] = current_kind if current_kind in ("hailo", "gstreamer") else "hailo"
    detector["model"] = row["name"]
    detector["models_dir"] = row["folder"].replace("\\", "/")

    if row["labels"]:
        data_dir = dc._data_dir(pkg_dir, dc._merged(pkg_dir))
        labels_path = os.path.join(data_dir, f"labels_{row['name']}.txt")
        with open(labels_path, "w", encoding="utf-8") as f:
            f.write("\n".join(row["labels"]) + "\n")
        detector["labels"] = labels_path.replace("\\", "/")
    if row["classes"]:
        detector["classes"] = row["classes"]
    elif row["labels"]:
        # No filter of its own: prune whatever filter the PREVIOUS model left
        # behind to this model's vocabulary. Carrying names it never emits does
        # not filter -- it silences the detector entirely.
        known = set(row["labels"])
        current = [str(c) for c in (detector.get("classes") or [])]
        kept = [c for c in current if c in known]
        if current and kept:
            detector["classes"] = kept
        elif current:
            detector.pop("classes", None)
    try:
        conf = float(row["confidence"])
        if 0 < conf < 1:
            detector["confidence"] = conf
    except (TypeError, ValueError):
        pass

    hailo = dict(detector.get("hailo") or {})
    for key in ("model", "models_dir", "labels", "classes", "confidence"):
        if key in detector:
            hailo[key] = detector[key]
        elif key == "classes":
            # Cleared above because none of its names survive this model: the
            # sub-table copy must clear too, or the stale filter comes back
            # (the app merges hailo OVER the top level).
            hailo.pop("classes", None)
    # A single selection is exclusive -- a stale [[detector.models]] would keep
    # the app in multi mode and silently ignore this choice.
    detector.pop("models", None)
    detector["hailo"] = hailo
    local["detector"] = detector
    dc._write_local(pkg_dir, local)
    return {"kind": detector["kind"], "model": row["name"], "id": row["id"],
            "models_dir": detector["models_dir"],
            "labels_count": len(row["labels"])}


# -- upload --------------------------------------------------------------------
#
# The one write this module makes, and it is the operator's own action: the
# Models page's "Upload model" hands over the pair a compile produces -- a
# .hef and a .json of the same name -- and it lands as <root>/<stem>/<stem>.hef
# + <stem>.json, which is the layout the scanner above already understands and
# the layout the guide tells people to build by hand. Nothing else in the
# folder is touched, and nothing is written until both files have been
# checked: a half-installed model is exactly what this replaces scp with.

#: A HEF larger than this is not a model anybody runs on this hardware.
MAX_HEF_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024


def _safe_stem(name):
    """The model's name, as a folder and file name. Letters, digits, `_`,
    `-` and `.` -- what the compile tools produce and nothing a path would
    read as a step up or out."""
    stem = os.path.splitext(os.path.basename(str(name or "")))[0].strip()
    if not stem or stem in (".", "..") or len(stem) > 80:
        raise ValueError("the model file needs a name")
    if any(c for c in stem if not (c.isalnum() or c in "._-")):
        raise ValueError("model name: letters, digits, dot, dash and underscore only")
    if stem.startswith("."):
        raise ValueError("model name cannot start with a dot")
    return stem


def check_pair(hef_name, manifest_name, manifest_text, arch=None):
    """Validate an upload BEFORE anything is written. Returns the parsed
    manifest and the stem, or raises ValueError in the operator's words.

    The rules are the ones the scanner and the runtime actually depend on:
    same stem on both files (that is how the sidecar is found); a JSON that
    parses to an object; a class list, because a detector with no names
    shows numbers; and, when this box's chip is known, a `target` that
    matches it -- a HEF compiled for another chip does not load, and the
    error it gives at start is not one an operator can act on.
    """
    stem = _safe_stem(hef_name)
    if not str(hef_name).lower().endswith(".hef"):
        raise ValueError("the weights file must be a .hef")
    if not str(manifest_name).lower().endswith(".json"):
        raise ValueError("the description file must be a .json")
    if _safe_stem(manifest_name) != stem:
        raise ValueError(f"the .hef and the .json must share one name: got "
                         f"{stem}.hef and {os.path.basename(str(manifest_name))}")
    if len(manifest_text) > MAX_MANIFEST_BYTES:
        raise ValueError("the .json is far larger than a model description")
    try:
        manifest = json.loads(manifest_text)
    except ValueError as exc:
        raise ValueError(f"the .json does not parse: {exc}")
    if not isinstance(manifest, dict):
        raise ValueError("the .json must be an object: { ... }")
    labels = manifest.get("classes")
    if labels is None:
        labels = manifest.get("labels") or manifest.get("class_names")
    if not isinstance(labels, list) or not labels or \
            not all(isinstance(c, str) and c.strip() for c in labels):
        raise ValueError('the .json needs "classes": a list of class names, one '
                         "per output class, in the model's order")
    n = manifest.get("num_classes")
    if n is not None and int(n) != len(labels):
        raise ValueError(f'"num_classes" says {n} but "classes" lists {len(labels)}')
    target = str(manifest.get("target") or manifest.get("arch") or "").strip().lower()
    if target and target not in _ARCHS:
        raise ValueError(f'"target" must be one of {", ".join(_ARCHS)}')
    if arch and target and target != arch:
        raise ValueError(f"this model was compiled for {target}; this box has a "
                         f"{arch}. A HEF runs only on the chip it was compiled for")
    hef_field = str(manifest.get("hef") or "")
    if hef_field and os.path.basename(hef_field) != f"{stem}.hef":
        raise ValueError(f'the .json names "hef": "{hef_field}" but the file is {stem}.hef')
    return manifest, stem


def install(stem, hef_tmp_path, manifest_text, replace=False):
    """Move a checked pair into the drop folder as <root>/<stem>/. `hef_tmp_path`
    is a file already on the same disk (the route streams the upload there),
    so the final step is a rename, never a second copy of half a gigabyte.

    Refuses to overwrite an existing model unless told to: the folder is the
    operator's, and a name clash is more often a mistake than an update.
    """
    stem = _safe_stem(stem)
    root = MODEL_DIR
    folder = os.path.join(root, stem)
    hef_dest = os.path.join(folder, f"{stem}.hef")
    json_dest = os.path.join(folder, f"{stem}.json")
    if os.path.exists(hef_dest) and not replace:
        raise ValueError(f"a model called {stem} is already in {root} - "
                         "delete it first, or upload under another name")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create {folder}: {exc}")
    if not os.access(folder, os.W_OK):
        raise ValueError(f"{folder} is not writable by the dashboard - fix its "
                         "ownership on the board, or copy the files in by hand")
    # Manifest first: it is small, and a HEF beside a missing JSON would list
    # with a warning where a JSON beside a missing HEF lists as nothing.
    tmp_json = json_dest + ".tmp"
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            f.write(manifest_text)
        os.replace(tmp_json, json_dest)
        os.replace(hef_tmp_path, hef_dest)
    except OSError as exc:
        for p in (tmp_json,):
            try:
                os.remove(p)
            except OSError:
                pass
        raise ValueError(f"could not write the model: {exc}")
    try:
        os.chmod(hef_dest, 0o644)
        os.chmod(json_dest, 0o644)
    except OSError:
        pass
    return get(f"{stem}/{stem}.hef") or get(stem)


def upload_tmp_dir():
    """Where a HEF is streamed while it uploads: beside its destination, so
    the final rename stays on one filesystem. Created on demand."""
    path = os.path.join(MODEL_DIR, ".uploads")
    os.makedirs(path, exist_ok=True)
    return path
