// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The first-time setup guide's greeting: shown once, in the middle of the screen,
// when someone first enters a new system. It says hi and what is about to happen;
// Start sends the card flying into the sidebar, where the guide lives from then on
// (SetupGuideItem, above the account button) and opens the first page with its guide.
//
// State is the same server-side per-SYSTEM record TutorialNav uses
// (/api/detector/tutorial): whoever sets a board up is often not whoever opens
// it next, so "has this board been walked through" is a fact about the board.
// Whether THIS browser pressed Start, by contrast, lives in localStorage.
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { ArrowRight, Sparkles, X } from "lucide-react";
import {
  SETUP_CHANGED_EVENT, SETUP_LANDED_EVENT, SETUP_OPEN_EVENT, SETUP_STARTED_KEY,
  setGuidesEnabled, setupStepUrl,
} from "../workflow/guideState.js";

const LABELS = { models: "AI Models", detection: "Camera Setup", database: "Database",
                 workflow: "Workflow", dashboard: "Dashboard", analytics: "Analytics" };

const FLIGHT_MS = 600;

export default function SetupWizard() {
  const navigate = useNavigate();
  const location = useLocation();
  const { system } = useApp();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  // "1" once Start was pressed in this browser: the guide then lives in the sidebar.
  const [started, setStarted] = useState(() => {
    try { return localStorage.getItem(SETUP_STARTED_KEY) || ""; } catch { return ""; }
  });
  // The card on its way to the sidebar: {dx, dy, sc}.
  const [flying, setFlying] = useState(null);
  const cardRef = useRef(null);

  const load = useCallback(() => {
    api.get("/api/detector/tutorial").then((s) => {
      // A system the guide has never touched is new -- also when it reuses the name
      // of one that was removed. Forget what the browser remembered of its
      // namesake, so the guide starts from its greeting again.
      if (s?.fresh) {
        try { localStorage.removeItem(SETUP_STARTED_KEY); } catch { /* private window */ }
        setStarted("");
      }
      setState(s);
    }).catch(() => setState(null));
  }, []);
  useEffect(load, [load]);
  // Re-read on every navigation, and when the sidebar's Next moved the step.
  useEffect(load, [load, location.pathname, system?.slug]);
  useEffect(() => {
    window.addEventListener(SETUP_CHANGED_EVENT, load);
    return () => window.removeEventListener(SETUP_CHANGED_EVENT, load);
  }, [load]);

  useEffect(() => {
    try {
      if (started) localStorage.setItem(SETUP_STARTED_KEY, started);
      else localStorage.removeItem(SETUP_STARTED_KEY);
    } catch { /* private window */ }
    window.dispatchEvent(new Event(SETUP_CHANGED_EVENT));
  }, [started]);

  // Once the guide was finished or skipped, the sidebar entry starts it over from
  // the first page: the greeting comes back.
  const stateRef = useRef(state);
  stateRef.current = state;
  useEffect(() => {
    const open = async () => {
      const s = stateRef.current;
      if (s && !s.done) return;
      try {
        setState(await api.post("/api/detector/tutorial",
                                { done: false, step: (s?.steps || Object.keys(LABELS))[0] }));
      } catch { return; }
      setStarted("");
      window.dispatchEvent(new Event(SETUP_CHANGED_EVENT));
      navigate("/");
    };
    window.addEventListener(SETUP_OPEN_EVENT, open);
    return () => window.removeEventListener(SETUP_OPEN_EVENT, open);
  }, [navigate]);

  // Only the greeting is a card: while the system is on its first step and Start has
  // not been pressed. Everything after is the sidebar entry and the pages' guides.
  if (!state || state.done || started) return null;
  const steps = state.steps || Object.keys(LABELS);
  if (steps.indexOf(state.step) !== 0) return null;

  const vw = typeof window !== "undefined" ? window.innerWidth : 1366;
  // Tiers, not a straight scale, so it does not creep with every pixel of resize.
  const width = vw >= 1920 ? 580 : vw >= 1600 ? 520 : 460;
  const centred = {
    position: "fixed", left: "50%", top: "50%", transform: "translate(-50%, -50%)",
    zIndex: 60, width, maxWidth: "calc(100vw - 32px)",
  };

  async function skip() {
    // Skipping the guide also turns the pages' own guides off: someone who waves the
    // walkthrough away does not want a guide opening on every page either. The
    // Page guides switch in the sidebar turns them back on.
    setGuidesEnabled(false);
    setBusy(true);
    try { setState(await api.post("/api/detector/tutorial", { done: true })); }
    catch { /* a tour that cannot save its position is not worth blocking on */ }
    finally { setBusy(false); }
    // The sidebar follows: the Page guides switch (now off) and "Start again".
    window.dispatchEvent(new Event(SETUP_CHANGED_EVENT));
  }

  // Start: the card shrinks and flies to the sidebar entry above the account button,
  // then the first page opens with its guide. With no sidebar to land in (a narrow
  // screen), or for someone who asked for less motion, it just goes.
  const begin = () => {
    const card = cardRef.current;
    const item = document.querySelector("[data-setup-guide-item]");
    const land = () => {
      setFlying(null);
      setStarted("1");
      // Saved on the system, so it no longer counts as untouched from here on.
      api.post("/api/detector/tutorial", { step: state.step }).catch(() => {});
      window.dispatchEvent(new Event(SETUP_LANDED_EVENT));
      navigate(setupStepUrl(state.step));
    };
    const still = typeof window !== "undefined" && window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!card || !item || still) { land(); return; }
    const c = card.getBoundingClientRect();
    const t = item.getBoundingClientRect();
    if (!t.width || !t.height) { land(); return; }
    setFlying({
      dx: (t.left + t.width / 2) - (c.left + c.width / 2),
      dy: (t.top + t.height / 2) - (c.top + c.height / 2),
      sc: Math.max(0.1, Math.min(0.5, t.width / c.width)),
    });
    setTimeout(land, FLIGHT_MS);
  };

  const style = flying ? {
    ...centred,
    transform: `translate(-50%, -50%) translate(${flying.dx}px, ${flying.dy}px) scale(${flying.sc})`,
    opacity: 0.25, pointerEvents: "none",
    transition: `transform ${FLIGHT_MS}ms cubic-bezier(.5, 0, .2, 1), opacity ${FLIGHT_MS}ms ease-in`,
  } : centred;

  return (
    <div className="sw-card" data-wizard-greeting ref={cardRef} style={style}>
      <div className="sw-top">
        <span className="sw-badge"><Sparkles size={20} /></span>
        <div className="sw-step">Welcome</div>
        <button className="sw-x" disabled={busy || !!flying}
                title="Skip the guide - you can set things up in any order"
                onClick={skip}><X size={16} /></button>
      </div>
      <h3 className="sw-title">Hi! Let's set up your AI system</h3>
      <p className="sw-hint">
        Right now, I'll guide you on how to create an AI system, one step at a time.
      </p>
      <ol className="sw-list">
        {steps.map((k, i) => (
          <li key={k}><span className="sw-num">{i + 1}</span>{LABELS[k] || k}</li>
        ))}
      </ol>
      <div className="sw-actions">
        <button className="sw-primary" data-wizard-greet-start disabled={!!flying} onClick={begin}>
          Start <ArrowRight size={16} />
        </button>
        <button className="sw-link" disabled={busy || !!flying} onClick={skip}>Skip</button>
      </div>
      <p className="sw-note" data-wizard-skip-note>
        Skip (or the X) also turns the page guides off. You can switch them back on with
        the <strong>Page guides</strong> switch at the bottom of the sidebar.
      </p>
    </div>
  );
}
