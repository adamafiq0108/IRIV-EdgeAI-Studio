# Registered sign-in devices: a browser is a DEVICE, an account may register
# at most ten of them, and one unused for thirty days must register again.
#
# Owner decision 2026-10-13, after the MAC address was ruled out as the
# identity (never sent by a browser, randomised by phones, spoofable): the
# server mints a random 128-bit id at the first sign-in, keeps it in an
# HttpOnly cookie, and stores only its hash. This drives the real login route
# and middleware (fastapi stubbed -- it is not installed on a dev box) and
# the service underneath, and pins:
#
#   * registration at sign-in, one device for many sign-ins of one browser;
#   * the cap: the 11th device is refused with the owner's sentence and NO
#     session; per account; master uncapped and able to remove anyone's;
#   * developer rows never shown to or removable by a customer;
#   * the 30-day sweep (fake clock): device gone, sessions revoked, cookie
#     cleared at next contact, a fresh id only through a real sign-in;
#   * legacy sessions from before the update: grouped, not counted, never
#     locked out, folded in when the browser signs in again;
#   * cookie attributes on HTTP and HTTPS; a made-up id is never adopted;
#   * a device cookie with no session opens nothing;
#   * the MAC lookup: non-IP input never reaches the subprocess, a missing or
#     failing `ip` gives "", the ARP table is read without a process at all;
#   * the audit rows, and a race for the last slot giving it to exactly one.
#
#     py -3 tests/smoke_signindevices.py
import asyncio
import os
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class _Resp:
    def __init__(self, content=None, status_code=200, headers=None):
        self.content, self.status_code = content, status_code
        self.headers = dict(headers or {})
        self.cookies = {}
        self.deleted = {}

    def set_cookie(self, key, value="", **kw):
        self.cookies[key] = (value, kw)

    def delete_cookie(self, key, **kw):
        self.deleted[key] = kw


if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail, self.headers = status_code, detail, headers
    fa.HTTPException = HTTPException
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = _Resp
    resp.RedirectResponse = resp.Response = resp.StreamingResponse = \
        resp.FileResponse = _Resp
    sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402
from core import config                                      # noqa: E402
from core.routes import auth as R                            # noqa: E402
from core.services import auth as A                          # noqa: E402
from core.services import device_sweep, neighmac             # noqa: E402

CFG = tempfile.mkdtemp()
A.set_master_password("master-pass-1", CFG)
MASTER = A.admin_username(CFG)
A.create_user("alice", "alice-pass-1", "*", CFG)
A.create_user("bob", "bob-pass-1", "*", CFG)
A.create_user("vendor", "vendor-pass-1", "*", CFG)
users = config.load_local("users", CFG)
users["users"]["vendor"]["developer"] = True
config.save_local("users", users, CFG)
STATE = types.SimpleNamespace(config_dir=CFG)

AUDIT = []
R.audit_service.record = lambda action, **kw: AUDIT.append((action, kw)) or True
neighmac.lookup = lambda ip, **kw: ""          # the MAC part has its own checks
CHROME_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


def req(path="/api/x", method="GET", body=None, cookies=None, token="",
        ip="192.168.1.20", https=False, ua=CHROME_WIN, state=None):
    async def _json():
        return body or {}
    headers = {"User-Agent": ua}
    if token:
        headers["X-Session"] = token
    return types.SimpleNamespace(
        url=types.SimpleNamespace(path=path, hostname="box",
                                  scheme="https" if https else "http"),
        method=method, headers=headers, cookies=dict(cookies or {}),
        client=types.SimpleNamespace(host=ip),
        app=types.SimpleNamespace(state=STATE),
        state=state or types.SimpleNamespace(), json=_json, body=_json)


def login(user, password, cookies=None, ip="192.168.1.20", https=False, ua=CHROME_WIN):
    """POST /api/auth/login. Returns (status, content, response)."""
    r = req("/api/auth/login", "POST", {"username": user, "password": password},
            cookies=cookies, ip=ip, https=https, ua=ua)
    try:
        out = asyncio.run(R.login_json(r))
    except HTTPException as e:
        return e.status_code, {"detail": e.detail}, None
    return out.status_code, out.content, out


def device_cookie(resp, https=False):
    name = A.DEVICE_COOKIE_SECURE if https else A.DEVICE_COOKIE
    return resp.cookies.get(name, (None, {}))


MIDDLEWARE = R.require()


def hit(path="/api/hub", token="", cookies=None, ip="192.168.1.20"):
    async def call_next(request):
        return _Resp({"ok": True}, 200)
    return asyncio.run(MIDDLEWARE(req(path, token=token, cookies=cookies, ip=ip),
                                  call_next))


def as_user(user, token, cookies=None, path="/api/auth/sessions", method="GET"):
    st = types.SimpleNamespace(user=user, session_sid=A.session_sid(token))
    return req(path, method, cookies=cookies, token=token, state=st)


def listing(user, token, cookies=None):
    return R.list_sessions(as_user(user, token, cookies))


def actions(name):
    return [kw for a, kw in AUDIT if a == name]


# -- registration at sign-in ------------------------------------------------------
code, body, resp = login("alice", "alice-pass-1")
raw, kw = device_cookie(resp)
check("a sign-in from a new browser succeeds and sets a device cookie",
      code == 200 and A.valid_device_id(raw or ""), (code, body, resp and resp.cookies))
check("the cookie is HttpOnly, SameSite=Lax, Path=/, not Secure on plain HTTP, ~400 days",
      kw.get("httponly") is True and kw.get("samesite") == "lax"
      and kw.get("path") == "/" and kw.get("secure") is False
      and kw.get("max_age") == 400 * 86400 and "domain" not in kw, kw)
check("the box stores the HASH of the id, never the id itself",
      raw not in open(os.path.join(CFG, "sessions.local.toml"), encoding="utf-8").read()
      and any(d.get("hash") == A.device_hash(raw) for d in A._devices_table(CFG).values()))
check("...and audits it as device.registered",
      len(actions("device.registered")) == 1
      and actions("device.registered")[0]["username"] == "alice")
tok1 = body["token"]
row = A._sessions_table(CFG)[A.session_sid(tok1)]
check("the session row points at its device", row.get("dev", "").startswith("d"), row)

# -- one browser, several sessions, one device ---------------------------------
toks = [tok1]
for _ in range(3):
    code, body, resp2 = login("alice", "alice-pass-1", cookies={A.DEVICE_COOKIE: raw})
    toks.append(body["token"])
    check("signing in again from the same browser keeps the same id",
          device_cookie(resp2)[0] == raw)
data = listing("alice", toks[-1], {A.DEVICE_COOKIE: raw})
mine = [d for d in data["devices"] if d["user"] == "alice"]
check("four sign-ins of one browser are ONE device with four sessions",
      len(mine) == 1 and mine[0]["sessions_count"] == 4 and data["device_count"] == 1,
      mine)
check("...it is this device, online, named by browser and system",
      mine[0]["current"] and mine[0]["status"] == "online"
      and mine[0]["name"].startswith("Chrome") and "Windows" in mine[0]["name"]
      and mine[0]["ip"] == "192.168.1.20", mine[0])
check("...registered only once in the audit", len(actions("device.registered")) == 1)
check("the old flat session list is still there for older callers",
      len([s for s in data["sessions"] if s["user"] == "alice"]) == 4
      and all("sid" in s and "device" in s for s in data["sessions"]))
check("no device id or hash is ever in the API answer",
      raw not in repr(data) and A.device_hash(raw) not in repr(data))

# -- the cap: ten devices, the eleventh refused, no session ----------------------
for i in range(9):                                 # 9 more browsers: 10 in all
    code, body, _ = login("alice", "alice-pass-1", ip=f"192.168.1.{100 + i}")
    check(f"browser {i + 2} registers", code == 200, body)
before = len(A.all_sessions(CFG))
code, body, resp11 = login("alice", "alice-pass-1", ip="192.168.1.250")
check("the 11th browser is refused with the owner's sentence",
      code == 403 and body.get("device_limit") is True
      and body.get("error") == ("This account already has 10 registered devices. "
                                "Remove one in Accounts > Login Sessions, or ask an "
                                "administrator."), (code, body))
check("...and NO session is created, no token, no cookie",
      len(A.all_sessions(CFG)) == before and "token" not in body
      and not resp11.cookies, resp11.cookies)
check("...audited as device.refused (result fail)",
      actions("device.refused") and actions("device.refused")[-1]["result"] == "fail")
code, body, _ = login("alice", "alice-pass-1", cookies={A.DEVICE_COOKIE: raw})
check("an already registered browser still signs in at the cap", code == 200, body)
tok_alice = body["token"]
code, body, _ = login("alice", "wrong-password", ip="192.168.1.251")
check("a wrong password is still a 401, the cap is not consulted first",
      code == 401, (code, body))

# -- per account -------------------------------------------------------------------
code, body, respb = login("bob", "bob-pass-1", cookies={A.DEVICE_COOKIE: raw})
check("alice at her cap does not stop bob", code == 200, body)
check("...the same browser keeps its id under a second account",
      device_cookie(respb)[0] == raw)
tok_bob = body["token"]
bob_view = listing("bob", tok_bob, {A.DEVICE_COOKIE: raw})
check("bob sees only his own devices, and counts 1 of 10",
      {d["user"] for d in bob_view["devices"]} == {"bob"}
      and bob_view["device_count"] == 1 and bob_view["device_limit"] == 10,
      bob_view["devices"])

# -- master: uncapped, sees all, removes anyone's (but not the vendor's) ------------
for i in range(12):
    code, body, _ = login(MASTER, "master-pass-1", ip=f"10.1.0.{i + 1}")
check("the master account is never capped (12 browsers)", code == 200, body)
tok_master = body["token"]
code, body, _ = login("vendor", "vendor-pass-1", ip="10.9.9.9")
tok_vendor = body["token"]
mview = listing(MASTER, tok_master)
check("master's list has every customer account's devices",
      {"alice", "bob", MASTER} <= {d["user"] for d in mview["devices"]}
      and mview["device_capped"] is False)
check("...but never the developer's", all(d["user"] != "vendor" for d in mview["devices"]))
vendor_dev = next(d for d in A.devices_view(CFG) if d["user"] == "vendor")
try:
    R.remove_device(vendor_dev["id"], as_user(MASTER, tok_master, method="DELETE"))
    check("master cannot remove a developer's device", False, "it was removed")
except HTTPException as e:
    check("master cannot remove a developer's device (same 404 as unknown)",
          e.status_code == 404)
alice_other = next(d for d in A.devices_view(CFG)
                   if d["user"] == "alice" and d["ip"] == "192.168.1.100")
try:
    R.remove_device(alice_other["id"], as_user("bob", tok_bob, method="DELETE"))
    check("bob cannot remove alice's device", False, "it was removed")
except HTTPException as e:
    check("bob cannot remove alice's device (404, not 403: no enumeration)",
          e.status_code == 404)
victim_tok = None
for s in A.all_sessions(CFG):
    if s["dev"] == alice_other["id"]:
        victim_tok = s["sid"]
out = R.remove_device(alice_other["id"], as_user(MASTER, tok_master, method="DELETE"))
check("master removes one of alice's devices", out["ok"] and out["sessions"] == 1, out)
check("...its session is revoked at once",
      A._sessions_table(CFG)[victim_tok]["revoked"] is True)
check("...audited as device.removed by master, naming alice",
      actions("device.removed") and actions("device.removed")[-1]["username"] == MASTER
      and "alice" in actions("device.removed")[-1]["detail"])
code, body, _ = login("alice", "alice-pass-1", ip="192.168.1.252")
check("the freed slot takes a new browser", code == 200, body)
for bad_ref in ("../x", "d123", "DROP", "legacy-zz", ""):
    try:
        R.remove_device(bad_ref, as_user(MASTER, tok_master, method="DELETE"))
        check(f"a malformed ref {bad_ref!r} is refused", False)
    except HTTPException as e:
        check(f"a malformed ref {bad_ref!r} is a 404", e.status_code == 404)

# -- the HTTPS cookie -------------------------------------------------------------
code, body, resph = login("bob", "bob-pass-1", https=True, ip="192.168.1.30")
sraw, skw = device_cookie(resph, https=True)
check("over HTTPS the cookie is __Secure-cytron_device, Secure, HttpOnly, Lax",
      A.valid_device_id(sraw or "") and skw.get("secure") is True
      and skw.get("httponly") is True and skw.get("samesite") == "lax"
      and A.DEVICE_COOKIE not in resph.cookies, resph.cookies)

# -- a made-up id is never adopted --------------------------------------------------
forged = "f" * 32
code, body, respf = login("bob", "bob-pass-1", cookies={A.DEVICE_COOKIE: forged},
                          ip="192.168.1.31")
check("a sign-in presenting an unknown id gets a FRESH one (no fixation)",
      code == 200 and device_cookie(respf)[0] not in (forged, None))
code, body, respm = login("bob", "bob-pass-1", cookies={A.DEVICE_COOKIE: "x; y"},
                          ip="192.168.1.32")
check("a malformed cookie is ignored, a fresh id is minted",
      code == 200 and A.valid_device_id(device_cookie(respm)[0] or ""))

# -- a device cookie is not a credential -------------------------------------------
r = hit("/api/hub", token="", cookies={A.DEVICE_COOKIE: raw})
check("a stolen device cookie with no session gets 401", r.status_code == 401,
      r.status_code)
r = hit("/api/auth/sessions", token="", cookies={A.DEVICE_COOKIE: raw})
check("...on the sessions list too", r.status_code == 401)

# -- the middleware keeps the cookie honest -------------------------------------------
r = hit("/api/hub", token=tok_alice, cookies={A.DEVICE_COOKIE: forged})
check("a cookie naming no device is cleared at the next contact",
      A.DEVICE_COOKIE in r.deleted and r.status_code == 200, (r.deleted, r.status_code))
R._DEVICE_REFRESHED.clear()
r = hit("/api/hub", token=tok_alice, cookies={A.DEVICE_COOKIE: raw})
check("a live device cookie is refreshed (400 days again) on use",
      r.cookies.get(A.DEVICE_COOKIE, (None, {}))[0] == raw
      and r.cookies[A.DEVICE_COOKIE][1].get("max_age") == 400 * 86400, r.cookies)
r = hit("/api/hub", token=tok_alice, cookies={A.DEVICE_COOKIE: raw})
check("...but not on every request", A.DEVICE_COOKIE not in r.cookies)

# -- Remove Other Devices -------------------------------------------------------------
st = types.SimpleNamespace(user="bob", session_sid=A.session_sid(tok_bob))
out = R.remove_other_devices(req("/api/auth/devices/remove-others", "POST",
                                 cookies={A.DEVICE_COOKIE: raw}, token=tok_bob, state=st))
left = [d for d in A.devices_view(CFG) if d["user"] == "bob"]
check("Remove Other Devices keeps only this device, and this session",
      out["removed"] >= 3 and len(left) == 1
      and A.session_sid(tok_bob) in left[0]["sids"]
      and A.verify_session(tok_bob, CFG) == "bob", (out, left))
check("...audited", actions("device.remove_others")[-1]["username"] == "bob")

# -- Sign Out Other Sessions keeps registrations ---------------------------------------
n_dev = len([d for d in A.devices_view(CFG) if d["user"] == "alice"])
st = types.SimpleNamespace(user="alice", session_sid=A.session_sid(tok_alice))
R.revoke_other_sessions(req("/api/auth/sessions/revoke-others", "POST",
                            token=tok_alice, state=st))
check("Sign Out Other Sessions ends sessions but keeps the registrations",
      len([d for d in A._devices_table(CFG).values() if d["user"] == "alice"]) == n_dev
      and A.verify_session(tok_alice, CFG) == "alice")
code, body, _ = login("alice", "alice-pass-1", ip="192.168.1.253")
check("...so alice is still at her cap afterwards", code == 403, (code, body))

# -- the 30-day sweep, fake clock ----------------------------------------------------------
later = int(time.time()) + 31 * 86400
seen_rows = []
n = device_sweep.run(CFG, now=later,
                     record=lambda a, **kw: seen_rows.append((a, kw)))
check("thirty-one days on, the sweep removes every device",
      n >= 10 and not [d for d in A._devices_table(CFG).values()], n)
check("...one device.expired audit row each, under the owning account",
      len(seen_rows) == n and all(a == "device.expired" for a, _ in seen_rows)
      and any(kw["username"] == "alice" for _, kw in seen_rows)
      and "inactive for 30 days" in seen_rows[0][1]["detail"], seen_rows[:2])
check("...and every session of those devices is revoked",
      A.verify_session(tok_alice, CFG) is None)
r = hit("/api/hub", token="", cookies={A.DEVICE_COOKIE: raw})
check("the expired browser's next contact is a 401 that clears its cookie",
      r.status_code == 401 and A.DEVICE_COOKIE in r.deleted, (r.status_code, r.deleted))
code, body, respr = login("alice", "alice-pass-1", cookies={A.DEVICE_COOKIE: raw})
check("signing in again REGISTERS again, with a new id",
      code == 200 and device_cookie(respr)[0] not in (raw, None))
check("a pass with nothing stale writes nothing",
      device_sweep.run(CFG, record=lambda *a, **k: None) == 0)

# -- a record that aged out between sweeps does not hold the cap ------------------------
CFG2 = tempfile.mkdtemp()
A.set_master_password("master-pass-1", CFG2)
A.create_user("carol", "carol-pass-1", "*", CFG2)
old = int(time.time()) - 40 * 86400
for i in range(10):
    A.register_device("carol", "", "Chrome · Windows", f"10.0.0.{i}", config_dir=CFG2,
                      now=old)
res = A.register_device("carol", "", "Firefox · Linux", "10.0.0.99", config_dir=CFG2)
check("ten devices unused for 40 days do not block an 11th: they expire on the way",
      res["created"] and len(res["expired"]) == 10
      and len(A._devices_table(CFG2)) == 1, res)

# -- legacy sessions: grouped, not counted, never locked out --------------------------------
CFG3 = tempfile.mkdtemp()
A.set_master_password("master-pass-1", CFG3)
A.create_user("dave", "dave-pass-1", "*", CFG3)


def legacy_session(user, words, ip):
    tok = A.issue_session(user, CFG3)
    A.register_session(user, A.session_sid(tok), device=words, ip=ip, config_dir=CFG3)
    return tok


L = [legacy_session("dave", "Chrome · Windows", "192.168.1.40") for _ in range(3)]
L.append(legacy_session("dave", "Safari · iPhone", "192.168.1.41"))
view = [d for d in A.devices_view(CFG3) if d["user"] == "dave"]
check("pre-update sessions group by (account, browser words, IP) into legacy devices",
      len(view) == 2 and all(d["legacy"] for d in view)
      and sorted(d["sessions"].__len__() for d in view) == [1, 3], view)
check("...they keep working after the update",
      all(A.verify_session(t, CFG3) == "dave" for t in L))
for i in range(10):
    A.register_device("dave", "", "Edge · Windows", f"10.2.0.{i}", config_dir=CFG3)
check("...and do not count toward the cap (10 real devices still allowed)",
      len([d for d in A._devices_table(CFG3).values() if d["user"] == "dave"]) == 10)
CFG4 = tempfile.mkdtemp()
A.set_master_password("master-pass-1", CFG4)
A.create_user("erin", "erin-pass-1", "*", CFG4)
tok = A.issue_session("erin", CFG4)
A.register_session("erin", A.session_sid(tok), device="Chrome · Windows",
                   ip="192.168.1.50", config_dir=CFG4)
res = A.register_device("erin", "", "Chrome · Windows", "192.168.1.50", config_dir=CFG4)
v = A.devices_view(CFG4)
check("when the browser signs in again its legacy sessions fold into the new device",
      len(v) == 1 and not v[0]["legacy"] and v[0]["id"] == res["ref"]
      and len(v[0]["sessions"]) == 1, v)
legacy_ref = None
tokL = legacy_session("dave", "Opera · Linux", "192.168.1.60")
legacy_ref = next(d["id"] for d in A.devices_view(CFG3)
                  if d["legacy"] and d["name"] == "Opera · Linux")
out = A.remove_device(legacy_ref, config_dir=CFG3)
check("removing a legacy device signs its sessions out",
      out and out["sessions"] == 1 and A.verify_session(tokL, CFG3) is None, out)
secret = A.ensure_secret(CFG3)
b3 = f"dave|{int(time.time()) + 300}"
check("a 3-part pre-registry token still verifies (nobody logged out by the update)",
      A.verify_session(f"{b3}|{A._sign(secret, b3)}", CFG3) == "dave")

# -- the race for the last slot --------------------------------------------------------------
CFG5 = tempfile.mkdtemp()
A.set_master_password("master-pass-1", CFG5)
A.create_user("fay", "fay-pass-1", "*", CFG5)
for i in range(9):
    A.register_device("fay", "", "Chrome · Windows", f"10.3.0.{i}", config_dir=CFG5)
wins, fails = [], []


def racer(i):
    try:
        wins.append(A.register_device("fay", "", "Chrome · Android", f"10.3.1.{i}",
                                      config_dir=CFG5))
    except A.DeviceLimitReached:
        fails.append(i)


threads = [threading.Thread(target=racer, args=(i,)) for i in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("six sign-ins racing for the last slot: exactly one gets it",
      len(wins) == 1 and len(fails) == 5
      and len(A._devices_table(CFG5)) == 10, (len(wins), fails))

# -- account delete and rename take the devices with them ------------------------------------
A.delete_user("fay", CFG5)
check("deleting an account deletes its device records", not A._devices_table(CFG5))
A.rename_devices_user("dave", "david", CFG3)
check("renaming an account moves its devices",
      {d["user"] for d in A._devices_table(CFG3).values()} == {"david"})

# -- the MAC lookup ------------------------------------------------------------------------------
import importlib                                              # noqa: E402
nm = importlib.reload(neighmac)
ARP = ("IP address       HW type     Flags       HW address            Mask     Device\n"
       "192.168.1.20     0x1         0x2         aa:bb:cc:dd:ee:01     *        eth0\n"
       "192.168.1.21     0x1         0x0         00:00:00:00:00:00     *        eth0\n")
calls = []


def runner(argv):
    calls.append(argv)
    return "192.168.1.30 dev eth0 lladdr AA:BB:CC:DD:EE:30 REACHABLE\n"


check("a client on the box's own LAN: MAC read from the ARP table",
      nm.lookup("192.168.1.20", read=lambda: ARP, runner=runner) == "aa:bb:cc:dd:ee:01"
      and not calls)
nm.clear_cache()
check("an incomplete entry is no answer",
      nm.lookup("192.168.1.21", read=lambda: ARP, runner=runner) == "")
check("a client from another network (not in the table): no MAC, no process",
      nm.lookup("10.20.30.40", read=lambda: ARP, runner=runner) == "" and not calls)
for hostile in ("1.2.3.4; reboot", "$(id)", "-h", "192.168.1.20 && ls", "box.local",
                "", None, 7, "::1", "127.0.0.1", "0.0.0.0", "224.0.0.1", "x" * 200):
    nm.clear_cache()
    got = nm.lookup(hostile, read=lambda: None, runner=runner)
    check(f"input {str(hostile)[:20]!r} never reaches the subprocess",
          got == "" and not calls, calls)
nm.clear_cache()
check("no ARP table: `ip -4 neigh show <ip>` with a fixed argv",
      nm.lookup("192.168.1.30", read=lambda: None, runner=runner) == "aa:bb:cc:dd:ee:30"
      and calls == [["ip", "-4", "neigh", "show", "192.168.1.30"]], calls)
nm.clear_cache()
check("an IPv4-mapped IPv6 peer is unwrapped before the lookup",
      nm.lookup("::ffff:192.168.1.20", read=lambda: ARP) == "aa:bb:cc:dd:ee:01")


def boom(argv):
    raise FileNotFoundError("ip")


nm.clear_cache()
check("`ip` missing: '-' (empty), never an exception",
      nm.lookup("192.168.1.31", read=lambda: None, runner=boom) == "")
nm.clear_cache()
check("`ip` failing or printing nonsense: empty",
      nm.lookup("192.168.1.32", read=lambda: None, runner=lambda a: "garbage") == "")
check("the real runner on a missing binary is empty, not a raise",
      nm._run(["definitely-not-a-binary-iriv"]) == "")
n0 = len(calls)
nm.clear_cache()
nm.lookup("192.168.1.30", read=lambda: None, runner=runner, now=1000)
nm.lookup("192.168.1.30", read=lambda: None, runner=runner, now=1030)
check("answers are cached per IP (one process a minute at most)",
      len(calls) - n0 == 1)
src = open(os.path.join(ROOT, "core", "services", "neighmac.py"), encoding="utf-8").read()
check("the subprocess never runs through a shell",
      "shell=False" in src and "shell=True" not in src and "os.system" not in src)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
