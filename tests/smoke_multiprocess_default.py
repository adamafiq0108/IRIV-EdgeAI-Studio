"""[detector.hailo].multi_process defaults by architecture when nobody said.

This product's whole reason for `_busy_message` and `MAX_RUNNING_APPS` is that
a Hailo-8 board routinely runs SEVERAL systems sharing one physical chip. Left
to open the accelerator EXCLUSIVELY by default, the second system on the box
refused with HAILO_OUT_OF_PHYSICAL_DEVICES(74) -- error 74, no name, no next
step -- until an operator found the one setting that fixes it. Reproduced on
192.168.1.234: a new system (couting_test) against an already-running one
(pcb_sensata), neither configured either way.

Sharing is now the default on Hailo-8 / 8L. Hailo-10H stays off -- different
hardware, and this default is not the call that decides FOR it.

THE DANGEROUS CASE, and it is measured, not guessed (see the comment on
_vdevice_params): mixing modes on one box -- one system exclusive, another
through the service -- brings the HailoRT service down with SIGSEGV and takes
the client with it via SIGBUS. Both through the service works (7.4 and 3.7 fps
sharing one chip, measured on this exact HailoRT/driver combination). So an
architecture default is only safe applied UNIFORMLY: every system of the same
architecture on a box has to move together, and an explicit setting -- once
one exists -- has to keep winning, or the very act of adding this default
could create the mix it is meant to prevent.

    python tests/smoke_multiprocess_default.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


src = read("systems", "demo", "app", "hailo.py")

# -- 1. the default exists, and it is arch-aware ------------------------------
check("there is an architecture-aware default",
      "def _default_multi_process(arch):" in src)
check("...on for hailo8 and hailo8l",
      'return arch in ("hailo8", "hailo8l")' in src)

sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import hailo                                          # noqa: E402

check("...hailo8 defaults to sharing", hailo._default_multi_process("hailo8") is True)
check("...hailo8l defaults to sharing", hailo._default_multi_process("hailo8l") is True)
check("...hailo10h defaults OFF", hailo._default_multi_process("hailo10h") is False,
      "different hardware; this default does not decide for it")
check("...an unknown/absent arch defaults OFF, not ON",
      hailo._default_multi_process(None) is False,
      "a board this cannot identify must not be opted into a shared service "
      "it has not been measured against")

# -- 2. explicit always wins, both directions --------------------------------
check("an EXPLICIT false is not overridden by the hailo8 default",
      "configured = self.config.get(\"multi_process\")" in src
      and "if configured is not None" in src,
      "dict.get on the raw key -- not .get(key, default) -- is what tells "
      "an ABSENT key apart from a present, falsy one")
check("...and the site that reads it prefers the explicit value, not the arch",
      re_order := (src.index("configured = self.config.get")
                   < src.index("self._vdevice = _open_vdevice(")))

# -- 3. the one config on the box that WOULD have created the dangerous mix --
# pcb_sensata explicitly pinned multi_process = false while every other
# hailo8 system defaulted (silently, via this same change) to true. Left as
# it was, this default would have produced exactly the mixed exclusive+service
# pairing the comment on _vdevice_params measures as fatal. Not a source
# assertion -- there is no source home for a deployment's own config -- but
# recorded here so the reasoning survives next to the code it depends on.

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
