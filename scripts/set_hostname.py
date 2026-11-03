#!/usr/bin/python3 -I
# -*- coding: utf-8 -*-
"""
set_hostname.py -- rename the box. Run as root through sudo by the hub
(core/services/hostname.py), installed root-owned at
/usr/local/lib/irivai/set-hostname.py by scripts/install.sh. The name arrives
on stdin, one line; nothing on argv, so the sudoers rule names this file with
no arguments (the mqtt-apply pattern).

WHY ONE HELPER: a rename needs root three times -- hostnamectl, the
127.0.1.1 line in /etc/hosts (without it every sudo prints "unable to resolve
host" and waits on DNS first, the hub's own sudo calls included) and avahi
re-announcing the new <name>.local. `nmcli general hostname` would do only the
first. Design: docs/team/kokoci/2026-10-05-hostname.md.

Re-validates the name itself: the caller's check is advice, this is the gate.
Writes only the hostname, that one /etc/hosts line, an avahi restart, and the
stale Chromium lock files that name the OLD host (Chromium refuses to start on
a profile it thinks belongs to another computer).
"""
import os
import re
import subprocess
import sys

LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def fail(msg):
    print("set-hostname: " + msg, file=sys.stderr)
    return 2


def _clear_chromium_locks(old):
    """Same rule as install.sh: only with no chromium running, and only a lock
    that names the old host -- it is stale by definition then."""
    if subprocess.run(["pgrep", "-x", "chromium"], capture_output=True).returncode == 0:
        return
    try:
        homes = ["/home/" + d for d in os.listdir("/home")]
    except OSError:
        homes = []
    for base in ["/root"] + homes:
        prof = os.path.join(base, ".config", "chromium")
        lock = os.path.join(prof, "SingletonLock")
        try:
            if not (os.path.islink(lock) and os.readlink(lock).startswith(old + "-")):
                continue
        except OSError:
            continue
        for f in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                os.unlink(os.path.join(prof, f))
            except OSError:
                pass


def main():
    if os.geteuid() != 0:
        return fail("must run as root")
    name = sys.stdin.readline(80).strip()
    if not LABEL.match(name) or name.isdigit() or name == "localhost":
        return fail("invalid name")
    old = os.uname().nodename.split(".")[0]
    r = subprocess.run(["/usr/bin/hostnamectl", "set-hostname", name],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return fail("hostnamectl: " + (r.stderr.strip() or "failed"))
    # /etc/hosts: the 127.0.1.1 line names this box, or every sudo waits on DNS.
    with open("/etc/hosts", encoding="utf-8") as fh:
        lines = fh.read().splitlines(True)
    line = "127.0.1.1\t%s\n" % name
    done = False
    for i, text in enumerate(lines):
        if re.match(r"127\.0\.1\.1\s", text):
            lines[i], done = line, True
    if not done:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(line)
    tmp = "/etc/hosts.irivai-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.chmod(tmp, 0o644)
    os.replace(tmp, "/etc/hosts")
    subprocess.run(["/usr/bin/systemctl", "restart", "avahi-daemon"],
                   capture_output=True, timeout=30)
    _clear_chromium_locks(old)
    print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
