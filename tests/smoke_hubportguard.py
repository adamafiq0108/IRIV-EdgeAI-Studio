# The software port cannot shadow a system's listener, an OTA after "restart
# Later" polls the right port, and a bad port never leaves a box with no
# dashboard (Pipi's review of the software-port feature, 2026-10-05).
#
#   1. hubport.reserved_by / check() knew the box services and the masters'
#      ports, but not a system's video stream or Modbus server -- free at Test
#      time while the app was stopped, then a reboot race the hub could lose.
#   2. update._write_job recorded the port THIS hub bound. After "Save,
#      restart Later" the update restarts the hub onto the NEW configured
#      port; the gate polled the old one and rolled a good update back.
#   3. hubport.port() range-checked [hub].port but not the port in
#      [hub].url: a hand edit to :80 crash-looped the hub on EACCES.
#   4. A configured port something else holds at start now falls back to
#      5100 once, with an Inbox notice, instead of a crash loop.
#
#     python tests/smoke_hubportguard.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                       # noqa: E402
from core.services import hubport, provision, update          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def box(hub):
    d = tempfile.mkdtemp()
    config.save_local("systems", {"hub": hub}, d)
    config.load.cache_clear()
    return d


# 3 -- the url's port is range-checked like [hub].port
check("a [hub].url on :80 does not reach uvicorn (5100 instead)",
      hubport.port(box({"url": "http://127.0.0.1:80"})) == 5100)
check("...an in-range url port still counts", hubport.port(box({"url": "http://127.0.0.1:6100"})) == 6100)
check("...and [hub].port still wins", hubport.port(box({"port": 6200, "url": "http://127.0.0.1:6100"})) == 6200)

# 1 -- stream ports and Modbus ports are reserved
real = (provision.stream_ports_in_use, provision.modbus_ports_in_use, hubport.listening)
provision.stream_ports_in_use = lambda systems_dir=None, config_dir=None: {5210}
provision.modbus_ports_in_use = lambda systems_dir=None, config_dir=None: {5025: "line_b"}
hubport.listening = lambda p: False
try:
    d = box({"port": 5100})
    check("a system's stream port is spoken for, even with its app stopped",
          "video" in hubport.reserved_by(5210, d))
    check("...and a system's Modbus server port, naming the system",
          "line_b" in hubport.reserved_by(5025, d))
    check("...and 5020, the 502 redirect's target", "Modbus" in hubport.reserved_by(5020, d))
    t = hubport.check(5210, d)
    check("the Test button refuses a stream port", t["usable"] is False and "video" in t["detail"])
    check("...and passes a genuinely free one", hubport.check(5333, d)["usable"] is True)
finally:
    provision.stream_ports_in_use, provision.modbus_ports_in_use, hubport.listening = real

# 2 -- the OTA job polls where the restarted hub will be
d = box({"port": 6300})
os.environ[hubport.ENV_PORT] = "5100"
os.environ[hubport.ENV_SOURCE] = "config"
check("after Save + restart Later, the job records the CONFIGURED port",
      update.job_hub_port(d) == 6300)
os.environ[hubport.ENV_SOURCE] = "argv"
check("...but a unit that bakes --port comes back on the running one",
      update.job_hub_port(d) == 5100)
src = open(os.path.join(ROOT, "core", "services", "update.py"), encoding="utf-8").read()
check("_write_job uses it", '"hub_port": job_hub_port(config_dir)}' in src
      and "hubport.running_port() or hubport.port(config_dir)}" not in src)

# 4 -- a taken configured port falls back once
taken = {6400}
hubport.listening = lambda p: p in taken
try:
    os.environ[hubport.ENV_SOURCE] = "config"
    p, why = hubport.fallback_if_taken(6400, d)
    check("a configured port something holds falls back to 5100, with a reason",
          p == 5100 and "6400" in why and os.environ[hubport.ENV_PORT] == "5100"
          and os.environ[hubport.ENV_SOURCE] == "fallback")
    os.environ[hubport.ENV_SOURCE] = "config"
    check("a free configured port is bound as set", hubport.fallback_if_taken(6401, d) == (6401, ""))
    os.environ[hubport.ENV_SOURCE] = "argv"
    check("an explicit --port is never second-guessed", hubport.fallback_if_taken(6400, d) == (6400, ""))
    os.environ[hubport.ENV_SOURCE] = "config"
    taken.add(5100)
    check("...nor moved when 5100 is taken too", hubport.fallback_if_taken(6400, d) == (6400, ""))
finally:
    hubport.listening = real[2]
    os.environ.pop(hubport.ENV_PORT, None)
    os.environ.pop(hubport.ENV_SOURCE, None)
hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
main = hub.split("def main():", 1)[1]
check("the hub falls back before binding and tells the admins",
      main.index("hubport.fallback_if_taken(") < main.index("uvicorn.run(")
      and "notify_admins(" in main and 'tab="network"' in main)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
