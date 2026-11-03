# The node inspector's rhythm, made once instead of per node.
#
# Every node's form is built from four things: a Field (label over control), a
# row of controls, a checkbox line, and a paragraph of explanation. Each had
# grown its own spacing one node at a time, so a form read as a stack of
# unrelated lines -- the label sat 3px off its control (as close to the field
# ABOVE as to its own), a checkbox was jammed against its sentence with no
# room to press, repeatable rows floated with no edges, and the Save that ends
# a node's setup sat below the fold on any long form.
#
# All of it is CSS on the shared primitives, which is the point: there is one
# rhythm and it applies to every node, including the ones nobody has drawn yet.
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


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


css = read("frontend", "src", "theme.css")
page = read("frontend", "src", "pages", "Workflow.jsx")

# -- one Field, one rhythm -----------------------------------------------------
check("every field is the same component", "function Field({ label, hint," in page)
check("...whose spacing is a class, not an inline number",
      'className="wf-field"' in page and 'className="wf-field-label"' in page
      and "marginBottom: 8, minWidth: 0" not in page)
# The rhythm now lives on `.rail-form`, which BOTH property rails wear --
# the workflow node inspector and the Builder's Properties panel. They do the
# same job on the same narrow column and had grown two dialects.
check("...the label sitting nearer its own control than the field above",
      ".rail-form .wf-field, .rail-form .ui-field {" in css
      and "margin-bottom: var(--sp-4)" in css
      and "margin-bottom: var(--sp-1); font-size: var(--fs-xs)" in css)
check("...and the Builder's rail reads the same, from the same rule",
      'className="builder-inspector rail-form"'
      in read("frontend", "src", "pages", "AnalyticsBuilder.jsx")
      and ".rail-form .ui-field > .ui-field-label" in css)
# A row of select + number + button must read as ONE line: same height.
check("controls share a height", "--wf-h: 32px;" in css
      and "min-height: var(--wf-h);" in css)
check("...and a keyboard user can see the focus", ":focus-visible {" in css
      and "outline-offset: -1px;" in css)

# -- a checkbox is a control, not a sentence with a box next to it -------------
check("checkbox lines are a class of their own", ".rail-form .wf-check {" in css)
check("...the whole row being the target",
      "cursor: pointer;" in css.split(".wf-check {")[1].split("}")[0])
check("...with the box given room and the accent colour",
      "width: 15px; height: 15px" in css and "accent-color: var(--accent" in css)
check("...and every one of them converted",
      # The Trigger's ticks moved to its own inspector on 2026-10-14.
      (page.count('className="wf-check"') + read("frontend", "src", "workflow",
       "TriggerInspector.jsx").count('className="wf-check"')) >= 5
      and 'style={{ gap: 8, alignItems: "flex-start", margin: "10px 0 14px" }}'
      not in page)

# -- a repeatable row is one thing ---------------------------------------------
check("conditions, cases and bands are boxed rows",
      ".rail-form .wf-listrow {" in css and 'className="wf-listrow"' in page)
check("...numbered in a fixed gutter so ten of them line up",
      ".wf-listrow-n {" in css and "min-width: 26px" in css
      and 'className="wf-listrow-n"' in page)
check("...with a square remove control, red only on hover",
      ".rail-form .wf-x {" in css and "color: var(--alert)" in css
      and 'className="btn wf-x"' in page)
check("...and no lowercase x doing the job of a glyph",
      ">x</button>" not in page)
check("\"add another\" reads as the end of a list, not the form's button",
      ".rail-form .wf-add {" in css and "border-style: dashed" in css
      and page.count("wf-add") >= 4)

# -- explanation reads as explanation ------------------------------------------
check("help paragraphs get line-height and their own weight",
      ".rail-form p.small, .rail-form p.muted {" in css
      and "line-height: 1.55" in css)

# -- the end of a form is reachable from anywhere in it ------------------------
check("Save is pinned to the bottom of the rail",
      ".wf-inspector .wf-done {" in css and "position: sticky" in css
      and 'className="wf-done"' in page)
check("...over the rail's own background, not the canvas",
      "background: var(--surface, #16181d);" in css.split(".wf-done {")[1])

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
