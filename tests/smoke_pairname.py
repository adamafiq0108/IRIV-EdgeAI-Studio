"""A paired device cannot take the local app's name (Pipi N-2, v1.0.39).

The local app's events are stamped host_serial "__local__". Pairing stored
whatever host_serial the device reported, so a device calling itself
"__local__" had its events shown as the local camera's. Now:

  1. slaves.valid_serial: letters, digits, '.', '_', '-', starting with a
     letter or digit, up to 64; nothing starting "__"
  2. pairing refuses a reserved or odd name with a plain error, and
     save_slave refuses it too
  3. an entry already stored under such a name loads without a crash,
     flagged "reserved", listed for removal, and is never polled or pinged
  4. the sync ingest never stamps a synced event with a reserved name, and a
     synced event is never an upsert (fail-closed since N-1)

    py -3 tests/smoke_pairname.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import settings as settings_service          # noqa: E402
from core.services import slaves as slaves_service              # noqa: E402
from core.services import sync as sync_service                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


# -- 1. the name rule -------------------------------------------------------------
good = ["10000000abcdef12", "iriv-cm5-01", "Box_2.lab", "a", "x" * 64]
badn = ["__local__", "__anything", "_x", "-x", ".x", "", "a b", "a/b", "x" * 65,
        "box;rm", "été", None]
check("ordinary device names are allowed", all(slaves_service.valid_serial(n) for n in good),
      [n for n in good if not slaves_service.valid_serial(n)])
check("__local__, anything starting __ and odd names are not",
      not any(slaves_service.valid_serial(n) for n in badn),
      [n for n in badn if slaves_service.valid_serial(n)])

# -- 2. pairing ----------------------------------------------------------------------
answers = {}


def fake_request(url, payload=None, timeout=None):
    return answers[url.rsplit("/api/", 1)[1]]


settings_service._request = fake_request
answers["agent/info"] = (200, {"host_serial": "__local__", "system_slug": "demo"})
answers["agent/pair"] = (200, {"ok": True, "token": "t"})
try:
    settings_service.pair("http://10.0.0.9:8000", "", "demo", "m1")
    err = None
except settings_service.SettingsError as exc:
    err = exc
check("pairing a device that calls itself __local__ is refused, in words",
      err is not None and err.status == 400 and "may not start with '__'" in str(err), err)
answers["agent/pair"] = (200, {"ok": True, "token": "t", "host_serial": "__local__"})
answers["agent/info"] = (200, {"host_serial": "dev-1", "system_slug": "demo"})
try:
    settings_service.pair("http://10.0.0.9:8000", "", "demo", "m1")
    err = None
except settings_service.SettingsError as exc:
    err = exc
check("...also when the name arrives in the pair answer", err is not None and err.status == 400)
answers["agent/pair"] = (200, {"ok": True, "token": "t"})
entry = settings_service.pair("http://10.0.0.9:8000", "", "demo", "m1")
check("an ordinary device pairs", entry["host_serial"] == "dev-1")
CFG = tempfile.mkdtemp()
try:
    settings_service.save_slave(dict(entry, host_serial="__local__"), CFG)
    saved = "saved"
except settings_service.SettingsError:
    saved = "refused"
check("save_slave refuses a reserved name too", saved == "refused")

# -- 3. already stored ----------------------------------------------------------------
cfg = {"slaves": {"__local__": {"system_slug": "demo", "token": "t1", "url": "http://10.0.0.8"},
                  "dev-1": {"system_slug": "demo", "token": "t2", "url": "http://10.0.0.9"}}}
loaded = slaves_service.load(cfg, "demo", CFG)
check("an entry stored as __local__ loads without a crash, flagged",
      [(s["host_serial"], bool(s.get("reserved"))) for s in loaded]
      == [("__local__", True), ("dev-1", False)], loaded)
polled = []
sync_service.poll_one = lambda s, c: (polled.append(s["host_serial"]) or ({}, [], None))
sync_service.ping_health = lambda s: polled.append("ping:" + s["host_serial"]) or {}
sync_service.poll_all(loaded, {})
sync_service.ping_all(loaded)
check("...and is never polled or pinged", sorted(polled) == ["dev-1", "ping:dev-1"], polled)
devices = settings_service.build_devices(loaded, [])
flag = next(d for d in devices if d["host_serial"] == "__local__")
check("...listed with a reason, so an admin can remove it",
      flag.get("reserved") and "Remove it" in flag.get("problem", "")
      and not next(d for d in devices if d["host_serial"] == "dev-1").get("reserved"))

# -- 4. the ingest -----------------------------------------------------------------------
master = open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
ingest = master.split("def _apply_sync(")[1].split("\ndef ")[0]
check("the sync ingest drops events under a reserved name",
      "if slaves_service.valid_serial(host) for ev in events]" in ingest)
check("...and passes no upsert keys (a synced event is never an update)",
      "events_repo.insert_many(rows)\n" in ingest)
from core.repos import events as events_repo                    # noqa: E402
from core.repos import datasets as datasets_repo                 # noqa: E402
seen = []
datasets_repo.expand = lambda r, slug, keys: seen.append(keys) or None
events_repo._fan_out_datasets([{"host_serial": "__local__", "system_slug": "demo"}],
                              None, None)
check("a row stamped __local__ arriving without upsert keys is a plain insert",
      seen == [None], seen)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
