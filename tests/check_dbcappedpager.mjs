// The server counts rows only up to 20,000 and says "20000+": a speed guard
// (smoke_dbscale pins it), not a limit on what can be read. The pagers took
// that capped count as the real total, so Next and Last stopped at row 20,000
// and everything older could not be reached. Past the cap there is a next page
// for as long as a full page comes back.
//   node tests/check_dbcappedpager.mjs
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
let bad = 0;
function check(label, ok) {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) bad += 1;
}
const db = readText(join(ROOT, "frontend/src/pages/Database.jsx"));
check("Database: the page keeps whether the count was capped", db.includes("setCapped(!!d.capped)"));
check("Database: while only the capped count is known, Next stays on while a full page came back",
      db.includes("const hasNext = effCapped ? (rows || []).length >= pageSize : page + 1 < pages;")
      && db.includes("disabled={!hasNext}"));
check("Database: the count says 20,000+ and the last page is not offered while the exact number is unknown",
      db.includes('{effTotal.toLocaleString()}{effCapped ? "+" : ""} rows')
      && db.includes("disabled={effCapped || page + 1 >= pages}"));
check("Database: the footer's range follows what was read past the cap",
      db.includes("const lastRow = effCapped ? firstRow + (rows || []).length - 1"));
check("Database: past the cap the exact count is asked for once per table (and on page one), from /count",
      db.includes("/api/datasets/${encodeURIComponent(chosen)}/count")
      && db.includes("exactFor.current !== chosen || page === 0")
      && db.includes("const exactTotal = capped && exact && exact.key === chosen ? exact.n : null;")
      && db.includes("const effCapped = capped && exactTotal === null;"));
check("Database: the exact number replaces 20,000+ in the footer and the Rows tile",
      db.includes("const effTotal = exactTotal ?? total;")
      && db.includes("exactTotal !== null ? exactTotal.toLocaleString()"));
const repo = readText(join(ROOT, "core/repos/datasets.py"));
const rt = readText(join(ROOT, "core/routes/datasets.py"));
const fn = repo.split("def count_exact(")[1].split("\ndef ")[0];
check("repo: count_exact is an unbounded count(*) on request, scoped by the same WHERE as query()",
      fn.includes("SELECT count(*) AS n FROM dataset_rows WHERE {where}") && !fn.includes("LIMIT")
      && fn.includes("_where(system_slug, dataset, types, filters, since, until)"));
check("route: GET /api/datasets/<key>/count answers from the table's own definition",
      rt.includes('@router.get("/api/datasets/{key}/count"') && rt.includes("datasets_repo.count_exact("));
const ev = readText(join(ROOT, "frontend/src/pages/EventTable.jsx"));
check("Database: rows per page offers 25, 50, 100, 200, 500 and 1000 (a read returns at most 1000)",
      db.includes("const PAGE_SIZES = [25, 50, 100, 200, 500, 1000];"));
check("Event log: the same, Next stays on past the cap",
      ev.includes("disabled={capped ? rows.length < PAGE_SIZE : page + 1 >= pages}"));
console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
