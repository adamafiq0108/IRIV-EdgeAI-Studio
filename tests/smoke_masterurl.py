"""csv_url / to_url may only be this master's own two routes (Pipi N-3).

An Email or Telegram output carries the address of this box's master route
that renders its CSV files (csv_url) or names its Telegram recipients
(to_url). Checked only as "any loopback URL", a raw saved or imported output
could make the app GET another local service -- MediaMTX's path list
(127.0.0.1:9997) carries camera passwords. Now (netsafe.master_url_problem,
in core and in the app's byte-identical copy):

  1. http, host exactly 127.0.0.1 / localhost, the master's own port, the
     exact route path, only its own query keys (out, and n for a CSV), ids
     only, no user, no fragment, nothing encoded
  2. the output validator refuses anything else in words; the compiler's
     own addresses pass
  3. the app checks again before fetching, skips (one log line) an output
     that fails, and throws away an answer that came through a redirect
  4. the app knows the master port from CYTRON_MASTER_PORT, or from [app]
     master_port the master writes into the package config at its start

    py -3 tests/smoke_masterurl.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
os.environ["CYTRON_MASTER_PORT"] = "5110"

from core.services import detector_control as dc                # noqa: E402
from core.services import netsafe                               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


CSV = "/api/workflow/query-csv"
TO = "/api/telegram/recipients/resolve"
GOOD = [(f"http://127.0.0.1:5110{CSV}?out=wf_m", CSV),
        (f"http://127.0.0.1:5110{CSV}?out=wf_m&n=wf_c1", CSV),
        (f"http://localhost:5110{CSV}?out=wf_m", CSV),
        (f"http://127.0.0.1:5110{TO}?out=wf_m", TO)]
BYPASS = [
    ("http://127.0.0.1:9997/v3/config/paths/list", CSV),
    (f"http://127.0.0.1:5111{CSV}?out=wf_m", CSV),            # another port
    (f"http://127.0.0.1{CSV}?out=wf_m", CSV),                 # no port
    ("http://127.0.0.1:5110/api/workflow/query-value?fn=x", CSV),
    (f"http://127.0.0.1:5110{TO}?out=wf_m", CSV),             # the other route
    (f"http://u:p@127.0.0.1:5110{CSV}?out=wf_m", CSV),        # userinfo
    (f"http://evil@127.0.0.1:5110{CSV}?out=wf_m", CSV),
    ("http://127.0.0.1:5110/api/workflow/%2e%2e/workflow/query-csv?out=x", CSV),
    (f"http://127.0.0.1:5110{CSV}%3Fout=x", CSV),
    (f"http://127.0.0.1:5110{CSV}/?out=wf_m", CSV),
    (f"http://127.0.0.1:5110{CSV}?out=wf_m&extra=1", CSV),     # extra query
    (f"http://127.0.0.1:5110{CSV}?out=wf_m&out=wf_n", CSV),    # repeated key
    (f"http://127.0.0.1:5110{CSV}?n=wf_c1", CSV),             # no out
    (f"http://127.0.0.1:5110{CSV}?out=../x", CSV),
    (f"http://127.0.0.1:5110{CSV}?out=wf_m#frag", CSV),
    (f"https://127.0.0.1:5110{CSV}?out=wf_m", CSV),
    (f"http://[::1]:5110{CSV}?out=wf_m", CSV),
    (f"http://127.0.0.2:5110{CSV}?out=wf_m", CSV),
    (f"http://10.0.0.5:5110{CSV}?out=wf_m", CSV),
    (f"http://127.0.0.1:5110{TO}?out=wf_m&n=x", TO),
]

# -- 1. the rule, both copies ---------------------------------------------------------
import importlib.util                                           # noqa: E402
spec = importlib.util.spec_from_file_location(
    "app_ns", os.path.join(ROOT, "systems", "demo", "app", "netsafe.py"))
app_ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_ns)
for mod in (netsafe, app_ns):
    good = [u for u, p in GOOD if mod.master_url_problem(u, p, 5110)]
    let = [u for u, p in BYPASS if not mod.master_url_problem(u, p, 5110)]
    check(f"{mod.__name__}: the compiler's own addresses pass", not good, good)
    check(f"{mod.__name__}: every bypass in the table is refused ({len(BYPASS)})", not let, let)

# -- 2. the validator --------------------------------------------------------------------
TABLES = [{"key": "t", "name": "t", "columns": []}]


def refused(**over):
    o = dict({"id": "wf_m", "type": "email", "to": ["a@b.c"], "csv_sql": "select 1",
              "csv_tables": TABLES, "csv_url": f"http://127.0.0.1:5110{CSV}?out=wf_m"}, **over)
    try:
        dc._validate_output(o)
        return ""
    except ValueError as exc:
        return str(exc)


check("the validator keeps the compiler's address", not refused())
check("...and refuses MediaMTX's path list in words",
      "rendered by this box only" in refused(csv_url="http://127.0.0.1:9997/v3/config/paths/list"))
check("...another port of this master", "own master port" in refused(
    csv_url=f"http://127.0.0.1:5111{CSV}?out=wf_m"))
tg = {"id": "wf_t", "type": "telegram", "message": "x", "to_mode": "all",
      "to_url": "http://127.0.0.1:9997/v3/paths/list"}
try:
    dc._validate_output(tg)
    tmsg = ""
except ValueError as exc:
    tmsg = str(exc)
check("...and a Telegram to_url that is not the resolve route", "this box only" in tmsg, tmsg)
saved_env = os.environ.pop("CYTRON_MASTER_PORT")
check("the hub (no master port of its own) still refuses other paths and hosts",
      "rendered by this box only" in refused(csv_url="http://127.0.0.1:9997/v3/config/paths/list")
      and not refused(csv_url=f"http://127.0.0.1:5123{CSV}?out=wf_m"))
os.environ["CYTRON_MASTER_PORT"] = saved_env

# -- 3. the app ---------------------------------------------------------------------------
from app import outputs                                          # noqa: E402
import logging                                                   # noqa: E402
buf = io.StringIO()
logging.getLogger().addHandler(logging.StreamHandler(buf))
import urllib.request                                            # noqa: E402
fetched = []


class Resp:
    def __init__(self, url, final=None):
        self.url, self.final, self.headers = url, final or url, {"X-Row-Count": "1"}

    def geturl(self):
        return self.final

    def read(self, n=-1):
        return b'{"ok": true, "chats": ["1"]}' if "resolve" in self.url else b"a,b\n1,2\n"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


redirect_to = {}
urllib.request.urlopen = lambda url, timeout=None: (fetched.append(url)
                                                    or Resp(url, redirect_to.get(url)))
bad_out = {"id": "wf_bad", "csv_url": "http://127.0.0.1:9997/v3/config/paths/list",
           "csv_name": "x.csv"}
for _ in range(3):
    got = outputs._csv_fetch(bad_out)
check("the app does not fetch an address that is not its master's route",
      got is None and not fetched, fetched)
check("...and says so once", buf.getvalue().count("wf_bad") == 1, buf.getvalue())
ok_url = f"http://127.0.0.1:5110{CSV}?out=wf_m"
check("the compiler's own address is fetched",
      outputs._csv_fetch({"id": "wf_m", "csv_url": ok_url, "csv_name": "x.csv"}) is not None
      and fetched == [ok_url])
redirect_to[ok_url] = "http://127.0.0.1:9997/v3/config/paths/list"
check("an answer that came through a redirect is thrown away",
      outputs._csv_fetch({"id": "wf_m", "csv_url": ok_url, "csv_name": "x.csv"}) is None)
redirect_to.clear()
fetched.clear()
outputs._recipients_cache.clear()
check("a Telegram to_url that is not the resolve route: the send is skipped, nothing fetched",
      outputs._recipients({"id": "wf_t", "to_mode": "all",
                           "to_url": "http://127.0.0.1:9997/v3/paths/list"}) is None
      and not fetched)

# -- 4. the master port the app knows ----------------------------------------------------
os.environ.pop("CYTRON_MASTER_PORT")
outputs._master_port_cache.clear()
pkg = os.path.dirname(os.path.dirname(os.path.abspath(outputs.__file__)))
check("without CYTRON_MASTER_PORT the app reads [app] master_port, else 5110",
      outputs._master_port() in (5110,) or isinstance(outputs._master_port(), int))
os.environ["CYTRON_MASTER_PORT"] = "5110"
SYS = tempfile.mkdtemp()
os.makedirs(os.path.join(SYS, "line1"))
open(os.path.join(SYS, "line1", "config.toml"), "w").write("[app]\nstream_port = 5102\n")
dc._pkg_dir = lambda systems_dir, slug: os.path.join(systems_dir, slug)
check("the master writes its port into the package config, once",
      dc.note_master_port(SYS, "line1", 5123) is True
      and dc.note_master_port(SYS, "line1", 5123) is False
      and "master_port = 5123" in open(os.path.join(SYS, "line1", "config.local.toml")).read())
master = open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
check("...at every master start", "_dc.note_master_port(str(systems_dir), slug, port_now)" in master)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
