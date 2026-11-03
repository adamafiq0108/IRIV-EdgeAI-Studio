# Four things an operator ran into in one sitting, all of them "the editor
# knows this and did not say it":
#
#   1. The query console refused to work at all on a box whose database user
#      lacks CREATEROLE, and told the operator to run a psql command that a
#      dashboard cannot run.
#   2. A Query node could be written but not TRIED: the way to find out whether
#      a statement was valid was to save the graph and walk to the Database
#      page.
#   3. The columns a Query may read were nowhere on the node, so the row stamp
#      -- called `ts` -- got typed as `Datetime` and the query returned nothing
#      with no visible reason.
#   4. A constant wired into a Data Table column was listed as UNUSED, with an
#      offer to delete it saying nothing would change.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


page = read("frontend", "src", "pages", "Workflow.jsx")
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
widget = read("frontend", "src", "components", "widgets", "Widget.jsx")

# -- 2. a Query can be tried where it is written ------------------------------
check("the Query node can run its own statement",
      "function QueryRunner({ sql })" in page and "<QueryRunner sql={d.sql} />" in page)
check("...through the console's own endpoint, not a second path into the database",
      'api.post("/api/datasets/sql", { sql: text })' in page)
# The endpoint is the whole safety story: read-only role, scoped views, row
# cap. A second route that ran SQL "just for a preview" would be a second
# thing to get right.
routes = read("core", "routes", "datasets.py")
check("...which is admin-gated and scoped, as it already was",
      'perm.require(request, "database.edit")' in routes
      and "sqlconsole.run(slug" in routes)
check("what comes back is shown as rows, and an error is shown as the error",
      "state.columns.map" in page and 'className="small c-danger"' in page)
check("...capped in the preview, and it says when it capped",
      "rows.slice(0, 20)" in page and "First 20 of" in page)

# -- 3. the columns are on the node, and the row stamp is one of them ---------
check("the Query node lists the columns it may read",
      "function QueryColumns({ cols, editable, onInsert })" in page
      and "<QueryColumns cols={queryColumns}" in page)
check("...taken from the WIRE: a column port offers that column",
      'handle.startsWith("o_")' in page
      and 'add(table.key, handle.slice(2), "wired", table.name)' in page)
check("...and the table port offers everything the table holds",
      "add(table.key, c.name, c.type, table.name)" in page)
# `ts` is the column nobody guesses: every row has one, it is what "when did
# this happen" means here, and it is what `Datetime` was reaching for.
# Under BOTH its names: `ts` is what the database calls it, `datetime` is
# what the Database page has always shown the operator.
check("...with the row stamp included either way, under the shown name",
      'add(table.key, "datetime", "when the row was written", table.name)' in page
      and 'add(table.key, "ts", "the same stamp, its database name", table.name)' in page)
check("...and the times are named as PostgreSQL's, not another dialect's",
      "extract(hour from ts)" in page)
check("a clicked column lands at the caret, not at the end",
      "function insertAtCaret(el, current, text)" in page
      and "setSelectionRange(a + text.length" in page)

# -- 4. a variable in use is not 'unused' -------------------------------------
# The old test was "nothing publishes fn_<name>_value". A TEXT constant wired
# into a Data Table column compiles INTO the column and publishes nothing at
# all -- so the node sat on the canvas, wired, doing its job, listed as unused.
check("'unused' now asks the CANVAS, not only the published values",
      "function usedVarNames(nodes)" in page
      and "&& !onCanvas.has(v.var)" in page)
check("...counting the node that IS the variable",
      "if (d.fnId) out.add(String(d.fnId))" in page)
check("...a Variable node storing or reading one",
      'n.type === "variable.ref"' in page
      and "/^fn_(.+)_value$/" in page)
check("...and a name typed into a formula, a message or a statement",
      "/fn_([a-z0-9_]+)_value\\b/g" in page)
check("the palette is given the canvas to ask about",
      "nodes = [], released = [] }" in page and "editable={editable} nodes={nodes}" in page)
check("the slug the canvas uses is the compiler's, shared not re-typed",
      "export const slug = (t) =>" in nodes_src)

# -- the chart that showed one series twice -----------------------------------
# A forecast draws the same numbers as a hatched band and as a line. The band
# was kept out of the legend and not out of the tooltip, so hovering read
# "Total Violation: 0" twice -- one number under two identical names, which
# reads as two series where there is one.
fc = widget.split("if (forecast) {")[1].split("if (!filled)")[0]
check("the forecast band is kept out of the tooltip as well as the legend",
      'legendType="none" tooltipType="none"' in fc, fc[:300])
check("...and the LINE is still the one that carries the reading",
      "<Line key={s.key}" in fc)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
