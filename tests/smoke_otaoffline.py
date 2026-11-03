# "Update manually": one offline update file, uploaded, checked, installed
# through the same applier as an online update (Tok Aba, 2026-10-05).
#
# Pinned:
#   1. a good file (newer version) is accepted: the three parts wait in a
#      private dir with the version and the signed notes; Install writes a
#      local-source job and starts the unit; the applier installs it with no
#      token and no download, re-checking signature and hash, and removes
#      the file when the job ends
#   2. refused BEFORE anything is written (no job, no status, nothing left
#      waiting): bad signature, altered manifest, tampered bundle, a developer
#      file on a customer box and the reverse, an older version, a "../"
#      entry, a link entry, an extra entry, a missing part, not an archive,
#      an oversize declared length, a part over its cap
#   3. the same version: accepted as same_version, Install refuses without
#      the confirm and goes ahead with it; the applier builds it beside the
#      live release (v<ver>-r1), never over it, and accepts the same version
#      only for a confirmed local job; Roll back from either twin reaches
#      the genuinely older release, never the other twin
#   4. cleanup: cancel, expiry, another boot, a job that ended; a file a job
#      is about to take is kept; one upload at a time; a unit that fails to
#      start frees the slot at once
#   5. the routes: admin (and developer on a dev box) gate, raw streamed
#      body, capped while it arrives and written off the loop, filename
#      never read
#   6. Pipi's review (2026-10-05): a pax / GNU long-name / sparse header
#      claiming hundreds of MB is refused at the header with memory still
#      flat (F1); no Content-Length is budgeted as the cap and the store is
#      re-checked while streaming (F2); the slot has a deadline, an idle
#      limit per chunk, and Cancel frees it (F3); an upload or a sweep in
#      Install's "starting" window leaves ready/ alone (F4); a reinstall's
#      prune keeps the genuinely older release for Roll back (F5)
import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from cryptography.hazmat.primitives import serialization          # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (   # noqa: E402
    Ed25519PrivateKey)

import core.version as cv                                      # noqa: E402
from core.services import offline_file as of                   # noqa: E402
from core.services import update as upd                        # noqa: E402
from core.services import update_offline as uo                 # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()
PUB = base64.b64encode(KEY.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def box(dev=False):
    d = tempfile.mkdtemp(prefix="smoke_otaoffline_cfg_")
    with open(os.path.join(d, "update.toml"), "w", encoding="utf-8") as f:
        f.write(f'[release]\nowner = "o"\nrepo = "r"\ndev_repo = "rd"\n'
                f'[signing]\npubkey = "{PUB}"\n')
    with open(os.path.join(d, "deploy.toml"), "w", encoding="utf-8") as f:
        f.write('mode = "dev"\n' if dev else 'mode = "customer"\n')
    return d


def tgz(members, path=None):
    """An outer archive from (name, bytes | TarInfo-tweak) pairs."""
    path = path or tempfile.mktemp(suffix=".tar.gz")
    with tarfile.open(path, "w:gz") as tf:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tf.addfile(info)
                continue
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def bundle(version):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tf:
        for name, data in (("VERSION", (version + "\n").encode()),
                           ("config/auth.toml", b"[session]\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def offline(version="1.0.16", app=upd.MANIFEST_APP, key=KEY, tamper=None,
            extra=(), drop=None):
    b = bundle(version)
    man = json.dumps({"app": app, "schema": 1, "version": version,
                      "bundle": {"name": "bundle.tar.gz",
                                 "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)},
                      "unpacked_size": 1000, "requires": {"hailort_driver": ""},
                      "notes_short": "Update:\n- {FEATURES UPDATE} a thing"},
                     indent=2, sort_keys=True).encode()
    sig = base64.b64encode(key.sign(man)) + b"\n"
    if tamper == "bundle":
        b = b[:-1] + bytes([b[-1] ^ 1])
    if tamper == "manifest":
        man = man.replace(b'"schema": 1', b'"schema": 2')
    parts = [("bundle.tar.gz", b, "f"), ("manifest.json", man, "f"),
             ("manifest.sig", sig, "f")]
    parts = [p for p in parts if p[0] != drop] + list(extra)
    return tgz(parts)


calls = {"unit": 0}
upd.ota_ready = lambda: True
upd.job_running = lambda: False
upd._start_unit = lambda: calls.__setitem__("unit", calls["unit"] + 1)
cv.VERSION = upd.VERSION = "1.0.15"
JOB, STATUS = upd._p("job.json"), upd._p("status.json")


def upload(path, cfg, channel="stable"):
    """What the route does: claim, stream to the part file, accept."""
    part = uo.begin(os.path.getsize(path))
    try:
        with open(path, "rb") as src, uo.open_part(part) as out:
            out.write(src.read())
        return uo.accept(part, cfg, channel)
    except uo.OfflineError:
        uo.discard()
        raise
    finally:
        uo.end()


def nothing_written(label):
    left = os.listdir(uo.offline_dir())
    check(f"{label}: nothing left waiting", left == [], left)
    check(f"{label}: no job and no status written",
          not os.path.exists(JOB) and not os.path.exists(STATUS))


# -- 1. a good file -------------------------------------------------------------
CFG = box()
info = upload(offline(), CFG)
check("a good newer file is accepted", info["version"] == "1.0.16"
      and info["same_version"] is False, info)
check("...with the signed notes to show", "a thing" in (info.get("notes") or ""), info)
ready = uo.ready_dir()
check("...its three parts wait in the private dir",
      sorted(os.listdir(ready)) == ["bundle.tar.gz", "manifest.json", "manifest.sig", "meta.json"],
      os.listdir(ready))
if os.name == "posix":
    check("the offline dir is 0700", (os.stat(uo.offline_dir()).st_mode & 0o777) == 0o700,
          oct(os.stat(uo.offline_dir()).st_mode))
check("nothing is written to the job or status before Install",
      not os.path.exists(JOB) and not os.path.exists(STATUS))

st = uo.install(info["id"], False, CFG)
job = json.load(open(JOB, encoding="utf-8"))
check("Install writes a local-source job", job.get("source") == "local"
      and job.get("local_dir") == ready and job.get("reinstall") is False
      and job.get("assets") == {}, job)
check("...and starts the unit", calls["unit"] == 1 and st["phase"] == "starting", (calls, st))

# The applier, as it runs on the box.
spec = importlib.util.spec_from_file_location(
    "_smoke_applier_offline", os.path.join(ROOT, "scripts", "apply_update.py"))
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)
REL = tempfile.mkdtemp(prefix="smoke_otaoffline_rel_")
OLD = os.path.join(REL, "v1.0.15")
os.makedirs(os.path.join(OLD, "config"))
job.update({"releases_dir": REL, "live_link": OLD, "config_dir": os.path.join(OLD, "config")})
upd._write_json(JOB, job)
net = []
au.read_token = lambda *a, **k: net.append("token") or ""
au.fetch_healing = lambda *a, **k: net.append("fetch") or b""
au.fetch_bundle = lambda *a, **k: net.append("download") or ""
au.free_bytes = lambda path: 1 << 40
au.build_venv = lambda job, release: None
au.carry_runtime = lambda job, src, dest: []
au.previous_release = lambda job: OLD
flips = []
au.flip = lambda link, target: flips.append(target)
real_prune = au.prune_releases
for name in ("restart_stack", "sync_apps", "revive_masters", "verify_masters",
             "gate_b", "prune_releases", "snapshot_apps"):
    setattr(au, name, lambda *a, **k: None)
au.running_systems = lambda job, since=0: []
au.running_masters = lambda job: {}
au.gate_a = lambda job: True
au.log = lambda msg: None
j = au.Job(upd.update_dir())
rc = au.do_apply(j)
check("the applier installs the file", rc == 0 and flips == [os.path.join(REL, "v1.0.16")],
      (rc, flips))
check("...with no token read and nothing downloaded", net == [], net)
check("...the new release is the file's bundle",
      open(os.path.join(REL, "v1.0.16", "VERSION")).read().strip() == "1.0.16")
au.drop_local(j)
check("the uploaded file is removed when the job ends", not os.path.exists(ready))
src = open(os.path.join(ROOT, "scripts", "apply_update.py"), encoding="utf-8").read()
main = src.split("def main()", 1)[1]
check("main() removes a local job's file however the job ends",
      "finally:" in main and "drop_local(job)" in main.split("finally:", 1)[1])
os.remove(JOB)
os.remove(STATUS)

# The applier re-checks: a bundle swapped after the hub accepted it is refused.
info = upload(offline(), CFG)
uo.install(info["id"], False, CFG)
job = json.load(open(JOB, encoding="utf-8"))
job.update({"releases_dir": REL, "live_link": OLD, "config_dir": os.path.join(OLD, "config")})
upd._write_json(JOB, job)
with open(os.path.join(uo.ready_dir(), "bundle.tar.gz"), "r+b") as f:
    f.seek(10)
    f.write(b"X")
try:
    au.do_apply(au.Job(upd.update_dir()))
    check("the applier re-checks the bundle hash", False, "installed")
except au.Fail as e:
    check("the applier re-checks the bundle hash", "signed hash" in str(e), str(e))
uo._clear()
os.remove(JOB)
os.remove(STATUS)

# -- 2. refusals, before anything is written ------------------------------------
cases = [
    ("bad signature", offline(key=OTHER), "not signed"),
    ("altered manifest", offline(tamper="manifest"), "not signed"),
    ("tampered bundle", offline(tamper="bundle"), "does not match"),
    ("a developer file on a customer box", offline(app=upd.MANIFEST_APP_DEV),
     "for developer boxes"),
    ("an older version", offline(version="1.0.14"), "older"),
    ("a ../ entry", offline(extra=[("../evil.txt", b"x", "f")]), "more than an update"),
    ("an absolute entry", offline(extra=[("/tmp/evil.txt", b"x", "f")]), "more than an update"),
    ("a link entry", tgz([("manifest.sig", b"", "sym")]), "links or devices"),
    ("an extra entry", offline(extra=[("notes.md", b"x", "f")]), "more than an update"),
    ("a missing part", offline(drop="manifest.sig"), "incomplete"),
]
junk = tempfile.mktemp(suffix=".tar.gz")
open(junk, "wb").write(b"this is not an archive at all")
cases.append(("not an archive", junk, "not an update file"))
for label, path, words in cases:
    try:
        upload(path, CFG)
        check(f"{label} is refused", False, "accepted")
    except uo.OfflineError as e:
        check(f"{label} is refused in plain words", words in str(e), str(e))
    nothing_written(label)
check("the ../ entry wrote nothing outside", not os.path.exists(
    os.path.join(os.path.dirname(uo.offline_dir()), "evil.txt")))

DEV = box(dev=True)
try:
    upload(offline(), DEV, "dev")
    check("a customer file on a developer box is refused", False, "accepted")
except uo.OfflineError as e:
    check("a customer file on a developer box is refused", "for customer boxes" in str(e), str(e))
nothing_written("customer file on a dev box")
info = upload(offline(version="1.0.16-dev", app=upd.MANIFEST_APP_DEV), DEV, "dev")
check("a developer file installs on a developer box", info["channel"] == "dev", info)
uo.discard()

try:
    uo.begin(uo.MAX_UPLOAD + 1)
    check("an oversize declared length is refused before reading", False, "claimed")
except uo.OfflineError as e:
    check("an oversize declared length is refused before reading", "too large" in str(e), str(e))
try:
    of.verify_file(offline(), tempfile.mkdtemp(), PUB, upd.MANIFEST_APP, max_bundle=16)
    check("a part over its cap is refused", False, "accepted")
except of.OfflineError as e:
    check("a part over its cap is refused", "larger than an update" in str(e), str(e))
nothing_written("oversize")

# -- 3. the same version ----------------------------------------------------------
info = upload(offline(version="1.0.15"), CFG)
check("the same version is accepted, marked same_version", info["same_version"] is True, info)
try:
    uo.install(info["id"], False, CFG)
    check("Install of the same version needs the confirm", False, "installed")
except uo.OfflineError as e:
    check("Install of the same version needs the confirm", "Confirm" in str(e), str(e))
check("...and wrote no job", not os.path.exists(JOB))
uo.install(info["id"], True, CFG)
job = json.load(open(JOB, encoding="utf-8"))
check("with the confirm it goes ahead as a reinstall", job.get("reinstall") is True, job)
SAME = os.path.join(REL, "v1.0.15")
j = au.Job(upd.update_dir())
j.releases, j.link = REL, SAME
check("a reinstall builds beside the live release, never over it",
      au.release_target(j) == SAME + "-r1", au.release_target(j))
j.link = os.path.join(REL, "v1.0.16")
check("...and in the plain directory when that is not live",
      au.release_target(j) == SAME, au.release_target(j))
os.makedirs(SAME + "-r1", exist_ok=True)
open(os.path.join(SAME + "-r1", "VERSION"), "w").write("1.0.15\n")
check("a rollback to v1.0.15-r1 expects the version the hub will report",
      au.release_version(SAME + "-r1", "1.0.15-r1") == "1.0.15")
pre = src.split("# -- 0. preflight", 1)[1].split("# -- 1.", 1)[0]
check("the applier allows the same version only for a confirmed local job",
      'same and not (local and job.d.get("reinstall"))' in pre)
# Roll back after a reinstall: v1.0.15 and v1.0.15-r1 are one version, and
# the button went between them for ever while the older release prune
# keeps (F5) was out of reach (Yaya's rehearsal S5/S6, 2026-10-05).
PV = tempfile.mkdtemp(prefix="smoke_otaoffline_prev_")
for n, v in (("v1.0.14", "1.0.14"), ("v1.0.15", "1.0.15"), ("v1.0.15-r1", "1.0.15")):
    os.makedirs(os.path.join(PV, n))
    open(os.path.join(PV, n, "VERSION"), "w").write(v + "\n")
real_releases_dir, real_live_link = upd.releases_dir, upd.live_link
upd.releases_dir = lambda: PV
upd.live_link = lambda: os.path.join(PV, "v1.0.15-r1")
check("Roll back from the reinstall reaches the older release, not its twin",
      upd.previous_version() == "1.0.14", upd.previous_version())
upd.live_link = lambda: os.path.join(PV, "v1.0.15")
check("...and from the plain twin too", upd.previous_version() == "1.0.14")
os.makedirs(os.path.join(PV, "v1.0.14-r1"))
open(os.path.join(PV, "v1.0.14-r1", "VERSION"), "w").write("1.0.14\n")
check("...the newest copy of that version", upd.previous_version() == "1.0.14-r1")
shutil.rmtree(os.path.join(PV, "v1.0.14"))
shutil.rmtree(os.path.join(PV, "v1.0.14-r1"))
check("twins alone leave nothing to roll back to", upd.previous_version() == "")
upd.releases_dir, upd.live_link = real_releases_dir, real_live_link
upd.previous_version = lambda: "1.0.15-r1"
check("the card says v1.0.15, not the directory name",
      upd.public_state({}, "stable", CFG)["previous"] == "1.0.15")
uo._clear()
os.remove(JOB)
os.remove(STATUS)

# -- 4. cleanup --------------------------------------------------------------------
info = upload(offline(), CFG)
check("cancel removes the waiting file", uo.discard() and os.listdir(uo.offline_dir()) == [])
info = upload(offline(), CFG)
meta = json.load(open(os.path.join(uo.ready_dir(), "meta.json")))
meta["at"] = time.time() - uo.EXPIRE_S - 1
json.dump(meta, open(os.path.join(uo.ready_dir(), "meta.json"), "w"))
uo.sweep()
check("an expired file is swept", os.listdir(uo.offline_dir()) == [])
try:
    uo.install(info["id"], False, CFG)
    check("an expired file cannot be installed", False, "installed")
except uo.OfflineError as e:
    check("an expired file cannot be installed", "no longer waiting" in str(e), str(e))
info = upload(offline(), CFG)
meta = json.load(open(os.path.join(uo.ready_dir(), "meta.json")))
meta["boot_id"] = "another-boot"
json.dump(meta, open(os.path.join(uo.ready_dir(), "meta.json"), "w"))
uo.sweep()
check("a file from before a reboot is swept", os.listdir(uo.offline_dir()) == [])
info = upload(offline(), CFG)
uo.install(info["id"], False, CFG)
uo.sweep()
check("a file a job is about to take is kept", os.path.isdir(uo.ready_dir()))
upd._write_json(STATUS, {"phase": "rolled_back", "started": int(time.time())})
uo.sweep()
check("...and swept once that job has ended", os.listdir(uo.offline_dir()) == [])
os.remove(JOB)
os.remove(STATUS)
open(os.path.join(uo.offline_dir(), "upload.part"), "wb").write(b"half")
uo.sweep()
check("a half upload is swept", os.listdir(uo.offline_dir()) == [])
uo.begin(10)
try:
    uo.begin(10)
    check("one upload at a time", False, "two claimed")
except uo.OfflineError as e:
    check("one upload at a time", "being uploaded" in str(e), str(e))
uo.end()

# -- 5. the routes -------------------------------------------------------------------
rsrc = open(os.path.join(ROOT, "core", "routes", "update_local.py"), encoding="utf-8").read()
usrc = open(os.path.join(ROOT, "core", "services", "update_offline.py"), encoding="utf-8").read()
for fn in ("upload", "install", "cancel"):
    body = rsrc.split(f"def {fn}(", 1)[1].split("\n@router", 1)[0]
    check(f"route {fn} goes through the update gate first",
          body.split("\n")[1].strip().startswith("channel = _box_channel(request")
          or body.split("\n")[1].strip().startswith("_box_channel(request"), body[:200])
check("the gate is update._gate on the box's own channel",
      "return _gate(request, action, update_service.CHANNEL_DEV" in rsrc)
up = rsrc.split("def upload(", 1)[1].split("\n@router", 1)[0]
check("the upload streams the raw body", "request.stream()" in up and "UploadFile" not in rsrc)
check("...capped while it arrives (received() holds the cap)",
      "update_offline.received(size, len(chunk)" in up and "MAX_UPLOAD" in
      usrc.split("def received(", 1)[1].split("\ndef ", 1)[0])
check("...and never reads a filename", "filename" not in rsrc)
for f in ("core/routes/update_local.py", "core/services/update_offline.py",
          "core/services/offline_file.py", "frontend/src/components/UpdatePanel.jsx"):
    check(f"no em dash in {f}", "—" not in open(os.path.join(ROOT, f), encoding="utf-8").read())

# -- 6. Pipi's review -----------------------------------------------------------------
# F1: Pipi's case, one header claiming 64 MB in a file of a few hundred KB.
import gzip                                                     # noqa: E402
import tracemalloc                                              # noqa: E402


def bomb(kind, claimed=64 << 20):
    path = tempfile.mktemp(suffix=".tar.gz")
    with gzip.open(path, "wb", compresslevel=1) as g:
        info = tarfile.TarInfo("x")
        info.type, info.size = kind, claimed
        g.write(info.tobuf(tarfile.PAX_FORMAT if kind == tarfile.XHDTYPE
                           else tarfile.GNU_FORMAT))
        zeros, left = b"\0" * (1 << 20), claimed
        while left > 0:
            g.write(zeros[:min(left, len(zeros))])
            left -= len(zeros)
        g.write(b"\0" * 1024)
    return path


for label, kind in (("pax", tarfile.XHDTYPE), ("GNU long name", tarfile.GNUTYPE_LONGNAME),
                    ("GNU sparse", tarfile.GNUTYPE_SPARSE)):
    path = bomb(kind)
    check(f"the {label} bomb is a small upload", os.path.getsize(path) < (1 << 20))
    tracemalloc.start()
    try:
        of.verify_file(path, tempfile.mkdtemp(), PUB, upd.MANIFEST_APP)
        check(f"a {label} header claiming 64 MB is refused", False, "accepted")
    except of.OfflineError as e:
        check(f"a {label} header claiming 64 MB is refused in plain words",
              "more than an update" in str(e), str(e))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    check(f"...with memory flat (peak {peak >> 20} MB)", peak < (8 << 20), peak)
cap = of._Capped(io.BytesIO(b"x" * 100), 10)
try:
    cap.read(11)
    check("the capped reader refuses a read past its budget", False, "read")
except of.OfflineError as e:
    check("the capped reader refuses a read past its budget", "larger inside" in str(e))
check("...seek and tell go through inside it", cap.seek(5) == 5 and cap.tell() == 5
      and cap.read(5) == b"xxxxx")
try:
    cap.seek(11)
    check("the capped reader refuses a seek past its budget", False, "sought")
except of.OfflineError:
    check("the capped reader refuses a seek past its budget", True)
check("the laptop tool checks a file with this same function",
      "offline_file.verify_file(" in open(os.path.join(ROOT, "scripts", "dev", "fetch_offline.py"),
                                          encoding="utf-8").read())

# F2: no Content-Length is budgeted as the cap; the store is re-checked while streaming.
asked = []


class _Vfs:
    def __init__(self, free):
        self.f_bavail, self.f_frsize = free, 1


real_statvfs = getattr(os, "statvfs", None)
os.statvfs = lambda path: asked.append(path) or _Vfs(2 * uo.MAX_UPLOAD)
try:
    uo.begin(0)
    check("no Content-Length is budgeted as MAX_UPLOAD (refused on a store with "
          "room for less than twice that)", False, "claimed")
except uo.OfflineError as e:
    check("no Content-Length is budgeted as MAX_UPLOAD (refused on a store with "
          "room for less than twice that)", "not enough free space" in str(e), str(e))
check("...and the slot is given back", uo.begin(10) and uo.end() is None)
os.statvfs = lambda path: asked.append(path) or _Vfs(1 << 40)
started = time.monotonic()
part = uo.begin(0)
asked.clear()
uo.received(1 << 20, 1 << 20, 0, started)
check("a chunk inside the first 64 MB does not re-check the store", asked == [], asked)
uo.received(uo.ROOM_EVERY + 5, 10, 0, started)
check("...crossing a 64 MB line does", len(asked) == 1, asked)
os.statvfs = lambda path: _Vfs(uo.MAX_UPLOAD)
try:
    uo.received(2 * uo.ROOM_EVERY + 5, 10, 0, started)
    check("a store that filled up while streaming refuses the upload", False, "went on")
except uo.OfflineError as e:
    check("a store that filled up while streaming refuses the upload",
          "not enough free space" in str(e), str(e))
if real_statvfs:
    os.statvfs = real_statvfs
else:
    del os.statvfs
try:
    uo.received(uo.MAX_UPLOAD + 1, 1, 0, started)
    check("the running total is still capped", False, "went on")
except uo.OfflineError as e:
    check("the running total is still capped", "too large" in str(e), str(e))

# F3: the slot has a deadline, a per-chunk idle limit, and Cancel frees it.
try:
    uo.received(5, 5, 0, started - uo.DEADLINE_S - 1)
    check("an upload past the deadline is stopped", False, "went on")
except uo.OfflineError as e:
    check("an upload past the deadline is stopped", "took too long" in str(e), str(e))
check("Cancel during an upload answers yes", uo.discard() is True)
try:
    uo.received(5, 5, 0, started)
    check("...and the upload stops at its next chunk", False, "went on")
except uo.OfflineError as e:
    check("...and the upload stops at its next chunk", "cancelled" in str(e), str(e))
uo.end()
check("...which frees the slot", uo.begin(10) and uo.end() is None)
check("a new upload starts with the cancel cleared", (uo.begin(10), uo.received(1, 1, 10, started),
                                                      uo.end())[1] is None)
check("the deadline and idle limit are minutes, not hours",
      uo.DEADLINE_S <= 30 * 60 and uo.IDLE_S <= 120, (uo.DEADLINE_S, uo.IDLE_S))
up = rsrc.split("def upload(", 1)[1].split("\n@router", 1)[0]
check("the route waits at most IDLE_S for each chunk",
      "asyncio.wait_for(" in up and "update_offline.IDLE_S" in up, up)
check("...and runs every chunk past received()", "update_offline.received(size, len(chunk)" in up)
check("...and writes each chunk off the event loop", "asyncio.to_thread(out.write, chunk)" in up)

# F4: an upload or a sweep in Install's "starting" window leaves ready/ alone.
info = upload(offline(), CFG)
held = []
upd._start_unit = lambda: held.append(uo._busy.locked())
uo.install(info["id"], False, CFG)
check("Install holds the slot across the hand-off", held == [True], held)
check("...and gives it back", not uo._busy.locked())
check("the status is 'starting' with no applier pid yet",
      upd.status()["phase"] == "starting" and not upd.job_running())
try:
    uo.begin(10)
    uo.end()                     # a begin() that wrongly succeeded must not hold the lock
    check("an upload in the starting window is refused", False, "claimed")
except uo.OfflineError as e:
    check("an upload in the starting window is refused", "already running" in str(e), str(e))
check("...and ready/ is untouched", os.path.isdir(uo.ready_dir()))
check("Cancel in the starting window is refused too",
      uo.discard() is False and os.path.isdir(uo.ready_dir()))
# The gap between the meta's installing and the "starting" status: the last
# job's ended phase is still on disk. (Bounded: a broken _handing_off left
# the lock held above and this hung for 240 s instead of failing, Yaya M4.)
upd._write_json(STATUS, {"phase": "done", "started": int(time.time()) - 5})
check("the slot is free to take for the gap test", uo._busy.acquire(timeout=5))
uo.sweep()
uo.end()
check("sweep leaves ready/ alone while Install holds the slot", os.path.isdir(uo.ready_dir()))
st = json.load(open(STATUS))
st["started"] = int(time.time()) - uo.EXPIRE_S - 1
upd._write_json(STATUS, {**st, "phase": "starting"})
uo.sweep()
check("a hand-off that never arrived is given up on after EXPIRE_S",
      os.listdir(uo.offline_dir()) == [])
os.remove(JOB)
os.remove(STATUS)


# A unit that fails to start (sudo rule missing, systemd refusing) wedged
# the slot for EXPIRE_S: upload "already running", Cancel a no-op, Install
# "upload it again" (Yaya bug 2, 2026-10-05). Now it frees the slot at once.
def _no_sudo():
    raise upd.UpdateError("could not start the updater - sudo: a password is required")


info = upload(offline(), CFG)
upd._start_unit = _no_sudo
try:
    uo.install(info["id"], False, CFG)
    check("a unit that fails to start raises", False, "went on")
except upd.UpdateError as e:
    check("a unit that fails to start raises", "could not start" in str(e), str(e))
check("...the status says failed, with the reason, so nothing waits on it",
      upd.status()["phase"] == "failed" and "password" in upd.status()["error"], upd.status())
check("...the file is no longer marked installing", not uo._meta().get("installing"), uo._meta())
check("...Cancel works at once", uo.discard() is True and os.listdir(uo.offline_dir()) == [])
info = upload(offline(), CFG)
check("...and so does a new upload", info.get("version") == "1.0.16", info)
upd._start_unit = lambda: calls.__setitem__("unit", calls["unit"] + 1)
n = calls["unit"]
check("...and Install of the new file", uo.install(info["id"], False, CFG)["phase"] == "starting"
      and calls["unit"] == n + 1, calls)
uo._clear()
os.remove(JOB)
os.remove(STATUS)

# F5: a reinstall's prune keeps the genuinely older release.
PR = tempfile.mkdtemp(prefix="smoke_otaoffline_prune_")
for n in ("v1.0.13", "v1.0.14", "v1.0.15", "v1.0.15-r1"):
    os.makedirs(os.path.join(PR, n))
j = type("J", (), {"releases": PR})()
real_prune(j, keep={os.path.join(PR, "v1.0.15-r1"), os.path.join(PR, "v1.0.15")})
check("a reinstall keeps the live, the previous AND the newest older release",
      sorted(os.listdir(PR)) == ["v1.0.14", "v1.0.15", "v1.0.15-r1"], os.listdir(PR))
os.makedirs(os.path.join(PR, "v1.0.16"))
real_prune(j, keep={os.path.join(PR, "v1.0.16"), os.path.join(PR, "v1.0.15-r1")})
check("a plain update still keeps exactly the two",
      sorted(os.listdir(PR)) == ["v1.0.15-r1", "v1.0.16"], os.listdir(PR))

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
