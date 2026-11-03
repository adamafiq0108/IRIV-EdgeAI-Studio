// The guided setup, as a companion that follows you around.
//
// The tour used to be a Next/Skip bar in the top-right of the three pages it
// walks through (TutorialNav), which meant it was only visible once you had
// already found the page it was going to send you to. A system created a
// minute ago lands on its Dashboard, where the tour said nothing at all - so
// the first question a new system asks ("what now?") was the one question the
// tour could not answer.
//
// So it lives here instead: centred for the very first step, since nobody
// has looked at any page yet, then bottom-right for the rest of the tour
// once Continue moves it past step 0, on every page, saying what the
// current step is and offering the one button that does it. It can be
// MINIMISED to a pill rather than dismissed, because the two reasons to want
// it gone are different - "not right now, I am reading this screen" and "I
// know how this works" - and only the second should end the tour.
//
// State is the same server-side per-SYSTEM record TutorialNav uses
// (/api/detector/tutorial): whoever sets a board up is often not whoever opens
// it next, so "has this board been walked through" is a fact about the board.
// Minimised-ness, by contrast, is a fact about this browser tab and lives in
// localStorage - one operator collapsing the panel must not collapse it on the
// screen in the next room.
import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { atPath } from "../state/app.jsx";
import { ArrowRight, Check, Minus, Sparkles, X } from "lucide-react";

const MINI_KEY = "setup.wizard.min";

const STEPS = {
  models: {
    to: "/models", label: "AI Models",
    title: "Choose an AI model",
    hint: "Pick a .hef from the board, or one of the built-in models. Nothing "
        + "can be detected until this system knows what to look for.",
  },
  detection: {
    to: "/detection", label: "Camera Setup",
    title: "Point it at a camera",
    hint: "USB, IP (RTSP) or a GigE camera on the network. Draw your zones on "
        + "the same page once the picture is up.",
  },
  workflow: {
    to: "/workflow", label: "Workflow",
    title: "Turn detections into decisions",
    hint: "Wire counts, conditions and alerts - this is where the system stops "
        + "watching and starts doing something.",
  },
};

export default function SetupWizard() {
  const navigate = useNavigate();
  const location = useLocation();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [mini, setMini] = useState(() => {
    try { return localStorage.getItem(MINI_KEY) === "1"; } catch { return false; }
  });

  const load = useCallback(() => {
    api.get("/api/detector/tutorial").then(setState).catch(() => setState(null));
  }, []);
  useEffect(load, [load]);
  // Re-read on every navigation: finishing a step from the page's own
  // TutorialNav bar has to move this panel too, and the two would otherwise
  // disagree about which step is current until a reload.
  useEffect(load, [load, location.pathname]);

  useEffect(() => {
    try { localStorage.setItem(MINI_KEY, mini ? "1" : "0"); }
    catch { /* private window */ }
  }, [mini]);

  // Nothing to say: no tour on this box, or it is finished or skipped.
  if (!state || state.done) return null;
  const steps = state.steps || Object.keys(STEPS);
  const idx = steps.indexOf(state.step);
  const step = STEPS[state.step];
  if (!step) return null;
  const next = steps[idx + 1];
  const here = atPath(location.pathname, step.to);

  async function advance(finish) {
    setBusy(true);
    try {
      setState(await api.post("/api/detector/tutorial",
                              finish ? { done: true } : { step: next }));
      if (!finish) navigate(STEPS[next].to);
    } catch {
      // A tour that cannot save its position is not worth blocking on.
      if (!finish) navigate(STEPS[next].to);
    } finally { setBusy(false); }
  }

  // The pill always lives in the corner -- minimising already means "out of
  // my way", so it never has a reason to sit in the middle of the screen.
  const cornerBox = {
    position: "fixed", right: 16, bottom: 16, zIndex: 60,
    boxShadow: "0 8px 30px rgba(0,0,0,.35)",
  };

  if (mini) {
    return (
      <button type="button" style={{ ...cornerBox }} className="btn btn-accent btn-sm"
              title="Open the setup guide"
              onClick={() => setMini(false)}>
        <Sparkles size={14} /> Setup {idx + 1}/{steps.length}
      </button>
    );
  }

  // First appearance only: nobody has looked at the page yet, so the corner
  // is a place to overlook rather than a place to notice. Centred is the one
  // spot a brand-new system's only screen guarantees gets seen. The moment
  // Continue moves the tour past step 0, it drops to the corner and stays
  // there for the rest of the walkthrough, out of the way of the page it is
  // now pointing at.
  const box = idx === 0 ? {
    position: "fixed", left: "50%", top: "50%",
    transform: "translate(-50%, -50%)", zIndex: 60,
    boxShadow: "0 8px 30px rgba(0,0,0,.35)",
  } : cornerBox;

  // 320px reads fine on a 1366-1440 laptop but small on a 24" monitor, so it
  // steps up at two viewport widths rather than staying one size everywhere.
  // Tiers, not a straight scale, so it does not creep with every pixel of
  // window resize -- just widens at points a real monitor actually sits at.
  const vw = typeof window !== "undefined" ? window.innerWidth : 1366;
  const panelWidth = vw >= 1920 ? 420 : vw >= 1600 ? 360 : 320;

  return (
    <div className="panel-card"
         style={{ ...box, width: panelWidth, maxWidth: "calc(100vw - 32px)", padding: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Sparkles size={15} style={{ color: "var(--accent-text, #04bade)" }} />
        <strong style={{ flex: 1, fontSize: 13 }}>
          Step {idx + 1} of {steps.length}
        </strong>
        {/* Minimise and Skip are deliberately separate controls with separate
            words: one is "not right now", the other is "never again", and a
            single ✕ that ended the tour made the cheap gesture do the
            expensive thing. */}
        <button className="btn btn-sm" title="Minimise - keep the guide for later"
                style={{ padding: "0 6px" }}
                onClick={() => setMini(true)}><Minus size={13} /></button>
        <button className="btn btn-sm" disabled={busy}
                title="Skip the guide - you can set things up in any order"
                style={{ padding: "0 6px" }}
                onClick={() => advance(true)}><X size={13} /></button>
      </div>

      <div style={{ fontWeight: 600, marginTop: 8 }}>{step.title}</div>
      <p className="muted small" style={{ marginTop: 4, marginBottom: 10 }}>
        {step.hint}
      </p>

      <div style={{ display: "flex", gap: 6 }}>
        {/* On the step's own page the button that matters is "done, what's
            next"; anywhere else it is "take me there". Offering both at once
            asked the operator to work out which one they meant. */}
        {here ? (
          <button className="btn btn-sm btn-accent" disabled={busy}
                  onClick={() => advance(!next)}
                  style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
            {next
              ? <>Done - next: {STEPS[next].label} <ArrowRight size={13} /></>
              : <>Finish <Check size={13} /></>}
          </button>
        ) : (
          <button className="btn btn-sm btn-accent"
                  onClick={() => navigate(step.to)}
                  style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
            Go to {step.label} <ArrowRight size={13} />
          </button>
        )}
        <button className="btn btn-sm" disabled={busy}
                onClick={() => advance(true)}>Skip</button>
      </div>
    </div>
  );
}
