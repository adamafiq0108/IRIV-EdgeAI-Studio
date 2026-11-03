// The status dots on canvas nodes, as plain functions (nodeStatus.js):
//
//   * the MQTT dot (client UAT 2026-10-04, item 1): green when the broker the
//     node talks to is online, red when it is not, grey while nobody knows -
//     this box's broker from the broker page's status, a named Connection
//     from the app's mqtt_<id>_ok / _error values
//
//   node tests/check_nodestatus.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".nodestatus-test.cjs");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) { if (detail) console.log(`      ${detail}`); bad += 1; }
};

await build({
  entryPoints: [join(FRONT, "src/workflow/nodeStatus.js")],
  bundle: true, platform: "node", format: "cjs", outfile: OUT, logLevel: "error",
});
const { mqttDot, injectDot, PRESS_WAIT_MS } = req(OUT);
rmSync(OUT, { force: true });

// -- the Inject dot -------------------------------------------------------------
// (The rendered circle is tests/check_inject.mjs; this is the rule itself.)
const inj = (values, extra = {}) => injectDot({ nodeId: "i1", data: { fnId: "kick" },
                                                values, appRunning: true, edges: [],
                                                ...extra });
check("no function yet, no circle", injectDot({ nodeId: "i1", data: {}, values: {} }) === null);
check("grey before the first injection", inj({}).ok === null);
check("green once the box took one", inj({ fn_kick_runs: 1, fn_kick_ok: 1 }).ok === true);
check("red when the box dropped one", inj({ fn_kick_runs: 1, fn_kick_ok: 0 }).ok === false);
check("a press waits up to PRESS_WAIT_MS for the run counter",
      inj({ fn_kick_runs: 1, fn_kick_ok: 1 },
          { pending: { runs: 1, at: 1000 }, now: 1000 + PRESS_WAIT_MS - 1 }).ok === null
      && inj({ fn_kick_runs: 1, fn_kick_ok: 1 },
             { pending: { runs: 1, at: 1000 }, now: 1000 + PRESS_WAIT_MS + 1 }).ok === false);
check("an output after it that failed since the injection fails it",
      inj({ fn_kick_runs: 1, fn_kick_ok: 1, fn_kick_last_at: 50,
            out_wf_o1_at: 51, out_wf_o1_ok: 0, out_wf_o1_error: "boom" },
          { edges: [{ source: "i1", target: "o1" }] }).title.includes("boom"));
check("...but one that failed BEFORE it does not",
      inj({ fn_kick_runs: 1, fn_kick_ok: 1, fn_kick_last_at: 50,
            out_wf_o1_at: 10, out_wf_o1_ok: 0 },
          { edges: [{ source: "i1", target: "o1" }] }).ok === true);

// -- the MQTT dot ---------------------------------------------------------------
check("a node that is not MQTT wears no dot",
      mqttDot({ type: "output.io_out", data: {}, values: {}, appRunning: true,
                brokerUp: true }) === null);
for (const type of ["variable.mqtt", "output.mqtt_publish", "output.mqtt"]) {
  const d = mqttDot({ type, data: { connection: "" }, values: {}, appRunning: false,
                      brokerUp: true });
  check(`${type} on this box's broker is green while the broker runs, app or no app`,
        d && d.ok === true && d.title.includes("online"), JSON.stringify(d));
}
let d = mqttDot({ type: "output.mqtt_publish", data: {}, values: {}, appRunning: true,
                  brokerUp: false });
check("...red when this box's broker is not running",
      d.ok === false && d.title.includes("not running"), JSON.stringify(d));
d = mqttDot({ type: "output.mqtt_publish", data: {}, values: {}, appRunning: true,
              brokerUp: null });
check("...grey before the broker page has answered", d.ok === null, JSON.stringify(d));

const conn = { connection: "mq_0000aaaa" };
d = mqttDot({ type: "output.mqtt_publish", data: conn, appRunning: true,
              values: { mqtt_mq_0000aaaa_ok: 1, mqtt_mq_0000aaaa_error: "" } });
check("a named Connection the app has connected is green",
      d.ok === true && d.title.includes("connected"), JSON.stringify(d));
d = mqttDot({ type: "variable.mqtt", data: conn, appRunning: true,
              values: { mqtt_mq_0000aaaa_ok: 0,
                        mqtt_mq_0000aaaa_error: "the broker refused the login" } });
check("...red with the app's reason when it is not",
      d.ok === false && d.title.includes("refused the login"), JSON.stringify(d));
d = mqttDot({ type: "variable.mqtt", data: conn, appRunning: true,
              values: { mqtt_mq_0000aaaa_ok: 0, mqtt_mq_0000aaaa_error: "" } });
check("...'not connected' when the app gives no reason",
      d.ok === false && d.title.includes("not connected"), JSON.stringify(d));
d = mqttDot({ type: "variable.mqtt", data: conn, appRunning: false,
              values: { mqtt_mq_0000aaaa_ok: 1 } });
check("...red while the app is down, whatever a stale value says",
      d.ok === false && d.title.includes("app is not running"), JSON.stringify(d));
d = mqttDot({ type: "variable.mqtt", data: conn, appRunning: true, values: {} });
check("...grey while the app has not reported that Connection yet",
      d.ok === null, JSON.stringify(d));

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall node status checks passed");
