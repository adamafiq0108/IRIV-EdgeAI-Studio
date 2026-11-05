# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
model_bootstrap.py -- the detection models a box needs, present from day one.

A fresh install used to ship with NO model at all: the catalogue (yolov8n,
yolov8s in systems/demo/models.toml) was fetched only when somebody pressed a
button on the AI Models page, and the class names came from a URL on the
Hailo Model Zoo repo that now answers 404. The detector refuses to start with
a configured labels file missing (app/hailo.py _load_labels), so a new box
could not detect anything until an engineer fetched both by hand.

So, three places, one piece of code:

  install    scripts/install.sh runs `python -m core.services.model_bootstrap`
             as the service user, into config.shared_models_dir()
  update     the hub runs ensure_step() in the background at start, so a box
             that updated over the air (or was installed offline) catches up
             -- once per version, with a back-off after a failure and an Inbox
             notice saying how to fetch by hand
  selection  builtin_models writes coco.txt the moment a model is chosen, if
             it is still missing

coco.txt is WRITTEN, never downloaded: the class names are models.COCO_CLASSES,
the one list the Models page, the presets and the detector all read. A HEF is
downloaded and checked: anything under MIN_HEF_BYTES -- an HTML error page, a
truncated transfer -- is thrown away rather than left where the detector would
try to load it.
"""

import logging
import os
import threading
import time
import urllib.request

from core import config as config_mod

_log = logging.getLogger(__name__)

#: The catalogue's detection models, fetched for the box's own accelerator.
DEFAULT_MODELS = ("yolov8n", "yolov8s")
#: A real YOLO HEF is several MB (yolov8n ~ 9 MB). Anything smaller is an error
#: page or a cut-off download.
MIN_HEF_BYTES = 1024 * 1024
#: Where the class names go, beside the HEFs (what builtin_models selects).
LABELS_FILE = "coco.txt"
#: Local state: which version already has its models, and the last attempt.
STATE_NAME = "models_bootstrap"
#: After a failed fetch, how long before the next try on the same version.
RETRY_S = 6 * 3600
#: How long after hub start the attempt waits: systems starting come first.
DELAY_S = 30.0

_state = {"thread": None}


def catalogue():
    """(models, zoo) from the product's catalogue (systems/demo/models.toml)."""
    from core.services import builtin_models                   # noqa: PLC0415
    doc = builtin_models._read_toml(
        os.path.join(str(config_mod._REPO_ROOT), "systems", "demo",
                     "models.toml"))
    return doc.get("models") or {}, doc.get("zoo") or {}


def write_labels(directory):
    """Write coco.txt into `directory` from models.COCO_CLASSES when it is
    missing or empty. Returns the path. No network."""
    from core.services import models as models_service         # noqa: PLC0415
    path = os.path.join(str(directory), LABELS_FILE)
    try:
        if os.path.getsize(path) > 0:
            return path
    except OSError:
        pass
    os.makedirs(str(directory), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(models_service.COCO_CLASSES) + "\n")
    os.replace(tmp, path)
    return path


def _present(filename, arch):
    """Whether this arch's HEF already sits in ANY model root (a box may have
    fetched into an older root; existence decides, not preference)."""
    for root in config_mod.model_search_roots():
        if os.path.exists(os.path.join(root, arch, filename)):
            return True
    return False


def download_lock(dest):
    """An exclusive, non-blocking lock on `dest`'s download, across processes:
    the hub's start-up fetch and an AI Models page Download can pick the same
    model at the same moment, and two writers on one .part file make a file
    neither of them downloaded. Returns an open handle (hold it, then
    release_download_lock) or None when somebody else is already fetching."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    handle = open(dest + ".lock", "a+")
    try:
        import fcntl                                           # noqa: PLC0415
    except ImportError:                                        # pragma: no cover
        return handle                   # no flock on this OS: nothing to share
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def release_download_lock(handle):
    if handle is None:
        return
    try:
        import fcntl                                           # noqa: PLC0415
        fcntl.flock(handle, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    handle.close()


def download_hef(url, dest, opener=None):
    """Fetch one HEF to `dest`. Returns None, or why it failed. The file only
    reaches `dest` whole and plausibly sized; a short or HTML answer is
    deleted, never renamed into place. Another process already fetching the
    same file is not raced (download_lock)."""
    opener = opener or urllib.request.urlopen
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    lock = download_lock(dest)
    if lock is None:
        return f"{os.path.basename(dest)} is already being downloaded"
    try:
        return _download_locked(url, dest, opener)
    finally:
        release_download_lock(lock)


def _download_locked(url, dest, opener):
    tmp = dest + ".part"
    try:
        with opener(url, timeout=60) as r, open(tmp, "wb") as out:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
        size = os.path.getsize(tmp)
        with open(tmp, "rb") as f:
            head = f.read(64).lstrip().lower()
        if size < MIN_HEF_BYTES or head.startswith((b"<", b"{")):
            return (f"{os.path.basename(dest)} came back as {size} bytes - "
                    "not a model file (an error page or a cut-off download)")
        os.replace(tmp, dest)
        return None
    except Exception as exc:                                    # noqa: BLE001
        return f"{os.path.basename(dest)}: {exc}"
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def ensure(arch=None, target=None, names=DEFAULT_MODELS, opener=None,
           log=print):
    """Make the catalogue's detection models and coco.txt present for `arch`.

    Returns {"arch", "fetched": [...], "present": [...], "failed": {name: why},
    "labels": path}. Existing files are skipped. No accelerator, no fetch --
    a HEF is compiled for one chip, so guessing would only fetch the wrong one.
    """
    from core.services import builtin_models                   # noqa: PLC0415
    # The chip as it NAMES itself: the bus alone cannot tell an 8L from an 8,
    # and a guessed build is a HEF that will not load (identify_arch).
    arch = arch or builtin_models.identify_arch()
    target = str(target or config_mod.shared_models_dir())
    out = {"arch": arch, "fetched": [], "present": [], "failed": {},
           "labels": "",
           "unidentified": bool(not arch and builtin_models.detect_arch())}
    # The class names first, and whatever happens next: a HEF dropped in by
    # hand is useless without them, and they need no network.
    try:
        out["labels"] = write_labels(target)
    except OSError as exc:
        out["failed"][LABELS_FILE] = str(exc)
    if not arch:
        return out
    models, zoo = catalogue()
    ver = (zoo.get("vers") or {}).get(arch)
    base = zoo.get("base_url")
    for name in names:
        spec = models.get(name) or {}
        filename = spec.get("file") or f"{name}.hef"
        if _present(filename, arch):
            out["present"].append(name)
            continue
        if not ver or not base:
            out["failed"][name] = f"no zoo release for {arch} in models.toml"
            continue
        log(f"  {name} ({arch}) ...")
        why = download_hef(base.format(ver=ver, arch=arch, name=name),
                           os.path.join(target, arch, filename), opener)
        if why:
            out["failed"][name] = why
        else:
            out["fetched"].append(name)
    return out


def missing(arch=None, names=DEFAULT_MODELS):
    """What ensure() would have to do: the model names (and coco.txt) not on
    the box for `arch`. [] when nothing is missing or no arch is known."""
    from core.services import builtin_models                   # noqa: PLC0415
    arch = arch or builtin_models.detect_arch()
    if not arch:
        return []
    models, _zoo = catalogue()
    out = [n for n in names
           if not _present((models.get(n) or {}).get("file") or f"{n}.hef", arch)]
    labels_somewhere = any(
        os.path.exists(os.path.join(r, LABELS_FILE))
        for r in config_mod.model_search_roots())
    if not labels_somewhere:
        out.append(LABELS_FILE)
    return out


def _load_state(config_dir=None):
    try:
        return dict(config_mod.load_local(STATE_NAME, config_dir))
    except ValueError:
        return {}


def ensure_step(config_dir=None, version="", now=None, notify=None,
                arch=None, run=None):
    """One decision, at hub start. Returns what it did, as a word:

      no_arch  - no accelerator on this box (a laptop, a dev VM)
      present  - every catalogue model and coco.txt is already here
      backoff  - it failed recently on this version; not yet
      ok       - it fetched what was missing
      failed   - it tried and something is still missing; admins are told
    """
    from core.services import builtin_models                   # noqa: PLC0415
    now = time.time() if now is None else now
    if notify is None:
        from core.services import inbox                        # noqa: PLC0415
        notify = inbox.notify_admins
    arch = arch or builtin_models.identify_arch()
    if not arch:
        # A chip on the bus that hailortcli cannot name yet (driver still
        # loading, runtime missing): nothing fetched, nothing recorded, so the
        # next start simply asks again.
        return "unidentified" if builtin_models.detect_arch() else "no_arch"
    if not missing(arch):
        return "present"
    state = _load_state(config_dir)
    if state.get("last_version") == version \
            and state.get("last_result") == "failed" \
            and now - float(state.get("last_attempt") or 0) < RETRY_S:
        return "backoff"
    state.update(last_attempt=int(now), last_version=version,
                 last_result="running")
    config_mod.save_local(STATE_NAME, state, config_dir)
    result = (run or ensure)(arch=arch, log=_log.info)
    still = missing(arch)
    ok = not still
    state.update(last_result="ok" if ok else "failed",
                 last_message="; ".join(f"{k}: {v}" for k, v in
                                        (result.get("failed") or {}).items())[:300])
    config_mod.save_local(STATE_NAME, state, config_dir)
    if not ok:
        notify("models", "The detection models could not be downloaded",
               "This box has no " + ", ".join(still) + f" for its {arch} "
               "accelerator, so detection cannot start with them. It will be "
               "tried again in 6 hours or with the next update. To fetch now, "
               "open AI Models and press Download, or run on the box:\n"
               f"python -m core.services.model_bootstrap",
               config_dir, page="vision")
    return "ok" if ok else "failed"


def start_ensure(config_dir=None, delay=DELAY_S):
    """ensure_step once, in the background. Never blocks, never raises: a hub
    that cannot reach the Model Zoo must still come up."""
    if _state["thread"] is not None:
        return False

    def work():
        time.sleep(delay)
        try:
            from core.version import VERSION                   # noqa: PLC0415
            _log.info("[HUB] detection models: %s",
                      ensure_step(config_dir, VERSION))
        except Exception:                                      # noqa: BLE001
            _log.exception("[HUB] detection models: check failed")

    t = threading.Thread(target=work, name="model-bootstrap", daemon=True)
    _state["thread"] = t
    t.start()
    return True


def main():
    """The installer's step. Exit 0 when everything is present afterwards,
    1 when something could not be fetched (the installer warns, and goes on),
    2 when there is no accelerator to fetch for."""
    result = ensure()
    target = config_mod.shared_models_dir()
    if result["labels"]:
        print(f"  class names: {result['labels']}")
    if not result["arch"]:
        print("  the accelerator could not be identified yet (hailortcli) - "
              "no models fetched" if result.get("unidentified")
              else "  no Hailo accelerator detected - no models fetched")
        return 2
    for name in result["present"]:
        print(f"  {name} ({result['arch']}) already present")
    for name in result["fetched"]:
        print(f"  {name} ({result['arch']}) downloaded to {target}")
    for name, why in result["failed"].items():
        print(f"  {name}: {why}")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
