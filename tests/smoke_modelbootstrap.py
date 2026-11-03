# A fresh box has its detection models -- and their class names -- from day one.
#
# Found on 192.168.1.47 (a fresh v1.0.12 install): the AI Models page offered
# yolov8s with "Needs its .hef", because nothing had ever fetched the
# catalogue, and coco.txt was missing everywhere because the Model Zoo URL it
# was downloaded from now answers 404 -- and the detector refuses to start
# when its configured labels file is missing.
#
# What is pinned here:
#   1. coco.txt is WRITTEN from models.COCO_CLASSES, with no network, and the
#      copy fetch_models.py carries is the same list in the same order
#   2. nothing downloads the class names any more
#   3. a HEF download is checked: an HTML page or a tiny answer is thrown away
#   4. ensure(): fetches what is missing for the arch, skips what is present,
#      does nothing without an accelerator
#   5. the hub's start step: missing -> fetch once; present -> nothing;
#      offline -> failed + a notice, then back-off on the same version
#   6. the installer has the step, after the venv and before the units, and
#      it cannot end the install
#   7. selecting a catalogue model writes coco.txt when none exists
#   8. a manifest's weights are found in the shared store for ITS arch only
import io
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
SHARED = tempfile.mkdtemp()
os.environ["CYTRON_BUILTIN_MODEL_DIR"] = SHARED
os.environ["CYTRON_NVME"] = tempfile.mkdtemp()

from core import config as config_mod                          # noqa: E402
from core.services import builtin_models                       # noqa: E402
from core.services import model_bootstrap as mb                # noqa: E402
from core.services import models as models_service             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


class FakeResponse:
    def __init__(self, body):
        self._body, self._at = body, 0

    def read(self, n):
        chunk = self._body[self._at:self._at + n]
        self._at += n
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(bodies, calls):
    def opener(url, timeout=60):
        calls.append(url)
        body = bodies(url)
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body)
    return opener


HEF = b"\x01HEF" + b"\x00" * (2 * 1024 * 1024)

# -- 1. coco.txt without the network ----------------------------------------
check("the shared models dir is the test's", str(config_mod.shared_models_dir())
      == SHARED, str(config_mod.shared_models_dir()))
path = mb.write_labels(SHARED)
with open(path, encoding="utf-8") as f:
    lines = [l.strip() for l in f if l.strip()]
check("coco.txt is written from COCO_CLASSES",
      tuple(lines) == models_service.COCO_CLASSES and len(lines) == 80, lines[:3])
with open(path, "w", encoding="utf-8") as f:
    f.write("person\n")
mb.write_labels(SHARED)
check("...an existing coco.txt is kept",
      open(path, encoding="utf-8").read() == "person\n")
os.remove(path)

script = read("systems", "demo", "scripts", "fetch_models.py")
block = script[script.index("COCO_CLASSES = ("):script.index(")\n", script.index("COCO_CLASSES = ("))]
embedded = tuple(re.findall(r'"([^"]+)"', block))
check("fetch_models.py carries the same 80 names in the same order",
      embedded == models_service.COCO_CLASSES,
      [a for a, b in zip(embedded, models_service.COCO_CLASSES) if a != b][:3])

# -- 2. nothing downloads the class names ------------------------------------
for rel in (("core", "services", "builtin_models.py"),
            ("core", "services", "model_bootstrap.py"),
            ("systems", "demo", "scripts", "fetch_models.py")):
    check(f"{'/'.join(rel)} no longer names the dead labels URL",
          "coco_eighty" not in read(*rel))

# -- 3. the HEF size check ----------------------------------------------------
dest = os.path.join(tempfile.mkdtemp(), "hailo8l", "yolov8n.hef")
why = mb.download_hef("u", dest, opener_for(lambda u: b"<html>404</html>", []))
check("an HTML page is not a model", why and not os.path.exists(dest), why)
why = mb.download_hef("u", dest, opener_for(lambda u: b"\x01" * 5000, []))
check("a 5 kB answer is not a model", why and not os.path.exists(dest), why)
check("...and no .part is left behind", not os.path.exists(dest + ".part"))
why = mb.download_hef("u", dest, opener_for(lambda u: HEF, []))
check("a real-sized HEF lands", why is None and os.path.getsize(dest) == len(HEF),
      why)

# -- 4. ensure() ---------------------------------------------------------------
calls = []
result = mb.ensure(arch="hailo8l", opener=opener_for(lambda u: HEF, calls),
                   log=lambda *a: None)
check("both catalogue models are fetched for the arch",
      sorted(result["fetched"]) == ["yolov8n", "yolov8s"]
      and all(os.path.getsize(os.path.join(SHARED, "hailo8l", f"{n}.hef")) > 1e6
              for n in ("yolov8n", "yolov8s")), result)
check("...from the zoo release for that arch",
      all("/v2.19.0/hailo8l/" in u for u in calls) and len(calls) == 2, calls)
check("...and coco.txt beside them",
      os.path.exists(os.path.join(SHARED, "coco.txt")), result["labels"])
calls.clear()
result = mb.ensure(arch="hailo8l", opener=opener_for(lambda u: HEF, calls),
                   log=lambda *a: None)
check("present files are skipped", calls == []
      and sorted(result["present"]) == ["yolov8n", "yolov8s"], (calls, result))
result = mb.ensure(arch=None, target=tempfile.mkdtemp())
check("no accelerator: nothing fetched, the class names still written",
      result["arch"] is None or result["fetched"] == [], result)
check("missing() is empty once everything is here", mb.missing("hailo8l") == [],
      mb.missing("hailo8l"))
check("...and names what another arch lacks",
      mb.missing("hailo10h") == ["yolov8n", "yolov8s"], mb.missing("hailo10h"))

# -- 5. the hub's start step ---------------------------------------------------
cfg = tempfile.mkdtemp()
notes = []


def notify(kind, title, body="", config_dir=None, **kw):
    notes.append((kind, title, body, kw))


runs = []


def run_ok(arch=None, log=None):
    runs.append(arch)
    return mb.ensure(arch=arch, opener=opener_for(lambda u: HEF, []),
                     log=lambda *a: None)


def run_offline(arch=None, log=None):
    runs.append(arch)
    return mb.ensure(arch=arch,
                     opener=opener_for(lambda u: OSError("no route"), []),
                     log=lambda *a: None)


check("present -> nothing to do",
      mb.ensure_step(cfg, "1.0.12", 1000, notify, arch="hailo8l", run=run_ok)
      == "present" and runs == [])
check("no accelerator -> nothing to do",
      mb.ensure_step(cfg, "1.0.12", 1000, notify, arch=None, run=run_ok)
      in ("no_arch", "present"))
builtin_models.detect_arch = lambda: None
check("...even when detection finds none",
      mb.ensure_step(cfg, "1.0.12", 1000, notify, run=run_ok) == "no_arch")
res = mb.ensure_step(cfg, "1.0.12", 1000, notify, arch="hailo8", run=run_offline)
check("missing and offline -> failed", res == "failed" and runs == ["hailo8"],
      (res, runs))
check("...and the admins are told how to fetch by hand",
      notes and notes[-1][0] == "models"
      and "model_bootstrap" in notes[-1][2]
      and notes[-1][3].get("page") == "vision", notes[-1:])
res = mb.ensure_step(cfg, "1.0.12", 2000, notify, arch="hailo8", run=run_ok)
check("...then backs off on the same version", res == "backoff"
      and runs == ["hailo8"], (res, runs))
res = mb.ensure_step(cfg, "1.0.12", 1000 + mb.RETRY_S + 1, notify,
                     arch="hailo8", run=run_ok)
check("...and tries again after the back-off, fetching", res == "ok"
      and runs == ["hailo8", "hailo8"], (res, runs))
check("...after which there is nothing to do",
      mb.ensure_step(cfg, "1.0.12", 99999, notify, arch="hailo8", run=run_ok)
      == "present")
res = mb.ensure_step(cfg, "1.0.13", 1100, notify, arch="hailo10h",
                     run=run_offline)
res2 = mb.ensure_step(cfg, "1.0.14", 1200, notify, arch="hailo10h",
                      run=run_ok)
check("a new version does not wait out the old back-off",
      (res, res2) == ("failed", "ok"), (res, res2))
check("the hub starts it in the background",
      "_model_bootstrap.start_ensure(config_dir)" in read("hub_server.py"))

# -- 6. the installer -----------------------------------------------------------
sh = read("scripts", "install.sh")
at_step = sh.index('say "Detection models"')
check("install.sh has a Detection models step", at_step > 0)
check("...after the Python environment",
      sh.index('say "Python environment"') < at_step)
check("...after the built-in models dir exists",
      sh.index("install -d -m 755 -o \"$SERVICE_USER\" -g \"$SERVICE_USER\" "
               "/opt/irivai-models") < at_step)
check("...and before the systemd units",
      at_step < sh.index('say "systemd units"', at_step))
step = sh[at_step:sh.index('say "systemd units"', at_step)]
check("...run as the service account",
      'sudo -u "$SERVICE_USER" "$PY" -m core.services.model_bootstrap' in step)
check("...and never fatal (|| rc=$?, warnings only)",
      "|| models_rc=$?" in step and "die " not in step
      and step.count("warn ") >= 2)

# -- 7. selecting a catalogue model writes coco.txt ------------------------------
os.remove(os.path.join(SHARED, "coco.txt"))
pkg = os.path.join(ROOT, "systems", "demo")
sel = builtin_models._resolve_selection(pkg, cfg, "yolov8n")
check("selecting yolov8n with no coco.txt anywhere writes one",
      os.path.exists(sel["labels"]) and sel["labels"].endswith("coco.txt"),
      sel)

# -- 8. a manifest's weights from the shared store, for its arch -----------------
up = tempfile.mkdtemp()
manifest = {"name": "yolov8s", "task": "detection", "arch": "hailo8l",
            "file": "yolov8s.hef", "labels": "yolov8s.labels",
            "postprocess": "nms_on_chip"}
with open(os.path.join(up, "yolov8s.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f)
row = models_service._listing_in(up, False, set())[0]
check("a preset manifest sees the catalogue's HEF in the shared store",
      row["weights_present"] is True, row)
# Only a hailo8l build on the box now (the step checks above fetched others).
import shutil                                                  # noqa: E402
for other in ("hailo8", "hailo10h"):
    shutil.rmtree(os.path.join(SHARED, other), ignore_errors=True)
manifest["arch"] = "hailo10h"
with open(os.path.join(up, "yolov8s.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f)
row = models_service._listing_in(up, False, set())[0]
check("...but not another chip's build of the same name",
      row["weights_present"] is False, row)

# -- review fixes (Yaya) -------------------------------------------------------------
# The bus cannot tell a Hailo-8L from a Hailo-8: fetch nothing until the chip
# names itself, and try again at the next start (nothing recorded).
saved_arch = (builtin_models.identify_arch, builtin_models.detect_arch)
builtin_models.identify_arch = lambda: None
builtin_models.detect_arch = lambda: "hailo8"            # the lspci guess
runs.clear()
try:
    res = mb.ensure_step(tempfile.mkdtemp(), "1.0.12", 5000, notify, run=run_ok)
    res_ensure = mb.ensure(target=tempfile.mkdtemp(),
                           opener=opener_for(lambda u: HEF, calls),
                           log=lambda *a: None)
finally:
    builtin_models.identify_arch, builtin_models.detect_arch = saved_arch
check("an accelerator hailortcli cannot name yet: nothing fetched, retried later",
      res == "unidentified" and runs == [], (res, runs))
check("...and the installer's ensure() fetches no guessed build",
      res_ensure["arch"] is None and res_ensure["unidentified"]
      and res_ensure["fetched"] == [], res_ensure)

# Two processes, one file: a download already under way is not raced.
saved_lock = mb.download_lock
mb.download_lock = lambda dest: None
try:
    why = mb.download_hef("u", os.path.join(tempfile.mkdtemp(), "hailo8l", "x.hef"),
                          opener_for(lambda u: HEF, []))
    ui = builtin_models._download("u", os.path.join(tempfile.mkdtemp(), "x.hef"))
finally:
    mb.download_lock = saved_lock
check("a HEF another process is downloading is left to it",
      "already being downloaded" in (why or ""), why)
check("...from the AI Models page's Download too",
      "already being downloaded" in (ui or ""), ui)

# A manifest's arch build in a LATER root beats a flat file in an earlier one.
early, late = tempfile.mkdtemp(), tempfile.mkdtemp()
with open(os.path.join(early, "yolov8m.hef"), "wb") as f:
    f.write(b"flat")
os.makedirs(os.path.join(late, "hailo8l"))
with open(os.path.join(late, "hailo8l", "yolov8m.hef"), "wb") as f:
    f.write(b"arch")
saved_roots = config_mod.model_search_roots
config_mod.model_search_roots = lambda: [early, late]
try:
    got = models_service._shared_weights_path("yolov8m.hef", "hailo8l")
finally:
    config_mod.model_search_roots = saved_roots
check("the arch build is preferred to a flat file in an earlier root",
      got == os.path.join(late, "hailo8l", "yolov8m.hef"), got)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all model bootstrap checks passed")
