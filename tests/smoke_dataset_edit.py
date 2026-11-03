# Editing rows by hand, in a store whose rows arrive from an append-only log.
#
# The Database tool lets a person add, change and delete rows. That is ordinary
# CRUD everywhere except here: rows reach this database by travelling through
# events.jsonl, which the master replays FROM OFFSET 0 on every boot. The
# insert is idempotent on event_id, which stops duplicates -- it does not stop
# RESURRECTION. A real DELETE would be undone by the next restart, and the
# operator would find the row back a week later with no explanation.
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

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

MIG = os.path.join(ROOT, "core", "db", "migrations")
sql = "\n".join(open(os.path.join(MIG, f), encoding="utf-8").read()
                for f in sorted(os.listdir(MIG)) if f.endswith(".sql"))

# 1. A DELETE IS A FLAG. This is the whole reason the column exists.
assert re.search(r"ADD COLUMN IF NOT EXISTS deleted_at", sql), \
    "no deleted_at -- a hand-deleted row comes back on the next boot"
src = open(ds.__file__, encoding="utf-8").read()
body = src.split("def delete_row(")[1].split("\ndef ")[0]
assert "UPDATE dataset_rows SET deleted_at" in body, body[:200]
assert "DELETE FROM" not in body, "delete_row really deletes -- replay undoes it"
print("== deleting a row flags it, so the log's replay cannot resurrect it")

# ...and every read hides flagged rows, in every query that touches them
for fn in ("_where", "values", "table_stats"):
    part = src.split(f"def {fn}(")[1].split("\ndef ")[0]
    assert "deleted_at IS NULL" in part, f"{fn} still returns deleted rows"
print("== and every read path filters them out")

# 2. an edit MERGES. Changing one cell must not blank the rest of the row --
#    the tool sends the cell you typed, not the row you were looking at.
body = src.split("def update_row(")[1].split("\ndef ")[0]
assert "cols || %s::jsonb" in body, body[:200]
assert "SET cols = %s" not in body, "an edit replaces the row instead of merging"
print("== editing a cell merges into the row rather than replacing it")

# 3. a hand-added row gets an id of its OWN, clear of the log's namespace, so
#    a replay can never collide with something a person typed
body = src.split("def insert_row(")[1].split("\ndef ")[0]
assert "manual-" in body, body[:200]
print("== a hand-added row is filed under its own id, not an event id")

# 4. VALUES ARE COERCED TO THE DECLARED TYPE ON THE WAY IN. A number column
#    holding "n/a" breaks every average over it afterwards, silently.
COLUMNS = [{"name": "qty", "type": "number"},
           {"name": "ok", "type": "boolean"},
           {"name": "label", "type": "text"},
           {"name": "meta", "type": "json"}]
cells = ds_routes._clean_cells(
    {"qty": "12.5", "ok": "yes", "label": "Bay 1", "meta": {"a": 1},
     "sneaky": "1' OR 1=1"}, COLUMNS)
assert cells == {"qty": 12.5, "ok": True, "label": "Bay 1", "meta": {"a": 1}}, cells
print("== cells are coerced to their declared type:", cells)

# a number that is not a number is stored as nothing, never as text
assert ds_routes._clean_cells({"qty": "n/a"}, COLUMNS) == {"qty": None}
assert ds_routes._clean_cells({"qty": ""}, COLUMNS) == {"qty": None}
print("== a value that cannot be its type becomes null, not text")

# ...and a column the table never declared cannot be written at all
assert ds_routes._clean_cells({"password": "x"}, COLUMNS) == {}
print("== an undeclared column is not written, the same rule as reading")

# 5. the widened type set. Eight names an operator recognises, mapped onto how
#    each behaves in SQL -- and the name tables were stored under before it
#    widened still reads correctly.
assert set(ds.COL_TYPES) == {"number", "text", "boolean", "date", "timestamp",
                             "json", "image", "uuid"}, ds.COL_TYPES
assert ds.col_types([{"name": "a", "type": "datetime"}])["a"] == "timestamp"
print("== eight column types, and the pre-widening name still understood")

# only `number` is cast; the time types compare as ISO text (no try_cast in
# Postgres, so one malformed value would break every query over the table)
assert "::" in ds._ref("a", "number")
for kind in ("text", "boolean", "date", "timestamp", "image", "uuid"):
    assert ds._ref("a", kind) == "cols->>'a'", (kind, ds._ref("a", kind))
print("== only numbers are cast; everything else compares as text")

# json has no single thing to compare, so it is never filtered on
where, params = ds._where("s", "t", {"blob": "json"}, {"blob": "x"}, None, None)
assert "blob" not in where, where
print("== a json column is not filterable, because there is nothing to match")

# 6. with no database reachable, every write is a quiet failure rather than a
#    crash -- the same contract as every read in this product
assert ds.insert_row("s", "t", {"a": 1}) is None
assert ds.update_row("s", "t", "r1", {"a": 1}) is False
assert ds.delete_row("s", "t", ["r1"]) == 0
assert ds.delete_row("s", "t", []) == 0
print("== with no store reachable, writes fail quietly and report it")
print("ALL OK")
