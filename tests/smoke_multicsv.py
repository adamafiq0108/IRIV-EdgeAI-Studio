"""Several CSV nodes into one message: every file attached (Tok Aba, 2026-10-14).

The pcb_sensata Email had two CSV nodes wired in (violation_cam1, violation_cam2)
and arrived with ONE attachment: the compiler took the first CSV wire and
dropped the rest. Now every CSV wired in rides on the message:

  1. compiled shape: 0 or 1 CSV wired compiles BYTE-IDENTICAL to before
     (golden_multicsv.json, captured before the change, Email and Telegram);
     2+ CSV keep the old csv_* fields from the FIRST file (an older app still
     attaches that one, exactly as before) and add `csvs`, one entry per file
     in wire order, each with its own fetch URL (?out=<output>&n=<csv node>)
  2. file names are unique on the message (a clash gets _2, _3 ...)
  3. the validator re-checks every entry like the single one (SELECT only,
     tables needed, this box only) and the TOML writer reads it back
  4. the master's query-csv route finds each file by output + node
  5. the app fetches and ATTACHES every file, fills {csv:name},
     {csv_text:name}, {csv_rows:name} per file and keeps {csv} / {csv_text}
     / {csv_rows} as the first file; a zero-row file says "(no rows)"
  6. Telegram posts one document per file, the message as the first caption

    py -3 tests/smoke_multicsv.py            (check)
    py -3 tests/smoke_multicsv.py --capture  (rewrite the golden; only before a change)
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
os.environ["CYTRON_MASTER_PORT"] = "5110"

from core.services import workflow as wf                     # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_multicsv.json")
TABLES = {"violation": {"key": "violation", "name": "Violation", "columns": [
    {"name": "cam", "type": "text"}, {"name": "total", "type": "number"}]}}

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def graph(n_csv, out="output.email", names=("violation_cam1", "violation_cam2")):
    nodes = [
        {"id": "t0", "type": "output.dataset", "position": {"x": 0, "y": 0},
         "data": {"dataset": "violation", "name": "violation", "mode": "change",
                  "sources": {"cam": {"src": "event", "field": "track_id"}}}},
        {"id": "s", "type": "variable.schedule", "position": {"x": 2, "y": 2},
         "data": {"fnId": "clock", "name": "Nightly", "every": "daily", "at": "22:00"}},
        {"id": "m", "type": out, "position": {"x": 6, "y": 0},
         "data": {"to": "a@b.c", "subject": "Report", "body": "see {csv}",
                  "chat_id": "123", "bot_token": "1:" + "x" * 24,
                  "message": "see {csv}"}},
    ]
    edges = [{"id": "e_s", "source": "s", "target": "m"}]
    for i in range(n_csv):
        q, c = f"q{i + 1}", f"c{i + 1}"
        nodes.append({"id": q, "type": "storage.query", "position": {"x": 2, "y": i},
                      "data": {"name": f"Query {i + 1}",
                               "sql": f"select cam, total from violation where cam = 'cam{i + 1}'"}})
        nodes.append({"id": c, "type": "storage.csv", "position": {"x": 4, "y": i},
                      "data": {"name": names[i], "max_rows": 0}})
        edges += [{"id": f"e_t{q}", "source": "t0", "target": q, "targetHandle": "table"},
                  {"id": f"e_{q}{c}", "source": q, "target": c},
                  {"id": f"e_{c}m", "source": c, "target": "m"}]
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def doc_of(n_csv, out="output.email", **kw):
    c = wf.compile_graph(graph(n_csv, out, **kw), [], tables=TABLES)
    return next(o for o in c["outputs"] if o["id"] == "wf_m"), c


CASES = [(n, out) for out in ("output.email", "output.telegram") for n in (0, 1, 2)]

if "--capture" in sys.argv:
    json.dump({f"{out}:{n}": doc_of(n, out)[0] for n, out in CASES},
              open(GOLDEN, "w", encoding="utf-8", newline="\n"), indent=1, sort_keys=True)
    print("captured", GOLDEN)
    sys.exit(0)

golden = json.load(open(GOLDEN, encoding="utf-8"))

# -- 1. compiled shape ----------------------------------------------------------
for n, out in CASES:
    doc, c = doc_of(n, out)
    want = golden[f"{out}:{n}"]
    if n < 2:
        check(f"{out} with {n} CSV compiles byte-identical to before",
              json.dumps(doc, sort_keys=True) == json.dumps(want, sort_keys=True),
              (sorted(set(doc) ^ set(want)), doc.get("csv_url")))
    else:
        old = {k: v for k, v in doc.items() if k != "csvs"}
        check(f"{out} with 2 CSV keeps every old field as the FIRST file (an older "
              "app attaches that one, as before)",
              json.dumps(old, sort_keys=True) == json.dumps(want, sort_keys=True),
              sorted(set(old) ^ set(want)))
        names = [e["name"] for e in doc.get("csvs") or []]
        check(f"...and adds csvs: both files, in wire order", names
              == ["violation_cam1.csv", "violation_cam2.csv"], doc.get("csvs"))
        urls = [e["url"] for e in doc.get("csvs") or []]
        check("...each with its own address on this box",
              urls == ["http://127.0.0.1:5110/api/workflow/query-csv?out=wf_m&n=wf_c1",
                       "http://127.0.0.1:5110/api/workflow/query-csv?out=wf_m&n=wf_c2"], urls)
        check("...its own statement and node",
              [e["node"] for e in doc["csvs"]] == ["wf_c1", "wf_c2"]
              and "cam2" in doc["csvs"][1]["sql"] and doc["csvs"][1]["tables"][0]["key"]
              == "violation", doc["csvs"][1])
        check("...and no warning", not c["warnings"] and not c["errors"],
              c["warnings"] + c["errors"])

# -- 2. unique names --------------------------------------------------------------
doc, _c = doc_of(2, names=("same", "same"))
check("two files with one name get a suffix, never one overwriting the other",
      [e["name"] for e in doc["csvs"]] == ["same.csv", "same_2.csv"], doc["csvs"])

# -- 3. the validator and the TOML writer -----------------------------------------
from core import config                                          # noqa: E402
from core.services import detector_control as dc                 # noqa: E402
doc, _c = doc_of(2)
clean = dc._validate_output(dict(doc))
check("the validator keeps every entry", [e["name"] for e in clean.get("csvs") or []]
      == ["violation_cam1.csv", "violation_cam2.csv"], clean.get("csvs"))
try:
    import tomllib
except ImportError:                                               # pragma: no cover
    tomllib = None
if tomllib:
    text = config.toml_text({"outputs": [clean]})
    back = tomllib.loads(text)["outputs"][0]
    check("...and the TOML writer reads it back exactly", back.get("csvs") == clean["csvs"],
          back.get("csvs"))


def refused(i, **over):
    d = json.loads(json.dumps(doc))
    d["csvs"][i].update(over)
    try:
        dc._validate_output(d)
        return ""
    except (ValueError, TypeError) as exc:
        return str(exc)


check("...a second file that is not a SELECT is refused",
      "only SELECT" in refused(1, sql="delete from violation"))
check("...a second file fetched off this box is refused",
      "this box only" in refused(1, url="http://10.0.0.9/api/workflow/query-csv"))
check("...and one with no tables",
      "needs the tables" in refused(1, tables=[]))

# -- 4. the route finds each file ---------------------------------------------------
src = io.open(os.path.join(ROOT, "core", "routes", "workflow.py"), encoding="utf-8").read()
resp = src.split("def _csv_response(")[1].split("\ndef ")[0]
check("query-csv takes n=<csv node> and finds that file on that output",
      "def query_csv(request: Request, out: str = \"\", n: str = \"\")" in src
      and "_csv_entries(" in resp)
import types                                                     # noqa: E402
try:
    import fastapi                                                # noqa: F401
except ImportError:
    # The dev box has no fastapi; the routes module only needs the names.
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.__getattr__ = lambda name: object
    fr = types.ModuleType("fastapi.responses")
    fr.__getattr__ = lambda name: object
    sys.modules.update({"fastapi": fa, "fastapi.responses": fr})
from core.routes import workflow as wr                           # noqa: E402
entries = wr._csv_entries(clean)
check("_csv_entries: one spec per file, each shaped like the single one",
      [e["csv_name"] for e in entries] == ["violation_cam1.csv", "violation_cam2.csv"]
      and all(e["csv_sql"] and e["csv_tables"] for e in entries), entries)
single, _c = doc_of(1)
check("...and a one-file message is its own single entry",
      [e["csv_name"] for e in wr._csv_entries(single)] == ["violation_cam1.csv"])

# -- 5. the app ---------------------------------------------------------------------
from app import outputs                                          # noqa: E402
fetched = []


class _Resp:
    def __init__(self, body, rows):
        self.body, self.headers = body, {"X-Row-Count": rows}

    def read(self, n):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


import urllib.request                                            # noqa: E402
_real_urlopen = urllib.request.urlopen


def fake_urlopen(req, timeout=None):
    url = req if isinstance(req, str) else req.full_url
    if "query-csv" in url:
        fetched.append(url)
        if url.endswith("n=wf_c2"):
            return _Resp(b"cam,total\n", "0")
        return _Resp(b"cam,total\ncam1,4\n", "1")
    sent.append((url, req.data if not isinstance(req, str) else None))
    return _Resp(b"{}", "0")


sent = []
urllib.request.urlopen = fake_urlopen
files = outputs._csv_fetch_all(clean)
check("the app fetches EVERY file, each from its own address",
      fetched == [e["url"] for e in clean["csvs"]], fetched)
check("...as (name, bytes, rows), in order",
      [f[0] for f in files] == ["violation_cam1.csv", "violation_cam2.csv"]
      and files[1][2] == "0", files)
text = outputs._fill("A {csv:violation_cam1.csv} B {csv_text:violation_cam2.csv} "
                     "C {csv_rows:violation_cam1.csv} D {csv} E {csv_text}",
                     clean, {}, {}, csvs=files)
check("{csv:name} / {csv_text:name} / {csv_rows:name} per file; {csv} and "
      "{csv_text} stay the first file; a zero-row file says (no rows)",
      text == "A violation_cam1.csv B (no rows) C 1 D violation_cam1.csv "
              "E cam,total\ncam1,4\n", repr(text))
one = outputs._fill("{csv_text}", {}, {}, {}, csv=("x.csv", b"a,b\n", "0"))
check("...also for the old single-file call", one == "(no rows)", one)
check("an older single-file document is still one file",
      [f[0] for f in outputs._csv_fetch_all(
          {"csv_url": "http://127.0.0.1:5110/api/workflow/query-csv?out=wf_m",
           "csv_name": "only.csv"})] == ["only.csv"])

# Email: both files attached, each its own MIME part.
import smtplib                                                    # noqa: E402
mails = []


class FakeSMTP:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        mails.append(msg)


smtplib.SMTP = FakeSMTP
outputs.threading.Thread = lambda target, daemon=True: type(
    "T", (), {"start": staticmethod(target)})()
fetched.clear()
mail = dict(clean, smtp_host="smtp.x", smtp_user="u@x", smtp_pass="p",
            subject="Report", body="see {csv:violation_cam2.csv}")
outputs._email(mail, {}, {})
parts = [(p.get_filename(), p.get_content_type()) for p in mails[-1].iter_attachments()] \
    if mails else []
check("the Email attaches BOTH files, each its own text/csv part",
      parts == [("violation_cam1.csv", "text/csv"), ("violation_cam2.csv", "text/csv")],
      parts)
check("...and names the second in the body",
      mails and "see violation_cam2.csv" in mails[-1].get_body().get_content())

# Telegram: one document per file, the message as the first file's caption.
tg = dict(doc_of(2, "output.telegram")[0])
tg = dc._validate_output(tg)
tg.update(bot_token="1:" + "x" * 24, chat_id="123", message="see {csv}")
sent.clear()
outputs._telegram(tg, {}, {})
docs = [u for u, _d in sent if u.endswith("/sendDocument")]
check("Telegram posts one document per file", len(docs) == 2, sent)
check("...the message rides as the FIRST file's caption only",
      sent and b'name="caption"' in sent[0][1] and b"see violation_cam1.csv" in sent[0][1]
      and b'name="caption"' not in sent[1][1], [d[:200] for _u, d in sent])
check("...each document carries its own file name",
      b'filename="violation_cam1.csv"' in sent[0][1]
      and b'filename="violation_cam2.csv"' in sent[1][1])
urllib.request.urlopen = _real_urlopen

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
