# The root-owned pieces a release needs reach a box with the update itself.
#
# WHAT WENT WRONG. Board .21 reached 1.0.26 over the air and still had only
# the helpers its install put in /usr/local/lib/irivai: an OTA swaps the tree
# as the service account, and only scripts/upgrade_steps.sh (root, by hand)
# installed the rest. So the storage card asked for "a one-time software
# update" on a box that had just been updated (Tok Aba, 2026-10-07).
#
# WHAT IS PINNED.
#   1. ONE list (scripts/root_items.json): upgrade_steps.sh and the OTA both
#      run scripts/root_steps.py on it, and install.sh writes the same text.
#   2. The release manifest pins sha256 of that list and every file it names,
#      read from the bundle; publish_release verify re-checks them.
#   3. Root installs only verified bytes: a bad signature, an unpinned or
#      tampered file installs NOTHING; a manifest older than the last applied
#      is skipped; a release path outside the releases dir is refused.
#   4. sudoers through visudo on a temp file, nothing left behind on refusal.
#   5. Idempotent: the second run changes nothing.
#   6. The applier asks for it before the flip, with the exact sudoers argv,
#      and never fails an update over it.
#   7. The storage cards name what is missing and what brings it.
#
#     python tests/smoke_rootsteps.py
import base64
import hashlib
import importlib.util
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
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "dev"))

import root_steps as rs                                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8", newline="").read()


ITEMS = json.loads(read("scripts", "root_items.json"))
INSTALL = read("scripts", "install.sh")
UP = read("scripts", "upgrade_steps.sh")
APPLY = read("scripts", "apply_update.py")
RS = read("scripts", "root_steps.py")
srcs = [e["src"] for e in ITEMS["helpers"] + ITEMS["nft"]] + [ITEMS["upgrade"][k] for k in ("linklocal", "mediamtx")]

# -- 1. one list -------------------------------------------------------------------
check("every file the list names is in the repo",
      all(os.path.isfile(os.path.join(ROOT, *s.split("/"))) for s in srcs),
      [s for s in srcs if not os.path.isfile(os.path.join(ROOT, *s.split("/")))])
check("the root step installs itself and is the list's first helper",
      ITEMS["helpers"][0] == {"src": "scripts/root_steps.py", "dest": "root-steps.py"})
for h in ITEMS["helpers"]:
    check(f"install.sh installs {h['dest']} from {h['src']}",
          f'install -m 755 "${{SRC_DIR}}/{h["src"]}" /usr/local/lib/irivai/{h["dest"]}' in INSTALL)
installed = set(re.findall(r'install -m 755 "\$\{SRC_DIR\}/scripts/\S+" /usr/local/lib/irivai/(\S+)', INSTALL))
check("...and every helper install.sh installs is on the list (or no update would refresh it)",
      installed == {h["dest"] for h in ITEMS["helpers"]},
      installed ^ {h["dest"] for h in ITEMS["helpers"]})
mr = re.search(r"^MASTER_RESTART='(.*)'$", INSTALL, re.M)
for s in ITEMS["sudoers"]:
    m = re.search(r"cat > /etc/sudoers.d/%s.tmp <<SUDOERS\n(.*?)\nSUDOERS" % s["name"], INSTALL, re.S)
    text = m and mr and m.group(1).replace("${MASTER_RESTART}", mr.group(1))
    check(f"sudoers {s['name']}: the same rule as install.sh",
          text == s["rule"].replace("{user}", "${SERVICE_USER}"), m and m.group(1)[:90])
upd = next(s["rule"] for s in ITEMS["sudoers"] if s["name"] == "irivai-update")
check("the master restart is one sudo regex, never a * (which matches spaces: any unit)",
      "/usr/bin/systemctl ^restart irivai-master@[a-z0-9_-]+[.]service$," in upd and "*" not in upd)
check("...and the applier only asks for units that regex grants",
      'systemctl("restart", f"irivai-master@{slug}.service")' in APPLY
      and 're.fullmatch(r"[a-z0-9_-]+\\.env", n)' in APPLY)
check("the query console helper runs isolated (-I) too",
      next(s["rule"] for s in ITEMS["sudoers"] if s["name"] == "irivai-db").endswith(
          "/usr/bin/python3 -I /usr/local/lib/irivai/db-console-role.py"))
written = set(re.findall(r"cat > /etc/sudoers.d/(irivai-[a-z0-9-]+).tmp <<SUDOERS", INSTALL))
check("...and every irivai sudoers file install.sh writes is on the list",
      written == {s["name"] for s in ITEMS["sudoers"]}, written ^ {s["name"] for s in ITEMS["sudoers"]})
for n in ITEMS["nft"]:
    m = re.search(r'write_unit /etc/systemd/system/%s.service "(.*?)"\n' % n["unit"], INSTALL, re.S)
    check(f"{n['unit']}.service: the same unit as install.sh",
          m and m.group(1) == "\n".join(n["unit_text"]))
check("install.sh remembers --allow-ap-forwarding where the updates look",
      "touch " + ITEMS["nft"][0]["optout"] in INSTALL and ITEMS["nft"][0]["unit"] == "irivai-ap-isolate")
check("upgrade_steps.sh runs the same step on the same list, by hand",
      'python3 -I "${HERE}/root_steps.py" "${ROOT_ARGS[@]}"' in UP and "ROOT_ARGS=(--tree \"$APP\")" in UP)
check("...and keeps no list of its own",
      "BOX_RULE=" not in UP and "for pair in" not in UP and "nft_unit" not in UP
      and "put_sudoers" not in UP)
check("...and its --allow-ap-forwarding is remembered for the updates",
      'act touch "$AP_OPTOUT"' in UP and "AP_OPTOUT=" + ITEMS["nft"][0]["optout"] in UP)
check("root_steps.py: LF, stdlib only, isolated interpreter in every sudo line",
      "\r" not in RS and not re.search(r"^\s*(import|from)\s+core", RS, re.M))
rule = next(s["rule"] for s in ITEMS["sudoers"] if s["name"] == "irivai-rootsteps")
check("the rootsteps rule is one exact command, no arguments allowed",
      rule == "{user} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/root-steps.py")
upd = read("config", "update.toml")
pub = re.search(r'^pubkey\s*=\s*"([^"]+)"', upd, re.M).group(1)
upd_py = read("core", "services", "update.py")
check("the compiled-in key is the OTA key (config and update.py)",
      pub in rs.PUBKEYS and re.search(r'PUBKEY_B64 = "%s"' % re.escape(pub), upd_py))

# -- 6. the applier ------------------------------------------------------------------
check("the applier asks before the flip, after the release is built",
      APPLY.index("    root_steps(target, man_bytes, sig_bytes)") < APPLY.index('job.say("flip", 80')
      and APPLY.index("build_venv(job, target)") < APPLY.index("    root_steps(target, man_bytes, sig_bytes)"))
check("...with exactly the argv the sudoers rule grants",
      '["sudo", "-n", "/usr/bin/python3", "-I", ROOT_STEPS]' in APPLY
      and 'ROOT_STEPS = "/usr/local/lib/irivai/root-steps.py"' in APPLY)
spec = importlib.util.spec_from_file_location("_au_rs", os.path.join(ROOT, "scripts", "apply_update.py"))
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)
tmp = tempfile.mkdtemp(prefix="rootsteps_")
au.ROOT_STEPS = os.path.join(tmp, "none.py")
check("a box without the root step: the update goes on (False, no raise)",
      au.root_steps("/r/v9.9.9", b"{}", b"sig") is False)
au.ROOT_STEPS = os.path.join(tmp, "root-steps.py")
open(au.ROOT_STEPS, "w").close()
calls = []
real_run = au.subprocess.run


def fake_run(argv, **kw):
    calls.append((argv, kw))
    return subprocess.CompletedProcess(argv, 1, "refused: x\n", "boom")


au.subprocess.run = fake_run
ok = au.root_steps("/r/v9.9.9-r1", b'{"a": 1}', b"c2ln\n")
req = json.loads(calls[0][1]["input"]) if calls else {}
check("...a refusal never fails the update", ok is False and len(calls) == 1)
check("...and the request names the release by name, with the manifest and signature",
      req.get("release") == "v9.9.9-r1" and base64.b64decode(req.get("manifest", "")) == b'{"a": 1}'
      and req.get("sig") == "c2ln", req)


def raise_os(argv, **kw):
    raise OSError("no sudo")


au.subprocess.run = raise_os
check("...nor does a sudo that cannot run", au.root_steps("/r/v9.9.9", b"{}", b"s") is False)
au.subprocess.run = real_run

# -- 2. the manifest pins what the bundle holds ------------------------------------------
import publish_release as pr                                   # noqa: E402

bundle = os.path.join(tmp, "bundle.tar.gz")
with tarfile.open(bundle, "w:gz") as tar:
    for rel in ["scripts/root_items.json"] + srcs:
        tar.add(os.path.join(ROOT, *rel.split("/")), arcname=rel)
pins = pr.root_pins(pr.pathlib.Path(bundle))
want = {rel: hashlib.sha256(open(os.path.join(ROOT, *rel.split("/")), "rb").read()).hexdigest()
        for rel in ["scripts/root_items.json"] + srcs}
check("the manifest's root table: sha256 of the list and of every file it names", pins == want)
check("...signed with the rest of the manifest, and re-checked by verify",
      '"root": root_pins(bundle)' in read("scripts", "dev", "publish_release.py")
      and 'doc.get("root") != root_pins(OUT / BUNDLE)' in read("scripts", "dev", "publish_release.py"))
short = os.path.join(tmp, "short.tar.gz")
with tarfile.open(short, "w:gz") as tar:
    tar.add(os.path.join(ROOT, "scripts", "root_items.json"), arcname="scripts/root_items.json")
try:
    pr.root_pins(pr.pathlib.Path(short))
    check("a bundle missing a listed file stops the release", False)
except SystemExit as exc:
    check("a bundle missing a listed file stops the release", "is not in" in str(exc), exc)

# -- 7. the cards' words --------------------------------------------------------------------
from core.services import roothelp, storage_job                  # noqa: E402

roothelp.LIB = tmp
roothelp.ROOT_STEPS = os.path.join(tmp, "absent-root-steps.py")
msg = storage_job.needs_installer()
check("no helper, no root step: names the helper and the one-time maintenance step",
      "storage-setup.py is not installed" in msg and "upgrade_steps.sh" in msg
      and "one-time software update" not in msg and "sudo " not in msg, msg)
roothelp.ROOT_STEPS = au.ROOT_STEPS
check("...a box with the root step is told the next update brings it",
      "next software update" in storage_job.needs_installer())
open(os.path.join(tmp, "storage-setup.py"), "w").close()
check("...a helper with no sudoers rule says that instead",
      "sudoers rule irivai-storage" in storage_job.needs_installer())

# -- 3..5. root's side, in a sandbox -----------------------------------------------------------
if os.name != "posix":
    print("\n(the root-side sandbox needs POSIX; skipped here)")
else:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    raw_pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    real_which, real_rs_run = rs._which, rs._run
    if real_which("openssl"):
        m = b"manifest"
        check("openssl fallback: a good signature verifies",
              rs._openssl_verify(raw_pub, m, key.sign(m)))
        check("...a wrong one does not", not rs._openssl_verify(raw_pub, m, other.sign(m)))
    if real_which("visudo"):
        good, badf = os.path.join(tmp, "g"), os.path.join(tmp, "b")
        open(good, "w").write("pi ALL=(root) NOPASSWD: /usr/bin/true\n")
        open(badf, "w").write("pi ALL=(root NOPASSWD /usr/bin/true\n")
        check("real visudo: a good rule passes, a broken one is refused",
              rs._visudo_ok(good) and not rs._visudo_ok(badf))
    rs.PUBKEYS = (base64.b64encode(raw_pub).decode(),)

    box = os.path.join(tmp, "box")
    releases = os.path.join(box, "releases")
    tree = os.path.join(releases, "v9.9.9")
    for rel in ["scripts/root_items.json"] + srcs:
        dst = os.path.join(tree, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, *rel.split("/")), dst)
    open(os.path.join(tree, "VERSION"), "w").write("9.9.9\n")
    for d in ("lib", "sudoers.d", "units", "etc"):
        os.makedirs(os.path.join(box, d), exist_ok=True)
    open(os.path.join(box, "units", "irivai-hub.service"), "w").write("[Service]\nUser=cytron\n")
    open(os.path.join(box, "etc", "update.conf"), "w").write(f"RELEASES_DIR={releases}\n")
    rs.LIB, rs.SUDOERS_DIR, rs.UNIT_DIR = (os.path.join(box, d) for d in ("lib", "sudoers.d", "units"))
    rs.HUB_UNIT = os.path.join(box, "units", "irivai-hub.service")
    rs.CONF, rs.STATE = os.path.join(box, "etc", "update.conf"), os.path.join(box, "etc", "state.json")
    tables, sysctl = set(), []
    rs._which = lambda name: "/fake/" + name if name == "nft" else None
    rs._visudo_ok = lambda p: b"NOPASSWD" in open(p, "rb").read()

    def sandbox_run(argv, timeout=60):
        if argv[0] == "systemctl":
            sysctl.append(argv[1:])
            if argv[1] == "restart":
                tables.add(next(n["table"] for n in ITEMS["nft"] if n["unit"] + ".service" == argv[2]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0 if argv[-1] in tables else 1, "", "")

    rs._run = sandbox_run
    rs.ETC = os.path.join(box, "etc", "irivai")
    for n in ITEMS["nft"]:
        if n.get("marker"):
            n["marker"] = os.path.join(rs.ETC, os.path.basename(n["marker"]))
    marker = next(n["marker"] for n in ITEMS["nft"] if n.get("marker"))

    def request(pinset=None, signer=key, version="9.9.9", release="v9.9.9",
                app="irivai-platform-release"):
        man = json.dumps({"app": app, "version": version,
                          "root": want if pinset is None else pinset}).encode()
        return json.dumps({"release": release, "manifest": base64.b64encode(man).decode(),
                           "sig": base64.b64encode(signer.sign(man)).decode()}).encode()

    def go(raw, reexec=True):
        rs.OUT.clear()
        real_load = rs.load

        def load_sandboxed(t, p=None):
            items, files = real_load(t, p)
            return ITEMS, files            # markers pointed into the sandbox
        rs.load = load_sandboxed
        try:
            return rs.verified(raw, reexec=reexec), [k for k, _ in rs.OUT], list(rs.OUT)
        finally:
            rs.load = real_load

    def lib_files():
        return sorted(f for f in os.listdir(rs.LIB) if not f.startswith("."))

    def wipe():
        for d in (rs.LIB, rs.SUDOERS_DIR):
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))
        for f in (rs.STATE, marker):
            if os.path.exists(f):
                os.remove(f)
        tables.clear()

    rc, kinds, out = go(request(signer=other))
    check("3. a signature by another key installs nothing", rc == 1 and lib_files() == [], out)
    rc, kinds, out = go(request(pinset={k: v for k, v in want.items() if k != srcs[3]}))
    check("...a file the manifest does not pin installs nothing",
          rc == 1 and lib_files() == [] and "not pinned" in out[-1][1], out)
    victim = os.path.join(tree, *ITEMS["helpers"][5]["src"].split("/"))
    orig = open(victim, "rb").read()
    open(victim, "ab").write(b"\n# evil\n")
    rc, kinds, out = go(request())
    check("...a tampered file in the release tree installs nothing",
          rc == 1 and lib_files() == [] and "does not match" in out[-1][1], out)
    open(victim, "wb").write(orig)
    rc, kinds, out = go(request(release="v9.9.9/../../etc"))
    check("...a release outside the releases directory is refused", rc == 2 and lib_files() == [], out)
    outside = os.path.join(tmp, "outside")
    shutil.copytree(tree, outside)
    os.symlink(outside, os.path.join(releases, "v8.8.8"))
    rc, kinds, out = go(request(release="v8.8.8"))
    check("...and so is a symlink planted in it that leads out", rc == 2 and lib_files() == [], out)

    rc, kinds, out = go(request())
    check("first run installs every helper and nftables rule file",
          rc == 0 and "refused" not in kinds
          and lib_files() == sorted([h["dest"] for h in ITEMS["helpers"]]
                                    + [n["dest"] for n in ITEMS["nft"]]), (rc, out))
    h0 = ITEMS["helpers"][4]
    check("...the very bytes the manifest pinned",
          open(os.path.join(rs.LIB, h0["dest"]), "rb").read()
          == open(os.path.join(ROOT, *h0["src"].split("/")), "rb").read()
          and os.stat(os.path.join(rs.LIB, h0["dest"])).st_mode & 0o777 == 0o755)
    sud = open(os.path.join(rs.SUDOERS_DIR, "irivai-storage")).read()
    check("...the sudoers rules for the hub unit's account, 0440, visudo-checked",
          sud == "cytron ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/storage-setup.py\n"
          and os.stat(os.path.join(rs.SUDOERS_DIR, "irivai-storage")).st_mode & 0o777 == 0o440
          and len(os.listdir(rs.SUDOERS_DIR)) == len(ITEMS["sudoers"]))
    check("...the nftables units, loaded, and the Modbus marker",
          all(os.path.isfile(os.path.join(rs.UNIT_DIR, n["unit"] + ".service")) for n in ITEMS["nft"])
          and tables == {n["table"] for n in ITEMS["nft"]} and open(marker).read() == "502 -> 5020\n")
    check("...and the version recorded, with the release app this box now takes",
          json.load(open(rs.STATE)) == {"version": "9.9.9", "app": "irivai-platform-release"})

    sysctl.clear()
    rc, kinds, out = go(request())
    check("5. the second run changes nothing", rc == 0 and set(kinds) == {"skipped"} and not sysctl, out)

    rc, kinds, out = go(request(version="9.9.8"))
    check("an older manifest than the last applied is skipped, nothing touched",
          rc == 0 and kinds == ["skipped"] and "older" in out[0][1], out)
    rc, kinds, out = go(request(version="9.9.10", app="irivai-platform-release-dev"))
    check("a release of the other app (dev on a customer box) is refused once one was applied",
          rc == 1 and kinds == ["refused"] and "takes root files from" in out[0][1], out)

    wipe()
    rs._visudo_ok = lambda p: "nmcli" not in open(p).read()          # the irivai-box rule
    rc, kinds, out = go(request())
    check("4. a rule visudo refuses is left out, said so, and no temp file stays behind",
          rc == 1 and not os.path.exists(os.path.join(rs.SUDOERS_DIR, "irivai-box"))
          and os.path.exists(os.path.join(rs.SUDOERS_DIR, "irivai-storage"))
          and [f for f in os.listdir(rs.SUDOERS_DIR) if f.startswith(".")] == []
          and any(k == "refused" and "irivai-box" in t for k, t in out), out)
    check("...the version is still recorded (signature, pins and helpers held), and the "
          "next update of the same version tries the rule again",
          json.load(open(rs.STATE))["version"] == "9.9.9")
    rs._visudo_ok = lambda p: True
    rc, kinds, out = go(request())
    check("...which it does", rc == 0 and os.path.exists(os.path.join(rs.SUDOERS_DIR, "irivai-box")), out)

    rs._sudo_regex_ok = lambda: False
    os.remove(os.path.join(rs.SUDOERS_DIR, "irivai-update"))
    rc, kinds, out = go(request())
    check("a sudo older than 1.9.10 gets no regex rule (it would read ^...$ literally)",
          not os.path.exists(os.path.join(rs.SUDOERS_DIR, "irivai-update"))
          and any("older than 1.9.10" in t for _, t in out), out)
    rs._sudo_regex_ok = lambda: True

    wipe()
    open(os.path.join(rs.LIB, "root-steps.py"), "w").write("# an old copy\n")
    reexec = []

    class FakeSub:
        CompletedProcess = subprocess.CompletedProcess
        # verified() and _self_ok() name these in their except clauses
        # (8acb39a9); without them the stand-in raised AttributeError there.
        SubprocessError = subprocess.SubprocessError
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(argv, **kw):
            reexec.append((argv, kw.get("input")))
            return subprocess.CompletedProcess(argv, 0, b"done: from the new copy\n", b"")

    real_sub, rs.subprocess = rs.subprocess, FakeSub
    raw = request()
    FakeSub.selftest_rc = 1

    def run_fake(argv, **kw):
        reexec.append((argv, kw.get("input")))
        rc = FakeSub.selftest_rc if argv[-1] == "--selftest" else 0
        return subprocess.CompletedProcess(argv, rc, b"done: from the new copy\n", b"")
    FakeSub.run = staticmethod(run_fake)
    rc, kinds, out = go(raw, reexec=False)
    check("a new root step that fails its own --selftest never replaces the old one",
          rc == 1 and open(os.path.join(rs.LIB, "root-steps.py")).read() == "# an old copy\n"
          and [a[-1] for a, _ in reexec] == ["--selftest"]
          and [f for f in os.listdir(rs.LIB) if f.startswith(".")] == [], (out, reexec))
    reexec.clear()
    FakeSub.selftest_rc = 0
    rc, kinds, out = go(raw, reexec=False)
    rs.subprocess = real_sub
    check("an old root step installs the new copy (after its --selftest) and hands the same "
          "request over",
          rc == 0 and open(os.path.join(rs.LIB, "root-steps.py"), "rb").read() == open(
              os.path.join(ROOT, "scripts", "root_steps.py"), "rb").read()
          and [a[-1] for a, _ in reexec] == ["--selftest", "--reexec"] and reexec[-1][1] == raw
          and lib_files() == ["root-steps.py"], (out, reexec))

    # A helper that does not even compile never replaces the one that works.
    items, files = rs.load(tree, want)
    flip = next(h for h in items["helpers"] if h["dest"] == "flip-link.py")
    open(os.path.join(rs.LIB, "flip-link.py"), "wb").write(b"# the working copy\n")
    files[flip["src"]] = b"def broken(:\n"
    rs.OUT.clear()
    rs.apply({"helpers": [flip]}, files)
    check("a helper that does not compile is refused and the old copy kept",
          open(os.path.join(rs.LIB, "flip-link.py"), "rb").read() == b"# the working copy\n"
          and rs.OUT and rs.OUT[0][0] == "refused", rs.OUT)

    # A marker path that climbs out of /etc/irivai is never written.
    evil = os.path.join(rs.ETC, "..", "escaped")
    items = json.loads(json.dumps(ITEMS))
    items["nft"][1]["marker"] = evil
    tables.clear()
    rs.OUT.clear()
    rs.apply({"nft": [items["nft"][1]]}, files)
    check("a marker path with .. is never written", not os.path.exists(evil))

    # A FIFO planted where a source should be is refused, not waited on.
    fifo_tree = os.path.join(releases, "v9.9.7")
    shutil.copytree(tree, fifo_tree)
    victim2 = os.path.join(fifo_tree, *srcs[2].split("/"))
    os.remove(victim2)
    os.mkfifo(victim2)
    try:
        rs.load(fifo_tree)
        check("a FIFO in place of a source is refused, without blocking", False)
    except rs.Refused as exc:
        check("a FIFO in place of a source is refused, without blocking", "not a regular file" in str(exc), exc)
    shutil.rmtree(fifo_tree)

    # The lock file is never opened through a symlink.
    rs.LOCK = os.path.join(box, "lock")
    os.symlink(os.path.join(box, "elsewhere"), rs.LOCK)
    try:
        rs._lock()
        check("the lock is never taken through a symlink", False)
    except OSError:
        check("the lock is never taken through a symlink", not os.path.exists(os.path.join(box, "elsewhere")))
    os.remove(rs.LOCK)
    fd = rs._lock()
    rs.LOCK_WAIT_S = 0
    check("...and a second taker gives up after the deadline instead of hanging",
          fd is not None and rs._lock() is None)
    os.close(fd)

    wipe()
    rc, kinds, out = go(json.dumps({"release": "v9.9.9", "sig": "x"}).encode())
    check("a request without a manifest is refused", rc == 2 and lib_files() == [])
    man = json.dumps({"app": "irivai-platform-release", "version": "9.9.9"}).encode()
    rc, kinds, out = go(json.dumps({"release": "v9.9.9", "manifest": base64.b64encode(man).decode(),
                                    "sig": base64.b64encode(key.sign(man)).decode()}).encode())
    check("a release from before the pins: nothing to install, nothing refused",
          rc == 0 and kinds == ["skipped"] and lib_files() == [], out)

    # By hand (upgrade_steps.sh): the tree as it is, CRLF made LF, a dry run touches nothing.
    wipe()
    crlf = os.path.join(tree, *ITEMS["helpers"][6]["src"].split("/"))
    data = open(crlf, "rb").read()
    open(crlf, "wb").write(data.replace(b"\n", b"\r\n"))
    real_load = rs.load
    rs.load = lambda t, p=None: (ITEMS, real_load(t, p)[1])
    rs.OUT.clear()
    check("--tree --dry-run changes nothing", rs.tree_mode(tree, dry=True) == 0 and lib_files() == [])
    rs.OUT.clear()
    rc = rs.tree_mode(tree)
    got = open(os.path.join(rs.LIB, ITEMS["helpers"][6]["dest"]), "rb").read()
    check("--tree installs the list with LF endings and records the version",
          rc == 0 and b"\r" not in got and json.load(open(rs.STATE))["version"] == "9.9.9", rs.OUT[-3:])
    items = json.loads(json.dumps(ITEMS))
    optout = os.path.join(box, "etc", "ap-forwarding-allowed")
    open(optout, "w").close()
    items["nft"][0]["optout"] = optout
    rs.OUT.clear()
    rs.apply(items, real_load(tree)[1])
    check("a box that allowed hotspot forwarding keeps it that way",
          any("not wanted on this box" in t for _, t in rs.OUT))
    rs.load = real_load
    rs._which, rs._run = real_which, real_rs_run

    # The BUNDLED copy, run as a program in a scratch root: its own --selftest
    # under -I, then a whole verified request through main(), the way sudo
    # starts it (paths pointed at the scratch box, nothing else changed).
    scratch = os.path.join(tmp, "scratch")
    rel_dir = os.path.join(scratch, "releases", "v9.9.9")
    with tarfile.open(bundle) as tar:
        tar.extractall(rel_dir, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    bundled = os.path.join(rel_dir, "scripts", "root_steps.py")
    r = subprocess.run([sys.executable, "-I", bundled, "--selftest"], capture_output=True, text=True)
    check("the bundled root_steps.py passes its --selftest under -I", r.returncode == 0, r.stdout + r.stderr)
    for d in ("lib", "sudoers.d", "units", "etc"):
        os.makedirs(os.path.join(scratch, d), exist_ok=True)
    open(os.path.join(scratch, "units", "irivai-hub.service"), "w").write("User=cytron\n")
    open(os.path.join(scratch, "etc", "update.conf"), "w").write(
        "RELEASES_DIR=" + os.path.join(scratch, "releases") + "\n")
    cfg = {"LIB": os.path.join(scratch, "lib"), "SUDOERS_DIR": os.path.join(scratch, "sudoers.d"),
           "UNIT_DIR": os.path.join(scratch, "units"), "HUB_UNIT": os.path.join(scratch, "units", "irivai-hub.service"),
           "CONF": os.path.join(scratch, "etc", "update.conf"), "STATE": os.path.join(scratch, "etc", "state.json"),
           "ETC": os.path.join(scratch, "etc"), "PUBKEYS": list(rs.PUBKEYS)}
    wrapper = ("import importlib.util, json, sys\n"
               "spec = importlib.util.spec_from_file_location('rsb', sys.argv[1])\n"
               "rs = importlib.util.module_from_spec(spec); spec.loader.exec_module(rs)\n"
               "for k, v in json.loads(sys.argv[2]).items(): setattr(rs, k, tuple(v) if k == 'PUBKEYS' else v)\n"
               "rs._is_root = lambda: True\n"
               "rs._which = lambda name: None\n"
               "rs._visudo_ok = lambda path: True\n"
               "sys.exit(rs.main(['root-steps.py', '--reexec']))\n")
    r = subprocess.run([sys.executable, "-I", "-c", wrapper, bundled, json.dumps(cfg)],
                       input=request(), capture_output=True)
    out_b = r.stdout.decode()
    check("...and installs the list from a signed request, run as a program",
          r.returncode == 0 and sorted(os.listdir(cfg["LIB"])) == sorted(h["dest"] for h in ITEMS["helpers"])
          and len(os.listdir(cfg["SUDOERS_DIR"])) == len(ITEMS["sudoers"])
          and json.load(open(cfg["STATE"]))["version"] == "9.9.9", out_b[-600:] + r.stderr.decode()[-300:])

shutil.rmtree(tmp, ignore_errors=True)
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
