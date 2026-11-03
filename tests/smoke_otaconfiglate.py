# A setting saved WHILE an update builds must survive the flip.
#
# The gap (Boboiboy, 57cf6ff6): do_apply carried the box's config
# (*.local.toml, deploy.toml) into the new release BEFORE the venv build had
# finished settling and before the app snapshot, then flipped. The hub keeps
# serving all that time, so a sign-in, a password change or a setting saved
# in between landed in the OUTGOING release's config and was left behind at
# the flip. Now carry_config runs again as the last step before the flip, and
# each file is replaced atomically.
#
# Pinned, by running the real do_apply with the slow and the root-only steps
# stubbed:
#   1. a config write made during the snapshot (after the first carry) is in
#      the new release when the flip happens
#   2. a brand-new .local.toml written in that window comes across too
#   3. no temp file from the atomic copy is left behind
#   4. source pin: do_apply's last carry_config sits after snapshot_apps and
#      before the flip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


spec = importlib.util.spec_from_file_location(
    "_smoke_applier_cfglate", os.path.join(ROOT, "scripts", "apply_update.py"))
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)

BASE = tempfile.mkdtemp(prefix="smoke_otaconfiglate_")
work = os.path.join(BASE, "update")
releases = os.path.join(BASE, "releases")
old = os.path.join(releases, "v1.0.0")
os.makedirs(os.path.join(old, "config"))
os.makedirs(work)
with open(os.path.join(old, "config", "users.local.toml"), "w", encoding="utf-8") as f:
    f.write('[admin]\npassword_hash = "before"\n')

# The bundle: a tiny tree with the product's stock config.
bundle_bytes = io.BytesIO()
with tarfile.open(fileobj=bundle_bytes, mode="w:gz") as tf:
    for name, data in (("VERSION", b"1.0.1\n"),
                       ("config/auth.toml", b"[session]\nidle_timeout_minutes = 60\n")):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
bundle_bytes = bundle_bytes.getvalue()
SHA = hashlib.sha256(bundle_bytes).hexdigest()

PREFIX = "https://api.github.com/repos/o/r/releases/assets/"
with open(os.path.join(work, "job.json"), "w", encoding="utf-8") as f:
    json.dump({"mode": "apply", "version": "1.0.1", "current": "1.0.0",
               "channel": "stable", "token_key": "token", "owner": "o", "repo": "r",
               "config_dir": os.path.join(old, "config"),
               "store_dir": os.path.join(BASE, "store"), "releases_dir": releases,
               "live_link": os.path.join(BASE, "live"), "manifest_app": "x",
               "bundle_asset": "bundle.tar.gz", "manifest_asset": "manifest.json",
               "sig_asset": "manifest.sig", "hub_port": 5100,
               "assets": {n: {"url": PREFIX + n, "size": 1}
                          for n in ("bundle.tar.gz", "manifest.json", "manifest.sig")}}, f)

seen = {}


def fake_bundle(url, tok, dest, sha, size, say, heal=None):
    with open(dest, "wb") as f:
        f.write(bundle_bytes)
    return dest


def late_write(job, dest):
    # The hub, still serving the outgoing release, saves a sign-in and a
    # setting while the update is snapshotting the apps.
    with open(os.path.join(old, "config", "users.local.toml"), "w", encoding="utf-8") as f:
        f.write('[admin]\npassword_hash = "changed-during-update"\n')
    with open(os.path.join(old, "config", "update.local.toml"), "w", encoding="utf-8") as f:
        f.write("auto_check = true\n")
    return []


def fake_flip(link, target):
    if "users" in seen:
        return                                      # (a rollback flip)
    cfg = os.path.join(target, "config")
    seen["users"] = open(os.path.join(cfg, "users.local.toml"), encoding="utf-8").read()
    seen["update"] = (open(os.path.join(cfg, "update.local.toml"), encoding="utf-8").read()
                      if os.path.exists(os.path.join(cfg, "update.local.toml")) else "")
    seen["tmp"] = [n for n in os.listdir(cfg) if n.endswith(".tmp")]


au.read_token = lambda config_dir, key="token": "ghp_fake"
au.fetch_healing = lambda job, fn, *a: b"{}"
au.verify = lambda m, s, p, a: {"version": "1.0.1", "unpacked_size": 100,
                                "bundle": {"sha256": SHA, "size": len(bundle_bytes)}}
au.free_bytes = lambda path: 1 << 40
au.fetch_bundle = fake_bundle
au.build_venv = lambda job, release: None
au.carry_runtime = lambda job, src, dest: []
au.previous_release = lambda job: old
au.snapshot_apps = late_write
au.running_systems = lambda job, since=0: []
au.running_masters = lambda job: {}
au.flip = fake_flip
for name in ("restart_stack", "sync_apps", "revive_masters", "verify_masters",
             "gate_b", "prune_releases"):
    setattr(au, name, lambda *a, **k: None)
au.gate_a = lambda job: True
au.log = lambda msg: None

rc = au.do_apply(au.Job(work))
check("do_apply finished", rc == 0, rc)
check("a password changed during the update is in the new release at the flip",
      "changed-during-update" in seen.get("users", ""), seen.get("users"))
check("a .local.toml first written during the update came across too",
      "auto_check = true" in seen.get("update", ""), seen.get("update"))
check("no temp file from the atomic copy is left in the new config",
      seen.get("tmp") == [], seen.get("tmp"))

src = open(os.path.join(ROOT, "scripts", "apply_update.py"), encoding="utf-8").read()
body = src.split("def do_apply", 1)[1].split("\ndef ", 1)[0]
last = body.rindex("carry_config(")
check("the last carry_config is after the snapshot and before the flip",
      body.index("snapshot_apps(") < last < body.index("flip(job.link, target)"))
carry = src.split("def carry_config", 1)[1].split("\ndef ", 1)[0]
check("carry_config replaces each file atomically",
      "os.replace(tmp, d)" in carry and "shutil.copy2(s, d)" not in carry)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
