// The MQTT nodes' test buttons, and what a Debug node shows of MQTT.
//
// Asked for on 2026-09-29 (Tok Aba): test buttons on MQTT Publish and MQTT
// Subscribe, and MQTT data visible through Debug nodes. Pinned by running the
// Debug history (probeHistory.js) and the publish result's rows, plus source
// checks that the buttons and the history are mounted where they belong.
//
//   node tests/check_mqtttest.mjs
import { createRequire } from "node:module";
import { readText } from "./_text.mjs";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".mqtttest-test.cjs");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) { if (detail) console.log(`      ${detail}`); bad += 1; }
};

const src = (p) => join(FRONT, "src", p).replace(/\\/g, "/");
await build({
  stdin: {
    contents: `export * from "${src("workflow/probeHistory.js")}";
               export { publishSteps, sendLabel } from "${src("workflow/MqttTest.jsx")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, platform: "node", format: "cjs", outfile: OUT, logLevel: "error",
  jsx: "automatic", external: ["react", "react/jsx-runtime", "react-dom"],
  // The API client reads window.location as it loads; nothing tested here
  // calls it, so it is swapped for an empty stand-in.
  plugins: [{
    name: "no-api-client",
    setup(b) {
      b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api", namespace: "stub" }));
      b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({
        contents: "export const api = {};", loader: "js" }));
    },
  }],
});
const h = req(OUT);
rmSync(OUT, { force: true });

// -- a Debug node on an MQTT Subscribe ---------------------------------------
const spec = { kind: "value", key: "fn_plc_value", label: "PLC parts" };
let vals = { fn_plc_value: 3, fn_plc_payload: '{"count":3}', fn_plc_topic: "plc/l1/parts",
             fn_plc_at: 1000 };
const e1 = h.entryFor(spec, vals);
check("a Subscribe's reading carries its payload and topic",
      e1.value === 3 && e1.payload === '{"count":3}' && e1.topic === "plc/l1/parts"
      && e1.t === 1000000, JSON.stringify(e1));
check("...and is added once", h.record("dbg1", e1) && !h.record("dbg1", h.entryFor(spec, vals)));
vals = { ...vals, fn_plc_at: 1001 };
check("the same value in a NEW message is a new line",
      h.record("dbg1", h.entryFor(spec, vals)) && h.historyOf("dbg1").length === 2);
check("nothing published yet is no line", h.entryFor(spec, {}) === null);

// -- a Debug node on anything else --------------------------------------------
const plain = { kind: "value", key: "fn_count_total", label: "Count" };
h.record("dbg2", h.entryFor(plain, { fn_count_total: 1 }));
h.record("dbg2", h.entryFor(plain, { fn_count_total: 1 }));
h.record("dbg2", h.entryFor(plain, { fn_count_total: 2 }));
check("a plain value logs each CHANGE", h.historyOf("dbg2").map((x) => x.value).join() === "1,2");
check("...with no payload row", h.historyOf("dbg2")[0].payload === undefined);

// -- a Debug node on an MQTT Publish ------------------------------------------
const out = { kind: "output", id: "wf_pub1", label: "Publish" };
const pv = { out_wf_pub1_at: 50, out_wf_pub1_ok: 1, out_wf_pub1_fired: 3,
             out_wf_pub1_note: "sent 42 to line1/count", out_wf_pub1_error: "" };
const e3 = h.entryFor(out, pv);
check("a Publish's line says what went out and where",
      e3.value === "sent" && e3.note === "sent 42 to line1/count" && e3.ok === true);
const e4 = h.entryFor(out, { ...pv, out_wf_pub1_ok: 0, out_wf_pub1_at: 51,
                             out_wf_pub1_error: "connection refused" });
check("...and a failure says why", e4.value === "FAILED" && e4.error === "connection refused");
check("a publish that never happened is no line",
      h.entryFor(out, { out_wf_pub1_at: 0 }) === null);

for (let i = 0; i < h.MAX + 10; i++) h.record("dbg3", { sig: `s${i}`, t: i, value: i });
check("history keeps the last MAX", h.historyOf("dbg3").length === h.MAX
      && h.historyOf("dbg3")[0].value === 10);
let heard = "";
const off = h.subscribe((id) => { heard = id; });
h.clearHistory("dbg3");
off();
check("Clear empties it and tells the list", h.historyOf("dbg3").length === 0 && heard === "dbg3");

// -- the publish result's rows ---------------------------------------------------
const rows = h.publishSteps({ connected: true, published: true, received: true, rtt_ms: 4.2 });
check("a good test reads yes, yes, yes with the round trip",
      rows.every(([, ok]) => ok) && rows[2][0].includes("4.2 ms"), JSON.stringify(rows));
const noEcho = h.publishSteps({ connected: true, published: true, received: false });
check("...and no echo is the last row failing", noEcho[2][1] === false && noEcho[1][1] === true);

// -- a test goes to the real topic (Yaya, review of 18c4480) ------------------
check("no warning: one click sends", h.sendLabel({ warning: "" }) === "Test publish");
check("a warning: the first click only arms it",
      h.sendLabel({ warning: "x", armed: false }) === "Test publish..."
      && h.sendLabel({ warning: "x", armed: true }) === "Send anyway");
check("busy wins", h.sendLabel({ busy: true, warning: "x", armed: true }) === "Testing...");

// -- mounted where they belong -------------------------------------------------
const read = (p) => readText(join(FRONT, "src", p));
const wf = read("pages/Workflow.jsx");
const sub = wf.slice(wf.indexOf('node.type === "variable.mqtt" && ('),
                     wf.indexOf('node.type === "variable.api" && ('));
check("Listen sits in the MQTT Subscribe inspector", sub.includes("<ListenTest node={node}"));
const pub = wf.slice(wf.indexOf('node.type === "output.mqtt_publish" && (<>'));
check("Test publish sits under the Publish topic",
      /label="Topic"[^\n]*\n[^\n]*\n\s*<PublishTest node=\{node\}/.test(pub));
// No clock of its own (Tok Aba 2026-10-08): the Trigger or Inject wired in
// says when, so the panel has no Every and no payload, says what to wire
// while nothing is, and explains the Trigger's holds without an em dash.
const pubPanel = pub.slice(0, pub.indexOf('node.type === "output.email"'));
check("the Publish inspector has no Every and no payload field",
      !pubPanel.includes('label="Every"') && !pubPanel.includes('set("interval_s")')
      && !pubPanel.includes('set("payload")'));
check("...says what to wire while nothing is wired",
      pubPanel.includes("Nothing is wired in, so this publishes nothing.")
      && pubPanel.includes("data-publish-unwired"));
check("...and how the Trigger in front decides, with no em dash",
      pubPanel.includes("Interval sends it again")
      && pubPanel.includes("every N seconds while the trigger holds")
      && pubPanel.includes("For N seconds sends it")
      && pubPanel.includes("An Inject publishes each time it fires")
      && !pubPanel.includes("\u2014"));
const dbg = wf.slice(wf.indexOf('node.type === "common.debug" && ('));
check("a Debug node's inspector lists its messages",
      dbg.slice(0, 1200).includes("<DebugHistory nodeId={node.id} />"));
check("both tests need edit rights", /<ListenTest node=\{node\} disabled=\{!editable\}/.test(wf)
      && /<PublishTest node=\{node\} disabled=\{!editable\}/.test(wf));
const tests = read("workflow/MqttTest.jsx");
check("the tests name a node, never a broker",
      tests.includes('node: node.id') && !/host:|password/.test(
        tests.replace(/\/\/.*$/gm, "")));
check("a listen is stopped when its panel closes",
      tests.includes('"/api/workflow/mqtt-test/listen/stop"'));
check("an empty listen says how to send something", tests.includes("snap.hint"));
check("the payload is shown and editable before sending",
      tests.includes('aria-label="Test payload"') && tests.includes("dry: true")
      && tests.includes("{ node: node.id, payload, confirm: armed }"));
check("the warning is shown when this box's own Subscribe would hear it",
      tests.includes("data-mqtt-warning") && tests.includes("plan?.warning"));
check("a Subscribe saved since the dry run still gets asked (the server's 409)",
      tests.includes("e.status === 409"));
const nodes = read("workflow/nodes.jsx");
check("the Debug face shows the MQTT topic and payload", nodes.includes("data-debug-mqtt"));
check("...and what a publish sent", nodes.includes('outStat(values, spec.id, "note")'));
check("...and records every reading", nodes.includes("recordProbe(nodeId, entryFor(spec, values))"));

if (bad) { console.log(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
