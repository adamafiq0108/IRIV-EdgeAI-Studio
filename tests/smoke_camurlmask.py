"""A camera password with "@" or ":" in it is masked whole, everywhere.

WHAT WAS WRONG. The URL-credential rule (app/faults.py, core/services/logs.py
and frontend/src/lib/camErrors.js maskCredentials) took the password as
"everything up to the FIRST @", and the backend's also stopped at a ":".
FFmpeg and urllib split the address at the LAST "@", so a password typed in
plain -- rtsp://admin:P@ssw0rd@192.168.1.10/ -- is a camera that WORKS, and
the moment it failed the camera card, "Details", the Logs page and the
heartbeat showed:

    rtsp://admin:***@ssw0rd@192.168.1.10/     (most of the password)
    rtsp://admin:pa:ss@1.2.3.4/               (backend: all of it)

Both look redacted at a glance, which is worse than not redacting. Found in
the v1.0.13 review of the camera-error change (e8316260), which promised the
password never reaches the screen.

Pinned: each redactor masks the WHOLE password for "@", ":", "%40" and plain
passwords, rtsp/rtsps/http, leaves user-only and credential-free URLs and a
plain host:port alone, and keeps the user and the host readable.

    python tests/smoke_camurlmask.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from app import faults                                             # noqa: E402
from core.services import logs                                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else "\n      " + str(detail)))
    if not ok:
        bad += 1


# [url, the secret that must vanish, what must still read]
SECRET = [
    ("rtsp://admin:P@ssw0rd@192.168.1.10:554/s1", "ssw0rd", "rtsp://admin:***@192.168.1.10:554/s1"),
    ("rtsp://admin:pa:ss@1.2.3.4/s", "pa:ss", "rtsp://admin:***@1.2.3.4/s"),
    ("rtsp://admin:a@b:c@1.2.3.4/s", "b:c", "rtsp://admin:***@1.2.3.4/s"),
    ("rtsp://admin:p%40ss@1.2.3.4/s", "p%40ss", "rtsp://admin:***@1.2.3.4/s"),
    ("rtsps://admin:hunter2@1.2.3.4/s", "hunter2", "rtsps://admin:***@1.2.3.4/s"),
    ("http://u:hunter2@cam/x.mjpg", "hunter2", "http://u:***@cam/x.mjpg"),
    ("rtsp://admin:P@ss@cam.local", "P@ss", "rtsp://admin:***@cam.local"),
    # Pipi L5: a "/" in the password stopped the old rule dead.
    ("rtsp://admin:ab/cd@1.2.3.4/s", "ab/cd", "rtsp://admin:***@1.2.3.4/s"),
    ("rtsp://admin:a/b@c:d@cam.local/x", "a/b@c:d", "rtsp://admin:***@cam.local/x"),
    ("rtsp://admin:x/9/@1.2.3.4:554/s", "x/9/", "rtsp://admin:***@1.2.3.4:554/s"),
]
ALONE = ["rtsp://admin@1.2.3.4/s", "rtsp://1.2.3.4:554/live",
         "http://127.0.0.1:5102/raw", "rtsp://cam:554/a@b"]


def frame(url):
    # The shapes the backend actually writes: repr-quoted in a sentence, and
    # bare inside a log line followed by more text.
    return [f"camera {url!r} did not open: username or password rejected (RTSP 401)",
            f"[cam1] camera disconnected: {url!r} gave no frames (x)",
            f"consuming {url} now; user@example.com notified"]


def js_mask(texts):
    node = shutil.which("node")
    if not node:
        return None
    url = "file:///" + os.path.join(ROOT, "frontend", "src", "lib",
                                    "camErrors.js").replace("\\", "/").lstrip("/")
    code = (f"const m = await import({json.dumps(url)});"
            "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>"
            "console.log(JSON.stringify(JSON.parse(s).map(m.maskCredentials))));")
    out = subprocess.run([node, "--input-type=module", "-e", code],
                         input=json.dumps(texts), capture_output=True,
                         text=True, timeout=60)
    return json.loads(out.stdout)


texts = [t for url, _, _ in SECRET for t in frame(url)]
alone = [t for url in ALONE for t in frame(url)]
redactors = [("app faults.redact", lambda xs: [faults.redact(x) for x in xs]),
             ("core logs.redact", lambda xs: [logs.redact(x) for x in xs]),
             ("page maskCredentials", js_mask)]

for name, fn in redactors:
    got = fn(texts)
    if got is None:
        print(f"skip {name}: node is not installed")
        continue
    i = 0
    for url, secret, shown in SECRET:
        for _ in frame(url):
            out = got[i]
            i += 1
            check(f"{name}: {url} -> no password", secret not in out
                  and "***@" in out and shown in out, out)
    kept = fn(alone)
    for before, after in zip(alone, kept):
        check(f"{name}: left alone: {before[:50]}", before == after
              or (name == "page maskCredentials" and "rtsp://admin@" in before),
              after)

print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
