# -*- coding: utf-8 -*-
"""
adapter.py -- the demo system's device-side half of Contract v1.

A read-only window onto what `app/` already recorded, plus an opt-in v1.2
control channel. It owns no detection logic, opens no camera, and writes
nothing except through the app's own start/stop.

    python -m agent.agent --adapter systems.demo.adapter:DemoAdapter --port 5101
    python tools/contract_check.py http://127.0.0.1:5101 --token <token>

The checker is the arbiter -- this file is done when it passes unedited.
"""

import os
import time

from agent.adapters.base import Adapter
from systems.demo.app import config as app_config
from systems.demo.app import faults
from systems.demo.app import runtime
from systems.demo.app.store import Store

PKG_DIR = os.path.dirname(os.path.abspath(__file__))


class DemoAdapter(Adapter):
    slug = "demo"
    display_name = "Demo System"
    #: Drives the nav and the widgets. Declare only what this app can serve --
    #: a capability without an implementation is a page that is always empty.
    capabilities = ("stream", "footage", "snapshots", "analytics.daily",
                    "settings.zones",
                    "control.device", "control.camera", "control.detection")

    def __init__(self, pkg_dir=None):
        self.pkg_dir = pkg_dir or PKG_DIR
        self.config = app_config.load(self.pkg_dir)
        self.data_dir = app_config.data_dir(self.config, self.pkg_dir)
        self.store = Store(self.data_dir)
        self._started = time.time()

    # -- required -----------------------------------------------------------

    def events_since(self, cursor, limit=200):
        """Exclusive, monotonic, restart-safe -- all three come free from the
        store's byte-offset cursor. A bad cursor raises ValueError there, which
        the agent turns into a 4xx (contract sec 2)."""
        return self.store.read_since(cursor, limit)

    def stats(self):
        """Standard keys plus this system's own buckets.

        Recomputed from the log on each call, which is honest and fast enough
        for a demo; a real system with millions of rows keeps counters as it
        writes and reads them here instead.
        """
        events = self.store.all_events()
        today = time.strftime("%Y-%m-%d")
        by_day, violations_by_day = {}, {}
        events_today = violations_today = violations_total = 0
        for e in events:
            day = (e.get("ts") or "")[:10]
            by_day[day] = by_day.get(day, 0) + 1
            is_violation = e.get("type") == "violation"
            if is_violation:
                violations_total += 1
                violations_by_day[day] = violations_by_day.get(day, 0) + 1
            if day == today:
                events_today += 1
                violations_today += 1 if is_violation else 0
        state = runtime.status(self.data_dir)
        out = {
            "events_today": events_today,
            "events_total": len(events),
            "violations_today": violations_today,
            "violations_total": violations_total,
            "uptime_s": int(state.get("uptime_s") or 0),
            "camera_running": bool(state.get("running")),
            # Masked even though the app now writes it masked: a state.json
            # from before 1.0.37 holds the raw source (Pipi H2).
            "camera_label": faults.mask_label(state.get("camera") or ""),
            # extra keys render generically on the dashboard; system-specific
            # numbers belong HERE, never in the event envelope
            "events_by_day": by_day,
            "violations_by_day": violations_by_day,
        }
        # Live zone counting (app/zones.py): objects_in_frame, violations_now,
        # zone_<id>_count, line_<id>_in/out/occupancy. Flat scalars on purpose --
        # that is what a Builder widget can bind to and what the master can sum
        # across devices. Merged rather than nested so a new zone needs no
        # dashboard change at all.
        for key, value in (state.get("counts") or {}).items():
            out.setdefault(key, value)
        return out

    # -- optional -----------------------------------------------------------

    def camera(self):
        state = runtime.status(self.data_dir)
        return {"label": faults.mask_label(state.get("camera") or ""),
                "running": bool(state.get("running"))}

    def app_running(self):
        """False when detection is down but the device is fine -- the whole
        reason the agent is a separate process."""
        return bool(runtime.status(self.data_dir)["running"])

    def footage_list(self, date_from=None, date_to=None, limit=200):
        """Metadata only; the bytes never leave the device until asked for."""
        rows = []
        for snap in self.store.snapshot_list():
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(snap["mtime"]))
            if date_from and ts[:10] < date_from[:10]:
                continue
            if date_to and ts[:10] > date_to[:10]:
                continue
            rows.append({"id": snap["id"], "ts": self.iso(ts), "type": "snapshot",
                         "device": None, "bytes": snap["bytes"], "mime": "image/jpeg"})
            if len(rows) >= int(limit or 200):
                break
        return rows

    def footage_bytes(self, footage_id):
        return self.store.snapshot_bytes(footage_id)

    def footage_delete(self, footage_ids):
        return self.store.delete_snapshots(footage_ids)

    def video_feed_url(self):
        """The app's existing MJPEG. The agent proxies this -- opening the
        camera here would take the second reader the hardware refuses."""
        port = (self.config.get("app") or {}).get("stream_port", 5102)
        return f"http://127.0.0.1:{port}/video_feed"

    def telemetry(self):
        """v1.3, best-effort: an unavailable metric is absent, never an error,
        and never merged into stats (the master SUMS stats)."""
        out = {}
        try:
            import psutil
        except ImportError:
            return out
        out["cpu_percent"] = psutil.cpu_percent(interval=None)
        out["mem_percent"] = psutil.virtual_memory().percent
        out["disk_percent"] = psutil.disk_usage(self.data_dir).percent
        return out

    # -- control channel (v1.2) --------------------------------------------
    # Served only because these domains are ALSO declared as capabilities
    # above. One without the other means the domain silently does not exist.

    def control_domains(self):
        return ("device", "camera", "detection")

    def control_get(self, domain):
        if domain == "device":
            state = runtime.status(self.data_dir)
            return {"settings": {}, "status": state, "meta": {"slug": self.slug},
                    "actions": [
                        {"name": "app.start", "label": "Start detection",
                         "destructive": False, "params": []},
                        {"name": "app.stop", "label": "Stop detection",
                         "destructive": True, "params": []},
                    ]}
        if domain == "camera":
            cam = dict(self.config.get("camera") or {})
            return {"settings": cam, "status": self.camera(), "actions": [], "meta": {}}
        if domain == "detection":
            det = dict(self.config.get("detector") or {})
            return {"settings": det, "status": {"kind": det.get("kind")},
                    "actions": [], "meta": {}}
        raise LookupError(f"unsupported control domain: {domain}")

    def control_set(self, domain, payload):
        """Settings changes are written to config.local.toml by a real system.
        The demo refuses rather than pretending: a 400 an operator can read
        beats a save that silently does nothing."""
        if domain not in self.control_domains():
            raise LookupError(f"unsupported control domain: {domain}")
        raise ValueError(
            "the demo adapter is read-only for settings; write config.local.toml "
            "and restart the app (app.stop then app.start)")

    def control_action(self, name, params, context=None):
        """Both actions return before the work is visible -- start spawns and
        returns, stop signals and waits only briefly (contract sec 11)."""
        if name == "app.start":
            ok, message = runtime.start(self.pkg_dir, self.data_dir)
            return {"ok": ok, "message": message}
        if name == "app.stop":
            ok, message = runtime.stop(self.data_dir)
            return {"ok": ok, "message": message}
        raise LookupError(f"unknown action: {name}")
