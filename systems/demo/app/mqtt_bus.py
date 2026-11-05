# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mqtt_bus.py -- one MQTT client per MQTT Connection, shared by every node.

An MQTT Connection ([[mqtt_clients]] in config.local.toml, set up in the
Workflow by core/services/mqtt_clients.py) is a broker and how to log in.
Before Connections, every MQTT Subscribe ran its own client and every MQTT
Publish opened a fresh TCP connection per message. Twelve nodes on one PLC
broker were twelve logins, and a publish every 0.2 s was five connects a
second. Now a Connection is ONE client in this app: every Subscribe adds its
topic to it, every Publish sends through it, and paho keeps it connected --
keep-alive, and reconnect with a backoff from the Connection's reconnect time
up to RECONNECT_CAP_S.

It starts on first use: a Connection no node uses never connects.

Not the test buttons' clients (core/services/mqtt_test.py): those run in the
MASTER, on their own client ids, and never touch these.

The login: the Connection's own username/password; for THIS box's broker
with no username, the platform's broker login (app/broker_login.py), re-read
whenever that file changes so a new password on the Broker page reconnects
every Connection to it.
"""

import collections
import os
import threading
import time

try:
    from . import broker_login
    from . import faults
except ImportError:                      # run as a loose script, not a package
    import broker_login
    import faults

log = faults.logger(__name__)

#: The longest wait between reconnect attempts, whatever the Connection's
#: reconnect time: a broker down for an hour is retried every half minute,
#: not every second, and not once an hour.
RECONNECT_CAP_S = 30

#: Messages published while a Connection is not connected (the first ones
#: after the app starts, any during a broker blip) wait here and go out on
#: connect, instead of being lost (Yaya, review of a34da37, M1): the old
#: publish.single connected per message and so delivered them. Bounded, and
#: aged: an alert from ten minutes ago delivered now would be a false one.
QUEUE_MAX = 100
QUEUE_MAX_AGE_S = 300

#: Tests set this: (client_id) -> a paho-like client.
CLIENT_FACTORY = None

_lock = threading.RLock()
_rows = {}                  # connection id -> its row from config
_conns = {}                 # connection id -> Conn (started ones only)


def topic_matches(flt, topic):
    """MQTT filter matching: + is one level, # is the rest (and the parent)."""
    fl, tl = str(flt or "").split("/"), str(topic or "").split("/")
    for i, part in enumerate(fl):
        if part == "#":
            return True
        if i >= len(tl) or (part != "+" and part != tl[i]):
            return False
    return len(fl) == len(tl)


def _login(row):
    user = str(row.get("username") or "")
    if user:
        return user, str(row.get("password") or "") or None
    return broker_login.for_address(row.get("host"), row.get("port"))


def _settings(row):
    """What makes a Connection need a new client when it changes."""
    return (str(row.get("host") or "127.0.0.1"), int(row.get("port") or 1883),
            _login(row), str(row.get("client_id") or ""),
            int(row.get("keepalive_s") or 60), int(row.get("reconnect_s") or 1))


def _client(client_id):
    if CLIENT_FACTORY is not None:
        return CLIENT_FACTORY(client_id)
    import paho.mqtt.client as mqtt                           # noqa: PLC0415
    version = getattr(mqtt, "CallbackAPIVersion", None)
    if version is not None:                                  # paho 2.x
        return mqtt.Client(version.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)


def _rc(value):
    try:
        return int(getattr(value, "value", value))
    except (TypeError, ValueError):
        return -1


class Conn:
    """One broker connection and the subscriptions riding on it."""

    def __init__(self, conn_id, row):
        self.id = conn_id
        self.row = dict(row)
        self.sig = _settings(row)
        self.client = None
        self.connected = False
        self.error = ""
        self.subs = {}          # handle -> (filter, callback)
        self._next = 0
        self.pending = collections.deque()   # (when, topic, payload, retain)
        self.dropped = 0                     # never silently: counted, logged

    @property
    def name(self):
        return str(self.row.get("name") or "") or \
            f"{self.row.get('host')}:{self.row.get('port')}"

    def client_id(self):
        # Blank = made up, unique per app: two apps (or two boxes) sharing a
        # broker under one id would knock each other off in a loop.
        given = str(self.row.get("client_id") or "").strip()
        return given or f"irivai-{os.getpid()}-{self.id}"

    def start(self):
        host, port, login, _cid, keepalive, reconnect = self.sig
        c = _client(self.client_id())
        if login:
            c.username_pw_set(login[0], login[1])
        try:
            c.reconnect_delay_set(min_delay=reconnect,
                                  max_delay=max(reconnect, RECONNECT_CAP_S))
        except (AttributeError, TypeError):
            pass

        def on_connect(*args):
            rc = _rc(args[3]) if len(args) > 3 else 0
            with _lock:
                self.connected = rc == 0
                self.error = "" if rc == 0 else (
                    "the broker refused the login" if rc in (4, 5, 134, 135)
                    else f"the broker refused the connection (code {rc})")
                filters = sorted({f for f, _cb in self.subs.values()})
            if rc == 0:
                for f in filters:
                    c.subscribe(f, qos=0)
                self.flush(c)
            else:
                log.warning("MQTT connection %s: %s", self.name, self.error)

        def on_disconnect(*args):
            with _lock:
                self.connected = False

        def on_message(_client, _userdata, msg):
            with _lock:
                targets = [cb for f, cb in self.subs.values()
                           if topic_matches(f, msg.topic)]
            for cb in targets:
                try:
                    cb(msg.topic, msg.payload)
                except Exception as exc:                     # noqa: BLE001
                    log.warning("MQTT connection %s: a subscriber failed: %s",
                                self.name, exc)

        c.on_connect = on_connect
        c.on_disconnect = on_disconnect
        c.on_message = on_message
        # Asynchronous: the loop thread connects, and reconnects, by itself.
        # Nothing here waits on a broker that may be down.
        c.connect_async(host, port, keepalive=keepalive)
        c.loop_start()
        self.client = c

    def stop(self):
        c, self.client = self.client, None
        self.connected = False
        if c is None:
            return
        for step in (c.disconnect, c.loop_stop):
            try:
                step()
            except Exception:                                # noqa: BLE001
                pass

    def queue(self, topic, payload, retain):
        """Hold one message until the broker answers. The OLDEST is dropped
        when the queue is full -- and counted and logged, never silently."""
        with _lock:
            if len(self.pending) >= QUEUE_MAX:
                self.pending.popleft()
                self._drop("the queue was full")
            self.pending.append((time.time(), topic, payload, bool(retain)))

    def _drop(self, why):
        self.dropped += 1
        if self.dropped == 1 or self.dropped % 100 == 0:
            log.warning("MQTT connection %s: %d message(s) not delivered (%s)",
                        self.name, self.dropped, why)

    def flush(self, client):
        """Send what waited, oldest first; what waited too long is dropped."""
        now = time.time()
        with _lock:
            waiting = list(self.pending)
            self.pending.clear()
        for when, topic, payload, retain in waiting:
            if now - when > QUEUE_MAX_AGE_S:
                with _lock:
                    self._drop(f"older than {QUEUE_MAX_AGE_S} s by the time "
                               "the broker answered")
                continue
            try:
                client.publish(topic, payload, qos=0, retain=retain)
            except Exception as exc:                         # noqa: BLE001
                with _lock:
                    self._drop(f"publish failed: {exc}")

    def subscribe(self, flt, callback):
        with _lock:
            self._next += 1
            handle = (self.id, self._next)
            known = any(f == flt for f, _cb in self.subs.values())
            self.subs[handle] = (flt, callback)
            live = self.connected and self.client is not None
        if live and not known:
            self.client.subscribe(flt, qos=0)
        return handle

    def unsubscribe(self, handle):
        with _lock:
            flt, _cb = self.subs.pop(handle, (None, None))
            still = any(f == flt for f, _cb2 in self.subs.values())
            live = self.connected and self.client is not None
        if flt is not None and live and not still:
            try:
                self.client.unsubscribe(flt)
            except Exception:                                # noqa: BLE001
                pass


def set_clients(rows):
    """Replace the Connection table from config ([[mqtt_clients]]). A
    Connection whose settings changed gets a new client -- its subscriptions
    move across -- and one that was deleted is closed."""
    table = {}
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            table[str(row["id"]).strip()] = dict(row)
    with _lock:
        _rows.clear()
        _rows.update(table)
        ids = list(_conns)
    for cid in ids:
        _refresh(cid)


def _refresh(cid):
    """Bring the started Connection `cid` in line with its row."""
    with _lock:
        conn = _conns.get(cid)
        row = _rows.get(cid)
    if conn is None:
        return
    if row is None:
        with _lock:
            _conns.pop(cid, None)
        conn.stop()
        return
    if _settings(row) != conn.sig:
        with _lock:
            subs = dict(conn.subs)
        conn.stop()
        fresh = Conn(cid, row)
        fresh.subs = subs
        fresh._next = conn._next
        fresh.pending = conn.pending          # waiting messages move across too
        fresh.dropped = conn.dropped
        with _lock:
            _conns[cid] = fresh
        fresh.start()


def _get(conn_id):
    """The started Conn for `conn_id`, starting it on first use, or
    LookupError when no such Connection exists (any more)."""
    cid = str(conn_id or "").strip()
    with _lock:
        row = _rows.get(cid)
        conn = _conns.get(cid)
    if row is None:
        raise LookupError(f"MQTT Connection {cid!r} does not exist - pick "
                          "another on the node")
    if conn is not None:
        if _settings(row) != conn.sig:       # e.g. the box broker's password
            _refresh(cid)
            with _lock:
                conn = _conns.get(cid)
        return conn
    conn = Conn(cid, row)
    with _lock:
        if cid in _conns:                     # raced with another first use
            return _conns[cid]
        _conns[cid] = conn
    conn.start()
    return conn


def subscribe(conn_id, flt, callback):
    """Add `flt` to Connection `conn_id`; callback(topic, payload_bytes) for
    each matching message. Returns a handle for unsubscribe()."""
    return _get(conn_id).subscribe(flt, callback)


def unsubscribe(handle):
    if not handle:
        return
    with _lock:
        conn = _conns.get(handle[0])
    if conn is not None:
        conn.unsubscribe(handle)


def publish(conn_id, topic, payload, retain=False):
    """Send one message through Connection `conn_id`: "sent", or "queued"
    while the broker is not connected (it goes out on connect, QUEUE_MAX /
    QUEUE_MAX_AGE_S permitting). Raises for a Connection that does not
    exist, or a broker that refused the message."""
    conn = _get(conn_id)
    if not conn.connected or conn.client is None:
        conn.queue(topic, payload, retain)
        return "queued"
    info = conn.client.publish(topic, payload, qos=0, retain=bool(retain))
    rc = _rc(getattr(info, "rc", 0))
    if rc != 0:
        raise RuntimeError(f"MQTT Connection '{conn.name}' did not take the "
                           f"message (code {rc})")
    return "sent"


def prestart(conn_ids):
    """Start the Connections the OUTPUTS use now, at app start and on every
    config load, so the first alert does not wait on a connect it triggers
    itself. A Connection that does not exist is logged, not raised."""
    for cid in sorted({str(c) for c in conn_ids or () if c}):
        try:
            _get(cid)
        except LookupError as exc:
            log.warning("MQTT: %s", exc)


def status(conn_id):
    with _lock:
        conn = _conns.get(str(conn_id or ""))
        return {"started": conn is not None,
                "connected": bool(conn and conn.connected),
                "error": conn.error if conn else "",
                "queued": len(conn.pending) if conn else 0,
                "dropped": conn.dropped if conn else 0}


def telemetry():
    """mqtt_<connection id>_ok / _error for every Connection this app knows,
    published with the counts (run.py) so the canvas can light a dot on each
    MQTT node: 1 = connected to its broker; 0 = not, and why ("" while the
    client is still connecting, "not in use" for a Connection no node here
    has started)."""
    with _lock:
        rows = list(_rows)
        conns = dict(_conns)
    out = {}
    for cid in rows:
        conn = conns.get(cid)
        ok = bool(conn and conn.connected)
        why = "" if ok else ((conn.error or "connecting") if conn else "not in use")
        out[f"mqtt_{cid}_ok"] = 1 if ok else 0
        out[f"mqtt_{cid}_error"] = str(why)[:120]
    return out


def close_all():
    with _lock:
        conns = list(_conns.values())
        _conns.clear()
    for c in conns:
        c.stop()
