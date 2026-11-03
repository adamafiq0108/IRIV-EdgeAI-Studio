# The wire that vanished.
#
# React Flow measures every handle ONCE and caches it by id. A node whose
# ports change shape after that -- a Trigger growing a Zone port, a Math node
# gaining an input, a Branch gaining a condition, a Data Table gaining a
# column, a counter turning on its PID port -- leaves the cache describing
# handles that are no longer there, and an edge whose handle is missing from
# the cache is simply NOT DRAWN.
#
# That is what "sometimes the line between nodes goes missing" was: the wire
# was still in the graph, still saved, still compiling, and came back on
# reload, because only the measurement was stale. The cure is to tell React
# Flow when a node's ports change, which nothing in this app was doing.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


nodes_src = io.open(os.path.join(ROOT, "frontend", "src", "workflow",
                                 "nodes.jsx"), encoding="utf-8").read()

check("the app tells React Flow when handles change",
      "useUpdateNodeInternals" in nodes_src
      and "function useHandleSync(id, signature)" in nodes_src)
check("...on every change of the signature, not once on mount",
      "[id, signature, updateNodeInternals]" in nodes_src)

# The signature is the handle IDS, both sides. A count would miss a swap that
# keeps the number the same -- exactly what a Trigger does when its Zone port
# replaces nothing and a Variable does when `io` changes which side it offers.
sig = nodes_src.split("useHandleSync(id, [")[1].split("].join(\"|\"))")[0]
check("the signature is built from the handle ids on both sides",
      "targetHandles || []).map((h) => h.id)" in sig
      and "sourceHandles || []" in sig, sig[:200])
check("...and from the flags that add or remove a port",
      "hasSource" in sig and "linkPorts" in sig and "portsInBody" in sig, sig[:200])

# NodeShell draws the ports for nearly every node, so one call there covers
# the whole palette -- including node types nobody has written yet.
shell = nodes_src.split("function NodeShell(")[1]
check("NodeShell re-measures for every node it draws",
      "useHandleSync(id, [" in shell.split("return (")[0])

# The Data Table draws its OWN ports (one pair per column), so it re-measures
# for itself: adding, renaming or reordering a column moves every handle below.
ds = nodes_src.split("const DatasetNode = memo(")[1]
check("the Data Table re-measures when its columns change",
      "useHandleSync(props.id, columns.map((c) => c.name).join(\",\")" in ds)

# The nodes with FIXED ports need none of this, and adding it would be noise:
# a Query has exactly one in and one out, for ever.
q = nodes_src.split("const QueryNode = memo(")[1].split("const ")[0]
check("a node whose ports never change does not re-measure",
      "useHandleSync" not in q)

# -- the OTHER half: an edge saved against a handle id that went away ---------
# The object watches and counters (Counter, In Frame, Zone Counter) once
# named their single output "out"; the redesign made it id-less. React Flow
# forgives a NULL edge (it attaches to the first source handle) but silently
# drops a NAMED edge whose id no longer exists -- so every wire drawn out of
# those nodes in the named era vanished from the canvas while staying in the
# graph and in the compile. The handle answers to "out" again; id-less
# edges keep attaching because null matches the first handle either way.
# The face box moved to the Properties panel (2026-10-14); the shell draws
# these ports now, and the one output is still named "out" (OUT_NAMED).
check("the single output is named 'out' for the edges that recorded it",
      '? [{ id: "out" }] : sources;' in nodes_src
      and "const OUT_NAMED = [" in nodes_src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
