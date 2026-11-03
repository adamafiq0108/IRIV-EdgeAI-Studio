# The ACTIONS tuple must describe the audit log that actually exists.
#
# WHAT IS WRONG TODAY. core/services/audit.py:28 calls ACTIONS "canonical
# action names ... so callers don't drift on spelling and the viewer can offer
# a stable filter list". Both halves have drifted, in both directions.
#
# Eleven constants are never written by anything: device.register,
# device.assign, config.sync, device.restart, device.shutdown, app.restart,
# credentials.rotate, token.view, credentials.revoke, builder.save,
# builder.publish. The last two exist only as FastAPI route NAMES
# (core/routes/builder.py:293,329) -- publishing a layout that then renders
# for every operator on the box writes no audit row at all. And
# core/services/credentials.py:43 searches the log for action ==
# "credentials.rotate" to find when the credential was last rotated; nothing
# ever writes one, so that lookup is permanently blank and the page it feeds
# is permanently wrong.
#
# Eleven written actions are not constants: password_reset,
# password_reset_link, password_reset_requested, password_reset_approved,
# password_reset_rejected, account.rename, system.settings, session.policy,
# session.revoke, session.revoke_others, camera.sdk. The Audit page builds its
# filter dropdown from ACTIONS (core/routes/audit.py:57 ->
# frontend/src/pages/Audit.jsx:39), so an admin asking "who reset that
# password" cannot filter for it: the action is in the table and not in the
# menu.
#
# WHY IT MATTERS IN THE FIELD. An audit trail is read exactly once -- after
# something has gone wrong, by someone who was not there. A menu that offers
# categories nothing writes, and hides the ones that matter, is worse than no
# menu: it is read as "nothing of that kind happened here".
#
# This file is red until the two sets agree.
import ast
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services.audit import ACTIONS                 # noqa: E402

#: The wrapper and the SQL themselves -- they call record() by definition and
#: are not call sites.
NOT_A_CALLER = {os.path.join("core", "services", "audit.py"),
                os.path.join("core", "repos", "audit.py")}

SKIP_DIRS = {".git", ".claude", "node_modules", "__pycache__", "tests",
             "frontend", "docs", "venv", ".venv"}


def py_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT)
            if rel in NOT_A_CALLER:
                continue
            yield rel, path


def _base(func):
    """The object a method was called on: audit_service.record -> 'audit_service'."""
    value = getattr(func, "value", None)
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


def is_record_call(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record"
            and _base(node.func) in ("audit", "audit_service", "audit_repo"))


def action_of(node):
    """The literal action a record() call writes, or None if it is a variable."""
    if node.args and isinstance(node.args[0], ast.Constant) \
            and isinstance(node.args[0].value, str):
        return node.args[0].value
    for kw in node.keywords:
        if kw.arg == "action" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            return kw.value.value
    return None


written = {}            # action -> "file:line"
dynamic_files = []      # files that pass record() a variable
for rel, path in py_files():
    src = io.open(path, encoding="utf-8").read()
    if "record(" not in src:
        continue
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if not is_record_call(node):
            continue
        action = action_of(node)
        if action is None:
            dynamic_files.append((rel, src))
        else:
            written.setdefault(action, f"{rel}:{node.lineno}")

assert written, "no audit record() call sites were found at all - has the " \
                "service been renamed? this test is then looking at nothing"

# A caller that builds the action name at runtime (core/routes/folders.py:57
# passes it in, core/routes/remote.py:36 wraps it) still SPELLS its actions as
# literals in the same file. Those count as written; anything else in such a
# file does not, so a stray string cannot make an unreachable constant look
# reachable.
for rel, src in dynamic_files:
    for action in ACTIONS:
        if f'"{action}"' in src or f"'{action}'" in src:
            written.setdefault(action, f"{rel} (via a helper)")

# -- every action the code writes must be a constant the viewer can offer -----
unlisted = sorted(a for a in written if a not in ACTIONS)
assert not unlisted, (
    "these actions are written to the audit log but are missing from ACTIONS, "
    "so the Audit page cannot filter for them:\n  "
    + "\n  ".join(f"{a}  ({written[a]})" for a in unlisted))
print("== every recorded action appears in ACTIONS")

# -- and every constant must be something the code actually writes ------------
never = [a for a in ACTIONS if a not in written]
assert not never, (
    "these actions are offered in the filter menu and nothing ever writes "
    "one, so the log reads as 'this never happened here':\n  "
    + "\n  ".join(never))
print("== every ACTIONS entry is written by something")

# credentials.py reads the log looking for this one. If it is not written, the
# rotation date that page shows is blank for ever and nobody can tell whether
# the credential on a board has ever been rotated.
assert "credentials.rotate" in written, \
    "core/services/credentials.py:43 searches for credentials.rotate and " \
    "nothing writes it"
print("== credentials.rotate is written, so the rotation lookup can find one")


# -- the destructive routes must write a row ----------------------------------
#
# The set above only proves the vocabulary is consistent. It would stay green
# if the eleven unwritten constants were simply deleted, leaving the product
# exactly as unaudited as it is now. These are the actions where "who did
# this" is the only question anybody will ask afterwards, and today not one of
# them records anything: an account deleted, a role changed, a system's
# footage purged, a board terminal handed to another program. The evidence is
# one line in a 2000-entry in-memory ring that a restart empties.
#
# (route file, the route's name=, what it destroys)
MUST_AUDIT = [
    ("core/routes/auth.py", "users.create", "a new account exists"),
    ("core/routes/auth.py", "users.update", "an account's role and permissions changed"),
    ("core/routes/auth.py", "users.delete", "an account is gone"),
    ("core/routes/auth.py", "users.reset_password", "an account was taken over"),
    ("core/routes/auth.py", "roles.delete", "a role is gone"),
    ("core/routes/hub.py", "hub.remove_system", "a system is gone"),
    ("core/routes/hub.py", "hub.purge_system_data", "a system's recorded data is gone"),
    ("core/routes/hub.py", "hub.set_board_release", "a terminal was handed to another program"),
    ("core/routes/models.py", "detector.snapshots_delete", "footage is gone"),
    ("core/routes/models.py", "detector.dataset_delete", "a dataset is gone"),
]


def route_name(fn):
    """The name= on a @router.post/delete/put/patch decorator, if any."""
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not (isinstance(func, ast.Attribute)
                and func.attr in ("post", "delete", "put", "patch")):
            continue
        for kw in dec.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                return kw.value.value
    return None


def called_names(fn):
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                out.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                out.add(node.func.attr)
    return out


def audits(fn, helpers):
    """Does this route write a row -- directly, or through a local helper?"""
    if any(is_record_call(n) for n in ast.walk(fn)):
        return True
    return bool(called_names(fn) & helpers)


missing = []
for rel, name, what in MUST_AUDIT:
    src = io.open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # One level of indirection: a module-level helper that records counts,
    # which is how folders.py and remote.py already do it.
    helpers = {f.name for f in funcs
               if route_name(f) is None
               and any(is_record_call(n) for n in ast.walk(f))}
    route = next((f for f in funcs if route_name(f) == name), None)
    assert route is not None, \
        f"{rel} has no route named {name!r} any more - this test needs updating"
    if not audits(route, helpers):
        missing.append(f"{rel}:{route.lineno}  {name}  -- {what}, and nothing says who")

assert not missing, (
    "these destructive routes write no audit row:\n  " + "\n  ".join(missing))
print("== every destructive route in the list writes an audit row")

print("ALL OK")
