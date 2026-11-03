# -*- coding: utf-8 -*-
"""
wifiscan.py -- what the root Wi-Fi helper can do on THIS box, a fresh scan
through it, and the trial join both connection-test buttons use.

WHY A SCAN NEEDS ROOT. NetworkManager's wifi.scan permission is auth_admin
for a caller with no login session, which is what the hub's service account
is (checked on board .21: polkit default allow_any=auth_admin, no rule from
the installer). Refused, `nmcli device wifi list --rescan yes` does not fail:
it prints the list it already had and exits 0. The Network page's Scan
button showed networks from whenever NetworkManager last scanned by itself --
in AP mode, from before the hotspot came up. The helper (scripts/
wifi_profile.py, kind "scan") asks as root instead. NetworkManager 1.52 lets
an explicit scan run while wlan0 is the access point (only its periodic
scans stop), the brcmfmac driver and cfg80211 do not refuse it, and
wpa_supplicant files the results; so "Search Wi-Fi" works with the hotspot
up and its clients on it.

WHY NOT A TEST BESIDE THE ACCESS POINT. The CM5's radio (brcmfmac, iw list
on .21) allows one AP and one station at once only on ONE channel, and the
site's network is almost never on the hotspot's channel. Field reports for
this chip: the AP follows the station to its channel (its clients drop), or
the station never associates (a good password reported bad). So the test
is a real join on wlan0 -- the access point is off while it runs -- with
the trial's limits (wifi_profile.py header) so it answers in time.

AN OLDER HELPER. A box updated over the air before its root step ran has
the old helper, which refuses both kinds. helper_can() reads the installed
copy's KINDS line (it is world-readable) BEFORE anything moves, and the
page offers "Switch without testing" (the old join) instead.
"""

import json
import subprocess
import uuid

from core.services import netconf
from core.services.netaddr import NetError

#: The hub's own limit on one helper run, ABOVE the longest run the helper
#: can make: its lock wait (LOCK_WAIT_S 90) plus a trial's own steps at their
#: limits (load 30, up --wait 25 + 15, address 20, journal 20, three pings 18,
#: load or put back 60, deleting the old profiles) -- about 280 s. Killing
#: sudo does not stop the root helper under it, so a limit below that left a
#: helper running on, able to keep a late join the page had called failed
#: (Pipi F3). This only stops a hung helper; a normal trial answers in 10-40
#: s. A killed helper's leftovers are swept by the next run (sweep).
HELPER_TIMEOUT_S = 300
#: The uuid the helper gives a test-only trial (wifi_profile.py NAMESPACE and
#: TEST_FILE; tests/smoke_wifitrial.py holds the two equal).
TEST_UUID = str(uuid.uuid5(uuid.UUID("5f0a1b7e-3c2d-4e8f-9a6b-1c2d3e4f5a6b"), "irivai-trial"))


def helper_can(kind):
    """Whether the INSTALLED helper does `kind` ("trial", "scan")."""
    try:
        with open(netconf.WIFI_HELPER, encoding="utf-8", errors="replace") as fh:
            text = fh.read(64 * 1024)
    except OSError:
        return False
    for line in text.splitlines():
        if line.startswith("KINDS = "):
            return ('"%s"' % kind) in line
    return False


def _helper(request, timeout=HELPER_TIMEOUT_S):
    """The helper's answer as a dict, ok or not (a refused trial is an
    answer, with its reason). NetError only when there is no answer at
    all. The request (it may hold a key) is never in a message."""
    try:
        proc = subprocess.run(list(netconf.WIFI_HELPER_ARGV), input=json.dumps(request),
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise NetError("sudo is not available on this box")
    except subprocess.TimeoutExpired:
        raise NetError("the Wi-Fi helper did not answer in time")
    except subprocess.SubprocessError as exc:
        raise NetError(exc.__class__.__name__)
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if isinstance(answer, dict) and answer:
            return answer
    raise NetError("the Wi-Fi helper gave no answer")


def _shape(rows):
    """One row per SSID, strongest first, in netconf.wifi_list's shape plus
    the channel: what a person picks is a NAME, not a radio."""
    best = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ssid = str(row.get("ssid") or "")
        if not ssid:
            continue                     # hidden: typed by hand instead
        try:
            signal = int(row.get("signal") or 0)
        except (TypeError, ValueError):
            signal = 0
        security = str(row.get("security") or "").strip()
        security = "" if security == "--" else security
        entry = {"ssid": ssid, "signal": signal, "secure": bool(security),
                 "security": security, "active": bool(row.get("in_use")),
                 "chan": str(row.get("chan") or ""),
                 # WPA-Enterprise wants a user name and a certificate the
                 # join path has no way to give.
                 "enterprise": "802.1x" in security.lower()}
        if ssid not in best or signal > best[ssid]["signal"]:
            best[ssid] = entry
    return sorted(best.values(), key=lambda n: -n["signal"])


def search():
    """(networks, fresh): a scan made now through the helper (fresh), or the
    list NetworkManager already had when the helper cannot scan (not fresh,
    and the page says so). Refused while wlan0 is switching."""
    from core.services import wifiswitch as ws                # noqa: PLC0415
    # One Wi-Fi job at a time: a switch or a test does not start while this
    # scan runs (wifiswitch.searching), and this does not start during one.
    with ws._lock:
        if ws._state.get("state") == "switching" or ws.searching:
            raise NetError("the Wi-Fi is busy (a switch, a test or another search) - "
                           "search again when it is done")
        ws.searching = True
    try:
        if netconf.wifi_helper_installed() and helper_can("scan"):
            answer = _helper({"kind": "scan"})
            if answer.get("ok"):
                return _shape(answer.get("networks")), bool(answer.get("fresh"))
        return netconf.wifi_list(rescan=False), False
    finally:
        with ws._lock:
            ws.searching = False


def trial(ssid, password, hidden, keep):
    """One trial join on wlan0 (the helper does it, key on stdin): the
    helper's answer -- {ok, address, gateway, internet} or {ok: false,
    reason, message}. A helper failure is answered as reason "failed"."""
    try:
        return _helper({"kind": "trial", "ssid": ssid, "psk": password or "",
                        "hidden": bool(hidden), "keep": bool(keep)})
    except NetError as exc:
        return {"ok": False, "reason": "failed", "message": str(exc)}


def leftover():
    """Whether a helper run was cut short mid-trial (the hub's time limit, a
    power cut): the test profile still known to NetworkManager, or a Wi-Fi
    profile still in a trial's strict form (auth-retries 1, which nothing
    else on the box writes). Read as the service account; no secrets."""
    try:
        out = netconf._nmcli(["-t", "-f", "UUID,TYPE", "connection", "show"], timeout=15)
    except NetError:
        return False
    for line in out.splitlines():
        parts = netconf._split(line)
        if len(parts) < 2:
            continue
        if parts[0] == TEST_UUID:
            return True
        if parts[1] == "802-11-wireless":
            try:
                tries = netconf._nmcli(["-g", "connection.auth-retries", "connection",
                                        "show", "uuid", parts[0]], timeout=15)
            except NetError:
                continue
            if tries.strip() == "1":
                return True
    return False


def sweep_at_start(log=None):
    """At hub start: one helper sweep when a trial leftover is seen, so its
    key does not wait at rest for the next Wi-Fi change (Pipi, 2026-10-08).
    The helper's lock keeps it from racing the boot's own AP step."""
    if not (netconf.wifi_helper_installed() and helper_can("sweep")) or not leftover():
        return []
    try:
        answer = _helper({"kind": "sweep"})
    except NetError as exc:
        if log:
            log(f"[NET] a Wi-Fi trial leftover could not be cleared: {exc}")
        return []
    swept = list(answer.get("swept") or [])
    if log:
        log("[NET] a Wi-Fi trial cut short was cleared: " + (", ".join(swept) or "nothing left"))
    return swept
