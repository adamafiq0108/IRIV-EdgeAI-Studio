# -*- coding: utf-8 -*-
"""
device_sweep.py -- the 30-day rule for registered sign-in devices.

A browser that has not been used for auth.DEVICE_EXPIRY_S is unregistered
and its sessions revoked, so it must sign in and register again before it
can use the box. The hub runs this at start and every auth.DEVICE_SWEEP_S
(hub_server.py, the same daemon-thread pattern as the retention sweep);
a sign-in also expires stale records on its way (auth.register_device), so a
cap is never held by a device the sweep simply has not reached yet.

Kept apart from auth.py because it writes audit rows, and the auth service
stays free of the audit/database layer.
"""

from core.services import audit as audit_service
from core.services import auth as auth_service


def run(config_dir=None, now=None, record=None):
    """One pass. Returns how many devices expired. `record` is the audit
    writer (a test passes its own); every expiry is one device.expired row
    under the account that owned the device."""
    record = record or audit_service.record
    gone = auth_service.expire_devices(config_dir, now=now)
    days = auth_service.DEVICE_EXPIRY_S // 86400
    for dev in gone:
        if not dev:
            continue
        record("device.expired", username=dev.get("user") or "",
               detail=f"{dev.get('name') or 'a device'}: inactive for {days} days",
               config_dir=config_dir)
    return len([d for d in gone if d])
