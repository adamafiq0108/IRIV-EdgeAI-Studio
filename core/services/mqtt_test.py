# -*- coding: utf-8 -*-
"""
mqtt_test.py -- "is my topic right?", answered from the box: a test publish
that listens for its own message, and a short listen on a Subscribe node's
topic that shows what arrives and what the node would make of it.

Asked for on 2026-09-29 (Tok Aba): "is there any way to check whether my
topic publish is already correct or not ... and for subscribe when I put the
topic then please make it able to test also".

WHY A ROUND TRIP

MQTT tells a publisher almost nothing. A publish to a misspelt topic succeeds
exactly as well as one to the right topic -- the broker accepts both and
hands each to whoever happens to be subscribed, which may be nobody. So the
test SUBSCRIBES to the exact topic first, then publishes, and reports whether
its own message came back: that proves the broker, the login and the topic
string, which are the three things that break quietly. What it cannot prove
is that the PLC or dashboard at the other end subscribes to that topic, and
the answer says so -- MQTT has no way to ask who is listening.

WHAT IT MUST NOT DO

  * Disturb the app. Every test is its own client with its own client id
    (irivai-test-<random>); the detection app's subscribers never see it.
  * Leak a password. The login comes from the node as saved on the box, or
    from this box's own broker login (mqtt_broker.local.toml) for a loopback
    address -- the same rule as app/broker_login.py -- and never goes into a
    reply.
  * Linger. Every connection has a hard deadline and is torn down in a
    finally; a listen is at most LISTEN_MAX_S and a sweep drops stale ones.
  * Replace a retained value. The test publish is never retained, even when
    the node is; the reply says why.
  * Become an open relay. The host, port and topic come from what the box has
    SAVED for the node, never from the request -- the route only names a node.
"""

import json
import secrets
import socket
import threading
import time

#: How long a test publish waits for the broker, then for its own message.
CONNECT_WAIT_S = 3.0
ECHO_WAIT_S = 3.0
#: A listen's length: what the page asks for, clamped to this.
LISTEN_MAX_S = 10.0
LISTEN_KEEP_S = 60.0          # a finished listen stays readable this long
LISTEN_MAX_MESSAGES = 50
MAX_LISTENS = 4               # box-wide: every master shares the slots
PAYLOAD_SHOWN = 300           # characters of a payload shown back
TOPIC_MAX = 256
#: One test publish per user at a time, and not faster than this.
PUBLISH_GAP_S = 2.0

OTHER_SUBSCRIBERS = ("Other subscribers: unknown - MQTT does not tell anyone "
                     "who is listening.")

#: Tests replace this with a fake. Takes a client id, returns a paho-like
#: client (connect, loop_start/stop, subscribe, publish, disconnect,
#: username_pw_set, on_connect/on_subscribe/on_message).
CLIENT_FACTORY = None


class TestRefused(Exception):
    """A test that will not run. The message is the whole answer."""


# ---------------------------------------------------------------------------
# Topics and payloads
# ---------------------------------------------------------------------------

def publish_topic_problem(topic):
    """Why `topic` cannot be published to, or ""."""
    t = str(topic or "")
    if not t.strip():
        return "the node has no topic"
    if "+" in t or "#" in t:
        return ("the topic has a wildcard (+ or #) - those are for "
                "subscribing; a publish needs one exact topic")
    if "\x00" in t or len(t.encode("utf-8")) > TOPIC_MAX:
        return "the topic is too long or has a control character"
    return ""


def filter_problem(topic):
    """Why `topic` is not a valid subscription filter, or ""."""
    t = str(topic or "")
    if not t.strip():
        return "the node has no topic"
    if "\x00" in t or len(t.encode("utf-8")) > TOPIC_MAX:
        return "the topic is too long or has a control character"
    levels = t.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            return "# may only be the whole LAST level, like plant/#"
        if "+" in level and level != "+":
            return "+ must be a whole level, like plant/+/count"
    return ""


def _shown(data):
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data).decode("utf-8", "replace")
    else:
        text = str(data)
    return text if len(text) <= PAYLOAD_SHOWN else text[:PAYLOAD_SHOWN] + "..."


def _dig(payload, path):
    """app/functions.py _dig, duplicated on purpose: core imports nothing from
    the app. smoke_mqtttest pins the two together."""
    cur = payload
    for part in [s for s in str(path or "").split(".") if s]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _as_number(value):
    """app/functions.py _as_number, duplicated on purpose (see _dig)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple, dict)):
        return float(len(value))
    return float(str(value).strip())


def _mqtt_number(value):
    """app/functions.py _mqtt_number, duplicated on purpose (see _dig): on
    MQTT an object or a list is not a number, so it is no reading at all
    rather than its length."""
    if isinstance(value, (list, tuple, dict)):
        raise ValueError("a JSON object or list is not a number - set the "
                         "value path to the field that holds it")
    return _as_number(value)


def preview(raw, json_path=""):
    """What an MQTT Subscribe node with this json_path makes of `raw`:
    {"value": float} or {"error": "..."} in words an operator can act on."""
    text = bytes(raw).decode("utf-8", "replace").strip() \
        if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
    try:
        doc = json.loads(text)
    except ValueError:
        doc = text
    path = str(json_path or "").strip()
    try:
        picked = _dig(doc, path)
    except (KeyError, IndexError, ValueError, TypeError):
        where = f"'{path}'" if path else "the payload"
        return {"error": f"{where} is not in this message"}
    if isinstance(picked, (list, tuple, dict)):
        return {"error": "a JSON object or list is not a number - set the "
                         "value path to the field that holds it"}
    try:
        return {"value": _mqtt_number(picked)}
    except (TypeError, ValueError):
        return {"error": f"not a number: {_shown(picked)[:60]!r}"
                         + ("" if path else " - set a JSON path if this is JSON")}


#: What a test publish sends when the node has no value yet. A plain word:
#: no MQTT Subscribe reads it as a number (it is not one), where a JSON
#: sample once read as its field count and could fire a rule.
SAMPLE_PAYLOAD = "test"
PAYLOAD_MAX = 1000


def publish_payload(doc, value):
    """The body the node would send for `value` (outputs.py _payload), or
    SAMPLE_PAYLOAD when the node has no value yet."""
    if doc.get("type") == "mqtt":                 # the retired alert node
        return str(doc.get("payload") or "1")
    if value is None:
        return SAMPLE_PAYLOAD
    if doc.get("format") == "json":
        return json.dumps({"key": doc.get("key"), "value": value,
                           "ts": round(time.time(), 3)})
    return str(value)


def payload_problem(text):
    """Why a payload typed into the test row cannot be sent, or ""."""
    if not isinstance(text, str):
        return "the payload must be text"
    if len(text) > PAYLOAD_MAX:
        return f"the payload is over {PAYLOAD_MAX} characters"
    if "\x00" in text:
        return "the payload has a NUL character"
    return ""


def topic_matches(flt, topic):
    """MQTT filter matching: + is one level, # is the rest (and the parent)."""
    fl, tl = str(flt or "").split("/"), str(topic or "").split("/")
    for i, part in enumerate(fl):
        if part == "#":
            return True
        if i >= len(tl) or (part != "+" and part != tl[i]):
            return False
    return len(fl) == len(tl)


def _norm(host):
    """A broker address as compared: lower case, no brackets, no trailing
    dot, and "" / 0.0.0.0 read as this box (the app's own default)."""
    h = str(host or "").strip().lower().strip("[]").rstrip(".")
    return "127.0.0.1" if h in ("", "0.0.0.0", "::") else h


#: This box's own names and addresses, cached: reading them is a syscall or
#: two and a test publish asks once per click.
_BOX_TTL_S = 60.0
_box = {"at": 0.0, "names": frozenset()}


def box_addresses(now=None):
    """Every way this box can be named as a broker host: loopback, its
    hostname and hostname.local, and each of its own IP addresses.

    Yaya (review of 6061106): a Subscribe pointed at the box's LAN address
    and a Publish pointed at 127.0.0.1 are the same broker, and a test that
    called them different would miss exactly the subscriber it exists to
    warn about."""
    now = time.monotonic() if now is None else now
    if _box["names"] and now - _box["at"] < _BOX_TTL_S:
        return _box["names"]
    names = {"127.0.0.1", "localhost", "::1"}
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    for n in {host, host.split(".")[0]} - {""}:
        names.update({_norm(n), _norm(n) + ".local"})
        try:
            for info in socket.getaddrinfo(n, None):
                names.add(_norm(info[4][0]))
        except OSError:
            pass
    try:
        names.add(_norm(socket.getfqdn()))
    except OSError:
        pass
    # Every interface's address, not only what the hostname resolves to
    # (which on a Pi is often just 127.0.1.1). Linux only; best effort.
    import shutil                                            # noqa: PLC0415
    import subprocess                                        # noqa: PLC0415
    if shutil.which("hostname"):
        try:
            out = subprocess.run(["hostname", "-I"], capture_output=True,
                                 text=True, timeout=2).stdout
            names.update(_norm(x) for x in out.split())
        except (OSError, subprocess.SubprocessError):
            pass
    _box.update(at=now, names=frozenset(names))
    return _box["names"]


def _same_broker(h1, p1, h2, p2, own=None):
    """Are host:port pairs the same broker? The port must match; the host
    matches when it is the same name, or both name THIS box."""
    try:
        if int(p1 or 1883) != int(p2 or 1883):
            return False
    except (TypeError, ValueError):
        return False
    a, b = _norm(h1), _norm(h2)
    if a == b:
        return True
    own = box_addresses() if own is None else own
    return a in own and b in own


def own_subscribers(functions, graph, host, port, topic, system=""):
    """The names of one system's MQTT Subscribe nodes that would receive a
    message published to host:port/topic -- the ones a test could set off.
    `system` names the system after the node when it is not the caller's."""
    names = {str((n.get("data") or {}).get("fnId") or ""):
             str((n.get("data") or {}).get("name") or "MQTT Subscribe")
             for n in (graph or {}).get("nodes") or []
             if n.get("type") == "variable.mqtt"}
    own = box_addresses()
    out = []
    for f in functions or []:
        if f.get("type") != "mqtt_sub" or f.get("enabled") is False:
            continue
        p = f.get("params") or {}
        if _same_broker(host, port, p.get("host") or "127.0.0.1", p.get("port"), own) \
                and topic_matches(p.get("topic"), topic):
            name = names.get(str(f.get("id") or "")) or f.get("name") \
                or f.get("id") or "MQTT Subscribe"
            out.append(f"{name} (in {system})" if system else name)
    return out


def box_subscribers(systems, host, port, topic):
    """own_subscribers over EVERY system on this box: `systems` is
    [(slug, label, functions, graph)], the caller's own slug first and
    unlabelled. Yaya: a Subscribe in another system on the same broker is
    set off just the same."""
    out = []
    for i, (_slug, label, functions, graph) in enumerate(systems or []):
        out += own_subscribers(functions, graph, host, port, topic,
                               "" if i == 0 else label)
    return out


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

def _client(client_id):
    if CLIENT_FACTORY is not None:
        return CLIENT_FACTORY(client_id)
    try:
        import paho.mqtt.client as mqtt                       # noqa: PLC0415
    except ImportError:
        raise TestRefused("paho-mqtt is not installed on this box - re-run "
                          "the installer so the MQTT nodes can work") from None
    version = getattr(mqtt, "CallbackAPIVersion", None)
    if version is not None:                                  # paho 2.x
        return mqtt.Client(version.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)


def _rc(value):
    """A CONNACK code as an int, from paho 1.x (int) or 2.x (ReasonCode)."""
    try:
        return int(getattr(value, "value", value))
    except (TypeError, ValueError):
        return -1


def _refusal(rc):
    # 4/5 in MQTT 3.1.1, 134/135 in MQTT 5.
    if rc in (4, 5, 134, 135):
        return ("the broker refused the login - check the username and "
                "password (for this box's broker: Settings > MQTT Broker)")
    return f"the broker refused the connection (code {rc})"


def _open(host, port, login, client_id, on_message=None):
    """A connected client, or TestRefused. The caller must _close() it."""
    client = _client(client_id)
    state = {"rc": None}
    connected = threading.Event()
    subscribed = threading.Event()

    def on_connect(*args):
        state["rc"] = _rc(args[3]) if len(args) > 3 else 0
        connected.set()

    client.on_connect = on_connect
    client.on_subscribe = lambda *a: subscribed.set()
    if on_message is not None:
        client.on_message = on_message
    if login:
        client.username_pw_set(login[0], login[1] or None)
    where = f"{host}:{port}"
    try:
        client.connect(host, int(port), keepalive=15)
    except ConnectionRefusedError:
        raise TestRefused(f"nothing is listening on {where} - is the broker "
                          "running? (Settings > MQTT Broker)") from None
    except (socket.timeout, TimeoutError):
        raise TestRefused(f"{where} did not answer - timed out") from None
    except (OSError, ValueError) as e:
        raise TestRefused(f"could not reach {where}: {e}") from None
    client.loop_start()
    if not connected.wait(CONNECT_WAIT_S):
        _close(client)
        raise TestRefused(f"{where} accepted the connection but never answered "
                          "as an MQTT broker - timed out")
    if state["rc"] != 0:
        _close(client)
        raise TestRefused(_refusal(state["rc"]))
    return client, subscribed


def _close(client):
    for step in (client.disconnect, client.loop_stop):
        try:
            step()
        except Exception:                                    # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Test publish
# ---------------------------------------------------------------------------

_pub_lock = threading.Lock()
_pub_busy = {}       # user -> (running, monotonic end)


def test_publish(host, port, topic, payload, login=None, retain=False,
                 user=""):
    """Subscribe to `topic`, publish `payload` to it, and watch for it.

    Returns {ok, connected, published, received, rtt_ms, sent, received_payload,
    notes, message}. Raises TestRefused for a test that could not run at all.
    """
    problem = publish_topic_problem(topic)
    if problem:
        raise TestRefused(problem)
    now = time.monotonic()
    with _pub_lock:
        running, ended = _pub_busy.get(user, (False, 0.0))
        if running or now - ended < PUBLISH_GAP_S:
            raise TestRefused("a test publish is already running - wait a "
                              "moment and try again")
        _pub_busy[user] = (True, 0.0)
    try:
        return _publish_once(host, port, topic, payload, login, retain)
    finally:
        with _pub_lock:
            # Remembered for PUBLISH_GAP_S after it ends: a double click is
            # one test, not two connections.
            _pub_busy[user] = (False, time.monotonic())


def _publish_once(host, port, topic, payload, login, retain):
    body = payload.encode("utf-8")
    echo = {"at": None, "payload": None, "others": 0}
    got = threading.Event()
    sent_at = [None]

    def on_message(_client, _userdata, msg):
        if msg.topic != topic:
            return
        if bytes(msg.payload) == body and echo["at"] is None and sent_at[0]:
            echo["at"] = time.monotonic()
            echo["payload"] = msg.payload
            got.set()
        else:
            echo["others"] += 1

    notes = []
    if retain:
        notes.append("The node publishes retained. This test was sent NOT "
                     "retained, so it does not replace the value the broker "
                     "keeps for this topic.")
    client, subscribed = _open(host, port, login,
                               "irivai-test-" + secrets.token_hex(4), on_message)
    try:
        client.subscribe(topic, qos=0)
        subscribed.wait(1.5)
        sent_at[0] = time.monotonic()
        info = client.publish(topic, body, qos=0, retain=False)
        published = _rc(getattr(info, "rc", 0)) == 0
        received = published and got.wait(ECHO_WAIT_S)
    finally:
        _close(client)
    out = {"ok": bool(received), "connected": True, "published": published,
           "received": bool(received), "rtt_ms": None, "sent": _shown(body),
           "received_payload": "", "notes": notes + [OTHER_SUBSCRIBERS]}
    if received:
        out["rtt_ms"] = round((echo["at"] - sent_at[0]) * 1000, 1)
        out["received_payload"] = _shown(echo["payload"])
        out["message"] = (f"Published to {topic} and received it back in "
                          f"{out['rtt_ms']} ms - the broker, the login and the "
                          "topic all work.")
    elif not published:
        out["message"] = "connected, but the broker did not accept the publish"
    else:
        out["message"] = (f"Published to {topic}, but it did not come back "
                          f"within {ECHO_WAIT_S:.0f} s. The broker may not allow "
                          "this login to subscribe to the topic (an ACL), or "
                          "the message was dropped.")
    if echo["others"]:
        out["notes"].append(f"{echo['others']} other message(s) arrived on this "
                            "topic meanwhile - something else publishes here too.")
    return out


# ---------------------------------------------------------------------------
# Listen (MQTT Subscribe)
# ---------------------------------------------------------------------------

_listen_lock = threading.Lock()
_listens = {}        # id -> Listen


class Listen:
    """One short subscription on its own client, collecting what arrives."""

    def __init__(self, owner, host, port, topic, login, json_path, seconds):
        self.id = secrets.token_hex(8)
        self.owner = owner                  # (user, node)
        self.topic = topic
        self.json_path = json_path
        self.seconds = max(1.0, min(LISTEN_MAX_S, float(seconds or LISTEN_MAX_S)))
        self.messages = []
        self.state = "connecting"           # connecting|listening|done|error
        self.error = ""
        self.started = time.time()
        self.ended = None
        self._stop = threading.Event()
        self._args = (host, port, login)
        self.slot = None                    # the box-wide slot it holds
        self._lock = threading.Lock()

    def _on_message(self, _client, _userdata, msg):
        with self._lock:
            if len(self.messages) >= LISTEN_MAX_MESSAGES:
                self._stop.set()
                return
            self.messages.append({"t": round(time.time(), 3), "topic": msg.topic,
                                  "payload": _shown(msg.payload),
                                  "retained": bool(getattr(msg, "retain", False)),
                                  **preview(msg.payload, self.json_path)})

    def run(self):
        host, port, login = self._args
        client = None
        try:
            client, subscribed = _open(host, port, login,
                                       "irivai-listen-" + secrets.token_hex(4),
                                       self._on_message)
            client.subscribe(self.topic, qos=0)
            subscribed.wait(1.5)
            self.state = "listening"
            self._stop.wait(max(0.0, self.seconds - (time.time() - self.started)))
            self.state = "done"
        except TestRefused as e:
            self.state, self.error = "error", str(e)
        except Exception as e:                                # noqa: BLE001
            self.state, self.error = "error", f"the listen failed ({type(e).__name__})"
        finally:
            if client is not None:
                _close(client)
            _release_slot(self.slot)
            self.slot = None
            self.ended = time.time()

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self._lock:
            msgs = list(self.messages)
        running = self.state in ("connecting", "listening")
        out = {"id": self.id, "state": self.state, "running": running,
               "error": self.error, "topic": self.topic,
               "json_path": self.json_path, "messages": msgs,
               "seconds": self.seconds,
               "elapsed": round((self.ended or time.time()) - self.started, 1)}
        if self.state == "done" and not msgs:
            out["hint"] = (f"Nothing arrived on {self.topic}. Publish something "
                           "to it - an Inject wired into an MQTT Publish, or on "
                           f"the box: mosquitto_pub -t '{self.topic}' -m 1")
        return out


def _sweep(now=None):
    now = time.time() if now is None else now
    for lid, lst in list(_listens.items()):
        if lst.ended and now - lst.ended > LISTEN_KEEP_S:
            _listens.pop(lid, None)
        elif now - lst.started > LISTEN_MAX_S + LISTEN_KEEP_S:
            lst.stop()
            _listens.pop(lid, None)


def _slot_dir(config_dir=None):
    from core import config                                  # noqa: PLC0415
    return config.root(config_dir) / ".mqtt-listen-slots"


def _claim_slot(folder):
    """One of MAX_LISTENS box-wide slots, or None when all are taken.

    The hub and every master are separate processes, each with its own
    _listens, so a count kept here would be a count per process. The slots
    are files in the platform's config directory -- the one place every
    master shares -- each held by an exclusive flock for as long as its
    listen runs. The kernel drops a flock when its holder dies, so a master
    that crashes mid-listen cannot leak a slot. Where flock does not exist
    (a developer's Windows checkout) this falls back to the in-process
    count, which is all there is to share there anyway."""
    try:
        import fcntl                                          # noqa: PLC0415
    except ImportError:
        return "local"
    import os                                                # noqa: PLC0415
    os.makedirs(folder, mode=0o700, exist_ok=True)
    for i in range(MAX_LISTENS):
        fd = os.open(os.path.join(folder, f"slot{i}"),
                     os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            continue
        return fd
    return None


def _release_slot(slot):
    if isinstance(slot, int):
        import os                                            # noqa: PLC0415
        try:
            os.close(slot)                  # closing the fd drops the flock
        except OSError:
            pass


def start_listen(owner, host, port, topic, login=None, json_path="",
                 seconds=LISTEN_MAX_S, run_async=True, config_dir=None):
    """Start a listen and return its snapshot. One per (user, node) at a time,
    MAX_LISTENS on the box across every master."""
    problem = filter_problem(topic)
    if problem:
        raise TestRefused(problem)
    with _listen_lock:
        _sweep()
        live = [x for x in _listens.values() if x.ended is None]
        for x in live:
            if x.owner == owner:
                raise TestRefused("already listening on this node - stop it "
                                  "or wait for it to finish")
        busy = TestRefused("too many listens are running on this box - try "
                           "again in a few seconds")
        slot = _claim_slot(_slot_dir(config_dir))
        if slot is None or (slot == "local" and len(live) >= MAX_LISTENS):
            raise busy
        lst = Listen(owner, host, port, topic, login, json_path, seconds)
        lst.slot = slot
        _listens[lst.id] = lst
    if run_async:
        threading.Thread(target=lst.run, name=f"mqtt-listen-{lst.id}",
                         daemon=True).start()
    else:
        lst.run()
    return lst.snapshot()


def get_listen(listen_id, owner_user):
    lst = _listens.get(str(listen_id or ""))
    if lst is None or lst.owner[0] != owner_user:
        raise TestRefused("that listen has finished or is not yours")
    return lst.snapshot()


def stop_listen(listen_id, owner_user):
    lst = _listens.get(str(listen_id or ""))
    if lst is None or lst.owner[0] != owner_user:
        raise TestRefused("that listen has finished or is not yours")
    lst.stop()
    return lst.snapshot()


# ---------------------------------------------------------------------------
# Whose login
# ---------------------------------------------------------------------------

LOOPBACK = ("", "127.0.0.1", "localhost", "::1")


def login_for(host, port, own=None, config_dir=None):
    """The login a test uses: the node's own when it names one, else this
    box's broker login for a loopback address on the broker's port."""
    if own and own[0]:
        return own
    if str(host or "").strip().lower() not in LOOPBACK:
        return None
    from core.services import mqtt_broker                    # noqa: PLC0415
    stored = mqtt_broker._stored(config_dir)
    try:
        same_port = int(port or 1883) == int(stored.get("port") or 1883)
    except (TypeError, ValueError):
        same_port = False
    if stored["username"] and same_port:
        return stored["username"], stored["password"]
    return None
