// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Whether this browser has been told not to open the guideline on the Workflow
// page. Kept apart from the guide components so the Workflow page can ask without
// loading the popup (and the diagrams in it) until it is actually shown.
import { api } from "../api/client.js";

// "1" once the person pressed Start on the greeting in this browser (SetupWizard).
export const SETUP_STARTED_KEY = "setup.wizard.started";
// The sidebar's "Setup guide" entry asks the wizard to start the guide over ...
export const SETUP_OPEN_EVENT = "setup-wizard-open";
// ...the wizard tells the sidebar when its step or state moved ...
export const SETUP_CHANGED_EVENT = "setup-wizard-changed";
// ...and when the greeting card has landed in the sidebar.
export const SETUP_LANDED_EVENT = "setup-wizard-landed";

/** Is the first-time greeting still waiting for its Start? Only then does a page
 *  keep its own guide closed: the greeting sits in the middle of the screen and the
 *  two must not be on top of each other. Once Start was pressed the guide lives in
 *  the sidebar and every page opens its guide by itself. */
export function setupTourActive() {
  return api.get("/api/detector/tutorial")
    .then((s) => {
      if (!s || s.done) return false;
      let started = "";
      try { started = localStorage.getItem(SETUP_STARTED_KEY) || ""; } catch { /* private mode */ }
      const first = (s.steps || [])[0] || "models";
      return s.step === first && !started;
    })
    .catch(() => false);
}

// Where each setup step lives, and how its guide is asked for: the page opens that
// guide by itself, whatever the Page guides switch or a page's own tick box says.
const SETUP_PAGES = {
  models: ["/models", "open"], detection: ["/detection", "open"],
  database: ["/database", "setup"], workflow: ["/workflow", "main"],
  dashboard: ["/", "open"], analytics: ["/analytics", "open"],
};

/** The address for a setup step: its page, with its guide asked for. A fresh value
 *  each time so asking again on the same page still counts as a new visit. */
export function setupStepUrl(step) {
  const [to, kind] = SETUP_PAGES[step] || ["/", "open"];
  return `${to}?guide=${kind}&t=${Date.now()}`;
}

// The Guide button in the top bar (GuideButton), next to the full-screen icon, asks for the guide of
// the page being shown; that page's own guide answers. Which guide a page has:
const PAGE_GUIDES = [
  ["/workflow/guideline", ""], // the Nodes Guideline is the reference itself: no guide of its own
  ["/analytics/builder", "analyticsBuilder"], ["/analytics", "analytics"], ["/builder", "dashboardBuilder"],
  ["/models", "models"], ["/detection", "camera"], ["/dataset", "dataset"],
  ["/database", "database"], ["/workflow", "workflow"], ["/", "dashboard"],
];

/** The guide key for a path, or "" for a page with no guide. */
export function guideKeyForPath(pathname) {
  const p = String(pathname || "/").replace(/\/+$/, "") || "/";
  for (const [prefix, key] of PAGE_GUIDES) {
    if (prefix === "/" ? p === "/" : (p === prefix || p.startsWith(prefix + "/"))) return key;
  }
  return "";
}

export const GUIDE_REQUEST_EVENT = "page-guide-request";

/** Ask the page showing guide `key` to open it. */
export function requestGuide(key) {
  window.dispatchEvent(new CustomEvent(GUIDE_REQUEST_EVENT, { detail: { key } }));
}

const ENABLED_KEY = "guides.enabled";
const CHANGED = "guides-enabled-change";

/** The switch in the sidebar: do page guides open by themselves? On unless turned off.
 *  A page's own Guide button always works. */
export function guidesEnabled() {
  try { return localStorage.getItem(ENABLED_KEY) !== "0"; } catch { return true; }
}

export function setGuidesEnabled(on) {
  try {
    if (on) localStorage.removeItem(ENABLED_KEY);
    else localStorage.setItem(ENABLED_KEY, "0");
  } catch { /* private mode: it lasts until the page is closed */ }
  window.dispatchEvent(new Event(CHANGED));
}

export const GUIDES_CHANGED_EVENT = CHANGED;
