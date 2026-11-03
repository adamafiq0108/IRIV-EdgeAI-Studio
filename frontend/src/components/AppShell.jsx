// Protected layout for every authenticated page. Fetches /api/bootstrap once,
// provides it via AppContext, and renders the topbar + sidebar + routed page.
// A 401 (no session) redirects to /login; any other error shows a message.
import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { api, ApiError, rememberHub } from "../api/client.js";
import { AppContext, applyBrand, hostsInScope } from "../state/app.jsx";
import { ErrorNote, SkelShell } from "./Loading.jsx";
import { ToastProvider } from "./ui/Toast.jsx";
import { AskProvider } from "./ui/ask.jsx";
import HubShell from "./HubShell.jsx";
import MasterShell from "./MasterShell.jsx";
import StandaloneShell from "./StandaloneShell.jsx";

export default function AppShell() {
  const [boot, setBoot] = useState(null);
  const [error, setError] = useState(null);
  const location = useLocation();

  // Global device scope: clicking a folder in the sidebar narrows every page to
  // just that folder's devices. Persisted so it survives navigation/reload.
  const [scope, setScopeState] = useState(() => {
    try { return JSON.parse(localStorage.getItem("fx-scope")) || { kind: "all" }; }
    catch { return { kind: "all" }; }
  });
  const setScope = useCallback((next) => {
    setScopeState((cur) => {
      const val = typeof next === "function" ? next(cur) : next;
      try { localStorage.setItem("fx-scope", JSON.stringify(val)); } catch { /* ignore */ }
      return val;
    });
  }, []);

  // The version this shell first loaded under. A later bootstrap naming
  // another one means the box was updated under this tab, and the bundle in
  // memory is the OLD release's: old client code (no setup-gate bounce, no
  // flag in its shell) running against the new server, which is how a board
  // showed the Settings page under a gate it did not know about. The document
  // is reloaded, not patched -- there is no second version of the code to
  // switch to.
  const firstVersion = useRef("");

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/bootstrap");
      if (d.version) {
        if (firstVersion.current && d.version !== firstVersion.current) {
          window.location.reload();
          return;
        }
        firstVersion.current = d.version;
      }
      applyBrand(d.brand);
      // Where this box's hub is, for every later bounce to a login screen --
      // a sign-out, or a session that expires under a background poll. Both
      // happen when there is nothing left to ask the server with.
      rememberHub(d.hub_url, d.mode === "hub");
      setBoot(d);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Warm the Camera Setup chunk once the shell is idle. It is the first page
  // of the guided setup and the one opened most after the Dashboard, and its
  // split chunk (router.jsx) cost a round trip -- a visible skeleton over
  // Wi-Fi -- on every first click. A hub has no such page, so it fetches
  // nothing. The browser caches the file; a second shell load pays nothing.
  const bootMode = boot?.mode;
  useEffect(() => {
    if (!bootMode || bootMode === "hub") return;
    const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1500));
    const cancel = window.cancelIdleCallback || clearTimeout;
    const id = idle(() => { import("../pages/Detection.jsx").catch(() => {}); });
    return () => cancel(id);
  }, [bootMode]);

  // Optimistic sidebar edits (device move/rename) patch this in place so the UI
  // updates instantly; the API call reconciles in the background. `updater` gets
  // the current sidebar and returns the next one.
  const patchSidebar = useCallback((updater) => {
    setBoot((b) => (b && b.sidebar ? { ...b, sidebar: updater(b.sidebar) } : b));
  }, []);

  // The set of host_serials the active scope allows (null = no filter). Derived
  // from the sidebar tree so pages can filter their device lists client-side.
  const scopeHosts = useMemo(
    () => hostsInScope(boot?.sidebar, scope), [boot, scope]);

  if (error) {
    if (error instanceof ApiError && error.status === 401) {
      // The bootstrap itself 401'd, so nothing told this page where the hub
      // is. Router-local, deliberately: this is the ONE case with no answer
      // to hand, and a master's own login sends the sign-in on to the hub
      // anyway (Login.jsx) -- one extra hop rather than a guessed port.
      const next = encodeURIComponent(location.pathname + location.search);
      return <Navigate to={`/login?next=${next}`} replace />;
    }
    return <ErrorNote error={error} />;
  }
  if (!boot) return <SkelShell />;

  // First run on the shipped password (UAT-103): the server refuses every
  // call but the setup ones, so there is no shell to draw yet. Any page goes
  // to the setup screen, and that screen renders on its own -- the shell's
  // rail, bell and polls would only collect 409s around it.
  if (boot.needs_account_setup) {
    if (location.pathname !== "/account-setup") {
      return <Navigate to="/account-setup" replace />;
    }
    return (
      <ToastProvider>
        <div style={{ padding: 24 }}><Outlet /></div>
      </ToastProvider>
    );
  }

  const ctx = {
    mode: boot.mode || "master",
    deployRole: boot.deploy_role || "standalone",
    brand: boot.brand,
    user: boot.user,
    system: boot.system,
    nav: boot.nav || [],
    capabilities: new Set(boot.capabilities || []),
    hubUrl: boot.hub_url || "",
    sidebar: boot.sidebar,
    reloadShell: load,
    patchSidebar,
    scope,
    setScope,
    scopeHosts,
    boot,
  };

  // The hub serves the same SPA in "hub" mode: no single system, so it gets the
  // admin shell (Systems grid + no-code builders) instead of a master's device
  // sidebar and paged nav.
  if (ctx.mode === "hub") {
    return (
      <AppContext.Provider value={ctx}>
        <ToastProvider>
          <AskProvider>
            <HubShell />
          </AskProvider>
        </ToastProvider>
      </AppContext.Provider>
    );
  }

  // A standalone system is a single local board, not the head/member of a
  // master/slave group. It gets a local-board shell (no fleet device tree, no
  // hub back-link, task-grouped nav) instead of the fleet MasterShell.
  const Shell = ctx.deployRole === "standalone" ? StandaloneShell : MasterShell;

  // Masters (and slaves) use the same shadcn shell template as the hub, so 5100
  // and every 511x system present one interface.
  return (
    <AppContext.Provider value={ctx}>
      {/* Toasts wrap the whole shell so any page can report a failure without
          inventing its own corner of the screen for it. Asking a question is
          the same idea in the other direction: one dialog for the whole app,
          so no page has to reach for the browser's own confirm box. */}
      <ToastProvider>
        <AskProvider>
          <Shell />
        </AskProvider>
      </ToastProvider>
    </AppContext.Provider>
  );
}
