# Master password reset from the box: hold USER five seconds, a six-digit
# code appears on the OLED, and the Forgot page takes it with a new password.
#
# The emailed link is the other way in; this one needs no address on file and
# no mailbox, which is the case it exists for. What it must hold:
#
# * the code is single-use and dies after ten minutes or five wrong guesses;
# * it is stored hashed, in the shared config dir (the hub's panel mints it,
#   any master's Forgot page may take it), never in plain text;
# * it resets MASTER and nobody else; the route is open (there is no session
#   to have) but rate-limited like the link reset, and the hold is audited;
# * the panel's long press no longer powers the box off, and the Front Panel
#   settings page no longer offers Shut down / Reboot (the sign-out menu does).
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth as auth_service                # noqa: E402
from core.services import panel_reset                         # noqa: E402

CFG = tempfile.mkdtemp()
auth_service.set_master_password("firstpass1", CFG)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


check("no code until the button is held", panel_reset.active(CFG) == 0)
code = panel_reset.begin(CFG)
check("the code is six digits", len(code) == 6 and code.isdigit(), code)
check("...and live for ten minutes", 590 < panel_reset.active(CFG) <= 600)
stored = io.open(os.path.join(CFG, "panel_reset.local.toml"), encoding="utf-8").read()
check("...stored hashed, never as the digits", code not in stored and "digest" in stored)
check("a wrong guess is refused", panel_reset.consume("000000", CFG) is False)
check("...and the code survives it", panel_reset.active(CFG) > 0)
check("the right code, with spaces the way the screen shows it, is taken",
      panel_reset.consume(code[:3] + " " + code[3:], CFG) is True)
check("...once: the same code is spent", panel_reset.consume(code, CFG) is False
      and panel_reset.active(CFG) == 0)

code = panel_reset.begin(CFG)
for _ in range(panel_reset.MAX_ATTEMPTS):
    panel_reset.consume("999999", CFG)
check("five wrong guesses burn the code", panel_reset.active(CFG) == 0
      and panel_reset.consume(code, CFG) is False)

code = panel_reset.begin(CFG)
panel_reset._save({**panel_reset._load(CFG), "expires": int(time.time()) - 1}, CFG)
check("an expired code is dead", panel_reset.consume(code, CFG) is False)
check("holding again mints a fresh code and restarts the clock",
      panel_reset.begin(CFG) != code and panel_reset.active(CFG) > 590)

# -- the route ---------------------------------------------------------------------
src = io.open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
block = src[src.index('name="auth.panel_reset"'):]
block = block[:block.index("@router.", 10)]
opens = src[src.index("OPEN_PREFIXES = ("):]
opens = opens[:opens.index(")")]
check("the route is open (nobody signed in can be asked to sign in)",
      '"/api/auth/panel-reset"' in opens)
check("...rate-limited like the link reset, and a miss counts",
      'ratelimit_service.check("reset", ip)' in block
      and 'ratelimit_service.record_failure("reset", ip)' in block)
check("...resets master and nobody else, then ends every master session",
      "set_master_password(password, cfg)" in block and "set_user_password" not in block
      and "revoke_sessions_of(who" in block)
check("...checks the password before spending the code",
      block.index("len(password) < 8") < block.index("panelreset_service.consume"))
check("...and tells master in their Inbox", "password_changed" in block)

# -- the panel ---------------------------------------------------------------------
panel = io.open(os.path.join(ROOT, "core", "services", "panel.py"), encoding="utf-8").read()
hold = panel[panel.index("def _on_hold(self):"):]
hold = hold[:hold.index("def ", 10)]
check("the long press shows the code, it does not power off",
      "request_master_reset" in hold and "shutdown" not in hold)
check("...and the screen has a page for it, code big, valid-for line",
      'page_id == "reset"' in panel and "size=2" in panel and "Valid 10 min" in panel)
check("...audited and posted to the admins' Inbox",
      '"password_reset_requested", "master"' in panel and "notify_admins" in panel)

# -- the pages -----------------------------------------------------------------------
forgot = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Forgot.jsx"), encoding="utf-8").read()
check("the Forgot page offers both ways: by email, or the code at the box",
      "/api/auth/forgot" in forgot and "/api/auth/panel-reset" in forgot
      and "At the box" in forgot and "one-time-code" in forgot)
settings = io.open(os.path.join(ROOT, "frontend", "src", "components", "PanelSettings.jsx"),
                   encoding="utf-8").read()
check("the Front Panel page says what the hold does now and offers no power buttons",
      "reset the master password" in settings and "Shut down" not in settings
      and '"reboot"' not in settings)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
