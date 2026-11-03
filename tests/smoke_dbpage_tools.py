# The Database page's three quiet gaps: a column could not be reordered, the
# whole-database export hid as an icon in the sidebar, and deleting a table
# meant knowing it lived inside the Edit dialog.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import datasets as datasets_service          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Database.jsx"),
               encoding="utf-8").read()

# -- column order is the operator's to set, and it STICKS ---------------------
# The schema dialog swaps rows; what matters below the UI is that the stored
# definition keeps list order exactly, because that order is what the grid,
# the exports and the workflow node's ports all read.
SYSTEMS = tempfile.mkdtemp()
pkg = os.path.join(SYSTEMS, "orderly")
os.makedirs(pkg, exist_ok=True)
with open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8") as f:
    f.write("[detector]\n")
cols = [{"name": "zebra", "type": "text"}, {"name": "apple", "type": "number"},
        {"name": "mid", "type": "boolean"}]
datasets_service.set_tables(SYSTEMS, "orderly",
                            [{"key": "t1", "name": "T1", "columns": cols}])
got = datasets_service.definitions(SYSTEMS, "orderly")["t1"]["columns"]
check("a table stores its columns in the order given, not sorted",
      [c["name"] for c in got] == ["zebra", "apple", "mid"], repr(got))
reordered = [cols[1], cols[2], cols[0]]
datasets_service.set_tables(SYSTEMS, "orderly",
                            [{"key": "t1", "name": "T1", "columns": reordered}])
got = datasets_service.definitions(SYSTEMS, "orderly")["t1"]["columns"]
check("...and a reorder round-trips", [c["name"] for c in got]
      == ["apple", "mid", "zebra"], repr(got))
# A recompile must not shuffle what the operator arranged: merge keeps the
# stored order and only APPENDS a column it has never seen.
merged = datasets_service.merge_declared(
    {"t1": {"key": "t1", "name": "T1",
            "columns": [dict(c) for c in reordered]}},
    {"t1": {"name": "T1", "columns": [{"name": "zebra", "type": "text"},
                                      {"name": "brand_new", "type": "number"}]}})
check("a workflow recompile keeps the arranged order, appending only the new",
      [c["name"] for c in merged["t1"]["columns"]]
      == ["apple", "mid", "zebra", "brand_new"], repr(merged["t1"]["columns"]))

# -- the schema dialog can move a column --------------------------------------
check("the Edit dialog has Move up / Move down on each column",
      'title="Move up"' in page and 'title="Move down"' in page)
check("...that swap adjacent rows",
      "[next[i - 1], next[i]] = [next[i], next[i - 1]]" in page
      and "[next[i], next[i + 1]] = [next[i + 1], next[i]]" in page)
check("...with the ends pinned",
      "disabled={i === 0}" in page and "disabled={i === cols.length - 1}" in page)

# -- and the dialog still FITS ------------------------------------------------
# A flex item's default min-width is its content, so the row of input +
# select + arrows + remove refused to shrink and pushed the dialog wider than
# itself: a form with a sideways scrollbar. The rows must be able to give.
css = io.open(os.path.join(ROOT, "frontend", "src", "theme.css"),
              encoding="utf-8").read()
check("every column-row child may shrink", ".db-colrow > * { min-width: 0; }" in css)
check("...the name field takes the slack from a zero basis",
      "flex: 1 1 0; min-width: 7rem;" in css)
check("...the type select can give some width back",
      "flex: 0 1 132px" in css)
check("...and the dialog never scrolls sideways",
      "overflow-y: auto; overflow-x: hidden;" in css)
check("the reorder arrows are one control-sized column, not two buttons",
      ".db-colmove" in css and 'className="db-colmove"' in page)

# -- export db sits beside export csv, in one Export menu (2026-10-14) --------
csv_at = page.find('<span className="db-export-name">Export CSV</span>')
db_at = page.find('<span className="db-export-name">Export database</span>')
menu_at = page.find("<DropdownMenuContent align=\"end\" className=\"db-export-menu\"")
check("Export database is an item of the toolbar's Export menu", db_at > 0 and menu_at > 0)
check("...right after Export CSV, in the same menu", menu_at < csv_at < db_at
      and db_at - csv_at < 800, f"menu@{menu_at} csv@{csv_at} db@{db_at}")
check("...disabled while a download is building",
      "disabled={!status.available || exporting}" in page)

# -- delete table is findable, and one flow -----------------------------------
check("Delete table is on the toolbar", ">\n                  Delete table" in page
      or "Delete table\n" in page)
check("one delete flow serves the toolbar and the dialog",
      page.count('title: `Delete the table "${active.name}"?`') == 1
      and "if (await deleteTable()) setSchemaOpen(false);" in page)
check("...behind a real confirm, marked destructive",
      "destructive: true })) return false;" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
