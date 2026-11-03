#!/usr/bin/env python3
"""
db_console_role.py -- give the platform's database user the one privilege the
query console needs, and nothing else.

WHY THIS EXISTS

The SQL console runs every query as an unprivileged per-system login role
(`cytron_ro_<slug>`) that has no rights on any base table -- see
core/services/sqlconsole.py. Creating that role needs CREATEROLE, which the
platform's own database user is not given when the database is set up by hand
(docs/install-appliance.md). The console then failed with an instruction to
run one psql command as a superuser, which nobody standing at a dashboard can
do -- the box has to be able to arrange its own console.

WHAT IT GRANTS, AND WHY THAT IS BOUNDED

  ALTER ROLE <app user> CREATEROLE

on PostgreSQL 16 or newer ONLY. From 16, CREATEROLE is a bounded privilege: a
role holding it may create roles and administer THE ONES IT CREATED, and it
cannot grant SUPERUSER, REPLICATION or BYPASSRLS, nor take over a role it did
not make. That is exactly the shape the console needs.

Before 16 the same keyword meant something much larger -- a CREATEROLE user
could alter ANY non-superuser role, including setting its password and then
logging in as it, which is a path to every other role on the server. This
script REFUSES on those versions and prints what to run by hand instead. A
privilege the box grants itself has to be one that cannot grow.

The script takes NO ARGUMENTS on purpose: it is reached through a sudoers rule
that names it exactly, so there is no argument for anything to be smuggled in.
The database name and user are read from the platform's own config, as root.

  sudo /usr/bin/python3 /usr/local/lib/irivai/db-console-role.py
"""

import pathlib
import re
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:                                # pragma: no cover
    tomllib = None

#: Where the platform is installed. The symlink, so a blue/green release swap
#: does not leave this pointing at an old tree.
INSTALL_DIR = pathlib.Path("/opt/irivai-systor")

#: A database identifier this script is willing to put in a statement. It is
#: read from a root-owned config file rather than from a request, and it is
#: still checked: a config file is a file, and one bad line should fail here
#: rather than reach the server.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

#: CREATEROLE is only bounded from PostgreSQL 16 (see the module docstring).
MIN_SERVER_VERSION = 160000


def _read_toml(path):
    if tomllib is None or not path.is_file():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def _pg_config(config_dir):
    """{user, dbname} for the platform's database, config then defaults."""
    merged = {}
    for name in ("storage.toml", "storage.local.toml"):
        pg = (_read_toml(config_dir / name) or {}).get("postgres") or {}
        merged.update({k: v for k, v in pg.items() if v})
    return {"user": str(merged.get("user") or "cytron"),
            "dbname": str(merged.get("dbname") or "cytron_dashboard")}


def _psql(dbname, sql):
    """One statement as the postgres superuser, over peer auth. Returns
    (ok, output)."""
    try:
        done = subprocess.run(
            ["runuser", "-u", "postgres", "--", "psql", "-d", dbname,
             "-tAc", sql],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (done.stdout or "").strip() or (done.stderr or "").strip()
    return done.returncode == 0, out


def main():
    config_dir = INSTALL_DIR / "config"
    if not config_dir.is_dir():
        config_dir = pathlib.Path(__file__).resolve().parent.parent / "config"
    pg = _pg_config(config_dir)
    if not _IDENT.match(pg["user"]) or not _IDENT.match(pg["dbname"]):
        print("the configured database user or name is not a plain "
              "identifier; refusing to build a statement out of it")
        return 2

    ok, version = _psql(pg["dbname"], "SHOW server_version_num;")
    if not ok:
        print(f"cannot reach the database as the postgres superuser: {version}")
        return 3
    try:
        num = int(version.split()[0])
    except (IndexError, ValueError):
        print(f"unreadable server version: {version!r}")
        return 3
    if num < MIN_SERVER_VERSION:
        print(f"PostgreSQL {num} is too old for this to be a safe grant: "
              "before 16, CREATEROLE could administer every other "
              "non-superuser role. Create the console role by hand, or "
              "upgrade the server.")
        return 4

    ok, out = _psql(pg["dbname"], f"ALTER ROLE {pg['user']} CREATEROLE;")
    if not ok:
        print(f"the grant failed: {out}")
        return 5
    print(f"{pg['user']} may now create the query console's read-only roles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
