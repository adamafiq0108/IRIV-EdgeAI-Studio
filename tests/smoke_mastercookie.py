# The hub forwards the operator's cookie to each master, over plain HTTP.
#
# On HTTPS the browser sends `__Secure-cytron_session`; a master reached over
# plain HTTP reads only `cytron_session` (core/routes/authtls.py), so the
# forwarded cookie got a 401 and the hub's Live Wall drew every system as
# "master unreachable". masters.for_master renames the secure cookie to the
# plain name for that one hop, and drops a plain pair beside it so a stale old
# HTTP sign-in cannot be read first.
#
# masters.py takes no framework imports, so this runs on a dev box as it is.
import io
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.services import auth, masters                         # noqa: E402

PLAIN, SECURE = auth.SESSION_COOKIE, auth.SECURE_COOKIE

# -- the two names here match the real ones ----------------------------------
assert masters._PLAIN == PLAIN and masters._SECURE == SECURE, (masters._PLAIN, masters._SECURE)

# -- the rename ---------------------------------------------------------------
assert masters.for_master(None) is None
assert masters.for_master("") == ""
assert masters.for_master(PLAIN + "=abc") == PLAIN + "=abc"       # plain HTTP: untouched
assert masters.for_master("theme=dark; " + PLAIN + "=abc") == "theme=dark; " + PLAIN + "=abc"
assert masters.for_master(SECURE + "=tok") == PLAIN + "=tok"
# a stale plain cookie beside the secure one is dropped, whichever comes first
assert masters.for_master(PLAIN + "=OLD; " + SECURE + "=NEW") == PLAIN + "=NEW"
assert masters.for_master(SECURE + "=NEW; " + PLAIN + "=OLD") == PLAIN + "=NEW"
# other cookies are kept, in order
assert masters.for_master("a=1; " + SECURE + "=NEW; b=2") == "a=1; " + PLAIN + "=NEW; b=2"
# a cookie whose NAME merely contains ours is not renamed or dropped
assert masters.for_master("x" + PLAIN + "=1; " + SECURE + "=NEW") == "x" + PLAIN + "=1; " + PLAIN + "=NEW"
print("== the secure cookie reaches a master under the plain name")

# -- through the real fetch_json / post_json against a master that only reads
#    the plain name ------------------------------------------------------------
seen = []


class FakeMaster(BaseHTTPRequestHandler):
    def _answer(self):
        jar = dict(p.strip().split("=", 1) for p in (self.headers.get("Cookie") or "").split(";") if "=" in p)
        seen.append(jar.get(PLAIN))
        ok = jar.get(PLAIN) == "NEW"
        body = b'{"ok": true, "rows": [1]}' if ok else b'{"ok": false}'
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), FakeMaster)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d" % srv.server_address[1]
try:
    got = masters.fetch_json(url, "/api/monitoring/live", SECURE + "=NEW")
    assert got and got.get("rows") == [1], got
    got = masters.fetch_json(url, "/api/monitoring/live", PLAIN + "=OLD; " + SECURE + "=NEW")
    assert got and got.get("rows") == [1], got
    posted = masters.post_json(url, "/api/x", {}, SECURE + "=NEW")
    assert posted and posted.get("ok") is True, posted
    # plain HTTP page: the cookie goes through exactly as before
    assert masters.fetch_json(url, "/api/monitoring/live", PLAIN + "=NEW")["rows"] == [1]
    # no session at all is still a refusal, not a crash
    assert masters.fetch_json(url, "/api/monitoring/live", None) is None
finally:
    srv.shutdown()
print("== fetch_json and post_json work for a master that reads only the plain cookie")
print("PASS")
