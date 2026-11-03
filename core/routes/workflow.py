# -*- coding: utf-8 -*-
"""
workflow.py -- the Workflow page's API: ONE graph per system.

There used to be a store of up to 50 named workflows with a management table in
front of it. That made an operator decide where a rule should live before they
could write it, and then remember -- for no benefit, because every workflow
compiled into the same detection app anyway. Now there is one canvas. A rule
that should not run is disconnected or its node disabled, not filed elsewhere.

Schema, registry, compiler and storage live in core/services/workflow.py; older
multi-workflow files are merged onto the single canvas on read (load_store).
This file resolves the system, enforces who may write, stamps identity metadata,
and turns a ValueError into a 400.

Writes are admin-only, same as functions and outputs.
"""

import re
import time

from fastapi import APIRouter, HTTPException, Request

from core.services import detector_control as dc
from core.services import builder as builder_service
from core.services import functions as functions_service
from core.services import main as main_service
from core.services import workflow as workflow_service
from core.services import auth as auth_service
from core.routes import perm
from core.services import redact as _redact
from core.services import class_filter as class_filter_service

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _system_context(request):
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        raise HTTPException(status_code=400,
                            detail="the workflow is a per-system view; open a system's dashboard")
    return systems_dir, system.slug


def _zones(systems_dir, slug):
    return dc._zone_list(dc._merged(dc._pkg_dir(systems_dir, slug)))


def _box_clock():
    """{zone, now, offset} for the BOX, never the browser.

    The zone name is read the way the operating system stores it -- the
    /etc/timezone line, or what /etc/localtime points at -- because that is
    the only place an IANA name like Asia/Kuala_Lumpur exists; Python's own
    tzname answers "+08" on the same machine, which names nothing. Falls back
    to the abbreviation, and never raises: a page that cannot say which zone
    it is in must still open.
    """
    import datetime                                          # noqa: PLC0415
    import os                                                # noqa: PLC0415
    import pathlib                                           # noqa: PLC0415

    now = datetime.datetime.now().astimezone()
    zone = ""
    try:
        zone = pathlib.Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        try:
            link = os.readlink("/etc/localtime")
            if "zoneinfo/" in link:
                zone = link.split("zoneinfo/", 1)[1]
        except OSError:
            zone = ""
    return {"zone": zone or now.tzname() or "",
            "now": now.strftime("%H:%M"),
            "offset": now.strftime("%z")}


def _camera_ids(systems_dir, slug):
    """The ids of every camera this system captures, or None on failure --
    None skips the compiler's stale-camera check rather than tripping it."""
    try:
        return {c["id"] for c in
                dc._camera_list(dc._merged(dc._pkg_dir(systems_dir, slug)))}
    except Exception:                                       # noqa: BLE001
        return None


def _classes(systems_dir, slug):
    """(every class the model can emit, the ones the keep-filter lets through).

    Same source the editor's pickers use, so a name the picker offers is never
    one the compiler rejects. (None, None) on failure -- an unreadable labels
    file must not turn every rule into an error."""
    try:
        v = functions_service.variables(systems_dir, slug)
        return (v.get("classes") or None, v.get("classes_active") or None)
    except Exception:                                       # noqa: BLE001
        return (None, None)


def _live_stats(request):
    st = request.app.state
    try:
        return main_service.aggregate_stats(st.slaves, main_service.live_local_stats(st))
    except Exception:                                       # noqa: BLE001
        return {}


def _live_values(stats):
    """Current fn_/zone_/line_/out_ stats so nodes show live values, same 4s
    polling contract as every other live page.

    out_ is what an OUTPUT reports about itself (fired / ok / at / error,
    outputs.telemetry). Without it the canvas could show every number a rule
    computed and nothing at all about whether the Telegram message, the email
    or the relay that rule fired actually went out -- the one question an
    operator cannot answer from the board.

    mqtt_ is whether each MQTT Connection is up (app/mqtt_bus.py telemetry),
    for the broker dot on every MQTT node."""
    return {k: v for k, v in stats.items()
            if k.startswith(("fn_", "zone_", "line_", "out_", "mqtt_"))}


def _runtime_mode(systems_dir, slug):
    """detection | logic | off -- the app with the AI on, the app with the AI
    off (every node that can stand alone running), or no app at all."""
    try:
        st = dc.status(systems_dir, slug)
    except Exception:                                       # noqa: BLE001
        return "off"
    return ("detection" if st.get("running")
            else "logic" if st.get("logic_running") else "off")


def _runtime_up(systems_dir, slug):
    """Is the app running this system's workflow, AI on or off? What the
    canvas's live values and an Inject's button care about, rather than
    whether DETECTION is up."""
    try:
        st = dc.status(systems_dir, slug)
    except Exception:                                       # noqa: BLE001
        return False
    return bool(st.get("app_running") or st.get("running")
                or st.get("logic_running"))


def _ai_status(systems_dir, slug):
    """{on, ok, reason}: the "AI running" dot (green = a real detector and a
    camera producing frames; red = off or failed, `reason` saying which)."""
    try:
        st = dc.status(systems_dir, slug)
    except Exception:                                       # noqa: BLE001
        return {"on": False, "ok": False, "reason": "status unavailable"}
    return {"on": bool(st.get("running")), "ok": bool(st.get("ai_ok")),
            "reason": str(st.get("ai_error") or "")}


def _filters_paused(systems_dir, slug):
    """Node ids of the Class Filters paused on the dashboard ([class_filters]
    paused); [] when unreadable -- a light, never a reason to fail the page."""
    try:
        return class_filter_service.table(systems_dir, slug)["paused"]
    except Exception:                                       # noqa: BLE001
        return []


def _get_store(systems_dir, slug):
    """The store, always holding exactly one workflow.

    No file at all synthesizes a graph from the existing [[functions]], so a
    system that was configured before the editor existed opens with its rules
    already on the canvas instead of a blank page. Nothing is written until the
    first admin save.
    """
    store = workflow_service.load_store(slug)
    if store is not None and store.get("workflows"):
        # The starter dashboards' cards live in the system package, where their
        # logic cannot be read. Adopt them onto the canvas the first time this
        # system's workflow is opened -- once done the package is empty, so
        # this is a no-op on every later visit.
        adopted = workflow_service.adopt_package_widgets(systems_dir, slug)
        w = store["workflows"][0]
        graph = w.get("graph") or {"nodes": [], "edges": []}
        seeded = _seed_example(graph, systems_dir, slug)
        # One alarm shape everywhere: legacy nodes with a baked-in alert are
        # rewritten to measure-node + connected Alert node.
        standardized = workflow_service.standardize_alerts(graph)
        # Nodes that were split by type and are now one node with a mode
        # (In Frame now/total) are rewritten in place -- same ids, wires and
        # positions, so an existing canvas keeps working untouched.
        merged = workflow_service.migrate_merged_nodes(graph)
        # A Zone that sat in series is rewired alongside: the rule now takes
        # the area on a port of its own, and the Zone has no input to pass a
        # detection stream through. Left alone, the class scope of those
        # rules would silently widen to every class the model detects.
        zoned = workflow_service.migrate_series_zones(graph)
        # An inline trigger becomes the Alert node it always compiled to, so
        # the reason a rule fires is on the canvas rather than inside a panel.
        trigs = workflow_service.migrate_inline_triggers(graph)
        # pid -> OID: the node setting, the port a wire records and a dataset
        # column's source. Silent otherwise -- the port simply stops existing.
        oid = workflow_service.migrate_pid_to_oid(graph)
        # A Cycle Time alert with no metric chosen was aimed at a head count,
        # and the default under it has moved to a clock. Written down before
        # the repair below, so the compiler refuses it rather than the graph
        # quietly meaning something else. One-shot, gated on schema version.
        dwellmetric = workflow_service.migrate_dwell_count_alerts(graph)
        # ...then the alarm's own settings move off the Condition onto an
        # Alert node. AFTER the one above, because each migration owns one
        # version step and stamps only that step: run out of order, a graph
        # two versions behind would skip the older of the two.
        splitalerts = workflow_service.migrate_split_alerts(graph)
        # An Alert pointing at a metric its rule no longer publishes: the
        # compiler corrects it and warns, every load, for ever. Written down
        # once here so the warning clears.
        remetric = workflow_service.migrate_condition_metrics(graph)
        # ...and LAST of the alarm chain: Condition/Alert/Compare/Switch become
        # the Branch family and the Trigger. Last because it consumes what the
        # migrations above normalise -- a Condition whose metric has just been
        # written down, and an Alert that has just been split out of one --
        # and rewriting the pair first would leave those with nothing to fix.
        branches = workflow_service.migrate_branch_family(graph)
        # ...and the Formula becomes the Math node for its operation. After
        # the branches: a Formula holding a COMPARISON is left where it is
        # (comparisons are a branch's job), and the branch pass is what has
        # just rewritten the nodes that used to do that comparing.
        maths = workflow_service.migrate_math_family(graph)
        # ...and the two counters become one. After everything above, because
        # the alarm chain reads a Zone Counter's own `limit` and metrics while
        # rewriting alerts, and this drops the limit key on the counters that
        # were using its default.
        counters = workflow_service.migrate_counter_merge(graph)
        # ...and the two object watches become one, whose zone port is
        # optional in the same way.
        watches = workflow_service.migrate_object_merge(graph)
        # ...and Cycle Time's five modes become three, with the two that were
        # really settings written down as settings.
        cycles = workflow_service.migrate_cycle_modes(graph)
        # ...and Compare folds into If / Else if, which is the same node with
        # one row. After the branch pass above, which is what MINTS Compares
        # out of the old Condition nodes -- run first, this would leave every
        # one of those behind.
        compares = workflow_service.migrate_compare_merge(graph)
        # ...and a Record Clip's one duration becomes the before/after pair.
        # Independent of the alarm chain above -- it touches only the output
        # node's own fields -- so where it sits in the order does not matter.
        clips = workflow_service.migrate_clip_window(graph)
        # ...and a chart's stacked fan-in moves onto its series ports, one
        # wire per port. Last of the node migrations: the ones above rewrite
        # node TYPES, and a wire spread over ports first would have to be
        # spread again for whatever they minted.
        charts = workflow_service.migrate_chart_series(graph)
        # MQTT nodes saved with their OWN broker settings get a shared MQTT
        # Connection; writes the system's config, so it runs here, once.
        from core.services import mqtt_clients as mqtt_clients_service
        mqttconns = mqtt_clients_service.migrate_graph(graph, systems_dir, slug)
        # ...and an MQTT Publish's own clock becomes an Inject wired into it.
        mqttconns = workflow_service.publish_follow.migrate(graph, slug) or mqttconns
        # Two wires between one rule and one card that mean the same thing
        # (one from before named ports): "delete one" was impossible advice.
        # The Detection Stream's and Class Filter's old Detections output is
        # their ON connection now (state_points.migrate).
        points = workflow_service.state_points.migrate(graph)
        deduped = workflow_service.prune_duplicate_wires(graph)
        # Wires to a Data Table column that was RENAMED have no port to
        # anchor to -- invisible on the canvas, undeletable by hand.
        from core.services import datasets as datasets_service
        pruned = workflow_service.prune_stale_column_wires(
            graph, datasets_service.definitions(systems_dir, slug))
        # A Class Filter the dashboard paused before the node had a switch:
        # the node says Off from now on (class_filter.adopt_paused), once.
        from core.services import class_filter as class_filter_service
        pausedmig = class_filter_service.adopt_paused(graph, systems_dir, slug)
        if points or adopted or seeded or standardized or merged or pruned or remetric or pausedmig                 or deduped or zoned or trigs or oid or dwellmetric or splitalerts                 or branches or maths or counters or watches or cycles                 or compares or clips or charts or mqttconns:
            graph["nodes"] = (graph.get("nodes") or []) + adopted
            w["graph"] = graph
            try:
                workflow_service.save_store(slug, store)
            except OSError:                                 # noqa: BLE001
                pass
        return store, bool(adopted or seeded)
    graph = workflow_service.migrate_from_config(systems_dir, slug)
    graph["nodes"] = (graph.get("nodes") or []) \
        + workflow_service.adopt_package_widgets(systems_dir, slug)
    _seed_example(graph, systems_dir, slug)
    workflow_service.standardize_alerts(graph)
    workflow_service.migrate_merged_nodes(graph)
    workflow_service.migrate_series_zones(graph)
    workflow_service.migrate_inline_triggers(graph)
    workflow_service.migrate_pid_to_oid(graph)
    workflow_service.migrate_dwell_count_alerts(graph)
    workflow_service.migrate_split_alerts(graph)
    workflow_service.migrate_condition_metrics(graph)
    workflow_service.migrate_branch_family(graph)
    workflow_service.migrate_math_family(graph)
    workflow_service.migrate_counter_merge(graph)
    workflow_service.migrate_object_merge(graph)
    workflow_service.migrate_cycle_modes(graph)
    workflow_service.migrate_compare_merge(graph)
    workflow_service.migrate_clip_window(graph)
    workflow_service.migrate_chart_series(graph)
    from core.services import mqtt_clients as mqtt_clients_service
    mqtt_clients_service.migrate_graph(graph, systems_dir, slug)
    workflow_service.publish_follow.migrate(graph)
    workflow_service.state_points.migrate(graph)
    migrated = bool(graph.get("nodes"))
    return {"version": 3, "workflows": [{
        "id": workflow_service.MAIN_ID,
        "name": workflow_service.MAIN_NAME,
        "description": "Imported from this system's existing functions" if migrated else "",
        "enabled": True, "author": "", "created": 0, "modified": 0,
        "graph": graph if migrated else {"nodes": [], "edges": []},
    }]}, migrated


def _seed_example(graph, systems_dir, slug):
    """Put a worked example on an untouched canvas. Mutates `graph`; returns
    whether it did anything.

    Only when nothing has been BUILT yet: adopted dashboard cards and comments
    do not count as building, but one analytics node means the operator is
    already working and an example would be clutter dropped into their graph.
    """
    nodes = graph.get("nodes") or []
    built = {"source", "filter", "zone", "analytics", "variable", "output"}
    if any(workflow_service.REGISTRY.get(n.get("type"), {}).get("category") in built
           for n in nodes):
        return False
    example = workflow_service.example_graph(_zones(systems_dir, slug))
    graph["nodes"] = nodes + example["nodes"]
    graph["edges"] = (graph.get("edges") or []) + example["edges"]
    return True


def _the_workflow(store):
    return store["workflows"][0]


def _require_python_rights(request, graph, previous=None):
    """Refuse a save that adds or changes Python-node code without the rights.

    Two separate gates, both here rather than in the UI, because hiding an
    editor hides a textarea and not the endpoint behind it:

      * the box must have opted in (config/workflow.toml [python].enabled),
        which takes shell access to set;
      * the account must be a DEVELOPER.

    Only CHANGED code is gated. An admin may move a Python node, rename it,
    rewire it or delete it -- what they may not do is write the code that
    runs. That keeps a shipped template usable by the people it shipped to.
    """
    cfg = request.app.state.config_dir
    was = {n["id"]: str((n.get("data") or {}).get("code") or "")
           for n in workflow_service.python_nodes(previous or {})}
    changed = [n for n in workflow_service.python_nodes(graph)
               if str((n.get("data") or {}).get("code") or "") != was.get(n["id"], "")]
    if not changed:
        return
    if not workflow_service.python_enabled(cfg):
        raise HTTPException(
            status_code=403,
            detail="this box does not run Python written in the browser - "
                   "set [python].enabled in config/workflow.toml on the board "
                   "to turn it on")
    if not auth_service.is_developer(getattr(request.state, "user", None), cfg):
        raise HTTPException(
            status_code=403,
            detail="writing Python is a developer action - an admin may use "
                   "and rewire an existing Python node, but not change its code")


def _recompile(systems_dir, config_dir, slug, store):
    """Compile the canvas; apply artifacts only when it is clean."""
    from core.services import datasets as datasets_service
    cls, active = _classes(systems_dir, slug)
    compiled = workflow_service.compile_store(
        store, _zones(systems_dir, slug), cls, active,
        dc.get_capture(systems_dir, slug),
        # The stored table registry: it decides a column's type wherever it
        # already has one, so two nodes cannot disagree about a column.
        datasets_service.definitions(systems_dir, slug),
        _camera_ids(systems_dir, slug))
    # Refused here for EVERY caller: the editor's save, card_style and the
    # recompile at start (app_supervisor.ensure_compiled); the latter two
    # draw nothing and are safe because load_store names every shape first.
    compiled["errors"] += class_filter_service.naming_errors(store=store)
    applied = False
    if not compiled["errors"]:
        try:
            # The code files first: a rule that calls a handler which is not
            # on disk yet would spend its first seconds reporting an error.
            for w in store.get("workflows") or []:
                workflow_service.write_python_code(
                    systems_dir, config_dir, slug, w.get("graph") or {})
            workflow_service.apply_artifacts(systems_dir, config_dir, slug, compiled)
            applied = True
            # The saved flow runs in the system's app whatever the AI
            # switch says: make sure that app is up (a no-op when it is).
            from core.services import app_supervisor           # noqa: PLC0415
            app_supervisor.reconcile_soon(systems_dir, slug, config_dir)
        except (ValueError, TypeError) as e:
            compiled["errors"].append({"node": "", "workflow": "", "message": str(e)})
    compiled["applied"] = applied
    return compiled


def _variables(request, systems_dir, slug, stats):
    from core.repos import events as events_repo
    from core.services import datasets as datasets_service
    from core.services import queries as queries_service
    out = {**functions_service.variables(systems_dir, slug),
           "zone_list": _zones(systems_dir, slug),
           # The tables a Data Table node may write into. Sent even when
           # empty: the palette needs to know there are none in order to say
           # so, rather than offering a node that cannot compile.
           "tables": sorted(datasets_service.definitions(systems_dir, slug).values(),
                            key=lambda t: t["name"]),
           # The SELECTs kept on the Database page, listed in the palette by
           # name: dragging one out gives a Query node that already holds the
           # statement, wired to the tables it reads.
           "saved_queries": queries_service.listed(systems_dir, slug),
           # Fields an Event Card may filter on, and the values actually seen
           # for each - typing "bay_cam" from memory is how a card ends up
           # counting nothing with no visible reason.
           "event_fields": list(events_repo.FILTERABLE),
           # Text keys are offered beside the numbers: a rule that picks a
           # word publishes one, and a picker that hides it is a picker that
           # says the value does not exist.
           "stat_keys": sorted(k for k, v in stats.items()
                               if isinstance(v, str)
                               or (isinstance(v, (int, float))
                                   and not isinstance(v, bool)))}
    try:
        out["event_values"] = events_repo.attributes(slug)
    except Exception:                                       # noqa: BLE001
        out["event_values"] = {}                # no DB: type the value instead
    return out


def _mail_accounts(config_dir=None):
    """The box's mailboxes for the Email node's picker: id, name, address.

    Never raises and never carries a password: an editor that cannot list the
    mailboxes must still open, and a credential has no reason to travel in a
    payload whose job is to describe a canvas.
    """
    try:
        from core.services import mail_accounts as mail_service
        return [{"id": a["id"], "name": a["name"], "sender": a["sender"],
                 "default": a["id"] == mail_service.default_id(config_dir)}
                for a in mail_service.load(config_dir)]
    except Exception:                                       # noqa: BLE001
        return []


def _telegram_bots(slug, config_dir=None):
    """THIS system's Telegram bot, as a list of nought or one.

    Not a picker any more. A bot belongs to exactly one system and a system to
    exactly one bot, so there is nothing for a node to choose -- and a list of
    every bot on the box was worse than useless here: it let a node on one
    system be wired to another system's bot, which nothing stopped and nothing
    reported.

    A list rather than a bare object because the editor already renders it as
    one, and "none assigned" is an ordinary state worth rendering as empty.
    Same contract as _mail_accounts otherwise: never raises, never carries the
    token.
    """
    try:
        from core.services import telegram_bots as tg_service
        bot = tg_service.for_system(slug, config_dir)
        return [{"id": bot["id"], "name": bot["name"],
                 "chat_id": bot["chat_id"], "default": True}] if bot else []
    except Exception:                                       # noqa: BLE001
        return []


def _released_board_ports():
    """Terminals handed to another program, or an empty set.

    Never raises: the editor must still load on a box with no board service,
    where the honest answer is "nothing is released".
    """
    try:
        from core.services import board_io
        return board_io.released()
    except Exception:                                       # noqa: BLE001
        return set()


@router.get("/api/workflow", name="workflow.get")
def get_workflow(request: Request):
    """The canvas, the node registry, the variables it can bind to, and the
    live value of everything it publishes -- one payload, so the editor never
    renders a graph and a vocabulary that disagree."""
    systems_dir, slug = _system_context(request)
    store, migrated = _get_store(systems_dir, slug)
    w = _the_workflow(store)
    stats = _live_stats(request)
    from core.services import datasets as datasets_service
    cls, active = _classes(systems_dir, slug)
    compiled = workflow_service.compile_store(
        store, _zones(systems_dir, slug), cls, active,
        dc.get_capture(systems_dir, slug),
        # The stored table registry: it decides a column's type wherever it
        # already has one, so two nodes cannot disagree about a column.
        datasets_service.definitions(systems_dir, slug),
        _camera_ids(systems_dir, slug))
    # zone id -> how many nodes reference it (the Zones page's delete guard).
    zone_usage = {}
    for n in (w.get("graph") or {}).get("nodes") or []:
        if n.get("type") == "zone.ref":
            z = (n.get("data") or {}).get("zone")
            if z:
                zone_usage[z] = zone_usage.get(z, 0) + 1
    return {
        "ok": True,
        "workflow": {**workflow_service.workflow_meta(w), "graph": w.get("graph"),
                     "variables": w.get("variables") or []},
        "graph": w.get("graph"),
        "zone_usage": zone_usage,
        # Whether the WORKFLOW is running (the app, AI on or off): what the
        # canvas's live values and probes mean by "the app is up".
        "app_running": _runtime_up(systems_dir, slug),
        # detection | logic (AI off, the rest running) | off: whether nodes
        # fed by a Detection Stream are marked "waits for detection".
        "runtime": _runtime_mode(systems_dir, slug),
        "waits_for_detection": compiled.get("waits_for_detection") or [],
        # The nodes after an ON/OFF point: {node id: "AI detection is off"},
        # what the canvas says they run when (state_points.py).
        "runs_when": compiled.get("runs_when") or {},
        # The "AI running" dot on the toolbar and on every Detection Stream.
        "ai": _ai_status(systems_dir, slug),
        # ...and the Class Filters paused on the dashboard: which of each
        # one's ON / OFF points is lit (also on /api/workflow/live).
        "filters_paused": _filters_paused(systems_dir, slug),
        # ...and whether the app CAN run without detection: an app copy from
        # before the AI switch runs the workflow only while detecting.
        "logic_supported": dc.unified_supported(systems_dir, slug),
        "migrated": migrated,
        "errors": compiled["errors"],
        "warnings": compiled["warnings"],
        # Compiled from the graph, so a node from before MQTT Connections
        # could carry a login here too: redacted like /api/functions.
        "functions": _redact.redact(compiled["functions"]),
        "registry": workflow_service.registry_public(),
        # Terminals handed to another program box-wide. The palette greys out
        # a board node whose every port is gone -- dragging one in would give
        # the operator a node that can only ever be idle.
        "released_ports": sorted(_released_board_ports()),
        # The box's mailboxes, so an Email node offers a picker rather than
        # asking an operator to remember an account id. Id, name and address
        # only -- never the password, which this payload has no business
        # carrying and the node has no business knowing.
        "mail_accounts": _mail_accounts(request.app.state.config_dir),
        # ...and the box's Telegram bots, on the same terms: id, name and chat
        # only, never the token.
        "telegram_bots": _telegram_bots(slug, request.app.state.config_dir),
        # The BOX's clock. A Schedule set to 12:00 means noon where the box is
        # standing, and the browser reading this page may be in another
        # country -- so the node names the zone it runs in rather than letting
        # the operator assume it is theirs.
        "clock": _box_clock(),
        "variables": _variables(request, systems_dir, slug, stats),
        "values": _live_values(stats),
        "editable": perm.editable(request, "workflow."),
    }


@router.put("/api/workflow", name="workflow.save")
async def save_workflow(request: Request):
    """Save the canvas. The graph is ALWAYS stored -- a save must never lose
    canvas work -- and artifacts are written only when it compiles clean."""
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    st = request.app.state
    body = await request.json()
    store, _ = _get_store(systems_dir, slug)
    w = _the_workflow(store)
    try:
        graph = workflow_service.clean_graph(body.get("graph") or {})
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    workflow_service.ensure_fn_ids(graph)
    _require_python_rights(request, graph, w.get("graph"))
    # A card's LOOK is the Builders' to set (card-style below); the canvas
    # carries it but may not change it, so the stored one is kept whatever the
    # browser sent -- a tab opened before the Builder restyled a card must not
    # put the old look back on its next save.
    workflow_service.keep_card_look(graph, w.get("graph"))
    # A CHART's colours (its lines', its bands') are look too, but they sit
    # inside fields the canvas still writes (the series' names, the limits),
    # so they are kept part by part: the canvas no longer offers them.
    workflow_service.keep_chart_colours(graph, w.get("graph"))
    # ...and a card's QUESTION ("Work out", "One bar per", the period) the
    # Builder changed after this tab loaded is not put back by it either.
    workflow_service.keep_builder_edits(graph, w.get("graph"))
    # A chart saved under the chart builder model is stored in its one clean
    # shape (services/chartmodel.py); an older chart is untouched.
    workflow_service.normalize_chart_nodes(graph)
    w["graph"] = graph
    # Declared variables ride beside the graph: names first, values later.
    if body.get("variables") is not None:
        try:
            w["variables"] = workflow_service.clean_variables(body["variables"])
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    if body.get("name") is not None:
        w["name"] = str(body["name"]).strip()[:60] or w["name"]
    w["modified"] = int(time.time())
    if not w.get("author"):
        w["author"] = getattr(request.state, "user", "") or ""
    workflow_service.save_store(slug, store)
    compiled = _recompile(systems_dir, st.config_dir, slug, store)
    return {"ok": not compiled["errors"], "applied": compiled["applied"],
            "graph": graph, "functions": _redact.redact(compiled["functions"]),
            "waits_for_detection": compiled.get("waits_for_detection") or [],
            "runs_when": compiled.get("runs_when") or {},
            "errors": compiled["errors"], "warnings": compiled["warnings"]}


#: What a dashboard editor may change on a workflow node: how the card LOOKS,
#: what it is CALLED, its threshold bands, -- on a chart -- what each of its
#: series is called and coloured, and the card's QUESTION as both panels show
#: it (shape, "Work out", one bar per, period, interval, rows, unit). Not its
#: metric, not its window, not which function feeds it -- those are the
#: wiring, and a dashboard page that could rewrite them would be a second
#: editor for the same thing with none of the canvas's context. The
#: list itself lives with the compiler (workflow.CARD_STYLE_FIELDS), beside
#: the look the canvas save is NOT allowed to write (keep_card_look): one
#: ownership table, read from both doors.
STYLE_FIELDS = workflow_service.CARD_STYLE_FIELDS


@router.post("/api/workflow/card-style", name="workflow.card_style")
async def card_style(request: Request):
    """Write a card's LOOK back onto the workflow node that publishes it.

    WHY THIS EXISTS. A wf_* card is rebuilt from its node on every compile, so
    a look chosen on the Analytics page could not simply live on the widget --
    it would last until the next workflow save and then vanish. The answer used
    to be an override map beside the layout, which kept the choice but left two
    places holding it: change the card on Analytics and the canvas node still
    showed the old one, change the node and the override quietly won.

    So the node holds it, both editors read it, and this route is how the
    dashboard side writes. One truth, and the two pages cannot drift.

    Only STYLE_FIELDS, and only on nodes that already exist: this is reachable
    by anyone who may edit a dashboard, which is a wider door than the canvas
    itself.
    """
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    st = request.app.state
    body = await request.json()
    cards = body.get("cards")
    if not isinstance(cards, list) or not cards:
        raise HTTPException(status_code=400, detail="no cards to style")

    store, _ = _get_store(systems_dir, slug)
    # A card naming no node (a package card, a node since deleted) is skipped,
    # not refused: the dashboard keeps its own override for those.
    changed = workflow_service.apply_card_style(store, cards)
    if not changed:
        return {"ok": True, "changed": 0}

    workflow_service.save_store(slug, store)
    # Recompiled straight away, so the layout's own copy of the card is
    # rewritten now rather than at some later save: the point of this route is
    # that the two sides agree, and agreeing in a minute is not agreeing.
    compiled = _recompile(systems_dir, st.config_dir, slug, store)
    return {"ok": not compiled["errors"], "changed": changed,
            "applied": compiled["applied"], "errors": compiled["errors"]}


@router.post("/api/workflow/compile", name="workflow.compile")
async def compile_workflow(request: Request):
    """Dry-run a graph: validate + compile, write nothing. Drives live badges
    while the operator edits."""
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        graph = workflow_service.clean_graph(body.get("graph") or {})
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    workflow_service.ensure_fn_ids(graph)
    cls, active = _classes(systems_dir, slug)
    compiled = workflow_service.compile_graph(graph, _zones(systems_dir, slug),
                                              cls, active,
                                              dc.get_capture(systems_dir, slug),
                                              cameras=_camera_ids(systems_dir,
                                                                  slug))
    compiled["errors"] += class_filter_service.naming_errors(graph)  # the live badges too
    compiled["warnings"] += _telegram_people_warnings(request, graph)
    return {"ok": not compiled["errors"], "functions": _redact.redact(compiled["functions"]),
            "errors": compiled["errors"], "warnings": compiled["warnings"]}


def _telegram_people_warnings(request, graph):
    """A Telegram node sending to linked people, on a bot nobody is linked to
    yet, sends to nobody. The compiler cannot see the links (they are config,
    not graph), so the live badges add it here."""
    nodes = [n for n in graph.get("nodes") or []
             if n.get("type") == "output.telegram"
             and (n.get("data") or {}).get("to_mode") in ("all", "selected")]
    if not nodes:
        return []
    try:
        from core.routes import telegram as tg_route          # noqa: PLC0415
        from core.services import telegram_links              # noqa: PLC0415
        bot = tg_route._bot_for_request(request)
        linked = bool(bot and telegram_links.people(
            bot, request.app.state.config_dir)["people"])
    except Exception:                                       # noqa: BLE001
        return []
    if linked:
        return []
    return [{"node": n["id"], "message": "Nobody is linked to this bot yet: they "
                                         "send /start to the bot first"}
            for n in nodes]


@router.get("/api/workflow/query-value", name="workflow.query_value")
def query_value(request: Request, fn: str = ""):
    """One number out of a Query, for the detection app's own poller.

    WHY this route exists at all: a Query is SQL, and the app that runs the
    workflow has no database. It emits events and core writes them; it has
    never held a Postgres connection and should not start now, on a box where
    it is the process doing inference. So when a Query is stored in a
    Variable, the compile gives the app a poller pointed here, and this -- the
    process that does have the database -- answers with the number.

    LOCALHOST ONLY, and that is the boundary rather than a nicety. Every other
    route on this server takes a session; this one is called by a local
    process that has none. What makes that safe is not the address alone but
    what the request can ask for: a function id, nothing else. The statement
    and the tables come from the COMPILED function document, so a caller
    cannot supply SQL, cannot widen which tables are readable, and cannot
    reach a function that no compile ever wrote. The query itself then runs
    through the same read-only role and per-system views as the console, so
    even the statement that IS there cannot write or read outside its system.
    """
    from core import net                  # a RELAYED loopback is not local (net.py)
    if not net.local_caller(request):
        raise HTTPException(status_code=403,
                            detail="this reading is served to this box only")
    systems_dir, slug = _system_context(request)
    fid = str(fn or "").strip()
    doc = next((f for f in functions_service.get_functions(systems_dir, slug)
                if f.get("id") == fid and f.get("type") == "query_value"), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="no such query variable")
    if not doc.get("enabled", True):
        raise HTTPException(status_code=409, detail="that query variable is off")
    return _query_value_of(request, systems_dir, slug, doc)


def _query_value_of(request, systems_dir, slug, doc):
    """{ok, value, ...} for one compiled query_value function: the reading the
    app's poller gets, and what Send a test now reads for a wired variable the
    live stats do not hold (yet)."""
    fid = doc.get("id")
    from core.services import sqlconsole                      # noqa: PLC0415
    p = doc.get("params") or {}
    if p.get("dataset"):
        # A TABLE COLUMN's aggregate (a Math input fed by a table): the same
        # repo aggregate a tile card runs, with the same period floor, so
        # the number a calculation reads is the number the card beside it
        # shows. No SQL comes from the document at all.
        from core.services import builder as builder_service  # noqa: PLC0415
        from core.services import datasets as datasets_service  # noqa: PLC0415
        table = datasets_service.definition(systems_dir, slug, p["dataset"]) or {}
        cols = table.get("columns") or []
        if not cols:
            return {"ok": False, "value": None,
                    "error": "the table this reads no longer exists"}
        widget = {"id": fid, "kind": "tile", "dataset": p["dataset"], "columns": cols,
                  "query": {"agg": p.get("agg") or "count",
                            "field": p.get("field") or None,
                            "range": p.get("range") or ""}}
        try:
            data = builder_service._dataset_data(widget, request.app.state.config_dir,
                                                 slug, systems_dir) or {}
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "value": None, "error": str(exc)[:200]}
        value = data.get("value")
        if value is None:
            return {"ok": False, "value": None, "error": "no rows in that period"}
        if p.get("text"):
            # A Trigger's Current OID: the stored word, as it is. Capped at
            # the column's own 500, so a whole-group cell ("k7-4, k7-5, ...")
            # reaches the guard with every id it stored.
            return {"ok": True, "value": str(value)[:500]}
        try:
            return {"ok": True, "value": float(value)}
        except (TypeError, ValueError):
            return {"ok": False, "value": None,
                    "error": "that column's latest value is not a number"}
    res = sqlconsole.scalar(slug, p.get("sql"),
                            {t["key"]: t for t in (p.get("tables") or [])
                             if t.get("key")},
                            column=p.get("column") or "",
                            pick=p.get("pick") or "first")
    if not res.get("ok"):
        # 200 with ok=false, deliberately. The poller's contract is that a
        # failed READ leaves the previous value standing and drops `ok` to 0 --
        # "no rows matched" is not "the total is zero", and an HTTP error here
        # would be indistinguishable from the master being restarted.
        return {"ok": False, "value": None,
                "error": str(res.get("error") or "the query failed")[:200]}
    return {"ok": True, "value": res.get("value"),
            "rows": res.get("rows"), "column": res.get("column")}


@router.post("/api/workflow/test-output", name="workflow.test_output")
async def test_output(request: Request):
    """Send ONE message from a node, now, with the values it has now.

    Why this is worth a button: everything between writing a message and
    reading it is invisible. The rule has to fire, the schedule has to come
    round, the window has to be open, the mailbox credentials have to be
    right -- and every one of those fails silently, at whatever hour the rule
    happens to trip, in a log nobody is watching. The first evidence is a
    report that never arrived, which is indistinguishable from nothing having
    happened.

    So this bypasses the trigger and the schedule and sends the message as
    written, with the placeholders filled from the CURRENT stats. What it
    proves is the half that stays broken quietly: recipients, credentials,
    the server, and whether the placeholders resolve to anything.

    The graph is compiled from the SAVED workflow rather than from the request
    body. A body would let a caller name their own SMTP host and recipients
    and have this box send it -- an open relay behind an editor button.
    """
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    node = re.sub(r"[^A-Za-z0-9_-]", "", str((body or {}).get("node") or ""))[:40]
    if not node:
        raise HTTPException(status_code=400, detail="which node?")

    store, _ = _get_store(systems_dir, slug)
    w = _the_workflow(store)
    from core.services import datasets as datasets_service
    cls, active = _classes(systems_dir, slug)
    compiled = workflow_service.compile_store(
        store, _zones(systems_dir, slug), cls, active,
        dc.get_capture(systems_dir, slug),
        datasets_service.definitions(systems_dir, slug),
        _camera_ids(systems_dir, slug))
    config_dir = request.app.state.config_dir
    outs = workflow_service._with_bot_settings(
        workflow_service._with_mail_settings(compiled["outputs"], slug, config_dir),
        slug, config_dir)
    doc = next((o for o in outs if o.get("id") == f"wf_{node}"), None)
    if doc is None:
        raise HTTPException(status_code=404,
                            detail="that node does not compile to a message - "
                                   "save the canvas first")
    if doc.get("type") not in ("email", "telegram"):
        raise HTTPException(status_code=400, detail="that node sends no message")

    # The placeholders, filled the way the real send fills them (Tok Aba,
    # 2026-10-14: a test showed raw braces where the 22:00 send would not).
    # Each WIRED value: what the system publishes now, else -- for a Query
    # stored in a variable -- read here the way the app's poller reads it.
    # A wired value with neither is empty text, exactly as in the app, and
    # the answer NAMES it, so an empty gap cannot pass for a zero. A name
    # nothing is wired to stays as written, as in the app, and is named too.
    stats = _live_stats(request) or {}
    values, missing = _test_values(request, systems_dir, slug,
                                   doc.get("vars") or {}, stats)
    files = [e.get("csv_name") for e in _csv_entries(doc)]
    texts = ([doc.get("subject"), doc.get("body")] if doc.get("type") == "email"
             else [doc.get("message")])
    unknown = workflow_service.unknown_placeholders(texts, doc.get("vars") or {},
                                                    files)
    # The ATTACHMENT, if a CSV node feeds this message. Built here rather than
    # left out: a test that arrives with a literal "{csv}" in the body and no
    # file is a test that proves the message can be sent and nothing about
    # what it will contain -- which is the half people are actually checking.
    # EVERY file the message attaches (two or more CSV nodes wired in), each
    # built the way the app's fetch builds it.
    made = []
    if doc.get("csv_sql"):
        for entry in _csv_entries(doc):
            try:
                made.append(_csv_payload(request.app.state.config_dir, slug, entry))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"the attached table {entry.get('csv_name') or ''} "
                           f"could not be built: {exc}")

    def fill(text):
        out = str(text or "")
        for name in (doc.get("vars") or {}):
            out = out.replace("{" + name + "}", values.get(name, ""))
        # Same tokens the app expands, so what a test shows is what arrives.
        out = csv_tokens(out, made)
        return out.replace("{time}", time.strftime("%Y-%m-%d %H:%M:%S"))

    import asyncio                                           # noqa: PLC0415
    if doc.get("type") == "telegram" and doc.get("to_mode") in ("all", "selected"):
        ok, message = await asyncio.to_thread(
            _send_test_people, request, slug, doc, fill, made)
    else:
        ok, message = await asyncio.to_thread(_send_one, doc, fill, made)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    notes = [f"value {name} not available (the variable has not been "
             "calculated yet)" for name in missing]
    notes += [f"{{{name}}} is not wired into this message and was sent as "
              "written" for name in unknown]
    return {"ok": True, "message": "; ".join([message] + notes),
            "missing": missing, "unknown": unknown}


#: How many of a Telegram node's people a TEST goes to. A test proves the
#: bot, the message and the files; fifty people getting "test" is not that.
TEST_RECIPIENTS = 3


def _send_test_people(request, slug, doc, fill, made):
    """A test of a node that sends to linked people: resolved exactly as the
    app resolves them (telegram_links.recipients, live links only), sent to
    the first TEST_RECIPIENTS, and said so with counts."""
    from core.routes import telegram as tg_route              # noqa: PLC0415
    from core.services import telegram_links                  # noqa: PLC0415
    bot = tg_route._bot_for_request(request, doc.get("account") or "")
    chats = (telegram_links.recipients(bot, doc["to_mode"], doc.get("to_people") or [],
                                       request.app.state.config_dir) if bot else [])
    if not chats:
        return False, ("Nobody is linked to this bot yet: they send /start to the "
                       "bot first" if doc["to_mode"] == "all"
                       else "none of the selected people can be reached")
    sent = failed = 0
    why = ""
    for chat in chats[:TEST_RECIPIENTS]:
        ok, message = _send_one({**doc, "chat_id": chat}, fill, made)
        if ok:
            sent += 1
        else:
            failed += 1
            why = message
    if not sent:
        return False, why
    head = f"sent to {sent} of {len(chats)} recipient{'s' if len(chats) != 1 else ''}"
    if len(chats) > TEST_RECIPIENTS:
        head += f" (a test goes to the first {TEST_RECIPIENTS})"
    if failed:
        head += f"; {failed} failed: {why}"
    return True, head


def _show(value):
    """A value as the app writes it into a message (outputs.py _num): 3, not
    3.0; yes/no for a flag."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


def _test_values(request, systems_dir, slug, wired, stats):
    """({name: text}, [names with no value]) for a test send's wired values.

    The live stats first -- the very numbers the app would fill in. A key
    they do not hold that belongs to a Query stored in a variable is read
    now, through the same code the app's poller calls (_query_value_of), so
    a test sent before the poller's first round still shows the number."""
    queries = {}
    for f in functions_service.get_functions(systems_dir, slug):
        if f.get("type") == "query_value" and f.get("enabled", True):
            queries[f"fn_{f.get('id')}_value"] = f
    values, missing = {}, []
    for name, key in wired.items():
        raw = stats.get(key)
        if raw is None and key in queries:
            try:
                got = _query_value_of(request, systems_dir, slug, queries[key])
            except Exception:                               # noqa: BLE001
                got = {}
            if got.get("ok"):
                raw = got.get("value")
        if raw is None:
            values[name] = ""
            # Both the fnId and the node's name map to one key: name it once.
            if not any(wired.get(m) == key for m in missing):
                missing.append(name)
        else:
            values[name] = _show(raw)
    return values, missing


@router.post("/api/workflow/inject", name="workflow.inject")
async def inject(request: Request):
    """Press an Inject node's button: fire it once on the RUNNING app.

    Gated like the "Send a test now" button beside it, on workflow.edit: a
    press drives real outputs -- a relay, a Modbus write, a message -- so it
    is an edit-level act, not something a viewer of the canvas may do.

    Only what is DEPLOYED can be pressed. The node is looked up in the saved
    graph and its function in the config the app is actually running; a node
    dragged on but not saved, or saved with an error, has nothing on the box
    to fire, and saying so beats a press that silently does nothing.
    """
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    node_id = re.sub(r"[^A-Za-z0-9_-]", "",
                     str((body or {}).get("node") or ""))[:40]
    if not node_id:
        raise HTTPException(status_code=400, detail="which node?")
    # press (inject once), or start / stop for a repeating Inject.
    action = str((body or {}).get("action") or "press")
    store, _ = _get_store(systems_dir, slug)
    try:
        # The runtime is checked BELOW, not here: a press with detection off
        # starts the workflow on its own rather than being refused.
        fn_id = workflow_service.inject_target(
            (_the_workflow(store) or {}).get("graph") or {},
            functions_service.get_functions(systems_dir, slug), node_id,
            True, action)
    except workflow_service.InjectRefused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    # No app (it has not come up yet, or died): bring it up, AI off, and
    # press it there. The press is written straight after; the app reads it
    # on its first ticks, well inside the freshness window, so the press is
    # not lost.
    if not _runtime_up(systems_dir, slug):
        # Through the SAME guards as every other start of the app: not for a
        # system powered off on the Hub, not while an update installs.
        from core.services import app_supervisor               # noqa: PLC0415
        word, why = app_supervisor.start_for_press(systems_dir, slug)
        if word != "started":
            raise HTTPException(status_code=409, detail=why)
    try:
        seq = dc.request_inject(systems_dir, slug, fn_id, action)
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"could not reach the app: {exc}")
    return {"ok": True, "seq": seq, "function": fn_id, "action": action}


def csv_tokens(out, made):
    """The {csv...} tokens of a message, filled from built files the way the
    app fills them (systems/*/app/outputs.py _fill): {csv} / {csv_text} /
    {csv_rows} are the FIRST file; {csv:name}, {csv_text:name} and
    {csv_rows:name} are the file with that name. {csv} is the attachment's
    NAME: the rows are already attached. An unbuilt table SAYS so rather than
    leaving raw braces in somebody's inbox, and a file with no rows says
    "(no rows)" rather than pasting a bare header."""
    def text_of(f):
        if not f:
            return "(no table available)"
        if not f["rows"]:
            return "(no rows)"
        return f["text"][:4000]
    first = made[0] if made else None
    out = str(out or "")
    for f in made or ():
        out = (out.replace("{csv:%s}" % f["name"], f["name"])
                  .replace("{csv_text:%s}" % f["name"], text_of(f))
                  .replace("{csv_rows:%s}" % f["name"], str(f["rows"])))
    out = out.replace("{csv}", first["name"] if first else "(no table available)")
    out = out.replace("{csv_text}", text_of(first))
    # Dropped from the editor, still expanded for a message written when it
    # was offered.
    return out.replace("{csv_rows}", str(first["rows"] if first else 0))


def _send_one(doc, fill, csv=None):
    """One message, synchronously, by the same route the app would take.

    `csv` is the list of built {name, text, rows} files when CSV nodes feed
    this message (one dict is taken as a list of one), and each travels as a
    FILE -- the app attaches them, so a test that only pasted the table into
    the body was testing a different message from the one that goes out at
    22:00.
    """
    files = [csv] if isinstance(csv, dict) else list(csv or [])
    if doc.get("type") == "telegram":
        import json as _json                                 # noqa: PLC0415
        import urllib.request                                # noqa: PLC0415
        token = str(doc.get("bot_token") or "").strip()
        chat = str(doc.get("chat_id") or "").strip()
        if not token or not chat:
            return False, "this Telegram node has no bot or chat id yet"
        text = fill(doc.get("message"))
        if files:
            # sendDocument with the message as the FIRST file's caption,
            # exactly as the app does it: Telegram cannot attach a file to a
            # text message after the fact, so a report with tables is one
            # document post per file, the words riding on the first.
            for i, f in enumerate(files):
                fields = {"chat_id": chat}
                if i == 0:
                    fields["caption"] = text[:1024]
                body, ctype = _multipart(fields, "document", f["name"],
                                         f["text"].encode("utf-8"), "text/csv")
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data=body, method="POST", headers={"Content-Type": ctype})
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        answer = _json.loads(resp.read(20000).decode("utf-8", "replace"))
                except Exception as exc:                     # noqa: BLE001
                    from core.services import telegram_bots as bot_service  # noqa: PLC0415
                    return False, bot_service.explain(exc)
                if not answer.get("ok"):
                    from core.services import telegram_bots as bot_service  # noqa: PLC0415
                    return False, bot_service.explain(
                        str(answer.get("description") or "Telegram refused it"))
            return True, (f"sent to chat {chat} with "
                          + ", ".join(f["name"] for f in files))
        payload = _json.dumps({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                answer = _json.loads(resp.read(20000).decode("utf-8", "replace"))
        except Exception as exc:                             # noqa: BLE001
            # The body carries the reason; urllib throws it away. One place
            # turns either into something a person can act on.
            from core.services import telegram_bots as bot_service  # noqa: PLC0415
            return False, bot_service.explain(exc)
        if not answer.get("ok"):
            from core.services import telegram_bots as bot_service  # noqa: PLC0415
            return False, bot_service.explain(
                str(answer.get("description") or "Telegram refused it"))
        return True, f"sent to chat {chat}"

    import smtplib                                            # noqa: PLC0415
    from email.message import EmailMessage                    # noqa: PLC0415
    to = doc.get("to") or []
    if isinstance(to, str):
        to = [to]
    to = [t for t in (str(x).strip() for x in to) if t]
    if not to:
        return False, "this Email node has nobody to send to yet"
    if not doc.get("smtp_host") or not doc.get("smtp_user"):
        return False, ("no mailbox is set up for this box yet - "
                       "Settings → Email")
    msg = EmailMessage()
    msg["Subject"] = fill(doc.get("subject")) or "IRIV Edge AI Studio - test"
    msg["From"] = doc.get("from_addr") or doc.get("smtp_user")
    msg["To"] = ", ".join(to)
    msg.set_content(fill(doc.get("body")) or "(this node has no message yet)")
    for f in files:
        msg.add_attachment(f["text"].encode("utf-8"), maintype="text",
                           subtype="csv", filename=f["name"])
    try:
        with smtplib.SMTP(doc["smtp_host"], int(doc.get("smtp_port") or 587),
                          timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(doc["smtp_user"], doc.get("smtp_pass") or "")
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, ("the mail server refused these credentials - Gmail "
                       "needs an App Password, not the account password "
                       "(Settings → Email)")
    except Exception as exc:                                  # noqa: BLE001
        return False, f"could not send: {exc}"
    return True, (f"sent to {', '.join(to)}"
                  + (" with " + ", ".join(f["name"] for f in files)
                     if files else ""))


def _multipart(fields, file_field, filename, blob, content_type):
    """(body, content-type) for one file plus a few text fields.

    Hand-rolled to match the app's own sender byte for byte (app/outputs.py):
    the test must post the same request the real send posts, or it is testing
    a different code path from the one it is meant to prove.
    """
    import secrets                                            # noqa: PLC0415
    boundary = "----irivai" + secrets.token_hex(8)
    out = []
    for key, value in fields.items():
        out.append(f"--{boundary}\r\n"
                   f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                   f"{value}\r\n".encode("utf-8"))
    out.append(f"--{boundary}\r\n"
               f'Content-Disposition: form-data; name="{file_field}"; '
               f'filename="{filename}"\r\n'
               f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    out.append(blob)
    out.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


@router.get("/api/workflow/query-csv", name="workflow.query_csv")
def query_csv(request: Request, out: str = "", n: str = ""):
    """A CSV node's rows, as a file, for the detection app to attach.

    Same shape and same reasoning as query-value next door: a Query is SQL,
    and the app that fires the message has no database -- it emits events and
    core writes them. So the compiled output carries the statement, and this,
    the process that does have the database, renders it.

    LOCALHOST ONLY, and the address is not the whole boundary. What the caller
    may name is an OUTPUT ID; the statement and the tables come from the
    compiled document, so it cannot supply SQL, cannot widen which tables are
    readable, and cannot reach an output no compile ever wrote. The query then
    runs through the same read-only role and per-system views as the console.

    `n` names ONE of the files on a message fed by several CSV nodes (its
    CSV node id); without it, the first file -- what an older app asks for.
    """
    from core import net                  # a RELAYED loopback is not local (net.py)
    if not net.local_caller(request):
        raise HTTPException(status_code=403,
                            detail="this file is served to this box only")
    systems_dir, slug = _system_context(request)
    return _csv_response(request, systems_dir, slug, out, n)


@router.post("/api/workflow/csv-file", name="workflow.csv_file")
async def csv_file(request: Request):
    """The same file, for the PERSON building the graph.

    Wiring a Query into a CSV node and then waiting for 22:00 to find out
    whether the attachment is the right shape is not a way to build anything.
    This is the Download button in the CSV node's inspector: the rows it would
    send, now, in the browser.

    It reads the SAVED GRAPH, not the compiled outputs, and that is the whole
    difference between this and the route above. A CSV node's file rides on the
    message it feeds, so a node not yet wired into an Email or a Telegram has
    no compiled document at all -- and "wire it into a message before you may
    look at it" is backwards: looking is how you decide what to wire it into.
    A Query and a table is enough.

    What arrives from the browser is a node id and nothing else. The statement
    still comes from the graph the compiler reads, so nobody can post SQL here
    or widen which tables are readable. Same permission as the query console,
    because it is the same read of the same rows.
    """
    perm.require(request, "database.edit")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    node = re.sub(r"[^A-Za-z0-9_-]", "", str((body or {}).get("node") or ""))[:40]
    from core.services import datasets as datasets_service     # noqa: PLC0415
    spec, why = workflow_service.csv_spec_for_node(
        workflow_service.load_store(slug), node,
        datasets_service.definitions(systems_dir, slug))
    if not spec:
        raise HTTPException(status_code=404, detail=why)
    return _render_csv(request, slug, spec)


def _csv_response(request, systems_dir, slug, out, n=""):
    """One message's attached CSV, rendered from its compiled document."""
    oid = re.sub(r"[^A-Za-z0-9_-]", "", str(out or ""))[:40]
    nid = re.sub(r"[^A-Za-z0-9_-]", "", str(n or ""))[:40]
    # By the OUTPUT that carries the file, or by the CSV NODE that made it --
    # the spec lives on the message, so either is a fair way to name it. On a
    # message with several files, `n` (or a node id as `out`) picks one.
    for o in dc.get_outputs(systems_dir, slug):
        if not o.get("csv_sql"):
            continue
        files = _csv_entries(o)
        if o.get("id") == oid:
            pick = next((e for e in files if e.get("csv_node") == nid), None)                 if nid else files[0]
        else:
            pick = next((e for e in files if e.get("csv_node") == oid), None)
        if pick is not None:
            return _render_csv(request, slug, pick)
    raise HTTPException(status_code=404, detail="no such attachment")


def _csv_entries(doc):
    """Every file a compiled message attaches, each as a single-file spec
    (csv_sql, csv_tables, csv_name, csv_rows, csv_node) that _csv_payload
    renders. `csvs` when the message has two or more; else the one."""
    many = [e for e in (doc.get("csvs") or []) if isinstance(e, dict)]
    if many:
        return [{"csv_sql": e.get("sql"), "csv_tables": e.get("tables") or [],
                 "csv_name": e.get("name"), "csv_rows": e.get("rows") or 0,
                 "csv_node": e.get("node") or ""} for e in many]
    return [doc] if doc.get("csv_sql") else []


def _csv_payload(config_dir, slug, doc):
    """{name, text, rows, truncated} for a compiled CSV spec, or ValueError.

    The one place rows become a file. The app's fetch, the inspector's Download
    button and the test send all come here, so all three get the same rows, the
    same cap and the same filename -- a test that attaches something subtly
    different from the real send is a test of nothing.
    """
    from core.services import sqlconsole                      # noqa: PLC0415
    res = sqlconsole.run(slug, doc.get("csv_sql"),
                         {t["key"]: t for t in (doc.get("csv_tables") or [])
                          if t.get("key")}, config_dir=config_dir)
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "the query failed"))
    import csv as _csv                                        # noqa: PLC0415
    import io as _io                                          # noqa: PLC0415
    columns = res.get("columns") or []
    rows = res.get("rows") or []
    limit = int(doc.get("csv_rows") or 0)
    kept = rows[:limit] if limit else rows
    buf = _io.StringIO()
    # QUOTE_MINIMAL with the default dialect: Excel and every spreadsheet on a
    # factory PC open this without being asked what the separator is.
    writer = _csv.writer(buf, lineterminator="\n")
    # Formula-looking TEXT is defused (Pipi N-5), the same OWASP rule the
    # table cards' CSV export uses (table_export.safe_cell): a cell or header
    # starting = + - @ tab or CR -- after any leading spaces or quotes -- gets
    # a leading '. Numbers, dates and every other value are written exactly
    # as before, so ordinary files are byte-identical.
    from core.services import table_export                    # noqa: PLC0415

    def safe(value):
        if value is None:
            return ""
        return table_export.safe_cell(value) if isinstance(value, str) else value
    writer.writerow([safe(c) for c in columns])
    for r in kept:
        writer.writerow([safe(r.get(c)) for c in columns])
    return {"name": str(doc.get("csv_name") or "query.csv"),
            "text": buf.getvalue(), "rows": len(kept),
            "truncated": bool(limit and len(rows) > limit)}


def _render_csv(request, slug, doc):
    """The same file as an HTTP response."""
    try:
        made = _csv_payload(request.app.state.config_dir, slug, doc)
    except ValueError as exc:
        # 400 with the reason rather than an empty file: an attachment with no
        # rows in it is indistinguishable from a quiet period, and the one
        # thing a report must not do is say "nothing happened" because the
        # query was broken.
        raise HTTPException(status_code=400, detail=str(exc))
    from fastapi.responses import Response                    # noqa: PLC0415
    return Response(
        made["text"], media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{made["name"]}"',
                 # The app reads these instead of parsing the file back, so a
                 # message can say "42 rows" without counting them twice.
                 "X-Row-Count": str(made["rows"]),
                 "X-Truncated": "1" if made["truncated"] else "0"})
