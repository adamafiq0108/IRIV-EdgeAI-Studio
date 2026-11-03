# -*- coding: utf-8 -*-
"""
template_media_sync.py -- template preview media on the signed template path.

The built-in templates travel from the developer hub to the boxes as a signed
file in the release repo (source_publish.py writes templates/formats.toml and
its .sig; template_updates.py reads them). Preview media (template_media.py)
rides the same road, beside it:

  templates/media.json        {"version": 1, "templates": {key: [entry...]}}
  templates/media.json.sig    ed25519 over those exact bytes (the store key)
  templates/media/<key>/<file>  the files, as bytes

Each entry names its file, kind, type, sha256, size and caption, in slide
order. formats.toml is untouched: it is byte-compared with the source repo
copy, and the update path carries a fixed set of keys per template.

THE BOX TAKES ONLY WHAT THE SIGNED LIST NAMES. The list is verified before
anything is read from it; each key's list must pass the same checks an upload
does (template_media.valid_list: names tied to hashes, at most 5 slides and 1
video, 5 MB a file); each file is read with a 5 MB ceiling and must match its
size, hash and sniffed kind before it is stored. A repository write is not a
signature, so a leaked publish token can put bytes in the repo but cannot
make a box keep them.

NEVER A FAILED UPDATE. Media is a nice-to-have on top of the templates: no
list, a list that does not verify, a file that will not download -- each is
noted in the result and the box carries on with whatever media it already
has. Files are content-named, so a pull cut short (the wizard's 6 s budget)
resumes where it stopped next time.
"""

import hashlib
import json
import logging
import posixpath
import time
import urllib.error

from core.services import store as store_service
from core.services import template_media as media

log = logging.getLogger(__name__)

MAX_LIST_BYTES = 1 << 20


def _paths(tgt):
    base = posixpath.dirname(tgt["path"])
    join = (lambda *p: posixpath.join(base, *p)) if base else posixpath.join
    return join("media.json"), (lambda key, name: join("media", key, name))


def manifest_bytes(all_items=None):
    """The list as published: sorted, LF, ASCII -- the bytes that are signed."""
    doc = {"version": 1, "templates": all_items if all_items is not None
           else media.all_items()}
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True)
            + "\n").encode("ascii")


def parse(blob):
    """{key: list} from published bytes; MediaError when it is not a list."""
    try:
        doc = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise media.MediaError("the published media list is not valid")
    tpl = doc.get("templates") if isinstance(doc, dict) else None
    if not isinstance(tpl, dict):
        raise media.MediaError("the published media list holds no templates")
    return tpl


def _git_blob_sha(blob):
    return hashlib.sha1(b"blob %d\0" % len(blob) + blob).hexdigest()


# ---------------------------------------------------------------------------
# Publishing (developer hub; called from source_publish.publish)
# ---------------------------------------------------------------------------

def publish(mir, tok, config_dir, message):
    """Upload every file the local lists name, then the list and its
    signature. Returns {files, uploaded, unchanged, error}; never raises --
    a media problem is reported beside a successful template publish."""
    from core.services import source_publish                # noqa: PLC0415
    out = {"files": 0, "uploaded": 0, "unchanged": True, "error": ""}
    try:
        lists = media.all_items()
        list_path, file_path = _paths(mir)
        for key, lst in sorted(lists.items()):
            for it in lst:
                out["files"] += 1
                blob = (media.root() / key / it["file"]).read_bytes()
                if hashlib.sha256(blob).hexdigest() != it["sha256"]:
                    raise media.MediaError(f"{key}/{it['file']} changed on disk")
                tgt = dict(mir, path=file_path(key, it["file"]))
                try:
                    meta = source_publish._gh(
                        tgt, f"contents/{tgt['path']}?ref={tgt['branch']}", tok)
                    if meta.get("sha") == _git_blob_sha(blob):
                        continue
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise ValueError(source_publish._explain(exc, tgt))
                except (urllib.error.URLError, OSError) as exc:
                    raise ValueError(f"could not reach GitHub: {exc}")
                source_publish._commit(tgt, blob, message, tok)
                out["uploaded"] += 1
        # The list after its files, the signature last: a publish that dies
        # half way leaves a list whose signature does not match, which every
        # box refuses, never one that names files not there yet.
        blob = manifest_bytes(lists)
        sig = store_service.sign_bytes(blob, config_dir).encode("ascii")
        a = source_publish._commit(dict(mir, path=list_path), blob, message, tok)
        b = source_publish._commit(dict(mir, path=list_path + ".sig"), sig,
                                   message, tok)
        out["unchanged"] = not out["uploaded"] and a["unchanged"] and b["unchanged"]
    except (ValueError, OSError, store_service.StoreError) as exc:
        out.update(unchanged=False, error=str(exc))
    return out


# ---------------------------------------------------------------------------
# Reading (boxes; called after template_updates' check)
# ---------------------------------------------------------------------------

def fetch_list(config_dir):
    """The verified published {key: list}, None when nothing is published,
    or ValueError (StoreError when it does not verify)."""
    from core.services import template_updates as tu        # noqa: PLC0415
    from core.services import update as update_service      # noqa: PLC0415
    tgt = tu.target(config_dir)
    tok = update_service.token(config_dir)
    list_path, _ = _paths(tgt)
    blob = tu._get(tgt, list_path, tok, MAX_LIST_BYTES)
    if blob is None:
        return None
    sig = tu._get(tgt, list_path + ".sig", tok, tu.MAX_SIG_BYTES)
    if sig is None:
        raise media.MediaError("the published media list carries no signature")
    store_service.verify_bytes(blob, sig.decode("ascii", "replace"), config_dir,
                               what="the published media list")
    return parse(blob)


def _time_left():
    from core.services import template_updates as tu        # noqa: PLC0415
    deadline = getattr(tu._budget, "deadline", None)
    return None if deadline is None else deadline - time.monotonic()


def pull(config_dir, remote=None):
    """Bring this box's media up to the signed list. Returns {installed,
    files, errors}; never raises."""
    from core.services import template_updates as tu        # noqa: PLC0415
    from core.services import update as update_service      # noqa: PLC0415
    out = {"installed": [], "files": 0, "errors": []}
    try:
        remote = fetch_list(config_dir) if remote is None else remote
    except (ValueError, OSError) as exc:
        out["errors"].append({"key": "", "error": str(exc)})
        return out
    if not remote:
        return out
    tgt = tu.target(config_dir)
    tok = update_service.token(config_dir)
    _, file_path = _paths(tgt)
    for key in sorted(remote):
        try:
            media.check_key(key)
            want = media.valid_list(remote[key])
            for it in want:
                # Already here (listed, or left by a pull cut short): the
                # bytes are checked, not the name trusted.
                p = media.root() / key / it["file"]
                if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == it["sha256"]:
                    continue
                left = _time_left()
                if left is not None and left < 1.0:
                    raise media.MediaError("out of time, continued next check")
                blob = tu._get(tgt, file_path(key, it["file"]), tok, media.MAX_BYTES)
                if blob is None:
                    raise media.MediaError(f"{it['file']} is not published")
                media.store_file(key, it, blob)
                out["files"] += 1
            if media.items(key) != want:
                media.save_list(key, want)
                out["installed"].append(key)
        except (ValueError, OSError) as exc:
            # Keeps what this key had: a refused or partial download never
            # replaces a working list.
            out["errors"].append({"key": key, "error": str(exc)})
    return out


def compare(config_dir, remote=None):
    """The developer hub's report: keys whose slides differ from the
    published list. Returns {differs: [key], error}."""
    try:
        remote = fetch_list(config_dir) if remote is None else remote
    except (ValueError, OSError) as exc:
        return {"differs": [], "error": str(exc)}
    remote = remote or {}
    local = media.all_items()
    shape = lambda lst: [(i.get("file"), i.get("caption", "")) for i in lst or []]  # noqa: E731
    keys = sorted(k for k in set(local) | set(remote)
                  if shape(local.get(k)) != shape(remote.get(k)))
    return {"differs": keys, "error": ""}


def after_check(config_dir, source_hub):
    """The hook template_updates' check calls: compare on the source hub,
    pull everywhere else. Never raises; the answer is merged into the
    check's result under `media`."""
    from core.services import update as update_service      # noqa: PLC0415
    try:
        # No token, no read: the release repo is private, and a box without
        # one has nothing to fetch with (the template check said so already).
        # ...and a wizard refresh whose budget the templates already spent
        # leaves media for the next check rather than holding the wizard.
        left = _time_left()
        if not update_service.token(config_dir) or (left is not None and left < 1.0):
            return {"media": {"installed": [], "differs": [], "errors": []}}
        if source_hub:
            return {"media": compare(config_dir)}
        res = pull(config_dir)
        for e in res["errors"]:
            log.warning("[TEMPLATES] preview media %s: %s", e["key"] or "list", e["error"])
        return {"media": res}
    except Exception as exc:                                # noqa: BLE001
        log.warning("[TEMPLATES] preview media skipped: %s", type(exc).__name__)
        return {"media": {"installed": [], "differs": [], "errors": []}}
