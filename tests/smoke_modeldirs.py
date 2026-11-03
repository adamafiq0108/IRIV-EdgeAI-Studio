# Built-in models and the operator's drop folder are DIFFERENT directories.
#
# They used to be one: config.shared_models_dir() downloaded catalogue models
# into /mnt/nvme/model, which services/local_models.py scans and lists as
# custom -- so every fetched model appeared twice, once in Default Model where
# it belongs and again under Custom Models as an unlabelled drop-in. This pins
# the split, and pins that neither root stops resolving models already on disk.
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "systems" / "demo"))

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


tmp = tempfile.mkdtemp()
builtin = os.path.join(tmp, "builtin-models")
nvme = os.path.join(tmp, "nvme")
drop = os.path.join(nvme, "model")
os.makedirs(builtin)
os.makedirs(drop)
os.environ["CYTRON_BUILTIN_MODEL_DIR"] = builtin
os.environ["CYTRON_NVME"] = nvme
os.environ["CYTRON_STORE"] = os.path.join(tmp, "store")

from core import config                                      # noqa: E402

print("== the two roots are not the same directory")
check("a built-in dir is offered when it exists",
      str(config.builtin_models_dir()) == builtin,
      str(config.builtin_models_dir()))
check("downloads go to the built-in dir, not the drop folder",
      str(config.shared_models_dir()) == builtin,
      str(config.shared_models_dir()))
check("the drop folder is NOT the download target",
      str(config.shared_models_dir()) != drop)

print("== but nothing already fetched stops resolving")
roots = [os.path.normpath(r) for r in config.model_search_roots()]
check("the built-in dir is searched", os.path.normpath(builtin) in roots)
check("the NVMe drop folder is still searched", os.path.normpath(drop) in roots,
      str(roots))

print("== a box with no built-in dir falls back to the NVMe")
# The installer makes /opt/irivai-models; a developer checkout has none, and
# must not have one conjured under /opt on first import.
os.environ.pop("CYTRON_BUILTIN_MODEL_DIR")
config.BUILTIN_MODEL_DIR = os.path.join(tmp, "does-not-exist")
check("an absent built-in dir is None, not created",
      config.builtin_models_dir() is None
      and not os.path.exists(config.BUILTIN_MODEL_DIR))

print("== the app resolves both roots too")
os.environ["CYTRON_BUILTIN_MODEL_DIR"] = builtin
for name in list(sys.modules):
    if name.startswith("app."):
        del sys.modules[name]
from app import config as app_config                         # noqa: E402

app_config.BUILTIN_MODEL_DIR = builtin
app_config.NVME_ROOT = nvme
app_config.NVME_MODEL_DIR = drop
app_roots = [os.path.normpath(r) for r in app_config.model_roots()]
check("the app searches the built-in dir",
      os.path.normpath(builtin) in app_roots, str(app_roots))
check("the app still searches the drop folder",
      os.path.normpath(drop) in app_roots, str(app_roots))
check("built-in is preferred over the drop folder",
      app_roots.index(os.path.normpath(builtin))
      < app_roots.index(os.path.normpath(drop)))

print("== the custom-model scanner never sees the built-in dir")
src = (ROOT / "core" / "services" / "local_models.py").read_text(encoding="utf-8")
check("local_models scans the drop folder, not /opt/irivai-models",
      "irivai-models" not in src)

print("== the installer creates it, owned by the service account")
sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
check("install.sh makes /opt/irivai-models",
      'install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" /opt/irivai-models' in sh)
check("and it lives OUTSIDE the release directory (an update would prune it)",
      "/opt/irivai-releases/irivai-models" not in sh)

# Not about model directories, but the same install step and the same class of
# silent failure: a --system account is in no supplementary groups, so every
# /dev/video* open is EACCES -- and detector_control.list_cameras() LISTS a
# node it cannot query rather than hiding it, so "no permission" showed up as
# nineteen cameras in the picker and no real camera could be opened at all.
print("== and gives the service account the hardware groups")
for grp in ("video", "render", "gpio", "i2c", "spi", "dialout"):
    check("install.sh adds %s" % grp,
          'for grp in video render gpio i2c spi dialout plugdev' in sh
          and 'usermod -aG "$grp" "$SERVICE_USER"' in sh)
    break                                   # one loop covers them all
check("HailoRT gets a home to write its cache into",
      'install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" "/home/${SERVICE_USER}"' in sh)

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
