"""Write install_v<VER>.sh for one unsigned box package, and SHA256SUMS.txt.

The installer is install_template.sh with @@VER@@ and @@SHA256@@ filled in
from the package itself (its VERSION file and its own hash), LF only, so a
CRLF checkout on the release laptop can never reach a box (c590430).

usage: py -3 render_installer.py <irivai-vX-box-unsigned.tar.gz> <out dir>
"""
import hashlib
import pathlib
import re
import sys
import tarfile

HERE = pathlib.Path(__file__).resolve().parent


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    pkg = pathlib.Path(argv[1]).resolve()
    out = pathlib.Path(argv[2]).resolve()
    with tarfile.open(pkg, "r:gz") as tar:
        ver = tar.extractfile("VERSION").read().decode("ascii").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(-dev)?", ver):
        raise SystemExit(f"unexpected VERSION in the package: {ver!r}")
    digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
    text = (HERE / "install_template.sh").read_bytes().decode("ascii")
    text = text.replace("\r\n", "\n")
    if "@@VER@@" not in text or text.count("@@SHA256@@") != 1:
        raise SystemExit("install_template.sh lost its placeholders")
    text = text.replace("@@VER@@", ver).replace("@@SHA256@@", digest)
    if "@@" in text:
        raise SystemExit("an unfilled placeholder is left in the installer")
    script = out / f"install_v{ver}.sh"
    script.write_bytes(text.encode("ascii"))
    sums = [(digest, pkg.name),
            (hashlib.sha256(script.read_bytes()).hexdigest(), script.name)]
    (out / "SHA256SUMS.txt").write_bytes(
        "".join(f"{h}  {n}\n" for h, n in sums).encode("ascii"))
    for h, n in sums:
        print(f"{h}  {n}")


if __name__ == "__main__":
    main(sys.argv)
