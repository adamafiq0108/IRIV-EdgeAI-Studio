# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""The Trigger node's Advanced settings: what it SENDS and how often.

A Trigger names a moment. Downstream of it, everything used to read the
rule's own 0/1 (`active`, the branch's `value`): on while the moment holds,
off when it lets go, and a fire on every rising edge. Tok Aba (2026-10-05)
asked for the three things Node-RED's own trigger node does on top of that:

  payload     what the output carries when it fires -- the Inject node's
              typed value (string, number, true/false, JSON, timestamp),
              cleaned by the same rule (functions.clean_inject_payload)
  idle        what it carries when it is NOT firing: 0, or a typed value
  how often   "at most once every N seconds", or "once per object" (one fire
              per tracked id, never again for the same one)
  hold        how long the payload stays on, one of three (Tok Aba,
              2026-10-05 and 2026-10-07):
              "realtime"  it follows the trigger live: on while it fires or
                          holds, idle the moment it lets go (no timer)
              "interval"  N s: on at a fire, and every N s after it the
                          trigger is checked -- still active with its value
                          unchanged keeps it on another N s, otherwise back
                          to idle. On at least N s, and as long as the
                          trigger stays as it was
              "hold"      N s: on N s, then back to idle whatever the moment
                          does meanwhile; a new trigger restarts the clock

Kept out of workflow.py on purpose: that file is the compiler, and these are
a handful of cleaning rules plus one graph walk. Everything here is OFF by
default -- a Trigger saved before this existed has none of the keys, and
`advanced()` answers None for it, so its compiled trigger is byte-for-byte
what it was (pinned in tests/smoke_triggerout.py).

The runtime half is systems/demo/app/functions.py (_parse_out, _rated and
_update_out); it publishes the output as fn_<rule>_out, which is what
everything wired after an Advanced Trigger reads (stamp_out_keys below).
"""

#: How often a Trigger may fire. "" is every time, exactly as before.
RATES = ("", "cooldown", "once_per_oid")
#: How its payload is held. Real-time is the default and writes nothing;
#: Hold compiles to the bare hold_s it always did, Interval adds
#: "hold": "interval" beside it.
HOLDS = ("realtime", "interval", "hold")
#: Longest cooldown or hold: a day. Past that it is a Schedule's question.
MAX_SECONDS = 86400


def _seconds(raw, what, low):
    """A whole-or-decimal number of seconds in [low, a day], or ValueError
    with the sentence the inspector shows."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number of seconds")
    if value != value or value < low or value > MAX_SECONDS:
        raise ValueError(f"{what} must be between {low:g} and {MAX_SECONDS} "
                         "seconds")
    return round(value, 1)


def _typed(ptype, payload, what):
    """(type, text) through the Inject's own rule, worded for this field."""
    from core.services import functions as functions_service  # noqa: PLC0415
    try:
        return functions_service.clean_inject_payload(ptype, payload)
    except ValueError as exc:
        raise ValueError(f"{what}: {exc}")


def hold_mode(d):
    """"realtime", "interval" or "hold" (or an unknown word, for advanced()
    to refuse). A Trigger saved before the choice existed has no out_hold:
    its out_hold_s answers -- 0 was "on while the moment holds", which IS
    Real-time, and N > 0 is Hold N, the very behaviour it was saved with."""
    d = d or {}
    mode = str(d.get("out_hold") or "").strip().lower()
    if mode:
        return mode
    return "hold" if _positive(d.get("out_hold_s")) else "realtime"


def is_set(d):
    """Does this Trigger's node data use any Advanced output setting?
    Real-time hold is the default, so it alone is not one."""
    d = d or {}
    return bool(str(d.get("payload_type") or "").strip()
                or str(d.get("idle_type") or "").strip()
                or str(d.get("rate") or "").strip()
                or hold_mode(d) != "realtime")


def _positive(raw):
    """A hold that is not plainly zero -- negative included, so the compile
    refuses it rather than reading it as "off"."""
    try:
        return float(raw or 0) != 0
    except (TypeError, ValueError):
        return True          # not a number: let advanced() say so


def advanced(d):
    """The compiled additions for one Trigger's node data, or None when it
    uses none of them. ValueError carries a plain sentence for the node.

    Shape (merged into the compiled trigger):
        {"out": {"type", "value", "idle_type", "idle_value", "hold_s"},
         -- hold_s absent is Real-time, hold_s alone is Hold, and
         hold_s with "hold": "interval" is Interval --
         "rate": "cooldown" | "once_per_oid", "rate_s": N}
    Only what was set is written: an absent payload means 1 (what the
    moment has always sent), an absent idle means 0, an absent rate means
    every time.
    """
    d = d or {}
    if not is_set(d):
        return None
    out = {}
    ptype = str(d.get("payload_type") or "").strip().lower()
    if ptype:
        out["type"], out["value"] = _typed(ptype, d.get("payload"),
                                           "the payload when triggered")
    itype = str(d.get("idle_type") or "").strip().lower()
    if itype:
        out["idle_type"], out["idle_value"] = _typed(
            itype, d.get("idle_payload"), "the payload when not triggered")
    mode = hold_mode(d)
    if mode not in HOLDS:
        raise ValueError("hold the payload must be real-time, re-checked "
                         "every so many seconds, or for so many seconds")
    if mode != "realtime":
        # A chosen Interval or Hold is 1 s to a day. A Hold saved before
        # the choice (no out_hold) keeps the old rule -- any positive N, the
        # old field stepped by 0.5 -- so an untouched canvas still compiles
        # to exactly what it did.
        explicit = bool(str(d.get("out_hold") or "").strip())
        what = "the hold interval" if mode == "interval" else "the hold time"
        hold = _seconds(d.get("out_hold_s") or 0, what, 1 if explicit else 0)
        if hold:
            out["hold_s"] = hold
            if mode == "interval":
                out["hold"] = "interval"
    extra = {"out": out}
    rate = str(d.get("rate") or "").strip().lower()
    if rate not in RATES:
        raise ValueError("how often it can fire must be every time, at most "
                         "once every so many seconds, or once per object")
    if rate == "cooldown":
        extra["rate"] = rate
        extra["rate_s"] = _seconds(d.get("rate_s"),
                                   "the time between two fires", 1)
    elif rate == "once_per_oid":
        extra["rate"] = rate
    return extra


def clean_compiled(trig):
    """The Advanced keys of an already-compiled trigger, re-checked for
    functions._validate_trigger: {} for a trigger that has none, so a config
    written before this existed validates to exactly what it was."""
    out = trig.get("out")
    extra = {}
    if isinstance(out, dict):
        clean = {}
        for tkey, vkey, what in (("type", "value", "the payload when triggered"),
                                 ("idle_type", "idle_value",
                                  "the payload when not triggered")):
            if out.get(tkey):
                clean[tkey], clean[vkey] = _typed(out[tkey], out.get(vkey), what)
        if out.get("hold_s"):
            clean["hold_s"] = _seconds(out["hold_s"], "the hold time", 0)
            if str(out.get("hold") or "") == "interval":
                clean["hold"] = "interval"
        extra["out"] = clean
    rate = str(trig.get("rate") or "").strip().lower()
    if rate == "cooldown":
        extra["rate"] = rate
        extra["rate_s"] = _seconds(trig.get("rate_s"),
                                   "the time between two fires", 1)
    elif rate == "once_per_oid" and str(trig.get("oid_source") or "").strip():
        extra["rate"] = rate
    return extra


#: What a 16-bit Modbus register holds (app/buses.py modbus_values: a
#: negative goes two's-complement, anything else outside this is refused).
REGISTER_RANGE = (-32768, 65535)


def register_problem(d, fc):
    """Why this Trigger's Advanced payload cannot be written to a Modbus
    REGISTER (FC 6 / 16), or "" when it can -- or when `fc` writes a coil,
    which takes anything as on/off.

    Said at compile time, on the node: the app refuses the same write at
    runtime (modbus_values), but that is one line in the log per fire, and a
    Trigger set to send "door open" into a holding register looks wired and
    armed on the canvas while it never writes anything.
    """
    try:
        if int(fc or 0) not in (6, 16):
            return ""
    except (TypeError, ValueError):
        return ""
    d = d or {}
    for tkey, vkey, what in (("payload_type", "payload", "when triggered"),
                             ("idle_type", "idle_payload", "when not triggered")):
        ptype = str(d.get(tkey) or "").strip().lower()
        if not ptype:
            continue
        shown = str(d.get(vkey) or "")[:40]
        if ptype == "string":
            return (f"the Trigger wired in sends text {what} ({shown!r}) and a "
                    f"register (FC {int(fc)}) holds a number - send a "
                    "number, or write a coil (FC 5 / 15), which reads any "
                    "payload as on or off")
        if ptype == "timestamp":
            return (f"the Trigger wired in sends the time {what}, which is far too "
                    f"large for one register (FC {int(fc)}) - send a number, "
                    "or write a coil (FC 5 / 15)")
        if ptype in ("number", "json"):
            try:
                value = float(str(d.get(vkey) or "").strip() or "x")
            except ValueError:
                if ptype == "number":
                    continue            # advanced() refuses it with its own words
                if str(d.get(vkey) or "").strip().lower() in ("true", "false"):
                    continue
                return (f"the Trigger wired in sends JSON {what} ({shown!r}) and a "
                        f"register (FC {int(fc)}) holds one number - send a "
                        "number, or write a coil (FC 5 / 15)")
            low, high = REGISTER_RANGE
            if not low <= value <= high:
                return (f"the payload {what} ({shown}) is outside what one "
                        f"register holds ({low} to {high})")
    return ""


def oid_rule_upstream(node_id, preds, nodes_ok, max_hops=12):
    """The nearest node upstream of a Trigger with OID ticked whose rule
    compiled -- the "who" Once per object counts -- or None.

    `preds` is the compiler's {node id: [(edge, src node)]}; `nodes_ok(src)`
    answers whether that node's rule compiled (it is in fn_meta). The walk
    skips the Trigger's own zone port (a place, not a who) and goes no
    further than a rule: what feeds a rule is a camera, which names nobody.
    Breadth first, so the rule nearest the Trigger answers when two do.
    """
    seen, frontier = {node_id}, [node_id]
    for _hop in range(max_hops):
        nxt = []
        for nid in frontier:
            for edge, src in preds.get(nid, ()) or ():
                # Its zone port is a place; its Current OID / Current value
                # ports lead back to a Data Table, whose writers are not this
                # moment's who (mirrors workflow.TRIGGER_SIDE_PORTS).
                if nid == node_id and str(edge.get("targetHandle") or "") \
                        in ("zone", "cur_oid", "cur_value"):
                    continue
                if src["id"] in seen:
                    continue
                seen.add(src["id"])
                if (src.get("data") or {}).get("OID") and nodes_ok(src):
                    return src
                nxt.append(src["id"])
        if not nxt:
            return None
        frontier = nxt
    return None


def stamp_out_keys(graph, preds, bases, slug, branch_alias):
    """Mark each Advanced Trigger with the stat key its output publishes,
    BEFORE any wire is resolved.

    A Formula or a Math node wired after a Trigger compiles long before the
    Trigger does (formulas are rules; the Trigger decorates one), so the key
    it reads has to be known up front -- the same reason a Set Variable's
    `_publish` is stamped early. The rule a Trigger decorates is the branch
    port or the Formula wired into it, and fn_<that rule>_out is the key.
    The Trigger compile re-stamps it from the rule it actually resolved.
    """
    for n in graph.get("nodes") or ():
        if n.get("type") != "output.trigger":
            continue
        # The compiler may be handed the saved graph itself, so a stamp from
        # an earlier compile is dropped first: a Trigger whose Advanced was
        # switched off must go back to reading exactly what it did.
        if "_out_key" in (n.get("data") or {}):
            n["data"] = {k: v for k, v in n["data"].items() if k != "_out_key"}
        if not is_set(n.get("data")):
            continue
        fid = ""
        for edge, src in preds.get(n["id"], ()) or ():
            if str(edge.get("targetHandle") or "") in ("zone", "oid", "cur_oid",
                                                       "cur_value"):
                continue
            got = branch_alias(edge, src, bases)
            if got:
                fid = got[0]
            elif src.get("type") in ("analytics.expr", "analytics.formula") \
                    or str(src.get("type") or "").startswith("math."):
                fid = slug((src.get("data") or {}).get("fnId"))
            break
        if fid:
            n["data"] = {**(n.get("data") or {}), "_out_key": f"fn_{fid}_out"}
