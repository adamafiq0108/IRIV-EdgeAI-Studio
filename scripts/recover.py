#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recover.py -- the last line of defence for an interrupted update.

Runs once at boot, before irivai-hub.service, and normally does nothing at
all. Its whole job is the case nobody plans for: the board lost power (or was
switched off, or wedged) part-way through an update, so the process that was
supposed to finish the job -- and to roll it back if it went wrong -- is gone.

Without this, a box in that state comes up on whichever release the symlink
happened to be pointing at when the lights went out, with no one to check
whether that release ever passed its health gate. If it does not start,
systemd restarts it every five seconds forever and the appliance is a brick
that only a site visit can fix.

The rule is deliberately blunt: if the last status says a job was in flight,
and that job's process is not alive from THIS boot, put the previous release
back. A box that comes up on the version it was already running is a box
someone can update again. A box that comes up on a half-installed one is not.

Installed at /usr/local/lib/irivai/recover.py -- OUTSIDE every release
directory, because a repair tool that lives in the thing being repaired is
not a repair tool. Stdlib only, for the same reason.
"""

import json
import os
import sys
import time

#: Phases where the live tree is (or may be) mid-change. Anything else either
#: had not touched it yet or had already finished with it.
IN_FLIGHT = ("flip", "restart", "gate", "appsync", "rollback")

#: Written by the installer, because this script runs as root at boot and the
#: store root is resolved partly from the asking user's home directory --
#: searching root's home would find nothing and quietly repair nothing.
CONF = "/etc/irivai/update.conf"

STORE_CANDIDATES = ("/mnt/nvme/cytron/store",
                    "/home/pi/.local/share/irivai-systor",
                    os.path.expanduser("~/.local/share/irivai-systor"))

LIVE_LINK = "/opt/irivai-systor"
RELEASES = "/opt/irivai-releases"


def _abs_value(value):
    """A conf value this script may act on: absolute path or nothing.

    The same rule core/services/update.py applies to the same file, for the
    same reason: this conf is written by a shell script, and an installer once
    captured a store-resolution warning banner INTO a value. This script runs
    as root at boot and flips symlinks with what it reads here -- a relative or
    garbage value must degrade to the built-in default, never be flipped to.

    POSIX-absolute, by prefix rather than os.path.isabs: the conf describes an
    appliance's filesystem, and the dev-box test suite parses the same file on
    Windows, where a newer Python calls "/opt/..." relative.
    """
    value = str(value or "").strip().strip('"').strip("'")
    return value if value.startswith("/") else ""


def _read_conf():
    global LIVE_LINK, RELEASES
    out = {}
    try:
        with open(CONF, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip()
    except OSError:
        return ""
    LIVE_LINK = _abs_value(out.get("LIVE_LINK")) or LIVE_LINK
    RELEASES = _abs_value(out.get("RELEASES_DIR")) or RELEASES
    return _abs_value(out.get("UPDATE_DIR"))


def log(msg):
    sys.stdout.write("irivai-recover: " + msg + "\n")
    sys.stdout.flush()


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def update_dir():
    told = _read_conf()
    if told and os.path.isdir(told):
        return told
    for base in STORE_CANDIDATES:
        path = os.path.join(base, "update")
        if os.path.isdir(path):
            return path
    return ""


def semver(v):
    out = []
    for piece in str(v or "").lstrip("vV").split(".")[:3]:
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def write_status(work, data):
    path = os.path.join(work, "status.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def flip(target):
    tmp = LIVE_LINK + ".new"
    if os.path.islink(tmp) or os.path.exists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, LIVE_LINK)


def main():
    work = update_dir()
    if not work:
        return 0
    try:
        with open(os.path.join(work, "status.json"), encoding="utf-8") as f:
            status = json.load(f)
    except (OSError, ValueError):
        return 0

    phase = str(status.get("phase") or "")
    if phase not in IN_FLIGHT:
        return 0
    # The updater may legitimately still be running -- systemd starts units in
    # parallel, and an update that survives a hub restart looks exactly like
    # this from here. Only a job from a PREVIOUS boot, or one whose process is
    # gone, is actually abandoned.
    if status.get("boot_id") == boot_id() and alive(status.get("pid")):
        return 0

    # Clear the maintenance flag either way: whatever it was protecting is
    # over, and leaving it set means the hub never starts a system again.
    try:
        os.remove(os.path.join(work, "maintenance"))
    except OSError:
        pass

    running = os.path.basename(os.path.realpath(LIVE_LINK))
    # The release to restore is the one the updater NAMED, not the newest one
    # lying about. "Newest other release" is exactly wrong twice over: during
    # an update it is the half-proved release being installed, and during a
    # rollback it is the version someone was deliberately escaping.
    previous = str(status.get("previous") or "")
    if previous and os.path.isdir(previous):
        name = os.path.basename(previous)
    else:
        try:
            others = sorted((n for n in os.listdir(RELEASES)
                             if n != running
                             and os.path.isdir(os.path.join(RELEASES, n))),
                            key=semver)
        except OSError:
            others = []
        # Last resort, and only downwards: without a recorded target the only
        # safe direction is a version older than the one being installed.
        older = [n for n in others if semver(n) < semver(status.get("version"))]
        if not older:
            log(f"interrupted update in phase '{phase}' and no recorded "
                f"release to fall back to -- leaving {running} in place")
            return 0
        name = older[-1]
        previous = os.path.join(RELEASES, name)

    if os.path.realpath(previous) == os.path.realpath(LIVE_LINK):
        log(f"interrupted update in phase '{phase}' -- {running} is already "
            f"the release to run; clearing the job")
        write_status(work, dict(status, phase="rolled_back", percent=100,
                                pid=os.getpid(), boot_id=boot_id(),
                                error=f"the update was interrupted during "
                                      f"'{phase}'"))
        return 0

    log(f"interrupted update in phase '{phase}' -- restoring {name}")
    try:
        flip(previous)
    except OSError as exc:
        log(f"could not restore: {exc}")
        return 1
    write_status(work, {
        "phase": "rolled_back", "percent": 100,
        "detail": f"an interrupted update was rolled back to {name}",
        "error": f"the update was interrupted during '{phase}'",
        "version": name.lstrip("vV"), "previous": "",
        "started": int(status.get("started") or 0),
        "pid": os.getpid(), "boot_id": boot_id(),
        "recovered_at": int(time.time())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
