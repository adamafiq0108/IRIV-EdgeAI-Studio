# A hotspot key the 2026-10-05..07 generator made goes back to iriv1234, once.
#
# Tok Aba (2026-10-07): "not random, it should be iriv1234 for AP mode". The
# generator wrote no marker, so the rule is the key's exact shape (12 of
# abcdefghjkmnpqrstuvwxyz23456789) AND no [ap].user_set (a person's Save).
# Pinned here, with the radio and the Inbox faked:
#   1. the shape: what matches and what does not
#   2. a generated key, no flag -> iriv1234 through apconf.save_ap (the flag
#      stays absent, the saved name stays), [ap].migrated_default = 1
#   3. runs once: a second start changes nothing and posts nothing, and a
#      generated-looking key stored after the run is never judged
#   4. a key with user_set = true, any other shape, iriv1234, no key: kept,
#      the run still marked done, no notice
#   5. the hotspot restarts only when it is broadcasting (apconf.apply_ap,
#      the path a page Save uses); one Inbox notice to the admins, no old key
#   6. the hub calls it before storing/raising the key and re-keys after it
#
#     python tests/smoke_apdefault.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                       # noqa: E402
from core.services import apconf, apdefault, hostinfo, inbox, netconf  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


hostinfo.ap_name = lambda: "iriv-0123abcd"
GEN = "k7mpq2wxhz9a"

# The radio and the Inbox, faked.
active = {"on": False}
netconf.ap_active = lambda: active["on"]
restarts = []
notices = []
inbox.notify_admins = lambda kind, title, body="", config_dir=None, **kw: \
    notices.append((kind, title, body, config_dir, kw))


def fake_restart(cfg):
    restarts.append(cfg)


def ap(cfg):
    return config.load_local("network", cfg).get("ap") or {}


def box(block):
    cfg = tempfile.mkdtemp()
    config.save_local("network", {"ap": dict(block)}, cfg)
    return cfg


def run(cfg):
    restarts.clear()
    notices.clear()
    return apdefault.migrate(cfg, restart=fake_restart)


# -- 1. the shape ------------------------------------------------------------
for key in (GEN, "abcdefghjkmn", "222222222222", "zyxwvutsrqpn"):
    check(f"{key!r} has the generator's shape", apdefault.looks_generated(key))
for key, why in (("k7mpq2wxhz9", "11 characters"), ("k7mpq2wxhz9ab", "13 characters"),
                 ("k7mpq2wxhz9o", "an o"), ("k7mpq2wxhz9l", "an l"),
                 ("k7mpq2wxhz9i", "an i"), ("k7mpq2wxhz90", "a 0"),
                 ("k7mpq2wxhz91", "a 1"), ("K7MPQ2WXHZ9A", "upper case"),
                 ("k7mpq2wx z9a", "a space"), ("iriv1234", "the default"),
                 ("", "empty"), (GEN + "\n", "a trailing newline")):
    check(f"...not: {why}", not apdefault.looks_generated(key))

# -- 2. a generated key goes back -------------------------------------------------
cfg = box({"mode": "ap", "password": GEN, "ssid": "Line 3 AP"})
check("a generated key with no flag is changed", run(cfg) is True)
a = ap(cfg)
check("...to iriv1234, with the saved name kept",
      a.get("password") == "iriv1234" and apconf.ap_ssid(cfg) == "Line 3 AP"
      and a.get("mode") == "ap", repr(a))
check("...user_set stays absent and the run is marked done",
      "user_set" not in a and a.get("migrated_default") == 1, repr(a))
check("...one Inbox notice to the admins, pointing at Settings -> Network",
      len(notices) == 1 and notices[0][0] == apdefault.NOTICE_KIND
      and notices[0][1] == "The access point password is back to iriv1234"
      and notices[0][3] == cfg
      and notices[0][4].get("page") == "settings" and notices[0][4].get("tab") == "network",
      repr(notices))
check("...whose text never carries the old key",
      all(GEN not in str(n) for n in notices))
check("...and the hotspot, not broadcasting, is not restarted", restarts == [])

# -- 3. once -----------------------------------------------------------------
check("a second start changes nothing", run(cfg) is False)
check("...and posts nothing, restarts nothing", notices == [] and restarts == [])
apconf.save_ap("Line 3 AP", "abcdefghjkmn", cfg)
check("a generated-looking key a person saves AFTER the run is kept",
      run(cfg) is False and netconf.ap_password(cfg) == "abcdefghjkmn" and notices == [])
marked = box({"password": GEN, "migrated_default": 1})
check("a box already marked keeps even a generated-looking key",
      run(marked) is False and ap(marked).get("password") == GEN)

# -- 4. what is never touched -----------------------------------------------------
for label, block in (("user_set = true", {"password": GEN, "user_set": True}),
                     ("another shape", {"password": "Site-AP-2026!"}),
                     ("a lowercase 11-char key", {"password": "k7mpq2wxhz9"}),
                     ("the default", {"password": "iriv1234"}),
                     ("no key at all", {"mode": "ap"})):
    c = box(block)
    changed = run(c)
    a = ap(c)
    check(f"{label}: kept, marked done, no notice, no restart",
          changed is False and a.get("password") == block.get("password")
          and a.get("user_set") == block.get("user_set")
          and a.get("migrated_default") == 1 and notices == [] and restarts == [], repr(a))
empty = tempfile.mkdtemp()
check("a box with no network.local.toml at all is only marked",
      run(empty) is False and ap(empty) == {"migrated_default": 1}, repr(ap(empty)))

# -- 5. a broadcasting hotspot is restarted -----------------------------------------
active["on"] = True
c = box({"mode": "ap", "password": GEN})
check("a broadcasting hotspot is restarted on the new key",
      run(c) is True and restarts == [c])
check("...after the key was stored", netconf.ap_password(c) == "iriv1234")
calls = []
apconf.apply_ap = lambda cfg=None: calls.append((apconf.ap_ssid(cfg), netconf.ap_password(cfg)))
t = apdefault._restart_ap(c)
t.join(10)
check("the default restart is apconf.apply_ap, the path a page Save uses",
      calls == [("iriv-0123abcd", "iriv1234")], repr(calls))
c2 = box({"mode": "wifi", "password": "k7mpq2wxhz9a", "user_set": True})
check("a user_set key is never restarted either", run(c2) is False and restarts == [])
active["on"] = False

# -- 6. the hub --------------------------------------------------------------------
with open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8") as fh:
    hub = fh.read()
i_mig = hub.find("_apdefault.migrate(config_dir)")
check("the hub runs the migration at start", i_mig > 0)
check("...before it stores a missing key and raises the AP",
      0 < i_mig < hub.find("ensure_ap_password(config_dir)") < hub.find("apply_wifi_mode("))
check("...and re-keys a hotspot already up when it changed the key",
      "rekey=not _had_key or _migrated" in hub)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
