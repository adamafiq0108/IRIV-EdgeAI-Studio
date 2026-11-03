# Before an update check or an OTA download is called "no internet", a dead
# default route beside a live one is moved once (routeheal) and the request
# is made again.
#
# Board .22: the update check died with "[Errno 113] No route to host"
# because eth0's Modbus gateway outranked eth1's router. Pinned:
#   1. check(): an offline failure + a heal that moved the route = one retry,
#      and the check succeeds; the heal runs once
#   2. a heal that changes nothing: no retry, Boboiboy's CHECK_OFFLINE wording
#   3. GitHub ANSWERING (an HTTPError, even 404) or a non-offline failure
#      (TLS) never runs the heal
#   4. the applier's offline() classifies like update.reach_error
#   5. the applier's manifest fetch: retried once after a heal, raised
#      as-is without one
#   6. fetch_bundle: a heal buys one extra attempt with no backoff; without
#      it the budget and the final wording are unchanged
#   7. heal_route runs once per job, and not at all on a live tree from
#      before routeheal; as the config's owner, never imports core
#   8. the runuser prefix only when the caller is root and the owner is not
#      (runuser refuses non-root callers; the apply unit runs as the owner);
#      a root caller with a root-owned or nameless config runs NOTHING
#      (Pipi, 1.0.15 gate L1: never the live tree as root)
#
#   py tests/smoke_updateheal.py
import importlib.util
import io
import os
import ssl
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import update as upd                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
NO_ROUTE = urllib.error.URLError(OSError(113, "No route to host"))
state = {"fail": [], "asked": 0, "healed": 0, "heal_answer": True}


class Resp(io.BytesIO):
    headers = {"ETag": '"e1"'}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


RELEASES = b'[{"tag_name": "v9.9.9", "body": "notes", "assets": []}]'


def fake_api(path, tok, accept="", headers=None, config_dir=None, channel=None):
    state["asked"] += 1
    if state["fail"]:
        raise state["fail"].pop(0)
    return Resp(RELEASES)


def fake_heal(config_dir=None):
    state["healed"] += 1
    return state["heal_answer"]


upd._api = fake_api
upd._heal = fake_heal
upd.token = lambda config_dir=None, channel=None: "ghp_fake"


def run(fail, heal_answer=True):
    state.update(fail=list(fail), asked=0, healed=0, heal_answer=heal_answer)
    upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})
    return upd.check(config_dir=CFG, channel="stable")


# -- 1. healed and retried ---------------------------------------------------------
out = run([NO_ROUTE])
check("errno 113 + a heal that moved the route: asked again and the check succeeds",
      out.get("error") == "" and out.get("latest") == "v9.9.9" and state["asked"] == 2,
      (out, state))
check("...the heal ran once", state["healed"] == 1, state)

# -- 2. nothing to heal ---------------------------------------------------------------
out = run([NO_ROUTE], heal_answer=False)
check("a heal that changes nothing: no retry, the worded offline sentence",
      out.get("error") == upd.CHECK_OFFLINE and state["asked"] == 1, (out, state))
out = run([NO_ROUTE, NO_ROUTE])
check("healed but still offline: one retry only, then the worded sentence",
      out.get("error") == upd.CHECK_OFFLINE and state["asked"] == 2
      and state["healed"] == 1, (out, state))

# -- 3. GitHub answered, or not an offline failure --------------------------------------
out = run([urllib.error.HTTPError("u", 404, "nf", {}, None)])
check("an HTTP 404 never runs the heal", state["healed"] == 0
      and out.get("error", "").startswith("no releases found"), (out, state))
out = run([urllib.error.URLError(ssl.SSLError("certificate verify failed"))])
check("a TLS failure never runs the heal", state["healed"] == 0
      and out.get("error") == upd.CHECK_UNREACHABLE, (out, state))

# -- the applier ---------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "_heal_applier", os.path.join(ROOT, "scripts", "apply_update.py"))
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)

# -- 4. offline() -------------------------------------------------------------------------
check("offline(): errno 113 / 101 / timeout yes; HTTP 404 and TLS no",
      au.offline(NO_ROUTE) and au.offline(urllib.error.URLError(OSError(101, "x")))
      and au.offline(TimeoutError()) and not au.offline(
          urllib.error.HTTPError("u", 404, "nf", {}, None))
      and not au.offline(urllib.error.URLError(ssl.SSLError("bad cert"))))
check("...the same errno set as update.reach_error", au.OFFLINE_ERRNOS == upd.OFFLINE_ERRNOS)


class FakeJob:
    healed = False


calls = {"n": 0}


def flaky(fails):
    def fn(*a):
        calls["n"] += 1
        if calls["n"] <= fails:
            raise NO_ROUTE
        return b"ok"
    return fn


# -- 5. fetch_healing ------------------------------------------------------------------------
healed = {"n": 0, "answer": True}


def fake_heal_route(job):
    healed["n"] += 1
    return healed["answer"]


au.heal_route = fake_heal_route
calls["n"] = 0
check("manifest fetch: healed, retried, answered",
      au.fetch_healing(FakeJob(), flaky(1)) == b"ok" and calls["n"] == 2)
healed["answer"] = False
calls["n"] = 0
try:
    au.fetch_healing(FakeJob(), flaky(1))
    raised = False
except urllib.error.URLError:
    raised = True
check("...no heal: the original error is raised, no retry", raised and calls["n"] == 1)

# -- 6. fetch_bundle ---------------------------------------------------------------------------
sleeps = []
au.time.sleep = lambda s: sleeps.append(s)
payload = b"x" * 1000
import hashlib                                                  # noqa: E402
sha = hashlib.sha256(payload).hexdigest()
opens = {"n": 0, "fails": 0}


class BResp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_open(url, tok, accept=None, headers=None):
    opens["n"] += 1
    if opens["n"] <= opens["fails"]:
        raise NO_ROUTE
    return BResp(payload)


au._open = fake_open
work = tempfile.mkdtemp()
said = []
heals = {"n": 0}


def heal_once():
    heals["n"] += 1
    return heals["n"] == 1


opens.update(n=0, fails=au.DOWNLOAD_TRIES)
sleeps.clear()
dest = au.fetch_bundle("u", "t", os.path.join(work, "b1"), sha, len(payload),
                       lambda *a: said.append(a), heal=heal_once)
check("a heal buys one attempt past the budget: all tries failing + heal still lands",
      os.path.exists(dest) and opens["n"] == au.DOWNLOAD_TRIES + 1, opens)
check("...no backoff before the healed retry", len(sleeps) == au.DOWNLOAD_TRIES - 1, sleeps)
check("...and the status line says the route was moved",
      any("route" in str(a) for a in said), said)
opens.update(n=0, fails=99)
try:
    au.fetch_bundle("u", "t", os.path.join(work, "b2"), sha, len(payload),
                    lambda *a: None)
    msg = ""
except au.Fail as exc:
    msg = str(exc)
check("no heal: the same budget and the same final wording as before",
      opens["n"] == au.DOWNLOAD_TRIES and "failed after" in msg, (opens, msg))

# -- 7. heal_route itself -------------------------------------------------------------------------
spec2 = importlib.util.spec_from_file_location(
    "_heal_applier2", os.path.join(ROOT, "scripts", "apply_update.py"))
au2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(au2)


class Job2:
    healed = False
    link = tempfile.mkdtemp()          # a "release" with no routeheal.py
    config_dir = tempfile.mkdtemp()


j = Job2()
check("a live tree from before routeheal: nothing is run",
      au2.heal_route(j) is False and j.healed is True)
check("...and a second call in the same job does nothing either", au2.heal_route(j) is False)
src = open(os.path.join(ROOT, "scripts", "apply_update.py"), encoding="utf-8").read()
check("the applier runs routeheal as a subprocess, as the config's owner",
      '"core.services.routeheal"' in src and '"runuser", "-u", owner' in src)

# -- 8. the runuser prefix: only a root caller drops to the owner -----------------------------
# Mamazila's Docker rehearsal: the apply unit runs as User=<service user>, and
# runuser refuses non-root callers (rc 1), which heal_route swallowed as
# "nothing healed". pwd / geteuid do not exist on the Windows dev box, so
# both are faked; the live tree is a temp dir with the two files it probes.
import types                                                    # noqa: E402

tree = tempfile.mkdtemp()
os.makedirs(os.path.join(tree, "core", "services"))
open(os.path.join(tree, "core", "services", "routeheal.py"), "w").close()
os.makedirs(os.path.join(tree, ".venv", "bin"))
open(os.path.join(tree, ".venv", "bin", "python"), "w").close()
cfg8 = tempfile.mkdtemp()

fake_pwd = types.ModuleType("pwd")
fake_pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name="irivai")
real_pwd = sys.modules.get("pwd")
sys.modules["pwd"] = fake_pwd
ran = []


class Done:
    stdout = '{"changed": true, "detail": "moved"}\n'


def fake_run(argv, **kw):
    ran.append(list(argv))
    return Done()


au2.subprocess.run = fake_run
au2.log = lambda *a, **k: None
euid = {"v": 0}
real_geteuid = getattr(os, "geteuid", None)
au2.os.geteuid = lambda: euid["v"]          # the applier's os IS this os; restored below


def heal_as(uid):
    euid["v"] = uid
    ran.clear()
    j = Job2()
    j.healed = False
    j.link = tree
    j.config_dir = cfg8
    return au2.heal_route(j)


try:
    ok = heal_as(0)
    check("euid 0, config owned by irivai: runuser -u irivai -- <venv python> ...",
          ok is True and ran and ran[0][:4] == ["runuser", "-u", "irivai", "--"]
          and ran[0][4] == os.path.join(tree, ".venv", "bin", "python")
          and ran[0][-3:] == ["-m", "core.services.routeheal", cfg8], ran)
    ok = heal_as(1000)
    check("euid == the owner (the apply unit's User=): the venv python is run directly, no runuser",
          ok is True and ran and ran[0][0] == os.path.join(tree, ".venv", "bin", "python")
          and "runuser" not in ran[0], ran)
    fake_pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name="root")
    ok = heal_as(0)
    check("euid 0 and a root-owned config: nothing is run at all (L1)",
          ok is False and not ran, ran)

    def no_name(uid):
        raise KeyError(uid)
    fake_pwd.getpwuid = no_name
    ok = heal_as(0)
    check("euid 0 and an owner with no name: nothing is run at all (L1)",
          ok is False and not ran, ran)
    fake_pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name="irivai")
    ok = heal_as(0)
    check("...while euid 0 with a named non-root owner still heals through runuser",
          ok is True and ran and ran[0][:4] == ["runuser", "-u", "irivai", "--"], ran)
finally:
    if real_geteuid is None:
        del os.geteuid
    else:
        os.geteuid = real_geteuid
    if real_pwd is None:
        del sys.modules["pwd"]
    else:
        sys.modules["pwd"] = real_pwd

print("\n%d failed" % bad if bad else "\nall passed")
sys.exit(1 if bad else 0)
