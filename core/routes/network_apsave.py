# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
network_apsave.py -- the work behind the access point's Save button
(POST /api/hub/network/ap/save in core/routes/network.py).

Lifted out of core/routes/network.py, which keeps the route and re-exports
these names (the tests call them there), so that file stays under its line
ceiling. Nothing changed in the move. The route module's own helpers, _via
and the status cache's _forget_status, are looked up there at call time:
one status cache, and a stub placed on the route module still reaches here.
The log lines keep the route module's logger name.
"""

import logging
import threading
import time

from core.services import apconf
from core.services import netconf

logger = logging.getLogger("core.routes.network")


#: How long a save waits before restarting an access point the caller is ON.
#: The reply has to reach the browser first: it is what tells the operator
#: to join the new network, and it cannot arrive over a radio that is
#: already restarting.
AP_RESTART_DELAY_S = 3.0


def _restart_ap_later(config_dir, delay=None):
    """Restart the AP on its stored values after the reply has gone out.

    Logs the NAME only; the key is never in a log line here (and NetError's
    message never carries it: the helper takes it on stdin, and _nmcli no
    longer quotes an argv)."""
    def run():
        time.sleep(AP_RESTART_DELAY_S if delay is None else delay)
        try:
            apconf.apply_ap(config_dir)
            logger.info("[NET] access point restarted as %s", apconf.ap_ssid(config_dir))
        except Exception as exc:                              # noqa: BLE001
            logger.warning("[NET] the access point did not restart on its new "
                           "settings (%s) - it is retried at the next boot", exc)
        finally:
            from core.routes import network as routes   # noqa: PLC0415
            routes._forget_status()
    threading.Thread(target=run, name="ap-restart", daemon=True).start()


def save_and_apply(ssid, password, config_dir=None, broadcasting=None, via=None,
                   later=_restart_ap_later):
    """The Save button's work, off the event loop. Returns {applied, message}.

    `applied`:
      unchanged  nothing differed from what is stored; nothing written;
      stored     the AP is not broadcasting: saved for the next time it is;
      now        broadcasting, the caller is NOT on it: restarted here, and
                 a failure is reported (the values stay saved);
      scheduled  broadcasting AND the caller came in over wlan0: restarted
                 a few seconds after this reply (_restart_ap_later), because
                 restarting it now would cut the reply off.
    """
    if broadcasting is None:
        broadcasting = bool(netconf.ap_active())
    result = apconf.save_ap(ssid, password, config_dir)
    if not result["changed"]:
        return {"applied": "unchanged", "message": "Nothing to save."}
    if not broadcasting:
        return {"applied": "stored",
                "message": "Saved. The access point uses it the next time it is on."}
    if via == netconf.AP_DEVICE:
        later(config_dir)
        return {"applied": "scheduled",
                "message": "Saved. The access point restarts in a few seconds; join "
                           f"{result['ssid']} with the new password."}
    try:
        apconf.apply_ap(config_dir)
    except netconf.NetError as exc:
        logger.warning("[NET] the access point did not restart on its new settings "
                       "(%s) - it is retried at the next boot", exc)
        return {"applied": "failed",
                "message": "Saved, but the access point did not restart: "
                           f"{exc}. It uses the new settings after a reboot."}
    logger.info("[NET] access point restarted as %s", result["ssid"])
    return {"applied": "now",
            "message": "Saved. The access point restarted with the new settings."}


def _save_request(request, body, config_dir):
    """POST ap/save's whole job, on a worker thread (_net): is the AP up,
    which interface carries this request -- both read BEFORE anything
    moves -- then save_and_apply. A field left out keeps its stored value."""
    broadcasting = bool(netconf.ap_active())
    from core.routes import network as routes           # noqa: PLC0415
    via = routes._via(request, {"devices": netconf.devices()})["via"] if broadcasting else ""
    ssid = body.get("ssid")
    if ssid is None:
        ssid = apconf.ap_ssid(config_dir)
    password = body.get("password")
    return save_and_apply(str(ssid), None if password is None else str(password),
                          config_dir, broadcasting, via)
