# Clear Log on the Logs page: every stored record goes, one trace stays
# (Tok Aba, 2026-10-07; core/routes/logs.py purge_logs).
#
# Pinned:
#   1. a non-admin is refused with a 403 and nothing is cleared
#   2. on a MASTER: the ring and the app's spool (and its rotation) are
#      emptied, and the ring then holds EXACTLY one record, the trace,
#      naming the operator and the count; a logs.clear audit entry is written
#   3. app.log is NOT cut: its lines above the clear mark leave the page, a
#      line written after it shows, and a file trimmed and refilled since
#      (same inode, different bytes) voids the mark rather than hiding text
#   4. the request's own access line is not written beside the trace
#   5. on the HUB: only the hub's ring is cleared, no master is asked and no
#      system's spool is touched (Tok Aba, 2026-10-14); one trace, one audit
#      entry naming no system; 5b. a successful /api/logs poll writes no
#      access row (QUIET_PREFIXES), a refused one still does
#   6. there is no way to skip the trace: `trace: false` is ignored
#   7. Settings' per-system "logs" delete empties app.log IN PLACE (a running
#      app holds it open) and removes the clear mark
#
#   py -3 tests/smoke_logpurge.py
import asyncio
import io
import json
import logging
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


# The dev box has no fastapi/starlette; the same stand-in smoke_boxrestart uses.
try:
    import fastapi                                              # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request = _HTTPException, object
    fr = types.ModuleType("fastapi.responses")
    fr.JSONResponse = object
    sr = types.ModuleType("starlette.requests")
    sr.ClientDisconnect = type("ClientDisconnect", (Exception,), {})
    sys.modules.update({"fastapi": fa, "fastapi.responses": fr,
                        "starlette": types.ModuleType("starlette"),
                        "starlette.requests": sr})
from fastapi import HTTPException                               # noqa: E402

from core import middleware                                     # noqa: E402
from core.routes import logs as logs_route                      # noqa: E402
from core.services import audit as audit_service                # noqa: E402
from core.services import auth as auth_service                  # noqa: E402
from core.services import log_collect                           # noqa: E402
from core.services import logs as logs_service                  # noqa: E402
from core.services import masters as masters_service            # noqa: E402
import core.services                                            # noqa: E402

auth_service.is_admin = lambda user, cfg=None: user == "master"
audits = []
audit_service.record = lambda action, **kw: audits.append((action, kw))
logs_service.install(logging.INFO)

# A system's data dir, answered by a stand-in for detector_control (heavy,
# and its data_dir rule is not what is under test).
DATA = tempfile.mkdtemp()
dc = types.ModuleType("core.services.detector_control")
dc.data_dir = lambda systems_dir, slug: DATA
dc.app_state = lambda systems_dir, slug: {}
PKG = tempfile.mkdtemp()
dc._pkg_dir = lambda systems_dir, slug: PKG
dc._merged = lambda pkg_dir: {}
dc._data_dir = lambda pkg_dir, cfg: DATA
sys.modules["core.services.detector_control"] = dc
core.services.detector_control = dc

SPOOL = os.path.join(DATA, "faults.jsonl")
CRASH = "[HailoRT] [error] CHECK failed - Lost communication with the server."
OTHER = "[HailoRT] [error] something else went wrong"
APPLOG = os.path.join(DATA, "app.log")


def seed():
    with io.open(SPOOL, "w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"ts": 1000.0 + i, "level": "ERROR",
                                "message": f"spooled {i}", "seq": i + 1,
                                "run": "r1"}) + "\n")
    io.open(SPOOL + ".1", "w", encoding="utf-8").write("{}\n")
    io.open(APPLOG, "w", encoding="utf-8").write(
        "[APP] just text\n" + CRASH + "\n")
    try:
        os.remove(os.path.join(DATA, log_collect.CLEAR_MARK_FILE))
    except OSError:
        pass
    for i in range(4):
        logging.getLogger("cytron.test").info("ring line %d", i)


def request(user, state, body=None):
    async def _json():
        return body or {}
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=state),
        state=types.SimpleNamespace(user=user, session_sid="s1",
                                    request_id="rid1"),
        headers={"cookie": "sid=x"}, json=_json,
        url=types.SimpleNamespace(path="/api/logs/purge"),
        client=types.SimpleNamespace(host="10.0.0.9"))


def call(user, state, body=None):
    try:
        return asyncio.run(logs_route.purge_logs(request(user, state, body)))
    except HTTPException as exc:
        return exc


MASTER = types.SimpleNamespace(config_dir=None, systems_dir="systems",
                               system=types.SimpleNamespace(slug="line1"))

# -- 1. non-admin -----------------------------------------------------------------
logs_service.clear()
seed()
held = len(logs_service.query(limit=1000))
r = call("operator", MASTER)
check("a non-admin is refused with 403",
      isinstance(r, HTTPException) and r.status_code == 403, repr(r))
check("...and nothing was cleared",
      len(logs_service.query(limit=1000)) >= held
      and os.path.getsize(SPOOL) > 0 and os.path.exists(SPOOL + ".1"))

# -- 2. master --------------------------------------------------------------------
logs_service.clear()
seed()
ring = len(logs_service.query(limit=1000))
before = io.open(APPLOG, encoding="utf-8").read()
audits.clear()
r = call("master", MASTER)
rows = logs_service.query(limit=1000)
# ring + 3 spooled rows + the one HailoRT incident scraped from app.log
check("the master answers ok with the count (ring + spool + app.log rows)",
      isinstance(r, dict) and r["ok"] and r["removed"] == ring + 4, repr(r))
check("the spool is emptied in place, its rotation removed",
      os.path.getsize(SPOOL) == 0 and not os.path.exists(SPOOL + ".1"))
check("exactly ONE record is left", len(rows) == 1, [x["message"] for x in rows])
check("...and it is the trace, naming the operator and the count",
      rows and rows[0]["message"] == f"Log cleared by master, {ring + 4} records removed"
      and rows[0]["user"] == "master" and rows[0]["event_id"] == "SYS-1080",
      rows and rows[0])
check("a logs.clear audit entry names the user, the system and the count",
      audits == [("logs.clear", {"username": "master", "source_ip": "10.0.0.9",
                                 "detail": {"systems": ["line1"],
                                            "removed": ring + 4,
                                            "unreached": []},
                                 "config_dir": None})], audits)


# -- 3. app.log is kept, hidden above the mark ------------------------------------
def codes():
    return sorted(x["event_code"]
                  for x in log_collect.app_records("systems", "line1"))


check("app.log is left exactly as it was",
      io.open(APPLOG, encoding="utf-8").read() == before)
check("...but its lines above the mark are off the page", codes() == [], codes())
with io.open(APPLOG, "a", encoding="utf-8") as f:
    f.write(OTHER + "\n")
check("a line written after the clear shows",
      codes() == ["AI_SERVICE_UNAVAILABLE"], codes())
# runtime.trim_log: the same file emptied in place and written again, longer
# than the mark, with different bytes where the mark points.
with io.open(APPLOG, "w", encoding="utf-8") as f:
    f.write("[APP] another run " * 20 + "\n" + CRASH + "\n")
check("a trimmed and refilled app.log voids the mark instead of hiding text",
      codes() == ["AI_SERVICE_CRASHED"], codes())

# -- 4. the access line -----------------------------------------------------------
captured = {}
fake_app = types.SimpleNamespace(
    middleware=lambda kind: (lambda fn: captured.setdefault("fn", fn)))
middleware.install_access_log(fake_app)


async def _next(req):
    req.state.quiet_access = True                 # what purge_logs sets
    return types.SimpleNamespace(status_code=200, headers={})

logs_service.clear()
asyncio.run(captured["fn"](types.SimpleNamespace(
    state=types.SimpleNamespace(), method="POST",
    url=types.SimpleNamespace(path="/api/logs/purge")), _next))
check("a quiet request writes no access line",
      not logs_service.query(limit=1000))

# -- 5. hub -----------------------------------------------------------------------
asked = []


def fake_post(url, path, body=None, cookie=None, timeout=None):
    asked.append((url, path, body, cookie))
    return {"ok": True, "removed": 5} if url.endswith("5001") else None

masters_service.post_json = fake_post
HUB = types.SimpleNamespace(config_dir=None, systems_dir="systems",
                            masters={"line1": {"url": "http://127.0.0.1:5001"},
                                     "line2": {"url": "http://127.0.0.1:5002"}})
logs_service.clear()
seed()
ring = len(logs_service.query(limit=1000))
audits.clear()
r = call("master", HUB)
rows = logs_service.query(limit=1000)
check("hub Clear Log clears the hub only: no master is asked (Tok Aba, 2026-10-14)",
      asked == [], asked)
check("...the count is the hub's own ring, and no system is named",
      r["removed"] == ring and r["systems"] == [] and r["unreached"] == [], repr(r))
check("the hub writes exactly one trace of its own",
      len(rows) == 1 and rows[0]["message"] == f"Log cleared by master, {ring} records removed",
      [x["message"] for x in rows])
check("...and an audit entry that names no system",
      len(audits) == 1 and audits[0][0] == "logs.clear"
      and audits[0][1]["detail"] == {"systems": [], "removed": ring,
                                     "unreached": []}, audits)
check("...and the system's app spool is untouched by the hub's clear",
      os.path.getsize(SPOOL) > 0 and os.path.exists(SPOOL + ".1"))

# -- 5b. the page's own polls stay out of the list ----------------------------------
check("a successful /api/logs poll is quiet (QUIET_PREFIXES)",
      "/api/logs" in middleware.QUIET_PREFIXES)
logs_service.clear()


async def _ok(req):
    return types.SimpleNamespace(status_code=200, headers={})


async def _bad(req):
    return types.SimpleNamespace(status_code=403, headers={})


for nxt in (_ok, _bad):
    asyncio.run(captured["fn"](types.SimpleNamespace(
        state=types.SimpleNamespace(), method="GET",
        url=types.SimpleNamespace(path="/api/logs")), nxt))
lines = logs_service.query(limit=1000)
check("...a successful poll writes no row, a refused one still does",
      len(lines) == 1 and "403" in (str(lines[0].get("http_status")) + lines[0]["message"]),
      [(x["message"], x.get("http_status")) for x in lines])

# -- 6. no skipping the trace -----------------------------------------------------
logs_service.clear()
seed()
r = call("master", MASTER, {"trace": False})
rows = logs_service.query(limit=1000)
check("trace: false is ignored, the trace is still written",
      r["ok"] and len(rows) == 1 and rows[0]["event_id"] == "SYS-1080",
      [x["message"] for x in rows])

# -- 7. Settings' logs delete ------------------------------------------------------
from core.services import retention                             # noqa: E402
seed()
log_collect.set_clear_mark(DATA, APPLOG)
with io.open(APPLOG, "rb") as held:            # stands in for the app's fd
    ino = os.stat(APPLOG).st_ino
    retention.purge_system("systems", "line1", "logs")
    check("the logs delete empties app.log in place, never unlinks it",
          os.path.exists(APPLOG) and os.path.getsize(APPLOG) == 0
          and os.stat(APPLOG).st_ino == ino)
check("...and removes the clear mark with it",
      not os.path.exists(os.path.join(DATA, log_collect.CLEAR_MARK_FILE)))

print("FAILED" if bad else "all passed")
sys.exit(1 if bad else 0)
