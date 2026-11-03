# -*- coding: utf-8 -*-
"""
services/datasets.py -- the tables a system records, and their columns.

THE REGISTRY. A table is an object in its own right, declared once in the
system's config.local.toml as [[tables]], and a Data Table node is a MAPPING
into it (which value feeds which column). It did not start that way: the
definition used to live inside the node, which meant two nodes writing to one
table could disagree about a column's type with the last one compiled winning,
and deleting a node deleted the definition while its rows stayed in the
database, unqueryable.

So the table wins. A node's `type` field is what it proposes for a column that
does not exist yet; for one that does, the registry's type is authoritative and
the node follows. Deleting a column removes it from the table, the page and the
charts, but never touches values already recorded -- re-adding the same name
brings that history straight back, because the rows are JSONB and a column is a
key inside them, not a schema.

It lives in config.local.toml beside [[outputs]] on purpose: `export_system`
already reads that file, so a template carries its tables with it. A node that
only REFERENCED a table would, once exported and installed elsewhere, record
into a table the new box has never heard of.

This is also the whitelist for every dataset query. A column name cannot be
bound as a SQL parameter -- it becomes a `cols->>'name'` fragment -- so a name
arriving from a browser is checked against what is declared here, on the
server, before it reaches core/repos/datasets.py.
"""

from core.repos import datasets as datasets_repo
from core.services import detector_control as dc

#: A table holds at most this many columns. Past this it stops fitting on a
#: screen and stops being a table an operator reads.
MAX_COLUMNS = 16

#: And a system this many tables -- a picker, not a database browser.
MAX_TABLES = 20


def clean_column(col, index=0):
    """One validated {name, type} column, or ValueError."""
    if not isinstance(col, dict):
        raise ValueError(f"column {index + 1} is not an object")
    name = str(col.get("name") or "").strip().lower()
    if not datasets_repo.COL_RE.match(name):
        raise ValueError(f"{col.get('name')!r} is not a column name - letters, "
                         "numbers and underscores, starting with a letter")
    kind = datasets_repo._TYPE_ALIASES.get(col.get("type"), col.get("type"))
    return {"name": name,
            "type": kind if kind in datasets_repo.COL_TYPES else "number"}


def clean_table(table):
    """One validated table definition, or ValueError."""
    if not isinstance(table, dict):
        raise ValueError("a table is an object")
    key = str(table.get("key") or table.get("dataset") or "").strip().lower()
    if not datasets_repo.COL_RE.match(key):
        raise ValueError(f"{table.get('key')!r} is not a table name - letters, "
                         "numbers and underscores, starting with a letter")
    rows = table.get("columns")
    if not isinstance(rows, (list, tuple)):
        raise ValueError(f"table {key!r} has no columns")
    if len(rows) > MAX_COLUMNS:
        raise ValueError(f"a table holds at most {MAX_COLUMNS} columns")
    columns, seen = [], set()
    for i, col in enumerate(rows):
        clean = clean_column(col, i)
        if clean["name"] in seen:
            raise ValueError(f"table {key!r} has two columns called "
                             f"{clean['name']!r}")
        seen.add(clean["name"])
        columns.append(clean)
    return {"key": key, "name": str(table.get("name") or key)[:60],
            "columns": columns}


#: What this system's collection of tables is CALLED on the Database page.
#:
#: A label and nothing else. The actual Postgres database is one shared
#: `cytron_dashboard` for the whole box (core/db), so there is no per-system
#: database to rename -- renaming it would rename every system's. What an
#: operator means by "the database name" is the heading above their tables,
#: which is this.
DEFAULT_LABEL = "Database"


def label(systems_dir, slug):
    """The display name for this system's tables, or the default."""
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    name = str(((cfg.get("database") or {}).get("name") or "")).strip()
    return name[:60] or DEFAULT_LABEL


@dc.locked
def set_label(systems_dir, slug, name):
    """Rename the collection. Blank restores the default.

    Cosmetic by construction: nothing keys off it, so it can be changed as
    often as anyone likes without orphaning a row, a widget or a query."""
    clean = " ".join(str(name or "").split())[:60]
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    local = dc._read_toml(dc._local_path(pkg_dir))
    block = dict(local.get("database") or {})
    if clean:
        block["name"] = clean
    else:
        block.pop("name", None)
    if block:
        local["database"] = block
    else:
        local.pop("database", None)
    dc._write_local(pkg_dir, local)
    return clean or DEFAULT_LABEL


def definitions(systems_dir, slug):
    """{key: {key, name, columns}} -- every table this system records."""
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    out = {}
    for table in cfg.get("tables") or []:
        try:
            clean = clean_table(table)
        except (ValueError, TypeError):
            continue        # a malformed row is skipped, never a 500
        out[clean["key"]] = clean
    return out


def definition(systems_dir, slug, key):
    """One table's definition, or None."""
    return definitions(systems_dir, slug).get(str(key or "").strip().lower())


def upsert_keys_from(outputs):
    """{table: {identity column, ...}} for every Data Table output, whatever
    its on_repeat.

    The column is the output's FIRST event track_id column -- the same one
    the app keys on (app/outputs.py dataset_upsert_key), so a group-mode table
    (ids_in_row = "group") passes too: its key is that same column, holding
    the whole group text. A set per table because two outputs may write to
    one table.

    Deliberately NOT filtered on on_repeat == "update" (lead decision on
    Pipi N-1). The master replays the local event log from offset 0 on every
    boot; an operator switching a table from update to insert would otherwise
    turn every historical refresh still in the log into a new row on the next
    restart. Only the local app's events get this map at all (the paired-
    device sync path passes none), so the event still cannot pick an
    arbitrary column -- only the table's identity column."""
    out = {}
    for o in outputs or []:
        if not isinstance(o, dict) or o.get("type") != "dataset":
            continue
        for col in o.get("columns") or []:
            if col.get("src") == "event" and col.get("field") == "track_id":
                if col.get("name"):
                    out.setdefault(str(o.get("dataset") or ""), set()).add(
                        str(col["name"]))
                break
    return out


def upsert_keys(systems_dir, slug):
    """Which column of which table a keyed update may use, from the system's
    OWN stored outputs (config.local.toml, validated by detector_control) -- never
    from the event asking for it (Pipi N-1). {} when unreadable: a refused
    update is a plain insert, so failing closed loses nothing."""
    try:
        return upsert_keys_from(dc.get_outputs(systems_dir, slug))
    except Exception:                                         # noqa: BLE001
        return {}


def set_tables(systems_dir, slug, tables):
    """Replace [[tables]] in config.local.toml. Raises ValueError on a bad one.

    Deliberately does NOT touch recorded rows. A definition says how to READ
    the history; editing it must not rewrite what was already true."""
    if not isinstance(tables, list):
        raise ValueError("tables must be a list")
    if len(tables) > MAX_TABLES:
        raise ValueError(f"at most {MAX_TABLES} tables")
    cleaned, seen = [], set()
    for table in tables:
        clean = clean_table(table)
        if clean["key"] in seen:
            raise ValueError(f"two tables are both called {clean['key']!r}")
        seen.add(clean["key"])
        cleaned.append(clean)
    _write_tables(systems_dir, slug, cleaned)
    return cleaned


@dc.locked
def _write_tables(systems_dir, slug, cleaned):
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    local = dc._read_toml(dc._local_path(pkg_dir))
    local["tables"] = cleaned
    dc._write_local(pkg_dir, local)


def merge_declared(existing, declared):
    """The registry after a canvas has been compiled: {key: table}.

    `declared` is what the Data Table nodes asked for, as
    {key: {"name", "columns": [{name, type}]}}.

    A table the registry does not have is NOT created here. Tables are made on
    the Database page, deliberately: a table is a thing somebody designs and
    then reads for months, and one invented as a side effect of wiring a node
    is a table nobody chose the shape of. The compiler refuses such a node by
    name (workflow.py), so this never sees one -- and if it somehow did,
    silently creating it is exactly the behaviour that rule exists to stop.

    A COLUMN on an existing table is added, so an operator filling in a node
    can extend the table they are writing into without leaving the canvas. Its
    type only applies if the table has no opinion: where the table already
    declares that column, the table wins and two nodes cannot disagree.

    A table nothing writes to is KEPT. Its rows are still there to read, and a
    table that vanished because someone unwired a node would take its own
    history off the page with it.
    """
    out = {key: {**table, "columns": [dict(c) for c in table["columns"]]}
           for key, table in (existing or {}).items()}
    for key, want in (declared or {}).items():
        table = out.get(key)
        if table is None:
            continue                        # not ours to create -- see above
        have = {c["name"] for c in table["columns"]}
        for col in want.get("columns") or []:
            if col["name"] in have:
                if col.get("type") == "text" and col.get("identity"):
                    # The one type the table does NOT get an opinion about. A
                    # recorded track id is run-scoped text ("k7m2-3"), and the
                    # query side casts a `number` column -- so leaving an
                    # operator's earlier `number` in place would not store a
                    # wrong-looking value, it would break every query over the
                    # whole table the moment the first row landed.
                    for existing_col in table["columns"]:
                        if existing_col["name"] == col["name"]:
                            existing_col["type"] = "text"
                continue                    # otherwise the registry's type stands
            if len(table["columns"]) >= MAX_COLUMNS:
                break
            # `identity` is a compile-time instruction about the TYPE, not a
            # property of the column; storing it would put a key in the
            # registry that nothing reads back.
            table["columns"].append({k: v for k, v in col.items()
                                     if k != "identity"})
            have.add(col["name"])
    return out


def install_tables(systems_dir, slug, tables):
    """Create the tables a TEMPLATE carries; merge columns into ones that exist.

    merge_declared's no-create rule guards against tables invented as a side
    effect of wiring a node. These are the opposite case: each was designed on
    a Database page -- the SOURCE box's -- and applying a template is the
    deliberate act of asking for that design here. Tables this system already
    has keep their shape (the column-level merge applies, existing types win),
    and nothing is ever dropped."""
    existing = definitions(systems_dir, slug)
    declared = {}
    for t in tables or []:
        clean = clean_table(t)
        declared[clean["key"]] = clean
    if len(existing) + len([k for k in declared if k not in existing]) > MAX_TABLES:
        raise ValueError(f"at most {MAX_TABLES} tables")
    seeded = dict(existing)
    for key, want in declared.items():
        if key not in seeded:
            seeded[key] = {**want, "columns": [dict(c) for c in want["columns"]]}
    merged = merge_declared(seeded, declared)
    if merged == existing:
        return existing
    _write_tables(systems_dir, slug, [merged[k] for k in sorted(merged)])
    return merged


def apply_declared(systems_dir, slug, declared):
    """Fold what a compiled canvas declared into the stored registry.

    Called from the workflow's artifact write, so drawing a Data Table node and
    saving creates its table -- an operator should not have to visit another
    page before their first row can be recorded. Writes only when something
    actually changed, so a recompile of an unchanged canvas is not a file
    write."""
    existing = definitions(systems_dir, slug)
    merged = merge_declared(existing, declared)
    if merged == existing:
        return existing
    _write_tables(systems_dir, slug, [merged[k] for k in sorted(merged)])
    return merged
