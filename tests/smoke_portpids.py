# -*- coding: utf-8 -*-
# Stopping a master must not depend on lsof (core/services/portpids.py).
# Yaya's Docker OTA rehearsal (2026-10-05): a python:3.13 image has no lsof,
# provision.stop_master found nothing, the new master died on "address
# already in use" and every update rolled back ("demo (still on 1.0.15)").
#
# Pinned:
#   1. the /proc walk on a fake tree: a LISTEN row on the port in tcp and
#      tcp6 maps to the pid holding that socket inode; a non-LISTEN row, a
#      row on another port, a pid whose fd dir cannot be read, and a stale
#      fd link are all skipped
#   2. lsof hidden (shutil.which -> None): listening_pids never runs lsof
#      and still answers from /proc; lsof present: its answer is used
#      first, and an lsof that names nothing falls through to /proc
#   3. live, on Linux: this process listens on a free port, lsof hidden,
#      and stop_master signals exactly this pid (kill captured, not run)
#   4. the sources: provision no longer calls lsof itself; install.sh lists
#      lsof among the apt packages (belt and braces); no em dash
#
#   py -3 tests/smoke_portpids.py
import io
import os
import socket
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import portpids, provision                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. the /proc walk on a fake tree -------------------------------------------------
PROC = tempfile.mkdtemp()
os.makedirs(os.path.join(PROC, "net"))
HEAD = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
        "retrnsmt   uid  timeout inode\n")


def row(sl, port, st, inode, width=8):
    zero = "0" * width
    return (f"   {sl}: {zero}:{port:04X} {zero}:0000 {st} 00000000:00000000 "
            f"00:00000000 00000000  1000        0 {inode} 1 0 100 0 0 10 0\n")


with open(os.path.join(PROC, "net", "tcp"), "w", encoding="ascii") as f:
    f.write(HEAD + row(0, 5110, "0A", 1111) + row(1, 5110, "01", 2222)
            + row(2, 5120, "0A", 3333))
with open(os.path.join(PROC, "net", "tcp6"), "w", encoding="ascii") as f:
    f.write(HEAD + row(0, 5110, "0A", 4444, width=32))


def pid_with(pid, links):
    fd = os.path.join(PROC, str(pid), "fd")
    os.makedirs(fd)
    for i, target in enumerate(links):
        try:
            os.symlink(target, os.path.join(fd, str(i)))
        except OSError as exc:                      # a Windows box without the privilege
            print(f"skip: this box cannot create symlinks ({exc}); fake /proc tree not built")
            sys.exit(0)


pid_with(100, ["/dev/null", "socket:[1111]"])        # the tcp listener
pid_with(200, ["socket:[2222]"])                     # a connected client
pid_with(300, ["socket:[4444]"])                     # the tcp6 listener
pid_with(400, ["socket:[3333]"])                     # another port
pid_with(500, [])                                    # no fds at all
os.makedirs(os.path.join(PROC, "600"))               # fd dir unreadable/absent
os.makedirs(os.path.join(PROC, "self", "fd"))        # not a pid: skipped
inodes = portpids._listen_inodes(5110, PROC)
check("1. the LISTEN rows on the port, tcp and tcp6, not the connected one",
      inodes == {"1111", "4444"}, inodes)
pids = portpids._pids_holding(inodes, PROC)
check("   ...map to the pids holding those inodes", pids == {100, 300}, pids)
check("   another port maps elsewhere",
      portpids._pids_holding(portpids._listen_inodes(5120, PROC), PROC) == {400})
check("   a port nobody listens on: nothing",
      portpids._pids_holding(portpids._listen_inodes(5999, PROC), PROC) == set())
check("   a missing /proc: nothing, no error",
      portpids.listening_pids(5110, os.path.join(PROC, "nope")) == set())

# -- 2. lsof hidden / present ------------------------------------------------------------
real_which, real_run = portpids.shutil.which, portpids.subprocess.run
RAN = []


def fake_run(argv, **kw):
    RAN.append(list(argv))
    if argv[0] == "lsof":
        if LSOF["raise"]:
            raise FileNotFoundError("lsof")
        return type("R", (), {"stdout": LSOF["out"], "returncode": 0})()
    return real_run(argv, **kw)


LSOF = {"out": "", "raise": False}
portpids.subprocess.run = fake_run
try:
    portpids.shutil.which = lambda name: None
    check("2. lsof hidden: the /proc walk answers and lsof is never run",
          portpids.listening_pids(5110, PROC) == {100, 300}
          and not any(a[0] == "lsof" for a in RAN), RAN)
    portpids.shutil.which = lambda name: "/usr/bin/lsof" if name == "lsof" else None
    LSOF["out"] = "777\n888\n"
    check("   lsof present: its answer comes first",
          portpids.listening_pids(5110, PROC) == {777, 888}
          and RAN[-1][:3] == ["lsof", "-ti", "tcp:5110"], RAN[-1:])
    LSOF["out"] = ""
    check("   lsof that names nothing: /proc still answers",
          portpids.listening_pids(5110, PROC) == {100, 300})
    LSOF["raise"] = True
    check("   lsof that will not run (the binary vanished): /proc still answers",
          portpids.listening_pids(5110, PROC) == {100, 300})
finally:
    portpids.shutil.which, portpids.subprocess.run = real_which, real_run

# -- 3. live: stop_master without lsof (Linux: /proc is real) ----------------------------
if os.name != "nt" and os.path.isdir("/proc/net"):
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    KILLS = []
    real_prun = provision.subprocess.run

    def capture(argv, **kw):
        if argv[0] == "kill":
            KILLS.append(list(argv))
            return type("R", (), {"stdout": "", "returncode": 0})()
        if argv[0] == "lsof":
            raise FileNotFoundError("lsof")
        return real_prun(argv, **kw)

    portpids.shutil.which = lambda name: None
    provision.subprocess.run = capture
    try:
        check("3. lsof hidden: this process is found listening on its own port",
              portpids.listening_pids(port) == {os.getpid()},
              portpids.listening_pids(port))
        ok = provision.stop_master(f"http://127.0.0.1:{port}")
        check("   stop_master signals exactly this pid and reports True",
              ok is True and KILLS == [["kill", str(os.getpid())]], (ok, KILLS))
        held.close()
        KILLS.clear()
        check("   with the socket closed: nothing signalled, False",
              provision.stop_master(f"http://127.0.0.1:{port}") is False and KILLS == [])
    finally:
        portpids.shutil.which = real_which
        provision.subprocess.run = real_prun
        held.close()
else:
    print("skip 3. live /proc check (not Linux)")

# -- 4. the sources -------------------------------------------------------------------------


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


prov = read("core", "services", "provision.py")
body = prov.split("def stop_master(url):", 1)[1].split("\ndef ", 1)[0]
check("4. stop_master asks portpids, never lsof by itself",
      "portpids.listening_pids(port)" in body and '"lsof"' not in body)
inst = read("scripts", "install.sh")
pkgs = inst.split("APT_PACKAGES=(", 1)[1].split("\n)", 1)[0]
check("   install.sh lists lsof among the apt packages",
      any(tok == "lsof" for ln in pkgs.splitlines() if not ln.strip().startswith("#")
          for tok in ln.split()), pkgs)
check("   no em dash", all(chr(0x2014) not in s for s in (
    prov, read("core", "services", "portpids.py"), read(__file__))))

print()
print("all good" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
