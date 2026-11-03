// Shared charting helpers. The library is recharts (SVG, composable) - it
// replaced Chart.js (canvas) so the ReUI chart templates, which are built on
// recharts, can be adopted directly instead of being reimplemented.
//
// No registration step here any more: recharts components import what they
// need. What survives is the part that was actually shared - the palette, the
// colour maths, and one tooltip/axis treatment so every chart in the product
// reads as the same chart.

//: Series fallback palette when a series carries no explicit colour.
export const SERIES_COLORS = ["#04bade", "#f59e0b", "#10b981", "#a855f7",
                             "#ef4444", "#3b82f6", "#eab308", "#ec4899"];

/** `hex` at `alpha`, or a safe blue if it is not a hex colour. */
export function hexToRgba(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return `rgba(29,78,216,${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** White or near-black text, whichever reads on `hex` -- an operator picking
 * a custom card background (Widget.jsx `bg`) chooses the paint, not the
 * theme's usual light/dark text, so the text has to follow the paint or a
 * bright yellow card silently swallows white-on-dark theme text. WCAG's own
 * relative-luminance formula, not a straight average: the eye weighs green
 * far more than blue, so an average would call a pure blue "light" when it
 * reads as dark. */
export function readableTextOn(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return undefined;
  const n = parseInt(m[1], 16);
  const chan = (v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = 0.2126 * chan(n >> 16) + 0.7152 * chan((n >> 8) & 255) + 0.0722 * chan(n & 255);
  return lum > 0.5 ? "#0a0a0a" : "#fff";
}

//: Axis/grid/tooltip treatment, shared so one chart never looks like a
//: different product from the one beside it. Muted enough that the DATA is
//: what carries the colour.
export const AXIS = {
  stroke: "rgba(148,163,184,.55)",
  fontSize: 10,
  tickLine: false,
  axisLine: false,
};

export const GRID = {
  stroke: "rgba(148,163,184,.16)",
  strokeDasharray: "3 3",
  vertical: false,
};

// -- READABLE AXES (rendering addendum, owner 2026-10-13: "the user can see
// the X and Y axis clearly, and every detail") ------------------------------
//
// The muted AXIS above was drawn without lines, at 10px, in a 55% grey: fine
// as decoration, hard to read a value off. A cartesian chart now draws its
// axis lines, tick marks, 11px tick labels and axis titles; the colours here
// are fallbacks for the SVG attributes, and theme.css (.chart-wrap
// .recharts-*) paints them from the theme's own tokens in light and dark.

//: Axis line and tick mark colour (overridden by theme.css).
export const AXIS_LINE = "rgba(100,116,139,.6)";
//: Tick label and axis title colour (overridden by theme.css).
export const AXIS_TEXT = "#64748b";
//: Rough width of one tick-label character at 11px, for layout decisions
//: made before the browser has measured anything.
export const CHAR_PX = 6.4;

/** A value as an axis tick, a data label or a tooltip prints it: grouped
 *  thousands, at most two decimals, 15.3k from ten thousand up. */
export function fmtValue(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const a = Math.abs(n);
  if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (a >= 1e4) return `${(n / 1e3).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, "")}k`;
  return n.toLocaleString("en-US", { maximumFractionDigits: a < 1 && a > 0 ? 3 : 2 });
}

/** A category label cut to `max` characters with an ellipsis (the tooltip
 *  shows it whole). */
export function truncateLabel(v, max = 40) {
  const s = String(v ?? "");
  return s.length > max ? `${s.slice(0, Math.max(1, max - 1))}…` : s;
}

/** How the category (X) axis of a chart `width` px wide fits `labels`:
 *    angle     0, -45 or -90 -- flat while they fit, slanted when short,
 *              upright when long
 *    height    px the axis needs for them (never clipped)
 *    interval  recharts' tick interval: every (interval+1)-th label, so
 *              labels never overlap however many points there are
 *    maxChars  where a label is cut
 *  A TIME axis stays flat and thins instead: "13 Oct 14:30" turned on its
 *  side reads worse than every third one level. `compact` is a small
 *  dashboard tile. */
export function categoryLayout(labels, width, { time = false, compact = false } = {}) {
  const texts = (labels || []).map((l) => String(l ?? ""));
  const n = Math.max(1, texts.length);
  const slot = Math.max(1, width) / n;
  const longest = Math.max(0, ...texts.map((t) => t.length));
  const flatNeed = Math.min(longest, 40) * CHAR_PX + 10;
  if (flatNeed <= slot) return { angle: 0, height: 24, interval: 0, maxChars: 40 };
  if (time) {
    return { angle: 0, height: 24, interval: Math.ceil(flatNeed / slot) - 1, maxChars: 40 };
  }
  const maxChars = compact ? 10 : 18;
  const shown = Math.min(longest, maxChars);
  const angle = shown <= 10 ? -45 : -90;
  const textPx = shown * CHAR_PX;
  const height = Math.round(Math.min(124, (angle === -45 ? textPx * 0.72 : textPx) + 14));
  const need = 15;                  // px between two rotated labels
  return { angle, height, interval: slot >= need ? 0 : Math.ceil(need / slot) - 1,
           maxChars };
}

/** Whether values are printed on the chart: only when the operator chose "on".
 *  Left automatic (or off) the numbers show on hover, in the tooltip -- always-
 *  printed numbers crowded the chart (Tok Aba, 2026-10-27). */
export function showValues(setting, points, seriesCount, { compact = false, kind = "bar" } = {}) {
  return setting === "on" && !compact;
}

export const TOOLTIP = {
  contentStyle: {
    background: "var(--surface2, #1d2026)",
    border: "1px solid rgba(128,128,128,.3)",
    borderRadius: 8,
    fontSize: 12,
    padding: "6px 10px",
    boxShadow: "0 8px 24px rgba(0,0,0,.4)",
  },
  labelStyle: { color: "var(--muted-text, #9ca3af)", marginBottom: 2 },
  itemStyle: { padding: 0 },
  cursor: { stroke: "rgba(148,163,184,.35)", strokeWidth: 1 },
};

/**
 * The widget data contract ({labels, values} or {labels, datasets}) reshaped
 * into what recharts wants: one row per x, one KEY per series.
 *
 * Chart.js took parallel arrays; recharts takes records. Converting here
 * rather than at every call site keeps the SERVER contract unchanged - no
 * layout, no compiled widget and no system package had to be rewritten to
 * change charting library.
 *
 * Returns {rows, series} where series is [{key, label, color}].
 */
//: Month names for axis labels. Fixed English, matching the rest of the
//: product's wording, so a label never changes with the browser's locale.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                "Oct", "Nov", "Dec"];
const two = (n) => String(n).padStart(2, "0");

/** One time-axis label, in the BROWSER's clock (services/timeaxis.py sends
 *  each point's start as epoch ms, so the server's clock never decides what
 *  the viewer reads):
 *    a month or longer        "Oct 2026"
 *    a day or longer          "13 Oct"
 *    shorter, all in one day  "14:30"
 *    shorter, across days     "13 Oct 14:30"
 */
export function axisLabel(ms, stepS, spanMs) {
  const d = new Date(ms);
  if (stepS >= 28 * 86400) return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  if (stepS >= 86400) return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  const hm = `${two(d.getHours())}:${two(d.getMinutes())}`;
  return spanMs && spanMs.oneDay ? hm : `${d.getDate()} ${MONTHS[d.getMonth()]} ${hm}`;
}

/** A point's whole bucket, start to end, for the tooltip:
 *  "13 Oct 2026 14:00 - 20:00", "13 Oct 2026 - 14 Oct 2026". */
export function bucketTitle(ms, stepS) {
  const a = new Date(ms);
  const b = new Date(ms + stepS * 1000);
  const day = (d) => `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  const hm = (d) => `${two(d.getHours())}:${two(d.getMinutes())}`;
  if (stepS >= 86400) return `${day(a)} - ${day(b)}`;
  return a.toDateString() === b.toDateString()
    ? `${day(a)} ${hm(a)} - ${hm(b)}`
    : `${day(a)} ${hm(a)} - ${day(b)} ${hm(b)}`;
}

/** The labels of a time axis the server sent as `keys` (epoch ms) and
 *  `step_s`, or null when it sent none -- older payloads keep their own
 *  server-written labels. */
export function timeLabels(data) {
  const keys = Array.isArray(data.keys) ? data.keys : null;
  const step = Number(data.step_s) || 0;
  if (!keys || !keys.length || !step
      || keys.length !== (data.labels || []).length) return null;
  const first = new Date(keys[0]);
  const last = new Date(keys[keys.length - 1]);
  const oneDay = first.toDateString() === last.toDateString();
  return keys.map((ms) => axisLabel(ms, step, { oneDay }));
}

export function toRows(data, fallbackLabel, accent) {
  const timed = timeLabels(data);
  const labels = timed || data.labels || [];
  const multi = Array.isArray(data.datasets) && data.datasets.length > 0;
  const series = multi
    ? data.datasets.map((d, i) => ({
        key: `s${i}`,
        // The series' own id, as the editor and the server know it. The
        // recharts dataKey stays POSITIONAL -- it has to be unique inside one
        // chart, and an id is only unique inside one widget's config.
        id: d.id || `s${i}`,
        label: d.label || `series ${i + 1}`,
        // Unset colour falls back BY POSITION, which is the rule the series
        // editor's swatches mirror: eight distinct colours before any repeat.
        color: d.color || SERIES_COLORS[i % SERIES_COLORS.length],
        // The series' OWN threshold bands (per camera, owner 2026-10-13),
        // or null to follow the chart's shared ones.
        bands: d.bands && Array.isArray(d.bands.at) && d.bands.at.length ? d.bands : null,
      }))
    : [{ key: "s0", label: fallbackLabel || "value",
         color: data.color || accent }];

  // A point a series HAS NO VALUE FOR comes back as null: several inputs
  // resampled onto one axis rarely all have a reading at every bucket. It is
  // carried through as undefined, deliberately, because that is the one value
  // both consumers read the same way -- recharts leaves a gap for it, and
  // Number(undefined) is NaN rather than Number(null)'s 0, which would let a
  // hole drag a chart's axis or a threshold band down to zero and show a
  // measurement nobody took.
  const at = (values, i) => {
    const v = (values || [])[i];
    return v === null ? undefined : v;
  };
  const rows = labels.map((label, i) => {
    const row = { x: label };
    // The whole bucket, for the tooltip (Widget.jsx labelFormatter).
    if (timed) row.span = bucketTitle(data.keys[i], Number(data.step_s));
    if (multi) {
      data.datasets.forEach((d, s) => { row[`s${s}`] = at(d.values, i); });
    } else {
      row.s0 = at(data.values, i);
    }
    return row;
  });
  return { rows, series };
}

//: The palette a multi-day chart cycles through for the days BEFORE the
//: newest one. Deliberately not random: a colour that changes on every poll
//: is a colour nobody can learn, and "Tuesday is the orange one" is the whole
//: point of the feature. Ordered so adjacent days never land on neighbouring
//: hues, because two days side by side are exactly the pair that has to be
//: told apart.
export const DAY_COLORS = ["#f59e0b", "#a855f7", "#10b981", "#ef4444",
                           "#3b82f6", "#eab308", "#ec4899", "#14b8a6"];

/** The day a chart label belongs to, or null if it is not a dated label.
 *
 *  Labels are built by builder._short_label, which gives a time bucket
 *  "MM-DD HH:MM" and a whole-day bucket "MM-DD" -- so the day is the first
 *  five characters of either. Anything else (a zone name, a number) is not a
 *  date and must not be guessed at: colouring "zone 1" and "zone 2" as two
 *  different days would be a confident lie.
 */
export function dayOf(label) {
  const s = String(label ?? "");
  if (/^\d{2}-\d{2}$/.test(s)) return s;
  // A browser-written time-axis label (axisLabel): "13 Oct", "13 Oct 14:30".
  const named = s.match(/^(\d{1,2} [A-Z][a-z]{2})(?: \d{2}:\d{2})?$/);
  if (named) return named[1];
  if (/^\d{2}-\d{2} \d{2}:\d{2}$/.test(s)) return s.slice(0, 5);
  // A full ISO stamp, in case a label reaches a chart untrimmed.
  if (/^\d{4}-\d{2}-\d{2}[T ]/.test(s)) return s.slice(5, 10);
  return null;
}

/** The distinct days a label list covers, in the order they appear, or null
 *  when it covers fewer than two -- one day needs no colouring, and a chart
 *  of zones is not a chart of days.
 *
 *  Returns [{day, from, to}] as INDEX positions into the labels (inclusive),
 *  plus each day's colour: the newest day keeps the accent the operator
 *  chose, and the ones before it walk back through DAY_COLORS. Newest-first
 *  because the accent is the colour they set for "this card", and the day
 *  they are looking at is today.
 */
export function daySpans(labels, accent) {
  const spans = [];
  for (let i = 0; i < labels.length; i += 1) {
    const day = dayOf(labels[i]);
    if (day === null) return null;              // not a dated axis at all
    const last = spans[spans.length - 1];
    if (last && last.day === day) last.to = i;
    else spans.push({ day, from: i, to: i });
  }
  if (spans.length < 2) return null;
  const n = spans.length;
  return spans.map((s, i) => ({
    ...s,
    color: i === n - 1 ? accent : DAY_COLORS[(n - 2 - i) % DAY_COLORS.length],
  }));
}

/** Hard-stop gradient stops that colour a line by the DAY it is passing
 *  through, the same trick bandStops plays on the value axis.
 *
 *  Across x rather than down y, so the gradient is horizontal and its offsets
 *  are index positions: a boundary sits half a sample before the first point
 *  of the new day, which is where the eye reads the change anyway. Two stops
 *  per boundary, because a day that fades into the next over ten pixels is a
 *  day nobody can point at.
 *
 *  `focus` dims every day but one -- the answer to "which of these humps was
 *  Tuesday", which a legend alone cannot give on a busy chart.
 */
export function dayStops(spans, count, focus, base = 1, dim = 0.12) {
  if (!spans || count < 2) return null;
  // Opacity, never an rgba() stop-color: SVG reads a stop's alpha from
  // stop-opacity, and a browser that ignores rgba() there paints the dimmed
  // day at full strength -- which is the one thing "dim the others" must not
  // do.
  const fade = (s) => (focus && s.day !== focus ? dim : base);
  const clamp = (v) => Math.max(0, Math.min(1, v));
  const stops = [];
  spans.forEach((s, i) => {
    const start = i === 0 ? 0 : (s.from - 0.5) / (count - 1);
    const end = i === spans.length - 1 ? 1 : (s.to + 0.5) / (count - 1);
    stops.push({ off: clamp(start), color: s.color, opacity: fade(s) });
    stops.push({ off: clamp(end), color: s.color, opacity: fade(s) });
  });
  return stops;
}
