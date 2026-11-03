# The developer channel reads its releases from a repo of its OWN, with a
# token of its own (Pipi gate 1.0.15-dev, M1; Tok Aba's decision 2026-10-05).
#
# WHY. A fine-grained token scopes to a repository, never to a tag or an
# asset, and every customer box holds the read token for irivai-release
# (config/update.local.toml, 0600, on a disk the customer owns). A dev bundle
# published there -- core/dev and scripts/dev as plain .py -- was downloadable
# with that token, which defeats "a customer can never have a developer
# account". The dev channel now lives in [release] dev_repo and is read with
# `dev_token`, which only a dev box ever holds.
#
# WHAT IS PINNED.
#   1. each channel's repo: stable -> repo, dev -> dev_repo, never the same
#   2. token isolation both ways: per-channel keys, the customer token never
#      sent to the dev repo, the dev token never to the customer repo -- by
#      check(), by the job file, and by the applier's asset-URL guard
#   3. no dev token is said in plain words, not as an HTTP code
#   4. a cache from the other repo cannot be applied (and is dropped)
#   5. publish_release.py: repo per channel, and --repo naming the other
#      channel's repo is refused in both directions
#   6. release.yml: dev tag -> dev repo with RELEASE_DEV_REPO_TOKEN only,
#      stable tag -> irivai-release with RELEASE_REPO_TOKEN only
#   7. the Customer build tab on a dev box confirms before installing over
#      the developer build (Pipi L4)
#   8. the false "ships compiled" comment is gone; the signing key and the
#      manifest app check are unchanged
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "dev"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import ghnet                                # noqa: E402
from core.services import update as upd                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def _read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _box(dev, local=""):
    """A config root with the REAL committed update.toml, a deploy.toml saying
    what kind of box it is, and an update.local.toml holding `local`."""
    d = tempfile.mkdtemp(prefix="smoke_otadevrepo_")
    shutil.copy(os.path.join(ROOT, "config", "update.toml"), d)
    if dev:
        io.open(os.path.join(d, "deploy.toml"), "w", encoding="utf-8").write('mode = "dev"\n')
    if local:
        io.open(os.path.join(d, "update.local.toml"), "w", encoding="utf-8").write(local)
    return d


OWNER = "adamafiq0108"
STABLE_REPO, DEV_REPO = "irivai-release", "irivai-release-dev"
CUST = _box(False, 'token = "ghp_customer"\n')
DEVBOX = _box(True, 'token = "ghp_customer"\ndev_token = "ghp_devonly"\n')
DEVNOTOK = _box(True, 'token = "ghp_customer"\n')

# -- 1. one repo per channel ---------------------------------------------------------
cfg = __import__("tomllib").loads(_read("config", "update.toml"))
rel = cfg.get("release") or {}
check("config/update.toml [release] names dev_repo beside repo",
      rel.get("repo") == STABLE_REPO and rel.get("dev_repo") == DEV_REPO, rel)
s_st, s_dev = upd.settings(DEVBOX, "stable"), upd.settings(DEVBOX, "dev")
check("stable reads irivai-release", (s_st["owner"], s_st["repo"]) == (OWNER, STABLE_REPO), s_st)
check("dev reads irivai-release-dev", (s_dev["owner"], s_dev["repo"]) == (OWNER, DEV_REPO), s_dev)
check("the defaults agree with the committed file",
      upd.DEFAULT_RELEASE["repo"] == STABLE_REPO and upd.DEFAULT_RELEASE["dev_repo"] == DEV_REPO)
NOREPO = _box(True)
io.open(os.path.join(NOREPO, "update.toml"), "w", encoding="utf-8").write(
    '[release]\nowner = "o"\nrepo = "only-stable"\n')
check("a config missing dev_repo does NOT fall back to the customer repo",
      upd.settings(NOREPO, "dev")["repo"] == DEV_REPO, upd.settings(NOREPO, "dev"))

# -- 2. token isolation -----------------------------------------------------------------
check("token keys differ per channel", upd.TOKEN_KEY == {"stable": "token", "dev": "dev_token"})
check("stable token on the dev box is the customer one", upd.token(DEVBOX, "stable") == "ghp_customer")
check("dev token on the dev box is the dev one", upd.token(DEVBOX, "dev") == "ghp_devonly")
check("a customer box answers no dev token", upd.token(CUST, "dev") == "")
T = _box(True)
upd.set_token("ghp_a", T, "stable")
upd.set_token("ghp_b", T, "dev")
check("set_token writes each channel's own key",
      upd.token(T, "stable") == "ghp_a" and upd.token(T, "dev") == "ghp_b")
upd.set_token("", T, "dev")
check("clearing the dev token leaves the customer token alone",
      upd.token(T, "stable") == "ghp_a" and upd.token(T, "dev") == "")
upd.set_token("ghp_b2", T, "dev")
upd.set_token("", T, "stable")
check("...and the reverse", upd.token(T, "stable") == "" and upd.token(T, "dev") == "ghp_b2")

# check(): what goes on the wire, per channel. ghnet.open_url is the one door.
sent, heads = [], []


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()
        self.headers = {"ETag": 'W/"x"'}

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _asset(repo, n):
    return {"name": n, "url": f"https://api.github.com/repos/{OWNER}/{repo}/releases/assets/{len(n)}",
            "size": 1}


def _rel(tag, repo):
    return {"tag_name": tag, "body": "", "assets": [
        _asset(repo, upd.BUNDLE_ASSET), _asset(repo, upd.MANIFEST_ASSET), _asset(repo, upd.SIG_ASSET)]}


def _fake_open(url, token=None, accept=None, agent="", timeout=0, headers=None, method=None):
    sent.append((url, token))
    heads.append(dict(headers or {}))
    repo = url.split("/repos/", 1)[1].split("/")[1]
    return _Resp([_rel("v99.0.0-dev", repo)] if repo == DEV_REPO else [_rel("v99.0.0", repo)])


_real_open = ghnet.open_url
ghnet.open_url = _fake_open
try:
    for ch in upd.CHANNELS:
        upd._write_json(upd._p(upd.CHECK_FILE[ch]), {})
    st = upd.check(force=True, config_dir=DEVBOX, channel="stable")
    dv = upd.check(force=True, config_dir=DEVBOX, channel="dev")
    check("both checks went out, one each", len(sent) == 2, sent)
    (u1, t1), (u2, t2) = sent[0], sent[1]
    check("stable check: customer repo with the customer token",
          f"/repos/{OWNER}/{STABLE_REPO}/" in u1 and t1 == "ghp_customer", sent[0])
    check("dev check: dev repo with the dev token",
          f"/repos/{OWNER}/{DEV_REPO}/" in u2 and t2 == "ghp_devonly", sent[1])
    check("the customer token never reached the dev repo",
          not any(DEV_REPO in u and t == "ghp_customer" for u, t in sent))
    check("the dev token never reached the customer repo",
          not any(f"/{STABLE_REPO}/" in u and t == "ghp_devonly" for u, t in sent))
    check("each channel got its own release", st["latest"] == "v99.0.0" and dv["latest"] == "v99.0.0-dev",
          (st["latest"], dv["latest"]))

    # -- 3. no dev token, said plainly -----------------------------------------------
    sent.clear()
    nt = upd.check(force=True, config_dir=DEVNOTOK, channel="dev")
    check("no dev token: nothing is sent at all (not even the customer token)", sent == [], sent)
    check("no dev token: token_set is False", nt["token_set"] is False)
    check("no dev token: the sentence is the plain one", nt["error"] == upd.NO_TOKEN["dev"], nt["error"])
    check("...names developer builds and says what to do",
          "developer" in nt["error"] and "paste" in nt["error"], nt["error"])
    check("...with no HTTP code or jargon in it",
          not re.search(r"\d{3}|HTTP|40\d|GitHub answered", nt["error"]), nt["error"])
    check("a missing token is not counted as a failed check",
          not upd._check_failed(upd.NO_TOKEN["dev"]) and not upd._check_failed(upd.NO_TOKEN["stable"]))
finally:
    ghnet.open_url = _real_open

# The job file: per-channel token key and repo.
upd.ota_ready = lambda: True
upd.job_running = lambda: False
job_dev = upd._write_job("apply", "99.0.0-dev", DEVBOX, "dev")
job_st = upd._write_job("apply", "99.0.0", DEVBOX, "stable")
check("dev job: dev_token + dev repo", (job_dev["token_key"], job_dev["repo"]) == ("dev_token", DEV_REPO), job_dev)
check("stable job: token + customer repo", (job_st["token_key"], job_st["repo"]) == ("token", STABLE_REPO), job_st)
check("both jobs keep their own manifest app",
      job_dev["manifest_app"] == upd.MANIFEST_APP_DEV and job_st["manifest_app"] == upd.MANIFEST_APP)

# -- 4. a cache from the other repo --------------------------------------------------
check("assets_in_repo: own repo passes", upd.assets_in_repo(
    {a["name"]: a for a in _rel("v1", DEV_REPO)["assets"]}, OWNER, DEV_REPO))
check("assets_in_repo: other repo fails", not upd.assets_in_repo(
    {a["name"]: a for a in _rel("v1", STABLE_REPO)["assets"]}, OWNER, DEV_REPO))
check("assets_in_repo: a prefix-sharing repo name fails", not upd.assets_in_repo(
    {"x": {"url": f"https://api.github.com/repos/{OWNER}/{STABLE_REPO}-dev-evil/releases/assets/1"}},
    OWNER, DEV_REPO) and not upd.assets_in_repo(
    {"x": {"url": f"https://api.github.com/repos/{OWNER}/{DEV_REPO}/releases/assets/1"}},
    OWNER, STABLE_REPO))
old = {"latest": "v99.0.0-dev", "etag": 'W/"old"',
       "assets": {a["name"]: a for a in _rel("v99.0.0-dev", STABLE_REPO)["assets"]}}
upd._write_json(upd._p(upd.CHECK_FILE["dev"]), old)
try:
    upd.apply("99.0.0-dev", DEVBOX, "dev")
    check("apply refuses a dev release listed from the customer repo", False, "it applied")
except upd.UpdateError as e:
    check("apply refuses a dev release listed from the customer repo",
          "different release repo" in str(e), str(e))
sent.clear()
ghnet.open_url = _fake_open
try:
    upd.check(config_dir=DEVBOX, channel="dev")
finally:
    ghnet.open_url = _real_open
check("a check drops the other repo's ETag instead of sending it",
      len(heads) and "If-None-Match" not in heads[-1], heads)
upd._write_json(upd._p(upd.CHECK_FILE["dev"]), old)
try:
    upd.apply("99.0.0-dev", DEVNOTOK, "dev")
    check("apply with no dev token refuses in plain words", False, "it applied")
except upd.UpdateError as e:
    check("apply with no dev token refuses in plain words", str(e) == upd.NO_TOKEN["dev"], str(e))

# The applier enforces the same rule before a single request.
spec = importlib.util.spec_from_file_location("_smoke_applier_devrepo",
                                              os.path.join(ROOT, "scripts", "apply_update.py"))
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)
fetched = []
au.fetch_healing = lambda job, fn, *a: fetched.append(a) or b""
for chan, key, repo, wrong in (("dev", "dev_token", DEV_REPO, STABLE_REPO),
                               ("stable", "token", STABLE_REPO, DEV_REPO)):
    work = tempfile.mkdtemp(prefix="smoke_otadevrepo_job_")
    io.open(os.path.join(work, "job.json"), "w", encoding="utf-8").write(json.dumps({
        "mode": "apply", "version": "99.0.0", "current": "1.0.0", "channel": chan,
        "token_key": key, "owner": OWNER, "repo": repo, "config_dir": DEVBOX,
        "store_dir": tempfile.mkdtemp(), "releases_dir": tempfile.mkdtemp(),
        "manifest_app": "x", "bundle_asset": upd.BUNDLE_ASSET,
        "manifest_asset": upd.MANIFEST_ASSET, "sig_asset": upd.SIG_ASSET,
        "assets": {a["name"]: a for a in _rel("v99.0.0", wrong)["assets"]}}))
    try:
        au.do_apply(au.Job(work))
        check(f"applier ({chan}) refuses assets from {wrong}", False, "no refusal")
    except au.Fail as e:
        check(f"applier ({chan}) refuses assets from {wrong}", "different release repo" in str(e), str(e))
check("...before any request carried the token", fetched == [], fetched)
ausrc = _read("scripts", "apply_update.py")
check("the applier still checks the manifest app name",
      'if m.get("app") != expect_app:' in ausrc and 'job.d.get("manifest_app")' in ausrc)

# The redirect rule still strips our header off-GitHub, for an asset in either repo.
for repo in (STABLE_REPO, DEV_REPO):
    req = urllib.request.Request(f"https://api.github.com/repos/{OWNER}/{repo}/releases/assets/1",
                                 headers={"Authorization": "Bearer ghp_x"})
    new = ghnet.NoAuthAcrossHosts().redirect_request(
        req, None, 302, "Found", {}, "https://release-assets.s3.amazonaws.com/x?sig=1")
    check(f"redirect off GitHub drops Authorization ({repo})",
          new is not None and not new.has_header("Authorization"))

# -- 5. publish_release.py -----------------------------------------------------------
import publish_release as pr                                   # noqa: E402
check("publish stable -> irivai-release",
      pr._repo(types.SimpleNamespace(repo=""), "stable") == (OWNER, STABLE_REPO))
check("publish dev -> irivai-release-dev",
      pr._repo(types.SimpleNamespace(repo=""), "dev") == (OWNER, DEV_REPO))
for chan, other in (("dev", STABLE_REPO), ("stable", DEV_REPO)):
    try:
        pr._repo(types.SimpleNamespace(repo=f"{OWNER}/{other}"), chan)
        check(f"--channel {chan} --repo {other} is refused", False, "accepted")
    except SystemExit as e:
        check(f"--channel {chan} --repo {other} is refused", "other channel" in str(e), str(e))
real_root = pr.ROOT
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "config"))
for label, text in (("one repo for both channels is refused",
                     '[release]\nowner = "o"\nrepo = "r"\ndev_repo = "R"\n'),
                    ("a [release] without dev_repo is refused for a dev publish",
                     '[release]\nowner = "o"\nrepo = "r"\n')):
    io.open(os.path.join(tmp, "config", "update.toml"), "w", encoding="utf-8").write(text)
    import pathlib
    pr.ROOT = pathlib.Path(tmp)
    try:
        got = pr._repo(types.SimpleNamespace(repo=""), "dev")
        check(label, False, f"returned {got}")
    except SystemExit:
        check(label, True)
    finally:
        pr.ROOT = real_root
pubsrc = _read("scripts", "dev", "publish_release.py")
check("cmd_publish passes the channel to _repo", "_repo(args, channel)" in pubsrc)

# -- 6. release.yml: channel -> repo and secret ---------------------------------------
yml = _read(".github", "workflows", "release.yml")
steps = re.split(r"\n      - ", yml)
pubs = [s for s in steps if "publish_release.py publish" in s]
check("release.yml has exactly two publish steps", len(pubs) == 2, len(pubs))
want = {"stable": "RELEASE_REPO_TOKEN", "dev": "RELEASE_DEV_REPO_TOKEN"}
for chan, secret in want.items():
    mine = [s for s in pubs if f"steps.channel.outputs.channel == '{chan}'" in s]
    check(f"one publish step runs only for channel {chan}", len(mine) == 1, len(mine))
    if len(mine) != 1:
        continue
    s = mine[0]
    other = want["dev" if chan == "stable" else "stable"]
    check(f"...it holds secrets.{secret} and no other release secret",
          f"secrets.{secret} }}" in s and f"secrets.{other} " not in s
          and f"secrets.{other}}}" not in s and s.count("secrets.") == 1, s)
    check(f"...and publishes --channel {chan} with --token-env {secret}",
          f"--token-env {secret} " in s and f"--channel {chan}" in s, s)
    check("...with no --repo override (the repo follows the channel)", "--repo" not in s)
check("no other step sees a release-repo write secret",
      sum(s.count("RELEASE_REPO_TOKEN }}") + s.count("RELEASE_DEV_REPO_TOKEN }}")
          for s in steps if s not in pubs) == 0)
check("the channel is still derived from the tag (-dev -> dev)",
      "*-dev)         channel=dev ;;" in yml and "*)             channel=stable ;;" in yml)

# -- 7. the Customer build tab on a dev box confirms ----------------------------------
rsrc = _read("core", "routes", "update.py")
check("GET /api/hub/update says whether this is a dev box",
      '"dev_box": update_service.dev_available(' in rsrc)
psrc = _read("frontend", "src", "components", "UpdatePanel.jsx")
check("UpdatePanel reads dev_box", "setDevBox(!!d.dev_box)" in psrc)
check("...and only the CUSTOMER tab on a dev box warns",
      "const overDev = !isDev && devBox;" in psrc)
check("...in plain words: it removes the developer tools, Roll back restores",
      "removes them from this box" in psrc and "Roll back puts the" in psrc
      and 'confirmLabel={overDev ? "Install customer build" : "Update now"}' in psrc)
check("no-token on the dev tab is explained, not flagged as an error",
      "!expired && !noToken" in psrc and "Developer update token" in psrc)

# -- 8. comments and key ----------------------------------------------------------------
for path in (("config", "update.toml"), ("core", "services", "update.py"),
             ("core", "routes", "update.py"), (".github", "workflows", "release.yml"),
             ("scripts", "dev", "publish_release.py")):
    txt = _read(*path)
    check(f"{'/'.join(path)}: the false 'ships compiled' claim is gone",
          "ships compiled" not in txt and "one repo under one token" not in txt)
check("update.toml says dev bundles live in a repo only dev boxes hold a token for",
      "which only dev boxes\n# hold a token for" in _read("config", "update.toml"))
KEY = "TjgmysV+4XU8VRGl7UqzQNaE7azT6dvuiqF7MLIUcLE="
check("the OTA pubkey is unchanged", upd.PUBKEY_B64 == KEY and cfg["signing"]["pubkey"] == KEY)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
