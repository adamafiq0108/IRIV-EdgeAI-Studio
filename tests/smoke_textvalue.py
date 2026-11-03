# A variable may hold a WORD, and the word travels.
#
# A Set Variable of text kind used to compile to nothing: the engine was
# numeric end to end, so `status` read 0 on the canvas for ever and the only
# place its word could land was a table column, as a fixed string. Now the
# expression engine carries text -- a literal ("in"), a compare against one
# (status == "in"), iff() handing the word through, + joining -- so the
# variable holds the word, the Variable node shows it, a column records
# whichever word it holds now, and a Compare can test it.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from app.functions import compile_expr, eval_expr, Function, FunctionEngine   # noqa: E402
from core.services import functions as fx                                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def ev(text, **variables):
    return eval_expr(compile_expr(text), variables)


# -- the engine ------------------------------------------------------------------------
check("a text literal evaluates to the word", ev('"in"') == "in", repr(ev('"in"')))
check("iff hands the word through, whichever branch",
      ev('iff(gate, "in", status)', gate=1, status="out") == "in"
      and ev('iff(gate, "in", status)', gate=0, status="out") == "out")
check("a variable holding a word reads as the word", ev("status", status="night shift") == "night shift")
check("compare against text is whole and exact",
      ev('status == "in"', status="in") == 1.0 and ev('status == "in"', status="inside") == 0.0
      and ev('status != "in"', status="out") == 1.0)
check("a number beside a word compares as text: 3 is '3'",
      ev('code == 3', code="3") == 1.0 and ev('code == "3"', code=3) == 1.0)
check("+ with a word joins", ev('"Bay " + n', n=3) == "Bay 3")
check("arithmetic on a word is arithmetic on 0, never a crash",
      ev("status * 2", status="in") == 0.0 and ev("status - 1", status="in") == -1.0)
check("a word is true when it says something, for not / and / or",
      ev("not status", status="") == 1.0 and ev("not status", status="in") == 0.0
      and ev('status and 1', status="in") == 1.0)
check("a numeric rule still returns a float", isinstance(ev("1 + 2"), float) and ev("1 + 2") == 3.0)
check("text() makes an unset variable empty text, leaves a word alone, and spells a number",
      ev("text(x)") == "" and ev("text(x)", x="in") == "in" and ev("text(3)") == "3"
      and ev("text(x)", x=0) == "", (ev("text(x)"), ev("text(x)", x="in"), ev("text(3)")))
try:
    compile_expr('"' + "x" * 121 + '"')
    check("...a 121-character literal", False)
except ValueError:
    check("...a 121-character literal", True)
try:
    fx.validate_expr('iff(gate, "in", status)')
    check("the master's validator accepts the same text literal", True)
except ValueError as exc:
    check("the master's validator accepts the same text literal", False, str(exc))

# -- the engine publishes the word as the variable's stat ---------------------------------
setter = Function({"id": "status", "name": "status", "type": "expr", "enabled": True,
                   "params": {"expr": 'iff(fn_gate_value, "in", text(fn_status_value))'}})
gate = Function({"id": "gate", "name": "gate", "type": "expr", "enabled": True,
                 "params": {"expr": "fn_line_in > 0"}})
e = FunctionEngine.__new__(FunctionEngine)
e.functions, e._trig, e._stats, e._live_oids, e._counts = [gate, setter], {}, {}, {}, {}
e._daily, e._dwell, e._travel, e._pending, e._links, e._bubbles, e._trails = {}, {}, {}, [], [], [], []
try:
    e.update({}, [], {}, {"fn_line_in": 0}, 100, 100, zones=[], now=0.0)
    unset = e._stats.get("fn_status_value")
    check("before anything sets it, a text variable reads as empty text - not 0",
          unset == "", repr(unset))
    e.update({}, [], {}, {"fn_line_in": 1}, 100, 100, zones=[], now=0.05)
    first = e._stats.get("fn_status_value")
    e.update({}, [], {}, {"fn_line_in": 0, "fn_status_value": first}, 100, 100, zones=[], now=0.1)
    second = e._stats.get("fn_status_value")
    check("the gated setter publishes the word as fn_status_value, and keeps it when the gate drops",
          first == "in" and second == "in", (first, second))
except Exception as exc:                                              # noqa: BLE001
    check("the engine runs a text rule end to end", False, repr(exc))

# -- the canvas shows it -----------------------------------------------------------------
nodes_src = open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"), encoding="utf-8").read()
check("the Variable node shows a word as it is, not NaN",
      'typeof v === "string" ? (v === "" ? "-" : v) : Math.round(v * 100) / 100' in nodes_src)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
