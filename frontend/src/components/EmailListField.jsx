// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A list of email recipients, one address per row (Tok Aba, 2026-10-14: "for
// Email To, instead of using comma, please make a button Add so the user can
// add another email").
//
// STORAGE DOES NOT CHANGE. Every caller still holds one comma-separated
// string ("a@x.com, b@y.com"): the workflow Email node's d.to, the footage
// dialog's address. The compiler, the app, templates and the test-send route
// all read that string, so this control only changes how it is typed:
//   - opening parses the string into rows (commas, semicolons and line breaks
//     between; nothing else, so an old "Ana <ana@x.com>" stays one row);
//   - nothing is written back until the user edits, so an old node opened and
//     left alone keeps its exact text;
//   - an edit writes the rows back trimmed, empty rows and repeats
//     (case-insensitive) dropped, joined with ", ".
// An address that does not look right gets a calm hint under its row, but is
// still written back: the server's own checks decide what is refused, and
// typing is never blocked.

import { useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";

/** Between addresses in a stored string. */
const STORED_SEP = /[,;\r\n]+/;
/** Between addresses in pasted or typed text: also any space. */
const PASTE_SEP = /[,;\s]+/;
const BARE = /^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$/;
const NAMED = /^[^<>]*<\s*([^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+)\s*>$/;

/** Does this look like an email address ("a@x.com" or "Ana <a@x.com>")? */
export function isEmail(text) {
  const t = String(text || "").trim();
  return BARE.test(t) || NAMED.test(t);
}

/** The stored string as rows. Always at least one row (an empty one). */
export function parseEmails(value) {
  const rows = String(value || "").split(STORED_SEP)
    .map((s) => s.trim()).filter(Boolean);
  return rows.length ? rows : [""];
}

/** Pasted text as addresses: commas, semicolons, spaces or line breaks. */
export function splitPasted(text) {
  return String(text || "").split(PASTE_SEP).map((s) => s.trim()).filter(Boolean);
}

/** Rows back to the stored string: trimmed, no empties, no repeats. */
export function serializeEmails(rows) {
  const seen = new Set();
  const out = [];
  for (const r of rows || []) {
    const t = String(r || "").trim();
    const key = t.toLowerCase();
    if (!t || seen.has(key)) continue;
    seen.add(key);
    out.push(t);
  }
  return out.join(", ");
}

/**
 * value     the stored comma-separated string
 * onChange  called with the new string, only when the user edits
 * max       the server's recipient limit, if there is one (shows "N of max")
 * label     names the group for screen readers ("To")
 */
export default function EmailListField({ value, onChange, disabled = false,
                                         max = 0, label = "Email addresses",
                                         placeholder = "name@example.com" }) {
  const [rows, setRows] = useState(() => parseEmails(value));
  const [focused, setFocused] = useState(-1);
  const [touched, setTouched] = useState(() => new Set());
  const inputs = useRef([]);
  const focusNext = useRef(-1);
  // The string this control last wrote. A value from OUTSIDE (another node
  // selected, a reload) re-parses; our own echo does not, or an empty row
  // just added would vanish the moment it was written back.
  const wrote = useRef(value);

  useEffect(() => {
    if ((value || "") === (wrote.current || "")) return;
    wrote.current = value;
    setRows(parseEmails(value));
    setTouched(new Set());
  }, [value]);

  useEffect(() => {
    if (focusNext.current < 0) return;
    const el = inputs.current[focusNext.current];
    focusNext.current = -1;
    if (el) el.focus();
  });

  const commit = (next, focusAt = -1) => {
    const list = next.length ? next : [""];
    setRows(list);
    if (focusAt >= 0) focusNext.current = focusAt;
    const s = serializeEmails(list);
    if (s !== (value || "")) {
      wrote.current = s;
      onChange?.(s);
    }
  };

  const setRow = (i, text) => {
    // A comma or semicolon typed in a row starts the next one.
    if (/[,;]/.test(text)) {
      const parts = text.split(/[,;]/).map((s) => s.trim());
      const next = [...rows];
      next.splice(i, 1, ...parts);
      commit(next, i + parts.length - 1);
      return;
    }
    const next = [...rows];
    next[i] = text;
    commit(next);
  };

  const add = (after = rows.length - 1) => {
    const next = [...rows];
    next.splice(after + 1, 0, "");
    commit(next, after + 1);
  };

  const remove = (i) => {
    if (rows.length === 1) { commit([""], 0); return; }
    const next = rows.filter((_, k) => k !== i);
    setTouched(new Set());
    commit(next, Math.max(0, i - 1));
  };

  const onPaste = (i) => (e) => {
    const text = e.clipboardData?.getData("text") || "";
    const parts = splitPasted(text);
    if (parts.length < 2) return;
    e.preventDefault();
    const next = [...rows];
    const keep = String(next[i] || "").trim() ? [next[i]] : [];
    next.splice(i, 1, ...keep, ...parts);
    commit(next, i + keep.length + parts.length - 1);
  };

  const onKeyDown = (i) => (e) => {
    if (e.key !== "Enter") return;
    // Never submits the form round it: Enter here means "next address".
    e.preventDefault();
    if (isEmail(rows[i])) {
      if (i + 1 < rows.length && !String(rows[i + 1] || "").trim()) {
        focusNext.current = i + 1;
        setRows([...rows]);
      } else add(i);
    } else {
      setTouched((t) => new Set(t).add(i));
    }
  };

  const filled = rows.filter((r) => String(r || "").trim()).length;
  const over = max > 0 && filled > max;

  return (
    <div className="email-list" role="group" aria-label={label}
         data-email-list="">
      {rows.map((r, i) => {
        const bad = !!String(r || "").trim() && !isEmail(r)
          && (focused !== i || touched.has(i));
        const lone = rows.length === 1 && !String(r || "").trim();
        return (
          <div key={i} className="email-list-row" data-email-row={i}>
            <div className="email-list-line">
              <input className="input email-list-input" type="text"
                     inputMode="email" autoComplete="off" spellCheck={false}
                     value={r} disabled={disabled}
                     placeholder={i === 0 ? placeholder : ""}
                     aria-label={`Email address ${i + 1}`}
                     aria-invalid={bad || undefined}
                     ref={(el) => { inputs.current[i] = el; }}
                     onChange={(e) => setRow(i, e.target.value)}
                     onPaste={onPaste(i)}
                     onKeyDown={onKeyDown(i)}
                     onFocus={() => setFocused(i)}
                     onBlur={() => {
                       setFocused(-1);
                       if (String(r || "").trim()) setTouched((t) => new Set(t).add(i));
                     }} />
              {!disabled && !lone && (
                <button type="button" className="email-list-remove"
                        aria-label={`Remove email address ${i + 1}`}
                        title="Remove" onClick={() => remove(i)}>
                  <X size={14} aria-hidden="true" />
                </button>
              )}
            </div>
            {bad && (
              <span className="email-list-hint" data-email-bad="">
                Not an email address
              </span>
            )}
          </div>
        );
      })}
      <div className="email-list-foot">
        {!disabled && (
          <button type="button" className="btn btn-sm btn-ghost btn-ic email-list-add"
                  aria-label="Add email" onClick={() => add()}>
            <Plus size={13} aria-hidden="true" /> Add email
          </button>
        )}
        {max > 0 && filled > 1 && (
          <span className={`email-list-count${over ? " over" : ""}`}
                data-email-count="">
            {over ? `${filled} of ${max} - only the first ${max} get the message`
                  : `${filled} of ${max}`}
          </span>
        )}
      </div>
    </div>
  );
}
