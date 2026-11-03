# A built-in template seeds a new system with nothing from the box it was
# authored on.
#
# UAT-102 (Ochobot, first run on board "iriv", 2026-10-04): People Counting
# seeded two "Live video" tiles for ip_camera_192_168_1_67 / _65 and zones
# "Line 2" / "Zone 3" with real coordinates onto a system whose one camera
# slot was `kind: unset` -- while the create wizard said a template's "zone
# bindings are deliberately blank". UAT-111: the same template shipped an
# unwired Buzzer and Snapshot, two warnings on every create. UAT-112: every
# template hint was empty and Blank Canvas was preselected.
#
# What this pins, for the shipped data AND for the landing path (a template
# update from the authoring box must not bring any of it back):
#
#   * config/formats.toml: no widget names a camera, no zones_json, no Zone
#     node names a zone, at most one video tile per page, no unwired
#     Buzzer/Snapshot, a hint on every template;
#   * apply_format_workflow lands a DIRTY format clean: cameras blank, no
#     zones, zone bindings blank;
#   * every built-in lands with no compile ERROR (a switched-off chain's
#     Trigger used to refuse the whole canvas -- workflow.py oid_off);
#   * the wizard preselects nothing and lists Blank Canvas first.
#
#     py -3 tests/smoke_templateseed.py
import json
import os
import shutil
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import provision, templates                 # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from core.services import workflow as wf                       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


with open(os.path.join(ROOT, "config", "formats.toml"), "rb") as f:
    SHIPPED = tomllib.load(f)["formats"]

# -- the shipped data --------------------------------------------------------------
for key, fmt in SHIPPED.items():
    check(f"{key}: has a one-line hint", bool(str(fmt.get("hint") or "").strip()))
    check(f"{key}: ships no zones", "zones_json" not in fmt)
    cams = [w.get("camera") for w in fmt.get("widgets") or [] if w.get("camera")]
    dash = json.loads(fmt.get("dashboard_json") or "{}")
    for page in dash.get("pages") or []:
        cams += [w.get("camera") for w in page.get("widgets") or [] if w.get("camera")]
        videos = [w for w in page.get("widgets") or [] if w.get("kind") == "video"]
        check(f"{key}/{page.get('id')}: at most one video tile (one camera slot)",
              len(videos) <= 1, [w.get("id") for w in videos])
    check(f"{key}: no widget names a camera", not cams, cams)
    store = json.loads(fmt.get("workflows_json") or "{}")
    for w in store.get("workflows") or []:
        g = w.get("graph") or {}
        wired = {e.get(s) for e in g.get("edges") or [] for s in ("source", "target")}
        for n in g.get("nodes") or []:
            d = n.get("data") or {}
            if n["type"] == "zone.ref":
                check(f"{key}: Zone node {n['id']} is unbound", d.get("zone") == "",
                      d.get("zone"))
            if n["type"] in ("output.buzzer", "output.snapshot"):
                check(f"{key}: {n['type']} {n['id']} is wired", n["id"] in wired)
            check(f"{key}: node {n['id']} names no camera", not d.get("camera"))

# -- a dirty format lands clean ---------------------------------------------------
dirty = {
    "label": "Dirty",
    "zones": [{"id": "zone_x", "name": "Line 2", "kind": "line",
               "points": [[0.6, 1.0], [0.6, 0.0]], "enabled": True}],
    "dashboard": {"pages": [{"id": "main", "name": "Dashboard", "widgets": [
        {"id": "v1", "title": "Live video", "kind": "video", "source": "stat",
         "camera": "ip_camera_192_168_1_67", "span": 4}]}]},
    "workflows": {"version": 3, "workflows": [{
        "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
        "author": "", "created": 0, "modified": 0,
        "graph": {"version": wf.SCHEMA_VERSION, "nodes": [
            {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
             "data": {"camera": "ip_camera_192_168_1_67"}},
            {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
             "data": {"zone": "zone_x"}},
        ], "edges": [{"id": "e1", "source": "s", "target": "z"}]}}]},
}
land, cfg = tempfile.mkdtemp(), tempfile.mkdtemp()
os.makedirs(os.path.join(land, "dirty"), exist_ok=True)
warnings = templates.apply_format_workflow(land, cfg, "dirty", dirty, key="dirty")
landed = dc._zone_list(dc._merged(dc._pkg_dir(land, "dirty")))
check("a format's zones do not land on a new system", not landed, landed)
from core.services import builder as bs                        # noqa: E402
layout = bs.load(cfg, "dirty")
vids = [w for p in layout.get("pages") or [] for w in p.get("widgets") or []
        if w.get("kind") == "video"]
check("a format's video tile lands on the system's first camera",
      vids and all(not w.get("camera") for w in vids), vids)
nodes = {n["id"]: n for w in wf.load_store("dirty")["workflows"]
         for n in w["graph"]["nodes"]}
check("a format's Zone node lands unbound", nodes["z"]["data"].get("zone") == "",
      nodes["z"]["data"])
check("...and its source names no camera", not nodes["s"]["data"].get("camera"),
      nodes["s"]["data"])
check("the caller's format is not mutated",
      dirty["dashboard"]["pages"][0]["widgets"][0]["camera"]
      == "ip_camera_192_168_1_67")

# -- every built-in lands without a compile error ---------------------------------
for key, fmt in provision.builtin_formats(None).items():
    land, cfg = tempfile.mkdtemp(), tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, "config", "formats.toml"), cfg)
    os.makedirs(os.path.join(land, key), exist_ok=True)
    w = templates.apply_format_workflow(land, cfg, key, fmt, key=key)
    check(f"{key}: lands with no compile error",
          not any("before it runs" in x or "could not" in x for x in w), w)
    check(f"{key}: its only to-dos are zones to pick",
          all("waiting for a" in x for x in w), w)

# -- the wizard ---------------------------------------------------------------------
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "CreateSystemWizard.jsx"), encoding="utf-8") as f:
    wiz = f.read()
# Template first (Tok Aba, 2026-10-22): the template is chosen on a Hub home
# card (or New System = the server's default, Blank Canvas) before the wizard
# opens, so the wizard takes it as given and has no picker step of its own.
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "TemplateGallery.jsx"), encoding="utf-8") as f:
    gallery = f.read()
check("the wizard starts from the template it was handed",
      'useState(template || "")' in wiz and 'tpl || tplData?.default' in wiz
      and '"format"' not in wiz)
check("...and the cards list Blank Canvas first",
      '(b.key === "blank_canvas") - (a.key === "blank_canvas")' in gallery)
# The "Finish setting it up" toast is gone (Tok Aba, 2026-10-26): a second
# warning stacked under "created" read as an error.
check("...and stacks no warning toast under the created one",
      "Finish setting it up" not in wiz)

# -- what the Hub home's cards and preview are fed --------------------------------
from core.services import template_preview                      # noqa: E402
shipped = provision.builtin_formats(os.path.join(ROOT, "config"))
pv = template_preview.summary(shipped["counting"])
check("the preview summary reads the counting build",
      "In" in pv["cards"] and pv["steps"] > 1 and pv["pages"], pv)
check("...and a blank template answers empty",
      template_preview.summary({}) == {"pages": [], "cards": [], "steps": 0, "tables": []})
imgs = tempfile.mkdtemp()
for n in ("counting-2.png", "counting.png", "counting-10.webp", "counting_x.png",
          "counting-a.png", "counting.svg", "other.png"):
    open(os.path.join(imgs, n), "wb").close()
check("pictures are found by key, in slider order, png/jpg/webp only",
      template_preview.images("counting", imgs)
      == ["/static/img/templates/counting.png", "/static/img/templates/counting-2.png",
          "/static/img/templates/counting-10.webp"],
      template_preview.images("counting", imgs))
check("...and a missing folder is no pictures, not an error",
      template_preview.images("counting", os.path.join(imgs, "nope")) == [])
shutil.rmtree(imgs, ignore_errors=True)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
