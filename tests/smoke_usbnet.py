# The eth1 port: jack LEDs, and an address that survives a reboot.
#
# eth1 is a Realtek RTL8153 on USB, not a Pi port. Two facts about it are load
# bearing and neither is visible from the dashboard:
#
#   * LEDSEL 0xdd90 ships as 0x0ca9 -- one LED per speed -- and the jack's two
#     LEDs are the 10M and 100M ones, so a GIGABIT link leaves the port dark.
#   * its MAC EEPROM is blank, so the kernel invents an address every boot and
#     the box moves to a new DHCP lease. A board at .173 became .10 because
#     somebody moved a cable between the two ports.
#
# The register maths is what this pins hardest: a wrong byte-enable writes a
# neighbouring register on a live NIC, and the failure would arrive as a dead
# link rather than as a dark LED.
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The helper is Linux-only (fcntl, usbfs), and this test runs on the
# development laptop as well. Nothing here issues an ioctl -- the control
# transfers are recorded, not sent -- so a stub is enough to import it.
if sys.platform != "linux":
    import types
    _stub = types.ModuleType("fcntl")
    _stub.ioctl = lambda *a, **k: 0
    sys.modules.setdefault("fcntl", _stub)

spec = importlib.util.spec_from_file_location(
    "usbnet_setup", os.path.join(ROOT, "scripts", "usbnet_setup.py"))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

FAIL = []


def check(what, ok, why=""):
    if not ok:
        FAIL.append(what + ((": " + why) if why else ""))
    print(("  ok  " if ok else "  FAIL") + "  " + what)


# -- the control transfer, byte for byte ---------------------------------------
#
# Recorded rather than sent: the point is that what we build matches what the
# r8152 driver builds for the same register, since that is the only evidence
# available off a board.
sent = []


class _Fd:
    pass


def _record(fd, request_type, value, index, buf):
    sent.append((request_type, value, index, bytes(bytearray(buf))))


real_xfer = u._xfer
u._xfer = _record

u.write_word(_Fd(), u.PLA_LEDSEL, u.LEDSEL_ALL)
rt, value, index, payload = sent[-1]
check("a register write is a vendor write to the device", rt == 0x40)
check("...at the DWORD-aligned address, which is how the chip is addressed",
      value == 0xDD90 and value % 4 == 0)
check("...tagged PLA, or it lands in the USB register file instead",
      index & 0xFF00 == 0x0100)
check("...with the WORD byte enables, so 0xdd92 (LED_FEATURE) is untouched",
      index & 0xFF == 0x33, hex(index))
check("...carrying the value little-endian", payload == bytes([0xFF, 0x0F, 0, 0]),
      repr(payload))

# An odd-word address shifts both the value and the enables, or it would write
# the wrong half of the dword.
sent.clear()
u.write_word(_Fd(), 0xDD92, 0x1234)
rt, value, index, payload = sent[-1]
check("a write to the high word aligns down and shifts its enables",
      value == 0xDD90 and index & 0xFF == 0xCC, hex(index))
check("...and shifts the value with them",
      payload == bytes([0, 0, 0x34, 0x12]), repr(payload))

u._xfer = real_xfer

check("the LED value lights every LED at every speed, not just at gigabit",
      u.LEDSEL_ALL == 0x0FFF,
      "0x0ca9 is the factory value that leaves a gigabit link dark")

# -- the address ---------------------------------------------------------------
mac = u.stable_mac("100000001234abcd", "eth1")
check("the same board and port always get the same address",
      mac == u.stable_mac("100000001234abcd", "eth1"))
check("a different board gets a different one",
      mac != u.stable_mac("100000009999ffff", "eth1"))
check("so does a second adapter on the same board",
      mac != u.stable_mac("100000001234abcd", "eth2"))
first = int(mac.split(":")[0], 16)
check("the address is locally administered, since we made it up",
      first & 0x02 == 0x02, mac)
check("...and unicast, or the kernel refuses it outright",
      first & 0x01 == 0, mac)

# -- and never at the cost of the link -----------------------------------------
#
# Setting an address needs the interface down. Bouncing eth1 to pin its MAC
# would drop the link the installer may be running over, to fix something that
# only matters at the next boot anyway.
src = io.open(os.path.join(ROOT, "scripts", "usbnet_setup.py"),
              encoding="utf-8").read()
check("a live interface is left alone", "takes effect at the next boot" in src)
check("...and the LED write still happens, because it is not disruptive",
      src.index("write_word(fd, PLA_LEDSEL") < src.index("serial = board_serial()"))

# -- shipped, and run at the right moment --------------------------------------
rules = io.open(os.path.join(ROOT, "scripts", "udev", "84-irivai-usbnet.rules"),
                encoding="utf-8").read()
check('the rule fires on the NET device, so the driver has already bound',
      'SUBSYSTEM=="net"' in rules and 'ACTION=="add"' in rules)
check("...for Realtek USB adapters", 'ATTRS{idVendor}=="0bda"' in rules)
check("...and passes the interface name the helper needs",
      "$env{INTERFACE}" in rules)

install = io.open(os.path.join(ROOT, "scripts", "install.sh"),
                  encoding="utf-8").read()
check("install.sh installs the helper root-owned, outside every release tree",
      "/usr/local/lib/irivai/usbnet-setup.py" in install
      and "chown root:root /usr/local/lib/irivai/usbnet-setup.py" in install)
check("...and the rule", "84-irivai-usbnet.rules" in install)
check("...and normalises its line endings like the other helpers, because a "
      "CRLF shebang is how this family of file fails",
      install.count("/usr/local/lib/irivai/usbnet-setup.py") >= 3)

print()
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
    raise SystemExit(1)
print("smoke_usbnet OK")
