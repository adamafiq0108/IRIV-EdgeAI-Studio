# -*- coding: utf-8 -*-
"""
apconf.py -- the access point's name and password, as the operator saves them.

Settings -> Network -> Access-point mode -> Save (2026-10-06, Tok Aba). Until
then a box that was ALREADY an access point offered only "Turn off AP mode":
no way to store a changed key, and the name was fixed to iriv-<serial>. Its
own module because netconf.py is at its line ceiling (the CI ratchet), and
because this is a different job: netconf drives NetworkManager, this decides
what the AP is CALLED and KEYED and when the radio hears about it.

APPLY-NOW, NOT AT-REBOOT -- like the mode switch (AP <-> Wi-Fi), which is
live too since 2026-10-07 (wifiswitch.py). Staging a new name or key would
leave the OLED and the Network page printing a key the radio is not
broadcasting until somebody reboots -- the one mismatch the panel exists to
prevent: a person at the box types what the screen says and cannot join.
So a save while broadcasting restarts the AP on the new values (the route asks
first and says who drops off), and a save with the AP off only stores them for
the next time it is up. The boot re-asserts the stored values either way
(netconf.apply_wifi_mode), so a restart that fails is put right by the next
reboot, and Ethernet never moves.

The key lives where it always has: network.local.toml [ap].password, 0600
(config.save_local), and reaches the radio through netconf.ap_enable -- the
root helper with the key on stdin, or the legacy hotspot command on a box
that has not had its helper installed yet. No new root step.

WHO CHOSE THE KEY -- [ap].user_set (Tok Aba, 2026-10-07: the key is iriv1234
unless a user set another). True once a person stores a key other than the
default through save_ap (the page's Save, a Reset Wi-Fi with a typed key,
scripts/dev/harden_box.sh); dropped again whenever the default is stored
(Reset Wi-Fi). Absent on a box that predates it, whatever its key. The one
reader that acts on its absence is apdefault.migrate: once per box, a key
with the 2026-10-05..07 generator's exact shape and no flag goes back to
iriv1234 (the generator wrote no marker, so the shape is all there is).
"""

from core.services import netconf
from core.services.netaddr import NetError

#: WPA2's ceiling for a passphrase (64 characters is read as a raw hex key,
#: which nobody types off a screen). Printable ASCII only: WPA2 hashes the
#: BYTES, and a phone's keyboard and the box can disagree about the bytes of
#: an accented letter. The floor is netconf.MIN_AP_PASSWORD.
MAX_AP_PASSWORD = 63
#: 802.11 counts an SSID in BYTES, not characters: 32 of them, so a name with
#: accents or emoji runs out sooner than its length on the page suggests.
MAX_AP_SSID_BYTES = 32


def check_ap_ssid(ssid):
    """The name, trimmed, or NetError in words the page can show as they are."""
    ssid = str(ssid if ssid is not None else "").strip()
    if not ssid:
        raise NetError("The network name cannot be empty.")
    if any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise NetError("The network name cannot contain tabs or other control "
                       "characters.")
    if len(ssid.encode("utf-8")) > MAX_AP_SSID_BYTES:
        raise NetError("The network name is too long. Wi-Fi allows %d bytes; "
                       "accented letters and emoji take more than one each."
                       % MAX_AP_SSID_BYTES)
    return ssid


def check_ap_password(password):
    """WPA2 passphrase rules, in words the page can show as they are."""
    password = str(password if password is not None else "")
    if len(password) < netconf.MIN_AP_PASSWORD:
        raise NetError("The password needs at least %d characters."
                       % netconf.MIN_AP_PASSWORD)
    if len(password) > MAX_AP_PASSWORD:
        raise NetError("The password can have at most %d characters." % MAX_AP_PASSWORD)
    if not all(32 <= ord(c) <= 126 for c in password):
        raise NetError("The password can use letters, digits, spaces and keyboard "
                       "symbols only (no accented letters or emoji).")
    return password


def ap_factory_name():
    """The setup name, "iriv-<serial>": what Reset Wi-Fi brings back."""
    from core.services import hostinfo                        # noqa: PLC0415
    return hostinfo.ap_name()


def ap_ssid(config_dir=None):
    """The name this box broadcasts: network [ap].ssid, else the setup name.

    The ONE reader, like netconf.ap_password(): the page and the boot ask
    here (the panel reads the same field, panel.ap_ssid), so a saved name
    cannot be on one and not the others. Not stored when it IS the setup
    name, so a box whose serial is read differently later still follows it.
    """
    from core import config                                   # noqa: PLC0415
    ap = config.load("network", config_dir).get("ap") or {}
    return str(ap.get("ssid") or "").strip() or ap_factory_name()


def ap_user_set(config_dir=None):
    """Whether a person stored this box's AP key ([ap].user_set)."""
    from core import config                                   # noqa: PLC0415
    return bool((config.load("network", config_dir).get("ap") or {}).get("user_set"))


def save_ap(ssid, password=None, config_dir=None):
    """Store the access point's name and key. Applies NOTHING to the radio.

    `password` None keeps the stored key. Both are validated before anything
    is written. The default key (netconf.DEFAULT_AP_PASSWORD) is a valid
    choice like any other: Reset Wi-Fi saves it, and an operator may type it
    back (Tok Aba, 2026-10-07). A new key sets or clears [ap].user_set
    (module docstring).

    Returns {"changed": bool, "ssid": str} -- never the key. Written to
    network.local.toml, 0600 (config.save_local), where the key already
    lived; the front panel is woken so the screen shows the new values at
    once rather than at its next tick.
    """
    from core import config                                   # noqa: PLC0415
    ssid = check_ap_ssid(ssid)
    stored_key = netconf.ap_password(config_dir)
    if password is None:
        password = stored_key
    elif password != stored_key:
        check_ap_password(password)
    if not password:
        raise NetError("The password needs at least %d characters."
                       % netconf.MIN_AP_PASSWORD)
    changed = ssid != ap_ssid(config_dir) or password != stored_key
    if changed:
        local = dict(config.load_local("network", config_dir))
        ap = dict(local.get("ap") or {})
        if ssid == ap_factory_name():
            ap.pop("ssid", None)
        else:
            ap["ssid"] = ssid
        ap["password"] = password
        # The flag follows the KEY only: a rename leaves it as it was, the
        # default clears it, any other new key is a person's choice.
        if password == netconf.DEFAULT_AP_PASSWORD:
            ap.pop("user_set", None)
        elif password != stored_key:
            ap["user_set"] = True
        local["ap"] = ap
        config.save_local("network", local, config_dir)
        _refresh_panel()
    return {"changed": changed, "ssid": ssid}


def _refresh_panel():
    """Redraw the OLED now. The panel reads the name and key live, but only
    on its next tick, and a person reading the screen while somebody saves
    should not wait for it. No panel running is not an error."""
    try:
        from core.services import panel                       # noqa: PLC0415
        panel.refresh()
    except Exception:                                         # noqa: BLE001
        pass


def apply_ap(config_dir=None):
    """Restart the broadcasting AP on the STORED name and key.

    Through netconf.ap_enable, so the helper (key on stdin, 0600 keyfile,
    `connection up` on the one irivai-ap profile) on a box that has it, and
    the legacy hotspot command on one that does not. This restarts what is
    saved; it does not choose a key.
    """
    return netconf.ap_enable(ap_ssid(config_dir), netconf.ap_password(config_dir))
