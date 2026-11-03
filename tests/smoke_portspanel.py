# The terminal block: the board's screw terminals, drawn as they are printed.
# It exists to answer "does anything happen at the terminal?" -- so the claims
# worth pinning are that its layout matches the real silkscreen, that it reads
# live state rather than inventing it, and that it stops polling when it is not
# being looked at.
#
# It used to be a drawer inside the Workflow editor as well. That was a second
# view of the board sitting inside the graph editor: two places to look, two to
# keep in step, and the terminals already have a page of their own. The block
# itself is unchanged and still drawn in the hardware footer.
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import board_io                            # noqa: E402

FE = os.path.join(ROOT, "frontend", "src")
with open(os.path.join(FE, "workflow", "TerminalBlock.jsx"), encoding="utf-8") as f:
    block = f.read()
with open(os.path.join(FE, "components", "HardwareFooter.jsx"), encoding="utf-8") as f:
    footer = f.read()
with open(os.path.join(FE, "pages", "Workflow.jsx"), encoding="utf-8") as f:
    page = f.read()
with open(os.path.join(FE, "theme.css"), encoding="utf-8") as f:
    css = f.read()

# 1. every port the BOARD has appears on the drawn block -- a terminal the
#    hardware exposes but the picture omits is one an installer cannot check
drawn = set(re.findall(r'port:\s*"([A-Z0-9]+)"', block))
have = {p["id"] for p in board_io.BOARD_PORTS}
# BUZZER and BUTTON are on the board but not on the screw terminals: they are
# fitted parts, with no terminal to wire, so they are deliberately not drawn.
expected = have - {"BUZZER", "BUTTON"}
assert drawn == expected, f"drawn {sorted(drawn)} != terminals {sorted(expected)}"
print("terminals drawn:", sorted(drawn))

# 2. the layout is the hardware's: two rows of eight
top = re.search(r"const TOP = \[(.*?)\];", block, re.S).group(1)
bottom = re.search(r"const BOTTOM = \[(.*?)\];", block, re.S).group(1)
assert top.count("{ text:") == 8, "top row must have 8 terminals"
assert bottom.count("{ text:") == 8, "bottom row must have 8 terminals"
# ...and the block renders both rows into one connector body
assert "TOP.map((pin, i) => socket(pin, i, false))" in block
assert "BOTTOM.map((pin, i) => socket(pin, i, true))" in block

# 3. state comes from the board, not from the graph
assert "/api/board/io" in block, "the panel must read live board state"
assert "authHeaders" in block, "the poll must carry this tab's session"
assert "p.busy" in block, "a line held by another program must be shown"
assert "p.value === 1" in block, "an input must show its measured level"

# 4. it stops polling when nothing is looking at it
assert "poll={portsOpen}" in footer, \
    "the block must only poll while its panel is open"
assert "poll" in block, "the block must honour a poll flag"

# 5. and it is NOT a second board view inside the graph editor
assert "PortsPanel" not in page and "setPortsOpen" not in page, \
    "the Workflow page carries a ports drawer again -- the terminals have a " \
    "page of their own, and two views of them is two things to keep in step"
assert not os.path.exists(os.path.join(FE, "workflow", "PortsPanel.jsx")), \
    "PortsPanel.jsx is back and nothing renders it"
# Tidy up took the permanent place the Ports button held: it is one of the two
# things done to a GRAPH rather than to a node, and it was behind the overflow.
assert re.search(r"setDebugOpen\(\(v\) => !v\)\}>Debug</button>", page)
assert page.index("Debug</button>") < page.index("\n              Tidy up\n"), \
    "Tidy up must sit beside Debug, not back inside the overflow menu"

# 6. the styling exists for every state the component asks for
for cls in (".iriv-term-socket", ".iriv-term-staple", ".iriv-term-block",
            ".iriv-term-vertical", ".iriv-term-tick"):
    assert cls in css, f"{cls} has no styling"
# Each socket STATE must be styled somewhere on a socket selector. Matched
# loosely on purpose: is-active is tone-qualified (.is-do0.is-active) so the
# lit colour matches the group, and pinning the exact selector here would
# make a palette change look like a broken test.
socket_rules = [ln for ln in css.splitlines() if ".iriv-term-socket" in ln]
for state in ("is-active", "is-busy", "is-used"):
    assert any(state in ln for ln in socket_rules), \
        f"socket state .{state} has no styling"

# The block is laid out as a BAR wherever it is drawn: the connector is one
# long row of terminals, and a narrow column had to stack and clip it.
assert "<TerminalBlock" in footer, "the hardware footer must draw the block"
assert ".iriv-term-block" in css, "the block must still be styled as a bar"

# -- both sockets are always drawn, and named on Camera Setup ------------------
# Rendering only the slots the payload names drew ONE socket the moment a
# camera was pinned to socket 0, on a board that has two -- and an empty socket
# is the answer to "where does the second camera go".
def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


bp = _read("frontend", "src", "components", "BoardPorts.jsx")
assert "const shown = [0, 1].map((slot) => by.get(slot) || { slot })" in bp,     "both sockets must render whatever the payload names"
assert '"is-plugged"' in bp,     "a camera plugged in but unused needs its own state, not the bare-socket look"
assert ".iriv-usb-sock.is-plugged" in _read("frontend", "src", "theme.css"),     "is-plugged must be styled or it renders identically to an empty socket"

# Names are OPT-IN. On Camera Setup "which socket is this camera in" is the
# question being asked; in the footer the same sentences pushed the panel wide
# enough to need a scrollbar, which is why they became tooltips.
assert "labels = false" in bp, "labels must default off"
assert "<UsbPorts usb={boardPorts.data?.usb} labels />" in     _read("frontend", "src", "pages", "Detection.jsx"),     "Camera Setup must ask for the socket labels"
assert "<UsbPorts usb={boardPorts.data?.usb} />" in     _read("frontend", "src", "components", "HardwareFooter.jsx"),     "the hardware footer must NOT carry the labels"

# Without the udev rules there are no /dev/cam-usb<slot> symlinks, so
# usb_ports() pins nothing and the map draws two empty sockets on a board with
# a camera in one of them.
sh = _read("scripts", "install.sh")
assert "83-cytron-cam-slots.rules" in sh and "udevadm control --reload" in sh,     "install.sh must install the camera slot rules and reload udev"


# The blue tongue is the READOUT, not decoration: two sockets both painted
# blue said "USB 3.0" and nothing about what is plugged in.
css = _read("frontend", "src", "theme.css")
assert "background: linear-gradient(180deg, #4a505b, #343941);" in css, (
    "an empty socket's tongue must be grey, not blue")
assert (".iriv-usb-sock.is-plugged::after," in css
        and ".iriv-usb-sock.is-live::after" in css), (
    "only an occupied socket turns blue")

print("smoke_portspanel OK")
