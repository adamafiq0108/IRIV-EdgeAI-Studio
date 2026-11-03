# Over-the-air updates: an appliance with no shell and no git learns a new
# release exists, downloads one signed bundle, and swaps itself -- and every
# guard in that path exists because the failure it stops is unrecoverable in
# the field. This test pins the guards, not the happy path: version compare
# that is numeric (a string compare refuses real updates), a signature checked
# BEFORE the JSON it covers is parsed, a tarball that cannot write outside its
# destination or hand a box a deploy.toml, an apply() that installs only the
# version the admin was shown, a "running" flag a dead job cannot wedge, a
# token that never leaves the box, an applier that survives the tree it is
# replacing, and a sudoers rule that grants exactly three commands.
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import version as ver                                # noqa: E402
from core.services import ghnet                                # noqa: E402
from core.services import update as upd                        # noqa: E402


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The applier is imported by path, exactly as it lives on a box: a COPY on the
# store volume, imported by nothing, importing nothing from the product.
au = _load(os.path.join(ROOT, "scripts", "apply_update.py"), "_smoke_applier")

# 1. Version semantics. The whole feature turns on one comparison, and the
#    obvious implementation is wrong: "1.10.0" < "1.9.0" as strings, so a
#    string compare refuses the tenth minor release forever.
assert ("1.10.0" < "1.9.0"), "the trap this section pins has moved"
assert ver.parse("1.10.0") == (1, 10, 0)
assert ver.is_newer("1.10.0", "1.9.0"), "1.10.0 IS newer than 1.9.0"
assert not ver.is_newer("1.9.0", "1.10.0")
assert not ver.is_newer("2.0.0", "2.0.0"), "equal is not newer"
assert ver.parse("v2.10.1") == (2, 10, 1), "a leading v is tag spelling"
assert ver.parse("2.1") == (2, 1, 0)
assert ver.parse("junk") == (0, 0, 0), "junk must compare, never crash"
assert ver.parse(None) == (0, 0, 0)
assert ver.parse("1.2.3-rc1") == (1, 2, 3)
# ...and the applier's own copy (stdlib-only, so it cannot import this one)
# agrees on every case that matters.
for a, b in (("1.10.0", "1.9.0"), ("v2.0.0", "1.99.99")):
    assert au.semver(a) > au.semver(b)
    assert au.semver(a) == ver.parse(a)
# The VERSION file is the single source of truth the release workflow tags
# against: it must exist, say something, and be LF-only -- a CRLF here means
# the health gate compares "2.1.0\r" against "2.1.0" forever.
with open(os.path.join(ROOT, "VERSION"), "rb") as f:
    raw = f.read()
assert raw.strip(), "VERSION must not be empty"
assert b"\r" not in raw, "VERSION must be LF-only"
assert ver.VERSION == raw.decode("utf-8").strip()

# 2. Sign/verify roundtrip, with the ordering that makes it worth having:
#    the signature is checked over the manifest's raw bytes BEFORE those bytes
#    are parsed as JSON. Parse first and the attacker's first byte of
#    influence lands in the JSON parser, pre-authentication.
import base64                                                  # noqa: E402
from cryptography.hazmat.primitives import serialization       # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey)

_priv = Ed25519PrivateKey.generate()
_pub_b64 = base64.b64encode(_priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _sign(data):
    return base64.b64encode(_priv.sign(data)).decode()


APP = upd.MANIFEST_APP
assert APP == "irivai-platform-release", "service and applier must agree"
good = json.dumps({"app": APP, "version": "9.9.9",
                   "bundle": {"sha256": "0" * 64, "size": 1}}).encode()
m = au.verify(good, _sign(good), _pub_b64, APP)
assert m["version"] == "9.9.9", "a good manifest must verify and parse"
# One flipped byte anywhere in the signed bytes fails.
bad = good[:-3] + bytes([good[-3] ^ 1]) + good[-2:]
try:
    au.verify(bad, _sign(good), _pub_b64, APP)
    raise AssertionError("a tampered manifest verified")
except au.Fail as e:
    assert "signature" in str(e), str(e)
# A correctly signed manifest for the WRONG app is someone else's release.
other = json.dumps({"app": "someone-elses-app"}).encode()
try:
    au.verify(other, _sign(other), _pub_b64, APP)
    raise AssertionError("a foreign app's manifest was accepted")
except au.Fail as e:
    assert "not a platform release" in str(e), str(e)
# No configured pubkey = no updates, never "skip the check".
try:
    au.verify(good, _sign(good), "", APP)
    raise AssertionError("an empty pubkey verified something")
except au.Fail as e:
    assert "no update signing key" in str(e), str(e)
# THE ORDERING PROOF. A valid signature over garbage bytes must die in the
# JSON step (signature already passed); an invalid signature over perfectly
# valid JSON must die in the signature step (JSON never parsed). If these two
# errors ever swap, parsing moved ahead of authentication.
garbage = b"this is not json {{{"
try:
    au.verify(garbage, _sign(garbage), _pub_b64, APP)
    raise AssertionError("garbage bytes verified AND parsed")
except au.Fail as e:
    assert "not valid JSON" in str(e), "valid sig + bad JSON must be the JSON error: " + str(e)
try:
    au.verify(good, _sign(b"different bytes"), _pub_b64, APP)
    raise AssertionError("a wrong signature was accepted")
except au.Fail as e:
    assert "signature" in str(e) and "JSON" not in str(e), "bad sig + good JSON must be the signature error: " + str(e)

# 3. Tar hardening: unpack() must contain a hostile archive even though its
#    signature verified -- a compromised build pipeline signs whatever it
#    builds, and "the update wrote /etc/passwd" is not a bug report anyone
#    files twice.
SAND = tempfile.mkdtemp(prefix="smoke_update_tar_")


def _tar(name, members):
    path = os.path.join(SAND, name)
    with tarfile.open(path, "w:gz") as tf:
        for mname, data, link in members:
            info = tarfile.TarInfo(mname)
            if link is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


for label, member in (
        ("parent traversal", ("../evil.py", b"import os", None)),
        ("absolute path", ("/etc/passwd", b"root::0:0::/:/bin/sh", None)),
        ("escaping symlink", ("escape", None, "../../outside.txt"))):
    dest = os.path.join(SAND, "out_" + label.split()[0])
    tp = _tar(label.split()[0] + ".tar.gz", [member])
    refused = False
    try:
        au.unpack(tp, dest, max_bytes=1 << 20)
    except Exception:                                          # noqa: BLE001
        refused = True                     # refusing outright is the best case
    # Refused or dropped -- either way NOTHING may land outside dest.
    assert not os.path.exists(os.path.join(SAND, "evil.py")),         label + ": a file escaped the destination"
    assert not os.path.exists(os.path.join(os.path.dirname(SAND),
                                           "outside.txt"))
    if not refused:
        for landed in ("evil.py", "passwd", "escape"):
            assert not os.path.lexists(os.path.join(dest, landed)),                 label + f": {landed} was extracted"
# The byte ceiling: a bundle that declares more than max_bytes is refused
# before a byte is written -- a signed-but-broken build must not fill the disk.
big = _tar("big.tar.gz", [("big.bin", b"x" * 4096, None)])
try:
    au.unpack(big, os.path.join(SAND, "out_big"), max_bytes=100)
    raise AssertionError("a tar over the byte ceiling unpacked")
except au.Fail as e:
    assert "more than it declares" in str(e), str(e)
# NEVER_SHIP: a release must not be able to hand a box a config/deploy.toml
# (it flips customer mode to dev mode) even when the bundle carries one.
ship = _tar("ship.tar.gz", [("config/deploy.toml", b'mode = "dev"\n', None),
                            ("core/ok.txt", b"fine", None)])
dest = os.path.join(SAND, "out_ship")
au.unpack(ship, dest, max_bytes=1 << 20)
assert os.path.isfile(os.path.join(dest, "core", "ok.txt"))
assert not os.path.exists(os.path.join(dest, "config", "deploy.toml")),     "deploy.toml rode in on a release"

# 4. Downgrade + approval pinning in the service. On a checkout (this box)
#    OTA is not even offered: no release layout, no symlink, and apply() says
#    so instead of failing half-way through.
assert upd.ota_ready() is False, "a source checkout must never read OTA-ready"
try:
    upd.apply("9.9.9")
    raise AssertionError("apply() ran on a source checkout")
except upd.UpdateError as e:
    assert "layout" in str(e), str(e)
try:
    upd.apply("")
    raise AssertionError("apply() accepted an empty version")
except upd.UpdateError:
    pass
# Now pretend the box IS laid out for OTA and pin the two version rules.
# The admin approved the version they were SHOWN: if a newer release landed
# since the check, installing that instead is not what was approved.
_real_job_running = upd.job_running
_real_ota_ready = upd.ota_ready
_real_token = upd.token
upd.ota_ready = lambda: True
upd.job_running = lambda: False
# token() grew a channel parameter with the two-channel model; the stub must
# match its arity or apply() dies on the stub instead of on the rule under test.
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
upd._write_json(upd._p("check.json"), {"latest": "9.9.9"})
try:
    upd.apply("9.9.8")
    raise AssertionError("apply() installed a version that was not the "
                         "cached latest")
except upd.UpdateError as e:
    assert "no longer the latest release" in str(e), str(e)
# And never a downgrade / reinstall: the cached latest equal to the running
# version is refused as "not newer" (rollback is the sanctioned way back).
upd._write_json(upd._p("check.json"), {"latest": ver.VERSION})
try:
    upd.apply(ver.VERSION)
    raise AssertionError("apply() reinstalled the running version")
except upd.UpdateError as e:
    assert "not newer" in str(e), str(e)

# 5. Staleness: job_running() must say False for any job that is not
#    genuinely alive RIGHT NOW, or a crashed applier wedges the Update button
#    until a site visit. Freshness is identity (boot id + live pid), never
#    mtime -- these boards have no RTC and the clock jumps when NTP lands.
upd.job_running = _real_job_running        # back to the real implementation
status_path = upd._p("status.json")
# A job from a previous boot: mid-phase, pid even ALIVE (ours) -- still False,
# because the boot id proves that pid is a recycled number, not our applier.
upd._write_json(status_path, {"phase": "download", "percent": 30,
                              "boot_id": "0000-not-this-boot",
                              "pid": os.getpid()})
assert upd.job_running() is False,     "a job from a previous boot must not read as running"
# This boot, but the writer is dead. pid 999999 cannot be a live process here
# (Windows pids are multiples of 4; CI containers never reach it).
upd._write_json(status_path, {"phase": "download", "percent": 30,
                              "boot_id": upd._boot_id(), "pid": 999999})
assert upd.job_running() is False, "a dead applier must not read as running"
# On Linux the /proc boot id is real, so a live pid + real boot id would read
# True -- that positive case needs a real applier and stays out of a smoke
# test. A terminal phase reads False on every platform:
upd._write_json(status_path, {"phase": "done", "percent": 100,
                              "boot_id": upd._boot_id(), "pid": os.getpid()})
assert upd.job_running() is False, "a finished job is not a running one"
assert upd.status()["running"] is False

# 6. Secrets never leak. public_state() is the ONLY shape a route may return:
#    no ETag, no asset URLs (they redirect to signed storage), no token --
#    the caller learns whether one is set and nothing else.
upd._write_json(upd._p("check.json"),
                {"latest": "9.9.9", "etag": 'W/"abc"',
                 "assets": {"bundle.tar.gz": {"url": "https://x", "size": 1}},
                 "token": "should-never-even-be-here"})
pub = upd.public_state()
for secret in ("etag", "assets", "token"):
    assert secret not in pub, f"public_state() leaked '{secret}'"
assert "token_set" in pub, "the boolean is the whole disclosure"
with open(os.path.join(ROOT, "core", "routes", "update.py"),
          encoding="utf-8") as f:
    rsrc = f.read()
routes = rsrc.count("@router.")
assert routes >= 6, "the update routes went missing"
# Every route is gated: channel-aware routes go through _gate() (whose first
# line is _require_admin), the channel-less ones call _require_admin directly.
# _gate's own internal call is the -1.
# "= _gate(request" counts the CALLS (not the def); _require_admin's count
# includes the one call inside _gate itself, hence the -1.
gated = rsrc.count("= _gate(request") + rsrc.count("_require_admin(request") - 1
assert gated == routes,     "every update route must reach _require_admin (directly or via _gate) -- " \
    "this is the most consequential button on the box"
_gate_src = rsrc.split("def _gate", 1)[1].split("\ndef ", 1)[0]
assert "_require_admin(request" in _gate_src,     "_gate stopped requiring admin -- every channel-aware route just lost " \
    "its primary gate"
assert "update_service.token(" not in rsrc,     "no route may read the token, even to mask it"

# 7. Applier independence: apply_update.py and recover.py run while the tree
#    beside them is being replaced, so importing anything from core. is a
#    time bomb -- it works on every bench and dies mid-swap in the field.
import ast                                                     # noqa: E402
for script in ("apply_update.py", "recover.py"):
    with open(os.path.join(ROOT, "scripts", script), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for name in mods:
            assert not (name == "core" or name.startswith("core.")),                 f"{script} imports {name} -- it must survive the tree it replaces"
with open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8") as f:
    inst = f.read()
# ...and recover.py is installed OUTSIDE every release directory: a repair
# tool that lives inside the thing being repaired is not a repair tool.
assert "/usr/local/lib/irivai/recover.py" in inst,     "recover.py must be installed outside the release dirs"
assert "${UPDATE_WORK}/apply_update.py" in inst,     "the apply unit must run the COPY on the store volume, not the tree"

# 8. Sudoers: the privileges THIS feature takes are restarting the platform's
#    own units, and pointing the live path at a release. Exactly four
#    commands, nothing else -- a fifth entry here is an escalation, not a
#    convenience, and this assertion exists to make adding one a decision.
#
#    The install script writes a second, unrelated grant (irivai-box: power
#    and network, for the front panel and the Network page). It is checked
#    separately below; what matters here is that the OTA rule did not quietly
#    grow, so this looks at the update file's own line rather than at every
#    NOPASSWD in the script.
sudo_lines = [ln for ln in inst.splitlines()
              if "NOPASSWD:" in ln and "irivai-apply.service" in ln]
assert len(sudo_lines) == 1, "expected exactly one update sudoers grant line"
# The masters come in through ${MASTER_RESTART}: by default ONE sudo regex
# (sudo 1.9.10+), never `irivai-master@*.service` -- a sudoers `*` matches
# spaces too, so that let the account restart any unit as root (Pipi,
# 2026-10-07). An older sudo gets one exact command per installed system.
assert "MASTER_RESTART='/usr/bin/systemctl ^restart irivai-master@[a-z0-9_-]+[.]service$, '" in inst
assert "irivai-master@*" not in sudo_lines[0], "the wildcard master restart is back"
granted = {c.strip() for c in sudo_lines[0].replace("${MASTER_RESTART}", "MASTERS, ")
           .split("NOPASSWD:", 1)[1].split(",")}
assert granted == {
    # --no-block is part of the grant, not decoration: sudoers matches the
    # argument list exactly, and `systemctl start` on a oneshot unit waits for
    # the whole job -- which here ends by restarting the caller. The service
    # must pass the same flag or the rule refuses it.
    "/usr/bin/systemctl start --no-block irivai-apply.service",
    "/usr/bin/systemctl restart irivai-hub.service",
    "MASTERS",
    # Added deliberately, 2026-09-18. Replacing /opt/irivai-systor means
    # creating /opt/irivai-systor.new inside /opt, and POSIX asks for write
    # permission on the PARENT directory -- /opt is root:root 755, so the
    # service account could unpack a release and never make it live. The
    # alternative was making /opt group-writable by that account, which would
    # let it replace anything else installed there for ever. This grants
    # nothing new: the account already owns the releases directory, so it
    # already decides what a release CONTAINS; the helper only lets it say
    # which one is live, by name, checked against that same directory.
    "/usr/local/lib/irivai/flip-link.py",
}, "the sudoers grant changed: " + repr(granted)

# The box grant, pinned for the same reason: it is written by the same script
# and every entry in it is a privilege somebody has to justify.
box_lines = [ln for ln in inst.splitlines()
             if "NOPASSWD:" in ln and "nmcli" in ln]
assert len(box_lines) == 1, "expected exactly one box sudoers grant line"
box_granted = {c.strip() for c in box_lines[0].split("NOPASSWD:", 1)[1].split(",")}
assert box_granted == {
    # Power: what the USER button does and what the dashboard's power buttons
    # ask for. Both paths, because the binary moved to /usr/sbin on trixie.
    "/usr/sbin/shutdown now", "/usr/sbin/reboot now",
    "/sbin/shutdown now", "/sbin/reboot now",
    # The network, for whoever is commissioning the box in front of it. NOT
    # `nmcli *` any more (2026-10-05): that let the account print every
    # stored key (`nmcli -s connection show`) and is why keys rode on argv.
    # Wi-Fi profiles go through the root helper (key on stdin); the rest is
    # the verbs core/services/netconf.py runs, -s/--show-secrets refused.
    "/usr/bin/python3 -I /usr/local/lib/irivai/wifi-profile.py",
    "/usr/bin/nmcli radio wifi on", "/usr/bin/nmcli radio wifi off",
    "/usr/bin/nmcli connection up *", "/usr/bin/nmcli connection down *",
    "/usr/bin/nmcli connection delete *", "/usr/bin/nmcli connection modify *",
    "/usr/bin/nmcli connection add type ethernet *",
    "/usr/bin/nmcli device connect wlan0",
    "!/usr/bin/nmcli -s *", "!/usr/bin/nmcli * -s", "!/usr/bin/nmcli * -s *",
    "!/usr/bin/nmcli --show-secrets *", "!/usr/bin/nmcli * --show-secrets",
    "!/usr/bin/nmcli * --show-secrets *",
    # The factory flow, which cannot share the OLED with this platform.
    "/usr/bin/systemctl enable --now nodered",
    "/usr/bin/systemctl disable --now nodered",
}, "the box sudoers grant changed: " + repr(box_granted)
from core.services import update as _u                        # noqa: E402
import inspect                                                # noqa: E402
assert '"--no-block"' in inspect.getsource(_u._start_unit),     "the service must start the unit with the flag the sudoers rule grants"

# 9. Offline dependency doctrine: a box in the field must never resolve PyPI
#    live -- it would install versions nobody tested, and fail entirely on a
#    site whose firewall allows GitHub and nothing else. A bundle without its
#    wheels is a broken bundle, refused; the install itself is --no-index.
with open(os.path.join(ROOT, "scripts", "apply_update.py"),
          encoding="utf-8") as f:
    ausrc = f.read()
venv_src = ausrc.split("def build_venv", 1)[1].split("\ndef ", 1)[0]
assert "did not carry its dependency wheels" in venv_src,     "a missing wheelhouse must be a refusal, not a PyPI fallback"
assert "--no-index" in venv_src, "pip must be forbidden from resolving PyPI"
assert "--find-links" in venv_src

# 10. The redirect rule: a private release asset 302s from GitHub to signed
#     storage, and forwarding our Bearer header across that hop is a
#     credential leak (and a 400 from S3, which sees two credentials).
import urllib.request                                          # noqa: E402
handler = ghnet.NoAuthAcrossHosts()
req = urllib.request.Request("https://api.github.com/repos/x/releases/assets/1",
                             headers={"Authorization": "Bearer ghp_secret"})
assert req.get_header("Authorization") == "Bearer ghp_secret"
new = handler.redirect_request(
    req, None, 302, "Found", {},
    "https://release-assets.s3.amazonaws.com/signed?X-Amz-Signature=abc")
assert new is not None
assert new.get_header("Authorization") is None,     "the Bearer header crossed into a non-GitHub host"
kept = handler.redirect_request(
    urllib.request.Request("https://api.github.com/x",
                           headers={"Authorization": "Bearer ghp_secret"}),
    None, 302, "Found", {}, "https://objects.githubusercontent.com/asset")
assert kept.get_header("Authorization") == "Bearer ghp_secret",     "a redirect that stays on GitHub keeps its auth"
# The applier restates the same rule (it imports nothing from core); prove
# its private copy behaves identically, not just that it exists.
ahandler = au._NoAuthAcrossHosts()
anew = ahandler.redirect_request(
    urllib.request.Request("https://api.github.com/x",
                           headers={"Authorization": "Bearer ghp_secret"}),
    None, 302, "Found", {}, "https://bucket.s3.amazonaws.com/y")
assert anew.get_header("Authorization") is None,     "the applier's own redirect handler must drop auth too"

# 11. Config carry-over: a new release directory arrives with the product's
#     stock config and NONE of this box's -- no accounts, no release token, no
#     customer-mode marker. Missed once, the update ships a box that reports
#     itself healthy (/api/health needs no login) and cannot be logged into.
#     carry_config() must move every *.local.toml plus deploy.toml, and must
#     NOT drag the old release's stock files over the new release's.
CARRY = tempfile.mkdtemp(prefix="smoke_update_carry_")
_oldc = os.path.join(CARRY, "old", "config")
_newc = os.path.join(CARRY, "new", "config")
os.makedirs(_oldc)
os.makedirs(_newc)
_carry_files = {"users.local.toml": b'[admin]\npassword_hash = "abc"\n',
                "update.local.toml": b'token = "ghp_device"\n',
                "deploy.toml": b'mode = "customer"\n'}
for _n, _b in _carry_files.items():
    with open(os.path.join(_oldc, _n), "wb") as f:
        f.write(_b)
with open(os.path.join(_oldc, "store.toml"), "wb") as f:
    f.write(b"# OLD release's stock store.toml -- must stay behind\n")
with open(os.path.join(_newc, "store.toml"), "wb") as f:
    f.write(b"# NEW release's stock store.toml -- must survive\n")
moved = au.carry_config(os.path.join(CARRY, "old"), os.path.join(CARRY, "new"))
assert set(moved) == set(_carry_files),     f"carry_config moved the wrong set: {sorted(moved)}"
for _n, _b in _carry_files.items():
    with open(os.path.join(_newc, _n), "rb") as f:
        assert f.read() == _b, f"{_n} arrived corrupted"
with open(os.path.join(_newc, "store.toml"), "rb") as f:
    assert b"NEW release" in f.read(),         "the old release's stock store.toml overwrote the new one's"
# ...and do_apply calls it BEFORE the flip: carried after, there is a window
# where the live symlink points at a release with no accounts in it, and a
# crash in that window strands the box there.
_apply_src = ausrc.split("def do_apply", 1)[1].split("\ndef ", 1)[0]
assert "carry_config(" in _apply_src, "do_apply no longer carries config"
assert _apply_src.index("carry_config(") < _apply_src.index("flip("),     "do_apply must carry this box's config BEFORE flipping the symlink"

# 12. pid 0 is not "no process". To os.kill(0, 0) it means the CALLER'S whole
#     process group, which always exists -- so a handoff status written with
#     pid 0 (apply() writes exactly that before the applier claims the pid)
#     would read as "an update is already running" forever, and the Update
#     button would refuse every attempt until someone power-cycled the box.
for junk in (0, -1, "", None, "not-a-pid"):
    assert upd._alive(junk) is False,         f"_alive({junk!r}) must be False -- pid 0 targets our own process group"
assert upd._alive(os.getpid()) is True, "our own pid IS alive"
upd._write_json(status_path, {"phase": "download", "percent": 10,
                              "boot_id": upd._boot_id(), "pid": 0})
assert upd.job_running() is False,     "a handoff that never reached the updater (pid 0, this boot, mid-phase) " \
    "must not read as a running job"
upd._write_json(status_path, {"phase": "done", "percent": 100})  # tidy

# 13. Rollback must not depend on the exception TYPE. Past the flip the box is
#     live on unproven code, and every way out of that block without rolling
#     back leaves it there: a systemctl timeout raises TimeoutExpired, a copy
#     error raises OSError -- neither is a Fail, and `except Fail` would let
#     both walk straight past the rollback. And the maintenance flag comes off
#     via _rm, never a bare os.remove: a flag already gone must not turn the
#     rollback path into a crash of its own.
_rb_src = ausrc.split("def do_rollback", 1)[1].split("\ndef ", 1)[0]
for fn_name, fn_src in (("do_apply", _apply_src), ("do_rollback", _rb_src)):
    handlers = [ln.strip() for ln in fn_src.splitlines()
                if ln.strip().startswith("except ")]
    assert any(h.startswith("except Exception") for h in handlers),         f"{fn_name}: the post-flip handler must catch Exception, not Fail"
    assert not any(h.startswith("except Fail") for h in handlers),         f"{fn_name}: `except Fail` would skip the rollback on a systemctl " \
        "timeout or a copy error"
    assert "os.remove(" not in fn_src,         f"{fn_name}: the maintenance flag must come off via _rm -- a bare " \
        "os.remove on a missing flag crashes inside the recovery path"
    assert "_rm(" in fn_src, f"{fn_name} stopped clearing the maintenance flag"

# 14. recover.py must never walk a box FORWARD. "The newest other release" is
#     the wrong answer twice over: during an apply it is the half-proved
#     release being installed, during a rollback it is the version someone was
#     deliberately escaping. The updater NAMES its fallback in every status
#     line, and the repair script must read exactly that.
with open(os.path.join(ROOT, "scripts", "recover.py"), encoding="utf-8") as f:
    rcsrc = f.read()
assert 'status.get("previous")' in rcsrc,     "recover.py no longer reads the release the updater named"
assert rcsrc.index('status.get("previous")') < rcsrc.index("os.listdir(RELEASES"),     "the named release must be consulted BEFORE any directory-listing guess"
# The last-resort guess may only go DOWN: strictly older than the version the
# interrupted job was installing, by semver, never string order.
assert 'semver(n) < semver(status.get("version"))' in rcsrc,     "the listdir fallback must filter to releases strictly OLDER than the " \
    "interrupted job's version"
# ...and the applier actually writes "previous" into every status line, or
# there is nothing for recover.py to read.
_say_src = ausrc.split("def say", 1)[1].split("\n    def ", 1)[0].split("\ndef ", 1)[0]
assert '"previous": self.previous' in _say_src,     "Job.say stopped recording the fallback release in status.json"

# 15. The applier's interpreter and its log path, pinned in the unit itself.
#     The applier needs `cryptography` to verify the manifest, and a stock
#     Raspberry Pi OS /usr/bin/python3 does not have it (and its ABI does not
#     match the release's wheelhouse) -- so the unit must run the release
#     venv's ${PY}. And it must log OUTSIDE the release tree: ${TARGET}/logs
#     does not exist in a freshly unpacked release, systemd does not create
#     parents for append:, and the unit would refuse to start on the second
#     update this box ever performs.
_apply_unit = inst.split('irivai-apply.service "[Unit]', 1)[1].split("write_unit", 1)[0]
assert "ExecStart=${PY} ${UPDATE_WORK}/apply_update.py" in _apply_unit,     "the apply unit must run the release venv python, from the store copy"
assert "/usr/bin/python3" not in _apply_unit,     "a stock python cannot import cryptography or match the wheelhouse ABI"
assert "StandardOutput=append:${UPDATE_WORK}/update.log" in _apply_unit
assert "StandardError=append:${UPDATE_WORK}/update.log" in _apply_unit
assert "${TARGET}/logs" not in _apply_unit,     "logging under ${TARGET}/logs fails on a fresh release -- systemd does " \
    "not create parents for append:"

# 16. Installer and runtime must agree on the store root. Its fallbacks run
#     through the asking user's HOME, so resolving it as root names a
#     directory the hub can neither read nor write -- the updater would then
#     look for its job where nothing ever writes one. The installer resolves
#     it as the service user and writes the answer down in /etc/irivai/
#     update.conf, because recover.py runs as root at boot and must not guess.
assert 'sudo -u "$SERVICE_USER" "$PY"' in inst,     "the store root must be resolved AS THE SERVICE USER, not as root"
assert "config.store_dir()" in inst,     "the installer must ask the product for the store root, not re-derive it"
assert "/etc/irivai/update.conf" in inst
for line in ("UPDATE_DIR=${UPDATE_WORK}", "LIVE_LINK=${TARGET}",
             "RELEASES_DIR=${RELEASES_DIR}"):
    assert line in inst, f"update.conf must record {line.split('=')[0]}"
assert 'CONF = "/etc/irivai/update.conf"' in rcsrc,     "recover.py must read the file the installer writes"
assert rcsrc.index("_read_conf()") < rcsrc.index("STORE_CANDIDATES:"),     "recover.py must consult update.conf BEFORE guessing candidate paths"
# And _read_conf() for real, against a temp file: it returns the update dir
# and repoints the module's link/releases paths.
rec = _load(os.path.join(ROOT, "scripts", "recover.py"), "_smoke_recover")
_conf = os.path.join(tempfile.mkdtemp(prefix="smoke_update_conf_"), "update.conf")
with open(_conf, "w", encoding="utf-8") as f:
    f.write("UPDATE_DIR=/mnt/nvme/cytron/store/update\n"
            "LIVE_LINK=/opt/elsewhere\n"
            "RELEASES_DIR=/opt/elsewhere-releases\n")
rec.CONF = _conf
assert rec._read_conf() == "/mnt/nvme/cytron/store/update"
assert rec.LIVE_LINK == "/opt/elsewhere", "LIVE_LINK must follow the conf"
assert rec.RELEASES == "/opt/elsewhere-releases", "RELEASES must follow the conf"
# A missing conf is a quiet fallback, not a crash -- and it must not blank the
# paths already in effect.
rec.CONF = _conf + ".does-not-exist"
assert rec._read_conf() == ""
assert rec.LIVE_LINK == "/opt/elsewhere" and rec.RELEASES == "/opt/elsewhere-releases"

# 17. KillMode=process survivors. Some boards' irivai-hub.service units set
#     KillMode=process ON PURPOSE, so a dashboard restart never takes a live
#     detection down -- which means restart_stack()'s hub restart does NOT
#     kill the masters the hub spawned as detached children. Left alone, the
#     update reports success while every one of those masters keeps serving
#     the OLD code forever. The fix: capture which masters answer BEFORE the
#     flip, then revive each one on the release that is actually live.
import re                                                      # noqa: E402
assert "KillMode=process" in ausrc,     "the applier lost the KillMode=process rationale -- the survivor-master " \
    "revive exists because of exactly that unit setting"

# masters_config: base + local merged with local winning, port pulled from
# each url, a row with no url simply absent (never a KeyError), and a url
# with a trailing path still yields its port.
MAST = tempfile.mkdtemp(prefix="smoke_update_masters_")
with open(os.path.join(MAST, "systems.toml"), "w", encoding="utf-8") as f:
    f.write('[masters.demo]\nurl = "http://127.0.0.1:5110"\n'
            '[masters.bare]\nname = "configured but no url"\n'
            '[masters.pathy]\nurl = "http://127.0.0.1:5150/"\n')
with open(os.path.join(MAST, "systems.local.toml"), "w", encoding="utf-8") as f:
    f.write('[masters.foo]\nurl = "http://127.0.0.1:5123"\n'
            '[masters.demo]\nurl = "http://127.0.0.1:5199"\n')


class _MJob:                       # masters_config/running_masters read only
    config_dir = MAST              # .config_dir -- a duck is plenty


_mc = au.masters_config(_MJob())
assert _mc == {"demo": 5199, "foo": 5123, "pathy": 5150},     f"masters_config merged wrong: {_mc!r} -- local must override base, " \
    "a url-less row must be absent, a trailing / must still yield the port"

# running_masters keeps exactly the configured masters whose /api/health
# answers -- health() is the arbiter, nothing else.
_real_health = au.health
try:
    au.health = lambda port, timeout=3: ({"version": "x"} if port == 5123
                                         else None)
    _up = au.running_masters(_MJob())
finally:
    au.health = _real_health
assert _up == {"foo": 5123},     f"running_masters must keep only the ports health() answered for: {_up!r}"

# revive_masters, by source: the subprocess it builds needs a full core/
# checkout under a venv to actually run, so the safety-critical shape is
# pinned in the text instead.
_rv = inspect.getsource(au.revive_masters)
assert "if not entries:\n        return" in _rv,     "revive_masters must be a no-op when nothing was running"
# Ordering IS the safety argument: stop the old-code survivor first (it still
# holds the camera and the port), spawn the new master, only then start the
# detection app. Index comparison, not mere presence.
assert _rv.index("dc.stop(") < _rv.index("spawn_master(") < _rv.index("dc.start("),     "revive must stop the old master BEFORE spawning, and spawn BEFORE " \
    "starting the app -- two masters fighting over one camera otherwise"
# systems_dir resolved exactly the way hub_server.py resolves it -- same
# config key, same default, same absolute-path check -- or the revive stops
# and starts apps in a directory the hub never looks at.
with open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8") as f:
    _hub_src = f.read()
_dir_pat = re.compile(r"""\.get\((['"])dir\1,\s*(['"])systems\2\)""")
assert _dir_pat.search(_rv) and "config.systems(" in _rv,     "revive's inline script must resolve systems dir via config.systems() " \
    "and the 'dir' key, the way hub_server.py does"
assert _dir_pat.search(_hub_src) and ".is_absolute()" in _hub_src,     "hub_server.py's own systems_dir resolution moved -- keep revive in step"
assert ".is_absolute()" in _rv,     "revive must handle a relative systems dir the way the hub does"
# One broken system must not strand the rest: each dc.stop and each dc.start
# is best-effort PER SLUG (its own try/except inside the loop), never one
# try/except wrapped around the whole batch.
assert _rv.count("except Exception: pass") >= 2,     "per-slug best-effort went missing -- one system's stop/start failure " \
    "would abort the revive of every other system"
assert "try: dc.stop(" in _rv and "try: dc.start(" in _rv,     "the try must sit on the individual dc.stop/dc.start call, not the loop"
# And the dev-box escape hatch: a Windows venv keeps its python under
# Scripts/, and this very test suite runs there.
assert "Scripts" in _rv, "revive lost its Windows venv fallback"

# The four call sites, each against the release that is (about to be) LIVE:
# do_apply's success path and do_rollback's success path revive onto `target`;
# both failure handlers revive onto `previous` -- the code that was genuinely
# running before anything changed. Any other pairing revives masters onto a
# release the symlink does not point at.
assert ausrc.count("revive_masters(job, target, was_masters)") == 2,     "expected revive-onto-target in do_apply's and do_rollback's success paths"
assert ausrc.count("revive_masters(job, previous, was_masters)") == 2,     "expected revive-onto-previous in both failure handlers"
assert ausrc.count("revive_masters(job,") == 5,     "a revive_masters call appeared with an unexpected release argument " \
    "(expected the def plus exactly the four pinned call sites)"
# ...and the survivor list is captured BEFORE the flip in both entry points,
# same doctrine as was_running: asked afterwards, the answer describes the
# new world, not the one the update must put back.
for _fn, _src in (("do_apply", _apply_src), ("do_rollback", _rb_src)):
    assert "was_masters = running_masters(job)" in _src,         f"{_fn} no longer records which masters were up"
    assert _src.index("was_masters = running_masters(job)") < _src.index("flip("),         f"{_fn} must capture running masters BEFORE the flip"

# ---------------------------------------------------------------------------
# The two-channel model: one repo, a stable and a dev stream told apart by tag.
# ---------------------------------------------------------------------------

# 18. is_dev_tag: the ONLY marker of a developer release is the exact "-dev"
#     suffix, case-insensitive -- tags are typed by people, and "-DEV" reaching
#     a customer box as a customer release is not a mistake worth allowing.
assert upd.is_dev_tag("v2.1.3-dev") is True
assert upd.is_dev_tag("V2.1.3-DEV") is True, "tag case must not matter"
assert upd.is_dev_tag("  v2.1.3-dev  ") is True, "whitespace is tolerated"
assert upd.is_dev_tag("v2.1.3") is False
assert upd.is_dev_tag("") is False and upd.is_dev_tag(None) is False
assert upd.is_dev_tag("v2.1.3-devel") is False
assert upd.is_dev_tag("v2.1.3-dev-rc1") is False,     "the developer suffix must be exact, not a prefix of a longer one"

# 18b. is_customer_tag: a customer release is a PLAIN VERSION, and the customer
#      channel is defined by that rather than by "whatever is not a dev tag".
#      The difference is the whole safety margin: both channels share one repo,
#      so an "anything else" rule would hand every experiment, every release
#      candidate and every mis-typed tag in it to the entire fleet.
assert upd.is_customer_tag("v2.1.3") is True
assert upd.is_customer_tag("2.1.3") is True, "the leading v is optional"
assert upd.is_customer_tag("v2.1") is True and upd.is_customer_tag("v2") is True
assert upd.is_customer_tag("v2.1.3-dev") is False
# The near misses. Each of these is a tag someone could plausibly push, and
# not one of them may reach an appliance: a box offered one would download it,
# fail the manifest's app check, and report a failed update for a release that
# was never meant for it.
for near in ("v2.1.3-devel", "v2.1.3-dev-rc1", "v2.1.3-rc1", "nightly",
             "v2.1.3.4", "v2.1.x", "", None):
    assert upd.is_customer_tag(near) is False,         f"{near!r} must not read as a customer release"
# Belt and braces at the level that actually decides: neither channel picks a
# near-miss tag up, so such a release is offered to nobody rather than to
# everybody.
for near in ("v2.1.9-devel", "v2.1.9-rc1"):
    rows = [{"tag_name": near, "assets": []}]
    assert upd._pick(rows, "stable") is None,         f"{near!r} must not be offered on the customer channel"
    assert upd._pick(rows, "dev") is None,         f"{near!r} must not be offered on the developer channel either"

# 19. _pick: newest matching release by PARSED version, never list order, and
#     never the other channel's. Drafts and prereleases are invisible.


def _rel(tag, **kw):
    row = {"tag_name": tag, "draft": False, "prerelease": False, "assets": []}
    row.update(kw)
    return row


_rels = [_rel("v1.9.0"),                 # the string-compare trap
         _rel("v1.10.0"),               # ...beaten only by numeric parse
         _rel("v2.0.0", draft=True),    # newest of all, but a draft
         _rel("v3.0.0", prerelease=True),
         _rel("v1.10.1-dev"),
         "junk", 42, {}, _rel("")]      # hostile rows must be skipped, not crash
assert upd._pick(_rels, "stable")["tag_name"] == "v1.10.0",     "stable must pick v1.10.0: numerically newest, skipping the draft, the " \
    "prerelease and the dev tag -- a string compare would have picked v1.9.0"
assert upd._pick(list(reversed(_rels)), "stable")["tag_name"] == "v1.10.0",     "the pick must not depend on the list's own order"
assert upd._pick(_rels, "dev")["tag_name"] == "v1.10.1-dev"
assert upd._pick([_rel("v1.0.0-dev")], "stable") is None,     "a dev tag must NEVER be returned for the stable channel"
assert upd._pick([_rel("v1.0.0")], "dev") is None,     "a customer tag must NEVER be returned for the dev channel"
assert upd._pick([], "stable") is None
assert upd._pick(None, "stable") is None
assert upd._pick("not-a-list", "dev") is None

# 20. channel_name / require_channel: unknown channels are refused, case and
#     whitespace are typing noise, and the dev channel does not exist on a box
#     that is not declared a dev box -- a MISSING deploy.toml fails closed.
assert upd.channel_name(None) == "stable", "no channel means the customer one"
assert upd.channel_name("") == "stable"
assert upd.channel_name("  DEV ") == "dev"
assert upd.channel_name("Stable") == "stable"
for junk in ("beta", "developer", "stable,dev", "latest"):
    try:
        upd.channel_name(junk)
        raise AssertionError(f"channel_name accepted {junk!r}")
    except upd.UpdateError:
        pass
CUST = tempfile.mkdtemp(prefix="smoke_update_cust_")   # no deploy.toml at all
DEVBOX = tempfile.mkdtemp(prefix="smoke_update_devbox_")
with open(os.path.join(DEVBOX, "deploy.toml"), "w", encoding="utf-8") as f:
    f.write('mode = "dev"\n')
assert upd.dev_available(CUST) is False,     "a box with no deploy.toml must fail closed to customer"
assert upd.dev_available(DEVBOX) is True
assert upd.require_channel("stable", CUST) == "stable"
assert upd.require_channel(" DEV ", DEVBOX) == "dev"
try:
    upd.require_channel("dev", CUST)
    raise AssertionError("the dev channel was allowed on a customer box")
except upd.UpdateError as e:
    assert "developer channel" in str(e), str(e)

# 21. CHECK_FILE: the two channels cache their answers in DIFFERENT files, so
#     one channel's answer can never be read back as the other's.
assert set(upd.CHECK_FILE) == set(upd.CHANNELS), "every channel needs a cache"
assert upd.CHECK_FILE["stable"] != upd.CHECK_FILE["dev"],     "a shared cache file would hand one channel the other's release"
upd.ota_ready = _real_ota_ready            # back to real for state()
upd.token = _real_token
upd._write_json(upd._p(upd.CHECK_FILE["stable"]),
                {"latest": "v7.7.7", "update_available": True})
upd._write_json(upd._p(upd.CHECK_FILE["dev"]),
                {"latest": "v7.7.8-dev", "update_available": True})
assert upd.state("stable")["latest"] == "v7.7.7",     "the stable state must come from the stable cache"
assert upd.state("dev")["latest"] == "v7.7.8-dev",     "the dev state must come from the dev cache"

# 22. settings(): the channels share one key but NOT one repo (Pipi gate
#     1.0.15-dev, M1; tests/smoke_otadevrepo.py pins the split). The manifest
#     app name is the guard the applier enforces before unpacking a byte.
_s_st = upd.settings(channel="stable")
_s_dev = upd.settings(channel="dev")
assert _s_st["manifest_app"] == upd.MANIFEST_APP
assert _s_dev["manifest_app"] == upd.MANIFEST_APP_DEV,     "the dev channel must demand the dev manifest app, or a customer " \
    "bundle could be installed as a developer build unnoticed"
assert upd.MANIFEST_APP != upd.MANIFEST_APP_DEV
assert _s_st["repo"] != _s_dev["repo"],     "each channel has a release repo of its own -- a dev bundle beside the " \
    "customer ones is readable with every customer box's token"
assert _s_st["channel"] == "stable" and _s_dev["channel"] == "dev"

# 23. check(), functionally: the list endpoint (never /releases/latest), a
#     per-channel ETag, and a 304 that keeps the cached answer WITHOUT
#     cross-contaminating the other channel. Both channels request the same
#     URL, so the one way this goes wrong is a shared ETag: channel A's cached
#     validator making channel B's first real fetch come back 304-empty.
import urllib.error as _uerr                                   # noqa: E402


class _FakeResp:
    def __init__(self, body, etag):
        self._body = json.dumps(body).encode()
        self.headers = {"ETag": etag}

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_api_calls = []
_real_api = upd._api


def _fake_api(path, tok, accept="application/vnd.github+json", headers=None,
              config_dir=None, channel=None):
    _api_calls.append({"path": path, "headers": dict(headers or {})})
    if _fake_api.status == 304:
        raise _uerr.HTTPError(path, 304, "Not Modified", {}, None)
    return _FakeResp(_fake_api.body, _fake_api.etag)


upd._api = _fake_api
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
try:
    for ch in upd.CHANNELS:                     # a clean cache for both
        upd._write_json(upd._p(upd.CHECK_FILE[ch]), {})
    _fake_api.status = 200
    _fake_api.etag = 'W/"one"'
    _fake_api.body = [_rel("v9.9.9"), _rel("v9.9.8-dev")]

    st = upd.check(config_dir=DEVBOX, channel="stable")
    assert _api_calls[-1]["path"] == "/releases?per_page=30",         "check() must LIST releases -- /releases/latest is repo-wide and " \
        "would hand a dev release to customer boxes"
    assert "If-None-Match" not in _api_calls[-1]["headers"],         "a first check has no ETag to send"
    assert st["latest"] == "v9.9.9" and st["channel"] == "stable"
    assert st["update_available"] is True and st["error"] == ""

    dv = upd.check(config_dir=DEVBOX, channel="dev")
    assert "If-None-Match" not in _api_calls[-1]["headers"],         "THE CROSS-CHANNEL ETAG TRAP: the dev channel's first check must not " \
        "send the stable channel's cached ETag -- a 304 there would freeze " \
        "the dev channel on an empty answer forever"
    assert dv["latest"] == "v9.9.8-dev" and dv["update_available"] is True

    # Now both caches are warm. A 304 must keep each channel's OWN answer and
    # still recompute update_available against the running version.
    _fake_api.status = 304
    st2 = upd.check(config_dir=DEVBOX, channel="stable")
    assert _api_calls[-1]["headers"].get("If-None-Match") == 'W/"one"',         "a warmed check must send its own channel's ETag"
    assert st2["latest"] == "v9.9.9" and st2["error"] == ""
    assert st2["update_available"] is True,         "update_available must be recomputed on the 304 path, not defaulted"
    dv2 = upd.check(config_dir=DEVBOX, channel="dev")
    assert dv2["latest"] == "v9.9.8-dev",         "a 304 on the dev channel kept the wrong channel's answer"
    assert (upd._read_json(upd._p(upd.CHECK_FILE["stable"]))["latest"]
            == "v9.9.9"), "the dev check overwrote the stable cache file"

    # force=True must bypass the validator: "Check now" is a person asking for
    # the truth, not for a cheap 304.
    _fake_api.status = 200
    upd.check(force=True, config_dir=DEVBOX, channel="stable")
    assert "If-None-Match" not in _api_calls[-1]["headers"],         "force=True must not send If-None-Match"

    # An empty channel stays HONESTLY empty across a 304: the first check says
    # "no release published", and the 304 that follows must keep saying it --
    # not blank the message and leave the panel silent.
    _fake_api.body = [_rel("v9.9.9")]           # customer releases only
    _fake_api.etag = 'W/"two"'
    dv3 = upd.check(force=True, config_dir=DEVBOX, channel="dev")
    assert dv3["latest"] == "" and "no release has been published" in dv3["error"]
    _fake_api.status = 304
    dv4 = upd.check(config_dir=DEVBOX, channel="dev")
    assert "no release has been published" in dv4["error"],         "a 304 on an empty channel must keep explaining WHY nothing is " \
        "offered -- a blank error here reads as 'up to date' with no version"
finally:
    upd._api = _real_api
    upd.token = _real_token

# 24. apply() across channels. The dev channel on a non-dev box is refused
#     before anything else is even looked at; and a version approved on one
#     channel can never be applied via the other, because each channel pins
#     the approval against ITS OWN cache.
upd.ota_ready = lambda: True
upd.job_running = lambda: False
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
try:
    upd.apply("9.9.9", config_dir=CUST, channel="dev")
    raise AssertionError("apply() took a dev-channel job on a customer box")
except upd.UpdateError as e:
    assert "developer channel" in str(e),         "the refusal must be the channel rule, not a later incidental one: " + str(e)
upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {"latest": "v9.9.9"})
upd._write_json(upd._p(upd.CHECK_FILE["dev"]), {"latest": "v9.9.8-dev"})
try:
    upd.apply("9.9.9", config_dir=DEVBOX, channel="dev")
    raise AssertionError("a stable-approved version was applied via the dev "
                         "channel")
except upd.UpdateError as e:
    assert "no longer the latest" in str(e), str(e)
try:
    upd.apply("9.9.8-dev", config_dir=DEVBOX, channel="stable")
    raise AssertionError("a dev-approved version was applied via the stable "
                         "channel")
except upd.UpdateError as e:
    assert "no longer the latest" in str(e), str(e)
# The job file carries the channel's identity for the applier: the channel,
# the token key to read, and the channel's OWN manifest app.
_job_dev = upd._write_job("apply", "9.9.8-dev", config_dir=DEVBOX, channel="dev")
assert _job_dev["channel"] == "dev"
assert _job_dev["token_key"] == "dev_token",     "the dev job must read the DEV token -- the customer token never goes " \
    "to the dev repo"
assert _job_dev["repo"] == upd.settings(DEVBOX, "dev")["repo"]
assert _job_dev["manifest_app"] == upd.MANIFEST_APP_DEV,     "a dev job must demand the dev manifest app -- this is the guard that " \
    "actually bites, since the token opens both bundles"
_job_st = upd._write_job("apply", "9.9.9", config_dir=DEVBOX, channel="stable")
assert _job_st["manifest_app"] == upd.MANIFEST_APP
assert _job_st["assets"] == {},     "a job's assets must come from its OWN channel's cache"
try:
    upd._write_job("apply", "9.9.9", config_dir=CUST, channel="dev")
    raise AssertionError("_write_job wrote a dev job on a customer box")
except upd.UpdateError:
    pass
upd.ota_ready = _real_ota_ready
upd.job_running = _real_job_running
upd.token = _real_token

# 25. The applier's side of the channel handshake. An OLD job.json (written
#     before channels existed) meeting the NEW applier must default to the
#     stable channel and the classic token key -- that pairing happens exactly
#     once, on the first update that crosses the feature boundary, because the
#     applier is always staged from the SAME running tree that wrote the job.
OLDJOB = tempfile.mkdtemp(prefix="smoke_update_oldjob_")
with open(os.path.join(OLDJOB, "job.json"), "w", encoding="utf-8") as f:
    json.dump({"mode": "apply", "version": "9.9.9", "current": "1.0.0"}, f)
_oj = au.Job(OLDJOB)
assert _oj.channel == "stable" and _oj.token_key == "token",     "a pre-channel job.json must default to stable + the classic token key"
# read_token reads EXACTLY the key the job names -- a prefix match would read
# `token_expires_at` as the token, and `dev_token` must not answer for `token`.
TOKDIR = tempfile.mkdtemp(prefix="smoke_update_tok_")
with open(os.path.join(TOKDIR, "update.local.toml"), "w", encoding="utf-8") as f:
    f.write('token_expires_at = "2027-01-01"\n'
            'dev_token = "ghp_dev"\n'
            'token = "ghp_customer"\n')
assert au.read_token(TOKDIR, "token") == "ghp_customer",     "read_token must match the key exactly, never by prefix"
assert au.read_token(TOKDIR, "dev_token") == "ghp_dev"
assert au.read_token(TOKDIR, "missing") == "", "a missing key is '', not a crash"
assert au.read_token(os.path.join(TOKDIR, "nope"), "token") == ""
assert "read_token(job.config_dir, job.token_key)" in ausrc,     "the applier must read the token by the JOB'S token_key -- reading a " \
    "hardcoded key spends the wrong credential the day the tokens split"

# 26. Source assertions for the channel gates and the header bell.
# The dev branch of _gate: developer account AND dev box, checked in that
# route, after the admin gate that _gate opens with.
_gate_src = rsrc.split("def _gate", 1)[1].split("\ndef ", 1)[0]
assert "_require_developer(" in _gate_src,     "_gate's dev branch must demand a developer ACCOUNT"
assert "dev_available(" in _gate_src,     "_gate's dev branch must also demand a dev BOX -- both locks, both routes"
assert _gate_src.index("_require_admin(") < _gate_src.index("_require_developer("),     "admin is the outer gate; developer is the extra lock on top"
# The service never asks GitHub for /releases/latest: that endpoint is
# repo-wide and would hand the newest DEV release to every customer box.
usrc = inspect.getsource(upd)
assert '_api("/releases/latest"' not in usrc and "'/releases/latest'" not in usrc,     "check() must never call /releases/latest -- it ignores channels"
assert '"/releases?per_page=' in usrc, "check() must list and filter releases"
# HubShell: the Platform Update nav item and its title are gone; the bell
# (NotificationCenter) is mounted in the header for every signed-in account
# (the Inbox is everyone's), but the UPDATE poll inside it runs only for an
# admin -- a non-admin's browser must not poll an endpoint that 403s at it
# for the whole session.
with open(os.path.join(ROOT, "frontend", "src", "components", "HubShell.jsx"),
          encoding="utf-8") as f:
    hsrc = f.read()
assert 'id: "update"' not in hsrc, "the Platform Update nav item came back"
_titles_src = hsrc.split("const TITLES", 1)[1].split("};", 1)[0]
assert "update:" not in _titles_src, "TITLES still names the removed page"
assert re.search(r"<NotificationCenter admin=\{!!user\?\.is_admin\}", hsrc),     "NotificationCenter must be told whether the account is an admin"
with open(os.path.join(ROOT, "frontend", "src", "components", "NotificationCenter.jsx"),
          encoding="utf-8") as f:
    _nsrc = f.read()
assert "{admin && <UpdateWatch" in _nsrc, "the update poll must run only for an admin"
assert 'usePoll("/api/hub/update"' not in _nsrc, "the update poll must not run unconditionally"
# NotificationCenter: the dev channel is watched ONLY when the server offered
# it -- ChannelWatch is a conditionally RENDERED component precisely so the
# poll does not exist at all on boxes where the answer is a guaranteed 404.
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "NotificationCenter.jsx"), encoding="utf-8") as f:
    nsrc = f.read()
assert nsrc.count("<ChannelWatch") == 1
assert re.search(r"devAvailable\s*&&\s*\(\s*<ChannelWatch", nsrc),     "ChannelWatch must render only behind devAvailable -- unconditional, " \
    "it polls a 404 once a minute on every customer box"
assert 'setChannel("stable")' in nsrc,     "losing the dev channel must drop the selection back to stable, or the " \
    "panel keeps asking for a channel the server answers 404 to"

print("smoke_update OK")
