// VariablesPanel - the workflow's variable manager, opened from the header.
//
// The declaration/constant form itself lives in NewVariableForm.jsx, shared
// with the palette's own "+ New variable…" dialog so the two triggers can
// never drift into offering different things.
import { useMemo, useState } from "react";
import NewVariableForm from "./NewVariableForm.jsx";

const slug = (t) => String(t || "").trim().toLowerCase()
  .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);

export default function VariablesPanel({ open, onClose, values, statKeys, nodes,
                                         editable, customVars, onCreateVar,
                                         onDeleteVar, onSelectNode, labelFor }) {
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const query = q.trim().toLowerCase();

  // Where each declared variable is used on the canvas, so a row can jump to
  // its node -- a Variable node reading or storing it (variable.ref), or a
  // constant's own node (variable.number, keyed by its fnId == the slug).
  const nodeByVar = useMemo(() => {
    const map = {};
    for (const n of nodes) {
      const d = n.data || {};
      if (n.type === "variable.ref"
          && (slug(d.name) || String(d.key || "").replace(/^fn_|_value$/g, ""))) {
        const key = slug(d.name);
        if (key && !map[key]) map[key] = n;
      } else if (n.type === "variable.number" && slug(d.fnId)) {
        map[slug(d.fnId)] = n;
      }
    }
    return map;
  }, [nodes]);

  const declared = (customVars || []).filter((v) =>
    !query || v.name.toLowerCase().includes(query) || v.var.includes(query));
  const declaredKeys = new Set((customVars || []).map((v) => `fn_${v.var}_value`));
  // Search matches the friendly label too -- "Zone 1" finds the key even
  // when the key itself is cam_..._zone_zone_msf..., which nobody types.
  const published = (statKeys.length ? statKeys : Object.keys(values))
    .filter((k) => !declaredKeys.has(k))
    .filter((k) => !query || k.toLowerCase().includes(query)
      || String(labelFor ? labelFor(k) : k).toLowerCase().includes(query));

  if (!open) return null;
  return (
    <div style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 300, zIndex: 15,
                  background: "var(--surface, #16181d)", borderLeft: "1px solid rgba(128,128,128,.3)",
                  display: "flex", flexDirection: "column", boxShadow: "-8px 0 24px rgba(0,0,0,.25)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", padding: 10,
                    borderBottom: "1px solid rgba(128,128,128,.2)" }}>
        <strong style={{ fontSize: 13 }}>Variables</strong>
        <div style={{ flex: 1 }} />
        <button className="btn btn-sm" disabled={!editable}
                onClick={() => setCreating((c) => !c)}>+ New variable</button>
        <button className="btn btn-sm" onClick={onClose}>×</button>
      </div>

      {creating && (
        <div style={{ padding: 10, borderBottom: "1px solid rgba(128,128,128,.2)" }}>
          <NewVariableForm editable={editable}
                          onCreate={(name, varName, constOpts) => {
                            onCreateVar(name, varName, constOpts);
                            setCreating(false);
                          }}
                          onCancel={() => setCreating(false)} />
        </div>
      )}

      <div style={{ padding: 10, borderBottom: "1px solid rgba(128,128,128,.15)" }}>
        <input className="input" placeholder="Search variables…" value={q}
               onChange={(e) => setQ(e.target.value)} />
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 8 }}>
        {declared.length > 0 && (
          <div className="muted small" style={{ padding: "2px 8px 4px", fontWeight: 600 }}>
            Declared
          </div>
        )}
        {declared.map((v) => {
          const key = `fn_${v.var}_value`;
          const live = values[key];
          const node = nodeByVar[v.var];
          return (
            // The raw key (fn_..._value, or camera-scoped and far worse) is
            // in `title` for anyone who needs to match it against a config
            // file -- not a second visible line under a name the operator
            // already typed. One name is what was declared; the key is an
            // implementation detail, not a second name to read.
            <div key={v.var} title={key}
                 onClick={() => node && onSelectNode(node.id)}
                 style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 8px", borderRadius: 7, marginBottom: 2,
                          cursor: node ? "pointer" : "default",
                          border: "1px solid rgba(139,92,246,.35)" }}>
              <div style={{ minWidth: 0, flex: 1, fontSize: 12, fontWeight: 600,
                           overflow: "hidden", textOverflow: "ellipsis",
                           whiteSpace: "nowrap" }}>
                {v.name}
              </div>
              <strong style={{ fontSize: 13, color: "var(--accent-text, #04bade)", flexShrink: 0 }}>
                {live === undefined ? "-"
                  : typeof live === "number" ? Math.round(live * 100) / 100 : String(live)}
              </strong>
              {editable && (
                <button className="btn btn-sm" title="Delete this variable name"
                        style={{ flexShrink: 0 }}
                        onClick={(e) => { e.stopPropagation(); onDeleteVar(v.var); }}>×</button>
              )}
            </div>
          );
        })}

        {published.length > 0 && (
          <div className="muted small" style={{ padding: "8px 8px 4px", fontWeight: 600 }}>
            Published by the pipeline
          </div>
        )}
        {published.map((k) => {
          const label = labelFor ? labelFor(k) : k;
          return (
            <div key={k} title={k}
                 style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 8px", borderRadius: 7, marginBottom: 2 }}>
              {/* The raw key was a second line here (cam_lrcp_u3_imx307__…),
                  which is exactly the wall of noise the label exists to
                  replace. It is still on the row -- as `title`, above -- for
                  whoever needs to match it against a config file. */}
              <div style={{ minWidth: 0, flex: 1, fontSize: 12, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {label}
              </div>
              {values[k] !== undefined && (
                <strong style={{ fontSize: 13, color: "var(--accent-text, #04bade)", flexShrink: 0 }}>
                  {typeof values[k] === "number"
                    ? Math.round(values[k] * 100) / 100 : String(values[k])}
                </strong>
              )}
            </div>
          );
        })}

        {!declared.length && !published.length && (
          <p className="muted small" style={{ padding: 8 }}>
            Nothing matches. Declare a variable above, or wait for the
            detection app to publish values.
          </p>
        )}
      </div>

      <p className="muted small" style={{ padding: 10, borderTop: "1px solid rgba(128,128,128,.15)",
                                          margin: 0 }}>
        Empty until wired, unless it's a Constant.
      </p>
    </div>
  );
}
