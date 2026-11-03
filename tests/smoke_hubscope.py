# One scoping rule for every hub surface.
#
# The Systems grid applied TWO tests (the account's systems grant AND the
# workshop/deployment ownership split) while Live Preview, System Control and
# Performance applied only the grant -- so an admin's Live Preview picker
# listed the developer's workbench systems the grid next door was hiding,
# which read as "showing other users' systems". The rule now lives in ONE
# predicate, hub.visible_to, and the routes all call it.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import hub as hub_service                  # noqa: E402
from core.services import auth as auth_service                # noqa: E402

# The predicate composes two auth answers; stub them so this tests the
# COMPOSITION, not the credential store.
GRANTS = {("admin", "line1"): True, ("admin", "bench"): True,
          ("dev", "line1"): True, ("dev", "bench"): True,
          ("op", "line1"): True, ("op", "bench"): False}
DEVELOPERS = {"dev"}

auth_service_allowed = auth_service.allowed
auth_service_is_dev = auth_service.is_developer
auth_service.allowed = lambda u, s, c=None: GRANTS.get((u, s), False)
auth_service.is_developer = lambda u, c=None: u in DEVELOPERS

MASTERS = {"line1": {"owner": "admin"},        # the deployment's system
           "bench": {"owner": "dev"},          # the developer's workbench
           "legacy": {}}                       # ownerless = the deployment's

try:
    # An admin sees the deployment's systems, never the workshop's -- even
    # with a "*"-wide grant, which is exactly the case the grant-only check
    # got wrong.
    assert hub_service.visible_to("admin", "line1", MASTERS, None)
    assert not hub_service.visible_to("admin", "bench", MASTERS, None)
    print("== an admin sees the deployment's systems, not the developer's bench")

    # A developer sees the bench, not the deployment's running lines.
    assert hub_service.visible_to("dev", "bench", MASTERS, None)
    assert not hub_service.visible_to("dev", "line1", MASTERS, None)
    print("== a developer sees the bench, not the deployment")

    # The grant still gates: an operator without a grant sees nothing extra.
    assert hub_service.visible_to("op", "line1", MASTERS, None)
    assert not hub_service.visible_to("op", "bench", MASTERS, None)
    print("== the systems grant still gates underneath")

    # Ownerless systems belong to the deployment.
    GRANTS[("admin", "legacy")] = True
    GRANTS[("dev", "legacy")] = True
    assert hub_service.visible_to("admin", "legacy", MASTERS, None)
    assert not hub_service.visible_to("dev", "legacy", MASTERS, None)
    print("== ownerless systems count as the deployment's")

    # No session context (internal callers): everything, the old behaviour.
    assert hub_service.visible_to("", "bench", MASTERS, None)
    assert hub_service.visible_to(None, "line1", MASTERS, None)
    print("== no username = no scoping, for internal callers")

    # ...and the routes actually use it: every hub listing loop goes through
    # the one predicate, so the pages cannot disagree again. Source scan, not
    # an import -- this box may not have fastapi, and the assertion is about
    # what the file SAYS either way.
    import io
    import inspect
    src = io.open(os.path.join(ROOT, "core", "routes", "hub.py"),
                  encoding="utf-8").read()
    for fn in ("def hub_streams", "def hub_pipelines", "def hub_health"):
        body = src[src.index(fn):src.index("@router", src.index(fn))]
        assert "visible_to" in body, f"{fn} does not scope via visible_to"
    assert "auth_service.allowed(username, system.slug" not in src, \
        "a hub listing still uses the bare grant check"
    assert "visible_to" in inspect.getsource(hub_service.build_cards)
    print("== streams, pipelines, health and the grid all call the one rule")
finally:
    auth_service.allowed = auth_service_allowed
    auth_service.is_developer = auth_service_is_dev
print("ALL OK")
