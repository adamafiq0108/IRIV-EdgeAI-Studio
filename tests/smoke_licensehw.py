# A licence key bound to the board's SECURE ELEMENT, not to a number the
# operating system reports.
#
# THE PROBLEM THIS FIXES. A key used to name the CM5 serial, which comes from
# /proc/cpuinfo -- a file on a disk that can be copied, on a system whose root
# belongs to whoever holds the board. Clone the card, make the clone report
# the original's serial, and the key activates on both. The ATECC608 cannot be
# copied that way: the private key inside it never leaves the silicon, so a
# board either signs the challenge or it does not.
#
# WHAT IS PINNED HERE. That a key naming a chip demands a signature; that a
# wrong chip, a missing chip and a chip that will not sign are each refused
# with a reason a person can act on; and -- the compatibility half, which
# matters just as much -- that every key issued BEFORE this existed still
# activates exactly as it did.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
CONFIG = tempfile.mkdtemp()
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import atecc                              # noqa: E402
from core.services import store                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


if not store.crypto_available():
    print("skipped: the cryptography package is not installed")
    sys.exit(77)                    # run_all.py SKIP, not a pass

from cryptography.hazmat.primitives import hashes            # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec     # noqa: E402
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils  # noqa: E402

ATECC_SERIAL = "0123d140fd0b7bdbee"
OTHER_SERIAL = "0123ffffffffffffee"

# A stand-in for the chip: a P-256 key in software, signing the same way the
# silicon does -- over a 32-byte digest, returning R and S as raw bytes.
DEVICE_KEY = ec.generate_private_key(ec.SECP256R1())
NUMBERS = DEVICE_KEY.public_key().public_numbers()
PUBKEY_HEX = (NUMBERS.x.to_bytes(32, "big") + NUMBERS.y.to_bytes(32, "big")).hex()


class FakeChip:
    """What the board reports about its secure element, and how it answers."""
    serial = ATECC_SERIAL
    present = True
    provisioned = True
    signs = True
    key = DEVICE_KEY


def install_fake():
    atecc.info = lambda config_dir=None, refresh=False: {
        "present": FakeChip.present, "revision": "00006003",
        "serial": FakeChip.serial if FakeChip.present else "",
        "config_locked": FakeChip.provisioned, "data_locked": FakeChip.provisioned,
        "provisioned": FakeChip.present and FakeChip.provisioned}
    atecc.present = lambda config_dir=None: FakeChip.present
    atecc.provisioned = lambda config_dir=None: (FakeChip.present
                                                 and FakeChip.provisioned)
    atecc.serial = lambda config_dir=None: (FakeChip.serial if FakeChip.present
                                            else "")

    def _sign(digest, slot=0, config_dir=None):
        if not (FakeChip.present and FakeChip.provisioned and FakeChip.signs):
            return None
        der = FakeChip.key.sign(digest, ec.ECDSA(asym_utils.Prehashed(hashes.SHA256())))
        r, s = asym_utils.decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    atecc.sign = _sign


install_fake()

# -- the chip proves itself ---------------------------------------------------------
ok, why = store.prove_hardware(PUBKEY_HEX)
check("a board holding the private key answers the challenge", ok, why)

# The challenge must be FRESH. Two proofs in a row must not be the same
# question, or a recording of one answer would serve for ever.
seen = []
real_sign = atecc.sign
atecc.sign = lambda digest, slot=0, config_dir=None: (seen.append(digest)
                                                      or real_sign(digest, slot))
store.prove_hardware(PUBKEY_HEX)
store.prove_hardware(PUBKEY_HEX)
atecc.sign = real_sign
check("...and is asked a different question every time, so an answer cannot be replayed",
      len(seen) == 2 and seen[0] != seen[1] and len(seen[0]) == 32)

# -- the ways it can fail, each with its own reason -----------------------------------
other = ec.generate_private_key(ec.SECP256R1())
on = other.public_key().public_numbers()
OTHER_PUBKEY = (on.x.to_bytes(32, "big") + on.y.to_bytes(32, "big")).hex()
ok, why = store.prove_hardware(OTHER_PUBKEY)
check("a board whose key is not the one on the licence is refused",
      not ok and "not the one" in why, why)

FakeChip.signs = False
ok, why = store.prove_hardware(PUBKEY_HEX)
check("a chip that will not answer is refused, and says so",
      not ok and "did not answer" in why, why)
FakeChip.signs = True

FakeChip.provisioned = False
ok, why = store.prove_hardware(PUBKEY_HEX)
check("a chip with no key yet is refused, and says THAT instead",
      not ok and "holds no key" in why, why)
FakeChip.provisioned = True

FakeChip.present = False
ok, why = store.prove_hardware(PUBKEY_HEX)
check("a board with no chip fitted is refused, and says that",
      not ok and "no secure element" in why, why)
FakeChip.present = True

# -- the licence payload, end to end --------------------------------------------------


def hw(payload):
    return store._hardware_check(payload)


check("a key that names no chip passes untouched - every key issued before this",
      hw({"serial": "abc"}) == (True, ""))
check("a key naming this chip AND its key passes",
      hw({"atecc": ATECC_SERIAL, "pubkey": PUBKEY_HEX})[0])
check("a key naming another board's chip is refused",
      hw({"atecc": OTHER_SERIAL, "pubkey": PUBKEY_HEX}) [0] is False
      and "different board" in hw({"atecc": OTHER_SERIAL})[1])
check("a key naming this chip but ANOTHER board's key is refused",
      hw({"atecc": ATECC_SERIAL, "pubkey": OTHER_PUBKEY})[0] is False)
check("a key naming a chip, on a board with none, is refused",
      (lambda: (setattr(FakeChip, "present", False),
                hw({"atecc": ATECC_SERIAL, "pubkey": PUBKEY_HEX}),
                setattr(FakeChip, "present", True))[1])()[0] is False)
# A serial with no public key is weaker, and passes on the right board. It is
# allowed on purpose (a board built before provisioning), never pretended to
# be proof.
check("a key naming the chip but carrying no public key compares serials only",
      hw({"atecc": ATECC_SERIAL}) == (True, ""))

# -- issuing: the fields are checked before a key is ever signed ------------------------
for kwargs, why in (
        ({"atecc_serial": "nothex"}, "a serial that is not hex"),
        ({"atecc_serial": "0123"}, "a serial of the wrong length"),
        ({"atecc_serial": ATECC_SERIAL, "device_pubkey": "abcd"},
         "a public key of the wrong length"),
        ({"device_pubkey": PUBKEY_HEX}, "a public key with no serial beside it")):
    try:
        store.issue_license(CONFIG, "Acme", "10000000abcdef01", "free", 30, "",
                            **kwargs)
        check("issuing refuses %s" % why, False)
    except store.StoreError:
        check("issuing refuses %s" % why, True)
    except Exception as exc:                                  # noqa: BLE001
        # No signing key on this box is a different refusal, and a fine one --
        # the point is that the shape is checked, so only report a surprise.
        check("issuing refuses %s" % why, "signing key" in str(exc), repr(exc))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
