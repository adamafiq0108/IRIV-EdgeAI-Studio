"""The grid a dashboard is drawn on: small, medium or big.

The GRID does not change with it -- twelve columns, always -- so a card that
spans 3x2 still spans 3x2 and nothing on a page anybody already arranged moves.
Only the pixels one row is worth change, so the whole page grows or shrinks
together.

Saved with the PAGE rather than kept as a browser preference, and that is the
point worth testing: the builder and the finished dashboard have to agree.
"What I arranged is not what I got" is the one thing those two grids exist to
prevent, and a size that lived in localStorage would be invisible to everybody
but the person who set it.

    python tests/smoke_gridsize.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service            # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. the vocabulary --------------------------------------------------------
check("three sizes, and no more",
      builder_service.PAGE_SIZES == ("small", "medium", "big"))
check("a page that says nothing is MEDIUM",
      builder_service._as_size(None) == "medium"
      and builder_service._as_size("") == "medium",
      "every layout was built at medium; a new setting must not move them")
check("...as is a page that says something silly",
      builder_service._as_size("enormous") == "medium"
      and builder_service._as_size(["big"]) == "medium",
      "this arrives from a browser")
check("each real size survives", [builder_service._as_size(s)
                                  for s in ("small", "medium", "big")]
      == ["small", "medium", "big"])
check("...and is read case- and space-insensitively",
      builder_service._as_size("  BIG ") == "big")

# -- 2. it survives the round trip -------------------------------------------
pages = builder_service._as_pages(
    {"pages": [{"id": "main", "name": "Dashboard", "size": "big", "widgets": []}]})
check("a stored page keeps its size", pages[0]["size"] == "big", pages)
check("...and a legacy flat layout is medium",
      builder_service._as_pages({"widgets": []})[0]["size"] == "medium",
      "the shape that predates pages entirely")

tmp = tempfile.mkdtemp()
saved = builder_service.save(tmp, "smoke", {
    "pages": [{"id": "main", "name": "Dashboard", "size": "small", "widgets": []}]})
check("a save writes the size", saved["pages"][0]["size"] == "small", saved)
back = builder_service.load(tmp, "smoke")
check("...and a load reads it back", back["pages"][0]["size"] == "small", back)

# The Analytics builder saves PARTIALLY -- only what the operator owns -- so a
# workflow recompile running at the same time cannot lose the wf_* cards. The
# size is the operator's, like the page name, and has to travel on that path
# too or the setting would silently not stick on Analytics.
merged = builder_service.merge_partial(
    {"slug": "smoke", "pages": [{"id": "main", "name": "Dashboard",
                                 "size": "medium", "widgets": []}]},
    {"pages": [{"id": "main", "name": "Dashboard", "size": "big",
                "widgets": []}]})
check("a PARTIAL save carries the size", merged["pages"][0]["size"] == "big",
      "the Analytics builder only ever saves partially")
kept = builder_service.merge_partial(
    {"slug": "smoke", "pages": [{"id": "p2", "name": "Other", "size": "small",
                                 "widgets": []}]},
    {"pages": []})
check("...and a page the request never mentions keeps what it had",
      kept["pages"][0]["size"] == "small")

# -- 3. both grids read it, and agree ----------------------------------------
grid = read("frontend", "src", "components", "widgets", "WidgetGrid.jsx")
check("the row height is named in ONE place",
      "GRID_SIZES = { small: 64, medium: GRID_ROW_PX, big: 116 }" in grid
      and "export const rowPx" in grid)
check("...medium IS the old constant, so nothing moves",
      "medium: GRID_ROW_PX" in grid,
      "if medium were re-typed as 88 it could drift from GRID_ROW_PX and "
      "every existing page would shift by the difference")
# The page and the canvas both take the height from useRowPx, which is
# rowPxFor applied to the current window -- one function, so the editor and the
# finished page cannot measure different things. What the number DOES with the
# window is executed rather than grepped, in tests/check_rowfit.mjs.
check("the finished dashboard uses it",
      "const rowH = useRowPx(size)" in grid
      and "gridAutoRows: `${rowH}px`" in grid)
check("...and the height follows the window, because a fixed row made a "
      "laptop show half a page",
      "export function rowPxFor" in grid and "export function useRowPx" in grid)
check("...shrinking only, so an arranged page is untouched on the screen it "
      "was arranged on",
      "Math.min(named, scaled)" in grid)

canvas = read("frontend", "src", "components", "widgets", "GridCanvas.jsx")
check("the editing canvas uses it too",
      "const rowH = useRowPx(size)" in canvas
      and "gridAutoRows: `${rowH}px`" in canvas,
      "the editor and the finished page share one geometry on purpose")
check("...including the DRAG maths",
      "(rowH + GRID_GAP)" in canvas and "GRID_ROW_PX + GRID_GAP" not in canvas,
      "a drag that still divided by the constant would move a card further "
      "than the pointer went on a big grid")
check("...and the cell guides behind it",
      f"${{rowH + GRID_GAP}}px" in canvas)

# -- 4. it is offered, and it reaches the page it describes -------------------
for page, what in (("AnalyticsBuilder.jsx", "the Analytics builder"),
                   ("Builder.jsx", "the Dashboard builder")):
    src = read("frontend", "src", "pages", page)
    check(f"{what} offers the three sizes",
          '["small", "Small"]' in src and '["big", "Big"]' in src)
    check(f"...passes it to the canvas", "size={size}" in src)
    check(f"...and marks the page unsaved when it changes",
          "setSize(v); setDirty(true)" in src,
          "a setting that saves silently is one nobody trusts")

check("the Analytics builder saves it onto the page it edits",
      "{ ...p, size, widgets: mine }" in read("frontend", "src", "pages",
                                              "AnalyticsBuilder.jsx"))
check("the Dashboard builder holds it ON the page, so the save carries it",
      "const size = pages[activePage]?.size" in read("frontend", "src", "pages",
                                                     "Builder.jsx"))

check("the computed Analytics page carries the saved size out",
      '"size": builder_service._as_size(saved.get("size"))'
      in read("core", "services", "analytics.py"),
      "Analytics computes its widgets fresh every request; the row height is "
      "a layout decision and has to be fetched from the file")
check("the dashboard route sends it",
      '"grid_size": builder_service._as_size(page.get("size"))'
      in read("core", "routes", "builder.py"))
for page, key in (("Analytics.jsx", "page.size"), ("Main.jsx", "data.grid_size")):
    src = read("frontend", "src", "pages", page)
    check(f"{page} renders at the size it was arranged at",
          f'const gridSize = {key} || "medium"' in src and "size={gridSize}" in src)

# -- 5. no second, silent copy of the idea ------------------------------------
view = read("frontend", "src", "components", "ui", "ViewSettings.jsx")
check("the dead 'density' toggle is gone",
      'update({ density: v })' not in view and 'density: "comfortable"' not in view,
      "it stored a value nothing read, and beside a real size control it "
      "would be a setting that contradicts the one that works")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
