// The guided setup's Next / Skip control, top-right of every page it walks
// through: AI Models → Camera Setup → Zones → Workflow.
//
// It disappears entirely once the tour is finished or skipped - a tutorial
// that keeps offering itself after you are done is clutter, and worse, it
// implies you missed something. The state lives on the SYSTEM (not this
// browser), because whoever sets a board up is often not whoever opens it
// next, and "has this board been walked through" is a fact about the board.
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { ArrowRight, Check } from "lucide-react";

const PATHS = {
  models: "/models",
  detection: "/detection",
  // Retired step, kept so a board saved mid-tour on it still resolves: zones
  // are a tab of Camera Setup now.
  zones: "/zones",
  workflow: "/workflow",
};

const LABELS = {
  models: "AI Models",
  detection: "Camera Setup",
  zones: "Zones",
  workflow: "Workflow",
};

/**
 * @param {string} step  which step THIS page is. The bar only shows when the
 *                       tour is on this page, so navigating away and back
 *                       cannot leave two pages both claiming to be next.
 */
export default function TutorialNav({ step }) {
  const navigate = useNavigate();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/api/detector/tutorial").then(setState).catch(() => setState(null));
  }, []);
  useEffect(load, [load]);

  if (!state || state.done || state.step !== step) return null;

  const steps = state.steps || Object.keys(PATHS);
  const idx = steps.indexOf(step);
  const next = steps[idx + 1];
  const last = !next;

  async function advance(finish) {
    setBusy(true);
    try {
      const body = finish ? { done: true } : { step: next };
      const d = await api.post("/api/detector/tutorial", body);
      setState(d);
      if (!finish) navigate(PATHS[next]);
    } catch {
      // A tour that cannot save its position is not worth blocking the page
      // over; move on and let the next load re-read the truth.
      if (!finish) navigate(PATHS[next]);
    } finally { setBusy(false); }
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span className="muted small">
        Setup {idx + 1} of {steps.length}
      </span>
      <button className="btn btn-sm" disabled={busy}
              title="Stop showing these - you can always set things up in any order"
              onClick={() => advance(true)}>
        Skip
      </button>
      <button className="btn btn-sm btn-accent" disabled={busy}
              onClick={() => advance(last)}
              style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        {last
          ? <>Finish <Check size={13} /></>
          : <>Next: {LABELS[next]} <ArrowRight size={13} /></>}
      </button>
    </span>
  );
}
