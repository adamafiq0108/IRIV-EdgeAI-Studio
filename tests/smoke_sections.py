# Per-system nav-category grants: the admin (or a developer) decides, per
# account and per system, which of the six sidebar categories that account
# sees. Storage is users.local.toml [users.<name>.sections]; enforcement is
# the MASTER's /api/bootstrap building a filtered nav server-side, with the
# SPA bouncing typed URLs back onto a visible page.
import json
import os
import re
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth                                # noqa: E402
from core.services import nav                                 # noqa: E402

CFG = tempfile.mkdtemp()

# 1. The six categories exist under stable keys, and the frontend's grouping
#    (nav-sections.js) names the SAME six keys -- a category on one side only
#    is a grant nobody can see or a rail group nobody can hide.
assert nav.SECTION_KEYS == ("overview", "ai_config", "data", "ai_train",
                            "integration", "diagnostics")
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "nav-sections.js"), encoding="utf-8") as f:
    njs = f.read()
front_keys = re.findall(r'key:\s*"([a-z_]+)"', njs)
assert tuple(front_keys) == nav.SECTION_KEYS, \
    f"frontend sections {front_keys} drifted from nav.SECTION_KEYS"
# ...and every nav page the backend can emit belongs to a section, so no
# grant silently leaves a controlled page visible.
emittable = {p for p, _l, _n in nav.PAGES} | {p for p, _l, _n in nav.ADMIN_PAGES}
mapped = {p for pages in nav.SECTION_PAGES.values() for p in pages}
assert emittable <= mapped, f"unmapped nav pages: {sorted(emittable - mapped)}"

# 2. nav.build honours a grant: only pages of allowed sections survive, for
#    admin pages too; None still means everything; an unmapped extra page is
#    never dropped.
system = types.SimpleNamespace(extra_pages=[{"page": "custom", "label": "X"}])
full = {i["page"] for i in nav.build(system, set(), is_admin=True)}
assert "detection" in full and "models" in full and "database" in full
only = {i["page"] for i in nav.build(system, set(), is_admin=True,
                                     sections={"overview", "diagnostics"})}
assert "main" in only and "events" in only and "logs" in only
assert "detection" not in only and "models" not in only \
    and "database" not in only and "dataset" not in only and "blynk" not in only
assert "custom" in only, "an unmapped package page must never vanish"

# 3. Grants persist per account per system; "*" (or the full set) clears the
#    row rather than storing an all-list that would go stale.
auth.create_user("op1", "password123", "counting,intrusion", CFG)
auth.update_user("op1", sections={"counting": ["overview", "diagnostics"]},
                 config_dir=CFG)
assert auth.section_grants("op1", "counting", CFG) == {"overview", "diagnostics"}
assert auth.section_grants("op1", "intrusion", CFG) is None, \
    "an unmentioned system keeps full visibility"
auth.update_user("op1", sections={"counting": list(nav.SECTION_KEYS)},
                 config_dir=CFG)
assert auth.section_grants("op1", "counting", CFG) is None
auth.update_user("op1", sections={"counting": "overview"}, config_dir=CFG)
auth.update_user("op1", sections={"counting": "*"}, config_dir=CFG)
assert auth.section_grants("op1", "counting", CFG) is None

# 4. Bad input refused: unknown keys, and an empty list (a system with no
#    visible category is a grant pretending to be a revocation).
for bad in ({"counting": ["nonsense"]}, {"counting": []}):
    try:
        auth.update_user("op1", sections=bad, config_dir=CFG)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass

# 5. Revoking a system takes its category grant with it -- a stale row must
#    not re-apply itself if the system is ever granted back.
auth.update_user("op1", sections={"counting": ["data"]}, config_dir=CFG)
auth.update_user("op1", systems="intrusion", config_dir=CFG)
assert auth.section_grants("op1", "counting", CFG) is None

# 6. The admin and a developer are never narrowed, and public_users carries
#    the grants so the accounts page can draw the chips.
assert auth.section_grants(auth.admin_username(CFG), "counting", CFG) is None
assert auth.section_grants("", "counting", CFG) is None
rows = {u["username"]: u for u in auth.public_users(CFG)}
assert "sections" in rows["op1"]

# 7. The wiring: the PATCH route forwards `sections`, and the master's
#    bootstrap both filters its nav with the grant and reports it to the SPA.
with open(os.path.join(ROOT, "core", "routes", "auth.py"),
          encoding="utf-8") as f:
    assert 'sections=body.get("sections")' in f.read()
with open(os.path.join(ROOT, "core", "routes", "spa.py"),
          encoding="utf-8") as f:
    spa = f.read()
assert "section_grants" in spa and "sections=grants" in spa
assert '"sections": "*" if grants is None else sorted(grants)' in spa

# 8. The SPA side: the accounts page offers the per-system categories (rows
#    of the access tree now, checkbox on the right -- see AccessTree),
#    and both system shells bounce a typed URL off a hidden category.
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "AccountsPanel.jsx"), encoding="utf-8") as f:
    acct = f.read()
assert "AccessTree" in acct and "Leave at least one category visible" in acct
assert "toggleSection" in acct, "the category grant lost its control"
with open(os.path.join(ROOT, "frontend", "src", "state", "app.jsx"),
          encoding="utf-8") as f:
    appjs = f.read()
assert "export function navRedirect" in appjs
# /builder is never in the nav (reached from the Dashboard's Edit button),
# so the guard must not police it -- it bounced the admin's own click once.
assert 'CONTROLLED.delete("builder")' in appjs
for shell in ("StandaloneShell.jsx", "MasterShell.jsx"):
    with open(os.path.join(ROOT, "frontend", "src", "components", shell),
              encoding="utf-8") as f:
        src = f.read()
    assert "navRedirect" in src and "<Navigate to={barred} replace />" in src, \
        f"{shell} does not guard hidden pages"

# 9. The stored TOML round-trips through the product's own writer (nested
#    [users.<name>.sections] tables were the gotcha to check).
auth.update_user("op1", systems="counting,intrusion", config_dir=CFG)
auth.update_user("op1", sections={"counting": ["overview"]}, config_dir=CFG)
import tomllib                                                # noqa: E402
with open(os.path.join(CFG, "users.local.toml"), "rb") as f:
    disk = tomllib.load(f)
assert disk["users"]["op1"]["sections"]["counting"] == "overview"

print("smoke_sections OK", json.dumps(sorted(nav.SECTION_KEYS)))
