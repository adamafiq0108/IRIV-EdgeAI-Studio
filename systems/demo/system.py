# -*- coding: utf-8 -*-
"""
system.py -- the reference system package.

This is the master-side half of a system: the hub card, the nav entry, and the
analytics widgets. It ships with the product so a fresh clone runs, and so
there is always one complete, working example to copy. Real deployments keep
their own packages in their own repo (docs/conventions.md sec 1) and point
`[systems].dir` at that folder.

Pair it with `tools/mock_slave.py` for a full stack on one laptop:

    python tools/mock_slave.py --port 5101 --token mock-token
    python master_server.py --system demo --port 5110
    python hub_server.py --port 5100

What a package may do: declare. What it may not do: talk to a device, read a
database, or import anything from core beyond `system_spec`. Event data
arrives through Contract v1 from each device's agent, already merged across
every device this master polls (core/services/main.py).

Every widget below reads the standard stats keys and the standard event
envelope (docs/contract-v1.md sec 3 and 4), so it renders for any conforming
system. A widget that needs a number nobody publishes belongs in the adapter
first, as a stats key, and only then here.
"""

from core.system_spec import SystemSpec, WidgetSpec


def _events_today(events, stats):
    """A tile reads stats and returns {value, sub}. `color` is optional."""
    return {"value": stats.get("events_today", 0), "sub": "events today"}


def _violations_today(events, stats):
    return {"value": stats.get("violations_today", 0), "sub": "violations today"}


def _objects_in_frame(events, stats):
    """Live occupancy from the zone engine (app/zones.py), published by the
    adapter as a flat stats key. Zero is a real answer here, not a gap."""
    return {"value": stats.get("objects_in_frame", 0), "sub": "objects in frame now"}


def _people_through(events, stats):
    """Net count through the demo's counting line.

    Reads the line's cumulative tallies rather than a per-frame number so a
    restart of the dashboard never resets what the device has counted.
    """
    inn = stats.get("line_door_in", 0)
    out = stats.get("line_door_out", 0)
    return {"value": inn - out, "sub": f"inside now ({inn} in / {out} out)"}


def _events_by_day(events, stats):
    """A chart returns {labels, values}; both lists, same length.

    `events_by_day` is a stats bucket the adapter pre-aggregates. Devices that
    ship totals rather than rows are the normal case -- aggregating on the
    device is what lets two devices' counts for the same day become one bar.
    """
    buckets = stats.get("events_by_day") or {}
    days = sorted(buckets)[-30:]
    return {"labels": [d[5:] for d in days], "values": [buckets[d] for d in days]}


def _recent_events(events, stats):
    """A table returns {columns, rows}. Rows are already-formatted strings --
    formatting is the master's job, never the frontend's."""
    rows = [
        [e.get("ts", "")[:19].replace("T", " "), e.get("device", ""), e.get("label", "")]
        for e in sorted(events, key=lambda e: e.get("ts", ""), reverse=True)
    ]
    return {"columns": ["When", "Device", "Event"], "rows": rows[:15]}


SPEC = SystemSpec(
    slug="demo",
    display_name="Demo System",
    subtitle="REFERENCE PACKAGE",
    icon="D",
    cover="cover-a",
    order=10,
    analytics=(
        WidgetSpec(
            id="events_today",
            title="Events Today",
            kind="tile",
            data_fn=_events_today,
        ),
        WidgetSpec(
            id="violations_today",
            title="Violations Today",
            kind="tile",
            data_fn=_violations_today,
        ),
        WidgetSpec(
            # Gated on the zone capability: a device running this package
            # without zones configured simply never renders these two, rather
            # than showing a confident zero.
            id="objects_in_frame",
            title="In Frame",
            kind="tile",
            data_fn=_objects_in_frame,
            capability="settings.zones",
        ),
        WidgetSpec(
            id="people_through",
            title="Through Doorway",
            kind="tile",
            data_fn=_people_through,
            capability="settings.zones",
        ),
        WidgetSpec(
            # `capability` gates the widget: a device that doesn't report
            # analytics.daily simply never renders this one. Nothing anywhere
            # branches on the slug.
            id="events_daily",
            title="Events Over Time",
            kind="line",
            data_fn=_events_by_day,
            capability="analytics.daily",
            span=4,
        ),
        WidgetSpec(
            id="recent_events",
            title="Recent Events",
            kind="table",
            data_fn=_recent_events,
            span=4,
        ),
    ),
)
