# -*- coding: utf-8 -*-
"""
services/system_groups.py -- folders for the systems on the hub grid.

The hub grid was one flat list with a text filter. That is fine for the three
or four systems a customer box runs, and not fine on a workshop machine where
the systems ARE the product catalogue -- built-in templates beside free ones
beside paid ones, told apart only by remembering which is which.

A group is display metadata and nothing else. It changes no port, no slug, no
package and no access: moving a system between folders is the same system, so
the only thing that can go wrong is that it appears under a different heading.
That is why it lives in config/systems.local.toml beside `owner` and `name`
(the other two "how this reads on the card" keys) rather than in Postgres --
the hub must work with no database at all, and a filing system that vanished
during an outage would be worse than none.

Deliberately NOT core/repos/folders.py: that table groups DEVICES inside one
system, is SQL-backed, and is scoped by system_slug. Systems are a level above
it and are TOML-backed. Same shape, different thing.
"""

import re

from core import config

#: A group id. Slugged from the name, so the stored file stays readable.
GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")

#: More than this and a grid of folders is worse than the flat list it
#: replaced -- you scroll past headings looking for a card.
MAX_GROUPS = 12

#: What a workshop machine sorts its systems into before anyone asks it to.
#: These are the three kinds a template author actually has, so the feature is
#: useful the first time it is opened rather than being an empty tool.
#: Seeded once, on first read; renaming or deleting them sticks, because the
#: seed only ever runs when there are no groups at all.
#: The folder that IS the publish switch: a system filed here is snapshotted
#: and offered as a built-in template in every admin's create wizard
#: (core/routes/hub.py hub_set_group). Renaming the folder keeps the id, so
#: the switch survives a rename; deleting the folder unpublishes nothing --
#: the systems fall back to Undefined and their formats simply stop being
#: refreshed.
BUILTIN_GROUP = "builtin"

DEFAULT_GROUPS = (
    {"id": "builtin", "name": "Built-in templates"},
    {"id": "free", "name": "Free templates"},
    {"id": "pro", "name": "Pro templates"},
)


def _slug(value):
    out = "".join(c if c.isalnum() else "_"
                  for c in str(value or "").strip().lower()).strip("_")[:32]
    return out


def groups(config_dir=None):
    """The ordered group list. Seeds the defaults when there are none.

    Read-only: seeding here would write a file on every page load, so the
    caller that wants them persisted asks for it (see ensure_defaults)."""
    stored = (config.systems(config_dir) or {}).get("groups")
    out = []
    seen = set()
    for entry in stored or []:
        if not isinstance(entry, dict):
            continue
        gid = _slug(entry.get("id") or entry.get("name"))
        if not GROUP_RE.match(gid or "") or gid in seen:
            continue
        seen.add(gid)
        out.append({"id": gid, "name": str(entry.get("name") or gid)[:60]})
    return out or [dict(g) for g in DEFAULT_GROUPS]


def ensure_defaults(config_dir=None):
    """Write the default groups if none are stored yet. Returns the list."""
    if (config.systems(config_dir) or {}).get("groups"):
        return groups(config_dir)
    return set_groups([dict(g) for g in DEFAULT_GROUPS], config_dir)


def set_groups(wanted, config_dir=None):
    """Replace the group list (create, rename, reorder, delete in one write).

    A system filed under a group that no longer exists is NOT touched. Its
    `group` key simply stops matching, so it falls back to Ungrouped and can
    be re-filed -- deleting a folder must not quietly rewrite what is in it.
    """
    if not isinstance(wanted, list):
        raise ValueError("groups must be a list")
    if len(wanted) > MAX_GROUPS:
        raise ValueError(f"at most {MAX_GROUPS} folders")
    out, seen = [], set()
    for entry in wanted:
        if not isinstance(entry, dict):
            raise ValueError("each folder is a {id, name} object")
        name = " ".join(str(entry.get("name") or "").split())[:60]
        gid = _slug(entry.get("id") or name)
        if not name:
            raise ValueError("a folder needs a name")
        if not GROUP_RE.match(gid or ""):
            raise ValueError(f"{name!r} does not make a usable folder name")
        if gid in seen:
            raise ValueError(f"two folders are both called {name!r}")
        seen.add(gid)
        out.append({"id": gid, "name": name})
    local = config.load_local("systems", config_dir)
    local["groups"] = out
    config.save_local("systems", local, config_dir)
    return out


def assignments(config_dir=None):
    """{slug: group id} for every system that has been filed."""
    masters = (config.systems(config_dir) or {}).get("masters") or {}
    out = {}
    for slug, entry in masters.items():
        gid = _slug((entry or {}).get("group"))
        if GROUP_RE.match(gid or ""):
            out[slug] = gid
    return out


def assign(slug, group_id, config_dir=None):
    """File one system under a group, or unfile it with a blank/None id.

    Writes the same `[masters.<slug>]` table `name` and `owner` live in, and
    preserves the committed `url` for a system that has never had a local
    block -- otherwise filing a system would orphan the master it points at.
    """
    gid = _slug(group_id)
    if gid and not GROUP_RE.match(gid):
        raise ValueError("that is not a folder id")
    if gid and gid not in {g["id"] for g in groups(config_dir)}:
        raise ValueError("no such folder")
    local = config.load_local("systems", config_dir)
    masters = local.setdefault("masters", {})
    entry = dict(masters.get(slug) or {})
    if not entry.get("url"):
        committed = ((config.systems(config_dir) or {}).get("masters") or {}).get(slug) or {}
        if committed.get("url"):
            entry["url"] = committed["url"]
    if gid:
        entry["group"] = gid
    else:
        entry.pop("group", None)
    masters[slug] = entry
    config.save_local("systems", local, config_dir)
    return gid
