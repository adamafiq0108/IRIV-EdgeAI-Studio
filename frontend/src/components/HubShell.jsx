// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// HubShell - the admin surface at the hub (port 5100). The hub serves the same
// SPA as the masters but in "hub" mode: no single system, so instead of a
// master's device sidebar it gets THIS admin sidebar, from which an operator
// runs an existing system (the Systems grid) or builds a new one - no code.
//
// Sections are switched client-side (local state), not routed, so every item
// works against the hub's own endpoints (/api/hub) and nothing 404s against a
// master-only API. Feature panels that need a system link OUT to that system's
// master, where the real backend lives.
import { Suspense, lazy, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { useApp } from "../state/app.jsx";
import { useTheme } from "../hooks/use-theme.js";
import CreateSystemWizard from "./CreateSystemWizard.jsx";
import {
  SidebarProvider, SidebarInset,
} from "./ui/sidebar.jsx";
import { TooltipProvider } from "./ui/tooltip.jsx";
// Split (perf item L6): the systems grid is HubShell's first screen, but
// keeping it out of the entry chunk lets the shell and the login paint first.
const Hub = lazy(() => import("../pages/Hub.jsx"));
import HealthPanel from "./HealthPanel.jsx";
import SettingsHub from "./SettingsHub.jsx";
import CapacityMeters from "./CapacityMeters.jsx";
import NotificationCenter from "./NotificationCenter.jsx";
import NewStoragePrompt from "./NewStoragePrompt.jsx";

// Split for the same reason the router splits it: the Log Hub is a page an
// admin opens occasionally, and its chunk has no business in the bundle that
// has to parse before the Systems grid paints.
import { Button } from "./ui/button.jsx";
import FullscreenButton from "./FullscreenButton.jsx";
import UserMenu from "./UserMenu.jsx";
import {
  LogOut, Moon, Sun,
  Users, Library,
  Database, CircuitBoard, Home, TriangleAlert,
} from "lucide-react";

const NAV = [
  { group: "Run", items: [
    { id: "systems", label: "Home", icon: Home, hint: "Your systems - create and open them here" },
  ]},
  // Settings and About are not in the rail: they live in the account menu at the top right
  // (UserMenu), next to Sign out. Logs is gone from the hub.
];

const TITLES = {
  systems: "Home",
  health: "About",
  vision: "New AI Vision system",
  accounts: "Account Management",
  settings: "Settings",
};

export default function HubShell() {
  const { brand, user } = useApp();
  const [section, setSection] = useState("systems");
  // Bumped whenever somebody is sent to a settings tab from elsewhere. It is
  // the key on SettingsHub below, so the panel REMOUNTS and reads the tab it
  // has just been given -- without it, a reader already sitting on Settings
  // would click "Go to Email" and watch nothing happen, which is worse than
  // no link at all.
  const [goto, setGoto] = useState(0);

  // Where a notice sends its reader. The tab travels through the same
  // localStorage key SettingsHub already remembers, so there is one way a
  // tab is chosen rather than two that can disagree.
  function go(page, tab) {
    if (tab) {
      try { localStorage.setItem("hub.settings.tab", tab); } catch { /* private mode */ }
      setGoto((n) => n + 1);
    }
    // The two pages that were folded into the system cards (run / stop / autostart)
    // and dropped (live preview): anything still pointing at them lands on Systems.
    setSection(page === "control" || page === "wall" ? "systems" : page);
  }
  // ...and from a dialog that has no `go` to call: Edit system and the
  // create wizard's "Settings > Network > Software port" link
  // (boxAccess.jsx goToSoftwarePort).
  useEffect(() => {
    const onGoto = (e) => go(e.detail?.page || "settings", e.detail?.tab);
    window.addEventListener("irivai:goto", onGoto);
    return () => window.removeEventListener("irivai:goto", onGoto);
  });
  // ...and which template it starts from: a Hub home card's "Use this
  // template", or "" from New System, which the wizard reads as the
  // server's default -- Blank Canvas.
  const [pendingTemplate, setPendingTemplate] = useState("");
  const [dark, setDark] = useTheme();
  const { data, reload } = usePoll("/api/hub", 10000);
  const cards = data?.cards || [];
  const head = TITLES[section] || TITLES.systems;
  // "Running" = the master answers. Counting only status "ok" would report 0
  // for a healthy master that simply has no devices paired yet ("none").
  const running = cards.filter((c) => c.url && c.status !== "down").length;


  return (
    <TooltipProvider delayDuration={200}>
      {/* .cy-app re-asserts the shadcn design tokens for this subtree - the
          legacy theme.css redefines some of the same names (see index.css). */}
      <SidebarProvider className="cy-app">
        <SidebarInset className="bg-muted/40">
          <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background/70 px-4 backdrop-blur-md">
            {/* The deployment's logo at the left of the top bar, where the rail's header used to
                be: also the way back Home from Settings or About. Read from brand.toml so a
                rebranded deployment swaps it with no code change. */}
            <button type="button" onClick={() => setSection("systems")} data-hub-brand
                    title={brand?.product_name || "IRIV EdgeAI Studio"}
                    className="flex shrink-0 items-center rounded-lg p-1 outline-none transition-colors
                               hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">
              <img src={"/" + String(brand?.logo || "static/img/cytron.png").replace(/^\/+/, "")}
                   alt="" className="h-9 w-auto max-w-40 object-contain" />
              <span className="ml-2.5 whitespace-nowrap text-base font-semibold tracking-tight">
                {brand?.product_name || "IRIV EdgeAI Studio"}
              </span>
            </button>
            {/* Title only. The subtitle repeated what the page itself says the
                moment you look at it, on every page, forever -- a sentence you
                have read a hundred times is furniture, not information. */}
            <div className="ml-8 min-w-0 flex-1">
              <h1 className="truncate text-sm font-semibold leading-tight">
                {head}
                {section === "systems" && cards.length ? (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {running} of {cards.length} running
                  </span>
                ) : null}
              </h1>
            </div>
            {/* What this box has left: systems and running cameras, against
                the caps the server actually enforces. In the header because
                both limits are met while doing something else -- creating a
                system, starting a camera -- and a limit you meet as a 400 is
                a limit nobody saw coming. */}
            <CapacityMeters />
            {/* Admin only, and mounted only for an admin rather than hidden
                from everyone else: every endpoint behind it is admin-gated, so
                an operator's browser would otherwise poll something that 403s
                at it once a minute for the life of the session. */}
            {/* Every signed-in account has an Inbox; the System and
                Important tabs inside are the admin's. */}
            {user?.name && user.name !== "guest"
              && <NotificationCenter admin={!!user?.is_admin} onGo={go} />}
            {/* Beside the theme toggle: both are "how this screen is shown",
                and a wall panel wants the browser chrome gone. */}
            <FullscreenButton />
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setDark((d) => !d)}
              aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
              title={dark ? "Light mode" : "Dark mode"}
            >
              {dark ? <Sun /> : <Moon />}
            </Button>
            {/* The account: Settings, About, Sign out, Reboot and Shut down. */}
            <UserMenu version={data?.version} onSettings={() => go("settings", "accounts")} onAbout={() => go("health")} />
          </header>
          {/* The NVMe drive the box expects is not mounted (core/services/
              storage_watch.py). On every page, not only Settings: new data is
              going to the built-in storage and kept for 60 days, which is
              the kind of thing found out weeks later otherwise. Rides on the
              /api/hub poll above, so it costs no request of its own, and
              goes away by itself once the drive is mounted again. */}
          {data?.storage_alarm?.active && (
            <div data-storage-alarm="1" role="alert"
                 className="flex items-start gap-2 border-b border-amber-500/50 bg-amber-500/10 px-4 py-2.5 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" />
              <span>{data.storage_alarm.text}</span>
            </div>
          )}
          {/* "New storage detected" (core/services/storage_setup.py): a drive
              fitted after install, asked about once. Master only, and only
              mounted for master, like the routes behind it. */}
          {user?.is_admin && <NewStoragePrompt />}
          <main className="flex-1 p-4 sm:p-6">
            {section === "systems"   && (
              <Suspense fallback={<div className="p-6 text-muted-foreground">Loading…</div>}>
                <Hub onCreate={(template) => {
                  setPendingTemplate(template || "");
                  setSection("vision");
                }} />
              </Suspense>
            )}
            {section === "health"    && <HealthPanel />}
            {/* Creating a system is reached from the Systems grid's "New
                system" card or a template card - it is not a nav
                destination of its own. */}
            {section === "vision"    && (
              <VisionPanel
                 template={pendingTemplate}
                 onCreated={async (created) => {
                   setPendingTemplate("");
                   setSection("systems");
                   reload().catch(() => {});
                 }}
                 onCancel={() => { setPendingTemplate("");
                                   setSection("systems"); }} />
            )}
            {/* One page, its own tab strip. */}
            {/* A SITE ADMIN reaches Settings too, with fewer tabs in it --
                their own staff, retention, mail, Telegram, network. The tab
                list comes from the server (auth.settings_tabs), because
                deciding it here would mean the page and the endpoints behind
                it could disagree about who may do what. */}
            {section === "settings" && user?.is_site_admin && (
              <SettingsHub key={goto} tabs={user?.settings_tabs} />
            )}
            {/* Audit.jsx is a master page component; it renders fine here -
                same API, mounted on the hub too. */}
          </main>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}

// ── feature panels ──────────────────────────────────────────────────────────

function PanelCard({ children, className = "" }) {
  return <div className={"rounded-xl border bg-card p-5 " + className}>{children}</div>;
}

function VisionPanel({ template, onCreated, onCancel }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PanelCard>
        <CreateSystemWizard template={template} onCreated={onCreated} onCancel={onCancel} />
      </PanelCard>
    </div>
  );
}
