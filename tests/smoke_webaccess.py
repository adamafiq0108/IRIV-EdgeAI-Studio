# Port or reverse proxy: the access mode as a setting, and every address that
# follows it (core/services/webaccess.py, weblinks.py, core/net.py).
#
# Tok Aba (2026-10-07): the Software port card is one choice of two -- "Use
# proxy (port 80)" or "Custom port" (today's behaviour, the default). The
# proxy puts the hub at http://<box>/ and every system at /s/<slug>/, all
# together. Pinned here:
#   a. port mode is exactly what it was: net.rewrite, the panel address, the
#      mDNS record, the restart's "open this next" all carry the port
#   b. Save stages, the NEXT boot applies; proxy is refused up front when
#      the box could not do it (helper, nft, port 80 taken, not the service)
#   c. the start-up redirect: helper asked with the bound port; a failure puts
#      the box back on ports and says why; port mode removes a stale one
#   d. proxy + live: cards, back links and created-system links lose the port
#      (systems become /s/<slug>), an old port in the Host is dropped, a
#      tunnel's is kept; proxy but NOT live: links stay port links
#   e. the master trusts X-Forwarded-Prefix only from loopback and only for
#      its own slug; index.html gets <base href> first in <head>
#   f. a page opened on an old port is redirected; API calls, the relay's
#      own requests and a tunnel are not
#   g. the card's routes: {"mode": "proxy"} tests and saves the mode, a port
#      body is the custom port as before
#
#     python tests/smoke_webaccess.py
import asyncio
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
for key in ("CYTRON_HUB_PORT", "CYTRON_HUB_PORT_SOURCE", "INVOCATION_ID"):
    os.environ.pop(key, None)

try:
    from starlette.requests import Request                    # noqa: E402
except ImportError:
    print("skip: starlette not installed")
    sys.exit(77)

from core import config, net                                  # noqa: E402
from core.services import (boxpending, discovery_mdns, hubport,  # noqa: E402
                           panel, webaccess, weblinks)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
io.open(os.path.join(CFG, "systems.toml"), "w", encoding="utf-8").write(
    'dir = "systems"\n[hub]\nurl = "http://127.0.0.1:5100"\n'
    '[masters.demo]\nurl = "http://127.0.0.1:5110"\n'
    '[masters.parking]\nurl = "http://127.0.0.1:5111"\n')
LOCAL = os.path.join(CFG, "systems.local.toml")
APP = types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG))


def local():
    return config.load_local("systems", CFG).get("hub") or {}


def set_local(text):
    io.open(LOCAL, "w", encoding="utf-8").write(text)
    config.load.cache_clear()


def req(host, path="/", peer="192.168.1.50", headers=(), query=b""):
    hdrs = [(b"host", host.encode())] + [(k.lower().encode(), v.encode())
                                          for k, v in headers]
    return Request({"type": "http", "method": "GET", "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": query,
                    "headers": hdrs, "client": (peer, 40000),
                    "server": ("127.0.0.1", 5100), "app": APP})


hubport.owns_setting = lambda: True
webaccess.can_run = lambda: True
webaccess.port80_listener = lambda paths=(): False
webaccess.nft_installed = lambda: True
weblinks._state.update(live=True, thread=True)     # no prober thread in a test

# -- a. port mode is unchanged ---------------------------------------------------
check("nothing set reads port mode", webaccess.mode(CFG) == "port")
check("a typo reads port mode", (set_local('[hub]\naccess = "proxxy"\n') or True)
      and webaccess.mode(CFG) == "port")
set_local("")
r = req("192.168.1.234:5100")
check("port mode: a card keeps its port",
      net.rewrite("http://127.0.0.1:5110", r) == "http://192.168.1.234:5110")
check("port mode: the hub link keeps its port",
      hubport.browser_url(r, CFG) == "http://192.168.1.234:5100")
check("port mode: the panel prints name:port",
      webaccess.panel_address("iriv-abc.local", CFG) == "iriv-abc.local:5100")
check("port mode: the mDNS record is what it was",
      webaccess.advertised_port(CFG) == 5100 and webaccess.txt_extra(CFG) == [])
after = boxpending.after(req("192.168.1.234:5100"), CFG)
check("port mode: after the restart, an address with the port",
      after["ip_urls"][0] == "http://192.168.1.234:5100" and after["proxy"] is False,
      after)

# -- b. stage / apply ---------------------------------------------------------------
check("Save proxy stages it, the mode does not move yet",
      webaccess.stage("proxy", "boot-1", CFG) == "proxy"
      and webaccess.mode(CFG) == "port" and local().get("pending_access") == "proxy")
st = boxpending.status(None, CFG)
check("...and the restart block says a restart waits for it",
      st["any"] and st["access"] == "proxy" and st["current"]["access"] == "port", st)
check("...the restart's next address has no port",
      boxpending.after(req("192.168.1.234:5100"), CFG)["ip_urls"][0]
      == "http://192.168.1.234")
check("the same boot applies nothing", webaccess.apply_pending("boot-1", CFG) is None)
res = webaccess.apply_pending("boot-2", CFG)
check("the next boot applies it", res == {"ok": True, "from": "port", "to": "proxy",
                                          "detail": ""} and webaccess.mode(CFG) == "proxy",
      res)
check("...and the pending value is gone", "pending_access" not in local())
check("saving the mode the box has cancels nothing waiting",
      webaccess.stage("proxy", "b", CFG) is None and not webaccess.pending(CFG))

set_local("")
webaccess.port80_listener = lambda paths=(): True
try:
    webaccess.stage("proxy", "b", CFG)
    check("port 80 taken: Save is refused", False)
except webaccess.AccessError as exc:
    check("port 80 taken: Save is refused, in words", "port 80" in str(exc), str(exc))
webaccess.port80_listener = lambda paths=(): False
webaccess.can_run = lambda: False
verdict = webaccess.check(CFG)
check("no helper: Test names it and the fix for this box (roothelp)",
      not verdict["usable"] and "web-redirect.py" in verdict["detail"]
      and ("maintenance step" in verdict["detail"] or "next software update"
           in verdict["detail"]), verdict)
webaccess.can_run = lambda: True
hubport.owns_setting = lambda: False
try:
    webaccess.stage("proxy", "b", CFG)
    check("not the box's own service: refused", False)
except webaccess.AccessError as exc:
    check("not the box's own service: refused", "not the box's own" in str(exc))
hubport.owns_setting = lambda: True
try:
    webaccess.stage("nginx", "b", CFG)
    check("an unknown mode is refused", False)
except webaccess.AccessError:
    check("an unknown mode is refused", True)

# a proxy saved, then the box can no longer do it at the restart
webaccess.stage("proxy", "b1", CFG)
webaccess.port80_listener = lambda paths=(): True
res = webaccess.apply_pending("b2", CFG)
check("refused at the restart: the old mode stays, the reason is kept",
      res["ok"] is False and webaccess.mode(CFG) == "port" and "port 80" in res["detail"],
      res)
webaccess.port80_listener = lambda paths=(): False

# -- c. the start-up redirect --------------------------------------------------------
calls = []


def runner(argv, input=None, **kw):
    calls.append((list(argv), input))
    return types.SimpleNamespace(returncode=runner.rc, stdout="", stderr=runner.err)


runner.rc, runner.err = 0, ""
set_local('[hub]\naccess = "proxy"\n')
check("proxy at start: the helper is asked for 80 -> the bound port",
      webaccess.ensure_redirect(5200, CFG, runner=runner) == ""
      and calls[-1] == (list(webaccess.HELPER_ARGV), '{"action": "on", "port": 5200}\n'),
      calls)
check("...through sudo -n and python3 -I, nothing on argv but the helper",
      webaccess.HELPER_ARGV == ("sudo", "-n", "/usr/bin/python3", "-I",
                                "/usr/local/lib/irivai/web-redirect.py"))
runner.rc, runner.err = 2, "web-redirect: another program already listens on port 80"
note = webaccess.ensure_redirect(5100, CFG, runner=runner)
check("a refused redirect puts the box back on ports and says why",
      webaccess.mode(CFG) == "port" and "already listens on port 80" in note
      and "port" in note, note)
calls.clear()
webaccess.redirect_set = lambda: True
runner.rc = 0
webaccess.ensure_redirect(5100, CFG, runner=runner)
check("port mode with a redirect still set: it is removed",
      calls and calls[-1][1] == '{"action": "off"}\n', calls)
calls.clear()
webaccess.redirect_set = lambda: False
webaccess.ensure_redirect(5100, CFG, runner=runner)
check("port mode with nothing set: no root call at all", calls == [], calls)
hubport.owns_setting = lambda: False
set_local('[hub]\naccess = "proxy"\n')
check("a second hub (not the service) never touches port 80",
      webaccess.ensure_redirect(5100, CFG, runner=runner) == "" and calls == [])
hubport.owns_setting = lambda: True

# -- d. links in proxy mode ------------------------------------------------------------
set_local('[hub]\naccess = "proxy"\n')
r = req("192.168.1.234")
check("a system's card becomes /s/<slug> on the same origin",
      net.rewrite("http://127.0.0.1:5110", r) == "http://192.168.1.234/s/demo")
check("...a path under it keeps its path",
      net.rewrite("http://127.0.0.1:5111/", r) == "http://192.168.1.234/s/parking")
check("the hub link is the origin, no port",
      hubport.browser_url(r, CFG) == "http://192.168.1.234")
check("an old port in the Host is dropped (an old bookmark)",
      net.rewrite("http://127.0.0.1:5110", req("iriv-x.local:5110"))
      == "http://iriv-x.local/s/demo")
check("a port that is not ours is kept (NAT, a tunnel on 8443)",
      net.rewrite("http://127.0.0.1:5110", req("site.example:8443"))
      == "http://site.example:8443/s/demo")
check("https from a TLS proxy in front is honoured",
      net.rewrite("", req("site.example", headers=[("X-Forwarded-Proto", "https")]),
                  default_port=5100) == "https://site.example")
check("a port that is neither the hub's nor a system's stays as it was",
      net.rewrite("http://127.0.0.1:9000", r) == "http://192.168.1.234:9000")
check("the panel prints the name alone, mDNS advertises 80 with access=proxy",
      webaccess.panel_address("iriv-abc.local", CFG) == "iriv-abc.local"
      and webaccess.advertised_port(CFG) == 80
      and "access=proxy" in webaccess.txt_extra(CFG))
weblinks._state["live"] = False
check("proxy set but NOT answering on 80: links stay port links",
      net.rewrite("http://127.0.0.1:5110", r) == "http://192.168.1.234:5110")
weblinks._state["live"] = True

# -- e. the prefix and the index ---------------------------------------------------------
via = [("X-Forwarded-Prefix", "/s/demo")]
check("the relay's prefix is trusted from loopback for our own slug",
      weblinks.prefix_of(req("box", peer="127.0.0.1", headers=via), "demo") == "/s/demo")
check("...not from the LAN", weblinks.prefix_of(req("box", headers=via), "demo") == "")
check("...not for another system's slug",
      weblinks.prefix_of(req("box", peer="127.0.0.1", headers=via), "parking") == "")
index = os.path.join(CFG, "index.html")
io.open(index, "w", encoding="utf-8").write(
    '<!doctype html><html><head><meta charset="utf-8"><script src="./assets/a.js">'
    '</script></head><body></body></html>')
page = weblinks.index_html(index, "/s/demo")
check("index.html: <base href> is the first thing in <head>",
      '<head><base href="/s/demo/"><meta' in page, page[:120])
check("...the hub (and a master on its port) gets /",
      '<head><base href="/"><meta' in weblinks.index_html(index, ""))
check("...a prefix that is not /s/<slug> is never written",
      '<base href="/">' in weblinks.index_html(index, '/s/x"><script>'))

# -- f. old ports redirect pages -----------------------------------------------------------
go = weblinks.direct_redirect(req("192.168.1.234:5110", "/workflow", query=b"a=1"),
                              "demo", 5110, CFG)
check("a page on a master's old port goes to /s/<slug>/<page>",
      go == "http://192.168.1.234/s/demo/workflow?a=1", go)
check("a page on the hub's old port goes to the origin",
      weblinks.direct_redirect(req("192.168.1.234:5100", "/settings"), None, 5100, CFG)
      == "http://192.168.1.234/settings")
check("the relay's own request is not redirected",
      weblinks.direct_redirect(req("192.168.1.234", "/x", peer="127.0.0.1", headers=via),
                               "demo", 5110, CFG) is None)
check("a tunnel (no port in Host) is answered where it is",
      weblinks.direct_redirect(req("abc.trycloudflare.com", "/x"), "demo", 5110, CFG)
      is None)
weblinks._state["live"] = False
check("not answering on 80: nothing is redirected",
      weblinks.direct_redirect(req("192.168.1.234:5110", "/x"), "demo", 5110, CFG) is None)
weblinks._state["live"] = True
set_local("")
check("port mode: nothing is redirected",
      weblinks.direct_redirect(req("192.168.1.234:5110", "/x"), "demo", 5110, CFG) is None)

# -- f2. the Hub decides, and nothing is left behind per system (Tok Aba) ---------------------
# Every system address is made from the box's mode when it is asked for: the
# Hub back on ports means every system's card, back link and redirect is on
# its port again, with no per-system state to clean up.
r = req("192.168.1.234")
set_local('[hub]\naccess = "proxy"\n')
on_proxy = [net.rewrite("http://127.0.0.1:5110", r), net.rewrite("http://127.0.0.1:5111", r),
            hubport.browser_url(r, CFG)]
set_local('[hub]\naccess = "port"\n')
on_ports = [net.rewrite("http://127.0.0.1:5110", r), net.rewrite("http://127.0.0.1:5111", r),
            hubport.browser_url(r, CFG)]
check("Hub on the proxy: every system on the proxy",
      on_proxy == ["http://192.168.1.234/s/demo", "http://192.168.1.234/s/parking",
                   "http://192.168.1.234"], on_proxy)
check("Hub back on ports: every system back on its own port, the same moment",
      on_ports == ["http://192.168.1.234:5110", "http://192.168.1.234:5111",
                   "http://192.168.1.234:5100"], on_ports)
check("...and no old-port page is redirected any more",
      weblinks.direct_redirect(req("192.168.1.234:5111", "/x"), "parking", 5111, CFG) is None)
check("...nothing about the access mode is stored per system",
      all("access" not in (e or {}) for e in
          (config.systems(CFG).get("masters") or {}).values()))
set_local("")

# -- g. the card's routes ---------------------------------------------------------------------
from core.routes import hubport as routes                       # noqa: E402

routes._require_owner = lambda r: None
routes._audit = lambda *a: None
boxpending.can_reboot = lambda: False


def body_req(payload):
    raw = json.dumps(payload).encode()
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/",
                    "raw_path": b"/", "query_string": b"",
                    "headers": [(b"host", b"192.168.1.234:5100"),
                                (b"content-type", b"application/json")],
                    "client": ("192.168.1.50", 1), "server": ("127.0.0.1", 5100),
                    "app": APP}, receive)


out = asyncio.run(routes.port_test(body_req({"mode": "proxy"})))
check("Test with mode proxy runs the port 80 checks",
      out["usable"] and {c["key"] for c in out["checks"]} == {"service", "nft", "helper",
                                                               "port80"}, out)
out = asyncio.run(routes.port_save(body_req({"mode": "proxy"})))
check("Apply with mode proxy stages the mode, not a port",
      out["access"]["pending"] == "proxy" and out["pending"] is None
      and out["restart"]["access"] == "proxy", out.get("access"))
out = asyncio.run(routes.port_save(body_req({"mode": "port", "port": 5100})))
check("Custom port with the current port cancels the pending proxy",
      out["access"]["pending"] is None and not out["restart"]["any"], out.get("restart"))
out = asyncio.run(routes.port_save(body_req({"port": 5200})))
check("a port-only body is the custom port, as before",
      out["pending"] == 5200 and out["access"]["mode"] == "port", out)

# -- the TXT record stays readable --------------------------------------------------------------
txt = discovery_mdns.txt_record("1.0", "hub", "abc", 5100) + ["access=proxy"]
check("TXT: the proxy keys ride after the existing ones",
      txt[:6] == discovery_mdns.txt_record("1.0", "hub", "abc", 5100))
check("panel.py reads its address from webaccess (no port of its own)",
      "webaccess.panel_address" in open(panel.__file__, encoding="utf-8").read())

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
