# Many users at once must not corrupt the account files or feel each other.
#
# Three defects this pins closed:
#   * save_local rewrote the whole file with no temp+rename -- a crash
#     mid-write truncated the users file, a box nobody can log in to;
#   * two concurrent read-modify-write cycles (two admins editing accounts,
#     two legacy-hash upgrades) lost one write silently;
#   * every authenticated request re-read two TOML files from disk inside the
#     middleware -- ten users made each other's requests wait on I/O.
import io
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                        # noqa: E402
from core.services import auth                                 # noqa: E402

CFG = tempfile.mkdtemp()
auth.set_master_password("adminpass1", CFG)

# -- atomic writes -------------------------------------------------------------
config.save_local("users", {"users": {"seed": {"salt": "s", "password_hash": "h",
                                               "systems": "*", "enabled": True}}},
                  CFG)
assert not [n for n in os.listdir(CFG) if n.endswith(".tmp")], \
    "the temp file was left behind"
src = io.open(os.path.join(ROOT, "core", "config.py"), encoding="utf-8").read()
block = src[src.index("def save_local("):]
block = block[:block.index("\ndef ", 5)]
assert "os.replace" in block, "save_local writes in place -- a crash tears the file"
print("== save_local goes through a temp file and an atomic replace")

# The temp file holds the secrets too, so it must never exist at the umask's
# 0644 (Pipi S2-I2): created 0600 by os.open, not chmod'ed after the write.
assert "O_EXCL" in block and "0o600" in block and "write_text" not in block, \
    "save_local's temp file is not created 0600"
if os.name == "posix":
    seen = []
    real_replace = os.replace
    old_mask = os.umask(0o022)
    try:
        os.replace = lambda a, b: (seen.append(os.stat(a).st_mode & 0o777),
                                   real_replace(a, b))[1]
        config.save_local("telegram", {"bots": {}}, CFG)
    finally:
        os.replace = real_replace
        os.umask(old_mask)
    assert seen == [0o600], f"the temp file was {oct(seen[0]) if seen else '?'} before the swap"
    assert os.stat(os.path.join(CFG, "telegram.local.toml")).st_mode & 0o777 == 0o600
real_replace = os.replace


def _broken_replace(a, b):
    raise OSError("disk full")


os.replace = _broken_replace
try:
    config.save_local("users", {}, CFG)
except OSError:
    pass
finally:
    os.replace = real_replace
assert not [n for n in os.listdir(CFG) if n.endswith(".tmp")], "a failed write left its temp file"
print("== the temp file is 0600 from its first byte, and a failed write leaves none")

# -- concurrent account edits both land ---------------------------------------
auth.create_user("alice", "alicepass1", "sys_a", CFG)
auth.create_user("bob", "bobpass1", "sys_a", CFG)
errs = []


def grant(user, slug):
    try:
        for _ in range(20):
            auth.grant_system(user, slug, CFG)
    except Exception as e:                                     # noqa: BLE001
        errs.append(e)


threads = [threading.Thread(target=grant, args=("alice", f"a{i}")) for i in range(3)] \
    + [threading.Thread(target=grant, args=("bob", f"b{i}")) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()
assert not errs, errs
users = auth.users(CFG)
a = users["alice"]["systems"].split(",")
b = users["bob"]["systems"].split(",")
assert all(f"a{i}" in a for i in range(3)), a
assert all(f"b{i}" in b for i in range(3)), b
assert "seed" in users, "a concurrent write dropped an unrelated account"
print("== six threads of grants: every grant landed, nothing was lost")

# -- the mtime cache sees edits, without re-reading per request ----------------
assert "alice" in auth.users(CFG)
# An edit through the proper writer changes the mtime, so the very next read
# picks it up...
auth.create_user("carol", "carolpass1", "*", CFG)
assert "carol" in auth.users(CFG), "the cache went stale past a write"
# ...and a HAND edit on disk (an operator with a shell) is seen too.
local = config.load_local("users", CFG)
local["users"]["dave"] = {"salt": "s", "password_hash": "h",
                          "systems": "*", "enabled": True}
time.sleep(0.02)                       # a distinct mtime on coarse filesystems
config.save_local("users", local, CFG)
assert "dave" in auth.users(CFG), "an on-disk edit was never picked up"
print("== the read cache follows the file's mtime, hand edits included")

# -- password hashing is threaded in the routes --------------------------------
routes = io.open(os.path.join(ROOT, "core", "routes", "auth.py"),
                 encoding="utf-8").read()
assert routes.count("asyncio.to_thread") >= 5, \
    "verify_login/hash calls back on the event loop -- logins stall everyone"
print("== every PBKDF2 call in the auth routes runs in a thread")
print("ALL OK")
