# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
sysport.py -- a system's own port: chosen at create, moved by Edit system.

Tok Aba (2026-10-06): the hub card's pencil edits the name AND the port; the
create wizard offers Automatic (next free) or a custom port. Its own module
so provision.py stays at its line ceiling; provision calls in here for the
create's custom-port check and the stream-port scan.

ONE PORT PER SYSTEM is offered: the master's, the address on the card
([masters.<slug>].url in systems.local.toml, the one fact every link reads --
the card, the hub's probes, the masters' system switcher; config reloads on
mtime, so other processes follow without a restart). The video stream port
([app].stream_port) stays where it is: the app serves it to the master on
this box and nothing outside opens it, so moving it would restart the app
and drop the camera for no gain. The detection app never talks to the
master's port, so a move restarts the master only.

A master systemd runs (irivai-master@<slug>) is refused: its port is in the
root-owned /etc/irivai/<slug>.env, the service account may only `systemctl
restart` it, and reconcile_masters makes the unit's port win at the next boot
anyway. No framework imports (docs/conventions.md sec 2).
"""

import os
import threading
import time
import tomllib
import urllib.parse

from core import config
from core.services import provision

#: How long a moved master gets to answer on its new port before the move is
#: undone. A master on a Pi imports the whole route table; 10-15 s is normal.
MOVE_TIMEOUT_S = 45

#: One move at a time: two saves racing would each stop the other's master.
_MOVE_LOCK = threading.Lock()


def _url_port(url):
    try:
        return urllib.parse.urlparse(url or "").port
    except ValueError:
        return None


def _entry(slug, config_dir=None):
    return ((config.systems(config_dir).get("masters") or {}).get(slug)) or {}


def master_port_of(slug, config_dir=None):
    """The port <slug>'s master is configured on, or None."""
    return _url_port(_entry(slug, config_dir).get("url"))


def stream_port_owners(systems_dir=None, config_dir=None):
    """{port: slug} for every system's MJPEG stream port, from each package's
    config.toml + config.local.toml [app].stream_port (the files
    detector_control.stream_port() reads). A refusal that names WHOSE stream
    holds a port is one the operator can act on. First found keeps a port."""
    out = {}
    root = systems_dir or provision._default_systems_dir(config_dir)
    for slug, pkg in provision.system_slugs(root, config_dir).items():
        app = {}
        for name in ("config.toml", "config.local.toml"):
            try:
                with open(os.path.join(pkg, name), "rb") as f:
                    app.update(tomllib.load(f).get("app") or {})
            except (OSError, tomllib.TOMLDecodeError):
                continue
        if not os.path.isdir(os.path.join(pkg, "app")):
            continue                                       # no detection app
        default = provision._DEFAULT_STREAM_PORT
        try:
            out.setdefault(int(app.get("stream_port") or default), slug)
        except (TypeError, ValueError):
            out.setdefault(default, slug)
    return out


def refusal(p, systems_dir=None, config_dir=None, slug=None):
    """Why `p` cannot be a system's master port, in words, or "".

    The box's other port checks in one place (hubport.check is the hub's
    twin): the hub's own port and the one it moves to at the next restart,
    the box's services (hubport.RESERVED: the 5020 Modbus redirect, MQTT
    1883, PostgreSQL, the video gateway...), every other system's master,
    every system's video stream and Modbus server, then a bind test for what
    the config does not know about. `slug` is the system being edited: its
    own current port is not a refusal."""
    from core.services import hubport                      # noqa: PLC0415
    if slug and p == master_port_of(slug, config_dir):
        return ""
    if p in (hubport.running_port(), hubport.port(config_dir)):
        return (f"{p} is the port this software (the Systems page) answers "
                "on - choose another")
    if p == hubport.pending_port(config_dir):
        return (f"{p} is the port this software moves to at the next restart "
                "- choose another")
    if p in hubport.RESERVED:
        return f"{p} is used by {hubport.RESERVED[p]} - choose another"
    for other, entry in (config.systems(config_dir).get("masters") or {}).items():
        if other != slug and _url_port((entry or {}).get("url")) == p:
            return (f"{p} is already the port of the system \"{other}\" "
                    "- choose another")
    for owners, what in ((stream_port_owners(systems_dir, config_dir), "live video stream"),
                         (provision.modbus_ports_in_use(systems_dir, config_dir),
                          "Modbus server")):
        owner = owners.get(p)
        if owner:
            whose = "this system's own" if owner == slug else f"the system \"{owner}\"'s"
            return f"{p} is {whose} {what} - choose another"
    if not provision._port_is_free(p):
        return (f"something on this box already listens on {p} - choose "
                "another, or stop that program first")
    return ""


def check_master_port(value, systems_dir=None, config_dir=None, slug=None):
    """The port as an int, or ValueError with the refusal in plain words."""
    from core.services import hubport                      # noqa: PLC0415
    try:
        p = hubport.parse(value)
    except hubport.PortError as exc:
        raise ValueError(str(exc)) from None
    why = refusal(p, systems_dir, config_dir, slug)
    if why:
        raise ValueError(why)
    return p


def suggest(systems_dir=None, config_dir=None):
    """What Automatic (next free) hands out now: the create's own allocator."""
    return provision.next_master_url(config.systems(config_dir), systems_dir,
                                     config_dir)[1]


def unit_managed(slug):
    """systemd runs this system's master: /etc/irivai/<slug>.env exists."""
    return os.path.exists(os.path.join(provision.UNIT_ENV_DIR, f"{slug}.env"))


def info(systems_dir, slug, config_dir=None):
    """What Edit system shows beside the port field: the port now, the
    stream port that stays, the Automatic suggestion, and -- when the port
    cannot be moved from here -- why not, in words."""
    url = _entry(slug, config_dir).get("url") or ""
    port, host = _url_port(url), urllib.parse.urlparse(url).hostname
    if unit_managed(slug):
        reason = (f"{slug} is run by the box's own service (irivai-master@{slug}), "
                  "whose port is set when the software is installed - this page "
                  "cannot move it")
    elif not port:
        reason = f"{slug} has no master configured"
    elif not provision.is_local_master(host):
        reason = f"{slug}'s master runs at {host} - change its port on that host"
    else:
        reason = ""
    try:
        suggested = suggest(systems_dir, config_dir)
    except Exception:                                      # noqa: BLE001
        suggested = None
    streams = {s: p for p, s in stream_port_owners(systems_dir, config_dir).items()}
    return {"slug": slug, "port": port, "stream_port": streams.get(slug),
            "suggested": suggested, "editable": not reason, "reason": reason}


def _set_url(slug, port, config_dir=None, url=None):
    """Point [masters.<slug>].url at `port` in systems.local.toml, keeping
    owner/name/role/autostart. Loopback, as every stored master url is;
    `url` puts back an exact earlier value (the undo)."""
    local = config.load_local("systems", config_dir)
    masters = local.setdefault("masters", {})
    entry = dict(masters.get(slug) or {})
    for key, value in _entry(slug, config_dir).items():
        entry.setdefault(key, value)
    entry["url"] = url or f"http://127.0.0.1:{int(port)}"
    masters[slug] = entry
    config.save_local("systems", local, config_dir)
    return entry["url"]


def _answers_as(url, slug, timeout_s):
    """Wait up to `timeout_s` for <url>/api/health to say it is <slug> -- a
    stranger answering on the port is not the move having worked."""
    from core.services import masters as masters_service    # noqa: PLC0415
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        body = masters_service.probe(url, timeout=1.5)
        if body and body.get("system_slug") == slug:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _wait_free(port, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not provision._port_is_free(port) and time.monotonic() < deadline:
        time.sleep(0.25)


def move(systems_dir, slug, value, repo_dir, config_dir=None, timeout=None):
    """Move <slug>'s master to another port. {moved, port, old_port,
    restarted}; ValueError (plain words) when refused or undone.

    Running: stop it on the old port, write the new url, start it on the new
    one and wait for it to answer AS this system. If it does not, whatever
    started is stopped, the old url goes back and the master is started on
    the old port again -- the system is never left on neither. Not running
    (stopped or powered off): only the url moves; it starts there next time.
    Unit-managed and remote masters are refused before anything happens."""
    wait = MOVE_TIMEOUT_S if timeout is None else timeout
    with _MOVE_LOCK:
        old_url = _entry(slug, config_dir).get("url") or ""
        old = _url_port(old_url)
        p = check_master_port(value, systems_dir, config_dir, slug=slug)
        if p == old:
            return {"moved": False, "port": p, "old_port": old, "restarted": False}
        facts = info(systems_dir, slug, config_dir)
        if not facts["editable"]:
            raise ValueError(facts["reason"])
        from core.services import masters as masters_service    # noqa: PLC0415
        body = masters_service.probe(old_url, timeout=1.5)
        if not (body and body.get("system_slug") == slug):
            _set_url(slug, p, config_dir)
            return {"moved": True, "port": p, "old_port": old, "restarted": False}
        provision.stop_master(old_url)
        _wait_free(old)
        new_url = _set_url(slug, p, config_dir)
        provision.spawn_master(repo_dir, slug, p)
        if _answers_as(new_url, slug, wait):
            return {"moved": True, "port": p, "old_port": old, "restarted": True}
        # Undo: nothing half-started may keep the new port, the config names
        # the old one again, and the system runs where it ran before.
        provision.stop_master(new_url)
        _set_url(slug, old, config_dir, url=old_url)
        _wait_free(old)
        provision.spawn_master(repo_dir, slug, old)
        back = _answers_as(old_url, slug, wait)
        raise ValueError(
            f"{slug} did not come up on port {p}, so it was put back on {old}"
            + (" and is running there again" if back
               else " (it is still starting there - check its card in a moment)")
            + ". Nothing was saved.")
