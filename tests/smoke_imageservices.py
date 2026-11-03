# The OS image's spare listeners are off on an appliance, each with a flag.
#
# Raspberry Pi OS ships a desktop image: wayvnc (a remote desktop on 5900),
# rpcbind (NFS's port mapper on 111, to the whole LAN) and a login getty on
# the UART -- the UART the RS485 port is muxed with, so a Modbus RTU master
# on that bus was talking to a login prompt. Pipi's board follow-up asked for
# all three off by default (item 4); scripts/dev/harden_box.sh did it by hand
# on .21.
#
# Pinned: install.sh disables each (socket AND service for rpcbind), only
# when the unit exists, with --keep-vnc / --keep-rpcbind /
# --keep-serial-console to keep one; the kernel console comes off the UART in
# cmdline.txt with a backup and a sanity check -- and the sed itself is RUN
# against the cmdline lines a CM5 image actually carries.
#
#     python tests/smoke_imageservices.py
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


with open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8") as fh:
    inst = fh.read()

for flag, var in (("--keep-vnc", "KEEP_VNC"), ("--keep-rpcbind", "KEEP_RPCBIND"),
                  ("--keep-serial-console", "KEEP_SERIAL_CONSOLE")):
    check(f"{flag} is parsed, defaults off and is documented",
          f"{flag})" in inst and f"{var}=1" in inst and f"{var}=0" in inst
          and inst.count(flag) >= 3)

step = inst.split("# THE IMAGE'S OTHER LISTENERS.", 1)
check("install.sh has the step, inside Appliance defaults", len(step) == 2
      and inst.index('say "Appliance defaults"') < inst.index("# THE IMAGE'S OTHER LISTENERS.")
      < inst.index('say "Hardening check"'))
step = step[1].split("# FRONT PANEL ON.", 1)[0] if len(step) == 2 else ""
check("a unit is only touched when the image has it",
      'systemctl cat "$unit" >/dev/null 2>&1 || continue' in step)
check("...and is disabled AND stopped, not removed",
      'systemctl disable --now "$unit"' in step and "apt-get" not in step
      and "purge" not in step)
check("wayvnc and its control service",
      "wayvnc.service wayvnc-control.service" in step)
check("rpcbind's socket as well as its service (the socket restarts it)",
      "rpcbind.socket rpcbind.service" in step)
check("the UART getty", "serial-getty@ttyAMA10.service" in step)
check("cmdline.txt: both locations, a backup kept once, put back if it looks wrong",
      "/boot/firmware/cmdline.txt /boot/cmdline.txt" in step
      and '[[ -f "${cmdline}.irivai-bak" ]] || cp -p' in step
      and "grep -q 'root='" in step and 'cp -p "${cmdline}.irivai-bak" "$cmdline"' in step)

sed = re.search(r"sed -i -E '([^']+)' \"\$cmdline\"", step)
check("the cmdline edit is one sed expression", sed is not None)
bash = shutil.which("bash")
if sed and bash:
    cases = (
        ("console=serial0,115200 console=tty1 root=PARTUUID=ab-02 rootfstype=ext4 fsck.repair=yes rootwait",
         "console=tty1 root=PARTUUID=ab-02 rootfstype=ext4 fsck.repair=yes rootwait"),
        ("console=tty1 root=PARTUUID=ab-02 rootwait console=ttyAMA10,115200",
         "console=tty1 root=PARTUUID=ab-02 rootwait"),
        ("console=tty1 root=/dev/mmcblk0p2 rootwait",
         "console=tty1 root=/dev/mmcblk0p2 rootwait"),
    )
    for before, want in cases:
        run = subprocess.run([bash, "-c", "sed -E '" + sed.group(1) + "'"],
                             input=before + "\n", capture_output=True, text=True)
        check("cmdline: " + before[:40] + "...", run.stdout == want + "\n",
              repr(run.stdout))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
