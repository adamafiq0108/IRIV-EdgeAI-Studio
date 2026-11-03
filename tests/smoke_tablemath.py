# A table column wired into a Math input: the column's recorded rows, reduced
# the way a card reduces them (Show: total, average, latest...; Period: today,
# this week...), as a live number the calculation reads -- and the Math's
# result then feeds a card like any other value.
#
# WHY IT COMPILES THE WAY IT DOES. The app that runs the arithmetic has no
# database, so the input becomes a query variable: the same poller a Query
# stored in a Variable already uses, pointed at this master, which runs the
# very aggregate a tile card would. Nothing new runs on the box; a column into
# a Math node is a column into a card with arithmetic after it.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fx                       # noqa: E402
from core.services import workflow as wf                        # noqa: E402

TABLES = {"logbook": {"key": "logbook", "name": "Logbook", "columns": [
    {"name": "total", "type": "number"},
    {"name": "shift", "type": "text"},
]}}
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    import io
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


MATH = next(k for k, s in wf.REGISTRY.items()
            if k.startswith("math.") and "add" in (s.get("ops") or []))


def graph(a_data, source_handle="o_total"):
    nodes = [
        {"id": "ds", "type": "output.dataset", "position": {"x": 0, "y": 0},
         "data": {"dataset": "logbook", "name": "Logbook", "mode": "alert", "sources": {}}},
        {"id": "m", "type": MATH, "position": {"x": 1, "y": 0},
         "data": {"fnId": "calc", "name": "Calc", "op": "add", "b_value": 2, **a_data}},
    ]
    edges = [{"id": "e1", "source": "ds", "target": "m",
              "sourceHandle": source_handle, "targetHandle": "a"}]
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def fn(c, fid):
    return next((f for f in c["functions"] if f["id"] == fid), None)


# -- the wire is allowed, on both sides -------------------------------------------------------
ok, why = wf.edge_allowed("output.dataset", MATH)
check("a table column may be wired into a Math input", ok, why)
check("...and the client agrees",
      'srcType === "output.dataset" && tgtType.startsWith("math.")'
      in read("frontend", "src", "workflow", "registry.js"))

# -- the input compiles to a query variable the master answers ----------------------------------
c = wf.compile_graph(graph({"a_agg": "sum", "a_range": "today"}), [], tables=TABLES)
check("the graph compiles clean", not c["errors"], [e["message"] for e in c["errors"]])
q = fn(c, "calc_a")
check("the input becomes a query variable named after the node and the input",
      q is not None and q["type"] == "query_value", q)
check("...carrying the table, the column, Show and Period - and no SQL",
      q and q["params"].get("dataset") == "logbook" and q["params"].get("field") == "total"
      and q["params"].get("agg") == "sum" and q["params"].get("range") == "today"
      and not q["params"].get("sql"), q and q["params"])
check("...that polls this master's query-value route",
      q and "/api/workflow/query-value?fn=calc_a" in q["params"].get("url", ""), q and q["params"].get("url"))
m = fn(c, "calc")
check("the Math node reads that variable and adds its typed value",
      m is not None and "fn_calc_a_value" in m["params"]["expr"] and "2" in m["params"]["expr"],
      m and m["params"]["expr"])

# -- defaults and clamps, the cards' own -------------------------------------------------------
c2 = wf.compile_graph(graph({}), [], tables=TABLES)
check("no Show chosen on a number column: the latest value",
      fn(c2, "calc_a")["params"]["agg"] == "latest" and fn(c2, "calc_a")["params"]["range"] == "")
c3 = wf.compile_graph(graph({"a_agg": "sum"}, source_handle="o_shift"), [], tables=TABLES)
check("a text column cannot be totalled - it is counted instead",
      fn(c3, "calc_a")["params"]["agg"] == "count", fn(c3, "calc_a")["params"])
c4 = wf.compile_graph(graph({"a_agg": "sum"}, source_handle="o_gone"), [], tables=TABLES)
check("a column that no longer exists is an error the operator can read",
      any("not a column" in e["message"] for e in c4["errors"]), [e["message"] for e in c4["errors"]])
c5 = wf.compile_graph(graph({"a_agg": "sum"}, source_handle="table"), [], tables=TABLES)
check("the table's own port is not a number - say to use a column's read port",
      any("READ port" in e["message"] for e in c5["errors"]), [e["message"] for e in c5["errors"]])

# -- the validator accepts the shape, and only sane values ------------------------------------
doc = {"id": "x_a", "name": "x", "type": "query_value", "enabled": True,
       "params": {"dataset": "logbook", "field": "total", "agg": "avg", "range": "week",
                  "tables": TABLES and [TABLES["logbook"]], "interval_s": 10,
                  "url": "http://127.0.0.1:5100/api/workflow/query-value?fn=x_a"}}
clean = fx.validate_function(doc)
check("a table aggregate validates without SQL",
      clean["params"].get("dataset") == "logbook" and clean["params"].get("agg") == "avg"
      and "sql" not in clean["params"], clean["params"])
for bad_params, why in ((dict(doc["params"], agg="median"), "an unknown aggregate"),
                        (dict(doc["params"], range="page"), "a page range"),
                        (dict(doc["params"], field="", agg="sum"), "a total with no column")):
    try:
        fx.validate_function({**doc, "params": bad_params})
        check(f"...and refuses {why}", False)
    except (ValueError, TypeError):
        check(f"...and refuses {why}", True)

# -- the route answers it the way a card is answered ---------------------------------------------
route = read("core", "routes", "workflow.py")
check("the query-value route runs the repo aggregate for a table shape, never SQL from the doc",
      'if p.get("dataset"):' in route and "builder_service._dataset_data(widget" in route)
check("the inspector asks Show and Period on a table-fed input",
      "tableInputOf(h)" in read("frontend", "src", "pages", "Workflow.jsx")
      and "the latest value (rows counted for a text column)" in read("frontend", "src", "pages", "Workflow.jsx"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
