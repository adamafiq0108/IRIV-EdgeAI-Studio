# Roles are all removable; the developer account is invisible to everyone else.
#
# Two rules that were each half-implemented:
#
# * "engineer / operator / viewer" shipped as UNDELETABLE built-ins, so a site
#   that uses none of them was stuck displaying all three. A role grants
#   nothing here (access is the systems allowlist plus the admin account), so
#   removing one narrows a dropdown, never anyone's access. admin and
#   developer are NOT roles -- an account name and a flag -- so neither is in
#   this list and neither can be removed through it.
#
# * the developer account was listed to admins, who could then touch none of
#   it (every control is refused). Filtering it in the PAGE would have hidden
#   the row, not the payload, so it is filtered in the route.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                      # noqa: E402
from core.services import auth as auth_service                # noqa: E402

CFG = tempfile.mkdtemp()

# -- every role is removable --------------------------------------------------
assert set(auth_service.roles(CFG)) == set(auth_service.BUILTIN_ROLES)

left = auth_service.delete_role("engineer", CFG)
assert "engineer" not in left, left
assert auth_service.roles(CFG) == left
print("== a built-in role can be removed:", left)

# ...and the removal is recorded rather than the built-in list being rewritten,
# so re-adding it reads as the built-in it is instead of a custom duplicate.
back = auth_service.create_role("engineer", CFG)
assert back.count("engineer") == 1, back
assert "engineer" in back
print("== and added back once, not twice")

# -- the guards that matter ---------------------------------------------------
auth_service.delete_role("engineer", CFG)
auth_service.delete_role("viewer", CFG)
only = auth_service.roles(CFG)
assert only == ["operator"], only
try:
    auth_service.delete_role("operator", CFG)
    raise AssertionError("the last role was removed")
except ValueError as exc:
    assert "only role" in str(exc), exc
    print("== the last role cannot be removed:", exc)

# A role still worn by an account stays: deleting it would leave that account
# labelled with something the platform no longer knows about.
auth_service.create_role("shift lead", CFG)
auth_service.create_user("bob", "password123", role="shift lead",
                         systems="*", config_dir=CFG)
try:
    auth_service.delete_role("shift lead", CFG)
    raise AssertionError("a role in use was removed")
except ValueError as exc:
    assert "still assigned" in str(exc), exc
    print("== a role still assigned cannot be removed:", exc)

# admin and developer are not roles, so they were never in this list at all.
assert "admin" not in auth_service.roles(CFG)
assert "developer" not in auth_service.roles(CFG)
print("== admin and developer are not roles and cannot be deleted as ones")

# -- the developer account is filtered server-side ----------------------------
SRC = io.open(os.path.join(ROOT, "core", "routes", "auth.py"),
              encoding="utf-8").read()
route = SRC[SRC.index('@router.get("/api/users"'):]
route = route[:route.index("@router.post")]
assert "is_developer" in route and 'u.get("developer")' in route, route
# ...and it is the LIST that is filtered, not a field blanked out.
assert re.search(r'if not auth_service\.is_developer\(\s*me', route), route
print("== /api/users hides developer accounts from everyone but a developer")
print("ALL OK")
