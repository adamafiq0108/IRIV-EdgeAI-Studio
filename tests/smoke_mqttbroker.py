# The installer puts an MQTT broker on the box.
#
# Asked for on 2026-09-29: .116 was installed without one, so the MQTT nodes,
# which default to 127.0.0.1:1883, had nothing to connect to. The installer
# now installs Mosquitto, enabled at boot, listening on loopback only -- an
# anonymous broker open to the plant LAN is not a default this product ships.
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


sh = read("scripts", "install.sh")
step = sh[sh.index("# 7e. The MQTT broker"):sh.index("# 8. systemd units")]

check("the broker is on by default", re.search(r"^INSTALL_MQTT=1$", sh, re.M))
check("...and --no-mqtt turns it off, documented in the help",
      "--no-mqtt)      INSTALL_MQTT=0" in sh and "  --no-mqtt " in sh)
check("the installer installs mosquitto", "apt-get install -y mosquitto" in step)
check("...enables it at boot", "systemctl enable mosquitto" in step)
check("...and binds it to loopback only", "listener 1883 127.0.0.1" in step
      and not re.search(r"listener 1883\s*$", step, re.M)
      and "0.0.0.0" not in step)
check("a port already served by something else is left alone",
      "sport = :1883" in step and "leaving that broker in place" in step)
check("...but Node-RED's broker is turned off first, as the appliance does anyway",
      '*node-red*' in step and "systemctl disable --now nodered" in step
      and step.index("disable --now nodered") < step.index("leaving that broker in place"))
check("a re-run keeps broker settings already on the box",
      "if [[ -f /etc/mosquitto/conf.d/irivai.conf ]]" in step
      and "keeping the broker settings already on this box" in step)
check("a failed install warns rather than ending the install",
      "|| warn" in step and "die" not in step)
check("opening it to the LAN is Settings > MQTT Broker's job, through the helper",
      "Settings > MQTT Broker" in step
      and "/usr/local/lib/irivai/mqtt-apply.py" in sh
      and "NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/mqtt-apply.py" in sh)
check("the MQTT nodes default to the address the broker listens on",
      '"host": "127.0.0.1", "port": 1883' in read("core", "services", "workflow.py"))

if os.name == "nt":
    # "bash" on Windows is the WSL launcher, not a shell; CI parses it.
    print("skip install.sh parse (Windows)")
else:
    r = subprocess.run(["bash", "-n", os.path.join(ROOT, "scripts", "install.sh")],
                       capture_output=True, text=True)
    check("install.sh still parses", r.returncode == 0, r.stderr)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
