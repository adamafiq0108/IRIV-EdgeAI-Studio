# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
httpsrun.py -- the running half of HTTPS (https.py is the setting and the
keys, core/tlsserve.py the server): what the listener is doing, the TLS
context it hands each connection, the port 443 redirect, and the card's
status.

RE-ISSUE WITHOUT A POLLING LOOP. Nothing in the product announces "the
box's address changed" (a DHCP renewal, a Wi-Fi switch, the hotspot coming
up). Instead the question is asked where the answer matters: when a client
opens a TLS connection, at most every CHECK_EVERY_S. live_context() then
compares the box's names and addresses with the certificate, issues a new
one if a name is missing (https.ensure, a few milliseconds of EC P-256),
and hands THAT connection the new context (tlsserve's SNI callback), so even
the first visitor on a new address gets a certificate naming it. A box
nobody connects to does no work at all.

PORT 443. The hub is not root, so it listens on a high port (8443 by
default) and the same root helper as the port 80 redirect points 443 at it
(scripts/web_redirect.py, action tls_on). Without the helper -- a box that
has not had the update or the one-time step that installs it -- HTTPS still
works at https://<box>:8443, and the card says why 443 does not.
"""

import fnmatch
import os
import stat
import subprocess
import threading
import time

from core import config
from core.services import https, tlscert, tlsnames

CHECK_EVERY_S = 30.0
TLS_STATE = "/etc/irivai/web-redirect-tls.nft"

_lock = threading.Lock()
#: redirect: the "send browsers to HTTPS" setting as the listener last read
#: it, so the hub's HTTP middleware never reads a file per request.
_rt = {"listening": False, "port": None, "error": "", "port443": False,
       "port443_detail": "", "redirect": False}
_ctx = {"ctx": None, "gen": -1, "checked": None}


#: What a crash between write and unlink can leave in config/: the OpenSSL
#: load file (a leaf key, tlscert.server_context) and https.write()'s temp
#: file (the root key). 0600, gitignored (config/.https*), swept at the
#: hub's start (core/tlsserve.py Listener.apply).
LEFTOVERS = (".https-load-*.pem", ".https.*.tmp")


def sweep(config_dir=None, min_age_s=60.0, now=None):
    """Delete leftover key files (Pipi L4): only those names, only regular
    files (never through a link), only older than `min_age_s` so a write in
    flight is never cut. The names removed; never raises."""
    base, now, gone = config.root(config_dir), now or time.time(), []
    try:
        names = os.listdir(base)
    except OSError:
        return gone
    for name in names:
        if not any(fnmatch.fnmatchcase(name, pat) for pat in LEFTOVERS):
            continue
        p = os.path.join(base, name)
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode) and now - st.st_mtime > min_age_s:
                os.unlink(p)
                gone.append(name)
        except OSError:
            pass
    return gone


def runtime(**values):
    """Update (listener, 443) and return a copy of what the hub is doing."""
    with _lock:
        _rt.update(values)
        return dict(_rt)


def build_context(config_dir=None):
    """A fresh SSLContext from the certificate on file (HttpsError /
    ssl.SSLError / OSError when it cannot be had)."""
    cert, key = https.material(config_dir)
    ctx = tlscert.server_context(cert, key, str(config.root(config_dir)))
    _ctx.update(ctx=ctx, gen=https.generation())
    return ctx


def live_context(config_dir=None, clock=time.monotonic, log=None):
    """The context a NEW connection should get: the names re-checked (and
    a certificate issued) at most every CHECK_EVERY_S, the context rebuilt
    whenever a certificate was issued since it was built. Never raises: a
    failure keeps the context it had and is put on the card."""
    with _lock:
        now = clock()
        if _ctx["checked"] is None or now - _ctx["checked"] >= CHECK_EVERY_S:
            _ctx["checked"] = now
            try:
                done = https.ensure(config_dir)
                if done["issued"] and log:
                    log("[HTTPS] issued a new certificate (%s)" % done["reason"])
            except https.HttpsError as exc:
                _rt["error"] = str(exc)
        if _ctx["ctx"] is None or _ctx["gen"] != https.generation():
            try:
                build_context(config_dir)
            except Exception as exc:                          # noqa: BLE001
                _rt["error"] = "the new certificate could not be loaded (%s)" \
                    % exc.__class__.__name__
        return _ctx["ctx"]


def prepare(config_dir=None):
    """The listener's start: issue what is due, then the context. Raises
    HttpsError (or ssl.SSLError) for the card -- the listener stays down,
    HTTP is untouched."""
    with _lock:
        https.ensure(config_dir)
        _ctx["checked"] = time.monotonic()
        return build_context(config_dir)


def ensure_443(config_dir=None, runner=subprocess.run):
    """Point 443 at the HTTPS port while it listens, or take it away.
    "" or the sentence for the card. Only the box's own service asks."""
    from core.services import hubport, roothelp, webaccess    # noqa: PLC0415
    if not hubport.owns_setting():
        runtime(port443=False, port443_detail=hubport.NOT_OWN_SERVICE)
        return ""
    st = https.settings(config_dir)
    if st["enabled"] and _rt["listening"]:
        if not webaccess.can_run():
            why = roothelp.missing(webaccess.HELPER, "irivai-web",
                                   "port 443 is not available (HTTPS still "
                                   "answers on port %d)" % st["port"])
            runtime(port443=False, port443_detail=why)
            return why
        ok, why = webaccess.run_helper({"action": "tls_on", "port": st["port"]}, runner)
        if not ok and "on or off" in why:      # a helper from before HTTPS
            why = ("the port 443 helper on this box is older than this "
                   "software; " + roothelp.fix())
        runtime(port443=ok, port443_detail="" if ok else why)
        return "" if ok else why
    if os.path.isfile(TLS_STATE) and webaccess.can_run():
        webaccess.run_helper({"action": "tls_off"}, runner)
    runtime(port443=False, port443_detail="")
    return ""


def required():
    """Is HTTPS the way in right now: the listener answers AND the box sends
    browsers there? Only then is a sign-in, a token or a reset code refused
    over plain HTTP (core/tlsserve.py) -- never while HTTPS is not actually
    answering, so a failed start can lock nobody out."""
    with _lock:
        return bool(_rt["listening"] and _rt["redirect"])


def redirect_target(host, path, query=""):
    """https://<host>[:port]<path> for the HTTP -> HTTPS redirect, or None
    while the redirect is off or HTTPS is not actually answering (never a
    redirect to nothing). 443 needs no port in the URL."""
    with _lock:
        if not (_rt["listening"] and _rt["redirect"]):
            return None
        port = None if _rt["port443"] else _rt["port"]
    netloc = host if port is None else f"{host}:{port}"
    return f"https://{netloc}{path}" + (f"?{query}" if query else "")


def _when(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def status(config_dir=None):
    """The HTTPS card: the setting, the listener, the root and the
    certificate. Public facts only -- never a key."""
    from core.services import webaccess                       # noqa: PLC0415
    out = https.settings(config_dir)
    rt = runtime()
    out.update(listening=rt["listening"], port443=rt["port443"],
               port443_detail=rt["port443_detail"], default_port=https.DEFAULT_PORT,
               helper=webaccess.helper_installed(), root=None, leaf=None, uncovered=[])
    out["error"] = out["error"] or (rt["error"] if out["enabled"] else "")
    try:
        doc = https.read(config_dir)
        ca = https.root_cert(config_dir)
    except https.HttpsError as exc:
        out["error"] = str(exc)
        return out
    if ca is not None:
        cn = ca.subject.rfc4514_string()
        out["root"] = {"fingerprint": tlscert.fingerprint(ca), "subject": cn,
                       "not_after": _when(tlscert.not_after(ca)),
                       "created": (doc.get("ca") or {}).get("created")}
        names, ips = tlsnames.current()
        out["uncovered"] = [v for v in names + ips if not tlscert.allowed(ca, v)]
    entry = doc.get("leaf") or {}
    try:
        leaf = tlscert.load_cert(entry.get("cert"))
        leaf_names, leaf_ips = tlscert.sans(leaf)
        out["leaf"] = {"names": leaf_names, "ips": leaf_ips,
                       "not_after": _when(tlscert.not_after(leaf)),
                       "issued": entry.get("issued"), "reason": entry.get("reason")}
    except ValueError:
        pass
    return out
