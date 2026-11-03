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
import ControlPanel from "./ControlPanel.jsx";
import LiveWallPanel from "./LiveWallPanel.jsx";
import {
  SidebarProvider, Sidebar, SidebarHeader, SidebarContent, SidebarFooter,
  SidebarGroup, SidebarGroupLabel, SidebarMenu, SidebarMenuItem, SidebarMenuButton,
  SidebarInset, SidebarTrigger, SidebarRail,
} from "./ui/sidebar.jsx";
import { Separator } from "./ui/separator.jsx";
import { TooltipProvider } from "./ui/tooltip.jsx";
// Split (perf item L6): the systems grid is HubShell's first screen, but
// keeping it out of the entry chunk lets the shell and the login paint first.
const Hub = lazy(() => import("../pages/Hub.jsx"));
import HealthPanel from "./HealthPanel.jsx";
import SettingsHub from "./SettingsHub.jsx";
import CapacityMeters from "./CapacityMeters.jsx";
import NotificationCenter from "./NotificationCenter.jsx";
import NewStoragePrompt from "./NewStoragePrompt.jsx";

// Developer panels: the template store, authoring built-in templates, and
// issuing license keys.
// `__DEV_TOOLS__` is a build-time literal (frontend/vite.config.js), so a
// customer build collapses these to null and Rollup drops the chunks -- the
// code is not in the bundle at all. Their endpoints are equally absent from a
// customer install (core/deploy.py), so shipping them would only ever produce
// panels that 404.
const DEV_TOOLS = __DEV_TOOLS__;
// The store is the PRODUCT's distribution channel, not a deployment's tool,
// so it joins them: developer only, and absent from a customer bundle.
const StorePanel = DEV_TOOLS ? lazy(() => import("./StorePanel.jsx")) : null;
// Split for the same reason the router splits it: the Log Hub is a page an
// admin opens occasionally, and its chunk has no business in the bundle that
// has to parse before the Systems grid paints.
const LogHub = lazy(() => import("../pages/Logs.jsx"));
import { Button } from "./ui/button.jsx";
import FullscreenButton from "./FullscreenButton.jsx";
import SessionMenu from "./SessionMenu.jsx";
import {
  LayoutGrid, LogOut, Moon, Sun,
  SlidersHorizontal, MonitorPlay, Activity, Users, ScrollText, Library,
  Settings as SettingsIcon,
  Database, Store, KeyRound, CircuitBoard, Home, TriangleAlert,
} from "lucide-react";

const NAV = [
  { group: "Run", items: [
    { id: "systems", label: "Systems", icon: LayoutGrid, hint: "Your systems - create and open them here" },
    { id: "control", label: "System Control", icon: SlidersHorizontal, hint: "Run, restart or stop each system" },
    { id: "health",  label: "Performance",    icon: Activity, hint: "Board + Hailo performance" },
    { id: "wall",    label: "Live Preview",   icon: MonitorPlay, hint: "Up to 4 live camera feeds" },
    // DEVELOPER only, and spread away entirely in a customer build -- same
    // treatment as System Templates and Licenses below. The store is the
    // PRODUCT's distribution channel, not a deployment's tool: what a
    // customer box runs is what was installed on it, and a catalogue of
    // other people's templates in an operator's or an admin's hub invites
    // installing something nobody chose for that deployment. Every store
    // endpoint enforces the same tier (core/routes/store.py) -- hiding the
    // nav item hides the button, not the URL.
    ...(DEV_TOOLS ? [
      { id: "store", label: "Template Store", icon: Store, devOnly: true,
        hint: "Ready-made systems published online - download and run" },
    ] : []),
  ]},
  // Settings - the box-wide decisions, managed ONCE here for every system on
  // it: who may sign in, how long data is kept, and which board terminals
  // this platform owns. Called Settings rather than Administration because
  // that is the word people look for when they want to change how the box
  // behaves; "Administration" reads as a place accounts live and nothing
  // else, which is where Board I/O and Data Retention went unfound.
  // Settings - the box-wide decisions, made ONCE for every system on the
  // box. ONE rail entry, with the choice inside it (SettingsHub.jsx).
  //
  // Accounts, Data Retention and Board I/O used to be three siblings here.
  // They are the same KIND of thing, and as three rail items they competed
  // for attention with Systems and Live Preview -- the two entries anyone
  // actually looks at constantly. The choice between them is made after you
  // have decided you are changing a setting, which is one level in.
  // `siteAdmin` rather than `adminOnly`: a site admin has settings of their
  // own (their staff, retention, mail), just fewer of them. What they may
  // open inside is decided server-side -- see SettingsHub's `tabs`.
  // Diagnostics - the hub's own event log. It existed all along and was
  // reachable only by TYPING /logs, because the hub switches sections in
  // local state rather than by route: the route resolves to this shell, and
  // this shell had no section to show for it. A page nobody can navigate to
  // is a page nobody opens when something breaks.
  // Admin only, matching the API: log lines carry paths, hostnames and stack
  // traces (core/routes/logs.py enforces the same thing, so hiding the item
  // hides the button, not the URL).
  { group: "Diagnostics", adminOnly: true, items: [
    { id: "logs", label: "Logs", icon: ScrollText,
      hint: "What this hub has been doing, and what failed" },
  ]},
  { group: "Settings", siteAdmin: true, items: [
    { id: "settings", label: "Settings", icon: SettingsIcon,
      hint: "Accounts, data retention and the board's terminals" },
  ]},
];

const TITLES = {
  systems: "Systems",
  control: "System Control",
  health: "Performance",
  wall: "Live Preview",
  vision: "New AI Vision system",
  accounts: "Account Management",
  logs: "Logs",
  ...(DEV_TOOLS ? { store: "Template Store", templates: "System Templates",
                    licenses: "Licenses" } : {}),
  settings: "Settings",
};

export default function HubShell() {
  const { brand, user } = useApp();
  // The developer panels need TWO facts: the account is a developer, and
  // this box carries the owner's tooling (core/deploy.py mounted core/dev;
  // the server reports it as user.dev_tools). A dev frontend build on a box
  // in customer mode has the panels and none of the routes behind them, and
  // used to draw every button so it could fail with a 404 or a 405.
  const devTools = !!user?.is_developer && !!user?.dev_tools;
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
    setSection(page);
  }
  // ...and from a dialog that has no `go` to call: Edit system and the
  // create wizard's "Settings > Network > Software port" link
  // (boxAccess.jsx goToSoftwarePort).
  useEffect(() => {
    const onGoto = (e) => go(e.detail?.page || "settings", e.detail?.tab);
    window.addEventListener("irivai:goto", onGoto);
    return () => window.removeEventListener("irivai:goto", onGoto);
  });
  // Which folder "New system" was opened FROM, if any -- so the system
  // lands there instead of Unfiled without a separate move afterwards.
  const [pendingFolder, setPendingFolder] = useState("");
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
        <Sidebar collapsible="icon">
          <SidebarHeader>
            <div className="flex items-center gap-2 px-1 py-1.5">
              {/* The deployment's vendor mark, same asset the masters show in
                  their top bar. Read from brand.toml so a rebranded deployment
                  swaps it with no code change; the path is stored without a
                  leading slash there. */}
              <img
                src={"/" + String(brand?.logo || "static/img/cytron.png").replace(/^\/+/, "")}
                alt=""
                className="size-8 shrink-0 rounded-lg object-contain"
              />
              <div className="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:hidden">
                <span className="truncate text-sm font-semibold">{brand?.product_name || "IRIV Edge AI Studio"}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {cards.length
                    ? `${running} of ${cards.length} running`
                    : "no systems yet"}
                </span>
              </div>
              {/* Home. The brand is a LINK back to the systems grid on a
                  master, but on the hub it was nothing at all -- so getting
                  back from Settings meant finding "Systems" in the rail. The
                  icon is the same door on every shell. Hidden with the rest of
                  the header when the rail collapses to icons, where the nav
                  itself is one click away. */}
              <Button variant="ghost" size="icon" title="Home"
                      className="size-7 shrink-0 group-data-[collapsible=icon]:hidden"
                      onClick={() => setSection("systems")}>
                <Home className="size-4" />
              </Button>
            </div>
          </SidebarHeader>
          <SidebarContent>
            {NAV.filter((grp) => (!grp.adminOnly || user?.is_admin)
                              && (!grp.siteAdmin || user?.is_site_admin)).map((grp) => (
              <SidebarGroup key={grp.group}>
                <SidebarGroupLabel>{grp.group}</SidebarGroupLabel>
                <SidebarMenu>
                  {grp.items
                    .filter((it) => (!it.devOnly || devTools)
                                 && (!it.adminOnly || user?.is_admin))
                    .map((it) => (
                    <SidebarMenuItem key={it.id}>
                      <SidebarMenuButton
                        isActive={section === it.id}
                        tooltip={it.hint}
                        onClick={() => {
                          // Clicking Settings always opens Accounts first (Tok
                          // Aba, 2026-10-21): the remembered tab is for coming
                          // back from a notice's link, not for the rail.
                          if (it.id === "settings") {
                            try { localStorage.setItem("hub.settings.tab", "accounts"); }
                            catch { /* private mode */ }
                            setGoto((n) => n + 1);
                          }
                          setSection(it.id);
                        }}
                      >
                        <it.icon />
                        <span>{it.label}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroup>
            ))}
          </SidebarContent>
          <SidebarFooter>
            <SessionMenu />
            {/* The running platform version. Small, quiet, and always the
                same place: a box in the field is supported by asking what it
                is running, and "check the About page" is a phone call. Hidden
                when the sidebar collapses to icons. */}
            {data?.version ? (
              <div className="px-2 pb-1 text-[10px] leading-none text-muted-foreground group-data-[collapsible=icon]:hidden">
                v{data.version}
              </div>
            ) : null}
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>

        <SidebarInset className="bg-muted/40">
          <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background/70 px-4 backdrop-blur-md">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-1 h-4" />
            {/* Title only. The subtitle repeated what the page itself says the
                moment you look at it, on every page, forever -- a sentence you
                have read a hundred times is furniture, not information. */}
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-sm font-semibold leading-tight">{head}</h1>
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
                <Hub onCreate={(folderId, template) => {
                  setPendingFolder(folderId || "");
                  setPendingTemplate(template || "");
                  setSection("vision");
                }}
                     onBrowseStore={devTools ? () => setSection("store") : null} />
              </Suspense>
            )}
            {section === "control"   && <ControlPanel />}
            {section === "health"    && <HealthPanel />}
            {section === "wall"      && <LiveWallPanel />}
            {/* .cy-legacy: the Log Hub is a legacy-styled page (theme.css),
                so it needs the legacy meanings of --muted and --accent back
                inside this shadcn shell - exactly as MasterShell wraps its
                routed pages. Without it the page's secondary text is painted
                with a BACKGROUND colour and reads dark-on-dark.
                Performance is LINKED, not embedded: HealthPanel is one
                section away and one reading of CPU, RAM, FPS, Hailo and
                temperature is enough for the box. */}
            {section === "logs" && user?.is_admin && (
              <div className="cy-legacy">
                <Suspense fallback={<div className="p-6 text-muted-foreground">Loading…</div>}>
                  <LogHub hub onPerformance={() => setSection("health")} />
                </Suspense>
              </div>
            )}
            {/* Lazy chunk, absent from a customer build -- hence the null
                guard as well as the developer check, same as the two below. */}
            {section === "store"     && devTools && StorePanel && (
              <Suspense fallback={<div className="muted p-6">Loading…</div>}>
                <StorePanel />
              </Suspense>
            )}
            {/* Creating a system is reached from the Systems grid's "New
                system" card or a template card - it is not a nav
                destination of its own. */}
            {section === "vision"    && (
              <VisionPanel
                 template={pendingTemplate}
                 onCreated={async (created) => {
                   // Metadata-only: the same call the "move to folder" menu
                   // action makes. Best-effort -- a failed assign here must
                   // not undo a system that was just successfully created.
                   if (pendingFolder && created?.slug) {
                     try {
                       await api.post(`/api/hub/systems/${encodeURIComponent(created.slug)}/group`,
                                      { group: pendingFolder });
                     } catch { /* the system still exists; fix the folder by hand */ }
                   }
                   setPendingFolder("");
                   setPendingTemplate("");
                   setSection("systems");
                   reload().catch(() => {});
                 }}
                 onCancel={() => { setPendingFolder(""); setPendingTemplate("");
                                   setSection("systems"); }}
                 onBrowseStore={devTools ? () => setSection("store") : null} />
            )}
            {/* One page, its own tab strip. The developer-only tabs are
                spread away in a customer build rather than hidden, exactly as
                they were when they had their own rail entries. */}
            {/* A SITE ADMIN reaches Settings too, with fewer tabs in it --
                their own staff, retention, mail, Telegram, network. The tab
                list comes from the server (auth.settings_tabs), because
                deciding it here would mean the page and the endpoints behind
                it could disagree about who may do what. */}
            {section === "settings" && user?.is_site_admin && (
              <SettingsHub key={goto} devTools={devTools}
                           tabs={user?.settings_tabs} />
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

function VisionPanel({ template, onCreated, onCancel, onBrowseStore }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PanelCard>
        <CreateSystemWizard template={template} onCreated={onCreated} onCancel={onCancel}
                            onBrowseStore={onBrowseStore} />
      </PanelCard>
    </div>
  );
}
