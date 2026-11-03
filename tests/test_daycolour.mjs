// A chart that spans days is coloured a day at a time, and one day can be
// looked at on its own.
//
// The picture in the screenshot was two days of hourly violation counts drawn
// as one line in one colour: the humps were visible, WHICH DAY each hump
// belonged to was not, so reading it meant counting tick labels along the
// bottom axis -- the reading the chart existed to save. So the day is a
// colour, the newest day keeps the accent the operator set for the card, and
// clicking a day dims the rest until it is clicked again.
//
//     node tests/test_daycolour.mjs
import fs from "node:fs";
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
let bad = 0;

function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : "\n      " + detail));
  if (!ok) bad += 1;
}

const { dayOf, daySpans, dayStops, DAY_COLORS } =
  await import("file://" + join(REPO, "frontend", "src", "components", "charts.js"));

// -- 1. what counts as a dated label -----------------------------------------
check("an hour bucket names its day", dayOf("09-07 13:00") === "09-07");
check("a day bucket is its own day", dayOf("09-08") === "09-08");
check("a full ISO stamp still names its day",
      dayOf("2026-09-07T13:00:00") === "09-07");
check("a zone name is NOT a date",
      dayOf("zone 1") === null && dayOf("42") === null,
      "colouring 'zone 1' and 'zone 2' as two different days is a confident lie");

// -- 2. the spans ------------------------------------------------------------
const LABELS = ["09-07 22:00", "09-07 23:00", "09-08 00:00", "09-08 01:00",
                "09-09 00:00"];
const spans = daySpans(LABELS, "#ff0000");
check("consecutive samples of one day are one span",
      spans.length === 3 && spans[0].from === 0 && spans[0].to === 1
      && spans[1].from === 2 && spans[1].to === 3);
check("the NEWEST day keeps the card's accent",
      spans[spans.length - 1].color === "#ff0000",
      "the accent is the colour they set for this card; today is the day they are looking at");
check("...and the days before it take distinct palette colours",
      spans[0].color !== spans[1].color
      && DAY_COLORS.includes(spans[0].color) && DAY_COLORS.includes(spans[1].color));
check("the same day list always gets the same colours",
      JSON.stringify(daySpans(LABELS, "#ff0000")) === JSON.stringify(spans),
      "a colour that changes on every poll is a colour nobody can learn");
check("one day is not coloured by day at all",
      daySpans(["09-07 01:00", "09-07 02:00"], "#ff0000") === null);
check("...and neither is an axis of names",
      daySpans(["zone 1", "zone 2"], "#ff0000") === null);

// -- 3. the gradient ---------------------------------------------------------
const stops = dayStops(spans, LABELS.length, "");
check("every boundary emits two stops at the same offset",
      stops.length === spans.length * 2
      && stops[1].off === stops[2].off,
      "a day that fades into the next over ten pixels is a day nobody can point at");
check("...running the full width of the picture",
      stops[0].off === 0 && stops[stops.length - 1].off === 1);
check("...in order",
      stops.every((s, i) => i === 0 || s.off >= stops[i - 1].off));

const focused = dayStops(spans, LABELS.length, "09-08");
check("focusing a day dims every other one",
      focused.filter((s) => s.opacity < 0.5).length === 4
      && focused.filter((s) => s.opacity === 1).length === 2);
check("...through stop-opacity, never an rgba() stop colour",
      focused.every((s) => /^#[0-9a-f]{6}$/i.test(s.color)),
      "a browser that ignores rgba() in stop-color paints the dimmed day at "
      + "full strength, which is the one thing 'dim the others' must not do");

// -- 4. wired into the chart and the inspector -------------------------------
const widget = readText(join(REPO, "frontend", "src", "components", "widgets", "Widget.jsx"));
check("the line's stroke carries the day",
      widget.includes("dayCuts ? `url(#${gid}-days)` : s.color"));
check("...and thresholds still win where both are set",
      widget.indexOf("stops ? `url(#${gid}-bands)`")
        < widget.indexOf("dayCuts ? `url(#${gid}-days)`")
      && widget.includes("const dayCuts = !banded"),
      "'the colour is the level' and 'the colour is the day' are two meanings "
      + "for one channel; a limit crossed is the more urgent reading");
check("the day gradient runs ACROSS the picture, not down it",
      widget.includes('x1="0" y1="0" x2="1" y2="0"'));
check("a bar chart takes the same colouring per bar",
      widget.includes("dayAt ? dayAt.map(") && widget.includes("<Cell key={i} fill={d ? d.color : s.color}"));
check("clicking the chart focuses that day",
      widget.includes("pickDay(dayOf(e?.activeLabel))"));
check("...clicking it again gives the whole chart back",
      widget.includes("setFocusDay((cur) => (cur === day ? \"\" : day || \"\"))"),
      "a highlight that expires on a timer goes away exactly when somebody is still looking at it");
check("...and the focus is NOT saved with the layout",
      widget.includes("const [focusDay, setFocusDay] = useState(\"\")")
      && !widget.includes("patch(current.id, { focusDay"),
      "it is a way of reading the picture, not a setting; the next person to "
      + "open the dashboard should see all of it");
check("the day key sits outside the sized chart box",
      widget.includes("<div className=\"chart-block\">")
      // (the legend, a row of switches since the legend toggle, sits between
      // the sized box and the day key -- both outside the box)
      && widget.includes("</div>\n          {/* The legend names the series")
      && widget.includes("{legendBox}\n          {dayKey}"),
      "chart-wrap is exactly the height the operator dragged the card to");

const shared = readText(join(REPO, "frontend", "src", "components", "widgets", "builderShared.jsx"));
check("the inspector offers it as a checkbox, on dated chart kinds only",
      shared.includes("export function DayColourToggle(")
      && shared.includes('DAY_KINDS = ["line", "area", "spline", "bar"]')
      && shared.includes("onChange({ by_day: e.target.checked })"));
check("...and it is off on a brand-new widget",
      shared.includes("by_day: false"),
      "a card that recolours itself the day somebody widens the range is a card that broke");

const builder = readText(join(REPO, "core", "services", "builder.py"));
check("the server stores it and hands it to the page",
      (builder.match(/"by_day":/g) || []).length >= 2
      && builder.includes('DAY_COLOUR_KINDS = ("line", "area", "spline", "bar")'),
      "stored, compiled, previewed in the inspector and still rendering plain "
      + "everywhere is exactly how `template` and `bands` each broke once");
check("...and refuses it on a kind with no dated axis",
      builder.includes("if kind in DAY_COLOUR_KINDS\n                       else False"));

const css = readText(join(REPO, "frontend", "src", "theme.css"));
check("the day chips are a hit target, not a 10px swatch",
      css.includes(".chart-day {") && css.includes("padding: 2px 7px"));
check("...and a dimmed chip stays readable",
      css.includes(".chart-day.is-off { opacity: .45; }"),
      "it is still the control that brings its day back");

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
