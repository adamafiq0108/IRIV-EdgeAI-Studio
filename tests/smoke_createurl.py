# Creating (or importing) a system answers with an address the browser can
# open, not the box's loopback.
#
# UAT-104 (Ochobot, first run on board "iriv", 2026-10-04): POST
# /api/hub/systems returned "url": "http://127.0.0.1:5102" and the wizard
# toasted "Starting at http://127.0.0.1:5102" to a person on a laptop, while
# the card on the grid (rewritten onto the request's host) said
# http://192.168.1.21:5102. Both answers now go through the same rewrite.
#
#     py -3 tests/smoke_createurl.py
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core import net                                           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def req(host):
    return types.SimpleNamespace(url=types.SimpleNamespace(hostname=host))


got = net.rewrite("http://127.0.0.1:5102", req("192.168.1.21"))
check("the loopback master url becomes the browser's host, same port",
      got == "http://192.168.1.21:5102", got)
got = net.rewrite("http://127.0.0.1:5102", req("iriv.local"))
check("...by name too", got == "http://iriv.local:5102", got)

with open(os.path.join(ROOT, "core", "routes", "hub.py"), encoding="utf-8") as f:
    hub = f.read()
create = hub[hub.index('def create_system('):]
create = create[:create.index("\n@router")]
check("the create route answers with the rewritten url",
      '**created, "url": net.rewrite(created.get("url") or "", request)}' in create,
      create[-400:])
imp = hub[hub.index("def import_system("):]
imp = imp[:imp.index("\n@router")]
check("...and so does the import route",
      '**created, "url": net.rewrite(created.get("url") or "", request)}' in imp,
      imp[-400:])

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
