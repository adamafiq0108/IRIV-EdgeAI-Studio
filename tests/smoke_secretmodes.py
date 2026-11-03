"""Secret-holding config files are owner-only (Tok Aba, 2026-10-14).

A system's config.local.toml holds its SMTP app password and Telegram bot
token, and on the box it was world-readable (0644). Now:

  1. every write of a *.local.toml through cfg_lock.write (detector_control
     edit_local and its setters, builtin_models, and through them provision
     and a template / system import) CREATES the file 0600, as
     config.save_local already did for config/*.local.toml
  2. hub and master start-up tighten existing files (config/*.local.toml and
     <systems dir>/*/config.local.toml) that are looser, never failing the
     start, logging a count only
  3. the detection app runs as the same account, so it still reads them

POSIX-only assertions are skipped on Windows (no such modes there); the
source pins run everywhere.

    py -3 tests/smoke_secretmodes.py
"""
import io
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                       # noqa: E402
from core.services import cfg_lock                            # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


POSIX = os.name == "posix"
TMP = tempfile.mkdtemp()
CFG = os.path.join(TMP, "config")
SYS = os.path.join(TMP, "systems")
os.makedirs(CFG)
os.makedirs(os.path.join(SYS, "line1"))

# -- 1. new writes -----------------------------------------------------------------
local = os.path.join(SYS, "line1", "config.local.toml")
cfg_lock.write(local, 'smtp_pass = "secret"\n')
plain = os.path.join(SYS, "line1", "notes.toml")
cfg_lock.write(plain, "a = 1\n")
check("cfg_lock.write writes the file", open(local, encoding="utf-8").read()
      == 'smtp_pass = "secret"\n')
src = read("core", "services", "cfg_lock.py").split("def write(")[1]
check("...creating a *.local.toml 0600 (O_EXCL, no follow), never chmod after",
      'mode = 0o600 if str(path).endswith(".local.toml") else 0o666' in src
      and "os.O_EXCL" in src and "O_NOFOLLOW" in src)
config.save_local("mail", {"a": 1}, CFG)
if POSIX:
    check("a system's config.local.toml is written 0600", mode(local) == 0o600, oct(mode(local)))
    check("...a config/*.local.toml too (save_local)",
          mode(os.path.join(CFG, "mail.local.toml")) == 0o600)
    check("...and a file that is not a secret keeps the umask's mode",
          mode(plain) != 0o600)
else:
    print("skip: no POSIX modes on this OS; the mode assertions run on the box")

# -- 2. start-up tightening -----------------------------------------------------------
old_sys = os.path.join(SYS, "line1", "config.local.toml")
old_cfg = os.path.join(CFG, "telegram.local.toml")
open(old_cfg, "w").write("token = 'x'\n")
if POSIX:
    os.chmod(old_sys, 0o644)
    os.chmod(old_cfg, 0o644)
n = config.tighten_secrets(CFG, (SYS,))
if POSIX:
    check("start-up tightens a 0644 system file and a 0644 config file",
          mode(old_sys) == 0o600 and mode(old_cfg) == 0o600 and n == 2, (n, oct(mode(old_sys))))
    check("...and a second start finds nothing to do", config.tighten_secrets(CFG, (SYS,)) == 0)
    os.chmod(plain, 0o644)
    config.tighten_secrets(CFG, (SYS,))
    check("...leaving other files alone", mode(plain) == 0o644)
    check("...and the owner (the app's account) still reads it",
          "smtp_pass" in open(old_sys, encoding="utf-8").read() or "token" in open(
              old_cfg, encoding="utf-8").read())
else:
    check("on Windows tightening is a no-op, never an error", n == 0)
check("a missing systems dir is not an error",
      config.tighten_secrets(CFG, (os.path.join(TMP, "nope"),)) >= 0)

for name in ("hub_server.py", "master_server.py"):
    text = read(name)
    block = text.split("config.tighten_secrets(")[1][:400] if "config.tighten_secrets(" in text else ""
    check(f"{name} tightens at start, logs a count only, never fails the start",
          block and "except Exception" in block and "%d secret file(s)" in block, block[:200])

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
