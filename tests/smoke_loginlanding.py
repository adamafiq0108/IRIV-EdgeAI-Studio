# Signing in on a system port lands on the Systems hub -- always.
#
# A master serves its own UI on its own port (5101, 5102, ...). Signing in
# there used to keep you there whenever the URL carried a deep link, and could
# keep you there even without one: the landing target came from a SECOND
# request (/api/public) that the login form did not wait for, so a slow or
# failed one read as "stay on this master".
#
# Now the login RESPONSE carries it, and the rule has no exceptions: on a
# master, every sign-in goes to the hub (port 5100 unless a deployment says
# otherwise). Arriving at the box means arriving at the grid; a system page is
# somewhere you go from there. The hub answers with an empty target, so it
# never bounces to itself and its own deep links -- already on 5100 -- are
# unaffected.
#
# fastapi is not installed on a dev box, so the wiring is read from the source
# and the port arithmetic is exercised directly.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import net                                           # noqa: E402


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the login response carries the landing target ---------------------------
AUTH = read("core", "routes", "auth.py")
login = AUTH[AUTH.index('@router.post("/api/auth/login"'):]
login = login[:login.index("@router.post", 10)]
assert '"hub_url": _hub_url(request)' in login, \
    "the login response does not tell the client where to land"
# Only a MASTER bounces. The hub has no system, so _hub_url() answers "" and a
# sign-in there stays on the grid it is already looking at.
helper = AUTH[AUTH.index("def _hub_url(request):"):]
helper = helper[:helper.index("\n@router")]
assert 'getattr(st, "system", None) is None' in helper, helper
# The port is the Software port setting, read in one place (hubport).
assert "hubport.browser_url(request" in helper, helper
print("== the login response carries the hub target, and only from a master")

# -- the client obeys it, deep link or not ------------------------------------
LOGIN_JSX = read("frontend", "src", "pages", "Login.jsx")
assert "const hub = d.hub_url || hubUrl;" in LOGIN_JSX, \
    "the login view does not prefer the response's target"
assert re.search(r"if \(hub\) \{\s*goWithSession\(withSession\(hub\)\);",
                 LOGIN_JSX), "the bounce is not unconditional"
# The old gate: a deep link (next !== "/") skipped the hub. Any surviving
# `next === "/"` test on the bounce would put that exception back.
bounce = LOGIN_JSX[LOGIN_JSX.index("const hub ="):]
bounce = bounce[:bounce.index("navigate(next")]
assert 'next === "/"' not in bounce, \
    "a deep link still skips the hub"
print("== every sign-in on a master bounces, deep link or not")

# -- the identity survives the port change ------------------------------------
# The hub is a different PORT, so it is a different origin: sessionStorage
# does not follow. Without withSession() the operator lands on the hub logged
# out -- which is a login that sends you back to a login.
assert "withSession" in bounce, bounce
print("== the session rides across the port change")

# -- first-run account setup lands the same way -------------------------------
# That screen is where a first sign-in is diverted TO, so finishing it has to
# arrive where a sign-in arrives.
SETUP = read("frontend", "src", "pages", "AccountSetup.jsx")
assert "withSession(hubUrl)" in SETUP, \
    "finishing account setup does not land on the hub"
assert '/api/public' in SETUP, SETUP[:400]
print("== finishing account setup lands on the hub too")

# -- and signing OUT leaves by the same door ---------------------------------
# A login screen on a system port is a door into ONE system: the next person
# at this browser should be met by the board, not by whichever system the last
# one had open.
logout = AUTH[AUTH.index('@router.post("/api/auth/logout"'):]
logout = logout[:logout.index("@router.post", 10)]
assert '"hub_url": _hub_url(request)' in logout, \
    "the logout response does not say where the login screen is"
SIGNOUT = read("frontend", "src", "components", "SignOutDialog.jsx")
# `hub || undefined`, so an empty answer falls through to what the shell
# remembered at boot and then to the hub's port on this host -- a logout whose
# request never landed still has to leave for the hub.
assert "loginUrl(hub || undefined)" in SIGNOUT, \
    "sign-out does not leave for the hub"
assert "withSession" not in SIGNOUT, \
    "sign-out carries a session to the login screen"
print("== signing out lands on the hub's login, carrying no identity")

# -- a session that expires under a poll goes the same way --------------------
CLIENT = read("frontend", "src", "api", "client.js")
assert "export function rememberHub" in CLIENT and "export function loginUrl" in CLIENT
assert "loginUrl(_hubUrl, p + window.location.search)" in CLIENT, \
    "the 401 bounce still assumes this port's login"
# `next` names a path on the MASTER, so it must not travel to the hub, which
# does not serve it.
lu = CLIENT[CLIENT.index("export function loginUrl"):]
lu = lu[:lu.index("\n}")]
# The two cross-port answers -- the known hub, and the port fallback -- both
# end at a bare /login. Only the same-origin last line may carry `next`.
cross = [ln for ln in lu.splitlines() if "/login" in ln and "next" not in ln]
assert len(cross) >= 2, lu          # the known hub, and the port fallback
# `next` appears only on the same-origin line: the two cross-port answers
# above return a bare /login, because it names a path the hub does not serve.
assert not [ln for ln in lu.splitlines()
            if "next" in ln and "/login" in ln and "hub" in ln.lower()], lu
SHELL = read("frontend", "src", "components", "AppShell.jsx")
assert 'rememberHub(d.hub_url, d.mode === "hub")' in SHELL, \
    "nothing remembers the hub for a later bounce"
print("== an expired session bounces to the hub too, without a stale next")

# -- and it leaves even when NOTHING answered ---------------------------------
# The difference between "always" and "usually": a logout whose request never
# landed, on a page that was never booted with an answer, still has to leave a
# system port. Last resort is this host on the hub's own port.
assert "HUB_PORT_FALLBACK" in CLIENT and '"5100"' in CLIENT, CLIENT[:400]
assert "window.location.port !== HUB_PORT_FALLBACK" in CLIENT, CLIENT
# ...but only for a page that HAS a port of its own. No port in the address
# means 80/443, the box's front door; bouncing that to :5100 broke HTTPS (5100
# is plain HTTP, so the browser showed ERR_SSL_PROTOCOL_ERROR after sign-out
# or an expired session).
assert "!_isHub && window.location.port && window.location.port !==" in CLIENT, CLIENT
# ...and the hub itself must never bounce to a port it may not be on: an empty
# hub_url means "I don't know" on a master and "I am it" on the hub, and only
# the boot payload can tell those apart.
assert "let _isHub = false" in CLIENT and "!_isHub &&" in CLIENT, CLIENT
print("== with no answer at all it still leaves a system port for 5100")

# -- the three answers come from ONE place ------------------------------------
# public, login and logout all have to agree about where the hub is; three
# copies of the same rewrite is three chances to drift.
assert AUTH.count("hubport.browser_url(") == 1 and "default_port=5100" not in AUTH, \
    "the hub URL is worked out in more than one place"
print("== one _hub_url(), used by public, login and logout")

# -- the port itself ----------------------------------------------------------
# The HOST always follows the request, so the bounce lands on the address the
# operator is actually browsing -- never a stale IP, never a localhost their
# laptop cannot reach. Only the port is deployment policy.
class Req:
    def __init__(self, host):
        self.headers = {"host": host}
        self.url = type("U", (), {"hostname": host.split(":")[0]})()
        self.client = None


# Nothing configured at all still produces a working link.
assert net.rewrite("", Req("192.168.1.234:5101"), default_port=5100) \
    == "http://192.168.1.234:5100"
# A configured hub URL keeps its port but follows the browsed host.
assert net.rewrite("http://10.0.0.5:8080/", Req("192.168.1.234:5101"),
                   default_port=5100) == "http://192.168.1.234:8080/"
print("== the port is policy, the host follows the browser")
print("ALL OK")
