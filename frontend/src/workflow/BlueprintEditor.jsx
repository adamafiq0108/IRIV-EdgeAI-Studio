// Blueprint editor - the Function Block's nested canvas. Double-clicking a
// Function Block on the main workflow opens this full-screen modal: a second
// ReactFlow where the block's LOGIC is wired from primitive nodes (values,
// arithmetic, compare, if/else, switch, clamp, range). The server compiles
// the blueprint to the same whitelisted expression Custom Formula uses
// (core/services/workflow.py blueprint_to_expr) - so this is authoring UI
// only, no new engine capability.
//
// "Delay" is deliberately NOT a node: expressions are stateless. Spacing
// repeated firings out lives on the block's trigger cooldown (inspector).
import { createContext, memo, useContext, useState } from "react";
import { createPortal } from "react-dom";
import { clickable, useEscape } from "../hooks/use-escape.js";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, addEdge,
  useNodesState, useEdgesState, Handle, Position, SelectionMode,
} from "@xyflow/react";

let _uid = 0;
const mint = () => `b${Date.now().toString(36)}${(++_uid).toString(36)}`;

// type -> {label, color, inputs, hasOut, defaults, hint}
export const BP_SPECS = {
  "bp.stat":    { label: "Value",   color: "#8b5cf6", inputs: [], hasOut: true,
                  defaults: { key: "" }, hint: "a live variable" },
  "bp.const":   { label: "Number",  color: "#8b5cf6", inputs: [], hasOut: true,
                  defaults: { value: 0 }, hint: "a fixed number" },
  "bp.math":    { label: "Math",    color: "#10b981", inputs: ["a", "b"], hasOut: true,
                  defaults: { op: "add" }, hint: "arithmetic" },
  "bp.compare": { label: "Compare", color: "#f59e0b", inputs: ["a", "b"], hasOut: true,
                  defaults: { op: ">" }, hint: "a vs b" },
  "bp.logic":   { label: "Logic",   color: "#f59e0b", inputs: ["a", "b"], hasOut: true,
                  defaults: { op: "and" }, hint: "and / or / not" },
  "bp.if":      { label: "If / Else", color: "#3b82f6", inputs: ["cond", "then", "else"],
                  hasOut: true, defaults: {}, hint: "condition ? then : else" },
  "bp.switch":  { label: "Switch",  color: "#3b82f6", inputs: ["v"], hasOut: true,
                  defaults: { cases: [{ when: 0, then: 0 }], default: 0 },
                  hint: "match a value to results" },
  "bp.clamp":   { label: "Clamp",   color: "#10b981", inputs: ["v", "lo", "hi"],
                  hasOut: true, defaults: {}, hint: "pin v into [lo, hi]" },
  "bp.between": { label: "In Range", color: "#f59e0b", inputs: ["v", "lo", "hi"],
                  hasOut: true, defaults: {}, hint: "1 while lo ≤ v ≤ hi" },
  // Rows of "when the inputs look like this, the answer is that" -- the
  // thing people reach for a spreadsheet to express. Compiles to the same
  // nest of iff()s someone would otherwise wire by hand, which is precisely
  // why it is worth having: the nest is unreadable a week later.
  "bp.table":   { label: "Logic Table", color: "#f59e0b",
                  inputs: ["a", "b", "c"], hasOut: true,
                  defaults: { rows: [{ a: "", a_op: "==", then: 1 }], default: 0 },
                  hint: "rows of conditions -> a result" },
  "bp.out":     { label: "Output",  color: "#ef4444", inputs: ["v"], hasOut: false,
                  defaults: {}, hint: "the block's value" },
};

const MATH_OPS = ["add", "sub", "mul", "div", "mod", "pow", "min", "max",
                  "abs", "round", "sqrt", "ceil", "floor"];
const UNARY = ["abs", "round", "sqrt", "ceil", "floor"];
const CMP_OPS = [">", ">=", "<", "<=", "==", "!="];

const BpCtx = createContext({ update: () => {}, statKeys: [], editable: false });

const inputStyle = { fontSize: 11, padding: "2px 6px", borderRadius: 5,
                     border: "1px solid rgba(128,128,128,.35)",
                     background: "var(--surface2, #14161a)",
                     color: "var(--text, #e8eaed)", maxWidth: 150 };

const BpNode = memo(function BpNode({ id, type, data, selected }) {
  const { update, statKeys, editable } = useContext(BpCtx);
  const spec = BP_SPECS[type];
  const d = data || {};
  const set = (k) => (e) => update(id, { [k]: e.target.value });
  const unary = type === "bp.math" && UNARY.includes(d.op);
  const inputs = type === "bp.math" && unary ? ["a"] : spec.inputs;

  return (
    <div style={{ minWidth: 120, borderRadius: 9, fontSize: 11,
                  background: "var(--surface, #16181d)",
                  border: `1.5px solid ${selected ? "var(--accent, #04bade)" : spec.color + "77"}`,
                  color: "var(--text, #e8eaed)" }}>
      <div style={{ padding: "4px 9px", fontWeight: 600, borderRadius: "9px 9px 0 0",
                    background: spec.color + "22", color: spec.color }}>
        {spec.label}
      </div>
      <div style={{ padding: "6px 9px", display: "grid", gap: 4 }}>
        {type === "bp.stat" && (
          <select className="nodrag" style={inputStyle} value={d.key || ""}
                  disabled={!editable} onChange={set("key")}>
            <option value="">- variable -</option>
            {statKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        )}
        {type === "bp.const" && (
          <input className="nodrag" style={{ ...inputStyle, width: 76 }} type="number"
                 step="any" value={d.value ?? 0} disabled={!editable}
                 onChange={(e) => update(id, { value: +e.target.value })} />
        )}
        {type === "bp.math" && (
          <select className="nodrag" style={inputStyle} value={d.op || "add"}
                  disabled={!editable} onChange={set("op")}>
            {MATH_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        )}
        {type === "bp.compare" && (
          <select className="nodrag" style={inputStyle} value={d.op || ">"}
                  disabled={!editable} onChange={set("op")}>
            {CMP_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        )}
        {type === "bp.logic" && (
          <select className="nodrag" style={inputStyle} value={d.op || "and"}
                  disabled={!editable} onChange={set("op")}>
            <option value="and">and</option><option value="or">or</option>
            <option value="not">not</option>
          </select>
        )}
        {type === "bp.switch" && (
          <div className="nodrag" style={{ display: "grid", gap: 3 }}>
            {(d.cases || []).map((c, i) => (
              <div key={i} style={{ display: "flex", gap: 3, alignItems: "center" }}>
                <input style={{ ...inputStyle, width: 46 }} type="number" step="any"
                       value={c.when ?? 0} disabled={!editable} title="when v equals"
                       onChange={(e) => {
                         const cases = (d.cases || []).map((x, j) =>
                           j === i ? { ...x, when: +e.target.value } : x);
                         update(id, { cases });
                       }} />
                <span style={{ color: "var(--muted-text, #6b6b6b)" }}>→</span>
                <input style={{ ...inputStyle, width: 46 }} type="number" step="any"
                       value={c.then ?? 0} disabled={!editable} title="result"
                       onChange={(e) => {
                         const cases = (d.cases || []).map((x, j) =>
                           j === i ? { ...x, then: +e.target.value } : x);
                         update(id, { cases });
                       }} />
                {editable && (
                  <span style={{ cursor: "pointer", color: "var(--alert-text)" }}
                        onClick={() => update(id, {
                          cases: (d.cases || []).filter((_, j) => j !== i) })}>×</span>
                )}
              </div>
            ))}
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              {editable && (d.cases || []).length < 12 && (
                <span style={{ cursor: "pointer", color: "var(--accent-text, #04bade)" }}
                      onClick={() => update(id, {
                        cases: [...(d.cases || []), { when: 0, then: 0 }] })}>
                  + case
                </span>
              )}
              <span style={{ color: "var(--muted-text, #6b6b6b)" }}>else</span>
              <input style={{ ...inputStyle, width: 46 }} type="number" step="any"
                     value={d.default ?? 0} disabled={!editable}
                     onChange={(e) => update(id, { default: +e.target.value })} />
            </div>
          </div>
        )}
        {type === "bp.table" && (
          <div className="nodrag" style={{ display: "grid", gap: 3 }}>
            {/* One row per rule, read top to bottom, first match wins. A
                BLANK cell means that input is not part of the rule -- which
                is what makes this readable next to a nest of if/else. */}
            {(d.rows || []).map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 2, alignItems: "center" }}>
                {["a", "b", "c"].map((k) => (
                  <span key={k} style={{ display: "flex", gap: 1 }}>
                    <select style={{ ...inputStyle, width: 32, padding: "1px" }}
                            value={r[`${k}_op`] || "=="} disabled={!editable}
                            title={`how to test ${k}`}
                            onChange={(e) => {
                              const rows = (d.rows || []).map((x, j) =>
                                j === i ? { ...x, [`${k}_op`]: e.target.value } : x);
                              update(id, { rows });
                            }}>
                      {CMP_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input style={{ ...inputStyle, width: 34 }} type="number" step="any"
                           value={r[k] ?? ""} disabled={!editable}
                           placeholder={k} title={`${k} - blank means ignore it`}
                           onChange={(e) => {
                             const rows = (d.rows || []).map((x, j) =>
                               j === i ? { ...x, [k]: e.target.value } : x);
                             update(id, { rows });
                           }} />
                  </span>
                ))}
                <span style={{ color: "var(--muted-text, #6b6b6b)" }}>→</span>
                <input style={{ ...inputStyle, width: 40 }} type="number" step="any"
                       value={r.then ?? 0} disabled={!editable} title="result"
                       onChange={(e) => {
                         const rows = (d.rows || []).map((x, j) =>
                           j === i ? { ...x, then: +e.target.value } : x);
                         update(id, { rows });
                       }} />
                {editable && (
                  <span style={{ cursor: "pointer", color: "var(--alert-text)" }}
                        onClick={() => update(id, {
                          rows: (d.rows || []).filter((_, j) => j !== i) })}>×</span>
                )}
              </div>
            ))}
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              {editable && (d.rows || []).length < 24 && (
                <span style={{ cursor: "pointer", color: "var(--accent-text, #04bade)" }}
                      onClick={() => update(id, {
                        rows: [...(d.rows || []), { a: "", a_op: "==", then: 1 }] })}>
                  + row
                </span>
              )}
              <span style={{ color: "var(--muted-text, #6b6b6b)" }}>else</span>
              <input style={{ ...inputStyle, width: 40 }} type="number" step="any"
                     value={d.default ?? 0} disabled={!editable}
                     onChange={(e) => update(id, { default: +e.target.value })} />
            </div>
          </div>
        )}
        {!["bp.stat", "bp.const", "bp.math", "bp.compare", "bp.logic", "bp.switch",
           "bp.table"].includes(type) && (
          <span style={{ color: "var(--muted-text, #6b6b6b)" }}>{spec.hint}</span>
        )}
      </div>

      {inputs.map((h, i) => (
        <div key={h}>
          <Handle type="target" position={Position.Left} id={h}
                  style={{ background: spec.color,
                           top: inputs.length === 1 ? "50%"
                             : `${30 + (i * 44) / (inputs.length - 1)}%` }} />
          <span style={{ position: "absolute", left: 7, fontSize: 8, color: "#999",
                         top: inputs.length === 1 ? "calc(50% - 6px)"
                           : `calc(${30 + (i * 44) / (inputs.length - 1)}% - 6px)` }}>
            {h}
          </span>
        </div>
      ))}
      {spec.hasOut && (
        <Handle type="source" position={Position.Right}
                style={{ background: spec.color }} />
      )}
    </div>
  );
});

const NODE_TYPES = Object.fromEntries(Object.keys(BP_SPECS).map((t) => [t, BpNode]));

const GROUPS = [
  ["Values", ["bp.stat", "bp.const"]],
  ["Arithmetic", ["bp.math", "bp.clamp"]],
  ["Rules", ["bp.compare", "bp.logic", "bp.between"]],
  ["Flow", ["bp.if", "bp.switch", "bp.table", "bp.out"]],
];

function Canvas({ blueprint, statKeys, editable, onSave, onClose }) {
  const [nodes, setNodes, onNodesChange] = useNodesState(
    (blueprint?.nodes || []).length ? blueprint.nodes
      : [{ id: mint(), type: "bp.out", position: { x: 420, y: 140 }, data: {} }]);
  const [edges, setEdges, onEdgesChange] = useEdgesState(blueprint?.edges || []);
  const update = (id, patch) => setNodes((ns) => ns.map(
    (n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));

  // One wire per input: connecting to an occupied input replaces the wire.
  const onConnect = (c) => setEdges((es) => addEdge(c, es.filter(
    (e) => !(e.target === c.target
             && (e.targetHandle || "in") === (c.targetHandle || "in")))));

  function add(type) {
    const spec = BP_SPECS[type];
    setNodes((ns) => [...ns, {
      id: mint(), type,
      position: { x: 80 + (ns.length % 4) * 150, y: 60 + (ns.length % 5) * 70 },
      data: JSON.parse(JSON.stringify(spec.defaults)),
    }]);
  }

  function save() {
    onSave({
      nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })),
      edges: edges.map(({ id, source, target, sourceHandle, targetHandle }) =>
        ({ id, source, target, sourceHandle, targetHandle })),
    });
    onClose();
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "170px 1fr", gap: 10,
                  height: "100%", minHeight: 0 }}>
      <div style={{ overflowY: "auto" }}>
        {GROUPS.map(([title, types]) => (
          <div key={title} style={{ marginBottom: 10 }}>
            <div className="muted small" style={{ fontWeight: 600, marginBottom: 4 }}>
              {title}
            </div>
            {types.map((t) => (
              <div key={t} onClick={() => editable && add(t)}
                   {...clickable(() => add(t), { disabled: !editable })}
                   style={{ padding: "5px 9px", marginBottom: 4, borderRadius: 7,
                            cursor: editable ? "pointer" : "default", fontSize: 12,
                            border: `1px solid ${BP_SPECS[t].color}55`,
                            borderLeft: `3px solid ${BP_SPECS[t].color}` }}>
                <strong>{BP_SPECS[t].label}</strong>
                <div className="muted" style={{ fontSize: 10 }}>{BP_SPECS[t].hint}</div>
              </div>
            ))}
          </div>
        ))}
        <p className="muted small">
          Click to add a node, wire left→right into <strong>Output</strong>.
          Need a delay between firings? That's the block's trigger
          <em> cooldown</em>, back in the inspector.
        </p>
      </div>
      {/* Same mouse scheme as the main canvas: right-drag pans, left-drag
          rubber-bands a selection. */}
      <div style={{ borderRadius: 10, overflow: "hidden", position: "relative",
                    border: "1px solid rgba(128,128,128,.25)" }}
           onContextMenu={(e) => e.preventDefault()}>
        <BpCtx.Provider value={{ update, statKeys, editable }}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
                     onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                     onConnect={onConnect}
                     panOnDrag={[1, 2]} selectionOnDrag
                     selectionMode={SelectionMode.Partial}
                     selectNodesOnDrag={false}
                     nodesDraggable={editable} nodesConnectable={editable}
                     deleteKeyCode={editable ? ["Delete", "Backspace"] : []}
                     fitView proOptions={{ hideAttribution: true }}
                     colorMode={document.documentElement.classList.contains("dark")
                       ? "dark" : "light"}>
            <Background gap={16} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </BpCtx.Provider>
        <div style={{ position: "absolute", top: 10, right: 10, display: "flex",
                      gap: 8, zIndex: 10 }}>
          <button className="btn btn-sm" onClick={onClose}>Cancel</button>
          {editable && (
            <button className="btn btn-sm btn-accent" onClick={save}>
              Save blueprint
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function BlueprintEditor({ open, title, blueprint, statKeys,
                                          editable, onSave, onClose }) {
  const [key, setKey] = useState(0);
  // Escape on the DOCUMENT, not on the backdrop: an onKeyDown there fires
  // only while focus is inside it, which is not where focus is when a dialog
  // has just opened.
  useEscape(open, onClose);
  if (!open) return null;
  // Portalled -- see Dialog in ui/primitives.jsx. This one opens over the
  // workflow canvas, which is transformed by React Flow on every pan and
  // zoom, so it is the clearest case of the trap in the app.
  return createPortal(
    <div style={{ position: "fixed", inset: 0, zIndex: 1000,
                  background: "rgba(0,0,0,.6)", display: "grid",
                  placeItems: "center" }}>
      <div className="panel-card" role="dialog" aria-modal="true"
           style={{ width: "92vw", height: "min(86vh, 900px)",
                    maxHeight: "calc(100vh - 32px)", overflow: "auto",
                    display: "grid",
                    gridTemplateRows: "auto 1fr", gap: 10, padding: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div className="section-title" style={{ margin: 0 }}>
            Blueprint - {title || "Function Block"}
          </div>
          <span className="muted small">
            compiles to a formula on save; errors show on the block node
          </span>
        </div>
        <ReactFlowProvider key={key}>
          <Canvas blueprint={blueprint} statKeys={statKeys} editable={editable}
                  onSave={(bp) => { onSave(bp); setKey((k) => k + 1); }}
                  onClose={onClose} />
        </ReactFlowProvider>
      </div>
    </div>,
    document.body,
  );
}
