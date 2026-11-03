# Threshold bands on a line chart.
#
# A line chart says how a number MOVED. It did not say whether the number was
# acceptable -- for that an operator had to read a value off the axis and
# compare it, every time, against a limit that lived in their head or in a
# rule on another page.
#
# So: thresholds on the value axis. N of them cut the axis into N+1 bands,
# each with its own colour, and the line takes the colour of the band it is
# passing through. "We went over at 14:20" is then read off the picture.
#
# The invariant this file mostly exists to defend: colours are ALWAYS exactly
# len(thresholds) + 1. A renderer that has to guess a band's colour when the
# list runs short draws a chart whose meaning depends on how far through the
# form the operator got.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as b                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. N thresholds, N+1 colours, always -------------------------------------
one = b.clean_bands({"at": [80]})
check("one threshold makes two bands", len(one["colors"]) == 2, one)
check("...coloured from the ramp when nothing was chosen",
      one["colors"] == [b.BAND_RAMP[0], b.BAND_RAMP[1]], one)
three = b.clean_bands({"at": [20, 50, 80], "colors": ["#111111"]})
check("three thresholds make four bands even from one colour",
      len(three["colors"]) == 4 and three["colors"][0] == "#111111", three)
check("...and a surplus of colours is cut to fit",
      len(b.clean_bands({"at": [5], "colors": ["#111111"] * 9})["colors"]) == 2)

# Order is not the operator's to get wrong: bands are ranges, and a range
# whose ends arrive in the wrong order has no values in it.
check("thresholds are sorted", b.clean_bands({"at": [80, 20, 50]})["at"]
      == [20.0, 50.0, 80.0])
# Two bands cannot share an edge -- "below 10, below 10, above 10" is not a
# question with an answer.
check("...and de-duplicated", b.clean_bands({"at": [10, 10, 10]})["at"] == [10.0])
check("no thresholds is no bands at all, not an empty shell",
      b.clean_bands({"at": []}) == {} and b.clean_bands(None) == {})
check("junk in the list is dropped, not crashed on",
      b.clean_bands({"at": ["x", None, 7]})["at"] == [7.0])
# A threshold nothing can be either side of is not a threshold, and NaN/inf
# travel through JSON as a null or an error.
check("...and so are NaN and infinity",
      b.clean_bands({"at": [float("nan"), float("inf"), 3]})["at"] == [3.0])
check("more than the cap is refused, not drawn as a rainbow",
      len(b.clean_bands({"at": list(range(20))})["at"]) == b.MAX_BANDS)
check("a colour that is not #rrggbb falls back to the ramp",
      b.clean_bands({"at": [1], "colors": ["red", "#00ff00"]})["colors"]
      == [b.BAND_RAMP[0], "#00ff00"])

# -- 2. it survives a save, and only where it means something ------------------
def widget(kind, **extra):
    return dict({"id": "w1", "kind": kind, "source": "history", "key": "count",
                 "range": "24h"}, **extra)


saved = b.validate([widget("line", bands={"at": [80], "colors": ["#111111"]})])[0]
check("a saved line chart keeps its bands", saved["bands"]["at"] == [80.0]
      and saved["bands"]["colors"][0] == "#111111", saved.get("bands"))
for kind in ("area", "spline"):
    check(f"...as does a {kind} chart, which also draws a line",
          b.validate([widget(kind, bands={"at": [3]})])[0]["bands"]["at"] == [3.0])
# A donut has no value axis to put a limit on, and a tile is one number: the
# band it is in is the only band there is.
# (A bar chart carries them since per-camera thresholds, 2026-10-13: each
# bar is painted by the band its value is in.)
check("a bar chart keeps its bands",
      b.validate([widget("bar", bands={"at": [3]})])[0]["bands"]["at"] == [3.0])
for kind in ("donut", "tile", "gauge"):
    check(f"a {kind} carries no bands -- it has no axis to put one on",
          b.validate([widget(kind, key="count",
                             bands={"at": [3]})])[0]["bands"] == {})
check("a chart with no bands set stays exactly as it was",
      b.validate([widget("line")])[0]["bands"] == {})

# The rendered dict is the ONLY shape the pages see. Leaving a field out of it
# is how a treatment can be stored, saved and previewed and still render plain
# everywhere -- the same fault `template` had.
rendered = read("core", "services", "builder.py").split("\"data\": data, \"builder\": True")[0]
check("the rendered widget carries its bands to the page",
      '"bands": (clean_bands(w.get("bands"))' in rendered)

# -- 3. the workflow canvas sets them too --------------------------------------
# A wf_* card is rebuilt from its node on every compile, so a threshold that
# the compiler does not write is a threshold the next save throws away.
wf = read("core", "services", "workflow.py")
check("the compiler cleans bands by the SAME rule as a save",
      "builder_service.clean_bands((data or {}).get(\"bands\"))" in wf,
      "a second rule here is how the two places drift apart")
check("...and every card it builds carries them",
      wf.count('"bands": _bands(d),') == wf.count('"anim": _anim(d),'),
      f'{wf.count(chr(34) + "bands" + chr(34) + ": _bands(d),")} of '
      f'{wf.count(chr(34) + "anim" + chr(34) + ": _anim(d),")} cards')

# -- 4. the picture ------------------------------------------------------------
w = read("frontend", "src", "components", "widgets", "Widget.jsx")
# The day-colouring added a second gradient that wants the same stroke; the
# bands still win it, because "the colour is the level" is the more urgent
# reading of the two (see test_daycolour.mjs).
# (A camera's OWN bands come first since per-series thresholds, 2026-10-13.)
check("the line's stroke is the band gradient",
      "const strokeOf = ownStops[s.key]" in w and ": stops ? `url(#${gid}-bands)`" in w
      and w.index("stops ? `url(#${gid}-bands)`") < w.index("dayCuts ? `url(#${gid}-days)`"))
# Each boundary emits TWO stops at one offset: a gradient interpolates, and a
# threshold that fades over ten pixels is one nobody can point at.
stops = w.split("function bandStops(")[1].split("\nfunction ")[0]
check("...with hard stops, so a boundary is a boundary",
      "stops.push({ off, color: colors[i + 1] });" in stops
      and "stops.push({ off, color: colors[i] });" in stops)
# An SVG gradient defaults to objectBoundingBox: 0 and 1 are the top and bottom
# of the painted shape, which for a stroke is the line's own extent. Using the
# AXIS range would misplace every colour change on any chart whose data does
# not fill its axis.
check("...measured against the line's own range, which is what the box IS",
      "Math.min(...nums), Math.max(...nums)" in w)
check("...and a flat line still gets the band it is sitting in",
      "if (!(span > 0))" in stops)
check("the thresholds are drawn as well as coloured",
      "<ReferenceLine key={`thr-${i}`} y={t}" in w)
check("...and the axis makes room for one above the data",
      "ifOverflow=\"extendDomain\"" in w and "const yTop = bands" in w)
# Colouring is for ONE series. "The line's colour is its level" and "each
# line's colour is which line it is" are two meanings for one channel.
check("banding applies to a single series only",
      "series.length === 1 && !stacked && !forecast" in w)
check("...but the limit lines are drawn whatever else is going on",
      "{bands && bands.at.map((t, i) => (" in w)

ui = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("the editor keeps the N+1 invariant while you type",
      "export function BandEditor(" in ui and "function commit(nextAt, nextColors)" in ui)
check("...re-sorting as you type, so the rows match the chart",
      "sort((a, b) => a[0] - b[0])" in ui)
check("...offered only where there is an axis to put a limit on",
      'export const BAND_KINDS = ["line", "area", "spline", "bar"];' in ui)
check("a new widget starts with no thresholds", "bands: {}," in ui)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
