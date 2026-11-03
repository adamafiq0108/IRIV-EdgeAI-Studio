"""A card says what it was told to say, and nothing else.

Two settings that looked broken from the outside, for opposite reasons:

  CAPTION appeared when nobody wrote one. Every data builder invented a
  fallback -- the aggregate ("count"), the column name, the word "latest",
  even the widget's own label -- so a wall of tiles carried a wall of small
  grey words, on some cards and not others depending on which builder made
  them. Clearing the field handed the fallback straight back, so there was no
  way to turn one off.

  UNIT never appeared at all on a tile. It was only ever rendered as a
  stand-in caption, so a card WITH a caption showed the caption and the unit
  went nowhere -- "every time I change it, nothing happens", correctly. A
  gauge had always drawn it beside the number, which is where a unit belongs.

    python tests/smoke_cardcaption.py
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


def tile(**extra):
    w = {"id": "w1", "title": "Rejects", "kind": "tile", "source": "stat",
         "key": "k", **extra}
    return builder_service.compute([w], [], {"k": 418})[0]["data"]


# -- 1. the caption is the operator's line ------------------------------------
check("a card nobody captioned says nothing under its value",
      tile()["sub"] == "",
      "the aggregate, the column name and the word 'latest' are not captions")
check("...a card that was captioned says it", tile(sub="per shift")["sub"] == "per shift")
check("...and a unit is not pressed into service as one",
      tile(unit="pcs")["sub"] == "",
      "a unit standing in for a caption is why some cards had a line and "
      "others did not")

# Every builder, not just the one that happened to be tested: the fallbacks
# were spread across six of them and each invented something different.
src = read("core", "services", "builder.py")
check("one function decides what a caption is",
      "def _caption(w):" in src
      and 'return str(w.get("sub") or "").strip()[:60]' in src)
check("...and no builder invents one behind its back",
      'w.get("sub") or w.get("unit")' not in src
      and '"sub": w.get("sub") or' not in src,
      "each of these was a card that captioned itself")

# -- 2. the unit is a unit ----------------------------------------------------
check("a tile carries its unit to the card", tile(unit="pcs")["unit"] == "pcs")
check("...alongside a caption, not instead of it",
      tile(sub="per shift", unit="pcs") == {**tile(sub="per shift", unit="pcs"),
                                            "sub": "per shift", "unit": "pcs"})
check("...and a card with no unit sends none", tile()["unit"] is None)

widget = read("frontend", "src", "components", "widgets", "Widget.jsx")
check("the tile draws the unit beside its number",
      "{data.unit}" in widget and 'fontSize: "0.45em"' in widget)
check("...only for a number",
      "Number.isFinite(Number(data.value))" in widget and "!widget.format" in widget,
      '"VIOLATE pcs" is not a reading')

# -- 3. an empty caption takes no space ---------------------------------------
check("no caption, no line at all",
      "{(d?.since || data.sub) && (" in widget,
      "an empty .stat-sub is still a block with a margin, so the card "
      "reserved the space for a line it was not drawing")
check("...and the comparison line still earns its place",
      "d?.since" in widget,
      "that one is a fact about the number rather than a label for it")
check("...with no label standing in for the caption",
      "data.sub || data.label" not in widget)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
