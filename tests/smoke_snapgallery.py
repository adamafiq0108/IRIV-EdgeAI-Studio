# The Camera snaps dashboard card: the newest snaps with Previous / Next.
#
# It is a media card (like the live video): no key, never marked AI-off, the
# camera optional, the count kept between 1 and 20 (10 when unset). The page
# lists the snaps itself, so the server only carries whose and how many.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import builder                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


check("snap_gallery is a widget kind", "snap_gallery" in builder.KINDS)
def one(**kw):
    out = builder.validate([{"id": "g", "kind": "snap_gallery", "title": "Snaps", **kw}])
    return out[0]


check("it needs no key", one().get("kind") == "snap_gallery")
check("a count over 20 is cut to 20", one(count=99).get("count") == 20, one(count=99))
check("no count means 10", one().get("count") == 10, one())
check("a bad count means 10", one(count="x").get("count") == 10, one(count="x"))
check("a count of 0 is 1", one(count=0).get("count") == 1, one(count=0))
check("the camera is kept", one(camera="main").get("camera") == "main")

src = open(os.path.join(ROOT, "frontend", "src", "components", "widgets", "Widget.jsx"),
           encoding="utf-8").read()
check("the card pages with Previous and Next, newest first",
      "SnapGalleryCard" in src and 'aria-label="Previous snap"' in src
      and 'aria-label="Next snap"' in src)
check("it offers the Builder palette", 'kind: "snap_gallery"' in open(
    os.path.join(ROOT, "frontend", "src", "components", "widgets", "builderShared.jsx"),
    encoding="utf-8").read())

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
