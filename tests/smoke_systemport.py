# A system's own port: chosen at create, moved by Edit system.
#
# Tok Aba (2026-10-06): the hub card's pencil became "Edit system" (name AND
# port) and the create wizard offers Automatic (next free) or a custom port.
# Pinned here, against a temp store / config / systems dir, with the master
# processes simulated (nothing is spawned, nothing real is killed):
#   1. create, automatic: the next free port, stream port master + 100
#   2. create, custom: that port; refused in words (no HTTP code, no em dash)
#      for the hub's port and its pending move, MQTT 1883, the 5020 Modbus
#      redirect, SSH (out of range), 70000, "abc", another system's master,
#      a system's video stream, a switched-on Modbus server, a port
#      something already listens on (a real bound socket)
#   3. edit, running: stopped on the old port, started on the new, config
#      follows, the stream port does not move
#   4. edit, it does not come up: whatever started is stopped, the old url
#      goes back, the master runs on the old port again, "Nothing was saved"
#   5. edit, not running: only the url moves, nothing is started
#   6. edit, unit-managed (irivai-master@<slug>): refused, nothing touched;
#      the port facts say editable false with the reason
#   7. the routes: PATCH with a port moves then renames; a refused move saves
#      no name; PATCH with a name only is the old rename (no port fields, no
#      process touched); port-only keeps the name; the check route answers
#      suggested + verdict; a non-admin is refused on all three
#
#     py -3 tests/smoke_systemport.py
import asyncio
import os
import re
import socket
import sys
import tempfile
import types
import urllib.parse
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
TMP = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = os.path.join(TMP, "store")

# The CI box may have no fastapi; the routes under test need none of it at
# call time (same stand-in as smoke_hubcammask.py).
if "fastapi" not in sys.modules:
    try:
        import fastapi                                       # noqa: F401
    except ImportError:
        fa = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        def _route(*_a, **_k):
            return lambda fn: fn

        class APIRouter:
            get = post = put = delete = patch = staticmethod(_route)

        fa.HTTPException, fa.APIRouter = HTTPException, APIRouter
        fa.Request = fa.UploadFile = object
        fa.File = fa.Form = fa.Query = lambda *a, **k: None
        sys.modules["fastapi"] = fa
        resp = types.ModuleType("fastapi.responses")
        resp.JSONResponse = resp.RedirectResponse = resp.FileResponse = \
            resp.StreamingResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp

from core import config                                      # noqa: E402
from core.services import hubport, provision, sysport        # noqa: E402
from core.services import masters as masters_service         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def plain(text):
    """A refusal a person can read: no HTTP code, no em dash."""
    return "\u2014" not in text and not re.search(r"\b(400|403|404|409|500)\b", text)


def port_of(url):
    return urllib.parse.urlparse(url or "").port


CFG = os.path.join(TMP, "config")
SYS = os.path.join(TMP, "systems")
os.makedirs(CFG)
with open(os.path.join(CFG, "systems.toml"), "w", encoding="utf-8") as f:
    f.write('dir = "systems"\n[hub]\nurl = "http://127.0.0.1:5100"\n')
os.makedirs(os.path.join(SYS, "demo", "app"))
open(os.path.join(SYS, "demo", "system.py"), "w").close()
with open(os.path.join(SYS, "demo", "config.toml"), "w", encoding="utf-8") as f:
    f.write("[app]\nstream_port = 5102\n[modbus_server]\nenabled = true\nmode = \"tcp\"\nport = 5030\n")
provision.UNIT_ENV_DIR = os.path.join(TMP, "etc-irivai")      # no unit yet
os.makedirs(provision.UNIT_ENV_DIR)

# Nothing in config is "listening"; HELD is what the bind test sees as taken.
HELD = set()
provision._port_is_free = lambda p: int(p) not in HELD

# -- simulated masters: LIVE {port: slug}; DUD ports never come up ---------
LIVE, DUD, CALLS = {}, set(), []


def fake_spawn(repo_dir, slug, port):
    CALLS.append(("spawn", slug, int(port)))
    if int(port) not in DUD:
        LIVE[int(port)] = slug


def fake_stop(url):
    CALLS.append(("stop", port_of(url)))
    return LIVE.pop(port_of(url), None) is not None


def fake_probe(url, timeout=1.5):
    slug = LIVE.get(port_of(url))
    return {"ok": True, "system_slug": slug} if slug else None


provision.spawn_master = fake_spawn
provision.stop_master = fake_stop
masters_service.probe = fake_probe

print("== create, automatic and custom")
a = provision.create_vision_system(SYS, "Alpha", CFG, template="")
check("automatic: the next free port (5110)", a["port"] == 5110, a)
with open(os.path.join(str(config.generated_systems_dir()), "alpha", "config.toml"),
          encoding="utf-8") as f:
    check("...its stream port is master + 100", "stream_port = 5210" in f.read())
b = provision.create_vision_system(SYS, "Bravo", CFG, template="", port=5150)
check("custom: the port asked for", b["port"] == 5150 and b["url"].endswith(":5150"), b)
check("suggested is the allocator's next port",
      sysport.suggest(SYS, CFG) == 5151, sysport.suggest(SYS, CFG))

real = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
real.bind(("0.0.0.0", 0))
real.listen(1)
held_port = real.getsockname()[1]
HELD.add(held_port)

local = config.load_local("systems", CFG)
local["hub"] = {"pending_port": 5300, "pending_boot": "x"}
config.save_local("systems", local, CFG)

refusals = {
    5100: "answers on", 5300: "moves to at the next restart",
    1883: "the MQTT broker", 5020: "Modbus server", 22: "between 1024 and 65535",
    70000: "between 1024 and 65535", "abc": "whole number",
    5110: 'the system "alpha"', 5210: "live video stream",
    5030: 'the system "demo"\'s Modbus server', held_port: "already listens",
}
before = sorted(os.listdir(str(config.generated_systems_dir())))
for value, words in refusals.items():
    try:
        provision.create_vision_system(SYS, f"Refused {value}", CFG, template="", port=value)
        check(f"custom {value} is refused", False, "it was accepted")
    except ValueError as exc:
        check(f"custom {value} is refused: {exc}", words in str(exc) and plain(str(exc)), exc)
check("...and no refused create left a package behind",
      sorted(os.listdir(str(config.generated_systems_dir()))) == before)
real.close()
HELD.discard(held_port)
probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe_sock.bind(("0.0.0.0", 0))
probe_sock.listen(1)
check("the real bind test sees a real listener",
      hubport.listening(probe_sock.getsockname()[1]))
probe_sock.close()

print("== edit, running")
LIVE[5150] = "bravo"
r = sysport.move(SYS, "bravo", 5160, ROOT, CFG, timeout=0.2)
check("moved and restarted", r == {"moved": True, "port": 5160, "old_port": 5150,
                                   "restarted": True}, r)
check("...stopped on the old port, started on the new",
      ("stop", 5150) in CALLS and ("spawn", "bravo", 5160) in CALLS, CALLS)
check("...config follows", sysport.master_port_of("bravo", CFG) == 5160)
check("...answering there as bravo", LIVE.get(5160) == "bravo" and 5150 not in LIVE, LIVE)
info = sysport.info(SYS, "bravo", CFG)
check("...its stream port did not move", info["stream_port"] == 5250, info)
r = sysport.move(SYS, "bravo", 5160, ROOT, CFG, timeout=0.2)
check("the same port moves nothing", r["moved"] is False and r["restarted"] is False, r)
try:
    sysport.move(SYS, "bravo", 5110, ROOT, CFG, timeout=0.2)
    check("moving onto alpha's port is refused", False)
except ValueError as exc:
    check("moving onto alpha's port is refused", 'the system "alpha"' in str(exc), exc)
try:
    sysport.move(SYS, "bravo", 5250, ROOT, CFG, timeout=0.2)
    check("moving onto its own stream port is refused", False)
except ValueError as exc:
    check("moving onto its own stream port is refused",
          "this system's own live video stream" in str(exc), exc)

print("== edit, does not come up -> rolled back")
DUD.add(5170)
CALLS.clear()
try:
    sysport.move(SYS, "bravo", 5170, ROOT, CFG, timeout=0.2)
    check("a move that never answers fails", False)
except ValueError as exc:
    msg = str(exc)
    check("...said plainly: " + msg,
          "did not come up on port 5170" in msg and "put back on 5160" in msg
          and "running there again" in msg and "Nothing was saved" in msg and plain(msg), msg)
check("...config is back on the old port", sysport.master_port_of("bravo", CFG) == 5160)
check("...the master runs on the old port again", LIVE.get(5160) == "bravo", LIVE)
check("...whatever started on the new port was stopped", ("stop", 5170) in CALLS, CALLS)

print("== edit, not running")
LIVE.clear()
CALLS.clear()
r = sysport.move(SYS, "bravo", 5180, ROOT, CFG, timeout=0.2)
check("only the url moves", r["moved"] and not r["restarted"]
      and sysport.master_port_of("bravo", CFG) == 5180, r)
check("...nothing started or stopped", not CALLS, CALLS)

print("== unit-managed")
local = config.load_local("systems", CFG)
local.setdefault("masters", {})["demo"] = {"url": "http://127.0.0.1:5111"}
config.save_local("systems", local, CFG)
with open(os.path.join(provision.UNIT_ENV_DIR, "demo.env"), "w", encoding="utf-8") as f:
    f.write("PORT=5111\n")
LIVE[5111] = "demo"
CALLS.clear()
try:
    sysport.move(SYS, "demo", 5190, ROOT, CFG, timeout=0.2)
    check("a systemd-run master is refused", False)
except ValueError as exc:
    check("a systemd-run master is refused: " + str(exc),
          "irivai-master@demo" in str(exc) and plain(str(exc)), exc)
check("...nothing touched", not CALLS and sysport.master_port_of("demo", CFG) == 5111)
info = sysport.info(SYS, "demo", CFG)
check("...port facts say not editable, with the reason",
      info["editable"] is False and "irivai-master@demo" in info["reason"], info)

print("== routes")
from core.routes import hub as hub_routes                    # noqa: E402
from core.routes import sysport as sysport_routes            # noqa: E402


class Reg(list):
    def get(self, slug):
        return next((x for x in self if x.slug == slug), None)


def state():
    names = provision.system_names(CFG)
    return SimpleNamespace(
        config_dir=CFG, systems_dir=SYS,
        registry=Reg(SimpleNamespace(slug=s, display_name=names.get(s, s.title()))
                     for s in ("demo", "alpha", "bravo")),
        masters=dict(config.systems(CFG).get("masters") or {}))


ST = state()


def rebuild(st):
    fresh = state()
    st.registry, st.masters = fresh.registry, fresh.masters


def request(body=None, user="owner"):
    async def _json():
        return body or {}
    return SimpleNamespace(app=SimpleNamespace(state=ST), json=_json,
                           state=SimpleNamespace(user=user), headers={},
                           url=SimpleNamespace(hostname="192.168.1.21"))


ADMINS = {"owner"}
sysport.MOVE_TIMEOUT_S = 0.2
with mock.patch.object(hub_routes.auth_service, "is_admin",
                       side_effect=lambda u, _c=None: u in ADMINS), \
     mock.patch.object(hub_routes, "_rebuild_registry", side_effect=rebuild), \
     mock.patch.object(hub_routes.groups_service, "assignments", return_value={}):
    LIVE.clear()
    LIVE[5180] = "bravo"
    CALLS.clear()
    out = asyncio.run(hub_routes.rename_system(
        "bravo", request({"name": "Bravo Line", "port": 5185})))
    check("PATCH name+port: moved, restarted, renamed",
          out["moved"] and out["restarted"] and out["port"] == 5185
          and out["name"] == "Bravo Line", out)
    check("...the answer's url is the browser's host on the new port",
          out["url"] == "http://192.168.1.21:5185", out)
    check("...the hub's in-memory masters follow",
          port_of(ST.masters["bravo"]["url"]) == 5185, ST.masters.get("bravo"))

    try:
        asyncio.run(hub_routes.rename_system(
            "bravo", request({"name": "Should Not Stick", "port": 1883})))
        check("PATCH onto the MQTT port is refused", False)
    except hub_routes.HTTPException as exc:
        check("PATCH onto the MQTT port is refused in words",
              exc.status_code == 400 and "MQTT broker" in exc.detail and plain(exc.detail), exc.detail)
    check("...and the name was not saved", provision.system_names(CFG).get("bravo") == "Bravo Line",
          provision.system_names(CFG))

    DUD.add(5195)
    try:
        asyncio.run(hub_routes.rename_system(
            "bravo", request({"name": "Also Not", "port": 5195})))
        check("PATCH to a port it never answers on fails", False)
    except hub_routes.HTTPException as exc:
        check("PATCH that does not come up: rolled back, said plainly",
              "put back on 5185" in exc.detail, exc.detail)
    check("...name unchanged, config and hub state on the old port",
          provision.system_names(CFG).get("bravo") == "Bravo Line"
          and port_of(ST.masters["bravo"]["url"]) == 5185, ST.masters.get("bravo"))

    CALLS.clear()
    out = asyncio.run(hub_routes.rename_system("alpha", request({"name": "Alpha Two"})))
    check("PATCH name only is the old rename",
          out == {"ok": True, "slug": "alpha", "name": "Alpha Two"} and not CALLS, (out, CALLS))
    out = asyncio.run(hub_routes.rename_system("alpha", request({"name": "Alpha Two", "port": 5110})))
    check("...so is the same port sent back", out.get("moved") is False and not CALLS, (out, CALLS))
    out = asyncio.run(hub_routes.rename_system("alpha", request({"port": 5112})))
    check("port only keeps the name", out["name"] == "Alpha Two"
          and provision.system_names(CFG).get("alpha") == "Alpha Two", out)

    chk = sysport_routes.port_check(request(), port="", slug="")
    check("check route: a suggestion with no port asked", chk.get("suggested") and "usable" not in chk, chk)
    chk = sysport_routes.port_check(request(), port="1883", slug="")
    check("check route: a refusal in words", chk["usable"] is False and "MQTT" in chk["detail"], chk)
    chk = sysport_routes.port_check(request(), port="5185", slug="bravo")
    check("check route: a system's own port is usable for it", chk["usable"] is True, chk)
    chk = sysport_routes.port_check(request(), port="5185", slug="alpha")
    check("...but not for another system", chk["usable"] is False, chk)
    info = sysport_routes.system_port("demo", request())
    check("port facts route: demo is unit-managed", info["editable"] is False, info)

    for call in (lambda: asyncio.run(hub_routes.rename_system("alpha", request({"port": 5120}, user="op")))
                 , lambda: sysport_routes.port_check(request(user="op"), port="5120"),
                 lambda: sysport_routes.system_port("alpha", request(user="op"))):
        try:
            call()
            check("a non-admin is refused", False)
        except hub_routes.HTTPException as exc:
            check("a non-admin is refused", exc.status_code == 403, exc.status_code)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
