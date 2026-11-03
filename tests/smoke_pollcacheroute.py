# -*- coding: utf-8 -*-
"""The shared poll cache through a REAL FastAPI app: the real session wall
(core/routes/auth.require), the real write hook, the real handlers of
/api/dashboard, /api/events, /api/capacity, /api/host/meter,
/api/detector/control and the hub's /api/hub, with only their data sources
replaced by counting fakes. Skipped (77) where FastAPI is not installed --
the dev laptop; smoke_pollcache.py covers the same code with stubs there.

  1. off: byte-identical bodies to on, and zero lookups
  2. on: 12 concurrent identical requests -> one computation
  3. role isolation over HTTP: master / site admin / operator interleaved,
     the camera password only ever reaches the accounts allowed it; the hub's
     cards per visible-system set; each request's own Host on its links
  4. invalidation: the account's own write (event status, tracker, the
     switch itself, any authenticated POST) is never followed by an answer
     computed before it
  5. never touches the cache: no session, a removed or disabled account,
     a cross-site write; the counters route is master/developer only

    python tests/smoke_pollcacheroute.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

try:
    import fastapi                                             # noqa: F401
except ImportError:
    print("skip: FastAPI is not installed here (smoke_pollcache.py runs the stub half)")
    sys.exit(77)

from fastapi import FastAPI                                    # noqa: E402

from core.routes import auth as auth_routes                    # noqa: E402
from core.routes import builder as builder_routes              # noqa: E402
from core.routes import capacity as capacity_routes            # noqa: E402
from core.routes import events as events_routes                # noqa: E402
from core.routes import hub as hub_routes                      # noqa: E402
from core.routes import models as models_routes                # noqa: E402
from core.routes import pollcache as pollcache_route           # noqa: E402
from core.routes import system as system_routes                # noqa: E402
from core.services import auth                                 # noqa: E402
from core.services import pollshare                            # noqa: E402
from core.services import sysconfig                            # noqa: E402
from core.services.pollcache import PollCache                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = os.path.join(tempfile.mkdtemp(), "config")
shutil.copytree(os.path.join(ROOT, "config"), CFG,
                ignore=shutil.ignore_patterns("*.local.toml", ".pollcache.epoch"))
SYSDIR = tempfile.mkdtemp()
auth.set_admin_username("boss", CFG)
auth.set_master_password("Not-the-shipped-1", CFG)
auth.create_user("ada", "passw0rd!", "line1", CFG, admin=True)   # site admin
auth.create_user("sam", "passw0rd!", "line1", CFG)               # operator, line1
auth.create_user("joe", "passw0rd!", "line1,line2", CFG)         # operator, both
auth.create_user("kim", "passw0rd!", "line1", CFG)               # removed later
SECRET = "rtsp://cam:P@ssw0rd@10.0.0.7/stream"


def token(user):
    sid = auth._new_sid()
    auth.register_session(user, sid, config_dir=CFG)
    return auth.issue_session(user, CFG, sid=sid)


TOK = {u: token(u) for u in ("boss", "ada", "sam", "joe", "kim")}


# -- the counting fakes behind the real handlers -------------------------------------
class World:
    version = 1
    delay = 0.0
    n = {"shaped": 0, "events": 0, "status": 0, "meter": 0, "capacity": 0, "probe": 0}


def fake_shaped(request):
    World.n["shaped"] += 1
    time.sleep(World.delay)
    return ({"running": True, "fps": 18.0}, {"verdict": "ok", "zones": []},
            {"objects_in_frame": World.version})


def fake_status(systems_dir, slug, reveal=False):
    World.n["status"] += 1
    return {"running": True, "version": World.version,
            "camera": {"id": "c1", "source": SECRET},
            "cameras": [{"id": "c1", "source": SECRET}]}


def fake_query(slug, **kw):
    World.n["events"] += 1
    return {"rows": [{"id": "e1", "status": f"v{World.version}"}], "total": 1,
            "limit": kw.get("limit")}


def fake_probe(masters, timeout=1.5):
    World.n["probe"] += 1
    return {slug: {"ai_running": True} for slug in masters}


def fake_meter():
    World.n["meter"] += 1
    return {"cpu": 12.5, "temp": 50.0}


def fake_count(*a, **k):
    World.n["capacity"] += 1
    return 2


def bump(*a, **k):
    World.version += 1
    return True


patches = [
    mock.patch.object(builder_routes, "_shaped", fake_shaped),
    mock.patch.object(builder_routes, "_setup_state", lambda *a: {"done": True}),
    mock.patch.object(builder_routes, "_caps", lambda st: set()),
    mock.patch.object(builder_routes.builder_service, "ensure_main_page", lambda *a, **k: {}),
    mock.patch.object(builder_routes.builder_service, "main_page", lambda layout: {
        "widgets": [{"id": "b1", "kind": "button", "data": {}}]}),
    mock.patch.object(builder_routes.builder_service, "compute", lambda widgets, *a, **k: [
        dict(w, data=dict(w.get("data") or {})) for w in widgets]),
    mock.patch.object(models_routes, "_system_context", lambda request: (SYSDIR, "line1")),
    mock.patch.object(models_routes.detector_control_service, "status", fake_status),
    mock.patch.object(models_routes.detector_control_service, "set_tracker_params", bump),
    mock.patch.object(events_routes.events_repo, "query", fake_query),
    mock.patch.object(events_routes.events_repo, "set_status", bump),
    mock.patch("core.services.host_health.host_meter", fake_meter),
    mock.patch("core.services.detector_control.fps_snapshot",
               lambda *a: {"running": True, "fps": 18.0}),
    mock.patch("core.services.provision.count_systems", fake_count),
    mock.patch("core.services.detector_control.running_apps", lambda *a: ["x"]),
    mock.patch("core.services.detector_control._sources_elsewhere_cached", lambda *a: ["c"]),
    mock.patch.object(hub_routes.masters_service, "probe_all", fake_probe),
    mock.patch("core.services.retention.resetting_slugs", lambda: []),
    mock.patch("core.services.storage_watch.current", lambda cfg: None),
]
for p in patches:
    p.start()


def make_master():
    app = FastAPI()
    st = app.state
    st.config_dir, st.systems_dir = CFG, SYSDIR
    st.system = SimpleNamespace(slug="line1", display_name="Line 1")
    st.registry, st.slaves, st.events = [st.system], [], []
    for r in (auth_routes.router, pollcache_route.router, builder_routes.router,
              events_routes.router, capacity_routes.router, system_routes.router,
              models_routes.router):
        app.include_router(r)

    @app.post("/api/test/anything")
    def anything():
        return {"ok": True}
    pollcache_route.install(app)
    app.middleware("http")(auth_routes.require("line1"))
    return app


def make_hub():
    app = FastAPI()
    st = app.state
    st.config_dir, st.systems_dir = CFG, SYSDIR
    st.registry = [SimpleNamespace(slug=s, display_name=s.upper(), subtitle="",
                                   icon="", cover="") for s in ("line1", "line2")]
    st.masters = {"line1": {"url": "http://127.0.0.1:5111"},
                  "line2": {"url": "http://127.0.0.1:5112"}}
    for r in (pollcache_route.router, hub_routes.router, capacity_routes.router):
        app.include_router(r)
    pollcache_route.install(app)
    app.middleware("http")(auth_routes.require(None))
    return app


MASTER, HUB = make_master(), make_hub()


async def call(app, method, path, user=None, host="box.local:5111", body=None,
               headers=()):
    raw_path, _, qs = path.partition("?")
    hdrs = [(b"host", host.encode())]
    if user:
        hdrs.append((b"x-session", TOK[user].encode()))
    data = b""
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.append((b"content-type", b"application/json"))
    hdrs += [(k.lower().encode(), v.encode()) for k, v in headers]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": raw_path,
             "raw_path": raw_path.encode(), "query_string": qs.encode(),
             "root_path": "", "headers": hdrs, "client": ("127.0.0.1", 50000),
             "server": (host.split(":")[0], 5111), "state": {}}
    sent = []
    done = asyncio.Event()

    async def receive():
        if not sent:
            sent.append(1)
            return {"type": "http.request", "body": data, "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}
    msgs = []

    async def send(m):
        msgs.append(m)
    await app(scope, receive, send)
    done.set()
    start = next(m for m in msgs if m["type"] == "http.response.start")
    blob = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, blob


def get(app, path, user=None, **kw):
    return asyncio.run(call(app, "GET", path, user, **kw))


def post(app, path, user=None, body=None, **kw):
    return asyncio.run(call(app, "POST", path, user, body=body or {}, **kw))


def fresh():
    pollshare.CACHE = PollCache()
    for k in World.n:
        World.n[k] = 0


READS = ("/api/dashboard", "/api/events?limit=50&offset=0&dir=desc", "/api/capacity",
         "/api/host/meter", "/api/detector/control")

# == 1. off ============================================================================
sysconfig.set_share_polls(False, CFG)
fresh()
off = {p: get(MASTER, p, "boss") for p in READS}
check("off: every read answers 200", all(r[0] == 200 for r in off.values()),
      {p: r[0] for p, r in off.items()})
check("off: zero cache lookups (no counter moved, nothing stored)",
      pollshare.CACHE.counters()["misses"] == 0 and len(pollshare.CACHE) == 0)
check("off: Cache-Control is still no-store",
      all(r[1].get("cache-control") == "no-store" for r in off.values()))
sysconfig.set_share_polls(True, CFG)
fresh()
on = {p: get(MASTER, p, "boss") for p in READS}
check("on: every body is byte-identical to off (first request, a miss)",
      all(on[p][2] == off[p][2] for p in READS),
      [p for p in READS if on[p][2] != off[p][2]])
again = {p: get(MASTER, p, "boss") for p in READS}
check("on: ...and the shared answer (a hit) is too",
      all(again[p][2] == off[p][2] for p in READS)
      and pollshare.CACHE.counters()["hits"] == len(READS), pollshare.CACHE.counters())
check("on: same status, same no-store, no new header",
      all(again[p][0] == 200 and again[p][1].get("cache-control") == "no-store"
          and not any(h.startswith("x-poll") for h in again[p][1]) for p in READS))

# == 2. concurrency ====================================================================
fresh()
World.delay = 0.3


async def burst():
    return await asyncio.gather(*[call(MASTER, "GET", "/api/dashboard", "boss")
                                  for _ in range(12)])
res = asyncio.run(burst())
World.delay = 0.0
check("12 tabs asking /api/dashboard at once: ONE computation",
      World.n["shaped"] == 1 and all(r[0] == 200 for r in res), World.n)
check("...and all 12 got the same bytes", len({r[2] for r in res}) == 1)

# == 3. isolation ======================================================================
fresh()
seen = {}
for user in ("boss", "sam", "ada", "sam", "boss", "ada", "sam"):
    status, _h, blob = get(MASTER, "/api/detector/control", user)
    seen.setdefault(user, []).append(json.loads(blob))
check("the camera password reaches master (who may change the camera)",
      all(SECRET in json.dumps(b) for b in seen["boss"]))
check("...and the site admin holding detection.camera.change",
      all(SECRET in json.dumps(b) for b in seen["ada"]))
check("...and NEVER the operator, interleaved with them",
      all("P@ssw0rd" not in json.dumps(b) for b in seen["sam"]))
check("each account's caps are its own on every answer",
      all(b["caps"] == [] for b in seen["sam"]) and all(b["caps"] for b in seen["boss"]))
check("three keys, three computations for seven requests", World.n["status"] == 3, World.n)
dash = {u: json.loads(get(MASTER, "/api/dashboard", u)[2]) for u in ("boss", "sam")}
check("dashboard: the Inject button is pressable for master, not for the operator",
      dash["boss"]["widgets"][0]["data"]["can_press"] is True
      and dash["sam"]["widgets"][0]["data"]["can_press"] is False)

auth.update_user("sam", admin=True, caps={"line1": ["detection.camera.change"]},
                 config_dir=CFG)
promoted = json.loads(get(MASTER, "/api/detector/control", "sam")[2])
check("sam promoted between two polls: the very next poll is built for the new grant",
      SECRET in json.dumps(promoted) and promoted["caps"] == ["detection.camera.change"])
auth.update_user("sam", admin=False, config_dir=CFG)
demoted = json.loads(get(MASTER, "/api/detector/control", "sam")[2])
check("...and demoted: the password is gone from the next poll at once",
      "P@ssw0rd" not in json.dumps(demoted) and demoted["caps"] == [])

fresh()
cards = {}
for user, host in (("sam", "box.local:5100"), ("joe", "box.local:5100"),
                   ("sam", "10.9.9.9:5100"), ("joe", "10.9.9.9:5100")):
    cards[(user, host)] = json.loads(get(HUB, "/api/hub", user, host=host)[2])["cards"]
check("hub: sam sees line1 only, joe both, interleaved",
      [c["slug"] for c in cards[("sam", "box.local:5100")]] == ["line1"]
      and [c["slug"] for c in cards[("joe", "10.9.9.9:5100")]] == ["line1", "line2"])
check("hub: one probe per visible-system set, not per request", World.n["probe"] == 2, World.n)
check("hub: every card link is on the asking request's own Host",
      cards[("sam", "box.local:5100")][0]["url"].startswith("http://box.local:")
      and cards[("sam", "10.9.9.9:5100")][0]["url"].startswith("http://10.9.9.9:"))
check("hub: no request's Host is ever stored in the shared value",
      all("10.9.9.9" not in json.dumps(v) and "box.local" not in json.dumps(v)
          for _e, v in pollshare.CACHE._entries.values()))

# == 4. invalidation ===================================================================
fresh()
first = json.loads(get(MASTER, "/api/events?limit=50", "boss")[2])
s, _h, _b = asyncio.run(call(MASTER, "PATCH", "/api/events/e1", "boss",
                             body={"status": "ack"}))
after = json.loads(get(MASTER, "/api/events?limit=50", "boss")[2])
check("event status changed: the next event list is computed after the write",
      s == 200 and after["rows"][0]["status"] != first["rows"][0]["status"], (s, after))
first = json.loads(get(MASTER, "/api/detector/control", "boss")[2])
s, _h, _b = post(MASTER, "/api/detector/control/tracker", "boss", body={"track_buffer": 30})
after = json.loads(get(MASTER, "/api/detector/control", "boss")[2])
check("tracker saved: the Detection page's next poll shows it",
      s == 200 and after["version"] == first["version"] + 1, (s, first["version"], after.get("version")))
dash1 = get(MASTER, "/api/dashboard", "boss")[2]
World.version += 1                       # what the write below "changed"
post(MASTER, "/api/test/anything", "sam")
check("ANY authenticated write (here an operator's) empties the cache",
      get(MASTER, "/api/dashboard", "boss")[2] != dash1)
# (A write in ANOTHER process -- the epoch stamp -- is pinned in
# smoke_pollcache.py; both apps here share one process and one cache.)
clears = pollshare.CACHE.counters()["clears"]
post(MASTER, "/api/auth/keepalive", "boss")
check("the keepalive touch does not empty it",
      pollshare.CACHE.counters()["clears"] == clears)
s, _h, _b = post(MASTER, "/api/system/settings", "boss", body={"share_polls": False})
check("the switch flips through /api/system/settings",
      s == 200 and sysconfig.share_polls(CFG) is False)
n = World.n["events"]
get(MASTER, "/api/events?limit=50", "boss")
get(MASTER, "/api/events?limit=50", "boss")
check("...and off takes effect on the next request: computed every time, cache empty",
      World.n["events"] == n + 2 and len(pollshare.CACHE) == 0)
post(MASTER, "/api/system/settings", "boss", body={"share_polls": True})

# == 5. what never reaches the cache ===================================================
fresh()
get(MASTER, "/api/dashboard", "boss")
base = pollshare.CACHE.counters()
s, _h, _b = get(MASTER, "/api/dashboard")
check("no session: 401, and the cache was not even looked at",
      s == 401 and pollshare.CACHE.counters() == base)
s, _h, _b = post(MASTER, "/api/test/anything", "boss",
                 headers=(("Origin", "http://evil.example"),))
check("a cross-site write: 403 at the wall, and it does not empty the cache",
      s == 403 and pollshare.CACHE.counters() == base and len(pollshare.CACHE) == 1)
s, _h, _b = post(MASTER, "/api/test/anything")
check("an anonymous write: 401, cache untouched", s == 401 and len(pollshare.CACHE) == 1)
auth.delete_user("kim", CFG)
s, _h, _b = get(MASTER, "/api/dashboard", "kim")
check("a removed account: 401 before the cache", s == 401
      and pollshare.CACHE.counters() == base)
auth.update_user("joe", enabled=False, config_dir=CFG)
s, _h, _b = get(HUB, "/api/hub", "joe", host="box.local:5100")
check("a disabled account: 401 before the cache", s == 401)
s, _h, _b = get(MASTER, "/api/system/poll-cache", "sam")
check("the counters route refuses an operator (403)", s == 403)
s, _h, _b = get(MASTER, "/api/system/poll-cache", "ada")
check("...and a site admin (master or developer only)", s == 403)
s, _h, blob = get(MASTER, "/api/system/poll-cache", "boss")
body = json.loads(blob)
check("master reads counts only: no key, no value, no account name",
      s == 200 and body["on"] is True and "boss" not in blob.decode()
      and set(body) == {"ok", "on", *pollshare.COUNTERS}, body)
with mock.patch.object(hub_routes.masters_service, "fetch_json",
                       lambda url, path, cookie=None, timeout=1.5: {
                           "ok": True, "hits": 5, "misses": 2, "entries": 1}):
    s, _h, blob = get(HUB, "/api/system/poll-cache", "boss", host="box.local:5100")
body = json.loads(blob)
check("on the hub the masters' counters are summed in",
      s == 200 and body["processes"] == 3 and body["hits"] >= 10, body)

for p in patches:
    p.stop()
print(f"\n{'all ok' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
