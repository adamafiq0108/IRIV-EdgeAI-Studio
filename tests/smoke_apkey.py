# The access point password defaults to "iriv1234", and a stored key is kept.
#
# HISTORY. Until 2026-10-05 config/network.toml shipped [ap].password =
# "iriv1234" and three readers fell back to the literal. 10-05..10-07 every
# box generated its own random key, the shipped one was refused as a new key,
# upgrade_steps.sh replaced a stored one and the hub posted an Inbox nag.
# Tok Aba's decision (2026-10-07), made knowing the key is in the manual:
# iriv1234 is THE default again -- the dashboard password still guards the
# box and the AP is isolated from the wired LAN.
#
# Pinned: the one reader with no fallback of its own, ensure_ap_password()
# storing the default once and keeping ANY stored key (a random per-box one
# from 10-05..07 included), the default accepted as a typed key, no nag left
# in the hub, the committed config shipping no key, and the installer's own
# snippet -- run here against a temp config -- writing iriv1234 on a new box
# and keeping the key of an existing one. [ap].user_set (a person chose the
# key) is written by apconf.save_ap ONLY; apdefault.migrate is the one place
# that acts on its absence (smoke_apdefault), install and updates never do.
#
#     python tests/smoke_apkey.py
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                       # noqa: E402
from core.services import netconf                             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def stored(cfg):
    return (config.load_local("network", cfg).get("ap") or {}).get("password")


check("the default access point password is iriv1234",
      netconf.DEFAULT_AP_PASSWORD == "iriv1234")
check("the random per-box key generator is gone",
      not hasattr(netconf, "new_ap_password") and not hasattr(netconf, "AP_KEY_ALPHABET"))
check("...and so is the shipped-key flag",
      not hasattr(netconf, "ap_password_is_shipped") and not hasattr(netconf, "SHIPPED_AP_PASSWORD"))

# -- the one reader, and filling a missing key --------------------------------
cfg = tempfile.mkdtemp()
check("no stored key reads as empty (the reader has no literal of its own)",
      netconf.ap_password(cfg) == "")
first = netconf.ensure_ap_password(cfg)
check("ensure_ap_password() stores iriv1234 in network.local.toml",
      first == "iriv1234" and stored(cfg) == "iriv1234", repr((first, stored(cfg))))
check("...and a second call keeps it", netconf.ensure_ap_password(cfg) == "iriv1234")
if os.name == "posix":
    mode = os.stat(os.path.join(cfg, "network.local.toml")).st_mode & 0o777
    check("...in a file only the service account can read (0600)", mode == 0o600, oct(mode))

old = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "ap", "password": "k7mpq2wxhz9a"}}, old)
check("ensure_ap_password keeps any stored key, a 10-05..07 one too (apdefault returns that once)",
      netconf.ensure_ap_password(old) == "k7mpq2wxhz9a" and stored(old) == "k7mpq2wxhz9a")
check("...and neither key is marked user_set by the boot path",
      "user_set" not in (config.load_local("network", old).get("ap") or {})
      and "user_set" not in (config.load_local("network", cfg).get("ap") or {}))

typed = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "wifi", "password": "somethingelse"}}, typed)
try:
    netconf.set_wifi_mode("ap", "iriv1234", config_dir=typed)
    check("iriv1234 is accepted as a typed AP password (Enable AP mode)",
          stored(typed) == "iriv1234", repr(stored(typed)))
except netconf.NetError as exc:
    check("iriv1234 is accepted as a typed AP password (Enable AP mode)", False, str(exc))
for short in ("iriv123", "x" * 64):
    try:
        netconf.set_wifi_mode("ap", short, config_dir=typed)
        check(f"...while {len(short)} characters is still refused", False)
    except netconf.NetError:
        check(f"...while {len(short)} characters is still refused", True)

# -- ap_enable takes the default --------------------------------------------------
sent = []
saved = (netconf._wlan_connection, netconf.wifi_helper_installed, netconf._wifi_profile,
         netconf.status)
netconf._wlan_connection = lambda: ""
netconf.wifi_helper_installed = lambda: True
netconf._wifi_profile = lambda note: sent.append(note)
netconf.status = lambda: {}
try:
    netconf.ap_enable("iriv-abc", "iriv1234")
    check("ap_enable raises the AP on iriv1234",
          sent and sent[-1].get("psk") == "iriv1234", repr(sent))
except netconf.NetError as exc:
    check("ap_enable raises the AP on iriv1234", False, str(exc))
finally:
    (netconf._wlan_connection, netconf.wifi_helper_installed, netconf._wifi_profile,
     netconf.status) = saved

# -- the code: one reader, no nag ---------------------------------------------------
for parts in (("core", "routes", "network.py"), ("core", "services", "panel.py"),
              ("hub_server.py",)):
    code = "\n".join(ln for ln in read(*parts).splitlines()
                     if not ln.strip().startswith("#"))
    check(f"{'/'.join(parts)} has no \"iriv1234\" fallback of its own",
          not re.search(r"or\s+\"iriv1234\"", code))
net_toml = read("config", "network.toml")
check("config/network.toml ships no [ap].password (the key lives in network.local.toml)",
      not re.search(r"(?m)^\s*password\s*=", net_toml))
route = read("core", "routes", "network.py")
check("the Network page reads the key through netconf and no longer flags the default",
      "netconf.ap_password()" in route and "ap_password_shipped" not in route)
hub = read("hub_server.py")
check("the hub stores the default on a key-less box at boot, before raising the AP",
      hub.index("ensure_ap_password(config_dir)") < hub.index("apply_wifi_mode("))
check("the hub posts no 'factory key' Inbox notice any more",
      "ap_key_shipped" not in hub and "factory key" not in hub)
fe = read("frontend", "src", "components", "NetworkPanel.jsx")
check("the Network page has no client-side refusal of iriv1234",
      "AP_SHIPPED_KEY" not in fe and "factory password" not in fe)

# -- a key just stored reaches a radio that is already up -------------------------
# The hub restarted by an OTA update (no reboot) may find the hotspot up on a
# key it did not store; without the Wi-Fi helper the boot path only raised an
# AP that was down. rekey=True (the hub just stored the key) raises it anyway.
import threading                                              # noqa: E402
raised = []
saved = (netconf.available, netconf.devices, netconf.radio_on, netconf.ap_active,
         netconf.ap_enable, netconf.wifi_helper_installed)
netconf.available = lambda: True
netconf.devices = lambda: [{"type": "wifi", "device": "wlan0"}]
netconf.radio_on = lambda: True
netconf.ap_active = lambda: "Hotspot"
netconf.ap_enable = lambda ssid, pw, **_k: raised.append(pw)
netconf.wifi_helper_installed = lambda: False
try:
    for rekey in (False, True):
        netconf.apply_wifi_mode("iriv-abc", "iriv1234", "ap", grace=0, rekey=rekey)
        for t in [t for t in threading.enumerate() if t.name == "wlan-mode"]:
            t.join(10)
finally:
    (netconf.available, netconf.devices, netconf.radio_on, netconf.ap_active,
     netconf.ap_enable, netconf.wifi_helper_installed) = saved
check("an AP already up is left alone at an ordinary start (no helper)...",
      raised[:1] == ["iriv1234"] and len(raised) == 1, repr(raised))
check("...and re-keyed when the hub has just stored the key",
      "rekey=not _had_key" in hub)

# -- the installer -------------------------------------------------------------
inst = read("scripts", "install.sh")
block = inst.split('say "Hotspot key"', 1)
check("install.sh has a hotspot key step", len(block) == 2)
if len(block) == 2:
    step = block[1].split("\nfi\n", 1)[0]
    check("...writing through the product's own function",
          "netconf.ensure_ap_password(cfg)" in step)
    check("...with no replacement of a stored key",
          "ap.pop(\"password\"" not in step and "is_shipped" not in step)
    check("...0600 and owned by the service account",
          'chmod 600 "${TARGET}/config/network.local.toml"' in step
          and 'chown "${SERVICE_USER}:${SERVICE_USER}" "${TARGET}/config/network.local.toml"' in step)
    # Run the installer's own Python snippet, as install.sh does (argv[1] =
    # the install dir, config under it), against a fresh and an existing box.
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", step, re.S)
    check("...and its Python snippet can be found", bool(m))
    if m:
        for label, seed, want in (("a NEW box", None, "iriv1234"),
                                  ("an existing box", "k7mpq2wxhz9a", "k7mpq2wxhz9a")):
            target = tempfile.mkdtemp()
            os.makedirs(os.path.join(target, "config"))
            # argv[1] has no core/ of its own here: PYTHONPATH supplies it.
            if seed:
                config.save_local("network", {"ap": {"password": seed}},
                                  os.path.join(target, "config"))
            env = dict(os.environ, PYTHONPATH=ROOT)
            r = subprocess.run([sys.executable, "-", target], input=m.group(1),
                               capture_output=True, text=True, env=env, timeout=60)
            got = stored(os.path.join(target, "config"))
            check(f"install.sh on {label} prints and stores {want}",
                  r.returncode == 0 and r.stdout.strip() == want and got == want,
                  repr((r.returncode, r.stdout, r.stderr[-300:], got)))
check("the Done banner names the hotspot key", "Hotspot key    :" in inst)

# -- who writes [ap].user_set ----------------------------------------------------
# Only Save's writer. A boot, install or update that wrote it (or re-keyed on
# its absence) would be guessing who chose a key stored before it existed.
for parts in (("scripts", "install.sh"), ("scripts", "upgrade_steps.sh"),
              ("hub_server.py",), ("core", "services", "netconf.py"),
              ("core", "services", "wifiswitch.py"), ("core", "services", "wifimove.py")):
    check(f"{'/'.join(parts)} never reads or writes [ap].user_set",
          "user_set" not in read(*parts))
apconf_src = read("core", "services", "apconf.py")
check("apconf.save_ap is the one writer of [ap].user_set",
      'ap["user_set"] = True' in apconf_src and 'ap.pop("user_set", None)' in apconf_src)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
