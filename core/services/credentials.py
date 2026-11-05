# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
credentials.py -- device credential status + atomic rotation flow (v2.0
#9/#11/#12/#13).

No framework imports (conventions sec 2). The route layer resolves the
device token from app.state and does admin/re-auth gating; this shapes the
status view and drives the rotation handshake against the agent's v1.3
rotate endpoints. Tokens are never returned to templates -- only view_token()
returns one, and only after the route has verified re-auth.
"""

import json
import urllib.error
import urllib.request

TIMEOUT_S = 10


def _call(url, path, token, method="POST"):
    req = urllib.request.Request(f"{url}{path}", data=b"" if method == "POST" else None,
                                 method=method)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def status_view(device, live, audit_rows):
    """Operational credential status only -- never the token (#9)."""
    last_auth = next((a["ts"] for a in audit_rows
                      if a.get("host_serial") == device["host_serial"]
                      and a.get("action") == "login"), "")
    last_rotate = next((a["ts"] for a in audit_rows
                        if a.get("host_serial") == device["host_serial"]
                        and a.get("action") == "credentials.rotate"), "")
    return {
        "host_serial": device["host_serial"],
        "label": (live or {}).get("display_name") or device.get("label"),
        "online": bool((live or {}).get("online")),
        "paired": bool(device.get("token")),
        "auth_status": "authenticated" if (live or {}).get("online") else "unreachable",
        "token_present": bool(device.get("token")),
        "last_rotation": last_rotate,
        "last_successful_auth": last_auth,
    }


def rotate(device, poll_one, save_token):
    """Atomic, zero-downtime rotation (#11).

    1. ask the agent to mint a new token (old stays valid),
    2. persist + switch to the new token,
    3. verify comms with it (poll),
    4. commit on the agent (old dies); abort on any failure so the old token
       keeps working -- no 401 gap either way.

    poll_one(entry) -> (updated, events, cursor); save_token(token) persists.
    Returns (ok, message).
    """
    url, old = device["url"], device["token"]
    if not url:
        return False, "device has no known address"

    st, body = _call(url, "/api/agent/rotate", old)
    if st != 200 or not body.get("token"):
        return False, body.get("error") or f"rotate request failed ({st})"
    new = body["token"]

    # Switch to the new token and prove it works before committing. If it
    # fails, we simply DON'T commit: the agent keeps both valid, the old
    # token is still primary and works, and the next rotation overwrites the
    # stale pending one. No 401 gap, no lockout.
    probe = dict(device, token=new)
    updated, _events, _cursor = poll_one(probe)
    if not updated.get("online"):
        return False, "new token failed verification; kept the current token"

    save_token(new)                          # persist encrypted + app.state
    st, _ = _call(url, "/api/agent/rotate/commit", new)
    if st != 200:
        # New token is verified and stored; the old is still valid until the
        # device commits. A later rotate (or manual commit) finishes it.
        return False, "verified and switched, but device commit failed; retry"
    return True, "credentials rotated"


def revoke(device):
    """Revoke on the device (#13): its token dies immediately."""
    url, token = device.get("url"), device.get("token")
    if not url or not token:
        return False
    st, _ = _call(url, "/api/agent/unpair", token)
    return st == 200
