# The workshop and the deployment are separate, BOTH ways: a developer sees
# their workbenches, an admin sees the deployment's systems, and neither set
# leaks into the other's hub.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                     # noqa: E402
from core.services import auth as auth_service              # noqa: E402
from core.services import hub as hub_service                # noqa: E402

CFG = tempfile.mkdtemp()

# One developer, one ordinary admin account.
auth_service.set_master_password("admin-pass-1", CFG)
auth_service.create_user("devver", "dev-pass-123", "*", config_dir=CFG)
local = config.load_local("users", CFG)
local["users"]["devver"][auth_service.DEVELOPER_FLAG] = True
config.save_local("users", local, CFG)
assert auth_service.is_developer("devver", CFG), "the test's developer is not one"


class Spec:
    """The bit of a registry entry cards() reads."""
    def __init__(self, slug):
        self.slug = slug
        self.display_name = slug
        self.subtitle = self.icon = self.cover = ""


REGISTRY = [Spec(s) for s in ("line_a", "line_b", "bench", "legacy")]
MASTERS = {
    "line_a": {"owner": "admin", "url": ""},      # the deployment's
    "line_b": {"owner": "", "url": ""},           # ownerless == deployment's
    "bench":  {"owner": "devver", "url": ""},     # the workshop's
    "legacy": {"url": ""},                        # no owner key at all
}


def seen(username):
    return {c["slug"] for c in hub_service.build_cards(
        REGISTRY, MASTERS, {}, username=username, config_dir=CFG)}


# 1. the developer gets their workbench, and ONLY that
assert seen("devver") == {"bench"}, seen("devver")
print("== a developer sees their own workbenches:", seen("devver"))

# 2. an admin gets the deployment's systems -- including ownerless ones, which
#    are what the box runs rather than something built to author a template
assert seen("admin") == {"line_a", "line_b", "legacy"}, seen("admin")
print("== an admin sees the deployment's systems:", sorted(seen("admin")))

# 3. neither set leaks into the other
assert "bench" not in seen("admin"), "a workbench showed up on the admin's hub"
assert not (seen("devver") & seen("admin")), "the two hubs overlap"
print("== neither set leaks into the other")

# 4. no session context (internal callers) still sees everything, which is
#    what the counting and provisioning paths rely on
assert seen(None) == {"line_a", "line_b", "bench", "legacy"}
print("== an unscoped call still sees every system, as its callers expect")
print("ALL OK")
