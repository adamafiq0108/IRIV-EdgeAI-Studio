# -*- coding: utf-8 -*-
"""Which boxes a Trigger's "Recolour the bounding box" paints.

A workflow Trigger decorates a branch port or a Formula, and that rule is
an expression: it watches no zone and narrows no class. So the box colour
has always landed on EVERY object the camera sees, for as long as the
moment holds (systems/demo/app/run.py _color_rules / _stamp_colors) --
"the bay is crowded" turned the whole picture red, the people outside the
bay included.

Tok Aba (2026-10-07): "if trigger have zone, then the bounding box in the
zone only change the color if user choose that". So a Trigger with a zone
offers "Only boxes inside the zone" (node data `box_in_zone`, off by
default, which is the behaviour above unchanged). On, the compiled trigger
names the zone (`box_zone`) and the runtime paints only the objects the
zone engine counts as inside it -- the det["zones"] membership every area
zone stamps, i.e. the same anchor/footprint and class test the zone rules
count with -- and leaves the rest their normal colour.

WHICH zone, the same answer the inspector shows (pages/Workflow.jsx
zoneForBoxes): a Zone wired into the Trigger's own zone port, else the
zone of the nearest zone rule behind it. Kept out of workflow.py (at its
line ceiling) and trigger_out.py (the payload half, at the strict 300).

WHICH classes, always (Tok Aba, box .49, 2026-10-07: a Trigger on "person
in the zone" turned a chair red too -- "the color change should only be
applied to the selected object type"): the classes the rule behind the
Trigger is about, read the way the rule itself reads them -- every Class
Filter on any path back from the Trigger, plus a watch's own list
(workflow.py upstream_classes walks the same wires). The compiled trigger
carries them as `box_classes` and the runtime paints no other class, zone
or no zone. No class anywhere (a frame count with no filter) is said on
the node and paints every class, as it always did.
"""


def classes_behind(node_id, preds):
    """The classes the rule behind a Trigger narrows to, or an empty set
    for "no Class Filter anywhere behind it". Every path back, because a
    Compare may join two rules and the boxes it paints are both rules'."""
    found, seen, stack = set(), set(), [node_id]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        for _edge, src in preds.get(nid, ()) or ():
            data = src.get("data") or {}
            if src.get("type") == "filter.class":
                found.update(str(c).strip() for c in (data.get("classes") or ())
                             if str(c).strip())
            elif str(src.get("type") or "").startswith("analytics.object"):
                # A watch minted while the picker lived on the node itself.
                found.update(str(c).strip() for c in (data.get("classes") or ())
                             if str(c).strip())
            stack.append(src["id"])
    return found


def narrow(trig, data, node_id, preds, zone_by_id, warn):
    """Say on a compiled, box-painting trigger WHICH boxes: the rule's
    classes always, and the zone when "Only boxes inside the zone" is
    ticked."""
    classes = classes_behind(node_id, preds)
    if classes:
        trig["box_classes"] = sorted(classes)
    else:
        warn(node_id, "no Class Filter behind this Trigger - every class in "
                      "view is recoloured; wire one into the rule it watches")
    if data.get("box_in_zone"):
        only_inside(trig, node_id, preds, zone_by_id, warn)


def zone_behind(node_id, preds, max_hops=12):
    """The zone id a Trigger's boxes would be tested against, or "".

    The Trigger's own zone port answers first. Otherwise the walk goes back
    over the wires, breadth first so the nearest rule answers, skipping the
    Trigger's zone and OID ports (OID names who, not the moment), and the
    first Zone node it meets is the one a zone rule upstream is wired to --
    a Zone only ever wires into a zone port."""
    for edge, src in preds.get(node_id, ()) or ():
        if str(edge.get("targetHandle") or "") == "zone" \
                and src.get("type") == "zone.ref":
            return str((src.get("data") or {}).get("zone") or "")
    seen, frontier = {node_id}, [node_id]
    for _hop in range(max_hops):
        nxt = []
        for nid in frontier:
            for edge, src in preds.get(nid, ()) or ():
                if nid == node_id and str(edge.get("targetHandle") or "") \
                        in ("zone", "oid", "cur_oid", "cur_value"):
                    continue
                if src["id"] in seen:
                    continue
                seen.add(src["id"])
                if src.get("type") == "zone.ref":
                    zid = str((src.get("data") or {}).get("zone") or "")
                    if zid:
                        return zid
                    continue
                nxt.append(src["id"])
        if not nxt:
            return ""
        frontier = nxt
    return ""


def only_inside(trig, node_id, preds, zone_by_id, warn):
    """Stamp trig["box_zone"] for a Trigger with "Only boxes inside the
    zone" ticked, or say on the node why it cannot, and leave the trigger
    painting every box as before (the inspector hides the tick without a
    zone, so a stale one must not quietly paint nothing)."""
    zid = zone_behind(node_id, preds)
    zone = zone_by_id.get(zid) if zid else None
    if zone is None:
        warn(node_id, "only boxes inside the zone needs a zone - wire one "
                      "into this Trigger's Zone port; meanwhile every box "
                      "is recoloured")
    elif str(zone.get("kind") or "area") != "area":
        warn(node_id, f"'{zone.get('name') or zid}' is a line, which has no "
                      "inside - every box is recoloured; wire an area zone "
                      "into this Trigger's Zone port")
    else:
        trig["box_zone"] = zid


def stamp_camera(trig, camera, preds, node_id, zone_by_id):
    """Say on a colouring trigger WHICH camera's picture it paints
    (`paint_camera`), so the runtime hands its colour rule to that camera.

    The bug it closes (product owner, 2026-10-12, two workflows on two
    cameras): a Trigger hangs off a branch or a Formula, and those compile
    to `expr` functions, which take no camera (they read numbers, not
    frames). The app's per-camera owner test then read "no camera" as the
    FIRST camera, so a violation on camera 2 recoloured camera 1's boxes
    and camera 2's never changed. The camera is the Detection Stream behind
    the Trigger (`camera`, workflow.py upstream_camera), else the camera of
    the zone it paints or the nearest zone behind it. Nothing found (a blank
    stream, or two streams on different cameras) stamps nothing and the
    rule's own camera stands, as before."""
    if not (trig.get("box_color") or trig.get("zone_color")):
        return
    if not camera:
        for zid in (trig.get("zone_paint"), trig.get("box_zone"),
                    zone_behind(node_id, preds)):
            camera = str((zone_by_id.get(zid or "") or {}).get("camera") or "")
            if camera:
                break
    if camera:
        trig["paint_camera"] = str(camera).strip()[:60]


def clean_compiled(trig, colour, zone_colour=""):
    """The box zone and classes of an already-compiled trigger, for
    functions._validate_trigger: {} unless it recolours boxes and names
    them. Its paint camera rides along whenever it paints anything."""
    cam = str(trig.get("paint_camera") or "").strip()[:60]
    out = {"paint_camera": cam} if cam and (colour or zone_colour) else {}
    if not colour:
        return out
    zid = str(trig.get("box_zone") or "").strip()[:64]
    if zid:
        out["box_zone"] = zid
    classes = [str(c).strip()[:64] for c in (trig.get("box_classes") or ())
               if str(c).strip()][:64]
    if classes:
        out["box_classes"] = classes
    return out
