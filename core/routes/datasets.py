# -*- coding: utf-8 -*-
"""
datasets.py -- read the tables an operator defined with a Dataset node.

The twin of routes/events.py, for rows the product does not know the shape of.
Same contract: parse the request, hand whitelisted parameters to the repo,
return JSON; the repo owns the SQL, and a Postgres outage degrades to an empty
result rather than a 500.

The whitelist is the difference. An event query is checked against a constant
(events_repo.FILTERABLE); a dataset's columns are named by whoever drew the
node, so every request is checked against the DEFINITION read from this
system's own config (core/services/datasets.py) -- never against anything the
client sent. A column the operator did not declare cannot reach SQL.

Scoped to the master's own system: `system_slug` comes from app.state, so one
system can never read another's rows.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from core.repos import datasets as datasets_repo
from core.services import auth as auth_service
from core.routes import perm
from core.services import datasets as datasets_service

router = APIRouter()


def _require_admin(request):
    """Editing a table's definition changes how every card and export over it
    reads. An operator may look; an admin may reshape."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="editing a table is an admin action")


def _context(request):
    """(systems_dir, slug) for the master serving this system, or a 400."""
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        raise HTTPException(status_code=400,
                            detail="datasets are a per-system view; open a system's dashboard")
    return systems_dir, system.slug


def _definition(request, key):
    systems_dir, slug = _context(request)
    found = datasets_service.definition(systems_dir, slug, key)
    if not found:
        raise HTTPException(status_code=404, detail="no such dataset")
    return slug, found


def _filters(params, columns):
    """Query params -> {column: value}, keeping only DECLARED columns."""
    names = {c["name"] for c in columns}
    out = {}
    for name in names:
        vals = params.getlist(name)
        if vals:
            out[name] = vals if len(vals) > 1 else vals[0]
    return out


@router.get("/api/datasets", name="datasets.list")
def list_datasets(request: Request):
    """Every dataset this system records, with its columns -- what the dataset
    page and the widget pickers build their controls from."""
    systems_dir, slug = _context(request)
    found = datasets_service.definitions(systems_dir, slug)
    return {"ok": True, "datasets": sorted(found.values(), key=lambda d: d["name"]),
            "types": list(datasets_repo.COL_TYPES),
            "max_columns": datasets_service.MAX_COLUMNS,
            "max_tables": datasets_service.MAX_TABLES,
            "editable": perm.editable(request, "database."),
            "aggregations": ["count", "sum", "avg", "min", "max", "unique"],
            "buckets": ["hour", "day", "week", "month"]}


@router.get("/api/datasets/status", name="datasets.status")
def datasets_status(request: Request):
    """What the Database page opens on: is the store reachable at all, and how
    full is each table.

    The reachability flag matters more than it looks. Every read in this
    product degrades to an empty result on a Postgres outage, which is right
    for a dashboard widget and terrible for a page about the database itself:
    without this, "no rows yet" and "there is no database" render identically.
    """
    systems_dir, slug = _context(request)
    found = datasets_service.definitions(systems_dir, slug)
    available = datasets_repo_available(request)
    stats = (datasets_repo.table_stats(slug, list(found))
             if available and found else {})
    tables = []
    for key in sorted(found):
        tables.append({**found[key],
                       **stats.get(key, {"rows": 0, "capped": False,
                                         "first_ts": None, "last_ts": None})})
    return {"ok": True, "available": available, "datasets": tables,
            "name": datasets_service.label(systems_dir, slug),
            "types": list(datasets_repo.COL_TYPES),
            "max_columns": datasets_service.MAX_COLUMNS,
            "max_tables": datasets_service.MAX_TABLES,
            "editable": perm.editable(request, "database.")}


def datasets_repo_available(request):
    """Is the row store reachable? Cached briefly by the events repo, which
    owns the probe -- both tables live in the same database."""
    from core.repos import events as events_repo
    return events_repo.available(request.app.state.config_dir)


@router.post("/api/datasets/name", name="datasets.rename")
async def rename_database(request: Request):
    """Rename this system's collection of tables. Admin only.

    A LABEL. The Postgres database is one shared `cytron_dashboard` for the
    whole box, so there is no per-system database to rename -- and renaming
    the real one would rename every system's. Nothing keys off this, so it is
    safe to change as often as anyone likes."""
    perm.require(request, "database.edit")
    systems_dir, slug = _context(request)
    body = await request.json()
    return {"ok": True,
            "name": datasets_service.set_label(systems_dir, slug,
                                               body.get("name"))}


@router.post("/api/datasets", name="datasets.save")
async def save_datasets(request: Request):
    """Replace the table registry. Admin only.

    Deliberately does not touch recorded rows: dropping a column removes it
    from the table, the page and the charts, while the values stay in the
    stored rows. Re-adding the same name brings that history straight back --
    a definition says how to READ what happened, and editing one must not
    rewrite what was already true."""
    perm.require(request, "database.edit")
    systems_dir, slug = _context(request)
    body = await request.json()
    try:
        saved = datasets_service.set_tables(systems_dir, slug,
                                            body.get("datasets") or [])
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "datasets": saved}


@router.get("/api/datasets/{key}/rows", name="datasets.rows")
def dataset_rows(key: str, request: Request):
    """Rows for the dataset table. Query params: any declared column
    (repeatable), from, to (ISO), order, dir(asc|desc), limit, offset."""
    slug, found = _definition(request, key)
    p = request.query_params
    result = datasets_repo.query(
        slug, found["key"], found["columns"],
        filters=_filters(p, found["columns"]),
        since=p.get("from"), until=p.get("to"),
        order=p.get("order", "ts"),
        descending=p.get("dir", "desc") != "asc",
        limit=p.get("limit", 200), offset=p.get("offset", 0))
    return {"ok": True, "dataset": found, **result}


@router.get("/api/datasets/{key}/count", name="datasets.count")
def dataset_count(key: str, request: Request):
    """The exact row count of one table, for the Database page once a table is
    past the 20,000 the rows route stops counting at. Same filters as /rows."""
    slug, found = _definition(request, key)
    p = request.query_params
    n = datasets_repo.count_exact(
        slug, found["key"], found["columns"],
        filters=_filters(p, found["columns"]),
        since=p.get("from"), until=p.get("to"))
    return {"ok": True, "total": n}


@router.post("/api/datasets/{key}/csv", name="datasets.csv")
async def dataset_csv(key: str, request: Request):
    """One table's rows as a CSV file, for a date range and the columns picked
    (the Database page's Export CSV; the same service and the same dialog as a
    table card's Download CSV, services/table_export.py). Body: {range | from,
    to, columns}. Every row in the range comes, up to the service's ceiling;
    read in a thread because a big table is minutes of work."""
    from core.services import table_export                    # noqa: PLC0415
    slug, found = _definition(request, key)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="bad request")
    card = {"kind": "table", "source": "dataset", "dataset": found["key"],
            "columns": found["columns"], "title": found.get("name") or found["key"]}
    spec = {k: body.get(k) for k in ("range", "from", "to") if body.get(k) is not None}
    st = request.app.state
    try:
        made = await asyncio.to_thread(
            table_export.export, card, spec, body.get("columns"), slug,
            config_dir=st.config_dir, systems_dir=st.systems_dir)
    except table_export.ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(content=made["text"], media_type="text/csv; charset=utf-8",
                    headers=table_export.response_headers(made))


@router.get("/api/datasets/{key}/aggregate", name="datasets.aggregate")
def dataset_aggregate(key: str, request: Request):
    """Grouped aggregation for a chart. Query params: agg, field (a declared
    column), group_by (a declared column) OR bucket (hour|day|week|month),
    plus column filters and from/to."""
    slug, found = _definition(request, key)
    p = request.query_params
    result = datasets_repo.aggregate(
        slug, found["key"], found["columns"],
        agg=p.get("agg", "count"),
        field=p.get("field"),
        group_by=p.get("group_by"),
        bucket=p.get("bucket"),
        filters=_filters(p, found["columns"]),
        since=p.get("from"), until=p.get("to"),
        limit=p.get("limit", 500))
    return {"ok": True, **result}


def _clean_cells(cells, columns):
    """{column: value} kept to the DECLARED columns and coerced to the type
    each one promises. A value that cannot be that type is stored as null
    rather than as text: a number column holding "n/a" breaks every average
    over it afterwards, silently and for ever."""
    types = datasets_repo.col_types(columns)
    out = {}
    for name, value in (cells or {}).items():
        kind = types.get(name)
        if kind is None:
            continue                 # not declared -> never stored
        if value is None or value == "":
            out[name] = None
            continue
        if kind == "number":
            try:
                out[name] = float(value)
            except (TypeError, ValueError):
                out[name] = None
        elif kind == "boolean":
            out[name] = str(value).strip().lower() in ("1", "true", "yes", "on")
        elif kind == "json":
            out[name] = value if isinstance(value, (dict, list)) else str(value)[:2000]
        else:
            out[name] = str(value)[:500]
    return out


@router.post("/api/datasets/{key}/rows", name="datasets.add_row")
async def add_row(key: str, request: Request):
    """Add a row by hand. Admin only."""
    perm.require(request, "database.edit")
    slug, found = _definition(request, key)
    body = await request.json()
    row_id = datasets_repo.insert_row(
        slug, found["key"], _clean_cells(body.get("cells"), found["columns"]))
    if not row_id:
        raise HTTPException(status_code=503, detail="the row store is unavailable")
    return {"ok": True, "event_id": row_id}


@router.patch("/api/datasets/{key}/rows/{row_id}", name="datasets.edit_row")
async def edit_row(key: str, row_id: str, request: Request):
    """Change cells on one row. Merged, so editing one cell cannot blank the
    rest. Admin only."""
    perm.require(request, "database.edit")
    slug, found = _definition(request, key)
    body = await request.json()
    cells = _clean_cells(body.get("cells"), found["columns"])
    if not cells:
        raise HTTPException(status_code=400, detail="no columns of this table were named")
    if not datasets_repo.update_row(slug, found["key"], row_id, cells):
        raise HTTPException(status_code=404, detail="no such row")
    return {"ok": True}


@router.post("/api/datasets/{key}/rows/delete", name="datasets.delete_rows")
async def delete_rows(key: str, request: Request):
    """Delete rows. Admin only.

    POST rather than DELETE because it takes a list -- deleting a selection
    one request at a time is a page that half-finishes when the link drops.

    The rows are FLAGGED, not removed: the master replays the event log from
    offset 0 on every boot, so a real delete would be undone by the next
    restart and the operator would find them back with no explanation."""
    perm.require(request, "database.edit")
    slug, found = _definition(request, key)
    body = await request.json()
    gone = datasets_repo.delete_row(slug, found["key"], body.get("rows") or [])
    return {"ok": True, "deleted": gone}


@router.get("/api/datasets/sql/catalog", name="datasets.sql_catalog")
def sql_catalog(request: Request):
    """What the query console may read: view name -> columns.

    Admin only, and it is the honest answer rather than a hint -- these are
    exactly the views the read-only role has been granted."""
    _require_admin(request)
    systems_dir, slug = _context(request)
    from core.services import sqlconsole
    tables = datasets_service.definitions(systems_dir, slug)
    return {"ok": True, "catalog": sqlconsole.catalog(slug, tables),
            "max_rows": sqlconsole.MAX_ROWS, "schema": sqlconsole.schema_for(slug)}


@router.post("/api/datasets/sql", name="datasets.sql")
async def run_sql(request: Request):
    """Run one read-only SELECT against THIS system's scoped views.

    Admin only. The scoping is not enforced here: the query runs as a role
    with no rights on any base table, against views owned by the privileged
    user that already filter to this system, with search_path set to that
    schema alone (core/services/sqlconsole.py). This route just carries the
    text -- which is why it can carry it safely."""
    perm.require(request, "database.edit")
    systems_dir, slug = _context(request)
    from core.services import sqlconsole
    body = await request.json()
    tables = datasets_service.definitions(systems_dir, slug)
    # A statement written for a Telegram button or a Watch carries %(from)s
    # and %(to)s, which only mean something once somebody taps. Run as typed
    # they reach Postgres as a bare percent and come back as `syntax error at
    # or near "%"` -- an error about the symptom, on a statement that is
    # correct. So the console binds the window the page picked (today when
    # none was) and says which.
    params, note = sqlconsole.preview_params(body.get("sql"),
                                             period=body.get("period"),
                                             start=body.get("from"),
                                             end=body.get("to"))
    out = sqlconsole.run(slug, body.get("sql"), tables,
                         request.app.state.config_dir, params=params)
    if note:
        out["bound"] = note
    return out


@router.get("/api/datasets/queries", name="datasets.queries")
def list_queries(request: Request):
    """The SELECTs somebody kept. Readable by anyone who may read the page --
    a saved statement is a definition, not a result."""
    systems_dir, slug = _context(request)
    from core.services import queries as queries_service
    return {"ok": True, "queries": queries_service.listed(systems_dir, slug),
            "max_queries": queries_service.MAX_QUERIES,
            "editable": perm.editable(request, "database.")}


@router.post("/api/datasets/queries", name="datasets.query_save")
async def save_query(request: Request):
    """Keep one query under a name. Same permission as running one.

    Upsert, so saving from the console twice under one name replaces rather
    than growing a list of near-identical statements."""
    perm.require(request, "database.edit")
    systems_dir, slug = _context(request)
    from core.services import queries as queries_service
    body = await request.json()
    try:
        saved = queries_service.save(systems_dir, slug, body or {})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "query": saved}


@router.delete("/api/datasets/queries/{key}", name="datasets.query_delete")
def delete_query(key: str, request: Request):
    """Forget a saved query. The rows it read are untouched -- this deletes a
    question, never an answer."""
    perm.require(request, "database.edit")
    systems_dir, slug = _context(request)
    from core.services import queries as queries_service
    if not queries_service.delete(systems_dir, slug, key):
        raise HTTPException(status_code=404, detail="no such saved query")
    return {"ok": True}


@router.get("/api/datasets/{key}/values", name="datasets.values")
def dataset_values(key: str, request: Request):
    """Distinct values per text column, for the filter controls."""
    slug, found = _definition(request, key)
    return {"ok": True,
            "values": datasets_repo.values(slug, found["key"], found["columns"])}
