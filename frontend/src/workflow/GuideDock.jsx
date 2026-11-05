// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The Workflow's guideline: a card at the top of the right-hand column, the same width as
// the node's properties card below it. It is always there. Its header folds it away and
// opens it again; opened, it holds the slideshow of the system's template (GuideFlow.jsx).
// The expand button lifts the same slideshow (same step) into a large view, and minimizing
// puts it back in its place. It is loaded only when it is opened, so the page does not wait on it.
import { Suspense, lazy, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { BookOpen, ChevronDown, Maximize2, Minimize2 } from "lucide-react";

const GuideFlow = lazy(() => import("./GuideFlow.jsx"));

const CLOSE_MS = 220;

export default function GuideDock({ open, onToggle, registry, nodeTypes, flash = false }) {
  const [big, setBig] = useState(false);     // the large view is wanted
  const [shown, setShown] = useState(false); // ...and is on screen (kept a moment to play the way out)
  const [at, setAt] = useState(0);

  const expand = () => { setShown(true); setBig(true); };
  const shrink = () => {
    setBig(false);
    setTimeout(() => setShown(false), CLOSE_MS);
  };

  useEffect(() => {
    if (!shown) return undefined;
    const key = (e) => { if (e.key === "Escape") shrink(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [shown]);

  return (
    <div className={"wf-guide-card panel-card" + (flash ? " wf-guide-flash" : "")} data-wf-guide
         data-open={open ? "1" : "0"}>
      <div className="wf-guide-bar">
        <button type="button" className="wf-guide-head" onClick={onToggle} aria-expanded={open}
                title={open ? "Fold the guide away" : "Open the guide"}>
          <BookOpen size={16} aria-hidden="true" />
          <span>Workflow guide</span>
          <ChevronDown size={16} className="wf-guide-chev" aria-hidden="true" />
        </button>
        {open && (
          <button type="button" className="wf-guide-expand" data-wf-guide-expand onClick={expand}
                  aria-label="Open the guide in a large view" title="Open in a large view">
            <Maximize2 size={15} aria-hidden="true" />
          </button>
        )}
      </div>
      {open && !shown && (
        <div className="wf-guide-body ng-docked">
          <Suspense fallback={<p className="muted small">Loading the guide...</p>}>
            <GuideFlow registry={registry} nodeTypes={nodeTypes} bare at={at} onAt={setAt} />
          </Suspense>
        </div>
      )}
      {open && shown && <div className="wf-guide-away muted small">Open in the large view</div>}
      {shown && createPortal(
        <div className={"wf-guide-big" + (big ? " in" : " out")} data-wf-guide-big
             onMouseDown={(e) => { if (e.target === e.currentTarget) shrink(); }}>
          <div className="wf-guide-big-panel panel-card" role="dialog" aria-label="Workflow guide">
            <div className="wf-guide-bar">
              <div className="wf-guide-head" style={{ cursor: "default" }}>
                <BookOpen size={16} aria-hidden="true" />
                <span>Workflow guide</span>
              </div>
              <button type="button" className="wf-guide-expand" data-wf-guide-shrink onClick={shrink}
                      aria-label="Bring the guide back" title="Minimize">
                <Minimize2 size={15} aria-hidden="true" />
              </button>
            </div>
            <div className="wf-guide-big-body ng-docked">
              <Suspense fallback={<p className="muted small">Loading the guide...</p>}>
                <GuideFlow registry={registry} nodeTypes={nodeTypes} bare big at={at} onAt={setAt} />
              </Suspense>
            </div>
          </div>
        </div>, document.body)}
    </div>
  );
}
