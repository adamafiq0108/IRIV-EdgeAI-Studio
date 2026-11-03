# Beta-tester wording fixes, pinned so they do not drift back.
#
#   5g  the Events Log subtitle: "<n>+ events recorded · See what the cameras
#       captured, stored according to your retention settings" -- "match"
#       instead of "recorded" while a filter or time window is set
#   7   no hard-coded "SD card" / "microSD" in anything a person reads: the
#       pages, notices, log messages, the installer. The root disk's label is
#       read from the DEVICE (host_health.root_disk_label: eMMC, SD card or
#       System disk) -- the one place allowed to say "SD card", marked
#       `disk-label-ok`. (Config keys and paths are not renamed; comments
#       may say either.)
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


ev = read("frontend", "src", "pages", "EventTable.jsx")
check("5g the Events Log counts events, with the count kept",
      '{total}{capped ? "+" : ""} event{total === 1 ? "" : "s"}' in ev)
check("5g ...RECORDED with no filter, MATCH once a filter or time window is set",
      '{dirty ? " match" : " recorded"}' in ev
      and "const dirty = Object.keys(EMPTY_FILTERS)" in ev
      and 'start: "", end: ""' in ev)
check("5g ...and says what they are in the new words",
      '" · See what the cameras captured, stored according to your "\n'
      '             + "retention settings"' in ev.replace("\r\n", "\n"))
check("5g the old sentence is gone",
      "kept for as long as retention says" not in ev)

SD = re.compile(r"sd[ -]?card|micro[ -]?sd", re.I)
# Every quoted string in the Python a person can see the output of, and every
# line of the pages (comments included -- the frontend has none left).
QUOTED = re.compile(r'"[^"]*"' + "|" + r"'[^']*'")
hits, allowed = [], []
for top in ("core", os.path.join("systems", "demo", "app"), "scripts"):
    for dirpath, _d, names in os.walk(os.path.join(ROOT, top)):
        if "static" in dirpath.split(os.sep):
            continue
        for n in names:
            if not n.endswith((".py", ".sh")):
                continue
            path = os.path.join(dirpath, n)
            for i, line in enumerate(io.open(path, encoding="utf-8",
                                             errors="replace"), 1):
                code = line.split("#", 1)[0] if n.endswith(".py") else line
                if n.endswith(".sh") and line.lstrip().startswith("#"):
                    continue
                if any(SD.search(q) for q in QUOTED.findall(code)):
                    if "disk-label-ok" in line:
                        allowed.append(os.path.relpath(path, ROOT))
                        continue
                    hits.append(f"{os.path.relpath(path, ROOT)}:{i}")
for dirpath, _d, names in os.walk(os.path.join(ROOT, "frontend", "src")):
    if "reui" in dirpath.split(os.sep):
        continue
    for n in names:
        if n.endswith((".jsx", ".js")):
            path = os.path.join(dirpath, n)
            for i, line in enumerate(io.open(path, encoding="utf-8"), 1):
                if SD.search(line):
                    hits.append(f"{os.path.relpath(path, ROOT)}:{i}")
check("7 no 'SD card' / 'microSD' in anything a person reads (eMMC)",
      not hits, hits)
check("7 ...the device-detected label is the only exception",
      allowed == [os.path.join("core", "services", "host_health.py")], allowed)
check("7 the storage row for / is labelled from the device",
      '"label": (root_disk_label(part.device) if mp == "/"'
      in read("core", "services", "host_health.py"))

sys.path.insert(0, ROOT)
import tempfile                                                 # noqa: E402
from core.services import host_health                          # noqa: E402


def board(kind=None, extra=()):
    """A fake /sys and /dev for one MMC disk."""
    root = tempfile.mkdtemp()
    sysd, devd = os.path.join(root, "sys"), os.path.join(root, "dev")
    os.makedirs(os.path.join(sysd, "block", "mmcblk0", "device"))
    os.makedirs(devd)
    if kind:
        with open(os.path.join(sysd, "block", "mmcblk0", "device", "type"),
                  "w", encoding="utf-8") as f:
            f.write(kind)
    for name in extra:
        open(os.path.join(devd, name), "w").close()
    return sysd, devd


lbl = host_health.root_disk_label
check("7 an MMC card with boot partitions is the eMMC",
      lbl("/dev/mmcblk0p2", *board(None, ("mmcblk0boot0", "mmcblk0boot1",
                                           "mmcblk0rpmb"))) == "eMMC")
check("7 ...sysfs type MMC alone says so too",
      lbl("/dev/mmcblk0p2", *board("MMC")) == "eMMC")
check("7 an SD card is called an SD card",
      lbl("/dev/mmcblk0p2", *board("SD")) == "SD card")
check("7 an NVMe or USB root, or an unreadable one, is a 'System disk'",
      lbl("/dev/nvme0n1p2", *board()) == "System disk"
      and lbl("/dev/sda2", *board()) == "System disk"
      and lbl("/dev/mmcblk0p2", *board()) == "System disk")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all beta wording checks passed")
