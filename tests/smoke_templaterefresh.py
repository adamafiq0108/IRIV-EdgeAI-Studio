# The template FOLLOWS its workbench: creating a system from a built-in
# format re-snapshots the format's linked workbench system at create time,
# so an edited workbench does not keep minting systems from the snapshot
# taken the day it was first published (the drag-out/drag-in republish
# dance nobody remembered).
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import provision                            # noqa: E402

# 1. The create route refreshes the format from its workbench BEFORE the
#    scaffold (the scaffold reads the format's starting model), keeps the
#    developer's filing (category/label/hint/zone), and falls back to the
#    last published version rather than blocking the create.
with open(os.path.join(ROOT, "core", "routes", "hub.py"),
          encoding="utf-8") as f:
    hub_src = f.read()
create = hub_src.split('name="hub.create_system"', 1)[1]
create = create.split("def _rebuild_registry", 1)[0]
refresh = create.split("THE TEMPLATE FOLLOWS ITS WORKBENCH", 1)
assert len(refresh) == 2, "create_system lost the workbench refresh"
block = refresh[1]
assert block.index("provision_service.promote_template") \
    < block.index("provision_service.create_vision_system"), \
    "the refresh must happen before the scaffold reads the format"
for kept in ('category=old.get("category")', 'label=old.get("label")',
             'hint=old.get("hint")', 'zone=old.get("zone")'):
    assert kept in block, f"a refresh must not wipe the format's {kept}"
assert "last published version" in block, \
    "a failed refresh must fall back, with a warning, not block the create"

# 2. Republishing an existing (non-draft) format keeps it published:
#    save_format preserves the stored draft state on an edit, so the
#    create-time refresh cannot quietly unpublish a template.
CFG = tempfile.mkdtemp()
provision.save_format("t_refresh", {"label": "T", "widgets": []}, CFG)
fmt = provision.builtin_formats(CFG, include_hidden=True).get("t_refresh")
assert fmt and fmt.get("draft"), "a brand-new format starts as a draft"
provision.set_format_draft("t_refresh", False, CFG)
provision.promote_template("t_refresh", {"name": "T2"}, CFG)
fmt = provision.builtin_formats(CFG).get("t_refresh")
assert fmt and not fmt.get("draft"), \
    "republishing (the create-time refresh) must not flip a format to draft"

# 3. The workbench link the refresh relies on round-trips.
provision.set_format_workbench("t_refresh", "some_workbench", CFG)
assert provision.format_workbench(CFG)["t_refresh"] == "some_workbench"
provision.set_format_workbench("t_refresh", "", CFG)
assert "t_refresh" not in provision.format_workbench(CFG)

print("smoke_templaterefresh OK")
