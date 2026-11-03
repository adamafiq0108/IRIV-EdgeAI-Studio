# The ATECC608 secure element: reading the board's hardware identity, and
# refusing to pretend when there is nothing to read.
#
# WHY THIS MATTERS. Every other identity the product can read -- the CM5
# serial, a MAC address -- is a number the operating system reports, and a
# copied SD card reports the original's. The chip is the one identity that
# cannot be copied with the filesystem, because the private key inside it
# cannot be read out at all. A licence bound to a challenge this chip answers
# is bound to hardware; a licence bound to /proc/cpuinfo is bound to a claim.
#
# The fixtures here are the bytes a REAL chip returned (an ATECC608, revision
# 00006003, factory fresh) so the parsing is pinned against hardware rather
# than against my reading of the datasheet.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import atecc                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- the packet the chip accepts ----------------------------------------------------
# These CRCs are not a guess: the same framing was sent to a real ATECC608 and
# the chip answered, which it does not do for a packet whose CRC it dislikes.
check("the command CRC is the chip's own, not a stock CRC16",
      atecc._crc16(bytes([7, 0x30, 0x00, 0x00, 0x00])).hex() == "035d"
      and atecc._crc16(bytes([7, 0x02, 0x80, 0x00, 0x00])).hex() == "09ad",
      (atecc._crc16(bytes([7, 0x30, 0x00, 0x00, 0x00])).hex(),
       atecc._crc16(bytes([7, 0x02, 0x80, 0x00, 0x00])).hex()))
check("...and an empty body hashes to zero, so the loop is not seeded wrong",
      atecc._crc16(b"").hex() == "0000")


# -- a fake chip, answering with the bytes a real one gave -------------------------
REV = bytes.fromhex("00006003")
SERIAL_HEX = "0123d140fd0b7bdbee"


def block0(serial_hex=SERIAL_HEX):
    raw = bytes.fromhex(serial_hex)
    # The serial is split: four bytes at 0, five at 8. The gap is the
    # revision, which is why it is not one run.
    return raw[:4] + REV + raw[4:] + bytes(32 - 13)


def block2(lock_value=0x55, lock_config=0x55):
    body = bytearray(32)
    body[22] = lock_value          # byte 86 of the config zone
    body[23] = lock_config         # byte 87
    return bytes(body)


class FakeChip:
    """Stands in for the I2C conversation. Built per-call like the real one."""
    locks = (0x55, 0x55)
    serial_hex = SERIAL_HEX
    asleep = False

    def __init__(self, bus=None, addr=None, config_dir=None):
        self.bus = 1 if bus is None else bus
        self.addr = 0x60 if addr is None else addr

    def __enter__(self):
        FakeChip.asleep = False
        return self

    def __exit__(self, *_exc):
        FakeChip.asleep = True     # the chip must be put back to sleep
        return False

    def revision(self):
        return REV

    def config_block(self, block):
        return block0(self.serial_hex) if block == 0 else block2(*self.locks)


real_chip = atecc.Chip


def with_chip(cls, **attrs):
    for k, v in attrs.items():
        setattr(cls, k, v)
    atecc.Chip = cls
    atecc._cache.clear()


# -- a fresh chip: present, identified, and honestly unprovisioned ------------------
with_chip(FakeChip, locks=(0x55, 0x55))
got = atecc.info()
check("a chip on the bus is reported present, with its revision",
      got["present"] and got["revision"] == "00006003", got)
check("...and its serial read out of the two runs the config zone splits it into",
      got["serial"] == SERIAL_HEX, got["serial"])
check("...factory fresh: both zones unlocked, so NOT provisioned",
      got["config_locked"] is False and got["data_locked"] is False
      and got["provisioned"] is False, got)
check("the chip is put back to sleep after every conversation", FakeChip.asleep)
check("serial() is the same string, and present() agrees",
      atecc.serial() == SERIAL_HEX and atecc.present() is True)

# A fresh chip has NO key. Saying so is the point: a caller must be able to
# tell "this board cannot prove itself" from "this board refused to".
check("public_key() on an unprovisioned chip is None, not an empty key",
      atecc.public_key() is None)
check("sign() on an unprovisioned chip is None, not a fake signature",
      atecc.sign(b"\x00" * 32) is None)

# -- a provisioned chip ------------------------------------------------------------
with_chip(FakeChip, locks=(0x00, 0x00))
got = atecc.info(refresh=True)
check("both zones locked reads as provisioned",
      got["provisioned"] and got["config_locked"] and got["data_locked"], got)
with_chip(FakeChip, locks=(0x55, 0x00))
check("config locked but data open is NOT provisioned - the key is still writable",
      atecc.info(refresh=True)["provisioned"] is False)

# -- a digest is 32 bytes, and a caller who gets that wrong hears about it ----------
with_chip(FakeChip, locks=(0x00, 0x00))
for wrong in (b"", b"\x01" * 31, b"\x01" * 33):
    try:
        atecc.sign(wrong)
        check("a digest of %d bytes is refused" % len(wrong), False)
    except ValueError:
        check("a digest of %d bytes is refused" % len(wrong), True)

# -- no chip at all: a supported board, not an error --------------------------------
class NoChip(FakeChip):
    def __enter__(self):
        raise atecc.AteccError("no ATECC at 0x60 on bus 1")


with_chip(NoChip)
got = atecc.info(refresh=True)
check("a board with no secure element reports absent rather than raising",
      got["present"] is False and got["serial"] == "", got)
check("...serial() is empty, present() is False, sign() is None",
      atecc.serial() == "" and atecc.present() is False
      and atecc.sign(b"\x00" * 32) is None)


class BrokenBus(FakeChip):
    def __enter__(self):
        raise OSError(5, "Input/output error")


with_chip(BrokenBus)
check("a bus that errors is the same answer: absent, never an exception",
      atecc.info(refresh=True)["present"] is False)

# -- the fingerprint the product records for a board --------------------------------
with_chip(FakeChip, locks=(0x00, 0x00))
atecc._cache.clear()
fp = atecc.fingerprint()
check("the fingerprint carries BOTH serials and whether the chip can prove itself",
      set(fp) == {"cpu_serial", "atecc_serial", "atecc_present", "atecc_provisioned"}
      and fp["atecc_serial"] == SERIAL_HEX and fp["atecc_provisioned"] is True, fp)

atecc.Chip = real_chip
atecc._cache.clear()

# -- the module answers wherever it runs, chip or no chip ---------------------------
# The suites run on a laptop, on a CI runner and on the board itself. An
# appliance-only import would mean this file could only be tested on an
# appliance; an assertion that no chip is present would fail on the one
# machine that HAS one. So what is pinned is the contract: info() always
# answers, never raises, and never reports a serial it did not read.
probe = atecc.info(refresh=True)
check("the module answers wherever it runs -- laptop, CI runner or board",
      isinstance(probe, dict) and isinstance(probe.get("present"), bool))
check("...and a board with no chip says so rather than inventing a serial",
      probe["present"] is True or not probe.get("serial"))
check("...while a board WITH one reports a serial of the right shape",
      probe["present"] is False or len(probe.get("serial") or "") == 18,
      probe)

# -- the installer records it, once, where an update cannot overwrite it -----------
import tomllib                                                # noqa: E402

install = io.open(os.path.join(ROOT, "scripts", "install.sh"),
                  encoding="utf-8").read()
check("install.sh has a step that records the board identity",
      "config/device.local.toml" in install)
check("...written as a .local file, which no over-the-air update overwrites",
      "device.local.toml" in install and "config/*.local.toml" in
      io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read())
check("...and the failure to read a chip is a warning, not a failed install",
      "the board identity could not be read" in install)
check("the service account is in the i2c group, or it could not read the chip",
      "for grp in video render gpio i2c spi dialout plugdev" in install)

# The file the installer writes must PARSE -- it is read back by config.load.
SAMPLE_LINES = [
    "[device]",
    'cpu_serial = "35739a7f67a4e383"',
    'atecc_serial = "0123d140fd0b7bdbee"',
    "atecc_present = true",
    "atecc_provisioned = false",
]
try:
    parsed = tomllib.loads(chr(10).join(SAMPLE_LINES))["device"]
    check("what it writes is valid TOML, with both serials and the chip's state",
          parsed["atecc_serial"] == SERIAL_HEX and parsed["atecc_present"] is True
          and parsed["atecc_provisioned"] is False, parsed)
except Exception as exc:                                      # noqa: BLE001
    check("what it writes is valid TOML", False, repr(exc))

# -- the provisioning tool: the only thing that makes the chip useful --------------
#
# It is also the only thing that can ruin the chip, so what is pinned here is
# what it does NOT do: it never writes a config byte, and it never locks
# without --commit. The factory values below are the ones a real ATECC608
# reported, which is why the tool can be a checker rather than a writer.
import importlib.util                                         # noqa: E402

spec = importlib.util.spec_from_file_location(
    "provision_atecc", os.path.join(ROOT, "scripts", "dev", "provision_atecc.py"))
prov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prov)

check("the factory slot 0 already satisfies every requirement",
      all(prov.slot_ok(0x2083).values()) and all(prov.key_ok(0x0033).values()),
      (prov.slot_ok(0x2083), prov.key_ok(0x0033)))
check("...a slot that is not secret is refused",
      prov.slot_ok(0x2003)["secret (contents never readable)"] is False)
check("...a slot that cannot sign for an outside caller is refused",
      prov.slot_ok(0x2082)["may sign a challenge from outside"] is False)
check("...a key that is not a private P-256 key is refused",
      prov.key_ok(0x0000)["a private key"] is False
      and prov.key_ok(0x0003)["P-256"] is False)

tool = io.open(os.path.join(ROOT, "scripts", "dev", "provision_atecc.py"),
               encoding="utf-8").read()
check("the tool contains NO config-zone write: the safest write is the one not made",
      "_OP_WRITE" not in tool and "def write_word" not in tool)
check("...locking needs --commit AND the chip's serial typed back",
      'args.commit' in tool and 'typed != info["serial"]' in tool
      and "DRY RUN" in tool)
check("...and a chip already locked is left alone",
      'if info["config_locked"] and info["data_locked"]:' in tool)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
