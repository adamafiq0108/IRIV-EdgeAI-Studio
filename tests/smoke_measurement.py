# Measurement setup from the master's side: the floor calibration, the
# try-it-now tools, and the per-object profiles -- everything the operator
# touches on the Measurement panel. The maths itself is pinned in
# smoke_measure.py; this pins how it is reached and what is stored.
#
# Three rules worth a test each:
#
#   * TAPS ARE NORMALISED. The operator taps a preview that is not the depth
#     grid's size. A tap carried as pixels of the wrong picture is the
#     top-left-quarter bug from the depth work, wearing a different hat.
#   * ONLY A DEPTH CAMERA carries a measurement table. On any other kind it
#     would be settings that quietly do nothing.
#   * THE FLOOR IS REFUSED when the scene does not read as a floor. A bad
#     calibration is worse than none: every later height is wrong by the same
#     amount and no single reading looks wrong.
import io
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import detector_control as dc             # noqa: E402
from core.services import measurement                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


FLOOR = {"normal": [0.0, 0.07, 0.9975], "d": -1995.0, "rms_mm": 3.2,
         "centre_mm": 2000.0, "coverage": 0.91, "at": "2026-09-17T12:00:00"}

# -- only a depth camera measures ------------------------------------------------
depth = dc._validate_camera({"source": "orbbec", "measure": {"floor": FLOOR}})
check("a depth camera carries its floor plane",
      depth.get("measure", {}).get("floor", {}).get("d") == -1995.0, depth.get("measure"))
usb = dc._validate_camera({"source": "0", "measure": {"floor": FLOOR}})
check("...and a USB camera does not, whatever the config says",
      "measure" not in usb, usb.get("measure"))

# -- the stored table is sanitised, not trusted ---------------------------------
nan_floor = dict(FLOOR, normal=[0.0, float("nan"), 1.0])
check("a NaN in the plane normal drops the floor rather than storing it",
      "floor" not in dc._clean_measure({"floor": nan_floor}),
      "a plane with NaN in it is a measurement that is never wrong and never right")
check("a zero normal is not a plane",
      "floor" not in dc._clean_measure({"floor": dict(FLOOR, normal=[0, 0, 0])}))
bgc = dc._clean_measure({"background": {"file": "measure/main_background.png", "coverage": 0.87,
                                          "width": 1280, "height": 720, "at": "2026-09-17T12:00:00"}})
check("the remembered scene's table is kept, its file pinned under measure/",
      bgc.get("background", {}).get("file") == "measure/main_background.png"
      and bgc["background"]["coverage"] == 0.87, bgc)
check("the colour scene rides beside the depth scene, pinned to the same folder",
      dc._clean_measure({"background": {"file": "measure/main_background.png",
                                        "rgb_file": "measure/main_background_rgb.png"}})
      ["background"].get("rgb_file") == "measure/main_background_rgb.png"
      and "rgb_file" not in dc._clean_measure({"background": {"file": "measure/a.png",
                                                              "rgb_file": "/etc/x.png"}})["background"])
check("calibration keeps the scene in colour too, best effort, and Auto fetches a frame to match",
      'background["rgb_file"] = rgb_rel' in read("core", "services", "measurement.py")
      and 'colour = fetch_frame(systems_dir, slug, cam["id"])' in read("core", "services", "measurement.py"))
check("an outline profile keeps its corners, its anchor and 2D/3D",
      (lambda p: p["tool"] == "outline" and p.get("points") and p.get("dims") == "3d"
       and p.get("anchor_box") == [0.2, 0.1, 0.8, 0.9])(
          dc._clean_measure({"profiles": [{"tool": "outline", "dims": "3d",
                                           "points": [[0.3, 0.2], [0.7, 0.2], [0.7, 0.8], [0.3, 0.8]],
                                           "anchor_box": [0.2, 0.1, 0.8, 0.9]}]})["profiles"][0]))
check("moved corners survive Measure now, and become the profile",
      all(k in read("frontend", "src", "components", "MeasurementDialog.jsx")
          for k in ("cornersEdited && corners && !redetect", 'tool: "outline", enabled: true, dims,', "Re-detect")))
check("the master notices when the picture no longer matches the remembered scene",
      'out["moved"] = drift > SCENE_DRIFT_FRACTION' in read("core", "services", "measurement.py")
      and 0.2 <= measurement.SCENE_DRIFT_FRACTION <= 0.6)
check("...and the panel says so, with Recalibrate beside it, and marks a borrowed-depth side",
      "state?.moved && (" in read("frontend", "src", "components", "MeasurementDialog.jsx")
      and 'result.approx?.[i] ? "≈"' in read("frontend", "src", "components", "MeasurementDialog.jsx"))
check("the master measures corners the operator set, as an outline tool",
      "outline" in dc.MEASURE_TOOLS and 'elif tool == "outline":' in read("core", "services", "measurement.py")
      and "vertices_override=pts" in read("core", "services", "measurement.py"))
check("the panel lets a corner be dragged, previews angles while it moves, re-measures on release",
      all(k in read("frontend", "src", "components", "MeasurementDialog.jsx")
          for k in ("setDragCorner(i)", "previewOutline(cs)", "remeasureOutline(cs)")))
check("an Auto profile remembers 2D or 3D, and only that",
      dc._clean_measure({"profiles": [{"tool": "auto", "dims": "3d"}]})["profiles"][0].get("dims") == "3d"
      and "dims" not in dc._clean_measure({"profiles": [{"tool": "auto", "dims": "4d"}]})["profiles"][0]
      and "dims" not in dc._clean_measure({"profiles": [{"tool": "sides", "dims": "3d"}]})["profiles"][0])
check("the route passes 2D/3D through and nothing else",
      '"3d" if str(body.get("dims") or "").lower() == "3d" else "2d"' in read("core", "routes", "models.py"))
_panel3 = read("frontend", "src", "components", "MeasurementDialog.jsx")
check("the panel offers the switch for Auto and shows tilt and volume",
      'if (tool === "auto") body.dims = dims;' in _panel3 and 'label="Tilt"' in _panel3 and 'label="Volume"' in _panel3)
check("the panel's Auto asks for the best cut, the live loop for the fast one",
      'quality="best"' in read("core", "services", "measurement.py")
      and "EXTRACT_BEST_PASSES if best else EXTRACT_PASSES" in read("systems", "demo", "app", "measure.py"))
check("the panel draws the extraction as the filled silhouette, sides numbered on it",
      "result.outline.silhouette?.length >= 3" in read("frontend", "src", "components", "MeasurementDialog.jsx"))
check("Auto extracts the object's own silhouette from the picture, seeded by depth",
      "extract_object(colour, (x1, y1, x2, y2), grid.shape[:2]," in read("systems", "demo", "app", "measure.py")
      and "cv2.grabCut(" in read("systems", "demo", "app", "measure.py"))
check("...the app hands the master a lossless frame",
      '"/frame"' in read("systems", "demo", "app", "run.py"))
check("...and a file outside that folder is dropped",
      "background" not in dc._clean_measure({"background": {"file": "../../etc/passwd"}})
      and "background" not in dc._clean_measure({"background": {"file": "measure/../x.png"}}))
check("a floor with the numbers a person checks keeps them",
      dc._clean_measure({"floor": FLOOR})["floor"].get("centre_mm") == 2000.0)

profiles = dc._clean_measure({"profiles": [
    {"id": "carton", "name": "Carton", "cls": "box", "tool": "auto", "sides": [0, 2, 99, -1, "x"]},
    {"name": "Pipe end", "cls": "pipe", "tool": "circle",
     "points": [[0.2, 0.3], [0.5, 0.31], [0.8, 0.3], [1.5, 0.2], "junk"]},
    {"name": "Nope", "tool": "laser"},
    "not a dict",
]})["profiles"]
check("profiles keep the tools the app knows and drop the rest",
      [p["tool"] for p in profiles] == ["auto", "circle"], [p.get("tool") for p in profiles])
check("...side indices are bounded and de-duplicated",
      profiles[0]["sides"] == [0, 2], profiles[0].get("sides"))
check("...manual taps are kept only inside the picture (0..1)",
      profiles[1]["points"] == [[0.2, 0.3], [0.5, 0.31], [0.8, 0.3]], profiles[1].get("points"))
check("...an auto profile carries no taps",
      "points" not in profiles[0])
anch = dc._clean_measure({"profiles": [
    {"tool": "sides", "points": [[0.4, 0.2], [0.4, 0.8]], "anchor_box": [0.3, 0.1, 0.5, 0.9]},
    {"tool": "sides", "points": [[0.4, 0.2], [0.4, 0.8]], "anchor_box": [0.5, 0.1, 0.3, 0.9]},
]})["profiles"]
check("a profile keeps the box its taps were placed on, and drops an inside-out one",
      anch[0].get("anchor_box") == [0.3, 0.1, 0.5, 0.9] and "anchor_box" not in anch[1], anch)
check("the app tells the master what it sees, boxes normalised",
      '"/detections"' in read("systems", "demo", "app", "run.py")
      and "round(box[0] / fw, 4)" in read("systems", "demo", "app", "run.py"))
check("...and a manual measurement says which object the taps were on",
      "object_under(points, live_detections(" in read("core", "services", "measurement.py")
      and measurement.object_under([[0.5, 0.5], [0.5, 0.7]],
                                   [{"box": [0, 0, 0.3, 0.3]}, {"box": [0.4, 0.4, 0.6, 0.8]}])["box"]
      == [0.4, 0.4, 0.6, 0.8]
      and measurement.object_under([[0.9, 0.9]], [{"box": [0, 0, 0.3, 0.3]}]) is None)
check("the panel saves that object with the profile, so the taps follow it",
      "anchor_box: under?.box || undefined" in read("frontend", "src", "components", "MeasurementDialog.jsx")
      and "follows the object" in read("frontend", "src", "components", "MeasurementDialog.jsx"))
check("...and Save is offered for an emptied list too",
      "profilesDirty" in read("frontend", "src", "components", "MeasurementDialog.jsx")
      and "profiles.length > 0 && (" not in read("frontend", "src", "components", "MeasurementDialog.jsx"))
check("a circle profile remembers what its taps span",
      dc._clean_measure({"profiles": [{"tool": "circle", "mode": "radius"}]})["profiles"][0]["mode"]
      == "radius"
      and dc._clean_measure({"profiles": [{"tool": "circle"}]})["profiles"][0]["mode"] == "diameter")
check("the profile list is bounded",
      len(dc._clean_measure({"profiles": [{"tool": "auto"}] * 100})["profiles"])
      == dc.MAX_MEASURE_PROFILES)

# -- the writer refuses a camera that cannot measure -----------------------------
systems_dir = tempfile.mkdtemp()
slug = "demo"
import shutil                                                # noqa: E402
shutil.copytree(os.path.join(ROOT, "systems", "demo"), os.path.join(systems_dir, slug))
# Depth cameras are offered on a developer box only (depth_offered, see
# smoke_depthoffer.py), and this is a test of depth measurement.
DEV_CONFIG = tempfile.mkdtemp()
with open(os.path.join(DEV_CONFIG, "deploy.toml"), "w", encoding="utf-8") as f:
    f.write('mode = "dev"\n')
dc.set_cameras(systems_dir, slug, [{"id": "cam1", "source": "0"},
                                   {"id": "d1", "source": "orbbec"}], config_dir=DEV_CONFIG)
try:
    dc.set_camera_measure(systems_dir, slug, "cam1", {"floor": FLOOR})
    check("saving a floor against a USB camera is refused", False)
except ValueError as exc:
    check("saving a floor against a USB camera is refused", "depth" in str(exc), str(exc))
saved = dc.set_camera_measure(systems_dir, slug, "d1", {"floor": FLOOR})
check("...and accepted against the depth camera",
      (saved.get("measure") or {}).get("floor", {}).get("centre_mm") == 2000.0)
saved = dc.set_camera_measure(systems_dir, slug, "d1", {"profiles": [{"tool": "auto", "cls": "box"}]})
check("saving profiles keeps the floor (a merge, not a replace)",
      saved["measure"].get("floor") and saved["measure"]["profiles"][0]["cls"] == "box",
      saved.get("measure"))
saved = dc.set_camera_measure(systems_dir, slug, "d1", {"floor": None})
check("...and clearing the floor keeps the profiles",
      "floor" not in saved["measure"] and saved["measure"].get("profiles"))

# -- 3D zones: the master stores a depth range, bounded ------------------------------
zone = dc._validate_zone({"id": "z", "kind": "area", "points": [[0, 0], [1, 0], [1, 1]],
                          "depth_min_mm": "500", "depth_max_mm": 999999})
check("a zone keeps a sane depth bound and drops an absurd one",
      zone.get("depth_min_mm") == 500 and "depth_max_mm" not in zone, zone)
check("...and a zone without one round-trips without the keys",
      "depth_min_mm" not in dc._validate_zone({"id": "z", "kind": "area",
                                               "points": [[0, 0], [1, 0], [1, 1]]}))
zones_page = read("frontend", "src", "pages", "Zones.jsx")
check("the range is offered only on a zone over a depth camera",
      '?.kind === "depth" && (' in zones_page and "depth_min_mm" in zones_page)
check("...and the depth view toggle too",
      '"&view=depth"' in zones_page and "mjpegOnly=" in zones_page)
check("the preview relay passes view=depth through, nothing else",
      'if str(view).strip().lower() == "depth":' in read("core", "routes", "models.py"))
run_src = read("systems", "demo", "app", "run.py")
check("the app serves the depth view on the same feed, painted with the overlay",
      'if view == "depth" and get_depth_view is not None' in run_src
      and "_paint(pipe.overlay, colour)" in run_src)

# -- profiles applied at detection time --------------------------------------------
check("the loop stamps depth and sizes between the tracker and the zones",
      run_src.index("pipe.measurer.stamp(") > run_src.index("tracks = pipe.tracker.update(")
      and run_src.index("pipe.measurer.stamp(") < run_src.index("result = pipe.engine.update("))
check("...and publishes the measurer's numbers with the counts",
      "counts.update(measure_stats)" in run_src)
check("a saved profile reaches the running measurer without reopening the camera",
      "have.measurer.configure(" in run_src
      and run_src.index("have.measurer.configure(") < run_src.index("if have.parked:\n"))
wf_src = read("core", "services", "workflow.py")
check("a Data Table column can capture the measured numbers",
      all(f in wf_src for f in ('"depth_mm"', '"length_mm"', '"diameter_mm"')))
check("...and the Workflow names the new metrics",
      'rest.startsWith("measure_")' in read("frontend", "src", "pages", "Workflow.jsx"))
measure_panel = read("frontend", "src", "components", "MeasurementDialog.jsx")
check("the panel lets each profile pick its numbers and its sides",
      'toggleIn(i, "fields", f.id)' in measure_panel and 'toggleIn(i, "sides", k)' in measure_panel)

# -- the floor is refused when it is not a floor -----------------------------------
src = read("core", "services", "measurement.py")
check("a scattered fit is not kept as a floor plane",
      "MAX_FLOOR_RMS_MM" in src and "is not a flat floor" in src
      and measurement.MAX_FLOOR_RMS_MM <= 30)
check("...and so is one through too little of the picture",
      "MIN_FLOOR_COVERAGE" in src and measurement.MIN_FLOOR_COVERAGE >= 0.25)
check("auto measurement without a calibration says to calibrate first",
      "calibrate first - auto measurement" in src)
check("calibration remembers the empty scene as the median of several pictures, and saves it",
      "capture_background(systems_dir, slug, cam[\"id\"])" in src and "np.nanmedian" in src
      and measurement.BACKGROUND_FRAMES >= 3 and 'cv2.imencode(".png", grid)' in src)
check("...the floor plane is a bonus, kept when flat and dropped with the reason when not",
      '"floor_problem": floor_problem' in src and '{"background": background, "floor": floor}' in src)
check("...and clearing forgets both, file included",
      '{"floor": None, "background": None}' in src and "os.remove(path)" in src)
check("the app loads the remembered scene beside the profiles",
      "measure_mod.load_background(config_mod.PKG_DIR" in read("systems", "demo", "app", "run.py"))
check("the panel calls the first step what it is",
      "1. Empty scene" in read("frontend", "src", "components", "MeasurementDialog.jsx"))

# -- depth comes from the app, never from the device -----------------------------
check("the master fetches depth from the running app's /depth",
      "/depth?camera=" in src and "urllib.request.urlopen" in src,
      "the SDK holds the camera exclusively; opening it here would fail or steal it")
check("...and loads the maths from the app, so there is ONE copy",
      '_load_app_module(dc._pkg_dir(systems_dir, slug), "measure")' in src)
check("...with the app answering a 404 for a camera that measures no depth",
      '"this camera measures no depth"' in read("systems", "demo", "app", "run.py"))

# -- taps are normalised -------------------------------------------------------------
check("taps arrive as fractions of the picture and are scaled to the grid",
      "float(pt[0]) * w, float(pt[1]) * h" in src)
routes = read("core", "routes", "models.py")
run_route = routes.split('name="detector.measure_run"', 1)[1].split("@router.", 1)[0]
check("...and the route refuses a tap outside 0..1",
      "0.0 <= u <= 1.0 and 0.0 <= v <= 1.0" in run_route)
check("...and more than 32 of them", "len(points) > 32" in run_route)
panel = read("frontend", "src", "components", "MeasurementDialog.jsx")
check("the panel sends fractions, computed from the picture it drew",
      "(e.clientX - r.left) / r.width" in panel and "(e.clientY - r.top) / r.height" in panel)

# -- who may do what ---------------------------------------------------------------------
for name, cap in (("detector.measure_calibrate", 'perm.require(request, "detection.camera.change")'),
                  ("detector.measure_profiles", 'perm.require(request, "detection.camera.change")'),
                  ("detector.measure_clear", 'perm.require(request, "detection.camera.change")')):
    block = routes.split(f'name="{name}"', 1)[1].split("@router.", 1)[0]
    check(f"{name.split('.')[1]} changes what the system measures, so it needs camera-change",
          cap in block)
for name in ("detector.measure_state", "detector.measure_run"):
    block = routes.split(f'name="{name}"', 1)[1].split("@router.", 1)[0]
    check(f"{name.split('.')[1]} is the operator's",
          '_require_operator(request, slug, "measurement")' in block)
check("the blocking fetch-and-fit runs off the event loop",
      "asyncio.to_thread(\n            measurement_service.calibrate_floor" in routes
      and "asyncio.to_thread(\n            measurement_service.run" in routes)

# -- the panel -------------------------------------------------------------------------
check("calibration asks first, and says what a wrong calibration costs",
      "Remember the empty scene?" in panel and "Clear the scene first" in panel)
check("the panel works in the order the work has to happen: scene, measure, profiles",
      panel.index("1. Empty scene") < panel.index("2. Measure") < panel.index("3. What each object carries"))
# The panel offers the tools an operator uses: Auto, Sides (two taps each),
# Circle (two taps plus what they span). Line and Polygon are still accepted
# by the server -- a profile saved with one keeps working -- but folded into
# Sides on the panel, because two taps per side IS a line and IS a polygon.
check("the panel offers Auto, Sides and Circle",
      all(f'id: "{t}"' in panel for t in ("auto", "sides", "circle")))
check("...and the server accepts them all, plus the older two",
      all(t in dc.MEASURE_TOOLS for t in ("auto", "sides", "circle", "line", "polygon")))
check("a circle tells the server what its two taps span",
      'if (tool === "circle") body.mode = circleMode;' in panel
      and 'str(body.get("mode") or "")' in routes)
check("...and the server takes radius or diameter, defaulting to diameter",
      '"radius" if str(mode).lower() == "radius" else "diameter"' in src)
check("the wait is shown over the footage, not only on a button",
      "placeItems: \"center\"" in panel and "<Loader2 size={34}" in panel)
check("sizes are written on the sides themselves",
      "result.segments.map(([a, b], i)" in panel and "Math.round(result.segments_mm[i])} mm" in panel)
check("...and on the circle, and on the auto box",
      "`⌀ ${Math.round(result.diameter_mm)} mm`" in panel
      and "${Math.round(result.length_mm)} × ${Math.round(result.width_mm)} mm" in panel)
check("a measured outline is drawn back over the picture with its sides labelled",
      "live.sides_mm?.[i]" in panel and "<polygon" in panel)
page = read("frontend", "src", "pages", "Detection.jsx")
check("only a depth camera gets the ruler button",
      'c.kind === "depth" && (' in page and "setMeasureFor(c)" in page)
check("...and the camera dialog offers Measurement beside Adjustments, in depth mode",
      'camMode === "depth" && (' in page and "setMeasureFor(activeCam)" in page)
check("the dialog's state is declared above the page's early returns",
      page.index("const [measureFor, setMeasureFor] = useState(null);")
      < page.index("if (error) return <ErrorNote"))
check("the panel sits above the camera dialog it opens from",
      'className="cy-app z-[80]" overlayClassName="z-[80]"' in panel)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
