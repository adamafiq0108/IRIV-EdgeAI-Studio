// Settings > Telegram: the customer copy pass (2026-10-11).
//
// Wording only; the Bot API calls, the token storage, assignment (one bot per
// system), the allowlist gate and the server's validation are unchanged.
// Pinned here: the spec headings, empty state, labels and help; the allowed
// lists said to gate COMMANDS only (telegram_net.allowed, empty = nobody) and
// not outgoing messages; the reassignment notice before Save; the form's
// validation titles, which refuse only what telegram_bots._clean refuses;
// the save never claiming Telegram was contacted; and no raw server or
// Telegram text (or token) in any toast.
//
// Email claims (2026-10-11): Allowed Email Addresses is the main list, with
// the "anyone who knows it can claim it first" warning; Allowed Chat IDs sits
// unchanged under a collapsed Advanced; each address shows Unclaimed or
// Claimed by, Remove frees it through /unlink after a confirmation; a group
// that refused a delete shows the make-it-an-admin hint.
//
// Security pass (Pipi, 2026-10-11): a claimant is shown as ID, @username (or
// "no username") and a quoted name, as separate pieces, in the row and the
// Remove dialog -- a first name "Ana B (@ana)" reads as a name with no
// username; a removed user is listed as blocked with Allow again (/unblock);
// a paused bot says so; the help says a group claim is addressed to the bot
// (/link, a mention, a reply) and why (privacy mode).
//
// Layout pass (Tok Aba, 2026-10-14): the block opens with an amber
// "Important Security Notice", then Email addresses (one per line, 4 rows,
// two example lines) and the drop-on-save hint; the how-to is five short
// steps in ONE collapsed "How people link their email" disclosure.
//
//   node tests/check_tgcopy.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".tgcopy-test.cjs");

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
          get() { return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__bots))); },
          post(url, body) {
            globalThis.__posts.push([url, body]);
            const a = (globalThis.__answer || {})[url];
            if (a && a.status) {
              const e = new Error(a.message); e.status = a.status;
              return Promise.reject(e);
            }
            return Promise.resolve(a || JSON.parse(JSON.stringify(globalThis.__bots)));
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
import TelegramPanel from "${p("src/components/TelegramPanel.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
import { AskProvider } from "${p("src/components/ui/ask.jsx")}";
export { act, h, TelegramPanel, ToastProvider, AskProvider };
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

async function choose(select, value) {
  const set = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype, "value").set;
  await m.act(async () => {
    set.call(select, value);
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
}

async function render(bots, fn) {
  globalThis.__bots = bots;
  globalThis.__posts = [];
  globalThis.__answer = {};
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null,
                           h(m.AskProvider, null, h(m.TelegramPanel))));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const TOKEN = "123456789:AAbbccddeeffgghhiijjkkllmmnn";
await render({ bots: [], default_id: "", systems: [] }, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("heading Telegram Notifications, true subtitle (assigned, not picked by name)",
        box.querySelector("h1")?.textContent === "Telegram Notifications"
        && t.includes("Connect Telegram bots to send alerts and notifications from this device.")
        && t.includes("Once a bot is assigned to a system")
        && !t.includes("selected by name"), t.slice(0, 300));
  check("Connected Bots with the spec empty state and button",
        t.includes("Connected Bots") && t.includes("No Telegram bots configured")
        && t.includes("Add a Telegram bot to send alerts from workflows to Telegram chats or groups.")
        && box.querySelector("[data-tg-empty] button")?.textContent.trim() === "Add Telegram Bot");
  await click(box.querySelector("[data-tg-empty] button"));
  const f = box.textContent.replace(/\s+/g, " ");
  check("the form is Add Telegram Bot with the BotFather steps",
        f.includes("Add Telegram Bot") && f.includes("How to create a Telegram bot")
        && f.includes("Open Telegram and start a conversation with @BotFather.")
        && f.includes("Each person then sends /start to the bot and sends their address "
                      + "when the bot asks for it. In a group they can also send /link "
                      + "followed by their address."));
  check("no by-hand chat ID lookup help: no getUpdates URL, no How to find a chat ID",
        !f.includes("getUpdates") && !f.includes("api.telegram.org")
        && !f.includes("How to find a chat ID") && !box.querySelector("[data-tg-howto] details"));
  check("the spec labels",
        ["Bot Name", "Assigned System", "Test Chat ID (Optional)", "Email addresses",
         "Allowed Chat IDs", "Welcome Message (Optional)", "Telegram Bot Token"]
          .every((l) => !!field(box, l)));
  const adv = box.querySelector("[data-tg-advanced]");
  check("Allowed Chat IDs sits under a collapsed Advanced section",
        adv?.tagName === "DETAILS" && !adv.open
        && adv.querySelector("summary")?.textContent.trim().startsWith("Advanced")
        && !!adv.querySelector("textarea")
        && !box.querySelector("[data-tg-email-help]")?.closest("details"));
  const block = box.querySelector("[data-tg-emails]");
  check("Allowed Email Addresses: the section title, then the notice first",
        block?.firstElementChild?.textContent.trim() === "Allowed Email Addresses"
        && block.children[1] === box.querySelector("[data-tg-email-warn]"));
  const warn = box.querySelector("[data-tg-email-warn]");
  check("...an amber note: icon, Important Security Notice, the bold claim line, trust line",
        warn?.getAttribute("role") === "note" && warn.classList.contains("tg-notice")
        && !!warn.querySelector(".tg-notice-title svg")
        && warn.querySelector(".tg-notice-title")?.textContent.trim() === "Important Security Notice"
        && warn.querySelector(".tg-notice-strong")?.textContent.replace(/\s+/g, " ").trim()
             === "Anyone who knows an email address on this list can claim it before its owner does."
        && [...warn.querySelectorAll("p")].at(-1)?.textContent.trim()
             === "Only add email addresses of people you know and trust.",
        warn?.textContent);
  const area0 = field(box, "Email addresses")?.querySelector("textarea");
  check("...then Email addresses, one per line, a 4-row box with two example lines",
        box.querySelector("[data-tg-email-help]")?.textContent.trim() === "Enter one email address per line."
        && area0?.rows === 4
        && area0?.getAttribute("placeholder") === "name@example.com\nanother@example.com");
  check("...and the save rule as a small hint",
        box.querySelector("[data-tg-email-drop]")?.textContent.replace(/\s+/g, " ").trim()
          === "Invalid email addresses will be removed when you save.");
  const howto = box.querySelector("[data-tg-link-howto]");
  const steps = [...(howto?.querySelectorAll("li") || [])].map((li) => li.textContent.replace(/\s+/g, " ").trim());
  check("the how-to is ONE collapsed disclosure, How people link their email",
        howto?.tagName === "DETAILS" && !howto.open
        && howto.querySelector("summary")?.textContent.trim() === "How people link their email"
        && box.querySelectorAll("[data-tg-link-howto]").length === 1);
  check("...five short steps: /start then the address, group forms, no /start again, linked only, privacy",
        JSON.stringify(steps) === JSON.stringify([
          "A person on the list sends /start to the bot, then sends their email address when the bot asks for it.",
          "In a group, the address must be a reply to the bot's question, /link name@example.com, or @BotName name@example.com.",
          "People who are already linked never need /start again.",
          "In a group, only linked people get answers.",
          "With Telegram's privacy mode on (the default), a bot that is not a group admin only sees commands, "
            + "mentions and replies in a group. Make the bot a group admin with 'Delete messages' if it "
            + "should delete the message that holds the address.",
        ]), JSON.stringify(steps));
  check("...and the old long paragraphs are gone",
        box.querySelector("[data-tg-privacy]")?.closest("details") === howto
        && !block.textContent.includes("so only list the addresses of people you know")
        && !block.textContent.includes("which is why a group claim must be addressed"));
  check("Assigned System offers Not assigned",
        field(box, "Assigned System").querySelector("option")?.textContent === "Not assigned");
  const allow = box.querySelector("[data-tg-allow-help]")?.textContent.replace(/\s+/g, " ") || "";
  check("Allowed Chat IDs: both lists empty denies commands, and outgoing is not affected",
        allow.includes("Leave this list and Allowed Email Addresses empty to deny command access to all chats.")
        && allow.includes("They do not affect where workflow notifications are sent.")
        && allow.includes("normally learned automatically when someone links their email address to the bot")
        && allow.includes("only for special cases"), allow);
  check("...an unknown chat may send /start (bot asks for a listed address); anything else is ignored",
        allow.includes("When Allowed Email Addresses is not empty, a chat that is not set up "
                       + "yet can send /start. The bot then asks for an email address from "
                       + "that list. Anything else from an unknown chat is ignored.")
        && !allow.includes("ignored without a reply"), allow);
  check("blank name says what it really falls back to",
        field(box, "Bot Name").textContent.includes("the test chat ID is used as its name"));
  check("the token keeps its show/hide control and the private warning",
        !!field(box, "Telegram Bot Token")?.querySelector("button")
        && field(box, "Telegram Bot Token").textContent.includes("Keep this token private."));
  check("the form has no invented test button",
        !button(box, "Send Test Message") && !button(box, "Test Chat ID"));

  const save = () => click(button(box, "Save Telegram Bot"));
  await save(); await settle();
  check("no token: Bot Token Required, nothing posted",
        toastText().includes("Bot Token Required") && globalThis.__posts.length === 0);
  await type(field(box, "Telegram Bot Token").querySelector("input"), "nonsense");
  await save(); await settle();
  check("a malformed token: Invalid Bot Token (shape only; saving does not contact Telegram)",
        toastText().includes("Invalid Bot Token") && globalThis.__posts.length === 0);
  await type(field(box, "Telegram Bot Token").querySelector("input"), TOKEN);
  await type(field(box, "Test Chat ID (Optional)").querySelector("input"), "@x");
  await save(); await settle();
  check("a bad test chat ID: Invalid Chat ID",
        toastText().includes("Invalid Chat ID") && globalThis.__posts.length === 0);
  await type(field(box, "Test Chat ID (Optional)").querySelector("input"), "-1001234567890");
  globalThis.__answer["/api/telegram/bots"] = { status: 400, message: `bad ${TOKEN}` };
  await save(); await settle();
  check("a 400: Could Not Save Telegram Bot, never the server text or token",
        globalThis.__posts.length === 1
        && toastText().includes("Could Not Save Telegram Bot")
        && !toastText().includes(TOKEN));
});

const TWO = { default_id: "b1",
  systems: [{ slug: "line_a", name: "Line A" }],
  bots: [
    { id: "b1", name: "Ops", system: "line_a", chat_id: "-100123", allow: [], has_token: true },
    { id: "b2", name: "Spare", system: "", chat_id: "", allow: ["1"], has_token: true }] };
await render(TWO, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("rows say Assigned System, Not assigned and who may send commands",
        t.includes("Assigned System: Line A") && t.includes("Not assigned")
        && t.includes("No one can send commands") && t.includes("1 allowed chat"));
  check("row actions are Send Test Message / Edit Bot / Delete Bot",
        !!button(box, "Send Test Message") && !!button(box, "Edit Bot")
        && !!box.querySelector('[aria-label="Delete Bot Spare"]'));
  globalThis.__answer["/api/telegram/bots/test"] = { status: 400,
    message: "Bad Request: chat not found - Telegram will not say more" };
  await click(button(box, "Send Test Message")); await settle();
  check("a failed test: Test Message Failed in words, not Telegram's text",
        toastText().includes("Test Message Failed")
        && toastText().includes("Telegram could not reach that chat.")
        && !toastText().includes("Bad Request"));
  // Edit the spare bot and pick Line A: the page says Ops is displaced.
  await click([...box.querySelectorAll("button")].filter((b) => b.textContent.trim() === "Edit Bot")[1]);
  await choose(field(box, "Assigned System").querySelector("select"), "line_a");
  check("reassigning says which bot loses the system, before Save",
        box.querySelector("[data-tg-displaced]")?.textContent.replace(/\s+/g, " ").includes(
          "Ops is currently assigned to Line A. Saving assigns this bot instead, and Ops becomes Not assigned."));
});

// -- email claims on the bot rows ----------------------------------------------
const CLAIMS = { default_id: "b1", systems: [],
  bots: [{ id: "b1", name: "Ops", system: "", chat_id: "", allow: ["7"], has_token: true,
           emails: ["ana@example.com", "bo@example.com"], delete_failed: true,
           claims_paused_until: Math.floor(Date.now() / 1000) + 3600,
           claims: [{ email: "ana@example.com", claimed: true, user_id: "111",
                      username: "ana", name: "Ana B", blocked: [] },
                    { email: "bo@example.com", claimed: false, user_id: "", username: "",
                      name: "", blocked: [{ user_id: "4321", username: "",
                                             name: "Ana B (@ana)" }] },
                    { email: "cy@example.com", claimed: true, user_id: "4321",
                      username: "", name: "Ana B (@ana)", blocked: [] }] }],
  // What /api/users answers (the shim serves one object for every GET).
  users: [{ username: "dee", email: "Dee@Example.com", enabled: true },
          { username: "off", email: "off@example.com", enabled: false },
          { username: "nomail", email: "", enabled: true }] };
await render(CLAIMS, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("the row counts addresses and chats",
        t.includes("2 allowed email addresses · 1 allowed chat"), t);
  const rows = [...box.querySelectorAll("[data-tg-claimed-by]")];
  const parts = (el) => ["id", "username", "name"].map(
    (k) => el.querySelector(`[data-tg-claim-${k}]`)?.textContent.replace(/\s+/g, " ").trim() || "");
  check("each address says Unclaimed or Claimed by: ID, username and name as separate pieces",
        t.includes("bo@example.com") && t.includes("Unclaimed") && rows.length === 2
        && JSON.stringify(parts(rows[0])) === JSON.stringify(["ID 111", "@ana", 'name shown as "Ana B"']),
        rows.map((r) => JSON.stringify(parts(r))).join(" | "));
  check("S2-M1 a first name \"Ana B (@ana)\" shows as a NAME with no username, beside its own ID",
        JSON.stringify(parts(rows[1]))
          === JSON.stringify(["ID 4321", "no username", 'name shown as "Ana B (@ana)"']),
        JSON.stringify(parts(rows[1])));
  const blocked = box.querySelector("[data-tg-blocked]");
  check("a removed user is listed as blocked from the address, by ID",
        !!blocked && blocked.textContent.replace(/\s+/g, " ").includes("Blocked from claiming:")
        && blocked.querySelector("[data-tg-claim-id]")?.textContent.trim() === "ID 4321"
        && !!box.querySelector('[aria-label="Allow Telegram user 4321 to claim bo@example.com again"]'));
  check("a paused bot says so, and that linked people are unaffected",
        box.querySelector("[data-tg-paused]")?.textContent.replace(/\s+/g, " ")
          .includes("Email claims on this bot are paused until")
        && box.querySelector("[data-tg-paused]").textContent.includes("People already linked"));
  check("only a claimed address offers Remove",
        !!box.querySelector('[aria-label="Remove claim on ana@example.com"]')
        && !box.querySelector('[aria-label="Remove claim on bo@example.com"]')
        && box.querySelector('[aria-label="Remove claim on ana@example.com"]').textContent.trim() === "Remove");
  check("a group that refused a delete shows the admin hint",
        box.querySelector("[data-tg-delete-hint]")?.textContent.replace(/\s+/g, " ").trim()
          === "Make the bot a group admin with 'Delete messages' to hide email addresses.");
  await click(box.querySelector('[aria-label="Remove claim on ana@example.com"]'));
  await settle();
  const dlg = document.body.textContent.replace(/\s+/g, " ");
  check("Remove asks first, naming who loses access and that the address stays",
        dlg.includes("Remove this claim?")
        && dlg.includes('Telegram user ID 111, username @ana, name shown as "Ana B" will no longer')
        && dlg.includes("cannot claim ana@example.com again until you choose Allow again")
        && dlg.includes("stays on the list") && globalThis.__posts.length === 0, dlg.slice(0, 400));
  await click(button(document.body, "Remove Claim")); await settle();
  check("...and confirming posts the unlink for that bot and address only",
        globalThis.__posts.length === 1
        && globalThis.__posts[0][0] === "/api/telegram/bots/unlink"
        && JSON.stringify(globalThis.__posts[0][1]) === JSON.stringify({ id: "b1", email: "ana@example.com" }),
        JSON.stringify(globalThis.__posts));
  globalThis.__posts = [];
  await click(box.querySelector('[aria-label="Allow Telegram user 4321 to claim bo@example.com again"]'));
  await settle();
  const ask2 = document.body.textContent.replace(/\s+/g, " ");
  check("Allow again asks first, naming the user by ID",
        ask2.includes("Allow this user again?")
        && ask2.includes("Telegram user ID 4321, no username") && globalThis.__posts.length === 0,
        ask2.slice(0, 400));
  await click(button(document.body, "Allow Again")); await settle();
  check("...and confirming posts the unblock for that bot, address and user only",
        globalThis.__posts.length === 1
        && globalThis.__posts[0][0] === "/api/telegram/bots/unblock"
        && JSON.stringify(globalThis.__posts[0][1])
          === JSON.stringify({ id: "b1", email: "bo@example.com", user_id: "4321" }),
        JSON.stringify(globalThis.__posts));
  // Editing: the address list is typed raw and parsed once, on save; the
  // claims are never sent back.
  await click(button(box, "Edit Bot")); await settle();
  const area = field(box, "Email addresses").querySelector("textarea");
  check("the edit form shows the saved addresses one per line",
        area.value === "ana@example.com\nbo@example.com", area.value);
  const pick = box.querySelector("[data-tg-account-pick]");
  check("Add From Accounts offers enabled accounts that have an address",
        !!pick && [...pick.options].map((o) => o.value).join(",") === ",dee@example.com",
        pick && [...pick.options].map((o) => o.value).join(","));
  await choose(pick, "dee@example.com");
  check("...and picking one appends it as a new line",
        field(box, "Email addresses").querySelector("textarea").value
          === "ana@example.com\nbo@example.com\ndee@example.com");
  const setArea = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value").set;
  await m.act(async () => {
    setArea.call(area, "ana@example.com\n\n  Cy@Example.com  \n");
    area.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  globalThis.__posts = [];
  await click(button(box, "Save Telegram Bot")); await settle();
  const sent = (globalThis.__posts[0] || [])[1] || {};
  check("saving sends the typed addresses as a list, blank lines dropped",
        JSON.stringify(sent.emails) === JSON.stringify(["ana@example.com", "Cy@Example.com"])
        && !("claims" in sent) && !("delete_failed" in sent) && !("emails_text" in sent)
        && !("claims_paused_until" in sent),
        JSON.stringify(sent));
  check("...and the chat ID list is untouched by it",
        JSON.stringify(sent.allow) === JSON.stringify(["7"]), JSON.stringify(sent.allow));
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall Telegram copy checks passed");
dom.window.close();
process.exit(0);
