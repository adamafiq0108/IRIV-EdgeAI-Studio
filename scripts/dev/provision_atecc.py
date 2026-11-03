#!/usr/bin/env python3
"""Give this board an identity it cannot lie about. Run once, at manufacture.

WHAT IT DOES. Generates a P-256 private key INSIDE the ATECC608 and locks the
chip so the key can never be read out or replaced. From then on the board can
sign a challenge and nothing else can: not a copy of its SD card, not the same
card in another board, not somebody who has read every file on it. The public
half is printed here, and that is what you keep -- register it against the
board you are shipping, and a licence can later demand proof.

THIS IS PERMANENT. Locking a zone is a one-way operation in the silicon. A
chip locked with the wrong slot layout cannot be relaid out; it can only be
replaced. So the default is a DRY RUN that shows exactly what it would do and
changes nothing. Committing needs --commit and the chip's own serial typed
back, because a script that can brick a chip should not be one keystroke away.

IT WRITES NOTHING TO THE CONFIG ZONE, on a normal chip. An ATECC608 leaves
the factory with slot 0 already laid out as a lockable P-256 private key that
can sign (SlotConfig[0] = 0x2083, KeyConfig[0] = 0x0033), which is exactly
what is wanted. So this tool CHECKS that layout and refuses if it is wrong,
rather than rewriting bytes that are already correct -- a config write is
permanent once the zone is locked, and the safest write is the one not made.
A chip whose layout does not match is reported and left alone, because a chip
that differs from every other chip is a question, not a thing to patch.

THE ORDER. Verify the layout, lock the config zone, generate the key, lock the
data zone. The key is generated AFTER the config lock because the chip refuses
to generate into a slot whose rules are still changeable, and the data zone is
locked last, because that is what makes the key permanent.

    scripts/dev/provision_atecc.py                 # dry run, changes nothing
    scripts/dev/provision_atecc.py --commit        # asks for the serial, then does it
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from core.services import atecc                              # noqa: E402

#: What slot 0 must already say, and why. Checked, never written: see the
#: docstring. Both come from the chip's own factory defaults.
#:
#:   SlotConfig[0] bit 7  IsSecret        the slot's contents never leave the chip
#:                 bit 0  ExtSignEnable   the key may sign a challenge from outside
#:                 bits 13 WriteConfig    GenKey is allowed to create the key
#:   KeyConfig[0]  bit 0  Private         what lives here is a private key
#:                 bits 2-4 KeyType = 4   P-256
#:                 bit 5  Lockable        the slot can be frozen
SLOT0_OFFSET = 20            # SlotConfig[0], bytes 20-21, little-endian
KEY0_OFFSET = 96             # KeyConfig[0], bytes 96-97, little-endian

_OP_LOCK = 0x17
_OP_GENKEY = 0x40


def describe(info):
    print("chip      : revision %s" % (info["revision"] or "?"))
    print("serial    : %s" % (info["serial"] or "?"))
    print("config    : %s" % ("LOCKED" if info["config_locked"] else "unlocked"))
    print("data      : %s" % ("LOCKED" if info["data_locked"] else "unlocked"))


def read_words(chip):
    """The whole config zone as bytes, read a block at a time."""
    return b"".join(chip.config_block(b) for b in range(4))


def _word(current, offset):
    return current[offset] | (current[offset + 1] << 8)


def slot_ok(value):
    """Does SlotConfig[0] describe a secret, signable, GenKey-able slot?"""
    return {
        "secret (contents never readable)": bool(value & 0x0080),
        "may sign a challenge from outside": bool(value & 0x0001),
        "GenKey may create the key": bool(value & 0x2000),
    }


def key_ok(value):
    """Does KeyConfig[0] describe a lockable P-256 private key?"""
    return {
        "a private key": bool(value & 0x0001),
        "P-256": ((value >> 2) & 0x07) == 4,
        "lockable": bool(value & 0x0020),
    }


def show_plan(current):
    """Print the layout found and whether it is usable. Returns True when it is."""
    slot = _word(current, SLOT0_OFFSET)
    key = _word(current, KEY0_OFFSET)
    print()
    print("slot 0 layout, as the chip left the factory:")
    print("  SlotConfig[0] = 0x%04x" % slot)
    checks = slot_ok(slot)
    checks.update(key_ok(key))
    print("  KeyConfig[0]  = 0x%04x" % key)
    print()
    for label, good in checks.items():
        print("  %s %s" % ("yes" if good else "NO ", label))
    print()
    if not all(checks.values()):
        print("This chip's slot 0 is not laid out for a signing key, and this")
        print("tool does not rewrite config bytes. Nothing was done.")
        return False
    print("The layout is already right, so NOTHING is written to the config")
    print("zone. What happens on --commit:")
    print("  1. lock the config zone      PERMANENT")
    print("  2. generate a P-256 key in slot %d" % atecc.KEY_SLOT)
    print("  3. lock the data zone        PERMANENT")
    print("The private half never leaves the chip, and after step 3 it can")
    print("never be replaced either.")
    return True


def lock_zone(chip, zone):
    """Lock the config (0) or data (1) zone, without a summary CRC.

    The CRC form exists so a caller can prove it knows the whole zone it is
    locking, which guards against locking a zone somebody else just changed.
    Skipped deliberately here: this tool writes nothing, so there is no write
    of its own to confirm, and it has just read and checked the bytes that
    matter.
    """
    chip.command(_OP_LOCK, 0x80 | (zone & 0x03), 0x0000, wait=0.04, want=10)


def generate_key(chip, slot):
    """Make a new private key in `slot`. Returns its public half, 64 bytes."""
    return bytes(chip.command(_OP_GENKEY, 0x04, slot, wait=0.15, want=75))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true",
                    help="actually write and lock. PERMANENT.")
    ap.add_argument("--slot", type=int, default=atecc.KEY_SLOT,
                    help="which slot holds the device key (default 0)")
    args = ap.parse_args()

    info = atecc.info(refresh=True)
    if not info["present"]:
        sys.exit("No ATECC on the bus. Check the carrier board and that this "
                 "user is in the i2c group.")
    describe(info)

    if info["config_locked"] and info["data_locked"]:
        print()
        print("This chip is already provisioned. Nothing to do -- and nothing")
        print("COULD be done: the layout is locked for the life of the chip.")
        key = atecc.public_key(args.slot)
        if key:
            print("public key: %s" % key.hex())
        return 0

    with atecc.Chip() as chip:
        current = read_words(chip)
        if not show_plan(current):
            return 1

        if not args.commit:
            print()
            print("DRY RUN. Nothing was written. Re-run with --commit to do it.")
            return 0

        print()
        print("This cannot be undone. Type the chip's serial to confirm:")
        print("  %s" % info["serial"])
        try:
            typed = input("serial> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if typed != info["serial"]:
            sys.exit("that is not this chip's serial -- nothing was done")

        if not info["config_locked"]:
            lock_zone(chip, 0)
            print("config zone locked")
        key = generate_key(chip, args.slot)
        print("key generated in slot %d" % args.slot)
        if not info["data_locked"]:
            lock_zone(chip, 1)
            print("data zone locked")

    print()
    print("PUBLIC KEY (keep this -- it is how you recognise this board):")
    print(key.hex())
    print()
    print("serial %s" % info["serial"])
    print("The private half never left the chip and never will.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
