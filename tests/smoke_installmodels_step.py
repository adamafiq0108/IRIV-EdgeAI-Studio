# install.sh step 7f (Detection models) must never end the install.
#
# WHAT WAS WRONG: a fresh install shipped no detection model (found on
# 192.168.1.47, v1.0.12), so 8f3cb45 added step 7f, which fetches yolov8n /
# yolov8s from the Model Zoo while the installer runs. That is a network call
# inside a script running under `set -euo pipefail`: a bare call that exits
# non-zero (offline -> 1, no accelerator -> 2, killed -> 137) ends the whole
# install right there, before the systemd units are written -- the same trap
# that cut the hardening check short (95d2324). smoke_modelbootstrap only
# checks the step's TEXT for the `|| models_rc=$?` idiom; this one RUNS the
# step, cut out of the real install.sh, under the real shell options with
# `sudo` stubbed to each exit code, and requires that the line after the
# step is reached with the code recorded -- and the right words said.
#
# Needs a real bash. On Windows "bash" on PATH is the WSL launcher, so Git
# for Windows' bash is used when present; otherwise the run is skipped (said
# so, not passed).
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def find_bash():
    if os.name != "nt":
        return shutil.which("bash")
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.exists(cand):
            return cand
    return None


src = open(os.path.join(ROOT, "scripts", "install.sh"), "rb").read() \
    .decode("utf-8").replace("\r\n", "\n")
m = re.search(r"^# 7f\. Detection models\n(.*?)^# -+\n# 8\. systemd units",
              src, re.S | re.M)
check("install.sh has step 7f before step 8", m is not None)
step = m.group(1) if m else ""
check("...and the step calls the bootstrap module as the service account",
      "core.services.model_bootstrap" in step and 'sudo -u "$SERVICE_USER"' in step)

bash = find_bash()
if not bash or not step:
    print("skip running step 7f: no real bash on this machine"
          if not bash else "skip running step 7f: step not found")
else:
    work = tempfile.mkdtemp()
    step_path = os.path.join(work, "step7f.sh")
    with open(step_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(step)
    cases = [(0, "detection models ready"),
             (1, "could not be downloaded"),
             (2, "no Hailo chip identified yet (normal on a new box before its first reboot)"),
             (137, "could not be downloaded")]
    for rc, words in cases:
        for target, label in ((work, ""), ("/nonexistent_iriv_dir", ", TARGET missing")):
            if label and rc != 0:
                continue
            script = (
                "set -euo pipefail\n"
                "say()  { printf '== %s\n' \"$*\"; }\n"
                "note() { printf '   %s\n' \"$*\"; }\n"
                "warn() { printf 'WARN: %s\n' \"$*\" >&2; }\n"
                f"sudo() {{ return {rc}; }}\n"
                f"INSTALL_SERVICE=1; TARGET='{target}'; SERVICE_USER=cytron; PY=python3\n"
                f"source '{step_path.replace(os.sep, '/')}'\n"
                "echo \"NEXT-STEP-REACHED rc=$models_rc\"\n")
            r = subprocess.run([bash, "-c", script], capture_output=True, text=True)
            out = r.stdout + r.stderr
            want_rc = 1 if label else rc
            want_words = "could not be downloaded" if label else words
            # The marker line IS the proof: it is echoed after the step, so
            # under set -e it only prints if the step did not end the script.
            # bash's own exit status is not asserted as well -- under a loaded
            # parallel run Git for Windows' bash once exited 1 AFTER printing
            # the marker with the right code (nothing on stderr to say why),
            # which is the shell runtime, not install.sh.
            check(f"bootstrap exit {rc}{label}: the install goes on (rc={want_rc})",
                  f"NEXT-STEP-REACHED rc={want_rc}" in out,
                  f"bash exit {r.returncode}\n{out}")
            check(f"...and says: {want_words}", want_words in out, out)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all install step 7f checks passed")
