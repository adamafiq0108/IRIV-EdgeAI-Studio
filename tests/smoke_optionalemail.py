# An account's recovery address is OPTIONAL.
#
# It used to be required from day one, on the reasoning that an account with
# no address has no way back in. That is not true of this box: the master is
# reset at the FRONT PANEL -- hold USER for five seconds and the OLED shows a
# single-use code (core/services/panel_reset.py) -- and every other account is
# reset by whoever owns the box. What an address adds is a way back in over
# the NETWORK, and a box on a closed network has nothing to send one through.
#
# An address that cannot be reached is worse than none: it reads as a working
# way back in, and the moment it is needed is the moment that is discovered.
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = os.path.join(tempfile.mkdtemp(), "config")
shutil.copytree(os.path.join(ROOT, "config"), CFG)

# -- the master's own address ------------------------------------------------------------
auth.set_admin_email("owner@example.com", CFG)
check("the master can have a recovery address", auth.admin_email(CFG) == "owner@example.com")
check('...and can take it off again with ""', auth.set_admin_email("", CFG) == ""
      and not auth.admin_email(CFG), repr(auth.admin_email(CFG)))
try:
    auth.set_admin_email("not-an-address", CFG)
    check("a malformed address is still refused", False)
except ValueError as exc:
    check("a malformed address is still refused", True, str(exc))

# -- an ordinary account -------------------------------------------------------------------
auth.create_user("lineop", "hunter2hunter2", ["demo"], CFG)
check("an account can be created with no address at all",
      not auth.user_email("lineop", CFG), repr(auth.user_email("lineop", CFG)))
auth.set_user_email("lineop", "line@example.com", CFG)
check("...given one later", auth.user_email("lineop", CFG) == "line@example.com")
check("...and cleared again", auth.set_user_email("lineop", "", CFG) == ""
      and not auth.user_email("lineop", CFG), repr(auth.user_email("lineop", CFG)))

# An address, once cleared, is really gone: a stale row would still match a
# forgot-password lookup and mail a link to somebody the operator removed.
auth.set_user_email("lineop", "line@example.com", CFG)
auth.set_user_email("lineop", "", CFG)
check("a cleared address no longer resolves to the account",
      not auth.account_for_email("line@example.com", CFG),
      auth.account_for_email("line@example.com", CFG))

# ...and the same for the master's.
auth.set_admin_email("owner@example.com", CFG)
auth.set_admin_email("", CFG)
check("...nor does a cleared master address",
      not auth.account_for_email("owner@example.com", CFG),
      auth.account_for_email("owner@example.com", CFG))

# Two accounts still cannot share one address: a forgot-password lookup
# resolves an address to ONE account, and two would make which one arbitrary.
auth.create_user("second", "hunter2hunter2", ["demo"], CFG)
auth.set_user_email("lineop", "shared@example.com", CFG)
try:
    auth.set_user_email("second", "shared@example.com", CFG)
    check("one address still belongs to one account", False)
except ValueError as exc:
    check("one address still belongs to one account", True, str(exc))

# -- the regression this change could have caused -------------------------------------------
# "" now means REMOVE rather than being refused, so a route that wrote the
# address unconditionally would wipe the master's the moment any caller posted
# that endpoint without mentioning it -- saving the sending mailbox alone, or
# the name and password alone. The write has to be guarded by the field's
# PRESENCE, not by its truthiness.
route = open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
setup = route.split('name="auth.setup_json"', 1)[1].split("@router.", 1)[0]
check("account-setup only writes the address when the body carries one",
      'if "email" in body:' in setup
      and setup.index('if "email" in body:') < setup.index("set_admin_email("),
      "set_admin_email is called unguarded - a body with no email field "
      "would clear the master's recovery address")

panel = open(os.path.join(ROOT, "frontend", "src", "components", "AccountsPanel.jsx"),
             encoding="utf-8").read()
check("the panel offers to remove an address",
      "async function removeEmail(" in panel and 'email: "" }' in panel)
check("...and says what happens instead, rather than reading as a fault",
      "hold the USER" in panel and "ask whoever owns the box" in panel)
check("...and creating an account no longer demands one",
      "nu.email.trim() && !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/" in panel)

# The way back in that needs no network at all is still there.
check("the master's panel reset is untouched",
      hasattr(auth, "ensure_secret") and os.path.exists(
          os.path.join(ROOT, "core", "services", "panel_reset.py")))

# -- what the security review found, pinned -------------------------------------------
# 1. The panel reset is the path that JUSTIFIES removing an address -- and it
#    ships disabled, because on a stock board the factory flow drives the same
#    I2C bus. Promising it on a box where it is off would talk an operator into
#    removing the last way into their own machine.
check("the panel reset is only promised where it actually runs",
      auth.panel_reset_ready(CFG) is False,
      "config/panel.toml ships enabled=false; this must report False")
check("...and the page is told which box it is on",
      '"panel_reset": (auth_service.panel_reset_ready(cfg)' in route
      and "auth_service.may_set_admin(me, cfg) else None)" in route)

# THREE settings gate the hold, not two. `when_held` is wired INSIDE the
# `button` branch (core/services/panel.py, _open), so a kiosk install with the
# button off -- which panel.toml documents as intended -- has no Button object
# for a hold to fire on, whatever hold_shutdown says. Claiming a panel reset
# there is the same lockout as claiming one on a stock board, with a narrower
# trigger.
import core.services.panel as panel_service                 # noqa: E402
_real = panel_service.settings
for _label, _cfg, _want in (
        ("stock board (panel off)", {"enabled": False, "button": True,
                                     "hold_shutdown": True}, False),
        ("kiosk (button off)", {"enabled": True, "button": False,
                                "hold_shutdown": True}, False),
        ("hold gesture off", {"enabled": True, "button": True,
                              "hold_shutdown": False}, False),
        ("panel running", {"enabled": True, "button": True,
                           "hold_shutdown": True}, True)):
    panel_service.settings = lambda c=None, _c=_cfg: _c
    check(f"...{_label}: reset {'available' if _want else 'NOT promised'}",
          auth.panel_reset_ready(None) is _want)
panel_service.settings = _real
check("...and the copy branches on it, rather than promising a panel",
      "data?.panel_reset" in panel and "the front" in panel
      and "no way back in" in panel)

# 2. The tier test was inverted: `admin` is the SITE ADMIN flag and `is_admin`
#    is on no row at all, so the master was told to ask its own owner, and a
#    site admin was pointed at a gesture that resets somebody else's account.
check("the master is identified by `builtin`, the field that exists",
      "const master = !!active.builtin;" in panel
      and "active.is_admin" not in panel)

# 3. The service allowed an empty address; the two routes still refused it, so
#    Remove email 400'd for every account but the master and creation still
#    demanded one. Service, route and UI have to agree on one rule.
check("PATCH /api/users lets an address be removed",
      'auth_service.clean_email(body.get("email"), allow_empty=True)' in route)
check("POST /api/users no longer demands one",
      '"an email address is required")' not in route.split("users.create", 1)[1]
      .split("@router.", 1)[0])
check("...and the menu offers Remove on every row, not just the master's",
      'onClick={() => { setMenuFor(""); removeEmail(); }}' in panel)

# 4. "No address by choice" and "never set up" were the same state, so the
#    master was pushed back to the first-run screen at every sign-in -- a
#    screen that demands the address they had just chosen to remove. The
#    login no longer reads the address at all (smoke_setupoptional.py): the
#    shipped password is the only thing that opens first run. The removal is
#    still recorded.
_login_gate = route.split("needs_setup = ", 1)[1].split("\n", 1)[0]
check("the login's first-run rule does not key on the recovery address",
      "admin_email" not in _login_gate, _login_gate)
auth.set_admin_email("owner@example.com", CFG)
check("...set when the address is removed", auth.set_admin_email("", CFG) == ""
      and auth.admin_email_optout(CFG))
auth.set_admin_email("owner@example.com", CFG)
check("...and cleared again when one is set", not auth.admin_email_optout(CFG))

# 6. An approval that can never send used to 502 and RE-CREATE its own row,
#    so the request resurrected on every attempt until it aged out.
approve = route.split('name="auth.reset_request_approve"', 1)[1].split("@router.", 1)[0]
check("an approval for an account with no address is closed, not resurrected",
      'if auth_service.user_email(row["user"], cfg):' in approve
      and "status_code=409" in approve)

# The confirm dialog has to use the props the dialog actually reads, or the
# warning it exists to show is never rendered.
check("the confirm dialog passes description/destructive, not invented props",
      'description: "The email address will be removed from this account. "' in panel
      and "+ (master" in panel and "destructive: master && !panel" in panel
      and "body: master" not in panel and "danger:" not in panel)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
