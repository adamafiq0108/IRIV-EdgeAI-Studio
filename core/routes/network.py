# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
network.py -- joining Wi-Fi, raising an access point, addressing an interface.

ADMIN ONLY, all of it including the read: nearby SSIDs are a survey of where
the box physically is, and every write here can take the box off the network
it is being configured over.

Each write returns the FULL status rather than an acknowledgement. A network
change is exactly the moment the browser's own idea of the box is least
trustworthy -- and if the reply arrives at all, it should be the new truth.
"""

import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from core.services import apconf
from core.services import auth as auth_service
from core.services import hostinfo, hostname, hubport
from core.services import netconf, uplinkguard, wifiscan, wifiswitch

router = APIRouter()


def _require_admin(request):
    # Site admin and above: how the box reaches the network is part of running
    # the operation on it (an admin whose alerts cannot leave the box has an
    # operation that does not work), while what the HARDWARE does -- the
    # terminals, the front panel -- stays with the box owner.
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


def _via(request, payload):
    """Mark the interface this request arrived on.

    The one fact the server knows and the browser cannot: which cable or
    radio is carrying this very conversation. The page uses it to warn before
    someone reconfigures the interface they are sitting on.
    """
    client = getattr(request, "client", None)
    peer = getattr(client, "host", "") if client else ""
    payload["client_ip"] = peer
    payload["via"] = ""
    for device in payload.get("devices") or []:
        # Same /24 as the caller is a heuristic, and deliberately a soft one:
        # it drives a warning, never a refusal.
        ip = device.get("ip") or ""
        if ip and peer and ip.rsplit(".", 1)[0] == peer.rsplit(".", 1)[0]:
            payload["via"] = device["device"]
            break
    return payload


def _defaults(payload):
    """The access point this box would raise: named from its serial, with
    the key network [ap].password holds (the one the panel prints; iriv1234
    unless somebody changed it). Admin-only payload, and the same characters
    are on the OLED."""
    # ap_name is what the box BROADCASTS (a saved name, else the setup one);
    # ap_name_factory is what Reset Wi-Fi brings back.
    payload["ap_name"] = apconf.ap_ssid()
    payload["ap_name_factory"] = hostinfo.ap_name()
    payload["ap_password"] = netconf.ap_password()
    # The hotspot's address: the one set, the one it really takes, and the
    # cable whose network blocks it (netconf.ap_address_plan).
    try:
        plan = netconf.ap_address_plan()
    except Exception:                                         # noqa: BLE001
        plan = {"address": netconf.ap_address(), "wanted": netconf.ap_address(),
                "conflict": ""}
    payload["ap_ip"] = plan["wanted"]
    payload["ap_ip_effective"] = plan["address"]
    payload["ap_ip_conflict"] = plan["conflict"]
    payload["ap_ip_default"] = netconf.DEFAULT_AP_ADDRESS
    # What wlan0 is SET to be against what it IS, and the live switch under
    # way or the last one's outcome (wifiswitch.py). A switch writes the mode
    # only once it worked, so a mismatch means the radio was moved by hand.
    payload["mode"] = netconf.wifi_mode()
    payload["current_mode"] = "ap" if payload.get("ap") else "wifi"
    payload["mode_mismatch"] = payload["mode"] != payload["current_mode"]
    payload["switch"] = wifiswitch.state()
    payload["wifi_test"] = wifiscan.helper_can("trial")   # Test buttons (wifitest.py)
    # A static uplink proving it reaches the internet, or the question the
    # page must ask when it does not (uplinkguard.py), per port.
    payload["uplink_check"] = uplinkguard.state()
    # What GET /api/health/box answers with on this box: the page matches it
    # against every address it probes after a switch, never trusting an
    # address alone (a lease the box held may be another device's now).
    payload["box_id"] = hostinfo.box_id()
    payload["box_url"] = "%s:%d" % (hostname.local_name(), hubport.port())
    # Interfaces given a fixed address LIVE since this boot, where the
    # operator chose "Later" for the reboot. Separate from mode_mismatch:
    # that one is about wlan0's job, this one about an address.
    payload["ipv4_reboot"] = netconf.ipv4_reboot_pending()
    return payload


# NOTHING below runs nmcli on the event loop (perf item L5). netconf.status()
# is 12-18 nmcli forks; awaited in place, every one of them stalled the whole
# hub -- every other page's request waited behind the Network page's poll.
# Each handler hands its netconf work to a worker thread instead.
#
#: netconf.status() reused for this long: the page polls, the AP banner
#: polls, and two tabs poll twice. Every write through this router drops it,
#: so a change is never answered with the state from before it.
STATUS_TTL_S = 2.0
_status_cache = {"at": 0.0, "value": None}
_status_lock = threading.Lock()


def _status():
    with _status_lock:
        if _status_cache["value"] is not None \
                and time.monotonic() - _status_cache["at"] < STATUS_TTL_S:
            return dict(_status_cache["value"])
    value = netconf.status()
    with _status_lock:
        _status_cache.update(at=time.monotonic(), value=value)
    return dict(value)


def _forget_status():
    with _status_lock:
        _status_cache.update(at=0.0, value=None)


async def _answer(request, payload=None):
    """{ok, ...the page's payload} built off the event loop: the status (when
    no write handed one back), the caller's interface and the defaults."""
    def build():
        return {"ok": True, **_defaults(_via(request, payload if payload is not None
                                             else _status()))}
    return await asyncio.to_thread(build)


async def _net(fn, *args, **kwargs):
    """A netconf WRITE on a worker thread; the status cache goes with it.
    Refused while a Wi-Fi switch is moving wlan0: it would race the switch."""
    if wifiswitch.busy():
        raise netconf.NetError("the Wi-Fi is switching right now - try again in a minute")
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    finally:
        _forget_status()


# The Save button's work lives in network_apsave.py; re-exported for the
# route below and the tests.
from core.routes.network_apsave import (AP_RESTART_DELAY_S,  # noqa: E402,F401
                                        _restart_ap_later, _save_request, save_and_apply)


@router.get("/api/hub/network", name="network.status")
async def network_status(request: Request):
    _require_admin(request)
    return await _answer(request)


@router.post("/api/hub/network/scan", name="network.scan")
async def network_scan(request: Request):
    _require_admin(request)
    try:     # a real scan through the root helper, AP mode too (wifiscan.py)
        networks, fresh = await asyncio.to_thread(wifiscan.search)
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "wifi": networks, "fresh": fresh}


@router.post("/api/hub/network/radio", name="network.radio")
async def network_radio(request: Request):
    _require_admin(request)
    body = await _body(request)
    try:
        payload = await _net(netconf.set_radio, bool(body.get("on")))
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request, payload)


@router.post("/api/hub/network/join", name="network.join")
async def network_join(request: Request):
    _require_admin(request)
    body = await _body(request)
    try:
        payload = await _net(netconf.join, str(body.get("ssid") or ""),
                             str(body.get("password") or ""),
                             bool(body.get("hidden")),
                             autoconnect=body.get("autoconnect", True) is not False)
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request, payload)


@router.post("/api/hub/network/forget", name="network.forget")
async def network_forget(request: Request):
    _require_admin(request)
    body = await _body(request)
    try:
        payload = await _net(netconf.forget, str(body.get("name") or ""))
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request, payload)


@router.post("/api/hub/network/ap", name="network.ap")
async def network_ap(request: Request):
    """Switch wlan0's job NOW, no reboot: {on: true} = access point, {on:
    false} = Wi-Fi -- {network} a saved one, {ssid, password, hidden} a new
    one, neither = any saved one in range. Answers at once, payload["switch"]
    "switching"; wifiswitch.py does it on a thread and, when the Wi-Fi
    cannot be joined, says why and leaves the hotspot OFF (never raised by
    itself, Tok Aba 2026-10-14). The page polls. Wi-Fi off means the access
    point; a failed join leaves wlan0 a Wi-Fi client on no network.
    """
    _require_admin(request)
    body = await _body(request)
    devices = await asyncio.to_thread(netconf.devices)
    via = _via(request, {"devices": devices})["via"]
    try:
        await _net(wifiswitch.start, "wifi" if body.get("on") is False else "ap", via=via,
                   devices=devices,
                   network=str(body.get("network") or ""), ssid=str(body.get("ssid") or ""),
                   password=str(body.get("password") or "") if body.get("on") is False else "",
                   hidden=body.get("hidden") is True, config_dir=request.app.state.config_dir)
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request)


@router.get("/api/health/box", name="network.box_identity")
def box_identity():
    """Which box answers here, and whether its Wi-Fi switch is still moving.

    OPEN and CROSS-ORIGIN, the only route in this file that is either. After
    a switch the Network page is still running at the address the box LEFT,
    and probes every address the box may have moved to (wifiswitch.candidates)
    from there -- a different origin, with no session there. It goes to the
    first one whose `box` is this box's and whose switch has finished. Open
    by name (auth.OPEN_EXACT), not by sitting under /api/health: Pipi L1.

    Deliberately thin: a hashed id (hostinfo.box_id), one word of switch
    state, nothing else. No address, name, version or network. The CORS
    header is on THIS response only; nothing else on the hub is readable
    cross-origin. A simple GET with no credentials, so no preflight.
    """
    from fastapi.responses import JSONResponse                # noqa: PLC0415
    return JSONResponse({"ok": True, "box": hostinfo.box_id(),
                         "switch": wifiswitch.state().get("state") or "idle"},
                        headers={"Access-Control-Allow-Origin": "*",
                                 "Cache-Control": "no-store"})


@router.post("/api/hub/network/ap/save", name="network.ap_save")
async def network_ap_save(request: Request):
    """Save the access point's name and password: {ssid, password}.

    Either may be left out (kept as stored). While the AP broadcasts, the
    change is made NOW, not at the next reboot (netconf, "APPLY-NOW"): the
    page has already asked, and said that every device on it drops off. With
    the AP off it is only stored. The key is never in the reply beyond what
    GET already returns to the same admins, and never in a log line.
    """
    _require_admin(request)
    body = await _body(request)
    try:
        outcome = await _net(_save_request, request, body, request.app.state.config_dir)
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    answer = await _answer(request)
    answer["ap_save"] = outcome
    return answer


@router.post("/api/hub/network/ipv4", name="network.ipv4")
async def network_ipv4(request: Request):
    _require_admin(request)
    body = await _body(request)
    # Which interface carries THIS request, worked out before anything is
    # written: an unplugged jack can be preset, the one the page rides on
    # never can (netconf.set_ipv4 refuses it).
    devs = await asyncio.to_thread(netconf.devices)
    via = _via(request, {"devices": devs})["via"]
    dhcp = bool(body.get("dhcp", True))
    device = str(body.get("device") or "")
    gateway = str(body.get("gateway") or "")
    # The Test button's question, asked again on Apply (Tok Aba 2026-10-09:
    # "when detected ip conflict then tell it is conflict"): an address
    # another device answers for is refused before anything is written. A
    # jack with no cable cannot ask (ipconflict: checked False) and saves.
    if not dhcp and netconf._valid_ip(str(body.get("ip") or "")):
        clash = await asyncio.to_thread(netconf.address_in_use, str(body.get("ip")),
                                        device, devs)
        if clash.get("in_use"):
            raise HTTPException(status_code=409, detail=clash["detail"] + " - pick "
                                "another address, or take that device off the network first")
    # Another write to the port supersedes a check or question on it.
    uplinkguard.disarm(device)
    # A LIVE fixed address with a gateway is checked after it is applied
    # (uplinkguard.py): as the uplink, put back if the internet does not
    # answer and nobody says Keep (Pipi M2); as standby, made machine-only.
    # What it is put back TO is read now, before anything is written.
    before = ("", [])
    if not dhcp and gateway.strip():
        try:
            before = await asyncio.to_thread(netconf.ipv4_snapshot, device)
        except netconf.NetError:
            before = ("", [])
    try:
        payload = await _net(netconf.set_ipv4, str(body.get("device") or ""),
                                   dhcp=dhcp,
                                   ip=str(body.get("ip") or ""),
                                   # The form asks for a subnet mask; older
                                   # callers send a prefix length. Either
                                   # reaches the same place (mask_to_prefix).
                                   prefix=body.get("mask") or body.get("prefix") or "24",
                                   gateway=str(body.get("gateway") or ""),
                                   dns=str(body.get("dns") or ""),
                                   # No `uplink`: the box decides which port
                                   # is (netuplink.auto_role); an older
                                   # page's field is ignored.
                                   via=via)
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    role = payload.get("role")
    if not payload.get("deferred") and before[0] and role in ("uplink", "standby"):
        # The reply's uplink_check (_defaults) shows it "checking" at once.
        uplinkguard.arm(device, before[0], before[1], str(body.get("ip") or "").strip(),
                        gateway.strip(), request.app.state.config_dir, role=role)
    if not payload.get("deferred"):
        # Live and fixed: on the interface now, but services that cached the
        # old address want a restart. The page asks Reboot now / Later; this
        # keeps "Later" on the page until the next boot. Live and back on
        # DHCP: what the reboot was for has been undone, so the note goes.
        try:
            if dhcp:
                await _net(netconf.clear_ipv4_reboot, str(body.get("device") or ""))
            else:
                await _net(netconf.mark_ipv4_reboot, str(body.get("device") or ""))
        except Exception:                                     # noqa: BLE001
            pass
    return await _answer(request, payload)


@router.post("/api/hub/network/ipv4/keep", name="network.ipv4_keep")
async def network_ipv4_keep(request: Request):
    """{device}: keep a static uplink the internet did not answer through
    (uplinkguard.py). Without this inside KEEP_S the box puts it back."""
    _require_admin(request)
    body = await _body(request)
    try:
        await asyncio.to_thread(uplinkguard.keep, str(body.get("device") or ""))
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request)


@router.post("/api/hub/network/ipv4/revert", name="network.ipv4_revert")
async def network_ipv4_revert(request: Request):
    """{device}: put that port's previous settings back now, without waiting
    out the question. The status cache goes with it."""
    _require_admin(request)
    body = await _body(request)
    try:
        await asyncio.to_thread(uplinkguard.revert, str(body.get("device") or ""))
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        _forget_status()
    return await _answer(request)


@router.post("/api/hub/network/ipv4/test", name="network.ipv4_test")
async def network_ipv4_test(request: Request):
    """Is this address already taken? The Test button beside the field.

    Asked BEFORE the address is applied, which is the only moment it is
    useful: two machines on one address is a fault that shows up as a page
    that stopped loading, on the very interface the operator is holding.
    """
    _require_admin(request)
    body = await _body(request)
    try:
        return await asyncio.to_thread(netconf.address_in_use,
                                       str(body.get("ip") or ""),
                                       str(body.get("device") or ""))
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/hub/network/reset", name="network.reset")
async def network_reset(request: Request):
    """Forget every saved Wi-Fi network and come back as the access point.

    The SETUP name comes back (iriv-<serial>; a saved name is dropped,
    whatever the page sends as ssid) and so does the default key, iriv1234
    (netconf.DEFAULT_AP_PASSWORD) -- the box is back to what the manual
    describes, which is the point of a reset (Tok Aba, 2026-10-07). A
    password the page sends is saved instead (validated like Save). Either
    way the key is STORED before the radio is raised, so the radio never
    broadcasts a key the front panel does not show.
    """
    _require_admin(request)
    body = await _body(request)
    config_dir = request.app.state.config_dir
    typed = str(body.get("password") or "")
    try:
        await _net(netconf.set_wifi_mode, "ap")
        await _net(apconf.save_ap, hostinfo.ap_name(),
                   typed or netconf.DEFAULT_AP_PASSWORD, config_dir)
        payload = await _net(netconf.reset, hostinfo.ap_name(), "")
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request, payload)
