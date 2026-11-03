#!/usr/bin/env python3
"""Re-point the live release symlink. The one step of an update that needs root.

WHY THIS EXISTS. An update unpacks into /opt/irivai-releases/v<version> and
then points /opt/irivai-systor at it. The unpacking is fine: the installer
gives the service account that directory. The pointing is not. Replacing
/opt/irivai-systor means creating /opt/irivai-systor.new *inside /opt*, and
/opt is root:root 755 on every Linux there is. Owning the symlink does not
help -- POSIX asks for write permission on the PARENT directory, not on the
link.

So the updater could unpack a release and never install it. A board installed
under /home/pi never showed this, because there the parent belongs to the
service account; a board installed under /opt could not update at all, and
said so with EACCES on the ROLLBACK flip, which made it read as a failure to
undo rather than a failure to do.

WHY NOT JUST LOOSEN /opt. Making /opt group-writable by the service account
lets it delete or replace anything else installed there, for ever, to fix one
symlink. This helper is the narrow alternative, and it follows the pattern
db-console-role.py already sets: root-owned, outside every tree the service
account can write, doing exactly one thing.

WHAT IT GRANTS. Nothing new. The service account already owns the releases
directory, so it can already decide what any release contains. This lets it
say which of those directories is live -- and only those, by name, validated
below. It will not point the link outside the releases directory, will not
follow a symlink out of it, and will not replace a live path that is a real
directory rather than a link.

    sudo /usr/local/lib/irivai/flip-link.py 1.0.1
"""
import os
import re
import sys

CONF = "/etc/irivai/update.conf"
DEFAULT_LINK = "/opt/irivai-systor"
DEFAULT_RELEASES = "/opt/irivai-releases"

#: A release directory name, without its "v". Deliberately strict: this string
#: becomes a path, and the only names that exist are the ones the release
#: workflow produces from a VERSION file.
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")


def _abs_value(value):
    """An absolute path, or nothing.

    Same rule recover.py applies to the same file, for the same reason: this
    conf is written by a shell script, an installer once captured a warning
    banner into a value, and this script runs as root and flips symlinks with
    what it reads. A relative or malformed value must fall back to the
    built-in default, never be acted on.
    """
    value = (value or "").strip().strip('"').strip("'")
    return value if value.startswith("/") and ".." not in value.split("/") else ""


def _conf():
    link, releases = "", ""
    try:
        with open(CONF, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip() == "LIVE_LINK":
                    link = _abs_value(value)
                elif key.strip() == "RELEASES_DIR":
                    releases = _abs_value(value)
    except OSError:
        pass
    return link or DEFAULT_LINK, releases or DEFAULT_RELEASES


def die(msg):
    print("flip-link: " + msg, file=sys.stderr)
    raise SystemExit(1)


def main(argv):
    if len(argv) != 2:
        die("usage: flip-link.py <version>")
    version = argv[1].strip().lstrip("vV")
    if not VERSION_RE.match(version):
        die(f"not a version: {argv[1]!r}")

    link, releases = _conf()
    target = os.path.join(releases, "v" + version)

    # The target must be a real directory sitting DIRECTLY in the releases
    # directory. realpath first: a symlink planted inside releases/ would
    # otherwise be a way to point the live path anywhere on the box.
    real_target = os.path.realpath(target)
    real_releases = os.path.realpath(releases)
    if os.path.dirname(real_target) != real_releases:
        die(f"{target} is not directly inside {releases}")
    if not os.path.isdir(real_target):
        die(f"no such release: {target}")

    # Never replace something that is not a link. A box whose live path is a
    # real directory is not laid out for this, and turning that directory into
    # a symlink would strand it -- with the only copy of whatever it held.
    if os.path.exists(link) and not os.path.islink(link):
        die(f"{link} is not a symlink; this board is not laid out for updates")

    parent = os.path.dirname(link)
    if not os.path.isdir(parent):
        die(f"{parent} does not exist")

    # Atomic, exactly as the installer and the updater do it: a temporary link
    # beside the real one keeps the rename on one filesystem, and os.replace
    # is a single rename, so there is no instant with no live path at all.
    tmp = link + ".new"
    try:
        if os.path.islink(tmp) or os.path.exists(tmp):
            os.remove(tmp)
        os.symlink(real_target, tmp)
        os.replace(tmp, link)
    except OSError as exc:
        die(f"could not point {link} at {real_target}: {exc}")

    print(f"{link} -> {real_target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
