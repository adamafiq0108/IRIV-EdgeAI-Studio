# Depth (3D) cameras are offered on a developer box only.
#
# Depth is R&D: measurement from depth is still being proved on the workshop
# board, and a customer box is not where that happens. The switch is the
# same DEPLOYMENT fact that gates the developer tooling -- deploy.toml
# `mode` -- so one build serves both: a customer box offers no Depth tab,
# lists no depth camera in discovery, and refuses a new one; a developer box
# is exactly as it was. A depth camera already stored on a customer box is
# carried through a rewrite untouched: the gate is on the way in.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import detector_control as dc             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def config_dir(mode):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "deploy.toml"), "w", encoding="utf-8") as f:
        f.write(f'mode = "{mode}"\n')
    return d


CUSTOMER, DEV = config_dir("customer"), config_dir("dev")
NONE = tempfile.mkdtemp()          # no deploy.toml at all

check("a customer box does not offer depth", dc.depth_offered(CUSTOMER) is False)
check("a developer box does", dc.depth_offered(DEV) is True)
check("a box with no deploy.toml fails closed, as the developer tooling does",
      dc.depth_offered(NONE) is False)

# -- discovery ---------------------------------------------------------------
real = dc.list_depth_cameras
dc.list_depth_cameras = lambda: [{"name": "Orbbec Gemini 336", "serial": "X",
                                  "source": "orbbec:X", "kind": "depth"}]
real_usb, real_gige = dc.list_cameras, dc.list_gige_cameras
dc.list_cameras = lambda: []
dc.list_gige_cameras = lambda: []
try:
    cust = dc.discover_cameras(CUSTOMER)
    dev = dc.discover_cameras(DEV)
finally:
    dc.list_depth_cameras = real
    dc.list_cameras, dc.list_gige_cameras = real_usb, real_gige
check("an Orbbec plugged into a customer box is not in the list",
      cust["depth"] == [] and cust["depth_offered"] is False, cust)
check("...and on a developer box it is", len(dev["depth"]) == 1 and dev["depth_offered"] is True, dev)

# -- adding one ----------------------------------------------------------------
# add_camera / set_cameras need a system on disk; the validation that refuses
# is what is pinned here, through the same entry points the routes call.
systems = tempfile.mkdtemp()
slug = "demo"
os.makedirs(os.path.join(systems, slug, "app"), exist_ok=True)
with open(os.path.join(systems, slug, "system.toml"), "w", encoding="utf-8") as f:
    f.write('[system]\nname = "demo"\n')
real_cams = dc.cameras
stored = []
dc.cameras = lambda *_a, **_k: list(stored)
try:
    try:
        dc.add_camera(systems, slug, {"source": "orbbec"}, config_dir=CUSTOMER)
        check("a customer box refuses a new depth camera", False, "it was accepted")
    except ValueError as e:
        check("a customer box refuses a new depth camera, with the way on",
              "not available on this box" in str(e) and "USB" in str(e), str(e))
    try:
        dc.set_cameras(systems, slug, [{"id": "d", "source": "orbbec"}], config_dir=CUSTOMER)
        check("...and through the whole-list write too", False, "it was accepted")
    except ValueError as e:
        check("...and through the whole-list write too", "not available on this box" in str(e), str(e))
    # A depth camera ALREADY stored is carried through a rewrite: refusing it
    # would lock the operator out of editing every other camera.
    stored[:] = [{"id": "d", "source": "orbbec", "kind": "depth"}]
    try:
        dc.set_cameras(systems, slug, [{"id": "d", "source": "orbbec"},
                                       {"id": "u", "source": "0"}], config_dir=CUSTOMER)
        carried = True
    except ValueError as e:
        carried = "not available" not in str(e)
    check("a depth camera already stored survives a rewrite on a customer box", carried)
finally:
    dc.cameras = real_cams

check("an ordinary camera is untouched by any of this",
      dc._validate_camera({"source": "0"})["kind"] == "usb")

# -- the page ------------------------------------------------------------------
page = read("frontend", "src", "pages", "Detection.jsx")
check("the camera panel hides the Depth tab unless the box offers it",
      'budget.depth_offered !== false' in page and 'm !== "depth"' in page)
check("...but keeps it for a depth camera being edited", 'camMode === "depth"' in page)
routes = read("core", "routes", "models.py")
check("the discovery route asks with the box's own config",
      "discover_cameras(\n        request.app.state.config_dir)" in routes.replace("\r\n", "\n"))
check("the budget the page reads carries the flag",
      '"depth_offered": depth_offered(config_dir)' in read("core", "services", "detector_control.py"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
