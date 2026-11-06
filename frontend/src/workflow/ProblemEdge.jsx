// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A wire with something wrong on it: drawn red, with an X at its midpoint.
// Hover the X for the reason. Used only for the wires the page marks as a
// problem (see `wireProblem` in pages/Workflow.jsx); every other wire is
// React Flow's own default edge.
import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from "@xyflow/react";

const ProblemEdge = memo(function ProblemEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  style, markerEnd, data, interactionWidth,
}) {
  const [path, lx, ly] = getBezierPath({ sourceX, sourceY, sourcePosition,
                                         targetX, targetY, targetPosition });
  const level = data?.level === "warn" ? "warn" : "error";
  const color = level === "warn" ? "#f59e0b" : "#ef4444";
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
                interactionWidth={interactionWidth}
                style={{ ...style, stroke: style?.stroke === "#2f8fff" ? style.stroke : color,
                         strokeWidth: style?.strokeWidth || 2 }} />
      <EdgeLabelRenderer>
        <div className="nodrag nopan" title={data?.reason || "something is wrong with this wire"}
             style={{ position: "absolute", pointerEvents: "all", cursor: "help",
                      transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`,
                      width: 18, height: 18, borderRadius: "50%", background: color,
                      color: "#fff", fontSize: 12, fontWeight: 800, lineHeight: "18px",
                      textAlign: "center", boxShadow: "0 0 0 2px var(--surface, #fff)" }}>
          ✕
        </div>
      </EdgeLabelRenderer>
    </>
  );
});

export const edgeTypes = { problem: ProblemEdge };
