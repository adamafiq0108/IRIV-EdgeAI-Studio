# A credential must not survive the redactor onto the Logs page.
#
# WHAT IS WRONG TODAY. core/services/logs.py says in its own docstring that
# "a stack trace with a bearer token in it becomes a credential leak the
# moment the page is shared", and then its regex does not catch the forms a
# real process actually produces. Measured against today's code:
#
#   'Authorization: Bearer abc.def.ghi'  ->  'Authorization=*** abc.def.ghi'
#   '{"password": "hunter2"}'            ->  unchanged
#   "{'token': 'abc123'}"                ->  unchanged
#   'postgresql://user:s3cret@10.0.0.5/db' -> unchanged
#
# The pattern wants `key=value` or `key: value` with nothing in between, so a
# quote character defeats it -- and where it does fire, `\S+` eats only the
# word "Bearer" and leaves the token standing next to a masked key, which
# reads as redacted and is not.
#
# WHY IT MATTERS IN THE FIELD. /api/logs is admin-only but the page is not:
# it is screenshotted, pasted into a support chat and mailed to us, because
# that is the whole point of having it instead of journalctl. A token that
# reaches that page reaches everyone the screenshot reaches, and on a customer
# board there is no rotation ceremony to follow afterwards.
#
# The dict-repr case is not hypothetical. record_exception() formats its
# context dict through "%s", so the one code path the redactor was written for
# renders secrets in exactly the quoted form it cannot match.
#
# THESE CASES ARE THE DESIRED BEHAVIOUR, NOT TODAY'S. This file is red until
# the redactor is fixed.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import logs as logs_service          # noqa: E402

# -- the secret must be gone, whatever quoting it arrived in -------------------
#
# (line as it is logged, the substring that must NOT survive)
LEAKS = [
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ('{"password": "hunter2"}', "hunter2"),
    ("{'token': 'abc123'}", "abc123"),
    ('POST /api/hub/update/token body={"token": "ghp_9xKq"}', "ghp_9xKq"),
    ("connecting to postgresql://cytron:s3cret@10.0.0.5:5432/db", "s3cret"),
    ("api_key=AIzaSyFAKE0000", "AIzaSyFAKE0000"),
    ("password=hunter2", "hunter2"),                 # the one form that works today
]
for line, secret in LEAKS:
    masked = logs_service.redact(line)
    assert secret not in masked, \
        f"the secret survived redaction: {line!r} -> {masked!r}"
print("== every quoting of a secret is masked, including the dict and JSON forms")

# -- and the line must still be worth reading ----------------------------------
#
# The cheap fix is to drop the whole line, or blank everything after the key.
# That trades a credential leak for a log that cannot diagnose anything: the
# reason a DSN error is in the ring at all is to say WHICH database was
# unreachable, and "internal error" is what the page already showed before any
# of this existed.
dsn = logs_service.redact("connecting to postgresql://cytron:s3cret@10.0.0.5:5432/db")
assert "10.0.0.5" in dsn, f"the host is gone, so the line says nothing: {dsn!r}"
assert "postgresql" in dsn, dsn
auth = logs_service.redact("Authorization: Bearer abc.def.ghi")
assert "Authorization" in auth, f"the key name is gone: {auth!r}"
print("== the host and the key name survive; only the credential goes")

# -- a secret with SPACES in it ------------------------------------------------
#
# Google issues an app password as four groups of four -- `abcd efgh ijkl
# mnop` -- and that is the single most common credential this product holds,
# because it is what every mailbox on every board authenticates with. A rule
# that stops at the first space masks "abcd" and leaves the other three
# quarters on screen, which is worse than not masking at all: the line reads
# as redacted. Caught on 2026-09-23 against the first version of this fix.
gmail = logs_service.redact(
    "{'sender': 'alerts@site.com', 'app_password': 'abcd efgh ijkl mnop'}")
for part in ("abcd", "efgh", "ijkl", "mnop"):
    assert part not in gmail, \
        f"part of the app password survived: {gmail!r}"
assert "alerts@site.com" in gmail, \
    f"the mailbox is gone, so the line no longer says which account failed: {gmail!r}"
print("== an app password with spaces in it is masked whole")

# An ordinary line is not a secret and must come through untouched -- a
# redactor that mangles normal text is one an operator learns to distrust.
plain = "camera 'usb0' delivering 3.7 fps against 10 agreed - reopened"
assert logs_service.redact(plain) == plain, logs_service.redact(plain)
print("== an ordinary line is passed through unchanged")

# -- the real path, not just the regex -----------------------------------------
#
# record_exception() is what the 500 handler calls (core/routes/logs.py:95).
# Its context dict becomes a dict repr inside the message, which is the case
# above -- pinned here through the actual handler so a fix to the regex that
# misses this call site still fails.
logs_service.clear()
logs_service.install()
try:
    raise RuntimeError("upstream refused")
except RuntimeError as exc:
    logs_service.record_exception(
        "POST /api/hub/update/token", exc,
        {"rid": "deadbeef", "user": "master", "token": "ghp_9xKqLIVE"})

entries = logs_service.query(limit=50)
assert entries, "record_exception put nothing in the ring"
blob = "\n".join(e["message"] + "\n" + e.get("traceback", "") for e in entries)
assert "ghp_9xKqLIVE" not in blob, \
    "the context dict carried a token onto the Logs page:\n" + blob[:400]
# The line still has to be findable: the request id is how a user's "it broke"
# becomes the traceback behind it (core/routes/logs.py:57-59).
assert "deadbeef" in blob, "the request id did not survive, so the line is unsearchable"
print("== record_exception's context dict reaches the ring with no secret in it")

# -- and the same through a traceback ------------------------------------------
#
# RingHandler redacts exc_info separately (core/services/logs.py:60-62), so it
# needs its own case: an exception whose MESSAGE quotes a config dict is the
# ordinary way a secret gets into a stack trace.
logs_service.clear()
try:
    raise ValueError("bad mailbox config {'app_password': 'zzTOPSECRET'}")
except ValueError as exc:
    logs_service.record_exception("email digest", exc, {"rid": "cafe1234"})

entries = logs_service.query(limit=50)
blob = "\n".join(e["message"] + "\n" + e.get("traceback", "") for e in entries)
assert any(e.get("traceback") for e in entries), "no traceback was captured at all"
assert "zzTOPSECRET" not in blob, \
    "a secret inside the traceback reached the page:\n" + blob[:600]
print("== a secret inside a captured traceback is masked too")

print("ALL OK")

# -- a record COLLECTED from another process is redacted too --------------------
#
# RingHandler.emit() only sees what THIS process logged. A row from a master or
# from a detection app was written as JSON by that process and read back here,
# so it passes through no handler at all. On 2026-09-23 that put a camera's
# RTSP password on the Logs page in clear, on a real board, the first time the
# merged view worked:
#
#   detail: camera 'rtsp://admin:f5f4f3f2f1@192.168.1.65:554/stream1'
#
# Admin-only is not private. That page is what people screenshot into a chat
# when they are asking for help, and every IP camera on the site shares that
# password.
collected = logs_service.as_record({
    "ts": 1.0, "level": "ERROR", "source": "app:line1",
    "message": "camera 'rtsp://admin:hunter2@192.168.1.65:554/stream1' gave no frames",
    "detail": "reopen failed for 'rtsp://admin:hunter2@192.168.1.65:554/stream1'",
})
assert "hunter2" not in collected["message"] + collected["detail"], \
    "a collected record carried a camera password to the page: %r" % (collected,)
assert "192.168.1.65" in collected["detail"], \
    "the camera address went with it, so the line no longer says WHICH camera"
print("== a record collected from another process is redacted on the way in")
