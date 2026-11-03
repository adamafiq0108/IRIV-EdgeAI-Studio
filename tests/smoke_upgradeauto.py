# The one-time root steps reach a box with the update, not with a terminal.
#
# WHAT WENT WRONG. scripts/upgrade_steps.sh did root work outside the
# release tree by hand, once; customers updated over the air from 1.0.34 to
# 1.0.37 and nobody ran it, so their boxes kept the old state (product owner,
# 2026-10-11: "add `sudo bash scripts/upgrade_steps.sh` into the code so it
# also runs for users who did not update to v1.0.36").
#
# WHAT IS PINNED.
#   1. The steps an unattended update may do are in the signed list
#      (root_items.json "upgrade") and run by the root step every update
#      already has (root_steps.upgrade, before the helpers): no new sudo rule,
#      no shell script run as root from the account's tree.
#   2. The gate: a box with no record (any box from before 1.0.37, 1.0.34 or
#      1.0.36 alike) runs them; a box that recorded the rev does not, until
#      the record is gone or the rev moves.
#   3. Idempotent: a box that already has every piece is changed in nothing.
#   4. Failure: offline apt, a hub port not saved yet, a step that cannot
#      write -- said, the rest still done, no record (the next update tries
#      again), never an exception.
#   5. Not there: the appliance defaults and --harden (operator choices).
#   6. install.sh records them as done on a fresh box (root_steps --tree).
#   7. apt is never started where it could be killed half-way: not with less
#      than APT_INSTALL_MIN_S of the budget left, not while dpkg --audit says
#      an earlier install is unfinished; --no-install-recommends (Pipi S1-L3)
#   8. root reads the hub's saved port only from a regular file owned by the
#      hub's User= or root, never through a symlink (S1-I1); a tampered or
#      unpinned drop-in in the release is refused with nothing written (S1-I2)
#   9. the video gateway's config (Pipi H1, hold lifted for 1.0.37): pinned,
#      written only where it differs, the box's old copy kept once as
#      .pre-<rev>, the gateway restarted and judged by its loopback API; one
#      that does not come back gets the old text back and a second restart,
#      said, no record (retried), never an exception
#
#     python tests/smoke_upgradeauto.py
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "dev"))

import root_steps as rs                                      # noqa: E402

if not hasattr(os, "geteuid"):                               # Windows dev box
    os.geteuid = lambda: 1000
    os.O_NONBLOCK = 0                                        # load() opens with it

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8", newline="").read()


ITEMS = json.loads(read("scripts", "root_items.json"))
UP = ITEMS.get("upgrade") or {}
INSTALL = read("scripts", "install.sh")
RS = read("scripts", "root_steps.py")

# -- 1, 5. the list -------------------------------------------------------------------
check("root_items.json has an upgrade table with a rev", rs.semver(UP.get("rev")) >= (1, 0, 37), UP)
apt = set((re.search(r"APT_PACKAGES=\((.*?)\n\)", INSTALL, re.S).group(1)).split())
check("...its packages are ones a fresh install gets too (APT_PACKAGES)",
      set(UP.get("packages", [])) <= apt, set(UP.get("packages", [])) - apt)
check("...nftables first-class: the nftables units need nft", "nftables" in UP.get("packages", []))
check("...the link-local drop-in is a file in the release",
      os.path.isfile(os.path.join(ROOT, *UP.get("linklocal", "x").split("/"))))
check("9. the gateway's mediamtx.yml is a file in the release, delivered by the root step",
      UP.get("mediamtx") == "scripts/mediamtx/mediamtx.yml"
      and os.path.isfile(os.path.join(ROOT, *UP["mediamtx"].split("/"))))
check("...nor the appliance defaults or sshd/sudo hardening",
      not re.search(r"wayvnc|rpcbind|cmdline\.txt|sshd|PermitRootLogin", RS))
check("1. the OTA runs them through the root step it already has, before the list",
      "        upgrade(items, files)\n        apply(items, files)" in RS
      and "        upgrade(items, files, dry=dry)\n        apply(items, files, dry=dry)" in RS)
check("...and never a shell script as root", "upgrade_steps.sh\"" not in RS and '"bash"' not in RS)
check("6. install.sh runs the root step on its checkout (records the steps as done)",
      'python3 -I "${SRC_DIR}/scripts/root_steps.py" --tree "$SRC_DIR"' in INSTALL)

import publish_release as pr                                 # noqa: E402

tmp = tempfile.mkdtemp(prefix="upgradeauto_")
bundle = os.path.join(tmp, "b.tar.gz")
with tarfile.open(bundle, "w:gz") as tar:
    for rel in ["scripts/root_items.json", UP["linklocal"], UP["mediamtx"]] + [
            e["src"] for e in ITEMS["helpers"] + ITEMS["nft"]]:
        tar.add(os.path.join(ROOT, *rel.split("/")), arcname=rel)
pins = pr.root_pins(pr.pathlib.Path(bundle))
check("the signed manifest pins the drop-in (root installs only pinned bytes)",
      pins.get(UP["linklocal"]) == hashlib.sha256(
          open(os.path.join(ROOT, *UP["linklocal"].split("/")), "rb").read()).hexdigest())
check("...and the gateway's yml, byte for byte",
      pins.get(UP["mediamtx"]) == hashlib.sha256(
          open(os.path.join(ROOT, *UP["mediamtx"].split("/")), "rb").read()).hexdigest())
check("...the publisher and the box agree on which upgrade keys are files",
      tuple(pr.UPGRADE_FILES) == tuple(rs.UPGRADE_FILES) and set(rs.UPGRADE_FILES) <= set(UP))

# -- 2..4. root's side, in a sandbox --------------------------------------------------------
box = os.path.join(tmp, "box")
live = os.path.join(box, "live")
for d in ("units", "var", "nm", "etc", os.path.join("live", "config")):
    os.makedirs(os.path.join(box, d), exist_ok=True)
rs.UNIT_DIR = os.path.join(box, "units")
rs.HUB_UNIT = os.path.join(rs.UNIT_DIR, "irivai-hub.service")
rs.STATE = os.path.join(box, "var", "root-steps.json")
rs.NM_CONF_DIR = os.path.join(box, "nm")
rs.MTX_DIR = os.path.join(box, "mediamtx")                   # absent: "not installed here"
CONF = {"LIVE_LINK": live}
rs._conf = lambda key, default: CONF.get(key, default)
MARK = rs._upgrade_mark()
APPLY = os.path.join(rs.UNIT_DIR, "irivai-apply.service")
DROPIN = os.path.join(rs.NM_CONF_DIR, rs.LINKLOCAL)
HUB_OLD = ("[Service]\nUser=cytron\nExecStart=/opt/irivai-systor/.venv/bin/python hub_server.py"
           " --port 5200\nKillMode=process\n")
APPLY_OLD = "[Service]\nType=oneshot\nUser=cytron\nExecStart=/x/apply_update.py\n"
files = {UP[k]: open(os.path.join(ROOT, *UP[k].split("/")), "rb").read()
         for k in ("linklocal", "mediamtx")}

fake = {"installed": set(), "online": True, "apt": [], "sysctl": [], "nm": "1.52.0", "audit": ""}
rs._which = lambda name: ("/usr/bin/" + name if name in ("dpkg", "dpkg-query", "apt-get", "nmcli")
                          else None)
rs._user_uid = lambda name: os.geteuid() if name == "cytron" else None


def fake_run(argv, timeout=60):
    if argv[0].endswith("/dpkg") and argv[1:] == ["--audit"]:
        fake["audits"] = fake.get("audits", 0) + 1
        return subprocess.CompletedProcess(argv, 0, fake["audit"], "")
    if argv[0].endswith("dpkg-query"):
        ok = argv[-1] in fake["installed"]
        return subprocess.CompletedProcess(argv, 0 if ok else 1,
                                           "install ok installed" if ok else "", "")
    if argv[0].endswith("nmcli") and argv[1] == "-v":
        return subprocess.CompletedProcess(argv, 0, f"nmcli tool, version {fake['nm']}\n", "")
    fake["sysctl"].append(argv)
    return subprocess.CompletedProcess(argv, 0, "", "")


def fake_apt(argv, deadline, need=5):
    fake["apt"].append(argv)
    if not fake["online"]:
        return False
    if "install" in argv:
        fake["installed"].add(argv[-1])
    return True


rs._run, rs._apt = fake_run, fake_apt


def box_from(version, mark=None, hub_saved=True):
    """A box as an older release left it."""
    for p in (MARK, DROPIN, rs.STATE):
        if os.path.exists(p):
            os.remove(p)
    open(rs.HUB_UNIT, "w").write(HUB_OLD)
    open(APPLY, "w").write(APPLY_OLD)
    open(os.path.join(live, "config", "systems.local.toml"), "w").write(
        "[hub]\nport = 5200\n" if hub_saved else "[masters]\n")
    json.dump({"version": version, "app": "irivai-platform-release"}, open(rs.STATE, "w"))
    if mark:
        json.dump({"rev": mark}, open(MARK, "w"))
    fake.update(installed={"avahi-utils"}, online=True, apt=[], sysctl=[], audit="", audits=0)


def go(dry=False):
    rs.OUT.clear()
    rs.upgrade(ITEMS, files, dry=dry)
    return list(rs.OUT)


def snapshot():
    return {p: open(p, "rb").read() for p in (rs.HUB_UNIT, APPLY, DROPIN, MARK) if os.path.exists(p)}


box_from("1.0.34")
out = go()
check("2. from 1.0.34 (no record): the steps run",
      not any(k == "refused" for k, _ in out) and json.load(open(MARK)) == {"rev": UP["rev"]}, out)
check("...missing packages installed, nftables before anything needs nft, avahi-utils left",
      {a[-1] for a in fake["apt"] if "install" in a} == {"nftables", "iputils-arping"}, fake["apt"])
check("7. ...after one dpkg --audit, without recommends",
      fake["audits"] == 1 and all("--no-install-recommends" in a for a in fake["apt"] if "install" in a),
      (fake["audits"], fake["apt"]))
check("...the baked --port dropped from the hub unit, daemon-reload, no hub restart",
      " --port" not in open(rs.HUB_UNIT).read() and "hub_server.py\n" in open(rs.HUB_UNIT).read()
      and ["systemctl", "daemon-reload"] in fake["sysctl"]
      and not [a for a in fake["sysctl"] if "restart" in a], fake["sysctl"])
check("...the link-local drop-in written byte for byte, NetworkManager told",
      open(DROPIN, "rb").read() == files[UP["linklocal"]]
      and ["/usr/bin/nmcli", "general", "reload", "conf"] in fake["sysctl"])
check("...KillMode=process on the apply unit, written, not reloaded mid-update",
      "Type=oneshot\nKillMode=process\n" in open(APPLY).read())

before, fake["apt"], fake["sysctl"] = snapshot(), [], []
out = go()
check("3. the second run changes nothing and asks apt for nothing",
      snapshot() == before and not fake["apt"] and not fake["sysctl"]
      and [k for k, _ in out] == ["skipped"], out)

box_from("1.0.36", mark=UP["rev"])
out = go()
check("2. from 1.0.36 with the steps recorded: not run again",
      [k for k, _ in out] == ["skipped"] and not fake["apt"]
      and " --port 5200" in open(rs.HUB_UNIT).read(), out)
box_from("1.0.37", mark=UP["rev"])
check("...nor from 1.0.37", [k for k, _ in go()] == ["skipped"] and not fake["apt"])

# A box that already has every piece (upgrade_steps.sh run by hand on 1.0.36,
# which wrote no record): runs, finds nothing to do, records.
box_from("1.0.36")
go()
os.remove(MARK)
before, fake["apt"], fake["sysctl"] = snapshot(), [], []
out = go()
check("2. record missing: run again, but a box that has it all is changed in nothing",
      {k: v for k, v in snapshot().items() if k != MARK} == {k: v for k, v in before.items()}
      and not fake["apt"] and not fake["sysctl"] and os.path.exists(MARK)
      and [k for k, _ in out].count("done") == 1, out)
json.dump({"rev": "1.0.36"}, open(MARK, "w"))
check("...an older rev in the record runs them too",
      any("recorded" in t for _, t in go()) and json.load(open(MARK))["rev"] == UP["rev"])

# -- 4. failure ---------------------------------------------------------------------------
box_from("1.0.34")
fake["online"] = False
out = go()
check("4. offline: the package is said, the other steps still done, NO record",
      not os.path.exists(MARK) and " --port" not in open(rs.HUB_UNIT).read()
      and os.path.exists(DROPIN) and any("could not install" in t for _, t in out)
      and not any(k == "refused" for k, _ in out), out)
check("...after one apt update for a stale list, inside the budget",
      sum(1 for a in fake["apt"] if "update" in a) == 1, fake["apt"])
fake.update(online=True, apt=[])
go()
check("...and the next update installs it and records",
      os.path.exists(MARK) and {"nftables", "iputils-arping"} <= fake["installed"])

box_from("1.0.34")
fake["audit"] = "The following packages are only half configured:\n nftables\n"
out = go()
check("7. dpkg left half-done: apt not started, said, the other steps done, NO record",
      not fake["apt"] and not os.path.exists(MARK) and os.path.exists(DROPIN)
      and any("dpkg --configure -a" in t for _, t in out)
      and not any(k == "refused" for k, _ in out), out)

box_from("1.0.34")
budget = rs.APT_BUDGET_S
rs.APT_BUDGET_S = rs.APT_INSTALL_MIN_S - 1
out = go()
rs.APT_BUDGET_S = budget
check("7. too little of the budget left: no install started, said, NO record",
      not fake["apt"] and not os.path.exists(MARK)
      and sum("not started, too little time left" in t for _, t in out) == 2, out)
check("...the floor leaves room for an install inside the budget",
      30 <= rs.APT_INSTALL_MIN_S < rs.APT_BUDGET_S <= 300)

box_from("1.0.34", hub_saved=False)
out = go()
check("4. a hub that has not saved its port yet keeps the --port (no move to 5100), no record",
      " --port 5200" in open(rs.HUB_UNIT).read() and not os.path.exists(MARK), out)

# 8. The saved port is read by root from a path the account can write.
SAVED = os.path.join(live, "config", "systems.local.toml")
if os.name == "posix" and os.geteuid() != 0:
    box_from("1.0.34")
    rs._user_uid = lambda name: 4321                         # not the file's owner
    out = go()
    rs._user_uid = lambda name: os.geteuid() if name == "cytron" else None
    check("8. a saved-port file owned by someone else is not believed",
          " --port 5200" in open(rs.HUB_UNIT).read() and not os.path.exists(MARK), out)
    box_from("1.0.34")
    os.rename(SAVED, SAVED + ".real")
    os.symlink(SAVED + ".real", SAVED)
    out = go()
    os.remove(SAVED)
    os.rename(SAVED + ".real", SAVED)
    check("8. ...nor one reached through a symlink",
          " --port 5200" in open(rs.HUB_UNIT).read() and not os.path.exists(MARK), out)
else:
    print("(the saved-port owner/symlink checks need POSIX as a normal user; skipped here)")

# 8. A drop-in in the tree that is not the signed one is refused at load().
bad_tree = os.path.join(tmp, "tampered")
with tarfile.open(bundle) as tar:
    tar.extractall(bad_tree, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
nm_path = os.path.join(bad_tree, *UP["linklocal"].split("/"))
nm_good = open(nm_path, "rb").read()
open(nm_path, "wb").write(nm_good[:-1] + bytes([nm_good[-1] ^ 1]))
for label, use_pins, want in (("a tampered drop-in", pins, "does not match the signed manifest"),
                              ("an unpinned drop-in", {k: v for k, v in pins.items()
                                                       if k != UP["linklocal"]}, "is not pinned")):
    try:
        rs.load(bad_tree, use_pins)
        why = ""
    except rs.Refused as exc:
        why = str(exc)
    check(f"8. {label} is refused at load (nothing reaches upgrade())", want in why, why)
open(nm_path, "wb").write(nm_good)

box_from("1.0.34")
os.rmdir(rs.NM_CONF_DIR)
open(rs.NM_CONF_DIR, "w").write("not a directory")
try:
    out = go()
    raised = None
except Exception as exc:                                     # noqa: BLE001
    out, raised = list(rs.OUT), exc
check("4. a step that cannot write is refused, said, and the others still run",
      raised is None and any(k == "refused" and "linklocal" in t for k, t in out)
      and "KillMode=process" in open(APPLY).read() and not os.path.exists(MARK), (raised, out))
os.remove(rs.NM_CONF_DIR)
os.makedirs(rs.NM_CONF_DIR)

box_from("1.0.34")
fake["nm"] = "1.42.4"
out = go()
check("NetworkManager older than 1.52: no drop-in, and that counts as finished",
      not os.path.exists(DROPIN) and os.path.exists(MARK), out)
fake["nm"] = "1.52.0"

box_from("1.0.34")
before = snapshot()
out = go(dry=True)
check("--dry-run: nothing written, no apt, no record",
      snapshot() == before and not fake["apt"] and not os.path.exists(MARK), out)

rs.OUT.clear()
rs.upgrade({"upgrade": {"rev": "1.0.37", "packages": ["ok", "rm -rf /"]}}, {})
check("a package name that is not a package name is refused, never passed to apt",
      any(k == "refused" for k, _ in rs.OUT) and not [a for a in fake["apt"] if "rm -rf /" in a])

# -- 9. the video gateway's config ------------------------------------------------------------
MTX = os.path.join(rs.MTX_DIR, rs.MTX_CONF)
BACKUP = MTX + ".pre-" + UP["rev"]
NEW_YML = files[UP["mediamtx"]]
OLD_YML = b"rtsp: yes\nwebrtc: yes\n# the box's own copy, MoQ on by default\n"
# The gateway: running or not, and which texts it starts on (answers its API).
gw = {"active": True, "takes": {OLD_YML, NEW_YML}, "restarts": 0}
rs._mtx_active = lambda: gw["active"]
rs._mtx_answers = lambda: open(MTX, "rb").read() in gw["takes"]
rs.MTX_WAIT_S = 1
real_restart = rs._mtx_restart


def counted_restart():
    gw["restarts"] += 1
    return real_restart()


rs._mtx_restart = counted_restart


def gateway_box(old=OLD_YML, active=True, takes=(OLD_YML, NEW_YML), backup=None):
    box_from("1.0.36")
    shutil.rmtree(rs.MTX_DIR, ignore_errors=True)
    os.makedirs(rs.MTX_DIR)
    if old is not None:
        open(MTX, "wb").write(old)
    if backup is not None:
        open(BACKUP, "wb").write(backup)
    gw.update(active=active, takes=set(takes), restarts=0)


gateway_box()
out = go()
check("9. a box on the old yml gets the pinned one, the gateway restarted once, recorded",
      open(MTX, "rb").read() == NEW_YML and gw["restarts"] == 1
      and ["systemctl", "restart", rs.MTX_UNIT] in fake["sysctl"]
      and os.path.exists(MARK) and any(k == "done" and "video gateway" in t for k, t in out), out)
check("...the box's own copy kept once beside it as .pre-<rev>",
      open(BACKUP, "rb").read() == OLD_YML, os.listdir(rs.MTX_DIR))
if os.name == "posix":
    check("...both 0644", all(os.stat(p).st_mode & 0o777 == 0o644 for p in (MTX, BACKUP)))
os.remove(MARK)
fake["sysctl"], gw["restarts"] = [], 0
out = go()
check("9. already current: not written, not restarted",
      gw["restarts"] == 0 and not fake["sysctl"] and os.path.exists(MARK)
      and any("video gateway: config already current" in t for _, t in out), out)

gateway_box(backup=b"an earlier attempt's backup\n")
go()
check("9. an existing .pre-<rev> is never overwritten (the FIRST copy is the box's own)",
      open(BACKUP, "rb").read() == b"an earlier attempt's backup\n")

gateway_box(takes=(OLD_YML,))
try:
    out, raised = go(), None
except Exception as exc:                                     # noqa: BLE001
    out, raised = list(rs.OUT), exc
check("9. a gateway that does not come back on the new yml: old text back, restarted again",
      raised is None and open(MTX, "rb").read() == OLD_YML and gw["restarts"] == 2, (raised, out))
check("...said as refused (and that it is running again), NO record, the other steps done",
      any(k == "refused" and "previous config is back and running" in t for k, t in out)
      and not os.path.exists(MARK) and os.path.exists(DROPIN), out)

gateway_box(takes=())
out = go()
check("9. ...and if even the old text does not start, that is said too",
      any(k == "refused" and "NOT running" in t for k, t in out) and open(MTX, "rb").read() == OLD_YML,
      out)

gateway_box(active=False)
out = go()
check("9. a gateway that is not running: the yml written, nothing started, recorded",
      open(MTX, "rb").read() == NEW_YML and gw["restarts"] == 0 and os.path.exists(MARK), out)

gateway_box(old=None)
out = go()
check("9. /etc/mediamtx with no yml in it: written, restarted, nothing to back up",
      open(MTX, "rb").read() == NEW_YML and not os.path.exists(BACKUP) and gw["restarts"] == 1, out)

gateway_box()
before = open(MTX, "rb").read()
out = go(dry=True)
check("9. --dry-run: the yml untouched, no restart, no backup",
      open(MTX, "rb").read() == before and gw["restarts"] == 0 and not os.path.exists(BACKUP), out)

shutil.rmtree(rs.MTX_DIR)
box_from("1.0.34")
out = go()
check("9. no gateway on the box (no /etc/mediamtx): skipped, and that counts as finished",
      not os.path.exists(rs.MTX_DIR) and os.path.exists(MARK)
      and any("video gateway: not installed here" in t for _, t in out), out)

yml_tree = os.path.join(tmp, "tampered_yml")
with tarfile.open(bundle) as tar:
    tar.extractall(yml_tree, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
open(os.path.join(yml_tree, *UP["mediamtx"].split("/")), "ab").write(b"\nmoq: yes\n")
try:
    rs.load(yml_tree, pins)
    why = ""
except rs.Refused as exc:
    why = str(exc)
check("9. a yml edited after signing (say, MoQ back on) is refused at load",
      "does not match the signed manifest" in why, why)

# -- the whole OTA request, signed, from a 1.0.34 box --------------------------------------------
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
except ImportError:
    Ed25519PrivateKey = None
if Ed25519PrivateKey is not None and os.name == "posix":
    key = Ed25519PrivateKey.generate()
    rs.PUBKEYS = (base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode(),)
    releases = os.path.join(box, "releases")
    tree = os.path.join(releases, "v1.0.37")
    with tarfile.open(bundle) as tar:
        tar.extractall(tree, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    CONF["RELEASES_DIR"] = releases
    rs.LIB, rs.SUDOERS_DIR = os.path.join(box, "lib"), os.path.join(box, "sudoers.d")
    for d in (rs.LIB, rs.SUDOERS_DIR):
        os.makedirs(d, exist_ok=True)
    rs.ETC = os.path.join(box, "etc", "irivai")
    rs._visudo_ok = lambda p: True
    box_from("1.0.34")
    man = json.dumps({"app": "irivai-platform-release", "version": "1.0.37", "root": pins}).encode()
    raw = json.dumps({"release": "v1.0.37", "manifest": base64.b64encode(man).decode(),
                      "sig": base64.b64encode(key.sign(man)).decode()}).encode()
    rs.OUT.clear()
    rc = rs.verified(raw, reexec=True)
    texts = [t for _, t in rs.OUT]
    first_helper = next((i for i, t in enumerate(texts) if t.startswith("helper ")), len(texts))
    check("the signed update from 1.0.34 runs the steps, then the helpers, and exits 0",
          rc == 0 and os.path.exists(MARK) and any("recorded" in t for t in texts[:first_helper])
          and json.load(open(rs.STATE))["version"] == "1.0.37", rs.OUT)
    rs.OUT.clear()
    rc = rs.verified(raw, reexec=True)
    check("...and the same update again does no one-time step", rc == 0 and any(
        "done on this box" in t for _, t in rs.OUT) and not any(
        k == "done" for k, _ in rs.OUT), rs.OUT)

    # The same signed request over a tree whose drop-in was changed after
    # signing, and one whose manifest leaves it unpinned: refused, nothing.
    nm_tree = os.path.join(tree, *UP["linklocal"].split("/"))
    nm_good = open(nm_tree, "rb").read()
    for label, edit_tree, root_pins in (
            ("a tampered", True, pins),
            ("an unpinned", False, {k: v for k, v in pins.items() if k != UP["linklocal"]})):
        box_from("1.0.34")
        if edit_tree:
            open(nm_tree, "wb").write(nm_good[:-1] + bytes([nm_good[-1] ^ 1]))
        man2 = json.dumps({"app": "irivai-platform-release", "version": "1.0.37",
                           "root": root_pins}).encode()
        raw2 = json.dumps({"release": "v1.0.37", "manifest": base64.b64encode(man2).decode(),
                           "sig": base64.b64encode(key.sign(man2)).decode()}).encode()
        rs.OUT.clear()
        rc = rs.verified(raw2, reexec=True)
        open(nm_tree, "wb").write(nm_good)
        check(f"8. {label} drop-in in a signed update: refused, nothing written, no record",
              rc == 1 and rs.refused() and not os.path.exists(DROPIN) and not os.path.exists(MARK)
              and not fake["apt"] and " --port 5200" in open(rs.HUB_UNIT).read(), rs.OUT)
else:
    print("(the signed end-to-end check needs POSIX and cryptography; skipped here)")

shutil.rmtree(tmp, ignore_errors=True)
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
