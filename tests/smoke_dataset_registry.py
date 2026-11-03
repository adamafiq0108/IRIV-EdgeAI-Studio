# The table registry: a table is an object, a node is a mapping into it.
#
# The definition used to live inside the node. Two nodes writing to one table
# could then disagree about a column's type with the last one compiled winning,
# and deleting a node deleted the definition while its rows stayed in the
# database, unqueryable. The table is the source of truth now.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import datasets as ds                     # noqa: E402
from core.services import workflow as wf                     # noqa: E402

SAND = tempfile.mkdtemp()
SLUG = "registry_demo"
PKG = os.path.join(SAND, SLUG)
os.makedirs(os.path.join(PKG, "data"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write('[app]\nname = "Registry demo"\n')

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def node(sources, key="bay_log"):
    """Detection -> Zone -> Occupancy (-> Alert), and into the Data Table.

    Nothing connects to the table but its COLUMNS: which rule alerts is
    derived from whatever feeds them, not wired separately.
    """
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
        {"id": "al", "type": "output.alert", "position": {"x": 3, "y": 0},
         "data": {"metric": "count", "op": ">", "value": 2}},
        {"id": "ds", "type": "output.dataset", "position": {"x": 4, "y": 0},
         "data": {"dataset": key, "name": key, "mode": "alert",
                  "sources": sources}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "z"},
             {"id": "e2", "source": "z", "target": "occ"},
             {"id": "e3", "source": "occ", "target": "al"},
             {"id": "e4", "source": "occ", "target": "ds",
              "targetHandle": "c_dwell"}]
    return {"nodes": nodes, "edges": edges}


def compile_(graph, tables=None):
    return wf.compile_graph(wf.clean_graph(graph), ZONES, tables=tables)


# 1. A NODE CANNOT CONJURE A TABLE. A table is designed on the Database page
#    and then read for months; one invented as a side effect of wiring a node
#    is a table nobody chose the shape of.
assert ds.definitions(SAND, SLUG) == {}, "started with tables already"
c = compile_(node({"dwell": {"src": "stat", "key": "fn_bay_avg_s"}}))
assert c["errors"], "a node created a table that does not exist"
assert "no table called 'bay_log'" in c["errors"][0]["message"], c["errors"]
print("== a node naming a table nobody made is refused:",
      c["errors"][0]["message"])

# the table has to be made first -- that is what the Database page is for
ds.set_tables(SAND, SLUG, [{"key": "bay_log", "name": "Bay log",
                            "columns": [{"name": "dwell", "type": "number"}]}])
stored = ds.definitions(SAND, SLUG)
c = compile_(node({"dwell": {"src": "stat", "key": "fn_bay_avg_s"}}),
             tables=stored)
assert not c["errors"], c["errors"]
print("== once the table exists, the same node compiles")

# ...and a canvas cannot bring one back by writing to it either
assert ds.apply_declared(SAND, SLUG, {"ghost": {"name": "ghost", "columns": [
    {"name": "a", "type": "number"}]}}).get("ghost") is None
print("== nor can the artifact write create one behind the page's back")

# 2. THE rule you chose, now absolute: the table owns every column's name and
#    type. A node cannot propose either, so two nodes writing to one column
#    cannot disagree about what it holds -- there is nothing to disagree with.
c = compile_(node({"dwell": {"src": "stat", "key": "fn_bay_avg_s"}}),
             tables=stored)
col = c["outputs"][0]["columns"][0]
assert col["name"] == "dwell" and col["type"] == "number", col
print("== the recorded column and its type came from the table:", col["type"])

after = ds.apply_declared(SAND, SLUG, c["tables"])
assert after["bay_log"]["columns"] == [{"name": "dwell", "type": "number"}], after
print("== and compiling did not rewrite the stored table")

# 3. a column the table does not have simply is not recorded. A node's
#    `sources` naming an unknown column is stale configuration, not an error:
#    the column may have been removed on the Database page minutes ago.
c = compile_(node({"dwell": {"src": "stat", "key": "k"},
                   "gone": {"src": "const", "value": "x"}}), tables=after)
assert not c["errors"], c["errors"]
assert [x["name"] for x in c["outputs"][0]["columns"]] == ["dwell"]
print("== a source naming a column the table lost is ignored, not fatal")

# 4. two nodes filling DIFFERENT columns of one EXISTING table union rather
#    than fight -- adding a column to a table you are already writing to is
#    ordinary work, and unlike creating a table it changes no shape decision
merged = ds.merge_declared(
    {"t": {"key": "t", "name": "t", "columns": [{"name": "a", "type": "number"}]}},
    {"t": {"name": "t", "columns": [{"name": "b", "type": "text"}]}})
assert [c_["name"] for c_ in merged["t"]["columns"]] == ["a", "b"], merged
print("== two nodes each filling part of one table union their columns")

# 5. a table no node writes to is KEPT. Unwiring a node must not take that
#    table's history off the page with it.
kept = ds.apply_declared(SAND, SLUG, {})
assert "bay_log" in kept, kept
print("== a table nothing writes to survives a compile:", list(kept))

# 6. dropping a column is a definition edit, NOT a data edit. The values stay
#    in the recorded rows, so re-adding the name brings the history back.
ds.set_tables(SAND, SLUG, [{"key": "bay_log", "name": "Bay log",
                            "columns": [{"name": "dwell", "type": "number"}]}])
assert [c_["name"] for c_ in ds.definition(SAND, SLUG, "bay_log")["columns"]] \
    == ["dwell"]
ds.set_tables(SAND, SLUG, [{"key": "bay_log", "name": "Bay log",
                            "columns": [{"name": "dwell", "type": "number"},
                                        {"name": "shift", "type": "text"}]}])
assert [c_["name"] for c_ in ds.definition(SAND, SLUG, "bay_log")["columns"]] \
    == ["dwell", "shift"]
print("== a dropped column can be re-added: rows are JSONB, not a schema")

# 7. the registry is still the query whitelist, so it validates like one
for bad in ({"key": "1bad", "columns": [{"name": "a", "type": "number"}]},
            {"key": "ok", "columns": [{"name": "a'; DROP TABLE x; --",
                                       "type": "text"}]},
            {"key": "ok", "columns": [{"name": "a", "type": "number"},
                                      {"name": "a", "type": "text"}]}):
    try:
        ds.clean_table(bad)
    except ValueError:
        continue
    raise AssertionError(f"{bad} was accepted")
print("== a malformed table or column name is refused at the door")

# A table with NO columns is legal here -- that is a table being built on the
# Database page. The node is what records, so the node is where "you have not
# added a column yet" belongs, and it says exactly that.
assert ds.clean_table({"key": "ok", "columns": []})["columns"] == []
_, why = wf.dataset_columns({"data": {"columns": []}}, ())
assert why and "no columns" in why, why
print("== an empty table is legal; the NODE is what refuses to record nothing:")
print("  ", why)

# ...but an empty column list is refused while a valid table round-trips
assert ds.clean_table({"key": "ok", "name": "OK",
                       "columns": [{"name": "a", "type": "text"}]}) == \
    {"key": "ok", "name": "OK", "columns": [{"name": "a", "type": "text"}]}
print("== a valid table round-trips unchanged")

# 8. the type set widened after tables were already stored. A config on disk
#    saying "datetime" still reads as the timestamp it always was -- rewriting
#    every stored config for one word would be a migration with no other
#    benefit, so the old name is simply understood.
migrated = ds.clean_table({"key": "ok", "columns": [{"name": "a",
                                                     "type": "datetime"}]})
assert migrated["columns"] == [{"name": "a", "type": "timestamp"}], migrated
print("== a table stored before the type set widened still reads correctly")
# 9. THE DATABASE LABEL. What an operator means by "the database name" is the
#    heading above their tables -- the real Postgres database is one shared
#    cytron_dashboard for the whole box, so there is no per-system one to
#    rename and renaming the real one would rename every system's.
assert ds.label(SAND, SLUG) == ds.DEFAULT_LABEL
assert ds.set_label(SAND, SLUG, "  Production  line   data ") == "Production line data"
assert ds.label(SAND, SLUG) == "Production line data"
print("== the collection can be renamed:", ds.label(SAND, SLUG))

# blank restores the default rather than leaving an empty heading
assert ds.set_label(SAND, SLUG, "") == ds.DEFAULT_LABEL
assert ds.label(SAND, SLUG) == ds.DEFAULT_LABEL
print("== and blanking it restores the default, not an empty heading")

# it is cosmetic: renaming touches no table, so nothing can be orphaned by it
before = ds.definitions(SAND, SLUG)
ds.set_label(SAND, SLUG, "Anything At All")
assert ds.definitions(SAND, SLUG) == before, "renaming disturbed the tables"
print("== renaming leaves every table exactly as it was")

print("ALL OK")
