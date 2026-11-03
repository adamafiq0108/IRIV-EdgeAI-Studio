"""Build the UNSIGNED box package for one release from a git worktree.

Product tree = `git archive HEAD` (LF blobs, git file modes) filtered by
  1. the curated customer recipe (DEV_README 1.2): .claude agent docs tests
     core/dev scripts/dev removed,
  2. scripts/dev/publish_release.py INCLUDE + _excluded(rel, "stable"),
plus the built frontend core/static/app (untracked, from disk).

usage: py -3 make_box_package.py <worktree> <out.tar.gz> <file-list.txt>
then:  py -3 render_installer.py <out.tar.gz> <out dir>   (installer + SHA256SUMS)
"""
import gzip
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tarfile

wt = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2])
listing = pathlib.Path(sys.argv[3])

spec = importlib.util.spec_from_file_location(
    "publish_release", wt / "scripts" / "dev" / "publish_release.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

CURATED = (".claude", "agent", "docs", "tests", "core/dev", "scripts/dev")
MTIME = 1700000000


def wanted(rel):
    if any(rel == c or rel.startswith(c + "/") for c in CURATED):
        return False
    top = rel.split("/", 1)[0]
    if top not in pr.INCLUDE:
        return False
    if pr._excluded(rel, "stable"):
        return False
    # every parent directory must pass too (pack prunes directories)
    parts = rel.split("/")
    for i in range(1, len(parts)):
        if pr._excluded("/".join(parts[:i]), "stable"):
            return False
    return True


raw = subprocess.run(["git", "-C", str(wt), "archive", "--format=tar", "HEAD"],
                     check=True, capture_output=True).stdout
src = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")

names = []
with open(out, "wb") as fh, \
        gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz, \
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
    for m in src.getmembers():
        if not m.isfile():
            if m.issym() or m.islnk():
                raise SystemExit(f"link in tree: {m.name}")
            continue
        rel = m.name
        if not wanted(rel):
            continue
        data = src.extractfile(m).read()
        info = tarfile.TarInfo(rel)
        info.size = len(data)
        info.mode = 0o755 if (m.mode & 0o100) else 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = MTIME
        tar.addfile(info, io.BytesIO(data))
        names.append(rel)
    app = wt / "core" / "static" / "app"
    if not (app / "index.html").is_file():
        raise SystemExit("core/static/app not built")
    for dirpath, dirnames, filenames in os.walk(app):
        dirnames.sort()
        for fn in sorted(filenames):
            p = pathlib.Path(dirpath) / fn
            rel = p.relative_to(wt).as_posix()
            if rel in names:
                continue
            data = p.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = MTIME
            tar.addfile(info, io.BytesIO(data))
            names.append(rel)

# owner-only chunks must be absent (publish_release._check_frontend rule)
bad = [n for n in names if n.startswith("core/static/app/assets/")
       and any(c in n for c in pr.OWNER_CHUNKS)]
if bad:
    raise SystemExit(f"owner chunks in a customer build: {bad}")
listing.write_bytes(("\n".join(sorted(names)) + "\n").encode("utf-8"))
print(f"{out}: {len(names)} files")
