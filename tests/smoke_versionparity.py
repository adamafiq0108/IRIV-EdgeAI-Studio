# The page and the server must agree on "is this release newer".
#
# WHAT WAS WRONG (fixed in 34da4b4): the Update button and the bell kept
# offering the release the box had just installed. The fix compares versions
# in TWO places: core/version.is_newer on the server (update_available, and
# apply() refusing what is not newer) and frontend/src/lib/version.js isNewer
# in the browser (offersUpdate, used by the card and the bell). If the two
# ever disagree -- 1.0.10 vs 1.0.9, a v-prefix, a -dev tag, a short tag --
# the page offers an Update the server then refuses, or hides one the server
# would install. smoke_updatestale checks the page CALLS isNewer; this runs
# both implementations over the same table and requires identical answers.
#
# Needs node (present wherever the frontend builds); skipped with a message
# otherwise.
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.version import is_newer                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


PAIRS = [
    ("1.0.10", "1.0.9"), ("1.0.9", "1.0.10"), ("v1.0.12", "1.0.12"),
    ("1.0.12", "v1.0.12"), ("V1.0.13", "1.0.12"), ("1.0.12", "1.0.12"),
    ("v1.0.13-dev", "1.0.12"), ("v1.0.12-dev", "1.0.12"),
    ("1.0.12", "1.0.12-dev"), ("v2", "1.9.9"), ("v1.1", "1.0.99"),
    ("1.0", "1.0.0"), ("1.10.0", "1.9.0"), ("", "1.0.0"), ("1.0.0", ""),
    ("nightly", "0.0.0"), ("1.0.12.4", "1.0.12"), (" v1.0.13 ", "1.0.12"),
    ("1.0.12-rc1", "1.0.11"), ("01.0.0", "1.0.0"),
]
node = shutil.which("node")
if not node:
    print("skip: no node on this machine - run where the frontend builds")
else:
    lib = os.path.join(ROOT, "frontend", "src", "lib", "version.js")
    script = (
        "import(process.argv[1]).then((m) => {"
        "  const pairs = JSON.parse(process.argv[2]);"
        "  console.log(JSON.stringify(pairs.map(([a, b]) => m.isNewer(a, b))));"
        "});")
    from pathlib import Path
    r = subprocess.run([node, "-e", script, Path(lib).as_uri(), json.dumps(PAIRS)],
                       capture_output=True, text=True)
    check("version.js loads and answers", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        page = json.loads(r.stdout.strip().splitlines()[-1])
        server = [bool(is_newer(a, b)) for a, b in PAIRS]
        for (a, b), p, s in zip(PAIRS, page, server):
            check(f"isNewer({a!r}, {b!r}): page {p} == server {s}", p == s)
        check("1.0.10 is newer than 1.0.9 (numeric, not text)", server[0] is True)
        check("the same version re-cut is not newer", server[5] is False)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("page and server agree on every version pair")
