# -*- coding: utf-8 -*-
"""
services/sqlconsole.py -- the SQL editor, and the wall that makes it safe.

Every other read in this product is scoped by `system_slug` in the repo layer
(core/repos/datasets.py). Raw SQL goes UNDERNEATH that: `SELECT * FROM events`
would return every system on the box, plus the audit log and the device table.
Promising in the UI not to do that is not a control, so the scoping is moved
into Postgres itself, where a query cannot argue with it.

HOW IT WORKS

  1. A login role `cytron_ro_<slug>` exists PER SYSTEM, with NO rights on any
     base table. Not "reduced" rights -- none. It cannot see public.events
     even by naming it in full, because the grant is not there to be used.

     One role per system rather than one shared read-only role, because
     search_path only governs UNQUALIFIED names: a shared role granted every
     system's schema could still read another system's rows by naming the
     schema in full. A role never granted that schema cannot.

  2. For each system, a schema `q_<slug>` holds VIEWS that are already scoped:
     `events` and `dataset_rows` filtered to that slug, plus one view per data
     table with its declared columns projected out of the JSONB, so
     `SELECT * FROM counting` returns `total_counting` as a real column
     instead of a blob nobody can read.

     The views are owned by the privileged user, and a view runs with its
     OWNER's rights. That is the whole trick: the role reads the view, the
     view reads the table, and there is no path from one to the other.

  3. The session runs with `search_path` set to that schema alone, in a
     READ ONLY transaction, with a statement timeout, and the result is capped
     by fetchmany rather than by trusting a LIMIT in the operator's text.

  4. The text itself must be a single SELECT or WITH. This is the LEAST
     important layer and is deliberately last: it is a convenience so mistakes
     read as friendly errors, not a security boundary. The boundary is the
     grant that does not exist.

A password for the role is generated once and kept in storage.local.toml,
beside the main one (gitignored, chmod 600).
"""

import re
import secrets

from core import config
from core.db import DbUnavailable, connect
from core.repos import datasets as datasets_repo

#: Prefix of the unprivileged role a console query runs as. There is ONE ROLE
#: PER SYSTEM, not one shared read-only role, and the difference is not
#: cosmetic: search_path only governs UNQUALIFIED names, so a single role
#: granted every system's schema could still read another system's rows by
#: naming the schema in full (`SELECT * FROM q_other.events`). A role that was
#: never granted that schema cannot, whatever it types.
RO_PREFIX = "cytron_ro_"


def ro_user(system_slug):
    """The role for one system. Identifiers cap at 63 bytes; the slug is
    already narrow, but truncate rather than produce an invalid name."""
    slug = re.sub(r"[^a-z0-9_]", "_", str(system_slug or "").lower())[:40]
    return f"{RO_PREFIX}{slug}" if slug else ""

#: Per-system schema holding its scoped views.
SCHEMA_PREFIX = "q_"

#: Rows a console query may return. The editor is for looking, not extracting;
#: past this, use Export CSV, which goes through the ordinary scoped reader.
MAX_ROWS = 500

#: Seconds one query may run. A console query that takes longer than this has
#: already failed as far as the person waiting is concerned.
TIMEOUT_MS = 10000

#: Only a read. Enforced by the transaction too -- this is the friendly half.
_READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

#: The one-shot helper that lets the box arrange its own console: it grants
#: the platform's database user CREATEROLE, as root, through a sudoers rule
#: that names this exact command with no arguments (scripts/install.sh).
#: Root-owned and not writable by the service account, so what it runs cannot
#: be edited by the process asking for it. -I since 2026-10-07 (Pipi); a box
#: whose rule predates that still names the command without it (OLD), until
#: its root step rewrites the rule, so that one is asked only if sudo refuses.
GRANT_HELPER = ("/usr/bin/python3", "-I", "/usr/local/lib/irivai/db-console-role.py")
GRANT_HELPER_OLD = ("/usr/bin/python3", "/usr/local/lib/irivai/db-console-role.py")

#: Tried at most once per process. A box where the helper is absent (a dev
#: checkout, an install that declined the sudoers rule) must not shell out on
#: every query to be told the same thing.
_grant_tried = False


def _try_grant_createrole():
    """Ask the box to grant its own database user CREATEROLE. (ok, output).

    The helper decides WHETHER it is safe -- it refuses on PostgreSQL before
    16, where CREATEROLE could administer every other non-superuser role. This
    side only decides whether to ask, and asks once.
    """
    global _grant_tried
    if _grant_tried:
        return False, "already tried"
    _grant_tried = True
    import subprocess                                         # noqa: PLC0415

    try:
        for argv in (GRANT_HELPER, GRANT_HELPER_OLD):
            done = subprocess.run(["sudo", "-n", *argv],
                                  capture_output=True, text=True, timeout=30)
            if "password is required" not in (done.stderr or ""):
                break
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (done.stdout or "").strip() or (done.stderr or "").strip()
    return done.returncode == 0, out


def schema_for(system_slug):
    slug = re.sub(r"[^a-z0-9_]", "_", str(system_slug or "").lower())[:40]
    return f"{SCHEMA_PREFIX}{slug}" if slug else ""


def _ro_password(config_dir=None):
    """The console role's password, generated once and kept locally."""
    local = config.load_local("storage", config_dir)
    pg = local.setdefault("postgres", {})
    if not pg.get("ro_password"):
        pg["ro_password"] = secrets.token_urlsafe(24)
        config.save_local("storage", local, config_dir)
    return pg["ro_password"]


def pg_user(config_dir=None):
    """The application's own database user -- named in the setup instruction
    when it turns out to lack the one privilege this needs."""
    return ((config.storage(config_dir) or {}).get("postgres") or {}).get(
        "user", "cytron")


def _quote(text):
    """A single-quoted SQL literal. Used only for values baked into a view
    definition (the system slug), never for anything a client sent."""
    return "'" + str(text).replace("'", "''") + "'"


def _create_role(conn, role, password, exists):
    """CREATE (or re-password) the console's login role. Raises on refusal."""
    with conn.cursor() as cur:
        if exists:
            cur.execute(f"ALTER ROLE {role} PASSWORD {_quote(password)}")
        else:
            cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD {_quote(password)} "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    conn.commit()


def _ensure_role(role, password, config_dir=None):
    """The console's per-system login role exists with a known password.

    Creating a role needs CREATEROLE, which the platform's own database user
    is not given when the database is set up by hand. Rather than leaving the
    operator with a psql command they cannot run from a dashboard, the box
    asks its own root helper to grant it -- once, and only on a server where
    that privilege is bounded (PostgreSQL 16+; the helper refuses below that).
    """
    try:
        conn = connect(config_dir)
    except DbUnavailable as exc:
        return False, str(exc)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            exists = bool(cur.fetchone())
        try:
            _create_role(conn, role, password, exists)
            return True, ""
        except Exception as exc:                              # noqa: BLE001
            conn.rollback()
            if "permission denied" not in str(exc).lower():
                raise
    finally:
        conn.close()

    granted, why = _try_grant_createrole()
    if not granted:
        dbname = ((config.storage(config_dir) or {}).get("postgres")
                  or {}).get("dbname", "cytron_dashboard")
        return False, (
            "the query console needs a read-only database role, and this "
            "database user may not create one. The box tried to arrange it "
            f"and could not ({why or 'no reason given'}). Run once as a "
            f"superuser:  sudo -u postgres psql -d {dbname} "
            f"-c 'ALTER ROLE {pg_user(config_dir)} CREATEROLE;'")
    # A privilege Postgres reads at LOGIN: only a new session has it.
    try:
        conn = connect(config_dir)
    except DbUnavailable as exc:
        return False, str(exc)
    try:
        _create_role(conn, role, password, exists)
        return True, ""
    except Exception as exc:                                  # noqa: BLE001
        conn.rollback()
        return False, str(exc).strip().split("\n")[0]
    finally:
        conn.close()


def ensure_ready(system_slug, tables, config_dir=None):
    """Create the role, the schema and the views. Idempotent; returns (ok, why).

    Run before every query rather than once at boot: a table added on the
    Database page has to appear in the console without a restart, and CREATE
    OR REPLACE VIEW on a handful of views costs nothing next to the query."""
    schema = schema_for(system_slug)
    role = ro_user(system_slug)
    if not schema or not role:
        return False, "no system"
    password = _ro_password(config_dir)
    # The role first, on a connection of its own: granting CREATEROLE takes
    # effect at LOGIN, so the retry has to be a new session, and a reconnect
    # in the middle of the schema work would leave a cursor pointing at a
    # closed connection.
    ok, why = _ensure_role(role, password, config_dir)
    if not ok:
        return False, why
    try:
        conn = connect(config_dir)
    except DbUnavailable as exc:
        return False, str(exc)
    try:
        # Every statement below names THIS system's role only.
        RO_USER = role
        with conn.cursor() as cur:
            # No rights on anything by default, now or in future. The console
            # sees exactly what is granted below and nothing else -- including
            # tables added by a later migration, which is why the DEFAULT
            # PRIVILEGES line matters as much as the REVOKEs.
            # Resolved once, on its own line: a multi-line expression inside an
            # f-string's {} only parses on Python 3.12+ (PEP 701) -- this ran
            # fine on this dev box's 3.14 and never got exercised on the 3.11
            # CI actually pins, where it is a bare SyntaxError at import time.
            dbname = (config.storage(config_dir).get("postgres") or {}).get(
                "dbname", "cytron_dashboard")
            cur.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {RO_USER}")
            cur.execute(f"REVOKE ALL ON SCHEMA public FROM {RO_USER}")
            cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                        f"REVOKE ALL ON TABLES FROM {RO_USER}")
            cur.execute(f"REVOKE ALL ON DATABASE {dbname} FROM {RO_USER}")
            cur.execute(f"GRANT CONNECT ON DATABASE {dbname} TO {RO_USER}")

            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO {RO_USER}")

            slug = _quote(system_slug)
            # DROP then CREATE, never CREATE OR REPLACE. Replace refuses to
            # change a view's column list at all -- rename a column on the
            # Database page and provisioning dies with "cannot change name of
            # view column", which is exactly the moment it has to work. The
            # grants are re-applied below, so dropping costs nothing.
            def view(name, body):
                cur.execute(f"DROP VIEW IF EXISTS {schema}.{name}")
                cur.execute(f"CREATE VIEW {schema}.{name} "
                            f"WITH (security_barrier) AS {body}")
            # The two product tables, pre-scoped. security_barrier so the
            # planner cannot push a user predicate underneath the filter and
            # leak a row through a side channel (a function that raises on the
            # value it was handed is the classic one).
            view("events",
                 "SELECT event_id, ts, type, severity, label, camera, zone, "
                 "confidence, track_id, person_id, object_id, detection_label, "
                 "direction, model, function, status, notes, detail "
                 f"FROM public.events WHERE system_slug = {slug}")
            view("dataset_rows",
                 "SELECT event_id, ts, dataset, cols, edited_at "
                 "FROM public.dataset_rows "
                 f"WHERE system_slug = {slug} AND deleted_at IS NULL")

            # One view per data table, with the declared columns projected out
            # of the JSONB. This is what makes the console worth having: the
            # operator writes `SELECT * FROM counting`, not a jsonb expression.
            live = set()
            for key, table in (tables or {}).items():
                if not datasets_repo.COL_RE.match(str(key)):
                    continue
                types = datasets_repo.col_types(table.get("columns"))
                # The row stamp under BOTH names. The column is `ts` in the
                # database and every stored query, and the Database page has
                # always shown it to the operator as "Datetime" -- its table
                # header and its CSV export both say so. So the console
                # answered to a name the product never showed anybody, and
                # "Datetime" -- the only name a person could have read -- was
                # an error. Same column, twice, rather than a rename: a query
                # somebody already wrote against `ts` must keep working.
                picks = ["event_id", "ts", "ts AS datetime"]
                for name, kind in types.items():
                    # `name` passed COL_RE, so it is a bare identifier.
                    picks.append(f"{datasets_repo._ref(name, kind)} AS {name}")
                view(key,
                     f"SELECT {', '.join(picks)} FROM public.dataset_rows "
                     f"WHERE system_slug = {slug} AND dataset = {_quote(key)} "
                     "AND deleted_at IS NULL")
                live.add(key)

            # A table deleted on the Database page must stop being queryable.
            cur.execute("SELECT table_name FROM information_schema.views "
                        "WHERE table_schema = %s", (schema,))
            for row in cur.fetchall():
                name = row["table_name"]
                if name not in live and name not in ("events", "dataset_rows"):
                    cur.execute(f"DROP VIEW IF EXISTS {schema}.{name}")

            cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {RO_USER}")
        conn.commit()
        return True, ""
    except Exception as exc:                                  # noqa: BLE001
        return False, str(exc)
    finally:
        conn.close()


def catalog(system_slug, tables, config_dir=None):
    """What the console can see: view name -> column names. Drives the editor's
    hints, and is the honest answer to "what may I query"."""
    out = {"events": ["event_id", "ts", "type", "severity", "label", "camera",
                      "zone", "confidence", "track_id", "person_id", "object_id",
                      "detection_label", "direction", "model", "function",
                      "status", "notes", "detail"],
           "dataset_rows": ["event_id", "ts", "dataset", "cols", "edited_at"]}
    for key, table in (tables or {}).items():
        if datasets_repo.COL_RE.match(str(key)):
            # `datetime` is listed as well as `ts`, and listed FIRST of the
            # two, because it is the name the Database page shows: the hints
            # should offer what the operator has already read on screen.
            out[key] = ["event_id", "datetime", "ts"] + list(
                datasets_repo.col_types(table.get("columns")))
    return out


#: The placeholders a statement written for a Telegram button or a Watch
#: carries. Postgres never sees them -- psycopg2 binds them -- so a console
#: that runs the text as typed hands the server a bare "%" and gets back
#: `syntax error at or near "%"`, which names the symptom and not the cause.
_BIND_RE = re.compile(r"%\((from|to)\)s")


def wants_dates(sql):
    """Does this statement expect a period bound into it?"""
    return bool(_BIND_RE.search(str(sql or "")))


#: The periods the console can bind. Same vocabulary as a card's Period
#: (builder.RANGES) plus "custom": two dates picked by hand, the way the
#: Analytics page's picker gives them. Days are the length of the window
#: ending today; 0 is all time.
PERIODS = {"all": 0, "today": 1, "week": 7, "month": 30, "custom": None}


def preview_params(sql, period="today", start=None, end=None, now=None):
    """(params, label) for RUNNING a parameterised statement by hand.

    A statement written for a button is incomplete on its own: the period
    arrives when somebody taps. So the console binds one -- the period the
    operator picked, today when nobody did -- and the caller says which,
    because a result over a window nobody chose looks wrong for no visible
    reason. `custom` takes two ISO dates (YYYY-MM-DD); the end date is
    INCLUSIVE, as a person reads "to 14 Sep", so the bound `to` is the
    midnight after it. A custom with no dates falls back to today rather
    than refusing: the console is for trying things.

    Returns (None, "") for a statement that binds nothing, which is most of
    them, and is why params default to None rather than {}.
    """
    if not wants_dates(sql):
        return None, ""
    import datetime                                           # noqa: PLC0415
    now = now or datetime.datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period = str(period or "today").lower()
    if period not in PERIODS:
        period = "today"

    def _day(text):
        try:
            return datetime.datetime.strptime(str(text or "")[:10], "%Y-%m-%d")
        except ValueError:
            return None

    if period == "custom":
        a, b = _day(start), _day(end)
        if a and not b:
            b = a
        if b and not a:
            a = b
        if a and b:
            if b < a:
                a, b = b, a
            stop = b + datetime.timedelta(days=1)
            return ({"from": a, "to": stop},
                    f"over {a.strftime('%Y-%m-%d')} to {b.strftime('%Y-%m-%d')}")
        period = "today"
    days = PERIODS[period]
    stop = today + datetime.timedelta(days=1)
    if not days:
        return ({"from": datetime.datetime(1970, 1, 1), "to": stop}, "over all time")
    first = today - datetime.timedelta(days=days - 1)
    label = {"today": f"over today ({today.strftime('%Y-%m-%d')})",
             "week": f"over the last 7 days (from {first.strftime('%Y-%m-%d')})",
             "month": f"over the last 30 days (from {first.strftime('%Y-%m-%d')})"}[period]
    return {"from": first, "to": stop}, label


def run(system_slug, sql, tables, config_dir=None, params=None):
    """Run one read-only query. Returns {ok, columns, rows, truncated, error}.

    The scoping is the ROLE and the VIEWS, not this function. What happens
    here is only what makes a mistake readable.

    `params` are BOUND, never formatted in: a Telegram command runs one saved
    statement over whatever period the person asked for, and a period pasted
    into SQL as text is the one place this feature could grow an injection.
    Written as %(from)s / %(to)s in the statement. A literal percent sign in a
    statement that binds anything has to be doubled -- psycopg2's rule, not
    ours, and the reason params default to None rather than {}."""
    text = str(sql or "").strip().rstrip(";").strip()
    if not text:
        return {"ok": False, "error": "nothing to run"}
    if ";" in text:
        return {"ok": False, "error": "one statement at a time"}
    if not _READ_ONLY.match(text):
        return {"ok": False, "error": "only SELECT (or WITH) queries can be run here"}

    ok, why = ensure_ready(system_slug, tables, config_dir)
    if not ok:
        return {"ok": False, "error": f"the query console is not available: {why}"}

    schema = schema_for(system_slug)
    pg = (config.storage(config_dir) or {}).get("postgres") or {}
    try:
        import psycopg2                                       # noqa: PLC0415
        import psycopg2.extras                                # noqa: PLC0415
    except ImportError as exc:                                # pragma: no cover
        return {"ok": False, "error": str(exc)}
    try:
        conn = psycopg2.connect(
            host=pg.get("host", "localhost"), port=int(pg.get("port", 5432)),
            dbname=pg.get("dbname", "cytron_dashboard"),
            user=ro_user(system_slug), password=_ro_password(config_dir),
            cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=3,
            # search_path is the schema ALONE: an unqualified name resolves to
            # a scoped view, and public is not on the path at all.
            options=f"-c statement_timeout={TIMEOUT_MS} "
                    f"-c search_path={schema} -c default_transaction_read_only=on",
        )
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(text, params) if params else cur.execute(text)
            if cur.description is None:
                return {"ok": False, "error": "that statement returned no rows"}
            columns = [d[0] for d in cur.description]
            fetched = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(fetched) > MAX_ROWS
        rows = []
        for r in fetched[:MAX_ROWS]:
            row = {}
            for k, v in dict(r).items():
                row[k] = v.isoformat() if hasattr(v, "isoformat") else v
            rows.append(row)
        return {"ok": True, "columns": columns, "rows": rows,
                "truncated": truncated}
    except Exception as exc:                                  # noqa: BLE001
        # Postgres' own message is the most useful thing anyone gets here --
        # "relation does not exist" names the mistake better than we could.
        return {"ok": False, "error": str(exc).strip().split("\n")[0]}
    finally:
        try:
            conn.rollback()
        except Exception:                                     # noqa: BLE001
            pass
        conn.close()


#: How a query's rows become ONE number, for a Variable.
#:
#: A query answers with a table; a variable holds a value. Something has to
#: say which, and guessing is the one thing that must not happen -- a variable
#: that silently means "the first row" on Monday and "the sum" on Tuesday is
#: worse than no variable. So the reduction is chosen by the operator and
#: named in the words they would use.
#:
#: `count` is the row count and needs no column at all, which is why it is
#: here rather than being written into every query as SELECT count(*).
PICKS = ("first", "last", "sum", "avg", "min", "max", "count")


def _as_float(value):
    """A cell as a float, or None. Booleans count (a SQL boolean answering
    "is it running" is a real reading); text that is not a number does not."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def scalar(system_slug, sql, tables, column="", pick="first", config_dir=None):
    """Run a query and reduce it to ONE number. {ok, value, error, rows}.

    `column` names which column to read; blank means the first column that
    has a number in it anywhere, which is what a query written for a value
    ("SELECT count(*)", "SELECT zone, total") almost always means. Positional
    because a hand-written statement has no schema to introspect.

    An EMPTY result is not an error and is not zero either -- "no rows matched"
    and "the answer is 0" are different facts, and a variable that reports 0
    for a query that returned nothing is how a dashboard says all-clear during
    an outage. It answers ok=False with no value, and the caller keeps whatever
    it had; `count` is the exception, because no rows genuinely is a count of
    zero.
    """
    pick = pick if pick in PICKS else "first"
    res = run(system_slug, sql, tables, config_dir=config_dir)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "value": None}
    rows, columns = res.get("rows") or [], res.get("columns") or []
    if pick == "count":
        return {"ok": True, "value": float(len(rows)), "rows": len(rows)}
    if not rows or not columns:
        return {"ok": False, "error": "the query returned no rows",
                "value": None, "rows": 0}
    name = str(column or "").strip()
    if name and name not in columns:
        return {"ok": False, "value": None, "rows": len(rows),
                "error": f"the query has no column named {name!r} - "
                         f"it returns {', '.join(columns[:6])}"}
    if not name:
        name = next((c for c in columns
                     if any(_as_float(r.get(c)) is not None for r in rows)),
                    columns[0])
    nums = [v for v in (_as_float(r.get(name)) for r in rows) if v is not None]
    if not nums:
        return {"ok": False, "value": None, "rows": len(rows),
                "error": f"column {name!r} holds no numbers"}
    value = ({"first": lambda n: n[0],
              "last": lambda n: n[-1],
              "sum": sum,
              "avg": lambda n: sum(n) / len(n),
              "min": min,
              "max": max}[pick])(nums)
    return {"ok": True, "value": float(value), "rows": len(rows),
            "column": name}
