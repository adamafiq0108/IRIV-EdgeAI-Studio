// analyticsRange - the date range the Analytics page is showing, shared with
// the pages that offer to "follow" it (Tok Aba, 2026-10-14: the SQL
// Playground's Period "Follow Analytics range").
//
// The Analytics page keeps its range in its URL (?start=&end=&day=), which is
// gone the moment somebody navigates away. So it also writes the range it is
// ACTUALLY showing here, every time it changes: the chosen day when one is
// picked, else the whole start..end span - the same pair the page's table
// cards hand to Download CSV as "This page's range" (Analytics.jsx
// exportCsv.pageRange). Session storage: one browser tab's own view, gone with
// the tab, never sent anywhere.
//
// The dates are local YYYY-MM-DD with an INCLUSIVE end, which is exactly what
// the SQL console's period "custom" takes (core/services/sqlconsole.py
// preview_params: from = midnight of the first day, to = midnight after the
// last), so a query with `ts >= %(from)s AND ts < %(to)s` reads the same days
// the Analytics cards do.

export const ANALYTICS_RANGE_KEY = "analytics.range";

const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
  + `-${String(d.getDate()).padStart(2, "0")}`;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Called by the Analytics page with the range it is showing. */
export function saveAnalyticsRange(range) {
  if (!range || !DAY.test(range.start || "") || !DAY.test(range.end || "")) return;
  try {
    sessionStorage.setItem(ANALYTICS_RANGE_KEY,
                           JSON.stringify({ start: range.start, end: range.end }));
  } catch { /* storage off or full: following simply falls back to today */ }
}

/** {start, end, set} for the range to follow. Never set this session: the
 *  Analytics page's own opening view, which is today. */
export function readAnalyticsRange(now = new Date()) {
  try {
    const got = JSON.parse(sessionStorage.getItem(ANALYTICS_RANGE_KEY) || "null");
    if (got && DAY.test(got.start || "") && DAY.test(got.end || "")) {
      return got.start <= got.end ? { ...got, set: true }
        : { start: got.end, end: got.start, set: true };
    }
  } catch { /* unreadable: the default below */ }
  const today = isoDay(now);
  return { start: today, end: today, set: false };
}

/** "14 Oct 2026" or "12 Oct 2026 - 14 Oct 2026". */
export function analyticsRangeWords({ start, end }) {
  const words = (iso) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-GB",
      { day: "numeric", month: "short", year: "numeric" });
  };
  return start === end ? words(start) : `${words(start)} - ${words(end)}`;
}
