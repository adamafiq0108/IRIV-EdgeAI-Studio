# The Logs page must not offer a filter that cannot return rows.
#
# WHAT WAS WRONG. install() captured at INFO and never moved, so no DEBUG
# record ever entered the ring -- while the page's level dropdown offered
# "DEBUG and worse", which is the filter an operator reaches for first when
# they are hunting detail. It returned nothing, every time, and the page gave
# no reason. Worse, it was restart-only: seeing our own debug output on a
# customer board meant an SSH session and a bounced master, by which time the
# intermittent fault everyone was chasing had been and gone.
#
# WHAT IS PINNED HERE.
#   * a DEBUG record enters the ring when capture is DEBUG, and does not when
#     capture is INFO -- including the root-logger half, which is where the
#     record actually died before (the logger drops it and no handler runs);
#   * a third-party library stays out at BOTH settings, because matplotlib,
#     pymodbus and friends at DEBUG bury our lines in seconds and "Debug" on
#     this page has to mean OUR debug;
#   * the switch is readable back through exactly what the page reads, so the
#     level shown and the level captured cannot drift apart;
#   * the ring goes back to its normal capacity when DEBUG goes off, so a
#     forgotten switch does not leave a box holding 8000 lines for ever.
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import logs as logs_service                 # noqa: E402

# Our own module loggers are NOT cytron.*: everything in the product uses
# getLogger(__name__), so this is the shape a real line arrives in.
ours = logging.getLogger("core.services.sync")
theirs = logging.getLogger("matplotlib.font_manager")

logs_service.install()
assert logs_service.capture_level() == "INFO", \
    "the shipped default is INFO -- DEBUG as a permanent default makes the " \
    "ring useless for the overnight-failure case it exists for"


def emit_all():
    ours.debug("our debug line")
    ours.info("our info line")
    theirs.debug("third-party debug line")
    theirs.warning("third-party warning line")


def ring():
    return "\n".join(e["level"] + " " + e["logger"] + " " + e["message"]
                     for e in logs_service.query(limit=200))


# -- at the default, DEBUG is not captured ------------------------------------
logs_service.clear()
emit_all()
held = ring()
assert "our debug line" not in held, "DEBUG reached the ring at the INFO default"
assert "our info line" in held, held
assert "third-party debug line" not in held, held
assert "third-party warning line" in held, \
    "a library's WARNING must always come through -- 'urllib3 gave up' is " \
    "exactly the line an operator needs"
print("== at the INFO default, no debug is captured and library warnings still are")

# ...and the dropdown does not offer what it cannot return.
assert "DEBUG" not in logs_service.capture_levels(), \
    "the page still offers a DEBUG filter that can only return nothing"
assert logs_service.capture_levels()[0] == "INFO"
print("== the level filter offers only levels the ring is capturing")

# -- switch it on -------------------------------------------------------------
logs_service.set_capture("DEBUG")
assert logs_service.capture_level() == "DEBUG"
# The root logger has to come down too, or the record dies at the logger and
# no handler is ever called -- the actual defect, not the handler's level.
assert logging.getLogger().level <= logging.DEBUG, \
    "the root logger was left at INFO, so DEBUG records never reach a handler"

logs_service.clear()
emit_all()
held = ring()
assert "our debug line" in held, "capture is DEBUG and our debug still did not land"
assert "third-party debug line" not in held, \
    "a third-party library's debug reached the page and will bury ours:\n" + held
assert logging.getLogger("matplotlib").level >= logging.WARNING, \
    "matplotlib was not pinned, so it will emit per-font chatter at DEBUG"
print("== with capture on, our debug lands and a third-party's never does")

# The ring grows while it is on, because at DEBUG 2000 lines is minutes.
assert logs_service.capacity() == logs_service.DEBUG_RECORDS, \
    f"capacity stayed at {logs_service.capacity()} with debug on"
assert "DEBUG" in logs_service.capture_levels()
print(f"== the ring grows to {logs_service.DEBUG_RECORDS} lines while debug is on")

# -- and off again, all the way -----------------------------------------------
#
# The old code only ever LOWERED the root level, so a box switched to DEBUG
# could never be switched back and would run at DEBUG until someone restarted
# the service.
logs_service.set_capture("INFO")
assert logs_service.capture_level() == "INFO"
assert logging.getLogger().level == logging.INFO, \
    "the root level stayed at DEBUG -- the switch only works one way"
assert logs_service.capacity() == logs_service.MAX_RECORDS, \
    "the ring is still oversized after debug was switched off"
logs_service.clear()
emit_all()
assert "our debug line" not in ring(), "debug is still being captured after the switch off"
print("== switching back to info restores the root level, the capacity and the filter")

# -- what the page reads back is what is in force ------------------------------
#
# Through summary(), which is the exact payload /api/logs hands the page. The
# level shown and the level captured drifting apart is the original bug in a
# new shape.
for wanted in ("DEBUG", "INFO"):
    logs_service.set_capture(wanted)
    summary = logs_service.summary()
    assert summary["capture"] == wanted, summary
    assert summary["capacity"] == logs_service.capacity(), summary
    assert (wanted in logs_service.capture_levels()), logs_service.capture_levels()
logs_service.set_capture("INFO")
print("== the capture level the page reads is the one actually in force")

# An unknown level is not a way to turn logging off by accident.
logs_service.set_capture("VERBOSE")
assert logs_service.capture_level() == "INFO", logs_service.capture_level()
print("== an unrecognised level falls back to INFO rather than silencing the ring")

# -- install() re-applies the level it is given --------------------------------
#
# It used to return early whenever a RingHandler was already attached, so a
# second caller's level was silently ignored -- which is how a test (or a
# reload) could leave the ring capturing something nobody asked for.
logs_service.install(logging.DEBUG)
assert logs_service.capture_level() == "DEBUG", \
    "install() ignored the level because the handler already existed"
handlers = [h for h in logging.getLogger().handlers
            if isinstance(h, logs_service.RingHandler)]
assert len(handlers) == 1, f"install() duplicated the handler: {len(handlers)}"
logs_service.set_capture("INFO")
print("== install() stays single-handler and still honours the level asked for")

print("ALL OK")
