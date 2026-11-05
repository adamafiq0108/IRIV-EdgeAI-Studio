# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
make_release.py -- build the file an admin uploads to update a box.

    cd frontend && npm run build        # builds the dashboard into core/static/app
    python scripts/make_release.py      # writes dist/irivai-<version>.tar.gz

The file holds the code, the built dashboard and MANIFEST.sha256 (the hash of
every other file). The box checks the manifest when the file is uploaded and
again when it is unpacked, so a damaged or half-sent file is refused.

A release never carries a box's own settings or keys: config/*.local.toml,
keys, .env files and caches are left out, and the box refuses a file that has
them. The box carries its own settings across to the new version itself.

The version is read from VERSION. Bump it first: a box refuses a version it is
already running.
"""
import hashlib
import os
import re
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ["core", "agent", "systems", "scripts", "tools", "config", "hub_server.py",
           "master_server.py", "run.sh", "requirements.txt", "VERSION", "LICENSE",
           "README.md", "SECURITY.md", "CONTRIBUTING.md"]
SKIP_DIR = {"__pycache__", "node_modules", ".git", "wheels", ".venv"}
SKIP_FILE = re.compile(r"(\.pyc$|\.map$|\.local\.toml$|\.pem$|\.key$|\.env$|\.lock$|id_rsa|id_ed25519)")
MANIFEST = "MANIFEST.sha256"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+.][A-Za-z0-9.]{1,20})?$")


def collect():
    out = []
    for item in INCLUDE:
        path = os.path.join(ROOT, item)
        if os.path.isfile(path):
            out.append(item)
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIR)
            for f in sorted(files):
                if SKIP_FILE.search(f):
                    continue
                out.append(os.path.relpath(os.path.join(root, f), ROOT).replace(os.sep, "/"))
    return out


def main():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
        version = fh.read().strip()
    if not VERSION_RE.match(version):
        print("VERSION is not a version number:", version)
        return 2
    if not os.path.isfile(os.path.join(ROOT, "core", "static", "app", "index.html")):
        print("The dashboard is not built. Run: cd frontend && npm run build")
        return 2
    files = collect()
    lines = []
    for rel in files:
        h = hashlib.sha256()
        with open(os.path.join(ROOT, rel), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(h.hexdigest() + "  " + rel)
    manifest = ("\n".join(lines) + "\n").encode("utf-8")
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "irivai-%s.tar.gz" % version)
    with tarfile.open(out, "w:gz") as tar:
        for rel in files:
            tar.add(os.path.join(ROOT, rel), arcname=rel, recursive=False)
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(manifest)
        info.mode = 0o644
        import io
        tar.addfile(info, io.BytesIO(manifest))
    print("%s  (%d files, %.1f MB)" % (out, len(files), os.path.getsize(out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
