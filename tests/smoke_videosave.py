# -*- coding: utf-8 -*-
"""The [video] "video saving" switches on the master side.

  1. One rule, twice: core/services/video_saving.settings equals the app's
     view_gate.settings on every case (the app cannot import core/).
  2. Payloads: with both switches off, overlay_view.page_info -- which is
     what /api/detector/webrtc, a dashboard video tile and /api/hub/streams
     all spread into their answers -- is byte for byte the slice-1 answer;
     a switch on adds `video_saving` and nothing else.
  3. The hub's overlay relay still keys on the `overlay` member: a server-
     mode system with a switch on is NOT relayed.
  4. Save/get through config.local.toml: defaults, a hold clamped, unknown
     keys refused, view_on_detect refused ON in server mode (allowed OFF),
     other [video] keys left alone; the route answers 400 for a refusal.
  5. Who draws the boxes: "server" is the default on both sides, an explicit
     "client" opts in, and set_overlay / POST .../video/overlay write
     [video] overlay (both ways, view_on_detect off on the way to server),
     refuse anything else with a 400, and leave an audit entry.

    python tests/smoke_videosave.py
"""
import asyncio
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

# The dev box runs the suite without FastAPI; the routes only need its names
# (the same stand-in smoke_hubwalloverlay uses).
if "fastapi" not in sys.modules:
    try:
        import fastapi                                         # noqa: F401
    except ImportError:
        fa = types.ModuleType("fastapi")
        fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
            get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
            put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
            patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail
        fa.HTTPException = HTTPException
        fa.Request = fa.UploadFile = object
        fa.Query = fa.File = fa.Form = lambda *a, **k: None
        fa.__path__ = []
        sys.modules["fastapi"] = fa
        resp = types.ModuleType("fastapi.responses")
        resp.StreamingResponse = resp.JSONResponse = object
        resp.RedirectResponse = resp.FileResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp
        st = types.ModuleType("starlette")
        st.__path__ = []
        bg = types.ModuleType("starlette.background")
        bg.BackgroundTask = lambda fn, *a, **k: types.SimpleNamespace(fn=fn)
        sys.modules.update({"starlette": st, "starlette.background": bg})

from app import view_gate                                    # noqa: E402
from core.services import overlay_view, video_saving           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CASES = [{}, {"video": "x"}, {"video": {}},
         {"video": {"on_demand": True}}, {"video": {"on_demand": "yes"}},
         {"video": {"on_demand": "maybe", "view_on_detect": 1}},
         {"video": {"view_on_detect": True, "view_hold_s": 1}},
         {"video": {"view_hold_s": 500}}, {"video": {"view_hold_s": "12.6"}},
         {"video": {"view_hold_s": None}}, {"video": {"view_hold_s": "junk"}}]

# -- 1. one rule ----------------------------------------------------------------
check("master and app read the switches the same way",
      all(video_saving.settings(c) == view_gate.settings(c) for c in CASES),
      [(c, video_saving.settings(c), view_gate.settings(c)) for c in CASES])
check("defaults: both off, hold 30 s",
      video_saving.settings({}) == {"on_demand": False, "view_on_detect": False,
                                    "view_hold_s": 30})
check("...and the app's constant agrees",
      view_gate.DEFAULT_HOLD_S == video_saving.DEFAULT_HOLD_S == 30
      and (view_gate.MIN_HOLD_S, view_gate.MAX_HOLD_S)
      == (video_saving.MIN_HOLD_S, video_saving.MAX_HOLD_S) == (2, 120))


# -- 2. payloads ------------------------------------------------------------------
def old_page_info(cfg):
    """overlay_view.page_info as slice 1 shipped it (6b51b41f)."""
    s = overlay_view.settings(cfg)
    if s["mode"] != "client":
        return {}
    return {"overlay": {"mode": "client", "delay_ms": s["delay_ms"]}}


OFF = [{}, {"video": {"overlay": "server"}},
       {"video": {"overlay": "client", "overlay_delay_ms": 120}},
       {"video": {"overlay": "client", "on_demand": False,
                  "view_on_detect": False, "view_hold_s": 45}},
       {"video": {"overlay": "server", "view_hold_s": 9}}]
check("both off: page_info byte-identical to before, every mode",
      all(json.dumps(overlay_view.page_info(c)) == json.dumps(old_page_info(c))
          for c in OFF), [(overlay_view.page_info(c), old_page_info(c)) for c in OFF])
on_client = overlay_view.page_info({"video": {"overlay": "client",
                                              "view_on_detect": True}})
check("view_on_detect on (client): overlay + video_saving",
      on_client == {"overlay": {"mode": "client", "delay_ms": 0},
                    "video_saving": {"on_demand": False, "view_on_detect": True,
                                     "view_hold_s": 30}}, on_client)
on_server = overlay_view.page_info({"video": {"overlay": "server",
                                              "on_demand": True}})
check("on_demand on (server): video_saving only, no overlay member",
      on_server == {"video_saving": {"on_demand": True, "view_on_detect": False,
                                     "view_hold_s": 30}}, on_server)
src = {n: io.open(os.path.join(ROOT, *n.split("/")), encoding="utf-8").read()
       for n in ("core/routes/models.py", "core/services/builder.py",
                 "core/routes/hub.py", "core/routes/hub_overlay.py")}
check("/api/detector/webrtc spreads page_info (no new poll, no new field)",
      "**overlay_view.page_info(cfg)," in src["core/routes/models.py"])
check("a dashboard video tile and the Live Wall spread for_system",
      "overlay = overlay_view.for_system(systems_dir, slug)"
      in src["core/services/builder.py"]
      and "**overlay_view.for_system(" in src["core/routes/hub.py"])

# -- 3. the hub relay keys on the overlay member ----------------------------------
check("the hub relay asks for the overlay member, not truthiness",
      "overlay_view.for_system(pkg, slug).get(\"overlay\")"
      in src["core/routes/hub_overlay.py"])
from fastapi import HTTPException                              # noqa: E402

from core.routes import hub_overlay as hub_route               # noqa: E402

hub_route.overlay_view.for_system = lambda pkg, slug: on_server
hub_route.hub_service.visible_to = lambda *a, **k: True
STATE = types.SimpleNamespace(
    registry=[types.SimpleNamespace(slug="lot")],
    masters={"lot": {"url": "http://127.0.0.1:1"}}, config_dir=None,
    systems_dir=tempfile.mkdtemp())
req = types.SimpleNamespace(
    app=types.SimpleNamespace(state=STATE), headers={},
    state=types.SimpleNamespace(user="op"),
    url=types.SimpleNamespace(hostname="10.0.0.5", scheme="http"))
try:
    got = hub_route.hub_overlay_events(req, slugs="lot", camera=[])
    code = 200
except HTTPException as exc:
    code = exc.status_code
check("a server-mode system with on_demand on is not relayed (404)", code == 404, code)

# -- 4. save / get ------------------------------------------------------------------
CFG = tempfile.mkdtemp()
SLUG = "lot"
pkg = os.path.join(CFG, SLUG)
os.makedirs(os.path.join(pkg, "app"), exist_ok=True)
io.open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8").write(
    '[app]\n[video]\noverlay = "server"\noverlay_hz = 12\n')

check("get: defaults and the overlay mode",
      video_saving.get(CFG, SLUG) == {"on_demand": False, "view_on_detect": False,
                                      "view_hold_s": 30, "overlay": "server"})
got = video_saving.save(CFG, SLUG, {"on_demand": True, "view_hold_s": 500})
check("save: on_demand on, the hold clamped to 120", got["on_demand"] is True
      and got["view_hold_s"] == 120, got)
local = io.open(os.path.join(pkg, "config.local.toml"), encoding="utf-8").read()
check("...written to [video] of config.local.toml", "[video]" in local
      and "on_demand = true" in local and "view_hold_s = 120" in local, local)
check("...leaving the other [video] keys alone",
      overlay_view.settings({"video": {"overlay": "server", "overlay_hz": 12}})
      == overlay_view.settings(__import__("core.services.detector_control",
                                          fromlist=["x"])._merged(pkg)))
for bad_body, why in (({"overlay": "client"}, "unknown key"),
                      ({"view_on_detect": True}, "server mode")):
    try:
        video_saving.save(CFG, SLUG, bad_body)
        refused = False
    except ValueError as exc:
        refused = True
        msg = str(exc)
    check(f"save refuses: {why}", refused, bad_body)
check("...and says what view_on_detect needs", video_saving.NEEDS_CLIENT in msg, msg)
check("view_on_detect OFF is always allowed",
      video_saving.save(CFG, SLUG, {"view_on_detect": False})["view_on_detect"] is False)
io.open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8").write(
    '[app]\n[video]\noverlay = "client"\n')
got = video_saving.save(CFG, SLUG, {"view_on_detect": True, "view_hold_s": 30})
check("client mode: view_on_detect saves on", got["view_on_detect"] is True
      and got["overlay"] == "client", got)

from core.routes import video_saving as route                  # noqa: E402

route._system_context = lambda r: (CFG, SLUG)
route.perm.require = lambda r, cap: None


AUDIT = []
route.audit_service.record = lambda action, **kw: AUDIT.append((action, kw))


class Req:
    def __init__(self, body):
        self._body = body
        self.state = types.SimpleNamespace(user="master")
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(config_dir=None))

    async def json(self):
        return self._body


ans = route.detector_get_video(Req(None))
check("GET answers the switches and the reason text",
      ans["ok"] and ans["video"]["view_on_detect"] is True
      and ans["needs_client"] == video_saving.NEEDS_CLIENT, ans)
try:
    asyncio.run(route.detector_set_video(Req({"bogus": 1})))
    code = 200
except HTTPException as exc:
    code = exc.status_code
check("POST: a refusal is a 400", code == 400, code)
ans = asyncio.run(route.detector_set_video(Req({"on_demand": False})))
check("POST: a save answers the new switches", ans["video"]["on_demand"] is False, ans)
ms = io.open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
check("the master mounts the route", "app.include_router(video_saving_router)" in ms)


# -- 4b. view_classes: which classes open the video --------------------------------
CLS = [{"video": {"view_classes": ["person", " car ", "person", "", None]}},
       {"video": {"view_classes": []}}, {"video": {"view_classes": "person"}},
       {"video": {"view_classes": None}}, {"video": {"view_on_detect": True}}]
check("master and app read view_classes the same way",
      all(video_saving.settings(c) == view_gate.settings(c) for c in CLS),
      [(c, video_saving.settings(c), view_gate.settings(c)) for c in CLS])
check("absent or not a list means every class (the key is not there)",
      "view_classes" not in video_saving.settings({})
      and "view_classes" not in video_saving.settings(CLS[2])
      and "view_classes" not in video_saving.settings(CLS[3]))
check("a list is cleaned: trimmed, de-duplicated, blanks dropped",
      video_saving.settings(CLS[0])["view_classes"] == ["person", "car"])
check("an empty list is kept: it means no class",
      video_saving.settings(CLS[1])["view_classes"] == [])
got = video_saving.save(CFG, SLUG, {"view_classes": ["person", "dog"]})
check("save: classes written to [video] and read back", got.get("view_classes") == ["person", "dog"], got)
local = io.open(os.path.join(pkg, "config.local.toml"), encoding="utf-8").read()
check("...as a TOML list in config.local.toml", "view_classes" in local and '"person"' in local, local)
got = video_saving.save(CFG, SLUG, {"view_classes": []})
check("save: none ticked is stored as an empty list", got.get("view_classes") == [], got)
got = video_saving.save(CFG, SLUG, {"view_classes": None})
check("save: null puts it back to every class (key removed)",
      "view_classes" not in got
      and "view_classes" not in io.open(os.path.join(pkg, "config.local.toml"), encoding="utf-8").read())
try:
    video_saving.save(CFG, SLUG, {"view_classes": "person"})
    refused = False
except ValueError:
    refused = True
check("save refuses a class list that is not a list", refused)
check("the page payload carries the choice only when one was made",
      "view_classes" not in video_saving.page_info(
          {"video": {"view_on_detect": True}})["video_saving"]
      and video_saving.page_info(
          {"video": {"view_on_detect": True, "view_classes": ["a"]}})["video_saving"]["view_classes"] == ["a"])

# -- 5. who draws the boxes --------------------------------------------------------
from app import overlay_feed                                   # noqa: E402

check("server is the default, master and app alike",
      overlay_view.DEFAULT_MODE == overlay_feed.DEFAULT_MODE == "server"
      and overlay_view.settings({})["mode"] == overlay_feed.settings({})["mode"]
      == "server" and overlay_view.settings({"video": {"overlay": "junk"}})["mode"]
      == "server")
check("...an untouched system's page answer has no overlay member",
      overlay_view.page_info({}) == {})
check("...and an explicit client opts in on both sides",
      overlay_view.settings({"video": {"overlay": "client"}})["mode"]
      == overlay_feed.settings({"video": {"overlay": " Client "}})["mode"] == "client"
      and overlay_view.page_info({"video": {"overlay": "client"}})
      == {"overlay": {"mode": "client", "delay_ms": 0}})

# The package says client and view_on_detect is on (from section 4).
got, before = video_saving.set_overlay(CFG, SLUG, "server")
local = io.open(os.path.join(pkg, "config.local.toml"), encoding="utf-8").read()
check("set_overlay server: written, previous mode returned",
      got["overlay"] == "server" and before == "client"
      and 'overlay = "server"' in local, (got, before, local))
check("...and view_on_detect switched off with it",
      got["view_on_detect"] is False and "view_on_detect = false" in local, local)
check("...other [video] keys left alone", "view_hold_s = 30" in local, local)
got, before = video_saving.set_overlay(CFG, SLUG, "client")
local = io.open(os.path.join(pkg, "config.local.toml"), encoding="utf-8").read()
check("set_overlay client: written to config.local.toml",
      got["overlay"] == "client" and before == "server"
      and 'overlay = "client"' in local, local)
for bad_mode in ("", "both", None, 3):
    try:
        video_saving.set_overlay(CFG, SLUG, bad_mode)
        refused = False
    except ValueError:
        refused = True
    check(f"set_overlay refuses {bad_mode!r}", refused)

ans = asyncio.run(route.detector_set_overlay(Req({"mode": "server"})))
check("POST overlay: answers the new mode", ans["ok"]
      and ans["video"]["overlay"] == "server", ans)
check("...and leaves one audit entry naming the change",
      len(AUDIT) == 1 and AUDIT[0][0] == "video.overlay"
      and AUDIT[0][1]["username"] == "master"
      and "client -> server" in AUDIT[0][1]["detail"], AUDIT)
for body in ({"mode": "nope"}, {}, ["client"]):
    try:
        asyncio.run(route.detector_set_overlay(Req(body)))
        code = 200
    except HTTPException as exc:
        code = exc.status_code
    check(f"POST overlay {body!r}: a 400", code == 400, code)
check("...and a refusal is not audited", len(AUDIT) == 1, AUDIT)
rsrc = io.open(os.path.join(ROOT, "core", "routes", "video_saving.py"),
               encoding="utf-8").read()
check("POST overlay takes the panel's grant",
      rsrc.count('perm.require(request, "detection.hud")') == 2)
panel = io.open(os.path.join(ROOT, "frontend", "src", "components",
                             "VideoSavingPanel.jsx"), encoding="utf-8").read()
check("the panel offers both choices and the turn-on button, no bare Off",
      "/api/detector/control/video/overlay" in panel
      and "Draw boxes in the browser" in panel and "Embed boxes in the video" in panel
      and "recommended" not in panel.lower()
      and "Switch to Draw Boxes in the Browser" in panel and "`Off - " not in panel)
check("Video Visibility is nested between the two radios, with the two use lines",
      panel.index('value="client"') < panel.index("data-video-visibility")
      < panel.index('aria-label="Show video only when an object is detected"')
      < panel.index('value="server"')
      and 'aria-labelledby="video-visibility-h"' in panel
      and "Standard accuracy and low CPU usage." in panel
      and "High accuracy, may impact CPU usage." in panel)
# Customer copy pass (2026-10-11): the on_demand switch is not drawn any more,
# while its API field stays (checked above) and the app keeps honouring it.
check("the panel no longer draws the on_demand switch",
      "<div>Pause video work" not in panel
      and 'aria-label="Pause video work' not in panel
      and "on_demand: on" not in panel)

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
