"""An empty, unwired Custom Formula is a draft, not an error.

Seen on .164 (2026-10-19, system z): a Custom Formula dropped on the canvas and
left empty compiled to the error "empty expression". ANY error stops the whole
workflow being applied (routes/workflow.py: `if not compiled["errors"]`), so
the Variable wired from an Object Detection, saved in the same pass, never
reached the app and read "..." with nothing saying why. An unfinished node is
the same kind of thing as an unconnected rule: switched off with a warning.

  1. empty + unwired: no error, a warning naming the node, nothing compiled for it
  2. the other nodes in the graph still compile (a constant beside it)
  3. empty but WIRED in still says so (that one is a mistake)
  4. typing a formula makes it compile as before

    python tests/smoke_emptyformula.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  {detail}"))
    if not ok:
        bad += 1


def build(expr, wired):
    nodes = [
        {"id": "k", "type": "variable.number", "position": {"x": 0, "y": 0},
         "data": {"fnId": "limit", "name": "Limit", "value": 3, "value_type": "number"}},
        {"id": "f", "type": "analytics.expr", "position": {"x": 2, "y": 0},
         "data": {"fnId": "formula_f", "name": "Formula", "expr": expr}},
    ]
    edges = [{"id": "e1", "source": "k", "target": "f"}] if wired else []
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), [])


c = build("", wired=False)
check("1 empty and unwired: no error",
      not c["errors"], c["errors"])
check("1 ...a warning names the node",
      any(w["node"] == "f" and "empty" in w["message"] for w in c["warnings"]), c["warnings"])
check("1 ...and nothing is compiled for it",
      not any(f["id"] == "formula_f" for f in c["functions"]), [f["id"] for f in c["functions"]])
check("2 the constant beside it still compiles",
      any(f["id"] == "limit" for f in c["functions"]), [f["id"] for f in c["functions"]])

c = build("", wired=True)
check("3 empty but wired in is still an error",
      any(e["node"] == "f" for e in c["errors"]), (c["errors"], c["warnings"]))

c = build("fn_limit_value * 2", wired=True)
check("4 with a formula it compiles as before",
      not c["errors"] and any(f["id"] == "formula_f" for f in c["functions"]),
      (c["errors"], [f["id"] for f in c["functions"]]))

print(f"\n{'%d FAILED' % bad if bad else 'all good'}")
sys.exit(1 if bad else 0)
