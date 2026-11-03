#!/usr/bin/env python3
"""Make the eth1 port behave like a soldered-on port. Run from udev.

eth1 is not a Raspberry Pi port. It is a Realtek RTL8153 hanging off USB, and
it arrives from the factory with two rough edges that a soldered port does not
have. Both are fixed here, once per appearance of the interface:

LEDS. The chip ships one-LED-per-speed: register 0xdd90 reads 0x0ca9, which is
nibbles 9 / a / c, where bit0 is 10M, bit1 is 100M, bit2 is 1G and bit3 is
activity. So LED0 lights at 10M, LED1 at 100M and LED2 at 1G. The jack has two
LEDs, wired to LED0 and LED1, which means a GIGABIT link leaves the port dark:
the one LED the chip is driving is not connected to anything. Reported as "the
eth1 lights do not work" on 2026-09-19, on a board whose link was perfectly
fine. We write 0x0fff instead -- every LED on at any speed, blinking on
traffic -- because which LED the jack uses is a board layout detail and this
is right for all of them.

The r8152 driver can do this itself, but only from a device tree property
("realtek,led-data") that a USB device on a Pi never gets. So we send the same
vendor control transfer the driver would. usbfs allows it to the DEVICE
recipient while the driver holds the interface, and the register is LED
configuration: it does not touch the link.

MAC. The adapter's address EEPROM is blank, so the kernel says

    r8152 3-1:1.0 (uninitialized): Invalid ether addr 00:00:00:00:00:00
    r8152 3-1:1.0 (uninitialized): Random ether addr 96:41:e5:6b:20:de

and invents a NEW address every boot. DHCP sees a new machine each time and
hands out a new lease, so the box moves address on every reboot -- which is
how a board at 192.168.1.173 became 192.168.1.10 just by having its cable
moved from one port to the other. We derive an address from the board serial
instead: same board, same address, for ever.

Setting an address needs the interface DOWN, which is true at udev add time
and false afterwards. Running this later therefore leaves the address alone
rather than bouncing a live link -- it takes effect at the next boot.
"""
import ctypes
import fcntl
import hashlib
import os
import struct
import subprocess
import sys

# Vendor control transfer, exactly as r8152 issues it.
REQ_REGS = 0x05
REQT_READ, REQT_WRITE = 0xC0, 0x40
MCU_TYPE_PLA = 0x0100
BYTE_EN_WORD = 0x33
PLA_LEDSEL = 0xDD90
USBDEVFS_CONTROL = 0xC0185500

#: Every LED on for any link speed, blinking on activity.
LEDSEL_ALL = 0x0FFF


class _Ctrl(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


def _xfer(fd, request_type, value, index, buf):
    ctrl = _Ctrl(request_type, REQ_REGS, value, index, len(buf), 1000,
                 ctypes.cast(buf, ctypes.c_void_p))
    fcntl.ioctl(fd, USBDEVFS_CONTROL, ctrl)


def read_word(fd, index):
    buf = (ctypes.c_uint8 * 4)()
    _xfer(fd, REQT_READ, index & ~3, MCU_TYPE_PLA, buf)
    dword = struct.unpack("<I", bytes(buf))[0]
    return (dword >> (8 * (index & 2))) & 0xFFFF


def write_word(fd, index, value):
    """ocp_write_word: the byte enables keep the neighbouring word intact."""
    shift = index & 2
    byen = BYTE_EN_WORD << shift
    raw = (value & 0xFFFF) << (8 * shift)
    buf = (ctypes.c_uint8 * 4).from_buffer_copy(struct.pack("<I", raw))
    _xfer(fd, REQT_WRITE, index & ~3, MCU_TYPE_PLA | (byen & 0x0F) | (byen & 0xF0),
          buf)


def usb_node(ifname):
    """The /dev/bus/usb node behind an interface, or None if it is not USB.

    Walking up from the interface rather than scanning for the vendor ID: a
    box can have two of these, and each must be programmed through its own
    node.
    """
    path = os.path.realpath("/sys/class/net/%s/device" % ifname)
    while path and path != "/":
        if os.path.exists(os.path.join(path, "busnum")):
            with open(os.path.join(path, "busnum")) as f:
                bus = int(f.read())
            with open(os.path.join(path, "devnum")) as f:
                num = int(f.read())
            return "/dev/bus/usb/%03d/%03d" % (bus, num)
        path = os.path.dirname(path)
    return None


def board_serial():
    """The Pi's serial, which is burnt in and unique. None if unreadable."""
    try:
        with open("/sys/firmware/devicetree/base/serial-number", "rb") as f:
            serial = f.read().strip(b"\x00").decode("ascii", "ignore").strip()
        if serial:
            return serial
    except OSError:
        pass
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def stable_mac(serial, ifname):
    """A locally administered address that is this board's, and only its.

    Hashed rather than sliced out of the serial so that two boards with
    neighbouring serials do not get neighbouring addresses, and salted with
    the interface name so a second adapter on the same board differs.
    """
    digest = hashlib.sha256(("irivai-usbnet:%s:%s" % (serial, ifname)).encode()).digest()
    mac = bytearray(digest[:6])
    mac[0] = (mac[0] | 0x02) & 0xFE          # locally administered, unicast
    return ":".join("%02x" % b for b in mac)


def _ip_binary():
    for path in ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip"):
        if os.path.exists(path):
            return path
    return None


def set_mac(ifname, mac):
    """(changed, why). Never bounces a link that is already carrying traffic."""
    try:
        with open("/sys/class/net/%s/address" % ifname) as f:
            current = f.read().strip()
        with open("/sys/class/net/%s/flags" % ifname) as f:
            up = int(f.read().strip(), 16) & 0x1
    except OSError as exc:
        return False, str(exc)
    if current == mac:
        return False, "already %s" % mac
    if up:
        return False, ("interface is up; %s takes effect at the next boot" % mac)
    binary = _ip_binary()
    if not binary:
        return False, "no ip(8) on this box"
    proc = subprocess.run([binary, "link", "set", "dev", ifname, "address", mac],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "ip refused").strip()
    return True, "%s -> %s" % (current, mac)


def main(argv):
    if len(argv) != 2:
        print("usage: usbnet-setup.py <interface>", file=sys.stderr)
        return 2
    ifname = argv[1]
    if not os.path.exists("/sys/class/net/%s" % ifname):
        return 0                                    # gone again already

    node = usb_node(ifname)
    if node:
        try:
            with open(node, "wb", buffering=0) as fd:
                if read_word(fd, PLA_LEDSEL) != LEDSEL_ALL:
                    write_word(fd, PLA_LEDSEL, LEDSEL_ALL)
                    print("%s: LEDs on for every link speed" % ifname)
        except OSError as exc:
            print("%s: LED config failed: %s" % (ifname, exc), file=sys.stderr)

    serial = board_serial()
    if serial:
        changed, why = set_mac(ifname, stable_mac(serial, ifname))
        if changed:
            print("%s: address pinned %s" % (ifname, why))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
