# The HTTPS card's routes (core/routes/https.py) behind the real session
# wall, and the session cookie over TLS (core/routes/auth.py).
#
#   a. no session: 401 for status, save, regenerate and the download
#   b. an operator and a site admin: 403 -- the card is master or developer
#   c. master: status (off, nothing generated), enable (a root appears),
#      download = the PUBLIC root only (PEM and DER, never a key), a new
#      root only with confirm, the regenerate cooldown is a 429, a port the
#      box needs is a 400
#   d. cookies: a sign-in over https sets __Secure-cytron_session with
#      Secure; over http the plain cookie without it (a plain-HTTP sign-in
#      is never blocked by an HTTPS one); each scheme reads ONLY its own
#      (Pipi L1: no fallback to a planted plain cookie on https); a sign-out
#      over https ends both cookies of the same account
#   e. CSRF over TLS (Pipi M3): an http:// page of the same host may not
#      write to the https side; an https page may; plain HTTP as before
#
#     python tests/smoke_httpsroutes.py
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
for key in ("CYTRON_HUB_PORT", "CYTRON_HUB_PORT_SOURCE", "INVOCATION_ID"):
    os.environ.pop(key, None)

try:
    from fastapi import FastAPI                                  # noqa: E402
    from cryptography import x509                                # noqa: E402
except ImportError:
    print("skip: fastapi or cryptography not installed")
    sys.exit(77)

from core.routes.auth import require as auth_require            # noqa: E402
from core.routes.auth import router as auth_router              # noqa: E402
from core.routes.https import router as https_router            # noqa: E402
from core.services import audit as audit_service                # noqa: E402
from core.services import auth as auth_service                  # noqa: E402
from core.services import hubport, https, tlsnames              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
audit_service.record = lambda *a, **k: None          # no database in a test
tlsnames.current = lambda: (["iriv-test.local", "iriv-test", "localhost"],
                            ["127.0.0.1", "192.168.1.234"])
hubport.owns_setting = lambda: False
auth_service.set_master_password("correct-horse-9", CFG)
MASTER = auth_service.admin_username(CFG)
auth_service.create_user("op", "operator-pass-1", "*", config_dir=CFG, role="operator")
auth_service.create_user("site", "siteadmin-pass-1", "*", config_dir=CFG, admin=True)

app = FastAPI()
app.state.config_dir = CFG
app.include_router(auth_router)
app.include_router(https_router)
app.middleware("http")(auth_require(None))


def call(method, path, scheme="http", headers=(), body=None, query=b""):
    """(status, {header: [values]}, body bytes) from one ASGI request."""
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = [(b"host", b"192.168.1.234:5100")] + [(k.lower().encode(), v.encode())
                                                 for k, v in headers]
    if body is not None:
        hdrs.append((b"content-type", b"application/json"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": scheme, "path": path, "raw_path": path.encode(),
             "query_string": query, "headers": hdrs, "client": ("192.168.1.50", 40000),
             "server": ("192.168.1.234", 5100), "root_path": ""}
    out = {"status": 0, "headers": {}, "body": b""}
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            for k, v in msg.get("headers") or []:
                out["headers"].setdefault(k.decode().lower(), []).append(v.decode("latin-1"))
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body") or b""
    asyncio.run(app(scope, receive, send))
    return out["status"], out["headers"], out["body"]


def login(user, password, scheme="http"):
    status, hdrs, body = call("POST", "/api/auth/login", scheme,
                              body={"username": user, "password": password})
    return status, hdrs.get("set-cookie", []), json.loads(body or b"{}").get("token")


def as_(token):
    return (("X-Session", token),)


ROUTES = (("GET", "/api/hub/https", None), ("POST", "/api/hub/https", {"enabled": True}),
          ("POST", "/api/hub/https/regenerate", {"what": "leaf"}),
          ("GET", "/api/hub/https/root.crt", None))

# -- a. no session ---------------------------------------------------------------
for method, path, body in ROUTES:
    st, _, _ = call(method, path, body=body)
    check(f"no session: {method} {path} -> 401", st == 401, st)

# -- b. not master -----------------------------------------------------------------
_, _, op = login("op", "operator-pass-1")
_, _, site = login("site", "siteadmin-pass-1")
for who, tok in (("operator", op), ("site admin", site)):
    for method, path, body in ROUTES:
        st, _, _ = call(method, path, headers=as_(tok), body=body)
        check(f"{who}: {method} {path} -> 403", st == 403, st)
check("...and nothing was generated by any of that", not os.path.exists(https.path(CFG)))

# -- c. master ---------------------------------------------------------------------
st, cookies, admin = login(MASTER, "correct-horse-9")
check("master signs in", st == 200 and admin, st)
st, _, body = call("GET", "/api/hub/https", headers=as_(admin))
d = json.loads(body)
check("status: on by default, port 8443, nothing generated until the listener starts, page not https",
      st == 200 and d["enabled"] is True and d["port"] == 8443 and d["root"] is None
      and d["page_https"] is False, d)
st, _, body = call("GET", "/api/hub/https/root.crt", headers=as_(admin))
check("download before enable: 404, not an empty file", st == 404, st)
st, _, body = call("POST", "/api/hub/https", headers=as_(admin), body={"enabled": True})
d = json.loads(body)
check("enable: on, a root with a fingerprint, a certificate naming the box",
      st == 200 and d["enabled"] and d["root"] and len(d["root"]["fingerprint"]) == 95
      and "192.168.1.234" in d["leaf"]["ips"], d)
check("...status never carries a key", b"PRIVATE" not in body)
st, hdrs, body = call("GET", "/api/hub/https/root.crt", headers=as_(admin))
check("download: the public root, PEM, as a CA certificate attachment",
      st == 200 and body.startswith(b"-----BEGIN CERTIFICATE-----") and b"PRIVATE" not in body
      and hdrs["content-type"][0].startswith("application/x-x509-ca-cert")
      and 'filename="iriv-edge-ai-root.crt"' in hdrs["content-disposition"][0], hdrs)
cert = x509.load_pem_x509_certificate(body)
check("...it is THE root (CA, fingerprint as on the card)",
      cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
      and cert.fingerprint(__import__("cryptography.hazmat.primitives.hashes",
                                      fromlist=["SHA256"]).SHA256()).hex().upper()
      == d["root"]["fingerprint"].replace(":", ""))
st, hdrs, der = call("GET", "/api/hub/https/root.crt", headers=as_(admin), query=b"format=der")
check("download ?format=der: the same root as DER (.cer)",
      st == 200 and x509.load_der_x509_certificate(der) == cert
      and "root.cer" in hdrs["content-disposition"][0])
st, _, body = call("GET", "/api/hub/https/root.crt", headers=as_(admin),
                   query=b"format=../../https.local.toml")
check("any other format is the PEM, never a file path", st == 200
      and body.startswith(b"-----BEGIN CERTIFICATE-----"))
st, _, body = call("POST", "/api/hub/https/regenerate", headers=as_(admin), body={"what": "root"})
check("a new root without confirm: 400, the root kept",
      st == 400 and b"confirm" in body
      and https.root_cert(CFG) == cert, body)
st, _, body = call("POST", "/api/hub/https/regenerate", headers=as_(admin),
                   body={"what": "root", "confirm": True})
check("a new root with confirm: a different fingerprint",
      st == 200 and json.loads(body)["root"]["fingerprint"] != d["root"]["fingerprint"], body)
st1, _, _ = call("POST", "/api/hub/https/regenerate", headers=as_(admin), body={"what": "leaf"})
st2, _, body = call("POST", "/api/hub/https/regenerate", headers=as_(admin), body={"what": "leaf"})
check("new certificate: once, then 429 inside the cooldown", st1 == 200 and st2 == 429,
      (st1, st2, body))
st, _, body = call("POST", "/api/hub/https", headers=as_(admin), body={"port": 5101})
check("a port the box needs: 400 with the reason", st == 400 and b"device agent" in body, body)
st, _, body = call("POST", "/api/hub/https", headers=as_(admin), body={"port": 80})
check("below 1024: 400", st == 400, body)
st, _, body = call("POST", "/api/hub/https", headers=as_(admin), body={"enabled": False})
check("disable: off, the root kept for the next time",
      st == 200 and not json.loads(body)["enabled"] and json.loads(body)["root"], body)

# -- d. cookies ----------------------------------------------------------------------
st, plain, _ = login(MASTER, "correct-horse-9", "http")
check("sign-in over http: cytron_session, not Secure",
      st == 200 and plain[0].startswith("cytron_session=") and "secure" not in plain[0].lower(),
      plain)
st, tls, tok = login(MASTER, "correct-horse-9", "https")
check("sign-in over https: __Secure-cytron_session, Secure, HttpOnly",
      st == 200 and tls[0].startswith("__Secure-cytron_session=")
      and "secure" in tls[0].lower() and "httponly" in tls[0].lower(), tls)
jar = "__Secure-cytron_session=" + tok
st, _, _ = call("GET", "/api/hub/https", "https", headers=(("Cookie", jar),))
check("over https the __Secure- cookie is the session", st == 200, st)
st, _, _ = call("GET", "/api/hub/https", "http", headers=(("Cookie", jar),))
check("over http it is not (a browser would never send it there)", st == 401, st)
st, _, _ = call("GET", "/api/hub/https", "https",
                headers=(("Cookie", "cytron_session=" + tok),))
check("a plain cookie is NOT the session on https (no fixation fallback)", st == 401, st)
_, _, ptok = login(MASTER, "correct-horse-9", "http")
both = jar + "; cytron_session=" + ptok
st, hdrs, _ = call("POST", "/api/auth/logout", "https", headers=(("Cookie", both),))
cookies = hdrs.get("set-cookie", [])
check("sign-out over https deletes the __Secure- cookie AND the plain one (same account)",
      st == 200 and any(c.startswith("__Secure-cytron_session=") and "Max-Age=0" in c
                        for c in cookies)
      and any(c.startswith("cytron_session=") and "Max-Age=0" in c for c in cookies), cookies)
check("...and both sessions are dead",
      call("GET", "/api/hub/https", "https", headers=(("Cookie", jar),))[0] == 401
      and call("GET", "/api/hub/https", "http",
               headers=(("Cookie", "cytron_session=" + ptok),))[0] == 401)

# -- e. CSRF over TLS ------------------------------------------------------------------
_, _, tok = login(MASTER, "correct-horse-9", "https")
same_http = (("Origin", "http://192.168.1.234:5100"),)
same_https = (("Origin", "https://192.168.1.234:8443"),)
st, _, body = call("POST", "/api/hub/https", "https", headers=as_(tok) + same_http,
                   body={"redirect_http": False})
check("https write from an http:// page of the same host: 403", st == 403, (st, body))
st, _, body = call("POST", "/api/hub/https", "https", headers=as_(tok) + same_https,
                   body={"redirect_http": False})
check("...from an https page: allowed", st == 200, (st, body))
st, _, body = call("POST", "/api/hub/https", "http", headers=as_(tok) + same_http,
                   body={"redirect_http": False})
check("plain http from the same host's http page: allowed as before", st == 200, (st, body))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
