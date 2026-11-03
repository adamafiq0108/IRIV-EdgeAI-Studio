"""A user-given URL may reach another computer, never this box (Pipi M-R1).

A webhook pointed at http://127.0.0.1:9997 drove MediaMTX's control API, whose
run hooks execute commands as its account. core/services/netsafe.py (and its
byte-identical copy in the detection app) refuses this box's own addresses
for every user-given URL the box sends to: webhook, webhook_publish and the
API value poller. Pinned:

  1. the two copies are identical and give the same verdict on every case
  2. the table: loopback in every spelling, ::1, 0.0.0.0, mapped IPv6,
     link-local and metadata, this box's own IP and hostname (and .local),
     localhost., userinfo tricks, IPv6 zone ids -- refused; a public name and
     a private LAN address -- allowed
  3. save: the output validator and the API value validator refuse them with
     the plain sentence; a compile puts it on the node as an ERROR; reading a
     saved bad output drops it instead of crashing
  4. send: a name resolving to the box is refused; the connection goes to the
     address that was checked (a resolver answering 127.0.0.1 the SECOND time
     changes nothing); a redirect is never followed; public and LAN allowed;
     a refused webhook logs once and records a failure
  5. MQTT to the box's own broker (127.0.0.1:1883) is untouched

    py -3 tests/smoke_netsafe.py
"""
import importlib.util
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netsafe as core_ns                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


APP_PATH = os.path.join(ROOT, "systems", "demo", "app", "netsafe.py")
spec = importlib.util.spec_from_file_location("app_netsafe_copy", APP_PATH)
app_ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_ns)

# -- 1. two copies, one rule ------------------------------------------------------
check("the app's copy is byte-identical to core's",
      open(APP_PATH, "rb").read() == open(core_ns.__file__, "rb").read())

OWN = frozenset({"192.168.1.231", "fe80::3f40:9949:8869:a5af"})
HOST = "iriv-box"
DNS = {"public.example.com": ["93.184.216.34"], "lan-plc.example.com": ["192.168.1.50"],
       "sneaky.example.com": ["93.184.216.34", "127.0.0.1"],
       "metadata.example.com": ["169.254.169.254"], "boxname.example.com": ["192.168.1.231"]}


def fake_resolve(host, port):
    if host not in DNS:
        raise OSError("not found")
    return DNS[host]


CASES = [
    # (url, allowed?)
    ("http://127.0.0.1:9997/v3/config/paths/add/x", False),
    ("http://localhost/x", False),
    ("http://LOCALHOST./x", False),
    ("http://foo.localhost/x", False),
    ("http://[::1]:9997/", False),
    ("http://0.0.0.0:9997/", False),
    ("http://[::]/", False),
    ("http://[::ffff:127.0.0.1]/", False),
    ("http://127.1/", False),
    ("http://2130706433/", False),
    ("http://0x7f000001/", False),
    ("http://0177.0.0.1/", False),
    ("http://169.254.169.254/latest/meta-data/", False),
    ("http://100.100.100.200/", False),
    ("http://metadata.google.internal/", False),
    ("http://192.168.1.231:8080/", False),             # this box's LAN IP
    ("http://iriv-box/", False),                       # this box's hostname
    ("http://iriv-box.local:5000/", False),
    ("http://evil@127.0.0.1/", False),                 # userinfo trick
    ("http://user:pw@localhost:9997/", False),
    ("http://[fe80::1%25eth0]/", False),               # zone id, link-local
    ("http://[fe80::3f40:9949:8869:a5af]/", False),
    ("http://sneaky.example.com/", False),             # one bad answer is enough
    ("http://metadata.example.com/", False),
    ("http://boxname.example.com/", False),
    ("https://public.example.com/hook", True),
    ("http://lan-plc.example.com/", True),
    ("http://192.168.1.50:1880/in", True),             # a PLC / server on the LAN
    ("http://10.0.0.7/", True),
    ("http://172.16.4.2/", True),
    ("http://8.8.8.8/", True),
]


def verdict(mod, url):
    try:
        mod.check(url, resolve=fake_resolve, own=OWN, hostname=HOST)
        return True
    except mod.Blocked:
        return False


wrong, differ = [], []
for url, allowed in CASES:
    a, b = verdict(core_ns, url), verdict(app_ns, url)
    if a != b:
        differ.append(url)
    if a != allowed:
        wrong.append((url, a))
check("both copies agree on every case", not differ, differ)
check(f"the table: {len(CASES)} cases, loopback / own / link-local / metadata refused, "
      "public and LAN allowed", not wrong, wrong)
try:
    core_ns.check("http://127.0.0.1:9997/", own=OWN, hostname=HOST)
    msg = ""
except core_ns.Blocked as exc:
    msg = str(exc)
check("the refusal reads as a plain sentence",
      msg == "Webhook addresses on this device itself are not allowed. Use an "
             "address of another computer.", msg)
check("not http(s): refused", not verdict(core_ns, "file:///etc/passwd")
      and not verdict(core_ns, "gopher://x/"))
check("this box's real own addresses are found (best effort, not empty here)",
      isinstance(core_ns.own_addresses(), frozenset))

# -- 3. save -----------------------------------------------------------------------
from core.services import detector_control as dc                # noqa: E402
from core.services import functions as fns                       # noqa: E402
from core.services import workflow as wf                         # noqa: E402


def refused(url, kind="webhook"):
    try:
        dc._validate_output({"id": "w", "type": kind, "url": url, "trigger": "alert"})
        return ""
    except ValueError as exc:
        return str(exc)


check("a webhook to the box is refused at save, in words",
      refused("http://127.0.0.1:9997/v3/config/paths/add/x").startswith(
          "Webhook addresses on this device itself are not allowed"))
check("...and a Publish to HTTP too", "not allowed" in refused("http://localhost/x",
                                                                 "webhook_publish"))
check("...while a public and a LAN address save", not refused("https://hooks.example.com/a")
      and not refused("http://192.168.1.50/in"))
check("...and saving never waits on DNS (a name is judged at send time)",
      not refused("http://does-not-exist.invalid/x"))
try:
    fns.validate_function({"id": "a1", "type": "api_poll", "name": "A",
                           "params": {"url": "http://127.0.0.1:9997/v3/paths/list"}})
    api_msg = ""
except ValueError as exc:
    api_msg = str(exc)
check("an API value reading the box is refused at save", "not allowed" in api_msg, api_msg)

g = wf.clean_graph({"nodes": [
    {"id": "s", "type": "variable.schedule", "position": {"x": 0, "y": 0},
     "data": {"fnId": "clock", "name": "Clock", "every": "daily", "at": "22:00"}},
    {"id": "h", "type": "output.webhook", "position": {"x": 1, "y": 0},
     "data": {"url": "http://127.0.0.1:9997/v3/config/paths/add/x", "method": "POST"}}],
    "edges": [{"id": "e", "source": "s", "target": "h"}]})
c = wf.compile_graph(g, [])
check("a saved webhook to the box is a compile ERROR on its node",
      any(e.get("node") == "h" and "not allowed" in e["message"] for e in c["errors"])
      and not any(o.get("id") == "wf_h" for o in c["outputs"]), c["errors"])
dc._merged = lambda pkg: {"outputs": [{"id": "old", "type": "webhook", "trigger": "alert",
                                       "url": "http://127.0.0.1:9997/x"},
                                      {"id": "ok", "type": "webhook", "trigger": "alert",
                                       "url": "https://hooks.example.com/a"}]}
dc._pkg_dir = lambda systems_dir, slug: "x"
check("an already-saved one is dropped when read, never a crash",
      [o["id"] for o in dc.get_outputs("systems", "demo")] == ["ok"])

# -- 4. send -------------------------------------------------------------------------
connected = []


class FakeConn:
    status = 200

    def __init__(self, ip, host, port, timeout, *a):
        connected.append((ip, host, port))

    def request(self, method, path, body=None, headers=None):
        self.sent = (method, path, body, headers)

    def getresponse(self):
        st = self.status

        class R:
            status = st

            def read(self, n):
                return b"{}"
        return R()

    def close(self):
        pass


class Redirect(FakeConn):
    status = 302


lookups = []


def rebinding(host, port):
    lookups.append(host)
    return ["93.184.216.34"] if len(lookups) == 1 else ["127.0.0.1"]


for mod in (core_ns, app_ns):
    connected.clear()
    lookups.clear()
    mod._PinnedHTTP = FakeConn
    mod._PinnedHTTPS = FakeConn
    st, _b = mod.request("https://rebind.example.com/hook", method="POST", data=b"x",
                         resolve=rebinding, own=OWN)
    check(f"{mod.__name__}: DNS rebinding -- ONE lookup, and the connection goes to "
          "the address that was checked, with the URL's own name",
          st == 200 and lookups == ["rebind.example.com"]
          and connected == [("93.184.216.34", "rebind.example.com", 443)], (lookups, connected))
    mod._PinnedHTTP = Redirect
    try:
        mod.request("http://public.example.com/", resolve=fake_resolve, own=OWN)
        redirected = "followed"
    except mod.Blocked as exc:
        redirected = str(exc)
    check(f"{mod.__name__}: a redirect is never followed", "redirect" in redirected,
          redirected)
    mod._PinnedHTTP = FakeConn
    connected.clear()
    mod.request("http://192.168.1.50:1880/in", own=OWN)
    check(f"{mod.__name__}: a LAN address is sent to", connected
          and connected[0][0] == "192.168.1.50")
    connected.clear()
    try:
        mod.request("http://sneaky.example.com/", resolve=fake_resolve, own=OWN)
        sneaky = "sent"
    except mod.Blocked:
        sneaky = "refused"
    check(f"{mod.__name__}: a name with any address on the box is refused, nothing sent",
          sneaky == "refused" and not connected)

# The app's webhook: refused, recorded, one log line, never sent.
from app import outputs                                          # noqa: E402
import logging                                                   # noqa: E402
buf = io.StringIO()
logging.getLogger().addHandler(logging.StreamHandler(buf))
outputs.threading.Thread = lambda target, daemon=True: type("T", (), {"start": staticmethod(target)})()
recorded = []
outputs._record = lambda o, exc=None: recorded.append(str(exc) if exc else "ok")
outputs.netsafe._PinnedHTTP = FakeConn
outputs.netsafe._PinnedHTTPS = FakeConn
connected.clear()
for _ in range(3):
    outputs._webhook({"id": "wh_bad", "url": "http://127.0.0.1:9997/v3/config/paths/add/x"})
check("the app refuses a webhook to the box: nothing sent, a failure recorded each time",
      not connected and len(recorded) == 3 and "not allowed" in recorded[0], recorded)
check("...and says so in the log ONCE", buf.getvalue().count("wh_bad") == 1, buf.getvalue())
from app import functions as app_fns                             # noqa: E402
try:
    app_fns._api_request({"url": "http://localhost:9997/v3/paths/list"})
    poll = "sent"
except Exception as exc:                                         # noqa: BLE001
    poll = str(exc)
check("the app's API value poller refuses the box too", "not allowed" in poll, poll)

# -- 5. MQTT to the box's own broker is untouched -------------------------------------
check("an MQTT Publish to 127.0.0.1:1883 still validates",
      dc._validate_output({"id": "m", "type": "mqtt_publish", "trigger": "any",
                           "host": "127.0.0.1", "port": 1883, "topic": "a/b",
                           "interval_s": 5, "key": "fn_x_value"}).get("type") == "mqtt_publish")

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
