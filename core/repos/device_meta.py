# -*- coding: utf-8 -*-
"""
repos/device_meta.py -- SQL for master-owned device metadata (display name,
description, notes, folder assignment). SQL only. Reads degrade to {} on a
Postgres outage so every device simply shows as Unassigned.
"""

from core.db import DbUnavailable, connect

_COLS = ("display_name", "description", "notes", "folder_id")


def list_all(config_dir=None):
    """{host_serial: {display_name, description, notes, folder_id}}."""
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT host_serial, display_name, description, notes, "
                        "folder_id FROM device_meta")
            return {r["host_serial"]: {k: r[k] for k in _COLS}
                    for r in cur.fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def get(host_serial, config_dir=None):
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT host_serial, display_name, description, notes, "
                        "folder_id FROM device_meta WHERE host_serial = %s",
                        (host_serial,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def upsert(host_serial, fields, config_dir=None):
    """Insert or update the given metadata fields (only keys in _COLS).
    Lazily creates the row for a device that has never been edited."""
    cols = {k: v for k, v in fields.items() if k in _COLS}
    if not cols:
        return get(host_serial, config_dir)
    conn = connect(config_dir)
    try:
        keys = list(cols)
        insert_cols = ", ".join(["host_serial"] + keys)
        placeholders = ", ".join(["%s"] * (len(keys) + 1))
        updates = ", ".join(f"{k} = excluded.{k}" for k in keys)
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO device_meta ({insert_cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (host_serial) DO UPDATE SET {updates}, "
                f"updated_at = now()",
                [host_serial] + [cols[k] for k in keys])
        conn.commit()
    finally:
        conn.close()
    return get(host_serial, config_dir)


def set_folder(host_serial, folder_id, config_dir=None):
    return upsert(host_serial, {"folder_id": folder_id}, config_dir)
