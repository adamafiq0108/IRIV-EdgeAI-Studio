# Changing a password must leave a trace, on every path that can change one.
#
# WHAT IS WRONG TODAY. There are three ways a password changes on this box,
# and the ordinary one is the silent one.
#
#   * the emailed reset link  -- core/routes/auth.py:710. Writes an audit row
#     (:739), posts "Your password was changed" to the account (:744) and
#     tells the admins (:748). This is the shape.
#   * the front-panel reset   -- core/routes/auth.py:756. Writes a row (:795)
#     and posts the notice (:797).
#   * POST /api/auth/password -- core/routes/auth.py:804. Writes NOTHING. No
#     audit row, no notice to the account, no notice to the admins.
#
# WHY IT MATTERS IN THE FIELD. This is the account-takeover path. Whoever
# holds a live session -- an unattended browser, a stolen cookie -- changes
# the password here, and auth.py:855 then revokes every other session of that
# account. The real owner is locked out, opens the Audit page to find out what
# happened, and finds nothing: the route records no action, and the only
# evidence anywhere is one access-log line in a 2000-entry in-memory ring that
# the next restart empties.
#
# core/services/inbox.py:5-9 already describes this notice as a thing the
# inbox exists for -- "your password was changed, if that was not you, tell
# your administrator". It is wired for two paths out of three, and the missing
# one is the one an attacker would use, because it is the only one that needs
# nothing but the session already open in front of them.
#
# The admins notice is required here for the same reason the emailed path
# requires it (auth.py:748): "tell your administrator" is not advice a locked
# out account can act on, so the administrator has to be told by the box.
#
# This file is red until the third path behaves like the other two.
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

SRC = io.open(os.path.join(ROOT, "core", "routes", "auth.py"),
              encoding="utf-8").read()
TREE = ast.parse(SRC)


def route_name(fn):
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
                and dec.func.attr in ("post", "delete", "put", "patch"):
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    return None


FUNCS = {route_name(f): f for f in ast.walk(TREE)
         if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
         and route_name(f)}


def calls(fn, base, attr):
    """Every `base.attr(...)` call inside this route."""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == attr \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == base:
            out.append(node)
    return out


def kwargs_of(call):
    return {kw.arg for kw in call.keywords}


def first_action(call):
    if call.args and isinstance(call.args[0], ast.Constant):
        return call.args[0].value
    for kw in call.keywords:
        if kw.arg == "action" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


# -- the reference paths, so this test fails loudly if THEY regress -----------
for name, where in (("auth.reset_json", "the emailed reset link"),
                    ("auth.panel_reset", "the front-panel reset")):
    fn = FUNCS.get(name)
    assert fn is not None, f"{name} is gone - this test needs updating"
    assert calls(fn, "audit_service", "record"), \
        f"{where} stopped writing an audit row"
    assert calls(fn, "inbox_service", "post"), \
        f"{where} stopped telling the account its password changed"
print("== the emailed and front-panel resets still audit and notify")

# -- and now the path that does neither ---------------------------------------
fn = FUNCS.get("auth.change_own_password")
assert fn is not None, \
    "POST /api/auth/password is gone - this test needs updating"

rows = calls(fn, "audit_service", "record")
assert rows, (
    "POST /api/auth/password (core/routes/auth.py:%d) writes no audit row. "
    "An account taken over through a live session leaves nothing on the "
    "Audit page." % fn.lineno)

action = first_action(rows[0])
assert action, "the audit row is written with a non-literal action name"
assert action in ACTIONS, (
    f"the row is written as {action!r}, which is not in ACTIONS "
    "(core/services/audit.py:28) - the Audit page cannot filter for it")
# Where from. "If that was not you" is unanswerable without an address to
# compare against the one the owner was sitting at.
assert "source_ip" in kwargs_of(rows[0]), \
    "the audit row carries no source_ip, so it cannot say where the change " \
    "came from"
print("== POST /api/auth/password writes an audit row, named and sourced")

notices = calls(fn, "inbox_service", "post")
assert notices, (
    "the account is never told its own password changed. core/services/"
    "inbox.py:5-9 describes this notice; two of the three password paths "
    "post it and this one does not.")
# To the account, not to a hard-coded name: whoever is signed in is whose
# password just changed.
to = notices[0].args[0] if notices[0].args else None
assert isinstance(to, ast.Name), \
    "the notice is not addressed to the signed-in account"
print("== the account gets a notice that its password changed")

admins = calls(fn, "inbox_service", "notify_admins")
assert admins, (
    "the admins are never told. A locked-out account is told to contact its "
    "administrator, and the administrator has no way to know -- the emailed "
    "path tells them (core/routes/auth.py:748) and this one does not.")
print("== the admins are told too, like the emailed reset path")

print("ALL OK")
