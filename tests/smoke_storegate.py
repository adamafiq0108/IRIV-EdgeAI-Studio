# The template store is developer-only, on every route.
#
# It is the PRODUCT's distribution channel, not a deployment's tool: what a
# customer box runs is what was installed on it, and a catalogue of other
# people's templates in an operator's -- or an admin's -- hub is an invitation
# to install something nobody chose for that deployment.
#
# Browsing used to be open to any signed-in user and installing was merely
# admin, so hiding the nav item hid the button and left the URL answering.
# This asserts the SERVER gate, because that is the one an attacker meets.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

SRC = io.open(os.path.join(ROOT, "core", "routes", "store.py"),
              encoding="utf-8").read()

# Every route function in the file, with its body up to the next decorator.
routes = []
for m in re.finditer(r'@router\.(get|post|delete)\("([^"]+)"', SRC):
    start = m.start()
    nxt = SRC.find("@router.", m.end())
    routes.append((m.group(2), SRC[start:nxt if nxt > 0 else len(SRC)]))

assert routes, "no store routes found -- did the file move?"
for path, body in routes:
    assert "_require_developer" in body, f"{path} is not developer-gated"
    assert "_require_admin" not in body, f"{path} still uses the admin gate"
print(f"== all {len(routes)} store routes call _require_developer:")
for path, _ in routes:
    print(f"     {path}")

# ...and the gate it calls is the real one, not a local stub that always
# passes. _require_developer lives in core/routes/hub.py and asks
# auth.is_developer.
HUB = io.open(os.path.join(ROOT, "core", "routes", "hub.py"),
              encoding="utf-8").read()
gate = HUB[HUB.index("def _require_developer"):]
gate = gate[:gate.index("\n@router")]
assert "auth_service.is_developer" in gate, gate
assert "403" in gate, gate
print("== and that gate asks auth.is_developer, answering 403")

# The UI follows: the nav entry and the panel are developer-gated AND spread
# away in a customer build, same as the other developer panels -- a customer
# bundle should not carry a page whose every endpoint would refuse it.
SHELL = io.open(os.path.join(ROOT, "frontend", "src", "components",
                             "HubShell.jsx"), encoding="utf-8").read()
nav = SHELL[SHELL.index('id: "store"'):]
nav = nav[:nav.index("\n")]
assert "devOnly: true" in nav, nav
assert 'const StorePanel = DEV_TOOLS ?' in SHELL, "StorePanel is not a dev-only chunk"
# The panel needs the ACCOUNT and the BOX: `devTools` is the developer flag
# AND the server's word that core/dev is mounted here. A dev frontend on a box
# in customer mode used to draw the store with no routes behind it.
assert 'section === "store"' in SHELL and "devTools && StorePanel" in SHELL
assert "const devTools = !!user?.is_developer && !!user?.dev_tools" in SHELL, \
    "devTools no longer requires both the account and the box"
assert "user?.is_admin && <StorePanel" not in SHELL, "an admin path to the store survives"
print("== the nav entry, the panel and its chunk are all developer-only")
print("ALL OK")
