# Every page except the workflow editor, on every size of screen -- and a PDF
# that is an A4 sheet whatever the window was doing.
#
# Two separate jobs that are easy to confuse:
#
#   1. SCREEN. A 12-column dashboard is 30px per column on a phone; a 236px
#      side rail beside a 360px screen leaves 120px for the page itself. Those
#      layouts have to change shape, not shrink.
#
#   2. PAPER. A print job lays out against the SHEET, and A4 at 96dpi is about
#      794 CSS pixels -- under most of the breakpoints below. So every screen
#      breakpoint fired while printing, and a report exported from a 27"
#      monitor came out in the phone layout. The width queries are scoped to
#      `screen` for that one reason, and this file exists to keep them that
#      way: the mistake is invisible on screen and only shows up on paper.
#
# The workflow editor is deliberately exempt (a node canvas is not a phone
# document), and so is the dashboard BUILDER's canvas -- the geometry is the
# thing being edited there, so it scrolls rather than rearranging.
import io
import os
import re
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


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


css = read("frontend", "src", "theme.css")

# -- 1. paper is not a small screen -------------------------------------------
# The check that matters most and reads as the most pedantic. A single
# unqualified `@media (max-width: 900px)` is enough to put a printed report
# into the tablet layout, and nothing on screen ever shows it.
unscoped = re.findall(r"@media \((?:max|min)-width:[^)]*\)", css)
check("no width query can fire on paper -- every one says `screen and`",
      not unscoped,
      "unscoped: " + ", ".join(unscoped[:4]))
scoped = re.findall(r"@media screen and \((?:max|min)-width:", css)
check("...and there are real ones being scoped, not an empty file",
      len(scoped) >= 20, f"{len(scoped)} found")

# The sheet itself, and the one page that prints.
check("the sheet is A4 with printer margins",
      "@page { size: A4 portrait; margin: 14mm 12mm; }" in css)
analytics = read("frontend", "src", "pages", "Analytics.jsx")
# No print button of our own any more. A button labelled PDF promises a report
# and hands over the window, which is a different thing; Ctrl+P is the path,
# and the @media print block is what makes it a sheet rather than a screenshot.
check("...and no button offers to print it for you",
      "window.print()" not in analytics and ">PDF<" not in analytics
      and "printPage" not in analytics)
check("...while the print stylesheet still shapes what Ctrl+P produces",
      "@media print {" in css and ".print-head {" in css
      and ".hw-footer, .toast-stack" in css)
# Tailwind's own breakpoints are min-width, so they behave the opposite way on
# paper (a 794px sheet gets the `md:` layout). The printed page must therefore
# not be built from them.
check("...and is built from the stylesheet's classes, not Tailwind breakpoints",
      "md:grid-cols" not in analytics and "lg:grid-cols" not in analytics,
      "a min-width utility resolves against the SHEET when printing")

# -- 2. the dashboard on a phone ----------------------------------------------
# A placed dashboard states each widget's cell as an INLINE style, because the
# placement is data. `!important` is the only thing a stylesheet has that
# reaches past that -- the alternative is measuring the window in JS.
grid = read("frontend", "src", "components", "widgets", "WidgetGrid.jsx")
check("the placed dashboard is reachable from CSS",
      'className="wgt-grid"' in grid and 'className="wgt-cell"' in grid)
collapse = css.split(".wgt-grid {")[1].split("}\n}")[0] if ".wgt-grid {" in css else ""
check("...and collapses to one column below a tablet",
      "grid-template-columns: minmax(0, 1fr) !important;" in collapse, collapse)
check("...with the placement dropped, not just the track count",
      "grid-column: 1 / -1 !important;" in css and "grid-row: auto !important;" in css,
      "a widget still holding column 7 of a 1-column grid lands nowhere")
# One column means DOM order IS the layout, and the widgets are stored in
# creation order.
check("...in reading order, which only DOM order can express at one column",
      "(a.grid?.y || 0) - (b.grid?.y || 0) || (a.grid?.x || 0) - (b.grid?.x || 0)"
      in grid)
# The BUILDER keeps its grid: rearranging the canvas would show a layout that
# cannot be saved.
check("the builder's canvas scrolls instead of rearranging",
      ".builder-canvas { overflow-x: auto;" in css
      and ".builder-canvas > * { min-width: 720px; }" in css)

# -- 3. layouts that cannot honour their own minimum ---------------------------
# A track with a 300px floor inside a 360px screen does not shrink -- it
# overflows. Inline styles cannot be media-queried, so these become classes.
dataset = read("frontend", "src", "pages", "Dataset.jsx")
check("the capture page's split is a class, not an inline template",
      'className="capture-split"' in dataset
      and "minmax(0,2fr) minmax(300px,1fr)" not in dataset)
check("...and gives the camera the full line when both no longer fit",
      ".capture-split { grid-template-columns: minmax(0, 1fr); }" in css)

footage = read("frontend", "src", "pages", "Footage.jsx")
check("the footage viewer is a class too",
      'className="fx-lightbox"' in footage
      and 'className="fx-lightbox-media"' in footage)
check("...so a phone gets its own padding and picture height",
      ".fx-lightbox { padding: 8px; }" in css
      and ".fx-lightbox-media { max-height: 62vh; }" in css)
# Landscape on a handset is short, not narrow -- a width query never sees it.
check("...and a short landscape screen is handled by HEIGHT, not width",
      "@media screen and (max-height: 520px)" in css)
check("...and the viewer is not printed", ".lightbox, .fx-lightbox" in css)

# -- 4. side rails ------------------------------------------------------------
# Every rail in the app stacks at the same width. They used to differ (860 for
# the database navigator, 900 for everything else), which read as two products.
for rail, rule in (("the database navigator", ".db-shell { flex-direction: column;"),
                   ("the settings nav", ".settings-grid { grid-template-columns: 1fr; }"),
                   ("the stream rail", ".stream-layout { grid-template-columns: 1fr; }")):
    check(f"{rail} stacks on a tablet", rule in css)
tablet = css.split("@media screen and (max-width: 900px)")
check("...all of them at the SAME width",
      any(".db-shell { flex-direction: column;" in b for b in tablet[1:]),
      "the navigator stacked at 860 while every other rail stacked at 900")

# -- 5. touch ------------------------------------------------------------------
# Sized by INPUT, not by width: a 1024px tablet is touched and a small desktop
# window is clicked, so a width query answers the wrong question.
check("touch targets are set by pointer type, not by screen width",
      "@media (pointer: coarse) {" in css)
coarse = css.split("@media (pointer: coarse) {")[1].split("\n}")[0]
check("...at a size a finger can hit", "min-height: 38px" in coarse, coarse)

# -- 6. nothing scrolls the page sideways --------------------------------------
check("the page never scrolls sideways, whatever a child does",
      "html, body { overflow-x: clip; }" in css)
check("...wide tables scroll inside their own card instead",
      ".table-card { overflow-x: auto;" in css)

# -- the page gives width back when the sidebar takes it --------------------
#
# A flex item defaults to min-width:auto, which refuses to shrink below its
# content's intrinsic width. Without min-w-0 on the inset, expanding the
# sidebar did not reflow the page: it pushed it off the right-hand edge, and
# the header chips, the Edit layout button and the third dashboard card went
# out of view.
#
# It does not even scroll -- the rule above deliberately clips sideways
# overflow on html/body, which is right for a stray wide child and means this
# fault presented as content simply CUT OFF with no way to reach it.
#
# One class, invisible until somebody opens the sidebar, and easy to lose the
# next time this component is taken from upstream.
sidebar = read("frontend", "src", "components", "ui", "sidebar.jsx")
parts = sidebar.split("function SidebarInset", 1)
check("the shell declares a SidebarInset", len(parts) == 2)
inset = parts[1].split("\nfunction ")[0] if len(parts) == 2 else ""
check("...which may shrink when the sidebar expands", "min-w-0" in inset,
      "without it the page overflows to the right instead of reflowing, and "
      "the overflow is clipped rather than scrollable")

# The other half: the widest thing on the Analytics page must absorb the loss
# itself rather than push the page wider.
strip = read("frontend", "src", "components", "ui", "DayStrip.jsx")
check("the day strip scrolls inside its own rail",
      "overflowX:" in strip and "minWidth: 0" in strip,
      "a month of days must not set the page's minimum width")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
