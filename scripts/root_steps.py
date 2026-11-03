#!/usr/bin/env python3
"""Install the root-owned pieces a release needs, from bytes root has verified.

WHY. An over-the-air update swaps the release tree as the service account, so
the helpers in /usr/local/lib/irivai, the /etc/sudoers.d rules and the
nftables units stayed as the INSTALL left them: board .21 reached 1.0.26 with
no storage-setup.py (2026-10-07). The applier now asks for this once per
update (one no-argument sudoers rule, request on stdin), and
scripts/upgrade_steps.sh runs the same list by hand (--tree).

WHAT ROOT TRUSTS (Pipi F2, gate 1.0.14): nothing the service account wrote.
The request carries the manifest bytes and signature, checked against the key
compiled in below (never the box's config, which the account edits); the
manifest's "root" table pins scripts/root_items.json and every file it names.
Each file is read ONCE into memory and hashed, and those same bytes are
written to the root-owned destination (temp file beside it, fsync, rename):
nothing can be swapped between check and write, and one mismatch installs
nothing. A manifest older than the last one applied is skipped (no downgrade
to a helper with a known fault); a rolled-back release keeps the newer
helpers, which take their requests on stdin and serve it unchanged.

ONE-TIME STEPS (upgrade(), the list's "upgrade" table). upgrade_steps.sh also
does root work outside the list, by hand, once; a box nobody ran it on
(customers went 1.0.34 -> 1.0.37 over the air) kept the old state. The part an
unattended update may do is done here, before the list, under the same
signature and pins: packages, the hub unit's baked --port, the link-local
drop-in, KillMode=process on the apply unit, the video gateway's config
(loopback-only, MoQ off: Pipi H1). Each step checks first; a box
records the rev it finished in upgrade-steps.json beside STATE and is not
asked again until the rev moves. A step that could not finish (offline) is
said and retried by the next update; nothing here ever fails an update.

    sudo -n /usr/bin/python3 -I /usr/local/lib/irivai/root-steps.py  < request
    python3 -I scripts/root_steps.py --tree <release dir> [--dry-run]   (root)

Prints done:/skipped:/refused: lines; exit 0, 1 if anything was refused, 2 for
a bad request; `--selftest` checks this copy can verify at all. Stdlib only.
KEEP `--reexec` + the request on stdin working: an older copy hands over to a
newer one that way, and only after the new copy passed --selftest.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

LIB = "/usr/local/lib/irivai"
SUDOERS_DIR = "/etc/sudoers.d"
UNIT_DIR = "/etc/systemd/system"
HUB_UNIT = "/etc/systemd/system/irivai-hub.service"
ETC = "/etc/irivai"
CONF = "/etc/irivai/update.conf"
STATE = "/var/lib/irivai/root-steps.json"
LOCK = "/run/irivai-root-steps.lock"
LOCK_WAIT_S = 300
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
ITEMS = "scripts/root_items.json"
SELF = "root-steps.py"
APPS = ("irivai-platform-release", "irivai-platform-release-dev")
#: The OTA signing key (config/update.toml [signing], update.py PUBKEY_B64),
#: compiled in: a key read from the account's config is a key it chooses. A
#: new key arrives as a new copy of this file, under the old key's signature.
PUBKEYS = ("TjgmysV+4XU8VRGl7UqzQNaE7azT6dvuiqF7MLIUcLE=",)
MAX_FILE = 4 << 20
RELEASE_RE = re.compile(r"^v[0-9][0-9A-Za-z.+-]{0,40}$")
NAME_RE = re.compile(r"^irivai-[a-z0-9-]{1,40}$")
FILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]{1,60}$")
#: upgrade()'s paths and limits. apt has this long in all, so the whole root
#: step stays inside the 600 s an older applier gives it.
NM_CONF_DIR = "/etc/NetworkManager/conf.d"
LINKLOCAL = "80-irivai-linklocal.conf"
APPLY_UNIT = "irivai-apply.service"
LIVE = "/opt/irivai-systor"
APT_BUDGET_S = 240
#: An install is not STARTED with less than this left of the budget: a
#: timeout SIGKILLs apt, and between unpack and configure that leaves "dpkg
#: was interrupted", which blocks every later install on the box (Pipi S1-L3).
APT_INSTALL_MIN_S = 90
#: The video gateway (scripts/mediamtx/install.sh puts it there). Its control
#: API on loopback is how a restart is judged: MediaMTX that rejected its
#: config exits, systemd's Restart=always brings it back to exit again, and
#: in all that time nothing answers there.
MTX_DIR = "/etc/mediamtx"
MTX_CONF = "mediamtx.yml"
MTX_UNIT = "irivai-mediamtx.service"
MTX_API = "http://127.0.0.1:9997/v3/config/global/get"
MTX_WAIT_S = 10
#: The "upgrade" table's keys that name a file in the release. Each is read
#: and pinned exactly as a helper is (publish_release.root_pins uses this).
UPGRADE_FILES = ("linklocal", "mediamtx")
OUT = []


class Refused(Exception):
    """A reason; what it stopped is left as it was."""


def _is_root():
    return os.geteuid() == 0


def say(kind, text):
    OUT.append((kind, text))
    print(f"{kind}: {text}", flush=True)


def refused():
    return any(k == "refused" for k, _ in OUT)


def _run(argv, timeout=60):
    """A tool that is missing or hangs is a failed step, never a crash."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": SAFE_PATH, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _which(name):
    return shutil.which(name, path=SAFE_PATH)


def semver(v):
    return tuple(int(re.match(r"\d*", p).group() or 0) for p in
                 (str(v or "").lstrip("vV").split(".") + ["0", "0", "0"])[:3])


def _conf(key, default):
    """update.conf's value if absolute and without '..' (flip-link's rule)."""
    try:
        with open(CONF, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if k.strip() == key and v.startswith("/") and ".." not in v.split("/"):
                    return v
    except OSError:
        pass
    return default


def verify_sig(data, sig_b64, keys=None):
    """ed25519 over the manifest's exact bytes: the system's cryptography
    (under -I only root-owned dist-packages are importable), else openssl."""
    try:
        sig = base64.b64decode(str(sig_b64).strip(), validate=True)
    except ValueError:
        return False
    for key in keys or PUBKEYS:
        raw = base64.b64decode(key)
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:
            if _openssl_verify(raw, data, sig):
                return True
            continue
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, data)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def _openssl_verify(raw, data, sig):
    tool = _which("openssl")
    der = bytes.fromhex("302a300506032b6570032100") + raw
    pem = b"-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der) + b"\n-----END PUBLIC KEY-----\n"
    if not tool:
        return False
    with tempfile.TemporaryDirectory() as d:
        paths = [os.path.join(d, n) for n in ("k.pem", "m", "s")]
        for path, body in zip(paths, (pem, data, sig)):
            with open(path, "wb") as f:
                f.write(body)
        return _run([tool, "pkeyutl", "-verify", "-pubin", "-inkey", paths[0], "-rawin",
                     "-in", paths[1], "-sigfile", paths[2]]).returncode == 0


def load(tree, pins=None):
    """Every source the list names, each read once: (items, {rel: bytes}).
    With `pins` (the signed root table) each must hash to its pin; a tree
    run by hand is taken as it is, CRLF made LF as upgrade_steps.sh did."""
    def one(rel):
        if rel.startswith("/") or ".." in rel.split("/") or "\\" in rel:
            raise Refused(f"{rel!r} is not a path inside the release")
        if pins is not None and not re.fullmatch(r"[0-9a-f]{64}", str(pins.get(rel, ""))):
            raise Refused(f"{rel} is not pinned by the signed manifest -- nothing installed")
        try:
            # No symlink, and never block on a FIFO planted in the tree:
            # only a regular file is read at all.
            fd = os.open(os.path.join(tree, *rel.split("/")), os.O_RDONLY | os.O_NONBLOCK
                         | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise Refused(f"{rel} is not a regular file")
                data = f.read(MAX_FILE + 1)
        except OSError as exc:
            raise Refused(f"{rel}: {exc.strerror or exc}")
        if len(data) > MAX_FILE:
            raise Refused(f"{rel} is larger than {MAX_FILE} bytes")
        if pins is None:
            return data.replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != pins[rel]:
            raise Refused(f"{rel} does not match the signed manifest -- nothing installed")
        return data
    files = {ITEMS: one(ITEMS)}
    items = json.loads(files[ITEMS])
    for entry in items.get("helpers", []) + items.get("nft", []):
        files[entry["src"]] = one(entry["src"])
    up = items.get("upgrade") or {}
    for key in UPGRADE_FILES:
        if up.get(key):
            files[up[key]] = one(up[key])
    return items, files


def _same(path, data):
    try:
        with open(path, "rb") as f:
            return f.read() == data
    except OSError:
        return False


def _put(dest, data, mode, check=None):
    """`data` to `dest` atomically, root-owned; `check(tmp)` may veto it.
    The temp name starts with a dot, which sudo skips in sudoers.d."""
    os.makedirs(os.path.dirname(dest), mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".irivai-", dir=os.path.dirname(dest))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        if check is not None and not check(tmp):
            raise Refused(f"{dest}: the new text did not validate -- left as it was")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def service_user():
    """The hub unit's User= (root-owned); "" means no sudoers rule is written."""
    try:
        with open(HUB_UNIT, encoding="utf-8") as f:
            m = re.search(r"^User=(\S+)\s*$", f.read(), re.M)
    except OSError:
        m = None
    return m.group(1) if m and USER_RE.match(m.group(1)) else ""


def _compiles(path):
    """A helper that does not even parse never replaces one that works."""
    try:
        with open(path, "rb") as f:
            compile(f.read(), path, "exec")
        return True
    except (SyntaxError, ValueError, OSError):
        return False


def _self_ok(path):
    """The new root step, as written, must pass its own --selftest first."""
    if not _compiles(path):
        return False
    try:
        return subprocess.run([sys.executable, "-I", path, "--selftest"], capture_output=True,
                              timeout=60, env={"PATH": SAFE_PATH}).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _helper_check(dest):
    return _self_ok if dest == SELF else (_compiles if dest.endswith(".py") else None)


def _sudo_regex_ok():
    """sudo 1.9.10+ matches ^...$ arguments as a regex; an older one would
    take them literally (and refuse every call), so such a rule is left out."""
    tool = _which("sudo")
    m = re.search(r"version (\d+)\.(\d+)\.(\d+)", _run([tool, "-V"]).stdout if tool else "")
    return not m or tuple(int(x) for x in m.groups()) >= (1, 9, 10)


def _visudo_ok(path):
    tool = _which("visudo")
    return bool(tool) and _run([tool, "-cf", path]).returncode == 0


def apply(items, files, dry=False):
    """Helpers, then sudoers (each only once its helper is there), then
    nftables; each only where the box differs, so it is safe to re-run."""
    for h in items.get("helpers", []):
        if not FILE_RE.match(h["dest"]):
            raise Refused(f"bad helper name {h['dest']!r}")
        dest = os.path.join(LIB, h["dest"])
        if _same(dest, files[h["src"]]):
            say("skipped", f"helper {dest}: already current")
            continue
        if not dry:
            try:
                _put(dest, files[h["src"]], 0o755, check=_helper_check(h["dest"]))
            except Refused as exc:
                say("refused", str(exc))
                continue
        say("done", f"helper {dest}")
    user, regex_ok = service_user(), None
    for s in items.get("sudoers", []):
        name, rule = s["name"], s["rule"]
        if not NAME_RE.match(name) or "\n" in rule or not rule.startswith("{user} ALL=(root) NOPASSWD: "):
            raise Refused(f"bad sudoers entry {name!r}")
        text = (rule.replace("{user}", user) + "\n").encode()
        missing = [r for r in s.get("requires", []) if not os.path.isfile(os.path.join(LIB, r))]
        if " ^" in rule and regex_ok is None:
            regex_ok = _sudo_regex_ok()
        if not user:
            say("skipped", f"sudoers {name}: no User= in {HUB_UNIT}")
        elif " ^" in rule and not regex_ok:
            say("skipped", f"sudoers {name}: this sudo is older than 1.9.10 (no regex rules), rule left as it was")
        elif missing and not dry:
            say("skipped", f"sudoers {name}: {', '.join(missing)} is not installed, rule left as it was")
        elif _same(os.path.join(SUDOERS_DIR, name), text):
            say("skipped", f"sudoers {name}: already current")
        else:
            try:
                if not dry:
                    _put(os.path.join(SUDOERS_DIR, name), text, 0o440, check=_visudo_ok)
                say("done", f"sudoers {name}")
            except Refused as exc:
                say("refused", str(exc))
    nft = _which("nft")
    for n in items.get("nft", []):
        unit, table = n["unit"], n["table"]
        if not (NAME_RE.match(unit) and FILE_RE.match(n["dest"]) and re.fullmatch(r"[a-z_]{1,40}", table)):
            raise Refused(f"bad nftables entry {unit!r}")
        if n.get("optout") and os.path.exists(n["optout"]):
            say("skipped", f"{unit}: not wanted on this box ({n['optout']})")
            continue
        if not nft:
            say("skipped", f"{unit}: no nft here (package nftables; scripts/upgrade_steps.sh installs it)")
            continue
        changed = False
        for path, data in ((os.path.join(LIB, n["dest"]), files[n["src"]]),
                           (os.path.join(UNIT_DIR, unit + ".service"),
                            ("\n".join(n["unit_text"]) + "\n").encode())):
            if not _same(path, data):
                changed = True
                if not dry:
                    _put(path, data, 0o644)
        loaded = _run([nft, "list", "table", "inet", table]).returncode == 0
        if not changed and loaded:
            say("skipped", f"{unit}: already loaded")
        elif dry:
            say("done", f"{unit}: would be (re)loaded")
        else:
            for verb in (["daemon-reload"], ["enable", unit + ".service"], ["restart", unit + ".service"]):
                _run(["systemctl", *verb], timeout=120)
            loaded = _run([nft, "list", "table", "inet", table]).returncode == 0
            say("done" if loaded else "refused", f"{unit}: {'loaded' if loaded else 'did not load'}")
        marker = str(n.get("marker", ""))
        if loaded and not dry and marker.startswith(ETC + "/") and ".." not in marker.split("/") \
                and not _same(marker, str(n.get("marker_text", "")).encode()):
            _put(marker, str(n.get("marker_text", "")).encode(), 0o644)


def _upgrade_mark():
    return os.path.join(os.path.dirname(STATE), "upgrade-steps.json")


def _apt(argv, deadline, need=5):
    """One apt-get call inside what is left of APT_BUDGET_S, not started with
    less than `need` seconds left; never raises."""
    left = deadline - time.monotonic()
    if left < need:
        return False
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=left,
                              env={"PATH": SAFE_PATH, "LC_ALL": "C",
                                   "DEBIAN_FRONTEND": "noninteractive"}).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _up_packages(up, files, dry):
    """upgrade_steps.sh step 4 (and what a later release added): FIRST, so
    the nftables units below find nft. Offline is a retry, not a failure."""
    pkgs = [str(p) for p in up.get("packages", [])]
    if not all(PKG_RE.match(p) for p in pkgs):
        raise Refused("bad package name in the upgrade list")
    query, apt = _which("dpkg-query"), _which("apt-get")
    if not (query and apt):
        say("skipped", "packages: not an apt host")
        return True
    missing = [p for p in pkgs if _run([query, "-W", "-f=${Status}", p]).stdout.strip()
               != "install ok installed"]
    if not missing:
        say("skipped", f"packages {', '.join(pkgs)}: already installed")
        return True
    if dry:
        say("done", f"packages {', '.join(missing)}: would be installed")
        return True
    # A dpkg left half-done (by anything, not only us) fails every install
    # and is not ours to repair unattended: said, and left for the next update.
    audit = _run([_which("dpkg") or "dpkg", "--audit"], timeout=30)
    if audit.returncode != 0 or audit.stdout.strip():
        say("skipped", "packages: dpkg reports an unfinished install (dpkg --audit); not "
                       "starting apt -- run: sudo dpkg --configure -a; the next update tries again")
        return False
    deadline, refreshed, left = time.monotonic() + APT_BUDGET_S, False, []
    install = [apt, "-y", "-q", "--no-install-recommends", "-o", "Dpkg::Options::=--force-confdef",
               "-o", "Dpkg::Options::=--force-confold", "install"]
    for p in missing:
        if deadline - time.monotonic() < APT_INSTALL_MIN_S:
            left.append(p)
            say("skipped", f"package {p}: not started, too little time left in this update "
                           "-- the next update tries again")
            continue
        ok = _apt(install + [p], deadline, APT_INSTALL_MIN_S)
        if not ok and not refreshed:        # a stale list names a version gone from the mirror
            refreshed = True
            _apt([apt, "-q", "update"], deadline)
            ok = _apt(install + [p], deadline, APT_INSTALL_MIN_S)
        if ok:
            say("done", f"package {p}: installed")
        else:
            left.append(p)
            say("skipped", f"package {p}: could not install (offline?) -- the next update tries again")
    return not left


def _user_uid(name):
    """The uid of account `name`, None when there is no such account."""
    try:
        import pwd
        return pwd.getpwnam(name).pw_uid
    except (ImportError, KeyError):
        return None


def _hub_port_saved():
    """The hub has the port as its setting ([hub].port in the live config),
    which it writes from a baked --port at its first start under systemd.
    Read only to decide; a value that cannot be read is "not saved". Opened
    as load() opens: no symlink, no FIFO or device, and owned by the hub's
    User= (or root), since root reads a path the service account can write."""
    path = os.path.join(_conf("LIVE_LINK", LIVE), "config", "systems.local.toml")
    user = service_user()
    owners = {0, _user_uid(user) if user else 0}
    try:
        import tomllib
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid not in owners:
                return False
            hub = tomllib.loads(f.read(MAX_FILE).decode("utf-8")).get("hub") or {}
        return isinstance(hub, dict) and isinstance(hub.get("port"), int)
    except (OSError, ValueError, ImportError):
        return False


def _up_hub_port(up, files, dry):
    """upgrade_steps.sh step 1. Under systemd the setting already wins, so
    this is tidy; done only once the setting holds the port, or dropping it
    would move the hub to the default. No restart: the update restarts it."""
    try:
        with open(HUB_UNIT, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        say("skipped", f"hub unit: {HUB_UNIT} not found")
        return True
    if not re.search(r" --port [0-9]+", text):
        say("skipped", "hub unit: no baked --port")
        return True
    if not _hub_port_saved():
        say("skipped", "hub unit: keeps its baked --port until the hub has saved the port as its "
                       "setting (its next start does); the next update drops it")
        return False
    if not dry:
        _put(HUB_UNIT, re.sub(r" --port [0-9]+", "", text).encode(), 0o644)
        _run(["systemctl", "daemon-reload"], timeout=120)
    say("done", "hub unit: baked --port removed (the port is the setting)")
    return True


def _up_linklocal(up, files, dry):
    """upgrade_steps.sh step 2: the drop-in, on NetworkManager 1.52+ only."""
    src = up.get("linklocal")
    nmcli = _which("nmcli")
    if not src:
        return True
    if not nmcli:
        say("skipped", "link-local: no NetworkManager")
        return True
    ver = (_run([nmcli, "-v"]).stdout.split() or ["0"])[-1]
    if semver(ver) < (1, 52, 0):
        say("skipped", f"link-local: NetworkManager {ver} is older than 1.52")
        return True
    dest = os.path.join(NM_CONF_DIR, LINKLOCAL)
    if _same(dest, files[src]):
        say("skipped", "link-local: drop-in already current")
        return True
    if not dry:
        _put(dest, files[src], 0o644)
        _run([nmcli, "general", "reload", "conf"])
    say("done", f"link-local: {dest}")
    return True


def _up_apply_unit(up, files, dry):
    """upgrade_steps.sh step 11. The update calling this IS that unit, so
    the file is written and nothing reloaded: it counts from the next
    daemon-reload or boot (core/services/update_revive.py covers the gap)."""
    path = os.path.join(UNIT_DIR, APPLY_UNIT)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        say("skipped", f"apply unit: {path} not found (no OTA layout)")
        return True
    if re.search(r"^KillMode=process$", text, re.M):
        say("skipped", "apply unit: KillMode=process already set")
        return True
    if re.search(r"^KillMode=", text, re.M):
        new = re.sub(r"^KillMode=.*$", "KillMode=process", text, flags=re.M)
    else:
        new = re.sub(r"^Type=oneshot$", "Type=oneshot\nKillMode=process", text, count=1, flags=re.M)
    if new == text:
        say("skipped", "apply unit: no Type=oneshot line to set KillMode beside; left as it was")
        return True
    if not dry:
        _put(path, new.encode(), 0o644)
    say("done", "apply unit: KillMode=process (from the next daemon-reload or boot)")
    return True


def _mtx_answers():
    """The gateway's control API answers on loopback (stdlib, short)."""
    import urllib.request
    try:
        with urllib.request.urlopen(MTX_API, timeout=1.5) as r:
            return r.status == 200
    except Exception:                                            # noqa: BLE001
        return False


def _mtx_active():
    return _run(["systemctl", "is-active", MTX_UNIT]).stdout.strip() == "active"


def _mtx_restart():
    """Restart the gateway; True once it is active AND answering, within
    MTX_WAIT_S. Live WebRTC drops for those seconds (the apps re-publish
    5 s after it is back, passthrough pulls are re-added by the app's tick,
    pages fall back to MJPEG and rejoin) -- and this runs before the update
    restarts the platform, which drops every view anyway."""
    _run(["systemctl", "restart", MTX_UNIT], timeout=60)
    deadline = time.monotonic() + MTX_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _mtx_answers() and _mtx_active():
            return True
    return False


def _up_mediamtx(up, files, dry):
    """upgrade_steps.sh step 3, unattended (Pipi H1): /etc/mediamtx/mediamtx.yml
    from the pinned release copy -- every listener but the product's off, every
    action loopback-only. Root-owned, outside the tree, so an update never
    reached it before. Only where it differs; the box's own copy kept once
    beside it (.pre-<rev>). A gateway that does not come back on the new text
    gets its old text back and is restarted again: a box with live video on
    the old config beats a box with none on the new one. Said, retried by the
    next update, never fails it."""
    src = up.get("mediamtx")
    if not src:
        return True
    if not os.path.isdir(MTX_DIR) or os.path.islink(MTX_DIR):
        say("skipped", f"video gateway: not installed here ({MTX_DIR} missing)")
        return True
    conf, data = os.path.join(MTX_DIR, MTX_CONF), files[src]
    if _same(conf, data):
        say("skipped", "video gateway: config already current")
        return True
    if dry:
        say("done", f"video gateway: {conf} would be replaced and the gateway restarted")
        return True
    try:
        fd = os.open(conf, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            old = f.read(MAX_FILE) if stat.S_ISREG(os.fstat(f.fileno()).st_mode) else None
    except OSError:
        old = None
    backup = f"{conf}.pre-{re.sub(r'[^0-9A-Za-z.]', '', str(up.get('rev')))}"
    if old is not None and not os.path.lexists(backup):
        _put(backup, old, 0o644)
    was_up = _mtx_active()
    _put(conf, data, 0o644)
    if not was_up:
        say("done", f"video gateway: {conf} (the gateway is not running; it reads it when it starts)")
        return True
    if _mtx_restart():
        say("done", f"video gateway: {conf}, restarted (loopback-only, MoQ and playback off)")
        return True
    back = False
    if old is not None:
        _put(conf, old, 0o644)
        back = _mtx_restart()
    say("refused", f"video gateway: not answering {MTX_WAIT_S}s after a restart on the new config; "
                   + ("the previous config is back and running" if back else
                      "the previous config is back but the gateway is NOT running"
                      if old is not None else "there was no previous config to restore")
                   + " -- the next update tries again")
    return False


def upgrade(items, files, dry=False):
    """The one-time steps (module docstring), once per box per rev. Never
    raises: a step that breaks is said and the update goes on."""
    up = items.get("upgrade")
    if not isinstance(up, dict) or not up.get("rev"):
        return
    rev = str(up["rev"])
    try:
        with open(_upgrade_mark(), encoding="utf-8") as f:
            had = str(json.load(f).get("rev") or "")
    except (OSError, ValueError, AttributeError):
        had = ""
    if had and semver(had) >= semver(rev):
        say("skipped", f"one-time upgrade steps: done on this box ({had})")
        return
    settled = True
    for step in (_up_packages, _up_hub_port, _up_linklocal, _up_apply_unit, _up_mediamtx):
        try:
            settled = step(up, files, dry) and settled
        except (Refused, OSError, ValueError, KeyError, TypeError) as exc:
            say("refused", f"one-time upgrade step {step.__name__[4:]}: {exc}")
            settled = False
    if dry:
        return
    if settled:
        try:
            _put(_upgrade_mark(), json.dumps({"rev": rev}).encode(), 0o644)
            say("done", f"one-time upgrade steps: recorded ({rev})")
        except OSError as exc:
            say("refused", f"one-time upgrade steps: could not record them: {exc}")
    else:
        say("skipped", "one-time upgrade steps: not all finished; the next update tries again")


def _state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _last_version():
    return str(_state().get("version") or "")


def _record(version, app=""):
    """The newest version applied, and the release app this box takes root
    files from (the first verified run decides it; never changed after)."""
    d = _state()
    if semver(version) >= semver(str(d.get("version") or "")):
        d["version"] = version
    d["app"] = d.get("app") or app
    _put(STATE, json.dumps({k: v for k, v in d.items() if v}).encode(), 0o644)


def verified(raw, reexec=False):
    """The OTA path: request, release dir, signature, pins, then the list."""
    try:
        req = json.loads(raw)
        release, man, sig = str(req["release"]), base64.b64decode(req["manifest"]), str(req["sig"])
    except (ValueError, KeyError, TypeError) as exc:
        say("refused", f"bad request: {exc}")
        return 2
    releases = _conf("RELEASES_DIR", "/opt/irivai-releases")
    tree = os.path.realpath(os.path.join(releases, release))
    if not RELEASE_RE.match(release) or os.path.dirname(tree) != os.path.realpath(releases) \
            or not os.path.isdir(tree):
        say("refused", f"{release!r} is not a release in {releases}")
        return 2
    if not verify_sig(man, sig):
        say("refused", "the manifest's signature does not verify -- nothing installed")
        return 1
    try:
        m = json.loads(man)
    except ValueError:
        m = {}
    if m.get("app") not in APPS or not isinstance(m.get("root"), dict):
        say("skipped", "this release's manifest pins no root files; nothing to install")
        return 0
    pinned = str(_state().get("app") or "")
    if pinned and m["app"] != pinned:
        say("refused", f"this box takes root files from {pinned} releases, not {m['app']}")
        return 1
    version, last = str(m.get("version") or ""), _last_version()
    if semver(version) < semver(last):
        say("skipped", f"{version} is older than {last}, whose root files this box keeps")
        return 0
    try:
        items, files = load(tree, m["root"])
        mine = next((files[h["src"]] for h in items.get("helpers", []) if h.get("dest") == SELF), None)
        if mine is not None and not reexec and not _same(os.path.join(LIB, SELF), mine):
            # This copy first, then hand over: the list may need what only the
            # NEW copy knows. It checks everything again by itself.
            _put(os.path.join(LIB, SELF), mine, 0o755, check=_self_ok)
            say("done", f"helper {os.path.join(LIB, SELF)} (the new copy finishes the job)")
            r = subprocess.run([sys.executable, "-I", os.path.join(LIB, SELF), "--reexec"],
                               input=raw, capture_output=True, timeout=600)
            sys.stdout.write(r.stdout.decode("utf-8", "replace"))
            return r.returncode
        upgrade(items, files)
        apply(items, files)
    except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        say("refused", str(exc))
        return 1
    # Signature and pins held and the helpers are written: that is what the
    # downgrade guard protects. A refused rule or unit is retried by the next
    # update of the same or a newer version all the same.
    _record(version, m["app"])
    return 1 if refused() else 0


def tree_mode(tree, dry=False):
    try:
        items, files = load(tree)
        upgrade(items, files, dry=dry)
        apply(items, files, dry=dry)
        if not dry:                     # as verified(): the helpers are written
            with open(os.path.join(tree, "VERSION"), encoding="utf-8") as f:
                _record(f.read().strip())
    except (Refused, OSError, ValueError, KeyError, TypeError) as exc:
        say("refused", str(exc))
    return 1 if refused() else 0


def selftest():
    """This copy can run at all: its key decodes, and a signature check goes
    through to a real verifier and says no to a wrong signature."""
    try:
        keys_ok = bool(PUBKEYS) and all(len(base64.b64decode(k)) == 32 for k in PUBKEYS)
        try:
            import cryptography                                  # noqa: F401
            verifier = True
        except ImportError:
            verifier = bool(_which("openssl"))
        ok = keys_ok and verifier and semver("v1.2.3") == (1, 2, 3) \
            and not verify_sig(b"selftest", base64.b64encode(bytes(64)).decode())
    except Exception:                                            # noqa: BLE001
        ok = False
    print("selftest: " + ("ok" if ok else "FAILED"))
    return 0 if ok else 1


def _lock():
    """The lock file, never through a symlink; waits LOCK_WAIT_S at most."""
    import fcntl
    import time
    fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() > deadline:
                os.close(fd)
                return None
            time.sleep(1)


def main(argv):
    if argv[1:] == ["--selftest"]:
        return selftest()
    if not _is_root():
        print("root-steps: run as root", file=sys.stderr)
        return 2
    if argv[1:] == ["--reexec"]:
        return verified(sys.stdin.buffer.read(1 << 20), reexec=True)   # the parent holds the lock
    fd = _lock()
    if fd is None:
        say("refused", f"another root step holds {LOCK}; nothing done")
        return 1
    try:
        if len(argv) >= 3 and argv[1] == "--tree":
            return tree_mode(os.path.realpath(argv[2]), "--dry-run" in argv[3:])
        return verified(sys.stdin.buffer.read(1 << 20))
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
