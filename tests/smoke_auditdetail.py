# The Audit page must show `detail`, the one column that says WHAT happened.
#
# WHAT WAS WRONG. /api/audit returns detail for every row
# (core/services/audit.py, out of the JSONB column added in
# core/db/migrations/0001_master_init.sql:45). The page threw it away: it drew
# Time, User, Action, Device, Result, IP, and `detail` appeared nowhere in the
# file.
#
# So the page showed "device.rename" and not what it was renamed to;
# "session.revoke" and not whose session was ended; "system.settings" and not
# which setting. The Accounts > Activity panel, reading the SAME endpoint, did
# show it (frontend/src/components/AccountsPanel.jsx). Two viewers of one log
# with different capability is how an admin concludes the audit trail simply
# has no detail in it, and stops looking.
#
# WHY IT MATTERS IN THE FIELD. The audit log is read once, after the fact, by
# someone who was not there. "Someone changed a setting on Tuesday" is not an
# answer; "email off, by master, from 192.168.1.40" is. Detail was already
# written and already sent over the wire -- it was dropped in the last ten
# lines of the journey.
#
# HOW IT MUST BE RENDERED. Through the shared helper, not by hand:
# frontend/src/components/auditDetail.js exists because rendering the value
# straight took the whole page down with "objects are not valid as a React
# child" -- detail is `{}` for most actions, which is truthy. Some callers
# pass a bare string instead (core/routes/auth.py writes "email on",
# core/routes/devices.py writes a dict), so both shapes reach the browser and
# a second hand-rolled renderer would meet the same crash again.
#
# It renders in the Message column of the shared log table now
# (frontend/src/pages/Audit.jsx), and this file is what keeps it there. It was
# tests/pending_auditdetail.py while it was red; the rename into the suite is
# part of the fix, because a test nobody runs is a regression nobody catches.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the server still sends it, so the page has something to render -----------
SVC = read("core", "services", "audit.py")
page_fn = SVC[SVC.index("def page("):]
assert '"detail"' in page_fn, \
    "the service stopped shipping detail - fix that first, the page cannot " \
    "render a field it is not sent"
ROUTE = read("core", "routes", "audit.py")
assert "audit_service.page(" in ROUTE and '"entries": entries' in ROUTE, ROUTE[-400:]
print("== /api/audit still carries detail on every row")

# -- the helper that makes it safe to render ----------------------------------
HELPER = read("frontend", "src", "components", "auditDetail.js")
assert "export function detailText" in HELPER, \
    "the shared renderer is gone; both viewers would have to hand-roll it"
print("== the shared detailText helper exists")

# -- the Audit page uses it ----------------------------------------------------
JSX = read("frontend", "src", "pages", "Audit.jsx")

assert "detailText" in JSX, (
    "frontend/src/pages/Audit.jsx never mentions detail. Every row on the "
    "page says an action happened and not what it did.")
assert re.search(r"import\s*\{[^}]*detailText[^}]*\}\s*from\s*['\"][^'\"]*auditDetail",
                 JSX), \
    "detail is rendered without the shared helper - the second hand-rolled " \
    "renderer is the one that crashes the page on an object detail"
assert re.search(r"detailText\(\s*\w+\.detail\s*\)", JSX), \
    "detailText is imported but never applied to the row's detail"
print("== the Audit page renders detail through the shared helper")

# A bare {e.detail} is the exact crash auditDetail.js was written to stop:
# most rows carry {} which is truthy, and React refuses to render an object.
# The page goes white, and it goes white on the page an admin opened BECAUSE
# something had already gone wrong.
assert not re.search(r"\{\s*\w+\.detail\s*\}", JSX), \
    "Audit.jsx renders {e.detail} raw - an object detail blanks the page"
print("== detail is never handed to React raw")

# -- and the two viewers of this log stay on the same renderer ----------------
PANEL = read("frontend", "src", "components", "AccountsPanel.jsx")
assert "detailText" in PANEL, \
    "the Activity panel stopped using the shared helper - the two views of " \
    "one log will drift"
print("== the Activity panel and the Audit page render detail the same way")

print("ALL OK")
