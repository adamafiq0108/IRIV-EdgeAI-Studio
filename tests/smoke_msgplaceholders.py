"""An unknown {name} in a message is a compile warning (Tok Aba, 2026-10-14).

The pcb_sensata Email said "{violation_summary}" while the values wired in
were violation_summary_cam1 and _cam2: the app leaves a name nothing fills
exactly as written, so the braces reached the inbox. Now:

  1. the compiler warns on the node for every {name} that is not a built-in,
     not a value wired in and not one of its files, naming the nearest wired
     name ("Did you mean {violation_summary_cam1}?")
  2. built-ins, wired names, {csv:name} for wired files and text that only
     looks like braces ({"a": 1}) are not warned about
  3. the Schedule that fires the message is not a value: {nightly0} warns
  4. the inspector mirrors the rule (msgPlaceholders.js, check_msgplaceholders)
     and the app's _fill docstring says what really happens to such a name

    py -3 tests/smoke_msgplaceholders.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


ZONES = [{"id": "z1", "name": "Bay", "kind": "area", "enabled": True}]


def compiled(body, out_type="output.email"):
    data = ({"to": "boss@example.com", "subject": "Daily {label}", "body": body}
            if out_type == "output.email"
            else {"chat_id": "123", "message": body})
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0}, "data": {"zone": "z1"}},
        {"id": "c1", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "vs1", "name": "violation_summary_cam1"}},
        {"id": "c2", "type": "analytics.frame", "position": {"x": 2, "y": 1},
         "data": {"fnId": "vs2", "name": "violation_summary_cam2", "mode": "realtime"}},
        {"id": "s0", "type": "variable.schedule", "position": {"x": 2, "y": 2},
         "data": {"fnId": "nightly0", "name": "Nightly", "every": "daily", "at": "12:00"}},
        {"id": "o1", "type": out_type, "position": {"x": 3, "y": 0}, "data": data},
    ]
    edges = [{"id": "e1", "source": "src", "target": "z"},
             {"id": "e2", "source": "z", "target": "c1"},
             {"id": "e3", "source": "src", "target": "c2"},
             {"id": "e4", "source": "c1", "target": "o1"},
             {"id": "e5", "source": "c2", "target": "o1"},
             {"id": "e6", "source": "s0", "target": "o1"}]
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES,
                         ["person"], ["person"])
    return [w["message"] for w in c["warnings"] if w.get("node") == "o1"], c


# -- 1. the warning ---------------------------------------------------------------
warns, c = compiled("A {violation_summary} B {violation_summary_cam2}")
check("it still compiles: a warning, never an error", not c["errors"], c["errors"])
check("an unknown name warns, naming the nearest wired one",
      warns == ["{violation_summary} is not wired into this message. Did you mean "
                "{violation_summary_cam1}?"], warns)
warns, _c = compiled("{zzz_totally_else}")
check("...and with nothing near, says it is sent as written",
      warns == ["{zzz_totally_else} is not wired into this message, so it is sent as "
                "written. Wire in the value it should show, or remove it."], warns)
warns, _c = compiled("{violation_summary}", "output.telegram")
check("a Telegram message is checked too",
      len(warns) == 1 and "{violation_summary_cam1}" in warns[0], warns)
warns, _c = compiled("{x1} {x1} {x2}")
check("each unknown name once", len(warns) == 2, warns)

# -- 2. what is not warned about ----------------------------------------------------
warns, _c = compiled("{label} {value} {fn} {time} {type} {message} {csv} {csv_text} "
                     "{csv_rows} {violation_summary_cam1} {vs2} {\"a\": 1}")
check("built-ins, wired names (by name and by id) and brace-like text are fine",
      warns == [], warns)

# -- 3. the clock is not a value ------------------------------------------------------
warns, _c = compiled("{nightly0}")
check("the Schedule that fires it is not a value: {nightly0} warns",
      len(warns) == 1 and warns[0].startswith("{nightly0}"), warns)

# -- helpers -------------------------------------------------------------------------
check("unknown_placeholders knows each wired file's tokens",
      wf.unknown_placeholders(["{csv:a.csv} {csv_text:a.csv} {csv_rows:a.csv} {csv:b.csv}"],
                              {}, ["a.csv"]) == ["csv:b.csv"])
check("closest_placeholder: within half the name, else nothing",
      wf.closest_placeholder("violation_summary", ["violation_summary_cam1", "x"])
      == "violation_summary_cam1"
      and wf.closest_placeholder("abc", ["xyzuvw"]) == "")

# -- 4. the app says what really happens ----------------------------------------------
app = io.open(os.path.join(ROOT, "systems", "demo", "app", "outputs.py"),
              encoding="utf-8").read()
fill = app.split("def _fill(")[1].split('"""')[1]
check("_fill's docstring says an unwired name stays literal, not that it vanishes",
      "resolves to nothing" not in fill and "stays in the message literally" in fill)

# -- 5. Send a test now fills values the way the real send does (job 3) -------------
import types                                                     # noqa: E402
try:
    import fastapi                                                # noqa: F401
except ImportError:
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

wr.functions_service.get_functions = lambda systems_dir, slug: [
    {"id": "qv", "type": "query_value", "enabled": True, "params": {}},
    {"id": "qdead", "type": "query_value", "enabled": True, "params": {}}]
asked = []


def fake_query(request, systems_dir, slug, doc):
    asked.append(doc["id"])
    return {"ok": True, "value": 12.0} if doc["id"] == "qv" else {"ok": False, "value": None}


wr._query_value_of = fake_query
values, missing = wr._test_values(
    None, "systems", "demo",
    {"people": "fn_people_count", "seen": "fn_seen_count", "seen_alias": "fn_seen_count",
     "total": "fn_qv_value", "dead": "fn_qdead_value"},
    {"fn_people_count": 3.0})
check("a live stat is used as the app writes it (3, not 3.0)", values["people"] == "3", values)
check("a Query variable the live stats lack is read now, the poller's way",
      values["total"] == "12" and "qv" in asked, (values, asked))
check("a wired value with nothing anywhere is EMPTY (as in the app), never raw braces",
      values["seen"] == "" and values["dead"] == "", values)
check("...and is named in the answer once per value",
      missing == ["seen", "dead"], missing)
route = io.open(os.path.join(ROOT, "core", "routes", "workflow.py"), encoding="utf-8").read()
test_route = route.split('name="workflow.test_output"')[1].split("\ndef csv_tokens")[0]
check("the test route fills every wired name and names missing and unknown ones",
      'out.replace("{" + name + "}", values.get(name, ""))' in test_route
      and "calculated yet)" in test_route
      and '"missing": missing, "unknown": unknown' in test_route
      and "if key in stats:" not in test_route)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
