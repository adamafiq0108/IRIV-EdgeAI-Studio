# Clear events on the Events Log: every stored event of THIS system goes, one
# audit row stays (owner request, 2026-10-11; core/routes/events.py
# clear_events, core/services/events.py clear).
#
# Pinned:
#   1. only an admin may clear: an operator, a viewer and no user are refused
#      with a 403 and nothing is deleted; the query answer's can_clear says
#      the same thing the route enforces
#   2. the auth middleware refuses the POST cross-site (403) and without a
#      session (401) before the route is reached
#   3. an admin clears THIS system's rows only -- another system's rows are
#      untouched -- and gets {ok, removed} with the real count
#   4. the live event log, its rotation and archives go with the rows (or the
#      master re-reads them at its next boot); snapshots, clips and the
#      dataset tables do not
#   5. the master's in-memory ring is emptied in place
#   6. an events.clear audit entry names the user, the system and the count
#   7. Postgres down is a 503 (DB_UNAVAILABLE), never "0 cleared", and then
#      NOTHING is touched: no rows, no files, no ring, no audit row
#   8. the filter-values cache of the cleared system is dropped (so the
#      dropdowns stop offering cameras that no longer have events); another
#      system's is kept, and a delete that never ran drops nothing
#   9. the existing best-effort callers (retention, provision) still get 0
#      when the database is down, not an exception
#
#   py -3 tests/smoke_eventsclear.py
import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# The dev box has no fastapi/starlette; the same stand-in smoke_logpurge uses,
# plus PATCH (update_event) and a JSONResponse the middleware can build.
try:
    import fastapi                                              # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")
    _route = lambda *a, **k: (lambda f: f)                     # noqa: E731
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=_route, post=_route, patch=_route, put=_route, delete=_route)

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request, fa.Query = _HTTPException, object, \
        (lambda *a, **k: None)
    fa.__path__ = []

    class _JSONResponse:
        def __init__(self, content=None, status_code=200, headers=None):
            self.content, self.status_code = content, status_code
            self.headers = dict(headers or {})
    fr = types.ModuleType("fastapi.responses")
    fr.JSONResponse = _JSONResponse
    fr.RedirectResponse = fr.Response = fr.StreamingResponse = object
    tpl = types.ModuleType("fastapi.templating")
    tpl.Jinja2Templates = lambda *a, **k: types.SimpleNamespace(
        env=types.SimpleNamespace(filters={}, globals={}))
    stat = types.ModuleType("fastapi.staticfiles")
    stat.StaticFiles = object
    sr = types.ModuleType("starlette.requests")
    sr.ClientDisconnect = type("ClientDisconnect", (Exception,), {})
    sys.modules.update({"fastapi": fa, "fastapi.responses": fr,
                        "fastapi.templating": tpl, "fastapi.staticfiles": stat,
                        "starlette": types.ModuleType("starlette"),
                        "starlette.requests": sr})
from fastapi import HTTPException                               # noqa: E402

# A system's data dir, answered by a stand-in for detector_control (heavy,
# and its data_dir rule is not what is under test).
PKG = tempfile.mkdtemp()
DATA = os.path.join(PKG, "data")
os.makedirs(DATA)
dc = types.ModuleType("core.services.detector_control")
dc._pkg_dir = lambda systems_dir, slug: PKG
dc._merged = lambda pkg_dir: {}
dc._data_dir = lambda pkg_dir, cfg: DATA
dc.data_dir = lambda systems_dir, slug: DATA
dc.app_state = lambda systems_dir, slug: {}
import core.services                                            # noqa: E402
sys.modules["core.services.detector_control"] = dc
core.services.detector_control = dc

from core.db import DbUnavailable                               # noqa: E402
from core.repos import events as events_repo                    # noqa: E402
from core.routes import auth as auth_routes                     # noqa: E402
from core.routes import events as events_route                  # noqa: E402
from core.services import audit as audit_service                # noqa: E402
from core.services import auth as auth_service                  # noqa: E402

auth_service.is_admin = lambda user, cfg=None: user in ("master", "developer")
audits = []
audit_service.record = lambda action, **kw: audits.append((action, kw))

# -- an in-memory event store behind the REAL repo function -------------------
TABLE = []          # rows: {"system_slug", "event_id"}
DOWN = {"on": False}


class _Cur:
    rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        assert sql.startswith("DELETE FROM events WHERE system_slug = %s"), sql
        keep = [r for r in TABLE if r["system_slug"] != params[0]]
        self.rowcount = len(TABLE) - len(keep)
        TABLE[:] = keep


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def close(self):
        pass


def _connect(config_dir=None, timeout_ms=None):
    if DOWN["on"]:
        raise DbUnavailable("connection refused")
    return _Conn()


events_repo.connect = _connect


def seed():
    TABLE[:] = ([{"system_slug": "line1", "event_id": f"a{i}"} for i in range(7)]
                + [{"system_slug": "line2", "event_id": f"b{i}"} for i in range(3)])
    for name in ("events.jsonl", "events.jsonl.1", "events.gen"):
        with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
            f.write('{"id": "x"}\n' if name != "events.gen" else "4")
    os.makedirs(os.path.join(PKG, "archive"), exist_ok=True)
    with open(os.path.join(PKG, "archive", "events-2026-10-01.jsonl.gz"), "wb") as f:
        f.write(b"gz")
    for sub, name in (("snapshots", "s1.jpg"), ("clips", "c1.mp4")):
        os.makedirs(os.path.join(DATA, sub), exist_ok=True)
        with open(os.path.join(DATA, sub, name), "wb") as f:
            f.write(b"x")
    events_repo._attr_cache.clear()
    events_repo._attr_cache["line1"] = (9e18, {"camera": ["cam-old"]})
    events_repo._attr_cache["line2"] = (9e18, {"camera": ["cam-b"]})


def state():
    return types.SimpleNamespace(
        config_dir=None, systems_dir="systems",
        system=types.SimpleNamespace(slug="line1"),
        events=[{"id": "r1"}, {"id": "r2"}])


def request(user, st):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=st),
        state=types.SimpleNamespace(user=user, session_sid="s1"),
        headers={}, url=types.SimpleNamespace(path="/api/events/clear"),
        client=types.SimpleNamespace(host="10.0.0.9"))


def call(user, st):
    try:
        return asyncio.run(events_route.clear_events(request(user, st)))
    except HTTPException as exc:
        return exc


def line(slug):
    return [r for r in TABLE if r["system_slug"] == slug]


def exists(*parts):
    return os.path.exists(os.path.join(*parts))


# -- 1. who may clear ---------------------------------------------------------
for who in ("operator", "viewer", None):
    seed()
    audits.clear()
    st = state()
    r = call(who, st)
    check(f"{who or 'no user'} is refused with 403",
          isinstance(r, HTTPException) and r.status_code == 403, repr(r))
    check("...and nothing was deleted or audited",
          len(line("line1")) == 7 and len(st.events) == 2
          and exists(DATA, "events.jsonl") and not audits)

events_repo.query = lambda *a, **k: {"rows": [], "total": 0, "capped": False}


def can_clear(user):
    req = request(user, state())
    req.query_params = types.SimpleNamespace(getlist=lambda c: [],
                                             get=lambda k, d=None: d)
    return events_route.query_events(req).get("can_clear")


check("the query answer offers Clear events to an admin only",
      can_clear("master") is True and can_clear("operator") is False
      and can_clear(None) is False)

# -- 2. the middleware in front of it -----------------------------------------
mw = auth_routes.require()


class _MwReq:
    def __init__(self, headers, cookies=None):
        self.method, self.headers, self.cookies = "POST", headers, cookies or {}
        self.url = types.SimpleNamespace(path="/api/events/clear",
                                         hostname="192.168.1.63", scheme="http")
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(config_dir=None))
        self.state = types.SimpleNamespace()


async def _never(req):
    raise AssertionError("the route was reached")

r = asyncio.run(mw(_MwReq({"Origin": "http://evil.example"}), _never))
check("a cross-site POST is refused with 403 before the route",
      r.status_code == 403, getattr(r, "status_code", r))
check("/api/events/clear is not an open path", not auth_routes.is_open("/api/events/clear"))
r = asyncio.run(mw(_MwReq({"Origin": "http://192.168.1.63:5101"}), _never))
check("a same-site POST with no session is a 401",
      r.status_code == 401, getattr(r, "status_code", r))

# -- 3..6. an admin clears -----------------------------------------------------
seed()
audits.clear()
st = state()
ring = st.events
r = call("master", st)
check("the admin gets {ok, removed} with this system's count",
      r == {"ok": True, "removed": 7}, repr(r))
check("this system's rows are gone", line("line1") == [], line("line1"))
check("another system's rows are untouched", len(line("line2")) == 3, line("line2"))
check("the live event log, its rotation and archives are gone",
      not exists(DATA, "events.jsonl") and not exists(DATA, "events.jsonl.1")
      and not exists(PKG, "archive", "events-2026-10-01.jsonl.gz"))
check("...but the generation counter stays (cursors stay monotonic)",
      exists(DATA, "events.gen"))
check("snapshots and clips are kept",
      exists(DATA, "snapshots", "s1.jpg") and exists(DATA, "clips", "c1.mp4"))
check("the in-memory ring is emptied in place", ring == [] and st.events is ring)
check("an events.clear audit entry names the user, the system and the count",
      audits == [("events.clear", {"username": "master", "source_ip": "10.0.0.9",
                                   "detail": {"system": "line1", "removed": 7},
                                   "config_dir": None})], audits)
check("the cleared system's filter values are dropped",
      "line1" not in events_repo._attr_cache)
check("another system's are kept", "line2" in events_repo._attr_cache)

seed()
TABLE[:] = line("line2")
r = call("developer", state())
check("nothing to delete is an honest 0, not an error",
      r == {"ok": True, "removed": 0}, repr(r))

# -- 7. database down ----------------------------------------------------------
seed()
audits.clear()
DOWN["on"] = True
st = state()
r = call("master", st)
check("Postgres down is a 503, not '0 cleared'",
      isinstance(r, HTTPException) and r.status_code == 503
      and getattr(r, "event_code", "") == "DB_UNAVAILABLE", repr(r))
check("...and nothing was touched: rows, files, ring, filter cache, audit",
      len(TABLE) == 10 and exists(DATA, "events.jsonl")
      and exists(PKG, "archive", "events-2026-10-01.jsonl.gz")
      and len(st.events) == 2 and "line1" in events_repo._attr_cache
      and not audits)

# -- 9. the best-effort callers ------------------------------------------------
check("delete_for_system without strict still answers 0 when down",
      events_repo.delete_for_system("line1") == 0)
DOWN["on"] = False
check("...and the row count when up", events_repo.delete_for_system("line1") == 7)

check("events.clear is a named audit action", "events.clear" in audit_service.ACTIONS)

print("\nOK" if not bad else f"\n{bad} FAILED")
sys.exit(1 if bad else 0)
