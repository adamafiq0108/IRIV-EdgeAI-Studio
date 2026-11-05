# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
dashboard_controls.py -- the dashboard cards that CHANGE something, and the
"paused" state they put on the cards after them.

Two cards beside the Inject button (builder.CONTROL_KINDS):

  ai_classes     which classes the AI model keeps. NOT a copy of anything:
                 it reads and saves the very setting the Models page's
                 Detection settings card does (detector.classes, or one
                 [[detector.models]] entry's own classes), through that
                 card's own route, /api/detector/control/params. A change on
                 either side is what the other shows on its next poll.
  class_filters  which NAMED Class Filters run (services/class_filter.py).

Each is gated on the route it calls, and the dashboard route adds whether
the person looking may use it (mark_gates) -- a viewer sees the ticks
read-only rather than ticks that answer "not allowed".
"""

from core.services import class_filter as class_filter_service

#: kind -> the capability its route requires.
GATES = {"button": "workflow.edit", "class_filters": "workflow.edit",
         "ai_classes": "models.select"}


def mark_gates(widgets, can):
    """Stamp each control with whether the viewer may use it: `can_press` on
    an Inject button (its old name), `can_edit` on the other two. `can(cap)`
    answers once per capability, not once per card."""
    asked = {}
    for w in widgets or ():
        cap = GATES.get(w.get("kind"))
        if cap and isinstance(w.get("data"), dict):
            if cap not in asked:
                asked[cap] = bool(can(cap))
            field = "can_press" if w["kind"] == "button" else "can_edit"
            w["data"][field] = asked[cap]
    return widgets


def class_choice(systems_dir, slug):
    """Per active model: every class it can emit (`labels`) and the ones it
    keeps (`classes`; empty = every class). A single-model system answers
    one entry with model "" -- the shared keys, which is what the save route
    writes when no model is named; a multi-model one answers each entry
    under its own name, since each carries its own classes."""
    from core.services import detector_control as dc           # noqa: PLC0415
    detector = dc._merged(dc._pkg_dir(systems_dir, slug)).get("detector") or {}
    hailo = detector.get("hailo") or {}
    shared = list(hailo.get("classes") or detector.get("classes") or [])
    entries = [e for e in (detector.get("models") or [])
               if isinstance(e, dict) and e.get("model")]
    if not entries:
        return {"models": [{"model": "", "labels": dc.model_labels(systems_dir, slug),
                            "name": str(hailo.get("model") or detector.get("model") or ""),
                            "classes": shared}]}
    labels = dc.model_labels_by_model(systems_dir, slug)
    return {"models": [{"model": str(e["model"]), "name": str(e["model"]),
                        "labels": labels.get(str(e["model"])) or [],
                        "classes": list(shared if e.get("classes") is None
                                        else e["classes"])}
                       for e in entries]}


def widget_data(w, systems_dir, slug):
    """The body of an ai_classes / class_filters card, or None for any other
    kind. Read fresh on every dashboard poll."""
    kind = w.get("kind")
    if kind not in ("ai_classes", "class_filters"):
        return None
    if not systems_dir or not slug:
        return {"models": []} if kind == "ai_classes" else {"filters": []}
    if kind == "ai_classes":
        # `locked` while detection is not fully stopped: the card greys its
        # ticks on this poll and opens them again on the first one after the
        # stop (core/services/class_lock.py; the route refuses regardless).
        from core.services import class_lock                   # noqa: PLC0415
        return dict(class_choice(systems_dir, slug),
                    locked=class_lock.detection_busy(systems_dir, slug),
                    locked_reason=class_lock.MESSAGE)
    t = class_filter_service.table(systems_dir, slug)
    paused = set(t["paused"])
    return {"filters": [{"node": n["node"], "name": n.get("name") or "",
                         "running": n["node"] not in paused}
                        for n in t["nodes"] if n.get("name")]}


def paused_info(systems_dir, slug):
    """{fns, all, cams, names} while a named Class Filter is paused: the
    function ids it holds back, every function and camera id (for exact key
    ownership), and fid -> the filter's name. None when nothing is paused.
    From the SETTING rather than the app's report, so a card says "paused"
    as soon as the tick is taken off. Never raises."""
    if not systems_dir or not slug:
        return None
    try:
        from core.services import detector_control as dc       # noqa: PLC0415
        t = class_filter_service.table(systems_dir, slug)
        if not t["paused"]:
            return None
        cfg = dc.system_config(systems_dir, slug) or {}
    except Exception:                                          # noqa: BLE001
        return None
    names = {}
    for n in t["nodes"]:
        if n.get("node") in t["paused"]:
            for fid in n.get("functions") or ():
                names.setdefault(str(fid), str(n.get("name") or ""))
    if not names:
        return None
    fns = {str(f.get("id") or "") for f in (cfg.get("functions") or [])
           if isinstance(f, dict)} | set(names)
    cams = {str(c.get("id") or "") for c in (cfg.get("cameras") or [])
            if isinstance(c, dict)} | {"main"}
    return {"fns": frozenset(names), "all": frozenset(i for i in fns if i),
            "cams": frozenset(c for c in cams if c), "names": names}


def mark_paused(w, data, stats, paused):
    """The AI-off treatment (builder._mark_ai_off), for one paused branch.

    A key the held-back rule no longer publishes is blanked -- never a 0,
    which reads as "nobody there" -- and a card made only of those IS the
    paused state. A key it still publishes is a KEPT tally (today's count,
    a running total): real, shown as it stands, under a "paused" badge."""
    from core.services import builder as builder_service       # noqa: PLC0415
    keys = builder_service._widget_keys(w)
    off, kept, why = [], [], ""
    for k in keys:
        owner = builder_service.key_owner(k, paused["all"], paused["cams"])
        if owner in paused["fns"]:
            why = why or paused["names"].get(owner, "")
            (kept if k in (stats or {}) else off).append(k)
    if not off and not kept:
        return data
    data = {**data, "paused_keys": off, "paused_filter": why}
    whole = bool(off) and len(off) == len(keys)
    if whole:
        data["paused"] = True
        if w.get("kind") in ("verdict", "status"):
            data.update(state="unknown", label="PAUSED", value=None)
    if not whole or w.get("kind") in builder_service.CHART_KINDS:
        data["paused_badge"] = True
    if off and isinstance(data.get("values"), list) \
            and isinstance(data.get("ids"), list):
        by_id = {str((s or {}).get("id") or ""): str((s or {}).get("key") or "")
                 for s in (w.get("series") or []) if isinstance(s, dict)}
        values, labels = list(data["values"]), list(data.get("labels") or [])
        for i, sid in enumerate(data["ids"]):
            if by_id.get(str(sid)) in off:
                values[i] = None
                if i < len(labels):
                    labels[i] = f"{labels[i]} (paused)"
        data["values"], data["labels"] = values, labels
    return data
