// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// MasterShell - a master's shell (ports 511x), built on the SAME shadcn sidebar
// template as HubShell so the hub and every system read as one product. The old
// horizontal topbar nav moves into the sidebar as a "Pages" group; the device
// tree (folders / scoping / device actions) embeds below it unchanged; the
// header carries the same collapse trigger and light/dark toggle as the hub.
//
// The page content itself is still the legacy-styled pages (theme.css). The
// shell root is .cy-app (shadcn token meanings), so the content wrapper carries
// .cy-legacy to restore the two colliding token meanings for that subtree -
// see index.css.
import { useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { useApp, PAGE_PATHS, navRedirect, atPath } from "../state/app.jsx";
import { goWithSession, withSession } from "../lib/session.js";
import DeviceTree from "./Sidebar.jsx";
import UserMenu from "./UserMenu.jsx";
import GuideSwitch from "./GuideSwitch.jsx";
import SetupGuideItem from "./SetupGuideItem.jsx";
import HardwareFooter from "./HardwareFooter.jsx";
import SetupWizard from "./SetupWizard.jsx";
import CapacityMeters from "./CapacityMeters.jsx";
import {
  SidebarProvider, Sidebar, SidebarHeader, SidebarContent, SidebarFooter,
  SidebarGroup, SidebarGroupLabel, SidebarMenu, SidebarMenuItem, SidebarMenuButton,
  SidebarInset, SidebarTrigger, SidebarRail,
} from "./ui/sidebar.jsx";
import { TooltipProvider } from "./ui/tooltip.jsx";
import { Button } from "./ui/button.jsx";
import FullscreenButton from "./FullscreenButton.jsx";
import GuideButton from "./GuideButton.jsx";
import { useTheme } from "../hooks/use-theme.js";
import {
  LayoutDashboard, BarChart3, Film,
  ScrollText, Hammer, Boxes, Plug,
  LogOut, Moon, Sun, Wrench, HardDrive, ChevronRight, ScanEye, TableProperties,
  Shapes, Camera, Images, Workflow as WorkflowIcon, FileClock,
  Database as DatabaseIcon, Home,
} from "lucide-react";

// Icons keyed by nav page id (core/services/nav.py). A page without one still
// renders - the label is the content, the icon is a courtesy.
const PAGE_ICONS = {
  main: LayoutDashboard,
  detection: ScanEye, zones: Shapes, workflow: WorkflowIcon,
  analytics: BarChart3, events: TableProperties,
  database: DatabaseIcon,
  footage: Film, dataset: Camera, dataset_library: Images,
  audit: ScrollText, logs: FileClock,
  builder: Hammer, models: Boxes, blynk: Plug,
};

export default function MasterShell() {
  const { brand, user, system, nav, sidebar, hubUrl, scope } = useApp();
  const [dark, setDark] = useTheme();
  const location = useLocation();

  const navItems = nav;
  const barred = navRedirect(navItems, location.pathname);

  // Devices flyout: the sidebar item expands the device tree in a panel to the
  // RIGHT of the rail rather than inline, so a deep folder tree never squeezes
  // the page nav. No outside-click dismiss on purpose: the tree opens its own
  // portalled dialogs and menus (rename, move, device actions), and an
  // outside-click close would swallow the first click on any of them. It
  // closes on Escape, on toggling the item, and on navigation.
  const [devicesOpen, setDevicesOpen] = useState(false);
  const [panelLeft, setPanelLeft] = useState(0);
  useEffect(() => { setDevicesOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!devicesOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setDevicesOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [devicesOpen]);
  function toggleDevices(e) {
    const r = e.currentTarget.getBoundingClientRect();
    setPanelLeft(r.right + 10);
    setDevicesOpen((v) => !v);
  }


  // Header title = the active nav item; /manage/:host isn't in the nav.
  const active = navItems.find((it) => {
    const to = PAGE_PATHS[it.page];
    return atPath(location.pathname, to);
  });
  const title = location.pathname.startsWith("/manage/") ? "Manage device"
    : active?.label || system?.display_name || "Dashboard";

  const brandBlock = (
    <div className="flex items-center gap-2 px-1 py-1.5">
      <img
        src={"/" + String(brand?.logo || "static/img/cytron.png").replace(/^\/+/, "")}
        alt=""
        className="size-8 shrink-0 rounded-lg object-contain"
      />
      <div className="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:hidden">
        <span className="truncate text-sm font-semibold">{brand?.product_name || "IRIV EdgeAI Studio"}</span>
        <span className="truncate text-xs text-muted-foreground">
          {system?.display_name || "master"}
        </span>
      </div>
      {/* The header is already a link to the hub -- this says so. A link that
          looks like nothing is a link nobody clicks. */}
      {hubUrl && (
        <Home className="size-4 shrink-0 text-muted-foreground group-data-[collapsible=icon]:hidden" />
      )}
    </div>
  );

  return (
    <TooltipProvider delayDuration={200}>
      {/* .cy-app re-asserts the shadcn design tokens for this subtree - the
          legacy theme.css redefines some of the same names (see index.css). */}
      {/* has-hw-footer publishes --hw-footer-h to everything inside, so the
          pages that size themselves to the viewport can subtract the strip
          (theme.css). It is a class rather than a global value so a shell
          without the footer loses no height to it. */}
      <SidebarProvider className="cy-app has-hw-footer">
        <Sidebar collapsible="icon">
          <SidebarHeader>
            {/* Brand links back to the Systems hub, like the old topbar did.
                The hub is another port: the click carries this tab's identity
                in the fragment (see lib/session.js). */}
            {hubUrl
              ? <a href={hubUrl} title="Back to Systems"
                   onClick={(e) => {
                     if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                     e.preventDefault();
                     goWithSession(withSession(hubUrl));
                   }}>{brandBlock}</a>
              : brandBlock}
          </SidebarHeader>
          <SidebarContent>
            {/* Devices sits ABOVE the page nav; clicking expands the tree in a
                flyout to the right. The active folder scope shows in the label
                so a filtered view is never a mystery. */}
            {sidebar ? (
              <SidebarGroup>
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton
                      isActive={devicesOpen || scope?.kind === "folder"}
                      tooltip="Devices"
                      onClick={toggleDevices}
                    >
                      <HardDrive />
                      <span>
                        Devices{scope?.kind === "folder" && scope.name ? ` · ${scope.name}` : ""}
                      </span>
                      <ChevronRight
                        className={"ml-auto size-4 transition-transform " + (devicesOpen ? "rotate-90" : "")}
                      />
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroup>
            ) : null}

            <SidebarGroup>
              <SidebarGroupLabel>Pages</SidebarGroupLabel>
              <SidebarMenu>
                {navItems.map((it) => {
                  const to = PAGE_PATHS[it.page];
                  if (!to) return null; // a system.extra_page with no known route
                  const Icon = PAGE_ICONS[it.page] || Wrench;
                  const isActive = atPath(location.pathname, to);
                  return (
                    <SidebarMenuItem key={it.page}>
                      <SidebarMenuButton
                        isActive={isActive}
                        tooltip={it.label}
                        render={<NavLink to={to} />}
                      >
                        <Icon />
                        <span>{it.label}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroup>

          </SidebarContent>
          <SidebarFooter>
            <GuideSwitch />
            <SetupGuideItem />
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>

        {/* Devices flyout - the device tree (folders, scoping, device actions,
            move/rename) unchanged, just housed in a panel beside the rail. */}
        {devicesOpen && sidebar ? (
          <div
            className="cy-app fixed inset-y-0 z-40 w-80 max-w-[85vw] overflow-hidden border-r bg-sidebar shadow-xl animate-in fade-in slide-in-from-left-2"
            style={{ left: panelLeft }}
            role="region"
            aria-label="Devices"
          >
            <div className="cy-legacy h-full overflow-y-auto p-3">
              <DeviceTree embedded />
            </div>
          </div>
        ) : null}

        <SidebarInset className="bg-muted/40">
          <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background/70 px-4 backdrop-blur-md">
            <SidebarTrigger className="-ml-1" />
            <div className="min-w-0 flex-1">
              {/* Page name only. The system's name is already in the brand
                  block at the top of the sidebar; a second copy here was a
                  line that never told anyone anything new. */}
              <h1 className="truncate text-sm font-semibold leading-tight">{title}</h1>
            </div>
            {/* Same box-wide meters as the hub header -- they must not vanish
                just because an operator opened a system. */}
            <CapacityMeters />
            {/* Beside the theme toggle: both are "how this screen is shown",
                and a shop-floor panel wants the browser chrome gone. */}
            <GuideButton />
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
            {/* The same account button as the hub's top bar: sign out, reboot, shut down, and the
                way back to the list of systems. */}
            <UserMenu onHome={hubUrl ? () => goWithSession(withSession(hubUrl)) : undefined} />
          </header>
          {/* .cy-legacy: the routed pages are legacy-styled. mx-auto/max-w
              mirrors the old .main container (without its topbar margin). */}
          <main className="cy-legacy flex-1 p-4 sm:p-6">
            <div className="mx-auto w-full max-w-[1500px]">
              {/* A typed URL into a category the admin hid for this account
                  bounces to the first page it may see - the nav omitting a
                  page and the router serving it anyway would be a lock with
                  the door open. */}
              {barred ? <Navigate to={barred} replace /> : <Outlet />}
            </div>
          </main>
          {/* Below <main>, in flow: the strip can never cover a live feed, a
              measurement overlay or an OCR pane, whatever the page does. */}
          <HardwareFooter />
        </SidebarInset>
        {/* The guided setup, floating bottom-right on every page rather than
            waiting on the pages it is trying to send you to. Renders nothing
            once the tour is finished or skipped. */}
        <SetupWizard />
      </SidebarProvider>
    </TooltipProvider>
  );
}
