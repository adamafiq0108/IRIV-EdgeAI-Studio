# What an account may DO on a page it can open.
#
# Before this, opening a page WAS the permission: every route that changed
# anything asked for master, so the two settings an account could have were
# "can change everything on this page" and "cannot see it". That is the wrong
# shape for the tier in the middle -- a site admin who re-points a camera when
# the old one fails, and who must NOT add a fifth, delete a zone, or rewrite
# the workflow the box was commissioned with.
#
# The claims worth pinning are the ones that make the checkbox honest:
#   * every capability in the catalogue is enforced at a route, and every
#     capability a route asks for is in the catalogue (a checkbox with nothing
#     behind it is a lie, and a route asking for an ungrantable id is a route
#     nobody can reach);
#   * the grant only ever runs downward -- master is never restricted by it,
#     an ordinary account holds none of it, and only master may set it;
#   * a whole-list write is judged by what it CHANGES, because "save the
#     cameras" and "delete three cameras" arrive at the same route.
import ast
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import auth                                # noqa: E402
from core.services import caps                                # noqa: E402

CFG = tempfile.mkdtemp()
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the catalogue and the routes agree ------------------------------------
ROUTE_FILES = ("models.py", "workflow.py", "builder.py", "functions.py",
               "datasets.py", "blynk.py")
asked = set()
for name in ROUTE_FILES:
    src = read("core", "routes", name)
    # Both forms: the plain guard, and the diff-driven ones that pick a
    # capability from a table (the whole-list camera and zone writes name
    # theirs in a tuple, then pass the winner to the same guard).
    asked |= set(re.findall(r'perm\.require\(request,\s*"([^"]+)"\)', src))
    asked |= {c for c in re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', src)
              if c in caps.ALL_CAPS}

check("every capability a route asks for is grantable",
      not (asked - set(caps.ALL_CAPS)), sorted(asked - set(caps.ALL_CAPS)))
check("...and every capability offered is asked for somewhere",
      not (set(caps.ALL_CAPS) - asked), sorted(set(caps.ALL_CAPS) - asked))
check("ids are unique", len(caps.ALL_CAPS) == len(set(caps.ALL_CAPS)))
check("every capability has a label a person can read",
      all(caps.label(c) != c and len(caps.label(c)) > 8 for c in caps.ALL_CAPS))
# The tree is what the accounts page draws. A capability under a page id the
# product does not have is a row that never appears.
from core.services import nav                                 # noqa: E402
check("every capability hangs under a real page",
      set(caps.CAP_PAGE.values()) <= set(nav.ALL_PAGES),
      sorted(set(caps.CAP_PAGE.values()) - set(nav.ALL_PAGES)))
check("the defaults are all real capabilities",
      set(caps.DEFAULT_ADMIN_CAPS) <= set(caps.ALL_CAPS))
# The defaults exist so an upgrade takes nothing away that the tier already
# had. What they must NOT contain is anything that CREATES or DESTROYS -- the
# whole point of the tier is that those stay with the box owner.
for cap in caps.DEFAULT_ADMIN_CAPS:
    check(f"the default {cap} neither adds nor deletes",
          not any(w in cap for w in (".add", ".remove", ".manage"))
          # Footage is the operation's own recording, and clearing last month
          # is its business -- the one deliberate exception.
          or cap.startswith("footage."))
check("...and the workflow is not one of them",
      "workflow.edit" not in caps.DEFAULT_ADMIN_CAPS)
check("...nor is adding or deleting a zone",
      "detection.zone.add" not in caps.DEFAULT_ADMIN_CAPS
      and "detection.zone.delete" not in caps.DEFAULT_ADMIN_CAPS)
check("...nor adding or removing a camera",
      not {"detection.camera.add", "detection.camera.remove",
           "detection.camera.activate"} & set(caps.DEFAULT_ADMIN_CAPS))
check("...but re-pointing one IS", "detection.camera.change"
      in caps.DEFAULT_ADMIN_CAPS)

# -- the grant itself ------------------------------------------------------
auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", "line_a", CFG, admin=True)
auth.create_user("sam", "passw0rd!", "line_a", CFG)

check("master is never restricted by a capability grant",
      auth.cap_grants("boss", "line_a", CFG) is None)
check("...so may() answers yes to anything for them",
      auth.may("boss", "line_a", "detection.camera.remove", CFG))
check("an admin starts on the tier's defaults",
      auth.cap_grants("ada", "line_a", CFG) == set(caps.DEFAULT_ADMIN_CAPS))
check("...which is enough to re-point a camera",
      auth.may("ada", "line_a", "detection.camera.change", CFG))
check("...and not enough to add or delete one",
      not auth.may("ada", "line_a", "detection.camera.add", CFG)
      and not auth.may("ada", "line_a", "detection.camera.remove", CFG))
check("...or to delete a zone",
      not auth.may("ada", "line_a", "detection.zone.delete", CFG))
check("an ordinary account holds none of them",
      auth.cap_grants("sam", "line_a", CFG) == set()
      and not auth.may("sam", "line_a", "detection.camera.change", CFG))

auth.update_user("ada", caps={"line_a": ["detection.camera.add"]}, config_dir=CFG)
check("master's tick is what is held afterwards",
      auth.cap_grants("ada", "line_a", CFG) == {"detection.camera.add"})
check("...and the defaults no longer apply to that system",
      not auth.may("ada", "line_a", "footage.delete", CFG))
check("...while a system never touched still has them",
      auth.cap_grants("ada", "line_b", CFG) == set(caps.DEFAULT_ADMIN_CAPS))

# An EMPTY grant is a real answer ("changes nothing here") and has to survive
# a round trip -- if it read back as "absent" the defaults would silently
# return, which is the opposite of what was just saved.
auth.update_user("ada", caps={"line_a": []}, config_dir=CFG)
check("an empty grant means empty, not 'unset'",
      auth.cap_grants("ada", "line_a", CFG) == set(),
      str(auth.cap_grants("ada", "line_a", CFG)))
auth.update_user("ada", caps={"line_a": "*"}, config_dir=CFG)
check("...and '*' puts the account back on the defaults",
      auth.cap_grants("ada", "line_a", CFG) == set(caps.DEFAULT_ADMIN_CAPS))

try:
    auth.update_user("ada", caps={"line_a": ["not.a.capability"]}, config_dir=CFG)
    check("an unknown capability is refused", False)
except ValueError as e:
    check("an unknown capability is refused", "unknown capability" in str(e), str(e))

# The grant belongs to the tier. Left behind on a demotion it would decide
# what a re-promoted account may change a year later.
auth.update_user("ada", caps={"line_a": ["footage.delete"]}, config_dir=CFG)
auth.update_user("ada", admin=False, config_dir=CFG)
check("demoting an account drops its capability grant",
      "caps" not in auth.users(CFG)["ada"])
auth.update_user("ada", admin=True, config_dir=CFG)

# -- the routes ------------------------------------------------------------
routes = read("core", "routes", "auth.py")
tree = ast.parse(routes)


def fn(name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(routes, node)
    raise AssertionError(f"{name} is gone from core/routes/auth.py")


check("a capability grant is refused to anyone but master",
      "_require_cap_grant(request, body)" in fn("update_user"))
check("...and the guard asks for master, not the site admin tier",
      "auth_service.is_admin(" in fn("_require_cap_grant"))
check("the accounts payload carries the catalogue the page draws",
      '"capabilities": caps_service.tree()' in routes
      and '"default_caps"' in routes and '"can_set_caps"' in routes)

perm = read("core", "routes", "perm.py")
check("the gate passes master and developer through",
      "auth_service.is_admin(user, cfg)" in perm)
check("...and asks the grant for everyone else",
      "auth_service.is_site_admin(user, cfg)" in perm
      and "auth_service.may(user, slug_of(request), cap, cfg)" in perm)
check("a refusal names the capability rather than saying 'admin only'",
      "caps_service.denial(cap)" in perm and "admin only" not in perm)

models = read("core", "routes", "models.py")
mt = ast.parse(models)


def mfn(name):
    for node in ast.walk(mt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(models, node)
    raise AssertionError(f"{name} is gone from core/routes/models.py")


# A whole-list write is the hole every per-route gate leaves: "save the
# cameras" and "delete three of them" are the same request with different
# contents.
src = mfn("detector_set_cameras")
for cap in ("detection.camera.add", "detection.camera.remove",
            "detection.camera.activate", "detection.camera.change"):
    check(f"the camera list write asks for {cap} when it does that",
          f'"{cap}"' in src)
check("...and judges it by the ids and flags, not by the route",
      "set(after) - set(before)" in src and "set(before) - set(after)" in src)
src = mfn("detector_set_zones")
for cap in ("detection.zone.add", "detection.zone.delete", "detection.zone.edit"):
    check(f"the zone write asks for {cap} when it does that", f'"{cap}"' in src)
check("re-pointing a camera is told apart from creating one",
      '"detection.camera.add" if creates' in mfn("detector_set_camera"))

# The pages draw their controls from the same answer that enforces them.
check("the camera page is told what it may change",
      '"caps": perm.held(request)' in mfn("detector_control_status"))
for name in ("detector_snapshots", "detector_clips"):
    check(f"{name} carries them too", '"caps": perm.held(request)' in mfn(name))

det = read("frontend", "src", "pages", "Detection.jsx")
check("Camera Setup offers Add only to an account that may add",
      'can("detection.camera.add")' in det)
check("...and the remove button only where removing is allowed",
      'can("detection.camera.remove")' in det)
check("...and the activation switch likewise",
      'can("detection.camera.activate")' in det)
zones = read("frontend", "src", "pages", "Zones.jsx")
check("Zones offers Delete only where deleting is allowed",
      'can("detection.zone.delete")' in zones)
check("...and the drawing tools only where adding is",
      'can("detection.zone.add")' in zones)
foot = read("frontend", "src", "pages", "Footage.jsx")
check("Footage asks the same grant the routes do",
      'can("footage.delete")' in foot and 'can("footage.export")' in foot
      and "is_site_admin" not in foot)

panel = read("frontend", "src", "components", "AccountsPanel.jsx")
check("the access tree replaced the two chip rows",
      "AccessTree" in panel and "function PageGrants" not in panel
      and "function CategoryGrants" not in panel)
check("...and draws its levels from the server, not from a copy",
      "data?.capabilities" in panel and "data?.sections" in panel)
check("...with a checkbox on every row",
      'type="checkbox"' in panel and "acct-tree-row" in panel)

# -- the camera a rule points at is a SLOT, not a device -------------------
reg = read("frontend", "src", "workflow", "registry.js")
check("the canvas names a camera by its slot",
      "export function camLabel" in reg and "`Camera ${i + 1}`" in reg)
wf = read("frontend", "src", "pages", "Workflow.jsx")
check("...and every camera picker uses it",
      wf.count("camLabel(vars.cameras, c.id)") == 3,
      "Detection Stream, the capture nodes, and Snapshots")
check("...as does the stat label", "camLabel(cameras, scoped[1])" in wf)
nodes = read("frontend", "src", "workflow", "nodes.jsx")
check("...and the Detection Stream node",
      "camLabel(variables.cameras, d.camera)" in nodes
      and "cam.name || cam.id" not in nodes)
check("Camera Setup labels its rail the same way, so the two match",
      "Camera {camList.indexOf(c) + 1}" in det)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
