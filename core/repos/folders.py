# -*- coding: utf-8 -*-
"""
repos/folders.py -- SQL for the device folder hierarchy. SQL only, no logic
(docs/conventions.md sec 2). Reads tolerate a Postgres outage (return []);
writes let DbUnavailable propagate so the route answers 503.
"""

from core.db import DbUnavailable, connect


def list_all(system=None, config_dir=None):
    """Folders as flat dicts {id, parent_id, name, system}. Scoped to `system`
    when given (each master shows only its own system's folders). [] on PG-down."""
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return []
    try:
        with conn.cursor() as cur:
            if system:
                cur.execute("SELECT id, parent_id, name, system FROM folders "
                            "WHERE system = %s ORDER BY name", (system,))
            else:
                cur.execute("SELECT id, parent_id, name, system FROM folders ORDER BY name")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def create(name, parent_id=None, system=None, config_dir=None):
    conn = connect(config_dir)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO folders (name, parent_id, system) VALUES (%s, %s, %s) "
                "RETURNING id, parent_id, name, system",
                (name, parent_id, system))
            row = dict(cur.fetchone())
        conn.commit()
        return row
    finally:
        conn.close()


def rename(folder_id, name, config_dir=None):
    conn = connect(config_dir)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE folders SET name = %s WHERE id = %s", (name, folder_id))
            found = cur.rowcount
        conn.commit()
        return found > 0
    finally:
        conn.close()


def move(folder_id, parent_id, config_dir=None):
    conn = connect(config_dir)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE folders SET parent_id = %s WHERE id = %s",
                        (parent_id, folder_id))
            found = cur.rowcount
        conn.commit()
        return found > 0
    finally:
        conn.close()


def delete(folder_id, config_dir=None):
    """Delete a folder; ON DELETE CASCADE removes nested folders, and
    device_meta.folder_id SET NULL returns their devices to Unassigned."""
    conn = connect(config_dir)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM folders WHERE id = %s", (folder_id,))
            found = cur.rowcount
        conn.commit()
        return found > 0
    finally:
        conn.close()


def descendant_ids(folder_id, config_dir=None):
    """folder_id and all folders beneath it (for move-into-descendant checks)."""
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {folder_id}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "WITH RECURSIVE sub AS ("
                "  SELECT id FROM folders WHERE id = %s "
                "  UNION ALL "
                "  SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id"
                ") SELECT id FROM sub", (folder_id,))
            return {r["id"] for r in cur.fetchall()}
    except Exception:
        return {folder_id}
    finally:
        conn.close()
