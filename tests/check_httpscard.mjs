// The HTTPS card on the Network page (frontend/src/components/HttpsCard.jsx),
// rendered against a shimmed api:
//   1. not master (the GET fails): no card at all
//   2. off: "Off", no fingerprint and no download; the switch posts
//      {enabled: true}; "Send browsers to HTTPS" cannot be touched while off
//   3. on: the state, the fingerprint, the addresses (no localhost), the
//      .crt and .cer downloads of the PUBLIC root route, the per-OS steps,
//      and the warning that a browser complains until the root is trusted
//   4. opened over https: a green "encrypted" note; switching off from
//      there asks first, and No posts nothing
//   5. a new root asks first, with a typed confirmation, and says every
//      client must trust it again; No posts nothing, Yes posts confirm
//   6. "Send browsers to HTTPS" asks first and names who stays on HTTP
//   7. a damaged file / port 443 refusal are said in plain words
//   8. no em dash, no key, no command for the BOX anywhere on the card (the
//      Windows step's certutil runs on the client, by design: Pipi L2)
//  10. customer view (Tok Aba, 2026-10-14; user.is_developer false): the
//      title, subtitle, Enabled / Disabled pill, "HTTPS is active", the
//      addresses each with a copy button, the note that HTTP is redirected automatically - and NOT Trust
//      This Device, Certificate Details, Advanced HTTPS Settings, the
//      fingerprint or the downloads. Cases 2-9 run as a developer account,
//      which still sees all of that.
//
//   node tests/check_httpscard.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".httpscard-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "PointerEvent", "KeyboardEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "localStorage"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;
global.sessionStorage = dom.window.sessionStorage;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          post(url, body) { globalThis.__posts.push([url, body]);
            return Promise.resolve(globalThis.__post || globalThis.__get); },
          get(url) { return globalThis.__get ? Promise.resolve(globalThis.__get)
                                             : Promise.reject(new Error("403")); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimAsk = {
  name: "shim-ask",
  setup(b) {
    b.onResolve({ filter: /ui\/ask\.jsx$/ }, () => ({ path: "ask-shim", namespace: "shim" }));
    b.onLoad({ filter: /^ask-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__asked = [];
        globalThis.__answers = [];
        const confirm = async (props) => {
          globalThis.__asked.push(props);
          return globalThis.__answers.length ? globalThis.__answers.shift() : false;
        };
        export function useConfirm() { return confirm; }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

// HttpsCard reads the account (useIsDeveloper), switched per case here.
globalThis.__user = { is_developer: true };
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state\/app\.jsx$/ }, () => ({ path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export function useApp() { return { user: globalThis.__user }; }
        export function useIsDeveloper() { return !!globalThis.__user?.is_developer; }`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi, shimAsk, shimApp],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import HttpsCard from "${p("src/components/HttpsCard.jsx")}";
      export { act };
      export function mount(el) {
        const root = createRoot(el);
        act(() => { root.render(h(HttpsCard)); });
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
  for (let i = 0; i < 6; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
};
const byText = (el, text) => [...el.querySelectorAll("button")]
  .find((b) => b.textContent.trim().endsWith(text));

async function render(get, fn, post = null) {
  globalThis.__get = get;
  globalThis.__post = post;
  globalThis.__posts.length = 0;
  globalThis.__asked.length = 0;
  globalThis.__answers.length = 0;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box);
  await flush();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const OFF = { ok: true, enabled: false, port: 8443, default_port: 8443, redirect_http: false,
              listening: false, port443: false, port443_detail: "", error: "", helper: true,
              root: null, leaf: null, uncovered: [], page_https: false };
const ON = { ...OFF, enabled: true, listening: true,
             root: { fingerprint: "AB:CD:EF", subject: "CN=IRIV Edge AI local CA a1b2c3d4",
                     not_after: "2036-10-10T00:00:00+00:00", created: "2026-10-10T00:00:00+00:00" },
             leaf: { names: ["iriv-a1.local", "iriv-a1", "localhost"],
                     ips: ["127.0.0.1", "::1", "192.168.1.21"],
                     not_after: "2027-11-11T00:00:00+00:00", issued: "2026-10-10T00:00:00+00:00",
                     reason: "new root" } };

// 1 ---------------------------------------------------------------------------
await render(null, async (box) => {
  check("1. not master: no card", !box.querySelector("[data-testid=https-card]"));
});

// 2 ---------------------------------------------------------------------------
await render(OFF, async (box) => {
  const card = box.querySelector("[data-testid=https-card]");
  check("2. off: the pill says Disabled", card && card.querySelector("[data-testid=https-state]")
    .textContent.trim() === "Disabled");
  check("...no fingerprint, no download", !box.querySelector("[data-testid=https-fingerprint]")
    && !box.querySelector("[data-testid=https-download]"));
  const sw = box.querySelector("[aria-label='HTTPS']");
  await click(sw);
  check("...the switch posts enabled: true", JSON.stringify(globalThis.__posts[0])
    === JSON.stringify(["/api/hub/https", { enabled: true }]), JSON.stringify(globalThis.__posts));
}, ON);

// 3 ---------------------------------------------------------------------------
await render(ON, async (box) => {
  const text = box.textContent;
  const state = box.querySelector("[data-testid=https-state]");
  check("3. on: the pill says Enabled, its title names the port",
        state.textContent.trim() === "Enabled" && state.getAttribute("title") === "On - port 8443"
        && state.className.includes("is-on"));
  check("...the fingerprint", box.querySelector("[data-testid=https-fingerprint]")
    .textContent.trim() === "AB:CD:EF");
  check("...the addresses, no localhost",
        text.includes("https://iriv-a1.local:8443/") && text.includes("https://192.168.1.21:8443/")
        && !text.includes("https://localhost"));
  const links = [...box.querySelectorAll("a[download]")].map((a) => a.getAttribute("href"));
  check("...downloads: the public root route, .crt and .cer",
        links.length === 2 && links[0].endsWith("api/hub/https/root.crt")
        && links[1].endsWith("api/hub/https/root.crt?format=der"), JSON.stringify(links));
  for (const os of ["Windows", "macOS", "iPhone", "Android", "Firefox"]) {
    check(`...trust steps for ${os}`, text.includes(os));
  }
  check("...says the browser warns until the root is trusted", /security\s+warning/.test(text));
  check("...Windows: the SHA-256 check with certutil on the .cer, Current User",
        text.includes("certutil -hashfile iriv-edge-ai-root.cer SHA256")
        && text.includes("Current User"));
  check("...compare the fingerprint on the device's own screen",
        /device's own screen/.test(text));
  check("...the root's reach is said as it is: local names and private addresses, websites only",
        /other devices on this network/.test(text) && /no programs and no e-mail/.test(text));
  check("...no green note on a plain-HTTP page", !box.querySelector("[data-testid=https-here]"));
});

// 4 ---------------------------------------------------------------------------
await render({ ...ON, page_https: true }, async (box) => {
  check("4. opened over https: the green note", !!box.querySelector("[data-testid=https-here]"));
  await click(box.querySelector("[aria-label='HTTPS']"));
  check("...switching off asks first and says this page stops",
        globalThis.__asked.length === 1 && /stops working/.test(globalThis.__asked[0].description));
  check("...No posts nothing", globalThis.__posts.length === 0);
});

// 5 ---------------------------------------------------------------------------
await render(ON, async (box) => {
  await click(byText(box, "Replace Root Certificate..."));
  const asked = globalThis.__asked[0] || {};
  check("5. Replace Root Certificate asks, with a typed confirmation",
        asked.requireText && /every computer and phone/i.test(asked.description), JSON.stringify(asked));
  check("...No posts nothing", globalThis.__posts.length === 0);
  globalThis.__answers.push(true);
  check("...and says computers and phones must trust the new authority again",
        /trust the new certificate authority again/.test(asked.description || ""));
  await click(byText(box, "Replace Root Certificate..."));
  check("...Yes posts what: root with confirm", JSON.stringify(globalThis.__posts[0])
    === JSON.stringify(["/api/hub/https/regenerate", { what: "root", confirm: true }]));
  await click(byText(box, "Generate New Certificate"));
  check("...Generate New Certificate asks nothing", JSON.stringify(globalThis.__posts[1])
    === JSON.stringify(["/api/hub/https/regenerate", { what: "leaf" }]));
});

// 7 ---------------------------------------------------------------------------
await render({ ...ON, listening: false, error: "the HTTPS file (https.local.toml) cannot be read",
               port443_detail: "x" }, async (box) => {
  check("7. a damaged file is said on the card",
        box.querySelector("[data-testid=https-error]")?.textContent.includes("cannot be read"));
});
await render({ ...ON, port443_detail: "the helper web-redirect.py is not installed on this box" },
             async (box) => {
  check("...and why 443 is not used, with the port that works",
        /Port 443 is not used: the helper/.test(box.textContent) && /answers on\s+port 8443/.test(box.textContent));
});

// 9. Customer copy pass (2026-10-11) -------------------------------------------
// Status stays in sight; the fingerprint, CA name, validity and covered names
// fold into Certificate Details and the port and regenerate buttons into
// Advanced HTTPS Settings; trust is explained apart from encryption.
await render(ON, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("9. titled Secure Connection (HTTPS), with HTTPS is active",
        t.includes("Secure Connection (HTTPS)")
        && box.querySelector("[data-testid=https-active]")?.textContent.includes("HTTPS is active"));
  check("...a developer still reads that HTTPS encrypts and why a warning can show",
        box.querySelector("[data-testid=https-dev-extras]")?.textContent.replace(/\s+/g, " ").includes(
          "HTTPS encrypts the connection between your browser and this device."));
  check("...the addresses stay in sight",
        !box.querySelector("[data-testid=https-urls]")?.closest("details")
        && box.querySelector("[data-testid=https-urls]")?.textContent.includes("Dashboard addresses"));
  const trust = box.querySelector("[data-testid=https-trust]");
  check("...Trust This Device says trust is separate from encryption",
        trust?.textContent.replace(/\s+/g, " ").includes(
          "The connection is encrypted whether or not the certificate is trusted."));
  check("...with both downloads and Copy Certificate Fingerprint in sight",
        !box.querySelector("[data-testid=https-download]").closest("details")
        && !!byText(box, "Copy Certificate Fingerprint")
        && !byText(box, "Copy Certificate Fingerprint").closest("details"));
  const det = box.querySelector("[data-testid=https-details]");
  check("...fingerprint, CA name, validity and covered names under Certificate Details",
        det?.tagName === "DETAILS" && !det.open
        && det.querySelector("summary")?.textContent.trim() === "Certificate Details"
        && det.contains(box.querySelector("[data-testid=https-fingerprint]"))
        && det.textContent.includes("CN=IRIV Edge AI local CA a1b2c3d4")
        && det.textContent.includes("renewed automatically 30 days before it expires")
        && det.contains(box.querySelector("[data-testid=https-sans]")));
  const adv = box.querySelector("[data-testid=https-advanced]");
  check("...the port and certificate actions under Advanced HTTPS Settings",
        adv?.tagName === "DETAILS" && !adv.open
        && adv.querySelector("summary")?.textContent.trim() === "Advanced HTTPS Settings"
        && adv.contains(byText(box, "Save HTTPS Port"))
        && adv.contains(byText(box, "Generate New Certificate"))
        && adv.contains(byText(box, "Replace Root Certificate..."))
        && adv.textContent.includes("HTTPS Port (default 8443)"));
  Object.defineProperty(globalThis.navigator, "clipboard", { configurable: true,
    value: { writeText: async (text) => { globalThis.__copied = text; } } });
  await click(byText(box, "Copy Certificate Fingerprint"));
  check("...the copy button still copies the fingerprint", globalThis.__copied === "AB:CD:EF",
        String(globalThis.__copied));
});

// 10. customer view ---------------------------------------------------------
globalThis.__user = { is_developer: false };
await render(ON, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("10. customer: title, subtitle and the Enabled pill",
        t.includes("Secure Connection (HTTPS)")
        && box.querySelector("[data-testid=https-about]")?.textContent.trim()
          === "Encrypted access to your device dashboard."
        && box.querySelector("[data-testid=https-state]")?.textContent.trim() === "Enabled",
        t);
  check("...HTTPS is active, with the certificate-warning line",
        box.querySelector("[data-testid=https-active]")?.textContent.replace(/\s+/g, " ").includes(
          "HTTPS is active") && box.querySelector("[data-testid=https-active]")?.textContent
          .replace(/\s+/g, " ").includes("Your connection is encrypted. Your browser may still display a "
          + "certificate warning if the device's certificate authority is not trusted."));
  const urls = box.querySelector("[data-testid=https-urls]");
  const copies = [...box.querySelectorAll("[data-testid=https-copy-url]")];
  check("...Dashboard addresses, one copy button each",
        urls?.textContent.includes("Dashboard addresses")
        && urls.textContent.includes("https://iriv-a1.local:8443/")
        && urls.textContent.includes("https://192.168.1.21:8443/")
        && copies.length === urls.querySelectorAll("li").length && copies.length === 2,
        String(copies.length));
  check("...no second switch: the redirect follows HTTPS, and the card says so",
        !box.querySelector("[data-testid=https-redirect]")
        && !box.querySelector("[aria-label='Redirect HTTP to HTTPS']")
        && /sent here automatically/.test(t));
  check("...and none of the developer extras",
        !box.querySelector("[data-testid=https-dev-extras]")
        && !/Trust This Device|Certificate Details|Advanced HTTPS Settings|fingerprint|\.crt|\.cer/i.test(t)
        && !box.querySelector("a[download]") && !box.querySelector("[data-testid=https-fingerprint]"),
        t.match(/.{20}(Trust This Device|Certificate Details|Advanced|fingerprint|\.crt|\.cer).{20}/i)?.[0]);
  Object.defineProperty(globalThis.navigator, "clipboard", { configurable: true,
    value: { writeText: async (text) => { globalThis.__copied = text; } } });
  await click(copies[1]);
  check("...a copy button copies its address", globalThis.__copied === "https://192.168.1.21:8443/",
        String(globalThis.__copied));
  check("...no em dash", !box.textContent.includes("\u2014"));
});
await render(OFF, async (box) => {
  const was = box.querySelector("[data-testid=https-state]")?.textContent.trim();
  await click(box.querySelector("[aria-label='HTTPS']"));
  check("...customer, off: Disabled, and the switch still posts enabled: true",
        was === "Disabled"
        && JSON.stringify(globalThis.__posts[0])
          === JSON.stringify(["/api/hub/https", { enabled: true }]), JSON.stringify(globalThis.__posts));
}, ON);
await render({ ...ON, listening: false, error: "the HTTPS file (https.local.toml) cannot be read" },
             async (box) => {
  check("...customer: a failure is still said, the pill reads Not answering",
        box.querySelector("[data-testid=https-error]")?.textContent.includes("cannot be read")
        && box.querySelector("[data-testid=https-state]")?.textContent.trim() === "Not answering");
});
globalThis.__user = { is_developer: true };

// 8 ---------------------------------------------------------------------------
await render({ ...ON, page_https: true }, async (box) => {
  const text = box.textContent;
  check("8. no em dash, no key, no command", !text.includes("\u2014") && !/PRIVATE|BEGIN /.test(text)
    && !/sudo|systemctl|nft /.test(text));
});

// 9 ---------------------------------------------------------------------------
// Card order on the Network page: Device Name, then Dashboard Access, with the
// HTTPS card immediately after it (nothing between them), in both layouts.
{
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(join(FRONT, "src", "components", "NetworkPanel.jsx"), "utf8");
  const tags = [...src.matchAll(/<(SoftwarePortCard|HostnameCard|HttpsCard) \/>/g)].map((m) => m[1]);
  check("9. order is Hostname, SoftwarePort, Https (both layouts)",
        tags.join(",") === "HostnameCard,SoftwarePortCard,HttpsCard,HostnameCard,SoftwarePortCard,HttpsCard",
        tags.join(","));
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
