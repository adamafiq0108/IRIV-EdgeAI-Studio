# An update can actually be made LIVE, on a board installed under /opt.
#
# WHAT WENT WRONG. apply_update unpacks a release into /opt/irivai-releases,
# which the installer gives the service account, and then points
# /opt/irivai-systor at it. That second step creates /opt/irivai-systor.new
# INSIDE /opt, and POSIX asks for write permission on the parent directory,
# not on the symlink. /opt is root:root 755. So the account could unpack a
# release and never install it.
#
# It hid because of where boards were installed. Under /home/pi the parent
# belongs to the service account and the flip works; only an /opt board fails.
# And it failed with EACCES on the ROLLBACK flip -- the real flip failed first,
# the handler tried to put the old release back and failed identically -- so
# the operator saw a failure to undo, naming the OLD version, and it read as a
# rollback bug.
#
# WHAT IS PINNED. That the flip falls back to a root helper rather than
# failing; that the helper refuses everything it should refuse, since it runs
# as root on a string the service account chose; and that the installer puts
# it somewhere an update cannot rewrite.
import io
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flip_link                                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


APPLY = read("scripts", "apply_update.py")
INSTALL = read("scripts", "install.sh")

# -- the updater asks for help instead of giving up ----------------------------------
check("flip() catches the permission error rather than dying on it",
      "except PermissionError:" in APPLY and "_flip_privileged(link, target)" in APPLY)
check("...and the helper lives OUTSIDE every release, where an update cannot "
      "rewrite it",
      'FLIP_HELPER = "/usr/local/lib/irivai/flip-link.py"' in APPLY)
check("...and it is asked for a VERSION, never handed a path to trust",
      'run(["sudo", "-n", FLIP_HELPER, version]' in APPLY)
check("...and a board without the helper is told exactly how to install it",
      "is not installed" in APPLY and "sudo install -m 755" in APPLY)

# -- the installer puts it there, root-owned, and permits the call --------------------
check("install.sh installs the helper root-owned",
      'install -m 755 "${SRC_DIR}/scripts/flip_link.py" '
      '/usr/local/lib/irivai/flip-link.py' in INSTALL
      and "chown root:root /usr/local/lib/irivai/flip-link.py" in INSTALL)
check("...and the sudoers rule names it, with no wildcard",
      "NOPASSWD:" in INSTALL
      and "/usr/local/lib/irivai/flip-link.py" in INSTALL.split("NOPASSWD:")[1])
check("the helper is not on the bench-only list -- a board needs it",
      "scripts/flip_link.py" not in INSTALL.split("BENCH_ONLY=(")[1].split(")")[0])

# -- what the helper refuses, running as root on a string it was handed ---------------
TMP = tempfile.mkdtemp()
RELEASES = os.path.join(TMP, "irivai-releases")
LIVE = os.path.join(TMP, "live", "irivai-systor")
os.makedirs(os.path.join(RELEASES, "v1.0.1"))
os.makedirs(os.path.dirname(LIVE))
flip_link.CONF = os.path.join(TMP, "nonexistent.conf")
flip_link.DEFAULT_LINK = LIVE
flip_link.DEFAULT_RELEASES = RELEASES


def refuses(argv1):
    try:
        flip_link.main(["flip-link.py", argv1])
        return False
    except SystemExit as exc:
        return exc.code != 0


for label, value in (
        ("a path instead of a version", "/etc/shadow"),
        ("a traversal out of the releases directory", "../../../etc"),
        ("a version with a slash in it", "1.0.1/../../etc"),
        ("a version that is not a version", "; rm -rf /"),
        ("an empty version", ""),
        ("a release that does not exist", "9.9.9")):
    check("the helper refuses " + label, refuses(value))

# A symlink planted inside the releases directory must not become a way to
# point the live path anywhere on the box. Only checkable where symlinks can
# be made without privilege.
try:
    os.symlink(os.path.join(TMP, "elsewhere"), os.path.join(RELEASES, "v6.6.6"))
    os.makedirs(os.path.join(TMP, "elsewhere"), exist_ok=True)
    check("...and a symlink planted in the releases directory leading out of it",
          refuses("6.6.6"))
    SYMLINKS = True
except (OSError, NotImplementedError):
    SYMLINKS = False
    print("skip  symlink cases -- this platform will not make one unprivileged")

# -- a live path that is a real directory is never replaced ---------------------------
os.makedirs(LIVE)
check("the helper refuses to replace a live path that is a real directory, "
      "which would strand whatever it holds",
      refuses("1.0.1"))
os.rmdir(LIVE)

# -- and the happy path actually points it -------------------------------------------
if SYMLINKS:
    rc = flip_link.main(["flip-link.py", "v1.0.1"])
    check("a real release is made live, and the leading v is accepted",
          rc == 0 and os.path.islink(LIVE)
          and os.path.realpath(LIVE) == os.path.realpath(
              os.path.join(RELEASES, "v1.0.1")))
    # Twice in a row: an update that is retried must not trip over its own
    # leftover link. REPLACING one is the whole point -- os.replace over an
    # existing symlink is what makes the swap atomic -- and it is exactly what
    # Windows refuses (WinError 5), so this half is checked where it matters.
    if os.name == "posix":
        check("...and flipping again, over an existing link, works",
              flip_link.main(["flip-link.py", "1.0.1"]) == 0)
        check("...leaving no .new link behind",
              not os.path.exists(LIVE + ".new"))
    else:
        print("skip  re-flip -- Windows refuses os.replace over a symlink")

# -- the conf is read, and a poisoned conf falls back rather than acting on it --------
conf = os.path.join(TMP, "update.conf")
io.open(conf, "w", encoding="utf-8").write(
    f"LIVE_LINK={LIVE}\nRELEASES_DIR={RELEASES}\n")
flip_link.CONF = conf
check("the helper reads LIVE_LINK and RELEASES_DIR from the conf",
      flip_link._conf() == (LIVE, RELEASES), flip_link._conf())

io.open(conf, "w", encoding="utf-8").write(
    "LIVE_LINK=not/absolute\nRELEASES_DIR=/opt/../etc\n")
link, releases = flip_link._conf()
check("...and a relative or traversing value falls back to the built-in default, "
      "because this runs as root on a file a shell script wrote",
      link == LIVE and releases == RELEASES, (link, releases))

# -- the file on disk is executable in its own right ----------------------------------
src = os.path.join(ROOT, "scripts", "flip_link.py")
check("the helper has a shebang, since sudo runs it directly",
      io.open(src, encoding="utf-8").readline().startswith("#!"))
if os.name == "posix":
    check("...and is executable in the repo",
          bool(os.stat(src).st_mode & stat.S_IXUSR))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
