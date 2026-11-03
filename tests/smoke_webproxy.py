# The hub's /s/<slug>/ relay (core/services/webproxy.py, wsrelay.py), driven
# as the ASGI middleware it is, in front of a REAL master-like server.
#
# Reverse-proxy mode puts every system behind the hub (Tok Aba, 2026-10-07),
# so the relay must be a pipe that changes nothing the master relies on:
#   a. port mode: /s/... is the hub's own app, except a GET for a known
#      system's page (a stale proxy bookmark): 307 to that system's port
#   b. a request reaches the master with the prefix stripped, the browser's
#      Host and cookie kept, and X-Forwarded-For/-Proto/-Host/-Prefix SET
#      by the relay (a browser's own copies are dropped)
#   c. an upload streams through intact; a Location gets the prefix
#   d. a never-ending MJPEG-like stream flows chunk by chunk, and a browser
#      that leaves closes the master's side
#   e. the local-only workflow paths are refused (also percent-encoded),
#      and the master refuses a forwarded loopback request for them anyway
#   f. an unknown system is 404, a stopped one 502 with a sentence
#   g. a WebSocket upgrade through the hub is closed (1008) until a master
#      needs one (Pipi); the relay itself still works when called: accepted
#      only after the master's, text both ways, cookie and prefix carried
#   h. (Pipi) X-Forwarded-For from a local hop: its LAST entry, in the relay
#      and in net.client_ip; a raw path that does not itself start
#      /s/<slug>/ is 400; Starlette's absolute trailing-slash redirect keeps
#      the prefix; a device's text/html is a download; a tunnel targets the
#      master's own port
#   i. (.21, v1.0.27) a LAN visitor through the relay gets <base href> =
#      /s/<slug>/ from a master served the way master_server.main() serves
#      it -- REAL uvicorn, whose default proxy-header rewrite had turned the
#      relay's loopback peer into the LAN client, so prefix_of() said "/"
#
#     python tests/smoke_webproxy.py
import asyncio
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
for key in ("CYTRON_HUB_PORT", "CYTRON_HUB_PORT_SOURCE", "INVOCATION_ID"):
    os.environ.pop(key, None)

try:
    import uvicorn                                             # noqa: E402
    from fastapi import FastAPI, Request                       # noqa: E402
    from fastapi.responses import RedirectResponse, StreamingResponse  # noqa: E402
except ImportError:
    print("skip: fastapi/uvicorn not installed")
    sys.exit(77)

from core import config, net                                  # noqa: E402
from core.services import webproxy, wsrelay                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# -- the master stand-in -------------------------------------------------------
master = FastAPI()
STREAM = {"opened": 0, "closed": 0}


@master.get("/api/echo")
def echo(request: Request):
    return {"path": request.url.path, "query": request.url.query,
            "host": request.headers.get("host"),
            "cookie": request.headers.get("cookie"),
            "xff": request.headers.get("x-forwarded-for"),
            "proto": request.headers.get("x-forwarded-proto"),
            "fhost": request.headers.get("x-forwarded-host"),
            "prefix": request.headers.get("x-forwarded-prefix"),
            "local": net.local_caller(request)}


@master.post("/api/upload")
async def upload(request: Request):
    h, n = hashlib.sha256(), 0
    async for chunk in request.stream():
        h.update(chunk)
        n += len(chunk)
    return {"n": n, "sha": h.hexdigest()}


@master.get("/api/stream")
async def stream():
    # Async, so the master's own disconnect handling cancels it and the
    # finally runs the moment the relay hangs up.
    async def gen():
        STREAM["opened"] += 1
        try:
            i = 0
            while True:
                i += 1
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + bytes([i % 250]) * 64
                await asyncio.sleep(0.03)
        finally:
            STREAM["closed"] += 1
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@master.get("/api/slash")
def slash():
    return {"ok": True}


@master.get("/redir")
def redir():
    return RedirectResponse("/login?next=%2Fworkflow", status_code=307)


@master.get("/api/workflow/query-value")
def query_value(request: Request):
    return {"local": net.local_caller(request)}


MPORT = free_port()
server = uvicorn.Server(uvicorn.Config(master, host="127.0.0.1", port=MPORT,
                                       log_level="warning", lifespan="off"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", MPORT), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.05)


# -- i. masters that serve index.html like master_server.spa_catch_all ----------
from fastapi.responses import HTMLResponse                     # noqa: E402
from core.services import weblinks                             # noqa: E402

INDEX = os.path.join(tempfile.mkdtemp(), "index.html")
io.open(INDEX, "w", encoding="utf-8").write(
    '<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>')


def spa_master(slug):
    app = FastAPI()

    @app.get("/{full_path:path}")
    def page(request: Request, full_path: str):
        return HTMLResponse(weblinks.index_html(INDEX, weblinks.prefix_of(request, slug)))
    return app


def serve(app, **opts):
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                        log_level="warning", lifespan="off", **opts))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return port, srv


# Served as production serves it (master_server.main) and as v1.0.27 did.
SPA_PORT, spa_server = serve(net.asgi(spa_master("asri_test")), **net.UVICORN)
OLD_PORT, old_server = serve(spa_master("oldshape"))


# -- a WebSocket master: raw RFC 6455, so no ws package is needed ----------------
WS = {"headers": b"", "got": []}


async def ws_master(reader, writer):
    head = await reader.readuntil(b"\r\n\r\n")
    WS["headers"] = head
    key = [ln.split(b":", 1)[1].strip() for ln in head.split(b"\r\n")
           if ln.lower().startswith(b"sec-websocket-key:")][0]
    writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                 b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
                 + wsrelay.accept_for(key) + b"\r\n\r\n")
    await writer.drain()
    try:
        while True:
            fin, op, payload = await wsrelay.read_frame(reader)
            if op == wsrelay.CLOSE:
                writer.write(wsrelay.encode(wsrelay.CLOSE, payload, mask=False))
                await writer.drain()
                break
            WS["got"].append(payload)
            writer.write(wsrelay.encode(wsrelay.TEXT, b"echo:" + payload, mask=False))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    writer.close()


# -- the hub's side: config, the middleware, an ASGI driver ----------------------
CFG = tempfile.mkdtemp()
DEAD = free_port()
WSPORT = free_port()
io.open(os.path.join(CFG, "systems.toml"), "w", encoding="utf-8").write(
    'dir = "systems"\n[hub]\nurl = "http://127.0.0.1:5100"\n'
    f'[masters.demo]\nurl = "http://127.0.0.1:{MPORT}"\n'
    f'[masters.stopped]\nurl = "http://127.0.0.1:{DEAD}"\n'
    f'[masters.wsdemo]\nurl = "http://127.0.0.1:{WSPORT}"\n'
    f'[masters.asri_test]\nurl = "http://127.0.0.1:{SPA_PORT}"\n'
    f'[masters.oldshape]\nurl = "http://127.0.0.1:{OLD_PORT}"\n')


def set_mode(mode):
    io.open(os.path.join(CFG, "systems.local.toml"), "w", encoding="utf-8").write(
        f'[hub]\naccess = "{mode}"\n')
    config.load.cache_clear()


INNER = {"calls": 0}


async def inner(scope, receive, send):
    INNER["calls"] += 1
    await send({"type": "http.response.start", "status": 299, "headers": []})
    await send({"type": "http.response.body", "body": b"hub"})

mw = webproxy.HubProxy(inner, config_dir=CFG)


async def call(method, path, headers=(), body=b"", chunk=None, stop_after=None,
               query=b""):
    gone = asyncio.Event()
    parts = ([body[i:i + chunk] for i in range(0, len(body), chunk)] if chunk and body
             else [body])
    queue = list(parts)
    sent = []

    async def receive():
        if queue:
            part = queue.pop(0)
            return {"type": "http.request", "body": part, "more_body": bool(queue)}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "http.response.body" and not msg.get("more_body"):
            gone.set()
        if stop_after and sum(1 for m in sent if m.get("body")) >= stop_after:
            gone.set()

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": query,
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
             "client": ("192.168.1.50", 50123), "server": ("127.0.0.1", 5100)}
    await asyncio.wait_for(mw(scope, receive, send), 15)
    start = next((m for m in sent if m["type"] == "http.response.start"), {})
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start.get("status"), dict(start.get("headers") or []), data, sent


async def main():
    # a. port mode -------------------------------------------------------------
    set_mode("port")
    status, _, data, _ = await call("GET", "/s/nosuch/api/echo")
    check("port mode: /s/<unknown>/ goes to the hub's own app, untouched",
          status == 299 and data == b"hub" and INNER["calls"] == 1, (status, data))
    status, hdrs, _, _ = await call("POST", "/s/demo/api/echo")
    check("port mode: a POST to /s/<slug>/ is the hub's too (never redirected)",
          status == 299, status)
    status, hdrs, _, _ = await call("GET", "/s/asri_test/workflow", query=b"a=1",
                                    headers=[("Host", "192.168.1.21:8080")])
    check("port mode: a stale /s/<slug>/ bookmark on the hub's port goes to the "
          "system's own port", status == 307 and hdrs.get(b"location")
          == f"http://192.168.1.21:{SPA_PORT}/workflow?a=1".encode(), (status, hdrs))
    set_mode("proxy")
    status, _, data, _ = await call("GET", "/settings/network")
    check("proxy mode: a hub path still goes to the hub", status == 299, status)

    # b. headers ----------------------------------------------------------------
    status, hdrs, data, _ = await call(
        "GET", "/s/demo/api/echo", query=b"a=1",
        headers=[("Host", "192.168.1.234"), ("Cookie", "cytron_session=t1"),
                 ("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-Prefix", "/s/evil"),
                 ("Connection", "keep-alive")])
    seen = json.loads(data or b"{}")
    check("the master gets the path without the prefix, query kept",
          status == 200 and seen.get("path") == "/api/echo" and seen.get("query") == "a=1",
          (status, seen))
    check("...the browser's Host and cookie",
          seen.get("host") == "192.168.1.234" and seen.get("cookie") == "cytron_session=t1",
          seen)
    check("...and the relay's own X-Forwarded-*: the real client, never the browser's copy",
          seen.get("xff") == "192.168.1.50" and seen.get("proto") == "http"
          and seen.get("fhost") == "192.168.1.234" and seen.get("prefix") == "/s/demo", seen)
    check("a relayed request is NOT a local caller on the master (net.local_caller)",
          seen.get("local") is False, seen)

    # c. upload + Location ---------------------------------------------------------
    blob = os.urandom(3 * 1024 * 1024 + 17)
    status, _, data, _ = await call(
        "POST", "/s/demo/api/upload", body=blob, chunk=65536,
        headers=[("Host", "box"), ("Content-Length", str(len(blob))),
                 ("Content-Type", "application/octet-stream")])
    got = json.loads(data or b"{}")
    check("a 3 MB upload streams through intact",
          status == 200 and got.get("n") == len(blob)
          and got.get("sha") == hashlib.sha256(blob).hexdigest(), (status, got))
    status, hdrs, _, _ = await call("GET", "/s/demo/redir", headers=[("Host", "box")])
    check("a root-relative Location gets the prefix",
          status == 307 and hdrs.get(b"location") == b"/s/demo/login?next=%2Fworkflow",
          (status, hdrs.get(b"location")))

    # d. a stream that never ends ----------------------------------------------------
    t0 = time.time()
    status, hdrs, data, sent = await call("GET", "/s/demo/api/stream",
                                          headers=[("Host", "box")], stop_after=4)
    chunks = [m for m in sent if m.get("body")]
    check("the stream flows chunk by chunk (no buffering to the end)",
          status == 200 and len(chunks) >= 4 and b"--frame" in data
          and hdrs.get(b"content-type", b"").startswith(b"multipart/x-mixed-replace")
          and time.time() - t0 < 10, (status, len(chunks)))
    for _ in range(100):
        if STREAM["closed"] >= 1:
            break
        await asyncio.sleep(0.05)
    check("...and a browser that leaves closes the master's side",
          STREAM["opened"] == 1 and STREAM["closed"] == 1, STREAM)

    # e. local-only paths -------------------------------------------------------------
    for path in ("/s/demo/api/workflow/query-value", "/s/demo/api/workflow/query-csv",
                 "/s/demo//api/workflow/query-value"):
        status, _, data, _ = await call("GET", path, headers=[("Host", "box")])
        check(f"refused by the relay: {path}", status == 403, (status, data))
    check("refused() sees the decoded path the master will route",
          webproxy.refused("/api/workflow/query-value/")
          and not webproxy.refused("/api/workflow/query-values"))

    # f. unknown / stopped -------------------------------------------------------------
    status, _, data, _ = await call("GET", "/s/nosuch/api/echo")
    check("an unknown system is 404", status == 404, status)
    status, _, data, _ = await call("GET", "/s/Bad-Slug/x")
    check("a malformed slug is the hub's (not relayed)", status == 299, status)
    status, _, data, _ = await call("GET", "/s/stopped/api/echo")
    check("a stopped system is 502, in words",
          status == 502 and b"is not running" in data, (status, data))

    # g. WebSocket ----------------------------------------------------------------------
    srv = await asyncio.start_server(ws_master, "127.0.0.1", WSPORT)
    sent, inbox = [], asyncio.Queue()
    await inbox.put({"type": "websocket.connect"})
    await inbox.put({"type": "websocket.receive", "text": "hello"})
    echoed = asyncio.Event()

    async def receive():
        msg = await inbox.get()
        return msg

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "websocket.send":
            echoed.set()
            await inbox.put({"type": "websocket.disconnect", "code": 1000})

    scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
             "path": "/s/wsdemo/api/live", "raw_path": b"/s/wsdemo/api/live",
             "query_string": b"", "subprotocols": [],
             "headers": [(b"host", b"box"), (b"cookie", b"cytron_session=t2"),
                         (b"sec-websocket-key", b"x"), (b"upgrade", b"websocket")],
             "client": ("192.168.1.51", 1), "server": ("127.0.0.1", 5100)}
    shut = []

    async def shut_send(msg):
        shut.append(msg)
    await asyncio.wait_for(mw(scope, receive, shut_send), 5)
    check("through the hub, a WebSocket upgrade is closed with 1008 (not relayed)",
          shut == [{"type": "websocket.close", "code": 1008}] and WS["headers"] == b"", shut)
    while not inbox.empty():
        inbox.get_nowait()
    await inbox.put({"type": "websocket.connect"})
    await inbox.put({"type": "websocket.receive", "text": "hello"})
    await asyncio.wait_for(wsrelay.relay(scope, receive, send, WSPORT,
                                         webproxy.upstream_target(scope, "wsdemo"),
                                         webproxy.forward_headers(scope, "wsdemo")), 10)
    kinds = [m["type"] for m in sent]
    check("the relay (kept for later): accepted once the master accepted",
          kinds[:1] == ["websocket.accept"], kinds)
    check("...text goes both ways",
          WS["got"] == [b"hello"]
          and any(m.get("text") == "echo:hello" for m in sent), (WS["got"], sent))
    head = WS["headers"].lower()
    check("...with the cookie, the prefix and the master's path",
          b"get /api/live http/1.1" in head and b"cytron_session=t2" in head
          and b"x-forwarded-prefix: /s/wsdemo" in head
          and head.count(b"sec-websocket-key") == 1, head)
    srv.close()

    # h. Pipi's review -------------------------------------------------------------------
    hdrs = webproxy.forward_headers(
        {"headers": [(b"host", b"box"), (b"x-forwarded-for", b"6.6.6.6, 10.0.0.9")],
         "client": ("127.0.0.1", 1), "scheme": "http"}, "demo")
    check("a local hop's X-Forwarded-For: its LAST entry, the one it appended",
          dict(hdrs)[b"x-forwarded-for"] == b"10.0.0.9", hdrs)
    for raw in (b"/s/de%6do/api/echo", b"/s/demo%2Fapi/echo"):
        sc = {"type": "http", "path": "/s/demo/api/echo", "raw_path": raw}
        check(f"raw path {raw!r} that does not itself start /s/demo/ is refused",
              webproxy.upstream_target(sc, "demo") is None)
    status, _, _, _ = await call("GET", "/s/demo/api/echo", headers=[("Host", "box")])
    check("...and a well-formed one is relayed", status == 200, status)
    status, hdrs, _, _ = await call("GET", "/s/demo/api/slash/", headers=[("Host", "box")])
    check("Starlette's trailing-slash redirect (absolute URL) keeps /s/<slug>",
          status in (307, 308) and hdrs.get(b"location") == b"http://box/s/demo/api/slash",
          (status, hdrs.get(b"location")))
    check("...an absolute Location on another host is left alone",
          webproxy.response_headers([(b"location", b"https://github.com/x")], "demo", b"box")
          == [(b"location", b"https://github.com/x")])

    # i. the served <base href> under the relay, a LAN visitor ---------------
    lan = [("Host", "192.168.1.21")]
    for page in ("/s/asri_test/", "/s/asri_test/workflow", "/s/asri_test/login"):
        status, _, data, _ = await call("GET", page, headers=lan)
        check(f'relay, LAN visitor: {page} is served <base href="/s/asri_test/">',
              status == 200 and b'<base href="/s/asri_test/">' in data,
              (status, data[:120]))
    status, _, data, _ = await call("GET", "/s/oldshape/", headers=lan)
    check("...and the trap is real: uvicorn's default rewrite would have served \"/\"",
          status == 200 and b'<base href="/">' in data, (status, data[:120]))
    with socket.create_connection(("127.0.0.1", SPA_PORT), timeout=5) as conn:
        conn.sendall(b"GET / HTTP/1.0\r\nHost: 192.168.1.21:5112\r\n"
                     b"X-Forwarded-Prefix: /s/asri_test\r\n\r\n")
        got = b""
        while True:
            more = conn.recv(65536)
            if not more:
                break
            got += more
    check("a loopback caller with the header but no relay XFF still gets the prefix "
          "(the old rule, unchanged)", b'<base href="/s/asri_test/">' in got, got[-200:])

    # frame codec round trip (both masks, all three length forms)
    for n in (0, 125, 126, 70000):
        payload = os.urandom(n)
        for mask in (True, False):
            r = asyncio.StreamReader()
            r.feed_data(wsrelay.encode(wsrelay.BINARY, payload, mask=mask))
            fin, op, back = await wsrelay.read_frame(r)
            if not (fin and op == wsrelay.BINARY and back == payload):
                check(f"frame round trip n={n} mask={mask}", False)
                break
    check("frames round-trip at 0/125/126/70000 bytes, masked and not", True)


asyncio.run(main())

# -- net.client_ip, the device relay's content types, the tunnel's port --------------
from starlette.requests import Request                              # noqa: E402
from core.routes import proxy as device_proxy                        # noqa: E402
from core.routes import remote as remote_routes                      # noqa: E402


def peer_req(peer, xff=None):
    h = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "headers": h, "client": (peer, 1)})


check("client_ip: a LAN peer's own X-Forwarded-For is ignored",
      net.client_ip(peer_req("192.168.1.50", "6.6.6.6")) == "192.168.1.50")
check("client_ip: from loopback, the LAST entry",
      net.client_ip(peer_req("127.0.0.1", "6.6.6.6, 10.0.0.9")) == "10.0.0.9")
check("client_ip: never the header verbatim (a sentence is not an address)",
      net.client_ip(peer_req("127.0.0.1", "hello, world")) == "127.0.0.1")
check("a device's image/jpeg and the MJPEG multipart pass as they are",
      device_proxy._safe("image/jpeg") == ("image/jpeg", {})
      and device_proxy._safe("multipart/x-mixed-replace; boundary=frame")[1] == {})
check("a device's text/html is sent as an octet-stream download",
      device_proxy._safe("text/html; charset=utf-8")
      == ("application/octet-stream", {"Content-Disposition": "attachment"}))
os.environ["CYTRON_MASTER_PORT"] = "5110"
check("a tunnel targets the master's own port, not the browser's 80",
      remote_routes._own_port() == 5110)
for f in ("hubport", "hostname", "storage", "storage_setup", "remote", "boxrestart",
          "mqtt_broker", "logs", "log_errors", "devices", "folders", "settings"):
    src = open(os.path.join(ROOT, "core", "routes", f + ".py"), encoding="utf-8").read()
    if "net.client_ip(request)" not in src or 'headers.get("X-Forwarded-For"' in src:
        check(f"routes/{f}.py records net.client_ip", False)
        break
else:
    check("the audit rows' IPs come from net.client_ip in all twelve routes", True)
from core.services import hub as hub_service                         # noqa: E402
lan_addr = hub_service._host_of("http://127.0.0.1:5112")
card = {"url": "http://192.168.1.21/s/asri_test", "summary": f"{lan_addr} · AI running"}
hub_service.readdress(card, "http://127.0.0.1:5112")
check("proxy mode: a card's summary names http://<box>/s/<slug>, not the port",
      card["summary"] == "192.168.1.21/s/asri_test · AI running", card)
card = {"url": "http://192.168.1.21:5112", "summary": f"{lan_addr} · AI running"}
hub_service.readdress(card, "http://127.0.0.1:5112")
check("port mode: the summary keeps <lan>:<port>",
      card["summary"] == f"{lan_addr} · AI running", card)
src = open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
check("master_server.main() serves net.asgi(app) with net.UVICORN",
      "_net.asgi(create_app(" in src and "**_net.UVICORN" in src)
for srv_ in (server, spa_server, old_server):
    srv_.should_exit = True
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
