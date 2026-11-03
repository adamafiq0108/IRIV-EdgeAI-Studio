# The three numbers somebody is watching, kept where they can see them.
#
# A graph with thirty rules publishes a hundred values, and Debug listed all of
# them in graph order under their RAW STAT KEYS -- live_s, net, avg_s -- while
# the node two inches away called the same numbers "Longest wait now" and
# "Inside". One number under two names is two numbers to whoever is reading.
#
# So: a star on the node, saved with the graph (what you pinned while tuning a
# line is a property of the line, not of the browser tab), pinned rows first
# whatever the filter says, and the node's own words in both places.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- the star is on the node, and it is SAVED ------------------------------
nodes = read("frontend", "src", "workflow", "nodes.jsx")
page = read("frontend", "src", "pages", "Workflow.jsx")
check("a node header carries a star", "pinNode(id)" in nodes)
check("...only where there is something to watch",
      'registry[type]?.metrics' in nodes
      and 'registry[type]?.category === "variable"' in nodes)
check("...and it reads its state off the node's own data",
      "const pinned = !!(data || {}).pinned;" in nodes)
check("clicking it does not also drag or select the node",
      'className="nodrag"' in nodes and "e.stopPropagation()" in nodes)
check("the canvas writes it into the graph and saves",
      "pinned: !n.data?.pinned" in page)

# The server keeps unknown node data verbatim -- no allowlist strips it -- so
# a pin survives a save/load round trip. That is what makes it persistent
# rather than session-only, and it is worth pinning down.
g = wf.clean_graph({"nodes": [
    {"id": "c1", "type": "analytics.counter", "position": {"x": 0, "y": 0},
     "data": {"fnId": "c1", "name": "Bay", "mode": "realtime", "pinned": True}}],
    "edges": []})
check("a pin survives the graph cleaner",
      g["nodes"][0]["data"].get("pinned") is True, str(g["nodes"][0]["data"]))

# -- Debug reads the pin, and the words ------------------------------------
panel = read("frontend", "src", "workflow", "DebugPanel.jsx")
check("Debug offers a pinned-only filter", "Pinned only" in panel)
check("...and pinned rows sort first whatever the filter says",
      "pinnedFns.has(b.id)" in panel and "pinnedFns.has(a.id)" in panel)
check("...saying what to do when nothing is pinned",
      "star a node to pin it" in panel)
check("Debug labels a value the way the node's port does",
      "labelFor" in panel and "label(metric)" in panel)
# The raw key is not lost: it is the tooltip, because a stat key is what you
# need when writing a formula or reading a log.
# The tooltip carries the description AND the key, on two lines -- a row that
# has a hint must not lose the key behind it.
check("...with the stat key still one hover away",
      "${hint(metric)}\\n${metric}` : metric}" in panel)

# -- one map, both places --------------------------------------------------
registry = read("frontend", "src", "workflow", "registry.js")
check("the port names live in one place", "export const PORT_LABELS" in registry)
check("...used by the node face", "const LINE_PORTS = PORT_LABELS;" in nodes)
check("...and by Debug through the page",
      "PORT_LABELS[metric]" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
