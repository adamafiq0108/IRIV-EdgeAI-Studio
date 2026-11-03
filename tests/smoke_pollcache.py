# -*- coding: utf-8 -*-
"""The shared poll cache: the mechanism, the policy, and perm.shared.

Runs on the dev laptop with no FastAPI (stubbed, like smoke_overlayroute);
smoke_pollcacheroute.py drives the same code through a real FastAPI app.

  1. pollcache.PollCache (pure): TTL on an injected clock, single-flight
     (N threads -> one computation), bounded wait, a leader's exception or
     a None / {"ok": False} answer never stored or handed to a waiter, LRU
     bound, key isolation, clear() as a generation, counters with no keys
  2. pollshare: [system] share_polls defaults off and off is ZERO lookups,
     the query whitelist, the authorisation key (tier, caps, hub visibility,
     re-read every request), writes empty this process AND the others (the
     epoch stamp), keepalive does not, switching off empties it
  3. perm.shared on stub requests: two accounts with different grants,
     interleaved, each only ever sees its own answer; a grant changed
     between two polls takes effect on the next one; no account = never
     shared; `after` runs per request on a copy
  4. source pins: which handlers opt in, which are deliberately left out,
     where the write hook sits, who may read the counters, the UI card

    python tests/smoke_pollcache.py
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

if "fastapi" not in sys.modules:
    try:
        import fastapi                                         # noqa: F401
    except ImportError:
        fa = types.ModuleType("fastapi")
        fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
            get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail
        fa.HTTPException = HTTPException
        fa.Request = object
        sys.modules["fastapi"] = fa

from core.routes import perm                                   # noqa: E402
from core.services import auth                                 # noqa: E402
from core.services import pollshare                            # noqa: E402
from core.services import sysconfig                            # noqa: E402
from core.services.pollcache import PollCache, storable        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# == 1. the mechanism ===================================================================
clk = Clock()
c = PollCache(max_entries=8, wait_s=2.0, clock=clk)
calls = []


def make(v):
    def compute():
        calls.append(v)
        return {"ok": True, "v": v}
    return compute


a1 = c.get("k", 0.5, make(1))
a2 = c.get("k", 0.5, make(2))
check("a second request inside the TTL is handed the first answer, not recomputed",
      a1 is a2 and calls == [1], calls)
clk.t += 0.49
check("...still inside at 0.49 s", c.get("k", 0.5, make(3))["v"] == 1)
clk.t += 0.02
check("...and recomputed once the TTL has passed", c.get("k", 0.5, make(4))["v"] == 4)
check("the TTL counts from when the value was COMPUTED (injected clock)",
      c._entries["k"][0] == clk.t + 0.5)

# -- single-flight: N together, one computation
c = PollCache(wait_s=5.0)
n_calls = [0]
gate = threading.Barrier(16)
out = []


def slow():
    n_calls[0] += 1
    time.sleep(0.3)
    return {"ok": True, "v": "shared"}


def worker():
    gate.wait()
    out.append(c.get("dash", 1.0, slow))


ts = [threading.Thread(target=worker) for _ in range(16)]
[t.start() for t in ts]
[t.join() for t in ts]
check("16 requests arriving together cost ONE computation",
      n_calls[0] == 1 and len(out) == 16 and all(o is out[0] for o in out), n_calls)
k = c.counters()
check("...counted as 1 miss and 15 coalesced",
      k["misses"] == 1 and k["coalesced"] == 15 and k["hits"] == 0, k)

# -- bounded wait: a slow leader does not hold its waiters hostage
c = PollCache(wait_s=0.05)
started = threading.Event()
res = {}


def very_slow():
    started.set()
    time.sleep(0.5)
    return {"ok": True, "who": "leader"}


lead = threading.Thread(target=lambda: res.setdefault("leader", c.get("x", 5, very_slow)))
lead.start()
started.wait()
t0 = time.monotonic()
res["waiter"] = c.get("x", 5, lambda: {"ok": True, "who": "waiter"})
waited = time.monotonic() - t0
lead.join()
check("a waiter gives up after wait_s and computes for itself",
      res["waiter"]["who"] == "waiter" and waited < 0.4
      and c.counters()["fallbacks"] == 1, (res, waited))

# -- a leader's exception reaches its own caller only
c = PollCache(wait_s=2.0)
started = threading.Event()
release = threading.Event()
got = {}


def boom():
    started.set()
    release.wait()
    raise RuntimeError("db down")


def lead_boom():
    try:
        c.get("e", 5, boom)
    except RuntimeError as exc:
        got["leader"] = str(exc)


lt = threading.Thread(target=lead_boom)
lt.start()
started.wait()
wt = threading.Thread(target=lambda: got.setdefault(
    "waiter", c.get("e", 5, lambda: {"ok": True, "v": "own"})))
wt.start()
time.sleep(0.05)
release.set()
lt.join()
wt.join()
check("the leader's exception is raised to the leader",
      got.get("leader") == "db down", got)
check("...and the waiter computes its own answer instead of inheriting it",
      got.get("waiter") == {"ok": True, "v": "own"}, got)
check("...and the exception was not stored",
      "e" not in c._entries or c._entries["e"][1] == {"ok": True, "v": "own"})

# -- refusals and missing answers are never kept
c = PollCache()
for v in (None, {"ok": False, "error": "no"}):
    n = [0]

    def f(v=v):
        n[0] += 1
        return v
    c.get("r", 5, f)
    c.get("r", 5, f)
    check(f"{v!r} is handed back but never stored", n[0] == 2 and len(c) == 0)
check("storable: a list or an ok dict is, None / ok False is not",
      storable([]) and storable({"ok": True}) and storable({"x": 1})
      and not storable(None) and not storable({"ok": False}))

# -- LRU bound
c = PollCache(max_entries=3)
for i in range(10):
    c.get(("q", i), 60, make(i))
    if i == 1:
        c.get(("q", 0), 60, make(-1))           # keep 0 recently used
check("a flood of distinct keys never holds more than max_entries",
      len(c) == 3 and c.counters()["evictions"] == 7, c.counters())
check("...dropping the least recently used first",
      set(c._entries) == {("q", 7), ("q", 8), ("q", 9)}, list(c._entries))

# -- key isolation
c = PollCache()
va = c.get(("dashboard", "", ("master", "*", None)), 60, lambda: {"ok": True, "who": "master"})
vb = c.get(("dashboard", "", ("user", (), None)), 60, lambda: {"ok": True, "who": "user"})
check("two authorisation keys never share an entry",
      va["who"] == "master" and vb["who"] == "user")

# -- clear() is a generation: a computation already running is not stored
c = PollCache(wait_s=2.0)
inside = threading.Event()
go = threading.Event()


def before_write():
    inside.set()
    go.wait()
    return {"ok": True, "v": "pre-write"}


holder = {}
th = threading.Thread(target=lambda: holder.setdefault("r", c.get("w", 60, before_write)))
th.start()
inside.wait()
c.clear()                               # the write lands while it computes
after_clear = c.get("w", 60, lambda: {"ok": True, "v": "post-write"})
go.set()
th.join()
check("a request after the clear does not join the computation started before it",
      after_clear["v"] == "post-write", after_clear)
check("...the old computation answers its own caller, and is not stored",
      holder["r"]["v"] == "pre-write" and c._entries["w"][1]["v"] == "post-write")
check("counters carry counts only, no keys and no values",
      set(c.counters()) == {"hits", "misses", "coalesced", "fallbacks", "stored",
                            "unstored", "evictions", "clears", "entries"})

# == 2. the policy ======================================================================
CFG = os.path.join(tempfile.mkdtemp(), "config")
shutil.copytree(os.path.join(ROOT, "config"), CFG,
                ignore=shutil.ignore_patterns("*.local.toml", ".pollcache.epoch"))
check("the switch is OFF unless somebody turns it on",
      sysconfig.share_polls(CFG) is False and sysconfig.settings(CFG)["share_polls"] is False)
pollshare.CACHE = PollCache()
keyed, computed = [], []


def keyfn():
    keyed.append(1)
    return "", ("master", "*", None)


def comp():
    computed.append(1)
    return {"ok": True, "n": len(computed)}


r = [pollshare.lookup("dashboard", keyfn, comp, CFG) for _ in range(3)]
check("off: every request computes, exactly as before",
      [x["n"] for x in r] == [1, 2, 3] and len(computed) == 3)
check("off: not even a key is built, and no counter moves (zero lookups)",
      keyed == [] and pollshare.CACHE.counters()["misses"] == 0
      and pollshare.CACHE.counters()["hits"] == 0)

check("the switch saves and reads back", sysconfig.set_share_polls(True, CFG) is True
      and sysconfig.share_polls(CFG) is True)
computed.clear()
r = [pollshare.lookup("dashboard", keyfn, comp, CFG) for _ in range(3)]
check("on: three polls inside the TTL cost one computation",
      len(computed) == 1 and r[0] is r[2])
pollshare.note_write("/api/auth/keepalive", CFG)
check("the keepalive touch is not a write that empties it", len(pollshare.CACHE) == 1)
pollshare.note_write("/api/detector/control/camera", CFG)
check("any other authenticated write empties it at once", len(pollshare.CACHE) == 0)
check("...and leaves the box-wide stamp for the other processes",
      os.path.exists(os.path.join(CFG, pollshare.EPOCH_NAME)))

# Another process writes: only the stamp moves. This process sees it on its
# next lookup and drops what it held.
pollshare.lookup("events", lambda: ("limit=50", ("user", (), None)), comp, CFG)
held = len(pollshare.CACHE)
epoch = os.path.join(CFG, pollshare.EPOCH_NAME)
n = os.stat(epoch).st_mtime_ns + 10_000_000
os.utime(epoch, ns=(n, n))
before = len(computed)
pollshare.lookup("events", lambda: ("limit=50", ("user", (), None)), comp, CFG)
check("a write in ANOTHER process (the stamp moved) empties this one before it answers",
      held == 1 and len(computed) == before + 1)

sysconfig.set_share_polls(False, CFG)
check("switching off is seen on the next request, which also empties the cache",
      len(pollshare.CACHE) == 1 and pollshare.lookup("dashboard", keyfn, comp, CFG)
      and len(pollshare.CACHE) == 0)
sysconfig.set_share_polls(True, CFG)

# -- the query whitelist
q = pollshare.query_key
check("only the parameters the handler reads are in the key, in one order",
      q([("limit", "50"), ("x", "junk"), ("dir", "desc")], {"limit", "dir"})
      == q([("dir", "desc"), ("limit", "50"), ("utm", "1")], {"limit", "dir"})
      == "dir=desc&limit=50")
check("...repeated values keep the order the handler will see",
      q([("type", "b"), ("type", "a")], {"type"}) != q([("type", "a"), ("type", "b")], {"type"}))
check("...and a value cannot pose as a separator",
      q([("type", "a&limit=1")], {"type", "limit"}) != q([("type", "a"), ("limit", "1")],
                                                         {"type", "limit"}))
check("an endpoint that reads no parameters ignores every one",
      q([("cachebust", "123")], frozenset()) == "")
computed.clear()
v1 = pollshare.lookup("events", lambda: (q([("type", "x" * 600)], {"type"}), ("u",)), comp, CFG)
v2 = pollshare.lookup("events", lambda: (q([("type", "x" * 600)], {"type"}), ("u",)), comp, CFG)
check("a query too long to share is computed every time, never stored",
      len(computed) == 2 and v1 is not v2)

# -- the authorisation key
auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", "line1", CFG, admin=True)
auth.create_user("sam", "passw0rd!", "line1", CFG)
auth.create_user("joe", "passw0rd!", "line1", CFG)
k_boss = pollshare.authz_key("boss", "line1", CFG)
k_ada = pollshare.authz_key("ada", "line1", CFG)
k_sam = pollshare.authz_key("sam", "line1", CFG)
check("master: master tier, every capability",
      k_boss[0] == auth.TIER_MASTER and k_boss[1] == "*", k_boss)
check("site admin and operator get different keys",
      k_ada != k_sam and k_ada[0] == auth.TIER_ADMIN and k_sam[0] == auth.TIER_USER)
check("two operators with the same tier and grants share a key (no username in it)",
      pollshare.authz_key("joe", "line1", CFG) == k_sam)
auth.update_user("ada", caps={"line1": ["detection.power"]}, config_dir=CFG)
check("a grant change moves the account to a new key on the very next request",
      pollshare.authz_key("ada", "line1", CFG) != k_ada
      and pollshare.authz_key("ada", "line1", CFG)[1] == ("detection.power",))
auth.update_user("ada", caps={"line1": "*"}, config_dir=CFG)
reg = [SimpleNamespace(slug="line1"), SimpleNamespace(slug="line2")]
check("on the hub the key carries the systems this account may see",
      pollshare.authz_key("sam", "", CFG, reg, {})[2] == ("line1",)
      and pollshare.authz_key("boss", "", CFG, reg, {})[2] == ("line1", "line2"))

# == 3. perm.shared on stub requests ====================================================
pollshare.CACHE = PollCache()
runs = []


def req(user, query=()):
    items = list(query)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, system=SimpleNamespace(slug="line1"))),
        state=SimpleNamespace(user=user),
        query_params=SimpleNamespace(multi_items=lambda: items))


@perm.shared("dashboard")
def fake_dashboard(request):
    """What /api/dashboard's per-account fields are made of."""
    runs.append(request.state.user)
    return {"ok": True, "caps": perm.held(request),
            "editable": perm.editable(request, "detection.")}


answers = {}
for user in ("boss", "sam", "boss", "joe", "sam", "boss"):
    answers.setdefault(user, []).append(fake_dashboard(req(user)))
own = {u: {"ok": True, "caps": perm.held(req(u)),
           "editable": perm.editable(req(u), "detection.")} for u in answers}
check("interleaved: every account only ever gets the answer built for its own key",
      all(a == own[u] for u, rows in answers.items() for a in rows), answers)
check("...master's caps never reach an operator",
      all(a["caps"] == [] for a in answers["sam"] + answers["joe"])
      and answers["boss"][0]["caps"])
check("...computed once per KEY (boss, and sam+joe), not once per request",
      len(runs) == 2, runs)
auth.update_user("sam", admin=True, config_dir=CFG)
after = fake_dashboard(req("sam"))
check("promoted between two polls: the next poll is computed for the new grants",
      after["caps"] == perm.held(req("sam")) and after["caps"] and len(runs) == 3)
auth.update_user("sam", admin=False, config_dir=CFG)
check("...and demoted: back to an operator's answer at once",
      fake_dashboard(req("sam"))["caps"] == [])
auth.delete_user("joe", CFG)
check("a removed account's key is an ordinary user's (the wall refuses it first)",
      pollshare.authz_key("joe", "line1", CFG)[0] == auth.TIER_USER)

before = pollshare.CACHE.counters()
fake_dashboard(req(None))
fake_dashboard(req(""))
check("no account on the request: computed, never looked up or stored",
      pollshare.CACHE.counters() == before)

seen_hosts = []


def on_host(request, payload):
    seen_hosts.append(request.host)
    return dict(payload, cards=[dict(c, url=request.host) for c in payload["cards"]])


@perm.shared("hub", after=on_host)
def fake_hub(request):
    return {"ok": True, "cards": [{"slug": "line1", "url": "configured"}]}


def hub_req(user, host):
    r = req(user)
    r.app.state = SimpleNamespace(config_dir=CFG, registry=reg, masters={})
    r.host = host
    return r


h1 = fake_hub(hub_req("boss", "10.0.0.5"))
h2 = fake_hub(hub_req("boss", "evil.example"))
cached = list(pollshare.CACHE._entries.values())
check("`after` runs per request: each caller gets its own host, shared or not",
      h1["cards"][0]["url"] == "10.0.0.5" and h2["cards"][0]["url"] == "evil.example")
check("...and never edits the shared value (no request's host is in the cache)",
      all("evil" not in str(v) and "10.0.0.5" not in str(v) for _e, v in cached))

sysconfig.set_share_polls(False, CFG)
pollshare.CACHE = PollCache()
runs.clear()
off = [fake_dashboard(req("boss")) for _ in range(3)]
check("off: the decorated handler runs on every request with identical output",
      len(runs) == 3 and off[0] == off[1] == off[2]
      and pollshare.CACHE.counters()["misses"] == 0 and len(pollshare.CACHE) == 0)

# == 4. source pins =====================================================================
opted = {("builder.py", '@router.get("/api/dashboard"', 'perm.shared("dashboard")'),
         ("events.py", '@router.get("/api/events"', 'perm.shared("events", params=QUERY_PARAMS)'),
         ("capacity.py", '@router.get("/api/capacity"', 'perm.shared("capacity")'),
         ("system.py", '@router.get("/api/host/meter"', 'perm.shared("host_meter")'),
         ("models.py", '@router.get("/api/detector/control"', 'perm.shared("detector_control")'),
         ("hub.py", '@router.get("/api/hub"', 'perm.shared("hub", after=_on_host)')}
for name, route, deco in sorted(opted):
    src = read("core", "routes", name)
    tail = src.split(route + ",", 1)[1].split("\n")[1]
    check(f"{name}: {route[13:]} opts in, directly under its route",
          tail.strip() == "@" + deco, tail)
check("every opted-in endpoint has a TTL, none longer than 2 s",
      {d.split('"')[1] for _n, _r, d in opted} == set(pollshare.TTLS)
      and max(pollshare.TTLS.values()) <= 2.0)
hub_src = read("core", "routes", "hub.py")
for route in ('@router.get("/api/hub/streams"',):
    check("/api/hub/streams is NOT shared (it forwards the caller's cookie)",
          "perm.shared" not in hub_src.split(route, 1)[1].split("\n")[1])
auth_src = read("core", "routes", "auth.py")
check("/api/inbox is NOT shared (it is the caller's own mail)",
      "perm.shared" not in auth_src.split('@router.get("/api/inbox"', 1)[1].split("\n")[1])
events_src = read("core", "routes", "events.py")
check("the events whitelist is every parameter query_events reads",
      all(f'p.get("{k}"' in events_src for k in ("from", "to", "order", "dir", "limit", "offset"))
      and "for col in events_repo.FILTERABLE" in events_src)
for name in ("master_server.py", "hub_server.py"):
    src = read(name)
    check(f"{name}: the write hook is added BEFORE the session wall (so inside it)",
          src.index("pollcache_route.install(app)") < src.index('app.middleware("http")(auth_require('))
    check(f"{name}: the counters route is mounted", "app.include_router(pollcache_route.router)" in src)
route_src = read("core", "routes", "pollcache.py")
check("the counters route is master or developer only",
      "if not auth_service.is_admin(me, st.config_dir):" in route_src
      and "status_code=403" in route_src)
check("...and the write hook acts only for an authenticated request",
      'getattr(request.state, "user", None)' in route_src)
post = auth_src.split('name="system.settings_save"', 1)[1].split("@router.", 1)[0]
check("the switch saves through /api/system/settings, named only, audited",
      'if "share_polls" in body:' in post and "shared poll cache" in post)
check("config/system.toml documents it, off", "\nshare_polls = false\n" in read("config", "system.toml"))
card = read("frontend", "src", "components", "PollCacheCard.jsx")
panel = read("frontend", "src", "components", "SystemPanel.jsx")
check("the card: title, hint, switch, counters only while on",
      "Shared poll cache (testing)" in card
      and "Every page still needs its own login and permissions." in card
      and "{on && <PollCacheStats />}" in card
      and "save({ share_polls: on })" in panel)
for path in (("core", "services", "pollcache.py"), ("core", "services", "pollshare.py"),
             ("core", "routes", "pollcache.py"), ("core", "routes", "perm.py")):
    check(f"{path[-1]}: no em dash", chr(0x2014) not in read(*path))
check("pollcache.py imports no framework and no config",
      not any(w in read("core", "services", "pollcache.py")
              for w in ("fastapi", "starlette", "from core")))

print(f"\n{'all ok' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
