# After an update the box must stop offering the update it just installed.
#
# Tok Aba: "after I update the software, it still shows the update button".
# The check cache lives on the store and outlives the release that wrote it.
# Checked while v1.0.11 ran, it holds latest=v1.0.12 and update_available=True
# -- computed against v1.0.11 -- and public_state() handed that flag back
# unchanged, so the box on v1.0.12 offered v1.0.12 until the next check hours
# later. (v1.0.12 was also re-cut several times under the same version; a
# re-cut is not newer and must not be offered either.) Pinned:
#   1. a cache written by the old version: no update offered on the new one
#   2. hub start rewrites the cache to the running version (reconcile_cache),
#      keeping the release it knows about and its ETag
#   3. a genuinely newer release is still offered
#   4. the same version re-cut is not offered, and apply() refuses it
#   5. the pages ask the same question (lib/version.js offersUpdate), so a
#      state read before the restart cannot keep the button or the bell lit
import io
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import update as upd                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
STABLE = upd._p(upd.CHECK_FILE["stable"])

# What v1.0.11 left behind after its last check.
OLD = {"current": "1.0.11", "channel": "stable", "latest": "v1.0.12",
       "notes": "Area:\n- {BUG SOLVED} something", "assets": {},
       "etag": 'W/"etag-of-v1.0.12"', "checked_at": 1, "error": "",
       "update_available": True}
upd._write_json(STABLE, dict(OLD))

upd.VERSION = "1.0.12"                       # the box now runs v1.0.12
st = upd.public_state(None, "stable", CFG)
check("1 the cache from v1.0.11 no longer offers v1.0.12 to a box running it",
      st["update_available"] is False and st["current"] == "1.0.12"
      and st["latest"] == "v1.0.12", st)

changed = upd.reconcile_cache("1.0.12")
cached = upd._read_json(STABLE)
check("2 hub start rewrites the cache to the running version",
      changed == ["stable"] and cached["current"] == "1.0.12"
      and cached["update_available"] is False, cached)
check("2 ...keeping the release it knows about and its ETag",
      cached["latest"] == "v1.0.12" and cached["etag"] == OLD["etag"]
      and cached["notes"] == OLD["notes"], cached)
check("2 ...and doing nothing the second time",
      upd.reconcile_cache("1.0.12") == [])
src = io.open(os.path.join(ROOT, "core", "services", "update.py"),
              encoding="utf-8").read()
ann = src[src.index("def announce_installed("):]
check("2 ...it runs at every hub start, before announce_installed can return",
      ann.index("reconcile_cache(running)") < ann.index("return None"))

upd._write_json(STABLE, dict(OLD, latest="v1.0.13", update_available=False))
check("3 a genuinely newer release is still offered",
      upd.public_state(None, "stable", CFG)["update_available"] is True)
upd.reconcile_cache("1.0.12")
check("3 ...and reconcile keeps offering it",
      upd._read_json(STABLE)["update_available"] is True)

upd._write_json(STABLE, dict(OLD, etag='W/"a-recut"', notes="re-cut",
                             update_available=True))
check("4 the same version re-cut is not offered as an update",
      upd.public_state(None, "stable", CFG)["update_available"] is False)
upd.ota_ready = lambda: True                 # a box laid out for OTA
upd.job_running = lambda: False
try:
    upd.apply("1.0.12", config_dir=CFG)
    refused = ""
except upd.UpdateError as exc:
    refused = str(exc)
except Exception as exc:                                       # noqa: BLE001
    refused = f"other: {exc}"
check("4 ...and apply() refuses it as not newer", "not newer" in refused, refused)

# 5. the pages ask the same question.
JS = r'''
import { isNewer, offersUpdate } from "%s";
const ok = [
  isNewer("v1.0.13", "1.0.12") === true,
  isNewer("v1.0.12", "1.0.12") === false,
  isNewer("1.10.0", "1.9.0") === true,
  isNewer("1.0.12-dev", "1.0.12") === false,
  offersUpdate({ update_available: true, latest: "v1.0.12", current: "1.0.12" }) === false,
  offersUpdate({ update_available: true, latest: "v1.0.13", current: "1.0.12" }) === true,
  offersUpdate({ update_available: false, latest: "v1.0.13", current: "1.0.12" }) === false,
  offersUpdate(null) === false,
];
console.log(JSON.stringify(ok));
''' % (os.path.join(ROOT, "frontend", "src", "lib", "version.js")
       .replace("\\", "/").replace("C:", "file:///C:"))
probe = os.path.join(tempfile.mkdtemp(), "v.mjs")
io.open(probe, "w", encoding="utf-8").write(JS)
try:
    out = subprocess.run(["node", probe], capture_output=True, text=True,
                         timeout=60).stdout.strip()
except (OSError, subprocess.SubprocessError) as exc:
    out = f"node failed: {exc}"
check("5 the pages compare versions the server's way (isNewer, offersUpdate)",
      out == "[" + ",".join(["true"] * 8) + "]", out)
for name in ("UpdatePanel.jsx", "NotificationCenter.jsx"):
    jsx = io.open(os.path.join(ROOT, "frontend", "src", "components", name),
                  encoding="utf-8").read()
    check(f"5 {name} offers an update only through offersUpdate",
          "offersUpdate(" in jsx and "update_available ?" not in jsx
          and "{state.update_available &&" not in jsx)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all stale-update checks passed")
