"""An error the camera-error map does not know is still shown password-masked.

WHAT WAS WRONG. e8316260 routed every camera error through
frontend/src/lib/camErrors.js, which masks the password in a camera URL. But
two places fell back to the RAW text when the error was not a known camera
cause: the System Control error cell (text AND hover title) and Camera
Setup's "Last run ended with:" line. Unknown is exactly where a URL turns up
unannounced -- a GStreamer error quoting its rtspsrc location, "camera reload
failed: <exception>", or an older app copy still running under the store --
so those two printed rtsp://admin:<password>@... in clear on the page people
screenshot for support.

Pinned: the fallback renders explainCamError's `said` (credentials masked),
never p.error / lastError directly, and `said` really is masked.

    python tests/smoke_camerrsaid.py
"""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else "\n      " + str(detail)))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


cp = read("frontend", "src", "components", "ControlPanel.jsx")
det = read("frontend", "src", "pages", "Detection.jsx")

# The JSX of the error cell: from explainCamError(p.error) to its </div>.
cell = cp[cp.index("explainCamError(p.error)"):]
cell = cell[:cell.index("</div>")]
check("System Control: the error cell renders no raw p.error",
      not re.search(r"[:?]\s*p\.error\b|\{p\.error\}", cell), cell)
check("System Control: the unknown fallback shows the masked text",
      cell.count("x?.said") >= 2, cell)

line = det[det.index("Last run ended with:"):]
line = line[:line.index("</p>")]
check('Camera Setup: "Last run ended with:" renders no raw lastError',
      "lastError" not in line, line)
check("...the unknown fallback shows the masked text", "lastX?.said" in line, line)

node = shutil.which("node")
if node:
    url = "file:///" + os.path.join(ROOT, "frontend", "src", "lib",
                                    "camErrors.js").replace("\\", "/").lstrip("/")
    raw = ("gst: Could not open resource for reading: "
           "rtspsrc location=rtsp://admin:hunter2@10.0.0.5/live")
    code = (f"const m = await import({json.dumps(url)});"
            f"const x = m.explainCamError({json.dumps(raw)});"
            "console.log(JSON.stringify({known: x.known, said: x.said}));")
    out = subprocess.run([node, "--input-type=module", "-e", code],
                         capture_output=True, text=True, timeout=60)
    x = json.loads(out.stdout)
    check("an unknown error's `said` has the password masked",
          not x["known"] and "hunter2" not in x["said"]
          and "admin:***@10.0.0.5" in x["said"], x)
else:
    print("skip the camErrors.js run: node is not installed")

print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
