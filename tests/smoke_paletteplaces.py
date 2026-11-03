# Every node the palette offers has somewhere to BE drawn.
#
# The bug this exists to stop: output.modbus_reg declared palette.cat
# "publish" -- a category with no drawer in PALETTE_ORDER, no colour in
# CATEGORY_STYLE and no mention anywhere in the frontend. The node was in the
# registry, in the /api payload and in the bundle, so nothing failed; it was
# simply drawn nowhere, and an operator looking for "Modbus Register" could
# not find or drag it. A palette place is a CONTRACT between the server
# registry and two files in the browser, and nothing checked the two ends
# agreed.
#
# Checked here rather than in check_palette_colors.mjs because only Python can
# read the server registry, which is the end that declares the place.
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


page = read("frontend", "src", "pages", "Workflow.jsx")
registry_js = read("frontend", "src", "workflow", "registry.js")
menu_js = read("frontend", "src", "workflow", "ContextMenu.jsx")


def js_list(src, name):
    body = src.split(f"const {name} = [", 1)[1].split("];", 1)[0]
    return [m.group(1) for m in re.finditer(r'"([^"]+)"', body)]


order = js_list(page, "PALETTE_ORDER")
subgroups = js_list(page, "SUBGROUP_ORDER")
styles = set(re.findall(r"^  (\w+):\s*\{\s*color:",
                        registry_js.split("export const CATEGORY_STYLE = {")[1]
                        .split("\n};")[0], re.M))

live = {k: v for k, v in wf.registry_public().items()
        if not v.get("deprecated") and not v.get("hidden")}

# -- 1. every live node has a drawer to land in ----------------------------------------
homeless = sorted({(v.get("palette") or {}).get("cat") or v.get("category")
                   for v in live.values()} - set(order))
check("every palette.cat a live node declares opens a drawer", not homeless,
      f"no drawer for: {homeless}")
check("...and every drawer has a colour", not set(order) - styles,
      sorted(set(order) - styles))

# -- 2. every sub-heading is ordered ----------------------------------------------------
groups = {(v.get("palette") or {}).get("group") for v in live.values()}
groups = sorted(g for g in groups if g)
stray = [g for g in groups if g not in subgroups]
check("every sub-heading a live node declares has a place in SUBGROUP_ORDER",
      not stray, f"unordered, so they fall to the bottom in registry order: {stray}")
# The other way round is a dead heading: harmless, but it is how the list rots.
check("...and SUBGROUP_ORDER names no heading that no longer exists",
      not [g for g in subgroups if g not in groups],
      [g for g in subgroups if g not in groups])

# -- 3. no drawer is empty --------------------------------------------------------------
# Except the two that draw themselves from live data rather than node types:
# Variables lists the names this graph publishes, Database lists the tables.
SELF_DRAWN = {"variable", "database"}
by_cat = {}
for k, v in live.items():
    by_cat.setdefault((v.get("palette") or {}).get("cat") or v.get("category"), []).append(k)
empty = [c for c in order if c not in SELF_DRAWN and not by_cat.get(c)]
check("no drawer is empty", not empty, f"open onto nothing: {empty}")

# -- 4. Filters: the two halves of "what am I looking at" -------------------------------
check("the Class Filter is filed under Class",
      (live["filter.class"].get("palette") or {}) == {"cat": "filter", "group": "Class"},
      live["filter.class"].get("palette"))
check("the Zone Filter is filed under Zone, in the same drawer",
      (live["zone.ref"].get("palette") or {}) == {"cat": "filter", "group": "Zone"},
      live["zone.ref"].get("palette"))
drawer = page.split('if (cat === "filter") {', 1)[1].split("\n        }\n", 1)[0]
check("...and the zones themselves are listed under the Zone Filter",
      "<SubHead>Class</SubHead>" in drawer and "<SubHead>Zone</SubHead>" in drawer
      and 'dragProps("zone.ref", { zone: z.id })' in drawer
      and drawer.index("<SubHead>Zone</SubHead>") < drawer.index('dragProps("zone.ref"'))
check("the AI Processing drawer no longer lists zones", 'if (cat === "zone") {' not in page)

# -- 5. the insert menu files nodes the same way the palette does -----------------------
# It walked PALETTE_ORDER while filtering on the WIRING category, so a
# palette-only drawer came up empty and storage.query (category "query") could
# not be inserted at all.
check("the right-click Insert menu places nodes by palette.cat",
      "const paletteCat = (s) => s.palette?.cat || s.category;" in menu_js
      and "paletteCat(s) === cat" in menu_js
      and re.search(r"s\.category === cat", menu_js) is None)
check("...and the zones are ADDED to the Filters group, not swapped in for it",
      'if (cat === "filter") {' in menu_js and "...generic," in menu_js
      and "zs.length ? zs : generic" not in menu_js)
# output.modbus_reg left the palette (retired beside output.rs485); the
# Modbus pair that replaced it is what has to be insertable now.
for t in ("storage.query", "storage.csv", "output.modbus_write",
          "variable.rs485"):
    cat = (live[t].get("palette") or {}).get("cat") or live[t]["category"]
    check(f"{t} is insertable ({cat})", cat in order)
check("output.modbus_reg is retired from the palette, not deleted",
      "output.modbus_reg" not in live
      and wf.REGISTRY["output.modbus_reg"].get("deprecated") is True)

# -- 6. searching the palette finds a node by what it DOES ------------------------------
check("the palette searches the description and the sub-heading, not only the label",
      "function nodeMatches(" in page and "nodeMatches(s, type, search)" in page
      and "spec.description" in page)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
