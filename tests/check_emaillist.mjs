// Email recipients as a list, one address per row (Tok Aba, 2026-10-14: "for
// Email To, instead of using comma, please make a button Add").
//
// Pinned here: the stored value is still ONE ", " string (parse/serialize
// round trip, old strings open as rows, nothing written on open, canonical
// strings with 0, 1 and 3 recipients come back identical so the compile is
// unchanged); Add appends a row and focuses it; Enter on a valid row adds the
// next; remove (hidden on a lone empty row); a pasted list or a typed comma
// splits into rows; empty rows and repeats (case-insensitive) dropped on
// write; a calm "Not an email address" under a bad row that never blocks
// typing; the "N of 10" counter on the workflow node; aria labels; where the
// control is used; no em dash.
//
//   node tests/check_emaillist.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".emaillist-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Event",
                 "KeyboardEvent", "MouseEvent", "HTMLInputElement",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h, useState } from "react";
import EmailListField, { parseEmails, serializeEmails, splitPasted, isEmail }
  from "${p("src/components/EmailListField.jsx")}";
export { act, parseEmails, serializeEmails, splitPasted, isEmail };
export function mount(el, initial, props = {}) {
  const log = [];
  function Host() {
    const [v, setV] = useState(initial);
    return h(EmailListField, { value: v, label: "To", ...props,
      onChange: (s) => { log.push(s); setV(s); } });
  }
  const root = createRoot(el);
  act(() => { root.render(h(Host)); });
  return { log, unmount: () => act(() => root.unmount()) };
}
`;
await build({
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);

// -- the pure half: parse / serialize ----------------------------------------
check("an old string opens as rows (commas, semicolons, line breaks)",
      JSON.stringify(m.parseEmails("a@x.com, b@y.com;c@z.com\nd@w.com"))
        === JSON.stringify(["a@x.com", "b@y.com", "c@z.com", "d@w.com"]));
check("...an empty string is one empty row",
      JSON.stringify(m.parseEmails("")) === JSON.stringify([""])
      && JSON.stringify(m.parseEmails(undefined)) === JSON.stringify([""]));
check("...a named address stays one row",
      JSON.stringify(m.parseEmails("Ana B <ana@x.com>, b@y.com"))
        === JSON.stringify(["Ana B <ana@x.com>", "b@y.com"]));
check("writing back: trimmed, empties and repeats (any case) dropped, ', ' joined",
      m.serializeEmails([" a@x.com ", "", "B@y.com", "b@Y.com", "  ", "c@z.com"])
        === "a@x.com, B@y.com, c@z.com");
check("...the domain is not lower-cased (the old field never did)",
      m.serializeEmails(["Ops@Plant.MY"]) === "Ops@Plant.MY");
check("...an invalid row is still written (the server decides)",
      m.serializeEmails(["a@x.com", "nonsense"]) === "a@x.com, nonsense");
for (const s of ["", "a@x.com", "a@x.com, b@y.com, c@z.com"]) {
  check(`golden: ${s ? s.split(",").length : 0} recipient(s) round-trip unchanged`,
        m.serializeEmails(m.parseEmails(s)) === s, m.serializeEmails(m.parseEmails(s)));
}
check("a pasted list splits on commas, semicolons, spaces and line breaks",
      JSON.stringify(m.splitPasted("a@x.com, b@y.com;c@z.com d@w.com\ne@v.com"))
        === JSON.stringify(["a@x.com", "b@y.com", "c@z.com", "d@w.com", "e@v.com"]));
check("isEmail: bare and named addresses yes, junk no",
      m.isEmail("a@x.com") && m.isEmail("Ana <a@x.com>") && !m.isEmail("a@x")
      && !m.isEmail("nonsense") && !m.isEmail("@x.com") && !m.isEmail("a b@x.com"));

// -- the control -----------------------------------------------------------
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 10)); });
const inputs = (box) => [...box.querySelectorAll("input")];
async function type(input, value) {
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await m.act(async () => {
    set.call(input, value);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
const click = (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});
const key = (el, k) => m.act(async () => {
  el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
});
const blur = (el) => m.act(async () => {
  el.dispatchEvent(new window.FocusEvent("focusout", { bubbles: true }));
});
const focus = (el) => m.act(async () => { el.focus(); });
async function paste(el, text) {
  const ev = new window.Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "clipboardData", { value: { getData: () => text } });
  await m.act(async () => { el.dispatchEvent(ev); });
  return ev.defaultPrevented;
}
async function render(initial, props, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, initial, props);
  await settle();
  try { return await fn(box, r.log); } finally { r.unmount(); box.remove(); }
}

await render("a@x.com,  b@y.com", {}, async (box, log) => {
  check("an old string opens as one input per address",
        inputs(box).map((i) => i.value).join("|") === "a@x.com|b@y.com");
  check("...and opening writes nothing back", log.length === 0, JSON.stringify(log));
  check("aria labels: the group, each address, each remove, Add",
        box.querySelector('[role="group"]')?.getAttribute("aria-label") === "To"
        && !!box.querySelector('input[aria-label="Email address 1"]')
        && !!box.querySelector('input[aria-label="Email address 2"]')
        && !!box.querySelector('button[aria-label="Remove email address 2"]')
        && !!box.querySelector('button[aria-label="Add email"]'));
  check("inputs: inputMode email, autocomplete off",
        inputs(box).every((i) => i.getAttribute("inputmode") === "email"
                                 && i.getAttribute("autocomplete") === "off"));
  const add = box.querySelector('button[aria-label="Add email"]');
  check("Add is a real button with the words Add email",
        add.type === "button" && add.textContent.trim() === "Add email");
  await click(add); await settle();
  check("Add appends an empty row and focuses it",
        inputs(box).length === 3 && document.activeElement === inputs(box)[2]
        && inputs(box)[2].value === "");
  check("...an empty row writes nothing new", log.length === 0 || log.at(-1) === "a@x.com, b@y.com",
        JSON.stringify(log));
  await type(inputs(box)[2], "c@z.com"); await settle();
  check("typing writes the same ', ' string", log.at(-1) === "a@x.com, b@y.com, c@z.com",
        JSON.stringify(log));
  await key(inputs(box)[2], "Enter"); await settle();
  check("Enter on a valid row adds the next and focuses it",
        inputs(box).length === 4 && document.activeElement === inputs(box)[3]);
  await type(inputs(box)[3], "B@Y.com"); await settle();
  check("a repeat (any case) is dropped when written",
        log.at(-1) === "a@x.com, b@y.com, c@z.com", JSON.stringify(log.at(-1)));
  await click(box.querySelector('button[aria-label="Remove email address 1"]')); await settle();
  check("remove drops that row",
        inputs(box).map((i) => i.value).join("|") === "b@y.com|c@z.com|B@Y.com"
        && log.at(-1) === "b@y.com, c@z.com", JSON.stringify(log.at(-1)));
});

await render("", {}, async (box, log) => {
  check("an empty value is one empty row, without a remove mark",
        inputs(box).length === 1 && !box.querySelector('button[aria-label^="Remove"]'));
  await focus(inputs(box)[0]);
  await type(inputs(box)[0], "nonsense"); await settle();
  check("a bad address is never blocked while typing, and no hint yet on the active row",
        inputs(box)[0].value === "nonsense" && !box.querySelector("[data-email-bad]")
        && log.at(-1) === "nonsense");
  await key(inputs(box)[0], "Enter"); await settle();
  check("Enter on an invalid row adds nothing and shows the hint",
        inputs(box).length === 1
        && box.querySelector("[data-email-bad]")?.textContent.trim() === "Not an email address"
        && inputs(box)[0].getAttribute("aria-invalid") === "true");
  check("...a lone filled row has a remove mark", !!box.querySelector('button[aria-label="Remove email address 1"]'));
  await click(box.querySelector('button[aria-label="Remove email address 1"]')); await settle();
  check("...which clears it back to one empty row",
        inputs(box).length === 1 && inputs(box)[0].value === "" && log.at(-1) === "");
  const took = await paste(inputs(box)[0], "a@x.com, b@y.com;c@z.com\nA@X.com");
  await settle();
  check("a pasted list splits into rows",
        took && inputs(box).map((i) => i.value).join("|") === "a@x.com|b@y.com|c@z.com|A@X.com"
        && log.at(-1) === "a@x.com, b@y.com, c@z.com", JSON.stringify(log.at(-1)));
  const single = await paste(inputs(box)[0], "just-one@x.com");
  check("...a single pasted address is left to the browser", !single);
  await type(inputs(box)[1], "b@y.com,"); await settle();
  check("a typed comma starts a new row and focuses it",
        inputs(box)[1].value === "b@y.com" && inputs(box)[2].value === ""
        && document.activeElement === inputs(box)[2]);
});

await render("a@x.com, nonsense", {}, async (box) => {
  check("an old invalid address shows the hint on open",
        box.querySelectorAll("[data-email-bad]").length === 1
        && box.querySelector('[data-email-row="1"] [data-email-bad]'));
});

await render(Array.from({ length: 11 }, (_, i) => `u${i}@x.com`).join(", "), { max: 10 },
  async (box) => {
    check("over the server's cap: N of max and who gets it",
          box.querySelector("[data-email-count]")?.textContent.trim()
            === "11 of 10 - only the first 10 get the message"
          && box.querySelector("[data-email-count]").classList.contains("over"));
  });
await render("a@x.com, b@y.com", { max: 10 }, async (box) => {
  check("...under it a quiet counter", box.querySelector("[data-email-count]")?.textContent.trim() === "2 of 10"
        && !box.querySelector("[data-email-count]").classList.contains("over"));
});
await render("a@x.com", { disabled: true }, async (box) => {
  check("read-only: inputs disabled, no Add, no remove",
        inputs(box).every((i) => i.disabled) && !box.querySelector("button"));
});

// -- where it is used ----------------------------------------------------------
const src = (rel) => readFileSync(join(FRONT, "src", rel), "utf8");
const wf = src("pages/Workflow.jsx");
const emailPart = wf.slice(wf.indexOf('node.type === "output.email" && (<>'));
check("the workflow Email node uses the list, cap 10, writing d.to",
      /<EmailListField label="To" value=\{d\.to \|\| ""\}[\s\S]{0,120}max=\{10\} onChange=\{\(to\) => onChange\(\{ to \}\)\}/
        .test(emailPart.slice(0, 1200)));
check("...and the old comma box is gone",
      !wf.includes("someone@example.com, other@"));
const fd = src("components/EmailFootageDialog.jsx");
check("the footage email dialog uses the list for To",
      fd.includes('<EmailListField label="To" value={addr} onChange={setAddr} />')
      && !fd.includes("separated by commas") && !fd.includes("someone@example.com, other@"));
const comp = src("components/EmailListField.jsx");
check("no em dash in the control, its uses or this check",
      ![comp, readFileSync(fileURLToPath(import.meta.url), "utf8")].some((t) => t.includes("\u2014")));

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall email list checks passed");
dom.window.close();
process.exit(0);
