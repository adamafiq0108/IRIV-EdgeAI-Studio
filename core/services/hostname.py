# -*- coding: utf-8 -*-
"""
hostname.py -- what the box calls itself on the LAN, and changing it.

Every box used to be named "iriv" by the installer, so every box claimed
iriv.local and avahi settled the race by renaming the losers iriv-2, iriv-3
in BOOT ORDER: the name on a front panel pointed at whichever box came up
first, and changed when the site's power came back in a different order.
A new install is named iriv-<last 8 of the serial> (hostinfo.default_
hostname, install.sh); an existing box keeps its name until master changes
it here.

READ: the hostname is asked of the kernel every time (a rename shows on the
panel without a restart), and local_name() prefers the name avahi actually
won over the one we asked for -- on a box still called "iriv" that is
"iriv-2.local", which is what a browser has to type.

WRITE needs root three times over: hostnamectl, the 127.0.1.1 line in
/etc/hosts (without it every sudo on the box waits on a DNS timeout), and
avahi re-announcing. That is ONE root helper, /usr/local/lib/irivai/set-
hostname.py, name on stdin, the mqtt-apply pattern -- installed with its
sudoers line by scripts/install.sh. Until a box has it, can_set() is False
and the page says so; nothing here falls back to a partial rename through
the nmcli rule, because a renamed box with a stale /etc/hosts is slower at
everything and says why nowhere.

SAVED IS NOT APPLIED (Tok Aba, 2026-10-05). stage() writes the name to
network.local.toml [box_name] with the boot it was saved in; the box keeps
its name. The hub's first start in a LATER boot (boxpending.apply_at_start,
before the mDNS advertisement, the front panel or any system starts) hands
it to the helper, which validates it again as root. A refusal, or a helper
gone since, leaves the old name and the page says what happened. Nothing
the service account can write is ever executed by root: the helper is
root-owned outside every release, and the name reaches it on stdin.

A rename does not touch any address. It drops exactly one thing: a browser
that reached the box as <old>.local -- the page names the new address
before it asks for the restart.
"""

import os
import re
import shutil
import socket
import subprocess

from core import config
from core.services import discovery_mdns, hostinfo, hubport

HELPER = "/usr/local/lib/irivai/set-hostname.py"
#: Exactly the sudoers entry install.sh writes (docs/team/kokoci/2026-10-05-
#: hostname.md); -I so the helper never imports from the caller's path.
HELPER_ARGV = ("sudo", "-n", "/usr/bin/python3", "-I", HELPER)
#: RFC 1123 label, lowercase. One label: <name>.local is the whole address.
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_REFUSED = {"localhost": "localhost is this machine to every machine"}
#: What a box without the helper says, on the card and on a refused save.
NEEDS_INSTALLER = ("this box needs a one-time update by the installer before "
                   "its hostname can be changed here")


class HostnameError(ValueError):
    """Operator-facing refusal; the sentence is the message."""


def current():
    return socket.gethostname().split(".")[0]


def local_name():
    """The .local name a browser should use for this box right now."""
    return discovery_mdns.own_host() or current() + ".local"


def validate(name):
    """The name as it will be set, or HostnameError with the reason."""
    name = str(name or "").strip().lower()
    if name.endswith(".local"):
        name = name[:-len(".local")]
    if not name:
        raise HostnameError("type a hostname for the box")
    if not _LABEL.match(name):
        raise HostnameError(
            "use letters a-z, digits and '-' only, up to 63 characters, "
            "not starting or ending with '-' (e.g. %s)" % hostinfo.default_hostname())
    if name.isdigit():
        raise HostnameError("a hostname of only digits reads as an address - add a letter")
    if name in _REFUSED:
        raise HostnameError(_REFUSED[name])
    return name


def helper_installed():
    return os.path.isfile(HELPER)


def can_set():
    """Helper there AND sudo would run it without a password. `sudo -n -l`
    answers that and runs nothing (mqtt_broker.helper_allowed)."""
    if not helper_installed() or not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-l", *HELPER_ARGV[2:]],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def status():
    name = current()
    default = hostinfo.default_hostname()
    return {"hostname": name, "local_name": local_name(), "default": default,
            "is_default": name == default,
            # "iriv" (or the OS image's raspberrypi) on more than one box is
            # the collision this exists to end; the page suggests the default.
            "shared_name": name in ("iriv", "raspberrypi"),
            "can_set": can_set(), "helper_installed": helper_installed()}


def reached_by_name(host, st=None):
    """Did the browser reach this box by its .local name? A rename takes
    exactly that session away, so the page warns before Apply."""
    st = st or status()
    host = str(host or "").lower().rstrip(".")
    return host.endswith(".local") and host in (st["local_name"].lower(),
                                                 st["hostname"] + ".local")


def set_name(name):
    """Rename the box through the root helper. Returns status()."""
    name = validate(name)
    if name == current():
        return status()
    if not can_set():
        raise HostnameError(NEEDS_INSTALLER)
    try:
        r = subprocess.run(list(HELPER_ARGV), input=name + "\n",
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostnameError("the rename did not run: %s" % exc) from exc
    if r.returncode != 0:
        last = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["no reason given"]
        raise HostnameError("the rename failed: %s" % last[0])
    discovery_mdns.renamed()        # and the front panel's line follows now
    return status()


# ---------------------------------------------------------------------------
# Pending: saved now, applied at the next restart of the box
# ---------------------------------------------------------------------------

def pending(config_dir=None):
    """The name waiting for a restart, or ""."""
    return str((config.load_local("network", config_dir).get("box_name") or {})
               .get("pending") or "")


def _write_pending(name, boot, config_dir=None):
    local = dict(config.load_local("network", config_dir))
    if name:
        local["box_name"] = {"pending": name, "boot": str(boot or "")}
    else:
        local.pop("box_name", None)
    config.save_local("network", local, config_dir)


def stage(name, boot, config_dir=None):
    """Save the name for the next restart (the page's Apply). The current
    name cancels a pending change. A box that could not apply it at the
    restart is refused now, rather than promised a name it will not get:
    no root helper, or a hub that is not the box's own service."""
    name = validate(name)
    if not hubport.owns_setting():
        raise HostnameError(hubport.NOT_OWN_SERVICE)
    if name == current():
        _write_pending("", "", config_dir)
        return None
    if not can_set():
        raise HostnameError(NEEDS_INSTALLER)
    other = taken_by(name)
    if other:
        raise HostnameError("another device on this network (%s) already answers to "
                            "%s.local - choose another hostname" % (other, name))
    _write_pending(name, boot, config_dir)
    return name


def taken_by(name):
    """The address of ANOTHER device already answering <name>.local, or "".
    mDNS names are first-come: a box renamed onto a name the LAN already has
    would come back as <name>-2.local, and the page would have sent the
    browser to the other device (Pipi L1). Asked of avahi-resolve (no root,
    list argv, a validated label); not installed or no answer -> "". One of
    this box's own addresses is not "another device"."""
    if not shutil.which("avahi-resolve"):
        return ""
    try:
        r = subprocess.run(["avahi-resolve", "-4", "-n", name + ".local"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = (r.stdout or "").split()
    ip = parts[1] if r.returncode == 0 and len(parts) >= 2 else ""
    return "" if not ip or ip in discovery_mdns.interface_ips() else ip


def apply_pending(boot, config_dir=None, rename=None):
    """At the hub's start: a name saved in an EARLIER boot is set through
    the root helper. None if nothing waits; else {ok, from, to, detail}.
    Any refusal keeps the old name; the pending value goes either way."""
    note = config.load_local("network", config_dir).get("box_name") or {}
    name = str(note.get("pending") or "")
    if not name or str(note.get("boot") or "") == str(boot):
        return None
    old = current()
    _write_pending("", "", config_dir)
    try:
        (rename or set_name)(name)
    except HostnameError as exc:
        return {"ok": False, "from": old, "to": name, "detail": str(exc)}
    now = current()
    if now != name:
        return {"ok": False, "from": old, "to": name,
                "detail": "the hostname did not change (the box still reports %s)" % now}
    return {"ok": True, "from": old, "to": name, "detail": ""}
