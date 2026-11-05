# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
remote.py -- reaching this master from outside the LAN: a domain, or a tunnel.

Two ways, and they are not alternatives so much as stages:

**Domain** -- the deployment owns a name and points it at this host, terminating
TLS at a reverse proxy in front of the master. Permanent, auditable, what a real
site runs. The product's job is small and entirely honest: record the public
URL, use it in emails and QR codes instead of a LAN IP, and tell the operator
whether DNS actually resolves to this host yet.

**Tunnel** -- `cloudflared` or `ngrok` opens an outbound connection and hands
back a public URL. No port forwarding, no DNS, no firewall change. It is how a
site gets remote access on day one, and how support reaches a device on a
network nobody controls.

The master never becomes the tunnel. It supervises a binary the operator
installed, and stores nothing but the URL and the process state -- so a crashed
tunnel is visible, and a dead master leaves nothing listening.

SECURITY: a tunnel publishes this master to the whole internet, and the login
page is then the only thing between the fleet and anyone who finds the URL.
`enabled` defaults to false, starting one is admin-only and audited, and the
UI must say plainly what it is about to expose.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse

from core import config as config_mod
from core.services import log_collect

PROVIDERS = ("cloudflared", "ngrok")
_URL_RE = re.compile(rb"https://[a-z0-9-]+\.(?:trycloudflare\.com|ngrok(?:-free)?\.app|ngrok\.io)")

_state = {"provider": None, "url": None, "running": False, "pid": None,
          "started_at": None, "error": None, "log": []}
_proc = None
_lock = threading.Lock()


# -- domain ------------------------------------------------------------------

def load(config_dir):
    """Remote settings: {public_url, tunnel:{provider, enabled, args}}."""
    # config_dir is None unless --config-dir was passed; resolve it to the
    # packaged config/ directory. (load()'s except clause catches OSError and
    # ValueError, so a TypeError from joining None escaped as a 500.)
    path = os.path.join(config_mod.root(config_dir), "remote.local.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("public_url", "")
    data.setdefault("tunnel", {})
    data["tunnel"].setdefault("provider", "cloudflared")
    data["tunnel"].setdefault("enabled", False)
    data["tunnel"].setdefault("args", "")
    return data


def save(config_dir, payload):
    """Validate and persist. Raises ValueError -> the route answers 400."""
    data = load(config_dir)
    if "public_url" in payload:
        data["public_url"] = normalize_url(payload["public_url"])
    tunnel = payload.get("tunnel") or {}
    if "provider" in tunnel:
        if tunnel["provider"] not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}")
        data["tunnel"]["provider"] = tunnel["provider"]
    if "enabled" in tunnel:
        data["tunnel"]["enabled"] = bool(tunnel["enabled"])
    if "args" in tunnel:
        # Stored as text, split with shlex at spawn time, never shell=True.
        data["tunnel"]["args"] = str(tunnel["args"] or "")[:200]

    # config_dir is None unless --config-dir was passed; resolve it to the
    # packaged config/ directory. (load()'s except clause catches OSError and
    # ValueError, so a TypeError from joining None escaped as a 500.)
    path = os.path.join(config_mod.root(config_dir), "remote.local.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600) if os.name != "nt" else None
    return data


def normalize_url(value):
    """Accept "dash.example.com" or a full URL; return "https://host[:port]"."""
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValueError("public URL must be http or https")
    if not parts.hostname:
        raise ValueError("public URL has no host")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def check_domain(public_url, port=None):
    """Does that name resolve, and to an address this host answers on?

    Answering "DNS points somewhere else" beats an operator discovering it from
    a customer who cannot log in.
    """
    if not public_url:
        return {"configured": False}
    host = urllib.parse.urlsplit(public_url).hostname
    out = {"configured": True, "host": host, "resolves": False,
           "addresses": [], "local_addresses": _local_addresses(), "matches_host": False}
    try:
        infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        out["error"] = f"DNS lookup failed: {e}"
        return out
    out["resolves"] = True
    out["addresses"] = sorted({i[4][0] for i in infos})
    out["matches_host"] = any(a in out["local_addresses"] for a in out["addresses"])
    return out


def _local_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(addresses)


def base_url(config_dir, request_base=""):
    """The URL to put in an email or a QR code: the configured public URL if
    there is one, else whatever the caller reached us on."""
    return load(config_dir).get("public_url") or request_base


# -- tunnel ------------------------------------------------------------------

def available(provider):
    """Absolute path to the provider binary, or None. The master never
    downloads or installs it -- that is an operator decision on their host."""
    return shutil.which(provider)


def status():
    with _lock:
        state = dict(_state)
    state["running"] = bool(_proc and _proc.poll() is None)
    state["providers"] = {p: bool(available(p)) for p in PROVIDERS}
    state["log"] = state.get("log", [])[-20:]
    return state


def start(config_dir, port, provider=None):
    """Spawn the tunnel and wait briefly for it to announce a URL.

    Returns (ok, message). Never raises on a missing binary: an operator asking
    for a tunnel on a host without cloudflared deserves a sentence, not a 500.
    """
    global _proc
    import shlex

    cfg = load(config_dir)
    provider = provider or cfg["tunnel"]["provider"]
    if provider not in PROVIDERS:
        return False, f"unknown provider {provider!r}"
    if status()["running"]:
        return False, "a tunnel is already running"
    binary = available(provider)
    if not binary:
        return False, (f"{provider} is not installed on this host. Install it, "
                       f"then start the tunnel again.")

    if provider == "cloudflared":
        argv = [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
    else:
        argv = [binary, "http", str(port), "--log", "stdout"]
    argv += shlex.split(cfg["tunnel"].get("args") or "")   # never shell=True

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with _lock:
        _state.update({"provider": provider, "url": None, "running": True,
                       "pid": proc.pid, "started_at": time.time(),
                       "error": None, "log": []})
    _proc = proc
    # Guarded: this thread is the ONLY thing that ever learns the tunnel's
    # public URL, and it reads a pipe owned by another process. A raise in it
    # (the provider killed mid-line, a decode fault) used to end the thread in
    # silence -- the tunnel then ran with no URL and no error, and the Remote
    # Access page sat on "starting" for ever with nothing to explain it.
    log_collect.spawn("remote-tunnel-reader", _reader, (proc,),
                      once=True).start()

    deadline = time.time() + 20            # cloudflared prints its URL in ~5s
    while time.time() < deadline:
        if status().get("url"):
            return True, status()["url"]
        if proc.poll() is not None:
            return False, "tunnel exited; see the log"
        time.sleep(0.4)
    return True, "tunnel started; URL not announced yet"


def _reader(proc):
    """Read the provider's output for its public URL, and keep the last lines
    for the UI -- a tunnel that fails says why on stdout and nowhere else.

    Returning is normal here: the loop ends when the provider closes its
    pipe, which is the tunnel stopping. The state it leaves behind says so,
    which is why this one is spawned once=True -- a record per clean exit
    would bury the raises that matter.
    """
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        with _lock:
            _state["log"].append(line)
            del _state["log"][:-200]
            if not _state["url"]:
                found = _URL_RE.search(raw)
                if found:
                    _state["url"] = found.group(0).decode()
    with _lock:
        _state["running"] = False
        _state["pid"] = None
        if _state["url"] is None:
            _state["error"] = "tunnel exited before announcing a URL"


def stop():
    global _proc
    if not (_proc and _proc.poll() is None):
        return False, "no tunnel running"
    _proc.terminate()
    try:
        _proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        _proc.kill()
    with _lock:
        _state.update({"running": False, "pid": None, "url": None})
    _proc = None
    return True, "tunnel stopped"
