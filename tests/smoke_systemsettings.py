# System settings: the switches that are about the BOX.
#
# Everything else in config is about a system, an account, or a way the box
# talks out. These are about the appliance -- what it may do at all -- and
# they had been scattered across whichever page happened to use them: auto
# logout under Accounts, and whether this box uses email at all nowhere,
# inferred from whether somebody had filled a mailbox in.
#
# The one that carries weight is `email`. Off means no reset links, no reset
# requests, and no addresses on accounts -- so the Forgot screen stops asking
# for one, which is the point: a field that accepts an address and then says
# "if that address is on an account here..." about a box with no mail path is
# a way back in that is not one.
import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth                              # noqa: E402
from core.services import sysconfig                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


CFG = os.path.join(tempfile.mkdtemp(), "config")
shutil.copytree(os.path.join(ROOT, "config"), CFG)

# -- the store -------------------------------------------------------------------------
check("a box uses email unless it says otherwise, so nothing in the field changes",
      sysconfig.email_enabled(CFG) is True)
check("...and it can be turned off", sysconfig.set_email_enabled(False, CFG) is False
      and sysconfig.email_enabled(CFG) is False)
check("...and back on", sysconfig.set_email_enabled(True, CFG) is True)
# A switch written by hand as a STRING is the one way a box could read the
# opposite of what its file says.
sysconfig._set("email", "false", CFG)
check('...and "false" typed into the file is false, not a non-empty string',
      sysconfig.email_enabled(CFG) is False)
sysconfig.set_email_enabled(True, CFG)
try:
    sysconfig._set("nope", 1, CFG)
    check("an unknown switch is refused rather than written", False)
except ValueError as exc:
    check("an unknown switch is refused rather than written", True, str(exc))

# -- who may touch it ------------------------------------------------------------------
route = read("core", "routes", "auth.py")
get = route.split('name="system.settings"', 1)[1].split("@router.", 1)[0]
post = route.split('name="system.settings_save"', 1)[1].split("@router.", 1)[0]
for name, block in (("reading", get), ("writing", post)):
    check(f"{name} the box switches is master or developer only",
          'if not auth_service.is_admin(me, cfg):' in block
          and "master or developer only" in block)
check("a write touches only the switches the body NAMES",
      'if "email" in body:' in post and 'if "auto_logout" in body:' in post)
check("...and is audited", 'audit_service.record("system.settings"' in post)
check("the page gets both switches and what it needs to explain them",
      all(k in get for k in ('"auto_logout"', '"idle_minutes"', '"panel_reset"')))
check("the tab is master and developer only, not site admin",
      "system" in auth.SETTINGS_TABS[auth.TIER_MASTER]
      and "system" in auth.SETTINGS_TABS[auth.TIER_DEVELOPER]
      and "system" not in auth.SETTINGS_TABS[auth.TIER_ADMIN])

# -- with email off, nothing asks for an address ---------------------------------------
check("the pre-login payload says whether this box uses email",
      '"email": sysconfig_service.email_enabled(cfg)}' in route)
forgot = route.split('name="auth.forgot_json"', 1)[1].split("@router.", 1)[0]
check("the forgot route makes no lookup at all with email off",
      "if sysconfig_service.email_enabled(cfg) else \"\")" in forgot)
# The invariant smoke_resetflow.py pins, and the reason this is a skipped
# LOOKUP rather than an early return: one place an attempt is counted, one
# answer, so a hit and a miss cannot come to differ.
check("...counted and answered in exactly one place, as a miss already was",
      forgot.count('record_failure("forgot"') == 1
      and forgot.count("return dict(_FORGOT_ANSWER)") == 1)
check("an address cannot be SET on a box that does not use email",
      "this box does not use email" in route)
check("...but removing one still can, so none is trapped on a row",
      "wants_address = bool(" in route and "if wants_address and not sysconfig_service.email_enabled(" in route)

page = read("frontend", "src", "pages", "Forgot.jsx")
check("the Forgot screen opens on the box, and drops the chooser",
      'if (d.email === false) { setEmailOff(true); setMode("box"); }' in page
      and "{!emailOff && (" in page)
# ...and opens there DIRECTLY. Which way back in a box has is not known until
# /api/public answers, so a screen that renders first and corrects itself
# paints the email form and then swaps it -- a flash on the one screen whose
# whole job is to say, calmly, what to do next.
check("...without painting the email form first and taking it back",
      "const [ready, setReady] = useState(false);" in page
      and ".finally(() => setReady(true));" in page
      and "{!ready ? (" in page)
panel = read("frontend", "src", "components", "AccountsPanel.jsx")
check("the Accounts menu stops offering an address it cannot save",
      'data?.can_set_tier && data?.email !== false' in panel)

# -- the page itself -------------------------------------------------------------------
hub = read("frontend", "src", "components", "SettingsHub.jsx")
check("System is a tab, beside Accounts",
      'id: "system"' in hub and "SystemPanel" in hub
      and hub.index('id: "system"') < hub.index('id: "retention"'))
sys_panel = read("frontend", "src", "components", "SystemPanel.jsx")
check("it holds both switches", '"/api/system/settings"' in sys_panel
      and "auto_logout: on" in sys_panel and "email: on" in sys_panel)
check("turning email off asks, and says what is left on THIS box",
      "data?.panel_reset" in sys_panel and "hold the USER button" in sys_panel
      and "a shell on the box" in sys_panel)
check("...and asks harder where the panel cannot answer either",
      'destructive: !panel' in sys_panel and 'requireText: panel ? null' in sys_panel)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
