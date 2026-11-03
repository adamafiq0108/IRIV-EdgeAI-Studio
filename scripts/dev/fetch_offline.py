#!/usr/bin/env python3
"""Copy every published offline update file to this laptop -- the OWNER's tool.

Each release carries irivai-update-v<version>.tar.gz (release.yml, the
`offline` step of publish_release.py): the signed bundle, its manifest and
signature in one archive, which "Update manually" on a box installs when the
box has no internet or no update token. This keeps a folder of them on the
laptop, ready to carry to a site.

    py -3 scripts/dev/fetch_offline.py                   customer releases
    py -3 scripts/dev/fetch_offline.py --channel dev     developer releases
    py -3 scripts/dev/fetch_offline.py --dest D:\\iriv    another folder

Where they go: --dest, else $IRIVAI_UPDATES_DIR, else the folder irivai-updates
BESIDE the repo (on Tok Aba's laptop C:\\Users\\User\\Desktop\\IRIV EDGE AI\\
irivai-updates). A folder inside the repo is refused: bundles are never
committed. A file already in the folder is skipped; a new one is downloaded
to a .part file, checked with the box's own code (signature with the OTA key
in config/update.toml, the manifest's app for the channel, the bundle's hash)
and only then given its name.

The token is read from $IRIVAI_RELEASE_TOKEN (customer repo) or
$IRIVAI_RELEASE_DEV_TOKEN (developer repo), else asked for without echo. Never
an argument: argv is visible to every process on the machine. It is sent to
api.github.com only; the redirect to GitHub's storage host goes without it.
"""
import argparse
import getpass
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.services import offline_file                      # noqa: E402

TOKEN_ENV = {"stable": "IRIVAI_RELEASE_TOKEN", "dev": "IRIVAI_RELEASE_DEV_TOKEN"}
APP = {"stable": "irivai-platform-release", "dev": "irivai-platform-release-dev"}
#: A name this tool will write to disk. Asset names come from GitHub; only
#: the offline file's own shape is accepted, never a path.
NAME_RE = re.compile(r"^irivai-update-v[0-9][0-9A-Za-z.+-]{0,40}\.tar\.gz$")
API_HOSTS = ("api.github.com",)


class _NoAuthOffGitHub(urllib.request.HTTPRedirectHandler):
    """The asset URL 302s to signed storage; the token must not follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname not in API_HOSTS:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_NoAuthOffGitHub)


def _open(url, tok, accept):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + tok, "Accept": accept,
        "User-Agent": "irivai-fetch-offline"})
    return _opener.open(req, timeout=120)


def get_json(url, tok):
    with _open(url, tok, "application/vnd.github+json") as r:
        return json.loads(r.read(16 << 20) or b"[]")


def download(url, tok, dest):
    with _open(url, tok, "application/octet-stream") as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def settings(channel):
    cfg = tomllib.loads((ROOT / "config" / "update.toml").read_text(encoding="utf-8"))
    rel, sign = cfg.get("release") or {}, cfg.get("signing") or {}
    repo = rel.get("dev_repo" if channel == "dev" else "repo")
    if not (rel.get("owner") and repo and sign.get("pubkey")):
        raise SystemExit("config/update.toml needs [release] owner, repo, dev_repo "
                         "and [signing] pubkey")
    return str(rel["owner"]), str(repo), str(sign["pubkey"])


def dest_dir(arg=None):
    """--dest, $IRIVAI_UPDATES_DIR, or irivai-updates beside the repo."""
    d = pathlib.Path(arg or os.environ.get("IRIVAI_UPDATES_DIR")
                     or ROOT.parent / "irivai-updates").resolve()
    if d == ROOT or ROOT in d.parents:
        raise SystemExit(f"{d} is inside the repo; update files are never kept there")
    d.mkdir(parents=True, exist_ok=True)
    return d


def offline_assets(releases):
    """(release tag, asset) for every offline update file listed."""
    out = []
    for rel in releases if isinstance(releases, list) else []:
        if rel.get("draft"):
            continue
        for a in rel.get("assets") or []:
            if NAME_RE.match(str(a.get("name") or "")):
                out.append((str(rel.get("tag_name") or ""), a))
    return out


def fetch(channel, dest, tok, log=print):
    """Download what is missing; returns {"got": [...], "skipped": [...],
    "refused": [...]}."""
    owner, repo, pub = settings(channel)
    base = f"https://api.github.com/repos/{owner}/{repo}"
    releases = []
    for page in range(1, 11):
        chunk = get_json(f"{base}/releases?per_page=100&page={page}", tok)
        releases += chunk if isinstance(chunk, list) else []
        if not isinstance(chunk, list) or len(chunk) < 100:
            break
    done = {"got": [], "skipped": [], "refused": []}
    for tag, asset in offline_assets(releases):
        name = asset["name"]
        final = dest / name
        if final.exists():
            done["skipped"].append(name)
            log(f"have     {name}")
            continue
        url = str(asset.get("url") or "")
        if not url.lower().startswith(f"{base}/releases/assets/".lower()):
            done["refused"].append(name)
            log(f"REFUSED  {name}: not an asset of {owner}/{repo}")
            continue
        part = dest / (name + ".part")
        scratch = tempfile.mkdtemp(prefix="fetch-offline-")
        try:
            download(url, tok, str(part))
            man = offline_file.verify_file(str(part), scratch, pub, APP[channel])
            if offline_file.offline_file_name(man.get("version")) != name:
                raise offline_file.OfflineError(
                    f"it holds v{man.get('version')}, not what its name says")
            os.replace(part, final)
            done["got"].append(name)
            log(f"got      {name}  ({final.stat().st_size >> 20} MB, verifies, {tag})")
        except (offline_file.OfflineError, OSError) as exc:
            done["refused"].append(name)
            log(f"REFUSED  {name}: {exc}")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            if part.exists():
                part.unlink()
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", default="stable", choices=("stable", "dev"))
    ap.add_argument("--dest", default="", help="folder to keep the files in")
    args = ap.parse_args(argv)
    dest = dest_dir(args.dest)
    tok = os.environ.get(TOKEN_ENV[args.channel], "").strip() or getpass.getpass(
        f"read token for the {args.channel} release repo (not echoed): ").strip()
    if not tok:
        raise SystemExit("no token")
    done = fetch(args.channel, dest, tok)
    print(f"{dest}: {len(done['got'])} new, {len(done['skipped'])} already here, "
          f"{len(done['refused'])} refused")
    return 1 if done["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
