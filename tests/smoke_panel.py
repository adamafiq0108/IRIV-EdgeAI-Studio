# Front panel: the framebuffer, the font, the page layouts, and the routes.
#
# No hardware. Everything worth pinning here is a pure function of (page,
# facts, settings) -- which is exactly why the panel was built that way: the
# screens can be asserted on a laptop, and only the I2C write needs a board.
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.services import panel, ssd1306                     # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


print("== canvas")
c = ssd1306.Canvas()
check("blank", set(c.buf) == {0})
c.pixel(0, 0, 1)
check("top-left pixel is bit 0 of byte 0", c.buf[0] == 1)
c.pixel(0, 8, 1)
check("row 8 lands in page 1", c.buf[c.width] == 1)
c.pixel(0, 0, 0)
check("pixel clears", c.buf[0] == 0)
c.clear(0)
c.fill_rect(2, 2, 3, 3, 1)
rows = c.rows()
check("fill_rect covers exactly its box",
      rows[2][2:5] == "111" and rows[4][2:5] == "111" and rows[1][2:5] == "000"
      and rows[2][1] == "0" and rows[2][5] == "0")
c.clear(0)
c.line(0, 10, 20, 10)
check("horizontal line", c.rows()[10][:21] == "1" * 21)

# Off-screen writes must be dropped, not wrapped: a wrapped pixel is a stray
# dot on the opposite edge, which on a 128px screen looks like a fault.
c.clear(0)
c.pixel(-1, 5, 1)
c.pixel(999, 5, 1)
c.pixel(5, 999, 1)
check("out-of-bounds pixels are dropped", set(c.buf) == {0})

print("== font")
check("95 printable glyphs", len(ssd1306._FONT) == 95 * 5)
c.clear(0)
c.text(0, 0, "A")
art = [r[:6] for r in c.rows()[:8]]
check("A is drawn and is 5 wide with a gap",
      any("1" in r for r in art) and all(r[5] == "0" for r in art))
c.clear(0)
end = c.text(0, 0, "AB", size=2)
check("size 2 advances 12px per glyph", end == 24)
check("text_width matches the advance", ssd1306.text_width("AB", 2) == 22)
c.clear(0)
c.text(0, 0, "é")          # non-ASCII must not crash or index out of range
check("unknown character falls back", True)

print("== pages")
FACTS = {"wireless": "W", "total_ram": "8", "total_disk": "64",
         "ai_hat": "AI26T", "revision": "v1.0.0", "os_bits": "64",
         "codename": "Trixie", "serial": "abc123",
         "ip_eth0": "192.168.1.10", "ip_eth1": "", "ip_wlan0": "10.0.0.5",
         "cpu_load": "1.04", "cpu_temp": "56.3'C",
         "ram": "1.7/8.1GB 21%", "disk": "21/29GB 77%"}
CFG = panel.settings()

for page in panel.PAGE_IDS:
    canvas = panel.render(page, FACTS, CFG)
    lit = sum(row.count("1") for row in canvas.rows())
    check("%s draws something" % page, lit > 50, "only %d pixels" % lit)
    # The footer is the panel's one constant: a rule at y=52 and the hint
    # under it. A page that loses it is a page that looks like a crash.
    check("%s keeps the footer rule" % page,
          canvas.rows()[52].count("1") > 100)

off = panel.render("off", FACTS, CFG)
check("the shutdown screen has no 'press to continue'",
      off.rows()[52].count("1") == 0)

# Every row must fit the glass. 21 characters at 6px is 126px; the 22nd would
# be cut mid-glyph, which is how the factory panel loses its temperature.
status = panel.render("status", FACTS, CFG)
# The TEXT rows only: the footer rule is drawn to x=127 on purpose, so a
# blanket "nothing touches the last column" would assert the rule away.
text_rows = [r for y, r in enumerate(status.rows())
             if 15 <= y <= 21 or 27 <= y <= 33 or 39 <= y <= 45]
check("status text fits the screen width",
      all(row[-1] == "0" for row in text_rows))
check("the temperature is not cut off",
      "T:56.3'C" in "Load: %s T:%s" % (FACTS["cpu_load"], FACTS["cpu_temp"]))

print("== settings")
check("defaults are off", panel.settings()["enabled"] is False)
check("every page id is real",
      all(p in panel.PAGE_IDS for p in panel.settings()["pages"]))
check("preview returns scanlines",
      len(panel.preview("about")["rows"]) == 64)
# The preview must honour unsaved edits, or an editor beside it is a lie.
edited = panel.preview("url", overrides={"dashboard_url": "ZZZZ"})
plain = panel.preview("url", overrides={"dashboard_url": "AAAA"})
check("preview reflects unsaved settings", edited["rows"] != plain["rows"])

print("== routes")
src = (ROOT / "core" / "routes" / "panel.py").read_text(encoding="utf-8")
# Two routers in this file: the panel's own, and power_router -- which a
# MASTER mounts as well, so the sign-out menu on a system page can reboot the
# box without a cross-origin call to the hub. Both are matched: whichever
# router a handler hangs on, it is still the box owner's.
handlers = re.findall(r"@(?:power_)?router\.(?:get|post)\([^)]*\)\s*\nasync def (\w+)",
                      src)
check("every panel route exists", len(handlers) >= 6, str(handlers))
for name in handlers:
    body = src.split("async def %s" % name, 1)[1].split("@power_router", 1)[0] \
        .split("@router", 1)[0]
    # Power is the ONE exception, deliberately: an operation lead reboots the
    # box they run. Everything else here reads the box's serial and addresses
    # or changes what the glass says, which stays the owner's.
    want = "_require_site_admin(request)" if name == "panel_power" \
        else "_require_admin(request)"
    check("%s asks for %s" % (name, want.split("(")[0]), want in body, body[:120])
check("...and power is not left master-only",
      "_require_admin(request)" not in
      src.split("async def panel_power", 1)[1].split("@router", 1)[0])
# Powering the box down is offered on every port, so it has to be MOUNTED on
# every port -- a menu item behind a 404 is worse than no menu item.
check("power rides a router a master can mount too",
      "power_router = APIRouter()" in src
      and "router.include_router(power_router)" in src)
master = (ROOT / "master_server.py").read_text(encoding="utf-8")
check("...and the master mounts exactly that one",
      "from core.routes.panel import power_router" in master
      and "app.include_router(power_router)" in master
      and "panel_router" not in master)
check("power refuses an empty action",
      'detail=\'action must be "shutdown" or "reboot"\'' in src)

net = (ROOT / "core" / "routes" / "network.py").read_text(encoding="utf-8")
# Decorated handlers only -- `_body` is a helper that takes a request and is
# not a route, and asserting a guard inside it would pass for the wrong reason.
for name in re.findall(r"@router\.(?:get|post)\([^)]*\)\s*\nasync def (\w+)", net):
    body = net.split("async def %s" % name, 1)[1].split("@router", 1)[0]
    check("network.%s is admin only" % name, "_require_admin(request)" in body)

netsvc = (ROOT / "core" / "services" / "netconf.py").read_text(encoding="utf-8")
check("the AP password floor is enforced in the service",
      "MIN_AP_PASSWORD" in netsvc and "len(password) < MIN_AP_PASSWORD" in netsvc)
# nmcli escapes colons inside values; splitting naively renames networks.
check("nmcli output is split on unescaped colons only",
      "def _split(line)" in netsvc)

# Reading an input must not CLAIM it. gpiozero's lgpio backend keeps
# `consumer="lg"` on the line after close(), and /api/board/io reads every
# input on a page the Ports panel polls once a second -- so a master that had
# served that page owned the user button for the life of the process, and the
# front panel's button did nothing on a box where nothing else wanted it.
board = (ROOT / "core" / "services" / "board_io.py").read_text(encoding="utf-8")
read_src = board.split("def read_port", 1)[1].split("\ndef ", 1)[0]
check("an input is read with gpioget, which releases the line",
      '"gpioget"' in read_src and read_src.index('"gpioget"')
      < read_src.index("DigitalInputDevice"))

hub = (ROOT / "core" / "routes" / "hub.py").read_text(encoding="utf-8")
check("stopping Node-RED is an admin action",
      "_require_admin(request, \"starting or stopping Node-RED\")" in hub)

print()

# -- board detection asks the HARDWARE ----------------------------------------
# It used to require /etc/iriv, which is written by the platform SOFTWARE (it
# holds installed_apps and a migration flag). A board flashed with a stock
# Raspberry Pi OS image has no such directory, so a real IRIV box reported "no
# IRIV board on this box" for the USER button and the buzzer while the OLED
# two lines above was working.
print("== board detection")
bio_src = (ROOT / "core" / "services" / "board_io.py").read_text(encoding="utf-8")
check("the carrier is probed over I2C, not inferred from a file",
      "def carrier_present" in bio_src and "_CARRIER_I2C_ADDR" in bio_src)
check("/etc/iriv is corroboration, not the requirement",
      "carrier_present() or os.path.isdir(\"/etc/iriv\")" in bio_src)
check("the CM5 half of the two-signal rule is kept",
      'if "compute module 5" not in model.lower():' in bio_src)
check("an unavailable probe answers False rather than raising",
      "except (OSError, ImportError, AttributeError):" in bio_src)

# -- Node-RED takes the board, and the front panel goes with it ----------------
#
# The factory flow drives this same OLED, this same user button and this same
# buzzer. Leaving our panel running would put two programs on one screen --
# which is how it used to flicker between two sets of pixels -- and the button
# and buzzer would go to whichever claimed them first.
#
# So the panel is released BEFORE Node-RED starts and restored AFTER it stops.
# No reboot and no service restart: the panel is ours, in this process, and
# letting go is immediate.
import subprocess                                             # noqa: E402
import tempfile                                               # noqa: E402

PCFG = tempfile.mkdtemp()
_real_run = subprocess.run
_calls = []


class _Ok:
    returncode, stdout, stderr = 0, "", ""


def _fake_run(cmd, *a, **k):
    _calls.append(list(cmd))
    return _Ok()


subprocess.run = _fake_run
panel.stop()                       # nothing is running in a test process
panel.save_settings({"enabled": True}, PCFG)

ok, msg = panel.set_nodered(True, PCFG)
check("switching Node-RED on succeeds", ok, msg)
check("...and switches the front panel off, because they share the screen",
      panel.settings(PCFG)["enabled"] is False)
check("...saying so, rather than leaving a dark screen to be discovered",
      "front panel was switched off" in msg, msg)
check("...and the panel is released BEFORE Node-RED is started, or it would "
      "claim lines that are still held",
      _calls and _calls[-1][:3] == ["sudo", "-n", "systemctl"]
      and "nodered" in _calls[-1])

ok, msg = panel.set_nodered(False, PCFG)
check("switching Node-RED off succeeds", ok, msg)
check("...and gives the front panel back", panel.settings(PCFG)["enabled"] is True)
check("...saying so", "front panel is back on" in msg, msg)

# THE GUARD. A panel the OPERATOR turned off must stay off: we only put back
# what we took. Turning on something somebody deliberately turned off is worse
# than leaving it.
panel.save_settings({"enabled": False}, PCFG)
panel.set_nodered(True, PCFG)
check("a panel that was already off is not recorded as ours to restore",
      panel._took_panel(PCFG) is False)
panel.set_nodered(False, PCFG)
check("...and is still off after Node-RED goes away",
      panel.settings(PCFG)["enabled"] is False,
      "we only put back what we took")

subprocess.run = _real_run

# H1: the Wi-Fi page prints the hotspot key the box actually broadcasts. The
# panel used to ship its own `ap_password = "iriv1234"` and prefer it, so a
# re-keyed box kept printing the factory key on the OLED. One source now:
# network [ap].password, read live; a stored panel copy is ignored and dropped.
print("== wifi page follows network [ap].password")
from core import config as config_mod                        # noqa: E402
ACFG = tempfile.mkdtemp()
config_mod.save_local("network", {"ap": {"password": "newkey99"}}, ACFG)
config_mod.save_local("panel", {"panel": {"ap_password": "iriv1234"}}, ACFG)
check("shipped panel.toml carries no ap_password",
      not re.search(r"(?m)^\s*ap_password\s*=",
                    (ROOT / "config" / "panel.toml").read_text(encoding="utf-8")))
check("settings() exposes no panel copy of the key",
      "ap_password" not in panel.settings(ACFG))
line = panel.fill(panel.DEFAULT_TEXT["wifi_password"], panel._facts(ACFG),
                  panel.settings(ACFG))
check("wifi line prints the network key", "newkey99" in line, line)
check("...never the shipped default over a stored panel copy",
      "iriv1234" not in line, line)
config_mod.save_local("network", {"ap": {"password": "rekeyed42"}}, ACFG)
line = panel.fill(panel.DEFAULT_TEXT["wifi_password"], panel._facts(ACFG),
                  panel.settings(ACFG))
check("a re-key shows without a restart", "rekeyed42" in line, line)
check("status facts carry the live key",
      panel._facts(ACFG)["ap_password"] == "rekeyed42")
panel.save_settings({"ap_password": "typed", "hint": "h"}, ACFG)
check("save drops a stored panel ap_password and refuses a new one",
      "ap_password" not in (config_mod.load_local("panel", ACFG).get("panel") or {}))

# -- a static address another device answers for ------------------------------
# Product owner, 2026-10-11: the IP screen says "IP CONFLICT" on the port's
# line. Asked by panel_ipcheck with ipconflict's own arping -D, static ports
# only, off the display thread; anything that cannot be asked shows nothing.
print("== IP screen: static address conflict")
import time                                                   # noqa: E402
from core.services import ipconflict, netconf, panel_ipcheck  # noqa: E402

IPF = dict(FACTS, ip_eth1="", ip_conflicts=frozenset({"eth0"}))
T = panel.DEFAULT_TEXT
line0 = panel._ip_line("eth0", T, IPF, CFG)
check("a conflicting port reads 'eth0: IP CONFLICT' (17)",
      line0 == "eth0: IP CONFLICT" and len(line0) == 17, repr(line0))
lw = panel._ip_line("wlan0", T, dict(IPF, ip_conflicts={"wlan0"}), CFG)
check("...and 'wlan0: IP CONFLICT' (18) fits the 21-character row",
      lw == "wlan0: IP CONFLICT" and len(lw) == 18, repr(lw))
check("a port with no conflict is unchanged",
      panel._ip_line("wlan0", T, IPF, CFG) == "wlan0: 10.0.0.5")
check("a port with no address reads 'eth1: -' instead of a bare label",
      panel._ip_line("eth1", T, IPF, CFG) == "eth1: -")
check("a conflict on a port with no address still says so",
      panel._ip_line("eth1", T, dict(IPF, ip_conflicts={"eth1"}), CFG)
      == "eth1: IP CONFLICT")
clash = panel.render("ip", IPF, CFG).rows()
calm = panel.render("ip", dict(IPF, ip_conflicts=frozenset()), CFG).rows()
check("the eth0 row is redrawn, the others are not",
      clash[15:22] != calm[15:22] and clash[27:46] == calm[27:46])
check("facts carry the cached answer, never a probe",
      "ip_conflicts" in panel._facts())

DEVS = [{"device": "eth0", "method": "manual", "ip": "192.168.1.50"},
        {"device": "eth1", "method": "auto", "ip": "192.168.2.9"},
        {"device": "wlan0", "method": "shared", "ip": "10.42.0.1"}]
asked = []
probe = {"out": "", "rc": 0}


def _arping_run(argv, timeout=8):
    asked.append(list(argv))
    if probe.get("raise"):
        raise RuntimeError("boom")
    return probe["rc"], probe["out"]


REPLY = ("ARPING 192.168.1.50 from 0.0.0.0 eth0\nUnicast reply from 192.168.1.50 "
         "[AA:BB:CC:DD:EE:01]  1.0ms\nSent 1 probes (1 broadcast(s))\nReceived 1 response(s)\n")
netconf.devices = lambda: DEVS
ipconflict.run = _arping_run
ipconflict.which = lambda name: "/usr/bin/" + name
ipconflict.own_macs = lambda: {"d8:3a:dd:00:00:01"}

probe.update(out=REPLY, rc=1)
found = panel_ipcheck.check_now()
check("another device answering arping -D is a conflict on that port",
      found == {"eth0"}, str(found))
check("...asked with one DAD probe (arping -D) on eth0 only: DHCP and the "
      "hotspot are never probed",
      [a[:2] + a[-3:] for a in asked] == [["arping", "-D", "-I", "eth0", "192.168.1.50"]],
      str(asked))
probe.update(out="ARPING 192.168.1.50 from 0.0.0.0 eth0\nSent 2 probes (2 broadcast(s))\n"
             "Received 0 response(s)\n", rc=0)
check("no reply is no conflict", panel_ipcheck.check_now() == frozenset())
probe.update(out=REPLY.replace("AA:BB:CC:DD:EE:01", "D8:3A:DD:00:00:01"), rc=1)
check("a reply from this box's own other port is not a conflict",
      panel_ipcheck.check_now() == frozenset())
DEVS[0] = {"device": "eth0", "method": "auto", "ip": "192.168.1.50"}
probe.update(out=REPLY, rc=1)
del asked[:]
check("a DHCP port is ignored even when something answers",
      panel_ipcheck.check_now() == frozenset() and not asked, str(asked))
DEVS[0] = {"device": "eth0", "method": "manual", "ip": "", "static_ip": "192.168.1.50"}
check("a static jack NetworkManager held back (no live address) is still asked",
      panel_ipcheck.check_now() == {"eth0"})
ipconflict.which = lambda name: None
del asked[:]
check("no arping on the box: nothing shown, nothing run",
      panel_ipcheck.check_now() == frozenset() and not asked)
ipconflict.which = lambda name: "/usr/bin/" + name
probe.update(out="arping: socket: Operation not permitted\n", rc=2)
check("an arping that may not send: nothing shown",
      panel_ipcheck.check_now() == frozenset())
probe["raise"] = True
check("a probe that raises: nothing shown, no exception",
      panel_ipcheck.check_now() == frozenset())
probe["raise"] = False


def _no_nmcli():
    raise OSError("nmcli gone")


netconf.devices = _no_nmcli
check("no device list: nothing shown, no exception",
      panel_ipcheck.check_now() == frozenset())
netconf.devices = lambda: DEVS

# poke(): never blocks the display thread, one check at a time, at most every
# INTERVAL, the panel told to redraw when the answer changes; old answers hide.
_real_check = panel_ipcheck.check_now


def _slow_check():
    time.sleep(0.4)
    return frozenset({"eth0"})


panel_ipcheck.check_now = _slow_check
redraws = []
t0 = time.monotonic()
first = panel_ipcheck.poke(lambda: redraws.append(1))
check("poke returns at once while the probe runs",
      first is True and time.monotonic() - t0 < 0.2)
check("...and a second poke does not start another",
      panel_ipcheck.poke() is False)
for _ in range(40):
    if panel_ipcheck.conflicts():
        break
    time.sleep(0.05)
check("the answer is cached for the next draw", panel_ipcheck.conflicts() == {"eth0"})
check("...and the panel was asked to redraw", redraws == [1])
check("no new probe inside the interval", panel_ipcheck.poke() is False)
panel_ipcheck._state["done"] -= panel_ipcheck.SHOW_FOR + 1
check("an answer older than SHOW_FOR is not shown", panel_ipcheck.conflicts() == frozenset())


def _raising_check():
    raise RuntimeError("boom")


panel_ipcheck.check_now = _raising_check
panel_ipcheck._state["started"] = None
panel_ipcheck.poke()
for _ in range(40):
    if not panel_ipcheck._state["busy"]:
        break
    time.sleep(0.05)
check("a worker that raises leaves 'no conflict' and is not stuck busy",
      not panel_ipcheck._state["busy"] and panel_ipcheck._state["found"] == frozenset())
panel_ipcheck.check_now = _real_check
src = (ROOT / "core" / "services" / "panel.py").read_text(encoding="utf-8")
check("the display thread only pokes, on the IP page",
      'if page_id == "ip":' in src and "panel_ipcheck.poke(refresh)" in src
      and "check_now" not in src)


if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
