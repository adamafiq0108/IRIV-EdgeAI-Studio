# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
template_media.py -- the pictures and video a built-in template's preview shows.

Tok Aba, 2026-10-22 (phase 2 of "template first"): whoever maintains the
built-in templates adds preview media on the developer hub's System
Templates page, without touching code, and every box shows it in the Hub
home's template preview (frontend TemplateGallery.jsx).

THE RULE, per template (Tok Aba): at most 5 slides, at most 1 of them a
video, every file at most 5 MB -- so at most 25 MB a template. Enforced here,
for the developer's uploads AND for the signed list a box downloads
(template_media_sync.py), so a list that breaks it is refused wherever it
comes from.

Where it lives: <store>/template_media/<key>/ -- outside the release, so an
update never loses it -- holding the files and media.json, the ordered list
[{file, kind, type, sha256, size, caption}]. The same layout on the developer
hub (where it is authored) and on a box (where the signed copy lands).

Trust and safety, in one place:
  * the KIND of a file is read from its first bytes (sniff), never from its
    name or the browser's type; only JPEG, PNG, WebP, GIF, MP4 and WebM pass,
    so SVG (script in an image) and anything else are refused;
  * file names are made here: the first 16 hex of the content's sha256 plus
    the extension of the sniffed kind. Nothing the uploader sends becomes a
    path, and a name is checked against FILE_RE before any read;
  * a file is served only when the key's media.json lists it, with the
    content type of its validated kind (routes/template_media.py adds
    nosniff).
"""

import hashlib
import json
import os
import pathlib
import re
import tempfile
import threading

from core import config
from core.services import provision as provision_service

MAX_BYTES = 5 * 1024 * 1024
MAX_SLIDES = 5
MAX_VIDEOS = 1
CAPTION_MAX = 140

#: extension -> (kind, content type). The only types there are.
TYPES = {"jpg": ("image", "image/jpeg"), "png": ("image", "image/png"),
         "webp": ("image", "image/webp"), "gif": ("image", "image/gif"),
         "mp4": ("video", "video/mp4"), "webm": ("video", "video/webm")}
FILE_RE = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp|gif|mp4|webm)$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST = "media.json"

#: Brands of the ISO media files a browser plays as MP4. QuickTime ("qt  ")
#: and HEIF pictures ("heic", "mif1") share the box format and are not MP4.
_MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
               b"avc1", b"M4V ", b"mmp4", b"dash", b"MSNV", b"f4v "}

TYPE_ERROR = ("Only JPEG, PNG, WebP or GIF pictures and MP4 or WebM videos "
              "can be used.")
SIZE_ERROR = "That file is larger than 5 MB. Pick a smaller one."
SLIDES_ERROR = ("This template already has 5 slides. Remove one before "
                "adding another.")
VIDEO_ERROR = ("This template already has a video, and only one is allowed. "
               "Remove it before adding another.")

_lock = threading.Lock()


class MediaError(ValueError):
    """A refusal in the words of the person who has to act on it."""


def sniff(blob):
    """The extension of the kind `blob` really is, or None."""
    head = bytes(blob[:64])
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in head:
        return "webm"
    return None


def check_rule(items):
    """At most MAX_SLIDES slides, at most MAX_VIDEOS videos -- or MediaError."""
    if len(items) > MAX_SLIDES:
        raise MediaError(SLIDES_ERROR)
    if sum(1 for it in items if it.get("kind") == "video") > MAX_VIDEOS:
        raise MediaError(VIDEO_ERROR)


def check_key(key):
    if not provision_service._FORMAT_KEY_RE.match(str(key or "")):
        raise MediaError("no such template")
    return str(key)


def clean_caption(text):
    """One line of plain text: control characters gone, spaces collapsed."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return " ".join(text.split())[:CAPTION_MAX]


def root():
    return pathlib.Path(config.store_dir()) / "template_media"


def _dir(key):
    return root() / check_key(key)


def valid_item(it):
    """A list entry as stored, or MediaError: every field checked, the name
    tied to the content hash, the kind and type to the name."""
    if not isinstance(it, dict):
        raise MediaError("a media entry is not an object")
    name = str(it.get("file") or "")
    sha = str(it.get("sha256") or "")
    size = it.get("size")
    if not FILE_RE.match(name) or not _SHA_RE.match(sha) \
            or not name.startswith(sha[:16]):
        raise MediaError("a media entry has a bad file name or hash")
    if not isinstance(size, int) or isinstance(size, bool) \
            or not 0 < size <= MAX_BYTES:
        raise MediaError(SIZE_ERROR)
    kind, ctype = TYPES[name.rsplit(".", 1)[1]]
    if it.get("kind") != kind or it.get("type") != ctype:
        raise MediaError("a media entry's kind does not match its file")
    return {"file": name, "kind": kind, "type": ctype, "sha256": sha,
            "size": size, "caption": clean_caption(it.get("caption"))}


def valid_list(items):
    """A whole list, each entry and the rule, or MediaError."""
    if not isinstance(items, list):
        raise MediaError("a media list is not a list")
    out = [valid_item(it) for it in items]
    if len({it["file"] for it in out}) != len(out):
        raise MediaError("a media list names the same file twice")
    check_rule(out)
    return out


def items(key, present_only=True):
    """The key's list as stored; [] when there is none or it is unreadable.
    present_only drops entries whose file is missing on disk."""
    try:
        d = _dir(key)
        raw = json.loads((d / MANIFEST).read_text("utf-8"))
        out = valid_list(raw.get("items") if isinstance(raw, dict) else raw)
    except (OSError, ValueError):
        return []
    return [it for it in out if not present_only or (d / it["file"]).is_file()]


def all_items():
    """{key: list} for every key with media here."""
    try:
        keys = sorted(p.name for p in root().iterdir() if p.is_dir()
                      and provision_service._FORMAT_KEY_RE.match(p.name))
    except OSError:
        return {}
    return {k: v for k in keys for v in [items(k)] if v}


def _atomic(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_list(key, new_items, prune=True):
    """Write the key's list; with prune, remove files it no longer names."""
    new_items = valid_list(new_items)
    d = _dir(key)
    blob = json.dumps({"items": new_items}, indent=1, sort_keys=True)
    _atomic(d / MANIFEST, (blob + "\n").encode("utf-8"))
    if prune:
        keep = {it["file"] for it in new_items}
        for p in d.iterdir():
            if FILE_RE.match(p.name) and p.name not in keep:
                try:
                    p.unlink()
                except OSError:
                    pass
    return new_items


def item_for(blob, caption=""):
    """The list entry for `blob` after the type and size checks."""
    if len(blob) > MAX_BYTES:
        raise MediaError(SIZE_ERROR)
    if not blob:
        raise MediaError("That file is empty.")
    ext = sniff(blob)
    if not ext:
        raise MediaError(TYPE_ERROR)
    sha = hashlib.sha256(blob).hexdigest()
    kind, ctype = TYPES[ext]
    return {"file": f"{sha[:16]}.{ext}", "kind": kind, "type": ctype,
            "sha256": sha, "size": len(blob), "caption": clean_caption(caption)}


def store_file(key, it, blob):
    """Put verified bytes in place under their content name."""
    if hashlib.sha256(blob).hexdigest() != it["sha256"] or len(blob) != it["size"] \
            or sniff(blob) != it["file"].rsplit(".", 1)[1]:
        raise MediaError("the file does not match its entry")
    _atomic(_dir(key) / it["file"], blob)


def add(key, blob, caption="", filename=""):
    """Add one uploaded file to the end of the key's slides."""
    ext = str(filename or "").rsplit(".", 1)[-1].lower() if "." in str(filename or "") else ""
    if ext and ext not in TYPES and ext != "jpeg":
        raise MediaError(TYPE_ERROR)
    it = item_for(blob, caption)
    with _lock:
        have = items(key)
        if any(h["file"] == it["file"] for h in have):
            raise MediaError("That file is already one of this template's slides.")
        check_rule(have + [it])
        store_file(key, it, blob)
        return save_list(key, have + [it], prune=False)


def arrange(key, order, captions=None):
    """New order and captions. `order` must name exactly the files there are."""
    captions = captions if isinstance(captions, dict) else {}
    with _lock:
        have = {it["file"]: it for it in items(key)}
        order = [str(f) for f in (order or [])]
        if sorted(order) != sorted(have):
            raise MediaError("The slide list changed meanwhile. Reload and try again.")
        out = [dict(have[f], caption=clean_caption(captions.get(f, have[f]["caption"])))
               for f in order]
        return save_list(key, out)


def remove(key, name):
    with _lock:
        have = items(key)
        if not any(it["file"] == name for it in have):
            raise MediaError("no such slide")
        return save_list(key, [it for it in have if it["file"] != name])


def path_of(key, name):
    """(path, content type) of a listed file, or None."""
    try:
        check_key(key)
    except MediaError:
        return None
    if not FILE_RE.match(str(name or "")):
        return None
    for it in items(key):
        if it["file"] == name:
            return _dir(key) / name, it["type"]
    return None


def listing(key):
    """What the Hub's template preview gets: [{url, kind, caption}]."""
    return [{"url": f"/api/hub/template-media/{key}/{it['file']}",
             "kind": it["kind"], "caption": it["caption"]} for it in items(key)]


def counts(lst):
    return {"slides": len(lst), "videos": sum(1 for it in lst if it["kind"] == "video"),
            "max_slides": MAX_SLIDES, "max_videos": MAX_VIDEOS, "max_bytes": MAX_BYTES}
