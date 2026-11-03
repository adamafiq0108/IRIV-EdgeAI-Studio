# The hub card's power button is DELIBERATELY asymmetric.
#
# Stop moves both ends. Stopping only the master left the app running headless
# -- holding the camera and the Hailo chip, and reviving itself the moment the
# master came back -- so the card said "offline" while the AI was still on.
#
# Start moves only the master. Running inference is a separate decision with a
# real cost: the camera, a share of the accelerator, and every rule in the
# workflow firing. Pressing Start on the Hub to LOOK at a system used to spend
# all of it, and on a box with a camera budget it took a camera away from a
# system that was using it. Detection is turned on inside the system.
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import hub as hub_service                  # noqa: E402

SRC = open(os.path.join(ROOT, "core", "routes", "hub.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)


def _fn(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{name} is gone from core/routes/hub.py")


power = _fn("power_system")

# 1. stop moves both ends; start moves neither the app nor anything else's
assert "_app_power(pkg_root, slug, \"stop\")" in power, power
assert "_app_power(pkg_root, slug, \"start\")" not in power, \
    "Start spends the camera and the accelerator on a system nobody asked to run"
print("== stop takes the detection app down with the master")
print("== start brings up the master only")

# 2. ORDER: the app is stopped BEFORE the master is killed, or it is orphaned
#    with nothing left to ask it to quit.
stop_at = power.index('_app_power(pkg_root, slug, "stop")')
kill_at = power.index("provision_service.stop_master(url)")
assert stop_at < kill_at, "the master is killed before its app is stopped"
print("== the app is stopped first, so nothing is left orphaned")

# 3. Start on a system that is already up is a no-op, not a way to arm the AI
#    from the Hub by pressing a button whose icon says "start the system".
probe_at = power.index("masters_service.probe(url")
early = power[probe_at:power.index("already running")]
assert "_app_power" not in early, \
    "an already-running master starts the detection app behind the operator"
# One call in the whole route, and it is the stop.
assert power.count("_app_power(pkg_root") == 1, power
print("== pressing Start on a running system changes nothing")

# 4. app failures never fail the button (the master half is the point)
helper_node = next(n for n in ast.walk(TREE)
                   if isinstance(n, ast.FunctionDef) and n.name == "_app_power")
assert "except Exception" in _fn("_app_power")
assert not [n for n in ast.walk(helper_node) if isinstance(n, ast.Raise)], \
    "_app_power re-raises: a system with no app would fail the whole button"
print("== a system with no detection app does not break the master's power")

# 5. and the card reads the three states the button drives
assert hub_service._status("http://x", None) == "down"
assert hub_service._status("http://x", {"devices_total": 0}) == "online"
assert hub_service._status("http://x", {"devices_total": 0,
                                        "ai_running": True}) == "running"
print("== offline / online / running follow master + ai_running")
print("ALL OK")
