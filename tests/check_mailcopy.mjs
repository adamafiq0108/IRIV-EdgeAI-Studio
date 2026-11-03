// Settings > Email: the customer copy pass (2026-10-11).
//
// Wording only; storage, defaults (smtp.gmail.com, 587), STARTTLS and the
// server's validation are unchanged. Pinned here: the empty state and its
// Add Email Account button, the form's spec labels and help (Gmail App
// Password, blank name uses the address, blank server uses smtp.gmail.com,
// STARTTLS on every port), the validation titles the form raises BEFORE
// posting, which refuse only what mail_accounts._clean refuses (a blank
// server or port is still allowed), a save that never claims to have tested
// the account, and no raw server or SMTP text in any toast.
//
//   node tests/check_mailcopy.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".mailcopy-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "HTMLInputElement",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// POST answers per url from globalThis.__answer: a value, or an Error with a
// status to reject with.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get() { return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__accounts))); },
          post(url, body) {
            globalThis.__posts.push([url, body]);
            const a = (globalThis.__answer || {})[url];
            if (a && a.status) {
              const e = new Error(a.message); e.status = a.status;
              return Promise.reject(e);
            }
            return Promise.resolve(a || JSON.parse(JSON.stringify(globalThis.__accounts)));
          },
        };
        export function friendlyError(e) { return "friendly:" + (e && e.status); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import MailPanel from "${p("src/components/MailPanel.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, MailPanel, ToastProvider };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 20)); });
const click = (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});
const button = (root, label) => [...root.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === label);
const field = (box, label) => [...box.querySelectorAll("label")]
  .find((l) => l.querySelector("span")?.textContent.trim().startsWith(label));
async function type(input, value) {
  const set = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value").set;
  await m.act(async () => {
    set.call(input, value);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
const toastText = () => document.body.textContent;

async function render(accounts, fn) {
  globalThis.__accounts = accounts;
  globalThis.__posts = [];
  globalThis.__answer = {};
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null, h(m.MailPanel)));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

await render({ accounts: [], default_id: "" }, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("Email Accounts with the spec empty state",
        t.includes("Email Accounts") && t.includes("No email accounts configured")
        && t.includes("Add an email account to send alerts from workflows or email footage from the Footage page."),
        t);
  check("the empty state offers Add Email Account",
        !!box.querySelector("[data-mail-empty] button")
        && box.querySelector("[data-mail-empty] button").textContent.trim() === "Add Email Account");
  await click(box.querySelector("[data-mail-empty] button"));
  const f = box.textContent.replace(/\s+/g, " ");
  check("the form is Add Email Account with the spec description",
        f.includes("Add Email Account")
        && f.includes("Configure an email account that this device can use to send alerts and other messages."));
  check("Using Gmail? help, and other providers may differ",
        box.querySelector("[data-mail-gmail-help]")?.textContent.replace(/\s+/g, " ").includes(
          "Security → 2-Step Verification → App passwords")
        && f.includes("Other email providers may use a different server, port and password type."));
  check("the five spec labels",
        ["Account Name", "Sending Email Address", "App Password",
         "Outgoing Mail Server (SMTP)", "SMTP Port"].every((l) => !!field(box, l)));
  check("blank Account Name says it falls back to the address",
        field(box, "Account Name").textContent.includes(
          "If left blank, the sending email address will be used as its name."));
  check("the password keeps its show/hide control",
        !!field(box, "App Password")?.querySelector("button"));
  check("the port keeps 587 and says STARTTLS",
        field(box, "SMTP Port").querySelector("input").value === "587"
        && field(box, "SMTP Port").textContent.includes("Port 587 uses STARTTLS encryption"));
  check("the server says what a blank one means",
        field(box, "Outgoing Mail Server (SMTP)").textContent.includes(
          "If left blank, smtp.gmail.com is used."));

  const save = () => click(button(box, "Save Email Account"));
  await save(); await settle();
  check("no address: Email Address Required, nothing posted",
        toastText().includes("Email Address Required") && globalThis.__posts.length === 0);
  await type(field(box, "Sending Email Address").querySelector("input"), "not-an-address");
  await save(); await settle();
  check("a bad address: Invalid Email Address",
        toastText().includes("Invalid Email Address") && globalThis.__posts.length === 0);
  await type(field(box, "Sending Email Address").querySelector("input"), "alerts@example.com");
  await save(); await settle();
  check("no password on a new account: App Password Required",
        toastText().includes("App Password Required") && globalThis.__posts.length === 0);
  // App passwords only (2026-10-14): a normal password for Gmail is refused
  // as you type and at Save, with nothing posted.
  await type(field(box, "App Password").querySelector("input"), "MyGmailPassw0rd!");
  check("a normal password for Gmail: the plain message under the field, as you type",
        box.querySelector("[data-mail-pw-problem]")?.textContent.includes(
          "That does not look like an app password. Gmail app passwords have 16 letters (for example abcd efgh ijkl mnop)."));
  await save(); await settle();
  check("...and at Save: Not an App Password, nothing posted",
        toastText().includes("Not an App Password") && globalThis.__posts.length === 0);
  await type(field(box, "App Password").querySelector("input"), "abcd efgh ijkl mnop");
  check("a Gmail app password in four groups is accepted (no message)",
        !box.querySelector("[data-mail-pw-problem]"));
  check("the field says App Password only, with the plain help and How to create one",
        field(box, "App Password").querySelector("input").getAttribute("placeholder") === "16-character app password"
        && field(box, "App Password").querySelector("input").getAttribute("autocomplete") === "new-password"
        && box.querySelector("[data-mail-pw-help]")?.textContent.replace(/\s+/g, " ").includes(
          "Use an app password, not your normal email password. Your email provider creates it in the account's security settings (for Gmail: Google Account > Security > 2-Step Verification > App passwords).")
        && box.querySelector("[data-mail-pw-howto] summary")?.textContent === "How to create one (Gmail)"
        && box.querySelector("[data-mail-pw-howto]").querySelectorAll("li").length === 5
        && !box.textContent.includes("Email Password or"));
  await type(field(box, "SMTP Port").querySelector("input"), "70000");
  await save(); await settle();
  check("port out of range: Invalid SMTP Port",
        toastText().includes("Invalid SMTP Port") && globalThis.__posts.length === 0);
  await type(field(box, "SMTP Port").querySelector("input"), "587");
  await type(field(box, "Outgoing Mail Server (SMTP)").querySelector("input"), "");
  globalThis.__answer["/api/mail/accounts"] = { status: 400,
    message: "'x' is not an email address zzSECRETzz" };
  await save(); await settle();
  check("a blank server is not refused by the form (the server defaults it)",
        globalThis.__posts.length === 1);
  check("a 400 says Could Not Save Email Account in words, never the server text",
        toastText().includes("Could Not Save Email Account")
        && toastText().includes("Check the information you entered and try again.")
        && !toastText().includes("zzSECRETzz"));
  globalThis.__answer["/api/mail/accounts"] = { accounts: [
    { id: "a1", name: "alerts@example.com", sender: "alerts@example.com",
      smtp_host: "smtp.gmail.com", smtp_port: 587, has_password: true }],
    default_id: "a1" };
  await save(); await settle();
  check("saved: Email Account Saved, and no claim that it was tested",
        toastText().includes("Email Account Saved")
        && toastText().includes("The email account has been saved and is available for use wherever email sending is supported.")
        && !/verified|works|delivered/i.test(
          [...document.querySelectorAll("[role=status], li")].map((n) => n.textContent).join(" ")));
});

await render({ accounts: [{ id: "a1", name: "Line Alerts", sender: "alerts@example.com",
                            smtp_host: "smtp.gmail.com", smtp_port: 587,
                            has_password: true }], default_id: "a1" }, async (box) => {
  check("the list shows the Default badge", box.textContent.includes("Default"));
  globalThis.__answer["/api/mail/accounts/test"] = { status: 400,
    message: "could not send: [Errno 111] Connection refused smtp.example" };
  await click(button(box, "Test")); await settle();
  check("a failed test is told in words, not smtplib's text",
        toastText().includes("The test email could not be sent")
        && toastText().includes("Check the server, port and password, then try again.")
        && !toastText().includes("Errno"));
  globalThis.__answer["/api/mail/accounts/test"] = { status: 400,
    message: "the mail server refused these credentials - Gmail needs an App Password" };
  await click(button(box, "Test")); await settle();
  check("a refused login names the App Password, without the server sentence",
        toastText().includes("did not accept this email address and password")
        && !toastText().includes("refused these credentials"));
  await click(button(box, "Edit"));
  check("editing: blank password keeps the saved one",
        box.textContent.includes("Edit Email Account")
        && box.textContent.includes("(leave blank to keep the saved password)"));
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall email copy checks passed");
dom.window.close();
process.exit(0);
