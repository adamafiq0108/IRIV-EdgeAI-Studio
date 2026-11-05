# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
manual_update.py -- update the software from a file the admin uploads.

There is no over-the-air update. A box is first installed from the OS image,
and after that an admin updates it by uploading a release file (built on a
computer with scripts/make_release.py) on the About page.

    irivai-<version>.tar.gz   the release: the code, the built dashboard, and
                              MANIFEST.sha256 listing every file's hash

The box keeps TWO releases, the live one and the previous one, so an update can
be undone and the drive does not fill up (scripts/apply_release.py prunes).

What this module does as the service user: check the file, unpack it into a
new folder under /opt/irivai-releases, carry the box's own settings across, and
ask the root helper to switch the live link and restart.

What the check proves, and what it does not. The manifest catches a damaged or
half-sent upload and a file that is not an IRIV release at all. It is not a
signature: there is no key, so it cannot say WHO made the file. The protection
against a hostile file is the admin login that gates the upload.
"""
import hashlib
import os
import re
import shutil
import subprocess
import tarfile

RELEASES = "/opt/irivai-releases"
LINK = "/opt/irivai-systor"
INCOMING = os.path.join(RELEASES, ".incoming")
STAGED = os.path.join(INCOMING, "update.tar.gz")
HELPER = ["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I",
          "/usr/local/lib/irivai/apply-release.py"]

MANIFEST = "MANIFEST.sha256"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+.][A-Za-z0-9.]{1,20})?$")
#: A release is ~6 MB of code and the built dashboard; this is far past any real
#: one and still bounded.
MAX_UPLOAD = 600 << 20
MAX_UNPACKED = 2 << 30
MAX_MEMBERS = 20000
REQUIRED = ("VERSION", "hub_server.py", "master_server.py", "core/__init__.py", MANIFEST)
#: Never in a release: they are the box's own, and a release must not replace
#: or ship them.
FORBIDDEN = re.compile(r"(\.local\.toml|\.pem|\.key|\.env|id_rsa|id_ed25519)$")
#: Carried from the live release into the new one.
CARRY = re.compile(r"^(config/[^/]+|systems/[^/]+/[^/]+)\.local\.toml$")


class UpdateError(ValueError):
    """A refusal, worded for the person on the About page."""


def layout_ready():
    """Is this box set up for updates: the live folder a link, the releases
    folder there and writable by the service user."""
    return (os.path.islink(LINK) and os.path.isdir(RELEASES)
            and os.access(RELEASES, os.W_OK))


def _read_version(folder):
    try:
        with open(os.path.join(folder, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def status():
    live_dir = os.path.realpath(LINK) if os.path.islink(LINK) else ""
    out = {"ready": layout_ready(), "live": None, "previous": None, "staged": None}
    if live_dir:
        out["live"] = {"name": os.path.basename(live_dir), "version": _read_version(live_dir)}
    try:
        with open(os.path.join(RELEASES, ".previous"), encoding="utf-8") as fh:
            prev = fh.read().strip()
    except OSError:
        prev = ""
    prev_dir = os.path.join(RELEASES, prev) if re.match(r"^[A-Za-z0-9._-]+$", prev) else ""
    if prev_dir and os.path.isdir(prev_dir):
        out["previous"] = {"name": prev, "version": _read_version(prev_dir)}
    if os.path.isfile(STAGED):
        try:
            out["staged"] = inspect(STAGED)
        except UpdateError as e:
            out["staged"] = {"error": str(e)}
    return out


def begin_upload():
    """The path the upload is written to, with the old one cleared."""
    if not layout_ready():
        raise UpdateError("this box is not set up for software updates")
    os.makedirs(INCOMING, exist_ok=True)
    discard()
    return STAGED


def discard():
    try:
        os.unlink(STAGED)
    except OSError:
        pass


def _safe_name(name):
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return False
    return ".." not in name.split("/")


def inspect(path):
    """Open the file and say what it is, or raise UpdateError. Reads the
    archive's index only; nothing is unpacked."""
    try:
        tar = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError, EOFError):
        raise UpdateError("that is not an update file (it could not be opened as .tar.gz)")
    names, total = set(), 0
    with tar:
        try:
            members = tar.getmembers()
        except (tarfile.TarError, OSError, EOFError):
            raise UpdateError("the file is damaged or was not fully uploaded")
        if len(members) > MAX_MEMBERS:
            raise UpdateError("the file holds too many files to be an update")
        for m in members:
            if not _safe_name(m.name):
                raise UpdateError("the file holds an unsafe path (" + m.name[:60] + ")")
            if not (m.isreg() or m.isdir()):
                raise UpdateError("the file holds something that is not a plain file "
                                  "or folder (" + m.name[:60] + ")")
            if m.isreg() and FORBIDDEN.search(m.name):
                raise UpdateError("an update must not carry a box's own settings or "
                                  "keys (" + m.name[:60] + ")")
            total += m.size
            names.add(m.name.rstrip("/"))
        if total > MAX_UNPACKED:
            raise UpdateError("the file is far larger than any update")
        missing = [r for r in REQUIRED if r not in names]
        if missing:
            raise UpdateError("this is not an IRIV release (missing " + ", ".join(missing) + ")")
        vfile = tar.extractfile("VERSION")
        version = (vfile.read(64).decode("utf-8", "replace").strip() if vfile else "")
    if not VERSION_RE.match(version):
        raise UpdateError("the file's version is not valid")
    return {"version": version, "files": len([n for n in names if n]),
            "bytes": os.path.getsize(path), "unpacked": total}


def _verify_manifest(folder):
    """Every file under `folder` against MANIFEST.sha256, both ways: a listed
    file that is wrong or gone, and a file that is not listed."""
    listed = {}
    with open(os.path.join(folder, MANIFEST), encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            digest, _, rel = line.partition("  ")
            if not re.match(r"^[0-9a-f]{64}$", digest) or not _safe_name(rel):
                raise UpdateError("the file's manifest is damaged")
            listed[rel] = digest
    seen = set()
    for root, _dirs, files in os.walk(folder):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), folder).replace(os.sep, "/")
            if rel == MANIFEST:
                continue
            seen.add(rel)
            if rel not in listed:
                raise UpdateError("the file holds something its manifest does not list (" + rel[:60] + ")")
            h = hashlib.sha256()
            with open(os.path.join(root, f), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != listed[rel]:
                raise UpdateError("the file is damaged (" + rel[:60] + " does not match its checksum)")
    gone = [r for r in listed if r not in seen]
    if gone:
        raise UpdateError("the file is incomplete (" + gone[0][:60] + " is missing)")


def _carry_state(live_dir, new_dir):
    """The box's own settings and passwords: every config/*.local.toml and each
    built-in system's own *.local.toml."""
    for root, _dirs, files in os.walk(live_dir):
        rel_root = os.path.relpath(root, live_dir).replace(os.sep, "/")
        if rel_root.count("/") > 1 or rel_root.startswith((".venv", "logs", "wheels")):
            continue
        for f in files:
            rel = (f if rel_root == "." else rel_root + "/" + f)
            if CARRY.match(rel):
                dst = os.path.join(new_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(live_dir, rel), dst)


def install(path=STAGED):
    """Unpack, verify, carry settings across, and switch. Returns what the page
    shows; the hub restarts a moment after this returns."""
    if not layout_ready():
        raise UpdateError("this box is not set up for software updates")
    found = inspect(path)
    live_dir = os.path.realpath(LINK)
    name = "v" + found["version"]
    if os.path.basename(live_dir) == name or _read_version(live_dir) == found["version"]:
        raise UpdateError("version " + found["version"] + " is already running")
    tmp = os.path.join(RELEASES, "." + name + ".tmp")
    final = os.path.join(RELEASES, name)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        _verify_manifest(tmp)
        _carry_state(live_dir, tmp)
        os.makedirs(os.path.join(tmp, "logs"), exist_ok=True)
        if os.path.exists(final):
            shutil.rmtree(final)
        os.rename(tmp, final)
    except UpdateError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except (tarfile.TarError, OSError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError("the update could not be unpacked: " + str(e)[:120])
    _run_helper("apply", name)
    discard()
    return {"version": found["version"], "name": name}


def rollback():
    if not layout_ready():
        raise UpdateError("this box is not set up for software updates")
    _run_helper("rollback", "")
    return {"ok": True}


def _run_helper(action, name):
    try:
        r = subprocess.run(HELPER, input=action + "\n" + name + "\n", text=True,
                           capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError("the box could not switch versions: " + str(e)[:100])
    if r.returncode != 0:
        msg = r.stdout.strip() or r.stderr.strip() or "failed"
        try:
            import json
            msg = json.loads(r.stdout).get("error", msg)
        except Exception:                                   # noqa: BLE001
            pass
        raise UpdateError("the box could not switch versions: " + str(msg)[:160])
