# An IP camera's password reaches no account that may not change the camera.
#
# WHAT WAS WRONG (Pipi H2, 2026-10-11). The product hides an RTSP password
# from accounts without detection.camera.change (/api/detector/control,
# /api/hub/pipelines), but three other reads handed it to the lowest tier:
#   * GET /api/dashboard and /api/overview/live returned camera.source raw
#     (detector_control.status());
#   * GET /api/main returned stats.camera_label = state.json `camera`, which
#     the app wrote as the raw source (Camera.label), as it did each
#     cameras[].label -- right beside a `source` key it already masked;
#   * the gst path wrote cam_cfg["source"] into state.json and the events.
# On .164 three systems' state.json held rtsp://admin:<password>@... A plain
# `user` opened devtools on the Dashboard and had the camera's admin login.
# Also: a label built from a pasted address ("IP camera admin:pw@host"), a
# password with "/" not masked (L5), the /tmp camera registry keyed by the
# raw URL (L1).
#
# WHAT IS PINNED.
#   1. the app writes no password: Camera.label, the state.json it writes,
#      every runtime.write(camera=...) and the event camera name.
#   2. core masks on the way out whatever state.json says: an OLD state.json
#      with the password in clear is masked by /api/dashboard,
#      /api/overview/live, /api/main and status() (default), with special
#      characters in the password ("/", "@", ":").
#   3. the one place that still sees it -- /api/detector/control for an
#      account holding detection.camera.change, whose form keeps saved
#      credentials by rebuilding them -- still does; a `user` does not.
#   4. a label is saved without user:password@ (server and page).
#   5. the /tmp camera registry names no camera address; sharing still works.
#
#     python tests/smoke_campasswordleak.py
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

if "fastapi" not in sys.modules:            # the routes are called as functions
    try:
        import fastapi                                       # noqa: F401
    except ImportError:
        import types
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
        fa.File = fa.Form = fa.Query = fa.Body = fa.Depends = lambda *a, **k: None
        sys.modules["fastapi"] = fa
        resp = types.ModuleType("fastapi.responses")
        resp.JSONResponse = resp.RedirectResponse = resp.FileResponse = \
            resp.StreamingResponse = resp.HTMLResponse = resp.PlainTextResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {str(detail)[:600]}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


#: Passwords a camera really is given, special characters and all.
PASSWORDS = ("S3cr3tPass", "ab/cd", "P@ss:w0rd", "a/b@c:d")


def leaks(obj):
    text = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    return [p for p in PASSWORDS if p in text]


from app import camera as camera_mod                              # noqa: E402
from app import faults                                            # noqa: E402

if camera_mod.cv2 is None:
    # Without OpenCV every camera reads as "synthetic" (Camera.synthetic), so
    # its label would never reach the masking. A box always has cv2; nothing
    # below opens a camera, so a stand-in is enough.
    camera_mod.cv2 = SimpleNamespace()

# -- 1. the app ---------------------------------------------------------------------
for pw in PASSWORDS:
    src = f"rtsp://admin:{pw}@10.0.0.9:554/stream1"
    cam = camera_mod.Camera({"source": src})
    check(f"1. Camera.label masks {pw!r}, keeps user and host; .source stays real",
          not leaks(cam.label) and "admin:***@10.0.0.9:554/stream1" in cam.label
          and cam.source == src, cam.label)
    check(f"1. a label typed from a pasted address is masked too ({pw!r})",
          not leaks(faults.mask_label(f"IP camera admin:{pw}@10.0.0.9")),
          faults.mask_label(f"IP camera admin:{pw}@10.0.0.9"))
check("1. a name with nothing secret is left as it is",
      faults.mask_label("Gate 3 (east)") == "Gate 3 (east)"
      and faults.mask_label("ops@plant.example") == "ops@plant.example"
      and camera_mod.Camera({"source": "0"}).label == "0")

rt_path = os.path.join(ROOT, "systems", "demo", "app", "runtime.py")
spec = importlib.util.spec_from_file_location("_leak_runtime", rt_path)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
data = tempfile.mkdtemp(prefix="leak_state_")
src = "rtsp://admin:a/b@c:d@10.0.0.9/stream1"
cam = camera_mod.Camera({"source": src})
# What run.py writes, field for field (the expressions are pinned below).
rt.write(data, running=True, pid=os.getpid(), camera=cam.label,
         cameras=[{"id": "main", "name": faults.mask_label(""), "label": cam.label,
                   "source": faults.redact(src)}])
written = json.load(io.open(os.path.join(data, "state.json"), encoding="utf-8"))
check("1. the state.json the app writes holds no password", not leaks(written), written)

RUN = read("systems", "demo", "app", "run.py")
writes = re.findall(r"runtime\.write\([^)]*?camera=([^,\n]+)", RUN)
check("1. every runtime.write(camera=...) in run.py is a masked label",
      writes and all(w.strip().startswith(("primary.cam.label", "pipe.cam.label",
                                           "(faults.mask_label("))
                     for w in writes), writes)
cams_block = RUN[RUN.index('cameras=[{"id": p.id,'):][:900]
check("1. state.json cameras[]: label from Camera.label, name and source masked",
      '"label": p.cam.label' in cams_block
      and '"name": faults.mask_label(' in cams_block
      and '"source": faults.redact(' in cams_block)
check("1. no raw cam_cfg source is published (state.json, event camera name)",
      'str(cam_cfg.get("source", "camera")) if gst_mode' not in RUN
      and RUN.count('faults.mask_label(str(cam_cfg.get("source", "camera")))') == 2)
check("1. a camera's name in its events is masked (pipe.label())",
      "return faults.mask_label(self.cfg.get(\"label\")" in RUN)
ADAPTER = read("systems", "demo", "adapter.py")
check("1. the fleet agent's camera_label and camera() label are masked",
      ADAPTER.count('faults.mask_label(state.get("camera") or "")') == 2)

# -- 5. the /tmp camera registry ------------------------------------------------------
reg_dir = tempfile.mkdtemp(prefix="leak_reg_")
camera_mod._CAM_REGISTRY = os.path.join(reg_dir, "cytron_cameras.json")
camera_mod.register_camera(src, 5555, "main")
raw = io.open(camera_mod._CAM_REGISTRY, encoding="utf-8").read()
check("5. the registry names no camera address (no password, no host)",
      not leaks(raw) and "10.0.0.9" not in raw and camera_mod.reg_key(src) in raw, raw)
check("5. a USB index keeps its plain key (the numeric alias reads it)",
      camera_mod.reg_key("0") == "0" and camera_mod.reg_key(src).startswith("h:"))
# Another live process owns it: a consumer with the same source finds it.
other = os.getppid()
camera_mod._reg_update(lambda reg: reg[camera_mod.reg_key(src)].update(pid=other))
check("5. a second system on the same camera still finds the owner's feed",
      camera_mod.shared_feed_source(src) == "http://127.0.0.1:5555/raw?camera=main",
      camera_mod.shared_feed_source(src))
# An app from before 1.0.37, still running, registered under the raw URL.
legacy = "rtsp://admin:S3cr3tPass@10.0.0.7/s"
camera_mod._reg_update(lambda reg: reg.update({legacy: {"port": 5556, "pid": other}}))
check("5. ...and an older app's raw-key entry is still found while it lives",
      camera_mod.shared_feed_source(legacy) == "http://127.0.0.1:5556/raw")
camera_mod._reg_update(lambda reg: reg[legacy].update(pid=2 ** 22 + 12345))
camera_mod.register_camera("1", 5557)
raw = io.open(camera_mod._CAM_REGISTRY, encoding="utf-8").read()
check("5. once that older app is gone, the next write scrubs its plaintext entry",
      legacy not in raw and not leaks(raw), raw)
DC_SRC = read("core", "services", "detector_control.py")
check("5. core's preview reads the registry by the same key",
      'key = s if s.isdigit() else "h:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]'
      in DC_SRC and 'return s if s.isdigit() else "h:" + hashlib.sha256(s.encode("utf-8"))'
      '.hexdigest()[:32]' in read("systems", "demo", "app", "camera.py"))

# -- 2, 3. core ---------------------------------------------------------------------------
from core.services import auth                                     # noqa: E402
from core.services import detector_control as dc                   # noqa: E402
from core.services import local_ingest                             # noqa: E402
from core.services import main as main_service                     # noqa: E402
from core.services import redact                                   # noqa: E402

R = dc.SOURCE_REDACTED
for pw in PASSWORDS:
    for text in (f"rtsp://admin:{pw}@10.0.0.9/s", f"IP camera admin:{pw}@10.0.0.9",
                 f"camera 'rtsp://admin:{pw}@10.0.0.9/s' stopped delivering"):
        out = redact.mask_text(text)
        check(f"2. mask_text hides {pw!r} in {text[:28]!r}...",
              not leaks(out) and "10.0.0.9" in out and redact.mask_text(out) == out, out)
check("4. a label is saved without user:password@",
      dc.clean_label("IP camera admin:a/b@c:d@10.0.0.9") == "IP camera 10.0.0.9"
      and dc.clean_label("rtsp://admin:pw@10.0.0.9/s") == "rtsp://10.0.0.9/s"
      and dc.clean_label("  Gate 3  ") == "Gate 3", dc.clean_label("IP camera admin:a/b@c:d@10.0.0.9"))

systems = tempfile.mkdtemp(prefix="leak_sys_")
slug = "gate"
pkg = os.path.join(systems, slug)
shutil.copytree(os.path.join(ROOT, "systems", "demo", "app"), os.path.join(pkg, "app"),
                ignore=shutil.ignore_patterns("__pycache__", "store_ext"))
sdata = os.path.join(systems, "data")
os.makedirs(sdata)
SECRET_SRC = "rtsp://admin:P@ss:w0rd@10.0.0.9:554/stream1"
with open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\nslug = "{slug}"\ndata_dir = {json.dumps(sdata)}\n\n'
            f'[camera]\nsource = {json.dumps(SECRET_SRC)}\n'
            f'label = "IP camera admin:ab/cd@10.0.0.9"\nwidth = 640\nheight = 480\nfps = 10\n')
# An OLD app's state.json: the password in clear in every field it had.
old_state = {"running": True, "pid": os.getpid(), "mode": "unified", "ai_on": True,
             "heartbeat": time.time(), "started_at": time.time() - 60,
             "camera": SECRET_SRC,
             "error": f"camera '{SECRET_SRC}' stopped delivering frames",
             "cameras": [{"id": "main", "label": SECRET_SRC, "source": SECRET_SRC,
                          "error": f"camera {SECRET_SRC!r} stopped delivering frames",
                          "fps": 10}],
             "counts": {"objects_in_frame": 2}}
with open(os.path.join(sdata, "state.json"), "w", encoding="utf-8") as f:
    json.dump(old_state, f)
pkg_rt = dc._app_runtime(pkg)
if pkg_rt is not None:                  # the copied app's runtime: say the pid is it
    pkg_rt._alive = lambda pid: True
    pkg_rt._owns = lambda pid, pkg_dir=None: True

st = dc.status(systems, slug)
check("2. status() (what /api/dashboard and /api/overview/live read) masks camera, "
      "cameras, label, error", not leaks(st) and st["camera"]["source"]
      == f"rtsp://admin:{R}@10.0.0.9:554/stream1", {k: st.get(k) for k in ("camera", "cameras", "error")})
check("2. ...while the app is reported running (the old state.json was read)",
      bool(st.get("app_running") or st.get("running")), st.get("running"))
full = dc.status(systems, slug, reveal=True)
check("3. status(reveal=True) still has the real source (the edit flow's need)",
      full["camera"]["source"] == SECRET_SRC)

ingest = local_ingest.LocalIngest(systems, slug)
stats = ingest.stats()
check("2. /api/main's local stats: camera_label from an old state.json, masked",
      not leaks(stats) and stats.get("camera_label") == f"rtsp://admin:{R}@10.0.0.9:554/stream1",
      stats.get("camera_label"))
fleet = [{"stats": {"camera_label": "rtsp://admin:S3cr3tPass@10.0.0.8/s"}, "camera": {
    "label": "rtsp://admin:S3cr3tPass@10.0.0.8/s"}, "display_name": ""}]
rolled = main_service.aggregate_stats(fleet, stats)
check("2. ...and the fleet roll-up masks a device's camera_label (an older agent)",
      not leaks(rolled), rolled.get("camera_label"))
check("2. ...and a device card's camera-label fallback",
      not leaks(main_service.build_devices(fleet)))

# The three routes, as a plain `user` (and, for the edit form, a master).
CFG = tempfile.mkdtemp(prefix="leak_cfg_")
auth.set_admin_username("boss", CFG)
auth.create_user("sam", "passw0rd!", slug, CFG)
if hasattr(auth._cached_local, "cache_clear"):
    auth._cached_local.cache_clear()


def req(user):
    state = SimpleNamespace(config_dir=CFG, systems_dir=systems,
                            system=SimpleNamespace(slug=slug, name=slug), slaves=[],
                            events=[], local_ingest=ingest, local_stats=None)
    return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(user=user),
                           query_params={}, headers={}, url=SimpleNamespace(path="/"),
                           method="GET", cookies={})


from core.routes import functions as fn_routes                     # noqa: E402
from core.routes import models as model_routes                      # noqa: E402

try:
    out = fn_routes.overview_live(req("sam"))
    check("2. GET /api/overview/live as a `user`: no password", not leaks(out),
          out.get("camera"))
except Exception as exc:                                            # noqa: BLE001
    check("2. GET /api/overview/live as a `user`: no password", False, repr(exc))

try:
    from core.routes import spa as spa_routes                       # noqa: E402
    out = spa_routes.main(req("sam"))
    check("2. GET /api/main as a `user`: no password", not leaks(out),
          (out.get("stats") or {}).get("camera_label"))
except Exception as exc:                                            # noqa: BLE001
    check("2. GET /api/main as a `user`: no password", False, repr(exc))

try:
    from core.routes import builder as builder_routes               # noqa: E402
    status, shaped, dstats = builder_routes._shaped(req("sam"))
    body = {"camera": status.get("camera") or {}, "stats": dstats}
    check("2. GET /api/dashboard's payload (camera, stats) as a `user`: no password",
          not leaks(body), body.get("camera"))
except Exception as exc:                                            # noqa: BLE001
    check("2. GET /api/dashboard's payload as a `user`: no password", False, repr(exc))
BUILDER = read("core", "routes", "builder.py")
check("2. /api/dashboard's camera is status()['camera'] from the masked status()",
      '"camera": status.get("camera") or {},' in BUILDER and "reveal" not in BUILDER
      and "reveal" not in read("core", "routes", "functions.py"))

out = model_routes.detector_control_status(req("sam"))
check("3. /api/detector/control as a `user` (no camera.change): masked", not leaks(out),
      out.get("camera"))
out = model_routes.detector_control_status(req("boss"))
check("3. ...as the master (camera.change): the real source, for the edit form",
      out["camera"]["source"] == SECRET_SRC)

# -- 4. the page's label --------------------------------------------------------------------
node = shutil.which("node")
if node:
    jsx = read("frontend", "src", "pages", "Detection.jsx")
    fns = "\n".join(re.search(r"export function %s\(.*?\n}\n" % n, jsx, re.S).group(0)
                    .replace("export ", "") for n in ("camSourceSafe", "camHost"))
    cases = [f"rtsp://admin:{pw}@10.0.0.9:554/s" for pw in PASSWORDS] + ["10.0.0.9"]
    code = fns + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(c => " \
                 "[camHost(c), camSourceSafe(c)])));"
    res = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True,
                         text=True, timeout=60)
    got = json.loads(res.stdout or "[]")
    check("4. the page names a pasted address by its host, never user:password",
          got and all(h in ("10.0.0.9:554", "10.0.0.9") and not leaks(h) for h, _ in got), got)
    check("4. ...and its source line drops everything to the LAST @",
          got and all(not leaks(s) for _, s in got), got)
    check("4. pickedLabel uses that host", "IP camera ${camHost(ip)}" in jsx)
else:
    print("skip 4: node is not installed")

shutil.rmtree(systems, ignore_errors=True)
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
