# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
extensions.py -- Store-template extension code, loaded from app/store_ext/.

A store template may ship Python the owner wrote: extra function evaluators
(and, later, output handlers) that the shipped rule engine does not have. The
code lands in THIS system's app/store_ext/ folder -- put there only by the
hub's store installer, which refuses any bundle whose signature does not
verify, and marked with .manifest_sha256 so a folder that appeared some other
way is not honoured.

Contract for a store_ext package:

    FUNCTIONS = {"handler_name": callable(fn, ctx) -> {suffix: number}}
    OUTPUTS   = {"handler_name": callable(output_cfg, event) -> None}

`fn` is the app's Function object (fn.params holds whatever the template's
functions carry); `ctx` is a dict with detections / tracks / zones / stats /
frame_w / frame_h / now / state, where `state` is a plain dict persisted for
that function between frames. Returned numbers are published as
fn_<id>_<suffix> stats, same as every built-in evaluator.

Failure doctrine: extension code is a GUEST. Any exception disables that
handler (logged once, fn_<id>_error stat set) -- it must never take the
detection loop down with it. Like every app module, this imports nothing
from core/.
"""

import hashlib
import importlib.util
import json
import logging
import os
import sys

log = logging.getLogger(__name__)

#: Written by the store installer beside the code, from the verified bundle's
#: manifest bytes. Its absence means the folder did not come through the
#: installer, and the loader refuses it.
MARKER = ".manifest_sha256"

_loaded = None
_broken = set()          # handler names disabled after an exception


def _ext_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "store_ext")


def load():
    """The extension module's {FUNCTIONS, OUTPUTS}, loaded once. Empty dicts
    when there is no store_ext -- the normal case, and byte-identical
    behaviour to a build without this feature."""
    global _loaded
    if _loaded is not None:
        return _loaded
    _loaded = {"functions": {}, "outputs": {}}
    ext = _ext_dir()
    init = os.path.join(ext, "__init__.py")
    if not os.path.isfile(init):
        return _loaded
    if not os.path.isfile(os.path.join(ext, MARKER)):
        log.error("store_ext/ has no %s marker - it was not placed by the "
                  "store installer, refusing to load it", MARKER)
        return _loaded
    try:
        spec = importlib.util.spec_from_file_location("store_ext", init)
        module = importlib.util.module_from_spec(spec)
        # Registered so the package's own relative imports resolve.
        sys.modules["store_ext"] = module
        spec.loader.exec_module(module)
        functions = dict(getattr(module, "FUNCTIONS", None) or {})
        outputs = dict(getattr(module, "OUTPUTS", None) or {})
        _loaded = {"functions": {str(k): v for k, v in functions.items()
                                 if callable(v)},
                   "outputs": {str(k): v for k, v in outputs.items()
                               if callable(v)}}
        log.info("store_ext loaded: %d function handler(s), %d output "
                 "handler(s)", len(_loaded["functions"]),
                 len(_loaded["outputs"]))
    except Exception:                                        # noqa: BLE001
        log.exception("store_ext failed to import - extensions disabled")
        _loaded = {"functions": {}, "outputs": {}}
    return _loaded


#: Python written in the Workflow editor. A separate folder from store_ext on
#: purpose: that one carries SIGNED template code and is verified against a
#: manifest, while this is code the box's own developer typed, written here by
#: core/services/workflow.py only when the board has opted in. Keeping them
#: apart means neither can be mistaken for the other.
WF_CODE_DIR = "wf_code"

#: Written by core/services/workflow.py: {filename: sha256}. A file that is
#: not listed, or whose bytes do not match, is NOT imported.
#:
#: The folder is an ordinary directory on a board people have shell access to,
#: and importing whatever .py happens to be sitting in it would make "drop a
#: file next to the app" equivalent to "run code inside the detection loop".
#: The hash also means an EDIT to code the editor wrote is caught, not just a
#: brand-new file.
WF_MANIFEST = ".wf_manifest.json"

_wf_cache = {}


def _wf_allowed(folder, filename):
    """True when the manifest vouches for this exact file."""
    try:
        with open(os.path.join(folder, WF_MANIFEST), "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        log.error("%s/%s missing or unreadable - refusing to import %s",
                  WF_CODE_DIR, WF_MANIFEST, filename)
        return False
    want = str((manifest or {}).get(filename) or "")
    if not want:
        log.error("%s is not in %s - refusing to import it",
                  filename, WF_MANIFEST)
        return False
    try:
        with open(os.path.join(folder, filename), "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return False
    if got != want:
        log.error("%s does not match %s - refusing to import it",
                  filename, WF_MANIFEST)
        return False
    return True


def _load_wf_handler(handler):
    """Import one workflow-written module and return its evaluate(). None when
    there is no such file -- the usual case, and not an error."""
    if handler in _wf_cache:
        return _wf_cache[handler]
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          WF_CODE_DIR)
    path = os.path.join(folder, f"{handler}.py")
    fn = None
    if os.path.isfile(path) and _wf_allowed(folder, f"{handler}.py"):
        try:
            spec = importlib.util.spec_from_file_location(
                f"wf_code.{handler}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            candidate = getattr(module, "evaluate", None)
            if callable(candidate):
                fn = candidate
            else:
                log.error("%s.py defines no evaluate(ctx)", handler)
        except Exception:                                    # noqa: BLE001
            log.exception("%s.py failed to import", handler)
    _wf_cache[handler] = fn
    return fn


#: Longest list a handler may return under one key -- the most coils one
#: Modbus request (FC 15) can write. Anything longer is truncated.
MAX_LIST = 1968


def run_function(handler, fn, ctx):
    """Call one extension evaluator. Returns {suffix: float} or None after the
    handler's first crash -- a guest that threw once stays benched, because a
    per-frame exception at 30 fps is a log flood and a stutter, not a signal."""
    if handler in _broken:
        return None
    func = load()["functions"].get(handler)
    # A wf_* handler is Python written in the editor. Its evaluate() takes
    # only ctx -- the node IS the function, so there is no second one to
    # identify -- and may return a bare number, which is what "return the
    # count" should be allowed to look like.
    wf_style = func is None and handler.startswith("wf_")
    if wf_style:
        func = _load_wf_handler(handler)
    if func is None:
        if handler not in _broken:
            _broken.add(handler)
            log.error("no function handler named %r", handler)
        return None
    try:
        out = func(ctx) if wf_style else func(fn, ctx)
        if isinstance(out, (int, float)) and not isinstance(out, bool):
            return {"value": round(float(out), 4)}
        # A bare LIST is the value too -- "return [1, 0, 1, 1]" is what a
        # Modbus Write on FC 15/16 wants, the same way msg.payload = [..] is
        # in Node-RED.
        if isinstance(out, (list, tuple)):
            out = {"value": out}
        if not isinstance(out, dict):
            return {}
        clean = {}
        for k, v in list(out.items())[:20]:
            if isinstance(v, (list, tuple)):
                # Kept as a list of numbers, capped at the most one Modbus
                # request can carry (1968 coils): the engine publishes its
                # first item as the stat and keeps the whole list beside it
                # for whatever reads lists (functions.list_stat).
                try:
                    clean[str(k)[:40]] = [round(float(x), 4)
                                          for x in list(v)[:MAX_LIST]]
                except (TypeError, ValueError):
                    continue
                continue
            try:
                clean[str(k)[:40]] = round(float(v), 4)
            except (TypeError, ValueError):
                continue
        return clean
    except Exception:                                        # noqa: BLE001
        _broken.add(handler)
        log.exception("store_ext handler %r crashed - disabled for this run",
                      handler)
        return None
