# The administrator is a DEFAULT identity, not a fixed one.
#
# The account shipped as the literal string "admin" in every check that
# mattered -- who may administer, who may open which system, which credential
# store a login is verified against -- so a deployment could never be moved off
# the name every unit of this product ships with. It is now a stored name with
# "admin" as its default, and this pins the three things that has to mean:
#
# * every check follows the rename, in both directions (the new name gains what
#   the old one had, and the old name keeps nothing);
# * the name cannot collide with an operator account, because the session
#   cookie carries a username and could not tell two identities apart;
# * the administrator appears on the accounts page as a row, which is what it
#   is -- listing zero accounts on a box somebody is signed into was a lie.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth as auth_service                # noqa: E402

CFG = tempfile.mkdtemp()

# -- the default --------------------------------------------------------------
assert auth_service.admin_username(CFG) == "admin"
assert auth_service.ADMIN_DEFAULT == "admin"
auth_service.set_master_password("firstpass1", CFG)
assert auth_service.verify_login("admin", "firstpass1", CFG)
assert auth_service.is_admin("admin", CFG)
print("== a fresh box ships as 'admin'")

# -- the rename ---------------------------------------------------------------
auth_service.set_admin_username("Adam", CFG)                  # case is folded
assert auth_service.admin_username(CFG) == "adam"

# The new name IS the administrator: same password, same powers, same access.
assert auth_service.verify_login("adam", "firstpass1", CFG)
assert auth_service.is_admin("adam", CFG)
assert auth_service.allowed("adam", "any-system", CFG)
print("== the new name carries the credential and the powers")

# ...and the old one is nobody. This is the half that a literal comparison
# scattered through the routes would have got wrong: a check left behind on
# "admin" would keep admitting a name that no longer belongs to anyone.
assert not auth_service.verify_login("admin", "firstpass1", CFG)
assert not auth_service.is_admin("admin", CFG)
assert not auth_service.allowed("admin", "any-system", CFG)
print("== and the old name keeps nothing")

# The name it was renamed FROM is now free to be an operator account, and the
# one it was renamed TO is not.
auth_service.create_user("admin", "password123", systems="*", config_dir=CFG)
try:
    auth_service.create_user("adam", "password123", systems="*", config_dir=CFG)
    raise AssertionError("an operator took the administrator's name")
except ValueError as exc:
    assert "adam" in str(exc), exc
    print("== an operator cannot take the administrator's name:", exc)

# ...and the collision is refused from the other side too.
try:
    auth_service.set_admin_username("admin", CFG)
    raise AssertionError("the administrator took an operator's name")
except ValueError as exc:
    assert "already an operator" in str(exc), exc
    print("== nor the administrator an operator's:", exc)
assert auth_service.admin_username(CFG) == "adam"

# A username is one word: it is typed at a login prompt and stored as a key.
for bad in ("", "  ", "two words", "has-dash", "x" * 40):
    try:
        auth_service.set_admin_username(bad, CFG)
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass
print("== a malformed username is refused")

# -- a name is not reused while something still answers to it ----------------
# delete_user leaves the `owner` on the systems that account created, keyed by
# nothing but the name, so a new account under that name would read as their
# owner. Creating over a live name used to overwrite the row outright.
from core import config                                       # noqa: E402


def refused(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except auth_service.UsernameTaken as exc:
        return str(exc)
    raise AssertionError(f"{fn.__name__}{args} was not refused")


assert refused(auth_service.create_user, "ADMIN", "password123", systems="*",
               config_dir=CFG) == auth_service.NAME_TAKEN_MSG
print("== a live name in another case is refused")

auth_service.create_user("olduser", "password123", systems="*", config_dir=CFG)
auth_service.create_user("idle", "password123", systems="*", config_dir=CFG)
config.save_local("systems", {"masters": {
    "line-1": {"url": "http://127.0.0.1:9101", "owner": "OldUser"},
    "line-2": {"url": "http://127.0.0.1:9102"}}}, CFG)
auth_service.delete_user("olduser", CFG)
auth_service.delete_user("idle", CFG)
assert refused(auth_service.create_user, "olduser", "password123",
               systems="*", config_dir=CFG) == auth_service.NAME_HELD_MSG
assert "olduser" not in auth_service.users(CFG)
print("== a deleted owner's name is refused while it still owns a system")
assert refused(auth_service.set_admin_username, "olduser",
               CFG) == auth_service.NAME_HELD_MSG
assert auth_service.admin_username(CFG) == "adam"
print("== and the administrator cannot rename onto it either")

auth_service.create_user("idle", "password123", systems="*", config_dir=CFG)
assert "idle" in auth_service.users(CFG)
auth_service.delete_user("idle", CFG)
print("== a deleted name that owns nothing is free again")

# The administrator keeping the name its own systems are filed under is not
# taking one, and is still allowed.
config.save_local("systems", {"masters": {
    "line-3": {"url": "http://127.0.0.1:9103", "owner": "adam"}}}, CFG)
assert auth_service.set_admin_username("adam", CFG) == "adam"
config.save_local("systems", {}, CFG)
print("== the administrator's own name stays its own")

# -- the row the accounts page renders ----------------------------------------
row = auth_service.admin_account(CFG)
assert row["username"] == "adam", row
assert row["builtin"] is True, row
assert row["enabled"] is True, row
# Every system, always -- not a list anybody maintains. An administrator that
# could be scoped to some systems is one that can be locked out of its own box.
assert row["systems"] == "*", row
assert not row["developer"], row
print("== it lists as an account:", row)

# -- no literal "admin" left in the checks ------------------------------------
SRC = io.open(os.path.join(ROOT, "core", "routes", "auth.py"),
              encoding="utf-8").read()
for literal in ('== "admin"', "== 'admin'", 'me == "admin"'):
    assert literal not in SRC, f"core/routes/auth.py still compares to {literal}"
assert "admin_username" in SRC
print("== the routes compare against admin_username(), not a literal")

# A taken name answers 409 with the service's words, which the Add User and
# Change Username dialogs print as they are.
for name in ("users.create", "auth.change_own_username", "auth.setup_json"):
    block = SRC[SRC.index(f'name="{name}"'):]
    block = block[:block.index("@router")]
    assert "except auth_service.UsernameTaken" in block, name
    assert "status_code=409" in block, name
print("== a taken name is a 409 on create, rename and first setup")

# The administrator is not managed through the per-user routes: it has no row
# for them to find, and the two that would "work" -- disable and delete -- are
# how a deployment locks itself out of itself.
for name in ("users.update", "users.reset_password", "users.delete"):
    block = SRC[SRC.index(f'name="{name}"'):]
    block = block[:block.index("return")]
    assert "_refuse_builtin" in block, f"{name} does not refuse the administrator"
print("== update / reset / delete refuse the administrator account")
print("ALL OK")
