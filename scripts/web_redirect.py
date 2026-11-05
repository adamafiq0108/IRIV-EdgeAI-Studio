#!/usr/bin/python3 -I
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
web_redirect.py -- port 80 to the hub's port, 443 to its HTTPS port, or no
redirect at all. Run as root through sudo by the hub (core/services/
webaccess.py, httpsrun.py), installed root-owned at
/usr/local/lib/irivai/web-redirect.py by scripts/install.sh and
scripts/upgrade_steps.sh. JSON on stdin, nothing on argv, so the sudoers
rule names this file with no arguments (the set-hostname pattern):

    {"action": "on", "port": 5100}       redirect TCP 80 -> 5100
    {"action": "off"}                    remove it
    {"action": "tls_on", "port": 8443}   redirect TCP 443 -> 8443 (HTTPS)
    {"action": "tls_off"}                remove that one

WHY A REDIRECT AND NOT A LISTENER: below 1024 a port needs root, and the
hub is not root. Giving it CAP_NET_BIND_SERVICE was rejected for Modbus 502
(Pipi, 2026-10-05) for the same reason it is not used here: the capability
lets the holder bind ANY low port on the site LAN. Each table moves one
port to one port, nothing else.

The tables, inet irivai_web for 80 and inet irivai_web_tls for 443 (IPv4
and IPv6; separate, so HTTPS works in port mode and proxy mode alike):
  prerouting  iifname != "lo", addressed to THIS box (fib daddr type
              local), tcp dport 80 -> :<port>   (the LAN, the hotspot)
  output      oifname  "lo"   tcp dport 80 -> :<port>   (the box's own kiosk
                                                          browser, the hub's
                                                          liveness probe)
Replaced atomically (add + delete + define in one nft transaction) and
recorded in /etc/irivai/web-redirect.nft (web-redirect-tls.nft for 443),
which is also what tells the software the redirect is set. The kernel
table does not survive a reboot on its own: the hub asks for it again at
every start, with the port it bound.

Re-validates everything itself: the caller's checks are advice, this is the
gate. Refuses a port outside 1024-65535, a port the box reserves, and any
"on" while another program listens on 80 (or 443 for tls_on): it would be
silently stolen.
"""
import json
import os
import subprocess
import sys

NFT = "/usr/sbin/nft"
TABLE = "irivai_web"
STATE = "/etc/irivai/web-redirect.nft"
TLS_TABLE = "irivai_web_tls"
TLS_STATE = "/etc/irivai/web-redirect-tls.nft"
#: Ports the hub may never be on (core/services/hubport.py RESERVED, the
#: ones a redirect would hand a browser to): the device agent, Modbus.
RESERVED = {5101, 5020}


def fail(msg):
    print("web-redirect: " + msg, file=sys.stderr)
    return 2


def listening_on_80(paths=("/proc/net/tcp", "/proc/net/tcp6"), port=80):
    """Some program LISTENS on TCP `port` (80 unless told)."""
    tail = ":%04X" % port
    for path in paths:
        try:
            with open(path, encoding="ascii") as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            if len(cols) > 3 and cols[3] == "0A" and cols[1].endswith(tail):
                return True
    return False


def ruleset(port, table=TABLE, front=80):
    """The whole nft script, `front` -> `port` (ints already checked)."""
    return (
        "add table inet %(t)s\n"
        "delete table inet %(t)s\n"
        "table inet %(t)s {\n"
        "    chain prerouting {\n"
        "        type nat hook prerouting priority dstnat; policy accept;\n"
        "        iifname != \"lo\" fib daddr type local tcp dport %(f)d redirect to :%(p)d\n"
        "    }\n"
        "    chain output {\n"
        "        type nat hook output priority -100; policy accept;\n"
        "        oifname \"lo\" tcp dport %(f)d redirect to :%(p)d\n"
        "    }\n"
        "}\n") % {"t": table, "p": port, "f": front}


def parse(raw):
    """(action, port) from the stdin JSON, or raise ValueError."""
    try:
        req = json.loads(raw)
    except ValueError:
        raise ValueError("stdin is not JSON") from None
    if not isinstance(req, dict):
        raise ValueError("stdin is not a JSON object")
    action = req.get("action")
    if action in ("off", "tls_off"):
        return action, None
    if action not in ("on", "tls_on"):
        raise ValueError("action must be on or off (or tls_on / tls_off)")
    port = req.get("port")
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError("port must be a whole number")
    if not 1024 <= port <= 65535 or port in RESERVED:
        raise ValueError("port %d is not a port the hub may be on" % port)
    return action, port


def turn_off(run=subprocess.run, tls=False):
    table, state = (TLS_TABLE, TLS_STATE) if tls else (TABLE, STATE)
    run([NFT, "delete", "table", "inet", table], capture_output=True, timeout=30)
    try:
        os.unlink(state)
    except FileNotFoundError:
        pass
    return 0


def turn_on(port, run=subprocess.run, tls=False):
    table, state, front = (TLS_TABLE, TLS_STATE, 443) if tls else (TABLE, STATE, 80)
    if listening_on_80(port=443) if tls else listening_on_80():
        return fail("another program already listens on port %d" % front)
    os.makedirs(os.path.dirname(state), mode=0o755, exist_ok=True)
    tmp = state + ".irivai-tmp"
    with open(tmp, "w", encoding="ascii") as fh:
        fh.write(ruleset(port, table, front))
    os.chmod(tmp, 0o644)
    r = run([NFT, "-f", tmp], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        os.unlink(tmp)
        return fail("nft: " + ((r.stderr or "").strip() or "failed"))
    os.replace(tmp, state)
    print(json.dumps({"ok": True, "port": port}))
    return 0


def main():
    if os.geteuid() != 0:
        return fail("must run as root")
    if not os.path.exists(NFT):
        return fail("nftables is not installed")
    try:
        action, port = parse(sys.stdin.readline(200))
    except ValueError as exc:
        return fail(str(exc))
    tls = action.startswith("tls_")
    if action.endswith("off"):
        return turn_off(tls=tls)
    return turn_on(port, tls=tls)


if __name__ == "__main__":
    sys.exit(main())
