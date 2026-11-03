# "In effect since" is a real date or it is nothing.
#
# The retention table gained a column for when the policy currently in force
# was set. There was no such fact stored -- and the two easy ways to fill the
# column would both have been lies: the install date is not when the policy was
# chosen, and "now" is not a date at all. So it is stamped when a policy is
# FIRST saved, persists with the policy it belongs to (a later save without a
# picked date keeps it, 2026-10-11), and reads as "product default" on a box
# where nobody has ever changed it.
#
# It is also not a value a request can set: back-dating "in effect since" would
# make the column say a retention rule had been running for months when it was
# set this morning.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import retention                           # noqa: E402

CFG = tempfile.mkdtemp()

# A fresh box has never had a policy set, and says so rather than inventing one.
assert retention.policy_set_at(CFG) is None
print("== an untouched box reports no date, not a made-up one")

before = int(time.time())
retention.set_policy({"events_days": 30}, CFG)
stamped = retention.policy_set_at(CFG)
assert stamped is not None and before <= stamped <= time.time() + 2, stamped
print("== saving a policy stamps it:", stamped)

# It survives being read back through the ordinary policy path, and does not
# leak into the policy itself -- policy() returns retention PERIODS, and a
# timestamp among them would end up clamped like one.
pol = retention.policy(CFG)
assert "set_at" not in pol, pol
assert pol["events_days"] == 30, pol
print("== and stays out of the policy values themselves")

# A later save WITHOUT a picked date keeps it (product owner, 2026-10-11). It
# used to move forward on every save, so changing one period restarted every
# period's clock and put the next deletion a whole period away.
time.sleep(1.1)
retention.set_policy({"clips_days": 10}, CFG)
again = retention.policy_set_at(CFG)
assert again == stamped, (stamped, again)
print("== a later save without a date keeps the stored date")

# It cannot be set from a request: the unknown-key check runs on the caller's
# dict, so an attempt to back-date it is refused outright.
try:
    retention.set_policy({"set_at": 0}, CFG)
    raise AssertionError("set_at was accepted from a caller")
except ValueError as exc:
    assert "set_at" in str(exc), exc
    print("== and cannot be back-dated by a request:", exc)
assert retention.policy_set_at(CFG) == again

# The panel reads it off sweep_status, beside the two dates it explains.
st = retention.sweep_status(CFG)
assert st["starts_at"] == again, (st["starts_at"], again)
assert st["starts_at_set"] is True
for key in ("cutoffs", "first_deletion", "due"):
    assert set(st[key]) == set(retention.LIMITS), (key, st[key])
print("== sweep_status carries starts_at, first_deletion, cutoffs and due")


# -- the start date is a real date, and it gates deletion ---------------------
#
# The column would otherwise be decoration: a date the page prints and the
# sweep ignores. The rule it states is that a period has to have RUN since the
# policy started -- a 45-day rule set today has nothing 45 days old under it,
# and deleting on the first pass would apply the new rule to data recorded
# under the old one.
DAY = 86400
retention.set_policy({"clips_days": 10}, CFG)
now = time.time()
assert not retention.sweep_status(CFG)["due"]["clips_days"], \
    "a policy set moments ago is deleting already"
first = retention.first_deletion_at("clips_days", config_dir=CFG)
assert abs(first - (retention.starts_at(CFG) + 10 * DAY)) < 2, first
print("== a policy set today deletes nothing for its first period")

# Back-dating is how a shortened policy is made to act now -- and the point of
# letting an operator set it at all: a line recording for a year should not be
# told its first deletion is a year away.
retention.set_starts_at(int(now - 400 * DAY), CFG)
assert retention.sweep_status(CFG)["due"]["clips_days"], \
    "back-dating the start did not make deletion due"
print("== back-dating the start makes it due immediately")

# A picked date is the stored date from then on: the next save with no date
# keeps it rather than snapping back to today.
picked = retention.starts_at(CFG)
assert picked == int(now - 400 * DAY), picked
retention.set_policy({"events_days": 20}, CFG)
assert retention.starts_at(CFG) == picked, (retention.starts_at(CFG), picked)
assert retention.sweep_status(CFG)["due"]["clips_days"]
print("== a picked earlier date survives a later save without a date")

# ...but not forward: a start in the future would push every deletion further
# out each time somebody opened the page.
retention.set_starts_at(int(now + 90 * DAY), CFG)
assert int(now) - 1 <= retention.starts_at(CFG) <= int(time.time()) + 1, \
    retention.starts_at(CFG)
print("== a start date in the future is clamped to today")

for bad in (0, -1, "", None, "soon"):
    try:
        retention.set_starts_at(bad, CFG)
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass
print("== a start that is not a date is refused")

# An UNSET policy behaves exactly as it always did -- rolling, by age. A box
# nobody has configured has been running the shipped defaults since it was
# switched on, and must not suddenly stop deleting because a column was added.
FRESH = tempfile.mkdtemp()
assert retention.starts_at(FRESH) == 0
assert all(retention.sweep_status(FRESH)["due"].values()), \
    "an unconfigured box stopped enforcing retention"
print("== an unconfigured box keeps deleting by age, as before")

# One month means one thing. It was 30 days in the status the page read and
# 30.5 in the two sweeps that actually delete, so the promised cutoff was half
# a day off what was enforced.
assert retention.span_days("history_months", {"history_months": 2}) == 2 * retention.MONTH_DAYS
assert retention.span_days("clips_days", {"clips_days": 7}) == 7
print("== a month is one length everywhere:", retention.MONTH_DAYS, "days")
print("ALL OK")
