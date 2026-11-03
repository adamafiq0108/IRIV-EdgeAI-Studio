# The event catalog is a contract, not a lookup table.
#
# WHAT WAS WRONG. There were no event codes at all. The same failure -- a
# store the board could not write to -- came back as a 400 from the model
# upload, a 500 from the media upload, a 502 from the video proxy and a 503
# from anything touching the database, each with its own sentence, most of
# them quoting an errno and an absolute path at an operator standing next to
# a machine. Support could not ask "what code does it show", because there
# was nothing to show.
#
# WHAT IS PINNED HERE, in the order it is easiest to break by accident.
#
#   * EVERY ENTRY IS COMPLETE. Six fields, none blank. The field that goes
#     missing first is `action`, because it is the one you have to think
#     about, and an entry with no suggested action is the one somebody meets
#     at two in the morning with nowhere to go.
#
#   * IDS ARE UNIQUE AND NEVER REUSED. A recycled id turns every old
#     screenshot, every support ticket and every customer's notebook into a
#     lie. Retired ids are listed and stay burned.
#
#   * EVERY CATEGORY IS ONE OF THE THIRTEEN, and all thirteen are used. An
#     uncategorised row is one the Logs page's filter can never reach, so it
#     may as well not have been logged.
#
#   * FIVE SEVERITIES, NOT SEVEN. SUCCESS and FATAL were asked for and
#     declined: `status` already carries success/failed, and logging.FATAL is
#     the same number as CRITICAL, so the two would drift. Pinned so the
#     decision has to be re-argued rather than quietly reversed.
#
#   * CRITICAL STAYS RARE. The exact set is written out below. Adding one
#     fails this test on purpose -- that is the conversation, not the diff.
#
#   * NO PATH, NO ERRNO, NO TRACEBACK, NO DSN in anything an operator reads.
#     This is the one that will be broken first, because pasting the
#     exception into the message reads perfectly well on a dev box.
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import log_catalog as catalog             # noqa: E402

FIELDS = ("code", "event_id", "category", "severity", "message", "action")

# -- every entry is complete ---------------------------------------------------
assert catalog.CATALOG, "the catalog is empty"
for entry in catalog.CATALOG.values():
    assert tuple(entry._fields) == FIELDS, \
        f"the Entry shape changed: {entry._fields} -- Ying builds table " \
        f"columns from these names"
    for field in FIELDS:
        value = getattr(entry, field)
        assert isinstance(value, str) and value.strip(), \
            f"{entry.code}: {field} is empty"
print(f"== all {len(catalog.CATALOG)} entries carry all six fields")

# The key and the entry's own `code` cannot drift apart, or a lookup by name
# returns an entry describing something else.
for code, entry in catalog.CATALOG.items():
    assert code == entry.code, f"{code} holds an entry for {entry.code}"

# -- ids are unique, and never reused ------------------------------------------
ids = [e.event_id for e in catalog.CATALOG.values()]
duplicates = sorted({i for i in ids if ids.count(i) > 1})
assert not duplicates, f"two events share an id: {duplicates}"
assert len(catalog.BY_EVENT_ID) == len(catalog.CATALOG), \
    "BY_EVENT_ID lost entries, so a lookup by the id on a screenshot fails"
reused = sorted(set(ids) & set(catalog.RETIRED_IDS))
assert not reused, \
    f"a retired id was handed to a new event: {reused} -- every old support " \
    f"ticket quoting it now describes the wrong fault"
print(f"== {len(ids)} ids, all unique, none reused from the retired list")

# -- every category is one of the thirteen, and all thirteen are used ----------
assert len(catalog.CATEGORIES) == 13, \
    f"the category list is {len(catalog.CATEGORIES)} long, not 13"
used = {e.category for e in catalog.CATALOG.values()}
stray = sorted(used - set(catalog.CATEGORIES))
assert not stray, f"uncategorised events: {stray}"
empty = sorted(set(catalog.CATEGORIES) - used)
assert not empty, \
    f"categories with no events at all: {empty} -- a filter that can only " \
    f"return nothing is the Logs page's oldest bug"
print("== every event is in one of the thirteen categories, and all thirteen "
      "are populated")

# -- an id sits in its category's own block ------------------------------------
#
# Not cosmetic: the operator reads the prefix as the category. "NET-3001 in
# the Storage category" is a code that lies at a glance.
for entry in catalog.CATALOG.values():
    prefix, base = catalog.BLOCKS[entry.category]
    head, _, number = entry.event_id.partition("-")
    assert head == prefix, \
        f"{entry.event_id} is {entry.category} but carries the {head} prefix"
    assert number.isdigit(), f"{entry.event_id} has no numeric part"
    assert base <= int(number) < base + 1000, \
        f"{entry.event_id} is outside the {entry.category} block ({base})"
print("== every id sits in its own category's block, with room left in each")

# -- five severities, not seven ------------------------------------------------
assert catalog.SEVERITIES == ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), \
    catalog.SEVERITIES
for banned in ("SUCCESS", "FATAL"):
    assert banned not in catalog.SEVERITIES, (
        f"{banned} came back as a severity. It was declined on purpose: "
        "SUCCESS duplicates the record's `status` field, and FATAL is the "
        "same level number as CRITICAL, so a row written as one reads back "
        "as the other and a filter silently misses it.")
for entry in catalog.CATALOG.values():
    assert entry.severity in catalog.SEVERITIES, \
        f"{entry.event_id} has severity {entry.severity!r}"
print("== five severities only; SUCCESS and FATAL stay out")

# -- CRITICAL stays rare -------------------------------------------------------
#
# CRITICAL means the machine has stopped doing the job it was bought for, or
# cannot be trusted. Written out in full so that promoting an event to
# CRITICAL is a deliberate act with a test to update, rather than a one-word
# diff nobody reviews. If everything is critical, nothing is.
CRITICAL = {
    "AI_HAILO_ABSENT",              # the board cannot reach its accelerator
    "AI_SERVICE_CRASHED",           # a detection box that stopped detecting
    "AI_PIPELINE_STALLED",          # camera still running, results stopped
    "STO_DISK_FULL",                # nothing is being recorded any more
    "SEC_BUNDLE_SIGNATURE_BAD",     # a package that is not ours
}
actual = {e.code for e in catalog.CATALOG.values() if e.severity == "CRITICAL"}
assert actual == CRITICAL, (
    "the CRITICAL set changed.\n"
    f"  added:   {sorted(actual - CRITICAL)}\n"
    f"  removed: {sorted(CRITICAL - actual)}\n"
    "CRITICAL is the level that gets somebody out of a chair. Keep it for "
    "a box that has stopped detecting, a board that cannot reach its own "
    "accelerator, a disk with nothing left, or a bundle that failed its "
    "signature.")
assert len(actual) * 8 < len(catalog.CATALOG), \
    "more than an eighth of the catalog is CRITICAL, which makes the level " \
    "meaningless on a page"
print(f"== exactly {len(actual)} events are CRITICAL, and they are the "
      f"right {len(actual)}")

# -- nothing technical in anything an operator reads ---------------------------
#
# `message` and `action` both appear in front of a person. The errno, the
# path and the traceback live on the record's `detail`, for the expandable
# panel, which is where they are actually useful.
LEAKS = (
    (re.compile(r"\[?Errno\b", re.I), "an errno"),
    (re.compile(r"\bE(?:ACCES|NOENT|NOSPC|PIPE|CONNREFUSED|TIMEDOUT|IO)\b"),
     "an errno name"),
    (re.compile(r"/[A-Za-z0-9_.\-]+/"), "a path"),
    (re.compile(r"[A-Za-z]:\\"), "a Windows path"),
    (re.compile(r"\.py\b"), "a source file"),
    (re.compile(r"\bTraceback\b", re.I), "a traceback"),
    (re.compile(r"\w+://"), "a URL or DSN"),
    (re.compile(r"\bpostgres(?:ql)?\b", re.I), "a DSN scheme"),
    (re.compile(r"\{\w+\}|%s|%d"), "an unfilled placeholder"),
    (re.compile(r"\bNone\b|\bnull\b"), "a Python None"),
    (re.compile(r"0x[0-9a-f]{4,}", re.I), "a memory address"),
)
for entry in catalog.CATALOG.values():
    for field in ("message", "action"):
        text = getattr(entry, field)
        for pattern, what in LEAKS:
            assert not pattern.search(text), \
                f"{entry.event_id} {field} carries {what}: {text!r}"
        assert text[0].isupper(), \
            f"{entry.event_id} {field} does not start as a sentence: {text!r}"
        assert text.rstrip().endswith((".", "?")), \
            f"{entry.event_id} {field} is not a sentence: {text!r}"
        assert len(text) <= 220, \
            f"{entry.event_id} {field} is {len(text)} characters -- too long " \
            f"for a toast or a panel"
print("== no message or action carries a path, an errno, a traceback or a DSN")

# The register the operator asked for, pinned on the one they quoted back.
assert catalog.CATALOG["AI_SERVICE_UNAVAILABLE"].message == \
    "AI detection service is unavailable. Live preview cannot be displayed.", \
    catalog.CATALOG["AI_SERVICE_UNAVAILABLE"].message

# -- the same failure cannot answer with two different statuses ----------------
for code in catalog.HTTP_STATUS:
    assert code in catalog.CATALOG, \
        f"HTTP_STATUS names {code}, which is not an event"
for code, status in catalog.HTTP_STATUS.items():
    assert 100 <= int(status) <= 599, f"{code} maps to status {status}"
print(f"== {len(catalog.HTTP_STATUS)} codes pin a single HTTP status each")

# -- the lookups a caller actually uses ----------------------------------------
#
# get() must never raise: a mistyped code inside a logging call would
# otherwise take the request down, which is strictly worse than a row that
# reads "unknown".
assert catalog.get("NOT_A_REAL_CODE") is None
assert catalog.get(None) is None
assert catalog.entry_of("NOT_A_REAL_CODE").code == "API_UNHANDLED", \
    "an unknown code produced an uncategorised record"
assert catalog.entry_of("ai_service_unavailable").event_id == "AI-5003", \
    "codes are not case-insensitive, so a lowercase call site logs as unknown"
assert catalog.status_for("DB_UNAVAILABLE") == 503
assert catalog.status_for("NOT_A_REAL_CODE") == 500

rows = catalog.as_dicts()
assert len(rows) == len(catalog.CATALOG)
assert set(rows[0]) == set(FIELDS), \
    f"the API payload's keys drifted from the Entry fields: {sorted(rows[0])}"
order = [catalog.CATEGORIES.index(r["category"]) for r in rows]
assert order == sorted(order), \
    "as_dicts() is not grouped by category, so the reference table is shuffled"
print("== lookups are total, case-insensitive, and the API payload matches "
      "the Entry fields")

print("ALL OK")
