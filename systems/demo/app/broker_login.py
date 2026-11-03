"""
broker_login.py -- the login this box's own MQTT broker wants, for MQTT nodes
that talk to it.

Settings > MQTT Broker can require a username and password
(core/services/mqtt_broker.py). This app's MQTT Subscribe and MQTT Publish
nodes default to 127.0.0.1:1883 and carry no login of their own, so a
secured broker would lock the box out of its own broker. Putting the password
into the compiled config instead would hand it to every page that reads a
workflow's functions or outputs.

So the app reads it here, at connect time, from the platform's 0600
mqtt_broker.local.toml. The path arrives in IRIVAI_MQTT_LOGIN, which the hub
and every master set before they launch an app; the fallback is where an
appliance install keeps its config.

Used only for THIS box's broker: a loopback host on the port the broker was
set to. A node pointed anywhere else, or one that names its own username,
gets nothing from here -- this box's password is never offered to somebody
else's broker.
"""

import os

try:                                   # 3.11+
    import tomllib as _toml

    def _parse(raw):
        return _toml.loads(raw.decode("utf-8"))
except ImportError:                    # 3.9/3.10
    import toml as _toml               # noqa: F401  (in requirements.txt)

    def _parse(raw):
        return _toml.loads(raw.decode("utf-8"))

ENV = "IRIVAI_MQTT_LOGIN"
FALLBACK = "/opt/irivai-systor/config/mqtt_broker.local.toml"
LOOPBACK = ("", "127.0.0.1", "localhost", "::1")

#: (path, mtime) -> login. Re-read only when the file changes, so a publish
#: every 0.2 s is a stat(), not a parse, and a new password from the page is
#: picked up on the next connect without a restart.
_cache = {"key": None, "login": None}


def _path():
    return os.environ.get(ENV) or FALLBACK


def _load():
    path = _path()
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    if _cache["key"] == key:
        return _cache["login"]
    login = None
    try:
        with open(path, "rb") as f:
            doc = _parse(f.read())
        row = doc.get("login") or {}
        user = str(row.get("username") or "")
        if user:
            login = {"username": user,
                     "password": str(row.get("password") or ""),
                     "port": int(row.get("port") or 1883)}
    except Exception:                                        # noqa: BLE001
        login = None
    _cache.update(key=key, login=login)
    return login


def for_address(host, port):
    """(username, password) for this box's broker at host:port, or None."""
    if str(host or "").strip().lower() not in LOOPBACK:
        return None
    login = _load()
    if not login:
        return None
    try:
        if int(port or 1883) != login["port"]:
            return None
    except (TypeError, ValueError):
        return None
    return login["username"], login["password"]
