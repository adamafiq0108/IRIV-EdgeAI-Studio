"""The ATECC608 secure element on the board, read side.

WHAT THIS CHIP IS FOR. Every other identity this product can read -- the CM5
serial in /proc/cpuinfo, a MAC address, a machine-id -- is a number the
operating system reports, and the operating system belongs to whoever holds
the board. Copy the card, edit the file, and the clone answers to the name of
the original. A licence bound to such a number is bound to a claim.

The ATECC is different in one way that matters: it can hold a private key that
physically cannot be read out, and sign a challenge with it. A clone has the
card but not the chip, so it cannot answer a challenge, and no amount of
editing config files gives it the answer. That is the whole reason this module
exists.

WHAT IT IS NOT. It does not stop somebody with root from deleting the check
that asks the question. Nothing running on hardware in someone else's hands
can. What it does is make a COPY detectable and make anything gated on the
answer -- updates, store content, a licence -- unobtainable for the copy. See
docs/security-review-2026-08.md for where that boundary sits.

A FACTORY-FRESH CHIP HAS NO KEYS. It ships with both zones unlocked, no slot
layout and nothing to sign with, so `info()["provisioned"]` is False and
`sign()` returns None. Laying out the slots and locking them is PERMANENT --
see scripts/dev/provision_atecc.py, which is the only thing here that writes.

THE PROTOCOL, since this talks to the chip directly rather than through a
vendor library (one fewer dependency on an appliance, and the subset needed is
small). The chip is asleep until woken by holding SDA low, which a write to
address 0x00 does. Commands are a word address byte, then a packet whose first
byte is its own length and whose last two are a CRC16 the chip checks. A reply
has the same shape. Execution takes milliseconds and the chip NACKs until it
is finished, so a read too early is an error to retry, not a failure.
"""

import os
import threading
import time

from core import config

try:                                                         # noqa: SIM105
    import fcntl
except ImportError:
    # Not a POSIX box: there is no /dev/i2c here either, so every entry point
    # below reports "no chip" rather than failing to import. The hub's tests
    # run on a laptop; the chip only ever exists on the appliance.
    fcntl = None

#: Where the chip sits. I2C bus 1 at 0x60 is the IRIV carrier's wiring;
#: config/system.toml [atecc] overrides both for a board that differs.
DEFAULT_BUS = 1
DEFAULT_ADDR = 0x60

#: Word addresses, the first byte of every write to the chip.
_WORD_RESET = 0x00
_WORD_SLEEP = 0x01
_WORD_IDLE = 0x02
_WORD_COMMAND = 0x03

#: Opcodes used here. Read and Info are read-only; GenKey and Sign need a
#: provisioned chip and are refused politely on a fresh one.
_OP_READ = 0x02
_OP_RANDOM = 0x1B
_OP_GENKEY = 0x40
_OP_INFO = 0x30
_OP_SIGN = 0x41
_OP_NONCE = 0x16

#: The reply to a wake, exactly. Anything else is a chip that is not there, or
#: a bus with something else on it.
_AWAKE = bytes((0x04, 0x11, 0x33, 0x43))

#: Config zone byte offsets. The serial is split in two runs, which is a
#: quirk of the layout rather than of this code.
_SN_A = slice(0, 4)
_SN_B = slice(8, 13)
_LOCK_VALUE = 86          # the DATA zone's lock byte
_LOCK_CONFIG = 87         # the CONFIG zone's lock byte
_UNLOCKED = 0x55

#: The slot the device key lives in once a board is provisioned. Slot 0 is the
#: conventional place for it and the provisioning tool writes it there.
KEY_SLOT = 0

_lock = threading.Lock()
_cache = {}


class AteccError(RuntimeError):
    """The chip answered, and the answer was a refusal or a broken frame."""


class AteccBusy(AteccError):
    """Another process held the bus for too long: no answer about the chip."""


def _crc16(data):
    """Atmel's CRC16 over a packet body: poly 0x8005, bits low to high.

    Not any of the named CRC16s -- the bit order is the chip's own, so this is
    written out rather than taken from a library that would have to be
    configured into the same shape anyway.
    """
    reg = 0
    for byte in data:
        for bit in (1, 2, 4, 8, 16, 32, 64, 128):
            data_bit = 1 if byte & bit else 0
            crc_bit = reg >> 15
            reg = (reg << 1) & 0xFFFF
            if data_bit != crc_bit:
                reg ^= 0x8005
    return bytes((reg & 0xFF, (reg >> 8) & 0xFF))


def _settings(config_dir=None):
    """(bus, address) from config/system.toml [atecc], else the defaults."""
    try:
        section = (config.load("system", config_dir) or {}).get("atecc") or {}
    except Exception:                                        # noqa: BLE001
        section = {}
    try:
        bus = int(section.get("bus", DEFAULT_BUS))
    except (TypeError, ValueError):
        bus = DEFAULT_BUS
    try:
        addr = int(section.get("address", DEFAULT_ADDR))
    except (TypeError, ValueError):
        addr = DEFAULT_ADDR
    return bus, addr


#: ONE process at a time may talk to the chip: it wakes on a pulse and sleeps
#: on a command, so a second process waking it mid-command puts the first one's
#: answer to sleep. The hub, every master and every detection app check the
#: hardware as they start -- together, at boot -- and without this lock most of
#: them read "no chip". The flock is taken on the /dev/i2c-N descriptor itself,
#: so only a process that may open the bus can hold it (no lock file for another
#: user to squat on), and it frees itself when its holder dies.
_BUS_LOCK_WAIT_S = 10.0


def _lock_bus(fd):
    """Hold the bus through `fd` or raise AteccBusy after _BUS_LOCK_WAIT_S."""
    if fcntl is None:
        return
    deadline = time.time() + _BUS_LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.time() > deadline:
                raise AteccBusy("the secure element is busy")
            time.sleep(0.02)


class Chip:
    """One conversation with the chip. Use it as a context manager: the chip
    is woken on the way in and put back to sleep on the way out, which is what
    keeps it from drawing current and what makes the next wake clean."""

    def __init__(self, bus=None, addr=None, config_dir=None):
        d_bus, d_addr = _settings(config_dir)
        self.bus = d_bus if bus is None else bus
        self.addr = d_addr if addr is None else addr
        self.fd = None

    def __enter__(self):
        if fcntl is None:
            raise AteccError("no I2C on this platform")
        self.fd = os.open("/dev/i2c-%d" % self.bus, os.O_RDWR)
        try:
            _lock_bus(self.fd)
            self.wake()
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_exc):
        try:
            self.sleep()
        finally:
            if self.fd is not None:
                os.close(self.fd)            # closing releases the bus lock
                self.fd = None
        return False

    def _select(self, addr):
        fcntl.ioctl(self.fd, 0x0703, addr)       # I2C_SLAVE

    def wake(self):
        self._select(0x00)
        try:
            os.write(self.fd, b"\x00")
        except OSError:
            pass                 # a NACK is expected; the low pulse is the point
        time.sleep(0.003)        # tWHI, generously
        self._select(self.addr)
        status = os.read(self.fd, 4)
        if status != _AWAKE:
            raise AteccError("no ATECC at 0x%02x on bus %d (got %s)"
                             % (self.addr, self.bus, status.hex()))

    def sleep(self):
        try:
            self._select(self.addr)
            os.write(self.fd, bytes((_WORD_SLEEP,)))
        except OSError:
            pass                 # going to sleep is best effort, by design

    def command(self, opcode, p1, p2, data=b"", wait=0.03, want=75, tries=3):
        """Send one command, return its payload.

        `wait` is the chip's typical execution time. It NACKs while it is
        still working, so a read that fails is retried rather than treated as
        a broken chip -- Sign takes tens of milliseconds and varies.
        """
        count = 7 + len(data)
        body = bytes((count, opcode, p1, p2 & 0xFF, (p2 >> 8) & 0xFF)) + data
        packet = bytes((_WORD_COMMAND,)) + body + _crc16(body)
        self._select(self.addr)
        os.write(self.fd, packet)
        time.sleep(wait)
        raw = b""
        for attempt in range(tries):
            try:
                raw = os.read(self.fd, want)
                break
            except OSError:
                if attempt == tries - 1:
                    raise AteccError("no reply to command 0x%02x" % opcode)
                time.sleep(wait)
        if not raw or raw[0] < 4 or raw[0] > len(raw):
            raise AteccError("short reply to 0x%02x: %s" % (opcode, raw.hex()))
        frame = raw[:raw[0]]
        if _crc16(frame[:-2]) != frame[-2:]:
            raise AteccError("bad CRC on the reply to 0x%02x" % opcode)
        payload = frame[1:-2]
        if len(payload) == 1:
            # A one-byte payload is a STATUS, and 0x00 is the only good one.
            if payload[0] != 0x00:
                raise AteccError("command 0x%02x refused: status 0x%02x"
                                 % (opcode, payload[0]))
        return payload

    # -- the reads ----------------------------------------------------------

    def config_block(self, block):
        """32 bytes of the config zone. Blocks are 32 bytes; the address is a
        word offset, so a whole block is block << 3."""
        return self.command(_OP_READ, 0x80, (block << 3) & 0xFFFF,
                            wait=0.005, want=35)

    def revision(self):
        return self.command(_OP_INFO, 0x00, 0x0000, wait=0.005, want=7)

    def random(self):
        """32 random bytes from the chip's generator. On an UNLOCKED chip
        these are a fixed test pattern, by the chip's own design -- it refuses
        to pretend to be a random source before it is provisioned."""
        return self.command(_OP_RANDOM, 0x00, 0x0000, wait=0.025, want=35)


def _read_info(bus=None, addr=None, config_dir=None):
    with Chip(bus, addr, config_dir) as chip:
        rev = chip.revision()
        block0 = chip.config_block(0)
        block2 = chip.config_block(2)
        serial_bytes = block0[_SN_A] + block0[_SN_B]
        lock_value = block2[_LOCK_VALUE - 64]
        lock_config = block2[_LOCK_CONFIG - 64]
        return {
            "present": True,
            "revision": rev.hex(),
            "serial": serial_bytes.hex(),
            "config_locked": lock_config != _UNLOCKED,
            "data_locked": lock_value != _UNLOCKED,
            # PROVISIONED means both zones locked: the slot layout is fixed
            # and whatever key was written is now unreadable and usable.
            "provisioned": lock_config != _UNLOCKED and lock_value != _UNLOCKED,
            "bus": chip.bus,
            "address": chip.addr,
        }


_ABSENT = {"present": False, "revision": "", "serial": "",
           "config_locked": False, "data_locked": False, "provisioned": False}


def info(config_dir=None, refresh=False):
    """What the chip is, or `present: False` when there is none.

    Never raises: a board with no secure element is a supported board, and
    every caller here is asking a question about the hardware rather than
    depending on it. Cached, because none of it changes while the box runs
    except through provisioning, which asks for a refresh itself.
    """
    with _lock:
        if not refresh and "info" in _cache:
            return dict(_cache["info"])
        try:
            got = _read_info(config_dir=config_dir)
        except AteccBusy:
            return dict(_ABSENT)         # no answer yet: not remembered as "no chip"
        except (AteccError, OSError):
            got = dict(_ABSENT)
        _cache["info"] = got
        return dict(got)


def present(config_dir=None):
    return bool(info(config_dir).get("present"))


def serial(config_dir=None):
    """The 9-byte serial as 18 hex characters, or "" when there is no chip.

    This is an IDENTIFIER, not proof: it is public, it is readable by anyone
    with the board, and software can be patched to report any value. Binding a
    licence to it raises the bar over the CM5 serial -- a copier now needs a
    board with a secure element on it -- but only `sign()` on a provisioned
    chip proves anything.
    """
    return str(info(config_dir).get("serial") or "")


def provisioned(config_dir=None):
    return bool(info(config_dir).get("provisioned"))


def public_key(slot=KEY_SLOT, config_dir=None):
    """The device key's public half (64 raw bytes, X then Y), or None.

    None on a chip that was never provisioned, which is the honest answer:
    there is no key, rather than a key that cannot be reached.
    """
    if not provisioned(config_dir):
        return None
    try:
        with Chip(config_dir=config_dir) as chip:
            # GenKey with mode 0x00 returns the PUBLIC half of an existing
            # private key. It generates nothing: generating is mode 0x04, and
            # that is the provisioning tool's job, not this module's.
            return bytes(chip.command(_OP_GENKEY, 0x00, slot, wait=0.12,
                                      want=75))
    except (AteccError, OSError):
        return None


def sign(digest, slot=KEY_SLOT, config_dir=None):
    """Sign a 32-byte digest with the device key. 64 raw bytes (R then S), or
    None when this board has no provisioned key.

    This is the answer to a challenge that a copy cannot give. The digest must
    be something the asker chose -- a random nonce, a timestamp, the hash of
    the thing being claimed -- or the answer proves only that somebody once
    had the board.
    """
    if len(digest) != 32:
        raise ValueError("a digest to sign must be exactly 32 bytes")
    if not provisioned(config_dir):
        return None
    try:
        with Chip(config_dir=config_dir) as chip:
            # The digest is loaded into TempKey with Nonce (passthrough mode
            # 0x03), then Sign mode 0x80 signs what is in TempKey.
            chip.command(_OP_NONCE, 0x03, 0x0000, digest, wait=0.03, want=10)
            return bytes(chip.command(_OP_SIGN, 0x80, slot, wait=0.12,
                                      want=75))
    except (AteccError, OSError):
        return None


def fingerprint(config_dir=None):
    """This board's identity as the product records it: the ATECC serial when
    there is a chip, the CM5 serial always, and whether the chip can prove it.

    Both are recorded rather than one, because they answer different
    questions. The CM5 serial says which board this is and survives a chip
    that was never fitted; the ATECC serial says which secure element is on
    it, and only that one can be asked to prove the claim.
    """
    got = info(config_dir)
    return {
        "cpu_serial": _cpu_serial(),
        "atecc_serial": got.get("serial") or "",
        "atecc_present": bool(got.get("present")),
        "atecc_provisioned": bool(got.get("provisioned")),
    }


def _cpu_serial():
    """The CM5 serial, devicetree first, then /proc/cpuinfo."""
    try:
        with open("/sys/firmware/devicetree/base/serial-number",
                  encoding="utf-8", errors="ignore") as f:
            value = f.read().strip("\x00 \n\t")
        if value:
            return value.lower()
    except OSError:
        pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":", 1)[1].strip().lower()
    except OSError:
        pass
    return ""
