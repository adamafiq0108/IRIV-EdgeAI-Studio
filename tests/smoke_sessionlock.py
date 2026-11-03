# The Lock switch: a session that does not time out while a page is watched.
#
# The session token carries a FIXED expiry stamped at login and nothing renews
# it, so the two screens people leave running -- a system dashboard and the
# live wall -- sign themselves out mid-shift. Watching a feed is not activity
# as far as an idle timer is concerned, and it cannot be taught otherwise.
#
# Renewing on every request would have been the easy fix and the wrong one:
# any page polling in the background would then hold every session open
# forever, which is the timeout deleted rather than configured. So renewal is
# an explicit act -- one endpoint, called only while an operator holds a page's
# Lock switch on -- and this pins the three properties that makes it safe:
#
# * it needs a live session (it renews one, it cannot mint one);
# * it renews the CONFIGURED window rather than granting a longer one;
# * the page stops renewing when it is left, so the lock cannot outlive the
#   screen that is showing it.
import io
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth as auth_service                # noqa: E402

CFG = tempfile.mkdtemp()
auth_service.set_master_password("firstpass1", CFG)

# -- a renewal is a re-issue, nothing more -----------------------------------
# A minted token is registered, as login does: since the session registry, an
# unregistered 4-part token is dead on purpose (that is what revocation IS).
first = auth_service.issue_session("admin", CFG)
auth_service.register_session("admin", auth_service.session_sid(first),
                              config_dir=CFG)
assert auth_service.verify_session(first, CFG) == "admin"
time.sleep(1.1)
# The keepalive path re-issues the SAME session: same sid, later expiry.
again = auth_service.issue_session("admin", CFG,
                                   sid=auth_service.session_sid(first))
assert auth_service.verify_session(again, CFG) == "admin"
expiry_of = lambda tok: int(tok.split("|")[-2])               # noqa: E731
assert expiry_of(again) > expiry_of(first), (first, again)
# ...to exactly the window the deployment configured. A renewal that granted
# a longer session would be a second, invisible timeout policy.
window = auth_service.idle_minutes(CFG) * 60
assert abs(expiry_of(again) - (time.time() + window)) < 5, expiry_of(again)
print(f"== a renewal re-issues the same {window // 60}-minute window, later")

# A token that has already expired stays dead: the switch renews a session, it
# does not resurrect one, so a screen left locked overnight after a restart
# still lands on the login page.
dead = f"admin|{int(time.time()) - 5}"
dead = f"{dead}|{auth_service._sign(auth_service.ensure_secret(CFG), dead)}"
assert auth_service.verify_session(dead, CFG) is None
print("== an expired token is not renewable")

# -- the route ----------------------------------------------------------------
SRC = io.open(os.path.join(ROOT, "core", "routes", "auth.py"),
              encoding="utf-8").read()
block = SRC[SRC.index('name="auth.keepalive"'):]
block = block[:block.index("@router.post", 10)]

# Not open: it must run behind the session middleware, or it would be a way to
# mint a session from no session at all.
opens = SRC[SRC.index("OPEN_PREFIXES = ("):]
opens = opens[:opens.index(")")]
assert "keepalive" not in opens, opens
assert 'getattr(request.state, "user"' in block, block
assert "401" in block, block
print("== /api/auth/keepalive is gated and renews only the caller's own session")

# It reports the window so the page can pace itself: a client guessing "every
# fifteen minutes" is signed out by a deployment that sets the timeout to five.
assert "idle_minutes" in block, block
assert "set_cookie" in block and "issue_session" in block, block
print("== and reports the window rather than leaving the page to guess")

# -- the box-wide switch --------------------------------------------------------
# The per-page Lock switch is gone (2026-09-14): the operator asked for ONE
# decision, made once in Settings > Accounts, instead of a control to remember
# on two screens. Off is thirty days, not never -- a token with no expiry is a
# token that outlives being copied off a screen. OFF out of the box: a box
# that signed its watched dashboard out mid-shift was the complaint.
assert auth_service.auto_logout(CFG) is False, "auto logout must default to off"
assert auth_service.idle_minutes(CFG) == auth_service.NO_AUTO_LOGOUT_MINUTES == 43200
tok = auth_service.issue_session("admin", CFG)
assert abs(expiry_of(tok) - (time.time() + 43200 * 60)) < 5, expiry_of(tok)
auth_service.set_auto_logout(True, CFG)
assert auth_service.auto_logout(CFG) is True
assert auth_service.idle_minutes(CFG) == 60
auth_service.set_auto_logout(False, CFG)
assert auth_service.idle_minutes(CFG) == 43200
print("== auto logout is off by default (thirty-day window); on = the configured window")

policy = SRC[SRC.index('name="auth.session_policy"'):]
policy = policy[:policy.index("@router.", 10)]
assert "is_admin" in policy and "403" in policy, policy
assert "set_auto_logout" in policy and "audit_service.record" in policy, policy
print("== /api/auth/sessions/policy is master/developer only and audited")

for page in (os.path.join("frontend", "src", "pages", "Main.jsx"),
             os.path.join("frontend", "src", "components", "LiveWallPanel.jsx")):
    text = io.open(os.path.join(ROOT, page), encoding="utf-8").read()
    assert "SessionLock" not in text, f"{page} still carries the Lock switch"
assert not os.path.exists(os.path.join(ROOT, "frontend", "src", "components", "SessionLock.jsx"))
# The switch lives on Settings > System, with the other box-wide switches --
# and nowhere else. It was on Accounts as well for a while, two controls for
# one setting, which is how a box ends up with a stale one.
panel = io.open(os.path.join(ROOT, "frontend", "src", "components", "AccountsPanel.jsx"),
                encoding="utf-8").read()
assert "/api/auth/sessions/policy" not in panel, "Accounts still writes the session policy"
assert "Settings &gt; System" in panel, "Accounts does not say where the switch went"
sys_panel = io.open(os.path.join(ROOT, "frontend", "src", "components", "SystemPanel.jsx"),
                    encoding="utf-8").read()
assert "auto_logout: on" in sys_panel and "Auto Logout" in sys_panel, "no switch in Settings"
print("== the Lock switch is gone from the dashboard and the wall; one switch, on System")
print("ALL OK")
