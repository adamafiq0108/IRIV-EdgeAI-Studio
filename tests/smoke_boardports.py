# The faceplate connector panels: USB sockets and ethernet jacks with live
# state, one payload (/api/board/ports), and a STANDARDISED camera-setup row
# -- every mode is picker + icon rescan + icon Ports, after an audit found
# three different shapes (icon here, text there, a button on its own row).
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import board_io                            # noqa: E402
from core.services import detector_control as dc              # noqa: E402

# 1. eth_ports answers on any host: a box with no eth* gives an empty list,
#    never a crash -- and every entry carries the full field set so the UI
#    can tell "unknown" from "down".
ports = board_io.eth_ports()
assert isinstance(ports, list)
for p in ports:
    assert {"name", "up", "carrier", "speed_mbps", "mac", "ip"} <= set(p)
print("eth_ports:", [(p["name"], p["up"]) for p in ports] or "none on this host")

# 2. usb_ports never crashes without udev slots or a systems dir, and every
#    row says whether the socket is occupied and who captures it
usb = dc.usb_ports(None)
assert isinstance(usb, list)
for p in usb:
    assert {"slot", "present", "name", "device", "used_by"} <= set(p)
print("usb_ports rows:", len(usb))

# 3. the route exists and is scoped to a system (auth + master context)
with open(os.path.join(ROOT, "core", "routes", "models.py"),
          encoding="utf-8") as f:
    routes_src = f.read()
assert '@router.get("/api/board/ports"' in routes_src
block = routes_src.split('"/api/board/ports"', 1)[1][:900]
assert "_system_context" in block
assert "usb_ports" in block and "eth_ports" in block

# 4. the camera dialog: one row shape for every mode. No text scan buttons
#    left, RefreshCw for every rescan, Cable for every Ports toggle.
with open(os.path.join(ROOT, "frontend", "src", "pages", "Detection.jsx"),
          encoding="utf-8") as f:
    page = f.read()
for stale in (">Rescan network<", ">Scan network<", '"Scanning…"',
              'title="Rescan">↻', 'title="Refresh">↻'):
    assert stale not in page, f"unstandardised scan button survives: {stale}"
assert page.count("<RefreshCw size={14}") >= 4, \
    "every mode's rescan must be the same icon button"
assert page.count("<Cable size={14}") >= 3, \
    "usb, rtsp and gige each need a Ports toggle"
# ...and the right panel per bus: sockets for USB, jacks for the network modes
assert "UsbPorts usb={boardPorts.data?.usb}" in page
assert page.count("EthPorts eth={boardPorts.data?.eth}") >= 2

# 5. the ports panels are drawn as the faceplate and poll only while shown
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "BoardPorts.jsx"), encoding="utf-8") as f:
    comp = f.read()
assert "iriv-usb-sock" in comp and "iriv-eth-jack" in comp
assert "authHeaders" in comp, "the poll must carry this tab's session"
assert "if (!active) return undefined" in comp, \
    "a closed panel must not keep polling the board"

# 6. the hardware footer offers the whole faceplate: eth, usb, terminals
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "HardwareFooter.jsx"), encoding="utf-8") as f:
    foot = f.read()
for part in ("EthPorts", "UsbPorts", "TerminalBlock", "hw-ports-pop"):
    assert part in foot, f"footer ports panel lost {part}"
assert "useBoardPorts(portsOpen)" in foot, "footer must poll only while open"

# 7. the CSS behind the visuals exists, connector and LED states included
with open(os.path.join(ROOT, "frontend", "src", "theme.css"),
          encoding="utf-8") as f:
    css = f.read()
for cls in (".iriv-usb-sock", ".iriv-eth-jack", ".iriv-eth-led",
            ".hw-ports-pop", ".iriv-usb-cap", ".iriv-eth-cap"):
    assert cls in css, f"{cls} has no styling"
for state in ("is-live", "is-empty", "is-green", "is-amber"):
    assert re.search(rf"\.iriv-(usb-sock|eth-jack|eth-led)\.{state}", css), \
        f"connector state .{state} has no styling"

print("smoke_boardports OK")
