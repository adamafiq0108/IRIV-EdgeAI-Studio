"""How big the words on a card are.

TWO settings, not one: a card's chrome (its title, and the note under its
value) and the card's answer (the number or word it exists to show) are read at
different distances. A wall display wants a huge number over a small title; a
dense page of small cards wants the opposite. One "font size" could say
neither.

The part worth testing hardest is the OVERRIDE. Most cards on a real dashboard
are compiled from workflow nodes, and a compiled card's row is rewritten on
every recompile -- so a size stored on the widget itself would appear to work
and then quietly vanish, which is worse than not offering the control.

    python tests/smoke_cardfont.py
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


TILE = {"id": "w1", "kind": "tile", "source": "stat", "key": "count"}

# -- 1. the vocabulary --------------------------------------------------------
check("four sizes", builder_service.TEXT_SIZES
      == ("small", "medium", "large", "huge"))
check("anything unknown is MEDIUM",
      builder_service._as_text_size("enormous") == "medium"
      and builder_service._as_text_size(None) == "medium",
      "every card was drawn at medium, so a layout that says nothing must "
      "render exactly as it did")

# -- 2. it survives validation ------------------------------------------------
out = builder_service.validate([{**TILE, "label_size": "small",
                                 "value_size": "huge"}])[0]
check("a widget carries both sizes",
      out["label_size"] == "small" and out["value_size"] == "huge", out)
out = builder_service.validate([TILE])[0]
check("...and one that names neither is medium twice",
      out["label_size"] == "medium" and out["value_size"] == "medium")
out = builder_service.validate([{**TILE, "label_size": ["big"],
                                 "value_size": 99}])[0]
check("...and nonsense from a browser is medium, not an error",
      out["label_size"] == "medium" and out["value_size"] == "medium")

# -- 3. the override, which is the whole point --------------------------------
fonts = builder_service._as_fonts(
    {"fonts": {"wf_a": {"label": "small", "value": "huge"},
               "wf_b": {"label": "medium", "value": "medium"},
               "wf_c": {"label": "nonsense"}}})
check("an override is kept for a card that says something",
      fonts.get("wf_a") == {"label": "small", "value": "huge"}, fonts)
check("...and dropped for one that only writes the default down",
      "wf_b" not in fonts,
      "a map full of mediums grows for ever and changes nothing")
check("...and a nonsense size is not an override at all",
      "wf_c" not in fonts, fonts)

tmp = tempfile.mkdtemp()
builder_service.save(tmp, "smoke", {
    "pages": [{"id": "main", "name": "Dashboard",
               "widgets": [{**TILE, "id": "wf_a"}]}],
    "fonts": {"wf_a": {"label": "small", "value": "huge"}}})
back = builder_service.load(tmp, "smoke")
w = back["pages"][0]["widgets"][0]
check("a saved override is applied to the widget on the way OUT",
      w.get("label_size") == "small" and w.get("value_size") == "huge",
      "every consumer -- the dashboard route, Analytics, the editors -- sees "
      "the dressed widget without having to know overrides exist")

merged = builder_service.merge_partial(
    {"slug": "s", "pages": [], "fonts": {"wf_a": {"label": "small"}}},
    {"pages": []})
check("a partial save that mentions no fonts keeps the stored ones",
      merged["fonts"] == {"wf_a": {"label": "small"}},
      "the Analytics builder only ever saves partially")
merged = builder_service.merge_partial(
    {"slug": "s", "pages": [], "fonts": {"wf_a": {"label": "small"}}},
    {"pages": [], "fonts": {"wf_a": {"label": "huge"}}})
check("...and one that does, replaces them",
      merged["fonts"] == {"wf_a": {"label": "huge"}})

# -- 3b. ...and reaches the page -----------------------------------------------
# compute() rebuilds every widget into the ONE shape the pages see, field by
# field. A field missing from that dict does not exist as far as the browser is
# concerned -- the value is stored, overridden, previewed in the inspector, and
# then rendered as the default. That has now caught `template`, `bands`, and
# both of these plus the accent colour: every setting an operator changed on
# Analytics appeared to do nothing.
rendered = builder_service.compute(
    [{"id": "w1", "title": "T", "kind": "tile", "source": "stat", "key": "k",
      "color": "#ff0000", "bg": "#00ff00",
      "label_size": "huge", "value_size": "small"}],
    [], {"k": 5})[0]
for field, want in (("label_size", "huge"), ("value_size", "small"),
                    ("color", "#ff0000"), ("bg", "#00ff00"),
                    ("template", "plain")):
    check(f"a rendered widget still carries {field}",
          rendered.get(field) == want,
          f"got {rendered.get(field)!r} -- anything a card is DRAWN from has "
          "to be listed in compute()'s output dict")

# The Analytics Overview dresses PACKAGE cards itself, from the same maps.
analytics_src = read("core", "services", "analytics.py")
check("the Overview applies every override, not three of five",
      'accented = layout.get("accents")' in analytics_src
      and 'fonts = layout.get("fonts")' in analytics_src,
      "a package card took a new colour in the editor and rendered in the old "
      "one, because only templates and colors were applied here")


# -- 3c. the colour survives every branch --------------------------------------
# A card's colour is copied into its `data` by whichever builder produced it,
# and _sql_data has three early returns: a failed statement, and one that came
# back with no columns. Both dropped the colour, so a red tile went BLUE the
# moment its query broke or returned nothing -- which reads as "the colour
# setting does nothing", and is the harder version of that complaint because
# the card is showing a colour nobody chose.
src = read("core", "services", "builder.py")
sql_block = src.split("def _sql_data", 1)[1].split("def _dataset_data", 1)[0]
returns = [r for r in sql_block.split("return {")[1:]]
for i, block in enumerate(returns):
    head = block[:220]
    # A table has no accent to carry; every other shape must keep it.
    if '"columns":' in head and '"rows":' in head and '"truncated"' in head:
        continue
    check(f"_sql_data return #{i + 1} carries the colour",
          '"color"' in head,
          "every branch, including the ones that failed: " + head[:90])

card = read("frontend", "src", "components", "widgets", "Widget.jsx")
check("...and the card falls back to the widget's own colour anyway",
      card.count("data.color || widget.color || accent") >= 3,
      "the colour is duplicated into `data` by each builder, so one branch "
      "forgetting it should not repaint the card in the page default")


# -- 4. the browser renders it ------------------------------------------------
widget = read("frontend", "src", "components", "widgets", "Widget.jsx")
check("medium is 1, so an untouched card is untouched",
      "TEXT_SCALE = { small: 0.8, medium: 1, large: 1.3, huge: 1.7 }" in widget)
check("the sizes travel as CSS variables, not as inline font sizes",
      '"--label-scale": scaleOf(widget.label_size)' in widget
      and '"--value-scale": scaleOf(widget.value_size)' in widget,
      "a variable leaves the stylesheet its say, so the phone and tablet "
      "rules still shrink the chosen size instead of being overridden")
check("...and a tile whose value is a WORD is scaled too",
      "textValue(data.value, scaleOf(widget.value_size))" in widget,
      "that size is inline, so it wins over the stylesheet and would have "
      "ignored the setting on exactly those cards")

theme = read("frontend", "src", "theme.css")
for cls, px, var in ((".stat-label", "12px", "label"),
                     (".stat-value", "32px", "value"),
                     (".stat-sub", "11px", "label")):
    rule = theme.split(cls + " {", 1)[1].split("}", 1)[0]
    check(f"{cls} scales with --{var}-scale",
          f"calc({px} * var(--{var}-scale, 1))" in rule, rule.strip()[:70])
check("the responsive rules scale too",
      "calc(28px * var(--value-scale, 1))" in theme
      and "calc(24px * var(--value-scale, 1))" in theme,
      "a fixed 28px on a tablet would have ignored the setting there")

shared = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("the inspector offers the title/note size on every kind",
      'label="Title & note size"' in shared and "TextSizePicker" in shared)
check("...and the value size only where there IS a value",
      'HAS_VALUE = ["tile", "gauge", "verdict"]' in shared
      and "HAS_VALUE.includes(current.kind)" in shared,
      "a control that does nothing on eight of ten shapes is one nobody "
      "trusts on the other two")

for page in ("AnalyticsBuilder.jsx", "Builder.jsx"):
    src = read("frontend", "src", "pages", page)
    # A COMPILED card sends its sizes to its node with the rest of its look;
    # a PACKAGE card, which has no node, keeps them in the override map. Both
    # halves matter: the map was the only home the package card ever had, and
    # the editors used to clear it for those cards and never write it back.
    check(f"{page} sends a compiled card's sizes to its node",
          "label_size: w.label_size" in src and "value_size: w.value_size" in src,
          "without this the control works until the next recompile")
    check(f"{page} keeps a package card's sizes in the override map",
          "setFont(fonts, w.id, w);" in src
          and ("fonts," in src or "fonts })" in src or "fonts, names" in src),
          "a package widget is declared in Python; the map is where its "
          "dressing lives")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
