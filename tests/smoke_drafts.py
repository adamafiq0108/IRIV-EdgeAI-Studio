# A node nobody has wired anything into is a DRAFT, not a mistake.
#
# Dragging a Snapshot onto the canvas and wiring it a minute later is how this
# page is used. Every one of those minutes painted the canvas with "compile
# errors" and a red node, over a node whose only fault was being new -- the
# operator's own pace, reported as a fault.
#
# The complaint is KEPT, word for word: it is still true and still the thing to
# fix. It arrives as the warning that dims the node instead of the error that
# reddens it, and it becomes an error again the moment something IS wired in --
# because wiring something up wrong is a real mistake, and that is when the
# compiler should say so.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


TABLES = {"bay_log": {"key": "bay_log", "name": "Bay log", "columns": [
    {"name": "shift", "type": "text"}]}}


def compile_(nodes, edges=()):
    return wf.compile_graph(
        wf.clean_graph({"nodes": nodes, "edges": list(edges)}), ZONES,
        tables=TABLES)


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": data}


def wire(src, tgt, handle=None):
    return {"id": f"e_{src}_{tgt}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": handle}


def warned(out, nid):
    return [w["message"] for w in out["warnings"] if w["node"] == nid]


# -- a node with nothing wired in is dim, not red ------------------------------
for nid, ntype, data in (
        ("snap", "output.snapshot", {}),
        ("br", "branch.cases", {"fnId": "b", "rules": [{"op": ">", "value": 1}]}),
        ("trg", "output.trigger", {"name": "Overcrowded"}),
        ("cnt", "analytics.counter", {"fnId": "c", "mode": "realtime"})):
    out = compile_([node(nid, ntype, **data)])
    check(f"{ntype} alone is switched off, not an error", not out["errors"],
          str(out["errors"]))
    msgs = warned(out, nid)
    check(f"...and {ntype} says why",
          any("switched off" in m for m in msgs), str(out["warnings"]))

# -- the SAME complaint, once something is wired -------------------------------
# A Trigger with nothing wired in is a draft; a Trigger fed by something that
# raises nothing is a mistake, and reads as one.
out = compile_([node("src", "source.detection"),
                node("f1", "filter.class", classes=["person"]),
                node("c1", "analytics.counter", fnId="c1", mode="realtime"),
                node("trg", "output.trigger", name="")],
               [wire("src", "f1"), wire("f1", "c1"), wire("c1", "trg")])
check("a wired Trigger with no name IS an error", bool(out["errors"]),
      str(out["warnings"]))
check("...and it is not also warned as a draft",
      not any("switched off" in m for m in warned(out, "trg")))

# -- a card keeps its errors, wired or not -------------------------------------
# A card is bound by a FIELD, not only by a wire: it can name a stat key or a
# table by hand and work with nothing wired to it. So "you named a table that
# does not exist" is a real mistake on an unwired card.
out = compile_([node("card", "dashboard.events", source_mode="dataset",
                     dataset="not_a_table", kind="bar")])
check("an unwired card still reports a table that does not exist",
      bool(out["errors"]), str(out["warnings"]))

# -- and a node that legitimately needs no wire is left alone ------------------
# A Data Table on a timer writes rows from constants and stats on its own
# clock. It raises no error, so the draft rule never touches it -- it must not
# be dimmed for having no input.
out = compile_([node("tbl", "output.dataset", dataset="bay_log", name="bay_log",
                     mode="every", interval_s=10,
                     sources={"shift": {"src": "const", "value": "night"}})])
check("a timer table with no wires is not dimmed",
      not any("switched off" in m for m in warned(out, "tbl")),
      str(out["warnings"]))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
