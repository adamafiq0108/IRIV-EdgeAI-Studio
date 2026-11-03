# One mtime-keyed TOML cache (perf audit item L3, 2026-10-02).
#
# detector_control re-parsed a system's config on every read (eight times per
# dashboard poll), and config.load was an lru_cache with no change check at
# all -- a master kept the systems / network / storage config it read at
# start while the hub rewrote it (the stale-config BUG pinned below). Now
# core/tomlcache.py parses a file again only when its (mtime, size) moves,
# and this process's own writers drop it as they write.
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


from core import config, tomlcache                                  # noqa: E402
from core.services import cfg_lock                                   # noqa: E402
from core.services import detector_control as dc                     # noqa: E402

d = tempfile.mkdtemp()
p = os.path.join(d, "a.toml")
with open(p, "w") as f:
    f.write("[app]\ntitle = 'one'\n")

# -- the cache itself ---------------------------------------------------------
tomlcache.stats.update(parses=0, hits=0)
for _ in range(50):
    tomlcache.read(p)
check("50 reads of an unchanged file parse it once", tomlcache.stats["parses"] == 1,
      tomlcache.stats)
doc = tomlcache.read(p)
doc["app"]["title"] = "changed in memory"
check("a caller changing what it got does not change the next read",
      tomlcache.read(p)["app"]["title"] == "one")
st = os.stat(p)
with open(p, "w") as f:
    f.write("[app]\ntitle = 'two, longer'\n")
os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))      # same clock tick
check("a write in the same clock tick is still seen (the size moved)",
      tomlcache.read(p)["app"]["title"] == "two, longer")
with open(p, "w") as f:
    f.write("[app\nbroken")
check("a broken file: the last good copy, with fallback",
      tomlcache.read(p, fallback=True)["app"]["title"] == "two, longer")
try:
    tomlcache.read(p)
    raised = False
except Exception:                                                    # noqa: BLE001
    raised = True
check("...and an error without it", raised)
os.remove(p)
check("a vanished file reads as its last good copy, with fallback (it does "
      "not blink out of existence)",
      tomlcache.read(p, fallback=True)["app"]["title"] == "two, longer")
tomlcache.invalidate(p)
check("...until invalidated", tomlcache.read(p, fallback=True) == {})

# -- detector_control ----------------------------------------------------------------
systems = tempfile.mkdtemp()
pkg = os.path.join(systems, "s1")
os.makedirs(pkg)
with open(os.path.join(pkg, "config.toml"), "w") as f:
    f.write("[app]\ntitle = 'base'\n")
dc._write_local(pkg, {"detector": {"kind": "none"}})
tomlcache.stats.update(parses=0, hits=0)
for _ in range(40):
    dc._merged(pkg)
check("40 config reads of a system parse its two files once each",
      tomlcache.stats["parses"] <= 2, tomlcache.stats)
dc._write_local(pkg, {"detector": {"kind": "hailo"}})
check("this process's own write is read back at once",
      dc._merged(pkg)["detector"]["kind"] == "hailo")
cfg_lock.write(dc._local_path(pkg), "[detector]\nkind = 'cpu'\n")
check("a cfg_lock write is too", dc._merged(pkg)["detector"]["kind"] == "cpu")
with open(dc._local_path(pkg), "w") as f:              # "another process"
    f.write("[detector]\nkind = 'other-process'\n")
check("another process's write is seen by its stamp",
      dc._merged(pkg)["detector"]["kind"] == "other-process")
dc._toml_cache.pop(dc._local_path(pkg), None)
check("code that pops the old _toml_cache still works (builtin_models)",
      dc._merged(pkg)["detector"]["kind"] == "other-process"
      and "dc._toml_cache.pop(dc._local_path(pkg_dir), None)"
      in read("core", "services", "builtin_models.py"))

# -- config.load: the stale-config bug ------------------------------------------------
cdir = tempfile.mkdtemp()
with open(os.path.join(cdir, "systems.toml"), "w") as f:
    f.write("[masters.a]\nrole = 'primary'\n")
check("config.load reads", config.load("systems", cdir)["masters"]["a"]["role"] == "primary")
check("...and hands every caller the same document, as the lru_cache did",
      config.load("systems", cdir) is config.load("systems", cdir))
time.sleep(0.01)
with open(os.path.join(cdir, "systems.local.toml"), "w") as f:     # the hub writes
    f.write("[masters.a]\nrole = 'standby'\n")
check("BUG FIXED: a file another process writes is seen by a running master",
      config.load("systems", cdir)["masters"]["a"]["role"] == "standby")
config.save_local("systems", {"masters": {"a": {"role": "primary"}}}, cdir)
check("save_local's own write is seen at once",
      config.load("systems", cdir)["masters"]["a"]["role"] == "primary")
config.load.cache_clear()
check("load.cache_clear() still exists for the code and tests that call it",
      config.load("systems", cdir)["masters"]["a"]["role"] == "primary")

# -- what it saves -------------------------------------------------------------------
big = {"functions": [{"id": f"f{i}", "type": "expr", "params": {"expr": "a+b" * 5, "k": i}}
                     for i in range(80)],
       "zones": [{"id": f"z{i}", "points": [[0.1 * j, 0.2 * j] for j in range(12)]}
                 for i in range(20)]}
dc._write_local(pkg, big)
import tomllib                                                       # noqa: E402
t = time.perf_counter()
for _ in range(50):
    with open(dc._local_path(pkg), "rb") as f:
        tomllib.load(f)
before = (time.perf_counter() - t) / 50 * 1000
dc._merged(pkg)
t = time.perf_counter()
for _ in range(50):
    dc._read_toml(dc._local_path(pkg))
after = (time.perf_counter() - t) / 50 * 1000
print(f"     (a {os.path.getsize(dc._local_path(pkg)) // 1024} KB system config: "
      f"{before:.2f} ms per read parsed every time, {after:.2f} ms cached)")
check("a cached read is cheaper than a parse", after < before)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
