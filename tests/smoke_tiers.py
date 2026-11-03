# Four tiers, and what each one is allowed to be.
#
# There used to be two: whoever holds the master account, and everybody else.
# So anyone who needed to add an operator had to be handed the master account,
# which hands them the whole box -- creating systems, the board's terminals,
# the front panel, and the ability to lock the owner out.
#
# The tier in between is for the person who runs an OPERATION on somebody
# else's box: they add their own staff, decide who sees which pages, set
# retention and how the box sends mail. They do not create systems, touch the
# hardware, or reach anything above them.
#
#   developer  >  master  >  admin  >  user
#
# The claims worth pinning are the ones an attacker would go for: that
# authority only ever runs downward, that a tier cannot promote itself, and
# that the grant an admin hands out cannot widen past the one master set.
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                       # noqa: E402
from core.services import auth                                # noqa: E402
from core.services import nav as nav_service                  # noqa: E402

CFG = tempfile.mkdtemp()
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- a box with one of each ------------------------------------------------
auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", "line_a,line_b", CFG, admin=True)
auth.create_user("sam", "passw0rd!", "line_a", CFG)
# The developer flag is not reachable from any route, so it is written the way
# the box itself would (scripts/dev/make_dev_account.py).
local = config.load_local("users", CFG)
local["users"]["dee"] = {"salt": "x", "password_hash": "y", "systems": "*",
                         "enabled": True, "developer": True}
config.save_local("users", local, CFG)
auth._cached_local.cache_clear() if hasattr(auth._cached_local, "cache_clear") else None

check("the box owner is master", auth.tier("boss", CFG) == auth.TIER_MASTER,
      auth.tier("boss", CFG))
check("a flagged account is a site admin", auth.tier("ada", CFG) == auth.TIER_ADMIN,
      auth.tier("ada", CFG))
check("an ordinary account is a user", auth.tier("sam", CFG) == auth.TIER_USER)
check("the developer outranks all of them",
      auth.tier("dee", CFG) == auth.TIER_DEVELOPER)
check("...and a name nobody holds grants nothing",
      auth.tier("nobody", CFG) == auth.TIER_USER)

# is_admin has meant "owns the box" in 40-odd call sites since long before this
# tier existed. If it ever started answering True for a site admin, every one
# of those routes would open at once.
check("is_admin still means MASTER, not the new tier",
      auth.is_admin("boss", CFG) and auth.is_admin("dee", CFG)
      and not auth.is_admin("ada", CFG) and not auth.is_admin("sam", CFG))
check("...and is_site_admin includes everyone above it",
      auth.is_site_admin("ada", CFG) and auth.is_site_admin("boss", CFG)
      and auth.is_site_admin("dee", CFG) and not auth.is_site_admin("sam", CFG))

# Switching an account off has to switch off everything it could do, rank
# included -- otherwise a disabled admin is still an admin to every check that
# reads the flag rather than the tier.
auth.update_user("ada", enabled=False, config_dir=CFG)
check("a disabled admin is not an admin", auth.tier("ada", CFG) == auth.TIER_USER)
auth.update_user("ada", enabled=True, config_dir=CFG)

# -- authority runs downward, never sideways -------------------------------
check("master manages an admin", auth.may_manage("boss", "ada", CFG))
check("an admin manages a user", auth.may_manage("ada", "sam", CFG))
check("an admin does NOT manage another admin",
      not auth.may_manage("ada", "ada", CFG))
auth.create_user("eve", "passw0rd!", "line_a", CFG, admin=True)
check("...not even a different one -- either could delete the other",
      not auth.may_manage("ada", "eve", CFG))
check("a user manages nobody", not auth.may_manage("sam", "ada", CFG)
      and not auth.may_manage("sam", "sam", CFG))
check("nobody manages a developer over the web",
      not auth.may_manage("boss", "dee", CFG))
check("...including another admin", not auth.may_manage("ada", "dee", CFG))

# Promotion is the one operation where the ACTOR's tier is the whole question.
check("only master and developer may grant the tier",
      auth.may_set_admin("boss", CFG) and auth.may_set_admin("dee", CFG)
      and not auth.may_set_admin("ada", CFG)
      and not auth.may_set_admin("sam", CFG))

# -- the cap master sets is enforced where systems are handed over ----------
auth.update_user("ada", max_systems=2, config_dir=CFG)
try:
    auth.update_user("ada", systems="line_a,line_b,line_c", config_dir=CFG)
    check("a capped account cannot be given more systems", False)
except ValueError as e:
    check("a capped account cannot be given more systems", "limited to 2" in str(e),
          str(e))
try:
    auth.update_user("ada", systems="*", config_dir=CFG)
    check("...and 'all systems' cannot be used to go around the cap", False)
except ValueError as e:
    check("...and 'all systems' cannot be used to go around the cap",
          "name them" in str(e), str(e))
auth.update_user("ada", systems="line_a,line_b", config_dir=CFG)
check("...but the cap itself is allowed",
      auth.users(CFG)["ada"]["systems"] == "line_a,line_b")
# A cap belongs to the tier that has one: left behind, it would apply again if
# the account were promoted a year later by somebody who never set it.
auth.update_user("ada", admin=False, config_dir=CFG)
check("demoting an account drops its cap",
      auth.MAX_SYSTEMS_FIELD not in auth.users(CFG)["ada"])
auth.update_user("ada", admin=True, config_dir=CFG)

# -- two grants, and neither can widen past the other ----------------------
# Master allows three pages on this system; the admin then grants a category
# whose pages include one master removed.
auth.update_user("sam", pages={"line_a": ["main", "detection", "footage"]},
                 config_dir=CFG)
check("master's page grant is stored",
      auth.page_grants("sam", "line_a", CFG) == {"main", "detection", "footage"},
      str(auth.page_grants("sam", "line_a", CFG)))
auth.update_user("sam", sections={"line_a": ["ai_config"]}, config_dir=CFG)
seen = auth.visible_pages("sam", "line_a", CFG)
check("the two grants INTERSECT, so neither widens the other",
      seen == {"detection"}, str(seen))
check("...and a category granting a page master removed does not restore it",
      "workflow" not in seen and "zones" not in seen, str(seen))
# Master is never restricted by its own grant -- somebody has to be able to fix
# a grant that locked everyone out.
check("master is never page-restricted",
      auth.page_grants("boss", "line_a", CFG) is None)

# A grant naming a page the product does not have is a grant on nothing.
try:
    auth.update_user("sam", pages={"line_a": ["not_a_page"]}, config_dir=CFG)
    check("an unknown page is refused", False)
except ValueError as e:
    check("an unknown page is refused", "unknown page" in str(e), str(e))

# The nav applies both, and an unmapped page survives a CATEGORY grant while
# still obeying a PAGE grant -- collapsing the two would lose that.
check("every page id the nav can draw is grantable",
      set(nav_service.ALL_PAGES) >= {p for p, _l, _c in nav_service.PAGES})

# -- the Settings tabs each tier may open ----------------------------------
check("an admin gets their staff, retention and the ways out",
      set(auth.settings_tabs("ada", CFG))
      == {"accounts", "retention", "mail", "telegram", "network", "mqtt"},
      str(auth.settings_tabs("ada", CFG)))
check("...and NOT the hardware -- that is the box owner's",
      "board" not in auth.settings_tabs("ada", CFG)
      and "panel" not in auth.settings_tabs("ada", CFG))
check("master gets the hardware too",
      "board" in auth.settings_tabs("boss", CFG)
      and "panel" in auth.settings_tabs("boss", CFG))
# ...on a box that carries the owner's tooling. The two developer tabs exist
# only where core/dev is mounted; the hub passes that fact in.
check("...and the developer gets everything, where the box has the tooling",
      set(auth.settings_tabs("dee", CFG, dev_tools=True))
      > set(auth.settings_tabs("boss", CFG)))
check("...and exactly master's tabs on a box without it",
      auth.settings_tabs("dee", CFG, dev_tools=False)
      == auth.settings_tabs("boss", CFG))
check("an ordinary account has no settings at all",
      auth.settings_tabs("sam", CFG) == [])

# -- the routes ------------------------------------------------------------
routes = read("core", "routes", "auth.py")
tree = ast.parse(routes)


def fn(name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(routes, node)
    raise AssertionError(f"{name} is gone from core/routes/auth.py")


for name in ("update_user", "delete_user", "reset_user_password"):
    body_src = fn(name)
    check(f"{name} refuses an account the caller does not outrank",
          "_require_over(request, username)" in body_src, body_src[:200])
# A password reset IS a takeover: whoever performs it can then sign in.
check("a password reset needs rank, not just the accounts page",
      "_require_over" in fn("reset_user_password"))
check("promotion is refused to anyone but master",
      "_require_tier_grant(request, body)" in fn("update_user")
      and "_require_tier_grant(request, body)" in fn("create_user"))
check("a per-page grant is refused to anyone but master",
      "_require_page_grant(request, body)" in fn("update_user"))
# The CATEGORY grant began as the admin's own control. It is master's now: an
# admin editing a grant is an admin editing their own account's grant one
# careless click later, and what an operation may reach is decided above it.
check("...and so is the category grant",
      "_require_section_grant(request, body)" in fn("update_user"))
check("...by the same rule, not a softer one",
      "auth_service.is_admin(" in fn("_require_section_grant"))
# The page lists you in order to show your OWN access, and authority runs
# strictly downward -- so every control on that row was one the route refuses.
check("each row says whether the viewer may act on it",
      'auth_service.may_manage(me, row["username"], cfg)' in fn("list_users")
      and '"manageable"' in fn("list_users"))
check("...and the guard itself asks for master, not site admin",
      "auth_service.is_admin(" in fn("_require_page_grant"))
check("the tier guard asks may_set_admin",
      "auth_service.may_set_admin(" in fn("_require_tier_grant"))
# An admin listing accounts must not be shown the rows above them.
check("the account list is scoped to what the caller may manage",
      "auth_service.may_manage(me, u[\"username\"], cfg)" in fn("list_users"))

hub = read("core", "routes", "hub.py")
check("creating a system stays with the box owner",
      "only the box owner can create a system" in hub)

# Footage is the operation's OWN recordings. An admin who has to ask the box
# owner to clear last month is an operator with a title. An ordinary account
# still cannot: looking at footage and destroying it are not one permission.
# WHICH admin gets it is a capability master ticks now (tests/smoke_caps.py);
# what is pinned here is that these four are no longer master-only.
models_src = read("core", "routes", "models.py")
mt = ast.parse(models_src)


def mfn(name):
    for node in ast.walk(mt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(models_src, node)
    raise AssertionError(f"{name} is gone from core/routes/models.py")


for name, cap in (("detector_snapshots_delete", "footage.delete"),
                  ("detector_snapshot_delete", "footage.delete"),
                  ("detector_clips_delete", "footage.delete"),
                  ("detector_footage_email", "footage.export")):
    src = mfn(name)
    check(f"{name} asks for {cap}",
          f'perm.require(request, "{cap}")' in src, src[:160])
    check("...and not for master", "_require_admin(request)" not in src)

# -- the pages ------------------------------------------------------------
shell = read("frontend", "src", "components", "HubShell.jsx")
check("a site admin reaches Settings", "user?.is_site_admin" in shell)
hub_settings = read("frontend", "src", "components", "SettingsHub.jsx")
check("...and the tab list comes from the server, not from the page",
      "tabs: allowed = null" in hub_settings
      and "allowed.includes(t.id)" in hub_settings)
panel = read("frontend", "src", "components", "AccountsPanel.jsx")
check("the tier control is offered only to whoever may set it",
      "canSet={!!data?.can_set_tier}" in panel and "if (!canSet" in panel)
check("the page grants likewise", "data?.can_set_pages" in panel)
check("...and so do the capabilities under them", "data?.can_set_caps" in panel)
check("...and the categories, which stopped being the admin's",
      "data?.can_set_sections" in panel)
check("a row the viewer cannot manage offers nothing",
      "active.manageable !== false" in panel)

# Ending a session and ending the box's day are one thought with three
# answers, so they live on one control -- but only two of them are the box
# owner's.
menu = read("frontend", "src", "components", "SessionMenu.jsx")
check("the sidebar's sign-out is a menu",
      "DropdownMenu" in menu and "Sign out" in menu
      and "Reboot" in menu and "Shut down" in menu)
check("...and an ordinary account is offered neither",
      "const mayPower = !!user?.is_site_admin;" in menu
      and "{mayPower && (" in menu)
for shell in ("HubShell.jsx", "MasterShell.jsx", "StandaloneShell.jsx"):
    src = read("frontend", "src", "components", shell)
    check(f"{shell} uses it", "<SessionMenu />" in src
          and "setSignOutOpen" not in src)
# DISCOVERABILITY. A tier only settable after the account exists reads as a
# tier that does not exist -- and the first place anyone looks for "admin" is
# the Roles list, which is three labels that grant nothing.
check("an admin can be made at creation, not only afterwards",
      'admin: !!nu.admin' in panel
      and 'Administrator - also manages their own users' in panel)
check("...and the list says which accounts are one",
      'user.admin) return <span className="acct-badge is-dev">Administrator' in panel)
check("...and the Roles tab points at where the tier really lives",
      "Need administrator access?" in panel
      and "Administrator access is assigned to a user account" in panel)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
