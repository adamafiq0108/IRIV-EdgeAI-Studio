// The shared card header and its on/off (frontend/src/components/ui/
// CardHeader.jsx, Tok Aba 2026-10-14): every card-level switch sits at the
// top right of its card's header row, its state label immediately left of it.
//   1. CardHeader: icon, the title in the card's first <strong>, the muted
//      line, and the control in .card-head-right, last in the row
//   2. StateSwitch: the label comes BEFORE the switch, says On / Off, is
//      green (is-on) while on and plain while off; the switch keeps the
//      caller's aria-label, title and disabled, and its own aria-checked
//   3. a click reaches the caller's onCheckedChange with the new value
//   4. custom words and a warn colour ("Enabled" / "Not answering")
//   5. theme.css paints the pill with the *-text tokens and the switch with
//      --brand-accent (never --accent / --muted as text)
//
//   node tests/check_cardheader.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".cardheader-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "PointerEvent", "KeyboardEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h, useState } from "react";
      import { Wifi } from "lucide-react";
      import { CardHeader, StateSwitch } from "${p("src/components/ui/CardHeader.jsx")}";
      export { act };
      function Demo({ props }) {
        const [on, setOn] = useState(props.checked);
        return h("div", { className: "panel-card" },
          h(CardHeader, { icon: Wifi, title: "Device Hotspot",
                          subtitle: "Allow phones and computers to connect directly to this device." },
            h(StateSwitch, { ...props, checked: on,
                             onCheckedChange: (v) => { globalThis.__calls.push(v); setOn(v); } })));
      }
      export function mount(el, props) {
        const root = createRoot(el);
        act(() => { root.render(h(Demo, { props })); });
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
const flush = async () => {
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

async function render(props, fn) {
  globalThis.__calls = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, props);
  await flush();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

await render({ checked: true, "aria-label": "Device hotspot", title: "Turn the hotspot off" },
async (box) => {
  const head = box.querySelector(".card-head");
  check("1. header: icon, title in the first strong, the line, control last",
        head && head.querySelector("svg.card-head-icon")
        && box.querySelector("strong")?.textContent === "Device Hotspot"
        && head.querySelector(".card-head-sub")?.textContent.startsWith("Allow phones")
        && head.lastElementChild?.classList.contains("card-head-right"));
  const pair = head.querySelector(".card-head-right .state-switch");
  const label = pair?.querySelector("[data-state-label]");
  const sw = pair?.querySelector("[aria-label='Device hotspot']");
  check("2. the label sits immediately left of the switch",
        !!label && !!sw && !!label.nextElementSibling
        && (label.nextElementSibling === sw || label.nextElementSibling.contains(sw)),
        pair?.outerHTML);
  check("...says On, green", label?.textContent === "On" && label.className === "state-pill is-on");
  check("...the switch keeps the caller's aria-label and title, and is checked",
        sw?.getAttribute("title") === "Turn the hotspot off"
        && (sw.getAttribute("aria-checked") === "true" || sw.hasAttribute("data-checked")));
  await act(async () => { sw.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
  check("3. a click reaches onCheckedChange with the new value",
        JSON.stringify(globalThis.__calls) === "[false]", JSON.stringify(globalThis.__calls));
  check("...and the label follows: Off, plain",
        label.textContent === "Off" && label.className === "state-pill");
});

await render({ checked: true, onLabel: "Enabled", offLabel: "Disabled", label: "Not answering",
               kind: "warn", "aria-label": "HTTPS", disabled: true }, async (box) => {
  const label = box.querySelector("[data-state-label]");
  check("4. custom words and the warn colour",
        label?.textContent === "Not answering" && label.className === "state-pill is-warn");
  const sw = box.querySelector("[aria-label='HTTPS']");
  check("...disabled is passed through",
        sw && (sw.hasAttribute("disabled") || sw.getAttribute("aria-disabled") === "true"
               || sw.hasAttribute("data-disabled")), sw?.outerHTML);
});

const css = readFileSync(join(FRONT, "src", "theme.css"), "utf8");
check("5. theme.css: the pill on the *-text tokens, the switch on --brand-accent",
      /\.state-pill\.is-on \{[^}]*color: var\(--ok-text\)/.test(css)
      && /\.state-pill \{[^}]*color: var\(--muted-text\)/.test(css)
      && /\.state-switch \[data-slot="switch"\]\[data-checked\] \{ background: var\(--brand-accent/.test(css));
const src = readFileSync(join(FRONT, "src", "components", "ui", "CardHeader.jsx"), "utf8");
check("...no em dash in the component", !src.includes("\u2014"));

// 6. Every card-level on/off uses the shared pair, so placement and look
//    are one thing (Tok Aba, 2026-10-14). Per page group: the component and
//    the aria-label of its card-level switch. List rows, nested options and
//    canvas inspectors keep the plain Switch on purpose.
const CARD_LEVEL = [
  ["NetworkPanel.jsx", ["Wi-Fi", "Device hotspot"]],
  ["HttpsCard.jsx", ["HTTPS"]],
  ["SystemPanel.jsx", ["Sign out automatically when idle", "Email"]],
  ["ChipBalanceCard.jsx", ["Hailo load balancer"]],
  ["PollCacheCard.jsx", ["Shared poll cache"]],
  ["BoardPanel.jsx", ["Node-RED"]],
  ["PanelSettings.jsx", ["Front panel"]],
];
for (const [file, labels] of CARD_LEVEL) {
  const text = readFileSync(join(FRONT, "src", "components", file), "utf8");
  for (const label of labels) {
    const at = text.indexOf(`aria-label="${label}"`);
    const open = text.lastIndexOf("<", at);
    check(`6. ${file}: the "${label}" switch is a StateSwitch`,
          at > 0 && text.slice(open, open + 13) === "<StateSwitch ", text.slice(open, open + 40));
  }
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
