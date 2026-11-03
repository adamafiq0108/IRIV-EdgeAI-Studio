"""A detector that fell back said why, once, and then never again.

Reproduced on 192.168.1.234: `couting_test` (a new system) shares the box's
ONE physical Hailo-8 with `pcb_sensata`, already running. Opening a second
VDevice fails with HAILO_OUT_OF_PHYSICAL_DEVICES(74); run.py catches exactly
that at startup, falls back to a silent detector so the video stream stays
up, and records hailo.py's _busy_message ("the AI accelerator is already in
use by ...") into runtime state -- once.

The MAIN LOOP's own per-frame heartbeat -- the write every reader actually
polls -- then set `error=primary.cam.error` unconditionally: the camera's own
error, nothing else. The camera had none (video was fine), so this wiped the
detector's message on the very first frame after it was recorded, and every
frame after. `core.services.detector_control.detector_error()` was written
specifically to surface this sentence to the hub and the Detection page; the
sentence never survived long enough to be read. Video flowing, ai_running
true, every rule reading zero, no error anywhere -- an operator with no next
step, on a system that was never going to run AI until either the other
system stopped or the message said so.

Source-level, not a running loop: reproducing an actual Hailo device conflict
needs the hardware this bug was found on. What is checked is the shape that
broke -- the per-frame write reads the detector's own error rather than
discarding it, and every place that changes the detector's state keeps that
variable in sync so the write has something current to read.

    python tests/smoke_detectorerror.py
"""
import io
import os
import re
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


run_py = read("systems", "demo", "app", "run.py")

# -- 1. the write every reader polls no longer discards the reason -----------
check("the per-frame heartbeat reads the detector's own error",
      # (With the AI off only the detector's own reason remains: a parked
      # camera's leftover fault is not live -- see smoke_aireview.)
      "error=((primary.cam.error or detector_error)" in run_py,
      "it wrote `error=primary.cam.error` alone -- the camera's error only --"
      "which is None whenever the CAMERA is fine, discarding whatever the "
      "detector itself had to say")
check("...camera error still wins when both exist",
      run_py.index("error=((primary.cam.error or detector_error)")
        < run_py.index("counts=counts"),
      "the comment above this write already promises these singular fields "
      "are 'the FIRST camera's' -- a camera problem is the more specific one")

# -- 2. every place the detector's state changes keeps it current ------------
# `detector_error` is a plain local across one function (main()); a site that
# changes what `det` is without updating it is a site whose news never
# reaches the write above.
sites = [
    ("the retry ladder clears it on recovery",
     "detector_retry_delay = DETECTOR_RETRY_MIN_S\n                    detector_error = None"),
    ("...and refreshes it on a repeat failure, not the FIRST failure's text",
     'detector_error = (f"{det_cfg.get(\'kind\') or \'configured\'} "\n'
     '                                      f"detector failed to load: {exc}")'),
    ("the model/confidence reload clears it on success",
     'det_cfg = new_det_cfg\n                                detector_error = None\n'
     '                                log.info("detector reloaded: %s", det.name)'),
    ("...and sets it on a reload failure",
     'detector_error = f"detector reload failed: {exc}"\n'
     '                                runtime.write(data_dir, detector=det.name,\n'
     '                                              error=detector_error)'),
]
for label, needle in sites:
    check(label, needle in run_py, "the write below it would read a stale value")

# -- 3. the message itself still names who is holding the chip ---------------
hailo_py = read("systems", "demo", "app", "hailo.py")
check("the busy message names the OTHER system, not just an error code",
      "_busy_message" in hailo_py and "already in use" in hailo_py)
check("...and how to actually run both",
      "[detector.hailo].multi_process = " in hailo_py
      and "EVERY system here that uses it" in hailo_py)

# -- 4. the API this was built to feed --------------------------------------
dc = read("core", "services", "detector_control.py")
check("detector_error() reads the SAME state field the loop writes",
      'return str((st or {}).get("error") or "")' in dc)
check("...and its docstring already named this exact failure mode",
      "reached no page" in dc)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
