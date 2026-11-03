"""A module may not call a private name it does not have.

Python resolves a global name when the line RUNS, so a call to a function that
was never defined or imported is not a syntax error, not an import error, and
not something any test catches unless it happens to exercise that exact line.
It is a 500 the first time a user reaches it.

That is what happened to "create a new system": core/routes/hub.py called
_apply_format_workflow, which lives in core/dev/formats.py and was never
imported. Creating a system failed with "internal error -- see Logs" on every
box, and nothing in the suite noticed, because no test creates a system.

Scoped to names starting with "_" on purpose. A private name is by convention
defined in the module that uses it, so an undefined one is nearly always this
bug rather than a dynamic import or a re-export -- which keeps this check
free of the false positives that would make it get switched off.

    python tests/check_undefined_names.py
"""
import ast
import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDERS = ("core", "systems/demo/app")
BUILTINS = set(dir(builtins))


def defined_names(tree):
    """Every name the module binds at any level: defs, imports, assignments.

    Deliberately flat rather than scope-aware. A name bound anywhere in the
    module is treated as available everywhere in it, which can only make this
    check quieter -- and a quiet check that people keep is worth more than a
    precise one they turn off.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            args = node.args if hasattr(node, "args") else None
            if args:
                for a in (args.posonlyargs + args.args + args.kwonlyargs):
                    names.add(a.arg)
                for a in (args.vararg, args.kwarg):
                    if a:
                        names.add(a.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


def used_private_names(tree):
    """(name, line) for every private global name the module READS."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.id.startswith("_") and not node.id.startswith("__"):
            out.append((node.id, node.lineno))
    return out


def main():
    bad = 0
    checked = 0
    for folder in FOLDERS:
        base = os.path.join(ROOT, folder)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read())
                except SyntaxError as exc:
                    print(f"{path}: cannot parse -- {exc}")
                    bad += 1
                    continue
                checked += 1
                have = defined_names(tree) | BUILTINS
                seen = set()
                for name, line in used_private_names(tree):
                    if name in have or name in seen:
                        continue
                    seen.add(name)
                    rel = os.path.relpath(path, ROOT).replace("\\", "/")
                    print(f"{rel}:{line}: {name} is used but never defined or "
                          "imported here -- it raises NameError the first time "
                          "this line runs")
                    bad += 1
    if bad:
        print(f"\n{bad} undefined name(s).")
        return 1
    print(f"== {checked} modules define every private name they use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
