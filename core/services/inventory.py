# -*- coding: utf-8 -*-
"""
inventory.py -- device metadata + folder organization (v2.0).

Pure functions + repo reads, no framework imports (conventions sec 2). Two
jobs:
  * merge_meta: stamp master-owned metadata (display_name/description/notes/
    folder_id) onto the live slave dicts each sync cycle. A device with no
    device_meta row (or folder_id NULL) is Unassigned.
  * folder tree + sidebar: turn the flat folders table into a nested tree and
    the file-explorer sidebar model, grouping devices under their folder and
    the rest under Unassigned.

Everything degrades: on a Postgres outage the repos return empty, so every
device is simply Unassigned and the tree is just the Unassigned group.
"""

from core.repos import device_meta as meta_repo
from core.repos import folders as folders_repo

_META_KEYS = ("display_name", "description", "notes", "folder_id")


def device_meta_map(config_dir=None):
    """{host_serial: {display_name, description, notes, folder_id}} (or {})."""
    return meta_repo.list_all(config_dir)


def merge_meta(slaves, meta):
    """Return new slave dicts with metadata stamped on (pure).

    display_name precedence: metadata display_name -> existing label ->
    host_serial. folder_id defaults to None (Unassigned).
    """
    out = []
    for s in slaves:
        m = meta.get(s.get("host_serial"), {})
        merged = dict(s)
        merged["display_name"] = (m.get("display_name")
                                  or s.get("label") or s.get("host_serial"))
        merged["description"] = m.get("description") or ""
        merged["notes"] = m.get("notes") or ""
        merged["folder_id"] = m.get("folder_id")
        out.append(merged)
    return out


def folder_snapshot(system=None, config_dir=None):
    """Flat folder rows for caching on app.state, scoped to `system` so a master
    only sees its own system's folders -- one system's folders never show in
    another system's dashboard."""
    return folders_repo.list_all(system, config_dir)


# -- write operations (raise ValueError -> route answers 400) ----------------

def _clean_name(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("folder name required")
    if len(name) > 100:
        raise ValueError("folder name too long (max 100)")
    return name


def create_folder(name, parent_id=None, system=None, config_dir=None):
    return folders_repo.create(_clean_name(name), parent_id, system, config_dir)


def rename_folder(folder_id, name, config_dir=None):
    if not folders_repo.rename(folder_id, _clean_name(name), config_dir):
        raise ValueError("folder not found")
    return {"id": folder_id, "name": name.strip()}


def move_folder(folder_id, new_parent_id, config_dir=None):
    """Reparent a folder. Rejects moving a folder into itself or any of its
    own descendants (which would orphan a subtree into a cycle)."""
    if new_parent_id is not None:
        if new_parent_id in folders_repo.descendant_ids(folder_id, config_dir):
            raise ValueError("cannot move a folder into itself or a descendant")
    if not folders_repo.move(folder_id, new_parent_id, config_dir):
        raise ValueError("folder not found")
    return {"id": folder_id, "parent_id": new_parent_id}


def delete_folder(folder_id, config_dir=None):
    if not folders_repo.delete(folder_id, config_dir):
        raise ValueError("folder not found")
    return {"id": folder_id}


def assign_device(host_serial, folder_id, config_dir=None):
    """Move a device into a folder (folder_id None = Unassigned)."""
    return meta_repo.set_folder(host_serial, folder_id, config_dir)


def update_device(host_serial, fields, config_dir=None):
    """Update master-owned display metadata. Whitelisted keys only; never
    touches device ID / token / url / hardware identity."""
    allowed = {}
    if "display_name" in fields:
        dn = (fields["display_name"] or "").strip()
        if len(dn) > 100:
            raise ValueError("display name too long (max 100)")
        allowed["display_name"] = dn or None
    for key in ("description", "notes"):
        if key in fields:
            val = (fields[key] or "").strip()
            if len(val) > 2000:
                raise ValueError(f"{key} too long (max 2000)")
            allowed[key] = val or None
    if "folder_id" in fields:
        allowed["folder_id"] = fields["folder_id"]
    if not allowed:
        raise ValueError("no editable fields provided")
    return meta_repo.upsert(host_serial, allowed, config_dir)


def folder_tree(folders):
    """Nest flat folder rows into {id, name, parent_id, children:[...]}.

    Iterative (no recursion depth limit). Orphans (parent missing, e.g. a
    stale cache after a delete) surface at the root so nothing vanishes.
    """
    nodes = {f["id"]: {"id": f["id"], "name": f["name"],
                       "parent_id": f["parent_id"], "children": []}
             for f in folders}
    roots = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    _sort(roots)
    return roots


def _sort(nodes):
    nodes.sort(key=lambda n: n["name"].lower())
    for n in nodes:
        _sort(n["children"])


def build_sidebar(folders, slaves):
    """File-explorer model: nested folder tree with each device placed under
    its folder_id, plus an Unassigned bucket. Devices carry the minimum the
    sidebar needs (host_serial, display_name, online) plus their capabilities,
    so the per-device menu only offers destinations the device supports."""
    tree = folder_tree(folders)
    by_folder = {}
    unassigned = []
    for s in slaves:
        entry = {"host_serial": s.get("host_serial"),
                 "display_name": s.get("display_name") or s.get("label")
                 or s.get("host_serial"),
                 "online": bool(s.get("online")),
                 "capabilities": sorted(s.get("capabilities") or ())}
        fid = s.get("folder_id")
        if fid is None:
            unassigned.append(entry)
        else:
            by_folder.setdefault(fid, []).append(entry)

    def attach(node):
        node["devices"] = sorted(by_folder.get(node["id"], []),
                                 key=lambda d: d["display_name"].lower())
        for child in node["children"]:
            attach(child)

    for root in tree:
        attach(root)
    return {"tree": tree,
            "unassigned": sorted(unassigned,
                                 key=lambda d: d["display_name"].lower())}
