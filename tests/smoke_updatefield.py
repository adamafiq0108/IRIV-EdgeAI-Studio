# -*- coding: utf-8 -*-
# What the first real OTA update on real hardware taught us (v2.1.1 -> v2.1.3
# on the dev Pi). Four defects, each invisible on every bench and fatal in the
# field, each pinned here so it cannot come back:
#
#   1. job.json recorded the config root as the relative "config"; the applier
#      runs as its own systemd unit with no working directory, so it resolved
#      it from / and every apply failed preflight on a box whose token was
#      exactly where it belonged.
#   2. _store_say printed its warning banner to STDOUT -- the very stream
#      install.sh captures to learn the store root -- so the banner became the
#      recorded UPDATE_DIR and the apply unit's ExecStart.
#   3. live_link()/releases_dir() hardcoded /opt; a --target install answered
#      ota_ready() False on a perfectly good layout.
#   4. revive_masters stopped the detection APP but not the MASTER (a separate
#      process KillMode=process deliberately keeps alive), so the update
#      reported success with a master still serving the OLD release; and
#      nothing verified that the processes just replaced were replaced.
#
# Plus the residue the same classes of mistake left elsewhere: a relative
# store_dir crossing the same process boundary, recover.py trusting the same
# conf file update.py learned to distrust, and a download loop that fsync'd an
# identical progress line hundreds of times into the Pi's log.
import contextlib
import hashlib
import importlib.util
import inspect
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                        # noqa: E402
from core.services import update as upd                        # noqa: E402


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


au = _load(os.path.join(ROOT, "scripts", "apply_update.py"), "_field_applier")
with open(os.path.join(ROOT, "scripts", "apply_update.py"),
          encoding="utf-8") as f:
    ausrc = f.read()

SAND = tempfile.mkdtemp(prefix="smoke_updatefield_")

# ---------------------------------------------------------------------------
# 1. The config root must survive the process boundary. _job_config_dir()
#    always answers ABSOLUTE, and expressed THROUGH the live symlink whenever
#    the config root lives inside the release -- the job outlives the flip, so
#    a path pinned to the outgoing release's real directory would read the
#    replaced version's config after the swap.
# ---------------------------------------------------------------------------
link = os.path.join(SAND, "live")                  # the symlink's own path
target = os.path.join(SAND, "releases", "v1.0.0")  # what it resolves to
cfg_inside = os.path.join(target, "config")
os.makedirs(cfg_inside)
outside = os.path.join(SAND, "elsewhere", "config")
os.makedirs(outside)

_real_live_link = upd.live_link
_real_realpath = os.path.realpath
upd.live_link = lambda: link
os.path.realpath = lambda p, _r=_real_realpath: (target if p == link else _r(p))
try:
    got = upd._job_config_dir(cfg_inside)
    assert os.path.isabs(got), "the applier has no working directory -- a " \
        "relative config root resolves from / and preflight dies on the token"
    assert got == os.path.join(link, "config"),         "a config root INSIDE the release must be expressed through the live " \
        f"symlink so it follows the flip -- got {got!r}"
    # The config root IS the release root: the answer is the link itself.
    assert upd._job_config_dir(target) == link
    # Outside the release: left as a plain absolute path, never rewritten
    # under the link (that would follow the flip into a tree it never lived in).
    got2 = upd._job_config_dir(outside)
    assert got2 == os.path.abspath(outside), got2
    assert not got2.startswith(link + os.sep)
    # The parent of the release (sibling trap: relpath == "..").
    par = upd._job_config_dir(os.path.join(SAND, "releases"))
    assert os.path.isabs(par) and not par.startswith(link + os.sep)
    # A relative input (the very shape that broke the Pi) still comes back
    # absolute, whatever else happens.
    assert os.path.isabs(upd._job_config_dir("config"))

    # A live link that cannot be resolved must degrade, not crash: realpath
    # raising is the applier-never-starts path.
    def _boom(p, _r=_real_realpath):
        raise OSError("no such link")
    os.path.realpath = _boom
    assert upd._job_config_dir(cfg_inside) == os.path.abspath(cfg_inside),         "an unresolvable live link must fall back to the plain absolute path"
    # ...and a link on another drive (Windows: relpath raises ValueError).
    os.path.realpath = lambda p, _r=_real_realpath: (
        "Z:\\other" if p == link else _r(p))
    if os.name == "nt":
        assert upd._job_config_dir(cfg_inside) == os.path.abspath(cfg_inside),             "a cross-drive live link must fall back, not crash on relpath"
finally:
    os.path.realpath = _real_realpath

# A live link that simply does not exist (no patching): no crash, absolute out.
upd.live_link = lambda: os.path.join(SAND, "no-such-link")
assert os.path.isabs(upd._job_config_dir(cfg_inside))
upd.live_link = _real_live_link

# And the writer uses it. _write_job recording bare config.root() is exactly
# the defect: the hub's relative "config" written into a file another process
# reads from /.
_wsrc = inspect.getsource(upd._write_job)
assert '"config_dir": _job_config_dir(config_dir)' in _wsrc,     "_write_job must record the config root via _job_config_dir"
assert "config.root(" not in _wsrc,     "_write_job must never record bare config.root(...) -- that is the " \
    "relative path that broke every apply on the first real hardware run"
# do_apply's refusal names the file it looked in: "no token is configured" on
# a box that plainly has one sent someone re-pasting a token that was never
# the problem.
assert "no update token was found in" in ausrc,     "the applier's token refusal must name the file it looked in"

# ---------------------------------------------------------------------------
# 2. A diagnostic about a path must not become the path. _store_say speaks on
#    STDERR only -- stdout is where install.sh's `print(store_dir())` capture
#    listens -- and still says each message once.
# ---------------------------------------------------------------------------
_msg = f"[STORE] smoke_updatefield banner {os.getpid()}"
_out, _err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(_out), contextlib.redirect_stderr(_err):
    config._store_say(_msg)
assert _out.getvalue() == "",     "_store_say wrote to stdout -- the installer captures stdout as the " \
    "store root, and a real appliance ended up with this banner inside its " \
    "apply unit's ExecStart"
assert _msg in _err.getvalue(), "the warning must still be SAID -- on stderr"
_out2, _err2 = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(_out2), contextlib.redirect_stderr(_err2):
    config._store_say(_msg)
assert _out2.getvalue() == "" and _err2.getvalue() == "",     "_store_say must still de-duplicate: once per process, per message"

# Belt AND braces in the installer: the store-root capture takes the LAST
# line regardless of what the resolution printed first.
with open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8") as f:
    inst = f.read()
_caps = [ln for ln in inst.splitlines()
         if "store_dir()" in ln and "$(" in ln]
assert _caps, "install.sh no longer asks the product for the store root"
for ln in _caps:
    assert "tail -n 1" in ln,         "every store_dir() capture must pipe through `tail -n 1`: " + ln.strip()
# No OTHER single-line capture of a `python -c` that imports core may take
# raw stdout: any such line must also take only the last line.
for ln in inst.splitlines():
    if "$(" in ln and "from core" in ln and "print(" in ln:
        assert "tail -n 1" in ln,             "a capture of core's stdout without `tail -n 1`: " + ln.strip()
# run_all.sh's PLAN heredoc also imports core; its consumers must key on line
# PREFIX (awk '/^hub /', grep '^ERR:') so a stray stdout line is ignored, not
# parsed as the plan.
with open(os.path.join(ROOT, "scripts", "run_all.sh"), encoding="utf-8") as f:
    runsh = f.read()
assert "/^hub /" in runsh and "^ERR:" in runsh,     "run_all.sh must select PLAN lines by prefix -- a stray line printed by " \
    "core would otherwise corrupt the plan the way the banner corrupted " \
    "update.conf"

# ---------------------------------------------------------------------------
# 3. The install layout is read, not assumed. _installed() trusts only an
#    absolute value out of /etc/irivai/update.conf; everything else degrades
#    to the built-in default -- on the path that decides whether the Update
#    button works at all.
# ---------------------------------------------------------------------------
_conf = os.path.join(SAND, "update.conf")
_real_conf = upd.INSTALL_CONF
upd.INSTALL_CONF = _conf
try:
    # Missing file: '' from _installed, defaults from the callers.
    assert upd._installed("LIVE_LINK") == ""
    assert upd.live_link() == "/opt/irivai-systor",         "with no conf, live_link must fall back to the /opt default"
    assert os.path.isabs(upd.releases_dir()),         "with no conf, releases_dir must still answer an absolute path"
    # Malformed, empty and RELATIVE values are all "not to be trusted": the
    # installer captured a warning banner into a value once already.
    with open(_conf, "w", encoding="utf-8") as f:
        f.write("this line has no equals sign\n"
                "LIVE_LINK=\n"
                "RELEASES_DIR=relative/path\n"
                "UPDATE_DIR=[STORE] WARNING: more than one store root\n")
    assert upd._installed("LIVE_LINK") == "", "an empty value is no value"
    assert upd._installed("RELEASES_DIR") == "",         "a RELATIVE value must be refused -- only absolute paths may steer " \
        "a process that runs from /"
    assert upd._installed("UPDATE_DIR") == "",         "the captured-banner value must be refused, not returned"
    assert upd._installed("MISSING_KEY") == ""
    assert upd.live_link() == "/opt/irivai-systor"
    # A good conf: both readers follow it (this is the --target fix -- the
    # dev Pi lives under /home/pi and ota_ready() said False there).
    # Host-absolute paths, because _installed judges with os.path.isabs and a
    # newer Windows Python calls "/home/pi" relative; the Pi's own shape is
    # asserted below where the host agrees with it.
    _lp = os.path.join(SAND, "live-target")
    _rp = os.path.join(SAND, "releases-target")
    with open(_conf, "w", encoding="utf-8") as f:
        f.write(f'LIVE_LINK="{_lp}"\n'
                f"RELEASES_DIR={_rp}\n")
    assert upd.live_link() == _lp,         "live_link must read the conf (quotes stripped) -- hardcoding /opt " \
        "refused updates on every --target install"
    assert upd.releases_dir() == _rp
    if os.path.isabs("/home/pi"):          # POSIX hosts: the Pi's own shape
        with open(_conf, "w", encoding="utf-8") as f:
            f.write("LIVE_LINK=/home/pi/irivai-systor\n")
        assert upd.live_link() == "/home/pi/irivai-systor"
finally:
    upd.INSTALL_CONF = _real_conf

# recover.py reads the SAME file as root at boot and flips symlinks with what
# it finds -- it must apply the SAME absolute-only rule, not trust a value
# update.py just learned to refuse.
rec = _load(os.path.join(ROOT, "scripts", "recover.py"), "_field_recover")
_rconf = os.path.join(SAND, "recover.conf")
with open(_rconf, "w", encoding="utf-8") as f:
    f.write("LIVE_LINK=relative/link\n"
            "RELEASES_DIR=[STORE] WARNING: banner\n"
            "UPDATE_DIR=not/absolute\n")
rec.CONF = _rconf
assert rec._read_conf() == "",     "recover.py must refuse a non-absolute UPDATE_DIR"
assert rec.LIVE_LINK == "/opt/irivai-systor",     "recover.py took a RELATIVE live link from the conf -- run from / at " \
    "boot, its flip would build a symlink somewhere under /"
assert rec.RELEASES == "/opt/irivai-releases",     "recover.py trusted a banner value update.py would have refused"
with open(_rconf, "w", encoding="utf-8") as f:
    f.write('LIVE_LINK="/home/pi/irivai-systor"\n'
            "RELEASES_DIR=/home/pi/irivai-releases\n"
            "UPDATE_DIR=/mnt/nvme/cytron/store/update\n")
assert rec._read_conf() == "/mnt/nvme/cytron/store/update"
assert rec.LIVE_LINK == "/home/pi/irivai-systor",     "recover.py must still FOLLOW a good conf (quotes stripped)"
assert rec.RELEASES == "/home/pi/irivai-releases"

# ---------------------------------------------------------------------------
# 4. Reviving a master means stopping the MASTER, not just its detection app
#    -- and then proving the replacement actually answers.
# ---------------------------------------------------------------------------
_rv = inspect.getsource(au.revive_masters)
_i_stop_app = _rv.index("dc.stop(")
_i_stop_master = _rv.index("stop_master(")
_i_spawn = _rv.index("spawn_master(")
assert _i_stop_master < _i_spawn,     "revive must stop the OLD master before spawning the new one -- " \
    "dc.stop() alone ends only the detection app, and the surviving master " \
    "(KillMode=process keeps it alive) still holds the port"
assert "try: provision.stop_master(" in _rv,     "stop_master must be per-entry and best-effort, like dc.stop"
assert "'http://127.0.0.1:%d' % e['port']" in _rv,     "stop_master must be aimed at the LOCAL listener on the entry's port"
# A signalled process does not exit within the same instruction: there must
# be a wait between the stop loop and the spawn loop.
_i_sleep = _rv.index("time.sleep", _i_stop_master)
assert _i_sleep < _i_spawn,     "revive lost the wait between stopping the old master and spawning the " \
    "new one -- the port has to actually be free before the replacement binds"

# verify_masters: called on BOTH forward paths, and on NEITHER rollback
# handler. The handlers are restoring the previous release; handing them an
# exception strands the box mid-recovery -- worse than anything the check
# guards against.
_apply_src = ausrc.split("def do_apply", 1)[1].split("\ndef ", 1)[0]
_rb_src = ausrc.split("def do_rollback", 1)[1].split("\ndef ", 1)[0]
for _fn, _src in (("do_apply", _apply_src), ("do_rollback", _rb_src)):
    _fwd, _handler = _src.split("except Exception", 1)
    assert "verify_masters(job, was_masters)" in _fwd,         f"{_fn}'s forward path must verify the revived masters -- every " \
        "other signal said success while a master served the OLD release"
    assert _fwd.index("revive_masters(") < _fwd.index("verify_masters("),         f"{_fn} must verify AFTER reviving"
    assert "verify_masters(" not in _handler,         f"{_fn}'s rollback handler must NOT verify -- raising while the " \
        "previous release is being put back strands the box mid-recovery"
assert ausrc.count("verify_masters(job, was_masters)") == 2,     "expected exactly the two forward-path verify_masters calls"

# verify_masters, functionally: health() is the arbiter, Fail is the verdict.
# Driven on a fake clock so the 90-second budget costs no test time.


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _VJob:
    def __init__(self, version="2.1.4"):
        self.msgs = []
        # The release being installed. verify_masters compares it against what
        # each master reports, so a stub without one hides the whole check.
        self.version = version

    def say(self, phase, percent, detail="", error=""):
        self.msgs.append((phase, percent, detail))


_real_time, _real_health = au.time, au.health
_clk = _Clock()
au.time = _clk
try:
    # Empty set: returns immediately, consulting nothing.
    def _never(port, timeout=3):
        raise AssertionError("health consulted for an empty entry set")
    au.health = _never
    _t0 = _clk.time()
    au.verify_masters(_VJob(), {})
    assert _clk.time() == _t0, "an empty set must not wait at all"

    # Everyone answers: returns cleanly and says so.
    au.health = lambda port, timeout=3: {"ok": True}
    _j = _VJob()
    au.verify_masters(_j, {"alpha": 5110, "beta": 5111}, budget=30)
    assert any("back on the new code" in m[2] for m in _j.msgs),         "a clean verify must report the systems back"

    # One master never answers: Fail, naming exactly the missing one -- this
    # raise is what turns a silent old-code survivor into a rollback.
    au.health = lambda port, timeout=3: ({"ok": True} if port == 5110 else None)
    _j2 = _VJob()
    try:
        au.verify_masters(_j2, {"alpha": 5110, "beta": 5111}, budget=30)
        raise AssertionError("verify_masters returned with a master missing")
    except au.Fail as e:
        assert str(e).endswith(": beta"),             "the Fail must name exactly the systems that never answered: " + str(e)
    assert any("waiting for 1 system" in m[2] for m in _j2.msgs),         "verify must report what it is waiting on while the budget runs"

    # ANSWERING IS NOT ENOUGH. The field failure this check exists for ends
    # with a perfectly healthy master answering the port -- the OLD one, which
    # outlived stop_master and left spawn_master nothing to bind. Only the
    # version tells them apart, so a master reporting the previous release
    # must fail the gate exactly as a silent port does.
    au.health = lambda port, timeout=3: (
        {"ok": True, "version": "2.1.4"} if port == 5110
        else {"ok": True, "version": "2.1.1"})
    _j3 = _VJob("2.1.4")
    try:
        au.verify_masters(_j3, {"alpha": 5110, "beta": 5111}, budget=30)
        raise AssertionError("a master still on the OLD release passed verify "
                             "-- this is the exact defect the check exists for")
    except au.Fail as e:
        assert "beta" in str(e) and "2.1.1" in str(e),             "the Fail must name the system AND the release it is stuck on: " + str(e)

    # A leading v on either side is spelling, not disagreement.
    au.health = lambda port, timeout=3: {"ok": True, "version": "v2.1.4"}
    au.verify_masters(_VJob("2.1.4"), {"alpha": 5110}, budget=30)

    # Tolerated when ABSENT: masters from a release older than the one that
    # added the field cannot report it, and the first update off such a
    # release must not roll itself back over a blind spot it cannot avoid.
    au.health = lambda port, timeout=3: {"ok": True}
    au.verify_masters(_VJob("2.1.4"), {"alpha": 5110}, budget=30)
finally:
    au.time = _real_time
    au.health = _real_health

# ---------------------------------------------------------------------------
# 5. The rest of job.json crosses the same process boundary: every path in it
#    must be absolute, even when store_dir() honours a relative CYTRON_STORE
#    at its word.
# ---------------------------------------------------------------------------
_JCFG = tempfile.mkdtemp(prefix="smoke_updatefield_cfg_")
_real_store = config.store_dir
_cwd = os.getcwd()
config.store_dir = lambda: "rel-store"     # what a relative CYTRON_STORE yields
os.chdir(SAND)                             # keep the side-written dirs in SAND
try:
    _job = upd._write_job("apply", "9.9.9", config_dir=_JCFG)
finally:
    os.chdir(_cwd)
    config.store_dir = _real_store
for _key in ("config_dir", "store_dir", "releases_dir", "live_link"):
    _v = _job[_key]
    # Absolute for the host, or the appliance's own POSIX shape ("/opt/...",
    # which a newer Windows Python calls relative): what may never appear is a
    # path the applier would resolve against its working directory.
    assert os.path.isabs(_v) or _v.startswith("/"),         f"job.json's {_key} is {_v!r} -- a relative path here is " \
        "resolved by the applier from /, the exact defect that failed every " \
        "apply on the first real hardware run"

# ---------------------------------------------------------------------------
# 6. The download loop says a progress line ONCE. On the Pi it wrote the same
#    "N of M MB" hundreds of times -- and each say() rewrites and fsyncs
#    status.json, against the very flash the download is racing.
# ---------------------------------------------------------------------------


class _FakeResp:
    status = 200

    def __init__(self, data):
        self._data, self._off = data, 0

    def read(self, n=-1):
        chunk = self._data[self._off:self._off + (n if n > 0 else len(self._data))]
        self._off += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_data = os.urandom(au.CHUNK * 70)          # 70 chunks: percent AND MB repeat
_sha = hashlib.sha256(_data).hexdigest()
_real_open = au._open
au._open = lambda url, tok, accept=None, headers=None: _FakeResp(_data)
_said = []
try:
    au.fetch_bundle("u", "t", os.path.join(SAND, "bundle.bin"), _sha,
                    len(_data), lambda ph, pc, dt: _said.append((ph, pc, dt)))
finally:
    au._open = _real_open
assert _said, "progress must still be reported"
assert len(_said) == len(set(_said)),     f"fetch_bundle repeated identical progress lines ({len(_said)} said, " \
    f"{len(set(_said))} distinct) -- the Pi logged the same line hundreds " \
    "of times, one fsync'd status.json rewrite each"
assert len(_said) < 70,     "fetch_bundle must say fewer lines than it reads chunks"
with open(os.path.join(SAND, "bundle.bin"), "rb") as f:
    assert f.read() == _data, "de-duplicating the reporting corrupted the file"

# ---------------------------------------------------------------------------
# 8. An update must not eat the models the box detects with.
#
# It did, on a real board. A system's detector points at
# <release>/systems/<slug>/models -- INSIDE the tree the flip replaces -- and
# a release bundle ships no weights (publish_release.py skips `models`
# entirely: they are per-accelerator and tens of megabytes). So the new tree
# arrived with that directory empty, and one update later the release still
# holding them was pruned. Nothing reported it: the systems kept running on
# the model already in memory, and the Models page simply showed "no weights"
# beside a card that could not be started.

_old = os.path.join(SAND, "rel_old")
_new = os.path.join(SAND, "rel_new")
_om = os.path.join(_old, "systems", "demo", "models")
os.makedirs(os.path.join(_om, "hailo10h"), exist_ok=True)
with open(os.path.join(_om, "hailo10h", "yolov8m.hef"), "wb") as f:
    f.write(b"WEIGHTS")
with open(os.path.join(_om, "yolov8m.labels"), "w", encoding="utf-8") as f:
    f.write("person\n")
with open(os.path.join(_om, "yolov8m.json"), "w", encoding="utf-8") as f:
    f.write('{"name": "stale"}')
_o2 = os.path.join(_old, "systems", "line2", "models")
os.makedirs(_o2, exist_ok=True)
with open(os.path.join(_o2, "custom.hef"), "wb") as f:
    f.write(b"MINE")
# The new release as a bundle unpacks it: a manifest may ship, weights never.
_nm = os.path.join(_new, "systems", "demo", "models")
os.makedirs(_nm, exist_ok=True)
with open(os.path.join(_nm, "yolov8m.json"), "w", encoding="utf-8") as f:
    f.write('{"name": "shipped"}')

_carried = au.carry_models(_old, _new)
assert _carried, "carry_models reported nothing carried"
with open(os.path.join(_nm, "hailo10h", "yolov8m.hef"), "rb") as f:
    assert f.read() == b"WEIGHTS",         "the weights a system detects with must reach the new release"
assert os.path.isfile(os.path.join(_nm, "yolov8m.labels")),     "the labels beside the weights must come too -- a detector reads both, " \
    "and carrying one without the other fails just as silently"
assert os.path.isfile(os.path.join(_new, "systems", "line2", "models",
                                   "custom.hef")),     "every system's models come across, not only the first"
with open(os.path.join(_nm, "yolov8m.json"), encoding="utf-8") as f:
    assert "shipped" in f.read(),         "a file the RELEASE shipped must win -- a stale copy from the tree " \
        "being replaced must not revert it"
assert os.path.isfile(os.path.join(_om, "hailo10h", "yolov8m.hef")),     "copied, never moved: the old release has to stay complete enough to " \
    "roll back to"
# Both directions of the flip carry them, and the source assertion says so:
# a rollback that restored the code but not the weights would leave the box
# on a release that cannot detect.
_ap_src = open(os.path.join(ROOT, "scripts", "apply_update.py"),
               encoding="utf-8").read()
assert _ap_src.count("carry_models(") >= 3,     "carry_models must run on the way forward AND on the way back, not just " \
    "where the loss was noticed"

print("smoke_updatefield OK")
