# The HTTPS listener for real (core/tlsserve.py): a uvicorn server on a free
# port with the box's own certificate, and Python's ssl as the browser.
#
#   a. a client that trusts ONLY the box's root and asks for the box's name
#      completes the handshake and gets the app, which sees scheme https;
#      the start swept an old leftover key file (httpsrun.sweep, Pipi L4)
#   b. the same client asking for a name the certificate lacks is refused,
#      and so is the bare hostname (never certified, Pipi M1), a client
#      that does not trust the root, and TLS 1.1
#   c. a new address/name on the box: the very next handshake carries a
#      certificate naming it (the SNI callback re-issues, rate-limited)
#   d. "new certificate" from the card: the next connection gets it with
#      the listener still up; stop() closes the port
#   e. a damaged file: the listener stays down with a sentence, no crash
#   f. "Send browsers to HTTPS": browser page loads over HTTP from another
#      machine only; never an API call, a script, this box, or https itself
#
#     python tests/smoke_httpslive.py
import asyncio
import os
import socket
import ssl
import sys
import tempfile
import warnings

# The TLS 1.1 probe below is the point; Python's warning about it is not.
warnings.simplefilter("ignore", DeprecationWarning)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
for key in ("CYTRON_HUB_PORT", "CYTRON_HUB_PORT_SOURCE", "INVOCATION_ID"):
    os.environ.pop(key, None)

try:
    import uvicorn                                          # noqa: E402,F401
    import cryptography                                     # noqa: E402,F401
except ImportError:
    print("skip: uvicorn or cryptography not installed")
    sys.exit(77)

from core import tlsserve                                   # noqa: E402
from core.services import hubport, https, httpsrun, tlscert, tlsnames  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
NAMES = ["iriv-test.local", "iriv-test", "localhost"]
tlsnames.current = lambda: (list(NAMES), ["127.0.0.1", "::1"])
hubport.owns_setting = lambda: False          # no root helper, no port 443 here


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = scope["scheme"].encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def client(port, name, cadata=None, max_version=None):
    """(ok, scheme the app saw or the error, the server's certificate DER)."""
    ctx = ssl.create_default_context(cadata=cadata) if cadata else ssl.create_default_context()
    if max_version:
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        ctx.maximum_version = max_version
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=name) as tls:
                der = tls.getpeercert(binary_form=True)
                tls.sendall(b"GET / HTTP/1.1\r\nHost: " + name.encode() + b"\r\n"
                            b"Connection: close\r\n\r\n")
                data = b""
                while True:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    data += chunk
        return True, data.rsplit(b"\r\n\r\n", 1)[-1].decode(), der
    except (ssl.SSLError, OSError) as exc:
        return False, repr(exc), None


async def main():
    port = free_port()
    https._set(CFG, port=port)
    https.set_enabled(True, CFG)
    listener = tlsserve.Listener(app, CFG, "127.0.0.1")
    stale = os.path.join(CFG, ".https-load-crashed.pem")
    with open(stale, "w", encoding="ascii") as fh:
        fh.write("x")
    os.utime(stale, (1, 1))
    await listener.apply(at_start=True)
    check("the hub's start swept an old leftover key file", not os.path.exists(stale))
    rt = httpsrun.runtime()
    check("the listener is up on the configured port", rt["listening"] and rt["port"] == port, rt)
    root = tlscert.cert_pem(https.root_cert(CFG))

    # -- a / b ---------------------------------------------------------------------------
    ok, seen, der = await asyncio.to_thread(client, port, "iriv-test.local", root)
    check("trusting only the box's root, the box's name: handshake and the app",
          ok and seen == "https", seen)
    ok, seen, _ = await asyncio.to_thread(client, port, "localhost", root)
    check("...localhost too", ok, seen)
    ok, seen, _ = await asyncio.to_thread(client, port, "evil.example", root)
    check("a name the certificate lacks is refused",
          not ok and ("mismatch" in seen.lower() or "certificate" in seen.lower()), seen)
    ok, seen, _ = await asyncio.to_thread(client, port, "iriv-test", root)
    check("the bare hostname is refused (no root vouches for one)", not ok, seen)
    ok, seen, _ = await asyncio.to_thread(client, port, "iriv-test.local")
    check("a client that does not trust the root is refused", not ok, seen)
    ok, seen, _ = await asyncio.to_thread(client, port, "iriv-test.local", root,
                                          ssl.TLSVersion.TLSv1_1)
    check("TLS 1.1 is refused", not ok, seen)

    # -- c. a new name, picked up by the next handshake ----------------------------------
    NAMES.insert(0, "renamed-box.local")
    httpsrun._ctx["checked"] = None           # as if CHECK_EVERY_S had passed
    ok, seen, der2 = await asyncio.to_thread(client, port, "renamed-box.local", root)
    check("a new name: the very next handshake names it, listener untouched",
          ok and der2 != der and httpsrun.runtime()["listening"], seen)
    ok, _, der3 = await asyncio.to_thread(client, port, "renamed-box.local", root)
    check("...and the one after reuses it (no re-issue per connection)", ok and der3 == der2)

    # -- d. a new certificate from the card, then stop -----------------------------------
    https._state["last"]["leaf"] = None
    https.regenerate("leaf", CFG)
    ok, _, der4 = await asyncio.to_thread(client, port, "iriv-test.local", root)
    check("'new certificate': the next connection gets it, same root", ok and der4 != der3)
    https._set(CFG, enabled=False)
    await listener.apply()
    ok, seen, _ = await asyncio.to_thread(client, port, "iriv-test.local", root)
    check("switched off: the port is closed", not ok and not httpsrun.runtime()["listening"], seen)

    # -- e. a damaged file -----------------------------------------------------------------
    https._set(CFG, enabled=True)
    with open(https.path(CFG), "a", encoding="utf-8") as fh:
        fh.write("\n[ca\n")
    await listener.apply()
    st = httpsrun.status(CFG)
    check("a damaged file: no listener, a sentence, no crash",
          not httpsrun.runtime()["listening"] and "cannot be read" in st["error"], st["error"])

    # -- f. the HTTP -> HTTPS redirect ----------------------------------------------------
    https.ensure(CFG, new_root=True)
    https._set(CFG, enabled=True, redirect_http=True, port=port)
    await listener.apply()

    def scope(path="/", accept=b"text/html", peer="192.168.1.50", scheme="http",
              method="GET", extra=()):
        return {"type": "http", "scheme": scheme, "method": method, "path": path,
                "raw_path": path.encode(), "query_string": b"x=1",
                "client": (peer, 1234),
                "headers": [(b"host", b"192.168.1.234:5100"), (b"accept", accept),
                            *extra]}
    go = tlsserve.target(scope())
    check("a browser page over HTTP -> https on the HTTPS port, path and query kept",
          tlsserve.wants_https(scope()) and go == f"https://192.168.1.234:{port}/?x=1", go)
    check("...under /s/<slug>/ too", tlsserve.wants_https(scope("/s/demo/live")))
    for label, sc in (("an API call", scope("/api/hub/systems")),
                      ("a system's API through the relay", scope("/s/demo/api/x")),
                      ("the health check", scope("/api/health", accept=b"*/*")),
                      ("a script (no text/html)", scope("/", accept=b"*/*")),
                      ("a POST", scope(method="POST")),
                      ("this box's own kiosk", scope(peer="127.0.0.1")),
                      ("a request a local hop forwarded",
                       scope(extra=((b"x-forwarded-for", b"1.2.3.4"),))),
                      ("an image fetch (sec-fetch-mode no-cors)",
                       scope(extra=((b"sec-fetch-mode", b"no-cors"),))),
                      ("HTTPS itself (never a loop)", scope(scheme="https"))):
        check(f"not redirected: {label}", not tlsserve.wants_https(sc))
    bad_host = scope()
    bad_host["headers"][0] = (b"host", b"evil\"><x")
    check("a Host that is not a plain host gets no redirect", tlsserve.target(bad_host) is None)
    httpsrun.runtime(port443=True)
    check("with 443 redirected: no port in the address",
          tlsserve.target(scope()) == "https://192.168.1.234/?x=1")
    httpsrun.runtime(port443=False)
    https._set(CFG, enabled=False)
    await listener.apply()
    check("HTTPS switched off (the one switch): no redirect", tlsserve.target(scope()) is None)
    await listener.stop()
    check("stopped: no redirect to nothing", httpsrun.redirect_target("h", "/") is None)


asyncio.run(main())
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
