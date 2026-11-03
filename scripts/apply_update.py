#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_update.py -- installs a platform release on an appliance.

This is the only code that touches the live tree, and it runs where nothing
else can reach it: as its own systemd unit (irivai-apply.service), started by
the hub through one narrow sudo rule, from a COPY of itself on the store
volume. Every part of that is deliberate.

    Its own unit, because the hub spawns its masters as children and they all
    share the hub's cgroup. An updater started by the hub would be killed the
    instant it restarted irivai-hub -- mid-swap, with nothing left alive to
    finish or to roll back.

    A copy outside the tree, because the tree is what is being replaced.

    Stdlib only, no `core.` imports, because the interpreter it runs under
    must not depend on either release. It reads its job from job.json and
    reports through status.json, both on the store volume.

Blue/green, not "copy over the old files"
-----------------------------------------
A release is a directory of its own under /opt/irivai-releases, with its own
virtualenv, built completely before anything live changes. Going live is one
atomic rename of the /opt/irivai-systor symlink. Rolling back is the same
rename the other way. There is no window in which the running tree is half
one version and half another -- power loss before the rename leaves the old
release untouched, and power loss after it leaves the new one complete.
Anything that dies in between is repaired at boot by recover.py.

The order of the phases is the safety argument, so it is worth stating:
everything that can fail cheaply (space, driver, signature, hash, dependency
install) happens BEFORE the first byte of live state changes.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# The same redirect rule core/services/ghnet.py enforces, restated here
# because this script imports nothing from the product. A private release
# asset 302s to signed storage; carrying our Bearer header across that hop
# leaks the token and makes the storage host reject the request outright.
# --------------------------------------------------------------------------
GITHUB_HOSTS = ("github.com", "api.github.com", "raw.githubusercontent.com",
                "uploads.github.com", "objects.githubusercontent.com")


class _NoAuthAcrossHosts(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        import urllib.parse
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        host = urllib.parse.urlparse(newurl).hostname or ""
        if host not in GITHUB_HOSTS:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_NoAuthAcrossHosts)

CHUNK = 1 << 16
GATE_A_S = 120          # hub must be serving the new version within this
GATE_A_FIRST_S = 60     # ...and must answer at all within this
GATE_B_S = 300          # systems get longer: Hailo and GStreamer are slow
DOWNLOAD_TRIES = 3

# Files and directories a release must never carry into a box, or must never
# take from one. Mutable state lives outside the release dirs and is linked
# in by install.sh; these are the belt to that brace.
NEVER_SHIP = ("config/deploy.toml", "config/store_signing.pem")


class Fail(RuntimeError):
    """An abort with an operator-readable reason."""


# ---------------------------------------------------------------------------
# Job + status
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, work):
        self.work = work
        with open(os.path.join(work, "job.json"), encoding="utf-8") as f:
            self.d = json.load(f)
        self.mode = self.d.get("mode") or "apply"
        self.version = str(self.d.get("version") or "").lstrip("vV")
        self.current = str(self.d.get("current") or "0.0.0")
        self.releases = self.d.get("releases_dir") or "/opt/irivai-releases"
        self.link = self.d.get("live_link") or "/opt/irivai-systor"
        self.config_dir = self.d.get("config_dir") or ""
        self.store_dir = self.d.get("store_dir") or ""
        self.port = int(self.d.get("hub_port") or 5100)
        self.assets = self.d.get("assets") or {}
        # Which channel this job came from, and which key in
        # update.local.toml holds its token. Defaulted for a job.json written
        # by an older release: the applier is copied out of the RUNNING tree,
        # so a new applier can meet an old job file exactly once.
        self.channel = str(self.d.get("channel") or "stable")
        self.token_key = str(self.d.get("token_key") or "token")
        self.phase = "starting"
        self.started = int(time.time())
        self._boot = boot_id()
        # The release to go back to, recorded in every status line. The
        # boot-time repair script reads exactly this rather than guessing:
        # "the newest other release" is the release being INSTALLED during an
        # apply, and the one being escaped during a rollback -- so a guess
        # walks an interrupted box forward onto untested code, or undoes the
        # very rollback someone asked for.
        self.previous = ""
        self.healed = False              # heal_route() runs once per job

    # -- status.json is the only way anyone learns what is happening. Written
    # atomically every time, because the hub, the UI and the boot-time
    # recovery script all read it while it is being written.
    def say(self, phase, percent, detail="", error=""):
        self.phase = phase
        path = os.path.join(self.work, "status.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"phase": phase, "percent": int(percent),
                       "detail": detail, "error": error,
                       "version": self.version, "mode": self.mode,
                       "previous": self.previous,
                       "started": self.started, "pid": os.getpid(),
                       "boot_id": self._boot}, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        log(f"[{phase}] {detail or ''}{(' -- ' + error) if error else ''}")


def log(msg):
    sys.stdout.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    sys.stdout.flush()


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_token(config_dir, key="token"):
    """The release token, straight from config/update.local.toml.

    Read here rather than passed in: an argument is visible in `ps` to every
    account on the box, and an environment variable is visible in
    /proc/<pid>/environ. Parsed by hand -- one scalar out of a file this
    script must not need a TOML library for.

    `key` names which channel's token to read (the job says which). The two
    live in the same file under different names, and reading the wrong one is
    not a 401 to shrug at: it would spend the customer token against the
    developer repo, or the reverse.
    """
    path = os.path.join(config_dir, "update.local.toml")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, _, value = line.partition("=")
                # An exact key, not a prefix: `token_expires_at` sitting above
                # `token` in the same file would otherwise be read as the
                # token and every download would 401. Same reason `dev_token`
                # must not answer to a request for `token`.
                if name.strip() == key:
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Signature. `cryptography` comes from the RUNNING release's venv, which is
# why the unit runs this script with that interpreter.
# ---------------------------------------------------------------------------

def verify(manifest_bytes, sig_b64, pubkey_b64, expect_app):
    import base64
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        raise Fail("this box cannot verify updates -- the 'cryptography' "
                   "package is missing")
    if not pubkey_b64:
        raise Fail("no update signing key is configured on this box")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64))
        key.verify(base64.b64decode(sig_b64.strip()), manifest_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise Fail(f"the update's signature does not verify: {exc}")
    # Parsed only AFTER the signature passes: JSON parsing is the first thing
    # an attacker gets to influence otherwise.
    try:
        m = json.loads(manifest_bytes)
    except ValueError:
        raise Fail("the update manifest is not valid JSON")
    if m.get("app") != expect_app:
        raise Fail("that is not a platform release manifest")
    return m


def semver(v):
    out = []
    for piece in str(v or "").lstrip("vV").split(".")[:3]:
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

def _open(url, tok, accept=None, headers=None):
    head = {"User-Agent": "irivai-update"}
    if accept:
        head["Accept"] = accept
    if tok:
        head["Authorization"] = "Bearer " + tok
    for k, v in (headers or {}).items():
        head[k] = v
    return _opener.open(urllib.request.Request(url, headers=head), timeout=60)


def fetch_small(url, tok, limit=1 << 20):
    with _open(url, tok, accept="application/octet-stream") as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise Fail("an update metadata file was larger than expected")
    return data


# ---------------------------------------------------------------------------
# A dead default route, fixed once before a download is called failed
# ---------------------------------------------------------------------------
# Board .22: a Modbus port's gateway that nothing answers at outranked the
# real uplink, and every GitHub request died with "[Errno 113] No route to
# host". The hub's core/services/routeheal.py moves such a route when another
# port's gateway answers; this runs it ONCE per job, from the live tree, AS
# THE ACCOUNT THAT OWNS THE CONFIG -- the same sudo grant the hub has, and an
# Inbox notice written by root would leave the hub unable to write its own
# inbox file. Stdlib only here, as everywhere in this script: it is a
# subprocess, and anything going wrong with it means "not healed".

#: The errnos update.reach_error calls "no internet" (kept in step by hand:
#: this script imports nothing from the product).
OFFLINE_ERRNOS = frozenset({101, 110, 111, 113, -2, -3, -5})


def offline(exc):
    """Did `exc` fail to reach GitHub at all (as against GitHub answering)?"""
    import socket
    if isinstance(exc, urllib.error.HTTPError):
        return False
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return True
    num = getattr(cause, "errno", None)
    if num is None:
        m = re.search(r"\[Errno (-?\d+)\]", str(cause))
        num = int(m.group(1)) if m else None
    return num in OFFLINE_ERRNOS or "timed out" in str(cause).lower()


def heal_route(job):
    """Run routeheal once for this job. True if it moved the default route."""
    if getattr(job, "healed", False):
        return False
    job.healed = True
    live = os.path.realpath(job.link)
    py = os.path.join(live, ".venv", "bin", "python")
    if not os.path.isfile(os.path.join(live, "core", "services", "routeheal.py")) \
            or not os.path.exists(py):
        return False                     # a release from before routeheal
    # -m puts the working directory (the live tree) first on sys.path; -s
    # keeps a user site-packages out of a root-started process.
    argv = [py, "-s", "-m", "core.services.routeheal", job.config_dir]
    # The apply unit runs as the service user (install.sh User=), which is
    # the config's owner: run the venv python directly. Only a root caller
    # (a manual `sudo apply_update.py`) drops to the owner, because runuser
    # refuses non-root callers ("may not be used by non-root users", rc 1)
    # and that refusal used to be swallowed below as "nothing healed".
    # A root caller whose config is root-owned, or whose owner has no name,
    # does NOT run the heal at all (Pipi, 1.0.15 gate L1): that would run a
    # service-writable tree as root. Nothing healed is the safe answer.
    try:
        import pwd
        owner = pwd.getpwuid(os.stat(job.config_dir or live).st_uid).pw_name
    except (ImportError, AttributeError, KeyError, OSError):
        owner = ""
    if getattr(os, "geteuid", lambda: -1)() == 0:
        if not owner or owner == "root":
            return False
        argv = ["runuser", "-u", owner, "--"] + argv
    try:
        r = subprocess.run(argv, cwd=live, capture_output=True, text=True,
                           timeout=120)
        answer = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return False
    if answer.get("changed"):
        log(f"[route] {answer.get('detail') or 'default route moved'}")
    return bool(answer.get("changed"))


def fetch_healing(job, fn, *args):
    """fn(*args), once more if it could not reach GitHub and heal_route()
    moved the default route."""
    try:
        return fn(*args)
    except (urllib.error.URLError, OSError) as exc:
        if not offline(exc) or not heal_route(job):
            raise
    return fn(*args)


def fetch_bundle(url, tok, dest, sha, size, say, heal=None):
    """Stream the bundle, resuming a partial file rather than restarting it.

    These boxes sit on whatever uplink the site has. A 150 MB download that
    starts from zero every time the link hiccups is a download that never
    finishes, so an interrupted attempt keeps its bytes and asks for the rest
    with a Range header. The hash is computed over the finished file, not
    while streaming: a resumed file has no single stream to hash.
    """
    part = dest + ".part"
    last_err = ""
    said = None
    tries, attempt = DOWNLOAD_TRIES, 0
    while attempt < tries:
        attempt += 1
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size and have > size:                    # a stale, wrong partial
            os.remove(part)
            have = 0
        if size and have == size:
            break
        try:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with _open(url, tok, accept="application/octet-stream",
                       headers=headers) as r:
                # A server that ignores Range answers 200 with the whole file;
                # appending then would corrupt it silently.
                mode = "ab" if (have and r.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if size:
                            # Only when the line would actually CHANGE. say()
                            # rewrites and fsyncs status.json and appends a log
                            # line every time it is called; called per 64 KB
                            # chunk it wrote the same "N of M MB" hundreds of
                            # times over on the first real update -- log noise,
                            # and thousands of needless fsyncs against the very
                            # flash the download is racing.
                            line = (5 + int(35 * have / size),
                                    f"{have >> 20} of {size >> 20} MB")
                            if line != said:
                                said = line
                                say("download", line[0], line[1])
            break
        except (urllib.error.URLError, OSError) as exc:
            last_err = str(exc)
            # `heal`: a dead default route moved to a live one is worth one
            # more attempt than the budget, and no backoff before it.
            if heal is not None and offline(exc) and heal():
                say("download", 5, "moved the internet route to a working "
                                   "gateway - retrying")
                tries += 1
                continue
            if attempt == tries:
                raise Fail(f"the download failed after {attempt} attempts "
                           f"({last_err})")
            time.sleep(2 ** attempt)

    if size and os.path.getsize(part) != size:
        raise Fail("the downloaded update was the wrong size")
    digest = hashlib.sha256()
    with open(part, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != sha:
        os.remove(part)
        raise Fail("the update did not match its signed hash -- refusing it")
    os.replace(part, dest)
    return dest


# ---------------------------------------------------------------------------
# Unpacking
# ---------------------------------------------------------------------------

def unpack(tar_path, dest, max_bytes):
    """Extract with the stdlib's own hostile-archive filter, plus a ceiling.

    `filter="data"` refuses absolute paths, parent traversal, symlinks that
    escape, devices and setuid bits -- the whole family of tar tricks, kept
    correct by people who track them. The byte ceiling is the one thing it
    does not cover: a bundle whose signature we trust should still not be
    able to fill the disk if the build ever goes wrong.
    """
    os.makedirs(dest, exist_ok=True)
    total = 0
    with tarfile.open(tar_path, "r:gz") as tf:
        members = []
        for m in tf.getmembers():
            total += max(0, m.size)
            if total > max_bytes:
                raise Fail("the update expands to far more than it declares "
                           "-- refusing it")
            name = m.name.replace("\\", "/").lstrip("./")
            if name in NEVER_SHIP:
                # A release must not be able to hand a box a deploy.toml (it
                # decides customer vs dev mode) or a signing key.
                continue
            members.append(m)
        try:
            tf.extractall(dest, members=members, filter="data")
        except TypeError:                       # Python < 3.12
            for m in members:
                if m.issym() or m.islnk() or m.isdev():
                    raise Fail("the update contains link or device entries")
                if m.name.startswith("/") or ".." in m.name.split("/"):
                    raise Fail("the update contains an unsafe path")
            tf.extractall(dest)
    return dest


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run(cmd, timeout=1800, nice=True):
    """Run a command politely: this box is encoding video while we work, and
    an update that stalls the live feeds is an outage of its own."""
    if nice and shutil.which("nice"):
        cmd = ["nice", "-n", "10"] + (
            ["ionice", "-c2", "-n7"] if shutil.which("ionice") else []) + cmd
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def systemctl(*args, timeout=180):
    return run(["sudo", "-n", "/usr/bin/systemctl", *args], timeout=timeout,
               nice=False)


def free_bytes(path):
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    st = os.statvfs(path or "/")
    return st.f_bavail * st.f_frsize


def health(port, timeout=3):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read(1 << 16) or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def masters_config(job):
    """slug -> port for every configured master, read directly from
    config/systems.toml + config/systems.local.toml.

    Read this way -- not through core. -- because it runs BEFORE the flip,
    while the tree about to be replaced is still what would answer for it.
    """
    base = _toml(os.path.join(job.config_dir, "systems.toml")).get("masters") or {}
    local = _toml(os.path.join(job.config_dir, "systems.local.toml")).get("masters") or {}
    merged = {}
    for src in (base, local):
        for slug, row in (src or {}).items():
            merged.setdefault(slug, {}).update(row or {})
    out = {}
    for slug, row in merged.items():
        m = re.search(r":(\d+)(?:/|$)", str(row.get("url") or ""))
        if m:
            out[slug] = int(m.group(1))
    return out


def running_masters(job):
    """Configured masters answering right now -- what the update must bring
    back, on new code, once the flip is done.

    Restarting irivai-hub.service does not necessarily touch these: a board
    whose hub unit sets KillMode=process (some do, precisely so a dashboard
    restart never takes a running detection down with it) leaves every
    master it spawned alive and untouched through that restart. Left alone,
    the update would report success while every one of those kept serving
    the OLD code indefinitely -- the update never actually reaching the
    thing running the cameras.
    """
    up = {}
    for slug, port in masters_config(job).items():
        if health(port, timeout=1) is not None:
            up[slug] = port
    # Written down for the hub (core/services/update_revive.py): what
    # revive_masters() spawns below is in THIS unit's cgroup and dies when
    # the oneshot exits, so the new hub is what actually brings these back,
    # and this file is how it knows which ones. Best effort: a job that
    # cannot write it still updates, and the hub falls back to the logs.
    try:
        with open(os.path.join(job.work, "was_masters.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"started": job.started, "masters": up}, f)
    except (OSError, AttributeError, TypeError):
        pass
    return up


def revive_masters(job, target, entries):
    """Bring every master that was running before the swap back up, on the
    NEW release's own code, THROUGH THE SAME FUNCTIONS the Start/Restart
    button already uses (provision.spawn_master, detector_control.start) --
    never a reimplementation of them.

    Safe to import core. HERE, though nowhere earlier in this script: gate_a
    has already proved this release's hub serves, so trusting its core/
    package is no longer the leap it would have been before the flip.

    Stops each system's detection AND the master process itself before
    spawning the replacement. Both, because they are two different things and
    only stopping one of them is what went wrong the first time this ran on
    real hardware: dc.stop() ends the DETECTION APP, while the master serving
    the port is a separate process that KillMode=process deliberately kept
    alive through the hub restart. spawn_master then had nowhere to bind, and
    the box finished the update reporting success with a master still
    executing the previous release's master_server.py -- out of the old
    release directory, which the updater keeps for rollback, so it ran on
    indefinitely. provision.stop_master() is the same call Remove uses, and it
    signals only a master that is local and listening.
    """
    if not entries:
        return
    py = os.path.join(target, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = os.path.join(target, ".venv", "Scripts", "python.exe")
    payload = json.dumps([{"slug": s, "port": p} for s, p in entries.items()])
    script = (
        "import json, sys, pathlib, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from core import config\n"
        "from core.services import provision, detector_control as dc\n"
        "entries = json.loads(sys.argv[2])\n"
        "cfgdir = sys.argv[3] or None\n"
        "scfg = config.systems(cfgdir)\n"
        "sysdir = pathlib.Path(scfg.get('dir', 'systems'))\n"
        "if not sysdir.is_absolute(): sysdir = pathlib.Path(sys.argv[1]) / sysdir\n"
        "repo = sys.argv[1]\n"
        "for e in entries:\n"
        "    pkg = str(provision._pkg_root(str(sysdir), e['slug']))\n"
        "    try: dc.stop(pkg, e['slug'])\n"
        "    except Exception: pass\n"
        "    try: provision.stop_master('http://127.0.0.1:%d' % e['port'])\n"
        "    except Exception: pass\n"
        # The port has to be free before the replacement asks for it, and a
        # signalled process does not exit within the same instruction.
        "time.sleep(2)\n"
        "for e in entries:\n"
        "    provision.spawn_master(repo, e['slug'], e['port'])\n"
        "time.sleep(2)\n"
        "for e in entries:\n"
        "    pkg = str(provision._pkg_root(str(sysdir), e['slug']))\n"
        "    try: dc.start(pkg, e['slug'])\n"
        "    except Exception: pass\n"
    )
    r = run([py, "-c", script, target, payload, job.config_dir],
           timeout=120, nice=False)
    if r.returncode != 0:
        job.say("gate", 90, "some systems did not come back cleanly",
                (r.stderr or "").strip()[-300:])


def verify_masters(job, entries, budget=90):
    """Every master that was answering before the swap is answering again.

    Not a formality. The first real update on real hardware finished with a
    master still executing the OLD release's master_server.py, and every
    signal the updater had said success: the hub was healthy, gate A passed,
    nothing had raised. The one question nobody asked was whether the
    processes this function had just replaced were actually replaced.

    Raised as Fail rather than logged, and therefore rolled back. A master
    that answered before an update and does not answer after it is a system
    an operator will find dark -- and the whole reason the previous release
    is still on the disk is so that discovery is not theirs to make.

    Called only on the way FORWARD. The rollback paths revive these same
    masters and must not be handed an exception for their trouble: raising
    inside the handler that is putting the previous release back would strand
    the box mid-recovery, which is worse than anything this check guards
    against.
    """
    if not entries:
        return
    deadline = time.time() + budget
    missing = list(entries)
    while time.time() < deadline:
        missing = []
        for slug, port in entries.items():
            got = health(port, timeout=2)
            if got is None:
                missing.append(slug)
                continue
            # Answering is not enough, and this is the whole reason the check
            # exists. The failure it was written for -- an old master that
            # outlived stop_master, leaving spawn_master nothing to bind --
            # ends with something perfectly healthy answering that port. Only
            # the version tells the two apart.
            #
            # Tolerated when absent: the masters of any release older than the
            # one that added the field do not report it, so the first update
            # off such a release still has to trust the port. That is a
            # one-time blind spot, not a permanent one.
            running = str(got.get("version") or "")
            if running and running.lstrip("vV") != job.version:
                missing.append(f"{slug} (still on {running})")
        if not missing:
            job.say("gate", 96, f"{len(entries)} system(s) back on the new code")
            return
        job.say("gate", 95, f"waiting for {len(missing)} system(s) to answer")
        time.sleep(5)
    raise Fail("these systems were running before the update and did not come "
               "back on " + job.version + ": " + ", ".join(sorted(missing)))


def master_slugs():
    """Systems that have a systemd unit of their own (the built-ins an
    installer wrote an env file for). Enumerated from disk rather than by
    passing a glob to systemctl: `restart 'irivai-master@*'` only matches
    units systemd has already LOADED, so a stopped system silently misses
    the restart it needs."""
    try:
        # Only names the sudoers rule's regex grants (irivai-master@[a-z0-9_-]+).
        return sorted(n[:-4] for n in os.listdir("/etc/irivai")
                      if re.fullmatch(r"[a-z0-9_-]+\.env", n))
    except OSError:
        return []


def restart_stack(job):
    """Restart the hub, then every system unit.

    Restarting the hub takes its whole cgroup with it -- the masters it
    spawned and their detection apps included, since a child keeps its
    parent's cgroup no matter how it was detached -- ONLY when the hub's
    unit kills by cgroup (systemd's default). A board whose unit sets
    KillMode=process (deliberately, so a dashboard restart never takes a
    live detection down with it) leaves every hub-spawned master untouched
    right through this restart, still running the old code. That case is
    NOT handled here: see running_masters()/revive_masters(), called after
    this function's callers reach a confirmed-good gate_a, which bring
    those survivors back up on the new release explicitly rather than
    assuming this restart reached them.
    """
    r = systemctl("restart", "irivai-hub.service")
    if r.returncode != 0:
        raise Fail("could not restart the hub -- " + (r.stderr or "").strip()[:200])
    for slug in master_slugs():
        systemctl("restart", f"irivai-master@{slug}.service")


def gate_a(job):
    """The hub is serving, and it is serving the NEW version.

    The version comes from /api/health, which reports a constant captured when
    the process started (core/version.py). Reading the VERSION file instead
    would pass the moment the files landed -- before the restart that actually
    matters.
    """
    deadline = time.time() + GATE_A_S
    first = time.time() + GATE_A_FIRST_S
    seen = None
    while time.time() < deadline:
        h = health(job.port)
        if h:
            seen = h.get("version") or ""
            if seen == job.version:
                return True
        elif time.time() > first and seen is None:
            # Nothing at all has answered in a minute: waiting out the rest of
            # the budget only delays the rollback.
            break
        job.say("gate", 88, f"waiting for the hub ({seen or 'no answer yet'})")
        time.sleep(3)
    raise Fail(f"the new version did not come up (health reported "
               f"{seen or 'nothing'}, expected {job.version})")


#: A detection app rewrites its state file about once a second; the runtime
#: calls one older than this dead (systems/demo/app/runtime.py).
STALE_AFTER_S = 30


def _toml(path):
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError, ImportError):
        return {}


def system_packages(job):
    """Every system package on this box: the ones a release ships and the ones
    an operator made. Both run detection, so both have to come back."""
    roots = [os.path.join(os.path.realpath(job.link), "systems"),
             os.path.join(job.store_dir, "systems")]
    out = {}
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            pkg = os.path.join(root, name)
            if os.path.isdir(os.path.join(pkg, "app")):
                out.setdefault(name, pkg)
    return out


def _data_dir(pkg):
    """Where a system writes -- the same three rules the app itself follows
    (systems/demo/app/config.py). Worth duplicating exactly: guessing wrong
    means gate B reads an empty list, decides every system came back, and
    reports a successful update over cameras that see nothing.
    """
    cfg = _toml(os.path.join(pkg, "config.toml"))
    local = _toml(os.path.join(pkg, "config.local.toml"))
    app = dict(cfg.get("app") or {})
    app.update(local.get("app") or {})
    explicit = str(app.get("data_dir") or "").strip()
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(pkg, explicit)
    slug = str(app.get("slug") or os.path.basename(pkg))
    if os.path.ismount("/mnt/nvme"):
        return os.path.join("/mnt/nvme/cytron", slug, "data")
    return os.path.join(pkg, "data")


def running_systems(job, since=0):
    """Systems that are detecting right now.

    Liveness is the runtime's own definition: a live pid AND a heartbeat that
    is not stale. `since` additionally demands the heartbeat be newer than a
    moment we choose -- after a restart that is what separates "this system is
    detecting again" from "this is the state file it left behind before we
    restarted it", which a recycled pid could otherwise pass.
    """
    now = time.time()
    out = []
    for slug, pkg in system_packages(job).items():
        state = os.path.join(_data_dir(pkg), "state.json")
        try:
            with open(state, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            continue
        pid = int(s.get("pid") or 0)
        beat = float(s.get("heartbeat") or 0)
        if not pid or not s.get("running"):
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        if now - beat > STALE_AFTER_S or beat < since:
            continue
        out.append(slug)
    return sorted(out)


def gate_b(job, want, since):
    """Every system that was detecting before is detecting again.

    Gate A only proves the web process starts. The failures that matter on
    this box are underneath it -- a runtime that cannot open the Hailo device
    after a version bump leaves a perfectly healthy hub in front of cameras
    that see nothing, which is exactly the silence an appliance must never
    ship into.
    """
    if not want:
        return True
    deadline = time.time() + GATE_B_S
    missing = list(want)
    while time.time() < deadline:
        back = set(running_systems(job, since=since))
        missing = [s for s in want if s not in back]
        if not missing:
            return True
        job.say("gate", 95, f"waiting for {len(missing)} system(s) to detect")
        time.sleep(5)
    raise Fail("these systems did not resume detecting: " + ", ".join(missing))


#: The one step of an update that can need root, and where it lives. Outside
#: every release, because a tool that repairs the tree cannot live inside it --
#: which also means an update cannot replace it, the same as recover.py.
FLIP_HELPER = "/usr/local/lib/irivai/flip-link.py"


def flip(link, target):
    """Point the live path at `target`, atomically.

    os.replace on a symlink is a single rename: there is no instant in which
    /opt/irivai-systor does not exist. Creating the temporary link beside the
    real one keeps the rename on the same filesystem, which is what makes it
    atomic at all.

    DIRECTLY WHERE THAT IS POSSIBLE, through a root helper where it is not.
    Replacing /opt/irivai-systor means creating /opt/irivai-systor.new inside
    /opt, and POSIX asks for write permission on the PARENT directory -- which
    is root:root 755. A board installed under /home/pi never met this, because
    there the parent belongs to the service account. A board under /opt could
    unpack a release and never install it, and reported EACCES from the
    ROLLBACK flip, so it read as a failure to undo rather than a failure to do.
    """
    tmp = link + ".new"
    try:
        if os.path.islink(tmp) or os.path.exists(tmp):
            os.remove(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, link)
        return
    except PermissionError:
        pass                            # the parent directory is not ours
    _flip_privileged(link, target)


def _flip_privileged(link, target):
    """The same flip, asked of the root helper.

    It takes a VERSION, not a path: the helper resolves the directory itself
    from /etc/irivai/update.conf and refuses anything that is not directly
    inside the releases directory. Nothing the caller says becomes a path
    without being checked against that.
    """
    if not os.path.exists(FLIP_HELPER):
        raise Fail(
            f"cannot point {link} at {target}: no write permission on "
            f"{os.path.dirname(link)} and {FLIP_HELPER} is not installed. "
            f"Install it once as root from this release: sudo install -m 755 "
            f"-o root -g root {os.path.join(os.path.realpath(link), 'scripts', 'flip_link.py')} "
            f"{FLIP_HELPER}")
    version = os.path.basename(target).lstrip("vV")
    r = run(["sudo", "-n", FLIP_HELPER, version], timeout=60, nice=False)
    if r.returncode != 0:
        raise Fail(f"the update could not be made live: "
                   f"{(r.stderr or r.stdout or '').strip()[:300]}")


#: Installs the release's helpers, sudoers rules and nftables units as root,
#: from the bytes the signed manifest pins (scripts/root_steps.py says how).
#: Root-owned and outside every release, like FLIP_HELPER; a box gets it from
#: scripts/upgrade_steps.sh once, and every update keeps it current after.
ROOT_STEPS = "/usr/local/lib/irivai/root-steps.py"


def root_steps(target, man_bytes, sig_bytes):
    """Ask root for the release's root pieces. Never fails the update: the
    pieces are additive, and a box without them keeps what it had (the card
    that needs a missing helper names it). The helper trusts nothing in this
    request but the signature it checks itself."""
    if not os.path.exists(ROOT_STEPS):
        log(f"root helpers: {ROOT_STEPS} is not on this box yet, so this update "
            f"cannot install them; scripts/upgrade_steps.sh run once as root "
            f"installs it, and every update after that does the rest")
        return False
    req = json.dumps({"release": os.path.basename(target),
                      "manifest": base64.b64encode(man_bytes).decode("ascii"),
                      "sig": sig_bytes.decode("ascii", "replace").strip()})
    try:
        r = subprocess.run(["sudo", "-n", "/usr/bin/python3", "-I", ROOT_STEPS],
                           input=req, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"root helpers: could not run {ROOT_STEPS}: {exc}")
        return False
    for line in (r.stdout or "").splitlines():
        log("root helpers: " + line)
    if r.returncode != 0:
        log(f"root helpers: exit {r.returncode} {(r.stderr or '').strip()[-300:]}")
    return r.returncode == 0


#: What counts as a system's CODE, mirroring provision.py's own list. Not just
#: app/*.py: a system also runs scripts/fetch_models.py and reads models.toml,
#: and a rollback that restored only the .py files would leave the old app
#: beside the new release's fetcher.
APP_CODE_ITEMS = ("app", "scripts", "models.toml")


def _copy_item(src, dest):
    """Copy one code item, file or directory alike.

    The directory case is not hypothetical: a store-installed template keeps
    its extension package at app/store_ext/, and treating every entry as a
    file made the ROLLBACK path throw IsADirectoryError -- inside the handler
    that was supposed to be saving the box.
    """
    if os.path.isdir(src):
        shutil.copytree(src, dest, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    elif os.path.isfile(src):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)


#: This box's own settings, as opposed to the product's defaults. They live in
#: config/ beside the shipped .toml files, so a new release directory starts
#: without them unless they are carried across deliberately.
CARRY_CONFIG = ("deploy.toml",)


def previous_release(job):
    return os.path.realpath(job.link)


def carry_config(src_release, dest_release):
    """Move this box's identity into the release about to go live.

    Accounts, the release token, the customer-mode marker, every system's
    local overrides: none of that belongs to a release, and none of it is in
    the bundle. Missed once, an update produces a box that reports itself
    perfectly healthy -- /api/health needs no login -- and has no accounts to
    log in with.

    Called TWICE by do_apply: once before the snapshot, and again as the last
    step before the flip. The hub keeps serving while the venv builds and the
    apps are snapshotted, and a sign-in or a setting saved in that time lands
    in the OUTGOING release's config; carried only the first time, it was
    lost at the flip (Boboiboy, 57cf6ff6). Each file is replaced atomically
    (a temp file beside it, then os.replace): a hub that reads the new
    release's config at any instant sees the old copy or the new one, never
    half of one.
    """
    src = os.path.join(src_release, "config")
    dest = os.path.join(dest_release, "config")
    if not os.path.isdir(src):
        return []
    os.makedirs(dest, exist_ok=True)
    moved = []
    for name in sorted(os.listdir(src)):
        if not (name.endswith(".local.toml") or name in CARRY_CONFIG):
            continue
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            continue
        d = os.path.join(dest, name)
        # Re-applying the version already live makes src and dest the same
        # directory, and copy2 onto itself raises rather than doing nothing.
        if os.path.abspath(s) == os.path.abspath(d):
            continue
        tmp = d + ".carry.tmp"
        shutil.copy2(s, tmp)
        try:
            os.chmod(tmp, 0o600 if name.endswith(".local.toml") else 0o644)
        except OSError:
            pass
        os.replace(tmp, d)
        moved.append(name)
    return moved


def carry_models(src_release, dest_release):
    """Move every system's MODEL WEIGHTS into the release about to go live.

    Written after an update destroyed them on a real board. A system's
    detector points at `<release>/systems/<slug>/models`, and the operator's
    .hef files sat there -- inside the tree the flip replaces. A release
    bundle deliberately ships no weights (they are per-accelerator and tens of
    megabytes, and publish_release.py skips `models` entirely), so the new
    tree arrives with that directory empty. One update later the old release
    is pruned to keep only one previous, and the files are gone for good.

    Nothing reported it. The systems kept running on the model already loaded
    in memory; the Models page showed "no weights" and a card that could not
    be started; the next restart would have failed to detect at all. What
    saved the board it happened on was an unrelated copy in an old directory
    that happened not to have been cleaned up.

    So the whole models directory comes across, not a chosen extension list: a
    detector reads its .hef, its labels file and whatever else the manifest
    names, and a rule that carried the weights but not the labels beside them
    would fail just as silently. Copied, never moved -- the old release has to
    stay complete enough to roll back to.
    """
    src_root = os.path.join(src_release, "systems")
    if not os.path.isdir(src_root):
        return []
    carried = []
    try:
        slugs = sorted(os.listdir(src_root))
    except OSError:
        return []
    for slug in slugs:
        src = os.path.join(src_root, slug, "models")
        if not os.path.isdir(src):
            continue
        dest = os.path.join(dest_release, "systems", slug, "models")
        os.makedirs(dest, exist_ok=True)
        for dirpath, _dirs, files in os.walk(src):
            rel = os.path.relpath(dirpath, src)
            out_dir = dest if rel == os.curdir else os.path.join(dest, rel)
            os.makedirs(out_dir, exist_ok=True)
            for name in files:
                out = os.path.join(out_dir, name)
                # The bundle wins where it shipped something: a manifest the
                # release updated should not be reverted by a stale copy from
                # the tree being replaced. Weights are never in the bundle, so
                # they always come across.
                if os.path.exists(out):
                    continue
                try:
                    shutil.copy2(os.path.join(dirpath, name), out)
                except OSError:
                    continue
                carried.append(f"{slug}/{os.path.relpath(out, dest)}")
    return carried


def snapshot_apps(job, dest):
    """Keep each system's copy of the app code before replacing it.

    Systems on the store volume run their OWN copy of the runtime, so an
    update has to refresh those too -- and a rollback that restores /opt but
    leaves new app code on the NVMe would run an old master against a new
    runtime, a combination nobody has ever tested.
    """
    root = os.path.join(job.store_dir, "systems")
    os.makedirs(dest, exist_ok=True)
    try:
        slugs = sorted(os.listdir(root))
    except OSError:
        return []
    kept = []
    for slug in slugs:
        pkg = os.path.join(root, slug)
        if not os.path.isdir(os.path.join(pkg, "app")):
            continue
        out = os.path.join(dest, slug)
        if os.path.exists(out):
            shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out, exist_ok=True)
        for item in APP_CODE_ITEMS:
            _copy_item(os.path.join(pkg, item), os.path.join(out, item))
        kept.append(slug)
    return kept


def sync_apps(job, source_release):
    """Push the new release's app code into every system on the store volume.

    The hub does this too, at boot (repair_store_apps), so this is belt to
    that brace -- it happens BEFORE the systems are allowed to start, which is
    the difference between starting once on new code and starting on old code
    and being corrected afterwards. A system's data, models and local config
    are never touched: those belong to the box.
    """
    src_pkg = os.path.join(source_release, "systems", "demo")
    root = os.path.join(job.store_dir, "systems")
    if not os.path.isdir(src_pkg) or not os.path.isdir(root):
        return []
    done = []
    for slug in sorted(os.listdir(root)):
        pkg = os.path.join(root, slug)
        if not os.path.isdir(os.path.join(pkg, "app")):
            continue
        for item in APP_CODE_ITEMS:
            _copy_item(os.path.join(src_pkg, item), os.path.join(pkg, item))
        done.append(slug)
    return done


def restore_apps(job, snap):
    root = os.path.join(job.store_dir, "systems")
    try:
        slugs = os.listdir(snap)
    except OSError:
        return
    for slug in slugs:
        pkg = os.path.join(root, slug)
        if not os.path.isdir(pkg):
            continue
        for item in APP_CODE_ITEMS:
            _copy_item(os.path.join(snap, slug, item),
                       os.path.join(pkg, item))


# ---------------------------------------------------------------------------
# An update from a file (a local-source job)
# ---------------------------------------------------------------------------

def local_source(job):
    """The directory a local-source job's file waits in. Inside the update
    dir and nowhere else: this script removes it when the job ends."""
    src = os.path.realpath(str(job.d.get("local_dir") or "") or job.work)
    if src == os.path.realpath(job.work) or not _inside(src, job.work):
        raise Fail("the uploaded update file is missing - upload it again")
    return src


def read_local(job, key, limit):
    try:
        with open(os.path.join(local_source(job), job.d[key]), "rb") as f:
            data = f.read(limit + 1)
    except OSError:
        raise Fail("the uploaded update file is missing - upload it again")
    if len(data) > limit:
        raise Fail("an update metadata file was larger than expected")
    return data


def check_local_bundle(job, sha, size):
    """The uploaded bundle, checked against the signed size and hash."""
    path = os.path.join(local_source(job), job.d["bundle_asset"])
    if not os.path.isfile(path) or (size and os.path.getsize(path) != size):
        raise Fail("the uploaded update was the wrong size")
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != sha:
        raise Fail("the update did not match its signed hash -- refusing it")
    return path


def drop_local(job):
    """Remove the uploaded file once its job has ended, however it ended."""
    try:
        shutil.rmtree(local_source(job), ignore_errors=True)
    except Fail:
        pass


def release_target(job):
    """The directory the new release is built in: releases/v<version>, or
    v<version>-r<n> while that one is the release running now. Reinstalling
    the same version from a file must never build over the live tree."""
    base = os.path.join(job.releases, "v" + job.version)
    live, target, n = os.path.realpath(job.link), base, 0
    while os.path.realpath(target) == live:
        n += 1
        target = f"{base}-r{n}"
    return target


def release_version(release, fallback):
    """The version a release directory reports (its VERSION file). A
    reinstall's directory is v<version>-r<n>, and the health gate has to
    expect what the hub will say, not the directory's name."""
    try:
        with open(os.path.join(release, "VERSION"), encoding="utf-8") as f:
            return f.read().strip().lstrip("vV") or fallback
    except OSError:
        return fallback


# ---------------------------------------------------------------------------
# The job itself
# ---------------------------------------------------------------------------

def do_apply(job):
    work = job.work
    staging = os.path.join(work, "staging")
    target = release_target(job)
    # An offline update file the hub already checked and left in the update
    # dir (core/services/update_offline.py): no token, no download, every
    # other step the same -- the signature and hash are checked again here.
    local = job.d.get("source") == "local"
    tok = "" if local else read_token(job.config_dir, job.token_key)
    if not tok and not local:
        # Names the file it looked in. "No token is configured" on a box that
        # plainly has one sends someone to re-paste a token that was never the
        # problem; the path says whether this is a missing token or a config
        # root that did not survive the trip between two processes.
        raise Fail("no update token was found in "
                   + os.path.join(job.config_dir, "update.local.toml"))

    # -- 0. preflight ------------------------------------------------------
    job.say("preflight", 2, "checking this box can take the update")
    # The same version only from a file, and only after the admin confirmed
    # it (the job says so); older never.
    same = semver(job.version) == semver(job.current)
    if semver(job.version) < semver(job.current) or (
            same and not (local and job.d.get("reinstall"))):
        raise Fail(f"{job.version} is not newer than {job.current}")
    # A box whose store fell back INSIDE the code tree would have its systems,
    # recordings and layouts deleted along with the release they sit in. That
    # box needs its storage fixed, not an update.
    if _inside(job.store_dir, job.releases):
        raise Fail("this box keeps its data inside the program folder -- "
                   "updating would delete it. Fix the storage location first.")
    if local:
        man_bytes = read_local(job, "manifest_asset", 1 << 20)
        sig_bytes = read_local(job, "sig_asset", 4096)
    else:
        man_asset = (job.assets.get(job.d["manifest_asset"]) or {}).get("url")
        sig_asset = (job.assets.get(job.d["sig_asset"]) or {}).get("url")
        bun_asset = job.assets.get(job.d["bundle_asset"]) or {}
        if not (man_asset and sig_asset and bun_asset.get("url")):
            raise Fail("that release is missing its bundle or manifest")
        # Every asset must be the JOB'S repo's own. The token rides with each
        # request, and each channel has a repo and a token of its own: a URL
        # from the other channel's repo would spend this token there (the
        # customer token at the dev repo, or the reverse). Checked before any
        # request.
        prefix = ("https://api.github.com/repos/%s/%s/releases/assets/"
                  % (job.d.get("owner"), job.d.get("repo"))).lower()
        if not all(str(u).lower().startswith(prefix)
                   for u in (man_asset, sig_asset, bun_asset["url"])):
            raise Fail("that release was listed from a different release repo "
                       "- check for updates again")
        man_bytes = fetch_healing(job, fetch_small, man_asset, tok)
        sig_bytes = fetch_healing(job, fetch_small, sig_asset, tok)

    manifest = verify(man_bytes, sig_bytes.decode("ascii", "replace"),
                      job.d.get("pubkey") or "", job.d.get("manifest_app"))
    if str(manifest.get("version") or "").lstrip("vV") != job.version:
        raise Fail("the release's manifest names a different version")

    bundle = manifest.get("bundle") or {}
    size = int(bundle.get("size") or 0)
    unpacked = int(manifest.get("unpacked_size") or (size * 4))
    # Space for: the tarball, its unpacked tree, and a virtualenv. Refusing
    # here is a message; running out mid-install is a service call.
    need = (0 if local else size) + int(unpacked * 1.2) + (600 << 20)
    for where in (work, job.releases):
        if free_bytes(where) < need:
            raise Fail(f"not enough free space on {where}: this update needs "
                       f"about {need >> 20} MB")

    req = (manifest.get("requires") or {}).get("hailort_driver") or ""
    if req:
        job.say("preflight", 4, "checking the Hailo driver")
        if not driver_ok(req):
            raise Fail(f"this release needs a Hailo driver {req}; the board "
                       f"has {driver_version() or 'none detected'}. Update "
                       f"the driver first.")

    # -- 1. download -------------------------------------------------------
    os.makedirs(staging, exist_ok=True)
    if local:
        job.say("download", 40, "checking the uploaded file")
        tar_path = check_local_bundle(job, str(bundle.get("sha256") or ""), size)
    else:
        job.say("download", 5, f"downloading {job.version}")
        tar_path = fetch_bundle(bun_asset["url"], tok,
                                os.path.join(staging, "bundle.tar.gz"),
                                str(bundle.get("sha256") or ""), size, job.say,
                                heal=lambda: heal_route(job))

    # -- 2. unpack ---------------------------------------------------------
    job.say("unpack", 45, "unpacking")
    tree = os.path.join(staging, "tree")
    if os.path.isdir(tree):
        shutil.rmtree(tree, ignore_errors=True)
    unpack(tar_path, tree, max_bytes=max(unpacked * 4, 1 << 30))

    # -- 3. build the release's own venv ----------------------------------
    # Offline, from wheels the release carries, against a lock file. A box in
    # the field must not resolve PyPI live: it would install versions nobody
    # tested, and it would fail entirely on a site with no outbound access
    # beyond GitHub.
    job.say("venv", 55, "installing dependencies")
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.move(tree, target)
    build_venv(job, target)
    # The accelerator runtime the bundle cannot ship. Carried from the venv
    # being replaced, or this release goes live unable to see -- silently,
    # because the app falls back to a detector that reports itself running.
    runtime = carry_runtime(job, previous_release(job), target)
    if runtime:
        # Built outside the f-string: nesting the same quote inside one is
        # PEP 701, which is a SyntaxError on 3.11 -- and this file is parsed
        # by whatever interpreter the box happens to have.
        names = ", ".join(runtime)
        log(f"carried the accelerator runtime: {names}")

    # -- 3b. carry this box's own settings into the new release -----------
    # config/ lives INSIDE a release directory, so a new release arrives with
    # the product's stock .toml files and none of this box's: no accounts, no
    # release token, no customer-mode marker. The box would come up healthy
    # (health needs no login) and locked out of itself. The local half is the
    # box's, not the release's, so it moves across every time.
    carry_config(previous_release(job), target)
    # ...and the weights those systems detect with, which live in the same
    # tree and are in no bundle. Losing them is silent until the next restart.
    carried = carry_models(previous_release(job), target)
    if carried:
        log(f"carried {len(carried)} model file(s) into the new release")

    # -- 3c. the root-owned pieces this release needs, before it goes live --
    job.say("rootsteps", 72, "updating the box's root helpers")
    root_steps(target, man_bytes, sig_bytes)

    # -- 4. snapshot what a rollback will need ----------------------------
    job.say("snapshot", 75, "saving the current app code")
    snap = os.path.join(work, "prev-apps")
    if os.path.isdir(snap):
        shutil.rmtree(snap, ignore_errors=True)
    snapshot_apps(job, snap)
    was_running = running_systems(job)
    # Captured BEFORE the flip: a master whose hub unit sets KillMode=process
    # (some boards do, so a dashboard restart never takes a live detection
    # down) survives restart_stack()'s hub restart untouched -- restarting
    # the hub alone would not update it, and the box would call the update
    # successful while it kept serving the old code indefinitely.
    was_masters = running_masters(job)
    previous = previous_release(job)
    job.previous = previous

    # -- 4b. this box's settings once more, the LAST step before the flip --
    # The hub kept serving through the venv build and the snapshot; anything
    # it wrote to config since 3b (a sign-in, a saved setting) is only in the
    # outgoing release until this copy.
    carry_config(previous, target)

    # -- 5..8. go live ----------------------------------------------------
    open(os.path.join(work, "maintenance"), "w").close()
    flipped_at = time.time()
    try:
        job.say("flip", 80, f"switching to {job.version}")
        flip(job.link, target)
        job.say("restart", 84, "restarting the platform")
        restart_stack(job)
        gate_a(job)

        job.say("appsync", 92, "updating system app code")
        sync_apps(job, target)
        # Systems come back only now: the hub skipped autostart while the
        # maintenance flag was up, so this is the first honest chance for the
        # detection apps to start against fully-swapped code.
        _rm(os.path.join(work, "maintenance"))
        restart_stack(job)
        gate_a(job)
        job.say("gate", 95, "bringing running systems back up on new code")
        revive_masters(job, target, was_masters)
        verify_masters(job, was_masters)
        gate_b(job, was_running, since=flipped_at)
    # Not `except Fail`. Past the flip, the box is running code that has not
    # proved itself, and EVERY way of leaving this block without rolling back
    # leaves it there: a systemctl call that times out, a copy that hits a
    # permission, a bug in this script. The reasons differ; the correct
    # response does not.
    except Exception as exc:                                # noqa: BLE001
        job.say("rollback", 90, "the update did not come up -- putting the "
                                "previous version back", str(exc))
        try:
            # Back up while the tree changes again: the hub is running by now
            # and would otherwise start systems against app code being
            # rewritten underneath them.
            open(os.path.join(work, "maintenance"), "w").close()
            flip(job.link, previous)
            restore_apps(job, snap)
            _rm(os.path.join(work, "maintenance"))
            restart_stack(job)
            gate_a_quiet(job, os.path.basename(previous).lstrip("vV"))
            # was_masters were stopped (or found already dead) on the way to
            # trying the new release -- bring them back on the code that was
            # actually running before any of this started.
            revive_masters(job, previous, was_masters)
        finally:
            _rm(os.path.join(work, "maintenance"))
        job.say("rolled_back", 100,
                f"rolled back to {os.path.basename(previous)}", str(exc))
        return 1
    finally:
        _rm(os.path.join(work, "maintenance"))

    # -- 9. tidy up --------------------------------------------------------
    shutil.rmtree(staging, ignore_errors=True)
    prune_releases(job, keep={target, previous})
    job.say("done", 100, f"running {job.version}")
    return 0


def gate_a_quiet(job, version):
    """Gate A after a rollback: report, never raise. The box is already back
    on code that ran here before; there is nothing further to fall back to."""
    deadline = time.time() + GATE_A_S
    while time.time() < deadline:
        h = health(job.port)
        if h and (h.get("version") or "") == version:
            return True
        time.sleep(3)
    return False


def do_rollback(job):
    """The Rollback button: same flip, no download, no version rules.

    Nothing is installed here -- the target already passed every check when it
    was installed, and it already ran on this box. That is why the newer-only
    rule does not apply.
    """
    target = os.path.join(job.releases, "v" + job.version)
    if not os.path.isdir(target):
        raise Fail(f"version {job.version} is not installed on this box")
    job.version = release_version(target, job.version)
    previous = previous_release(job)
    job.previous = previous
    snap = os.path.join(job.work, "prev-apps")
    was_running = running_systems(job)
    was_masters = running_masters(job)
    # The box's own accounts, token and settings live in config/ inside a
    # release, and the one being restored last saw them at whatever state they
    # were in when it was replaced. Carry the current ones back, or rolling
    # back would also roll back every password changed since.
    carry_config(previous, target)
    carry_models(previous, target)
    open(os.path.join(job.work, "maintenance"), "w").close()
    flipped_at = time.time()
    try:
        job.say("flip", 40, f"switching back to {job.version}")
        flip(job.link, target)
        if os.path.isdir(snap):
            restore_apps(job, snap)
        job.say("restart", 60, "restarting the platform")
        restart_stack(job)
        _rm(os.path.join(job.work, "maintenance"))
        restart_stack(job)
        gate_a(job)
        revive_masters(job, target, was_masters)
        verify_masters(job, was_masters)
        gate_b(job, was_running, since=flipped_at)
    except Exception as exc:                                # noqa: BLE001
        # Even a rollback can fail to come up. Going forward again is the only
        # remaining move, and it is a version that WAS running a minute ago.
        # The terminal status is written LAST: a "failed" written before the
        # restore would tell the boot-time repair script that nothing was in
        # flight, on a box whose flip had not finished.
        job.say("rollback", 90, "could not roll back -- restoring "
                + os.path.basename(previous), str(exc))
        try:
            flip(job.link, previous)
            restart_stack(job)
            revive_masters(job, previous, was_masters)
        except Exception:                                   # noqa: BLE001
            pass
        _rm(os.path.join(job.work, "maintenance"))
        job.say("failed", 100, "could not roll back; the previous version was "
                               "restored", str(exc))
        return 1
    finally:
        _rm(os.path.join(job.work, "maintenance"))
    job.say("done", 100, f"running {job.version}")
    return 0


def build_venv(job, release):
    venv = os.path.join(release, ".venv")
    lock = os.path.join(release, "requirements.lock")
    if not os.path.exists(lock):
        lock = os.path.join(release, "requirements.txt")
    wheels = os.path.join(release, "wheels")
    r = run([sys.executable, "-m", "venv", "--system-site-packages", venv],
            timeout=600)
    if r.returncode != 0:
        raise Fail("could not create the new environment -- "
                   + (r.stderr or "").strip()[:200])
    if not os.path.isdir(wheels):
        # Never fall back to resolving PyPI live. That would install whatever
        # is current on the day the box updates -- versions nobody tested --
        # and it would simply fail on a site whose firewall allows GitHub and
        # nothing else. A bundle without its wheels is a broken bundle.
        raise Fail("this release did not carry its dependency wheels")
    pip = os.path.join(venv, "bin", "pip")
    # Offline install: the bundle carries every wheel this release needs.
    cmd = [pip, "install", "--no-input", "--disable-pip-version-check",
           "--no-index", "--find-links", wheels, "-r", lock]
    r = run(cmd, timeout=3600)
    if r.returncode != 0:
        raise Fail("dependency install failed -- "
                   + (r.stderr or r.stdout or "").strip()[-400:])


#: Packages the ACCELERATOR provides, which no release bundle can ship.
#: HailoRT is installed per device by the vendor's SDK against the silicon in
#: that box; it is not on PyPI, it is not in requirements.lock, and the
#: wheelhouse CI builds has never contained it. A release therefore cannot
#: supply it -- it can only avoid taking it away.
RUNTIME_PACKAGES = ("hailo_platform", "hailort", "hailo_tappas")


def _venv_python(release):
    for rel in (("bin", "python"), ("Scripts", "python.exe")):
        p = os.path.join(release, ".venv", *rel)
        if os.path.exists(p):
            return p
    return ""


def _imports(python, module):
    if not python:
        return False
    r = run([python, "-c", f"import {module}"], timeout=60, nice=False)
    return r.returncode == 0


def carry_runtime(job, src_release, dest_release):
    """Carry the accelerator runtime into the release about to go live.

    Written after an update stopped a board detecting without saying a word.

    The updater builds each release its own venv, offline, from the bundled
    wheelhouse. HailoRT is not in that wheelhouse and cannot be: the vendor's
    SDK installs it per device. So the new venv came up without it, the
    detection app caught the missing import, fell back to a detector that sees
    nothing "so the stream stays up", and reported itself running. Video
    flowed, every rule read zero, and no page said anything was wrong.

    An update may add capability. It must never quietly remove one. So this
    asks the OUTGOING venv what it had and makes sure the incoming one has it
    too, copying the package straight across -- same box, same interpreter,
    same architecture, so the installed files are the installed files.

    If the old venv never had it, nothing happens: a box with no accelerator,
    or one running the mock detector, is not missing anything. If it HAD it
    and the copy does not take, the update fails and rolls back, because a box
    that cannot see is worse than a box that did not update.
    """
    old_py, new_py = _venv_python(src_release), _venv_python(dest_release)
    if not old_py or not new_py:
        return []
    carried = []
    for mod in RUNTIME_PACKAGES:
        if not _imports(old_py, mod):
            continue                       # the box never had it; nothing owed
        if _imports(new_py, mod):
            continue                       # already there (system site-packages)
        # Where it lives in the outgoing venv, asked of that interpreter
        # rather than guessed from a path pattern.
        r = run([old_py, "-c",
                 "import importlib.util as u, os, sys;"
                 f"s = u.find_spec({mod!r});"
                 "sys.stdout.write(os.path.dirname(s.origin) if s and s.origin else '')"],
                timeout=60, nice=False)
        src = (r.stdout or "").strip()
        if not src or not os.path.isdir(src):
            continue
        dest_site = None
        for root, dirs, _files in os.walk(os.path.join(dest_release, ".venv")):
            if os.path.basename(root) == "site-packages":
                dest_site = root
                break
        if not dest_site:
            continue
        try:
            shutil.copytree(src, os.path.join(dest_site, os.path.basename(src)),
                            dirs_exist_ok=True)
            # The .dist-info beside it, or pip believes the package is not
            # installed and a later install could quietly replace it.
            parent = os.path.dirname(src)
            for name in os.listdir(parent):
                if name.startswith(mod.replace("_", "")) or name.startswith(mod):
                    if name.endswith((".dist-info", ".egg-info")):
                        shutil.copytree(os.path.join(parent, name),
                                        os.path.join(dest_site, name),
                                        dirs_exist_ok=True)
        except (OSError, shutil.Error) as exc:
            raise Fail(f"could not carry {mod} into the new release: {exc}")
        if not _imports(new_py, mod):
            raise Fail(f"the new release cannot load {mod}, which this box "
                       "uses to detect -- refusing to go live blind")
        carried.append(mod)
    return carried


def prune_releases(job, keep):
    """Keep the running release and one to fall back to; delete the rest.

    A reinstall's "previous" is the same version (v<ver> beside the new
    v<ver>-r<n>), so keeping only those two deleted the genuinely older
    release and left Roll back nowhere to go but the same version (Pipi F5,
    2026-10-05). When everything in `keep` is one version, the newest
    release below it is kept too.
    """
    try:
        names = sorted(os.listdir(job.releases), key=semver)
    except OSError:
        return
    keep = {os.path.realpath(k) for k in keep}
    versions = {semver(os.path.basename(k)) for k in keep}
    if len(versions) == 1:
        older = [n for n in names if semver(n) < next(iter(versions))
                 and os.path.isdir(os.path.join(job.releases, n))]
        if older:
            keep.add(os.path.realpath(os.path.join(job.releases, older[-1])))
    for name in names:
        path = os.path.realpath(os.path.join(job.releases, name))
        if path in keep or not os.path.isdir(path):
            continue
        shutil.rmtree(path, ignore_errors=True)


def driver_version():
    for path in ("/sys/module/hailo_pci/version", "/sys/module/hailo1x_pci/version"):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return ""


def driver_ok(spec):
    """Compare the board's Hailo driver against a manifest requirement like
    '>=5.3,<5.4'. An undetectable driver passes: refusing to update a board
    whose driver we merely cannot READ would strand it."""
    have = driver_version()
    if not have:
        return True
    hv = semver(have)
    for clause in str(spec).split(","):
        clause = clause.strip()
        for op in (">=", "<=", "==", ">", "<"):
            if clause.startswith(op):
                want = semver(clause[len(op):])
                ok = {">=": hv >= want, "<=": hv <= want, "==": hv == want,
                      ">": hv > want, "<": hv < want}[op]
                if not ok:
                    return False
                break
    return True


def _inside(path, parent):
    try:
        return os.path.commonpath([os.path.realpath(path),
                                   os.path.realpath(parent)]) == \
            os.path.realpath(parent)
    except (OSError, ValueError):
        return False


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def main():
    work = os.environ.get("IRIVAI_UPDATE_DIR") or os.path.dirname(
        os.path.abspath(__file__))
    try:
        job = Job(work)
    except (OSError, ValueError) as exc:
        log(f"no update job to run: {exc}")
        return 2
    # One job at a time. The route checks this too, but the check that counts
    # is the one next to the work: two appliers unpacking into the same
    # staging directory is the one race that could corrupt a release.
    lock_path = os.path.join(work, "lock")
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        log("another update is already running")
        return 3
    try:
        job.say(job.phase, 1, "starting")
        return do_rollback(job) if job.mode == "rollback" else do_apply(job)
    except Fail as exc:
        job.say("refused" if job.phase == "preflight" else "failed", 100,
                "the update did not run", str(exc))
        return 1
    except Exception as exc:                                # noqa: BLE001
        import traceback
        traceback.print_exc()
        job.say("failed", 100, "the update stopped unexpectedly", str(exc))
        return 1
    finally:
        if job.d.get("source") == "local":
            drop_local(job)


if __name__ == "__main__":
    sys.exit(main())
