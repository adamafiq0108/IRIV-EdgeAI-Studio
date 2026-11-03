# A rule that picked a WORD read 0 on every card, and the word could not even
# be chosen from a picker.
#
# Found on 192.168.1.115, system object_detection. The operator's graph was
# analytics.object -> branch.cases -> two Set Variable nodes writing the text
# "Chair" / "People" into fn_object_name_value -> a dashboard card each. The
# app was right the whole time: its state.json held
#
#     counts["fn_object_name_value"] = "People"
#
# and the function engine publishes a string on purpose (systems/demo/app/
# functions.py -- "A rule that picked a word publishes the word"). The word
# was thrown away by the READERS: the local ingest copied only numbers out of
# state.json, the cross-device combine had no answer for a label, and the live
# route filtered its values to numbers. Both key pickers were numeric-only
# too, so the key could not be picked even once it existed. The tile showed 0
# and the canvas Debug drawer showed the Set Variable node with nothing at all
# under it.
#
# The numbers-only rule was not wrong where it came from, which is the other
# half of this: the history sampler, the Blynk pusher and every chart read the
# same dict, and none of them can do anything with a word. So this pins both
# sides -- a word survives to every LIVE channel, and everything that needs a
# number still refuses it.
#
#     py -3 tests/smoke_textstat.py
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# The route files are the point of this test and they import fastapi, which is
# not installed on every dev box (same trick as tests/smoke_dataset_query.py).
if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), put=lambda *a, **k: (lambda f: f),
        delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    sys.modules["fastapi"] = fa

from core.routes import functions as fn_routes               # noqa: E402
from core.routes import workflow as wf_routes                # noqa: E402
from core.services import blynk as blynk_service             # noqa: E402
from core.services import builder as builder_service         # noqa: E402
from core.services import history as history_service         # noqa: E402
from core.services import local_ingest                       # noqa: E402
from core.services import main as main_service               # noqa: E402
from core.services import overview as overview_service       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# The board's own case, as it sits in state.json right now.
WORD_KEY = "fn_object_name_value"
LONG = "L" * 300

SYSTEMS = tempfile.mkdtemp()
SLUG = "object_detection"
PKG = os.path.join(SYSTEMS, SLUG)
DATA = os.path.join(PKG, "data")
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\ndata_dir = "{DATA.replace(os.sep, "/")}"\n')
with open(os.path.join(DATA, "state.json"), "w", encoding="utf-8") as f:
    json.dump({"running": True, "camera": "cam0",
               "counts": {WORD_KEY: "People",
                          "fn_seen_count": 3,
                          "fn_long_value": LONG,
                          "fn_list_value": [1, 2],
                          "fn_flag_value": True,
                          "fn_day_by_day": {"2026-09-24": 2}}}, f)

ingest = local_ingest.LocalIngest(SYSTEMS, SLUG)


# -- 1. the app's word leaves state.json --------------------------------------
stats = ingest.stats()
check("a text count leaves state.json", stats.get(WORD_KEY) == "People",
      stats.get(WORD_KEY))
check("...beside the numbers, which are untouched",
      stats.get("fn_seen_count") == 3, stats.get("fn_seen_count"))
check("a long word is capped before it is published",
      stats.get("fn_long_value") == "L" * main_service.TEXT_MAX,
      len(str(stats.get("fn_long_value"))))
check("...because it rides the 4s poll to every open dashboard",
      main_service.TEXT_MAX == 120, main_service.TEXT_MAX)
# Anything that is neither a number nor a word is still dropped: the readers
# above this can render a number and a word, and nothing else.
check("a list is still dropped", "fn_list_value" not in stats, stats)
check("a bool is still dropped", "fn_flag_value" not in stats, stats)
check("a per-day bucket still arrives as a bucket",
      stats.get("fn_day_by_day") == {"2026-09-24": 2}, stats.get("fn_day_by_day"))


# -- 2. the roll-up across devices --------------------------------------------
agree = main_service.aggregate_stats([{"stats": {WORD_KEY: "People"}},
                                      {"stats": {WORD_KEY: "People"}}])
check("two devices saying the same word answer with the word",
      agree.get(WORD_KEY) == "People", agree.get(WORD_KEY))

argue = main_service.aggregate_stats([{"stats": {WORD_KEY: "People"}},
                                      {"stats": {WORD_KEY: "Chair"}}])
check("two devices saying different words answer with nothing",
      WORD_KEY not in argue,
      "there is no system-wide answer, and picking one device's would put it "
      "on a tile that claims to speak for the system")

long_roll = main_service.aggregate_stats([{"stats": {"fn_long_value": LONG}}])
check("the roll-up caps a long word too",
      long_roll.get("fn_long_value") == "L" * main_service.TEXT_MAX,
      len(str(long_roll.get("fn_long_value"))))
check("a list still combines to nothing",
      "fn_list_value" not in main_service.aggregate_stats(
          [{"stats": {"fn_list_value": [1, 2]}}]))

# A device publishing TEXT under a standard key must not take the Main page
# down with it: those four keys are summed as ints for every tile on it.
mixed = main_service.aggregate_stats([{"stats": {"events_total": "People"}},
                                      {"stats": {"events_total": 4}}])
check("text under a standard key costs that key, not the whole page",
      mixed.get("events_total") == 4, mixed.get("events_total"))


# -- 3. both live routes carry it ---------------------------------------------
state = types.SimpleNamespace(
    system=types.SimpleNamespace(slug=SLUG), systems_dir=SYSTEMS,
    slaves=[], local_ingest=ingest, local_stats=None,
    config_dir=tempfile.mkdtemp())
request = types.SimpleNamespace(app=types.SimpleNamespace(state=state),
                                state=types.SimpleNamespace(user=None))

live = fn_routes.overview_live(request)
check("/api/overview/live carries the word",
      live["values"].get(WORD_KEY) == "People", live["values"].get(WORD_KEY))
check("...and still carries the numbers",
      live["values"].get("fn_seen_count") == 3, live["values"].get("fn_seen_count"))
check("...and still leaves the buckets out",
      "fn_day_by_day" not in live["values"],
      "this payload is the live scalar of each key")

# The Functions page's own payload. perm.editable is answered here rather than
# dragged in: what this asserts is the stat vocabulary, not who may edit it.
fn_routes.perm = types.SimpleNamespace(editable=lambda *a, **k: True)
payload = fn_routes.get_functions(request)
check("the expression builder's picker offers the text key",
      WORD_KEY in payload["variables"]["stat_keys"],
      payload["variables"]["stat_keys"])
check("...and its live value rides along",
      payload["values"].get(WORD_KEY) == "People", payload["values"])

# The canvas. _live_values never filtered by TYPE, only by key prefix, so it
# needed no change -- which is exactly why it is pinned here: it is the thing
# the Debug drawer reads.
values = wf_routes._live_values(main_service.aggregate_stats([], ingest.stats()))
check("the canvas sees the word on the wire", values.get(WORD_KEY) == "People",
      values.get(WORD_KEY))

variables = wf_routes._variables(request, SYSTEMS, SLUG,
                                 main_service.aggregate_stats([], ingest.stats()))
check("the canvas picker offers the text key",
      WORD_KEY in variables["stat_keys"], variables["stat_keys"])
check("...and still offers the numbers",
      "fn_seen_count" in variables["stat_keys"], variables["stat_keys"])
check("...and still hides the buckets",
      "fn_day_by_day" not in variables["stat_keys"], variables["stat_keys"])


# -- 4. everything that needs a NUMBER still refuses it -----------------------
history_service.sample({WORD_KEY: "People", "fn_seen_count": 3}, now=1.0e9)
check("the history sampler refuses a word",
      not history_service.has(WORD_KEY),
      "a chart of a word is a chart of nothing, and the ring holds scalars")
check("...and still samples the number beside it",
      history_service.has("fn_seen_count"))

blynk_service.save({"enabled": True, "token": "t" * 8,
                    "mappings": [{"key": WORD_KEY, "pin": "V1"}]})
check("the Blynk pusher sends nothing for a word",
      blynk_service.push({WORD_KEY: "People"}, now=1.0e9) is False,
      "a virtual pin is a number; there is no text to send and no way to say "
      "so to the cloud")

tile = builder_service.validate([{"id": "t1", "kind": "tile", "source": "stat",
                                  "key": WORD_KEY, "format": "text"}])[0]
card = builder_service.compute([tile], [], {WORD_KEY: "People"})[0]
check("a tile bound to the key shows the word",
      card["data"]["value"] == "People", card["data"])

status = builder_service.validate([{"id": "s1", "kind": "status",
                                    "source": "stat", "key": WORD_KEY}])[0]
shown = builder_service.compute([status], [], {WORD_KEY: "People"})[0]
check("a status card reads a word as no number, not as a crash",
      shown["data"]["value"] == 0.0, shown["data"])

# The zone verdict is a COMPARISON against the limit, inside the one payload
# the Main page and the Dashboard both render from. A paired device is free to
# publish text under any key it likes, and "People" > 1 raises.
verdict = overview_service.zone_overview(
    {"zones": [{"id": "z1", "name": "Bay", "kind": "area", "enabled": True,
                "actions": [{"type": "violation", "limit": 1}]}]},
    {"zone_z1_count": "People"})
check("a zone verdict reads a word as no reading, not as a 500",
      verdict["zones"][0]["verdict"] == "unknown", verdict["zones"])

donut = builder_service.validate([
    {"id": "d1", "kind": "donut", "source": "stat", "key": WORD_KEY,
     "series": [{"source": "stat", "key": WORD_KEY, "label": "Word"}]}])[0]
drawn = builder_service.compute([donut], [], {WORD_KEY: "People"})[0]
check("a chart slice of a word is 0, not an exception",
      drawn["data"].get("values") == [0.0], drawn["data"])

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
