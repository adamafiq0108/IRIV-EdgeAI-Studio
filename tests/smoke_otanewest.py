# A box must always update to the NEWEST published release of its channel.
#
# Tok Aba, 2026-10-05: "after I update v1.0.15, user that currently v1.0.12
# suddenly update into v1.0.14". v1.0.13, v1.0.14 and v1.0.15 were published
# within a day. A box on v1.0.12 checks on a six-hour timer; the timer ran
# between v1.0.14 and v1.0.15, the cache said v1.0.14, the Update card showed
# the cache, and apply() installed what the cache said without asking GitHub
# again -- the version shown and the version installed agreed, and both were
# stale. (_pick already ordered by parsed version in v1.0.12, so list order
# was never the cause.) Now apply() asks GitHub first, forced, and installs
# only when the version pressed is still the newest with all its files up.
#
# Pinned:
#   1. releases listed out of order, and dated the wrong way round, still
#      pick the highest version
#   2. a newer release whose files are still uploading stays the one offered
#      (never the one below it), is marked "publishing", and apply() waits
#      for its files rather than installing anything else
#   3. the bug itself: a cache from before the newest release, Update pressed
#      for the cached version -> refused as superseded, the cache now names
#      the newest, and Update for THAT one goes ahead
#   4. the ETag path: the timer's check keeps the cached answer on 304, a
#      changed list comes through, and apply()'s own check never sends an
#      ETag at all
#   5. never a downgrade, never a reinstall, never a withdrawn release
#   6. a check that gets no answer refuses the install in plain words, and
#      that refusal is not a miss in the timer's "could not check" streak
#   7. rollback is untouched: no version rule, no GitHub call
#   8. the route answers "superseded" as its own catalogued event, a refusal
#      reaches the page in the service's words (`details`), and the state a
#      route may return carries the publishing flag and no secrets
#   9. the card reads the flags (source pins)
import asyncio
import io
import json
import os
import re
import sys
import tempfile
import types
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import ghnet                                # noqa: E402
from core.services import log_catalog as catalog               # noqa: E402
from core.services import update as upd                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
OWNER, REPO = "adamafiq0108", "irivai-release"
PREFIX = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/"
STABLE = upd._p(upd.CHECK_FILE["stable"])
JOB = upd._p("job.json")

# The box: v1.0.12, laid out for OTA, a token, no job running, and nothing
# that would call sudo or try to heal a route.
upd.VERSION = "1.0.12"
upd.ota_ready = lambda: True
upd.job_running = lambda: False
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
upd._start_unit = lambda: None
upd._heal = lambda config_dir=None: False
upd._stage_applier = lambda: ""


def _asset(tag, name):
    return {"name": name, "url": f"{PREFIX}{tag}-{name}", "size": 1}


def _rel(tag, assets=None, **kw):
    """A release row as GitHub lists it; all three files unless told."""
    names = (upd.BUNDLE_ASSET, upd.MANIFEST_ASSET, upd.SIG_ASSET) if assets is None else assets
    row = {"tag_name": tag, "draft": False, "prerelease": False, "body": f"Area:\n- {tag}",
           "assets": [_asset(tag, n) for n in names]}
    row.update(kw)
    return row


# One fake GitHub: `listing` is what /releases returns, `etag` the ETag of
# that listing, `fail` an exception to raise instead. Every request is
# recorded with the headers it carried.
github = {"listing": [], "etag": 'W/"a"', "fail": None, "calls": []}


class _Resp:
    def __init__(self, body, etag):
        self._b = json.dumps(body).encode()
        self.headers = {"ETag": etag}

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "x", {}, io.BytesIO(b""))


def _fake_open(url, token=None, accept=None, agent="", timeout=0, headers=None, method=None):
    headers = dict(headers or {})
    github["calls"].append((url, headers))
    if github["fail"] is not None:
        raise github["fail"]
    if headers.get("If-None-Match") == github["etag"]:
        raise _http(304)
    return _Resp(github["listing"], github["etag"])


ghnet.open_url = _fake_open


def _apply(version):
    """apply() reduced to its outcome: 'ok' or the refusal's sentence."""
    try:
        upd.apply(version, CFG, "stable")
        return "ok"
    except upd.UpdateError as exc:
        return str(exc)


def _latest():
    return upd._read_json(STABLE).get("latest")


def _plain(text):
    return not re.search(r"\b[1-5]\d\d\b|HTTP|urlopen|Errno", text or "")


# -- 1. out of order, dated backwards --------------------------------------------
github["listing"] = [_rel("v1.0.13", created_at="2026-10-05T10:00:00Z"),
                     _rel("v1.0.15", created_at="2026-10-04T10:00:00Z"),
                     _rel("v1.0.14", created_at="2026-10-05T12:00:00Z")]
st = upd.check(force=True, config_dir=CFG, channel="stable")
check("1 the highest version is picked, not the first listed nor the newest dated",
      st["latest"] == "v1.0.15" and st["update_available"] is True, st)
check("1 ...and the pick is the same with the list reversed",
      upd._pick(list(reversed(github["listing"])), "stable")["tag_name"] == "v1.0.15")
check("1 a complete release is not marked publishing", st["publishing"] is False, st)

# -- 2. the newest release is listed but its files are still uploading ---------------
github["listing"] = [_rel("v1.0.14"), _rel("v1.0.15", assets=[upd.MANIFEST_ASSET])]
github["etag"] = 'W/"b"'
st = upd.check(force=True, config_dir=CFG, channel="stable")
check("2 the newest release stays the one offered while its files go up, never the one below",
      st["latest"] == "v1.0.15" and st["update_available"] is True, st)
check("2 ...and is marked publishing", st["publishing"] is True, st)
check("2 ...with no error (it is news, not a fault)", st["error"] == "", st)
r = _apply("1.0.15")
check("2 apply() waits for the files rather than installing", "still being published" in r, r)
check("2 ...in plain words", _plain(r), r)
check("2 the release below it cannot be installed in the meantime",
      "no longer the latest" in _apply("1.0.14"), _apply("1.0.14"))
# The files land. Nothing on the box changes; pressing Update asks again.
github["listing"] = [_rel("v1.0.14"), _rel("v1.0.15")]
github["etag"] = 'W/"c"'
check("2 once the files are up, the same press installs it", _apply("1.0.15") == "ok")
job = upd._read_json(JOB)
check("2 ...and the job names that version with all three files",
      job.get("version") == "1.0.15" and sorted(job.get("assets") or {}) ==
      sorted([upd.BUNDLE_ASSET, upd.MANIFEST_ASSET, upd.SIG_ASSET]), job)

# -- 3. the bug: a stale cache, then a newer release ----------------------------------
# What the v1.0.12 box's timer wrote between v1.0.14 and v1.0.15.
github["listing"] = [_rel("v1.0.13"), _rel("v1.0.14")]
github["etag"] = 'W/"d"'
upd.check(force=True, config_dir=CFG, channel="stable")
check("3 the cache says v1.0.14, as the card did", _latest() == "v1.0.14")
# v1.0.15 is published; no check has run since. Update is pressed for 1.0.14.
github["listing"] = [_rel("v1.0.13"), _rel("v1.0.14"), _rel("v1.0.15")]
github["etag"] = 'W/"e"'
github["calls"].clear()
try:
    upd.apply("1.0.14", CFG, "stable")
    outcome = "it installed v1.0.14"
except upd.UpdateSuperseded as exc:
    outcome = exc
except upd.UpdateError as exc:
    outcome = f"wrong refusal: {exc}"
check("3 pressing Update for the cached v1.0.14 does NOT install it",
      isinstance(outcome, upd.UpdateSuperseded), outcome)
check("3 ...the refusal names the newest version and the one pressed",
      getattr(outcome, "newest", "") == "1.0.15" and getattr(outcome, "asked", "") == "1.0.14", outcome)
check("3 ...in plain words that say what to do",
      _plain(str(outcome)) and "press Update again" in str(outcome), outcome)
check("3 ...GitHub was asked exactly once, before anything was written",
      len(github["calls"]) == 1, github["calls"])
check("3 the cache now names v1.0.15, so the card offers it",
      _latest() == "v1.0.15" and upd.public_state(None, "stable", CFG)["update_available"] is True)
check("3 the job file still names the last job, not v1.0.14",
      upd._read_json(JOB).get("version") == "1.0.15")
check("3 pressing Update for v1.0.15 installs it", _apply("1.0.15") == "ok")
job = upd._read_json(JOB)
check("3 ...with v1.0.15's own files",
      job.get("version") == "1.0.15" and all(
          "v1.0.15-" in (a.get("url") or "") for a in job.get("assets", {}).values()), job)

# -- 4. the ETag path --------------------------------------------------------------------
github["calls"].clear()
st = upd.check(force=False, config_dir=CFG, channel="stable")       # the timer's check
check("4 the timer's check sends the ETag it holds",
      github["calls"] and github["calls"][-1][1].get("If-None-Match") == 'W/"e"', github["calls"])
check("4 ...and a 304 keeps the cached answer, no error",
      st["latest"] == "v1.0.15" and st["error"] == "" and st["update_available"] is True, st)
github["listing"] = [_rel("v1.0.15"), _rel("v1.0.16")]
github["etag"] = 'W/"f"'
st = upd.check(force=False, config_dir=CFG, channel="stable")
check("4 a changed listing comes through the ETag path", st["latest"] == "v1.0.16", st)
check("4 a press for the version it replaced is caught by the cache, with no request",
      "no longer the latest" in _apply("1.0.15") and github["calls"][-1][1].get("If-None-Match"))
github["calls"].clear()
check("4 a press for the newest goes ahead", _apply("1.0.16") == "ok")
check("4 ...and apply()'s own check carried no ETag (forced, so a stale answer is impossible)",
      len(github["calls"]) == 1 and "If-None-Match" not in github["calls"][0][1], github["calls"])

# -- 5. never a downgrade, never a reinstall, never a withdrawn release -----------------
upd.VERSION = "1.0.16"
r = _apply("1.0.16")
check("5 the running version is not reinstalled", "not newer" in r, r)
upd.VERSION = "1.0.17"
r = _apply("1.0.16")
check("5 a version below the running one is refused (that is Rollback's job)", "not newer" in r, r)
upd.VERSION = "1.0.12"
github["listing"] = [_rel("v1.0.15")]                   # v1.0.16 withdrawn
github["etag"] = 'W/"g"'
before = upd._read_json(JOB)
r = _apply("1.0.16")
check("5 a release withdrawn since the check is not installed",
      "no longer the latest" in r and _latest() == "v1.0.15", (r, _latest()))
check("5 ...and no job was written for it", upd._read_json(JOB) == before)

# -- 6. no answer from GitHub ---------------------------------------------------------
github["listing"] = [_rel("v1.0.15")]
upd.check(force=True, config_dir=CFG, channel="stable")
for label, fail in (("offline", urllib.error.URLError(OSError(113, "No route to host"))),
                    ("rate-limited", _http(403)),
                    ("token expired", _http(401))):
    github["fail"] = fail
    r = _apply("1.0.15")
    check(f"6 {label}: the install is refused rather than started",
          r.startswith("could not confirm"), r)
    check(f"6 {label}: ...in plain words", _plain(r), r)
    check(f"6 {label}: ...and the cache keeps the release it knew", _latest() == "v1.0.15")
    # The press's own check is not the timer's: with "Check for updates
    # automatically" off, two refused presses posted the Inbox notice "the
    # box will try again later", which it would not (Yaya, 2026-10-05).
    streak = upd._read_json(STABLE)
    check(f"6 {label}: ...and is not a miss in the 'could not check' streak",
          not streak.get("check_fails") and not streak.get("check_notice"), streak)
check("6 the expired-token refusal says what to do", "paste a new one" in r, r)
github["fail"] = urllib.error.URLError(OSError(113, "No route to host"))
for _ in range(upd.CHECK_FAILS_NOTICE):
    upd.check(force=True, config_dir=CFG, channel="stable")
streak = upd._read_json(STABLE)
check("6 ...while the timer's and Check now's misses still count",
      streak.get("check_fails") == upd.CHECK_FAILS_NOTICE, streak)
github["fail"] = None
upd.check(force=True, config_dir=CFG, channel="stable")
check("6 ...and an answer clears the streak", not upd._read_json(STABLE).get("check_fails"))

# -- 7. rollback is untouched ----------------------------------------------------------
upd.previous_version = lambda: "1.0.11"
github["calls"].clear()
github["fail"] = urllib.error.URLError(OSError(113, "No route to host"))
try:
    upd.rollback(CFG)
    r = "ok"
except upd.UpdateError as exc:
    r = str(exc)
github["fail"] = None
job = upd._read_json(JOB)
check("7 rollback to the previous release goes ahead with no network at all",
      r == "ok" and job.get("mode") == "rollback" and job.get("version") == "1.0.11" and
      github["calls"] == [], (r, job, github["calls"]))

# -- 8. the route and the state ----------------------------------------------------------
entry = catalog.entry_of("SYS_UPDATE_SUPERSEDED")
check("8 superseded is its own catalogued event",
      entry.event_id == "SYS-1034" and catalog.HTTP_STATUS.get("SYS_UPDATE_SUPERSEDED") == 409, entry)
check("8 ...whose words say what happened and what to do",
      "newer version" in entry.message and "Update again" in entry.action, entry)
route = io.open(os.path.join(ROOT, "core", "routes", "update.py"), encoding="utf-8").read()
check("8 the apply route maps UpdateSuperseded before the generic failure",
      route.index("UpdateSuperseded") < route.index('"SYS_UPDATE_APPLY_FAILED"')
      and 'details=(f"Version {e.newest}' in route, "")
st = upd.public_state(None, "stable", CFG)
check("8 the state a route returns carries publishing and no secrets",
      "publishing" in st and "etag" not in st and "assets" not in st, sorted(st))
# The route itself, with the gate stood in for (smoke_updatelock's recipe):
# a refused press answers with the service's sentence in `details`, which is
# what the card shows; before, only the catalog's "could not be installed"
# reached the screen (Yaya, 2026-10-05).
hub_stub = types.ModuleType("core.routes.hub")
hub_stub._require_admin = hub_stub._require_developer = lambda request, action: None
sys.modules["core.routes.hub"] = hub_stub
if "fastapi" not in sys.modules:                               # the dev box has none
    try:
        import fastapi                                         # noqa: F401
    except ImportError:
        fa = types.ModuleType("fastapi")
        fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
            get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail="", headers=None):
                super().__init__(detail)
                self.status_code, self.detail, self.headers = status_code, detail, headers
        fa.HTTPException, fa.Request = HTTPException, object
        sys.modules["fastapi"] = fa
from core.routes import update as routes                       # noqa: E402


async def _json():
    return {"version": "1.0.15", "channel": "stable"}
req = types.SimpleNamespace(
    query_params={}, state=types.SimpleNamespace(user="admin"), json=_json,
    app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)))
github["fail"] = urllib.error.URLError(OSError(113, "No route to host"))
try:
    asyncio.run(routes.apply(req))
    got = None
except Exception as exc:                                   # noqa: BLE001
    got = exc
github["fail"] = None
details = str(getattr(got, "event_details", "") or "")
check("8 a refused press carries the refusal in the body's details",
      details.startswith("could not confirm") and "no internet" in details
      and _plain(details), (got, details))
check("8 ...under the apply-failed code, with the catalog's sentence as the error",
      getattr(got, "event_code", "") == "SYS_UPDATE_APPLY_FAILED"
      and "could not be installed" in str(getattr(got, "detail", "")), got)

# -- 9. the card -------------------------------------------------------------------------
panel = io.open(os.path.join(ROOT, "frontend", "src", "components", "UpdatePanel.jsx"),
                encoding="utf-8").read()
check("9 the card refreshes and names the newest version on a superseded press",
      'SUPERSEDED = "SYS-1034"' in panel and "e.data?.event_id === SUPERSEDED" in panel
      and "load();" in panel.split("event_id === SUPERSEDED", 1)[1][:400], "")
check("9 the card says when the newest release is still being published",
      "state.publishing" in panel and "still being published" in panel)
check("9 the card refreshes a cache older than a few minutes as it opens",
      "STALE_S" in panel and "check(true)" in panel)

print()
if bad:
    print(f"{bad} FAILED")
    sys.exit(1)
print("all good")
