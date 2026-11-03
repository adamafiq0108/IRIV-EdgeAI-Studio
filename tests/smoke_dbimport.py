"""The export is a door that opens both ways.

Exporting one system's data to a SQLite file has worked for a while; there was
no way back in, which makes an export a souvenir rather than a move -- and "we
bought another box" is the case the system export was built for in the first
place.

Two things this pins that are easy to get wrong and impossible to see:

  THE PRIMARY KEY IS GLOBAL. events.event_id and dataset_rows.event_id are
  unique across the whole box, not per system, because a device's event id is
  unique by contract. Importing one system's data into ANOTHER system on the
  SAME box therefore collides with every row of the original, and ON CONFLICT
  DO NOTHING would import exactly nothing while looking like it worked. So
  rows are re-keyed when the slugs differ -- deterministically, so a second
  import of the same file adds nothing rather than duplicating.

  THE TYPES HAVE TO TRAVEL. The export's docstring always claimed to carry the
  Data Table definitions and the file never did: only the flat table_<key>
  views, which keep column NAMES and lose every type. So an import could
  rebuild the shape of a table and not what its columns were.

    python tests/smoke_dbimport.py
"""
import io
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import dbexport                              # noqa: E402
from core.services import dbimport                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


TABLES = [{"key": "pcb", "name": "PCB",
           "columns": [{"name": "zone", "type": "text"},
                       {"name": "total", "type": "number"}]}]


def make_export(slug="alpha", with_tables=True):
    """A file shaped exactly like a real export, without needing Postgres."""
    path = os.path.join(tempfile.mkdtemp(), f"{slug}.db")
    out = sqlite3.connect(path)
    dbexport._write_meta(out, slug)
    out.execute('CREATE TABLE events ("event_id" TEXT, "ts" TEXT, '
                '"system_slug" TEXT, "type" TEXT, "detail" TEXT)')
    out.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    [("e1", "2026-09-01T10:00:00+08:00", slug, "violation", "{}"),
                     ("e2", "2026-09-01T11:00:00+08:00", slug, "info", None)])
    out.execute('CREATE TABLE dataset_rows ("event_id" TEXT, "ts" TEXT, '
                '"system_slug" TEXT, "dataset" TEXT, "cols" TEXT)')
    out.execute("INSERT INTO dataset_rows VALUES (?, ?, ?, ?, ?)",
                ("e1", "2026-09-01T10:00:00+08:00", slug, "pcb",
                 '{"zone": "A", "total": 3}'))
    if with_tables:
        dbexport._write_tables(out, TABLES)
    else:
        # The older shape: the flat view only, no types anywhere.
        out.execute('CREATE TABLE "table_pcb" ("ts" TEXT, "zone" TEXT, "total" TEXT)')
    out.commit()
    out.close()
    return path


# -- 1. the export carries what it always said it did ------------------------
path = make_export()
db = sqlite3.connect(path)
have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("the export writes the Data Table definitions", "tables" in have,
      "the docstring promised these from the start and the file never had them")
row = db.execute("SELECT key, name, columns FROM tables").fetchone()
check("...with the columns as the operator designed them",
      row and row[0] == "pcb" and '"type": "number"' in row[2],
      "a table_<key> view keeps names and loses every type")
db.close()

# -- 2. reading a file changes nothing ---------------------------------------
found = dbimport.inspect(path)
check("inspect says whose data it is", found["system_slug"] == "alpha")
check("...and how much of it there is",
      found["counts"] == {"events": 2, "dataset_rows": 1},
      "'import this database' is unanswerable from a filename")
check("...and which tables come with it",
      [t["key"] for t in found["tables"]] == ["pcb"]
      and found["tables"][0]["columns"][1]["type"] == "number")

old = dbimport.inspect(make_export(with_tables=False))
check("an older export still gives up its shape",
      [t["key"] for t in old["tables"]] == ["pcb"]
      and [c["name"] for c in old["tables"][0]["columns"]] == ["zone", "total"])
check("...as text, which is the type that loses nothing",
      all(c["type"] == "text" for c in old["tables"][0]["columns"]),
      "a number stored as text still reads as a number; a number forced "
      "into the wrong type does not come back at all")

# -- 3. a file that is not ours is refused, and says why ---------------------
junk = os.path.join(tempfile.mkdtemp(), "notours.db")
j = sqlite3.connect(junk)
j.execute("CREATE TABLE whatever (a TEXT)")
j.commit(); j.close()
try:
    dbimport.inspect(junk)
    check("a SQLite file that is not an export is refused", False)
except dbimport.NotAnExport as e:
    check("a SQLite file that is not an export is refused", True)
    check("...and the message says how to see the right shape",
          "export_meta" in str(e) and "Database page" in str(e))

text = os.path.join(tempfile.mkdtemp(), "hello.txt")
io.open(text, "w", encoding="utf-8").write("not a database at all")
try:
    dbimport.inspect(text)
    check("...and so is a file that is not a database", False)
except dbimport.NotAnExport:
    check("...and so is a file that is not a database", True)

# -- 4. the primary key, which is the whole problem --------------------------
check("a restore onto the same system keeps its ids",
      dbimport._rekey("alpha", "alpha", "e1") == "e1",
      "a restore should be exactly what was taken")
moved = dbimport._rekey("beta", "alpha", "e1")
check("...but a move to another system re-keys them", moved != "e1" and moved,
      "event_id is a PRIMARY KEY across the whole box, so importing alpha's "
      "rows into beta would collide with every one of alpha's own")
check("...deterministically, so importing twice adds nothing the second time",
      dbimport._rekey("beta", "alpha", "e1") == moved)
check("...and differently per target, so one file can seed two systems",
      dbimport._rekey("gamma", "alpha", "e1") != moved)
check("a row with no id is dropped rather than invented",
      dbimport._rekey("beta", "alpha", "") is None
      and dbimport._rekey("beta", "alpha", None) is None)

# -- 5. replace is never inferred --------------------------------------------
src = read("core", "services", "dbimport.py")
check("append is the default", 'mode="append"' in src)
check("...and an unknown mode is refused, not guessed",
      'raise ValueError("mode is append or replace")' in src)
check("the uploaded file is opened read-only", "immutable=1" in src,
      "a SQLite journal replay on somebody else's database is a write we "
      "have no reason to perform")
check("jsonb columns are cast, not handed over as text",
      'f"%s::jsonb" if c in jsonb else "%s"' in src,
      "SQLite carried detail and cols as TEXT; handing text to a jsonb "
      "column is an error, not a cast")

# -- 6. the two ways in ------------------------------------------------------
routes = read("core", "routes", "models.py")
check("the Database page has a route to import into this system",
      '@router.post("/api/detector/import/db"' in routes)
check("...admin-gated, unlike the export beside it",
      'perm.require(request, "database.edit")' in routes.split(
          '"/api/detector/import/db"')[1][:2500])
check("...and it reads before it writes",
      '"preview", "append", "replace"' in routes
      and 'mode == "preview"' in routes)
check("...spooled to disk, not held in memory",
      "tempfile.NamedTemporaryFile" in routes and "await file.read(1024 * 1024)" in routes)

hub = read("core", "routes", "hub.py")
check("importing a system can bring a database with it",
      "database: UploadFile = File(None)" in hub)
check("...spooled the same way, and cleaned up either way",
      "_spool_upload" in hub and "os.unlink(db_path)" in hub)
tmpl = read("core", "services", "templates.py")
check("...loaded last, after the tables it belongs to exist",
      tmpl.index("db_path, slug, config_dir, root") > tmpl.index("recompile(systems_dir"),
      "a row belongs to a table; rows first would land against definitions "
      "that do not exist yet")
check("...and a bad database file does not cost the operator the system",
      "the database file was not imported" in tmpl)

# -- 7. what the operator is shown -------------------------------------------
page = read("frontend", "src", "pages", "Database.jsx")
check("the Database page reads the file before offering to write it",
      'postDb(file, "preview")' in page and "setPending({ file, found })" in page)
check("...and says whose data it is",
      "a different system to this one" in page)
check("...and what replace means, in the words of what is lost",
      "deleted" in page and "c-danger" in page)
hubpage = read("frontend", "src", "pages", "Hub.jsx")
check("the hub asks whether the system has a database",
      "Bring its database" in hubpage and "No database" in hubpage)
check("...and will not import a half-answered question",
      "pendingImport?.withDb && !pendingImport?.db" in hubpage)
check("...and says what does not travel",
      "Snapshots and clips are not in that file" in hubpage)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
