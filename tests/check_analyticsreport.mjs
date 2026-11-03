// The Analytics PDF report (lib/analyticsReport.js, the button on the Analytics
// page).
//
//   1. planPages: cards keep their place relative to each other, are scaled to
//      the paper, are never cut by a page foot, and paging moves a card and
//      everything below it down together (two cards of a row stay a row)
//   2. the words: period, creation stamp, file name
//   3. the real jsPDF puts pictures on pages and writes the header and the
//      "Page n of N" footers (a stand-in picture; html2canvas needs a browser)
//   4. the page: the button is there, the libraries load only on a click, and
//      the report is made from the grid's own cards
//
//   node tests/check_analyticsreport.mjs
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const SRC = join(FRONT, "src");
const R = await import(pathToFileURL(join(SRC, "lib", "analyticsReport.js")).href);
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);

let bad = 0;
const ok = (c, msg, detail = "") => {
  if (c) console.log(`ok   ${msg}`);
  else { bad += 1; console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); }
};
const near = (a, b, e = 0.01) => Math.abs(a - b) < e;

// -- 1. paging ------------------------------------------------------------------
const GW = 1000;                              // grid width in px
const cell = (id, left, top, width, height) => ({ id, left, top, width, height });
{
  const { pages, scale } = R.planPages([cell(0, 0, 0, 500, 100), cell(1, 500, 0, 500, 100)], GW);
  ok(near(scale, 186 / GW), "the grid fills the width between the margins", scale);
  ok(pages.length === 1 && pages[0].length === 2, "two cards that fit make one page");
  const [a, b] = pages[0];
  ok(near(a.x, 12) && near(a.w, 93) && near(b.x, 105) && near(b.w, 93),
     "a half-width card is half the content width, side by side", JSON.stringify(pages[0]));
  ok(near(a.y, R.FIRST_TOP) && near(b.y, R.FIRST_TOP),
     "page 1 starts under the report header, both cards on one row");
  ok(near(a.h, 100 * scale) && near(b.h, a.h), "heights keep the card's shape", a.h);
}
{
  // rows of 250 px (46.5 mm): 5 fit on page 1 (34 .. 285), the 6th starts page 2
  const cells = [];
  for (let i = 0; i < 7; i += 1) cells.push(cell(i, 0, i * 260, 1000, 250));
  const { pages } = R.planPages(cells, GW);
  ok(pages.length === 2, "a card that would cross the page foot starts the next page",
     pages.map((p) => p.length).join(","));
  const n1 = pages[0].length;
  const last = pages[0][n1 - 1];
  ok(last.y + last.h <= 297 - 12 + 0.001, "nothing on page 1 reaches into the foot");
  ok(near(pages[1][0].y, R.NEXT_TOP), "the next page starts near its top, with no header");
  ok(near(pages[1][1].y - pages[1][0].y, 260 * (186 / GW)),
     "the cards below keep their spacing after the move");
}
{
  // two cards on one row where the taller one does not fit: BOTH move together
  const cells = [];
  for (let i = 0; i < 5; i += 1) cells.push(cell(i, 0, i * 260, 1000, 250));
  cells.push(cell(5, 0, 1300, 500, 250), cell(6, 500, 1300, 500, 400));
  const { pages } = R.planPages(cells, GW);
  const find = (id) => pages.flatMap((p, i) => p.map((c) => ({ ...c, page: i }))).find((c) => c.id === id);
  ok(find(5).page === find(6).page && near(find(5).y, find(6).y) && find(5).page === 1,
     "cards that shared a row still share it after paging", JSON.stringify([find(5), find(6)]));
}
{
  const tall = R.planPages([cell(0, 0, 0, 1000, 2000)], GW);
  ok(tall.pages.length === 1 && tall.pages[0].length === 1,
     "a card taller than a page is still placed once, never dropped");
  ok(R.planPages([], GW).pages.length === 1 && R.planPages([], GW).pages[0].length === 0,
     "no cards: one empty page, no crash");
  const unsorted = R.planPages([cell(1, 0, 300, 1000, 100), cell(0, 0, 0, 1000, 100)], GW);
  ok(unsorted.pages[0][0].id === 0, "cards are placed top to bottom whatever order they came in");
}

// -- 2. the words -----------------------------------------------------------------
ok(R.niceDay("2026-10-30") === "30 Oct 2026" && R.niceDay("") === "" && R.niceDay("x") === "x",
   "a day is written the way the sample report writes it");
ok(R.periodText("2026-10-01", "2026-10-31", "") === "1 Oct 2026 to 31 Oct 2026"
   && R.periodText("2026-10-01", "2026-10-31", "2026-10-30") === "30 Oct 2026"
   && R.periodText("2026-10-30", "2026-10-30", "") === "30 Oct 2026",
   "the period is one day or from-to");
ok(R.reportStamp(new Date(2026, 9, 30, 11, 19)) === "2026-10-30 11:19", "Created 2026-10-30 11:19");
ok(R.reportFileName("Staircase 4", "2026-10-01", "2026-10-31", "") === "analytics-staircase-4-2026-10-01_2026-10-31.pdf"
   && R.reportFileName("Staircase 4", "2026-10-01", "2026-10-31", "2026-10-30") === "analytics-staircase-4-2026-10-30.pdf"
   && R.reportFileName("", "", "", "") === "analytics-system.pdf",
   "the file name is safe and says the system and the days");

// -- 3. the real jsPDF ---------------------------------------------------------------
{
  const { jsPDF } = req("jspdf");
  // a 1x1 white JPEG
  const JPG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=";
  const cells = [];
  for (let i = 0; i < 6; i += 1) cells.push(cell(i, 0, i * 260, 1000, 250));
  const { pages } = R.planPages(cells, GW);
  const images = Object.fromEntries(cells.map((c) => [c.id, JPG]));
  let doc = null;
  try {
    doc = R.assemblePdf(jsPDF, { pages, images, title: "IRIV Edge AI · staircase4",
                                 subtitle: "30 Oct 2026", dateLabel: "30 Oct 2026",
                                 created: "2026-10-30 11:19" });
  } catch (e) { ok(false, "assemblePdf runs with the real jsPDF", e.message); }
  if (doc) {
    const raw = Buffer.from(doc.output("arraybuffer")).toString("latin1");
    ok(raw.startsWith("%PDF-"), "it writes a PDF");
    ok(doc.getNumberOfPages() === pages.length, "one PDF page per planned page", doc.getNumberOfPages());
    ok(/ANALYTICS REPORT/.test(raw) || raw.includes("ANALYTICS"), "the red report title is on page 1");
    ok(raw.includes("Page 1 of 2") && raw.includes("Page 2 of 2"), "every page says Page n of N");
    ok(raw.includes("Created 2026-10-30 11:19"), "the creation time is on the first page");
  }
}

// -- 4. the page ----------------------------------------------------------------------
{
  const page = readFileSync(join(SRC, "pages", "Analytics.jsx"), "utf8");
  const lib = readFileSync(join(SRC, "lib", "analyticsReport.js"), "utf8");
  ok(page.includes("data-analytics-pdf") && page.includes("Download PDF")
     && page.includes("downloadAnalyticsPdf"), "the Analytics page has a Download PDF button");
  ok(/<div ref=\{gridRef\}>\s*<WidgetGrid/.test(page), "the report reads the cards of the grid on screen");
  ok(!/^import .*(jspdf|html2canvas-pro)/m.test(page + lib),
     "the libraries are not imported at load, only when the button is pressed");
  ok(/import\("jspdf"\)/.test(lib) && /import\("html2canvas-pro"\)/.test(lib),
     "...they are loaded on demand inside the download");
  ok(/restore = lightTheme\(\)/.test(lib) && /finally \{\s*restore\(\);/.test(lib),
     "the page goes light for the capture and is always put back, even after an error");
  for (const [name, text] of [["Analytics.jsx", page], ["analyticsReport.js", lib]]) {
    ok(!text.includes(String.fromCharCode(0x2014)), `no em dash in ${name}`);
  }
  const pkg = JSON.parse(readFileSync(join(FRONT, "package.json"), "utf8"));
  ok(pkg.dependencies.jspdf === "4.2.1" && pkg.dependencies["html2canvas-pro"] === "2.5.2",
     "both libraries are pinned to an exact version", JSON.stringify([pkg.dependencies.jspdf, pkg.dependencies["html2canvas-pro"]]));
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\ncheck_analyticsreport: ok");
