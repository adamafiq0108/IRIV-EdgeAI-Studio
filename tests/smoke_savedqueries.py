"""A SELECT worth keeping, kept.

The console was a scratchpad: type a statement, read the rows, close the tab.
Anything useful written there had to be written AGAIN in a Query node, from
memory, because the Database page and the canvas shared nothing -- which is how
"shift totals" ends up existing three times with three different definitions.

So a query becomes an object, the way a table already was: saved by name on the
Database page, listed in the workflow palette under Database, and dropped onto
a canvas ALREADY WIRED to the tables it reads. That last part is the point --
a Query with no table wired in compiles to nothing, and asking the operator to
redraw lines the palette already knew about is asking them to copy an answer.

The rules this pins:

  * SELECT only, checked at SAVE time as well as at run time: a statement that
    can never run must not sit in the list looking like one somebody may click;
  * the key is stable, because the palette and the config both name it -- a key
    re-minted on every read is a saved query that changes identity by itself;
  * the tables are found in the TEXT against the registry, so the dropped node
    knows what to wire without anybody typing a list;
  * and the CSV node has an INPUT PORT. The compiler accepted Query -> CSV from
    the day it landed; the node fell through to the source shell, which draws
    no target handle, so the one wire it exists for could not be drawn.

    python tests/smoke_savedqueries.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import queries as qs                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


KNOWN = ["counting", "rejects"]

# -- 1. what may be saved -----------------------------------------------------
q = qs.clean_query({"name": "Busiest zones",
                    "sql": "select zone, count(*) from counting group by zone"},
                   KNOWN)
check("a SELECT saves", q["sql"].startswith("select zone"))
check("...under a key made from its name", q["key"] == "q_busiest_zones", q["key"])
check("...recording the table it reads", q["tables"] == ["counting"], q["tables"])

for sql, why in (("delete from counting", "a DELETE"),
                 ("update counting set pid = 1", "an UPDATE"),
                 ("select 1; drop table counting", "two statements"),
                 ("", "an empty statement")):
    try:
        qs.clean_query({"name": "x", "sql": sql}, KNOWN)
        check(f"{why} is refused", False, "it saved")
    except ValueError:
        check(f"{why} is refused", True)

try:
    qs.clean_query({"name": "  ", "sql": "select 1"}, KNOWN)
    check("an unnamed query is refused", False, "it saved")
except ValueError:
    check("an unnamed query is refused", True,
          "the palette lists these by name - an unnamed one is unfindable")

# A statement naming two tables depends on both, or the dropped node arrives
# able to read half of what it asks for.
two = qs.clean_query({"name": "Joined", "sql":
                      "select * from counting join rejects on true"}, KNOWN)
check("every table the text names is recorded", two["tables"] == KNOWN, two["tables"])
check("...and a table this system does not have is not invented",
      qs.clean_query({"name": "Z", "sql": "select * from elsewhere"},
                     KNOWN)["tables"] == [])

# The key is what the config, the page and the palette all agree on. COL_RE
# caps it at 32 characters, so a long name must still produce a key the config
# can hold -- one that fails would be re-minted on every single read.
from core.repos import datasets as datasets_repo             # noqa: E402
long_name = "totals for every zone on every camera this month"
key = qs.clean_query({"name": long_name, "sql": "select 1"}, KNOWN)["key"]
check("a long name still makes a storable key",
      bool(datasets_repo.COL_RE.match(key)), key)
check("...and a second query of the same name gets its own",
      qs._key_of("Busiest zones", {"q_busiest_zones"}) == "q_busiest_zones_2",
      qs._key_of("Busiest zones", {"q_busiest_zones"}))

# -- 2. round trip through the system's own config ----------------------------
tmp = tempfile.mkdtemp()
systems_dir = os.path.join(tmp, "systems")
os.makedirs(os.path.join(systems_dir, "demo"), exist_ok=True)

saved = qs.save(systems_dir, "demo", {"name": "Shift totals",
                                      "sql": "select count(*) from counting"})
check("a saved query survives the write", saved["key"] in
      qs.definitions(systems_dir, "demo"))
again = qs.save(systems_dir, "demo", {"key": saved["key"], "name": "Shift totals",
                                      "sql": "select 1 from counting"})
check("...saving over it by key REPLACES rather than duplicates",
      len(qs.definitions(systems_dir, "demo")) == 1 and again["key"] == saved["key"],
      qs.definitions(systems_dir, "demo"))
check("...and the statement is the new one",
      qs.definitions(systems_dir, "demo")[saved["key"]]["sql"] == "select 1 from counting")

# A key the box has never seen is a client's guess, not a rename: adopting it
# would let a browser choose the id the palette and the config key off.
minted = qs.save(systems_dir, "demo", {"key": "q_not_here", "name": "Other",
                                       "sql": "select 2"})
check("an unknown key is re-minted, not adopted", minted["key"] != "q_not_here",
      minted["key"])
check("the id is stable across reads",
      qs.definitions(systems_dir, "demo")[minted["key"]]["key"] == minted["key"])

check("deleting one forgets it", qs.delete(systems_dir, "demo", minted["key"])
      and minted["key"] not in qs.definitions(systems_dir, "demo"))
check("...and deleting what is not there says so",
      qs.delete(systems_dir, "demo", "q_nope") is False)

# -- 3. the routes ------------------------------------------------------------
routes = read("core", "routes", "datasets.py")
check("saving a query is the same permission as running one",
      'perm.require(request, "database.edit")' in
      routes.split('name="datasets.query_save"')[1].split("@router")[0])
check("...deleting one too",
      'perm.require(request, "database.edit")' in
      routes.split('name="datasets.query_delete"')[1].split("@router")[0])
check("...while LISTING them needs no edit right",
      "perm.require" not in
      routes.split('name="datasets.queries"')[1].split("@router")[0],
      "a saved statement is a definition, not a result")

wf_routes = read("core", "routes", "workflow.py")
check("the palette is told what has been saved",
      '"saved_queries": queries_service.listed(systems_dir, slug)' in wf_routes)

# -- 4. the canvas ------------------------------------------------------------
page = read("frontend", "src", "pages", "Workflow.jsx")
check("the Database drawer lists them by name",
      "Saved queries ({qs.length})" in page)
check("...and dropping one wires it to the tables it reads",
      "const addSavedQuery" in page and 'targetHandle: "table"' in page)
check("...reusing a table already on the canvas rather than duplicating it",
      'n.type === "output.dataset"' in page.split("const addSavedQuery")[1]
      .split("}, [registry")[0],
      "two nodes for one table is two places to read the same rows from")
check("the palette's table list never reaches node data",
      "extra._tables" in page,
      "`_tables` is drop instructions, not something to save on the node")

nodes = read("frontend", "src", "workflow", "nodes.jsx")
check("the CSV node has a shell of its own",
      '"storage.csv": CsvNode' in nodes,
      "the wiring category `query` matches no shell, so it fell through to "
      "SourceNode -- which draws NO input port")
check("...which is what gives it an input port at all",
      "const CsvNode" in nodes
      and "targetHandles" not in nodes.split("const CsvNode")[1].split("});")[0])

db = read("frontend", "src", "pages", "Database.jsx")
check("the console can keep a query", "Save query" in db
      and '/api/datasets/queries' in db)
check("...and lists what has been kept", 'tab === "saved"' in db)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
