# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
apply_release.py -- switch the box to another installed release.

Run as root through sudo by the hub (core/services/manual_update.py), installed
root-owned at /usr/local/lib/irivai/apply-release.py by scripts/install.sh. The
hub runs as the service user and cannot write to /opt, which is where the live
link lives; this is the one step that needs root.

    /opt/irivai-systor          a symlink to the live release
    /opt/irivai-releases/<name> one folder per installed version
    /opt/irivai-releases/.previous   the name of the release that was live before

Input on stdin, two lines, nothing on argv (the sudoers rule names the script
with no arguments):

    apply       switch to <name>, keep the old live release as the previous one
    rollback    switch back to the previous release
    <name>      the release folder to switch to (ignored by rollback)

Only two releases are kept: the live one and the previous one. Everything else
in /opt/irivai-releases is removed after a switch, so a box that is updated
for years does not fill its drive.

The switch is one rename over the link, so the box has a live release at every
instant; a power cut leaves it on the old one or the new one, never neither.
Then the hub and every systemd master are restarted (without waiting: the hub
is the caller, and it is among what restarts).
"""
import json
import os
import re
import shutil
import subprocess
import sys

RELEASES = "/opt/irivai-releases"
LINK = "/opt/irivai-systor"
PREVIOUS = os.path.join(RELEASES, ".previous")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
REQUIRED = ("hub_server.py", "master_server.py", "VERSION", "core")


def fail(msg):
    print(json.dumps({"ok": False, "error": msg}))
    return 2


def _inside(path):
    real = os.path.realpath(path)
    return real.startswith(RELEASES + os.sep) and os.path.isdir(real)


def _check_target(name):
    if not NAME.match(name or ""):
        return None, "that is not a release name"
    target = os.path.join(RELEASES, name)
    if os.path.islink(target) or not _inside(target):
        return None, "no such release"
    missing = [r for r in REQUIRED if not os.path.exists(os.path.join(target, r))]
    if missing:
        return None, "the release is incomplete (missing " + ", ".join(missing) + ")"
    return target, None


def _read_previous():
    try:
        with open(PREVIOUS, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _switch(target):
    tmp = LINK + ".new"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    os.symlink(target, tmp)
    os.replace(tmp, LINK)                       # atomic: one rename over the link


def _prune(keep):
    for entry in os.listdir(RELEASES):
        path = os.path.join(RELEASES, entry)
        if entry.startswith(".") or entry in keep or os.path.islink(path):
            continue
        if NAME.match(entry) and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def _restart():
    subprocess.run(["/usr/bin/systemctl", "--no-block", "restart", "irivai-hub.service"],
                   capture_output=True, timeout=30)
    listed = subprocess.run(
        ["/usr/bin/systemctl", "list-units", "--no-legend", "--plain", "--state=active",
         "irivai-master@*.service"], capture_output=True, text=True, timeout=30).stdout
    for line in listed.splitlines():
        unit = line.split()[0] if line.split() else ""
        if re.match(r"^irivai-master@[a-z0-9_-]+\.service$", unit):
            subprocess.run(["/usr/bin/systemctl", "--no-block", "restart", unit],
                           capture_output=True, timeout=30)


def main():
    if os.geteuid() != 0:
        return fail("must run as root")
    action = sys.stdin.readline(20).strip()
    name = sys.stdin.readline(60).strip()
    if action not in ("apply", "rollback"):
        return fail("action is apply or rollback")
    if not os.path.islink(LINK):
        return fail("this box is not set up for updates (the live folder is not a link)")
    live = os.path.basename(os.path.realpath(LINK))
    if action == "rollback":
        name = _read_previous()
        if not name:
            return fail("there is no previous version to go back to")
    target, err = _check_target(name)
    if err:
        return fail(err)
    if os.path.basename(target) == live:
        return fail("that version is already the live one")
    # The Python environment is shared between releases: link it in if the new
    # release does not carry its own.
    venv = os.path.join(target, ".venv")
    if not os.path.exists(venv):
        os.symlink(os.path.realpath(os.path.join(LINK, ".venv")), venv)
    _switch(target)
    with open(PREVIOUS, "w", encoding="utf-8") as fh:
        fh.write(live + "\n")
    _prune({os.path.basename(target), live})
    _restart()
    print(json.dumps({"ok": True, "live": os.path.basename(target), "previous": live}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
