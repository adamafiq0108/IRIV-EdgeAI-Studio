// The ONE Counter's canvas half (frontend/src/workflow/registry.js): what it
// is counting, read off its wires, and the swap between analytics.counter and
// analytics.entry_exit as a line goes on or comes off.
//
// Tok Aba, 2026-10-13: "Counter" and "Line Counter" are one node; a Zone
// wired makes it a zone counter, a Line a line counter, nothing an object
// counter. The server half (and the golden compile of old workflows) is
// tests/smoke_counterunify.py.
//
//   node tests/check_counterunify.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".counterunify-test.cjs");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) { if (detail) console.log(`      ${detail}`); bad += 1; }
};

await build({
  entryPoints: [join(FRONT, "src/workflow/registry.js")],
  bundle: true, platform: "node", format: "cjs", outfile: OUT, logLevel: "error",
});
const R = req(OUT);
rmSync(OUT, { force: true });

// The two specs as the server serves them -- only what the helpers read.
const registry = {
  "analytics.counter": {
    data: { fnId: "", name: "Counter", mode: "realtime", OID: false,
            enter_after_s: 0, leave_after_s: 0 },
    modes: { realtime: { metrics: ["count"] },
             distinct: { metrics: ["total", "new", "today", "week", "month"] } },
  },
  "analytics.entry_exit": {
    data: { fnId: "", name: "Line counter", mode: "in", OID: false,
            distinct: false, momentary: true, totals: false },
    metrics: ["in", "out", "net", "cross"],
    modes: { in: { metrics: ["in", "cross"] }, out: { metrics: ["out", "cross"] },
             both: { metrics: ["in", "out", "net", "cross"] } },
  },
};
const zone = { id: "z", type: "zone.ref", data: { zone: "bay" } };
const line = { id: "l", type: "zone.ref", data: { zone: "door" } };
const cnt = { id: "c", type: R.COUNTER,
              data: { fnId: "counter_ab12", name: "Counter", mode: "distinct",
                      OID: true } };
const e = (id, source, target, targetHandle = null, sourceHandle = null) => (
  { id, source, target, targetHandle, sourceHandle });

// -- what it is counting ----------------------------------------------------------
check("nothing wired: object counter",
      R.counterMode(cnt, [e("1", "f", "c", "in")]) === "object");
check("a Zone wired: zone counter",
      R.counterMode(cnt, [e("1", "z", "c", "zone")]) === "zone");
check("a Line on the Line port: line counter",
      R.counterMode(cnt, [e("1", "l", "c", R.COUNTER_LINE_PORT)]) === "line");
check("an old line counter is a line counter, wired or not",
      R.counterMode({ id: "x", type: R.LINE_COUNTER }, []) === "line");
check("any other node has no counter mode",
      R.counterMode({ id: "x", type: "analytics.dwell" }, []) === "");
check("the subtitles are plain words",
      R.COUNTER_MODE_LABEL.zone === "zone counter"
      && R.COUNTER_MODE_LABEL.line === "line counter"
      && R.COUNTER_MODE_LABEL.object === "object counter");

// -- Counter -> line counter -------------------------------------------------------
const wires = [
  e("in", "f", "c", "in"),
  e("ln", "l", "c", R.COUNTER_LINE_PORT),
  e("card", "c", "card", null, null),           // the count, id-less grab
  e("named", "c", "ds", "c_n", "out"),          // the count, named
  e("oid", "c", "ds", "c_who", "OID"),
  e("new", "c", "trig", null, "new"),           // only a zone counter has it
  e("other", "f", "g", null, null),             // not the Counter's at all
];
const toLine = R.counterToLine(registry, cnt, wires);
const byId = Object.fromEntries(toLine.edges.map((x) => [x.id, x]));
check("becomes the line counter type, same id, same fnId, same name",
      toLine.node.type === R.LINE_COUNTER && toLine.node.id === "c"
      && toLine.node.data.fnId === "counter_ab12"
      && toLine.node.data.name === "Counter");
check("leads with the line counter's default direction, live by default",
      toLine.node.data.mode === "in" && toLine.node.data.momentary === true
      && toLine.node.data.distinct === false);
check("keeps its zone-counter choice for the way back",
      toLine.node.data.count_mode === "distinct");
check("the line moves onto the line counter's own port (zone)",
      byId.ln.targetHandle === "zone");
check("the count's wires land on the lead tally",
      byId.card.sourceHandle === "in" && byId.named.sourceHandle === "in");
check("the OID wire stays the OID", byId.oid.sourceHandle === "OID");
check("a port only a zone counter has comes off, and is counted",
      !byId.new && toLine.dropped === 1, `dropped ${toLine.dropped}`);
check("wires that are not the Counter's are untouched",
      byId.other === wires[6] && byId.in === wires[0]);
check("the inputs are not mutated", wires[1].targetHandle === R.COUNTER_LINE_PORT
      && cnt.type === R.COUNTER && cnt.data.mode === "distinct");

const back = R.counterToLine(registry, { ...cnt, data: { ...cnt.data, line_mode: "out" } },
                             [e("ln", "l", "c", R.COUNTER_LINE_PORT),
                              e("card", "c", "card")]);
check("a direction kept from before comes back, and the lead follows it",
      back.node.data.mode === "out"
      && back.edges.find((x) => x.id === "card").sourceHandle === "out");

// -- line counter -> Counter -------------------------------------------------------
const lc = { id: "c", type: R.LINE_COUNTER,
             data: { fnId: "lc", name: "Door", mode: "both", OID: true,
                     momentary: true, totals: true, count_mode: "realtime" } };
const lw = [e("a", "c", "card", null, "in"), e("b", "c", "card2", null, "out"),
            e("t", "c", "card3", null, "total_in"), e("o", "c", "ds", "c_who", "OID"),
            e("f", "f", "c", "in")];
const toC = R.lineToCounter(registry, lc, lw);
const cb = Object.fromEntries(toC.edges.map((x) => [x.id, x]));
check("becomes a Counter again, same id and fnId",
      toC.node.type === R.COUNTER && toC.node.data.fnId === "lc");
check("its zone-counter choice comes back, its direction is kept",
      toC.node.data.mode === "realtime" && toC.node.data.line_mode === "both"
      && !("count_mode" in toC.node.data));
check("the lead tally's wire goes to the count",
      cb.a.sourceHandle === "out");
check("the OID stays; the extra tallies come off and are counted",
      cb.o.sourceHandle === "OID" && !cb.b && !cb.t && toC.dropped === 2,
      `dropped ${toC.dropped}`);
check("the detections wire is untouched", cb.f === lw[4]);
const round = R.counterToLine(registry, toC.node, toC.edges);
check("off and on again gives the same line counter back",
      round.node.type === R.LINE_COUNTER && round.node.data.mode === "both"
      && round.node.data.totals === true && round.node.data.count_mode === "realtime");

// -- when the canvas swaps ---------------------------------------------------------
const nodes = [zone, line, cnt, { id: "f", type: "filter.class", data: {} }];
const before = [e("in", "f", "c", "in")];
let sw = R.counterSwaps(nodes, before, [...before, e("ln", "l", "c", R.COUNTER_LINE_PORT)]);
check("a new wire on the Line port swaps the Counter to a line counter",
      sw.toLine.join() === "c" && !sw.toCounter.length);
sw = R.counterSwaps(nodes, before, [...before, e("zn", "z", "c", "zone")]);
check("a Zone wire does not", !sw.toLine.length && !sw.toCounter.length);
sw = R.counterSwaps(nodes, [...before, e("ln", "l", "c", R.COUNTER_LINE_PORT)],
                    [...before, e("ln", "l", "c", R.COUNTER_LINE_PORT)]);
check("an unchanged graph swaps nothing", !sw.toLine.length && !sw.toCounter.length);

const lnodes = [line, { ...lc }, { id: "f", type: "filter.class", data: {} }];
const lined = [e("in", "f", "c", "in"), e("ln", "l", "c", "zone")];
sw = R.counterSwaps(lnodes, lined, [lined[0]]);
check("taking the line off a line counter swaps it back",
      sw.toCounter.join() === "c" && !sw.toLine.length);
sw = R.counterSwaps(lnodes.filter((n) => n.id !== "l"), lined, [lined[0]], lnodes);
check("...and so does deleting the line's Zone node",
      sw.toCounter.join() === "c");
sw = R.counterSwaps(lnodes, lined, [lined[1]]);
check("taking the DETECTIONS wire off does not", !sw.toCounter.length);
sw = R.counterSwaps(lnodes, [lined[0]], [lined[0]]);
check("an old line counter saved with no line is left alone",
      !sw.toCounter.length && !sw.toLine.length);
sw = R.counterSwaps(lnodes.filter((n) => n.id !== "c"), lined, []);
check("deleting the counter itself swaps nothing", !sw.toCounter.length);
// Undo restores a snapshot: the line wire is gone AND the node is a Counter
// again in the same step, so there is no line counter left to swap.
sw = R.counterSwaps([line, cnt], lined, [lined[0]]);
check("undo of a swap is not itself a swap", !sw.toCounter.length && !sw.toLine.length);

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
