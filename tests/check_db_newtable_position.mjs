// The Database sidebar reads: search, then New Table, then the TABLES list.
// Source order is focus order, so this pins keyboard order too. New Table
// must no longer sit in the right-hand toolbar.
//
//   node tests/check_db_newtable_position.mjs
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { readText } from "./_text.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "frontend", "src");
const src = readText(join(ROOT, "pages", "Database.jsx"));
const css = readText(join(ROOT, "theme.css"));

let bad = 0;
const ok = (c, m) => { if (!c) { bad++; console.log("FAIL " + m); } else console.log("ok   " + m); };

const search = src.indexOf('className="input db-search"');
const btn = src.indexOf("db-newtable");
const tables = src.indexOf("<span>Tables</span>");
ok(search > 0 && btn > 0 && tables > 0, "search, New Table button and TABLES head all found");
ok(search < btn && btn < tables, "source order: search, New Table, TABLES list");
ok(/db-newtable[\s\S]{0,200}setNewTable\(true\)/.test(src), "button still opens the new-table dialog");
ok(/\{editable && \(\s*<button type="button" className="btn btn-sm db-newtable"/.test(src),
   "button still gated on editable");
const toolbar = src.slice(src.indexOf('className="db-toolbar"'));
ok(!toolbar.slice(0, toolbar.indexOf("db-kpis")).includes("New Table"),
   "toolbar no longer holds New Table");
ok(/\.db-newtable\s*\{[^}]*width:\s*100%/.test(css), "button is full sidebar width");

process.exit(bad ? 1 : 0);
