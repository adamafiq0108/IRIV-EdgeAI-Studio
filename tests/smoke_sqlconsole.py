# The SQL console, and the reason it is allowed to exist.
#
# Every other read in this product is scoped by system_slug in the repo layer.
# Raw SQL goes UNDERNEATH that: `SELECT * FROM events` would return every
# system on the box, plus the audit log and the device table. So the scoping
# is moved into Postgres, where a query cannot argue with it:
#
#   * a login role with NO rights on any base table -- not reduced, none
#   * per-system schemas of views, owned by the privileged user, already
#     filtered to that slug (a view runs with its OWNER's rights, which is
#     what lets the role read the view without reading the table)
#   * search_path set to that schema alone, in a read-only transaction
#
# The text checks in run() are the LEAST important layer and are last on
# purpose. This file asserts the order of that reasoning, so a later change
# that "simplifies" the role away has to delete a test that says why not.
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import sqlconsole as sc                   # noqa: E402

TABLES = {"counting": {"key": "counting", "name": "Counting",
                       "columns": [{"name": "total_counting", "type": "number"},
                                   {"name": "shift", "type": "text"}]}}

# 1. THE TEXT CHECKS. Friendly, not load-bearing -- but they must still hold,
#    because a readable refusal is the difference between a typo and a mystery.
for bad, why in (
        ("", "nothing to run"),
        ("DELETE FROM counting", "only SELECT"),
        ("UPDATE counting SET shift = 'x'", "only SELECT"),
        ("DROP VIEW counting", "only SELECT"),
        ("INSERT INTO counting VALUES (1)", "only SELECT"),
        ("SELECT 1; DROP TABLE events", "one statement"),
        ("select 1; select 2", "one statement")):
    out = sc.run("demo", bad, TABLES)
    assert not out["ok"], (bad, out)
    assert why in out["error"], (bad, out["error"])
print("== writes and stacked statements are refused with a readable reason")

# a trailing semicolon is normal typing, not an attempt at anything
assert "one statement" not in (sc.run("demo", "SELECT 1;", TABLES).get("error") or "")
print("== a trailing semicolon is not treated as a second statement")

# WITH ... SELECT is a read, and refusing it would be wrong
assert "only SELECT" not in (
    sc.run("demo", "WITH t AS (SELECT 1) SELECT * FROM t", TABLES).get("error") or "")
print("== a WITH query is a read and is allowed")

# 2. THE SCHEMA IS PER SYSTEM, and its name cannot be anything but an
#    identifier -- it is interpolated into DDL.
assert sc.schema_for("counting_template_editor") == "q_counting_template_editor"
assert sc.schema_for("a'; DROP SCHEMA public; --") == "q_a___drop_schema_public____"
assert re.match(r"^q_[a-z0-9_]*$", sc.schema_for("A-B.C"))
assert sc.schema_for("") == ""
print("== the schema name is always a bare identifier:", sc.schema_for("A-B.C"))

# 3. THE ROLE HAS NOTHING. Read the provisioning as text: it must revoke on the
#    base tables AND on future ones, or a table added by a later migration
#    arrives readable to the console by default.
src = open(sc.__file__, encoding="utf-8").read()
# The provisioning is read as two pieces: the ROLE (its own function, because
# granting CREATEROLE only takes effect on a new login, so the retry needs a
# fresh connection) and the schema/view work that follows it.
setup = (src.split("def _create_role(")[1].split("\ndef ensure_ready(")[0]
         + src.split("def ensure_ready(")[1].split("\ndef ")[0])

# ...and it is a role PER SYSTEM. search_path only governs UNQUALIFIED names,
# so one shared role granted every system's schema could still read another
# system's rows by naming the schema in full -- `SELECT * FROM q_other.events`.
# A role never granted that schema cannot, whatever it types.
assert sc.ro_user("counting_template_editor") == "cytron_ro_counting_template_editor"
assert sc.ro_user("intrusion") != sc.ro_user("counting")
assert re.match(r"^cytron_ro_[a-z0-9_]*$", sc.ro_user("A-B.C"))
assert sc.ro_user("") == ""
assert "role = ro_user(system_slug)" in setup
print("== one role per system:", sc.ro_user("intrusion"), "vs",
      sc.ro_user("counting"))
assert "REVOKE ALL ON ALL TABLES IN SCHEMA public" in setup
assert "ALTER DEFAULT PRIVILEGES" in setup and "REVOKE ALL ON TABLES" in setup
assert "NOSUPERUSER" in setup and "NOCREATEDB" in setup and "NOINHERIT" in setup
assert "GRANT SELECT ON ALL TABLES IN SCHEMA" in setup
print("== the role is created with nothing, and nothing is granted by default")

# 3b. THE BOX MAY ARRANGE ITS OWN CONSOLE -- within a bound that cannot grow.
#
# The console was unusable on a box whose database user lacks CREATEROLE: the
# error handed the operator a psql command nobody can run from a dashboard. So
# the box asks a root helper to grant it. That is a privilege change, so the
# shape of it is pinned here rather than left to a reviewer to notice.
helper = open(os.path.join(ROOT, "scripts", "db_console_role.py"),
              encoding="utf-8").read()
# The ONE statement it may run. Anything wider (GRANT ... SUPERUSER, a
# statement built from a request) would make this a way to own the server.
helper_code = helper.split('"""', 2)[2]     # everything past the module docstring
assert "CREATEROLE;" in helper_code and "SUPERUSER" not in helper_code
assert helper_code.count("ALTER ROLE") == 1
# Bounded ONLY from PostgreSQL 16. Before that, CREATEROLE could administer
# every other non-superuser role -- set its password and log in as it -- which
# is a path to the whole server and must not be granted automatically.
assert "MIN_SERVER_VERSION = 160000" in helper
assert "server_version_num" in helper and "num < MIN_SERVER_VERSION" in helper
# The database name and user come from root-owned config, never from a
# request, and are still checked before they reach a statement.
assert "_IDENT = re.compile" in helper and "_IDENT.match" in helper
# Run as root through sudo, so the command it names must take NO arguments:
# an argument is somewhere for a second statement to hide.
assert 'GRANT_HELPER = ("/usr/bin/python3", "-I", "/usr/local/lib/irivai/db-console-role.py")' in src
# The rule from before -I (2026-10-07) is asked only when sudo refuses the new one.
assert 'for argv in (GRANT_HELPER, GRANT_HELPER_OLD):' in src and '"sudo", "-n", *argv' in src
assert '"password is required" not in' in src
# Asked once per process: a box where the helper is absent must not shell out
# on every query to be told the same thing.
assert "_grant_tried" in src and "if _grant_tried:" in src
# ...and when it cannot be arranged, the operator still gets the instruction.
assert "Run once as a " in src and "ALTER ROLE {pg_user(config_dir)} CREATEROLE" in src
print("== the box may grant itself CREATEROLE on PostgreSQL 16+, once, "
      "through a root helper that takes no arguments")

# The sudoers rule names that exact command -- no wildcard, no shell.
inst = open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8").read()
assert "/usr/bin/python3 -I /usr/local/lib/irivai/db-console-role.py" in inst
assert "install -m 755 \"${SRC_DIR}/scripts/db_console_role.py\" " \
       "/usr/local/lib/irivai/db-console-role.py" in inst
# Root-owned, outside anything the service account can write: what it runs
# must not be editable by the process that asks for it.
assert "chown root:root /usr/local/lib/irivai/db-console-role.py" in inst
print("== the sudoers rule names it exactly, and root owns what it runs")

# 4. THE VIEWS ARE SCOPED AND BARRIERED. Without security_barrier the planner
#    may push a user predicate underneath the slug filter, and a function that
#    raises on the value it was handed becomes a side channel.
assert "security_barrier" in setup
# DROP then CREATE, never CREATE OR REPLACE: replace refuses to change a
# view's column list, so renaming a column on the Database page would kill
# provisioning at exactly the moment it has to work.
assert "CREATE OR REPLACE VIEW" not in setup, "replace cannot change a column list"
assert "DROP VIEW IF EXISTS {schema}.{name}" in setup
assert "WHERE system_slug = {slug}" in setup
assert "deleted_at IS NULL" in setup
print("== every view is slug-scoped and a security barrier")

# a table dropped on the Database page stops being queryable
assert "DROP VIEW IF EXISTS" in setup
print("== a deleted table's view is dropped, not left behind")

# 5. THE SESSION. search_path is the scoped schema ALONE -- public is not on
#    it, so an unqualified name can only resolve to a scoped view -- and the
#    connection is read-only with a statement timeout.
run_src = src.split("def run(")[1]
assert "search_path={schema}" in run_src
assert "user=ro_user(system_slug)" in run_src, "the query runs as a shared role"
assert "default_transaction_read_only=on" in run_src
assert "readonly=True" in run_src
assert "statement_timeout" in run_src
assert 0 < sc.TIMEOUT_MS <= 30000 and 0 < sc.MAX_ROWS <= 5000
print(f"== the session is read-only, capped at {sc.MAX_ROWS} rows "
      f"and {sc.TIMEOUT_MS}ms, on the scoped schema alone")

# the cap is enforced by FETCHING, not by trusting a LIMIT in the operator's
# text -- which they could simply not write
assert "fetchmany" in run_src and "MAX_ROWS + 1" in run_src
print("== the row cap is enforced by the fetch, not by the query text")

# 6. WHAT THE CONSOLE ADVERTISES is what the role was granted: the two product
#    views, plus one per data table with its declared columns projected out of
#    the JSONB. That projection is the point -- `SELECT * FROM counting`
#    returns total_counting as a column, not a blob.
cat = sc.catalog("demo", TABLES)
assert set(cat) == {"events", "dataset_rows", "counting"}, sorted(cat)
# The row stamp is advertised under BOTH its names, `datetime` first. The
# column is `ts` in the database, and the Database page has always shown it to
# the operator as "Datetime" -- its table header and its CSV export both say
# so -- so the console answered to a name the product never showed anybody.
assert cat["counting"] == ["event_id", "datetime", "ts",
                           "total_counting", "shift"], cat["counting"]
assert "system_slug" not in cat["events"], "the scoping column is exposed"
print("== the console advertises exactly the scoped views:", sorted(cat))

# a table whose key is not an identifier never becomes a view
assert "bad key" not in sc.catalog("demo", {"bad key": {"columns": []}})
print("== a malformed table name never becomes a view")

# 7. with no database reachable, it fails as a message rather than a traceback
out = sc.run("demo", "SELECT 1", TABLES)
assert not out["ok"] and out["error"], out
print("== with no database reachable it reports why:", out["error"][:60])
print("ALL OK")
