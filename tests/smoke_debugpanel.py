# The Debug panel: what a graph is actually doing, read at a glance.
#
# Four things were in the way of that glance, all of them visible in one
# screenshot of a normal canvas:
#
#   * every row repeated the same paragraph. METRIC_INFO entries come in two
#     shapes -- "Longest wait now (s) - the longest anyone has waited" has a
#     label in front of the dash, and "the formula's result, or the number an
#     API value last read" is a sentence with no label at all. labelFor took
#     the text before the dash, so the second shape printed WHOLE against
#     every formula, API value and Set Variable on the canvas.
#   * a Schedule's next_s read 85394. Nobody converts that in their head.
#   * one branch node appeared as three top-level rows ("If / Else if - branch
#     1", "- branch 2", "- otherwise"), which reads as three nodes.
#   * the jump button took the left edge, so no two names started in the same
#     place and the list could not be scanned down.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


panel = read("frontend", "src", "workflow", "DebugPanel.jsx")
page = read("frontend", "src", "pages", "Workflow.jsx")

# -- 1. a row gets a LABEL; the sentence is the tooltip ---------------------------------
lf = page.split("labelFor={(metric) => {", 1)[1].split("}}", 1)[0]
check("a description is only used as a label when it HAS one, before the dash",
      'info.includes(" - ")' in lf, lf.strip()[:120])
check("...otherwise the metric key itself, read as words",
      'String(metric).replace(/_/g, " ")' in lf)
check("the sentence is handed over separately, for the tooltip",
      'hintFor={(metric) => METRIC_INFO[metric] || ""}' in page
      # Pinned to the two names arriving TOGETHER, not to the closing brace
      # of the prop list: the panel grew a third prop (fnOwners, which says
      # which node published a rule whose id is not that node's fnId) and
      # this check failed on a change that has nothing to do with labels.
      and "labelFor, hintFor" in panel
      # Both, on two lines: the sentence explains the row, the key is what you
      # need to write a formula or read a log, and a row that has a sentence
      # must not lose the key behind it.
      and "${hint(metric)}\\n${metric}` : metric}" in panel)
# The entry that caused it is still the shape that would cause it again.
info = page.split("const METRIC_INFO = {", 1)[1]
value_line = re.search(r'\n  value: "([^"]+)"', info)
check("...and the entry that exposed it is still a dash-less sentence",
      value_line and " - " not in value_line.group(1), value_line and value_line.group(1))

# -- 2. seconds read as time --------------------------------------------------------------
check("metrics counted in seconds are shown as a duration",
      "function fmtDuration(" in panel and "isDuration(metric)" in panel
      and 'metric.endsWith("_s")' in panel)
check("...and every other number groups its thousands",
      "n.toLocaleString()" in panel)

# -- 3. one node, one card ----------------------------------------------------------------
# The panel groups a branch node's ways out by splitting on the separator the
# COMPILER puts in their names. If either side changes the split silently
# stops working, so both ends are checked here rather than trusted.
graph = {"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
     "data": {"classes": ["person"]}},
    {"id": "c1", "type": "analytics.frame", "position": {"x": 2, "y": 0},
     "data": {"fnId": "src", "name": "Src", "mode": "realtime"}},
    {"id": "b1", "type": "branch.cases", "position": {"x": 3, "y": 0},
     "data": {"fnId": "sw", "name": "If / Else if", "metric": "fn_src_count",
              "rules": [{"op": ">", "value": 1}, {"op": ">", "value": 5}],
              "otherwise": True}},
], "edges": [
    {"id": "e1", "source": "m1", "target": "f1"},
    {"id": "e2", "source": "f1", "target": "c1"},
    {"id": "e3", "source": "c1", "target": "b1"},
]}
out = wf.compile_graph(graph, [], ["person"], ["person"])
names = [f["name"] for f in out["functions"] if f["name"].startswith("If / Else if")]
check("a branch node still compiles to one function per way out", len(names) == 3, names)
sep = " · "
check("...each named with the separator the panel splits on",
      all(sep in n for n in names), names)
check("...and they share one title, which is what the panel groups them under",
      len({n.split(sep)[0] for n in names}) == 1, names)
check("the panel does that split", 'const SEP = " \\u00b7 ";' in panel
      and "byTitle" in panel and "g.items.push({ fn, part });" in panel)

# -- 4. the list can be scanned, and searched ---------------------------------------------
values = panel.split('{tab === "values" &&', 1)[1].split('{tab === "events"', 1)[0]
check("the name takes the left edge; the jump button sits at the right",
      values.index("<strong") < values.index('onClick={() => onSelectNode('))
check("a hundred published numbers are searchable",
      'placeholder="Find a value..."' in panel and "const needle = q.trim()" in panel
      and "metricsOfFn(fn).some((m) =>" in panel)
check("Problems says so from whichever tab you are on",
      'id === "problems" && problems.length && tab !== id' in panel)

# -- 5. a row belongs to the node that published it ---------------------------------------
#
# Reported as "Set Variable didn't display the result": the heading was there
# and nothing was under it. Two separate causes, and this is the one on this
# side of the wire -- the panel matches rows to nodes on node.data.fnId, and a
# Set Variable's compiled rule is named after the VARIABLE it writes, never
# after the node. They can never match, so the jump arrow beside that heading
# was disabled on every canvas in the field and a star on the node pinned a
# function id that does not exist.
setvar_graph = {"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
     "data": {"classes": ["person"]}},
    {"id": "c1", "type": "analytics.frame", "position": {"x": 2, "y": 0},
     "data": {"fnId": "seen", "name": "Seen", "mode": "realtime"}},
    # The variable being written, wired into input a. This is the node that
    # NAMES the rule the setter compiles to.
    {"id": "v1", "type": "variable.ref", "position": {"x": 3, "y": 0},
     "data": {"name": "object name", "key": "fn_object_name_value", "io": "in"}},
    {"id": "sv", "type": "math.setvar", "position": {"x": 4, "y": 0},
     "data": {"fnId": "set_variable_j066", "name": "Set Variable",
              "value_type": "text"}},
], "edges": [
    {"id": "e1", "source": "m1", "target": "f1"},
    {"id": "e2", "source": "f1", "target": "c1"},
    {"id": "e3", "source": "v1", "target": "sv", "targetHandle": "a"},
    {"id": "e4", "source": "c1", "target": "sv", "targetHandle": "b"},
]}
sv_out = wf.compile_graph(setvar_graph, [], ["person"], ["person"])
sv_fn = [f for f in sv_out["functions"] if f["id"] == "object_name"]
check("a Set Variable's rule is named after the variable, not the node",
      bool(sv_fn) and not [f for f in sv_out["functions"]
                           if f["id"] == "set_variable_j066"],
      [f["id"] for f in sv_out["functions"]])
# So the panel cannot resolve it from node.data.fnId alone, and must be told.
check("the panel is told which node published a rule it cannot name",
      "fnOwners" in panel and "nodeByFn[fid] = id;" in panel
      and "fnOwners={fnOwners}" in page,
      "DebugPanel still keys rows on node.data.fnId only")
# Resolved on the PAGE, because the answer is in the edges: which Variable is
# wired into the setter's input a.
owners = page.split("const fnOwners = (() => {", 1)[1].split("})();", 1)[0]
check("...resolved from the wire, on the page that can see the edges",
      'n.type === "math.setvar"' in owners and "faceReads.get(n.id)" in owners)
check("...and a wired Variable, which publishes under its own name",
      'n.type === "variable.ref"' in owners and "incoming.has(n.id)" in owners)
# A heading with no rows under it is what the operator actually saw. The rows
# are reverse-engineered from the live stats by prefix, so a rule that has not
# published yet renders as a name and a gap -- which reads as broken.
check("a heading with nothing published under it says so",
      "!g.items.some(({ fn }) => metricsOfFn(fn).length)" in panel
      and "nothing published yet" in panel)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
