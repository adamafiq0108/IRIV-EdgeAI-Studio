// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// ExprEditor - the formula editor for Custom Formula (analytics.expr) and
// Formula variable (variable.formula) nodes.
//
// Tok Aba's design (2026-10-14). The Expression box is the SINGLE source of
// truth: there is no separate equation builder, and every helper only INSERTS
// text into it. Four ways in:
//   1. click: the Variables and Functions panels and the operator buttons
//      insert at the caret (× and ÷ are shown, * and / go in);
//   2. type the whole formula;
//   3. type part of a name and pick the suggestion (Enter, Tab or a click;
//      Enter is only taken while the suggestion list is open);
//   4. pick a template, which FILLS the box with an editable formula.
//
// Validates as you type against the server's AST whitelist (the rule is DATA,
// never code): POST /api/functions/validate-expr, the same endpoint the
// Functions page uses. That endpoint answers ok / error / the names read; it
// does not evaluate. So the result type is the engine's rule (every formula
// is a number; a comparison gives 1 or 0), and "Now:" is the value this very
// node last PUBLISHED (fn_<fnId>_value in the live values the canvas already
// polls), worked out by the server's own evaluator - nothing is evaluated in
// the browser.
//
// The names an expression uses are what grow the node's input ports
// (exprNames below): unchanged. The chip row under the box shows them; a
// name the validator reports as not published is amber. Colouring inside the
// textarea itself was left out on purpose: an overlay that has to track the
// caret, selection, IME composition and native undo is a source of bugs for
// little gain, and the chips say the same thing.
import { useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import { CircleCheck, CircleX, TriangleAlert, ChevronDown } from "lucide-react";
import { api } from "../api/client.js";
import { InfoHint } from "../components/ui/InfoHint.jsx";
import { WorkflowCtx } from "./nodes.jsx";

//: The operator row: [shown, inserted, spoken]. The rest of the engine's
//: operators sit behind "More" so none is lost.
const OPERATORS = [
  ["+", "+", "plus"], ["−", "-", "minus"], ["×", "*", "times"],
  ["÷", "/", "divided by"], ["%", "%", "modulo"], ["(", "(", "open bracket"],
  [")", ")", "close bracket"], [">", ">", "greater than"], ["==", "==", "equals"],
  ["and", "and", "and"], ["or", "or", "or"],
];
const MORE_OPERATORS = [
  ["<", "<", "less than"], [">=", ">=", "at least"], ["<=", "<=", "at most"],
  ["!=", "!=", "not equal to"], ["not", "not", "not"],
];

//: Every function the engine allows, grouped the way the Formula node groups
//: them (core/services/workflow.py FORMULA_GROUPS). Too many to sit in a row
//: of buttons -- a searchable list is the only readable shape for 30-odd, and
//: the hints are what make them findable by what they DO.
const FUNCTION_GROUPS = ["Arithmetic", "Rounding", "Powers & roots",
                         "Logs & growth", "Trigonometry",
                         "Ratios & scaling", "Choose", "Constants"];
const FUNCTIONS = [
  ["min(a, b)", "Arithmetic", "lowest, smallest"],
  ["max(a, b)", "Arithmetic", "highest, biggest, peak"],
  ["avg(a, b)", "Arithmetic", "mean, average"],
  ["abs(a)", "Arithmetic", "magnitude, drop the minus sign"],
  ["sign(a)", "Arithmetic", "direction: -1, 0 or +1"],
  ["round(a)", "Rounding", "nearest whole number"],
  ["ceil(a)", "Rounding", "always up"],
  ["floor(a)", "Rounding", "always down"],
  ["trunc(a)", "Rounding", "cut the fraction off"],
  ["sqrt(a)", "Powers & roots", "square root"],
  ["cbrt(a)", "Powers & roots", "cube root"],
  ["pow(a, b)", "Powers & roots", "exponent, squared, cubed"],
  ["hypot(a, b)", "Powers & roots", "distance, pythagoras, diagonal"],
  ["ln(a)", "Logs & growth", "natural log, base e"],
  ["log(a, b)", "Logs & growth", "log of a to base b"],
  ["log10(a)", "Logs & growth", "decibels, orders of magnitude"],
  ["log2(a)", "Logs & growth", "doublings"],
  ["exp(a)", "Logs & growth", "exponential growth"],
  ["sin(a)", "Trigonometry", "sine, radians"],
  ["cos(a)", "Trigonometry", "cosine, radians"],
  ["tan(a)", "Trigonometry", "tangent, radians"],
  ["asin(a)", "Trigonometry", "arcsine"],
  ["acos(a)", "Trigonometry", "arccosine"],
  ["atan(a)", "Trigonometry", "arctangent"],
  ["atan2(y, x)", "Trigonometry", "bearing, heading, direction"],
  ["degrees(a)", "Trigonometry", "radians to degrees"],
  ["radians(a)", "Trigonometry", "degrees to radians"],
  ["pct(part, whole)", "Ratios & scaling", "percent, percentage, share"],
  ["scale(v, lo, hi)", "Ratios & scaling", "normalise to 0..1"],
  ["lerp(a, b, t)", "Ratios & scaling", "interpolate, mix, blend"],
  ["clamp(v, lo, hi)", "Ratios & scaling", "limit, cap, bound"],
  // The engine's "if" is called iff: the hint says if, the name stays iff.
  ["iff(cond, a, b)", "Choose", "if this then that, conditional (the engine's if)"],
  ["pi", "Constants", "3.14159…"],
  ["e", "Constants", "2.71828…"],
  ["tau", "Constants", "2 pi"],
].map(([label, group, hint]) => ({ key: label, label, group, hint,
                                   name: label.split("(")[0], call: label.includes("(") }));

//: Every name the ENGINE owns: the function names, the constants, and the
//: word operators. Anything else an expression mentions is a value the
//: operator means to feed in -- which is what gives a Custom Formula one
//: input port per name it refers to.
export const EXPR_RESERVED = new Set([
  ...FUNCTIONS.map((f) => f.key.split("(")[0]),
  "and", "or", "not",
]);

//: The names an expression refers to that the engine does not own, in the
//: order they are first written. One input port each: wiring a value to the
//: port called `capacity` is what makes `capacity` mean that value, whatever
//: the node it came from is called -- so renaming that node afterwards cannot
//: quietly zero a formula.
//:
//: The cap is a safety ceiling, not a design limit. It was 8, and a formula
//: with ten names (ten parking lots, summed) silently lost the last two ports:
//: they never appeared on the node, nothing could be wired to them, and the
//: editor still said "Formula looks valid". The server binds a port to a name
//: with no limit at all, so only this list ever held it back.
export const EXPR_MAX_NAMES = 40;
export function exprNames(expr, max = EXPR_MAX_NAMES) {
  const out = [];
  for (const m of String(expr || "").matchAll(/[A-Za-z_][A-Za-z0-9_]*/g)) {
    const w = m[0];
    if (EXPR_RESERVED.has(w) || out.includes(w)) continue;
    out.push(w);
    if (out.length >= max) break;
  }
  return out;
}

//: Ready-to-use templates, in plain names: each name becomes an input port,
//: and wiring a value to it is what gives it meaning.
export const TEMPLATES = [
  { label: "Violation percentage", expr: "(violation_count / max(object_count, 1)) * 100" },
  { label: "Total",              expr: "a + b" },
  { label: "Difference",         expr: "entry_count - exit_count" },
  { label: "Average of two",     expr: "(a + b) / 2" },
  { label: "Percentage",         expr: "(part / total) * 100" },
  { label: "Occupancy %",        expr: "people_inside / capacity * 100" },
  { label: "Remaining capacity", expr: "capacity - people_inside" },
  { label: "Alert flag",         expr: "iff(violations_now > 0, 1, 0)" },
  { label: "Clamped 0..100",     expr: "clamp(value, 0, 100)" },
];

/** The word the caret is in the middle (or at the end) of: {start, word}. */
export function wordAt(text, caret) {
  const before = String(text || "").slice(0, caret);
  const m = /[A-Za-z_][A-Za-z0-9_]*$/.exec(before);
  return m ? { start: caret - m[0].length, word: m[0] } : null;
}

/** "12.5" for a live number, the raw text for anything else. */
function shown(v) {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(+v.toFixed(4));
  if (typeof v === "boolean") return v ? "1" : "0";
  return String(v);
}

/** A collapsible panel with its own search, whose rows insert text. */
function InsertPanel({ id, title, sub, items, groups = null, editable, onPick, testid }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const query = q.trim().toLowerCase();
  const hit = (it) => !query || [it.label, it.hint, it.name].some(
    (s) => String(s || "").toLowerCase().includes(query));
  const rows = items.filter(hit);
  const ordered = groups
    ? groups.flatMap((g) => rows.filter((r) => r.group === g).map((r, i) => ({ ...r, head: i === 0 ? g : null })))
    : rows;
  return (
    <div className={"expr-panel" + (open ? " is-open" : "")} data-testid={testid}>
      <button type="button" className="expr-panel-head" aria-expanded={open}
              aria-controls={`${id}-body`} onClick={() => setOpen((v) => !v)}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <strong className="small">{title}</strong>
          <span className="muted small" style={{ display: "block" }}>{sub}</span>
        </span>
        <ChevronDown size={16} aria-hidden="true" className="expr-chev" />
      </button>
      {open && (
        <div id={`${id}-body`} className="expr-panel-body">
          <input className="input" value={q} placeholder={`Search ${title.toLowerCase()}…`}
                 aria-label={`Search ${title.toLowerCase()}`}
                 onChange={(e) => setQ(e.target.value)} />
          <ul className="expr-panel-list">
            {ordered.length === 0 && <li className="muted small" style={{ padding: "6px 8px" }}>Nothing matches.</li>}
            {ordered.map((it) => (
              <li key={it.key}>
                {it.head && <div className="expr-panel-group">{it.head}</div>}
                <button type="button" className="expr-panel-row" disabled={!editable}
                        title={it.hint || undefined} onClick={() => onPick(it)}>
                  <code>{it.label}</code>
                  {it.hint && <span className="muted small">{it.hint}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function ExprEditor({ value, onChange, statKeys = [], editable,
                                     labelFor, knownNames = [], wired = [], fnId = null }) {
  const [check, setCheck] = useState({ ok: true, error: null, keys: [] });
  const [pending, setPending] = useState(false);
  const [more, setMore] = useState(false);
  const [suggest, setSuggest] = useState(null);     // {start, word, items, active}
  const timer = useRef(null);
  const areaRef = useRef(null);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const { values } = useContext(WorkflowCtx) || {};
  const text = value || "";

  // Debounced live validation.
  useEffect(() => {
    clearTimeout(timer.current);
    if (!text.trim()) { setCheck({ ok: true, error: null, keys: [] }); setPending(false); return undefined; }
    setPending(true);
    timer.current = setTimeout(() => {
      api.post("/api/functions/validate-expr", { expr: text })
        .then((r) => { setCheck(r); setPending(false); })
        .catch(() => setPending(false));
    }, 300);
    return () => clearTimeout(timer.current);
  }, [text]);

  // Unknown names read as 0 at runtime -- surface which referenced variables
  // are not currently published, the classic silent-zero trap. Names the
  // WIRES provide are known by definition: the compiler substitutes them.
  const missing = (check.keys || []).filter(
    (k) => statKeys.length && !statKeys.includes(k) && !knownNames.includes(k));
  const used = exprNames(text);

  // The Variables panel: the names wired in (with what they are), then the
  // names the formula already uses. A name is a port: wiring a value into it
  // is what gives it meaning, so there is no catalogue of raw stat keys here.
  const variableItems = useMemo(() => {
    const out = [];
    for (const w of wired) {
      if (!w?.name || out.some((o) => o.name === w.name)) continue;
      out.push({ key: `w:${w.name}`, name: w.name, label: w.name,
                 hint: w.label ? `wired in: ${w.label}` : "wired in" });
    }
    for (const n of [...knownNames, ...used]) {
      if (out.some((o) => o.name === n)) continue;
      out.push({ key: `u:${n}`, name: n, label: n,
                 hint: knownNames.includes(n) ? "wired in" : "used in this formula" });
    }
    return out;
  }, [wired, knownNames, used.join(",")]);   // eslint-disable-line react-hooks/exhaustive-deps

  function place(next, caret) {
    onChange(next);
    requestAnimationFrame(() => {
      const el = areaRef.current;
      if (!el) return;
      el.focus();
      el.selectionStart = el.selectionEnd = caret;
    });
  }

  /** Put `ins` in place of the selection (or of [from, to]). */
  function insert(ins, from = null, to = null) {
    const el = areaRef.current;
    const s = from ?? (el ? el.selectionStart ?? text.length : text.length);
    const e = to ?? (el ? el.selectionEnd ?? s : s);
    place(text.slice(0, s) + ins + text.slice(e), s + ins.length);
    setSuggest(null);
  }

  const opText = (op) => (op === "(" || op === ")" ? op : ` ${op} `);
  const pickFunction = (f, from, to) => insert(f.call ? `${f.name}(` : f.name, from, to);

  // Autocomplete: the word at the caret against the variables and functions.
  function refreshSuggest(el) {
    const at = wordAt(el.value, el.selectionStart ?? el.value.length);
    if (!at || !editable) { setSuggest(null); return; }
    const w = at.word.toLowerCase();
    const vars = variableItems.filter((v) => v.name.toLowerCase().startsWith(w) && v.name !== at.word)
      .map((v) => ({ ...v, kind: "variable" }));
    const fns = FUNCTIONS.filter((f) => f.name.toLowerCase().startsWith(w) && f.name !== at.word)
      .map((f) => ({ ...f, kind: "function" }));
    const items = [...vars, ...fns].slice(0, 8);
    setSuggest(items.length ? { ...at, items, active: 0 } : null);
  }

  function accept(item) {
    if (!suggest) return;
    const end = suggest.start + suggest.word.length;
    if (item.kind === "function") pickFunction(item, suggest.start, end);
    else insert(item.name, suggest.start, end);
  }

  function onKeyDown(e) {
    if (!suggest) return;                       // closed: Enter, Tab, arrows are the textarea's
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = suggest.items.length;
      setSuggest({ ...suggest, active: (suggest.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n });
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      accept(suggest.items[suggest.active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setSuggest(null);
    }
  }

  // Click a chip: select that name in the box, so it can be read or retyped.
  function selectName(name) {
    const el = areaRef.current;
    const m = new RegExp(`(^|[^A-Za-z0-9_])(${name})(?![A-Za-z0-9_])`).exec(text);
    if (!el || !m) return;
    const s = m.index + m[1].length;
    el.focus();
    el.setSelectionRange(s, s + name.length);
  }

  const live = fnId != null && values ? values[`fn_${fnId}_value`] : undefined;
  const compares = /(==|!=|>=|<=|[<>]|\band\b|\bor\b|\bnot\b)/.test(text);
  const listId = `expr-sug-${uid}`;

  let status;
  if (!text.trim()) {
    status = <span className="muted small">Type a formula, pick a template, or click a variable, function or operator.</span>;
  } else if (check.error) {
    status = (
      <span className="expr-status is-bad" role="status">
        <CircleX size={15} aria-hidden="true" /> <span>{check.error}</span>
      </span>
    );
  } else {
    status = (
      <span className="expr-status" role="status">
        <span className={missing.length ? "expr-status-line is-warn" : "expr-status-line is-ok"}>
          {missing.length ? <TriangleAlert size={15} aria-hidden="true" />
                          : <CircleCheck size={15} aria-hidden="true" />}
          {missing.length
            ? `Not published yet (reads as 0): ${missing.join(", ")}`
            : pending ? "Checking…" : "Formula looks valid"}
        </span>
        <span className="muted small" data-expr-result="">
          Result type: Number{compares ? " (a comparison gives 1 or 0)" : ""}
          {live !== undefined && live !== null
            ? <> - Now: <b data-expr-now="">{shown(live)}</b></>
            : " - Preview available when input data is available."}
        </span>
      </span>
    );
  }

  return (
    <div className="expr-ed" data-expr-editor="">
      <div className="expr-head">
        <label htmlFor={`expr-${uid}`} className="wf-field-label" style={{ margin: 0 }}>
          Expression
          <InfoHint label="About Expression">
            Maths over the wired-in values, by name: wire an output into this
            node and its name becomes usable here, as an input port. iff(condition,
            then, else) is the engine's if; comparisons give 1 or 0; unknown
            variables read as 0.
          </InfoHint>
        </label>
        <span className="expr-tag">Formula editor</span>
        <select className="input expr-templates" value="" disabled={!editable}
                aria-label="Templates" data-expr-templates=""
                onChange={(e) => {
                  if (!e.target.value) return;
                  place(e.target.value, e.target.value.length);
                }}>
          <option value="">Templates…</option>
          {TEMPLATES.map((t) => (
            <option key={t.label} value={t.expr}>{t.label}: {t.expr}</option>
          ))}
        </select>
      </div>

      <div className={"expr-box" + (check.error ? " is-bad" : "")}>
        <div className="expr-area-wrap">
        <textarea id={`expr-${uid}`} ref={areaRef} className="expr-area" rows={3} value={text}
                  disabled={!editable} spellCheck={false} autoComplete="off"
                  placeholder="e.g. (violation_count / max(object_count, 1)) * 100"
                  role="combobox" aria-autocomplete="list" aria-expanded={!!suggest}
                  aria-controls={listId}
                  aria-activedescendant={suggest ? `${listId}-${suggest.active}` : undefined}
                  aria-invalid={!!check.error}
                  onChange={(e) => { onChange(e.target.value); refreshSuggest(e.target); }}
                  onKeyDown={onKeyDown}
                  onClick={() => setSuggest(null)}
                  onBlur={() => setTimeout(() => setSuggest(null), 150)} />
        {suggest && (
          <ul id={listId} role="listbox" className="expr-suggest" aria-label="Suggestions">
            {suggest.items.map((it, i) => (
              <li key={`${it.kind}-${it.key}`} id={`${listId}-${i}`} role="option"
                  aria-selected={i === suggest.active}
                  className={i === suggest.active ? "is-active" : ""}
                  onMouseDown={(e) => { e.preventDefault(); accept(it); }}>
                <code>{it.kind === "function" ? it.label : it.name}</code>
                <span className="muted small">{it.kind === "function" ? it.hint : (it.hint || "variable")}</span>
              </li>
            ))}
          </ul>
        )}
        </div>
        <div className="expr-vars" data-expr-vars="">
          {used.length ? used.map((n) => (
            <button key={n} type="button" className={"expr-chip" + (missing.includes(n) ? " is-warn" : "")}
                    title={labelFor && labelFor(n) !== n ? labelFor(n) : `Select ${n} in the formula`}
                    onClick={() => selectName(n)}>{n}</button>
          )) : null}
          <span className="muted small">{used.length ? "Variables are highlighted" : "Variables you use appear here"}</span>
        </div>
      </div>

      <div className="expr-ops" role="group" aria-label="Operators">
        {[...OPERATORS, ...(more ? MORE_OPERATORS : [])].map(([show, op, say]) => (
          <button key={op} type="button" className="btn btn-sm expr-op" disabled={!editable}
                  aria-label={`Insert ${say}`} title={`Insert ${op}`} data-op={op}
                  onClick={() => insert(opText(op))}>{show}</button>
        ))}
        <button type="button" className="btn btn-sm expr-op" aria-expanded={more}
                onClick={() => setMore((v) => !v)}>{more ? "Less" : "More"}</button>
      </div>

      <div className="expr-panels">
        <InsertPanel id={`vars-${uid}`} title="Variables" testid="expr-variables"
                     sub="Search and insert available variables"
                     items={variableItems} editable={editable}
                     onPick={(it) => insert(it.name)} />
        <InsertPanel id={`fns-${uid}`} title="Functions" testid="expr-functions"
                     sub="Insert functions such as max(), min(), and if()"
                     items={FUNCTIONS} groups={FUNCTION_GROUPS} editable={editable}
                     onPick={(f) => pickFunction(f)} />
      </div>

      <div className="expr-validity" data-expr-validity="">{status}</div>
    </div>
  );
}
