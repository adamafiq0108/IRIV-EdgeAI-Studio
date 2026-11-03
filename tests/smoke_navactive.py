# Which rail entry is highlighted, and what the AI Preparation category is called.
#
# The rail decided "am I on this page" with pathname.startsWith(to). Two of the
# product's own routes share a prefix -- /dataset and /dataset-library -- so
# opening the Library lit up BOTH entries, and an operator could not tell from
# the rail which page they were on. A nav target matches when the path IS it,
# or continues with a new SEGMENT; "-library" is not a new segment.
#
# The category KEY stays `ai_train` whatever the label says: it is what a
# per-system visibility grant is stored under (users.local.toml
# [users.<name>.sections]), so renaming the key would silently drop every
# grant already written against it.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import nav as nav_service                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the label, on both sides ---------------------------------------------------------
sections = read("frontend", "src", "components", "nav-sections.js")
check("the category reads 'AI Preparation' on the server",
      nav_service.SECTION_LABELS["ai_train"] == "AI Preparation",
      nav_service.SECTION_LABELS["ai_train"])
check("...and in the browser", '"AI Preparation"' in sections)
check("...while the KEY stays ai_train, so existing grants still resolve",
      "ai_train" in nav_service.SECTION_PAGES
      and nav_service.SECTION_PAGES["ai_train"] == ("dataset", "dataset_library"))
check("nothing still says 'AI Train'",
      "AI Train" not in sections and "AI Train" not in read("core", "services", "nav.py"))

# -- the highlight ---------------------------------------------------------------------
app = read("frontend", "src", "state", "app.jsx")
check("there is one helper for 'is the rail on this page'", "export function atPath(" in app)
# The rule, read off the source: equal, or followed by a slash.
body = app[app.index("export function atPath("):]
body = body[:body.index("\n}")]
check("...and it matches on a SEGMENT boundary, not a bare prefix",
      'pathname === to' in body and '+ "/"' in body, body)

paths = dict(re.findall(r'(\w+):\s*"(/[^"]*)"', app[app.index("PAGE_PATHS = {"):]))
collide = [(a, b) for a in paths.values() for b in paths.values()
           if a != b and b.startswith(a) and not b[len(a):].startswith("/")]
check("the two routes that caused it are still a prefix pair, so this matters",
      ("/dataset", "/dataset-library") in collide, collide)

# Every shell and button asks the helper. A bare startsWith on a nav target is
# the bug coming back.
for f in ("MasterShell.jsx", "StandaloneShell.jsx", "NextPageButton.jsx", "SetupWizard.jsx"):
    src = read("frontend", "src", "components", f)
    stray = [ln.strip() for ln in src.splitlines()
             if "pathname.startsWith(" in ln and '"/manage/"' not in ln]
    check(f"{f} decides the active entry through atPath",
          "atPath" in src and not stray, stray)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
