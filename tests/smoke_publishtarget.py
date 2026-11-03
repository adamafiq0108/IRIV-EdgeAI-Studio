# A release goes to the RELEASE repo, and to no other.
#
# WHAT WENT WRONG. publish_release._repo() read config/update.toml by scanning
# every line for `owner` and `repo` and keeping the last pair it saw. On
# 2026-09-11 a [source] section arrived, naming the repo that built-in
# templates are published FROM -- and being last in the file, it won. Every
# release from that day aimed at irivai-systor rather than irivai-release.
#
# It hid well. GitHub answers 404, not 403, for a repository a token cannot
# write to, so the failure read as "your token lacks access" and was hunted as
# a permissions problem across several attempts. The destination was wrong the
# whole time.
#
# WHAT IS PINNED. That the destination comes from [release] specifically, that
# a second section carrying the same key names cannot displace it, and that
# the file this depends on still HAS both sections -- a test that passes
# because the trap was quietly removed is a test that stops guarding anything.
import io
import os
import sys
import tempfile
import tomllib
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "dev"))

import publish_release as pr                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tomllib.loads(io.open(os.path.join(ROOT, "config", "update.toml"),
                            encoding="utf-8").read())

# -- the trap is still in the file --------------------------------------------------
check("update.toml still has BOTH a [release] and a [source] section",
      isinstance(CFG.get("release"), dict) and isinstance(CFG.get("source"), dict),
      "if [source] ever goes away, this test must be reconsidered rather than "
      "left passing for the wrong reason")
check("...and they name DIFFERENT repos, which is what made this possible",
      CFG["release"].get("repo") != CFG["source"].get("repo"),
      (CFG["release"].get("repo"), CFG["source"].get("repo")))
check("...with [source] written after [release], so a last-wins scan loses",
      io.open(os.path.join(ROOT, "config", "update.toml"),
              encoding="utf-8").read().index("[source]")
      > io.open(os.path.join(ROOT, "config", "update.toml"),
                encoding="utf-8").read().index("[release]"))

# -- and the publisher reads the right one ------------------------------------------
owner, repo = pr._repo(types.SimpleNamespace(repo=""))
check("a publish goes to the [release] repo",
      (owner, repo) == (CFG["release"]["owner"], CFG["release"]["repo"]),
      f"{owner}/{repo}")
check("...and NOT to the source repo, which is where it was going",
      repo != CFG["source"]["repo"], repo)

# -- an explicit override still wins -------------------------------------------------
check("--repo overrides the config",
      pr._repo(types.SimpleNamespace(repo="someone/elsewhere"))
      == ("someone", "elsewhere"))

# -- and a config that cannot answer says so, rather than guessing --------------------
real_root = pr.ROOT
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "config"))


def with_config(text):
    io.open(os.path.join(tmp, "config", "update.toml"), "w",
            encoding="utf-8").write(text)
    import pathlib
    pr.ROOT = pathlib.Path(tmp)
    try:
        return pr._repo(types.SimpleNamespace(repo=""))
    finally:
        pr.ROOT = real_root


for label, text in (
        ("a config with no [release] section is refused",
         '[source]\nowner = "a"\nrepo = "b"\n'),
        ("...as is one whose [release] names no repo",
         '[release]\nowner = "a"\n'),
        ("...and one that is not valid TOML at all",
         '[release\nowner = ')):
    try:
        got = with_config(text)
        check(label, False, f"returned {got} instead of refusing")
    except SystemExit as exc:
        check(label, True)
        if "no release repo" not in str(exc) and "does not parse" not in str(exc):
            check(f"...and {label} explains itself", False, str(exc))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
