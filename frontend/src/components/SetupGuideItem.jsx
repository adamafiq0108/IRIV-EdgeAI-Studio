// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// "Setup guide", at the bottom of the sidebar, above the account button. After the
// greeting's Start it is the guide's home: it says which step the system is on and
// has a Next button, so moving on is one click here, and each Next opens the next
// page with that page's guide. Once the guide was finished or skipped it starts it
// over from the first page.
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, Check, Sparkles } from "lucide-react";
import { api } from "../api/client.js";
import { GuideSwitchRow } from "./GuideSwitch.jsx";
import {
  SETUP_CHANGED_EVENT, SETUP_LANDED_EVENT, SETUP_OPEN_EVENT, SETUP_STARTED_KEY,
  setGuidesEnabled, setupStepUrl,
} from "../workflow/guideState.js";

const LABELS = { models: "AI Models", detection: "Camera Setup", database: "Database",
                 workflow: "Workflow", dashboard: "Dashboard", analytics: "Analytics" };

export default function SetupGuideItem() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  // The last guide is done: say how to turn the page guides off.
  const [finished, setFinished] = useState(false);
  // Has Start been pressed on the greeting in this browser? Until then the entry is
  // not shown (but keeps its place, so the greeting has somewhere to fly to).
  const [started, setStarted] = useState(false);
  // The greeting has just landed here: a short pulse says "this is where it went".
  const [pulse, setPulse] = useState(false);

  const load = useCallback(() => {
    api.get("/api/detector/tutorial").then(setState).catch(() => setState(null));
    try { setStarted(!!localStorage.getItem(SETUP_STARTED_KEY)); } catch { setStarted(false); }
  }, []);
  useEffect(load, [load, pathname]);
  useEffect(() => {
    const landed = () => { setPulse(true); setTimeout(() => setPulse(false), 1600); };
    window.addEventListener(SETUP_CHANGED_EVENT, load);
    window.addEventListener(SETUP_LANDED_EVENT, landed);
    return () => {
      window.removeEventListener(SETUP_CHANGED_EVENT, load);
      window.removeEventListener(SETUP_LANDED_EVENT, landed);
    };
  }, [load]);

  if (!state) return null;
  const steps = state.steps || Object.keys(LABELS);
  const going = !state.done;
  const idx = Math.max(0, steps.indexOf(state.step));
  const next = steps[idx + 1];
  // Waiting for the greeting's Start: kept in place, invisible.
  const waiting = going && !started && idx === 0;

  async function advance() {
    setBusy(true);
    try {
      if (next) {
        setState(await api.post("/api/detector/tutorial", { step: next }));
        navigate(setupStepUrl(next));
      } else {
        setState(await api.post("/api/detector/tutorial", { done: true }));
        setFinished(true);
      }
      window.dispatchEvent(new Event(SETUP_CHANGED_EVENT));
    } catch { /* the page reads the truth again on its next load */ }
    finally { setBusy(false); }
  }

  // After the last guide: where the Page guides switch is, and what it does.
  const doneDialog = finished && createPortal(
    <div className="ui-dialog-backdrop" data-setup-finished onMouseDown={() => setFinished(false)}>
      <div className="ui-dialog ng-popup" role="dialog" aria-modal="true" aria-label="All set"
           style={{ width: "min(520px, 96vw)" }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="ui-dialog-head">
          <h3>You're all set</h3>
          <button className="toast-close" onClick={() => setFinished(false)} aria-label="Close">×</button>
        </div>
        <div className="ui-dialog-body">
          <p style={{ margin: "0 0 12px", lineHeight: 1.55 }}>
            That was the last step. Each page will keep opening its guide the first time you
            visit it. When you no longer need them, turn them off with the
            {" "}<strong>Page guides</strong> switch at the bottom of the sidebar, just above
            your account. You can switch them back on there any time.
          </p>
          <GuideSwitchRow />
        </div>
        <div className="ui-dialog-foot ng-popup-foot">
          <button type="button" className="btn btn-sm btn-accent" data-setup-finished-ok
                  onClick={() => setFinished(false)}>Got it</button>
        </div>
      </div>
    </div>,
    document.body);

  // Skip ends the setup guide here too, and turns the page guides off with it, the
  // same as Skip on the greeting. The Page guides switch turns them back on.
  async function skip() {
    setGuidesEnabled(false);
    setBusy(true);
    try {
      setState(await api.post("/api/detector/tutorial", { done: true }));
      window.dispatchEvent(new Event(SETUP_CHANGED_EVENT));
    } catch { /* the page reads the truth again on its next load */ }
    finally { setBusy(false); }
  }

  if (!going) {
    return (
      <>
      {doneDialog}
      <button type="button" data-setup-guide-item className="sw-side group-data-[collapsible=icon]:items-center group-data-[collapsible=icon]:px-0"
              title="Run the setup guide again from the first page"
              onClick={() => window.dispatchEvent(new Event(SETUP_OPEN_EVENT))}>
        <span className="sw-side-head group-data-[collapsible=icon]:justify-center">
          <Sparkles size={16} aria-hidden="true" />
          <span className="group-data-[collapsible=icon]:hidden">
            <strong>Setup guide</strong>
            <small>Start again from the first page</small>
          </span>
        </span>
      </button>
      </>
    );
  }

  return (
    <div data-setup-guide-item aria-hidden={waiting || undefined}
         style={waiting ? { visibility: "hidden" } : undefined}
         className={"sw-side on group-data-[collapsible=icon]:items-center group-data-[collapsible=icon]:px-0"
                    + (pulse ? " sw-pulse" : "")}>
      <button type="button" className="sw-side-head group-data-[collapsible=icon]:justify-center"
              title={`Setup guide: step ${idx + 1} of ${steps.length}, ${LABELS[state.step] || state.step}`}
              onClick={() => navigate(setupStepUrl(state.step))}>
        <Sparkles size={16} aria-hidden="true" />
        <span className="group-data-[collapsible=icon]:hidden">
          <strong>Setup guide</strong>
          <small>Step {idx + 1} of {steps.length}: {LABELS[state.step] || state.step}</small>
        </span>
      </button>
      <button type="button" className="sw-side-next group-data-[collapsible=icon]:hidden" disabled={busy} data-setup-next onClick={advance}>
        {next
          ? <>Next: {LABELS[next] || next} <ArrowRight size={14} /></>
          : <>Finish <Check size={14} /></>}
      </button>
      <button type="button" className="sw-side-skip group-data-[collapsible=icon]:hidden" disabled={busy} data-setup-skip onClick={skip}
              title="End the setup guide - this also turns the page guides off">
        Skip the guide
      </button>
    </div>
  );
}
