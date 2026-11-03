# Paging the activity log, and the account page's system list.
#
# Two fixes that share a page:
#
# * Activity showed the newest 100 entries and stopped. There was no way to
#   reach entry 101, so the log a deployment keeps for a year could only ever
#   be read for its most recent day. It pages now, up to the server's own
#   ceiling of 1000 a page, with the ceiling and the clamp in the SAME place
#   they always were -- a page asking for more gets 1000, not an error.
#
# * The systems chips still listed the developer's template systems, so an
#   admin granting access saw systems that are not theirs and could hand
#   somebody a grant on one. Every other hub surface scopes on hub.visible_to;
#   this page was the last one that did not.
#
# fastapi and psycopg are not installed on a dev box, so the wiring is read
# from the source and the paging arithmetic is checked directly.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# -- the repo asks for one row more than the page ------------------------------
REPO = io.open(os.path.join(ROOT, "core", "repos", "audit.py"),
               encoding="utf-8").read()
q = REPO[REPO.index("def query("):]
q = q[:q.index("\ndef ", 5)] if "\ndef " in q[5:] else q
assert "OFFSET %s" in q, "the query does not page"
assert re.search(r"int\(limit\)\s*\+\s*1", q), \
    "the query does not fetch the extra row that reveals a next page"
assert "count(" not in q.lower().replace("count(*) over the whole log", ""), \
    "counting the whole log on every page turn buys a number nobody acts on"
print("== the query pages, and learns 'there is more' from one extra row")

# -- the service hands back that page and never the extra row -----------------
SVC = io.open(os.path.join(ROOT, "core", "services", "audit.py"),
              encoding="utf-8").read()
p = SVC[SVC.index("def page("):]
assert "rows[:limit]" in p, "the extra row is returned to the caller"
assert "len(rows) > limit" in p, "has_more is not derived from the extra row"
assert "return out, has_more" in p, p[-200:]
# recent() keeps its old shape, so nothing that already called it has to change.
r = SVC[SVC.index("def recent("):SVC.index("def page(")]
assert "page(" in r and "[0]" in r, r
print("== page() returns (rows, has_more); recent() still returns a list")

# -- the route clamps, and reports what it did --------------------------------
RT = io.open(os.path.join(ROOT, "core", "routes", "audit.py"),
             encoding="utf-8").read()
assert "min(1000" in RT, "the 1000-row ceiling is gone"
assert "max(0, int(offset))" in RT, "a negative offset is not clamped"
for key in ('"limit"', '"offset"', '"has_more"'):
    assert key in RT, f"the response does not report {key}"
print("== the route clamps to 1000 and reports limit / offset / has_more")

# The page picker must not offer a size the server will silently reduce.
JSX = io.open(os.path.join(ROOT, "frontend", "src", "components",
                           "AccountsPanel.jsx"), encoding="utf-8").read()
sizes = re.search(r"ACTIVITY_SIZES\s*=\s*\[([^\]]*)\]", JSX)
assert sizes, "the size choices are not declared"
chosen = [int(n) for n in re.findall(r"\d+", sizes.group(1))]
assert chosen and max(chosen) <= 1000, chosen
assert "offset=${page * size}" in JSX, "the page number never becomes an offset"
print("== the picker offers", chosen, "-- none above the server's ceiling")

# A new account, or a new page size, starts at the top: page 3 of the old
# query is not page 3 of the new one.
assert re.search(r"setPage\(0\);\s*\}, \[username, size\]", JSX), \
    "changing account or page size does not reset the page"
print("== changing the account or the size returns to the first page")

# -- the account page scopes its systems like every other hub surface ---------
AUTH = io.open(os.path.join(ROOT, "core", "routes", "auth.py"),
               encoding="utf-8").read()
listing = AUTH[AUTH.index('name="users.list"'):]
listing = listing[:listing.index("@router.")]
assert "visible_to" in listing, "/api/users still lists every system"
# ...and it is the MASTERS map that is narrowed, so the names, owners and the
# slug list all come from the same scoped set rather than three different ones.
assert re.search(r"masters = \{slug: entry for slug, entry in masters\.items\(\)",
                 listing), listing
print("== /api/users scopes its systems on hub.visible_to")

# -- Activity Log: the developer's entries are not a deployment's business --
route = RT[RT.index('name="audit.query"'):]
assert "is_developer" in route, "the unfiltered log is not scoped"
assert "exclude_usernames" in route, "the scope is not passed to the query"
# Filtered in the QUERY, not in the page: hiding rows in the browser hides the
# rows and not the payload, and it would make the page counts lie.
assert "exclude_usernames" in REPO and "ALL(%s)" in REPO, \
    "the exclusion is not applied in SQL"
# ...and bound, never interpolated: these are operator-supplied account names.
excl = REPO[REPO.index("hidden = ["):REPO.index("where = ")]
assert "params.append(hidden)" in excl and "%" not in excl.replace("%s", ""), excl
print("== the log excludes developer accounts for everyone but a developer, "
      "in SQL and by parameter")

# The tab exists, and asks for the whole log rather than for username=""
assert ">Activity Log<" in JSX and "All Activities" not in JSX, \
    "there is no Activity Log tab (or the old All Activities name is back)"
assert "username ? `username=" in JSX, \
    "an empty username is sent as a filter for the empty string"
print("== the Activity Log tab asks for the log unfiltered")
print("ALL OK")
