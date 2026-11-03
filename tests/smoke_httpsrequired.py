# HTTPS is the normal way in: while it answers, a sign-in, a session header or
# an API token sent over PLAIN HTTP from another machine is refused (426) --
# not redirected, because a redirect runs after the secret has already crossed
# the network -- and HTTPS responses pin browsers to HTTPS (HSTS). Nothing is
# refused while HTTPS is not actually answering, so a failed start locks
# nobody out. Loopback (the kiosk, the hub-to-master hop) is exempt.
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

try:
    from core import tlsserve                                   # noqa: E402
    from core.services import httpsrun                          # noqa: E402
except ImportError as exc:
    print(f"skip: {exc}")
    sys.exit(0)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def scope(path="/api/hub", method="GET", peer="192.168.1.50", scheme="http", headers=()):
    return {"type": "http", "scheme": scheme, "method": method, "path": path,
            "raw_path": path.encode(), "query_string": b"", "client": (peer, 1234),
            "headers": [(b"host", b"192.168.1.234:5100"), *headers]}


S = tlsserve.sends_secret
check("a session header over plain HTTP from the LAN is a secret",
      S(scope(headers=[(b"x-session", b"tok")])))
check("...a Bearer token too", S(scope(headers=[(b"authorization", b"Bearer mon_x")])))
check("...a sign-in, a token sign-in, a password change, a reset",
      all(S(scope(p, "POST")) for p in ("/api/auth/login", "/api/auth/monitor-token",
                                        "/api/auth/password", "/api/auth/reset")))
check("...through the /s/<slug>/ relay as well", S(scope("/s/demo/api/auth/login", "POST")))
check("not a secret: a plain GET, the health check, a sign-out, a page",
      not any(S(scope(p, m)) for p, m in (("/api/hub", "GET"), ("/api/health", "GET"),
                                          ("/api/auth/logout", "POST"), ("/", "GET"))))
check("not refused: this box itself (loopback), a request a local hop forwarded, HTTPS itself",
      not S(scope(headers=[(b"x-session", b"t")], peer="127.0.0.1"))
      and not S(scope(headers=[(b"x-session", b"t"), (b"x-forwarded-for", b"1.2.3.4")]))
      and not S(scope(headers=[(b"x-session", b"t")], scheme="https")))

# -- required(): only while HTTPS actually answers ----------------------------------
httpsrun.runtime(listening=False, redirect=True)
check("HTTPS not answering: nothing is required (no lockout)", httpsrun.required() is False)
httpsrun.runtime(listening=True, redirect=True)
check("HTTPS answering: required", httpsrun.required() is True)


async def run(sc):
    sent = []

    async def app(scope_, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    await tlsserve.HttpsRedirect(app)(sc, receive, send)
    return sent


async def main():
    out = await run(scope(headers=[(b"authorization", b"Bearer mon_x")]))
    start = out[0]
    check("the middleware refuses it with 426 and says to use https://",
          start["status"] == 426 and b"https://" in out[1]["body"]
          and (b"upgrade", b"TLS/1.2") in start["headers"], start)
    out = await run(scope("/api/auth/login", "POST"))
    check("...a sign-in over HTTP is refused before it is handled", out[0]["status"] == 426)
    out = await run(scope("/api/health"))
    check("...a health check over HTTP still answers (the update gate polls it)",
          out[0]["status"] == 200 and not any(h[0] == b"strict-transport-security"
                                              for h in out[0]["headers"]))
    out = await run(scope("/api/hub", scheme="https", headers=[(b"x-session", b"t")]))
    check("HTTPS responses carry HSTS", out[0]["status"] == 200
          and tlsserve.HSTS in out[0]["headers"], out[0])
    httpsrun.runtime(listening=False)
    out = await run(scope("/api/auth/login", "POST"))
    check("HTTPS not answering: the same request goes through, no HSTS",
          out[0]["status"] == 200)
    out = await run(scope("/api/hub", scheme="https"))
    check("...and an HTTPS response carries no HSTS", tlsserve.HSTS not in out[0]["headers"])


asyncio.run(main())
print()
if bad:
    print(f"{bad} FAILED")
    sys.exit(1)
print("all good")
