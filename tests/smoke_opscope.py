# Running a system is not administering the platform.
#
# Start, stop, restart, autostart and the capture switches were all admin-only,
# so an operator standing in front of a stopped line could see that it was
# stopped and not start it -- the answer to "the line is down" was to go and
# find whoever holds the admin password. They are open to whoever was GRANTED
# the system now, which is the same authority that decides whether the row is
# visible at all: if you can see it, you can work it.
#
# What stays admin-only is everything that changes what the deployment IS
# rather than what it is doing: creating, renaming, importing, exporting,
# deleting a system's data. This pins both halves, because opening the first
# is only safe if the second held.
#
# fastapi is not installed on a dev box (the Pi has it), so the gate's DECISION
# is tested through the service call it is made of, and the route wiring is
# read from the source.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth as auth_service                # noqa: E402

CFG = tempfile.mkdtemp()
auth_service.set_master_password("adminpass1", CFG)
auth_service.create_user("lina", "password123", systems="line_a", config_dir=CFG)
auth_service.create_user("sam", "password123", systems="*", config_dir=CFG)

SRC = io.open(os.path.join(ROOT, "core", "routes", "hub.py"),
              encoding="utf-8").read()

# -- the gate is admin OR the grant, and nothing else -------------------------
gate_src = SRC[SRC.index("def _require_operator"):]
gate_src = gate_src[:gate_src.index("\ndef ", 5)]
assert "is_admin" in gate_src and "allowed" in gate_src, gate_src
assert "403" in gate_src, gate_src


def gate(user, slug):
    """What _require_operator decides, made of the same two calls."""
    return (auth_service.is_admin(user, CFG)
            or auth_service.allowed(user, slug, CFG))


assert gate("lina", "line_a"), "an operator cannot work their own system"
print("== the operator granted a system may operate it")

assert not gate("lina", "line_b"), "an operator reached somebody else's system"
print("== and only that one -- another team's line is refused")

assert gate("sam", "line_b"), "an all-systems grant did not carry"
assert gate("admin", "line_b"), "the administrator was refused"
print("== an all-systems grant and the administrator reach everything")

# A grant on an account that has been switched off grants nothing: disabling an
# account has to stop it DOING things, not merely stop it signing in.
auth_service.update_user("lina", enabled=False, config_dir=CFG)
assert not gate("lina", "line_a"), "a disabled account could still operate"
auth_service.update_user("lina", enabled=True, config_dir=CFG)
print("== a disabled account operates nothing")

# Nobody, and a name that does not exist, get the same answer as a stranger.
assert not gate("", "line_a") and not gate(None, "line_a")
assert not gate("nobody", "line_a")
print("== an unknown or missing caller is refused")


# -- which routes moved, and which did not ------------------------------------
def body(route_name):
    start = SRC.index(f'name="{route_name}"')
    end = SRC.index("@router.", start + 10)
    return SRC[start:end]


for name in ("hub.power", "hub.detection", "hub.autostart", "hub.capture"):
    b = body(name)
    assert "_require_operator" in b, f"{name} is still admin-only"
    assert "_require_admin" not in b, f"{name} carries both gates"
    # The gate is passed the route's OWN slug, not some other value: a gate
    # checking the wrong system is a gate that checks nothing.
    assert re.search(r"_require_operator\(request,\s*slug,", b), b
print("== power / detection / autostart / capture ask for the grant, on their own slug")

for name in ("hub.rename_system", "hub.export_system", "hub.import_system",
             "hub.remove_system", "hub.purge_system_data", "hub.create_system"):
    b = body(name)
    # Either form of the same gate: the shared helper, or the is_admin call
    # written out (a few of these predate the helper).
    assert "_require_admin" in b or "is_admin" in b, f"{name} lost its gate"
    assert "_require_site_admin" not in b, \
        f"{name} was opened to the admin tier -- which systems EXIST is the " \
        "box owner's decision, not an operation's"
    assert "_require_operator" not in b, f"{name} was opened up"
print("== create / rename / export / import / remove / purge stayed master-only")

# Retention is the exception, deliberately: how long an operation's own data
# is kept is that operation's business, and a site admin who cannot set it has
# to ask the box owner every time a policy changes.
b = body("hub.set_retention")
assert "_require_site_admin" in b, "retention lost its gate"
assert "_require_operator" not in b, "retention was opened to any operator"
print("== retention is the site admin's, everything else the box owner's")
print("ALL OK")
