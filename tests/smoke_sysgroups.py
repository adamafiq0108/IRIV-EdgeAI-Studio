# Folders for the hub grid: a workshop's systems ARE its template catalogue,
# and a flat list of them is only tellable apart by remembering which is which.
#
# A group is display metadata. It changes no port, slug, package or access, so
# the only invariant that really matters is the one this file spends most of
# its assertions on: editing the FILING must never change what is filed.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = STORE

from core import config                                      # noqa: E402
from core.services import system_groups as sg                # noqa: E402

CFG = tempfile.mkdtemp()

# 1. it arrives useful. A workshop sorts systems into built-in, free and paid
#    before anyone asks it to, so the feature works the first time it is
#    opened instead of being an empty tool.
assert [g["id"] for g in sg.groups(CFG)] == ["builtin", "free", "pro"]
print("== defaults out of the box:", [g["name"] for g in sg.groups(CFG)])

sg.ensure_defaults(CFG)
assert [g["id"] for g in sg.groups(CFG)] == ["builtin", "free", "pro"]
print("== and they persist once written")

# 2. create, rename and reorder are one write, and the ID is what survives a
#    rename -- it is what each filed system points at, so renaming a folder
#    must not empty it
sg.assign("demo_a", "free", CFG)
assert sg.assignments(CFG) == {"demo_a": "free"}, sg.assignments(CFG)
sg.set_groups([{"id": "free", "name": "Community templates"},
               {"id": "builtin", "name": "Built-in templates"},
               {"id": "pro", "name": "Pro templates"}], CFG)
assert [g["name"] for g in sg.groups(CFG)][0] == "Community templates"
assert sg.assignments(CFG) == {"demo_a": "free"}, "a rename emptied the folder"
print("== renaming and reordering keeps what is filed:", sg.assignments(CFG))

# 3. DELETING A FOLDER DOES NOT TOUCH THE SYSTEMS IN IT. They fall back to
#    unfiled and can be re-filed; a delete of a heading must never quietly
#    rewrite what sat under it.
sg.set_groups([{"id": "builtin", "name": "Built-in templates"}], CFG)
assert sg.assignments(CFG) == {"demo_a": "free"}, \
    "deleting the folder rewrote the system's filing"
assert "free" not in {g["id"] for g in sg.groups(CFG)}
print("== deleting a folder leaves its systems alone; they read as unfiled")

# ...and re-creating it by the same name puts them straight back
sg.set_groups([{"id": "builtin", "name": "Built-in templates"},
               {"id": "free", "name": "Free templates"}], CFG)
assert sg.assignments(CFG)["demo_a"] == "free"
print("== recreating it refiles them, because the id is the link")

# 4. filing writes the SAME table `name` and `owner` live in, and never
#    disturbs the url a master is reached on
local = config.load_local("systems", CFG)
local.setdefault("masters", {})["demo_b"] = {"url": "http://127.0.0.1:5123",
                                             "owner": "dev", "name": "Demo B"}
config.save_local("systems", local, CFG)
sg.assign("demo_b", "builtin", CFG)
entry = (config.systems(CFG) or {})["masters"]["demo_b"]
assert entry["url"] == "http://127.0.0.1:5123", entry
assert entry["owner"] == "dev" and entry["name"] == "Demo B", entry
assert entry["group"] == "builtin", entry
print("== filing leaves url, owner and name untouched:", entry)

# unfiling removes the key rather than storing a blank one
sg.assign("demo_b", "", CFG)
entry = (config.systems(CFG) or {})["masters"]["demo_b"]
assert "group" not in entry and entry["url"], entry
print("== unfiling removes the key, keeping the rest of the entry")

# 5. a folder nobody made cannot be filed into -- a typo would otherwise
#    create a heading that exists only as a dangling reference
try:
    sg.assign("demo_b", "nope", CFG)
except ValueError as exc:
    print("== filing into a folder that does not exist is refused:", exc)
else:
    raise AssertionError("filed into a nonexistent folder")

# 6. the usual name hygiene, since these ids are written to a config file
for bad in ([{"name": ""}],
            [{"name": "Free"}, {"name": "free"}],
            [{"name": "!!!"}],
            [{"name": f"f{i}"} for i in range(sg.MAX_GROUPS + 1)]):
    try:
        sg.set_groups(bad, CFG)
    except ValueError:
        continue
    raise AssertionError(f"{bad} was accepted")
print("== blank, duplicate, unusable and too-many folder names are refused")
print("ALL OK")
