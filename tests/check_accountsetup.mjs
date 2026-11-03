// The first-run Account setup screen: what is required says so, what is
// optional can be left blank, and half a mailbox is refused in plain words.
//
// Tok Aba, 2026-10-05: the recovery email and the sending mailbox became
// optional at first run, the required fields carry a red "*" (with the word
// "required" for a screen reader) in place of the old "- required" suffix,
// and the page is centred. Pinned by rendering AccountSetup against a
// shimmed api:
//   1. on the shipped password, New / Confirm / Current password carry the
//      marker and the "* required" legend shows; Username never does
//   2. the recovery email and the mailbox fields are not `required`
//   3. an owner who already chose a password sees no marker, "blank to keep"
//   4. an address with no app password is refused before any request
//   5. everything optional left blank still submits
//   6. the page is the centred setup layout
//
//   node tests/check_accountsetup.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".accountsetup-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/account-setup" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "HTMLFormElement", "Element",
                 "Node", "SVGElement", "Event", "CustomEvent", "MouseEvent",
                 "FormData", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage", "sessionStorage"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// GET answers globalThis.__get(url); every POST is recorded and answers ok.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get(url) { return Promise.resolve(globalThis.__get(url)); },
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve({ ok: true }); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { MemoryRouter } from "react-router-dom";
import AccountSetup, { mailboxProblem } from "${p("src/pages/AccountSetup.jsx")}";
export { act, h, AccountSetup, mailboxProblem };
export function mount(el) {
  const root = createRoot(el);
  act(() => { root.render(h(MemoryRouter, null, h(AccountSetup))); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  loader: { ".css": "empty" },
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

const CTX = { ok: true, current: "", username: "iriv", is_default_username: true,
              password_is_default: true, smtp_ready: false };

async function render(ctx) {
  globalThis.__get = (url) => (url.includes("account-setup") ? ctx : { hub_url: "" });
  globalThis.__posts = [];
  const el = document.createElement("div");
  document.body.appendChild(el);
  const r = m.mount(el);
  await flush();
  return { el, done: () => { r.unmount(); el.remove(); } };
}
const label = (el, text) => [...el.querySelectorAll("label")]
  .find((l) => l.textContent.replace(/\s+/g, " ").trim().startsWith(text));
const marked = (l) => !!(l && l.querySelector(".req")
  && l.querySelector(".sr-only")?.textContent.includes("required"));
const field = (el, name) => el.querySelector(`[name="${name}"]`);
async function submit(el, values) {
  for (const [k, v] of Object.entries(values)) field(el, k).value = v;
  await act(async () => {
    el.querySelector("form").dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }));
  });
  await flush();
}

// -- 1, 2, 6. a board still on the shipped password -----------------------------
{
  const { el, done } = await render(CTX);
  for (const t of ["New password", "Confirm new password", "Current password"]) {
    check(`1 "${t}" carries the required marker`, marked(label(el, t)),
          label(el, t)?.outerHTML);
  }
  check("1 Username is not marked", !marked(label(el, "Username")));
  check("1 the old \"- required\" suffix is gone",
        !el.textContent.includes("- required"), el.textContent);
  check("1 the \"* required\" legend shows",
        el.querySelector(".req-legend")?.textContent.replace(/\s+/g, " ").trim()
          === "* required", el.querySelector(".req-legend")?.outerHTML);
  check("1 the star itself is hidden from a screen reader",
        [...el.querySelectorAll(".req")].every((s) => s.getAttribute("aria-hidden") === "true"));
  for (const n of ["email", "smtp_sender", "smtp_password", "smtp_host"]) {
    check(`2 ${n} is optional`, field(el, n) && !field(el, n).required);
  }
  check("2 the recovery section says it can be added later",
        el.textContent.includes("add it later under") && el.textContent.includes(
          "Without a recovery email, a lost password cannot be reset by email."),
        el.textContent);
  check("6 the page is the centred setup layout",
        !!el.querySelector("section.setup-page > .setup-col > .panel-card"));
  check("6 ...with no fixed inline width left on the card",
        !el.querySelector(".panel-card[style]"));
  done();
}

// -- 3. an owner who already chose a password ------------------------------------
{
  const { el, done } = await render({ ...CTX, password_is_default: false,
                                      is_default_username: false,
                                      current: "owner@example.com" });
  check("3 no required marker anywhere", !el.querySelector(".req"));
  check("3 no legend", !el.querySelector(".req-legend"));
  check("3 New password still reads \"blank to keep\"",
        label(el, "New password")?.textContent.includes("- blank to keep"));
  done();
}

// -- 4. half a mailbox is refused before the round trip ---------------------------
{
  const { el, done } = await render({ ...CTX, password_is_default: false });
  await submit(el, { smtp_sender: "alerts@example.com" });
  const err = el.querySelector(".login-error")?.textContent || "";
  check("4 an address with no app password is refused in plain words",
        err.includes("Add the app password too"), err);
  check("4 ...and nothing was sent", globalThis.__posts.length === 0,
        JSON.stringify(globalThis.__posts));
  done();
}
check("4 a lone app password asks for the address",
      m.mailboxProblem("", "abcd", "").includes("Add the sending address too"));
check("4 a lone SMTP server asks for the address",
      m.mailboxProblem("", "", "smtp.example.com").includes("sending address"));
check("4 all blank is fine", m.mailboxProblem("", "", "") === "");
check("4 address and a Gmail app password is fine, with or without spaces",
      m.mailboxProblem("a@b.co", "abcd efgh ijkl mnop", "") === ""
      && m.mailboxProblem("a@b.co", "abcdefghijklmnop", "smtp.gmail.com") === "");
// App passwords only (2026-10-14).
check("4 a normal password for Gmail (blank server = Gmail) is refused in plain words",
      m.mailboxProblem("a@b.co", "MyPassw0rd!", "").startsWith("That does not look like an app password.")
      && m.mailboxProblem("a@b.co", "abcd efgh", "smtp.googlemail.com").startsWith("That does not look like"));
{
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(join(FRONT, "src", "pages", "AccountSetup.jsx"), "utf8");
  check("4 the setup field is labelled App Password, with the plain help and How to create one",
        src.includes('<label htmlFor="setup-app-password">App Password</label>')
        && src.includes('placeholder="16-character app password"')
        && src.includes("Use an app password, not your normal email password.")
        && src.includes("How to create one (Gmail)") && src.includes('autoComplete="new-password"'));
}
check("4 another server takes what its provider issued",
      m.mailboxProblem("a@b.co", "Any-Thing#1", "smtp.office365.com") === "");

// -- 5. everything optional blank still submits ------------------------------------
{
  const { el, done } = await render({ ...CTX, password_is_default: false });
  await submit(el, {});
  const [url, body] = globalThis.__posts[0] || [];
  check("5 a blank recovery email and mailbox still submit",
        url === "/api/auth/account-setup", JSON.stringify(globalThis.__posts));
  check("5 ...with the address and mailbox blank",
        body && !body.email && !body.smtp_sender && !body.smtp_password,
        JSON.stringify(body));
  done();
}

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall account-setup checks passed");
process.exit(0);
