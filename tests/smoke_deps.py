# What the product SHELLS OUT TO has to be installed by the installer.
#
# None of it can come from pip: v4l2-ctl and nmcli are programs, the GStreamer
# elements are plugins, and OpenCV / PyGObject / hailo_platform are the
# distro's builds against the distro's libraries. So they were installed by
# hand, board by board -- and a board installed with scripts/install.sh came up
# with no camera mode list, no WebRTC publish, no GigE camera and no Network
# page, each one degrading quietly.
#
# This test is the list, checked against the code that needs it: a tool the
# product runs, with no package in install.sh, is the bug -- caught here rather
# than on a customer's bench.
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = open(os.path.join(ROOT, "scripts", "install.sh"),
               encoding="utf-8").read()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- the list itself -----------------------------------------------------------
block = re.search(r"APT_PACKAGES=\((.*?)\n\)", INSTALL, re.S)
check("install.sh declares APT_PACKAGES", bool(block))
# Comments inside the array are bash comments, not package names.
packages = set(" ".join(ln.split("#", 1)[0] for ln in
                        (block.group(1) if block else "").splitlines()).split())

# Each entry: the package, and WHY -- the file that stops working without it.
NEEDED = [
    ("python3-opencv", "systems/demo/app/camera.py reads frames with cv2"),
    ("python3-numpy", "what OpenCV hands back"),
    ("v4l-utils", "core/services/detector_control.py runs v4l2-ctl"),
    ("ffmpeg", "ffprobe -- the same questions asked of an RTSP camera"),
    ("python3-gi", "systems/demo/app/publisher.py imports gi"),
    ("gstreamer1.0-tools", "publisher.py looks for gst-launch-1.0"),
    ("gstreamer1.0-plugins-base", "appsrc, videoconvert"),
    ("gstreamer1.0-plugins-bad", "openh264enc -- a CM5 has no hardware encoder"),
    ("gstreamer1.0-rtsp", "rtspclientsink -- the element that PUBLISHES"),
    ("libaravis-0.8-0", "aravissrc -- opens a GigE Vision camera"),
    ("aravis-tools-cli", "arv-tool -- names the cameras Aravis can open"),
    ("network-manager", "core/services/netconf.py runs nmcli"),
    ("i2c-tools", "the board's I2C header"),
    ("python3-smbus2", "core/services/board_io.py talks to the OLED"),
    ("iputils-arping", "ipconflict.py / panel_ipcheck.py: arping -D, the IP conflict test"),
    ("nftables", "the hotspot isolation and Modbus 502 units, web-redirect.py"),
    ("avahi-utils", "discovery_mdns.py advertises the box, hostname.py resolves it"),
]
for pkg, why in NEEDED:
    check(f"install.sh installs {pkg} ({why})", pkg in packages)

# -- and the tools those packages carry are really the ones the code runs ------
# Read from ALL the product's Python (what ships: core/, the built-in app,
# the agent, scripts/ but not scripts/dev), so a NEW external tool with no
# package fails here. Every tool is mapped: a package APT_PACKAGES must hold,
# or a string saying where else it comes from.
ELSEWHERE = "installed by its own step in install.sh"
BASE = "base system (Debian essential/required, or systemd)"
TOOL_PACKAGE = {
    "v4l2-ctl": "v4l-utils",
    "nmcli": "network-manager",
    "gst-launch-1.0": "gstreamer1.0-tools",
    "gst-inspect-1.0": "gstreamer1.0-tools",
    "arv-tool-0.8": "aravis-tools-cli",
    "ffprobe": "ffmpeg",
    "ffmpeg": "ffmpeg",
    "arping": "iputils-arping",
    "ping": "iputils-ping",
    "iw": "iw",
    "gpioget": "gpiod",
    "gpioinfo": "gpiod",
    "i2cdetect": "i2c-tools",
    "lspci": "pciutils",
    "openssl": "openssl",
    "sfdisk": "fdisk",
    "avahi-browse": "avahi-utils",
    "avahi-publish-service": "avahi-utils",
    "avahi-resolve": "avahi-utils",
    "nft": "nftables",
    "lsof": "lsof",
    "hailortcli": ELSEWHERE + " (hailo-all, where the archive has it)",
    "mosquitto": ELSEWHERE + " (7e; the hub installs a missing broker too)",
    "mosquitto_passwd": ELSEWHERE + " (7e, with mosquitto)",
    "apt-get": "the installer's own host (an apt host)",
    "dpkg-query": "the installer's own host (an apt host)",
    "dpkg": "the installer's own host (an apt host)",
    # The Raspberry Pi OS image, not a Debian package name the installer can
    # ask for on every image; board_live.py and hostinfo.py fall back.
    "pinctrl": "Raspberry Pi OS image",
    "vcgencmd": "Raspberry Pi OS image",
    # Falls back to /etc/os-release (hostinfo.codename).
    "lsb_release": "optional (hostinfo falls back)",
    # The Windows dev box only (provision.py, runtime.py branch on os.name).
    "taskkill": "Windows only", "tasklist": "Windows only", "netstat": "Windows only",
}
for tool in ("systemctl", "journalctl", "hostnamectl", "udevadm", "sudo", "visudo", "ip",
             "ss", "lsblk", "umount", "runuser", "wipefs", "blkid", "mkfs.ext4", "getconf",
             "ionice", "nice", "kill", "hostname", "pgrep", "mount"):
    TOOL_PACKAGE[tool] = BASE

PY_SOURCES = [p for d in ("core", os.path.join("systems", "demo", "app"), "agent")
              for p in glob.glob(os.path.join(ROOT, d, "**", "*.py"), recursive=True)
              if os.sep + "dev" + os.sep not in p[len(ROOT):]]
PY_SOURCES += glob.glob(os.path.join(ROOT, "scripts", "*.py"))
PY_SOURCES += [os.path.join(ROOT, "hub_server.py"), os.path.join(ROOT, "master_server.py")]
CALL = re.compile(
    r'(?:\brun|_run|Popen|check_output|check_call)\(\s*\[\s*"([A-Za-z0-9_.\-/]+)"'
    r'|_must\(run,\s*\[\s*"([A-Za-z0-9_.\-/]+)"'
    r'|which\(\s*"([A-Za-z0-9_.\-]+)"'
    r'|\b(?:argv|cmd)\s*=\s*\[\s*"([A-Za-z0-9_.\-/]+)"'
    r'|return \[\s*"([a-z][A-Za-z0-9_.\-]+)",')
called = {}
for path in PY_SOURCES:
    with open(path, encoding="utf-8", errors="replace") as f:
        for m in CALL.finditer(f.read()):
            name = next(g for g in m.groups() if g).rsplit("/", 1)[-1]
            if not name.startswith("ipv"):          # nmcli setting names, not programs
                called.setdefault(name, os.path.relpath(path, ROOT))
check("the scan finds the tools it should (it reads the code, not a list)",
      {"arping", "nmcli", "ffprobe", "sfdisk", "lspci", "gpioinfo"} <= set(called),
      sorted(called))
for tool in sorted(called):
    if tool not in TOOL_PACKAGE:
        check(f"{tool} is a known external tool", False,
              f"{called[tool]} runs it -- add it (and its package) to TOOL_PACKAGE here "
              "and to APT_PACKAGES in scripts/install.sh")
        continue
    pkg = TOOL_PACKAGE[tool]
    if " " not in pkg:
        check(f"the package carrying {tool} is installed ({pkg})", pkg in packages)
check("hailortcli and the broker come from their own install steps",
      "apt-get install -y hailo-all" in INSTALL and "apt-get install -y mosquitto" in INSTALL)
check("on a Hailo-8/8L the sharing service is switched on (multi_process is their default)",
      "systemctl enable --now hailort.service" in INSTALL
      and "detect_arch_uncached()" in INSTALL and "hailo8|hailo8l)" in INSTALL)
check("...and every package an update adds to a box is one a fresh install has",
      set(json.loads(read("scripts", "root_items.json"))["upgrade"]["packages"]) <= packages)

# -- the venv has to be able to SEE them ---------------------------------------
# There is no pip wheel for hailo_platform at all, and an isolated venv cannot
# see the distro's OpenCV or PyGObject either.
check("the venv is created with --system-site-packages",
      "python3 -m venv --system-site-packages" in INSTALL)
check("...and an older isolated venv is repaired rather than left broken",
      "include-system-site-packages" in INSTALL)

# -- the gateway is installed, not left as a step in a document ----------------
check("install.sh installs the video gateway",
      "scripts/mediamtx/install.sh" in INSTALL)
check("...without a failed download failing the whole install",
      re.search(r"mediamtx/install\.sh\"?\s*\\\n\s*\|\| warn", INSTALL)
      is not None)

# -- the NPU runtime -----------------------------------------------------------
check("hailo-all is installed where it exists", "hailo-all" in INSTALL)
check("...and its absence is a warning, not a failed install",
      "detection runs on CPU" in INSTALL)

# -- "can this box open a GigE camera" is asked of the PLUGIN -------------------
# The pipeline opens a camera with aravissrc; arv-tool only lists them, and on
# Debian 13 it is in a package of its own that nothing depends on. Judging
# "openable" by the tool called a working box broken.
dc = read("core", "services", "detector_control.py")
check("the GigE plugin check asks gst-inspect for aravissrc",
      'gst-inspect-1.0", "aravissrc"' in dc)
check("...and `opens` is decided by that, not by arv-tool",
      "bool(plugin and reachable)" in dc)
# Aravis names a device <vendor>-<model>-<serial> -- verified against arv-tool
# on a Hikrobot MV-CS050-60GC. A name built any other way cannot be opened.
check("a scanned camera is named the way Aravis names it",
      re.search(r'cam\["vendor"\],\s*\n\s*cam\["model"\],\s*\n\s*cam\["serial"\]',
                dc) is not None)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
