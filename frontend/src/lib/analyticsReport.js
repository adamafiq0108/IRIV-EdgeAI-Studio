// analyticsReport.js - the Analytics page as a PDF report.
//
// The report is the page the operator is looking at, card by card, on A4 pages
// with a report header on the first page and the date and "Page n of N" at the
// foot of every page. It is made in the browser: each card is drawn to a
// picture with html2canvas-pro (the fork that reads oklch and the other new colour functions the app styles use) and placed with jsPDF, so it reproduces whatever
// the Builder put on the page (a chart, a verdict, a custom card) without a
// second description of every card kind on the server. Both libraries are
// loaded only when the button is pressed, so the page itself costs nothing.
//
// THE LAYOUT keeps the operator's arrangement: a card is placed where it sits
// on screen (its position and width relative to the grid, scaled to the
// paper), and a card is never cut in two -- one that would cross the foot of a
// page starts the next page instead. planPages() is that rule on its own, with
// no DOM, so tests/check_analyticsreport.mjs can pin it.
//
// The pictures are taken in the light theme whatever the screen shows: paper is
// white, and a dark card on a printed page is unreadable and expensive.

//: A4 portrait, millimetres.
export const PAGE = { w: 210, h: 297, margin: 12, footer: 12, gap: 3 };

//: Where content starts on page 1 (under the report header) and on the others.
export const FIRST_TOP = 34;
export const NEXT_TOP = 14;

/**
 * Place cards on pages.
 *   cells      [{ id, left, top, width, height }] in screen pixels, relative to
 *              the grid's own top-left corner.
 *   gridWidth  the grid's width in pixels (what `left` and `width` are of).
 * Returns { pages: [[{ id, x, y, w, h }]], scale } with x, y, w, h in mm.
 *
 * A card keeps its place relative to the cards beside it. Paging only ever
 * moves a card DOWN, to the top of the next page, and moves everything at or
 * below it by the same amount, so two cards that shared a row still do.
 */
export function planPages(cells, gridWidth, { page = PAGE, firstTop = FIRST_TOP,
                                              nextTop = NEXT_TOP } = {}) {
  const contentW = page.w - 2 * page.margin;
  const bottom = page.h - page.footer;
  const scale = gridWidth > 0 ? contentW / gridWidth : 1;
  const sorted = [...cells].sort((a, b) => a.top - b.top || a.left - b.left);
  const pages = [[]];
  let top = firstTop;                 // where the current page's content starts
  let origin = sorted.length ? sorted[0].top * scale : 0;   // grid mm at that start
  for (const c of sorted) {
    const w = c.width * scale;
    const h = c.height * scale;
    const x = page.margin + c.left * scale;
    let y = top + (c.top * scale - origin);
    if (y + h > bottom && pages[pages.length - 1].length) {
      // It does not fit under what is already on this page: a fresh page,
      // starting at this card's own row.
      pages.push([]);
      top = nextTop;
      origin = c.top * scale;
      y = top;
    }
    pages[pages.length - 1].push({ id: c.id, x, y, w, h });
  }
  return { pages, scale };
}

/**
 * Put the live page in the light theme behind a full-window cover, and return
 * the function that puts everything back. Safe where there is no document.
 */
export function lightTheme(doc = typeof document !== "undefined" ? document : null) {
  if (!doc) return () => {};
  const root = doc.documentElement;
  const hadDark = root.classList.contains("dark");
  const theme = root.getAttribute("data-theme");
  const cover = doc.createElement("div");
  cover.setAttribute("role", "status");
  cover.setAttribute("data-report-cover", "");
  cover.textContent = "Preparing the PDF report...";
  cover.style.cssText = "position:fixed;inset:0;z-index:2147483647;display:flex;"
    + "align-items:center;justify-content:center;background:#ffffff;color:#475069;"
    + "font:600 15px system-ui,sans-serif;";
  doc.body.appendChild(cover);
  root.classList.remove("dark");
  root.setAttribute("data-theme", "light");
  return () => {
    if (hadDark) root.classList.add("dark");
    if (theme === null) root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    cover.remove();
  };
}

/** Let the page repaint (and a chart redraw) after a change of theme. */
export function reportSettle() {
  return new Promise((ok) => {
    if (typeof requestAnimationFrame !== "function") { ok(); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(ok, 120)));
  });
}

/** "2026-10-30 11:19" in the browser's own clock. */
export function reportStamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "30 Oct 2026" from "2026-10-30". Anything else comes back as it was. */
export function niceDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : String(iso || "");
}

/** The period a report covers, in words: one day, or from - to. */
export function periodText(start, end, day) {
  if (day) return niceDay(day);
  if (start && end && start !== end) return `${niceDay(start)} to ${niceDay(end)}`;
  return niceDay(start || end);
}

/** A file name that is safe on every system: lower case, digits, dashes. */
export function reportFileName(system, start, end, day) {
  const slug = String(system || "system").toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "system";
  const span = day || (start && end && start !== end ? `${start}_${end}` : (start || end || ""));
  return `analytics-${slug}${span ? `-${span}` : ""}.pdf`;
}

/** The cards of an Analytics grid, with their place in it. */
export function cellsOf(grid) {
  const box = grid.getBoundingClientRect();
  const nodes = grid.querySelectorAll(":scope > .wgt-grid > .wgt-cell, :scope > .stats-grid > *");
  return {
    gridWidth: box.width,
    nodes: [...nodes],
    cells: [...nodes].map((el, i) => {
      const r = el.getBoundingClientRect();
      return { id: i, left: r.left - box.left, top: r.top - box.top,
               width: r.width, height: r.height };
    }),
  };
}

/**
 * Put the pictures on the pages. Pure apart from the jsPDF it is given, so a
 * test can run it with the real library and a stand-in picture.
 *   images  { id: data URL } for every card in `pages`
 * Returns the jsPDF document.
 */
export function assemblePdf(jsPDF, { pages, images, title, subtitle, dateLabel,
                                     created = reportStamp() }) {
  const doc = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });
  pages.forEach((placed, p) => {
    if (p > 0) doc.addPage();
    for (const place of placed) {
      doc.addImage(images[place.id], "JPEG", place.x, place.y, place.w, place.h,
                   undefined, "FAST");
    }
  });
  const n = pages.length;
  for (let p = 1; p <= n; p += 1) {
    doc.setPage(p);
    if (p === 1) header(doc, { title, subtitle, dateLabel, created });
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(140, 146, 158);
    doc.text(dateLabel, PAGE.margin, PAGE.h - 7);
    doc.text(`Page ${p} of ${n}`, PAGE.w - PAGE.margin, PAGE.h - 7, { align: "right" });
  }
  return doc;
}

/**
 * Build the PDF and hand it to the browser as a download.
 *   grid      the element that holds the page's WidgetGrid
 *   title     "IRIV Edge AI · <system>"
 *   subtitle  under the title (the period)
 *   dateLabel the bold date at the right of the header
 *   fileName  what the download is called
 *   onProgress(done, total) between cards
 */
export async function downloadAnalyticsPdf({ grid, title, subtitle, dateLabel,
                                             fileName, onProgress }) {
  const { nodes, cells, gridWidth } = cellsOf(grid);
  if (!nodes.length) throw new Error("There is nothing on this page to put in a report.");
  const [{ jsPDF }, { default: html2canvas }] = await Promise.all([
    import("jspdf"), import("html2canvas-pro")]);

  const { pages, scale } = planPages(cells, gridWidth);
  const images = {};
  let done = 0;
  // Paper is white: the pictures are taken in the light theme. html2canvas
  // copies the computed colours of a chart's SVG from the LIVE page (a clone
  // cannot be re-themed), so the page itself goes light for the few seconds
  // this takes, behind a cover that says what is happening -- nobody sees the
  // flash -- and goes back exactly as it was.
  const restore = lightTheme();
  await reportSettle();
  try {
  for (const placed of pages) {
    for (const place of placed) {
      const el = nodes[place.id];
      const canvas = await html2canvas(el, {
        // About 150 dpi at the size it lands on paper, never blurry, never huge.
        scale: Math.min(3, Math.max(1.5, (place.w / 25.4) * 150 / Math.max(1, el.offsetWidth))),
        backgroundColor: "#ffffff",
        logging: false,
        useCORS: true,
      });
      images[place.id] = canvas.toDataURL("image/jpeg", 0.92);
      done += 1;
      if (onProgress) onProgress(done, nodes.length);
    }
  }
  } finally {
    restore();
  }
  const doc = assemblePdf(jsPDF, { pages, images, title, subtitle, dateLabel });
  doc.save(fileName);
  return { pages: pages.length, cards: nodes.length, scale };
}

/** The first page's header: product and system at the left, the red report
 *  title with the date and the creation time at the right, a red rule under. */
function header(doc, { title, subtitle, dateLabel, created }) {
  const L = PAGE.margin;
  const R = PAGE.w - PAGE.margin;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(15);
  doc.setTextColor(26, 31, 44);
  doc.text(title, L, 18);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(110, 118, 135);
  if (subtitle) doc.text(subtitle, L, 24);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  doc.setTextColor(179, 38, 30);
  doc.text("ANALYTICS REPORT", R, 17, { align: "right" });
  doc.setFontSize(10);
  doc.setTextColor(26, 31, 44);
  doc.text(dateLabel, R, 23, { align: "right" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8.5);
  doc.setTextColor(110, 118, 135);
  doc.text(`Created ${created}`, R, 28, { align: "right" });
  doc.setDrawColor(179, 38, 30);
  doc.setLineWidth(0.5);
  doc.line(L, 30, R, 30);
}
