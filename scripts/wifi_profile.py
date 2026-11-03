#!/usr/bin/env python3
"""
wifi_profile.py -- write a Wi-Fi profile as root, with the key off every argv.

WHY THIS EXISTS

The box's hotspot key and the SITE's Wi-Fi passphrase used to reach
NetworkManager as nmcli arguments run through sudo:

    sudo -n nmcli device wifi hotspot ifname wlan0 ssid S password <AP KEY>
    sudo -n nmcli connection add ... wifi-sec.psk <SITE PSK>

sudo logs every command it authorises, argv and all, to the journal and
auth.log (readable by the adm group, which on Raspberry Pi OS includes the
installer's login account); for the 60 seconds nmcli runs, /proc/<pid>/cmdline
shows it to every local user. And the hotspot one ran at EVERY boot (Yaya M-5,
Pipi's follow-up 2b). The site passphrase is the more valuable of the two: it
outlives any rotation of the box.

So the platform asks, and this script -- root-owned, installed OUTSIDE every
release tree at /usr/local/lib/irivai/wifi-profile.py, the terms of
mqtt-apply.py -- decides. The request is ONE JSON object on stdin:

    {"kind": "ap" | "join" | "trial" | "scan" | "sweep", "ssid": str,
     "psk": str, "hidden": bool, "autoconnect": bool, "keep": bool}

("scan" and "sweep" carry nothing else; "trial" is "join" made to prove
itself, below.)

WHAT IT WILL DO, AND NOTHING ELSE

  * write ONE NetworkManager keyfile under /etc/NetworkManager/system-
    connections/ (0600 root, written to a temp file and renamed): irivai-ap
    for kind ap, irivai-wifi-<hex of the SSID> for kind join. A fixed uuid per
    profile, so a rewrite replaces rather than duplicates;
  * delete any OTHER profile with the same name (the "Hotspot" nmcli made
    before this existed, with the old key in it; an SSID's half-made profile)
    -- for a join or a kept trial only once the new one HAS joined (Pipi,
    2026-10-08: a failed attempt must not cost the network saved before);
  * `nmcli connection load <file>` and `nmcli connection up uuid <uuid>`;
  * kind join: a profile that will not come up is deleted again, so a wrong
    passphrase is not retried against the AP for ever -- and the profile it
    replaced in the same file, if there was one, is put back exactly as it
    was (a 0600 copy kept beside it while the attempt runs, a dotfile, which
    NetworkManager never loads);
  * kind ap: an unchanged keyfile on an AP that is already up is left alone,
    so the hub can re-assert the hotspot at every start without dropping it;
  * kind trial (Tok Aba, 2026-10-08, "Test and switch"): the join, written
    first in a strict form that cannot linger -- autoconnect off, one key
    attempt (auth-retries 1), a lease required (may-fail off, a 15 s DHCP
    wait) and `nmcli --wait` bounded -- brought up, and kept only when wlan0
    holds a real address (NOT 169.254: the box's NetworkManager config has
    ipv4.link-local=fallback, so a failed DHCP still "activates"). Kept means
    the keyfile is rewritten in the ordinary join form (autoconnect on, no
    trial limits) and loaded without bouncing the connection. Anything else
    -- refused, timed out, no address, an exception -- takes the profile down
    and deletes keyfile and profile in a `finally`. A trial that is only a
    test ("keep": false) uses a profile of its own, irivai-trial, so testing
    a network the box already has saved leaves the saved one alone;
  * kind sweep, and the first step of EVERY run: what a helper killed
    mid-trial left behind (the hub's time limit, a power cut) is cleared --
    the test profile irivai-trial deleted, a strict trial keyfile finished
    when wlan0 is live on it and deleted when not, a saved profile's kept
    copy put back. The key in such a leftover sits at rest (0600 root,
    autoconnect off) only until the next helper run, and the hub runs one at
    start when it sees a leftover (wifiscan.sweep_at_start);
  * one run at a time: an exclusive lock (/run/irivai-wifi-profile.lock), so
    a sweep never clears the files of a trial still running;
  * kind scan: ask NetworkManager for a fresh scan of wlan0 and answer the
    list. Root because the service account has no polkit session, and
    NetworkManager's wifi.scan is auth_admin for that: nmcli, refused, prints
    the OLD list and exits 0, so the Network page silently showed stale
    networks. NetworkManager 1.52 allows an explicit scan while wlan0 is the
    access point (only its periodic scans stop), so this works in AP mode and
    the hotspot stays up.

It trusts NOTHING in the request: the kind is one of KINDS, the interface
is fixed (wlan0), the SSID is 1-32 bytes and is written as a byte list (no
keyfile escaping to get wrong), the key is 8-63 printable ASCII or 64 hex, and
every other field is a bool. The key reaches a 0600 root file -- the form
NetworkManager stores it in anyway -- and never an argv or the output.

Reached through exactly one sudoers entry, with no arguments:

  sudo -n /usr/bin/python3 -I /usr/local/lib/irivai/wifi-profile.py < request.json

and answers one JSON object on stdout: {"ok": bool, "message": str,
"name": str, "changed": bool} (trial adds "address"; scan answers {"ok",
"fresh", "networks": [{ssid, signal, security, chan, in_use}]}).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

CONN_DIR = "/etc/NetworkManager/system-connections"
IFNAME = "wlan0"
AP_NAME = "Hotspot"
AP_FILE = "irivai-ap"
TEST_FILE = "irivai-trial"
MAX_REQUEST = 4 * 1024
#: What this helper will do. The hub reads this line from the installed copy
#: (wifiscan.helper_can) to know whether a box updated over the air already
#: has the new kinds, before it takes the access point down for one.
KINDS = ("ap", "join", "trial", "scan", "sweep")
#: A saved profile's copy while an attempt may replace it. NetworkManager
#: ignores dotfiles in its keyfile directory (as it does _write's temp files).
KEEP_PREFIX = ".irivai-keep-"
LOCK = "/run/irivai-wifi-profile.lock"
LOCK_WAIT_S = 90
#: The lines only a trial's strict form has (profile(strict=True)).
STRICT_LINES = ("auth-retries=1", "may-fail=false", "dhcp-timeout=")
#: How long a trial may take to associate, authenticate and lease; inside the
#: hub's 35 s join budget (wifiswitch.JOIN_BUDGET_S), so a trial always
#: answers before the hub gives up on it and raises the access point.
TRIAL_WAIT_S = 25
TRIAL_DHCP_S = 15
MAX_NETWORKS = 200
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

#: A fixed namespace, so the uuid of a profile is a function of its name:
#: writing the same profile twice replaces it, never adds a second.
NAMESPACE = uuid.UUID("5f0a1b7e-3c2d-4e8f-9a6b-1c2d3e4f5a6b")

# fullmatch, not match: "$" also matches before a trailing newline (Pipi F2).
_HEX64 = re.compile(r"[0-9A-Fa-f]{64}")


class Refused(ValueError):
    """A request this helper will not carry out; the message is the answer."""


def _bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise Refused("hidden and autoconnect must be true or false")


def validate(req):
    """The request, re-checked from nothing. Returns a clean dict."""
    if not isinstance(req, dict):
        raise Refused("the request must be one JSON object")
    kind = req.get("kind")
    if kind not in KINDS:
        raise Refused("kind must be one of " + ", ".join(KINDS))
    if kind in ("scan", "sweep"):
        return {"kind": kind}
    ssid = req.get("ssid")
    if not isinstance(ssid, str) or not ssid.strip():
        raise Refused("a network name is required")
    raw = ssid.encode("utf-8")
    if len(raw) > 32:
        raise Refused("a network name is at most 32 bytes")
    if any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise Refused("a network name cannot hold control characters")
    psk = req.get("psk") or ""
    if not isinstance(psk, str):
        raise Refused("the password must be text")
    if psk or kind == "ap":
        printable = all(32 <= ord(c) <= 126 for c in psk)
        if not ((8 <= len(psk) <= 63 and printable) or _HEX64.fullmatch(psk)):
            raise Refused("a Wi-Fi password is 8-63 printable characters "
                          "(or 64 hex digits)")
    client = kind in ("join", "trial")
    return {"kind": kind, "ssid": ssid, "psk": psk,
            "hidden": _bool(req.get("hidden"), False) if client else False,
            # A trial that passes is kept as a network the box rejoins.
            "autoconnect": (True if kind == "trial" else
                            _bool(req.get("autoconnect"), True) if client else False),
            # trial only: keep the network when it passes ("Test and switch")
            # or take it down again and leave nothing ("Test connection").
            "keep": _bool(req.get("keep"), True) if kind == "trial" else False}


def _kf_string(value):
    """A GKeyFile string value: backslash, then every space, escaped."""
    return value.replace("\\", "\\\\").replace(" ", "\\s")


def _ssid_bytes(ssid):
    """The SSID as NetworkManager's byte-list form, which needs no escaping
    and cannot be misread (an all-digit SSID, a semicolon, a space)."""
    return "".join("%d;" % b for b in ssid.encode("utf-8"))


def profile(clean, strict=False):
    """(name, file stem, uuid, keyfile text) for a validated request. A KEPT
    trial is the join's profile (same name, file and uuid, so it replaces a
    saved one); a trial that is only a test has a file and uuid of its own,
    so testing a network the box already knows never touches the saved one.
    `strict` is the form while it is being tried (module header)."""
    if clean["kind"] == "ap":
        name, stem = AP_NAME, AP_FILE
    elif clean["kind"] == "trial" and not clean.get("keep"):
        name, stem = clean["ssid"], TEST_FILE
    else:
        name = clean["ssid"]
        stem = "irivai-wifi-" + clean["ssid"].encode("utf-8").hex()
    uid = str(uuid.uuid5(NAMESPACE, stem))
    auto = clean["autoconnect"] and not strict
    lines = ["[connection]", "id=" + _kf_string(name), "uuid=" + uid,
             "type=wifi", "interface-name=" + IFNAME,
             "autoconnect=" + ("true" if auto else "false")]
    lines += ["auth-retries=1", ""] if strict else [""]
    lines += [
             "[wifi]", "mode=" + ("ap" if clean["kind"] == "ap" else "infrastructure"),
             "ssid=" + _ssid_bytes(clean["ssid"])]
    if clean["kind"] == "ap":
        # What `nmcli device wifi hotspot` writes: 2.4 GHz, WPA2-CCMP only.
        lines += ["band=bg"]
    if clean["hidden"]:
        lines += ["hidden=true"]
    lines += [""]
    if clean["psk"]:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", "psk=" + _kf_string(clean["psk"])]
        if clean["kind"] == "ap":
            lines += ["proto=rsn", "pairwise=ccmp", "group=ccmp"]
        lines += [""]
    lines += ["[ipv4]", "method=" + ("shared" if clean["kind"] == "ap" else "auto")]
    lines += ["may-fail=false", "dhcp-timeout=%d" % TRIAL_DHCP_S, ""] if strict else [""]
    lines += ["[ipv6]", "method=" + ("ignore" if clean["kind"] == "ap" else "auto"), ""]
    return name, stem, uid, "\n".join(lines)


def _run(argv, timeout=90):
    env = {"PATH": SAFE_PATH, "LANG": "C"}
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=env, stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def _nm_reason(text):
    line = (text or "").splitlines()[0] if text else "the command failed"
    return re.sub(r"^Error:\s*", "", line)


def _split(line):
    """One `nmcli -t` line on its unescaped colons (a name may hold one)."""
    out, buf, escaped = [], "", False
    for char in line:
        if escaped:
            buf, escaped = buf + char, False
        elif char == "\\":
            escaped = True
        elif char == ":":
            out.append(buf)
            buf = ""
        else:
            buf += char
    out.append(buf)
    return out


def _others_named(name, keep_uuid):
    """uuids of saved WI-FI profiles called `name` that are not ours and not
    the access point's. A name is whatever a nearby radio broadcasts: an SSID
    called "Wired connection 1" or "Hotspot" must never cost the box its
    cable or its AP (Pipi F1)."""
    proc = subprocess.run(["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
                          capture_output=True, text=True, timeout=20,
                          env={"PATH": SAFE_PATH, "LANG": "C"}, stdin=subprocess.DEVNULL)
    ap_uuid = str(uuid.uuid5(NAMESPACE, AP_FILE))
    out = []
    for line in (proc.stdout or "").splitlines():
        parts = _split(line)
        if (len(parts) >= 3 and parts[0] == name and parts[2] == "802-11-wireless"
                and parts[1] and parts[1] not in (keep_uuid, ap_uuid)):
            out.append(parts[1])
    return out


def _active(uid):
    proc = subprocess.run(["nmcli", "-t", "-f", "UUID", "connection", "show", "--active"],
                          capture_output=True, text=True, timeout=20,
                          env={"PATH": SAFE_PATH, "LANG": "C"}, stdin=subprocess.DEVNULL)
    return uid in (proc.stdout or "").split()


def _write(path, text):
    """0600 root, written whole and renamed: NetworkManager ignores a keyfile
    anyone but root can read, and never sees half of one."""
    fd, tmp = tempfile.mkstemp(dir=CONN_DIR, prefix=".irivai-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _keep_path(stem):
    return os.path.join(CONN_DIR, KEEP_PREFIX + stem)


def _keep_copy(path, stem):
    """Copy the saved profile in `path`, if any, before an attempt writes it."""
    try:
        with open(path, encoding="utf-8") as fh:
            _write(_keep_path(stem), fh.read())
    except OSError:
        pass


def _forget_copy(stem):
    try:
        os.unlink(_keep_path(stem))
    except OSError:
        pass


def _put_back(path, stem, uid):
    """After a failed attempt: the saved profile as it was, else nothing."""
    try:
        with open(_keep_path(stem), encoding="utf-8") as fh:
            before = fh.read()
    except OSError:
        before = None
    _run(["nmcli", "connection", "down", "uuid", uid], timeout=30)
    if before is None:
        _run(["nmcli", "connection", "delete", "uuid", uid], timeout=30)
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    _write(path, before)
    _run(["nmcli", "connection", "load", path], timeout=30)
    _forget_copy(stem)


def _drop_others(name, uid):
    for other in _others_named(name, uid):
        _run(["nmcli", "connection", "delete", "uuid", other], timeout=30)


def apply(clean):
    name, stem, uid, text = profile(clean)
    path = os.path.join(CONN_DIR, stem + ".nmconnection")
    if clean["kind"] == "join":
        return _join(clean, name, stem, uid, text, path)
    try:
        with open(path, encoding="utf-8") as fh:
            unchanged = fh.read() == text
    except OSError:
        unchanged = False
    _drop_others(name, uid)
    if clean["kind"] == "ap" and unchanged and _active(uid):
        return {"ok": True, "message": "the access point is already up", "name": name,
                "changed": False}
    if not unchanged:
        _write(path, text)
    rc, err = _run(["nmcli", "connection", "load", path], timeout=30)
    if rc != 0:
        return {"ok": False, "message": _nm_reason(err), "name": name, "changed": not unchanged}
    rc, err = _run(["nmcli", "connection", "up", "uuid", uid], timeout=90)
    if rc != 0:
        return {"ok": False, "message": _nm_reason(err), "name": name, "changed": not unchanged}
    return {"ok": True, "message": "up", "name": name, "changed": not unchanged}


def _join(clean, name, stem, uid, text, path):
    """kind join: the old profile(s) for the SSID survive a failed attempt."""
    _keep_copy(path, stem)
    joined = False
    try:
        _write(path, text)
        rc, err = _run(["nmcli", "connection", "load", path], timeout=30)
        if rc == 0:
            rc, err = _run(["nmcli", "connection", "up", "uuid", uid], timeout=90)
        if rc != 0:
            # Not kept: with autoconnect on, NetworkManager would retry a
            # wrong passphrase against the access point for ever.
            return {"ok": False, "message": _nm_reason(err), "name": name, "changed": True}
        joined = True
        _drop_others(name, uid)
        _forget_copy(stem)
        return {"ok": True, "message": "up", "name": name, "changed": True}
    finally:
        if not joined:
            _put_back(path, stem, uid)


# -- trial: a join that must prove itself -------------------------------------

#: Why a trial failed, as one word the hub turns into a sentence. Read from
#: NetworkManager's own sentence first, then from what NetworkManager and
#: wpa_supplicant logged during the trial (root reads the journal; only the
#: word leaves this helper, never a log line).
_REASONS = (
    # a wrong key: the 4-way handshake failed after association, and
    # NetworkManager asked for a new key nobody could give it
    ("wrong_password", ("pre-shared key may be incorrect", "reason=wrong_key",
                        "secrets were required", "'no-secrets'", "4-way handshake failed")),
    # found, but the radio could not get on: association or authentication
    # rejected or timed out -- weak signal, a full or MAC-filtered AP
    ("assoc_failed", ("ctrl-event-assoc-reject", "ctrl-event-auth-reject",
                      "authentication with", "association request to",
                      "'supplicant-timeout'", "'supplicant-disconnect'",
                      "'supplicant-failed'")),
    ("not_found", ("could not be found", "ctrl-event-network-not-found",
                   "'ssid-not-found'", "no network with ssid")),
    ("no_address", ("ip configuration could not be reserved", "'ip-config-unavailable'",
                    "dhcp")),
    ("timeout", ("timeout expired", "timed out")),
)


def classify(nm_text, journal="", address=""):
    """One of wrong_password, assoc_failed, not_found, no_address, timeout,
    failed. NetworkManager's sentence decides when it states one of the two
    it states plainly; otherwise the journal does, in the order of _REASONS
    (a rejected key outranks the association noise around it), and last
    NetworkManager's sentence again ("Timeout expired")."""
    nm = (nm_text or "").lower()
    if not nm and (not address or address.startswith("169.254.")):
        return "no_address"     # came up with no lease, or link-local only
    table = dict(_REASONS)
    for word in ("not_found", "wrong_password"):
        if any(k in nm for k in table[word]):
            return word
    text = (journal or "").lower()
    for word, keys in _REASONS:
        if any(k in text for k in keys):
            return word
    for word, keys in _REASONS:
        if any(k in nm for k in keys):
            return word
    return "failed"


def _out(argv, timeout=20):
    """stdout of a read-only command, "" when it fails."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": SAFE_PATH, "LANG": "C"}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout or ""


def _wlan_ipv4():
    """(address, gateway) wlan0 holds now; "" for either it lacks."""
    out = _out(["nmcli", "-g", "IP4.ADDRESS,IP4.GATEWAY", "device", "show", IFNAME])
    lines = (out.splitlines() + ["", ""])[:2]
    return lines[0].split(" | ")[0].split("/")[0].strip(), lines[1].strip()


def _journal(since, uid=""):
    """What NetworkManager and wpa_supplicant said about wlan0 (or the trial's
    profile) since `since` (epoch). Only those lines: eth0's own DHCP lines
    would otherwise read as "no address" for a Wi-Fi that never got that far."""
    out = _out(["journalctl", "--no-pager", "-o", "cat", "-n", "400",
                "--since", "@%d" % int(since), "-u", "NetworkManager",
                "-u", "wpa_supplicant"])
    return "\n".join(line for line in out.splitlines()
                     if IFNAME in line or (uid and uid in line))


def _pings(host):
    """Does `host` answer one ping sent out of wlan0 itself; None: no host."""
    if not host:
        return None
    try:
        return subprocess.run(["ping", "-n", "-q", "-c", "1", "-W", "2", "-I", IFNAME, host],
                              capture_output=True, timeout=6, env={"PATH": SAFE_PATH},
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _drop(path, uid):
    """The trial's profile gone: down, deleted, and its keyfile removed."""
    _run(["nmcli", "connection", "down", "uuid", uid], timeout=30)
    _run(["nmcli", "connection", "delete", "uuid", uid], timeout=30)
    try:
        os.unlink(path)
    except OSError:
        pass


def trial(clean):
    name, stem, uid, strict = profile(clean, strict=True)
    path = os.path.join(CONN_DIR, stem + ".nmconnection")
    started, kept = time.time() - 1, False
    answer = {"ok": False, "name": name, "changed": True, "address": "",
              "reason": "failed", "message": ""}
    if clean["keep"]:
        _keep_copy(path, stem)     # a saved profile for the SSID, put back on failure
    try:
        _write(path, strict)
        rc, err = _run(["nmcli", "connection", "load", path], timeout=30)
        if rc == 0:
            rc, err = _run(["nmcli", "--wait", str(TRIAL_WAIT_S), "connection", "up",
                            "uuid", uid], timeout=TRIAL_WAIT_S + 15)
        address, gateway = _wlan_ipv4() if rc == 0 else ("", "")
        if rc != 0 or not address or address.startswith("169.254."):
            said = _nm_reason(err) if rc != 0 else ""
            answer.update(reason=classify(said, _journal(started, uid), address),
                          message=said or "no address from DHCP")
            return answer
        answer.update(ok=True, reason="", address=address, message="joined",
                      gateway=_pings(gateway),
                      internet=bool(_pings("1.1.1.1") or _pings("8.8.8.8")))
        if clean["keep"]:
            # Passed: the ordinary join profile, loaded under the live
            # connection without bouncing it (the next activation uses it).
            _write(path, profile(clean)[3])
            _run(["nmcli", "connection", "load", path], timeout=30)
            kept = True
            _drop_others(name, uid)    # only now: the new one has joined
            _forget_copy(stem)
        return answer
    finally:
        if not kept:
            if clean["keep"]:
                _put_back(path, stem, uid)
            else:
                _drop(path, uid)


# -- sweep: what a killed run left behind ----------------------------------------------

def _finished(text):
    """A strict trial keyfile in its ordinary join form (profile(clean)[3])."""
    out = []
    for line in text.split("\n"):
        if any(line.startswith(k) for k in STRICT_LINES):
            continue
        out.append("autoconnect=true" if line == "autoconnect=false" else line)
    return "\n".join(out)


def sweep():
    """Clear every trial leftover (module header). Returns what it did."""
    did = []
    try:
        names = sorted(os.listdir(CONN_DIR))
    except OSError:
        return did
    active = _active_uuids()
    test_uid = str(uuid.uuid5(NAMESPACE, TEST_FILE))
    test_path = os.path.join(CONN_DIR, TEST_FILE + ".nmconnection")
    if TEST_FILE + ".nmconnection" in names or test_uid in active:
        _drop(test_path, test_uid)
        did.append("test")
    for fname in names:
        if not fname.startswith("irivai-wifi-") or not fname.endswith(".nmconnection"):
            continue
        stem = fname[:-len(".nmconnection")]
        path = os.path.join(CONN_DIR, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        uid = str(uuid.uuid5(NAMESPACE, stem))
        if "auth-retries=1" not in text.split("\n"):
            if os.path.exists(_keep_path(stem)):
                # A copy is removed the moment an attempt succeeds, so one
                # still here means a join cut short: its profile never proved
                # itself. Kept only when wlan0 is live on it (Pipi F4).
                if uid in active:
                    _forget_copy(stem)
                    did.append("copy")
                else:
                    _put_back(path, stem, uid)
                    did.append("put back")
            continue
        if uid in active:
            # It joined and the helper died before keeping it: keep it now.
            _write(path, _finished(text))
            _run(["nmcli", "connection", "load", path], timeout=30)
            _forget_copy(stem)
            did.append("finished")
        else:
            _put_back(path, stem, uid)
            did.append("put back")
    for fname in names:      # a copy whose profile file is gone: put it back
        if fname.startswith(KEEP_PREFIX):
            stem = fname[len(KEEP_PREFIX):]
            if stem + ".nmconnection" not in names and stem.startswith("irivai-wifi-"):
                _put_back(os.path.join(CONN_DIR, stem + ".nmconnection"), stem,
                          str(uuid.uuid5(NAMESPACE, stem)))
                did.append("put back")
        elif fname.startswith(".irivai-") and not fname.startswith(KEEP_PREFIX):
            try:
                os.unlink(os.path.join(CONN_DIR, fname))   # a half-written temp
                did.append("temp")
            except OSError:
                pass
    return did


def _active_uuids():
    proc = subprocess.run(["nmcli", "-t", "-f", "UUID", "connection", "show", "--active"],
                          capture_output=True, text=True, timeout=20,
                          env={"PATH": SAFE_PATH, "LANG": "C"}, stdin=subprocess.DEVNULL)
    return set((proc.stdout or "").split())


def _lock():
    """One helper run at a time (fcntl is imported here: the tests load this
    module on Windows too)."""
    import fcntl                                              # noqa: PLC0415
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() > deadline:
                os.close(fd)
                raise Refused("another Wi-Fi change is still running - try again in a minute")
            time.sleep(0.5)


def scan():
    rc, _ = _run(["nmcli", "device", "wifi", "rescan", "ifname", IFNAME], timeout=20)
    out = _out(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN", "device", "wifi",
                "list", "ifname", IFNAME, "--rescan", "yes" if rc == 0 else "no"], timeout=45)
    networks = []
    for line in out.splitlines()[:MAX_NETWORKS]:
        parts = _split(line)
        if len(parts) < 5 or not parts[1]:
            continue
        networks.append({"in_use": parts[0].strip() == "*", "ssid": parts[1][:64],
                         "signal": int(parts[2]) if parts[2].isdigit() else 0,
                         "security": parts[3][:40], "chan": parts[4][:4]})
    return {"ok": True, "message": "scanned", "fresh": rc == 0, "networks": networks}


def run(clean):
    """Under the lock: the sweep first, then the request."""
    fd = _lock()
    try:
        swept = sweep()
        if clean["kind"] == "sweep":
            return {"ok": True, "message": "swept", "swept": swept}
        return (scan() if clean["kind"] == "scan" else
                trial(clean) if clean["kind"] == "trial" else apply(clean))
    finally:
        os.close(fd)


def main():
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "message": "this helper must run as root"}))
        return 1
    raw = sys.stdin.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        print(json.dumps({"ok": False, "message": "the request is too large"}))
        return 1
    try:
        clean = validate(json.loads(raw or "null"))
        result = run(clean)
    except Refused as exc:
        result = {"ok": False, "message": str(exc)}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Never the request itself in the answer: it holds the key.
        result = {"ok": False, "message": exc.__class__.__name__}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
