# Every read-modify-write of a system's config.local.toml holds the config
# lock -- in this process AND across processes (core/services/cfg_lock.py).
#
# Yaya (on 6c3c541): set_hud and set_modbus_server read the file OUTSIDE the
# lock and wrote it back through _write_local, so a hub Start landing between
# their read and their write was lost -- deterministically, in two processes.
# The lock itself was fine; two writers had simply never been put under it.
# One missed writer is enough, so this does not list writers: it FINDS them.
#
#   1. every function in core/ and systems/demo/app that calls _write_local or
#      _write_pkg_local is decorated with a config lock (@locked, @dc.locked,
#      @_pkg_locked) or makes the call inside a `with` that takes one
#      (pkg_config_lock / config_lock / _config_lock / edit_local)
#   2. only the two low-level writers call cfg_lock.write
#   3. the race Yaya reproduced, run for real: a locked writer in one process
#      and set_hud in another lose nothing
import ast
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


WRITERS = {"_write_local", "_write_pkg_local"}
LOCK_DECORATORS = {"locked", "_pkg_locked"}
LOCK_WITH = {"pkg_config_lock", "config_lock", "_config_lock", "edit_local"}
# The writers themselves, and the lock's own implementation.
EXEMPT = {"_write_local", "_write_pkg_local", "edit_local", "write"}


def _name(node):
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def files():
    for top in ("core", os.path.join("systems", "demo", "app")):
        for dirpath, _dirs, names in os.walk(os.path.join(ROOT, top)):
            for n in names:
                if n.endswith(".py"):
                    yield os.path.join(dirpath, n)


def unlocked_calls(fn):
    """Calls to a writer in `fn` that no config-lock `with` encloses."""
    out = []

    def walk(node, guarded):
        for child in ast.iter_child_nodes(node):
            g = guarded
            if isinstance(child, (ast.With, ast.AsyncWith)):
                if any(_name(item.context_expr) in LOCK_WITH
                       for item in child.items):
                    g = True
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
                continue              # a nested def is judged on its own
            if isinstance(child, ast.Call) and _name(child) in WRITERS \
                    and not g:
                out.append(child.lineno)
            walk(child, g)

    walk(fn, False)
    return out


offenders, writers_seen, raw_writers = [], 0, []
for path in files():
    rel = os.path.relpath(path, ROOT)
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
        if any(_name(c) == "write" and isinstance(c.func, ast.Attribute)
               and _name(c.func.value) == "cfg_lock" for c in calls):
            raw_writers.append(node.name)
        if node.name in EXEMPT:
            continue
        if not any(_name(c) in WRITERS for c in calls):
            continue
        writers_seen += 1
        if any(_name(d) in LOCK_DECORATORS for d in node.decorator_list):
            continue
        lines = unlocked_calls(node)
        if lines:
            offenders.append(f"{rel}:{node.name} (line {', '.join(map(str, lines))})")

check(f"every config.local.toml writer holds the config lock "
      f"({writers_seen} found)", not offenders and writers_seen >= 15,
      "\n      ".join(offenders) or writers_seen)
check("only the two low-level writers call cfg_lock.write",
      sorted(raw_writers) == ["_write_local", "_write_pkg_local"], raw_writers)

# -- the race, for real ------------------------------------------------------------
probe = os.path.join(tempfile.mkdtemp(), "hudrace.py")
with open(probe, "w", encoding="utf-8") as f:
    f.write(f'''import os, sys, tempfile
sys.path.insert(0, {ROOT!r})
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
from core.services import detector_control as dc
pkg, role = sys.argv[1], sys.argv[2]
dc._pkg_dir = lambda sd, slug: pkg
row = dc.HUD_ROWS[0][0]
for i in range(150):
    if role == "hub":
        with dc.edit_local(pkg) as local:
            local["rev"] = {{"n": int((local.get("rev") or {{}}).get("n") or 0) + 1}}
    elif role == "hud":
        dc.set_hud("/x", "demo", {{row: i % 2 == 0}})
    else:
        dc.set_modbus_server("/x", "demo", {{"enabled": i % 2 == 0}})
''')
pkg = tempfile.mkdtemp()
open(os.path.join(pkg, "config.local.toml"), "w").close()
procs = [subprocess.Popen([sys.executable, probe, pkg, role])
         for role in ("hub", "hud", "modbus")]
for p_ in procs:
    p_.wait()
import tomllib                                                  # noqa: E402
with open(os.path.join(pkg, "config.local.toml"), "rb") as f:
    doc = tomllib.load(f)
check("a locked writer beside set_hud and set_modbus_server in two other "
      "processes: every write survives",
      (doc.get("rev") or {}).get("n") == 150 and "hud" in doc
      and "modbus_server" in doc, doc)
check("...and no temp file is left behind by the racing writers",
      not [n for n in os.listdir(pkg) if n.endswith(".tmp")],
      os.listdir(pkg))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all config-writer checks passed")
