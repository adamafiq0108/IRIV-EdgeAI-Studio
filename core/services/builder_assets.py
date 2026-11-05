# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
builder_assets.py -- the pictures an Image card shows (Tok Aba, 2026-10-14).

An Image card on the Dashboard or Analytics page shows a picture the operator
UPLOADED. Nothing else: no external URL (a box is often offline, and a URL is
a request the operator's viewers make to somebody else), no SVG (a picture
that can carry script), no file name from the browser.

    <store>/builder_assets/<slug>/<32 hex>.<png|jpg|webp|gif>

The layout stores only the 32-hex ASSET ID; the path is derived here and
nowhere else. What a file IS is decided by its first bytes (sniff), never by
its name or the MIME type the browser claimed, and it is served back under
that sniffed type with nosniff and a CSP that allows nothing to run.

Cleanup: a picture the layout stops naming is deleted when the layout is
saved (the widget was deleted or its picture replaced), and any file no
layout names is swept once it is older than ORPHAN_GRACE_S -- a fresh upload
waits for the save that names it.
"""

import os
import re
import secrets
import struct
import time

from core import config as config_mod

#: The largest picture accepted, checked while the body streams in.
MAX_BYTES = 2 * 1024 * 1024
#: The largest width or height, read from the file's own header.
MAX_DIM = 4096
#: A file no layout names is left alone this long: an upload is made before
#: the layout that names it is saved.
ORPHAN_GRACE_S = 3600
#: At most this many files looked at per sweep -- a save stays cheap.
SWEEP_LIMIT = 500
#: Per SYSTEM, counted from the files really on disk (Pipi N-4): without a
#: ceiling anyone allowed to edit a dashboard could fill the disk 2 MB at a
#: time. Past the SOFT line an upload first sweeps pictures no layout names.
MAX_FILES = 50
MAX_TOTAL_BYTES = 40 * 1024 * 1024
SOFT_FILES = 40
SOFT_TOTAL_BYTES = 32 * 1024 * 1024
QUOTA_MESSAGE = ("This system has reached its picture limit. Remove an unused "
                 "picture first.")


class QuotaError(ValueError):
    """The system's picture store is full (the route answers 413)."""

ASSET_RE = re.compile(r"^[0-9a-f]{32}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: kind -> (file extension, Content-Type)
TYPES = {"png": ("png", "image/png"), "jpeg": ("jpg", "image/jpeg"),
         "webp": ("webp", "image/webp"), "gif": ("gif", "image/gif")}
_EXT_TYPE = {ext: mime for ext, mime in TYPES.values()}


def asset_dir(slug):
    """This system's folder. Refuses a slug that is not one."""
    if not _SLUG_RE.match(str(slug or "")):
        raise ValueError("bad system")
    return os.path.join(str(config_mod.store_dir()), "builder_assets", slug)


def is_asset_id(value):
    return bool(ASSET_RE.match(str(value or "")))


# -- what a file is -----------------------------------------------------------

def _png_size(b):
    # 8-byte signature, then the IHDR chunk: length, "IHDR", width, height.
    if len(b) < 24 or b[12:16] != b"IHDR":
        raise ValueError("not a picture")
    return struct.unpack(">II", b[16:24])


def _gif_size(b):
    if len(b) < 10:
        raise ValueError("not a picture")
    return struct.unpack("<HH", b[6:10])


def _webp_size(b):
    # RIFF....WEBP then one of three chunk kinds.
    if len(b) < 30:
        raise ValueError("not a picture")
    chunk = b[12:16]
    if chunk == b"VP8 ":
        if b[23:26] != b"\x9d\x01\x2a":
            raise ValueError("not a picture")
        w, h = struct.unpack("<HH", b[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L":
        if b[20] != 0x2F:
            raise ValueError("not a picture")
        bits = int.from_bytes(b[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        w = int.from_bytes(b[24:27], "little") + 1
        h = int.from_bytes(b[27:30], "little") + 1
        return w, h
    raise ValueError("not a picture")


def _jpeg_size(b):
    # Walk the markers to the first start-of-frame, which says the size.
    i = 2
    n = len(b)
    while i + 4 <= n:
        if b[i] != 0xFF:
            raise ValueError("not a picture")
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", b[i + 2:i + 4])[0]
        if seg < 2:
            raise ValueError("not a picture")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 > n:
                break
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + seg
    raise ValueError("not a picture")


def sniff(data):
    """("png" | "jpeg" | "webp" | "gif", width, height) from the file's own
    bytes, or ValueError. Everything else -- SVG, HTML, a renamed anything --
    is refused here, whatever it was called."""
    b = bytes(data[:65536]) if data else b""
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        kind, (w, h) = "png", _png_size(b)
    elif b.startswith(b"\xff\xd8\xff"):
        kind, (w, h) = "jpeg", _jpeg_size(b)
    elif b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        kind, (w, h) = "webp", _webp_size(b)
    elif b[:6] in (b"GIF87a", b"GIF89a"):
        kind, (w, h) = "gif", _gif_size(b)
    else:
        raise ValueError("only PNG, JPEG, WebP or GIF pictures")
    if not (0 < w <= MAX_DIM and 0 < h <= MAX_DIM):
        raise ValueError(f"the picture must be at most {MAX_DIM} x {MAX_DIM} pixels")
    return kind, w, h


# -- store, read, remove -----------------------------------------------------------

def usage(slug):
    """(files, bytes) this system's pictures take on disk now."""
    try:
        folder = asset_dir(slug)
    except ValueError:
        return 0, 0
    files = total = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0, 0
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path):
                files += 1
                total += os.path.getsize(path)
        except OSError:
            continue
    return files, total


def save(slug, data, layout=None):
    """Store an uploaded picture; returns {"id", "type", "width", "height"}.
    The name is generated; the browser's name never reaches the disk.

    Refused with QuotaError past MAX_FILES / MAX_TOTAL_BYTES for the system.
    `layout` (callable or dict: the system's saved layout) lets an upload past
    the soft line sweep the pictures nothing names first; without it no
    picture is ever removed here."""
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"the picture must be at most {MAX_BYTES // (1024 * 1024)} MB")
    files, total = usage(slug)
    if (files >= SOFT_FILES or total + len(data) > SOFT_TOTAL_BYTES) \
            and layout is not None:
        try:
            current = layout() if callable(layout) else layout
            after_save(slug, {}, current or {})
        except Exception:                                   # noqa: BLE001
            pass                    # a sweep that fails leaves the cap to decide
        files, total = usage(slug)
    if files >= MAX_FILES or total + len(data) > MAX_TOTAL_BYTES:
        raise QuotaError(QUOTA_MESSAGE)
    kind, w, h = sniff(data)
    ext = TYPES[kind][0]
    folder = asset_dir(slug)
    os.makedirs(folder, exist_ok=True)
    aid = secrets.token_hex(16)
    target = os.path.join(folder, f"{aid}.{ext}")
    tmp = target + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)
    return {"id": aid, "type": TYPES[kind][1], "width": w, "height": h}


def _find(slug, aid):
    """The stored file for an id, or None."""
    if not is_asset_id(aid):
        return None
    folder = asset_dir(slug)
    for ext in _EXT_TYPE:
        path = os.path.join(folder, f"{aid}.{ext}")
        if os.path.isfile(path):
            return path
    return None


def read(slug, aid):
    """(bytes, content type) for an id, or None. The type is sniffed again
    from the bytes on disk, so a file that changed underneath is never
    served under a type it no longer is."""
    path = _find(slug, aid)
    if not path:
        return None
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None
    try:
        kind, _, _ = sniff(data)
    except ValueError:
        return None
    return data, TYPES[kind][1]


def remove(slug, aid):
    path = _find(slug, aid)
    if path:
        try:
            os.remove(path)
            return True
        except OSError:
            pass
    return False


def remove_all(slug):
    """A system is gone: its pictures go with it."""
    try:
        folder = asset_dir(slug)
    except ValueError:
        return 0
    n = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
                n += 1
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass
    return n


def referenced(layout):
    """Every asset id an Image card of `layout` names."""
    out = set()
    for p in (layout or {}).get("pages") or []:
        for w in (p or {}).get("widgets") or []:
            if isinstance(w, dict) and w.get("kind") == "image_card" and is_asset_id(w.get("asset")):
                out.add(w["asset"])
    return out


def after_save(slug, before, after, now=None):
    """Clean up after a layout save: pictures `before` named and `after` does
    not are deleted at once (a card deleted, or its picture replaced); any
    file no layout names and older than ORPHAN_GRACE_S is swept. Bounded by
    SWEEP_LIMIT. Returns the ids removed."""
    keep = referenced(after)
    gone = []
    for aid in referenced(before) - keep:
        if remove(slug, aid):
            gone.append(aid)
    try:
        folder = asset_dir(slug)
    except ValueError:
        return gone
    if not os.path.isdir(folder):
        return gone
    now = time.time() if now is None else now
    for i, name in enumerate(sorted(os.listdir(folder))):
        if i >= SWEEP_LIMIT:
            break
        aid, _, ext = name.partition(".")
        path = os.path.join(folder, name)
        if aid in keep:
            continue
        try:
            stale = now - os.path.getmtime(path) > ORPHAN_GRACE_S
        except OSError:
            continue
        # A leftover ".part" from an interrupted upload, or a picture nothing
        # names any more -- either way, once it is old enough.
        if stale and (ext.endswith("part") or (is_asset_id(aid) and ext in _EXT_TYPE)):
            try:
                os.remove(path)
                gone.append(aid)
            except OSError:
                pass
    return gone
