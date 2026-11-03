# Data Table "When the same object is seen again": add a new row (the
# original behaviour, still the default) or update the existing row, so a
# table holds ONE row per object id (user request, 2026-10-13).
#
# Three layers, each checked here:
#   compile   core/services/workflow.py emits on_repeat="update" only when it
#             was asked for AND a column names the object; an old graph
#             compiles to the same document as before (no new key).
#   app       app/outputs.py dataset_upsert_key says which column a write is
#             keyed on; app/run.py _log_row puts it on the event (detail.upsert).
#   master    core/repos/datasets.py expand + insert_many refresh the newest
#             live row holding that id, or insert when there is none.
#
# There is no Postgres on the dev box, so the master half runs against an
# in-memory stand-in that implements the three statements insert_many sends
# (bulk insert, keyed UPDATE, single INSERT). It proves the orchestration --
# which rows go which way, collapse, replay -- not Postgres's own SQL.
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
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

# -- an in-memory dataset_rows ------------------------------------------------
DB = []          # [{event_id, ts, system_slug, dataset, cols, deleted_at, seq}]
LOCKS = []


def _insert(eid, ts, slug, dataset, cols):
    if any(r["event_id"] == eid for r in DB):
        return 0
    DB.append({"event_id": eid, "ts": ts, "system_slug": slug,
               "dataset": dataset, "cols": json.loads(cols),
               "deleted_at": None, "seq": len(DB)})
    return 1


class _Cur:
    rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if "pg_advisory_xact_lock" in sql:
            LOCKS.append(params[0])
            self.rowcount = 1
        elif sql.startswith("UPDATE dataset_rows SET cols = cols ||"):
            new, ts, slug, dataset, contain = params
            want = json.loads(contain)
            live = [r for r in DB if r["system_slug"] == slug
                    and r["dataset"] == dataset and r["deleted_at"] is None
                    and all(r["cols"].get(k) == v for k, v in want.items())]
            live.sort(key=lambda r: (r["ts"], r["seq"]), reverse=True)
            if live:
                live[0]["cols"].update(json.loads(new))
                live[0]["ts"] = ts
            self.rowcount = 1 if live else 0
        elif sql.startswith("INSERT INTO dataset_rows") and "VALUES (%s" in sql:
            self.rowcount = _insert(*params)
        else:
            raise AssertionError("unexpected SQL: " + sql[:80])


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def close(self):
        pass


def _execute_values(cur, sql, values):
    assert "ON CONFLICT (event_id) DO NOTHING" in sql, sql
    cur.rowcount = sum(_insert(*v) for v in values)


pg = types.ModuleType("psycopg2")
pg.extras = types.ModuleType("psycopg2.extras")
pg.extras.execute_values = _execute_values
sys.modules.setdefault("psycopg2", pg)
sys.modules.setdefault("psycopg2.extras", pg.extras)

from core.repos import datasets as ds                          # noqa: E402
from core.repos import events as events_repo                   # noqa: E402
from core.services import datasets as dsvc                     # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from core.services import workflow as wf                       # noqa: E402
from app import outputs as out                                 # noqa: E402

ds.connect = lambda config_dir=None: _Conn()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# =============================================================================
# app + master: the write path, end to end
# =============================================================================
LOG = []         # every event the app appended, for the replay check
SEQ = [0]

# The system's stored outputs, on disk the way the master reads them (Pipi
# N-1): every table a test writes through `hook` is saved here first, and the
# keyed-update permission comes from services/datasets.upsert_keys reading it
# back -- never from the event.
SYS = tempfile.mkdtemp()
os.makedirs(os.path.join(SYS, "pcb"), exist_ok=True)
OUTPUTS = {}


def store_output(o):
    if OUTPUTS.get(o["dataset"]) != o:
        OUTPUTS[o["dataset"]] = dict(o)
        dc.set_outputs(SYS, "pcb", list(OUTPUTS.values()))


def keys():
    return dsvc.upsert_keys(SYS, "pcb")


def hook(o, cols):
    """app/run.py _log_row, minus the store: the same detail it builds."""
    SEQ[0] += 1
    store_output(o)
    detail = {"dataset": o.get("dataset"), "cols": cols}
    key = out.dataset_upsert_key(o, cols)
    if key:
        detail["upsert"] = key
    ev = {"id": f"demo-{SEQ[0]:04d}", "system_slug": "pcb",
          "host_serial": "__local__",
          "ts": f"2026-10-13T10:00:{SEQ[0]:02d}", "detail": detail}
    LOG.append(ev)
    events_repo._fan_out_datasets([ev], upsert_keys=keys())


HOOKS = {"dataset": hook}
COLS = [{"name": "oid", "type": "text", "src": "event", "field": "track_id"},
        {"name": "n", "type": "number", "src": "stat", "key": "cnt"},
        {"name": "when", "type": "timestamp", "src": "event", "field": "ts"}]


def table(name, on_repeat=None, columns=COLS, mode="alert"):
    o = {"id": f"wf_{name}", "type": "dataset", "enabled": True,
         "dataset": name, "name": name, "mode": mode, "columns": columns}
    if on_repeat:
        o["on_repeat"] = on_repeat
    return o


def seen(o, tid, n):
    """One alert about object `tid` with count `n`, fired the way fire() does."""
    ev = {"ts": f"2026-10-13T10:00:{SEQ[0] + 1:02d}",
          "detail": {"detections": [{"track_id": tid, "confidence": 0.9}]}}
    return out._fire_one(o, HOOKS, ev, {"cnt": n})


def rows(name):
    return [r for r in DB if r["dataset"] == name and r["deleted_at"] is None]


RUN = out._RUN_ID

# 1. default: a new row every time -- unchanged behaviour
t = table("t_default")
for n in (1, 2, 3):
    seen(t, 7, n)
r = rows("t_default")
check("default (no setting): 3 sightings of one OID = 3 rows",
      len(r) == 3 and [x["cols"]["n"] for x in r] == [1.0, 2.0, 3.0], r)
check("default: no event carries detail.upsert",
      not any("upsert" in e["detail"] for e in LOG), LOG[-1])
t = table("t_insert", on_repeat="insert")
for n in (1, 2):
    seen(t, 7, n)
check("explicit 'insert' behaves as the default", len(rows("t_insert")) == 2)

# 2. update mode: one row per OID, the LAST values kept
t = table("t_upd", on_repeat="update")
for n in (1, 2, 3):
    seen(t, 7, n)
r = rows("t_upd")
check("update: 3 sightings of one OID = 1 row", len(r) == 1, r)
check("update: the row keeps the last values and the last-seen time",
      r and r[0]["cols"]["n"] == 3.0 and r[0]["cols"]["when"] == r[0]["ts"]
      and r[0]["cols"]["oid"] == f"{RUN}-7", r)
check("update: the row keeps the FIRST event id (first sighting recoverable)",
      r and r[0]["event_id"] == next(e["id"] for e in LOG
                                     if e["detail"]["dataset"] == "t_upd"), r)
check("update: each write took the per-object lock",
      LOCKS and all(f"|t_upd|oid|{RUN}-7" in k for k in LOCKS[-3:]), LOCKS[-3:])

# 3. two different OIDs = two rows
seen(t, 8, 10)
seen(t, 8, 11)
r = {x["cols"]["oid"]: x["cols"]["n"] for x in rows("t_upd")}
check("update: two OIDs = two rows, each with its own last value",
      r == {f"{RUN}-7": 3.0, f"{RUN}-8": 11.0}, r)

# 4. an OID from another run is another object
saved = out._RUN_ID
out._RUN_ID = "zzzz999"
try:
    seen(t, 7, 99)
finally:
    out._RUN_ID = saved
r = {x["cols"]["oid"]: x["cols"]["n"] for x in rows("t_upd")}
check("update: the same tracker number from another run is a separate row",
      len(r) == 3 and r.get("zzzz999-7") == 99.0 and r.get(f"{RUN}-7") == 3.0, r)

# 5. a table with no identity column is unaffected by the setting
t = table("t_noid", on_repeat="update", columns=COLS[1:])
for n in (1, 2, 3):
    seen(t, 7, n)
check("no identity column: 'update' still adds a row each time",
      len(rows("t_noid")) == 3)
check("no identity column: the app never asks for an upsert",
      out.dataset_upsert_key(t, {"n": 1}) is None)

# 6. a blank identity (tracker not confirmed yet) is inserted, not merged
t = table("t_blank", on_repeat="update")
for n in (1, 2):
    ev = {"ts": "2026-10-13T10:01:00", "detail": {"detections": [{"confidence": .9}]}}
    out._fire_one(t, HOOKS, ev, {"cnt": n})
check("update: rows with no identity are never merged together",
      len(rows("t_blank")) == 2, rows("t_blank"))

# 7. an existing table that already holds duplicates (pcb_sensata): only the
#    NEWEST matching row is refreshed, the older ones stay as history
oid = f"{RUN}-5"
for i, ts in enumerate(("2026-10-13T09:00:00", "2026-10-13T09:30:00")):
    _insert(f"old-{i}", ts, "pcb", "t_dupes",
            json.dumps({"oid": oid, "n": float(i), "when": ts}))
t = table("t_dupes", on_repeat="update")
seen(t, 5, 42)
r = sorted(rows("t_dupes"), key=lambda x: x["event_id"])
check("duplicates already there: no new row, only the newest one refreshed",
      len(r) == 2 and r[0]["cols"]["n"] == 0.0 and r[1]["cols"]["n"] == 42.0, r)

# 8. a hand-deleted row is not resurrected by an update; the object gets a
#    fresh row instead
t = table("t_del", on_repeat="update")
seen(t, 4, 1)
rows("t_del")[0]["deleted_at"] = "now"
seen(t, 4, 2)
r = rows("t_del")
check("deleted row stays deleted; the next sighting starts a new row",
      len(r) == 1 and r[0]["cols"]["n"] == 2.0, r)

# 9. timer-paced writes (`every`) and on-change refresh the one row too
t = table("t_every", on_repeat="update", mode="every")
t["interval_s"] = 5
dets = [{"track_id": 9, "confidence": .9}]
for i, n in enumerate((1, 2, 3)):
    out._dataset_tick(t, {"cnt": n}, 1000.0 + 10 * i, HOOKS, dets)
r = rows("t_every")
check("every-N-seconds: one row per object, refreshed on each tick",
      len(r) == 1 and r[0]["cols"]["n"] == 3.0, r)

# 10. REPLAY: the master re-reads the whole log on boot. The table must end
#     exactly where it was -- same rows, same last values.
before = sorted((r["event_id"], json.dumps(r["cols"], sort_keys=True), r["ts"])
                for r in DB if r["deleted_at"] is None)
events_repo._fan_out_datasets(LOG, upsert_keys=keys())
after =sorted((r["event_id"], json.dumps(r["cols"], sort_keys=True), r["ts"])
               for r in DB if r["deleted_at"] is None)
check("replaying the full log from offset 0 leaves the tables unchanged",
      before == after, set(before) ^ set(after))

# 11. expand() refuses a malformed key and a key whose value is blank
ev = {"id": "x1", "ts": "2026-10-13T10:00:00",
      "detail": {"dataset": "t", "cols": {"oid": "a"}, "upsert": "oid; drop"}}
check("expand: an illegal column name is not honoured",
      "upsert" not in ds.expand(ev, "pcb"))
ev["detail"].update(upsert="oid", cols={"oid": ""})
check("expand: a blank id is inserted, not keyed", "upsert" not in ds.expand(ev, "pcb"))

# 12. the real hook in app/run.py builds the same detail as the stand-in
src = io.open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
              encoding="utf-8").read()
body = src.split("def _log_row(")[1].split("\n    def ")[0]
check("app/run.py _log_row keys the event on dataset_upsert_key",
      "outputs_mod.dataset_upsert_key(o, cols)" in body
      and 'detail["upsert"] = key' in body, body[:300])

# =============================================================================
# Pipi N-1 (1.0.38 gate): only the local app, only on the table's identity
# =============================================================================
EV = [0]


def crafted(name, cols, upsert, host="__local__", fan_keys=True):
    """An event straight onto the ingest path, the way a paired device or a
    tampered log would deliver it -- nothing from app/outputs.py."""
    EV[0] += 1
    ev = {"id": f"n1-{EV[0]:04d}", "system_slug": "pcb", "host_serial": host,
          "ts": f"2026-10-13T11:00:{EV[0]:02d}",
          "detail": {"dataset": name, "cols": cols, "upsert": upsert}}
    events_repo._fan_out_datasets([ev], upsert_keys=keys() if fan_keys else None)


# N1. lead decision: for LOCAL events the table's current on_repeat is not
#     consulted. A table refreshed under "update", then switched to "add a
#     new row", must not explode on the next boot's log replay.
t = table("n_sw", on_repeat="update")
mark = len(LOG)
for n in (1, 2, 3):
    seen(t, 3, n)
history = LOG[mark:]
check("N-1: update table holds one row before the switch",
      len(rows("n_sw")) == 1 and rows("n_sw")[0]["cols"]["n"] == 3.0, rows("n_sw"))
store_output(table("n_sw", on_repeat="insert"))         # operator switches it
check("N-1: switched table keeps its identity key (setting not consulted)",
      keys().get("n_sw") == {"oid"}, keys())
events_repo._fan_out_datasets(history, upsert_keys=keys())     # boot replay
r = rows("n_sw")
check("N-1: local event, table now set to insert: hint still honoured on "
      "replay (no row explosion)",
      len(r) == 1 and r[0]["cols"]["n"] == 3.0, r)

# N1b. a table with no identity column at all: a local hint is a plain insert
t = table("n_noid", columns=COLS[1:])
store_output(t)
crafted("n_noid", {"n": 1.0}, "n")
crafted("n_noid", {"n": 1.0}, "n")
check("N-1: table with no identity column + local hint = plain insert",
      len(rows("n_noid")) == 2 and "n_noid" not in keys(), rows("n_noid"))

# N2. table set to update, but the hint names a column that is not its identity
t = table("n_col", on_repeat="update")
seen(t, 3, 5)
crafted("n_col", {"oid": "someone-else", "n": 5.0}, "n")
r = sorted(rows("n_col"), key=lambda x: x["seq"])
check("N-1: key column that is not the identity column = plain insert",
      len(r) == 2 and r[0]["cols"]["oid"] == f"{RUN}-3", r)

# N3. a paired device's event on a table that IS set to update
crafted("n_col", {"oid": f"{RUN}-3", "n": 777.0}, "oid", host="SERIAL0001")
r = sorted(rows("n_col"), key=lambda x: x["seq"])
check("N-1: a non-local host's update hint = plain insert, row untouched",
      len(r) == 3 and r[0]["cols"]["n"] == 5.0 and r[2]["cols"]["n"] == 777.0, r)
# ...and the synced path in master_server passes no keys at all, so even a
# device stamped '__local__' cannot get one through it
crafted("n_col", {"oid": f"{RUN}-3", "n": 778.0}, "oid", fan_keys=False)
check("N-1: no keys (the synced path) = plain insert even for '__local__'",
      len(rows("n_col")) == 4 and sorted(rows("n_col"), key=lambda x: x["seq"])[0]
      ["cols"]["n"] == 5.0)

# N4. the legitimate local update still updates
crafted("n_col", {"oid": f"{RUN}-3", "n": 9.0}, "oid")
r = [x for x in rows("n_col") if x["cols"]["oid"] == f"{RUN}-3"]
check("N-1: a local event on the table's own identity column still updates",
      len(rows("n_col")) == 4 and any(x["cols"]["n"] == 9.0 for x in r)
      and not any(x["cols"]["n"] == 778.0 for x in r), r)

# N5. group mode (ids_in_row = group): keyed on the whole group cell, through
#     the same checks
t = table("n_grp", on_repeat="update")
t["ids_in_row"] = "group"
grp_ev = {"ts": "2026-10-13T12:00:00",
          "detail": {"detections": [{"track_id": 2, "confidence": .9},
                                    {"track_id": 1, "confidence": .8}]}}
out._fire_one(t, HOOKS, grp_ev, {"cnt": 2})
out._dataset_groups.clear()          # memory lost (restart): same group again
out._fire_one(t, HOOKS, grp_ev, {"cnt": 3})
r = rows("n_grp")
cell = f"{RUN}-1, {RUN}-2"
check("N-1: group + update still refreshes the one group row",
      len(r) == 1 and r[0]["cols"]["oid"] == cell and r[0]["cols"]["n"] == 3.0, r)
crafted("n_grp", {"oid": cell, "n": 50.0}, "oid", host="SERIAL0001")
check("N-1: group table, non-local hint = plain insert",
      len(rows("n_grp")) == 2 and r[0]["cols"]["n"] == 3.0, rows("n_grp"))
check("N-1: the group table's key is its identity column", keys().get("n_grp") == {"oid"},
      keys())

# N6. a refused hint is never an error and never loses the row
try:
    crafted("n_sw", {"oid": "x"}, "oid; drop")
    crafted("n_missing_table", {"oid": "x"}, "oid")
    ok = len(rows("n_missing_table")) == 1
except Exception as e:                                       # noqa: BLE001
    ok = repr(e)
check("N-1: refused hints degrade to inserts, never raise", ok is True, ok)

# =============================================================================
# compile
# =============================================================================
TABLES = {"pcb": {"key": "pcb", "name": "PCB",
                  "columns": [{"name": "oid", "type": "text"},
                              {"name": "n", "type": "number"},
                              {"name": "when", "type": "timestamp"}]}}
SOURCES = {"oid": {"src": "event", "field": "track_id"},
           "when": {"src": "event", "field": "ts"},
           "n": {"src": "const", "value": "1"}}


def graph(sources=SOURCES, **extra):
    data = {"dataset": "pcb", "name": "PCB", "mode": "every", "interval_s": 10,
            "sources": sources, **extra}
    return {"nodes": [{"id": "db", "type": "output.dataset",
                       "position": {"x": 0, "y": 0}, "data": data}],
            "edges": []}


def compiled(g):
    res = wf.compile_graph(wf.clean_graph(g), [], tables=TABLES)
    assert not res["errors"], res["errors"]
    docs = [o for o in res["outputs"] if o["type"] == "dataset"]
    assert len(docs) == 1, docs
    return docs[0], [w["message"] for w in res["warnings"] if w["node"] == "db"]


old, _ = compiled(graph())
check("an old graph (no setting) compiles with no on_repeat key",
      "on_repeat" not in old, old)
same, _ = compiled(graph(on_repeat="insert"))
check("'Add a new row' compiles to the identical document", same == old,
      (same, old))
upd, notes = compiled(graph(on_repeat="update"))
check("'Update the existing row' compiles to on_repeat=update",
      upd.get("on_repeat") == "update"
      and {k: v for k, v in upd.items() if k != "on_repeat"} == old, upd)
check("...with no warning when there is an identity column",
      not any("update the existing row" in m for m in notes), notes)
noid, notes = compiled(graph(sources={"n": {"src": "const", "value": "1"}},
                             on_repeat="update"))
check("no identity column: setting dropped, with a plain warning",
      "on_repeat" not in noid
      and any("update the existing row" in m for m in notes), (noid, notes))
check("the new node default is 'insert'",
      wf.REGISTRY["output.dataset"]["data"].get("on_repeat") == "insert")

print()
print("FAILED" if bad else "all passed", f"({bad} failing)" if bad else "")
sys.exit(1 if bad else 0)
