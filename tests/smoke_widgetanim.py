# How a card ARRIVES, and how it moves when its numbers change.
#
# Every chart in this product was built to hold still: each series said
# isAnimationActive={false}, because a wall display that redraws every few
# seconds must not twitch. That is still the default -- what changed is that
# an operator can now ASK for motion, per card, and the list of what they may
# ask for is one list shared by the Builder and the workflow canvas.
#
# The pinned properties are the ones that go wrong quietly: a default that
# starts moving on upgrade, a keyframe that ends somewhere other than where
# the card belongs, an option offered on a widget it cannot act on, and a
# choice the compiler drops on the next save.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service                # noqa: E402
from core.services import workflow as wf                            # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


shared = read("frontend", "src", "components", "widgets", "builderShared.jsx")
widget = read("frontend", "src", "components", "widgets", "Widget.jsx")
css = read("frontend", "src", "theme.css")
page = read("frontend", "src", "pages", "Workflow.jsx")

# -- 1. the vocabulary, and the one that must stay the default ----------------
WANT = ["fade_in", "fade_out", "scale_in", "scale_out", "slide_up",
        "slide_down", "slide_left", "slide_right", "rotate", "grow",
        "stagger", "morph", "opacity", "color", "enter_update_exit"]
check("every animation offered is a value the server accepts",
      all(a in builder_service.ANIMATIONS for a in WANT),
      [a for a in WANT if a not in builder_service.ANIMATIONS])
check("...and stillness is one of them, first",
      builder_service.ANIMATIONS[0] == "")
# A dashboard that has been still for a year must not start moving because the
# box was updated.
check("a card that never chose one stays still",
      builder_service.validate([{"id": "w1", "kind": "line", "source": "stat", "key": "seen"}])[0]["anim"] == "")
check("...and so does a card carrying a name the server does not know",
      builder_service.validate(
          [{"id": "w1", "kind": "line", "source": "stat", "key": "seen",
            "anim": "explode"}])[0]["anim"] == "")
check("a chosen one survives the save",
      builder_service.validate(
          [{"id": "w1", "kind": "line", "source": "stat", "key": "seen",
            "anim": "slide_up"}])[0]["anim"] == "slide_up")
# Replaying an ARRIVAL on every poll is the fastest way to make a wall display
# unreadable, so it is a second, separate decision -- and it is off.
check("replay-on-new-data is off unless asked for",
      builder_service.validate(
          [{"id": "w1", "kind": "line", "source": "stat", "key": "seen",
            "anim": "fade_in"}])[0]["anim_replay"] is False)
check("...and on when it is",
      builder_service.validate(
          [{"id": "w1", "kind": "line", "source": "stat", "key": "seen",
            "anim": "fade_in", "anim_replay": True}])[0]["anim_replay"] is True)

# -- 2. a workflow card keeps its choice ---------------------------------------
# The Builder's partial save deliberately keeps the stored compiled copy of a
# wf_* card, so a choice made only in the Builder is discarded on the next
# save. The template lives on the NODE for that reason; the animation has to
# live in the same place or it decays the same way.
graph = wf.clean_graph({"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "f1", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "seen", "name": "Seen", "mode": "realtime"}},
    {"id": "c1", "type": "dashboard.graph", "position": {"x": 2, "y": 0},
     "data": {"name": "Seen over time", "kind": "area", "metric": "count",
              "template": "gradient", "anim": "slide_up", "anim_replay": True}},
], "edges": [{"id": "e1", "source": "m1", "target": "f1"},
             {"id": "e2", "source": "f1", "target": "c1"}]})
out = wf.compile_graph(graph, [], ["person"], ["person"])
check("a dashboard node compiles cleanly with an animation", not out["errors"],
      out["errors"])
w = (out["widgets"] or [{}])[0]
check("...and the card carries it, beside the template",
      w.get("anim") == "slide_up" and w.get("template") == "gradient", w)
check("...replay included", w.get("anim_replay") is True, w)
check("a node that chose nothing compiles to stillness",
      wf._anim({}) == "" and wf._anim({"anim": "nope"}) == "")

# -- 3. one list, both editors -------------------------------------------------
# The picker lives in ONE place and is used by the dashboard editors only.
# It was on the workflow node as well, and two panels offering one setting is
# a question -- "which one wins?" -- whose answer, whichever was saved last,
# is invisible. A card is dressed where it is looked at; the choice is written
# back onto the node (/api/workflow/card-style) so a recompile keeps it.
check("the picker is shared, and written once",
      "export function AnimationPicker(" in shared
      and "<AnimationPicker kind={current.kind}" in shared)
check("...and the workflow node does not offer a second one",
      "AnimationPicker" not in page,
      "the node stores the animation; it does not ask for it")
check("...so the editor's choice is written back to the node",
      '"anim", "anim_replay",' in read("core", "services", "workflow.py")
      and "anim: w.anim" in read("frontend", "src", "pages", "Builder.jsx"),
      "without this a builder-set animation would last until the next "
      "recompile and then vanish")
check("the options are grouped by what they act on",
      "export const ANIM_FAMILIES" in shared
      and '["appear", "When it appears"]' in shared)
# An option that silently does nothing is worse than one that is not offered:
# there is nothing to draw on a number, a pill or a video feed.
check("the drawn-on ones are disabled where there are no shapes",
      "export const ANIM_DRAWS_SHAPES" in shared
      and 'disabled={fam === "draw" && !shapes}' in shared
      and '" - needs a chart"' in shared)
check("...and the replay tick is only offered for an arrival",
      'family === "appear" && chosen' in shared)

# -- 4. what the card actually does --------------------------------------------
# The value labels (LabelList) hold still on purpose, so a number that is
# already on screen stays put; only a SERIES must not refuse to animate.
check("every series asks the widget instead of hard-coding stillness",
      "isAnimationActive={false}" not in re.sub(
          r"<LabelList[\s\S]*?/>", "", widget.split("function animProps")[1]),
      "a series still refuses to animate unconditionally")
check("...and no-animation returns exactly the old behaviour",
      "if (!animated) return { isAnimationActive: false };" in widget)
check("a stagger delays each series by its position",
      "anim === \"stagger\"" in widget and "i * STAGGER_MS" in widget)
check("the animation moves the CARD, so the title travels with the picture",
      'className={`stat-card${animClass ? " " + animClass : ""}`}' in widget)
check("...and it only replays when the operator asked it to",
      "widget.anim_replay ? JSON.stringify(data ?? null) : \"\"" in widget)

# Every keyframe must END where the card belongs: a half-finished animation
# (a tab backgrounded mid-run, a reduced-motion override) can then never leave
# a dashboard misplaced.
anim_css = css.split("-- widget animations")[1]
for name in ("wgt-fade-in", "wgt-scale-in", "wgt-slide-up", "wgt-slide-left",
             "wgt-rotate"):
    body = anim_css.split(f"@keyframes {name} ")[1].split("}\n")[0]
    check(f"{name} ends at rest",
          "to { opacity: 1; transform: none; }" in body
          or "to { opacity: 1; }" in body, body)
check("the ones that blink return to full opacity",
      "100% { opacity: 1; }" in anim_css)
check("a colour change cross-fades rather than jumping",
      "transition: fill .6s ease, stroke .6s ease;" in anim_css)
# A dashboard on a factory wall is exactly where somebody may have turned
# motion off.
check("reduced motion switches all of it off",
      "@media (prefers-reduced-motion: reduce)" in anim_css
      and ".wgt-anim { animation: none !important; }" in anim_css)

# -- 5. the two names of a table ----------------------------------------------
# A table shown as "PCB-SENSATA-CAM1" is `pcb_sensata` in SQL: a display name
# may carry spaces, dashes and capitals, and a table name may not. A Query
# written from the header failed with "relation does not exist", naming the
# one thing the canvas never showed.
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
check("the Data Table node shows the name a Query must type",
      "table && d.dataset && table.name !== d.dataset" in nodes_src
      and 'title="the name to use in a Query"' in nodes_src)
check("...and the Query's column list leads with that name",
      'title="the table\'s name in SQL - click to write it in"' in page
      and "· shown as " in page)

# ...and the row stamp under the name the Database page shows.
console = read("core", "services", "sqlconsole.py")
check("the row stamp answers to the name the product shows: datetime",
      '"ts AS datetime"' in console
      and '["event_id", "datetime", "ts"]' in console)
check("...without breaking a query already written against ts",
      '"event_id", "ts", "ts AS datetime"' in console)

# -- 6. constants are visible where variables are looked for -------------------
check("the palette lists the canvas's constants",
      "Constant values ({constants.length})" in page
      and 'n.type === "variable.number"' in page)
check("...with what each one is set to, read from the node",
      'nd.value_type === "boolean"' in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
