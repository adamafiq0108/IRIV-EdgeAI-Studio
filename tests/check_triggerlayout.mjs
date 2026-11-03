// The Trigger inspector's layout (Tok Aba, 2026-10-14):
// frontend/src/workflow/TriggerInspector.jsx, mounted by pages/Workflow.jsx.
//   1. three numbered sections (Trigger identity / When should it fire? /
//      What should happen?) and a collapsed Advanced settings; the name hint
//      says it is shown in alerts
//   2. ONE firing dropdown: Every time / At most once every N seconds / Once
//      per object / When a new object joins (watchdog), each with its plain
//      description under it
//   3. every saved shape opens on the option that matches how it behaves
//      (the same reading the server makes: a rate wins)
//   4. each choice writes only the fields the old two controls wrote
//      (d.rate, d.repeat), nothing renamed; the seconds field appears only for
//      the interval option and writes d.rate_s
//   5. payload duration: Real-time / Re-check every N seconds / For N seconds,
//      the same d.out_hold / d.out_hold_s writes as before
//   6. opt-in rows: ticking reveals the settings beneath, writes the same
//      fields (alert; draw + box_color; zone_color); a set row shows a
//      one-line summary closed; event (alert, payload) and visual (boxes,
//      zone) are separate rows
//   7. Advanced settings (collapsed) holds the duplicate guard, writing
//      d.dup_guard / d.dup_threshold as before
//   8. no em dash; Workflow.jsx mounts it inside the Trigger branch only
//
//   node tests/check_triggerlayout.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".triggerlayout-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "HTMLSelectElement", "Element", "Node",
                 "SVGElement", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
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
      import { act, createElement as h } from "react";
      import TriggerFields, { FIRING, HOLDS, triggerFiring, firingPatch }
        from "${p("src/workflow/TriggerInspector.jsx")}";
      export { act, FIRING, HOLDS, triggerFiring, firingPatch };
      export function mount(el, props) {
        const root = createRoot(el);
        act(() => { root.render(h(TriggerFields, props)); });
        return { unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { act } = m;

// The server's defaults for a new Trigger (core/services/workflow.py).
const NEW = { name: "", alert: false, severity: "warn", message: "", cooldown_s: 30,
              repeat: "cooldown", draw: false, box_color: "", zone_color: "",
              payload_type: "", payload: "", idle_type: "", idle_payload: "",
              rate: "", rate_s: 10, out_hold: "", out_hold_s: 0 };

function render(data, opts, fn) {
  const changes = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, {
    d: { ...NEW, ...data }, editable: true, onChange: (patch) => changes.push(patch),
    node: { id: "t1", type: "output.trigger" }, edges: opts.edges || [],
    oidTracked: !!opts.oidTracked, oidWired: !!opts.oidWired,
    boxZone: opts.boxZone || null, triggerZone: opts.triggerZone || null });
  try { return fn(box, changes); } finally { r.unmount(); box.remove(); }
}
// The two radio-card groups: "Firing" (step 2) and "Output behavior"
// (inside Payload options).
const group = (box, kind) => box.querySelector(`[data-rc^="${kind}-"]`);
const radios = (box, kind) => [...(group(box, kind)?.querySelectorAll("[role=radio]") || [])];
const chosen = (box, kind) => radios(box, kind).find((r) => r.getAttribute("aria-checked") === "true")?.dataset.radio;
const radio = (box, kind, id) => group(box, kind)?.querySelector(`[data-radio="${id}"]`);
const pickRadio = (box, kind, id) => act(() => { radio(box, kind, id).click(); });
const keyOn = (el, k) => act(() => {
  el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
});
const row = (box, id) => box.querySelector(`[data-trg-row="${id}"]`);
const openPayload = (box) => act(() => { row(box, "payload").querySelector(".trg-row-more").click(); });

// 1 ---------------------------------------------------------------------------
render({ name: "Overcrowded" }, {}, (box) => {
  const heads = [...box.querySelectorAll("h4.trg-h")].map((x) => x.textContent);
  check("1. three numbered sections",
        JSON.stringify(heads) === JSON.stringify(["1. Trigger identity",
          "2. When should it fire?", "3. What should happen?"]), JSON.stringify(heads));
  check("...the subtitle and the Output name with its hint (shown in alerts)",
        box.textContent.includes("Create a named event when a condition is met.")
        && box.querySelector("input[placeholder^='e.g. Overcrowded']")?.value === "Overcrowded"
        && box.textContent.includes("Used to identify this trigger in the workflow and shown in alerts."));
  const adv = box.querySelector("details[data-trg-advanced]");
  check("...and Advanced settings, collapsed, with its line",
        !!adv && !adv.open && adv.querySelector("summary").textContent.includes(
          "Deduplication, object tracking, timing intervals, and other technical options."));
  const step2 = box.querySelector("section[aria-labelledby='trg2-t1']");
  check("...step 2 holds only the Firing group (Output behavior is under Payload options)",
        step2.contains(group(box, "fire")) && !step2.querySelector("select")
        && !group(box, "hold") && !box.querySelector("select[data-trg-firing], select[data-trg-hold]"));
});
render({}, {}, (box) => {
  check("a nameless Trigger still says it needs a name, and the field shows the example",
        box.textContent.includes("A Trigger needs a name")
        && box.querySelector("input[placeholder^='e.g. Overcrowded']")?.value === "");
});

// 2 ---------------------------------------------------------------------------
render({}, { oidTracked: true }, (box) => {
  const g = group(box, "fire");
  check("2. Firing is a radiogroup headed Firing, with its line",
        !!g.querySelector("[role=radiogroup]")
        && g.querySelector(".rc-head").textContent
          === "FiringControls how often a trigger event can happen.");
  const rows = radios(box, "fire").map((r) => [r.dataset.radio,
    r.querySelector(".rc-title").textContent, r.querySelector(".rc-about").textContent]);
  check("...four cards with Tok Aba's exact titles and lines",
        JSON.stringify(rows) === JSON.stringify([
          ["every", "Every time", "Fire whenever the condition triggers."],
          ["rate", "At most once every N seconds", "Limit how frequently new trigger events can fire."],
          ["per_object", "Once per object id (OID)", "Fire only once for each tracked object."],
          ["new_object", "When a new object id (OID) detected.", "Fire when a newly detected object enters the tracked set."]]),
        JSON.stringify(rows));
  check("...each with an icon, the chosen one aria-checked and the only Tab stop",
        radios(box, "fire").every((r) => r.querySelector("svg.rc-icon"))
        && chosen(box, "fire") === "every"
        && radios(box, "fire").map((r) => r.tabIndex).join(",") === "0,-1,-1,-1");
});

// 3 ---------------------------------------------------------------------------
const SHAPES = [
  ["a fresh Trigger", {}, "every"],
  ["a timer cooldown alarm", { alert: true, repeat: "cooldown", cooldown_s: 45 }, "every"],
  ["an alarm saved before repeat existed", { alert: true, repeat: undefined }, "every"],
  ["a watchdog (only for a new object)", { alert: true, repeat: "new_object" }, "new_object"],
  ["repeat new_object with no alert (no watchdog at runtime)", { repeat: "new_object" }, "every"],
  ["a rate every N s", { rate: "cooldown", rate_s: 20 }, "rate"],
  ["a rate with a watchdog alarm under it (the rate wins)",
   { alert: true, repeat: "new_object", rate: "cooldown" }, "rate"],
  ["once per object", { rate: "once_per_oid" }, "per_object"],
  ["once per object over a watchdog alarm", { alert: true, repeat: "new_object",
                                              rate: "once_per_oid" }, "per_object"],
];
for (const [what, data, want] of SHAPES) {
  render(data, { oidTracked: true }, (box) => {
    check(`3. ${what} opens on ${want}`,
          chosen(box, "fire") === want && m.triggerFiring({ ...NEW, ...data }) === want,
          chosen(box, "fire"));
  });
}

// 4 ---------------------------------------------------------------------------
const pick = (data, choice) => render(data, { oidTracked: true }, (box, changes) => {
  pickRadio(box, "fire", choice);
  return changes.at(-1);
});
check("4. At most once every N s writes rate cooldown only",
      JSON.stringify(pick({}, "rate")) === '{"rate":"cooldown"}');
check("...Once per object writes rate once_per_oid only",
      JSON.stringify(pick({}, "per_object")) === '{"rate":"once_per_oid"}');
check("...the watchdog writes rate off and repeat new_object",
      JSON.stringify(pick({ alert: true }, "new_object")) === '{"rate":"","repeat":"new_object"}');
check("...Every time from a rate turns the rate off and nothing else",
      JSON.stringify(pick({ rate: "cooldown" }, "every")) === '{"rate":""}');
check("...Every time from a watchdog puts the alarm back on its timer",
      JSON.stringify(pick({ alert: true, repeat: "new_object" }, "every"))
        === '{"rate":"","repeat":"cooldown"}');
check("...and every choice round-trips: written, then read back as itself",
      m.FIRING.every((f) => m.triggerFiring({ ...NEW, alert: true,
                                              ...m.firingPatch(f.id, { ...NEW, alert: true }) }) === f.id));
render({}, { oidTracked: true }, (box, changes) => {
  keyOn(radio(box, "fire", "every"), "ArrowDown");
  check("...the arrow keys move and choose (ArrowDown from Every time)",
        JSON.stringify(changes.at(-1)) === '{"rate":"cooldown"}', JSON.stringify(changes));
  keyOn(radio(box, "fire", "every"), "ArrowUp");
  check("...and wrap (ArrowUp from Every time goes to the last enabled)",
        JSON.stringify(changes.at(-1)) === '{"rate":"once_per_oid"}', JSON.stringify(changes));
});
render({}, { oidTracked: true }, (box) => {
  check("the seconds field is not there for Every time", !box.querySelector("[data-trg-rate-s]"));
});
render({ rate: "cooldown", rate_s: 20 }, { oidTracked: true }, (box, changes) => {
  const s = box.querySelector("[data-trg-rate-s]");
  const item = radio(box, "fire", "rate").closest(".rc-item");
  check("...it appears directly under At most once every N s, holding rate_s",
        s?.value === "20" && item.contains(s) && !radio(box, "fire", "rate").contains(s));
  act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(s, "7");
    s.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  check("...and writes rate_s", JSON.stringify(changes.at(-1)) === '{"rate_s":7}',
        JSON.stringify(changes));
});
render({ rate: "once_per_oid" }, {}, (box) => {
  check("...no seconds for Once per object, and its warning when OID is not ticked",
        !box.querySelector("[data-trg-rate-s]") && box.textContent.includes("needs to know which object"));
});
render({}, { oidTracked: false }, (box, changes) => {
  const per = radio(box, "fire", "per_object");
  const neu = radio(box, "fire", "new_object");
  check("Once per object needs OID on the rule, the watchdog needs Raise an alert, said plainly",
        per.getAttribute("aria-disabled") === "true" && per.querySelector(".rc-reason")?.textContent === "Needs OID on the rule."
        && neu.getAttribute("aria-disabled") === "true" && neu.querySelector(".rc-reason")?.textContent === "Needs Raise an alert.");
  pickRadio(box, "fire", "per_object");
  check("...a disabled card writes nothing when clicked", changes.length === 0, JSON.stringify(changes));
  keyOn(radio(box, "fire", "every"), "ArrowDown");
  keyOn(radio(box, "fire", "every"), "ArrowDown");
  check("...and the arrow keys skip it", changes.every((c) => c.rate !== "once_per_oid"),
        JSON.stringify(changes));
});

// 5 ---------------------------------------------------------------------------
render({}, {}, (box, changes) => {
  openPayload(box);
  const g = group(box, "hold");
  const ticks = [...row(box, "payload").querySelectorAll("label.wf-check")];
  check("5. Output behavior is a radiogroup inside the Payload options row, below the two Send payload ticks",
        !!g && row(box, "payload").contains(g) && !!g.querySelector("[role=radiogroup]")
        && ticks.length === 2 && (ticks[1].compareDocumentPosition(g) & 4) !== 0);
  const rows = radios(box, "hold").map((r) => [r.dataset.radio,
    r.querySelector(".rc-title").textContent, r.querySelector(".rc-about").textContent]);
  check("...headed Output behavior, with Tok Aba's exact titles and lines",
        g.querySelector(".rc-head").textContent
          === "Output behaviorControls how the output behaves after the trigger fires."
        && JSON.stringify(rows) === JSON.stringify([
          ["realtime", "Follow the condition (live)", "Output stays active while the condition is true, then turns off."],
          ["interval", "Re-check every N seconds", "Re-evaluate the condition at a specified interval."],
          ["hold", "Stay active for N seconds", "Keep the output active for a set duration, then turn it off."]])
        && chosen(box, "hold") === "realtime" && !box.querySelector("[data-trg-hold-s]"),
        JSON.stringify(rows));
  pickRadio(box, "hold", "hold");
  check("...the same writes as before", JSON.stringify(changes.at(-1))
        === '{"out_hold":"hold","out_hold_s":10}', JSON.stringify(changes));
});
render({ out_hold: "interval", out_hold_s: 7 }, {}, (box) => {
  const s = box.querySelector("[data-trg-hold-s]");
  check("...a saved Re-check opens the row, its seconds directly under the chosen card",
        chosen(box, "hold") === "interval" && s?.value === "7"
        && radio(box, "hold", "interval").closest(".rc-item").contains(s));
});

// 6 ---------------------------------------------------------------------------
render({ name: "Overcrowded" }, {}, (box, changes) => {
  const ids = [...box.querySelectorAll("[data-trg-row]")].map((r) => r.dataset.trgRow);
  check("6. four opt-in rows, event and visual apart",
        JSON.stringify(ids) === '["alert","boxes","zone","payload"]', JSON.stringify(ids));
  check("...each marked Optional, each with its line",
        [...box.querySelectorAll("[data-trg-row]")].every((r) => r.querySelector(".trg-badge")?.textContent === "Optional")
        && row(box, "alert").textContent.includes("Choose the alert severity and optional message.")
        && row(box, "boxes").textContent.includes("Configure bounding-box colour and zone behavior.")
        && row(box, "payload").textContent.includes("Configure triggered and non-triggered values."));
  check("...unticked, nothing beneath them", !box.querySelector(".trg-row-body"));
  act(() => { row(box, "alert").querySelector("input[type=checkbox]").click(); });
  check("...ticking Raise an alert writes alert only", JSON.stringify(changes.at(-1)) === '{"alert":true}');
  act(() => { row(box, "boxes").querySelector("input[type=checkbox]").click(); });
  check("...ticking Highlight objects writes draw and the default colour",
        JSON.stringify(changes.at(-1)) === '{"draw":true,"box_color":"#FF0000"}');
  act(() => { row(box, "zone").querySelector("input[type=checkbox]").click(); });
  check("...ticking Highlight the zone writes zone_color",
        JSON.stringify(changes.at(-1)) === '{"zone_color":"#FF0000"}');
});
render({ name: "Overcrowded", alert: true, severity: "crit", draw: true, box_color: "#00FF00",
         box_in_zone: true, zone_color: "#0000FF" },
       { boxZone: { name: "Bay" }, triggerZone: { name: "Bay" } }, (box) => {
  const line = (id) => row(box, id).querySelector("[data-trg-line]").textContent;
  check("a set row shows its summary while closed",
        line("alert") === "Alert: critical, 'Overcrowded', again every 30 s"
        && line("boxes") === "Boxes: #00FF00, inside Bay"
        && line("zone") === "Zone: #0000FF, Bay" && !box.querySelector(".trg-row-body"),
        [line("alert"), line("boxes"), line("zone")].join(" | "));
  act(() => { row(box, "alert").querySelector(".trg-row-more").click(); });
  const body = row(box, "alert").querySelector(".trg-row-body");
  check("...opening it reveals its settings beneath it (severity, message, raise again)",
        body && body.textContent.includes("Severity") && body.textContent.includes("Message (optional)")
        && body.textContent.includes("Raise again every (s)"));
});

// 6b. the payload ticks say what they do, and what is sent otherwise --------
render({}, {}, (box) => {
  act(() => { row(box, "payload").querySelector(".trg-row-more").click(); });
  const ticks = [...row(box, "payload").querySelectorAll("label.wf-check")].map((l) => l.textContent);
  check("6b. Send payload when triggered (otherwise 1) / when not triggered (otherwise 0)",
        JSON.stringify(ticks) === JSON.stringify(["Send payload when triggered (otherwise 1)",
                                                  "Send payload when not triggered (otherwise 0)"]),
        JSON.stringify(ticks));
  check("...and the row says the default the same way",
        row(box, "payload").querySelector("[data-trg-line]").textContent
          === "Configure triggered and non-triggered values.");
});
render({ payload_type: "string", payload: "door" }, {}, (box) => {
  check("...a set payload row's line: Sends door when triggered, 0 when not triggered",
        row(box, "payload").querySelector("[data-trg-line]").textContent
          === "Sends door when triggered, 0 when not triggered, live");
});
for (const [data, line] of [
  [{ out_hold: "interval", out_hold_s: 10 }, "Sends 1 when triggered, 0 when not triggered, re-checked every 10 s"],
  [{ out_hold: "hold", out_hold_s: 10 }, "Sends 1 when triggered, 0 when not triggered, stays active for 10 s"],
  [{ out_hold_s: 15 }, "Sends 1 when triggered, 0 when not triggered, stays active for 15 s"]]) {
  render(data, {}, (box) => {
    check(`...a saved duration (${JSON.stringify(data)}) opens the row and its line says it`,
          row(box, "payload").querySelector("[data-trg-line]").textContent === line
          && !!row(box, "payload").querySelector(".trg-row-body [data-rc^='hold-'] [role=radiogroup]"),
          row(box, "payload").querySelector("[data-trg-line]").textContent);
  });
}
render({}, {}, (box) => {
  check("...the default (live, nothing set) keeps the row closed with its description",
        !row(box, "payload").querySelector(".trg-row-body")
        && row(box, "payload").querySelector("[data-trg-line]").textContent
          === "Configure triggered and non-triggered values.");
});

// 7 ---------------------------------------------------------------------------
render({}, {}, (box, changes) => {
  const adv = box.querySelector("details[data-trg-advanced]");
  const guard = [...adv.querySelectorAll("label.wf-check")]
    .find((l) => l.textContent.includes("Skip an object the table already holds"));
  check("7. the duplicate guard sits in Advanced settings", !!guard);
  act(() => { guard.querySelector("input").click(); });
  check("...writing dup_guard as before", JSON.stringify(changes.at(-1)) === '{"dup_guard":true}');
});
render({ dup_guard: true, dup_threshold: 4 }, { edges: [{ target: "t1", targetHandle: "cur_oid" }] }, (box) => {
  check("...with its threshold (timer mode)",
        box.querySelector("input[aria-label='Total difference threshold']")?.value === "4");
});

// 8 ---------------------------------------------------------------------------
const SRC = readFileSync(join(FRONT, "src", "workflow", "TriggerInspector.jsx"), "utf8");
const PAGE = readFileSync(join(FRONT, "src", "pages", "Workflow.jsx"), "utf8");
check("8. no em dash", !SRC.includes("\u2014"));
check("...Workflow.jsx mounts it in the Trigger branch, and the old pair is gone",
      /node\.type === "output\.trigger" && \(\s*<>\s*(\{wiredToSnap && <SnapFrames [^\n]*\}\s*)?<TriggerFields /.test(PAGE)
      && !PAGE.includes("function TriggerOutFields(") && !PAGE.includes("function TriggerGuardFields("));
const NODES = readFileSync(join(FRONT, "src", "workflow", "nodes.jsx"), "utf8");
check("...the node face reads the same fields for its ports",
      NODES.includes("(!!d.alert && d.repeat === \"new_object\" && !d.rate) || oidWired")
      && NODES.includes("(!!d.zone_color || (!!d.draw && !!d.box_in_zone) || zoneWired)")
      && NODES.includes("(!!d.dup_guard || guardWired(\"cur_oid\"))"));

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
