# A board terminal our OWN workflow drives must not be blamed on Node-RED.
#
# The detection apps are separate processes from the hub, and libgpiod reports
# every lgpio consumer as "lg" -- ours and everybody else's. So a pin one of
# our own rules was driving looked byte-for-byte identical to one the factory
# Node-RED flow owned, and the workflow inspector said so:
#
#   "DO0 is held by another program (lg) -- most often the factory Node-RED
#    flow. Free it there, or this node will fail to drive the pin."
#
# ...about a pin that system's own Board Output node was driving, sending an
# operator into Node-RED to hunt for a claim that was never there.
#
# Only the app can answer this, so it publishes the terminals it holds in its
# state file (`io_held`) and board_io reads them back. Three outcomes now:
# the owning system sees its own rule (not a conflict), another system sees a
# real conflict WITH THE OWNER NAMED, and a genuine stranger still reads as
# one.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)

from core.services import board_io as b                       # noqa: E402

# The board is not present on a dev box, and the pin table is only applied on
# a real IRIV -- so stand in for both the board and the kernel's line view.
b.detected = lambda: True
b.gpio_lines = lambda: {
    23: {"name": "GPIO23", "direction": "output", "consumer": "lg"},   # DO0
    24: {"name": "GPIO24", "direction": "output", "consumer": "lg"},   # DO1
    19: {"name": "GPIO19", "direction": "output", "consumer": "lg"},   # BUZZER
    12: {"name": "GPIO12", "direction": "input", "consumer": ""},      # L0, free
}
# DO0 driven by our own counting system; DO1 by a different system of ours;
# BUZZER by something that is not ours at all (the factory flow).
b.port_owners = lambda: {"DO0": "counting", "DO1": "packing"}


def port(rows, pid_):
    return next(p for p in rows if p["id"] == pid_)


# 1. The system whose own rule drives DO0 is not in conflict with itself.
own = port(b.ports("counting"), "DO0")
assert own["busy"] is False, \
    "a terminal driven by THIS system's own rule is not a conflict"
assert own["owner"] == "counting", "the owner must still be reported"

# 2. A DIFFERENT system sees a real conflict -- two rules, one relay -- and
#    the owner is named, which is the fact the board alone cannot supply.
other = port(b.ports("packing"), "DO0")
assert other["busy"] is True, "another system's claim IS a conflict"
assert other["owner"] == "counting", \
    "the conflicting system must be named, not left as a mystery"
assert other["consumer"] == "counting", \
    "consumer must name the system rather than the meaningless 'lg'"

# 3. With no system in context (the hub-wide board page) nothing our own box
#    drives should read as a stranger's.
for pid_ in ("DO0", "DO1"):
    row = port(b.ports(), pid_)
    assert row["busy"] is False, f"{pid_} is ours; the hub must not call it busy"

# 4. A line held by something that is NOT one of our apps is still reported
#    honestly -- this is the case the original message was written for.
buzzer = port(b.ports("counting"), "BUZZER")
assert buzzer["busy"] is True, "a genuine stranger's claim must still show"
assert buzzer["owner"] == "", "no owner means we cannot name it"
assert buzzer["consumer"] == "lg", "so the raw consumer stands"

# 5. A free line is free.
assert port(b.ports("counting"), "L0")["busy"] is False

# 6. The app publishes what only it can know, and the reader is wired to it.
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()
assert "io_held=_held_ports()" in run_src, \
    "the app must publish its held terminals in the per-cycle state write"
with open(os.path.join(ROOT, "systems", "demo", "app", "iriv.py"),
          encoding="utf-8") as f:
    assert "def held_ports" in f.read(), "the app needs a held_ports()"
with open(os.path.join(ROOT, "core", "services", "board_io.py"),
          encoding="utf-8") as f:
    assert 'state.get("io_held")' in f.read(), \
        "port_owners must read what the app publishes"

# 7. The route must pass WHICH system is asking -- without it every port a
#    workflow drives reports itself held by a stranger.
with open(os.path.join(ROOT, "core", "routes", "models.py"),
          encoding="utf-8") as f:
    assert "board_io_service.inventory(slug)" in f.read(), \
        "/api/board/io must tell the service which system is asking"

print("smoke_boardowner OK")
