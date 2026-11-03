// The Workflow page's MQTT broker pill, and the Settings tab behind it.
//
// Asked for on 2026-09-29: an MQTT node reading nothing and a broker that is
// down look identical on the canvas, so the page says which. Pinned by
// running the pill's words for every broker state, plus source checks that
// the pill is mounted, the tab exists, and the page says what "no login"
// means.
//
//   node tests/check_brokerpill.mjs
import { createRequire } from "node:module";
import { readText } from "./_text.mjs";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".brokerpill-test.cjs");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) { if (detail) console.log(`      ${detail}`); bad += 1; }
};

await build({
  entryPoints: [join(FRONT, "src/components/brokerPill.js")],
  bundle: true, platform: "node", format: "cjs", outfile: OUT, logLevel: "error",
});
const { pillFor } = req(OUT);
rmSync(OUT, { force: true });

const local = pillFor({ state: "running", port: 1883, mode: "local" });
check("running, local only", local.text === "MQTT broker: running on :1883 (local only)"
      && local.tone === "ok", JSON.stringify(local));
const sec = pillFor({ state: "running", port: 1884, mode: "secured" });
check("running, network, secured",
      sec.text === "MQTT broker: running on :1884 (network, secured)" && sec.tone === "ok",
      sec.text);
const open = pillFor({ state: "running", port: 1883, mode: "open" });
check("running, network, open - no password, as a warning",
      open.text === "MQTT broker: running on :1883 (network, open - no password)"
      && open.tone === "warn", JSON.stringify(open));
check("running, local only and secured",
      pillFor({ state: "running", port: 1883, mode: "local_secured" }).text
      === "MQTT broker: running on :1883 (local only, secured)");
check("not running", pillFor({ state: "down", port: 1883 }).text === "MQTT broker: not running");
check("not installed", pillFor({ state: "not_installed", port: 1883 }).text
      === "MQTT broker: not installed");
check("someone else's broker is named",
      pillFor({ state: "other", port: 1883, owner: "node-red" }).text
      === "MQTT broker: port 1883 is used by node-red");
check("no answer yet is not a verdict", pillFor(null).tone === "muted");

const read = (...p) => readText(join(FRONT, "src", ...p));
const wf = read("pages", "Workflow.jsx");
check("the Workflow page mounts the pill", wf.includes("<BrokerStatus nodes={nodes} />")
      && wf.includes('import BrokerStatus from "../components/BrokerStatus.jsx"'));
const hub = read("components", "SettingsHub.jsx");
check("Settings has an MQTT Broker tab", /id: "mqtt".*BrokerPanel/.test(hub));
const panel = read("components", "BrokerPanel.jsx");
check("the page says what local only means",
      panel.includes("Only applications on this device can connect. To let other"));
check("Who can connect is its own choice, both ways",
      panel.includes("Who Can Connect") && panel.includes("This Device Only")
      && panel.includes("Devices on the Network")
      && /value="local"/.test(panel) && /value="network"/.test(panel));
// Customer copy pass (2026-10-11): the spec's heading and closing sentences,
// the "every message / any topic" facts kept (there are no topic ACLs).
const openWarning = "Devices on this network can connect without a password, read "
  + "every message and publish to any topic, including ones machines act on. Only "
  + "use this configuration on a trusted, isolated network. Enable authentication "
  + "for normal deployments.";
check("network with no login carries the warning, word for word",
      panel.replace(/\s+/g, " ").includes(openWarning)
      && /form\.access === "network" && !form\.secure/.test(panel));
check("...and a login still carries the no-TLS one",
      panel.replace(/\s+/g, " ").includes("Without TLS, the username, password and every "
        + "message travel unencrypted on the network."));
check("an old saved broker keeps behaving as it did (login = network, none = local)",
      /access: st\.managed && st\.access === "network" \? "network" : "local"/.test(panel));
check("the password is never pre-filled", /password: "",/.test(panel)
      && !/password: se\.|settings\?\.password\b/.test(panel));
check("TLS: no dead checkbox for customers, a plain status for developers",
      !/disabled checked=\{false\}/.test(panel)
      && /\{isDev && \(\s*<p[^>]*\s+[^>]*data-broker-tls/.test(panel)
      && panel.includes("Not available yet"));
const pill = read("components", "BrokerStatus.jsx");
check("only a manager is offered the settings", pill.includes("can_manage"));
check("the pill shows on every workflow, MQTT nodes or not",
      /export default function BrokerStatus\(\{ nodes \}\) \{\s*return <Pill/.test(pill)
      && !pill.includes("hasMqttNode"));

if (bad) { console.log(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
