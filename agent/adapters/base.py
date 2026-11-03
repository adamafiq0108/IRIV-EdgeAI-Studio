# -*- coding: utf-8 -*-
"""
base.py -- The interface every system adapter implements.

An adapter is a read-only window onto whatever a system already records. It
owns no detection logic, opens no camera, and writes nothing. Subclass this,
implement five methods, and agent.py serves Contract v1 on top of it.

See docs/contract-v1.md. Conformance is decided by tools/contract_check.py,
not by this docstring.
"""

import datetime


class Adapter:
    """Base class for system adapters.

    Subclasses set the class attributes and implement the abstract methods.
    Everything else -- routing, auth, pairing, JSON shaping -- is agent.py's job.
    """

    #: lowercase/underscore, matches the deployment's system package folder
    slug = ""
    #: human name for the hub card
    display_name = ""
    #: see docs/contract-v1.md sec 6. Drives nav; nothing branches on slug.
    capabilities = ()

    # -- required -----------------------------------------------------------

    def events_since(self, cursor, limit=200):
        """Return (events, next_cursor).

        `cursor` is None on first call, else whatever next_cursor you last
        returned. Rules that the harness enforces (contract-v1.md sec 2):

          - Exclusive: return events strictly AFTER cursor. Returning the
            cursor's own event again makes the master double-count forever.
          - Monotonic, never reused, never reordered.
          - Stable across restarts: derive it from durable storage (a row id,
            a byte offset), never from a counter in memory.
          - No events left: echo `cursor` back as next_cursor.

        Each event must satisfy the envelope in contract-v1.md sec 3. Build them
        with self.event().
        """
        raise NotImplementedError

    def stats(self):
        """Return the stats dict (contract-v1.md sec 4).

        Standard keys are optional but a system serving none gets an empty Main
        page. Extra keys are fine and render generically -- put system-specific
        numbers here, never in the event envelope.
        """
        raise NotImplementedError

    # -- optional -----------------------------------------------------------

    def camera(self):
        """Return {"label": str, "running": bool}."""
        return {"label": "", "running": False}

    def app_running(self):
        """True if the detection app is alive.

        The agent is a separate process precisely so this can answer False --
        detection apps get killed by updates and operators, and the master must
        show "detection stopped" rather than the device vanishing.
        """
        return False

    def footage_list(self, date_from=None, date_to=None, limit=200):
        """Return a list of {id, ts, type, device, bytes, mime}.

        Metadata only. Footage stays on the device that recorded it.
        """
        return []

    def footage_bytes(self, footage_id):
        """Return (bytes, mimetype), or (None, None) if absent."""
        return None, None

    def footage_delete(self, footage_ids):
        """Delete the given snapshot ids from the device; return how many were
        removed. Default: unsupported (0). Adapters backed by a snapshot store
        override this."""
        return 0

    def video_feed_url(self):
        """Local URL of the detection app's existing MJPEG, or None.

        The agent proxies this. It must never open the camera itself -- most
        hardware refuses a second reader, so doing so breaks live detection.
        """
        return None

    def telemetry(self):
        """Optional (Contract v1.3): per-device system telemetry for the
        master dashboard -- e.g. cpu_percent, mem_percent, disk_percent,
        cpu_temp_c, load_avg, net_up_bps/net_down_bps, platform_version,
        installed_apps. Rides its OWN top-level summary field, never stats
        (the master sums stats; a summed CPU% is meaningless). Default {}."""
        return {}

    # -- optional control channel (Contract v1.2) ---------------------------
    # Contract v1 adapters are read-only windows; v1.2 lets a deployment
    # OPT IN to master-driven management. A domain is served only when it is
    # returned by control_domains() AND declared as a `control.<domain>`
    # capability. Defaults keep every existing adapter read-only.

    def control_domains(self):
        """Names of control domains this adapter supports (e.g. "device",
        "mqtt", "camera", "storage"). Default: none."""
        return ()

    def control_get(self, domain):
        """Return the domain envelope:
            {"settings": {...}, "status": {...},
             "actions": [{"name","label","destructive","params"}],
             "meta": {...}}
        Secrets in settings must be masked; `destructive: true` is what
        drives confirmation UX on the master."""
        raise LookupError(f"unsupported control domain: {domain}")

    def control_set(self, domain, payload):
        """Apply settings for a domain; return the FRESH envelope.
        Raise ValueError for invalid payloads (agent answers 400). Only
        overwrite a secret when the payload carries a non-masked value."""
        raise LookupError(f"unsupported control domain: {domain}")

    def control_action(self, name, params, context=None):
        """Run a named, domain-prefixed action (e.g. "device.reboot",
        "storage.purge"). Return a result dict (merged into the response).
        Raise LookupError for unknown names (404), ValueError for bad
        params (400). Reboot-class actions must schedule the work and
        RETURN FIRST. context carries {"token": caller bearer} when a
        deployment wants to act with the caller's authority."""
        raise LookupError(f"unknown action: {name}")

    # -- helpers ------------------------------------------------------------

    def event(self, id, ts, type, severity, label, device=None,
              snapshot_id=None, detail=None):
        """Build one envelope, with the ISO/offset rule handled for you."""
        return {
            "id": f"{self.slug}-{id}" if not str(id).startswith(f"{self.slug}-") else str(id),
            "ts": self.iso(ts),
            "type": type,
            "severity": severity,
            "label": label,
            "device": device,
            "snapshot_id": snapshot_id,
            "detail": detail or {},
        }

    @staticmethod
    def iso(value):
        """Coerce a timestamp to ISO 8601 *with an offset*.

        Naive timestamps are rejected by the contract, and both existing
        systems store them naive ("2026-07-15 10:00:00"). A naive value is
        assumed to be this device's local time -- which is what those apps
        meant when they wrote it -- and stamped with the local offset.
        """
        if isinstance(value, str):
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.datetime.strptime(value[:19], fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                try:
                    parsed = datetime.datetime.fromisoformat(value)
                except ValueError as e:
                    raise ValueError(f"unparseable timestamp {value!r}: {e}")
            value = parsed

        if not isinstance(value, datetime.datetime):
            raise TypeError(f"expected datetime or str, got {type(value).__name__}")

        if value.tzinfo is None:
            value = value.astimezone()
        return value.isoformat()

    @staticmethod
    def now():
        """This device's current time, with offset. Every summary must carry it."""
        return datetime.datetime.now().astimezone().isoformat()
