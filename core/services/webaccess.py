# -*- coding: utf-8 -*-
"""
webaccess.py -- how a browser reaches this box: by PORT, or through the
REVERSE PROXY on port 80. A setting, master or developer only.

Tok Aba (2026-10-07): an alternative to the port settings, so people open
the box with no port in the address. PORT mode is today's behaviour and the
default, unchanged: the hub on its Software port (hubport.py), each system
on its own (sysport.py). PROXY mode is one address: the hub at /, every
system at /s/<slug>/, relayed by the hub to that system's master on
127.0.0.1 (webproxy.py). Never a mix: the hub in proxy mode means every
link to every system is the proxy form too (weblinks.py).

PORT 80 NEEDS ROOT, THE SOFTWARE DOES NOT GET IT. The hub keeps its own
port; an nftables redirect sends 80 to it, the Modbus 502 -> 5020 pattern
(scripts/nftables/modbus-redirect.nft). The table is written by ONE root
helper, /usr/local/lib/irivai/web-redirect.py (scripts/web_redirect.py),
JSON on stdin, the set-hostname pattern: root-owned outside every release,
an exact sudoers line, the port re-validated as root. Nothing the service
account writes is ever executed by root.

SAVED IS NOT APPLIED, like the port and the name (boxpending.py): Save
writes [hub].pending_access with its boot; the hub's first start in a later
boot takes it (apply_pending), then, once its port is known, asks the
helper for the redirect (ensure_redirect). A redirect that cannot be made
puts the box back in port mode and tells the admins: a box whose links say
"no port" while nothing answers on 80 is a box nobody can open.

Old addresses keep working in both modes: the hub and every master still
listen on their own ports (decision and reasons in weblinks.py).
"""

import json
import os
import shutil
import subprocess

from core import config
from core.services import hubport

PORT, PROXY = "port", "proxy"
MODES = (PORT, PROXY)
HTTP_PORT = 80

HELPER = "/usr/local/lib/irivai/web-redirect.py"
#: Exactly the sudoers entry install.sh and scripts/root_items.json write; -I so
#: the helper never imports from the caller's path.
HELPER_ARGV = ("sudo", "-n", "/usr/bin/python3", "-I", HELPER)
NFT = "/usr/sbin/nft"
#: Written by the helper only (root, 0644): present = the redirect is set.
STATE_FILE = "/etc/irivai/web-redirect.nft"


def needs_helper():
    """Why the helper cannot run here, and the one fix for THIS box
    (core/services/roothelp.py)."""
    from core.services import roothelp                       # noqa: PLC0415
    return roothelp.missing(HELPER, "irivai-web", "the box cannot answer on port 80")


class AccessError(ValueError):
    """Operator-facing refusal; the sentence is the message."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def mode(config_dir=None):
    """"port" or "proxy". Anything else in the file reads as port: a typo
    must never move the box's address."""
    hub = config.systems(config_dir).get("hub") or {}
    value = str(hub.get("access") or PORT).strip().lower()
    return value if value in MODES else PORT


def pending(config_dir=None):
    """The mode waiting for a restart, or ""."""
    hub = config.load_local("systems", config_dir).get("hub") or {}
    value = str(hub.get("pending_access") or "")
    return value if value in MODES else ""


def port80_listener(paths=("/proc/net/tcp", "/proc/net/tcp6")):
    """True if some program LISTENS on TCP 80, False if none, None where the
    kernel's table cannot be read (not Linux). Read, not bound: binding 80
    needs root, so a bind test would say "taken" on every box."""
    seen = False
    for path in paths:
        try:
            with open(path, encoding="ascii") as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        seen = True
        for row in rows:
            cols = row.split()
            if len(cols) > 3 and cols[3] == "0A" and cols[1].endswith(":0050"):
                return True
    return False if seen else None


def helper_installed():
    return os.path.isfile(HELPER)


def can_run():
    """Helper there AND sudo would run it without a password. `sudo -n -l`
    answers that and runs nothing (hostname.can_set)."""
    if not helper_installed() or not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-l", *HELPER_ARGV[2:]],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def nft_installed():
    return os.path.exists(NFT)


def redirect_set():
    """The helper's own record that the redirect is in place."""
    return os.path.isfile(STATE_FILE)


def check(config_dir=None):
    """The Test button for "Reverse proxy": every precondition, each with its
    own sentence. {ok, usable, checks: [{key, ok, detail}], detail}."""
    listener, helper, nft = port80_listener(), can_run(), nft_installed()
    checks = [
        {"key": "service", "ok": hubport.owns_setting(),
         "detail": "this is the box's own service" if hubport.owns_setting()
         else hubport.NOT_OWN_SERVICE},
        {"key": "nft", "ok": nft,
         "detail": "nftables is installed" if nft
         else "nftables is not installed on this box (scripts/install.sh "
              "installs it)"},
        {"key": "helper", "ok": helper,
         "detail": "the port 80 helper is installed" if helper
         else needs_helper()},
        {"key": "port80", "ok": listener is False,
         "detail": "port 80 is free" if listener is False
         else ("another program on this box already answers on port 80 - "
               "stop it first" if listener else
               "port 80 could not be checked on this system")},
    ]
    bad = [c for c in checks if not c["ok"]]
    return {"ok": True, "usable": not bad, "checks": checks,
            "detail": bad[0]["detail"] if bad else
            "ready - the box can answer on port 80"}


# ---------------------------------------------------------------------------
# Writing: saved now, applied at the next restart of the box
# ---------------------------------------------------------------------------

def _write(key_values, config_dir=None):
    local = dict(config.load_local("systems", config_dir))
    hub = dict(local.get("hub") or {})
    for key, value in key_values.items():
        if value in (None, ""):
            hub.pop(key, None)
        else:
            hub[key] = value
    local["hub"] = hub
    config.save_local("systems", local, config_dir)


def stage(value, boot, config_dir=None):
    """Save the mode for the next restart (the card's Apply). The mode the
    box is in now cancels a pending change. Proxy is refused NOW when the
    box could not apply it at the restart."""
    value = str(value or "").strip().lower()
    if value not in MODES:
        raise AccessError("choose Port or Reverse proxy")
    if not hubport.owns_setting():
        raise AccessError(hubport.NOT_OWN_SERVICE)
    if value == mode(config_dir):
        _write({"pending_access": None, "pending_access_boot": None}, config_dir)
        return None
    if value == PROXY:
        verdict = check(config_dir)
        if not verdict["usable"]:
            raise AccessError(verdict["detail"])
    _write({"pending_access": value, "pending_access_boot": str(boot or "")},
           config_dir)
    return value


def apply_pending(boot, config_dir=None):
    """At the hub's start (boxpending.apply_at_start): a mode saved in an
    EARLIER boot becomes the mode. None if nothing waits; else {ok, from,
    to, detail}. The pending value goes either way (no retry every boot)."""
    hub = config.load_local("systems", config_dir).get("hub") or {}
    want = str(hub.get("pending_access") or "")
    if want not in MODES or str(hub.get("pending_access_boot") or "") == str(boot):
        return None
    old = mode(config_dir)
    _write({"pending_access": None, "pending_access_boot": None}, config_dir)
    if want == PROXY:
        verdict = check(config_dir)
        if not verdict["usable"]:
            return {"ok": False, "from": old, "to": want, "detail": verdict["detail"]}
    _write({"access": want}, config_dir)
    return {"ok": True, "from": old, "to": want, "detail": ""}


def run_helper(payload, runner=subprocess.run):
    """(ok, sentence) from the root helper, the payload as JSON on stdin."""
    try:
        r = runner(list(HELPER_ARGV), input=json.dumps(payload) + "\n",
                   capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "the port 80 helper did not run: %s" % exc
    if r.returncode != 0:
        last = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["no reason given"]
        return False, last[0]
    return True, ""


def ensure_redirect(hub_port, config_dir=None, runner=subprocess.run):
    """hub_server.main, once the port to bind is known: the redirect the
    mode needs, made or removed. "" when all is well, else the sentence for
    the admins. Proxy that cannot be made falls back to port mode."""
    if not hubport.owns_setting():
        return ""
    if mode(config_dir) == PROXY:
        ok, why = (run_helper({"action": "on", "port": int(hub_port)}, runner)
                   if can_run() else (False, needs_helper()))
        if ok:
            return ""
        _write({"access": PORT}, config_dir)
        return ("The box could not answer on port 80 (%s), so it is back to "
                "addresses with a port. Settings -> Network -> Access." % why)
    if redirect_set() and can_run():
        run_helper({"action": "off"}, runner)
    return ""


def status(config_dir=None):
    """The Access card, minus the restart block the route adds."""
    from core.services import weblinks                        # noqa: PLC0415
    current = mode(config_dir)
    return {"mode": current, "pending": pending(config_dir) or None,
            "modes": list(MODES), "default": PORT,
            "live": weblinks.live() if current == PROXY else None,
            "helper": helper_installed(), "prefix": weblinks.PREFIX}


def panel_address(local_name, config_dir=None):
    """What the front panel prints: <name>.local, with the hub's port only
    in port mode (panel.py)."""
    if mode(config_dir) == PROXY:
        return local_name
    return "%s:%d" % (local_name, hubport.port(config_dir))


def advertised_port(config_dir=None):
    """The mDNS service's port: the one a browser should use."""
    return HTTP_PORT if mode(config_dir) == PROXY else hubport.port(config_dir)


def txt_extra(config_dir=None):
    """Extra mDNS TXT strings: none in port mode, so its record is exactly
    what it was; `access=proxy` and the systems' path prefix otherwise."""
    if mode(config_dir) != PROXY:
        return []
    return ["access=proxy", "systems_path=/s/"]
