# Querying a dataset: the column names are the operator's, so the DECLARATION
# has to be the whitelist.
#
# An event query is checked against a constant (events_repo.FILTERABLE). A
# dataset has no constant to check against, and a column name cannot be bound
# as a parameter -- it becomes a `cols->>'name'` fragment. So two things stand
# in for that constant, and both are exercised here: the name must match
# COL_RE, and it must appear in the columns the operator actually declared.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# fastapi is a master-only dependency and is not installed on this dev box;
# the route logic under test is plain python, so stub the import surface (the
# same trick as tests/smoke_security.py).
import types                                                 # noqa: E402

if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), put=lambda *a, **k: (lambda f: f),
        delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    sys.modules["fastapi"] = fa
    fr = types.ModuleType("fastapi.responses")
    fr.Response = type("Response", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["fastapi.responses"] = fr

from core.repos import datasets as ds                        # noqa: E402
from core.routes import datasets as ds_routes                # noqa: E402
from core.services import builder as builder_service         # noqa: E402

DECLARED = [{"name": "zone", "type": "text"},
            {"name": "dwell", "type": "number"},
            {"name": "shift", "type": "text"}]

# 1. the declaration becomes a {name: type} whitelist, and anything that could
#    not survive as an identifier is dropped on the way in
types = ds.col_types(DECLARED + [{"name": "a'; DROP TABLE events; --", "type": "text"},
                                 {"name": "Mixed Case", "type": "text"},
                                 {"name": "", "type": "text"}])
assert types == {"zone": "text", "dwell": "number", "shift": "text"}, types
print("== the declaration is the whitelist:", types)

# 2. a declared column is filtered by VALUE binding, never by interpolation.
#    Text and datetime use CONTAINMENT (cols @> '{"zone":"Bay"}') rather than
#    cols->>'zone' = %s, because only containment can use the GIN index --
#    the ->> form made every filter on a big table a sequential scan.
where, params = ds._where("demo", "bay_log", types,
                          {"zone": "Bay", "shift": ["day", "night"]}, None, None)
assert "cols @> %s::jsonb" in where, where
assert "cols->>" not in where, where
assert params[:2] == ["demo", "bay_log"], params
# the column name travels INSIDE a bound JSON parameter, not in the SQL text
assert '{"zone": "Bay"}' in params, params
assert '{"shift": "day"}' in params and '{"shift": "night"}' in params, params
print("== text filters use indexable containment, still fully bound:", where)

# a number column keeps the text form: a JSON number would demand an exact
# float repr match ("5" never matching 5.0), and wrong-but-indexed is worse
where, params = ds._where("demo", "bay_log", types, {"dwell": 5}, None, None)
assert "cols->>'dwell' = %s" in where, where
assert params == ["demo", "bay_log", "5"], params
print("== a number column compares as text rather than matching wrongly")

# 3. THE test: a column the operator never declared cannot reach SQL, however
#    it is spelled
for attack in ("dwell'; DROP TABLE dataset_rows; --",
               "no_such_column",
               "ts) AS x, (SELECT password FROM users"):
    where, params = ds._where("demo", "bay_log", types, {attack: "x"}, None, None)
    # Only the scope clauses survive: nothing of the attacker's reached SQL.
    assert where == "system_slug = %s AND dataset = %s AND deleted_at IS NULL", where
    assert params == ["demo", "bay_log"], params
print("== an undeclared column never reaches the query, in any spelling")

# 4. the same holds for ordering and grouping, which cannot be bound either
res = ds.query("demo", "bay_log", DECLARED, order="ts); DROP TABLE x; --")
assert res["rows"] == [] and res["total"] == 0, res   # no store: empty, not an error
print("== an unknown sort column falls back rather than being interpolated")

# 5. a number column is cast for arithmetic; a text column refuses it outright
#    rather than letting Postgres raise on the cast mid-page
assert ds._ref("dwell", "number").startswith("NULLIF("), ds._ref("dwell", "number")
assert ds.aggregate("demo", "bay_log", DECLARED, agg="avg", field="shift") == \
    {"buckets": []}, "averaging a text column was attempted"
print("== averaging a text column is refused before it reaches SQL")

# 5b. a datetime is NOT cast. ISO already compares chronologically as text,
#     and Postgres has no try_cast -- one malformed value in one row would make
#     the cast raise for the whole query, taking a page down over a typo.
dated = DECLARED + [{"name": "started", "type": "timestamp"}]
assert ds.col_types(dated)["started"] == "timestamp", ds.col_types(dated)
# ...and the name it was stored under before the type set widened still reads.
assert ds.col_types([{"name": "started", "type": "datetime"}])["started"] \
    == "timestamp"
assert "::" not in ds._ref("started", "timestamp"), ds._ref("started", "timestamp")
assert ds._ref("started", "timestamp") == "cols->>'started'"
assert ds.aggregate("demo", "bay_log", dated, agg="sum", field="started") == \
    {"buckets": []}, "summing a datetime was attempted"
where, params = ds._where("demo", "bay_log", ds.col_types(dated),
                          {"started": "2026-08-06T09:00:00"}, None, None)
assert "cols @> %s::jsonb" in where, where
assert '{"started": "2026-08-06T09:00:00"}' in params, params
print("== a datetime filters by containment, sorts as ISO, is never summed")


# 6. the route layer keeps only declared columns out of the query string
class Params:
    def __init__(self, data):
        self.data = data

    def getlist(self, key):
        return self.data.get(key, [])


filters = ds_routes._filters(Params({"zone": ["Bay"], "shift": ["day", "night"],
                                     "password": ["x"], "1=1": ["y"]}), DECLARED)
assert filters == {"zone": "Bay", "shift": ["day", "night"]}, filters
print("== the route drops undeclared query params:", filters)

# 7. a widget carries the declaration it was built against, and its query is
#    clamped to it -- a stored layout is rendered for every operator afterwards
w = builder_service.validate([{
    "id": "w1", "kind": "bar", "source": "dataset", "dataset": "bay_log",
    "columns": DECLARED,
    "query": {"agg": "avg", "field": "dwell", "group_by": "shift",
              "filters": {"zone": "Bay", "sneaky": "1"}},
}])[0]
assert w["dataset"] == "bay_log", w
assert w["query"]["field"] == "dwell" and w["query"]["group_by"] == "shift", w
assert w["query"]["filters"] == {"zone": "Bay"}, w["query"]["filters"]
print("== a stored widget's query is clamped to its declaration:", w["query"])

# ...and one that names a column it did not declare loses that column, rather
# than storing a name the renderer would later interpolate.
w = builder_service.validate([{
    "id": "w2", "kind": "bar", "source": "dataset", "dataset": "bay_log",
    "columns": DECLARED,
    "query": {"agg": "sum", "field": "salary", "group_by": "ssn"},
}])[0]
assert w["query"]["field"] == "" and w["query"]["group_by"] == "", w["query"]
print("== an undeclared field is dropped from the stored widget")

# 8. a dataset widget with no declaration is refused outright: it would have
#    nothing to check a column name against
for broken in ({"dataset": "bay_log", "columns": []},
               {"dataset": "", "columns": DECLARED},
               {"dataset": "1 OR 1=1", "columns": DECLARED}):
    try:
        builder_service.validate([{"id": "w", "kind": "table",
                                   "source": "dataset", **broken}])
    except ValueError as exc:
        assert "dataset" in str(exc), exc
    else:
        raise AssertionError(f"a widget with {broken} was accepted")
print("== a dataset widget without a valid declaration is refused")
print("ALL OK")
