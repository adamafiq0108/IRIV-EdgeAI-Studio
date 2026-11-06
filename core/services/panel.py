# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
panel.py -- the box's front panel: OLED, user button, buzzer.

This is the Python of the factory Node-RED flows, in one process instead of
forty wired nodes:

  * the USER button steps through the OLED pages, and beeps when pressed;
  * holding it for five seconds shuts the box down (the dashboard can ask for
    the same thing, and for a reboot);
  * the pages themselves -- identity, how to reach the dashboard, the IP
    addresses, the live board status -- redraw from `hostinfo`.

WHY IT IS PART OF THE HUB, not a service of its own: everything on the panel
is something the hub already knows or already computes, the panel must not
outlive the software it reports on, and a second unit is a second thing to
install, update and fail. It is a daemon thread that gives up quietly --
no board, no OLED, no gpiozero, a screen someone else is holding -- because
a front panel is the last thing that should keep an appliance from starting.

ONE OWNER PER SCREEN. The factory Node-RED flow drives this same OLED, the
same button and the same buzzer. Both running means two programs writing
opposite pixels down one I2C bus, so `status()` reports the conflict and
`config/panel.local.toml` `enabled = false` is the way to stand down. Nothing
here disables anybody else's software.
"""

import subprocess
import threading
import time

from core import config as config_mod
from core.services import board_io, hostinfo, hostname, panel_ipcheck, ssd1306, webaccess

#: Page ids in the order the button walks them.
PAGE_IDS = ("about", "wifi", "url", "ip", "status")

#: Every word the panel prints, and what it says out of the box.
#:
#: A line is a TEMPLATE: `{serial}`, `{ip_eth0}`, `{cpu_temp}` and the rest of
#: what hostinfo publishes are substituted at draw time. That is the whole
#: point -- an integrator rebadging the box, or writing the panel in another
#: language, should not need a new build, and a fixed string with the facts
#: welded into the code cannot be either.
#:
#: An unknown placeholder renders as nothing rather than raising: a typo in a
#: settings field must not blank the screen.
DEFAULT_TEXT = {
    "about_title": "IRIV EdgeAI Studio",
    "about_model": "{wireless}R{total_ram}E{total_disk}",
    "about_accel": "{ai_hat}",
    "wifi_title": "ACCESS DASHBOARD:",
    "wifi_step": "1. Connect WiFi AP:",
    "wifi_password": "Password: {ap_password}",
    "url_title": "ACCESS DASHBOARD:",
    "url_step": "2. Open this url:",
    "ip_title": "IP ADDRESSES:",
    "ip_eth0": "eth0: {ip_eth0}",
    "ip_eth1": "eth1: {ip_eth1}",
    "ip_wlan0": "wlan0: {ip_wlan0}",
    "status_title": "STATUS:",
    "status_load": "Load: {cpu_load} T:{cpu_temp}",
    "status_ram": "RAM:  {ram}",
    "status_disk": "Disk: {disk}",
    "off": "Shutting down...",
    "reset_title": "MASTER PW RESET",
    "reset_hint": "Type on Forgot page",
}

#: Which lines belong to which page, so the editor can show one page's
#: wording at a time instead of seventeen fields in a column.
TEXT_BY_PAGE = {
    "about": ("about_title", "about_model", "about_accel"),
    "wifi": ("wifi_title", "wifi_step", "wifi_password"),
    "url": ("url_title", "url_step"),
    "ip": ("ip_title", "ip_eth0", "ip_eth1", "ip_wlan0"),
    "status": ("status_title", "status_load", "status_ram", "status_disk"),
}

#: A settings field is a line on a 128px screen, not an essay. Generous
#: enough for a translated label, short enough that nothing can be pasted in
#: to make the panel spend its whole loop drawing.
MAX_TEXT = 120


class _Blanks(dict):
    """Unknown placeholders render empty instead of raising KeyError."""

    def __missing__(self, key):
        return ""


def fill(template, facts, cfg=None):
    """Substitute {placeholders} into one line of panel wording.

    Never raises. A malformed template -- a stray brace, an unclosed field --
    is printed as typed, which shows the operator their own mistake on the
    screen they are editing. The alternative is a blank page and no clue.
    """
    values = _Blanks(facts or {})
    for key in ("dashboard_url", "hint"):
        values.setdefault(key, str((cfg or {}).get(key, "")))
    values.setdefault("ap_name", ap_ssid())
    # The hotspot key is a FACT (network config), never a panel setting --
    # see ap_password(). _facts() carries it; a bare fill() reads it live.
    values.setdefault("ap_password", ap_password())
    try:
        return str(template).format_map(values)
    except (ValueError, IndexError, AttributeError, TypeError):
        return str(template)


def ap_password(config_dir=None):
    """The hotspot key the box actually broadcasts: network [ap].password.

    Read LIVE (config.load re-reads when the file changes), and the panel has
    no copy of its own. It used to: config/panel.toml shipped
    `ap_password = "iriv1234"` and the panel preferred it, so a box whose
    hotspot had been re-keyed on the Network page kept printing the factory
    key on the OLED -- the one place a person standing at the box reads it
    from (Yaya's board check of .21, Pipi's follow-up 2a). One source, the
    network config, and the screen follows a re-key without a hub restart.

    No fallback of its own: an empty key prints empty rather than a literal
    the radio may not be using. The hub stores the default (iriv1234) at boot
    when there is no key, netconf.ensure_ap_password, so in practice the
    screen shows either that or the key somebody saved.
    """
    ap = config_mod.load("network", config_dir).get("ap") or {}
    return str(ap.get("password") or "")


def ap_ssid(config_dir=None):
    """The access point's NAME, read live like the key: network [ap].ssid
    when the Network page saved one, else the setup name iriv-<serial>.

    The Wi-Fi page used to print hostinfo.ap_name() straight from the serial,
    so a saved name would never have reached the screen at all (Tok Aba's
    Save, 2026-10-06). Same source as apconf.ap_ssid; read here rather than
    imported so the panel keeps no import of the network layer."""
    ap = config_mod.load("network", config_dir).get("ap") or {}
    return str(ap.get("ssid") or "").strip() or hostinfo.ap_name()


wlan_note = ""      # what wifiswitch.py says on the Wi-Fi page while it moves wlan0


def _facts(config_dir=None):
    """hostinfo's snapshot plus the facts that live in config, for render()."""
    ap = config_mod.load("network", config_dir).get("ap") or {}
    return {**hostinfo.snapshot(), "ap_password": ap_password(config_dir),
            "ap_name": ap_ssid(config_dir), "wlan_note": wlan_note,
            "wlan_mode": str(ap.get("mode") or "ap"),
            "ip_conflicts": panel_ipcheck.conflicts()}


def refresh():
    """Redraw the running panel now (a saved AP name or key, a Wi-Fi switch);
    the loop re-reads every tick anyway. Whether a panel is running."""
    panel = _panel
    if panel is None:
        return False
    panel._wake.set()
    return True


def placeholders():
    """What an editor may offer, so the list is defined once."""
    return ("wireless", "total_ram", "total_disk", "ai_hat", "revision",
            "os_bits", "codename", "serial", "ap_name", "ap_password",
            "dashboard_url", "ip_eth0", "ip_eth1", "ip_wlan0",
            "cpu_load", "cpu_temp", "ram", "disk")

#: How often the current page is redrawn. The live facts behind it refresh on
#: their own two-second cadence, so this only decides how soon a change shows.
_TICK = 1.0

#: Hold this long for the panel's one long-press action. Long enough that a
#: pocket cannot do it, short enough to be discoverable.
#:
#: The action is a MASTER PASSWORD RESET, not a shutdown (2026-09-14). An
#: owner locked out of their own box is standing at it; holding a button on
#: it is proof of that. The screen shows a six-digit code (panel_reset) that
#: the Forgot page takes with a new master password -- no email needed,
#: which is the point: the emailed link is the other option, and a box with
#: no address on file still has an owner. Powering off is the dashboard's
#: sign-out menu, where it is confirmed.
HOLD_SECONDS = 5.0

#: The beeps. A short click to acknowledge a press, a longer one to say that
#: something irreversible has begun.
BEEP_PRESS_MS = 25
BEEP_ACTION_MS = 200

_panel = None
_panel_lock = threading.Lock()


def settings(config_dir=None):
    """The panel's configuration, defaults included.

    Kept as one flat table rather than a schema: every value here is a string
    or a bool that somebody may want to change on a customer box, and the
    file is the only place that decision lives.
    """
    cfg = dict(config_mod.load("panel", config_dir).get("panel") or {})
    out = {
        "enabled": bool(cfg.get("enabled", False)),
        "i2c_bus": int(cfg.get("i2c_bus", ssd1306.DEFAULT_BUS)),
        "address": int(cfg.get("address", ssd1306.DEFAULT_ADDRESS)),
        "button": bool(cfg.get("button", True)),
        "buzzer": bool(cfg.get("buzzer", True)),
        # Hold USER five seconds = a master password-reset code on screen.
        # The key keeps its old name so a deployment's saved `false` still
        # turns the long press off.
        "hold_shutdown": bool(cfg.get("hold_shutdown", True)),
        # What page 2 tells somebody standing at the box. Not a guess at the
        # address -- the box has several -- but the port and path they should
        # put after whichever IP page 3 shows them: <this box's own
        # name>.local (hostname.local_name, the name avahi actually won --
        # not a literal "iriv", which on a bench of boxes is somebody
        # else's), on the Software port; no port in proxy mode. Typed wins.
        "dashboard_url": str(cfg.get("dashboard_url")
                             or webaccess.panel_address(hostname.local_name(), config_dir)),
        # No `ap_password` here: the Wi-Fi page prints network [ap].password
        # (ap_password()), and a stored panel copy is ignored on purpose.
        "hint": str(cfg.get("hint", "Press USER to cont...")),
        "pages": [p for p in (cfg.get("pages") or PAGE_IDS)
                  if p in PAGE_IDS] or list(PAGE_IDS),
    }
    # Wording: the defaults with the deployment's overrides on top. Unknown
    # keys are dropped rather than kept -- a line nothing draws is a settings
    # field that silently does nothing.
    text = dict(DEFAULT_TEXT)
    for key, value in (cfg.get("text") or {}).items():
        if key in DEFAULT_TEXT:
            text[key] = str(value)[:MAX_TEXT]
    out["text"] = text
    return out


def save_settings(patch, config_dir=None):
    """Merge a patch into config/panel.local.toml and re-apply it live."""
    local = dict(config_mod.load_local("panel", config_dir).get("panel") or {})
    # Migration: a key saved by the old form is a second copy of the hotspot
    # key that can only disagree with the real one. Dropped on the next save.
    local.pop("ap_password", None)
    for key, value in (patch or {}).items():
        if key not in ("enabled", "i2c_bus", "address", "button", "buzzer",
                       "hold_shutdown", "dashboard_url",
                       "hint", "pages", "text"):
            continue
        if key == "pages":
            value = [p for p in (value or []) if p in PAGE_IDS]
            if not value:
                continue
        if key == "text":
            # Only lines that exist, only as short strings -- and only the
            # ones that DIFFER from the default, so a deployment's override
            # file records decisions rather than freezing today's wording and
            # never following a change to it.
            merged = dict(local.get("text") or {})
            for line, words in (value or {}).items():
                if line not in DEFAULT_TEXT:
                    continue
                words = str(words)[:MAX_TEXT]
                if words == DEFAULT_TEXT[line]:
                    merged.pop(line, None)
                else:
                    merged[line] = words
            value = merged
            if not value:
                local.pop("text", None)
                continue
        local[key] = value
    config_mod.save_local("panel", {"panel": local}, config_dir)
    panel = _panel
    if panel:
        panel.reload(config_dir)
    return settings(config_dir)


# -- drawing ------------------------------------------------------------------

def render(page_id, facts, cfg, canvas=None):
    """Draw one page onto a canvas and return it.

    A pure function of (page, facts, settings): no hardware, no globals. That
    is what lets the browser preview show exactly what the panel shows, and
    what lets a test assert a layout on a machine with no board in it.

    The coordinates are the factory flow's own, kept to the pixel. An
    operator who has seen the old screen should not have to re-learn it.
    """
    canvas = canvas or ssd1306.Canvas()
    canvas.clear(0)
    text = (cfg or {}).get("text") or DEFAULT_TEXT

    def line(key):
        return fill(text.get(key, DEFAULT_TEXT.get(key, "")), facts, cfg)

    if page_id == "about":
        # The right-hand column starts at x=78, so the big left-hand lines
        # have 76px. Eight characters at size 2 is 96 -- which is how
        # "HAILO10H" came to be printed straight through "Trixie". Anything
        # that will not fit is drawn at size 1 rather than over its
        # neighbour: a smaller model name is readable, an overlapped one is
        # not, and truncating the name of the accelerator is worse than both.
        _big(canvas, 1, 1, line("about_title"), 126)
        _big(canvas, 2, 19, line("about_model"), 76)
        _big(canvas, 2, 36, line("about_accel"), 76)
        canvas.text(85, 20, facts.get("revision", ""))
        canvas.text(85, 30, "%s-bit" % facts.get("os_bits", ""))
        canvas.text(78, 40, facts.get("codename", ""))
    elif page_id == "wifi" and (facts.get("wlan_note") or facts.get("wlan_mode") == "wifi"):
        canvas.text(1, 1, line("wifi_title"))  # joined/switching: no AP lines
        canvas.line(1, 9, 100, 9)
        canvas.text(1, 15, facts.get("wlan_note") or "1. Box is on WiFi:")
        canvas.text(1, 29, "wlan0: %s" % (facts.get("ip_wlan0") or "-"))
    elif page_id == "wifi":
        canvas.text(1, 1, line("wifi_title"))
        canvas.line(1, 9, 100, 9)
        canvas.text(1, 15, line("wifi_step"))
        canvas.text(1, 29, (facts or {}).get("ap_name") or ap_ssid())  # saved, live
        canvas.text(1, 40, line("wifi_password"))
    elif page_id == "url":
        canvas.text(1, 1, line("url_title"))
        canvas.line(1, 9, 100, 9)
        canvas.text(1, 18, line("url_step"))
        canvas.text(1, 33, cfg.get("dashboard_url") or "")
    elif page_id == "ip":
        canvas.text(1, 1, line("ip_title"))
        canvas.line(1, 9, 77, 9)
        for y, iface in ((15, "eth0"), (27, "eth1"), (39, "wlan0")):
            canvas.text(1, y, _ip_line(iface, text, facts, cfg))
    elif page_id == "status":
        canvas.text(1, 1, line("status_title"))
        canvas.line(1, 9, 65, 9)
        # "Load: 1.04 Temp: 56.3'C" is 23 characters and the row holds 21, so
        # the factory panel shows the temperature with its last digits cut
        # off -- a number that is not readable at all. The default label
        # loses the letters instead: "56.3'C" says temperature without being
        # told.
        canvas.text(1, 15, _fit(line("status_load")))
        canvas.text(1, 27, _fit(line("status_ram")))
        canvas.text(1, 39, _fit(line("status_disk")))
    elif page_id == "off":
        canvas.text(1, 20, line("off"), size=1)
        return canvas                       # no hint line: nothing to press
    elif page_id == "reset":
        # The code, big, with the two words that say what it is for. Nothing
        # else: this screen is read across a room.
        canvas.text(1, 1, line("reset_title"))
        canvas.line(1, 9, 100, 9)
        code = str((facts or {}).get("_reset_code") or "")
        canvas.text(22, 18, code[:3] + " " + code[3:], size=2)   # "123 456", 84px
        canvas.text(1, 44, line("reset_hint"))
        canvas.text(1, 56, "Valid 10 min")
        return canvas

    # The standing footer, drawn last so a long value cannot overwrite it.
    canvas.line(1, 52, 127, 52)
    canvas.text(3, 56, cfg.get("hint") or "")
    return canvas


#: What a port's line says when another device answers for its static
#: address (panel_ipcheck). Fixed wording, not a panel setting: "eth0: IP
#: CONFLICT" is 17 characters and "wlan0: IP CONFLICT" 18, so it fits the
#: 21-character row whatever the port, and it REPLACES the address -- the
#: row has no room for both, and the address is on the Network page.
CONFLICT_TEXT = "IP CONFLICT"


def _ip_line(iface, text, facts, cfg):
    """One port's row on the IP screen.

    A conflict wins over the template. No address prints "-" (the Wi-Fi
    page's convention) rather than a bare "eth0: " that reads like a
    drawing fault; an unplugged jack is the usual reason.
    """
    facts = facts or {}
    if iface in (facts.get("ip_conflicts") or ()):
        return _fit("%s: %s" % (iface, CONFLICT_TEXT))
    key = "ip_" + iface
    if not facts.get(key):
        facts = {**facts, key: "-"}
    return fill(text.get(key, DEFAULT_TEXT.get(key, "")), facts, cfg)


def _fit(text, columns=21):
    """Trim to what a 5x7 row holds on a 128px screen."""
    return text if len(text) <= columns else text[:columns]


def _big(canvas, x, y, text, room):
    """Draw at size 2, dropping to size 1 when it will not fit in `room`.

    The identity page puts three large lines beside a narrow column of small
    ones, and a value nobody sized for -- "HAILO10H", eight characters at
    twelve pixels each -- ran straight through the column beside it. Shrinking
    is the only option that keeps the whole value on the screen: truncating
    the name of the accelerator, or letting two lines overlap, both destroy
    the fact the line exists to state.
    """
    size = 2 if ssd1306.text_width(text, 2) <= room else 1
    # A vertical nudge, so a shrunk line sits on the same optical row as the
    # size-2 one it replaced rather than floating at the top of its band.
    canvas.text(x, y + (0 if size == 2 else 3), text, size=size)


# -- the running panel --------------------------------------------------------

def _claim_error(pin, exc):
    """Say WHO has the line, not just that the claim failed.

    A GPIO line has exactly one owner, and on this board the other claimant
    is almost always the factory Node-RED flow -- which is why the screen can
    work (I2C has no such lock) while the button does nothing at all. "GPIO
    busy" with no name attached is a support call; naming the consumer is the
    whole answer.
    """
    text = str(exc)
    if "busy" not in text.lower() and "in use" not in text.lower():
        return text
    consumer = (board_io.gpio_lines().get(pin) or {}).get("consumer") or ""
    # libgpiod reports EVERY lgpio client as the bare string "lg", so the
    # consumer field alone cannot tell our own app from the factory flow --
    # and "held by lg" is not an answer anybody can act on. On this board the
    # other lgpio client is Node-RED, so when it is running, say so.
    if consumer in ("", "lg", "?"):
        if nodered_state()["active"]:
            return ("BCM %d is held by Node-RED - stop it on the Board I/O "
                    "page to use the button and buzzer" % pin)
        return "BCM %d is held by another program" % pin
    return 'BCM %d is held by "%s"' % (pin, consumer)


class Panel:
    """Owns the screen, the button and the buzzer for the life of the hub."""

    def __init__(self, config_dir=None):
        self.config_dir = config_dir
        self.cfg = settings(config_dir)
        self.display = None
        self.canvas = ssd1306.Canvas()
        self.page = 0
        self.state = "running"          # running | shutdown | reboot
        self.notice = None              # ("reset", code) while the code is on screen
        self.notice_until = 0.0
        self.error = ""
        # Per-device, not one shared `error`: the screen, the button and the
        # buzzer fail independently and for different reasons, and a page
        # that reports only the first of them sends somebody looking in the
        # wrong place.
        self.button_error = ""
        self.buzzer_error = ""
        self.started_at = time.time()
        self._button = None
        self._buzzer = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        if self._thread:
            return self
        self._open_display()
        self._open_gpio()
        self._thread = threading.Thread(target=self._loop, name="panel",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._button is not None:
            try:
                self._button.close()
            except Exception:                                # noqa: BLE001
                pass
            self._button = None
        if self._buzzer is not None:
            try:
                self._buzzer.close()
            except Exception:                                # noqa: BLE001
                pass
            self._buzzer = None
        if self.display:
            self.display.close()

    def reload(self, config_dir=None):
        """Pick up an edited configuration without a restart."""
        self.cfg = settings(config_dir or self.config_dir)
        if self.page >= len(self.cfg["pages"]):
            self.page = 0
        self._wake.set()

    def _open_display(self):
        display = ssd1306.Display(bus=self.cfg["i2c_bus"],
                                  address=self.cfg["address"])
        if display.open():
            self.display = display
            self.error = ""
        else:
            self.display = None
            self.error = display.error

    def _open_gpio(self):
        """Claim the button and the buzzer, or carry on without them.

        Both are marked as ours in board_io so the Board I/O page does not
        report the terminals this very process is holding as held by a
        stranger.
        """
        self.button_error = ""
        self.buzzer_error = ""
        if not board_io.detected():
            self.button_error = self.buzzer_error = "no IRIV board on this box"
            return
        try:
            from gpiozero import Button, DigitalOutputDevice  # noqa: PLC0415
        except ImportError:
            self.button_error = self.buzzer_error = "gpiozero not installed"
            return
        # The BUZZER is claimed per beep, not for the life of the hub. It is
        # a shared instrument: a workflow's Buzzer output drives the same pin,
        # and holding it here would mean whichever of the two started first
        # silenced the other for good. A beep is 25 milliseconds -- the window
        # in which the two can collide is worth less than the alarm a rule is
        # trying to sound.
        self._buzzer = None
        if self.cfg["button"]:
            pin = board_io.PORT_BY_ID["BUTTON"]["pin"]
            try:
                # Active LOW, as the flow's `payload == 0` test says. The
                # 50ms debounce is what stops one press from walking three
                # pages on a mechanical contact.
                self._button = Button(pin, pull_up=True, bounce_time=0.05,
                                      hold_time=HOLD_SECONDS)
                self._button.when_pressed = self._on_press
                if self.cfg["hold_shutdown"]:
                    self._button.when_held = self._on_hold
                board_io._CLAIMED.add(pin)
            except Exception as exc:                          # noqa: BLE001
                self.button_error = _claim_error(pin, exc)

    # -- inputs -------------------------------------------------------------
    def _on_press(self):
        self.beep(BEEP_PRESS_MS)
        # A press while the code shows dismisses it (the code stays valid);
        # the next press steps the pages as usual.
        if self.notice:
            self.clear_notice()
            return
        self.next_page()

    def _on_hold(self):
        # Held the full five seconds. Releasing earlier never reaches here --
        # gpiozero starts the timer on press and cancels it on release, which
        # is the trigger node's `reset` in one line.
        self.request_master_reset()

    # -- master password reset ------------------------------------------------
    def request_master_reset(self):
        """Put a reset code on the screen for ten minutes.

        The code is minted by panel_reset and written to the shared config
        directory, so the Forgot page on any port can take it. Audited and
        posted to the admins' Inbox: a stranger holding the button is on
        record, and master learns about it whether or not it was them.
        Holding again mints a new code and restarts the clock.
        """
        with self._lock:
            if self.state != "running":
                return False
        from core.services import audit as audit_service     # noqa: PLC0415
        from core.services import inbox as inbox_service     # noqa: PLC0415
        from core.services import panel_reset                # noqa: PLC0415
        code = panel_reset.begin(self.config_dir)
        with self._lock:
            self.notice = ("reset", code)
            self.notice_until = time.monotonic() + panel_reset.VALID_SECONDS
        self.beep(BEEP_ACTION_MS)
        self._wake.set()
        try:
            audit_service.record("password_reset_requested", "master",
                                 source_ip="front panel",
                                 detail="USER button held 5s - reset code shown",
                                 config_dir=self.config_dir)
            inbox_service.notify_admins(
                "reset_request", "Master password reset started at the box",
                "Somebody held the USER button on the front panel for five "
                "seconds. A reset code is on the screen for ten minutes; typed "
                "into the Forgot page it sets a new master password. If nobody "
                "you know is at the box, check who is.", self.config_dir,
                page="settings", tab="accounts")
        except Exception:                                     # noqa: BLE001
            pass
        return True

    def clear_notice(self):
        """Back to the pages -- the code was used, or the operator pressed."""
        with self._lock:
            self.notice = None
        self._wake.set()

    def beep(self, ms):
        """Sound the buzzer. Non-blocking, and momentary at the pin.

        Claimed and released around each beep so a workflow driving the same
        buzzer keeps working -- see _open_gpio. A beep that cannot get the
        line is recorded and dropped: a missed acknowledgement tone is not
        worth waiting on a pin for.
        """
        if not self.cfg.get("buzzer") or not ms:
            return

        def _run():
            pin = board_io.PORT_BY_ID["BUZZER"]["pin"]
            try:
                from gpiozero import DigitalOutputDevice      # noqa: PLC0415
            except ImportError:
                self.buzzer_error = "gpiozero not installed"
                return
            try:
                device = DigitalOutputDevice(pin, initial_value=True)
            except Exception as exc:                          # noqa: BLE001
                self.buzzer_error = _claim_error(pin, exc)
                return
            self.buzzer_error = ""
            try:
                time.sleep(max(1, int(ms)) / 1000.0)
            finally:
                try:
                    device.off()
                    device.close()
                except Exception:                             # noqa: BLE001
                    pass
        threading.Thread(target=_run, daemon=True).start()

    # -- pages --------------------------------------------------------------
    def pages(self):
        return self.cfg["pages"] or list(PAGE_IDS)

    def page_id(self):
        pages = self.pages()
        return pages[self.page % len(pages)]

    def set_page(self, index):
        pages = self.pages()
        self.page = int(index) % len(pages)
        self._wake.set()
        return self.page_id()

    def next_page(self):
        return self.set_page(self.page + 1)

    # -- power --------------------------------------------------------------
    def shutdown(self):
        return self._power("shutdown", ["sudo", "shutdown", "now"])

    def reboot(self):
        return self._power("reboot", ["sudo", "reboot", "now"])

    def _power(self, state, argv):
        """Beep, say so on the screen, then hand off to systemd.

        The quarter-second between the message and the command is the flow's
        own delay and it earns its place: it is the difference between a
        panel that goes dark having said what happened and one that simply
        dies mid-sentence.
        """
        with self._lock:
            if self.state != "running":
                return False
            self.state = state
        self.beep(BEEP_ACTION_MS)
        try:
            self._draw("off")
        except Exception:                                     # noqa: BLE001
            pass
        time.sleep(0.25)
        # Software off FIRST (services/software_off.py): left running, the
        # apps and masters are what the OS waits up to 90 s for at power-off.
        try:
            from core.services import software_off           # noqa: PLC0415
            software_off.turn_off()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.state = "running"
            self.error = "%s: %s" % (" ".join(argv), exc)
            return False
        return True

    # -- the loop -----------------------------------------------------------
    def _draw(self, page_id=None):
        facts = _facts(self.config_dir)
        # The reset code takes the page over until it expires or is pressed
        # away. The code itself stays valid either way (panel_reset owns
        # that); this is only about what the screen shows.
        if page_id is None and self.notice:
            if time.monotonic() >= self.notice_until:
                with self._lock:
                    self.notice = None
            else:
                page_id = self.notice[0]
                facts = {**facts, "_reset_code": self.notice[1]}
        page_id = page_id or self.page_id()
        if page_id == "ip":
            # A static port's address taken by another device (product
            # owner, 2026-10-11). Only while this page is up, at most every
            # 45 s, off this thread; the answer shows on a later tick.
            panel_ipcheck.poke(refresh)
        render(page_id, facts, self.cfg, self.canvas)
        if self.display is None:
            return False
        # Nothing new to show, nothing to send. Four of the five pages change
        # only when a fact behind them does, and pushing an identical kilobyte
        # down the bus every second is work with no observable effect.
        if self.display.canvas.buf == self.canvas.buf:
            return True
        self.display.canvas.buf[:] = self.canvas.buf
        return self.display.flush()

    def _loop(self):
        misses = 0
        while not self._stop.is_set():
            try:
                if self.state == "running":
                    if not self._draw() and self.display is None:
                        # The screen went away mid-run (unplugged, or the bus
                        # wedged). Retry, slowly: a panel that reopens an
                        # absent device every second is a log full of noise.
                        misses += 1
                        if misses % 30 == 0:
                            self._open_display()
                    else:
                        misses = 0
            except Exception as exc:                          # noqa: BLE001
                self.error = str(exc)
            self._wake.wait(_TICK)
            self._wake.clear()

    # -- what the UI reads --------------------------------------------------
    def frame(self):
        """The current screen as scanlines, plus what produced it."""
        return {
            "rows": self.canvas.rows(),
            "width": self.canvas.width,
            "height": self.canvas.height,
            "page": self.page_id(),
            "index": self.page % max(1, len(self.pages())),
        }


def preview(page_id, config_dir=None, overrides=None):
    """Render a page WITHOUT touching the hardware, for the editor.

    `overrides` are unsaved settings from the form, so the preview answers
    "what would this look like" rather than "what does it look like now" --
    which is the only reason to have a preview beside an editor at all.
    """
    cfg = settings(config_dir)
    for key, value in (overrides or {}).items():
        if key not in cfg:
            continue
        # `text` MERGES. A caller sending one edited line must not blank the
        # sixteen it did not mention.
        if key == "text" and isinstance(value, dict):
            cfg["text"] = {**cfg["text"],
                           **{k: str(v)[:MAX_TEXT] for k, v in value.items()
                              if k in DEFAULT_TEXT}}
        else:
            cfg[key] = value
    canvas = render(page_id if page_id in PAGE_IDS + ("off",) else "about",
                    _facts(config_dir), cfg)
    return {"rows": canvas.rows(), "width": canvas.width,
            "height": canvas.height, "page": page_id}


# -- module-level entry points ------------------------------------------------

def start(config_dir=None):
    """Bring the panel up if it is switched on. Safe to call anywhere."""
    global _panel
    with _panel_lock:
        if _panel is not None:
            return _panel
        cfg = settings(config_dir)
        if not cfg["enabled"]:
            return None
        _panel = Panel(config_dir).start()
        return _panel


def stop():
    global _panel
    with _panel_lock:
        if _panel is not None:
            _panel.stop()
            _panel = None


def current():
    return _panel


def nodered_running():
    """Whether the factory flow -- the other owner of this screen -- is up.

    Reported, never acted on. Turning off somebody else's software because
    it is in our way is not a decision a status endpoint gets to make.
    """
    try:
        out = subprocess.run(["systemctl", "is-active", "nodered"],
                             capture_output=True, text=True, timeout=5)
        return (out.stdout or "").strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def nodered_state():
    """{active, enabled, installed} for the factory flow's unit."""
    def ask(verb):
        try:
            out = subprocess.run(["systemctl", verb, "nodered"],
                                 capture_output=True, text=True, timeout=5)
            return (out.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    active = ask("is-active")
    enabled = ask("is-enabled")
    return {
        "active": active == "active",
        # "enabled" is the boot decision and "active" is right now. Both,
        # because stopping a unit that is still enabled means it comes back
        # at the next power cycle and takes the screen with it.
        "enabled": enabled == "enabled",
        "installed": bool(active or enabled),
        "state": active or "unknown",
    }


#: Recorded when WE switched the front panel off to hand Node-RED the board.
#: Only then is it switched back on afterwards: turning on something an
#: operator deliberately turned off would be worse than leaving it off.
NR_TOOK_PANEL = "off_for_nodered"


def _took_panel(config_dir=None, value=None):
    """Read, or write, the note that we own the panel being off."""
    local = dict(config_mod.load_local("panel", config_dir).get("panel") or {})
    if value is None:
        return bool(local.get(NR_TOOK_PANEL))
    if value:
        local[NR_TOOK_PANEL] = True
    else:
        local.pop(NR_TOOK_PANEL, None)
    config_mod.save_local("panel", {"panel": local}, config_dir)
    return bool(value)


def set_nodered(on, config_dir=None):
    """Start/enable or stop/disable the factory flow. (ok, message).

    Both halves together on purpose: a flow stopped but left enabled is the
    worst of the three states -- the screen works until the next reboot, and
    then does not, with nothing in this session to blame.

    This is the ONE place the product touches somebody else's service, and it
    does it only because the two cannot share the hardware and the operator
    has to be able to choose from the page where the conflict is visible.

    AND THE FRONT PANEL GOES WITH IT. Node-RED's factory flow drives this same
    OLED, this same user button and this same buzzer. Leaving our panel
    running would put two programs on one screen, which is how it used to
    flicker between two sets of pixels -- and the button and buzzer would
    simply be claimed by whichever started first.

    So the panel is stopped BEFORE Node-RED starts, which releases the lines
    in this process, and started again after Node-RED stops. No reboot and no
    service restart: the panel is ours, in this process, and letting go is
    immediate. A reboot would work too and would take the dashboard down with
    it for a minute, to achieve the same thing.
    """
    freed = ""
    if on and settings(config_dir)["enabled"]:
        # Before, not after: the lines have to be free when Node-RED claims
        # them, or it starts without the screen and says nothing about why.
        save_settings({"enabled": False}, config_dir)
        stop()
        _took_panel(config_dir, True)
        freed = " The front panel was switched off; it drives the same screen, button and buzzer."

    verb = ["enable", "--now"] if on else ["disable", "--now"]
    try:
        proc = subprocess.run(["sudo", "-n", "systemctl"] + verb + ["nodered"],
                              capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "systemctl refused").strip()

    restored = ""
    if not on and _took_panel(config_dir):
        # After, not before: Node-RED holds the lines until it is down.
        save_settings({"enabled": True}, config_dir)
        _took_panel(config_dir, False)
        start(config_dir)
        restored = " The front panel is back on."

    return True, (("Node-RED started and will run at boot." + freed) if on
                  else ("Node-RED stopped and will not run at boot." + restored))


def status(config_dir=None):
    """Everything the settings page needs in one read."""
    cfg = settings(config_dir)
    panel = _panel
    out = {
        "settings": cfg,
        "page_ids": list(PAGE_IDS),
        "detected": board_io.detected(),
        "running": bool(panel),
        "screen": bool(panel and panel.display),
        "error": panel.error if panel else "",
        "button": bool(panel and panel._button),
        # The buzzer is claimed per beep, so there is no handle to report.
        # "ready" here means the last beep was not refused.
        "buzzer": bool(panel and panel.cfg.get("buzzer")
                       and not panel.buzzer_error),
        "button_error": panel.button_error if panel else "",
        "buzzer_error": panel.buzzer_error if panel else "",
        "text_by_page": {k: list(v) for k, v in TEXT_BY_PAGE.items()},
        "placeholders": list(placeholders()),
        "default_text": dict(DEFAULT_TEXT),
        "nodered": nodered_state(),
        "facts": _facts(config_dir),
        "ap_name": ap_ssid(config_dir),
    }
    out["frame"] = panel.frame() if panel else preview(
        cfg["pages"][0] if cfg["pages"] else "about", config_dir)
    return out
