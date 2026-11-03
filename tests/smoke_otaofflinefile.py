# The offline update file every release carries, and the laptop tool that
# keeps a folder of them (Tok Aba, 2026-10-05).
#
# Pinned:
#   1. publish_release.py `offline` packs bundle + manifest + signature into
#      irivai-update-v<VERSION>.tar.gz (a dev VERSION keeps its -dev), checks
#      it with the box's own code, and gives the same bytes on a rebuild; a
#      manifest for the other channel or another version is refused
#   2. `publish` attaches it to the same release, after the manifest
#   3. release.yml builds it after Verify, before either publish step, with
#      no secret, for whichever channel the tag names
#   4. scripts/dev/fetch_offline.py downloads what is missing, verifies each
#      file before giving it its name, skips files already there, refuses a
#      bad file (leaving nothing), an asset from another repo and a name that
#      is not an offline file's; the folder is refused inside the repo; the
#      token comes from the environment or a prompt, never argv
import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "dev"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from cryptography.hazmat.primitives import serialization          # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (   # noqa: E402
    Ed25519PrivateKey)

from core.services import offline_file as of                   # noqa: E402
import publish_release as pr                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


KEY = Ed25519PrivateKey.generate()
PUB = base64.b64encode(KEY.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
REAL_ROOT, REAL_OUT = pr.ROOT, pr.OUT


def release_tree(version, app=None, key=KEY):
    """A fake checkout with a built, signed release in dist/release."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="smoke_offlinefile_"))
    (root / "config").mkdir()
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "config" / "update.toml").write_text(
        f'[release]\nowner = "o"\nrepo = "r"\ndev_repo = "rd"\n[signing]\npubkey = "{PUB}"\n',
        encoding="utf-8")
    out = root / "dist" / "release"
    out.mkdir(parents=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tf:
        data = (version + "\n").encode()
        info = tarfile.TarInfo("VERSION")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (out / pr.BUNDLE).write_bytes(raw.getvalue())
    app = app or pr.app_name("dev" if version.endswith("-dev") else "stable")
    man = pr._json_bytes({"app": app, "version": version, "schema": 1,
                          "bundle": {"name": pr.BUNDLE, "size": len(raw.getvalue()),
                                     "sha256": hashlib.sha256(raw.getvalue()).hexdigest()},
                          "notes_short": "", "unpacked_size": 10})
    (out / pr.MANIFEST).write_bytes(man)
    (out / pr.SIG).write_bytes(base64.b64encode(key.sign(man)) + b"\n")
    pr.ROOT, pr.OUT = root, out
    return root, out


def offline(channel):
    pr.cmd_offline(argparse.Namespace(channel=channel, pubkey="", mtime="1700000000"))


# -- 1. offline -------------------------------------------------------------------
root, out = release_tree("1.0.16")
offline("stable")
f = out / "irivai-update-v1.0.16.tar.gz"
check("offline packs irivai-update-v<VERSION>.tar.gz", f.exists(), os.listdir(out))
with tarfile.open(f, "r:gz") as tf:
    names = sorted(m.name for m in tf)
check("...holding exactly the bundle, the manifest and the signature",
      names == sorted([pr.BUNDLE, pr.MANIFEST, pr.SIG]), names)
check("...byte for byte",
      tarfile.open(f, "r:gz").extractfile(pr.BUNDLE).read() == (out / pr.BUNDLE).read_bytes())
man = of.verify_file(str(f), tempfile.mkdtemp(), PUB, "irivai-platform-release")
check("...and it verifies the way a box checks it", man["version"] == "1.0.16")
first = hashlib.sha256(f.read_bytes()).hexdigest()
offline("stable")
check("a rebuild gives the same bytes", hashlib.sha256(f.read_bytes()).hexdigest() == first)

root, out = release_tree("1.0.16-dev")
offline("dev")
check("a dev release gets irivai-update-v1.0.16-dev.tar.gz",
      (out / "irivai-update-v1.0.16-dev.tar.gz").exists(), os.listdir(out))
check("...the same name the box side uses",
      of.offline_file_name("1.0.16-dev") == pr.offline_name() == "irivai-update-v1.0.16-dev.tar.gz")
root, out = release_tree("1.0.16", app="irivai-platform-release-dev")
try:
    offline("stable")
    check("a manifest for the other channel is refused", False, "packed")
except SystemExit as e:
    check("a manifest for the other channel is refused", "is not this --channel" in str(e), str(e))
root, out = release_tree("1.0.16", key=Ed25519PrivateKey.generate())
try:
    offline("stable")
    check("a file that does not verify is refused and not left", False, "packed")
except SystemExit as e:
    check("a file that does not verify is refused and not left",
          "does not verify" in str(e) and not (out / "irivai-update-v1.0.16.tar.gz").exists(), str(e))

# -- 2. publish attaches it -----------------------------------------------------------
root, out = release_tree("1.0.16")
offline("stable")
sent = []


def fake_api(url, token, data=None, method=None, ctype="application/json", allow_404=False):
    sent.append((method or "GET", url))
    if url.endswith("/releases/tags/v1.0.16"):
        return {"id": 7, "body": "", "assets": [],
                "upload_url": "https://uploads.github.com/repos/o/r/releases/7/assets{?name}"}
    return {}


real_api = pr._api
pr._api = fake_api
try:
    pr.cmd_publish(argparse.Namespace(token_env="", token="t", channel="stable", repo="",
                                      prerelease=False))
finally:
    pr._api = real_api
ups = [u.split("name=", 1)[1] for m, u in sent if m == "POST" and "name=" in u]
check("publish uploads the offline file to the same release",
      ups == [pr.BUNDLE, pr.SIG, pr.MANIFEST, "irivai-update-v1.0.16.tar.gz"], ups)
check("...in the channel's own repo",
      all("/repos/o/r/" in u for m, u in sent), sent)
pr.ROOT, pr.OUT = REAL_ROOT, REAL_OUT

# -- 3. release.yml -----------------------------------------------------------------
yml = open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8").read()
steps = re.split(r"\n      - ", yml)
off = [s for s in steps if "publish_release.py offline" in s]
check("release.yml has one offline step", len(off) == 1, len(off))
if off:
    s = off[0]
    check("...for the tag's channel",
          '--channel "${{ steps.channel.outputs.channel }}"' in s and " if:" not in s, s)
    check("...with no secret", "secrets." not in s, s)
    order = [i for i, st in enumerate(steps) if "publish_release.py verify" in st] \
        + [steps.index(s)] \
        + [i for i, st in enumerate(steps) if "publish_release.py publish" in st]
    check("...after Verify and before both publish steps",
          len(order) == 4 and order[0] < order[1] < order[2] < order[3], order)

# -- 4. fetch_offline -----------------------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "_smoke_fetch_offline", os.path.join(ROOT, "scripts", "dev", "fetch_offline.py"))
fo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fo)
fo.settings = lambda channel: ("o", "r", PUB)
BASE = "https://api.github.com/repos/o/r"
files = {}
for v in ("1.0.15", "1.0.16"):
    _, out = release_tree(v)
    offline("stable")
    files[f"{BASE}/releases/assets/{v}"] = out / f"irivai-update-v{v}.tar.gz"
_, out = release_tree("1.0.17", key=Ed25519PrivateKey.generate())
shutil.copy(out / pr.MANIFEST, out / "m")
with tarfile.open(out / "bad.tar.gz", "w:gz") as tf:
    for n in (pr.BUNDLE, pr.MANIFEST, pr.SIG):
        tf.add(out / n, arcname=n)
files[f"{BASE}/releases/assets/1.0.17"] = out / "bad.tar.gz"
pr.ROOT, pr.OUT = REAL_ROOT, REAL_OUT
releases = [
    {"tag_name": f"v{v}", "assets": [{"name": f"irivai-update-v{v}.tar.gz",
                                      "url": f"{BASE}/releases/assets/{v}"},
                                     {"name": "bundle.tar.gz", "url": f"{BASE}/releases/assets/b{v}"}]}
    for v in ("1.0.15", "1.0.16", "1.0.17")]
releases.append({"tag_name": "v9", "assets": [
    {"name": "irivai-update-v9.0.0.tar.gz", "url": "https://api.github.com/repos/x/y/releases/assets/9"},
    {"name": "../irivai-update-v1.tar.gz", "url": f"{BASE}/releases/assets/e"}]})
fetched = []
fo.get_json = lambda url, tok: releases if "page=1" in url else []
fo.download = lambda url, tok, dest: (fetched.append(url), shutil.copy(files[url], dest))
dest = fo.dest_dir(tempfile.mkdtemp(prefix="smoke_offline_dest_"))
quiet = lambda *a: None                                         # noqa: E731
got = fo.fetch("stable", dest, "tok", log=quiet)
check("fetch downloads and verifies what is missing",
      got["got"] == ["irivai-update-v1.0.15.tar.gz", "irivai-update-v1.0.16.tar.gz"], got)
check("...refuses a file that does not verify and leaves nothing of it",
      "irivai-update-v1.0.17.tar.gz" in got["refused"]
      and sorted(os.listdir(dest)) == ["irivai-update-v1.0.15.tar.gz", "irivai-update-v1.0.16.tar.gz"],
      (got, os.listdir(dest)))
check("...refuses an asset from another repo before downloading it",
      "irivai-update-v9.0.0.tar.gz" in got["refused"]
      and not any("/x/y/" in u for u in fetched), fetched)
check("...and ignores names that are not an offline file's",
      not any(u.endswith(("/b1.0.15", "/e")) for u in fetched), fetched)
fetched.clear()
got = fo.fetch("stable", dest, "tok", log=quiet)
check("a second run skips the files already there",
      sorted(got["skipped"]) == ["irivai-update-v1.0.15.tar.gz", "irivai-update-v1.0.16.tar.gz"]
      and not any(u.endswith(("1.0.15", "1.0.16")) for u in fetched), (got, fetched))
try:
    fo.dest_dir(os.path.join(ROOT, "irivai-updates"))
    check("a folder inside the repo is refused", False, "accepted")
except SystemExit:
    check("a folder inside the repo is refused", True)
check("the default folder is irivai-updates beside the repo, outside it",
      fo.ROOT.parent / "irivai-updates" == pathlib.Path(ROOT).parent / "irivai-updates")
src = open(os.path.join(ROOT, "scripts", "dev", "fetch_offline.py"), encoding="utf-8").read()
# Pipi F1 (2026-10-05): the laptop tool checks with the box's verify_file, so a
# pax header claiming 64 MB in a 70 KB asset is refused there too, memory flat.
import gzip                                                     # noqa: E402
import tracemalloc                                              # noqa: E402
bombp = pathlib.Path(tempfile.mkdtemp()) / "bomb.tar.gz"
with gzip.open(bombp, "wb", compresslevel=1) as g:
    info = tarfile.TarInfo("x")
    info.type, info.size = tarfile.XHDTYPE, 64 << 20
    g.write(info.tobuf(tarfile.PAX_FORMAT))
    for _ in range(64):
        g.write(b"\0" * (1 << 20))
    g.write(b"\0" * 1024)
files[f"{BASE}/releases/assets/1.0.18"] = bombp
releases.append({"tag_name": "v1.0.18", "assets": [
    {"name": "irivai-update-v1.0.18.tar.gz", "url": f"{BASE}/releases/assets/1.0.18"}]})
tracemalloc.start()
got = fo.fetch("stable", dest, "tok", log=quiet)
peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
check("the laptop tool refuses a pax-header bomb and leaves nothing of it",
      "irivai-update-v1.0.18.tar.gz" in got["refused"]
      and not any("1.0.18" in n for n in os.listdir(dest)), (got, os.listdir(dest)))
check(f"...with memory flat (peak {peak >> 20} MB)", peak < (8 << 20), peak)
check("the token never comes from argv", '"--token"' not in src and "getpass.getpass(" in src
      and "IRIVAI_RELEASE_TOKEN" in src and "IRIVAI_RELEASE_DEV_TOKEN" in src)
check("...and is dropped off the GitHub API host on a redirect",
      'new.remove_header("Authorization")' in src)
check("no em dash in the new files",
      all("—" not in open(os.path.join(ROOT, *p), encoding="utf-8").read()
          for p in (("scripts", "dev", "fetch_offline.py"), ("scripts", "dev", "publish_release.py"),
                    (".github", "workflows", "release.yml"))))

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
