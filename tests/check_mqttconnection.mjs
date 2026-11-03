// The MQTT Connection row and editor, and the Broker page's "Reachable at".
//
// A beta tester (2026-10-02): "MQTT there no broker IP? add user and password
// in mqtt, add topic user can change the topic themselves". Pinned by running
// the editor's save body (the password only travels when typed) and source
// checks that both MQTT nodes carry the Connection row and an editable topic,
// that no password is ever filled in from the server, and that the Broker
// page says where other devices reach it.
//
//   node tests/check_mqttconnection.mjs
import { createRequire } from "node:module";
import { readText } from "./_text.mjs";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".mqttconn-test.cjs");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) { if (detail) console.log(`      ${detail}`); bad += 1; }
};

const src = (p) => join(FRONT, "src", p).replace(/\\/g, "/");
await build({
  stdin: {
    contents: `export { saveBody, DEFAULTS } from "${src("components/MqttConnectionEditor.jsx")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, platform: "node", format: "cjs", outfile: OUT, logLevel: "error",
  jsx: "automatic", external: ["react", "react/jsx-runtime", "react-dom", "lucide-react"],
  plugins: [{
    name: "no-browser-modules",
    setup(b) {
      b.onResolve({ filter: /api\/client\.js$|ui\/Toast\.jsx$/ },
                  (a) => ({ path: a.path, namespace: "stub" }));
      b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({
        contents: "export const api = {}; export const useToast = () => ({});",
        loader: "js" }));
    },
  }],
});
const m = req(OUT);
rmSync(OUT, { force: true });

check("a new connection is this box's broker", m.DEFAULTS.host === "127.0.0.1"
      && m.DEFAULTS.port === 1883 && m.DEFAULTS.keepalive_s === 60
      && m.DEFAULTS.reconnect_s === 1 && m.DEFAULTS.tls === false);
const kept = m.saveBody({ id: "mq_1", name: "B", password: "", password_set: true,
                          label: "B", this_box: false }, false);
check("a blank password is not sent (the stored one is kept)",
      !("password" in kept) && !("password_set" in kept) && !("label" in kept)
      && kept.id === "mq_1", JSON.stringify(kept));
check("a typed password is sent", m.saveBody({ password: "x1" }, true).password === "x1");
check("adding sends no id", !("id" in m.saveBody({ id: "mq_1" }, true)));

const read = (p) => readText(join(FRONT, "src", p));
const ed = read("components/MqttConnectionEditor.jsx");
check("the editor never fills the password from the server",
      ed.includes('setForm(found ? { ...DEFAULTS, ...found, password: "" }'));
check("the editor has the tester's fields",
      ["IP address", "Port", "User name", "Password", "Client ID", "Keep alive (s)",
       "Reconnect time (s)"].every((f) => ed.includes(`label="${f}"`)));
check("...and SSL/TLS shown but not offered", ed.includes("SSL/TLS - coming later")
      && /checked=\{false\} disabled readOnly/.test(ed));

const wf = read("pages/Workflow.jsx");
const sub = wf.slice(wf.indexOf('node.type === "variable.mqtt" && ('),
                     wf.indexOf('node.type === "variable.api" && ('));
check("MQTT Subscribe has the Connection row and an editable topic",
      sub.includes("<MqttConnectionRow") && /label="Topic"/.test(sub)
      && sub.includes('onChange={set("topic")}'));
const pub = wf.slice(wf.indexOf('node.type === "output.mqtt_publish" && (<>'));
check("MQTT Publish has the Connection row and an editable topic",
      pub.slice(0, 2000).includes("<MqttConnectionRow")
      && pub.slice(0, 3000).includes('onChange={set("topic")}'));
check("the editor takes over the inspector, and a connection added is picked",
      wf.includes("<MqttConnectionEditor connId={mqttEdit}")
      && wf.includes("onChange({ connection: c.id })"));
check("a node saved before Connections still shows its own fields",
      sub.includes('"connection" in d ? (') && sub.includes('onChange={set("host")}'));
check("nothing picked is a hint, not an error",
      wf.includes("None picked: this box's own broker (127.0.0.1:1883).")
      && !/<MqttConnectionRow[^>]*required=\{true\}/.test(wf));

const bp = read("components/BrokerPanel.jsx");
check("the Broker page says where other devices reach it, with Copy",
      bp.includes("Connection Addresses") && bp.includes("navigator.clipboard.writeText")
      && bp.includes("<Reachable s={s} addresses={data.addresses || []} />"));
check("...and the login fields are up front and always on show",
      // Section names from the 2026-10-11 copy pass.
      bp.indexOf('title="Login Credentials"') > 0
      && bp.indexOf('title="Login Credentials"') < bp.indexOf('title="Connection Settings"')
      && bp.includes('disabled={!form.secure} data-broker-field="username"')
      && !bp.includes("{form.secure && (\n            <>\n              <label"));

if (bad) { console.log(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
