# -*- coding: utf-8 -*-
"""
redact.py -- keep credentials out of what the function and output routes
send to a browser, and put them back when a client sends a list back.

Yaya's review of a34da37 (M2): on a box updated from v1.0.12, GET
/api/functions -- any signed-in account that may open the system -- returned
the compiled [[functions]] still holding an MQTT Subscribe's username and
password, until somebody saved the workflow. The recompile at start fixes
that box's config; this fixes the ROUTES, generally: any string under a key
that names a secret is replaced with REDACTED on the way out, whatever the
node type and whenever it was compiled.

The write routes take whole lists back. A client that sends a list it read
must not wipe every password in it, so restore() puts the stored value back
wherever REDACTED comes back (matched by the item's id and the key's path).

URL PASSWORDS (Pipi H2/L5, 2026-10-11). A camera address carries its
password in the URL, under a key ("source", "label", "camera") that names no
secret, so the key rule above never sees it. mask_source() is the one rule
for a source field (the edit round trip, detector_control.unmask_source,
depends on its exact output); mask_text() is what every READER applies to a
label or a line of text it hands out, so a state.json written by an older
app, still holding a plaintext password, is masked on the way out all the
same. mask_url_passwords() is the log rule (core/services/logs.py), the same
one the app keeps in app/faults.py.
"""

import copy
import re

#: What a hidden secret reads as. Not "" -- an empty password is a real,
#: different value, and must stay distinguishable from one we hid.
REDACTED = "__redacted__"

#: Key names that hold a credential: password, app_password, smtp_pass,
#: passwd, token, bot_token, secret, api_key...
_SECRET_KEY = re.compile(r"pass(word|wd)?$|_pass$|token$|secret|api_?key", re.I)


def is_secret_key(key):
    return bool(_SECRET_KEY.search(str(key)))


#: A URL inside a text, to the first character no URL carries unquoted.
_URL_RE = re.compile(r"""([A-Za-z][A-Za-z0-9+.\-]*://)([^\s'"<>]*)""")
_SCHEME_START = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
#: "host:554/a@b" is a host, a port and a path, not user:password.
_PORT_PATH_RE = re.compile(r"\d{1,5}/")
#: user:password@host with no scheme, in free text (a label made from a URL).
_BARE_RE = re.compile(r"""(?<![\w/:])([^\s'"<>/:@]+):(?!//)([^\s'"<>]*)@(?=[^\s'"<>@]+)""")


def mask_source(source, hidden=REDACTED):
    """`source` (one camera address) with its URL password replaced.

    The password runs to the LAST "@" -- where FFmpeg and urllib split it --
    so one holding "@", ":" or "/" is hidden whole (smoke_camurlmask's
    lesson). The user and the host stay readable: they are what an operator
    checks."""
    s = str(source or "")
    head, sep, rest = s.partition("://")
    if not sep:
        return s
    at = rest.rfind("@")
    if at <= 0:
        return s
    user, colon, password = rest[:at].partition(":")
    if not colon or not password:
        return s
    return f"{head}://{user}:{hidden}@{rest[at + 1:]}"


def mask_url_passwords(text, hidden="***"):
    """Every URL password in a line of text, masked (the log rule)."""
    def one(m):
        rest = m.group(2)
        at = rest.rfind("@")
        if at <= 0:
            return m.group(0)
        user, colon, password = rest[:at].partition(":")
        if not colon or not password or "/" in user or _PORT_PATH_RE.match(password):
            return m.group(0)
        return f"{m.group(1)}{user}:{hidden}{rest[at:]}"
    return _URL_RE.sub(one, str(text))


def mask_text(text, hidden=REDACTED):
    """A label or a line a reader hands out, with every credential in it
    masked: a whole address (mask_source), URLs inside the text, and a bare
    user:password@host. Idempotent; never raises on a non-string."""
    s = "" if text is None else str(text)
    if _SCHEME_START.match(s.strip()):
        s = mask_source(s, hidden)
    s = mask_url_passwords(s, hidden)
    return _BARE_RE.sub(lambda m: f"{m.group(1)}:{hidden}@", s)


def redact(obj):
    """A copy of `obj` with every non-empty string under a secret key
    replaced by REDACTED, at any depth."""
    if isinstance(obj, dict):
        return {k: (REDACTED if is_secret_key(k) and isinstance(v, str) and v
                    else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return copy.copy(obj)


def _restore_one(new, old):
    if isinstance(new, dict) and isinstance(old, dict):
        out = {}
        for k, v in new.items():
            if v == REDACTED and is_secret_key(k):
                out[k] = old.get(k, "")
            else:
                out[k] = _restore_one(v, old.get(k))
        return out
    if isinstance(new, list) and isinstance(old, list):
        return [_restore_one(v, old[i] if i < len(old) else None)
                for i, v in enumerate(new)]
    if new == REDACTED:
        return ""                       # nothing stored to put back
    return new


def restore(items, stored):
    """`items` (a list a client sent) with REDACTED put back from `stored`
    (the list on disk), matched by each item's id. A REDACTED with nothing
    to restore from becomes "" -- never the placeholder itself."""
    by_id = {str((s or {}).get("id") or ""): s for s in (stored or [])
             if isinstance(s, dict)}
    out = []
    for item in items or []:
        if isinstance(item, dict):
            old = by_id.get(str(item.get("id") or ""), {})
            out.append(_restore_one(item, old))
        else:
            out.append(item)
    return out
