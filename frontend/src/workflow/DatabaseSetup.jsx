// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Database Setup: how to make the table a Data Table node writes to, as a
// slideshow popup. It opens on the Database page (from the Examples guideline's
// "Database Setup" button, or the button on the page itself).
import { Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { GuideSwitchRow } from "../components/GuideSwitch.jsx";
import { Welcome } from "./GuideWelcome.jsx";

const STEPS = [
  { title: "Welcome",
    body: "Create the table that keeps your numbers, so a chart can draw them over time.",
    shot: () => (
      <Welcome heading="Welcome to Database Setup"
               lead="Create the table that keeps your numbers, so a chart can draw them over time."
               learn={["Open Database", "Name it and add columns"]} />) },
  { title: "Open Database",
    body: "In the side menu open Database, then press New Table.",
    shot: () => (
      <div className="ng-mk-shot ng-mk-center">
        <b>Database</b>
        <span className="muted small">Select a table from the left panel, or create a new table.</span>
        <span className="btn btn-sm btn-accent ng-new">New Table</span>
      </div>) },
  { title: "Name it and add columns",
    body: "Type a table name, add a column for each value you want to keep, then press Create "
        + "table. Next you find the table on the Workflow, where the guide there picks up.",
    shot: () => (
      <div className="ng-mk-shot">
        <b>New table</b>
        <span className="small muted">TABLE NAME</span>
        <span className="input">test</span>
        <span className="small muted">COLUMNS</span>
        <span className="ng-mk-row"><span className="input">count</span><span className="input">Number</span></span>
        <span className="ng-mk-row"><span className="btn btn-sm">Cancel</span>
          <span className="btn btn-sm btn-accent ng-new">Create table</span></span>
      </div>) },
];

export default function DatabaseSetup({ open, onClose }) {
  const [at, setAt] = useState(0);
  const [mini, setMini] = useState(false);
  // Closing ends it: the next time it opens it starts from the first step.
  const close = () => { setAt(0); setMini(false); onClose?.(); };
  useEffect(() => {
    if (!open || mini) return undefined;
    const key = (e) => {
      if (e.key === "Escape") onClose?.();
      if (e.key === "ArrowRight") setAt((n) => Math.min(STEPS.length - 1, n + 1));
      if (e.key === "ArrowLeft") setAt((n) => Math.max(0, n - 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, onClose, mini]);
  if (!open) return null;
  const step = STEPS[at];
  const last = at === STEPS.length - 1;
  return createPortal(
    <div className={mini ? "ng-dock" : "ui-dialog-backdrop"} onMouseDown={mini ? undefined : close}
         data-db-setup-popup>
      <div className={"ui-dialog ng-popup" + (mini ? " ng-minicard" : "")} role="dialog"
           aria-modal={!mini} aria-label="Database Setup"
           style={mini ? undefined : { width: "min(720px, 96vw)" }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="ui-dialog-head">
          <h3>Database Setup</h3>
          <button type="button" className="btn btn-sm ng-minbtn ng-minbtn-icon" onClick={() => setMini(!mini)} aria-label={mini ? "Expand the guide" : "Minimize the guide"} title={mini ? "Expand the guide" : "Minimize - keep the guide in the corner"} data-guide-minimize>{mini ? <Maximize2 size={15} aria-hidden="true" /> : <Minimize2 size={15} aria-hidden="true" />}</button>
          <button className="toast-close" onClick={close} aria-label="Close">×</button>
        </div>
        <div className="ui-dialog-body">
          <div className="ng-slider-head">
            <span className="ng-tag">Step {at + 1} of {STEPS.length}</span>
            <strong>{step.title}</strong>
          </div>
          <div key={at} className="ng-mk-big">{step.shot()}</div>
          <p className="ng-slide-body" data-ng-slide-body>{step.body}</p>
          <div className="ng-slider-nav">
            <button type="button" className="btn btn-sm" disabled={at === 0}
                    onClick={() => setAt(at - 1)} data-ng-prev>Back</button>
            <div className="ng-dots">
              {STEPS.map((s, i) => (
                <button key={s.title} type="button" aria-label={`Step ${i + 1}: ${s.title}`}
                        title={s.title} className={"ng-dot" + (i === at ? " on" : "")}
                        onClick={() => setAt(i)} />
              ))}
            </div>
            {last
              ? <button type="button" className="btn btn-sm btn-accent" onClick={close}
                        data-ng-done>Done</button>
              : <button type="button" className="btn btn-sm btn-accent"
                        onClick={() => setAt(at + 1)} data-ng-next>Next</button>}
          </div>
        </div>
        {!mini && <div className="ui-dialog-foot ng-popup-foot"><GuideSwitchRow /></div>}
      </div>
    </div>,
    document.body);
}
