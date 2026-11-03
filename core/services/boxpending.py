# -*- coding: utf-8 -*-
"""
boxpending.py -- the software port and the hostname change at the NEXT
RESTART OF THE BOX, never on Save.

Tok Aba (2026-10-05): master or a developer changes either from the
Network page with no terminal; saving does not take effect; the page says a
restart is needed and offers Restart now / Later; until the box restarts it
answers on the same port and the same name; one restart applies both.

WHY A REBOOT, NOT A HUB RESTART. The port alone could follow a hub restart,
but the name cannot: every process caches gethostname() at start (the
hub's mDNS advertisement, the masters, the apps, the device agent), avahi
announces it, NetworkManager hands it to the DHCP server, a desktop
Chromium locks its profile with it. A reboot is the one scope that restarts
all of them, it is already the Network page's answer for a fixed address
(the Wi-Fi mode switches live since 2026-10-07), and its sudoers rule
(`reboot now`, irivai-box) is on every box installed since 2026-08-28. Same
rule for both, one sentence.

WHEN IT APPLIES: the hub's first start in a boot OTHER than the one the
change was saved in (/proc/sys/kernel/random/boot_id). Restart now, the
front panel's reboot, a power cut: all apply it. A hub crash or an OTA
restart in the same boot moves nothing -- "until the box restarts" holds.

The ACCESS MODE (port or reverse proxy, webaccess.py) rides the same
restart, for the same reason: every link, the panel and the mDNS record
change with it, and one restart moves them all together.

WHO APPLIES IT. apply_at_start() runs in hub_server.main before anything
binds or advertises: the port by the hub itself (no root: the port is the
account's own setting, hubport.py), the name by the root helper
/usr/local/lib/irivai/set-hostname.py through its no-argument sudoers rule
(hostname.py), which validates the name again as root. Each refusal keeps
the OLD value, the result is kept for this boot (last()) for the cards, and
a refusal also reaches the admins' Inbox.
"""

import shutil
import subprocess
import threading
import time

from core import config, net
from core.services import discovery_mdns, hostname, hubport, webaccess

#: The panel's own reboot argv; the sudoers rule names /usr/sbin/reboot now.
REBOOT_ARGV = ("sudo", "-n", "reboot", "now")


def boot_id():
    """This boot's id, or "" where the kernel has none (a dev box)."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def apply_at_start(config_dir=None):
    """hub_server.main, before the port is resolved: apply what an earlier
    boot saved. None when nothing was waiting; else the record last() shows."""
    boot = boot_id()
    if not hubport.owns_setting() or not boot:   # a second hub applies nothing
        return None
    port = hubport.apply_pending(boot, config_dir)
    name = hostname.apply_pending(boot, config_dir)
    access = webaccess.apply_pending(boot, config_dir)
    if port is None and name is None and access is None:
        return None
    record = {"boot": boot, "at": int(time.time())}
    for key, res in (("port", port), ("name", name), ("access", access)):
        if res is not None:
            record.update({f"{key}_ok": bool(res["ok"]), f"{key}_from": str(res["from"]),
                           f"{key}_to": str(res["to"]), f"{key}_detail": res["detail"]})
    local = dict(config.load_local("network", config_dir))
    local["box_restart"] = record
    config.save_local("network", local, config_dir)
    return record


def failures(record):
    """The plain sentences for an Inbox notice, one per refused change."""
    out = []
    if record and record.get("port_ok") is False:
        out.append(f"The box restarted but kept port {record['port_from']}: "
                   f"{record['port_detail']}.")
    if record and record.get("name_ok") is False:
        out.append(f"The box restarted but kept the hostname {record['name_from']}: "
                   f"{record['name_detail']}.")
    if record and record.get("access_ok") is False:
        out.append("The box restarted but kept addresses with a port (it could "
                   f"not answer on port 80): {record['access_detail']}.")
    return out


def last(config_dir=None):
    """What the restart that started THIS boot applied, or None."""
    record = dict(config.load_local("network", config_dir).get("box_restart") or {})
    return record if record and record.get("boot") == boot_id() else None


def can_reboot():
    """May this account reboot the box without a password? Asked of sudo
    (`sudo -n -l` runs nothing), as hostname.can_set does."""
    if not hubport.under_systemd() or not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-l", *REBOOT_ARGV[2:]],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def reboot(delay=1.0):
    """Reboot in `delay` seconds, from a thread, so the reply leaves first.
    Through the front panel when it runs (its beep and farewell screen),
    else the same sudo line."""
    def _go():
        time.sleep(delay)
        try:
            from core.services import panel                  # noqa: PLC0415
            running = panel.current()
        except Exception:                                    # noqa: BLE001
            running = None
        if running and running.reboot():
            return
        try:
            subprocess.Popen(list(REBOOT_ARGV), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError:
            pass
    threading.Thread(target=_go, name="box-restart", daemon=True).start()


def _is_ip(host):
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts)


def after(request, config_dir=None):
    """Where the dashboard will be once the box has restarted: by name and
    by address. The browser's own address first when it used one -- the
    one it can certainly reach again (core/net.py: the request's host)."""
    port = hubport.pending_port(config_dir) or hubport.running_port() \
        or hubport.port(config_dir)
    # Reverse proxy after the restart (webaccess.py): port 80, no port in it.
    proxy = (webaccess.pending(config_dir) or webaccess.mode(config_dir)) \
        == webaccess.PROXY
    shown = "" if proxy else f":{port}"
    name = hostname.pending(config_dir)
    name = f"{name}.local" if name else hostname.local_name()
    host = net.request_host(request) if request is not None else ""
    ips = [host] if _is_ip(host) else []
    ips += [ip for ip in discovery_mdns.interface_ips()
            if ip not in ips and not ip.startswith("127.")]
    return {"port": webaccess.HTTP_PORT if proxy else port, "name": name,
            "proxy": proxy, "name_url": f"http://{name}{shown}",
            "ip_urls": [f"http://{ip}{shown}" for ip in ips]}


def status(request=None, config_dir=None):
    """The block both cards draw from: what waits, where to go after, can
    the page restart the box, whether THIS hub is the one that applies a
    saved change (`applies`: only irivai-hub.service does, hubport.owns_
    setting; the cards refuse Apply and promise nothing otherwise), and
    what the last restart did."""
    port = hubport.pending_port(config_dir)
    name = hostname.pending(config_dir)
    access = webaccess.pending(config_dir)
    return {"port": port, "hostname": name or None, "access": access or None,
            "current": {"port": hubport.running_port() or hubport.port(config_dir),
                        "name": hostname.local_name(),
                        "access": webaccess.mode(config_dir)},
            "any": bool(port or name or access), "can_reboot": can_reboot(),
            "applies": hubport.owns_setting(),
            "after": after(request, config_dir), "last": last(config_dir)}
