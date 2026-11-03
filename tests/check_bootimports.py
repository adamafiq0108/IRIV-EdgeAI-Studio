"""The hub and every master must still IMPORT -- every in-repo import resolves.

Deleting a module is only safe when nothing imports it any more. Removing the
old Jinja page system (core/view.py) is the case that made this check: four
route modules and both servers imported `view`, and one left behind is an
ImportError at boot -- the hub or a master that never comes up, which no
route test notices because they import the routes they test one by one.

Two layers:
  1. static, everywhere: every `import core...` / `from core... import x` in
     the servers, core/ and the agent names a module (or a package member)
     that exists on disk. Cheap, and runs on a dev box without FastAPI.
  2. live, where FastAPI is installed (the box): actually import hub_server
     and master_server (uvicorn stubbed if absent), which runs every
     module-level import the way boot does.

    python tests/check_bootimports.py
"""
import ast
import importlib
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

bad = []


def module_exists(dotted):
    path = os.path.join(ROOT, *dotted.split("."))
    return os.path.isfile(path + ".py") or os.path.isfile(
        os.path.join(path, "__init__.py"))


def has_member(package, name):
    """`from package import name`: a submodule, or a name in __init__.py."""
    if module_exists(f"{package}.{name}"):
        return True
    init = os.path.join(ROOT, *package.split("."), "__init__.py")
    if not os.path.isfile(init):
        init = os.path.join(ROOT, *package.split(".")) + ".py"
    try:
        tree = ast.parse(open(init, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return True
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                (a.asname or a.name).split(".")[0] == name for a in node.names):
            return True
    return False


files = ["hub_server.py", "master_server.py"]
for top in ("core", "agent"):
    for dirpath, _d, names in os.walk(os.path.join(ROOT, top)):
        if "static" in dirpath.split(os.sep) or "__pycache__" in dirpath:
            continue
        files += [os.path.relpath(os.path.join(dirpath, n), ROOT)
                  for n in names if n.endswith(".py")]

checked = 0
for rel in files:
    try:
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
    except SyntaxError as exc:
        bad.append(f"{rel}: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in ("core", "agent"):
                    checked += 1
                    if not module_exists(a.name):
                        bad.append(f"{rel}:{node.lineno} import {a.name}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                and (node.module or "").split(".")[0] in ("core", "agent"):
            if not module_exists(node.module):
                bad.append(f"{rel}:{node.lineno} from {node.module}")
                continue
            for a in node.names:
                checked += 1
                if a.name != "*" and not has_member(node.module, a.name):
                    bad.append(f"{rel}:{node.lineno} from {node.module} "
                               f"import {a.name}")

print(f"== {checked} in-repo imports across {len(files)} files resolve"
      if not bad else "FAIL unresolved in-repo imports:\n  " + "\n  ".join(bad))

try:
    import fastapi                                             # noqa: F401
except ImportError:
    print("== FastAPI not installed here -- the live import runs on the box")
else:
    if "uvicorn" not in sys.modules:
        try:
            import uvicorn                                     # noqa: F401
        except ImportError:
            sys.modules["uvicorn"] = types.ModuleType("uvicorn")
            sys.modules["uvicorn"].run = lambda *a, **k: None
    for name in ("hub_server", "master_server"):
        try:
            importlib.import_module(name)
            print(f"== {name} imports")
        except Exception as exc:                               # noqa: BLE001
            bad.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"FAIL {name} does not import: {exc}")

if bad:
    sys.exit(1)
print("ALL OK")
