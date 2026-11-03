# -*- coding: utf-8 -*-
"""
portpids.py -- which local processes LISTEN on a TCP port, without lsof.

provision.stop_master used to ask `lsof -ti tcp:<port>` and nothing
installed or checked lsof. Pi OS ships it as a standard package, so the
field boxes had it; a minimal image did not (Yaya's Docker OTA rehearsal,
2026-10-05): stop_master quietly found nothing, the replacement master died
on "address already in use", verify_masters saw the OLD version still
answering and rolled every update back, and Remove on the Hub page left
the master squatting on its port.

The kernel already publishes the answer: /proc/net/tcp and tcp6 list every
socket with its state and inode, and /proc/<pid>/fd links the inode to the
process that holds it. Two reads and a walk, no package. lsof stays as a
fast path when it IS there (one call instead of a walk over /proc), and
the /proc walk runs whenever lsof is missing, fails, or names nothing.

A process's fd dir is readable only by its owner (or root): a master run
by another account is invisible here, as it was to lsof without root.
"""

import os
import shutil
import subprocess

#: /proc/net/tcp state column for LISTEN.
_LISTEN = "0A"


def _listen_inodes(port, proc):
    """Socket inodes listening on `port`, from /proc/net/tcp and tcp6."""
    want = f":{int(port):04X}"
    out = set()
    for name in ("tcp", "tcp6"):
        try:
            with open(os.path.join(proc, "net", name), encoding="ascii") as fh:
                next(fh, None)                               # the header line
                for line in fh:
                    cols = line.split()
                    if len(cols) > 9 and cols[1].endswith(want) \
                            and cols[3] == _LISTEN:
                        out.add(cols[9])
        except (OSError, StopIteration):
            continue
    return out


def _pids_holding(inodes, proc):
    """The pids whose fd table holds any of `inodes` (socket:[<inode>])."""
    want = {f"socket:[{i}]" for i in inodes}
    found = set()
    if not want:
        return found
    try:
        entries = os.listdir(proc)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        fd_dir = os.path.join(proc, entry, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue                        # another account's process, or gone
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) in want:
                    found.add(int(entry))
                    break
            except OSError:
                continue
    return found


def _lsof(port):
    """lsof's answer when it is installed and finds something, else None."""
    if not shutil.which("lsof"):
        return None
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{int(port)}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pids = {int(p) for p in out.split() if p.strip().isdigit()}
    return pids or None


def listening_pids(port, proc="/proc"):
    """The set of local pids listening on TCP `port` (Linux). lsof first
    when installed, the /proc walk otherwise or when lsof names nothing.
    Empty on a box with neither (nothing to signal, said truthfully)."""
    pids = _lsof(port)
    if pids:
        return pids
    return _pids_holding(_listen_inodes(port, proc), proc)
