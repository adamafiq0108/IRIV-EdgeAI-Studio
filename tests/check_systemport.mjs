// Edit system (the hub card's pencil) and the create wizard's port choice.
//
// Tok Aba (2026-10-06): "Rename system" became "Edit system": the Name field
// exactly as before, plus the system's PORT -- the current one by default, a
// custom one typed, or Auto (the next free). The wizard's name step offers
// Automatic (next free) or a Custom port, and the Review step says which.
// Pinned by rendering both against a shimmed api:
//   1. the dialog is titled Edit system; the Name field, its value and its
//      hint are the rename's; the port field shows the current port
//   2. "Suggested: <next free>" is shown and Auto fills it in; a usable new
//      port says what Save does ("Saving moves asri_test to port 5113 and
//      restarts it; anything that points at 192.168.1.21:5112 must be
//      changed.")
//   3. a refused port shows the server's words and Save is disabled
//   4. Save with the port left alone sends the name only (the old rename);
//      Save with a new port sends both
//   5. a unit-managed system: port read-only, the reason shown, no Auto
//   6. wizard: Automatic is the default and shows the suggestion; Custom
//      checks the typed port (a refusal keeps Next off); Review shows
//      "custom, 5120" / "automatic (next free, 5113 now)"; Create sends the
//      custom port and no port for automatic
//   7. no em dash anywhere on either
//   8. THE HUB DECIDES the address (Tok Aba, 2026-10-07): with the Hub on
//      the proxy (GET /api/hub/port, the Software port card's) both say
//      "Uses the proxy, like the Hub: http://<box>/s/<slug>/", the port is
//      an "Internal port" behind Advanced, still editable, and the stream
//      line names MJPEG and UDP 8189; with the Hub on ports, the port field
//      as before plus a note whose link opens Settings > Network. Neither
//      offers a switch, and neither ever posts to /api/hub/port
//
//   node tests/check_systemport.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".systemport-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "localStorage", "DocumentFragment"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// GET answers globalThis.__get(url); PATCH/POST are recorded.
const shimApi = {
  name: "shim-api",
  setup(b) {
    // Both spellings: the components' "../api/client.js" and hooks.js's own
    // "./client.js" (useApi would otherwise reach the real fetch).
    b.onResolve({ filter: /client\.js$/ }, (a) => (
      a.path.endsWith("api/client.js")
      || (a.path === "./client.js" && /[\\/]src[\\/]api$/.test(a.resolveDir))
        ? { path: "api-shim", namespace: "shim" } : undefined));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__sent = [];
        export const api = {
          get(url) { return Promise.resolve(globalThis.__get(url)); },
          patch(url, body) { globalThis.__sent.push(["PATCH", url, body]);
            return Promise.resolve({ ok: true, slug: "asri_test", name: body.name || "Asri",
                                     ...(body.port ? { port: body.port, moved: true, restarted: true } : {}) }); },
          post(url, body) { globalThis.__sent.push(["POST", url, body]);
            return Promise.resolve({ ok: true, name: body.name, started: true, url: "http://x", warnings: [] }); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state\/app\.jsx$/ }, () => ({ path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export function useApp() { return { user: { is_admin: true } }; }`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi, shimApp],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import EditSystemDialog from "${p("src/components/EditSystemDialog.jsx")}";
      import CreateSystemWizard from "${p("src/components/CreateSystemWizard.jsx")}";
      export { act };
      export function mountEdit(el, card) {
        const root = createRoot(el);
        act(() => { root.render(h(EditSystemDialog, { card, onOpenChange: () => {} })); });
        return { unmount: () => act(() => root.unmount()) };
      }
      export function mountWizard(el) {
        const root = createRoot(el);
        // Template first (2026-10-22): the card chose it, the wizard opens
        // on the Name step.
        act(() => { root.render(h(CreateSystemWizard, { template: "people" })); });
        return { unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { act } = m;

const wait = async (ms) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await wait(0);
};
const type = async (el, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
};
const button = (text) => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const submit = async () => {
  const form = document.querySelector("form");
  await act(async () => {
    form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  });
  await wait(0);
};

const CHECKS = {
  "5113": { usable: true, detail: "5113 is free" },
  "5120": { usable: true, detail: "5120 is free" },
  "1883": { usable: false, detail: "1883 is used by the MQTT broker - choose another" },
};
let portFacts = { ok: true, slug: "asri_test", port: 5112, stream_port: 5212,
                  suggested: 5113, editable: true, reason: "" };
let hubPort = { ok: true, running: 5100 };          // no access block: as before
globalThis.__get = (url) => {
  if (url === "/api/hub/port") return hubPort;
  if (url.startsWith("/api/hub/ports/check")) {
    const q = new URLSearchParams(url.split("?")[1] || "");
    const port = q.get("port");
    return { ok: true, suggested: 5113, ...(port ? { port: Number(port), ...(CHECKS[port] || { usable: true, detail: `${port} is free` }) } : {}) };
  }
  if (url.endsWith("/port")) return portFacts;
  if (url === "/api/hub/templates") {
    return { ok: true, templates: [{ key: "people", label: "People counting", hint: "" }],
             saved: [], updates: { token: true } };
  }
  return { ok: true };
};

const CARD = { slug: "asri_test", name: "Asri", url: "http://192.168.1.21:5112" };

// ---- Edit system ----------------------------------------------------------
let host = document.createElement("div");
document.body.appendChild(host);
let ui = m.mountEdit(host, CARD);
await wait(0);
const body = () => document.body.textContent;
check("titled Edit system", body().includes("Edit system") && !body().includes("Rename system"), body());
const nameInput = document.getElementById("es-name");
check("the Name field is the rename's: value and hint",
      nameInput?.value === "Asri"
      && body().includes("Up to 60 characters. Leave blank to restore the name the system package declares."));
const portInput = document.getElementById("es-port");
check("the port field shows the current port", portInput?.value === "5112", portInput?.value);
check("Suggested: next free is shown", body().includes("Suggested: 5113"), body());
check("...and the stream port that stays", body().includes("keeps port 5212"), body());

globalThis.__sent.length = 0;
await submit();
check("Save with the port left alone sends the name only",
      globalThis.__sent.length === 1 && JSON.stringify(globalThis.__sent[0][2]) === '{"name":"Asri"}',
      JSON.stringify(globalThis.__sent));

ui.unmount();
ui = m.mountEdit(host, CARD);
await wait(50);      // the port block waits for the hub's answer (no flash of the wrong one)
await click(button("Auto"));
await wait(400);
check("Auto fills in the next free port", document.getElementById("es-port").value === "5113");
const move = document.querySelector("[data-port-move]")?.textContent || "";
check("the move is said before Save",
      move === "Saving moves asri_test to port 5113 and restarts it; anything that points at 192.168.1.21:5112 must be changed.",
      move);
await type(document.getElementById("es-port"), "1883");
await wait(400);
const verdict = document.querySelector("[data-port-verdict]")?.textContent || "";
check("a refused port shows the server's words", verdict.includes("MQTT broker"), verdict);
check("...and Save is disabled", button("Save")?.disabled === true);
await type(document.getElementById("es-port"), "5113");
await wait(400);
check("a usable port arms Save again", button("Save")?.disabled === false);
globalThis.__sent.length = 0;
await submit();
const sent = globalThis.__sent[0] || [];
check("Save with a new port sends name and port",
      sent[0] === "PATCH" && sent[1] === "/api/hub/systems/asri_test"
      && sent[2]?.port === 5113 && sent[2]?.name === "Asri", JSON.stringify(sent));
check("no em dash in Edit system", !body().includes("\u2014"));
ui.unmount();

portFacts = { ok: true, slug: "demo", port: 5110, stream_port: 5102, suggested: 5113,
              editable: false,
              reason: "demo is run by the box's own service (irivai-master@demo), whose port is set when the software is installed - this page cannot move it" };
ui = m.mountEdit(host, { slug: "demo", name: "Demo", url: "http://192.168.1.21:5110" });
await wait(0);
check("unit-managed: port read-only", document.getElementById("es-port")?.disabled === true);
check("...the reason is shown",
      (document.querySelector("[data-port-reason]")?.textContent || "").includes("irivai-master@demo"));
check("...and no Auto", !button("Auto"));
ui.unmount();
host.remove();

// ---- the create wizard ----------------------------------------------------
host = document.createElement("div");
document.body.appendChild(host);
ui = m.mountWizard(host);
await wait(0);
await type(host.querySelector('[aria-label="System name"]'), "Loading Bay");
await wait(0);
const auto = button("Automatic (next free)");
check("name step: Automatic (next free) is the default",
      auto?.getAttribute("aria-checked") === "true"
      && button("Custom port")?.getAttribute("aria-checked") === "false");
check("...showing the suggestion",
      (host.querySelector("[data-port-note]")?.textContent || "").includes("Suggested: 5113"));
await click(button("Custom port"));
const custom = host.querySelector('[aria-label="Custom port"]');
check("Custom port shows a number field", !!custom);
await type(custom, "1883");
await wait(400);
check("a refused custom port is said and keeps Next off",
      (host.querySelector("[data-port-note]")?.textContent || "").includes("MQTT broker")
      && button("Next")?.disabled === true);
await type(custom, "5120");
await wait(400);
check("a free custom port arms Next", button("Next")?.disabled === false);
await click(button("Next"));
check("Review shows the custom port", host.textContent.includes("custom, 5120"), host.textContent);
globalThis.__sent.length = 0;
await click(button("Create system"));
check("Create sends the custom port",
      globalThis.__sent[0]?.[1] === "/api/hub/systems" && globalThis.__sent[0][2].port === 5120,
      JSON.stringify(globalThis.__sent));
check("no em dash in the wizard", !host.textContent.includes("\u2014"));
ui.unmount();

ui = m.mountWizard(host);
await wait(0);
await type(host.querySelector('[aria-label="System name"]'), "Dock");
await wait(0);
await click(button("Next"));
check("Review shows automatic with the port it would take",
      host.textContent.includes("automatic (next free, 5113 now)"), host.textContent);
globalThis.__sent.length = 0;
await click(button("Create system"));
check("Create on automatic sends no port",
      globalThis.__sent[0] && !("port" in globalThis.__sent[0][2]), JSON.stringify(globalThis.__sent));
ui.unmount();

// ---- 8. the Hub decides --------------------------------------------------------
const hubAt = (mode) => ({ ok: true, running: 5100, pending: null,
                           access: { mode, pending: null, live: true }, restart: {} });
const noBoxPosts = () => !globalThis.__sent.some(([, u]) => u === "/api/hub/port");
portFacts = { ok: true, slug: "asri_test", port: 5112, stream_port: 5212,
              suggested: 5113, editable: true, reason: "" };
host = document.createElement("div");
document.body.appendChild(host);

hubPort = hubAt("proxy");
ui = m.mountEdit(host, CARD);
await wait(0);
check("Edit, Hub on the proxy: uses the proxy, like the Hub, at /s/<slug>/",
      (document.querySelector("[data-proxy-address]")?.textContent || "")
        .includes("Uses the proxy, like the Hub: http://192.168.1.21/s/asri_test/"),
      document.querySelector("[data-proxy-address]")?.textContent);
check("...no switch offered", !document.querySelector("[role=radio]")
      && !body().includes("Use proxy (port 80)"));
check("...no port field until Advanced", !document.getElementById("es-port"));
check("...the stream line: same address (MJPEG), WebRTC on UDP 8189",
      (document.querySelector("[data-stream-proxy]")?.textContent || "").includes("MJPEG")
      && body().includes("UDP port 8189") && !body().includes("keeps port 5212"));
await click(document.querySelector("[data-advanced]"));
check("Advanced: the internal port, still editable",
      document.getElementById("es-port")?.value === "5112" && body().includes("Internal port")
      && document.getElementById("es-port")?.disabled === false);
await click(button("Auto"));
await wait(400);
check("...a move keeps the proxy address",
      (document.querySelector("[data-port-move]")?.textContent || "")
        .includes("its address stays http://192.168.1.21/s/asri_test/"));
globalThis.__sent.length = 0;
await submit();
check("...Save is the system's own PATCH only", globalThis.__sent.length === 1
      && globalThis.__sent[0][0] === "PATCH" && noBoxPosts(), JSON.stringify(globalThis.__sent));
ui.unmount();

hubPort = hubAt("port");
ui = m.mountEdit(host, CARD);
await wait(0);
check("Edit, Hub on ports: the port field as before, no proxy address",
      document.getElementById("es-port")?.value === "5112"
      && !document.querySelector("[data-proxy-address]") && body().includes("keeps port 5212"));
check("...a note that the Hub uses ports, pointing at the Dashboard Access card",
      (document.querySelector("[data-hub-ports]")?.textContent || "")
        .includes("The Hub uses ports; switch it to Standard Access in Settings > Network > Dashboard Access"));
let went = null;
window.addEventListener("irivai:goto", (e) => { went = e.detail; }, { once: true });
await click(document.querySelector("[data-goto-software-port]"));
check("...whose link opens Settings > Network",
      went?.page === "settings" && went?.tab === "network", JSON.stringify(went));
check("no em dash in the address lines", !body().includes("\u2014"));
ui.unmount();

hubPort = hubAt("proxy");
ui = m.mountWizard(host);
await wait(0);
await type(host.querySelector('[aria-label="System name"]'), "Loading Bay");
await wait(0);
check("wizard, Hub on the proxy: the new address, like the Hub",
      (host.querySelector("[data-proxy-address]")?.textContent || "")
        .includes("Uses the proxy, like the Hub: http://192.168.1.21/s/loading_bay/"),
      host.querySelector("[data-proxy-address]")?.textContent);
check("...the port choice under Advanced", !button("Automatic (next free)"));
await click(host.querySelector("[data-advanced]"));
check("...Advanced shows it as the internal port",
      !!button("Automatic (next free)") && host.textContent.includes("Internal port"));
await click(button("Next"));
check("Review names the address", host.textContent.includes("http://192.168.1.21/s/loading_bay/"));
globalThis.__sent.length = 0;
await click(button("Create system"));
await wait(0);
check("...Create sends no port and nothing to /api/hub/port",
      globalThis.__sent.length === 1 && !("port" in globalThis.__sent[0][2]) && noBoxPosts(),
      JSON.stringify(globalThis.__sent));
ui.unmount();

hubPort = hubAt("port");
ui = m.mountWizard(host);
await wait(0);
await type(host.querySelector('[aria-label="System name"]'), "Dock");
await wait(0);
check("wizard, Hub on ports: the port choice as before, the Hub note",
      !!button("Automatic (next free)") && !host.querySelector("[data-proxy-address]")
      && !!host.querySelector("[data-hub-ports]"));
ui.unmount();
host.remove();

console.log(bad ? `FAILED (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
