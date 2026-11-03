# Handing a board terminal to another program (Node-RED, a customer script).
#
# A GPIO line is ONE piece of hardware and every system on the box shares it,
# so this is a box-wide decision, not a per-system setting: two systems
# disagreeing about who drives DO0 is the conflict the page exists to prevent.
#
# The load-bearing rule is the REFUSAL. Releasing a terminal a rule is bound
# to would drop the line while something is driving it -- a settings click
# opening a relay that is holding a machine safe. So it is refused, and the
# refusal names the rules, because "remove those rules first" is useless
# without saying which.
#
# Taking a terminal BACK is never blocked: by definition nothing of ours is
# driving a released line.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import board_io as b                       # noqa: E402

CFG = tempfile.mkdtemp()

# No system on this box uses anything, unless a test says so.
_users = {}
b.port_users = lambda systems_dir=None: dict(_users)

# 1. Nothing is released to begin with.
assert b.released(CFG) == set()

# 2. A free terminal can be handed over, and it sticks.
assert b.set_released("DO1", True, None, CFG) == {"DO1"}
assert b.released(CFG) == {"DO1"}

# 3. ...and taken back.
assert b.set_released("DO1", False, None, CFG) == set()
assert b.released(CFG) == set()

# 4. A terminal a rule is bound to is REFUSED, and the message names the rule
#    and the system -- the whole point of refusing rather than warning.
_users["DO0"] = [{"slug": "packing", "label": "Zone 1 violations → DO", "kind": "output"}]
try:
    b.set_released("DO0", True, None, CFG)
    raise AssertionError("releasing a terminal in use must be refused")
except ValueError as e:
    msg = str(e)
    assert "packing" in msg, "the refusal must name the system"
    assert "Zone 1 violations" in msg, "the refusal must name the rule"
assert b.released(CFG) == set(), "a refused release must change nothing"

# 5. Taking one back is never blocked, even while rules reference it -- that
#    is the direction that makes the terminal MORE owned, not less.
b.set_released("DO0", False, None, CFG)

# 6. An unknown terminal is an error, not a silently stored name: a released
#    list full of typos would report terminals as handed over while the
#    platform kept driving them.
for bad in ("", "DO9", "nonsense"):
    try:
        b.set_released(bad, True, None, CFG)
        raise AssertionError(f"{bad!r} must not be accepted as a terminal")
    except ValueError:
        pass

# 7. Every terminal on the board map can be released -- the page covers all
#    eight, including the buzzer and the button.
for pid in ("DI0", "DI1", "DO0", "DO1", "L0", "L1", "BUZZER", "BUTTON"):
    _users.pop(pid, None)
    assert pid in b.set_released(pid, True, None, CFG), f"{pid} must be releasable"
assert len(b.released(CFG)) == 8

# 8. A released terminal idles the node that uses it rather than deleting it,
#    and the compiler is what enforces that (a warning + off_nodes, the same
#    treatment a switched-off capture node gets).
with open(os.path.join(ROOT, "core", "services", "workflow.py"),
          encoding="utf-8") as f:
    wf = f.read()
assert "_released_ports()" in wf, "the compiler must consult the released set"
assert wf.count("has been handed to another program") >= 2, \
    "both the output nodes and the Board Input must be idled"
assert "off_nodes.add(n[\"id\"])" in wf

# 9. The editor is told, so the palette can refuse the drag rather than let
#    someone drop a node that could only ever be idle.
with open(os.path.join(ROOT, "core", "routes", "workflow.py"),
          encoding="utf-8") as f:
    assert '"released_ports"' in f.read(), \
        "/api/workflow must carry the released terminals"
with open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
          encoding="utf-8") as f:
    page = f.read()
assert "allReleased" in page and "PORTS_OF" in page, \
    "the palette must gate board nodes on their terminals"

# 10. The route that changes ownership is admin-only.
with open(os.path.join(ROOT, "core", "routes", "hub.py"), encoding="utf-8") as f:
    hub = f.read()
assert "/api/hub/board/release" in hub
seg = hub.split("/api/hub/board/release", 1)[1].split("@router", 1)[0]
assert "_require_admin" in seg, "changing terminal ownership must be admin-only"

# 11. The panel lives inside the HUB shell, which re-asserts the shadcn
#     tokens as `.cy-app`. In that scope `--muted` is a BACKGROUND colour
#     (oklch 26.9% in dark mode), not a text colour -- the legacy `.muted`
#     class resolves to it and paints near-black text on a near-black card,
#     which is exactly how this page shipped unreadable. `.table` is another
#     trap: in this stylesheet it is only `display:table`, so it gives a
#     table no padding and no rules between rows.
with open(os.path.join(ROOT, "frontend", "src", "components", "BoardPanel.jsx"),
          encoding="utf-8") as f:
    panel = f.read()
for cls, why in (('className="muted', "`.muted` is a background colour in .cy-app"),
                 ('className="table', "`.table` is only display:table -- no padding, no rules"),
                 ('className="badge', "`.badge` is a legacy class, unstyled in .cy-app"),
                 ('className="stack', "`.stack` does not exist in this stylesheet")):
    assert cls not in panel, f"BoardPanel must not use {cls}...\": {why}"
assert "var(--muted-foreground" in panel,     "secondary text must use the token that MEANS secondary text in both themes"
assert "var(--muted," not in panel,     "--muted is a background colour in the hub shell"

# -- while Node-RED holds the board, the switches do not pretend otherwise ----
#
# A GPIO line has exactly one consumer, so the factory flow and this platform
# cannot both have the board -- which the toggle already said in prose. The
# per-terminal switches stayed live anyway, so each was a button that would
# fail, and the failure arrived as "held by another program (lg)" rather than
# as the thing the operator switched on two minutes earlier.
panel = io.open(os.path.join(ROOT, "frontend", "src", "components",
                             "BoardPanel.jsx"), encoding="utf-8").read()
assert "const nrOwns = !!data.nodered?.active" in panel,     "the page must know when Node-RED holds the board"
assert "nodered?.enabled" not in panel,     ("keyed on ACTIVE, not enabled: a terminal is held or free NOW, while "
     "enabled is what happens at the next boot")
# A prefix, not the whole expression: the same switch is also disabled for
# the front panel below, and pinning the exact string would make adding a
# THIRD claimant look like a regression rather than a feature.
assert "disabled={busy === p.id || nrOwns" in panel, "every terminal switch must be disabled while Node-RED holds the board"

# -- and the OTHER claimant, which is ours ------------------------------------
#
# The front panel runs in the hub process and takes its pins straight out of
# this same board map: PORT_BY_ID["BUTTON"] (BCM 4) and ["BUZZER"] (BCM 19).
# So while it runs, those two terminals are as unavailable to a workflow node
# as anything Node-RED holds -- and the page showed them owned by the platform
# and switchable, which was true of the platform and false of the terminal.
#
# Per terminal, not whole-board: the panel takes two, Node-RED takes the lot.
route = io.open(os.path.join(ROOT, "core", "routes", "hub.py"),
                encoding="utf-8").read()
assert 'panel_holds.append("BUTTON")' in route, "the button is claimed off the board map"
# The buzzer is claimed per beep rather than held, so a node CAN take it and
# the panel's next beep fails instead. An intermittent conflict is worse to
# diagnose than a steady one, so it counts as held here too.
assert 'panel_holds.append("BUZZER")' in route, "the buzzer is claimed off the board map too"
assert 'panel_cfg.get("button")' in route, "a panel running with the button switched OFF is not holding it"
assert '"holds": panel_holds' in route, "the board state must say which terminals the panel holds"
assert "panelHolds.has(p.id)" in panel, "the page must disable exactly those terminals, not the whole board"
assert "Disable front-panel control under Settings" in panel, "and say where to free them"
assert "Stop Node-RED below to control it from this device" in panel,     "a disabled switch must say why, not just refuse to move"

print("smoke_boardrelease OK")
