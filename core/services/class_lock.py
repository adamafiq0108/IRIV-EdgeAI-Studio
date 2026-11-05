# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
class_lock.py -- AI model classes are added or removed only while AI
detection is stopped.

Tok Aba, 2026-10-07: changing which classes the model keeps while it runs
rebuilds the detector under a live camera, and the counts and rules fed by
those classes jump mid-shift. So every route that adds or removes classes
(the AI Models page's Detection settings, the dashboard's "AI model classes"
card, a running model's manifest save) asks here first and answers 409 with
MESSAGE while detection is anything but fully stopped. The pages read the
same answer (`classes_locked`) so their controls grey out before anyone
tries, and come back on their next poll once detection has stopped.

NOT the Class Filter: pausing or editing a named filter is a workflow
setting, cheap, and stays free while detection runs.
"""

#: Said by the 409 and by the pages, word for word.
MESSAGE = "Stop AI detection to add or remove classes"


def detection_busy(systems_dir, slug):
    """True unless AI detection is FULLY stopped.

    Running, or on its way down: status() follows the switch, so Stop reads
    "stopped" the moment it is pressed while the app is still holding the
    detector until its next config poll. That gap is "stopping", and it is
    locked too -- either from a `stopping` flag status() carries, or from the
    app's own state file still saying the AI is on while the app is alive.
    Any read that fails counts as busy: refusing a class edit is cheap,
    letting one through under a live detector is the thing being prevented.
    """
    from core.services import detector_control as dc           # noqa: PLC0415
    try:
        st = dc.status(systems_dir, slug)
    except Exception:                                          # noqa: BLE001
        return True
    if st.get("running") or st.get("stopping"):
        return True
    if not st.get("app_running"):
        return False
    return dc.app_state(systems_dir, slug).get("ai_on") is True


def _clean(classes):
    """A class list as the stores keep it: stripped strings, no blanks. A
    comma string (an old manifest form) is split the same way."""
    if isinstance(classes, str):
        classes = classes.split(",")
    return [str(c).strip() for c in (classes or []) if str(c).strip()]


def current_classes(systems_dir, slug, model=None):
    """The classes kept now for `model` -- its [[detector.models]] entry when
    one is named that, else the shared keys -- the same choice
    detector_control.set_detector_params makes when it writes."""
    from core.services import detector_control as dc           # noqa: PLC0415
    detector = dc._merged(dc._pkg_dir(systems_dir, slug)).get("detector") or {}
    shared = (detector.get("hailo") or {}).get("classes") or detector.get("classes")
    for e in detector.get("models") or ():
        if isinstance(e, dict) and e.get("model") == str(model or ""):
            return _clean(shared if e.get("classes") is None else e["classes"])
    return _clean(shared)


def changes_classes(systems_dir, slug, classes, model=None):
    """Whether saving `classes` adds or removes any. None = the body does not
    touch classes (a confidence-only save), which is never a change; order is
    not a change either."""
    if classes is None:
        return False
    return set(_clean(classes)) != set(current_classes(systems_dir, slug, model))


def refuse_reason(systems_dir, slug, classes, model=None):
    """MESSAGE when this save would add or remove classes while detection is
    not stopped, else None. The class comparison runs first: a save that keeps
    the classes as they are never pays for the status read."""
    if not changes_classes(systems_dir, slug, classes, model):
        return None
    return MESSAGE if detection_busy(systems_dir, slug) else None


def flags(systems_dir, slug):
    """What GET /api/detector/control/params adds, so the AI Models page greys
    its ticks on the read it already polls, before anyone tries."""
    return {"classes_locked": detection_busy(systems_dir, slug),
            "classes_locked_reason": MESSAGE}


def _refuse(reason):
    # A 409 from the route that called: the routes file sits at its CI line
    # ceiling, so the raise lives here, the way routes/perm.require raises.
    if reason:
        from fastapi import HTTPException                       # noqa: PLC0415
        raise HTTPException(status_code=409, detail=reason)


def require_stopped(systems_dir, slug, body):
    """POST /api/detector/control/params: refuse a body that adds or removes
    classes while detection runs. Confidence alone, or the same classes
    re-sent, still saves."""
    if isinstance(body, dict):
        _refuse(refuse_reason(systems_dir, slug, body.get("classes"),
                              body.get("model")))


def require_stopped_manifest(state, body):
    """POST /api/models: the same refusal for a manifest save, which re-applies
    a RUNNING model's classes straight to the detector -- the Detection
    settings change by another door. Checked before anything is written; a
    model not in use saves as before."""
    from core.services import detector_control as dc           # noqa: PLC0415
    system = getattr(state, "system", None)
    systems_dir = getattr(state, "systems_dir", None)
    spec = (body.get("manifest") or body) if isinstance(body, dict) else None
    if not (system and systems_dir and isinstance(spec, dict)):
        return
    name = str(spec.get("name") or "")
    try:
        active = (dc.status(systems_dir, system.slug).get("detector")
                  or {}).get("model")
    except (ValueError, TypeError, OSError):
        return
    if name and active and name in str(active).split(" + "):
        _refuse(refuse_reason(systems_dir, system.slug, spec.get("classes"),
                              name))
