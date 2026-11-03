// THE in / does / out SUMMARY of every workflow node (Tok Aba, 2026-10-14:
// "all nodes should have this", then "in the PROPERTIES of every node only,
// not at the node itself"). Three rows -- what goes in, what is done to it,
// what comes out -- at the top of the node's Properties panel
// (pages/Workflow.jsx NodeSummaryBox), read-only: the settings under it stay
// the one place anything changes, so the box can never disagree with them.
// The node faces stay compact; the object watch and the Counter used to wear
// these rows on the face, and now read them here like every other node.
//
// Three tiers, so no node is ever blank and no row invents a fact:
//
//   1. `in` and `out` from the node's wiring role, in ONE small vocabulary
//      (TYPE_WORDS) -- the same word for the same kind of wire everywhere;
//      `out` names the type only, `does` says the meaning.
//   2. a hand-written `does` that follows the node's CURRENT settings, for the
//      nodes people use most (DOES below);
//   3. otherwise the registry's own description, first sentence, cut to fit.
//
// Rows are at most ROW_MAX characters (they sit in a 210-280px card), plain
// beginner words, no em dash. Pure functions only: no React, no fetch.
import { chartType, chartTypeOf } from "../components/widgets/chartTypes.js";
import { shapeLabel } from "../components/widgets/cardFields.jsx";

//: The words a wire's TYPE is said in, on every node. Keys are what the
//: tables below use; the values are what the reader sees.
export const TYPE_WORDS = {
  objects: "objects",
  number: "number",
  yesno: "yes/no (0/1)",
  text: "text",
  value: "any value",
  picture: "picture",
  rows: "table rows",
  file: "file",
  event: "event",
  time: "time",
  zone: "zone",
  line: "line",
  none: "nothing",
};
export const ROW_MAX = 44;

//: Nodes that have no summary: canvas furniture, not steps of a pipeline.
//: A Comment is a note, and a Link pair is a wire that jumps.
export const SUMMARY_SKIP = ["note.comment", "common.link_in", "common.link_out"];

/** `s` cut to ROW_MAX at a word boundary, with "..." when cut. */
export function fit(s, max = ROW_MAX) {
  const t = String(s ?? "").replace(/\s+/g, " ").replace(/\u2014/g, "--").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 3);
  const sp = cut.lastIndexOf(" ");
  return `${(sp > max / 2 ? cut.slice(0, sp) : cut).replace(/[\s,.;:·-]+$/, "")}...`;
}

const list = (xs, n = 3) => {
  const a = (xs || []).filter(Boolean).map(String);
  if (!a.length) return "";
  return a.length > n ? `${a.slice(0, n).join(", ")} +${a.length - n}` : a.join(", ");
};
const num = (v, d = 0) => (v === undefined || v === null || v === "" ? d : v);
const OPS = { ">": "over", ">=": "at least", "<": "under", "<=": "at most", "==": "equal to",
              "!=": "not", between: "between", changed: "changed", rising: "turns on",
              falling: "turns off", above: "over", below: "under", equal: "equal to",
              outside: "outside", inside: "between", on: "on", off: "off" };
function ruleWords(r) {
  if (!r) return "";
  const op = OPS[r.op] || r.op || "";
  if (["changed", "rising", "falling", "on", "off"].includes(r.op)) return op;
  if (["between", "inside", "outside"].includes(r.op)) return `${op} ${num(r.value)} and ${num(r.value2)}`;
  return `${op} ${num(r.value)}`;
}

// -- tier 1: what goes in, what comes out ----------------------------------------

//: in / out per type, as TYPE_WORDS keys. A list on `in` reads "a · b".
//: Category defaults first, then the types that differ.
const CATEGORY_IO = {
  source: [["none"], "objects"],
  filter: [["objects"], "objects"],
  zone: [["none"], "zone"],
  analytics: [["objects"], "number"],
  variable: [["none"], "number"],
  logic: [["value"], "yesno"],
  dashboard: [["value"], "none"],
  output: [["yesno"], "none"],
  query: [["rows"], "rows"],
  common: [["value"], "value"],
};
const TYPE_IO = {
  "common.inject": [["none"], "value"],
  "common.debug": [["value"], "none"],
  "function.start": [["none"], "value"],
  "function.end": [["value"], "value"],
  "function.call": [["value"], "value"],
  "state.watch": [["none"], "yesno"],
  "filter.preprocess": [["picture"], "picture"],
  "analytics.dwell": [["objects", "zone"], "number"],
  "analytics.travel": [["line", "line"], "number"],
  "analytics.speed": [["line", "line"], "number"],
  "analytics.cycle_time": [["zone", "zone"], "number"],
  "analytics.heatmap": [["objects", "zone"], "number"],
  "analytics.occupancy": [["objects", "zone"], "number"],
  "analytics.crowd": [["objects", "zone"], "number"],
  "analytics.visitors": [["objects", "zone"], "number"],
  "analytics.object": [["objects", "zone"], "yesno"],
  "analytics.object_frame": [["objects"], "yesno"],
  "analytics.object_zone": [["objects", "zone"], "yesno"],
  "analytics.anomaly": [["objects", "zone"], "yesno"],
  "analytics.counter": [["objects", "zone", "line"], "number"],
  "analytics.entry_exit": [["objects", "line"], "number"],
  "analytics.violations": [["objects", "zone"], "number"],
  "analytics.total": [["value"], "number"],
  "analytics.expr": [["number"], "number"],
  "analytics.formula": [["number"], "number"],
  "analytics.python": [["objects"], "number"],
  "analytics.block": [["value"], "number"],
  "math.andor": [["yesno"], "yesno"],
  "math.not": [["yesno"], "yesno"],
  "math.setvar": [["value"], "value"],
  "variable.schedule": [["none"], "event"],
  "variable.board_input": [["none"], "yesno"],
  "variable.ref": [["value"], "value"],
  "variable.formula": [["none"], "number"],
  "variable.mqtt": [["none"], "number"],
  "variable.api": [["none"], "number"],
  "variable.rs485": [["none"], "number"],
  "logic.delay": [["value"], "value"],
  "logic.catch": [["event"], "event"],
  "output.trigger": [["yesno"], "event"],
  "output.alert": [["value"], "event"],
  "output.event": [["yesno"], "event"],
  "output.dataset": [["value"], "rows"],
  "output.webhook_publish": [["value"], "none"],
  "output.mqtt_publish": [["value"], "none"],
  "output.modbus_reg": [["number"], "none"],
  "output.io_pwm": [["number"], "none"],
  "output.zone_switch": [["yesno"], "none"],
  "storage.query": [["rows"], "rows"],
  "storage.csv": [["rows"], "file"],
  "integration.command": [["text"], "rows"],
  "integration.photos": [["event"], "picture"],
  "integration.watch": [["rows"], "event"],
  "output.email": [["event"], "none"],
  "output.telegram": [["event"], "none"],
};
const IO_BY_PREFIX = [["math.", [["number"], "number"]], ["branch.", [["value"], "yesno"]],
                      ["dashboard.", [["value"], "none"]]];

/** [inKeys, outKey] for a type. */
export function ioOf(type, spec = {}) {
  if (TYPE_IO[type]) return TYPE_IO[type];
  const pre = IO_BY_PREFIX.find(([p]) => type.startsWith(p));
  if (pre) return pre[1];
  return CATEGORY_IO[spec.category] || [["value"], "value"];
}

// -- tier 2: what it does, from its settings --------------------------------------

const SCHEDULE = (d) => {
  const at = d.at || "12:00";
  switch (d.every) {
    case "minutes": return `fire every ${num(d.interval_min, 15)} minutes`;
    case "hourly": return `fire every hour at :${String(num(d.minute)).padStart(2, "0")}`;
    case "weekly": return `fire weekly on ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][num(d.weekday)] || "Mon"} at ${at}`;
    case "monthly": return `fire monthly on day ${num(d.day, 1)} at ${at}`;
    default: return `fire daily at ${at}`;
  }
};
const INJECT_WHAT = { timestamp: "the time", string: "text", boolean: "yes/no",
                      json: "JSON" };
//: What an Inject or a constant hands out, by its own type setting.
const INJECT_OUT = { timestamp: "time", string: "text", boolean: "yesno", json: "text" };
const VALUE_OUT = { text: "text", boolean: "yesno" };
const SEVERITY = { info: "info", warn: "warning", critical: "critical" };
const DWELL = { countdown: "time budget each arrival spends", present: "how long objects stay",
                dwell: "how long objects stay", timer: "how long objects stay",
                stamps: "how long objects stay", occupancy: "clock runs while occupied" };
const CTYPE = { switch: "pick a way per value", cases: "if / else if tests",
                range: "sort into bands", onoff: "split on and off", multi: "tests joined" };

/** Hand-written `does` for the commonly used nodes, from `d` (node data).
 *  ctx: {zoneWired, cmode, zoneName, opLabel, chartName, shapeName}. "" = none. */
function doesOf(type, d, ctx, spec = {}) {
  // Short enough to sit after a sentence inside ROW_MAX.
  const where = ctx.zoneWired ? " · inside the zone" : " · whole view";
  const rules = Array.isArray(d.rules) ? d.rules : [];
  // The object watch and the Counter family: what their old face box said.
  const distinct = (d.mode || "realtime") !== "realtime";
  switch (type) {
    case "analytics.object":
      return ctx.zoneWired ? "detect inside the zone wired in" : "detect in the whole view";
    case "analytics.object_frame":
      return "detect in the whole view";
    case "analytics.object_zone":
      return "detect inside the zone wired in";
    case "analytics.anomaly":
      return ctx.zoneWired ? "NG if a snap finds one inside the zone" : "NG if a snap finds one, else GOOD";
    case "analytics.entry_exit":
      return `count what crosses the line · ${{ in: "going in", out: "going out",
                                                  both: "both ways" }[d.mode || "in"] || "going in"}`;
    case "analytics.counter":
      // A Line on its Line port makes it a line counter (registry.counterMode).
      if (ctx.cmode === "line") return "count what crosses the line";
      return (distinct ? "count each object once" : "count what is there now") + where;
    case "analytics.frame":
      return distinct ? "count each object once" : "count what is in view now";
    case "analytics.violations":
      return distinct ? "count each entrant once"
        : "count what is inside the zone wired in";
    case "source.detection":
      return `what the AI sees${d.model ? ` · ${d.model}` : " · every model"}`
        + (d.camera ? ` · ${d.camera}` : "");
    case "filter.class":
      // Off (its switch, the dashboard card's pause): the branch is held.
      if (d.running === false) return "off · the rules after it are paused";
      return (d.classes || []).length ? `keep only ${list(d.classes)}` : "keep every class (none picked)";
    case "filter.preprocess":
      return (d.steps || []).length ? `adjust the picture · ${d.steps.length} steps`
        : "adjust the picture (no steps yet)";
    case "zone.ref":
      return d.zone ? `the area "${ctx.zoneName || d.zone}"` : "pick a zone in the settings";
    case "analytics.dwell":
      return (DWELL[d.mode || "present"] || DWELL.present)
        + (["occupancy"].includes(d.mode) ? "" : where);
    case "analytics.travel":
      return d.speed && d.speed !== "off" ? `speed from line A to line B (${d.speed_unit || "kmh"})`
        : "time from line A to line B";
    case "analytics.heatmap":
      return `map where objects stand · ${num(d.cols, 12)}x${num(d.rows, 8)}`;
    case "analytics.proximity":
      return `${d.class_a || "any"} near ${d.class_b || "any"} · under ${Math.round(num(d.max_distance, 0.15) * 100)}%`;
    case "analytics.guard":
      return `bubble round ${list(d.classes, 2) || "(no class)"} · ${num(d.margin_pct, 50)}%`;
    case "analytics.trails":
      return `trail behind ${list(d.classes, 2) || "every object"} · ${num(d.length_s, 10)}s`;
    case "analytics.total":
      return (d.mode === "sum" ? "increment by every increase" : "count events (+1 each time it turns on)")
        + (d.reset === "daily" ? " · resets daily" : "");
    case "analytics.expr":
      return d.expr ? `work out ${d.expr}` : "work out a formula (none yet)";
    case "analytics.formula":
    case "math.arith": case "math.extreme": case "math.average": case "math.round":
    case "math.clamp": case "math.power": case "math.root": case "math.percent":
    case "math.scale": case "math.blend": case "math.advanced": case "math.andor":
    case "math.not":
      // The operation picked, else the node's own name (a one-operation
      // node: "Round", "Not").
      return `work out: ${ctx.opLabel || ((spec.ops || {})[d.op] || {}).label || spec.label || d.op || "?"}`;
    case "math.setvar":
      return "store the second value in the Variable";
    case "variable.number":
      return `hold ${d.value_type === "text" ? `"${d.value ?? ""}"` : num(d.value)}`;
    case "variable.ref":
      return d.key ? `the variable "${String(d.key).replace(/^fn_(.+)_value$/, "$1")}"`
        : "a named value (pick one)";
    case "variable.schedule":
      return SCHEDULE(d);
    case "variable.mqtt":
      return d.topic ? `read a number from ${d.topic}` : "read a number from MQTT";
    case "variable.api":
      return `read a number from a URL every ${num(d.interval_s, 5)}s`;
    case "variable.board_input":
      return `read the ${d.port || "DI0"} switch${d.invert ? " (flipped)" : ""}`;
    case "variable.rs485":
      return `read FC ${num(d.fc, 3)} at ${num(d.address)} every ${num(d.interval_s, 5)}s`;
    case "state.watch":
      return `fire when ${d.watch === "filter" ? "the filter" : "the AI"} turns ${d.want || "on"}`;
    case "common.inject":
      return `send ${INJECT_WHAT[d.ptype || "timestamp"] || "a number"}`
        + (d.repeat === "interval" ? ` every ${num(d.every, 1)} ${d.every_unit || "seconds"}`
          : d.once ? " once at start" : " when pressed");
    case "common.debug":
      return "show the value live on the canvas";
    case "function.start":
      return `start "${d.fn || "?"}"${(d.params || []).length ? ` · ${d.params.length} inputs` : ""}`;
    case "function.end":
      return `give back from "${d.fn || "?"}"`;
    case "function.call":
      return d.fn ? `run the function "${d.fn}"` : "run a function (pick one)";
    case "logic.delay":
      return `wait ${num(d.delay_s, 5)}s, then pass it on`;
    case "logic.catch":
      return "fire when the code before it fails";
    case "logic.when":
      return `true while ${ruleWords({ op: d.mode, value: d.value, value2: d.value2 })}`
        + (num(d.hold_s) ? ` for ${d.hold_s}s` : "");
    case "branch.compare": case "branch.cases":
      return rules.length > 1 ? `${rules.length} tests · first true wins`
        : `test: ${ruleWords(rules[0]) || "a value"}${d.otherwise === false ? "" : " · else"}`;
    case "branch.range":
      return `sort into ${rules.length || 1} bands`;
    case "branch.match":
      return `a way per value · ${rules.length || 1} cases`;
    case "branch.multi":
      return `${rules.length || 1} tests joined with ${(d.join || "and").toUpperCase()}`;
    case "logic.switch":
      return CTYPE[d.ctype] || "decide what happens next";
    case "output.trigger": {
      const what = d.alert ? `raise a ${SEVERITY[d.severity] || "warning"} alert`
        : "mark the moment it turns true";
      return what + (d.draw ? " · paints boxes" : d.zone_color ? " · paints zone" : "");
    }
    case "output.alert":
      return `alert when ${ruleWords({ op: d.op, value: d.value })}`;
    case "output.dataset":
      return `${d.op === "update" || d.on_repeat === "update" ? "update a row in" : "add a row to"} `
        + `${d.name || d.dataset || "a table"}`
        + ({ every: ` every ${num(d.interval_s, 60)}s`, change: " on change",
             crossing: " per crossing" }[d.mode] || "");
    case "storage.query":
      return d.sql ? "run your SQL over the tables wired in" : "combine tables with SQL (none yet)";
    case "storage.csv":
      return `turn the rows into a CSV file${num(d.max_rows) ? ` · ${d.max_rows} rows` : ""}`;
    case "output.snapshot":
      return d.mode === "interval" ? `save a photo every ${num(d.interval_s, 5)}s while true`
        : "save a photo when it fires";
    case "output.record":
      return `record a clip · ${num(d.before_s)}s before, ${num(d.after_s, 5)}s after`;
    case "output.telegram": {
      // WHO hears it (Tok Aba, 2026-10-14): the bot's linked people, all or
      // picked, or one typed chat as before.
      if (d.to_mode === "all") return "send a Telegram message to all linked people";
      if (d.to_mode === "selected") {
        const n = (d.to_people || []).length;
        return `send a Telegram message to ${n} selected ${n === 1 ? "person" : "people"}`;
      }
      return d.chat_id ? `send a Telegram message to chat ${d.chat_id}`
        : "send a Telegram message";
    }
    case "output.email":
      return d.to ? `email ${d.to}` : "send an email";
    case "output.mqtt_publish":
      return `publish to ${d.topic || "an MQTT topic"}`;
    case "output.webhook":
      return `call a URL (${d.method || "POST"}) when it fires`;
    case "output.webhook_publish":
      return `post the value every ${num(d.interval_s, 5)}s`;
    case "output.modbus_write":
      return `write FC ${num(d.fc, 6)} at ${d.address === "" ? "?" : num(d.address)}`;
    case "output.io_out":
      return `${d.action === "off" ? "turn off" : d.action === "pulse" ? `pulse ${num(d.pulse_ms)} ms` : "turn on"} ${d.port || "DO0"}`;
    case "output.buzzer":
      return `beep for ${num(d.pulse_ms, 500)} ms`;
    case "output.io_pwm":
      return d.source === "fixed" ? `drive ${d.port || "L0"} at ${num(d.level, 128)}`
        : `drive ${d.port || "L0"} from the value`;
    case "output.zone_switch":
      return `${d.action === "disable" ? "turn off" : "turn on"} a zone when 1`;
    case "integration.command":
      return d.command ? `answer /${String(d.command).replace(/^\//, "")}` : "answer a Telegram command";
    case "integration.photos":
      return `send up to ${num(d.most, 10)} snapshots`;
    case "integration.watch":
      return `check every ${num(d.every_min, 15)} min · ${ruleWords({ op: d.op, value: d.value })}`;
    case "dashboard.graph":
    case "dashboard.chart":
      return ctx.chartName ? `show as a ${ctx.chartName}` : "show as a chart";
    case "dashboard.card": case "dashboard.counter": case "dashboard.gauge":
    case "dashboard.verdict": case "dashboard.events": case "dashboard.table":
      return ctx.shapeName ? `show as ${ctx.shapeName}` : "show on the dashboard";
    default:
      return "";
  }
}

/** The registry's own words, first sentence: tier 3. */
function described(spec) {
  const desc = String((spec && spec.description) || "").trim();
  if (!desc) return (spec && spec.label) || "";
  const first = desc.split(/(?<=[.!?])\s/)[0];
  return first.replace(/[.!?]$/, "");
}

/** {in, does, out, tier} for a node, or null for SUMMARY_SKIP.
 *  `ctx`: {zoneWired, zoneName, opLabel, chartName, shapeName}. */
export function nodeSummary(type, d = {}, spec = {}, ctx = {}) {
  if (SUMMARY_SKIP.includes(type)) return null;
  d = d || {};
  const [ins, out0] = ioOf(type, spec);
  // What it hands out follows its own type setting where it has one.
  const out = type === "common.inject" ? (INJECT_OUT[d.ptype || "timestamp"] || "number")
    : type === "variable.number" ? (VALUE_OUT[d.value_type] || "number") : out0;
  // An optional zone or line port reads as wired or not: "objects · zone"
  // only when a zone is on it, "objects · line" only on a line counter.
  const inKeys = ins.filter((k, i) => !(i > 0 && ((k === "zone" && ctx.zoneWired === false)
    || (k === "line" && type === "analytics.counter" && ctx.cmode !== "line"))));
  const hand = doesOf(type, d, ctx, spec);
  return {
    in: fit(inKeys.map((k) => TYPE_WORDS[k]).join(" · ")),
    does: fit(hand || described(spec) || type),
    out: fit(TYPE_WORDS[out]),
    tier: hand ? 2 : 3,
  };
}

/** The summary as the Properties panel shows it, with what only the canvas
 *  knows: whether a zone (or a line) is wired, the zone's name, and the names
 *  the inspector uses for the operation, the chart type and the card shape.
 *  `zones` is the system's zone list; `cmode` registry.counterMode's answer. */
export function summaryFor({ type, data, spec, zones = [], zoneWired = false, cmode = "" }) {
  const dd = data || {};
  spec = spec || {};
  return nodeSummary(type, dd, spec, {
    zoneWired, cmode,
    zoneName: ((zones || []).find((z) => z.id === dd.zone) || {}).name,
    opLabel: ((spec.ops || {})[dd.op] || {}).label || "",
    chartName: (chartType(chartTypeOf(dd.kind || "area", dd.x_type
      || (dd.bucket ? "time" : "column"))) || {}).name || "",
    shapeName: shapeLabel(dd.kind || { "dashboard.gauge": "gauge", "dashboard.verdict": "verdict",
                                       "dashboard.counter": "tile", "dashboard.table": "table" }[type]
                          || "tile"),
  });
}
