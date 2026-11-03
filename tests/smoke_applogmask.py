# -*- coding: utf-8 -*-
"""A camera's password never reaches app.log or the Logs page.

camera.py names a camera by its source in a dozen log lines ("camera %r:
device rate ...", "camera %r: reopen failed ..."), and an RTSP source is
rtsp://user:password@host/... -- so every one of them wrote the password into
app.log, and the WARNING-and-worse ones into the spool the Logs page shows.
faults masks URL passwords once, in the formatter and in the spool entry,
whatever the call site passed.

    python tests/smoke_applogmask.py
"""
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import faults                                         # noqa: E402

SECRET = "Pa55w0rd"
SRC = f"rtsp://admin:{SECRET}@192.168.1.64:554/Streaming/Channels/102"
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def record(msg, *args, level=logging.ERROR, exc=None):
    return logging.LogRecord("app.camera", level, __file__, 1, msg, args, exc)


fmt = faults._Format()
line = fmt.format(record("camera %r: device rate %.0f fps", SRC, 25.0, level=logging.INFO))
check("app.log line masks the password, keeps user and host",
      SECRET not in line and "admin:***@192.168.1.64" in line, line)

try:
    raise RuntimeError(f"cannot open {SRC}")
except RuntimeError:
    exc = sys.exc_info()
line = fmt.format(record("camera %r: reopen failed", SRC, exc=exc))
check("...and in the traceback", SECRET not in line, line)

entry = faults.to_entry(record("camera %r: reopen failed", SRC, exc=exc))
check("spool entry (the Logs page) masks message and traceback",
      SECRET not in entry["message"] and SECRET not in entry.get("traceback", ""),
      entry)
check("a password with @ in it is masked to the last @",
      SECRET not in faults.mask_urls("rtsp://u:P@" + SECRET + "@cam/x"))
check("a line with no URL is untouched",
      fmt.format(record("plain text: a=b", level=logging.INFO)).endswith(
          "[APP] plain text: a=b"))

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
