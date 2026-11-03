# -*- coding: utf-8 -*-
"""When an MQTT Publish publishes: when what is wired into it says so.

The node used to carry its own clock ("Every N seconds") on top of whatever
was wired in, so a Trigger set to Interval 10 s in front of a Publish set to
Every 5 s published on two clocks at once, and the Trigger's own settings
(Real-time, Interval, For N seconds, once per object) changed nothing about
when the broker heard from it. Tok Aba (2026-10-08): the interval comes ONLY
from the node in front of it, the way a Modbus Write already follows its
Trigger. One place to set it, one answer.

What each wired value compiles to (app/outputs.py _publish_follow reads it):

  fire    an Inject or a Schedule: published on each of its fires, so the
          Inject's own repeat IS the clock (and a press is one publish)
  change  anything else -- a Trigger, a Condition or Compare, a Formula, a
          plain value: published when the value changes. `beat` names the
          rule a Trigger or Condition decorates; each of its fires, and each
          Interval re-check that keeps a Trigger's payload on, publishes
          again even when the value is the same (app/functions.py
          fire_beat). So Real-time sends the payload at a fire and the idle
          payload when it lets go; Interval N sends it again every N s while
          the trigger holds; For N seconds sends it once, then the idle
          payload; once per object sends it once per new object.

Nothing wired in, it publishes nothing (the compiler warns on the node).

MIGRATION (migrate below). A node saved before this still has `interval_s`
(and maybe `payload`, what it sent with nothing wired). Wired, those keys are
dropped: the wire rules now. Unwired, it was publishing on its own clock on a
running site, and must not silently stop: an Inject repeating every N s
(once at start too, as the old clock did) is drawn in front of it, carrying
the old payload (or the time). Run on load, which writes the store once and
leaves an Inbox note, and again inside the compiler, so a graph that reaches
it unmigrated (an editor tab opened before the update) compiles the same.
"""
import logging

_log = logging.getLogger(__name__)

NODE = "output.mqtt_publish"
#: What only a node saved before this carries: its own clock and its own
#: nothing-wired payload.
LEGACY_KEYS = ("interval_s", "payload")
#: The node's canvas warning when nothing is wired in.
UNWIRED = ("nothing is wired in, so this publishes nothing - wire a Trigger "
           "or an Inject into it to publish")
#: The Inject's repeat bounds (core/services/functions.py
#: MIN/MAX_INJECT_INTERVAL_S); the old clock went down to 0.2 s.
MIN_S, MAX_S = 1.0, 86400.0


def follow(fn, clock_metric):
    """The keys that say WHEN one wired value is published (shape above).
    `clock_metric` is the compiler's CLOCK_FN_METRIC."""
    metric = clock_metric.get(fn.get("type"))
    if metric:
        return {"mode": "fire", "fired": f"fn_{fn['id']}_{metric}"}
    doc = {"mode": "change"}
    if fn.get("trigger") or fn.get("triggers"):
        doc["beat"] = fn["id"]
    return doc


def _every(raw):
    """The old clock in seconds, as the app read it (blank or 0 was 5 s),
    held to what an Inject can repeat at."""
    try:
        every = float(raw or 5)
    except (TypeError, ValueError):
        every = 5.0
    return round(min(MAX_S, max(MIN_S, every)), 1)


def _free(want, taken):
    """`want`, or `want` with x appended until nothing else has it."""
    out = want
    while out in taken:
        out = (out + "x")[-40:]
    taken.add(out)
    return out


def _slug(value):
    return "".join(c if c.isalnum() else "_"
                   for c in str(value or "").strip().lower()).strip("_")[:40]


def migrate(graph, slug="", notify=None):
    """Move every MQTT Publish's own clock onto the graph (see the module
    docstring). Mutates `graph`; returns True when it changed. With `slug`,
    says so in the log and leaves the admins an Inbox note (`notify`, the
    inbox's notify_admins by default); never raises for the note."""
    nodes, edges = (graph or {}).get("nodes"), (graph or {}).get("edges")
    if not isinstance(nodes, list):
        return False
    if not isinstance(edges, list):
        edges = graph["edges"] = []
    fed = {str(e.get("target") or "") for e in edges if isinstance(e, dict)}
    ids = {str(n.get("id") or "") for n in nodes if isinstance(n, dict)}
    edge_ids = {str(e.get("id") or "") for e in edges if isinstance(e, dict)}
    fids = {_slug((n.get("data") or {}).get("fnId")) for n in nodes
            if isinstance(n, dict)}
    changed, made = False, []
    for i, n in enumerate(list(nodes)):
        if not isinstance(n, dict) or n.get("type") != NODE:
            continue
        d = n.get("data") if isinstance(n.get("data"), dict) else {}
        if not any(k in d for k in LEGACY_KEYS):
            continue
        # A new node in the list, not an edit in place: the compiler may be
        # handed node dicts that are still the stored ones.
        nodes[i] = {**n, "data": {k: v for k, v in d.items()
                                  if k not in LEGACY_KEYS}}
        changed = True
        if str(n.get("id") or "") in fed:
            continue                        # wired: the wire rules now
        every = _every(d.get("interval_s"))
        payload = str(d.get("payload") or "")[:200]
        nid = _free(str(n.get("id") or "")[:34] + "_clock", ids)
        pos = n.get("position") if isinstance(n.get("position"), dict) else {}
        nodes.append({
            "id": nid, "type": "common.inject",
            "position": {"x": float(pos.get("x") or 0) - 240,
                         "y": float(pos.get("y") or 0)},
            "data": {"fnId": _free(("mqtt_clock_" + _slug(n.get("id")))[:40], fids),
                     "name": f"Every {every:g}s", "topic": "",
                     "ptype": "string" if payload else "timestamp",
                     "payload": payload, "once": True, "once_delay_s": 0.1,
                     "repeat": "interval", "every": every,
                     "every_unit": "seconds", "autostart": True}})
        edges.append({"id": _free(nid + "_wire", edge_ids), "source": nid,
                      "target": n["id"]})
        made.append(f"{d.get('topic') or 'its topic'} every {every:g}s")
    if made and slug:
        _log.info("[WORKFLOW] %s: MQTT Publish clock moved to an Inject: %s",
                  slug, "; ".join(made))
        try:
            if notify is None:
                from core.services import inbox             # noqa: PLC0415
                notify = inbox.notify_admins
            notify("workflow", "MQTT Publish now follows an Inject",
                   f"System '{slug}': an MQTT Publish with nothing wired in "
                   "published on its own clock. That clock is now an Inject "
                   "wired in front of it, so it keeps publishing as before ("
                   + "; ".join(made)[:300] + "). Change how often on the "
                   "Inject, or wire a Trigger instead.", page="systems")
        except Exception:                                   # noqa: BLE001
            _log.exception("[WORKFLOW] %s: MQTT Publish note not posted", slug)
    return changed
