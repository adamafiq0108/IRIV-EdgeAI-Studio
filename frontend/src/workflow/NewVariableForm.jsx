// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The body of "+ New variable", shared by the Variables panel (inline,
// expands in place) and the palette's own dialog (a popup) -- one place for
// the field, so the two triggers can never drift into offering different
// things.
//
// Declaring a variable makes a NAME. Nothing else is asked here: whether it is
// a normal variable or a constant is chosen on the variable itself afterwards
// (Kind), where the value can be typed in too.
import { useState } from "react";

const slug = (t) => String(t || "").trim().toLowerCase()
  .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);

/**
 * onCreate(name, varName) -- the name as typed, and the key it is stored under.
 */
export default function NewVariableForm({ editable = true, autoFocus = true,
                                          onCreate, onCancel }) {
  const [name, setName] = useState("");

  function submit() {
    const trimmed = name.trim();
    // One name types the key too -- a space becomes an underscore, nothing
    // else to fill in separately. Upper or lower case is yours: the name is
    // shown as typed, and the key it is stored under is always lowercase.
    const key = slug(trimmed);
    if (!trimmed || !key) return;
    onCreate(trimmed, key);
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <input className="input" placeholder="name - e.g. total violation"
             value={name} autoFocus={autoFocus}
             onChange={(e) => setName(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && submit()} />
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <button className="btn btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn btn-sm btn-accent" disabled={!editable}
                onClick={submit}>Create</button>
      </div>
    </div>
  );
}
