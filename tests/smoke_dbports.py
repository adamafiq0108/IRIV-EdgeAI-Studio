# A Database node's ports must be VISIBLE, not merely present.
#
# Every column on a Database node has always carried two handles -- one in on
# the left, one out on the right -- and they wired up correctly. None of them
# could be seen: the node's root set `overflow: hidden`, and a port sits half
# outside the node's edge on purpose (that is what makes it read as a socket
# on the border rather than a dot inside the card). So the overflow being
# clipped clipped every port away, and the one node type with the most ports
# looked like the only one with no way in or out.
#
# The two database nodes were the ONLY ones that clipped themselves, which is
# exactly why they were the only ones missing their points.
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRC = os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx")
with open(SRC, encoding="utf-8") as f:
    src = f.read()


def node_body(name):
    """One node component's source, up to the next component."""
    start = src.index(f"const {name} = memo(")
    nxt = src.find("\nconst ", start + 10)
    return src[start:nxt if nxt > 0 else len(src)]


# 1. Neither database node may clip itself -- this is the whole bug.
for name in ("DatasetNode", "QueryNode"):
    body = node_body(name)
    root = body[:body.index("}}>")]          # the root element's style block
    assert 'overflow: "hidden"' not in root, \
        f"{name} must not clip its own ports away"

# 2. ...and no OTHER node may start doing it either, which is how this would
#    silently come back on the next node someone writes.
for m in re.finditer(r"borderRadius: 10, overflow: \"hidden\"", src):
    raise AssertionError("a node root is clipping its ports again")

# 3. Every column still carries BOTH ends. The ports were never the missing
#    part, and a fix that made them visible by removing one would be worse
#    than the bug.
ds = node_body("DatasetNode")
assert 'type="target" position={Position.Left} id={`c_${c.name}`}' in ds, \
    "each column needs an IN port"
assert 'type="source" position={Position.Right} id={`o_${c.name}`}' in ds, \
    "each column needs an OUT port"

# 4. The rounded corners that `overflow: hidden` was doing are still done --
#    otherwise the fix trades invisible ports for a square header poking out
#    of a rounded card.
for name in ("DatasetNode", "QueryNode"):
    assert '"9px 9px 0 0"' in node_body(name), \
        f"{name}'s header must round its own top corners now"
assert '"0 0 9px 9px"' in ds, "the footer must round its own bottom corners"

# 5. Ports are drawn by the shared helper, so they match every other node --
#    "the same as normal nodes" is the actual requirement.
assert "function portStyle(" in src
ps = src[src.index("function portStyle("):]
ps = ps[:ps.index("\n}")]
assert 'borderRadius: "50%"' in ps, "a port is a circle"
assert "border: `2px solid ${color}`" in ps

print("smoke_dbports OK")
