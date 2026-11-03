# -*- coding: utf-8 -*-
"""
services/queries.py -- the SELECTs an operator decided were worth keeping.

The console was a scratchpad: type a statement, read the rows, close the tab.
Everything useful someone wrote there had to be written AGAIN in a Query node
on the canvas, from memory, because the two pages shared nothing. That is how
"shift totals" ends up existing three times with three different definitions.

So a query becomes an object, exactly the way a table did (services/datasets.py
tells that story): declared once, in this system's config.local.toml as
[[queries]], listed on the Database page, and offered in the workflow palette
by NAME -- drag "Busiest zones" onto the canvas and get a Query node that
already holds the statement.

Three rules it keeps:

  * SELECT only, checked here as well as at run time. A saved statement is one
    somebody will run later without re-reading it;
  * the TABLES it reads are recorded at save time, found by name in the text
    against the registry. That is what lets the palette wire the dropped node
    up to the tables it needs instead of handing over a query that cannot
    compile until the operator guesses which lines to draw;
  * a table the query names that no longer exists is simply not recorded --
    the statement is kept as written, and the Query node will say what it
    cannot read the way it always did.

It lives beside [[tables]] on purpose: `export_system` reads that file, so a
template carries its saved queries with it rather than arriving with a
Database page full of tables and nothing to ask of them.
"""

import re

from core.repos import datasets as datasets_repo
from core.services import detector_control as dc
from core.services import sqlconsole

#: A system holds at most this many saved queries -- a list an operator reads,
#: not a query library.
MAX_QUERIES = 40

#: Longest statement kept. Well past anything anyone writes in the console's
#: box, and short enough that the config file stays a file a person can open.
MAX_SQL = 8000


def _key_of(name, taken=()):
    """A stable id for a saved query, from its name. `q_busiest_zones`.

    Prefixed because the key shares a namespace with nothing and is read by
    people: a bare `counting` in the config would read as a table."""
    base = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    # 28, not 32: COL_RE caps the key at 32 characters, and a collision suffix
    # (`_2`, `_11`) has to fit inside that or the key it mints is one the
    # config cannot store -- which would re-mint on every read.
    base = ("q_" + base)[:28].rstrip("_") or "q_query"
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    return key


def read_only(sql):
    """(text, error). The same wall the console applies, applied at SAVE time.

    Checking only on the way out would mean a statement that can never run
    still sits in the list looking like a query somebody may click."""
    text = str(sql or "").strip().rstrip(";").strip()
    if not text:
        return "", "the query is empty"
    if len(text) > MAX_SQL:
        return "", f"a saved query is at most {MAX_SQL} characters"
    if ";" in text:
        return "", "one statement at a time"
    if not sqlconsole._READ_ONLY.match(text):
        return "", "only SELECT (or WITH) queries can be saved"
    return text, ""


def tables_named(sql, known):
    """Which of the system's tables this statement mentions, in registry order.

    A word-boundary match on the key, which is what the console's own catalog
    chips insert. It is deliberately generous -- naming a table in a comment
    records a dependency that is merely unnecessary, while MISSING one hands
    the canvas a query that cannot read what it asks for."""
    text = str(sql or "")
    out = []
    for key in known or ():
        if re.search(r"\b" + re.escape(key) + r"\b", text, re.IGNORECASE):
            out.append(key)
    return out


def clean_query(q, known=(), taken=()):
    """One validated {key, name, sql, tables}, or ValueError."""
    if not isinstance(q, dict):
        raise ValueError("a query is an object")
    name = " ".join(str(q.get("name") or "").split())[:60]
    if not name:
        raise ValueError("name the query - the palette lists it by name")
    text, why = read_only(q.get("sql"))
    if why:
        raise ValueError(why)
    key = str(q.get("key") or "").strip().lower()
    if not datasets_repo.COL_RE.match(key or ""):
        key = _key_of(name, taken)
    return {"key": key, "name": name, "sql": text,
            "tables": tables_named(text, known)}


def definitions(systems_dir, slug):
    """{key: {key, name, sql, tables}} -- every saved query, malformed skipped."""
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    known = _table_keys(systems_dir, slug)      # read once, not once per row
    out = {}
    for q in cfg.get("queries") or []:
        try:
            clean = clean_query(q, known, taken=set(out))
        except (ValueError, TypeError):
            continue                    # a bad row is skipped, never a 500
        out[clean["key"]] = clean
    return out


def listed(systems_dir, slug):
    """The saved queries as a list, by name -- what a page or palette shows."""
    return sorted(definitions(systems_dir, slug).values(),
                  key=lambda q: q["name"].lower())


def _table_keys(systems_dir, slug):
    from core.services import datasets as datasets_service    # noqa: PLC0415
    return list(datasets_service.definitions(systems_dir, slug))


def save(systems_dir, slug, q):
    """Add or replace ONE saved query; returns it. Raises ValueError.

    Upsert rather than replace-the-list: the console saves one query at a
    time, and a page that had to send the whole collection would drop
    anything saved from another tab in between."""
    known = _table_keys(systems_dir, slug)
    current = definitions(systems_dir, slug)
    key = str((q or {}).get("key") or "").strip().lower()
    if key and key not in current:
        # An id for a query that is not here is a client's guess, not a
        # rename -- mint a fresh one rather than adopting it.
        q = {**q, "key": ""}
    clean = clean_query(q, known, taken=set(current))
    if clean["key"] not in current and len(current) >= MAX_QUERIES:
        raise ValueError(f"at most {MAX_QUERIES} saved queries")
    current[clean["key"]] = clean
    _write(systems_dir, slug, current)
    return clean


def delete(systems_dir, slug, key):
    """Forget one saved query. True if it was there."""
    current = definitions(systems_dir, slug)
    if str(key or "").strip().lower() not in current:
        return False
    current.pop(str(key).strip().lower())
    _write(systems_dir, slug, current)
    return True


@dc.locked
def _write(systems_dir, slug, current):
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    local = dc._read_toml(dc._local_path(pkg_dir))
    rows = [current[k] for k in sorted(current)]
    if rows:
        local["queries"] = rows
    else:
        local.pop("queries", None)
    dc._write_local(pkg_dir, local)
