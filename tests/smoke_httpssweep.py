# Leftover HTTPS key files are swept at the hub's start (core/services/
# httpsrun.py sweep, Pipi's HTTPS review L4) and are never committed.
#
#   a. an old .https-load-*.pem (a leaf key) and .https.*.tmp (the root key)
#      are deleted
#   b. a fresh one (a write in flight) is kept, as is every other file:
#      https.local.toml itself, a look-alike name, a directory, a link
#   c. a missing config directory is no error
#   d. .gitignore covers config/.https*
#
#     python tests/smoke_httpssweep.py
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

try:
    from core.services import httpsrun                            # noqa: E402
except ImportError as exc:                     # cryptography, via tlscert
    print(f"skip: {exc}")
    sys.exit(77)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
NOW = time.time()
OLD = NOW - 600


def make(name, age=OLD):
    p = os.path.join(CFG, name)
    with open(p, "w", encoding="ascii") as fh:
        fh.write("x")
    os.utime(p, (age, age))
    return p


make(".https-load-abc123.pem")
make(".https.k9z.tmp")
make(".https-load-fresh.pem", NOW - 5)
make("https.local.toml")
make(".https-load-abc.pem.keep")
make("other.https.tmp")
os.mkdir(os.path.join(CFG, ".https.dir.tmp"))
os.utime(os.path.join(CFG, ".https.dir.tmp"), (OLD, OLD))
target = make("target.txt")
link = os.path.join(CFG, ".https-load-link.pem")
try:
    os.symlink(target, link)
    has_link = True
except (OSError, NotImplementedError):          # Windows without the privilege
    has_link = False

gone = httpsrun.sweep(CFG, now=NOW)
left = set(os.listdir(CFG))
check("old leaf-key and root-key leftovers deleted",
      sorted(gone) == [".https-load-abc123.pem", ".https.k9z.tmp"], gone)
check("a fresh one (a write in flight) is kept", ".https-load-fresh.pem" in left)
check("https.local.toml and look-alike names are kept",
      {"https.local.toml", ".https-load-abc.pem.keep", "other.https.tmp"} <= left, left)
check("a directory with the name is kept", ".https.dir.tmp" in left)
if has_link:
    check("a link with the name is not followed or removed",
          ".https-load-link.pem" in left and os.path.exists(target))
else:
    print("skip a link with the name (no symlink privilege here)")
check("a missing config directory is no error",
      httpsrun.sweep(os.path.join(CFG, "nope"), now=NOW) == [])
ignore = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().splitlines()
check(".gitignore covers config/.https*", "config/.https*" in ignore)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
