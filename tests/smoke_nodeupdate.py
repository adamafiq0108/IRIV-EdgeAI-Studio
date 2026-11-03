# The installer keeps Node.js and Node-RED current.
#
# scripts/install.sh moves Node.js onto the latest LTS (NodeSource) and updates
# Node-RED, where the board has it, to its latest release with npm. Neither
# failing stops the install, Node-RED is left on or off as it was, and
# --skip-node-update leaves both alone. Source-level: the step itself needs
# apt, a network and root, so it is exercised on a box by running the installer.
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8").read()
bad = 0


def check(label, ok):
    global bad
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        bad += 1


check("Node.js is moved onto the latest LTS through NodeSource",
      "setup_lts.x" in src and "apt-get install -y nodejs" in src)
check("Node-RED is updated to its latest release with npm",
      "npm install -g --unsafe-perm node-red@latest" in src)
check("only where Node-RED is already on the board",
      "command -v node-red" in src)
check("a failed update keeps the old version and does not stop the install",
      "could not update Node.js -- keeping" in src and "could not update Node-RED -- keeping" in src)
check("--skip-node-update leaves both alone",
      "--skip-node-update)  SKIP_NODE_UPDATE=1" in src and "SKIP_NODE_UPDATE=0" in src)
check("the update never switches Node-RED on (the appliance default turns it off)",
      "systemctl enable nodered" not in src.split("Appliance defaults")[0]
      and "systemctl start nodered" not in src)
ok = True            # only where there is a real bash: a bare Windows box has none
if os.name != "nt":
    try:
        ok = subprocess.run(["bash", "-n", os.path.join(ROOT, "scripts", "install.sh")]).returncode == 0
    except OSError:
        ok = True
check("install.sh still parses", ok)

if bad:
    print(f"\n{bad} FAILED")
    raise SystemExit(1)
print("ALL OK")
