// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A small, still picture of workflow nodes wired together, drawn by the canvas's
// own node components inside its own context, on the canvas's dotted ground.
// The Node Guideline page uses it for a node on its own, for a pair of nodes, and
// for the build-it-step-by-step slider (workflow/GuideFlow.jsx).
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import * as XYF from "@xyflow/react";
import { Background, ReactFlow, ReactFlowProvider } from "@xyflow/react";
// The hooks come off the namespace so a test harness that stands in for React Flow with a few
// components still builds this file; they are only called when a diagram is drawn.
const { useNodesInitialized, useReactFlow, useStore } = XYF;
import "@xyflow/react/dist/style.css";
import { WorkflowCtx } from "./nodes.jsx";
import { useDarkClass } from "../hooks/use-theme.js";

/** What a node is drawn with in a diagram: its defaults, named. */
export const sampleData = (spec, extra) => ({
  ...(spec?.data || {}), name: spec?.data?.name || spec?.label, ...(extra || {}) });

/** Fits the picture again whenever its box changes size: the guide moves between a popup and a
 *  narrow column, and a fit made at the old width would leave the nodes cut off. */
function RefitOnResize({ pad, maxZ }) {
  const { fitView } = useReactFlow();
  const w = useStore((s) => s.width);
  const h = useStore((s) => s.height);
  // Also once the nodes have been measured: a fit made on their guessed size leaves the real
  // ones past the edge.
  const measured = useNodesInitialized();
  useEffect(() => {
    const fit = () => fitView({ padding: pad, maxZoom: maxZ, minZoom: 0.1 });
    const a = setTimeout(fit, 60);
    const b = setTimeout(fit, 300);
    return () => { clearTimeout(a); clearTimeout(b); };
  }, [w, h, pad, maxZ, fitView, measured]);
  return null;
}

/** nodes: [{id, type, x, y, data?, mark?}]; edges: [{source, target, targetHandle?}].
 *  A node with `mark` is drawn with a red ring (what this step just added). */
// What a node takes up on the canvas, for sizing a scrollable picture.
const NODE_W = 330;
const NODE_H = 240;

/** `scrollable`: the picture keeps the nodes at a readable size (never smaller than 55%) and, when
 *  they do not fit the box, scrolls left-right and top-down instead of shrinking them to nothing. */
export function Diagram({ nodes, edges, registry, nodeTypes, height = 150, label, zones, tables,
                          scrollable = false, zoomMax = 0.62 }) {
  const wrap = useRef(null);
  const [availW, setAvailW] = useState(0);
  useEffect(() => {
    const el = wrap.current;
    if (!scrollable || !el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setAvailW(el.clientWidth));
    ro.observe(el);
    setAvailW(el.clientWidth);
    return () => ro.disconnect();
  }, [scrollable]);
  const base = useContext(WorkflowCtx);
  // The parent re-renders on every pixel of a drag of the split bar and passes new arrays each time.
  // New node objects read to React Flow as nodes never measured, which reset them to their guessed
  // size (the red ring came away from its node), so what is built from them is kept until the
  // CONTENT changes.
  const nodesKey = JSON.stringify(nodes);
  const edgesKey = JSON.stringify(edges);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stableEdges = useMemo(() => edges, [edgesKey]);
  // A zone to name in the example: a Zone Filter shows the area it points to.
  const ctx = useMemo(() => ({
    ...base, registry,
    // the wires, so a Counter can tell it is a zone or a line counter
    edges: stableEdges.map((e, i) => ({ id: `e${i}`, ...e })),
    variables: { ...(base.variables || {}),
                 zone_list: zones || [{ id: "zone_1", name: "Zone 1", kind: "area" }],
                 // a Data Table draws its ports from the table it writes to
                 tables: tables || base.variables?.tables || [] },
  }), [base, registry, zones, tables, stableEdges]);
  const dark = useDarkClass();
  // Room above the nodes for the numbers drawn over them.
  const pad = nodes.some((n) => n.num)
    ? { top: "60px", right: "3%", bottom: "3%", left: "3%" } : 0.12;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const flowNodes = useMemo(() => nodes.map((n) => ({
    id: n.id, type: n.type, position: { x: n.x, y: n.y },
    data: sampleData(registry[n.type], n.data),
    // `num`: a numbered red ring, the number above the node on a short line.
    className: n.num ? `ng-new ng-num ng-num-${n.num}` : n.mark ? "ng-new" : undefined,
    // A size to fit the view to before the card has been measured: without it
    // the first fit sees nodes of no size and frames only the first of them.
    initialWidth: 260, initialHeight: 130,
    draggable: false, selectable: false, connectable: false,
  })), [nodesKey, registry]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const flowEdges = useMemo(() => edges.map((e, i) => ({
    id: `e${i}`, source: e.source, target: e.target,
    targetHandle: e.targetHandle, sourceHandle: e.sourceHandle, animated: true })), [edgesKey]);
  // Nodes are never drawn larger than this: in the narrow guide column a node at full size fills
  // the picture and scrolls. (A full-width picture, such as a page's own, keeps full size.)
  const maxZ = scrollable ? zoomMax : 1;
  // The size of the picture inside a scrollable box: what the nodes need at a readable scale,
  // or the box itself when that is less.
  let inner = { width: "100%", height: "100%" };
  if (scrollable && availW && nodes.length) {
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
    const cw = Math.max(...xs) - Math.min(...xs) + NODE_W;
    const ch = Math.max(...ys) - Math.min(...ys) + NODE_H;
    const s = Math.max(0.5, Math.min(maxZ, (availW - 20) / cw));
    inner = { width: Math.max(availW - 2, Math.round(cw * s * 1.3)),
              height: Math.max(height - 2, Math.round(ch * s * 1.1)) };
  }
  return (
    <div className="ng-mini" ref={wrap}
         style={{ height, ...(scrollable ? { overflow: "auto" } : null) }}
         data-ng-mini={label || nodes.map((n) => n.type).join(",")}>
      <div style={inner}>
      <WorkflowCtx.Provider value={ctx}>
        <ReactFlowProvider>
          <ReactFlow nodes={flowNodes} minZoom={0.1} edges={flowEdges} nodeTypes={nodeTypes}
                     fitView fitViewOptions={{ padding: pad, maxZoom: maxZ, minZoom: 0.1 }}
                     onInit={(flow) => {
                       const fit = () => flow.fitView({ padding: pad, maxZoom: maxZ, minZoom: 0.1 });
                       requestAnimationFrame(fit);
                       setTimeout(fit, 150);
                     }}
                     panOnDrag={scrollable} zoomOnScroll={false} zoomOnPinch={false}
                     zoomOnDoubleClick={false} nodesDraggable={false}
                     nodesConnectable={false} elementsSelectable={false}
                     preventScrolling={false} proOptions={{ hideAttribution: true }}
                     colorMode={dark ? "dark" : "light"}>
            <Background gap={18} size={1} />
            <RefitOnResize pad={pad} maxZ={maxZ} />
          </ReactFlow>
        </ReactFlowProvider>
      </WorkflowCtx.Provider>
      </div>
    </div>
  );
}
