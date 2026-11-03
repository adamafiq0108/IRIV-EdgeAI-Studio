# The Monitor account: one fixed sign-in that sees every system, opens only
# Dashboard / Analytics / Footage until master ticks more, may start and stop
# detection and download footage, and can never be anything stronger.
#
# What is worth pinning is what an attacker or a mistake would go for: that the
# row cannot be promoted or deleted through account management, that switching
# it off ends everything it could do (sign-in, token, sessions), that the
# access token is stored as a hash and dies with a replacement, and that the
# capabilities it holds are exactly the two it is fixed to.
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

if "fastapi" not in sys.modules:                  # master-only dependency
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), put=lambda *a, **k: (lambda f: f),
        delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {
        "__init__": lambda self, status_code=500, detail="", headers=None:
            setattr(self, "status_code", status_code) or setattr(self, "detail", detail)})
    fa.Request = object
    sys.modules["fastapi"] = fa
    fr = types.ModuleType("fastapi.responses")
    fr.Response = type("Response", (), {"__init__": lambda self, *a, **k: None})
    fr.JSONResponse = fr.Response
    sys.modules["fastapi.responses"] = fr

from core.routes import perm                                   # noqa: E402
from core.services import auth                                 # noqa: E402
from core.services import caps as caps_service                # noqa: E402
from core.services import monitor_account as mon              # noqa: E402

CFG = tempfile.mkdtemp()
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def raises(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Exception as e:                                     # noqa: BLE001
        return e
    return None


auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", "line_a", CFG, admin=True)
auth.create_user("bob", "passw0rd!", "line_a", CFG)

# -- off until somebody turns it on ------------------------------------------
st = mon.state(CFG)
check("nothing exists before it is switched on",
      not st["configured"] and not st["enabled"] and not auth.users(CFG).get("monitor"))

# -- switching on ---------------------------------------------------------------
check("a username that is not the administrator's or a taken one",
      isinstance(raises(mon.enable, "boss", "passw0rd!", CFG), ValueError)
      and raises(mon.enable, "bob", "passw0rd!", CFG) is not None)
st = mon.enable("wall", "passw0rd!", CFG)
check("switched on under the admin's chosen name",
      st["enabled"] and st["username"] == "wall" and mon.is_monitor("wall", CFG))
check("it has no password sign-in at all: an access token only",
      auth.verify_login("wall", "passw0rd!", CFG) is False
      and auth.verify_login("wall", "", CFG) is False)
row = auth.users(CFG)["wall"]
check("every system, now and later (systems = *)", row["systems"] == "*"
      and auth.allowed("wall", "line_a", CFG) and auth.allowed("wall", "anything_new", CFG)
      and auth.allowed("wall", None, CFG))
check("by default only Dashboard, Analytics and Footage",
      st["pages"] == ["main", "analytics", "footage"]
      and auth.page_grants("wall", "line_a", CFG) == {"main", "analytics", "footage"}
      and auth.page_grants("wall", "a_new_system", CFG) == {"main", "analytics", "footage"})
check("it holds exactly the two fixed capabilities",
      auth.cap_grants("wall", "line_a", CFG) == {"detection.power", "footage.download"}
      and tuple(caps_service.MONITOR_CAPS) == ("detection.power", "footage.download"))
check("it is an ordinary user tier, never an admin",
      auth.tier("wall", CFG) == auth.TIER_USER and not auth.is_admin("wall", CFG)
      and not auth.is_site_admin("wall", CFG) and not auth.is_developer("wall", CFG))

# -- it cannot be promoted, edited or deleted through account management ------
check("account management refuses to edit it",
      isinstance(raises(auth.update_user, "wall", admin=True, config_dir=CFG), ValueError)
      and isinstance(raises(auth.update_user, "wall", systems="line_a", config_dir=CFG), ValueError)
      and isinstance(raises(auth.update_user, "wall", enabled=False, config_dir=CFG), ValueError))
check("...and to delete it",
      isinstance(raises(auth.delete_user, "wall", CFG), ValueError)
      and "wall" in auth.users(CFG))
# A hand-edited row that claims admin on the monitor is still a plain user.
import copy                                                    # noqa: E402
from core import config as _cfgmod                             # noqa: E402
raw = _cfgmod.load_local("users", CFG)
raw["users"]["wall"]["admin"] = True
raw["users"]["wall"]["developer"] = True
auth._save_local("users", raw, CFG)
check("an admin or developer flag on the row changes nothing",
      auth.tier("wall", CFG) == auth.TIER_USER)
mon.enable("wall", "", CFG)
check("...and switching on again clears them",
      not auth.users(CFG)["wall"].get("admin") and not auth.users(CFG)["wall"].get("developer"))
check("the public account list marks it",
      [u for u in auth.public_users(CFG) if u["username"] == "wall"][0]["monitor"] is True)

# -- capabilities at the routes ---------------------------------------------------
class _Req:
    def __init__(self, user):
        self.state = types.SimpleNamespace(user=user)
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(config_dir=CFG, system=types.SimpleNamespace(slug="line_a")))


def refused(cap, user):
    try:
        perm.require(_Req(user), cap)
        return False
    except Exception:                                          # noqa: BLE001
        return True


check("the monitor may start and stop detection and download footage",
      not refused("detection.power", "wall") and not refused("footage.download", "wall"))
check("...and nothing else a page can change",
      all(refused(c, "wall") for c in caps_service.ALL_CAPS
          if c not in caps_service.MONITOR_CAPS))
check("an ordinary account still holds none of them",
      refused("detection.power", "bob") and refused("footage.download", "bob"))
try:
    perm.require_any(_Req("wall"), ("footage.download", "footage.export"))
    ok_any = True
except Exception:                                              # noqa: BLE001
    ok_any = False
check("footage download accepts the narrow capability or the wide one", ok_any)
check("...but the wide one (email, schedules) is not the monitor's",
      refused("footage.export", "wall"))

# -- the admin chooses which pages it may open -----------------------------------
check("master or the site admin may tick more pages",
      mon.set_pages(["main", "analytics", "footage", "events"], CFG)["pages"]
      == ["main", "analytics", "footage", "events"]
      and auth.page_grants("wall", "line_b", CFG) == {"main", "analytics", "footage", "events"})
check("never none, never an unknown page, never a developer page",
      isinstance(raises(mon.set_pages, [], CFG), ValueError)
      and isinstance(raises(mon.set_pages, ["nope"], CFG), ValueError)
      and isinstance(raises(mon.set_pages, ["blynk"], CFG), ValueError))

# -- the access tokens: one per central device ------------------------------------
made = mon.make_token("Plant dashboard", CFG)
token = made["token"]
check("a token is shown once and only its hash is stored",
      token.startswith("mon_") and token not in open(
          os.path.join(CFG, "users.local.toml"), encoding="utf-8").read()
      and mon.state(CFG)["has_token"] and mon.tokens(CFG)[0]["name"] == "Plant dashboard"
      and "hash" not in mon.tokens(CFG)[0])
check("it identifies the monitor, and which device it is",
      mon.verify_token(token, CFG) == ("wall", made["id"])
      and mon.verify_token(token + "x", CFG) is None
      and mon.verify_token("mon_nope", CFG) is None and mon.verify_token("", CFG) is None)
check("a device needs a name, and each name is used once",
      isinstance(raises(mon.make_token, "", CFG), ValueError)
      and isinstance(raises(mon.make_token, "plant DASHBOARD", CFG), ValueError))
other = mon.make_token("Office wall", CFG)
check("each device has its own token",
      mon.verify_token(other["token"], CFG) == ("wall", other["id"]) and
      len(mon.tokens(CFG)) == 2)
check("sign-in is remembered (last used, from where)",
      mon.verify_token(token, CFG, ip="10.0.0.9") and
      any(t["last_used"] for t in mon.tokens(CFG)))
check("the box's identity (hostname and ports' MACs) rides along for the connecting program",
      set(mon.box_identity()) == {"hostname", "mac", "macs"})
# A session the first token opened ends when that token is removed; the other's stays.
s1 = auth.issue_session("wall", CFG)
auth.register_session("wall", auth.session_sid(s1), device=mon.session_device(made["id"]), config_dir=CFG)
s2 = auth.issue_session("wall", CFG)
auth.register_session("wall", auth.session_sid(s2), device=mon.session_device(other["id"]), config_dir=CFG)
check("both sessions verify before a token is removed",
      auth.verify_session(s1, CFG) == "wall" and auth.verify_session(s2, CFG) == "wall")
mon.revoke_token(made["id"], CFG)
check("removing a device's token: it cannot connect and its session ends",
      mon.verify_token(token, CFG) is None and not auth.verify_session(s1, CFG))
check("...while another device is untouched",
      mon.verify_token(other["token"], CFG) == ("wall", other["id"])
      and auth.verify_session(s2, CFG) == "wall")
check("removing an unknown token says so", isinstance(raises(mon.revoke_token, "nope", CFG), KeyError))
mon.revoke_token(other["id"], CFG)
check("removing the last one leaves none", not mon.state(CFG)["has_token"]
      and mon.tokens(CFG) == [])

# -- which systems it may open, and keeping them online ----------------------------
from core.services import monitor_online as online             # noqa: E402

check("by default it may open every system, and all are kept online",
      mon.state(CFG)["systems"] == {"all": True, "slugs": []}
      and mon.keeps_online("line_a", CFG) and mon.keeps_online("later_one", CFG))
mon.set_systems(False, ["line_a", "line_b"], ["line_a", "line_b", "line_c"], CFG)
check("the admin can allow only some",
      mon.state(CFG)["systems"]["slugs"] == ["line_a", "line_b"]
      and auth.allowed("wall", "line_a", CFG) and not auth.allowed("wall", "line_c", CFG)
      and mon.keeps_online("line_b", CFG) and not mon.keeps_online("line_c", CFG))
check("never none, never a system the box does not have",
      isinstance(raises(mon.set_systems, False, [], ["line_a"], CFG), ValueError)
      and isinstance(raises(mon.set_systems, False, ["ghost"], ["line_a"], CFG), ValueError))
mon.enable("wall", "", CFG)
check("switching on again keeps the chosen systems",
      mon.state(CFG)["systems"]["slugs"] == ["line_a", "line_b"])
started = []
masters = {"line_a": {"url": "http://127.0.0.1:5101"},
           "line_b": {"url": "http://127.0.0.1:5102"},
           "line_c": {"url": "http://127.0.0.1:5103"}}
got = online.ensure_online(masters, "/repo", CFG,
                           answers=lambda url: url.endswith("5101"),
                           spawn=lambda repo, slug, port: started.append((slug, port)),
                           local=lambda host: True)
check("a dropped master of an allowed system is brought back",
      got == ["line_b"] and started == [("line_b", 5102)])
check("...and a system the monitor may not open is left alone",
      all(slug != "line_c" for slug, _p in started))
mon.disable(CFG)
check("switched off, nothing is kept online for it",
      online.keep_list(masters, CFG) == [])
mon.enable("wall", "", CFG)
mon.set_systems(True, [], [], CFG)

# -- renaming and switching off ---------------------------------------------------
mon.enable("screen", "", CFG)
check("a new name renames the account",
      mon.username(CFG) == "screen" and "wall" not in auth.users(CFG)
      and mon.is_monitor("screen", CFG))
tok = mon.make_token("Spare", CFG)["token"]
mon.disable(CFG)
check("switched off: no token, no access",
      mon.verify_token(tok, CFG) is None and not auth.allowed("screen", "line_a", CFG)
      and not mon.is_monitor("screen", CFG))
check("...but the account and its page choices are kept for next time",
      mon.state(CFG)["configured"] and mon.state(CFG)["pages"][0] == "main")
check("it cannot be given a token while it is off",
      isinstance(raises(mon.make_token, "Another", CFG), ValueError))
mon.enable("screen", "", CFG)
check("switching on again restores it (with new tokens only)",
      mon.is_monitor("screen", CFG) and mon.verify_token(tok, CFG) is None)


# -- the pages that drive it ----------------------------------------------------------
def src(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


panels = src("frontend", "src", "components", "MonitorPanels.jsx")
check("System settings carries the switch, the sign-in and the per-device tokens",
      "MonitorSwitchCard" in src("frontend", "src", "components", "SystemPanel.jsx")
      and "/api/hub/monitor/token" in panels and "Remove" in panels
      and "Device name" in panels)
accounts = src("frontend", "src", "components", "AccountsPanel.jsx")
check("Accounts shows a Monitor tab only while it is on, and never lists it as an ordinary user",
      "MonitorAccess" in accounts and "monitorOn" in accounts
      and "!u.monitor" in accounts)
routes = src("core", "routes", "monitor.py")
check("the switch and tokens are master's; pages and systems are the site admin's",
      routes.count("_master(request)") >= 3 and routes.count("_site_admin(request)") >= 3)
check("the token sign-in is open, throttled and audited",
      "/api/auth/monitor-token" in src("core", "routes", "auth.py")
      and 'ratelimit_service.check("login"' in routes
      and 'audit_service.record("login"' in routes)
hub = src("core", "routes", "hub.py")
check("a system open to the Monitor cannot be powered off",
      "monitor_account.keeps_online(slug, st.config_dir)" in hub)

# -- Pipi's review (2026-10-20) ------------------------------------------------------
check("an admin cannot reset the Monitor's password (it is set in System settings)",
      isinstance(raises(auth.set_user_password, mon.username(CFG), "newpassw0rd!", CFG), ValueError)
      and auth.verify_login(mon.username(CFG), "passw0rd!", CFG) is False)
check("the configuration routes of pages it has no part in are closed to it",
      all(mon.blocked(p) for p in ("/api/workflow", "/api/builder/export", "/api/hub/board",
                                   "/api/devices", "/api/devices/x", "/api/detector/rtsp_scan",
                                   "/api/detector/dataset/list", "/api/models/yolo/file",
                                   "/api/media", "/api/detector/control/modbus", "/api/board/io",
                                   "/api/email/config"))
      and not any(mon.blocked(p) for p in ("/api/main", "/api/analytics/x", "/api/workflow/live",
                                           "/api/workflow/inject", "/api/detector/snapshots",
                                           "/api/detector/clips", "/api/detector/footage/download",
                                           "/api/detector/control/power", "/api/auth/me",
                                           "/api/workflow/csv-file")))
check("the wall is wired into the session middleware, for the monitor only",
      "monitor_account.is_monitor(user, config_dir)" in src("core", "routes", "auth.py")
      and "monitor_account.blocked(path)" in src("core", "routes", "auth.py"))
check("the Monitor cannot power systems at all",
      "The Monitor account cannot power systems." in hub)
# A token that signs in in a loop only ends its own oldest sessions.
t5 = mon.make_token("Loop", CFG)
for _i in range(8):
    sess = auth.issue_session(mon.username(CFG), CFG)
    auth.register_session(mon.username(CFG), auth.session_sid(sess), device=mon.session_device(t5["id"]),
                          config_dir=CFG)
    mon.trim_token_sessions(t5["id"], CFG)
live = [x for x in auth.all_sessions(CFG) if x["device"] == mon.session_device(t5["id"])]
check("a token holds at most five sessions at once", len(live) == mon.MAX_TOKEN_SESSIONS, len(live))
mon.disable(CFG)
mon.enable(mon.username(CFG), "", CFG)
check("turning it off removes every token: they are not revived on turning it back on",
      mon.tokens(CFG) == [] and mon.verify_token(t5["token"], CFG) is None)

check("a program may send the session or the access token as Authorization: Bearer",
      'auth_h[:7].lower() == "bearer "' in src("core", "routes", "auth.py")
      and "bearer.startswith(monitor_account.TOKEN_PREFIX)" in src("core", "routes", "auth.py")
      and 'ratelimit_service.record_failure("login", bucket)' in src("core", "routes", "auth.py"))

foot = src("frontend", "src", "pages", "Footage.jsx")
check("the Footage page shows Download (selected, or all) to the narrow download capability too",
      '(can("footage.download") || can("footage.export"))' in foot
      and 'Download all' in foot and 'Download (${selected.size})' in foot)
print()
if bad:
    print(f"{bad} FAILED")
    sys.exit(1)
print("all good")
