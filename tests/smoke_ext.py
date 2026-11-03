# Extension runtime smoke: a custom function handler wired through the app's
# FunctionEngine, in the system the store install just created.
import pathlib
import sys

SAND = pathlib.Path(__file__).parent / "store_sandbox"
PKG = SAND / "store" / "systems" / "store_demo"
if not (PKG / "app" / "functions.py").is_file():
    # This test runs INSIDE the system a store install creates, so it needs
    # that install to have happened. Run it here rather than depending on
    # test order -- a suite whose result changes with the order it runs in is
    # a suite nobody can trust.
    import subprocess

    done = subprocess.run([sys.executable,
                           str(pathlib.Path(__file__).parent / "smoke_store.py")],
                          capture_output=True, text=True,
                          cwd=str(pathlib.Path(__file__).resolve().parent.parent))
    if done.returncode == 77:       # no signing key on this machine
        print("SKIP: needs the store install, which needs the signing key")
        raise SystemExit(77)
    if done.returncode != 0:
        print(done.stdout, done.stderr)
        raise SystemExit("the store install this test builds on failed")
assert (PKG / "app" / "functions.py").is_file(), "install did not scaffold app/"

ext = PKG / "app" / "store_ext"
ext.mkdir(exist_ok=True)
# idempotence: a previous run leaves the marker behind, which would break
# step 1 ("no marker -> refused")
(ext / ".manifest_sha256").unlink(missing_ok=True)
(ext / "__init__.py").write_bytes(b"""
def people_pressure(fn, ctx):
    # silly but real: persons / frame area unit, plus persistent state
    st = ctx["state"]
    st["frames"] = st.get("frames", 0) + 1
    persons = [d for d in ctx["detections"] if d.get("label") == "person"]
    return {"value": len(persons) * 2.5, "frames": st["frames"]}

def broken(fn, ctx):
    raise RuntimeError("boom")

FUNCTIONS = {"people_pressure": people_pressure, "broken": broken}
OUTPUTS = {}
""")

sys.path.insert(0, str(PKG))
from app import functions as fx                             # noqa: E402
from app import extensions as extensions_mod                # noqa: E402

# 1. No marker -> refused.
loaded = extensions_mod.load()
assert loaded == {"functions": {}, "outputs": {}}, loaded
print("== unmarked store_ext refused to load")

# 2. Marker present -> loads.
(ext / ".manifest_sha256").write_bytes(b"deadbeef")
extensions_mod._loaded = None
loaded = extensions_mod.load()
assert "people_pressure" in loaded["functions"], loaded
print("== marked store_ext loaded:", sorted(loaded["functions"]))

# 3. Through the engine.
fn = fx.Function({"id": "pressure", "type": "custom",
                  "params": {"handler": "people_pressure"}})
bad = fx.Function({"id": "bad", "type": "custom",
                   "params": {"handler": "broken"}})
eng = fx.FunctionEngine([fn, bad])
dets = [{"label": "person", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.4},
        {"label": "person", "x": 0.5, "y": 0.2, "w": 0.2, "h": 0.4},
        {"label": "car", "x": 0.7, "y": 0.6, "w": 0.2, "h": 0.2}]
for i in range(3):
    eng.update({"crossing_events": []}, dets, {}, {}, 640, 480, zones=[])
stats = eng.stats() if hasattr(eng, "stats") else eng._stats
print("== engine stats:", {k: v for k, v in stats.items() if k.startswith("fn_")})
assert stats.get("fn_pressure_value") == 5.0
assert stats.get("fn_pressure_frames") == 3
assert stats.get("fn_bad_error") == 1
print("== custom evaluator + state + crash containment all OK")

# 4. Missing type refused at parse (validator parity).
try:
    fx.Function({"id": "x", "type": "custom", "params": {}})
    print("!! custom without handler ACCEPTED")
except ValueError as e:
    print("== custom without handler refused:", e)
