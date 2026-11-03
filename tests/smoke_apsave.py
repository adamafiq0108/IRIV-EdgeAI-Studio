# The access point's name and password can be SAVED (Settings -> Network ->
# Access-point mode -> Save, Tok Aba 2026-10-06).
#
# WHAT WAS MISSING. A box already broadcasting showed only "Turn off AP mode":
# no way to store a changed password, and the name was fixed to iriv-<serial>.
# Pinned here, with nmcli and the root helper faked:
#   1. validation: name 1-32 BYTES, no control characters; password 8-63
#      printable ASCII (the default iriv1234 accepted); plain sentences
#   2. store: network.local.toml [ap].ssid / .password (0600), the setup name
#      not stored, an unchanged save writes nothing, password None keeps it
#   3. apply: AP off = stored only (no nmcli); broadcasting, caller on a cable
#      = restarted now through the helper with the new name and key;
#      broadcasting, caller ON wlan0 = restarted after the reply; a failed
#      restart says so and keeps the values
#   4. the front panel: the Wi-Fi page draws the saved name and key from the
#      next draw, and a save wakes the panel at once
#   5. Reset Wi-Fi: the setup name AND the default key iriv1234 come back
#      (Tok Aba, 2026-10-07); nothing typed is broadcast without being saved
#   5b. [ap].user_set: set when a person stores a key other than the default,
#      untouched by a rename, dropped whenever the default is stored (Reset),
#      never added to a key stored before the flag existed
#   6. the key is in no log line and no error message, the routes are admin
#      only, and the boot raises the SAVED name
#
#     python tests/smoke_apsave.py
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                       # noqa: E402
from core.services import apconf, hostinfo, netconf, panel     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except netconf.NetError as exc:
        return str(exc)
    return ""


hostinfo.ap_name = lambda: "iriv-0123abcd"

# Every log record, at every level, from every logger: the key must be in none.
records = []


class _Grab(logging.Handler):
    def emit(self, record):
        records.append(self.format(record))


logging.getLogger().addHandler(_Grab())
logging.getLogger().setLevel(logging.DEBUG)

# -- 1. validation -----------------------------------------------------------
for name, why in (("", "empty"), ("   ", "blank"), ("a" * 33, "33 bytes"),
                  ("\U0001F600" * 9, "36 bytes in 9 emoji"), ("my\tap", "a tab")):
    msg = refused(apconf.check_ap_ssid, name)
    check(f"name refused: {why}", bool(msg), repr(name))
check("...in a plain sentence, no HTTP code",
      refused(apconf.check_ap_ssid, "a" * 33).startswith("The network name is too long"))
check("a 32-byte name is taken", apconf.check_ap_ssid("b" * 32) == "b" * 32)
check("...and a name is trimmed", apconf.check_ap_ssid("  Site AP  ") == "Site AP")
check("an 8-emoji name (32 bytes) is taken", bool(apconf.check_ap_ssid("\U0001F600" * 8)))
for pw, why in (("short77", "7 characters"), ("x" * 64, "64 characters"),
                ("cafécafé", "an accented letter"), ("", "empty")):
    check(f"password refused: {why}", bool(refused(apconf.check_ap_password, pw)))
check("...in a plain sentence",
      refused(apconf.check_ap_password, "short77") == "The password needs at least 8 characters.")
for pw in ("abcdefgh", "x" * 63, "with space ~!@#"):
    check(f"password taken: {len(pw)} printable characters",
          apconf.check_ap_password(pw) == pw)

# -- 2. store ----------------------------------------------------------------
cfg = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "ap", "password": "oldkey2345"}}, cfg)
check("no saved name reads as the setup name", apconf.ap_ssid(cfg) == "iriv-0123abcd")
out = apconf.save_ap("Line 3 AP", "newkey6789", cfg)
local = config.load_local("network", cfg).get("ap") or {}
check("save stores the name and the key in network.local.toml",
      local.get("ssid") == "Line 3 AP" and local.get("password") == "newkey6789"
      and local.get("mode") == "ap", repr(local))
check("...answers changed and the name, never the key",
      out == {"changed": True, "ssid": "Line 3 AP"}, repr(out))
check("...and the one readers follow", apconf.ap_ssid(cfg) == "Line 3 AP"
      and netconf.ap_password(cfg) == "newkey6789")
if os.name == "posix":
    mode = os.stat(os.path.join(cfg, "network.local.toml")).st_mode & 0o777
    check("...in a file only the service account can read (0600)", mode == 0o600, oct(mode))
check("the same values again change nothing",
      apconf.save_ap("Line 3 AP", "newkey6789", cfg)["changed"] is False)
check("password None keeps the stored key",
      apconf.save_ap("Line 4 AP", None, cfg)["changed"] is True
      and netconf.ap_password(cfg) == "newkey6789")
check("the default key iriv1234 is accepted as a new key (Tok Aba, 2026-10-07)",
      apconf.save_ap("Line 4 AP", "iriv1234", cfg)["changed"] is True
      and netconf.ap_password(cfg) == "iriv1234")
apconf.save_ap("Line 4 AP", "newkey6789", cfg)
check("a bad key writes nothing",
      bool(refused(apconf.save_ap, "Other", "short", cfg))
      and apconf.ap_ssid(cfg) == "Line 4 AP")
apconf.save_ap("iriv-0123abcd", None, cfg)
check("saving the setup name stores no name (it follows the serial)",
      "ssid" not in (config.load_local("network", cfg).get("ap") or {}))
shipped = tempfile.mkdtemp()
config.save_local("network", {"ap": {"password": "iriv1234"}}, shipped)
check("a box on the default key can change only its name",
      apconf.save_ap("Renamed", None, shipped)["changed"] is True)

# -- 2b. who chose the key: [ap].user_set ---------------------------------------
def user_set(c):
    return (config.load_local("network", c).get("ap") or {}).get("user_set")


check("a box on the default key is not user_set, and a rename keeps it so",
      user_set(shipped) is None and apconf.ap_user_set(shipped) is False)
apconf.save_ap("Renamed", "chosenkey42", shipped)
check("a key a person saves (not the default) is recorded as user_set",
      user_set(shipped) is True and apconf.ap_user_set(shipped) is True)
apconf.save_ap("Renamed again", None, shipped)
apconf.save_ap("Renamed again", "chosenkey42", shipped)
check("...a rename, or the same key again, leaves it set", user_set(shipped) is True)
apconf.save_ap("Renamed again", "iriv1234", shipped)
check("...and storing the default clears it (the key is the default again)",
      user_set(shipped) is None and netconf.ap_password(shipped) == "iriv1234")
legacy = tempfile.mkdtemp()
config.save_local("network", {"ap": {"password": "k7mpq2wxhz9a"}}, legacy)
apconf.save_ap("Legacy AP", None, legacy)
apconf.save_ap("Legacy AP", "k7mpq2wxhz9a", legacy)
check("a key stored before the flag existed is kept and NOT marked on a rename "
      "(its origin is unknown)", user_set(legacy) is None
      and netconf.ap_password(legacy) == "k7mpq2wxhz9a")

# -- 3. apply, nmcli and the helper faked ----------------------------------------
# The dev box has no fastapi; the same stand-in smoke_boxrestart uses.
try:
    import fastapi                                            # noqa: F401
except ImportError:
    import types
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request = _HTTPException, object
    sys.modules["fastapi"] = fa
from core.routes import network as routes                      # noqa: E402

nm_calls, helper_calls = [], []
state = {"ap": "Hotspot", "helper": True, "helper_fail": ""}


def fake_nmcli(args, sudo=False, timeout=45):
    nm_calls.append(tuple(args))
    if args[:3] == ["-t", "-f", "GENERAL.CONNECTION"]:
        return "GENERAL.CONNECTION:%s\n" % (state["ap"] or "")
    if args[:3] == ["-t", "-f", "802-11-wireless.mode"]:
        return "802-11-wireless.mode:ap\n"
    return ""


def fake_helper(request):
    helper_calls.append(dict(request))
    if state["helper_fail"]:
        raise netconf.NetError(state["helper_fail"])
    return {"ok": True, "name": "Hotspot", "changed": True}


real_nmcli = netconf._nmcli
netconf._nmcli = fake_nmcli
netconf._wifi_profile = fake_helper
netconf.wifi_helper_installed = lambda: state["helper"]
netconf.status = lambda: {"available": True, "devices": [], "ap": state["ap"]}
later = []

cfg = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "ap", "password": "firstkey234"}}, cfg)

nm_calls.clear()
helper_calls.clear()
said = routes.save_and_apply("Site AP", "secondkey56", cfg, broadcasting=False, via="",
                             later=later.append)
check("AP off: stored only", said["applied"] == "stored"
      and netconf.ap_password(cfg) == "secondkey56", repr(said))
check("...nothing touches the radio", not nm_calls and not helper_calls and not later,
      repr((nm_calls, helper_calls)))
check("...and says it is for next time", "next time" in said["message"])

said = routes.save_and_apply("Site AP", "secondkey56", cfg, broadcasting=True, via="eth0",
                             later=later.append)
check("an unchanged save restarts nothing", said["applied"] == "unchanged"
      and not helper_calls and not later)

said = routes.save_and_apply("Site AP 2", "thirdkey789", cfg, broadcasting=True, via="eth0",
                             later=later.append)
check("broadcasting, caller on a cable: restarted now", said["applied"] == "now", repr(said))
check("...through the helper, with the NEW name and key on its stdin",
      helper_calls == [{"kind": "ap", "ssid": "Site AP 2", "psk": "thirdkey789"}],
      repr(helper_calls))
check("...no key on any nmcli argv",
      not any("thirdkey789" in " ".join(c) for c in nm_calls), repr(nm_calls))

helper_calls.clear()
said = routes.save_and_apply("Site AP 3", "fourthkey12", cfg, broadcasting=True, via="wlan0",
                             later=later.append)
check("broadcasting, caller ON the AP: restart left for after the reply",
      said["applied"] == "scheduled" and later == [cfg] and not helper_calls, repr(said))
check("...the reply names the network to join",
      "Site AP 3" in said["message"] and "fourthkey12" not in said["message"])

# The scheduled restart itself, at no delay.
routes._restart_ap_later(cfg, delay=0)
for t in [t for t in threading.enumerate() if t.name == "ap-restart"]:
    t.join(5)
check("...and the delayed restart raises the saved values",
      helper_calls[-1:] == [{"kind": "ap", "ssid": "Site AP 3", "psk": "fourthkey12"}],
      repr(helper_calls))

state["helper_fail"] = "Connection activation failed"
said = routes.save_and_apply("Site AP 4", "fifthkey345", cfg, broadcasting=True, via="eth0",
                             later=later.append)
check("a failed restart says so and keeps the values",
      said["applied"] == "failed" and "after a reboot" in said["message"]
      and netconf.ap_password(cfg) == "fifthkey345", repr(said))
routes._restart_ap_later(cfg, delay=0)
for t in [t for t in threading.enumerate() if t.name == "ap-restart"]:
    t.join(5)
state["helper_fail"] = ""

# The legacy path (no helper yet): the existing hotspot command.
state["helper"] = False
nm_calls.clear()
routes.save_and_apply("Legacy AP", "sixthkey678", cfg, broadcasting=True, via="eth0",
                      later=later.append)
hot = [c for c in nm_calls if c[:3] == ("device", "wifi", "hotspot")]
check("no helper: the existing hotspot command, new name and key",
      len(hot) == 1 and "Legacy AP" in hot[0] and "sixthkey678" in hot[0], repr(nm_calls))
state["helper"] = True

# A timed-out nmcli quotes its argv in str(TimeoutExpired); it must not reach
# the page or the log (that argv carries the key on a legacy box).
real_run = subprocess.run


def timing_out(argv, **kw):
    raise subprocess.TimeoutExpired(argv, kw.get("timeout", 1))


subprocess.run = timing_out
try:
    msg = refused(real_nmcli, ["device", "wifi", "hotspot", "ifname", "wlan0",
                               "ssid", "X", "password", "leakykey99"], sudo=True)
finally:
    subprocess.run = real_run
check("a timed-out nmcli is reported without its argv",
      msg and "leakykey99" not in msg and "did not answer" in msg, msg)

# -- 4. the front panel ------------------------------------------------------
pcfg = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "ap", "password": "panelkey111"}}, pcfg)


class Rec:
    def __init__(self):
        self.texts = []

    def clear(self, *_):
        pass

    def line(self, *_):
        pass

    def text(self, x, y, words, size=1):
        self.texts.append(str(words))


def wifi_page(config_dir):
    canvas = Rec()
    panel.render("wifi", panel._facts(config_dir), panel.settings(config_dir), canvas)
    return canvas.texts


before = wifi_page(pcfg)
check("the Wi-Fi page prints the setup name and the key",
      "iriv-0123abcd" in before and "Password: panelkey111" in before, repr(before))


class FakePanel:
    def __init__(self):
        self._wake = threading.Event()


fake = FakePanel()
panel._panel = fake
apconf.save_ap("Packing Line", "panelkey222", pcfg)
check("a save wakes the running panel at once", fake._wake.is_set())
after = wifi_page(pcfg)
check("...and the next draw prints the NEW name and key, no restart",
      "Packing Line" in after and "Password: panelkey222" in after
      and "panelkey111" not in " ".join(after), repr(after))
check("...the {ap_name} placeholder follows too",
      panel.fill("{ap_name}", panel._facts(pcfg)) == "Packing Line")
fake._wake.clear()
apconf.save_ap("Packing Line", "panelkey222", pcfg)
check("an unchanged save does not wake it", not fake._wake.is_set())
panel._panel = None
check("no panel running is not an error", apconf.save_ap("Line 9", None, pcfg)["changed"])

# -- 5. the routes: Save, Reset, admin only ------------------------------------
from fastapi import HTTPException                             # noqa: E402


class _State:
    pass


class FakeRequest:
    def __init__(self, body, config_dir, peer="192.168.1.50"):
        self._body = body
        self.app = type("A", (), {"state": _State()})()
        self.app.state.config_dir = config_dir
        self.state = _State()
        self.state.user = {"username": "someone"}
        self.client = type("C", (), {"host": peer})()

    async def json(self):
        return self._body


rcfg = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "ap", "password": "routekey111",
                                     "ssid": "Custom Name"}}, rcfg)
# The readers the routes call without a config_dir go to this test's dir.
for mod, fn in ((apconf, "ap_ssid"), (netconf, "ap_password"),
                (netconf, "wifi_mode"),
                (netconf, "ipv4_reboot_pending")):
    orig = getattr(mod, fn)
    setattr(mod, fn, (lambda o: lambda config_dir=None: o(config_dir or rcfg))(orig))
real_set_mode = netconf.set_wifi_mode
netconf.set_wifi_mode = (lambda mode, password=None, config_dir=None:
                         real_set_mode(mode, password, config_dir or rcfg))
netconf.devices = lambda: [{"device": "eth0", "type": "ethernet", "ip": "192.168.1.9"},
                           {"device": "wlan0", "type": "wifi", "ip": "10.42.0.1"}]

real_is_site_admin = routes.auth_service.is_site_admin
routes.auth_service.is_site_admin = lambda user, cfg=None: False
denied = []
for handler, body in ((routes.network_ap_save, {"ssid": "X", "password": "abcdefgh1"}),
                      (routes.network_status, {}), (routes.network_reset, {})):
    try:
        asyncio.run(handler(FakeRequest(body, rcfg)))
    except HTTPException as exc:
        denied.append(exc.status_code)
check("Save, the status (which carries the key) and Reset are admin only",
      denied == [403, 403, 403], repr(denied))
check("...and a refused Save wrote nothing", apconf.ap_ssid(rcfg) == "Custom Name")
routes.auth_service.is_site_admin = lambda user, cfg=None: True

helper_calls.clear()
answer = asyncio.run(routes.network_ap_save(
    FakeRequest({"ssid": "Dock AP", "password": "routekey222"}, rcfg)))
check("POST ap/save from a cable while broadcasting: applied now",
      answer["ap_save"]["applied"] == "now" and answer["ap_name"] == "Dock AP"
      and helper_calls[-1]["psk"] == "routekey222", repr(answer.get("ap_save")))
check("...the page is told the setup name too", answer["ap_name_factory"] == "iriv-0123abcd")
check("...and the key the page saved is recorded as user_set",
      (config.load_local("network", rcfg).get("ap") or {}).get("user_set") is True)
try:
    asyncio.run(routes.network_ap_save(FakeRequest({"password": "short"}, rcfg)))
    check("a bad password is refused by the route", False)
except HTTPException as exc:
    check("a bad password is refused by the route, in words",
          exc.status_code == 400 and exc.detail.startswith("The password needs"), exc.detail)

helper_calls.clear()
asyncio.run(routes.network_reset(FakeRequest({"ssid": "Typed", "password": ""}, rcfg)))
check("Reset Wi-Fi brings back the setup name (a saved name is dropped)",
      apconf.ap_ssid(rcfg) == "iriv-0123abcd"
      and "ssid" not in (config.load_local("network", rcfg).get("ap") or {}))
check("...and the default key iriv1234, stored (so the screen shows it)",
      netconf.ap_password(rcfg) == "iriv1234"
      and (config.load_local("network", rcfg).get("ap") or {}).get("password") == "iriv1234")
check("...and raises the AP on exactly those",
      helper_calls[-1:] == [{"kind": "ap", "ssid": "iriv-0123abcd", "psk": "iriv1234"}],
      repr(helper_calls))
check("...and drops [ap].user_set (Save set it for routekey222 above)",
      "user_set" not in (config.load_local("network", rcfg).get("ap") or {}))
asyncio.run(routes.network_reset(FakeRequest({"password": "typedkey333"}, rcfg)))
check("a key typed into an older page's Reset is SAVED before it is broadcast",
      netconf.ap_password(rcfg) == "typedkey333"
      and helper_calls[-1]["psk"] == "typedkey333")
check("...and, being a key a person typed, is recorded as user_set",
      (config.load_local("network", rcfg).get("ap") or {}).get("user_set") is True)
routes.auth_service.is_site_admin = real_is_site_admin

# -- 6. the key in no log line; the boot raises the saved name -----------------
keys = ("secondkey56", "thirdkey789", "fourthkey12", "fifthkey345", "sixthkey678",
        "panelkey111", "panelkey222", "routekey111", "routekey222", "typedkey333",
        "leakykey99")
leaked = [r for r in records if any(k in r for k in keys)]
check("no log line carries a key (%d lines checked)" % len(records), not leaked,
      repr(leaked[:3]))
check("...while the restarts were logged by name",
      any("access point restarted as" in r for r in records)
      and any("did not restart" in r for r in records), repr(records[-5:]))

with open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8") as fh:
    hub = fh.read()
check("the boot raises the SAVED name (apconf.ap_ssid), not the serial's",
      "apply_wifi_mode(_apconf.ap_ssid(config_dir)" in hub)
with open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8") as fh:
    src = fh.read()
check("no em dash in the route", "\u2014" not in src)
check("the save outcome never names the key field",
      "password" not in json.dumps(answer.get("ap_save")))

print("\n%s" % ("all access point save checks passed" if not bad
                else "%d access point save check(s) FAILED" % bad))
sys.exit(1 if bad else 0)
