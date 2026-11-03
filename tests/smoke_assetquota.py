"""Image card uploads have a per-system ceiling (Pipi N-4, v1.0.39).

Without one, anyone allowed to edit a dashboard could fill the disk 2 MB at a
time. builder_assets.save now:

  1. counts the system's pictures from the files really on disk
  2. past SOFT_FILES / SOFT_TOTAL_BYTES first sweeps pictures no layout names
     (older than the grace, like a layout save does), keeping every named one
  3. refuses past MAX_FILES / MAX_TOTAL_BYTES with QuotaError and the plain
     sentence, which the route answers as 413
  4. other systems are not affected

    py -3 tests/smoke_assetquota.py
"""
import os
import struct
import sys
import tempfile
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder_assets as ba                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


def png():
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))


ba.MAX_FILES, ba.SOFT_FILES = 5, 3
PIC = png()
ids = [ba.save("sysa", PIC)["id"] for _ in range(5)]
check("pictures up to the cap are stored", ba.usage("sysa")[0] == 5)
try:
    ba.save("sysa", PIC)
    msg = ""
except ba.QuotaError as exc:
    msg = str(exc)
check("past the cap: refused with the plain sentence",
      msg == "This system has reached its picture limit. Remove an unused picture first.", msg)
check("...another system is not affected", bool(ba.save("sysb", PIC)["id"]))

# Over the soft line, an upload sweeps pictures nothing names (old enough).
old = time.time() - ba.ORPHAN_GRACE_S - 10
for aid in ids[2:]:
    path = ba._find("sysa", aid)
    os.utime(path, (old, old))
layout = {"pages": [{"widgets": [{"kind": "image_card", "asset": ids[2]}]}]}
got = ba.save("sysa", PIC, layout=lambda: layout)
left = {n.split(".")[0] for n in os.listdir(ba.asset_dir("sysa"))}
check("over the soft line, unnamed old pictures are swept first and the upload fits",
      got["id"] in left and ids[2] in left and ids[3] not in left and ids[4] not in left
      and ids[0] in left and ids[1] in left, sorted(left))

ba.MAX_FILES, ba.SOFT_FILES = 50, 40
ba.MAX_TOTAL_BYTES = len(PIC) * 8
try:
    for _ in range(10):
        ba.save("sysc", PIC)
    total_msg = ""
except ba.QuotaError as exc:
    total_msg = str(exc)
check("the total size is capped too", "picture limit" in total_msg
      and ba.usage("sysc")[1] <= ba.MAX_TOTAL_BYTES, ba.usage("sysc"))

route = open(os.path.join(ROOT, "core", "routes", "builder.py"), encoding="utf-8").read()
up = route.split('name="builder.asset_upload"')[1].split("@router")[0]
check("the route answers a full store with 413 and passes the saved layout for the sweep",
      "except builder_assets.QuotaError as e:" in up and "status_code=413" in up
      and "builder_service.load(config_dir, slug)" in up)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
