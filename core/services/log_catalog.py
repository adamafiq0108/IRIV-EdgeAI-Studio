# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
log_catalog.py -- the permanent list of things that can happen to this product.

WHY A CATALOG AT ALL. The same failure used to read four different ways
depending on which route caught it: an unwritable store was "the model folder
is not writable: [Errno 13] Permission denied" on one page, "could not save"
with a 500 on another, and a bare 502 on a third. An operator cannot search for
a fault they have seen before, and support cannot ask "what was the code?",
because there were no codes. Here the ENTRY owns the wording -- the call site
passes facts, never prose -- so an identical failure reads identically on the
Logs page, in a support mail and in a screenshot from a customer site.

WHY THE IDS LOOK LIKE THIS. Operators on this product come from Siemens and
Cognex equipment, where a fault is a short alphanumeric code they can quote
down a phone line. One prefix per category, one thousand-block per prefix, and
numbering that leaves gaps inside each block so a new event can be filed next
to its neighbours instead of at the end. An id is PERMANENT: once shipped it
keeps its meaning for ever, and an event that goes away leaves its number
burned rather than freeing it for something else -- a recycled code turns every
old screenshot and support ticket into a lie.

WHY ONLY FIVE SEVERITIES. The operator asked for SUCCESS and FATAL as well.
Both were declined on purpose: `status` already carries success/failed on every
record, so SUCCESS would be a second, drift-prone copy of it, and Python's
logging.FATAL is an alias for CRITICAL (the same number, 50), so a record could
be written FATAL and read back CRITICAL. Two names for one level is a filter
that silently misses rows.

CRITICAL IS RARE BY DESIGN. It means the machine has stopped doing the job it
was bought for, or cannot be trusted: the accelerator is missing, the detection
pipeline has stopped producing results, the disk is full, or a bundle's
signature did not verify. Everything else that is merely broken is ERROR. If
CRITICAL ever becomes common on a page it stops being a reason to walk to the
line.

NO PATHS, NO ERRNOS, NO TRACEBACKS IN `message`. The user-facing text is what
appears on a wall-mounted panel next to a running machine; the technical text
travels separately on the record, for the expandable panel on the Logs page.
smoke_logcatalog.py pins that, because the natural thing for the next person to
do is paste the exception into the message, and it reads fine on a dev box.

No framework imports at module scope (docs/conventions.md sec 2): the masters,
the app processes and the tests all import this, and most of them have no web
framework loaded at all.
"""

import collections

#: The only categories that exist. A record with anything else is a bug, not a
#: new category -- the Logs page's filter is built from this tuple, so an
#: unlisted value is a row the operator can never filter to.
CATEGORIES = (
    "System",
    "Camera",
    "AI Model",
    "AI Inference",
    "Network",
    "API",
    "Database",
    "Storage",
    "Authentication",
    "User Activity",
    "Audit Trail",
    "Security",
    "Performance",
)

#: Names and order match logging's own levels so a catalog severity can be
#: handed straight to logger.log() without a translation table to get wrong.
SEVERITIES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: Which prefix and thousand-block belongs to which category. Kept as data
#: rather than as a convention in someone's head, because the smoke test uses
#: it to prove no entry has strayed into a neighbour's block.
BLOCKS = {
    "System": ("SYS", 1000),
    "Camera": ("CAM", 2000),
    "Network": ("NET", 3000),
    "AI Model": ("MDL", 4000),
    "AI Inference": ("AI", 5000),
    "API": ("API", 6000),
    "Database": ("DB", 7000),
    "Storage": ("STO", 8000),
    "Authentication": ("AUTH", 9000),
    "User Activity": ("USR", 10000),
    "Audit Trail": ("AUD", 11000),
    "Security": ("SEC", 12000),
    "Performance": ("PERF", 13000),
}

#: One event. Six fields, all required -- an entry missing its `action` is the
#: one an operator meets at two in the morning with nothing to try next, which
#: is the whole reason the field exists.
Entry = collections.namedtuple(
    "Entry", "code event_id category severity message action")

#: Ids that once existed and must never be handed to a different event. Empty
#: today; the first retirement goes here rather than quietly vanishing, or the
#: number gets reused and every old support ticket starts lying.
RETIRED_IDS = frozenset()

_ENTRIES = [
    # -- System (SYS-1xxx) ---------------------------------------------------
    ("SYS_STARTED", "SYS-1001", "System", "INFO",
     "The system started.",
     "No action needed."),
    ("SYS_SHUTDOWN", "SYS-1002", "System", "INFO",
     "The system is shutting down.",
     "No action needed."),
    ("SYS_CONFIG_INVALID", "SYS-1010", "System", "ERROR",
     "The system settings could not be read and defaults are in use.",
     "Open Settings, check the values and save again."),
    ("SYS_MASTER_UNREACHABLE", "SYS-1020", "System", "ERROR",
     "A vision system on this board is not responding.",
     "Restart the system from the dashboard. If it does not come back, "
     "restart the board."),
    ("SYS_APP_EXITED", "SYS-1021", "System", "ERROR",
     "The detection application stopped unexpectedly and is being restarted.",
     "Watch for a repeat. Repeated restarts mean the model or the camera "
     "settings need attention."),
    ("SYS_PROCESS_CRASHED", "SYS-1022", "System", "ERROR",
     "A program on this board stopped unexpectedly and is being restarted.",
     "Check the affected systems are running. Report this code if it "
     "happens again."),
    ("SYS_TASK_STOPPED", "SYS-1023", "System", "ERROR",
     "A background task stopped, so the feature it runs is no longer "
     "working.",
     "Restart the board to bring the task back, then report this code."),
    ("SYS_OUTPUT_FAILED", "SYS-1070", "System", "ERROR",
     "An alert or output action did not complete.",
     "Open the workflow, check that action's settings, and run it again."),
    ("SYS_UPDATE_CHECK_FAILED", "SYS-1030", "System", "WARNING",
     "The board could not check for a software update.",
     "Confirm the board has internet access, then check again."),
    ("SYS_UPDATE_APPLY_FAILED", "SYS-1031", "System", "ERROR",
     "The software update could not be installed. The board is still running "
     "the previous version.",
     "Check for updates again. If it fails a second time, contact support "
     "with this code."),
    ("SYS_UPDATE_BUSY", "SYS-1032", "System", "WARNING",
     "An update is already running on this board.",
     "Wait for the update in progress to finish before starting another."),
    ("SYS_UPDATE_ROLLED_BACK", "SYS-1033", "System", "WARNING",
     "The board was rolled back to its previous software version.",
     "Confirm the systems are running, then report the version that failed."),
    ("SYS_UPDATE_SUPERSEDED", "SYS-1034", "System", "WARNING",
     "A newer version was published since this board last checked, so the "
     "one shown was not installed.",
     "The Update card now shows the newest version. Press Update again to "
     "install it."),
    ("SYS_IO_PORT_HELD", "SYS-1040", "System", "WARNING",
     "That terminal is already in use by another system on this board.",
     "Release the terminal in the system that holds it, or choose a "
     "different terminal."),
    ("SYS_POWER_OFF_BUSY", "SYS-1050", "System", "WARNING",
     "The board is already shutting down.",
     "Wait for the board to power off."),
    ("SYS_POWER_OFF_FAILED", "SYS-1051", "System", "ERROR",
     "The board could not be shut down.",
     "Power the board off at the supply if it will not shut down from here."),
    ("SYS_ALERT_SUPPRESSED", "SYS-1060", "System", "INFO",
     "An alert was not sent because the same alert fired recently.",
     "Adjust the alert's quiet period if you expect to be told every time."),
    # Written by the Logs page's Clear Log as the one row it leaves behind,
    # with the operator and the count in the message (core/routes/logs.py).
    ("SYS_LOG_CLEARED", "SYS-1080", "System", "INFO",
     "The log was cleared.",
     "No action needed. Records from before this point are gone."),

    # -- Camera (CAM-2xxx) ---------------------------------------------------
    ("CAM_CONNECTED", "CAM-2001", "Camera", "INFO",
     "The camera is connected.",
     "No action needed."),
    ("CAM_DISCONNECTED", "CAM-2002", "Camera", "ERROR",
     "The camera stopped responding and the live view has ended.",
     "Check the camera cable and power, then restart the system."),
    ("CAM_NOT_FOUND", "CAM-2003", "Camera", "ERROR",
     "The selected camera is not attached to this board.",
     "Reconnect the camera, or pick a different one in Camera settings."),
    ("CAM_UNREACHABLE", "CAM-2004", "Camera", "ERROR",
     "The camera could not be reached over the network.",
     "Check the camera is powered, on the same network, and still at the "
     "address it was set up with."),
    ("CAM_BUSY", "CAM-2010", "Camera", "WARNING",
     "That camera is already in use by another system on this board.",
     "Stop the system using the camera, or choose a different camera."),
    ("CAM_STREAM_STALLED", "CAM-2020", "Camera", "ERROR",
     "The camera stopped sending pictures.",
     "Check the cable, then restart the system. A USB camera on a long lead "
     "is the usual cause."),
    ("CAM_SETTING_REJECTED", "CAM-2030", "Camera", "WARNING",
     "The camera did not accept that setting and kept its previous value.",
     "Choose a value the camera supports, shown in the settings list."),

    # -- Network (NET-3xxx) --------------------------------------------------
    ("NET_INTERFACE_DOWN", "NET-3001", "Network", "ERROR",
     "A network port on this board has no link.",
     "Check the network cable and the switch port."),
    ("NET_PEER_UNREACHABLE", "NET-3010", "Network", "ERROR",
     "Another board on the network could not be reached.",
     "Confirm the other board is powered on and on the same network."),
    ("NET_UPSTREAM_TIMEOUT", "NET-3011", "Network", "WARNING",
     "A device took too long to answer and the request was given up on.",
     "Check the device is powered and reachable, then try again."),
    ("NET_MODBUS_NO_REPLY", "NET-3020", "Network", "WARNING",
     "The Modbus device did not reply.",
     "Check the wiring, the unit address and the baud rate."),
    ("NET_MQTT_DISCONNECTED", "NET-3030", "Network", "WARNING",
     "The MQTT broker connection dropped and is being retried.",
     "Check the broker address and that the broker is running."),
    ("NET_TELEGRAM_FAILED", "NET-3040", "Network", "WARNING",
     "The Telegram message could not be sent.",
     "Check the board's internet access and the bot token."),
    ("NET_SMTP_FAILED", "NET-3041", "Network", "ERROR",
     "The email could not be sent.",
     "Check the mail server address, the port and the account password in "
     "Email settings."),

    # -- AI Model (MDL-4xxx) -------------------------------------------------
    ("MDL_LOADED", "MDL-4001", "AI Model", "INFO",
     "The detection model loaded.",
     "No action needed."),
    ("MDL_LOAD_FAILED", "MDL-4010", "AI Model", "ERROR",
     "The detection model could not be loaded.",
     "Choose a different model, or upload the model again."),
    ("MDL_NOT_FOUND", "MDL-4011", "AI Model", "ERROR",
     "The selected detection model is not installed on this board.",
     "Pick a model from the model list, or upload the one you need."),
    ("MDL_UPLOAD_REJECTED", "MDL-4020", "AI Model", "WARNING",
     "That file was not accepted as a detection model.",
     "Upload a model built for this board's accelerator."),
    ("MDL_STORE_UNWRITABLE", "MDL-4021", "AI Model", "ERROR",
     "The model could not be saved to this board.",
     "Free up space on the board, then upload the model again."),

    # -- AI Inference (AI-5xxx) ----------------------------------------------
    ("AI_HAILO_BUSY", "AI-5001", "AI Inference", "WARNING",
     "The AI accelerator is in use by another system on this board.",
     "Stop the system using the accelerator, then start this one."),
    ("AI_HAILO_ABSENT", "AI-5002", "AI Inference", "CRITICAL",
     "This board cannot reach its AI accelerator. Detection is not running.",
     "Power the board down fully, check the accelerator is seated, then "
     "power it back on."),
    ("AI_SERVICE_UNAVAILABLE", "AI-5003", "AI Inference", "ERROR",
     "AI detection service is unavailable. Live preview cannot be displayed.",
     "Restart the system from the dashboard."),
    ("AI_SERVICE_CRASHED", "AI-5004", "AI Inference", "CRITICAL",
     "The AI detection service stopped. Nothing is being detected.",
     "Restart the system. If it stops again, report this code with the time "
     "it happened."),
    ("AI_PIPELINE_STALLED", "AI-5010", "AI Inference", "CRITICAL",
     "Detection has stopped producing results while the camera is still "
     "running.",
     "Restart the system. If it stalls again, reduce the number of cameras "
     "or choose a smaller model."),
    ("AI_MODEL_RELOADED", "AI-5020", "AI Inference", "INFO",
     "Detection restarted with a different model.",
     "No action needed."),

    # -- API (API-6xxx) ------------------------------------------------------
    ("API_REQUEST", "API-6001", "API", "INFO",
     "A request was handled.",
     "No action needed."),
    ("API_BAD_REQUEST", "API-6010", "API", "WARNING",
     "That request could not be understood and nothing was changed.",
     "Check the values on the page and try again."),
    ("API_NOT_FOUND", "API-6011", "API", "WARNING",
     "The item you asked for no longer exists.",
     "Refresh the page to see what is there now."),
    ("API_CONFLICT", "API-6012", "API", "WARNING",
     "That action cannot be done while the previous one is still running.",
     "Wait for the action in progress to finish, then try again."),
    ("API_CLIENT_DISCONNECTED", "API-6020", "API", "INFO",
     "The browser closed the page before the answer was ready.",
     "No action needed."),
    ("API_UPSTREAM_BAD_GATEWAY", "API-6030", "API", "ERROR",
     "A service on this board did not answer.",
     "Restart the system from the dashboard, then reload the page."),
    ("API_UNHANDLED", "API-6040", "API", "ERROR",
     "The system could not complete that request.",
     "Try again. If it keeps happening, report this code and the time."),

    # -- Database (DB-7xxx) --------------------------------------------------
    ("DB_UNAVAILABLE", "DB-7001", "Database", "ERROR",
     "The database is unavailable, so stored history cannot be read or "
     "changed. Live detection is unaffected.",
     "Check the database service is running on this board, then try again."),
    ("DB_QUERY_FAILED", "DB-7010", "Database", "ERROR",
     "The database could not answer that request.",
     "Try again. If it keeps happening, report this code."),
    ("DB_WRITE_TIMEOUT", "DB-7011", "Database", "WARNING",
     "The database took too long to accept a write and the record was "
     "dropped.",
     "Check the board is not out of disk space."),
    ("DB_RETENTION_PURGED", "DB-7020", "Database", "INFO",
     "Old records were removed to keep the database within its limit.",
     "No action needed. Raise the retention period if you need longer "
     "history."),

    # -- Storage (STO-8xxx) --------------------------------------------------
    ("STO_DISK_LOW", "STO-8001", "Storage", "WARNING",
     "This board is running low on storage.",
     "Remove old recordings or exports, or reduce the retention period."),
    ("STO_DISK_FULL", "STO-8002", "Storage", "CRITICAL",
     "This board has no storage left. Recordings and history are no longer "
     "being saved.",
     "Free space now: remove old recordings and exports."),
    ("STO_WRITE_FAILED", "STO-8010", "Storage", "ERROR",
     "A file could not be saved on this board.",
     "Check the board has free space, then try again."),
    ("STO_FILE_NOT_FOUND", "STO-8011", "Storage", "WARNING",
     "That file is no longer on this board.",
     "Refresh the page to see what is there now."),
    ("STO_MEDIA_SAVE_FAILED", "STO-8020", "Storage", "ERROR",
     "The uploaded file could not be saved.",
     "Check the board has free space, then upload it again."),

    # -- Authentication (AUTH-9xxx) ------------------------------------------
    ("AUTH_LOGIN_OK", "AUTH-9001", "Authentication", "INFO",
     "A user signed in.",
     "No action needed."),
    ("AUTH_LOGIN_FAILED", "AUTH-9002", "Authentication", "WARNING",
     "Sign in failed. The username or password was not correct.",
     "Try again. Ask an administrator to reset the password if needed."),
    ("AUTH_LOGOUT", "AUTH-9003", "Authentication", "INFO",
     "A user signed out.",
     "No action needed."),
    ("AUTH_SESSION_EXPIRED", "AUTH-9010", "Authentication", "INFO",
     "The session ended and the user was signed out.",
     "Sign in again."),
    ("AUTH_FORBIDDEN", "AUTH-9020", "Authentication", "WARNING",
     "This account is not allowed to do that.",
     "Ask an administrator to carry out the action, or to change the "
     "account's role."),
    # A signed-in account mistyping its OWN current password (password
    # change, rename, account setup). It used to fall through to the bare
    # 403 -> AUTH_FORBIDDEN, whose action told the administrator to "ask an
    # administrator" about their own typo (UAT-119).
    ("AUTH_WRONG_CURRENT", "AUTH-9021", "Authentication", "WARNING",
     "The current password is not correct.",
     "Type the password you signed in with."),
    ("AUTH_PASSWORD_CHANGED", "AUTH-9030", "Authentication", "INFO",
     "A password was changed.",
     "No action needed."),

    # -- User Activity (USR-10xxx) -------------------------------------------
    ("USR_SYSTEM_CREATED", "USR-10001", "User Activity", "INFO",
     "A vision system was created.",
     "No action needed."),
    ("USR_SYSTEM_DELETED", "USR-10002", "User Activity", "INFO",
     "A vision system was deleted.",
     "No action needed."),
    ("USR_SETTING_CHANGED", "USR-10010", "User Activity", "INFO",
     "A setting was changed.",
     "No action needed."),
    ("USR_DEVICE_RENAMED", "USR-10020", "User Activity", "INFO",
     "A board was renamed.",
     "No action needed."),

    # -- Audit Trail (AUD-11xxx) ---------------------------------------------
    ("AUD_RECORDED", "AUD-11001", "Audit Trail", "INFO",
     "An action was written to the audit trail.",
     "No action needed."),
    ("AUD_WRITE_FAILED", "AUD-11010", "Audit Trail", "ERROR",
     "An action could not be written to the audit trail.",
     "Check the database is running. The action itself still took effect."),
    ("AUD_EXPORTED", "AUD-11020", "Audit Trail", "INFO",
     "The audit trail was exported.",
     "No action needed."),

    # -- Security (SEC-12xxx) ------------------------------------------------
    ("SEC_SHIPPED_PASSWORD", "SEC-12001", "Security", "WARNING",
     "An account is still using the password the board shipped with.",
     "Change the password now. A shipped password is public knowledge."),
    ("SEC_LICENSE_INVALID", "SEC-12010", "Security", "ERROR",
     "The licence for that template is not valid on this board.",
     "Check the licence key, or request one issued for this board."),
    ("SEC_BUNDLE_SIGNATURE_BAD", "SEC-12020", "Security", "CRITICAL",
     "A downloaded package failed its signature check and was not installed.",
     "Do not retry over the same network. Report this code to support."),
    ("SEC_RATE_ABUSE", "SEC-12030", "Security", "WARNING",
     "Too many attempts came from one source and were turned away.",
     "Wait, then try again. Repeated bursts should be reported."),

    # -- Performance (PERF-13xxx) --------------------------------------------
    ("PERF_SLOW_REQUEST", "PERF-13001", "Performance", "WARNING",
     "A page took much longer than usual to answer.",
     "Check how many systems are running on this board at once."),
    ("PERF_FPS_BELOW_TARGET", "PERF-13010", "Performance", "WARNING",
     "Detection is running slower than the rate this system was set up for.",
     "Reduce the number of cameras, or choose a smaller model."),
    ("PERF_TEMP_THROTTLE", "PERF-13020", "Performance", "WARNING",
     "The board is hot and has slowed itself down.",
     "Improve airflow around the board and check the fan."),
    ("PERF_QUEUE_BACKLOG", "PERF-13030", "Performance", "WARNING",
     "Work is arriving faster than this board can process it and results are "
     "falling behind.",
     "Reduce the camera frame rate, or the number of running systems."),
]

CATALOG = {row[0]: Entry(*row) for row in _ENTRIES}

BY_EVENT_ID = {entry.event_id: entry for entry in CATALOG.values()}

#: The HTTP status a code answers with, so the SAME failure cannot leave one
#: route as a 400 and another as a 500. It was exactly that -- an unwritable
#: store answered 400, 500, 502 and 503 in four different routes -- that made
#: "what does this error mean" unanswerable. Codes absent here are not raised
#: over HTTP, or their status genuinely varies with the caller.
HTTP_STATUS = {
    "SYS_UPDATE_CHECK_FAILED": 502,
    "SYS_UPDATE_APPLY_FAILED": 400,
    "SYS_UPDATE_BUSY": 409,
    "SYS_UPDATE_SUPERSEDED": 409,
    "SYS_POWER_OFF_BUSY": 409,
    "SYS_POWER_OFF_FAILED": 500,
    "SYS_IO_PORT_HELD": 409,
    "CAM_NOT_FOUND": 404,
    "CAM_BUSY": 409,
    "MDL_NOT_FOUND": 404,
    "MDL_UPLOAD_REJECTED": 400,
    "MDL_STORE_UNWRITABLE": 507,
    "AI_HAILO_BUSY": 409,
    "AI_SERVICE_UNAVAILABLE": 503,
    "API_BAD_REQUEST": 400,
    "API_NOT_FOUND": 404,
    "API_CONFLICT": 409,
    "API_CLIENT_DISCONNECTED": 499,
    "API_UPSTREAM_BAD_GATEWAY": 502,
    "API_UNHANDLED": 500,
    "DB_UNAVAILABLE": 503,
    "DB_QUERY_FAILED": 500,
    "STO_WRITE_FAILED": 507,
    "STO_FILE_NOT_FOUND": 404,
    "STO_MEDIA_SAVE_FAILED": 507,
    "AUTH_LOGIN_FAILED": 401,
    "AUTH_SESSION_EXPIRED": 401,
    "AUTH_FORBIDDEN": 403,
    "AUTH_WRONG_CURRENT": 403,
    "SEC_LICENSE_INVALID": 403,
    "SEC_RATE_ABUSE": 429,
}


def get(code):
    """The entry for a code, or None.

    Never raises. A logging call that blows up because somebody mistyped a
    code takes the request down with it, which is a far worse outcome than a
    line that reads "unknown".
    """
    return CATALOG.get(str(code or "").upper())


def entry_of(code):
    """The entry for a code, falling back to API_UNHANDLED so every record
    that reaches the ring still carries an id and a category -- an
    uncategorised row is one the Logs page's filters can never show."""
    return get(code) or CATALOG["API_UNHANDLED"]


def status_for(code, default=500):
    return HTTP_STATUS.get(str(code or "").upper(), default)


def all_entries():
    """Every entry, in the order the Logs page's reference table shows them:
    by category in CATEGORIES order, then by id."""
    order = {name: i for i, name in enumerate(CATEGORIES)}
    return sorted(CATALOG.values(),
                  key=lambda e: (order.get(e.category, 99), e.event_id))


def as_dicts():
    """The catalog as plain dicts -- what /api/logs/catalog hands the page."""
    return [dict(entry._asdict()) for entry in all_entries()]


def http_error(code, cause=None, status=None, level=None, details=None, **meta):
    """Build the HTTPException for a catalog code.

    The browser gets the entry's wording and nothing else. `cause` is the
    technical text (the errno, the path, the driver's complaint); it rides on
    the exception for the ring to pick up, because that string is the only
    thing that ever actually diagnoses the fault, and throwing it away to keep
    the panel clean is how we ended up with unfixable bug reports.

    fastapi is imported here rather than at module scope so a master or an app
    process can use the catalog without a web framework loaded.

    `level` is for a raise site that has HANDLED the failure (an upstream that
    is down, answered by a sentence): the record is written at that level and
    without a traceback, because the traceback of a deliberate raise points at
    the raise and diagnoses nothing. `details` is technical text the response
    carries in its own `details` field -- never in `error`, which is the toast.
    """
    from fastapi import HTTPException

    entry = entry_of(code)
    exc = HTTPException(status_code=status or status_for(entry.code),
                        detail=entry.message)
    exc.event_code = entry.code
    exc.event_cause = None if cause is None else str(cause)
    exc.event_meta = meta or {}
    exc.event_level = level
    exc.event_details = None if details is None else str(details)
    return exc
