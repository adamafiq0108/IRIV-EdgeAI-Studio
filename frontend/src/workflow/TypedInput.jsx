// TypedInput -- one value field that says WHAT KIND of value it holds.
//
// A value typed into a box is ambiguous until its type is chosen: "5" is a
// number to a Compare and a word to an MQTT topic, and "true" is a word unless
// something says it is a switch. So the type sits in FRONT of the value, with
// a glyph that can be read at a glance (a-z, 0-9, a switch, braces, a clock),
// and the value field below it changes shape to match: a true/false picker, a
// JSON box that says whether it parses, nothing at all for a timestamp that
// is only taken when it fires.
//
// Built for the Inject node first and kept generic, so the next node that
// needs a typed constant (a Python node's parameters, a Set Variable) takes
// the same control instead of growing its own. The value is always carried as
// TEXT -- one field that round-trips every type -- and the server re-checks it
// with the same rules (core/services/functions.py clean_inject_payload).
import { Braces, Clock, ToggleRight } from "lucide-react";

//: The types, in picker order: glyph (text or icon), picker label, and what a
//: freshly chosen one starts as.
export const TYPED_INPUT_TYPES = {
  string:    { glyph: "a-z", label: "string",     start: "" },
  number:    { glyph: "0-9", label: "number",     start: "0" },
  boolean:   { icon: ToggleRight, label: "true / false", start: "true" },
  json:      { icon: Braces, label: "JSON",       start: "{}" },
  timestamp: { icon: Clock,  label: "timestamp",  start: "" },
};

/** Why `value` is not a valid `type`, or "" when it is. The same sentence
 *  the server gives, so the field and a failed save never disagree. */
export function typedInputError(type, value) {
  const text = value == null ? "" : String(value);
  if (type === "number") {
    if (!text.trim() || !Number.isFinite(Number(text))) {
      return `'${text.slice(0, 40)}' is not a number`;
    }
  }
  if (type === "json") {
    try { JSON.parse(text); } catch (e) {
      return `the JSON does not parse: ${e.message}`;
    }
  }
  if (type === "boolean" && !["true", "false"].includes(text)) {
    return "choose true or false";
  }
  return "";
}

function Glyph({ type }) {
  const t = TYPED_INPUT_TYPES[type] || TYPED_INPUT_TYPES.string;
  const Icon = t.icon;
  return (
    <span aria-hidden className="mono"
          style={{ display: "inline-flex", alignItems: "center",
                   justifyContent: "center", minWidth: 30, height: 28,
                   padding: "0 4px", borderRadius: 6, fontSize: 11,
                   border: "1px solid rgba(128,128,128,.4)",
                   background: "rgba(128,128,128,.10)", flexShrink: 0 }}>
      {Icon ? <Icon size={14} /> : t.glyph}
    </span>
  );
}

/**
 * `types` limits the picker (every type by default). onChange receives
 * ({ type, value }) together, because changing the type usually has to
 * change the value too -- "hello" is no number.
 */
export function TypedInput({ type, value, onChange, types, disabled,
                             label = "Value" }) {
  const offered = (types && types.length ? types
    : Object.keys(TYPED_INPUT_TYPES)).filter((t) => TYPED_INPUT_TYPES[t]);
  const current = TYPED_INPUT_TYPES[type] ? type : offered[0];
  const text = value == null ? "" : String(value);
  const problem = typedInputError(current, text);
  const setType = (next) => {
    // Keep what was typed when it is still valid for the new type; start the
    // new type's own way otherwise, so a switch never lands on an error.
    const keep = next !== "timestamp" && !typedInputError(next, text)
      && !(next === "string" && current === "timestamp");
    onChange({ type: next, value: keep ? text : TYPED_INPUT_TYPES[next].start });
  };
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <div className="wf-row" style={{ alignItems: "center", gap: 6 }}>
        <Glyph type={current} />
        <select className="input" style={{ flex: 1, minWidth: 0 }}
                value={current} disabled={disabled}
                aria-label={`${label} type`}
                onChange={(e) => setType(e.target.value)}>
          {offered.map((t) => (
            <option key={t} value={t}>
              {TYPED_INPUT_TYPES[t].glyph ? `${TYPED_INPUT_TYPES[t].glyph}  ` : ""}
              {TYPED_INPUT_TYPES[t].label}
            </option>
          ))}
        </select>
      </div>
      {current === "boolean" && (
        <select className="input" value={text === "false" ? "false" : "true"}
                disabled={disabled} aria-label={label}
                onChange={(e) => onChange({ type: current, value: e.target.value })}>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      )}
      {(current === "string" || current === "number") && (
        <input className="input" value={text} disabled={disabled}
               aria-label={label} inputMode={current === "number" ? "decimal" : undefined}
               onChange={(e) => onChange({ type: current, value: e.target.value })} />
      )}
      {current === "json" && (
        // A box, not a line: JSON worth typing is usually more than one key.
        <textarea className="input mono" rows={4} value={text}
                  disabled={disabled} aria-label={label} spellCheck={false}
                  onChange={(e) => onChange({ type: current, value: e.target.value })} />
      )}
      {current === "timestamp" && (
        <span className="muted small">
          milliseconds since 1970, taken at the moment it fires
        </span>
      )}
      {problem && current !== "timestamp" && (
        <span className="small c-warn" role="alert">{problem}</span>
      )}
    </div>
  );
}
