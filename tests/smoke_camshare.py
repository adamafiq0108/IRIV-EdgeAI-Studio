# Two systems, one camera: the second consumes the first one's /raw feed
# instead of fighting for the device. That only works if the OWNER is in the
# registry -- and ownership was a flag set once, at the end of one open path.
#
# Seen on a box running two systems on one USB camera: the owner held
# /dev/video2, served /raw on its stream port perfectly, and the registry held
# no entry for it at all. The second system then retried the busy device for
# ever ("gave no frames after 3 reconnect attempts") against a camera that was
# working in the next tab, and nothing healed it: the heal itself was guarded
# by the same flag that had never been set.
#
# Ownership is now asked of the camera's STATE -- open, not consuming, has a
# stream port -- so an owner that never registered says so on its next
# heartbeat, whatever path opened it.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import camera as cm                                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# A registry of this test's own, so a real app on the dev box is untouched.
cm._CAM_REGISTRY = os.path.join(tempfile.mkdtemp(), "cameras.json")


def camera(source="0", port=5237, cap=object(), consuming=None):
    c = cm.Camera({"id": "cam1", "source": source, "_stream_port": port})
    c._cap = cap                    # "opened", without a device to open
    c._consuming = consuming
    return c


# -- an owner that never registered registers itself ------------------------
cam = camera()
check("a camera holding a device owns it", cam.owns_device())
check("...and the registry does not know yet",
      str(cam.source) not in cm._reg_load())
cam.reaffirm()
entry = cm._reg_load().get("0") or {}
check("one heartbeat is enough to say so",
      entry.get("pid") == os.getpid() and entry.get("port") == 5237, str(entry))
check("...and it names WHICH camera, for a system running several",
      entry.get("camera") == "cam1", str(entry))

# -- and a clobbered entry is still healed (what this used to do) -----------
cm._reg_update(lambda reg: reg.pop("0", None))
cam.reaffirm()
check("an entry another app clobbered comes back",
      (cm._reg_load().get("0") or {}).get("pid") == os.getpid())

# -- but a consumer must never claim the device it is BORROWING -------------
# It holds no device: registering would point every other app at a feed that
# is itself a copy, and the real owner's entry would be overwritten.
cm._reg_update(lambda reg: reg.pop("0", None))
borrower = camera(consuming="http://127.0.0.1:5226/raw")
check("a camera consuming a shared feed owns nothing",
      not borrower.owns_device())
borrower.reaffirm()
check("...and registers nothing", "0" not in cm._reg_load())

# -- nor does a camera that is not open, or one with nowhere to serve from --
check("a closed camera owns nothing", not camera(cap=None).owns_device())
check("...and one with no stream port has nothing to offer",
      not camera(port=0).owns_device())
# A file is not shareable: it is not a device anyone can be locked out of, and
# every app can open its own reader.
check("a file source is not registered",
      not camera(source="file:clip.mp4").owns_device())

# -- the failure message names the holder -----------------------------------
src = open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
           encoding="utf-8").read()
check("a busy device says which system has it",
      "is held by another " in src and "shared feed did not answer" in src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
