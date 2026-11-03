// StandaloneShell - the shell for a standalone system: ONE local board running
// its own dashboard and AI, not the head or member of a master/slave group.
//
// It shares MasterShell's shadcn sidebar template so the product still reads as
// one thing, but drops the two fleet affordances that make no sense on a single
// board: the Devices tree/flyout (there is one board, not a fleet to scope) and
// the hub back-link (a standalone system belongs to no hub). The page nav is
// grouped into local-board sections - Board, AI, System - instead of one flat
// "Pages" list, because a standalone operator thinks in "what is my board
// doing" not "which system am I managing".
//
// The routed pages themselves are the same capability-gated pages MasterShell
// renders (theme.css / .cy-legacy); this shell only reframes the navigation.
import { useMemo, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { useApp, PAGE_PATHS, navRedirect, atPath } from "../state/app.jsx";
import { goWithSession, withSession } from "../lib/session.js";
import SessionMenu from "./SessionMenu.jsx";
import {
  SidebarProvider, Sidebar, SidebarHeader, SidebarContent, SidebarFooter,
  SidebarGroup, SidebarGroupLabel, SidebarMenu, SidebarMenuItem, SidebarMenuButton,
  SidebarInset, SidebarTrigger, SidebarRail,
} from "./ui/sidebar.jsx";
import { Separator } from "./ui/separator.jsx";
import { TooltipProvider } from "./ui/tooltip.jsx";
import { Button } from "./ui/button.jsx";
import FullscreenButton from "./FullscreenButton.jsx";
import { useTheme } from "../hooks/use-theme.js";
import HardwareFooter from "./HardwareFooter.jsx";
import SetupWizard from "./SetupWizard.jsx";
import CapacityMeters from "./CapacityMeters.jsx";
import { LogOut, Moon, Sun, Wrench, Cpu, ChevronRight, Home } from "lucide-react";
// Icons and section order live in ONE module, shared with the "next page"
// button every page shows top-right -- the rail, the breadcrumb and that
// button must all agree on what the order of a section is.
import { PAGE_ICONS, SECTIONS } from "./nav-sections.js";

function groupNav(nav) {
  const byPage = new Map(nav.map((it) => [it.page, it]));
  const placed = new Set();
  const groups = SECTIONS.map((sec) => {
    const items = sec.pages
      .filter((p) => byPage.has(p))
      .map((p) => { placed.add(p); return byPage.get(p); });
    return { label: sec.label, items };
  });
  // Anything the backend nav emitted that no section claims - keep it, in a
  // trailing "More" group, rather than silently dropping it.
  const leftover = nav.filter((it) => !placed.has(it.page));
  if (leftover.length) groups.push({ label: "More", items: leftover });
  return groups.filter((g) => g.items.length);
}

export default function StandaloneShell() {
  const { brand, user, system, nav, hubUrl } = useApp();
  const [dark, setDark] = useTheme();
  const location = useLocation();


  const navItems = nav;
  const groups = groupNav(navItems);
  const barred = navRedirect(navItems, location.pathname);

  // Which groups are collapsed, remembered across visits -- a rail an
  // operator tidied that untidies itself on the next page load is not a
  // preference, it is a nuisance. Stored as the CLOSED set (`false` entries)
  // so a group added to SECTIONS later starts open rather than hidden.
  const [openGroups, setOpenGroups] = useState(() => {
    try { return JSON.parse(localStorage.getItem("nav.groups") || "{}") || {}; }
    catch { return {}; }
  });
  const toggleGroup = (label) => setOpenGroups((cur) => {
    const next = { ...cur, [label]: cur[label] === false };
    try { localStorage.setItem("nav.groups", JSON.stringify(next)); }
    catch { /* private mode: the rail still works, it just forgets */ }
    return next;
  });

  // Header title = the active nav item; /manage/:host isn't in the nav.
  const active = navItems.find((it) => {
    const to = PAGE_PATHS[it.page];
    return atPath(location.pathname, to);
  });
  const title = location.pathname.startsWith("/manage/") ? "Manage device"
    : active?.label || system?.display_name || "Dashboard";

  // The group holding the CURRENT page is always shown, whatever the stored
  // preference says. Collapsing a group is about hiding what you are not
  // using; hiding the page you are looking at -- so the rail shows nothing
  // highlighted -- is a different and much worse thing.
  const activeGroupObj = active
    && groups.find((g) => g.items.some((it) => it.page === active.page));
  const activeGroup = activeGroupObj?.label;
  // The section's pages BEFORE the current one, in their setup order -- the
  // breadcrumb's middle. Collapsed on small screens (the sm: classes below):
  // a five-step trail on a phone would push the theme button off the edge.
  const trail = activeGroupObj
    ? activeGroupObj.items.slice(
        0, activeGroupObj.items.findIndex((it) => it.page === active.page))
      .filter((it) => PAGE_PATHS[it.page])
    : [];

  // Brand block. A standalone system usually belongs to no hub, so there is
  // nothing to link back to -- but if a hub IS configured (this board runs a
  // hub alongside its system), link back to it, same as a master does.
  const brandBlock = (
    <div className="flex items-center gap-2 px-1 py-1.5">
      <img
        src={"/" + String(brand?.logo || "static/img/cytron.png").replace(/^\/+/, "")}
        alt=""
        className="size-8 shrink-0 rounded-lg object-contain"
      />
      <div className="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:hidden">
        <span className="truncate text-sm font-semibold">{brand?.product_name || "IRIV Edge AI Studio"}</span>
        <span className="truncate text-xs text-muted-foreground">
          {system?.display_name || "standalone"}
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
      {/* has-hw-footer publishes --hw-footer-h to everything inside, so the
          pages that size themselves to the viewport can subtract the strip
          (theme.css). */}
      <SidebarProvider className="cy-app has-hw-footer">
        <Sidebar collapsible="icon">
          <SidebarHeader>
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
            {/* Board identity sits at the top where the fleet Devices tree is
                on a master - a standalone board is its own single "device".
                Board health lives on the hub's Health page, so this is a
                LABEL, not a link.
                Rendered as a plain div rather than SidebarMenuButton: that
                component is a real <button>, so it took a hand cursor, a
                hover highlight and keyboard focus, and every one of those
                promises a click that does nothing. Same padding/size
                classes so it still lines up with the nav items under it. */}
            <SidebarGroup>
              <SidebarMenu>
                <SidebarMenuItem>
                  <div
                    className="flex h-8 w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm text-sidebar-foreground/70 [&>svg]:size-4 [&>svg]:shrink-0"
                    title={system?.display_name || "This board"}
                  >
                    <Cpu />
                    <span className="truncate group-data-[collapsible=icon]:hidden">
                      {system?.display_name || "This board"}
                    </span>
                  </div>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>

            {groups.map((group) => (
              <SidebarGroup key={group.label}>
                {/* Collapsible: seven groups of pages is a long rail, and the
                    ones an operator is not using are pure scroll. The group
                    holding the CURRENT page is force-opened below, so
                    collapsing can never hide where you already are. */}
                <SidebarGroupLabel
                  onClick={() => toggleGroup(group.label)}
                  style={{ cursor: "pointer", userSelect: "none",
                           display: "flex", alignItems: "center", gap: 4 }}
                  title={(openGroups[group.label] === false && group.label !== activeGroup) ? "Expand" : "Collapse"}
                >
                  <ChevronRight size={11} aria-hidden
                                style={{ flex: "none", transition: "transform .15s ease",
                                         transform: `rotate(${(openGroups[group.label] === false && group.label !== activeGroup) ? 0 : 90}deg)` }} />
                  {group.label}
                </SidebarGroupLabel>
                <SidebarMenu hidden={(openGroups[group.label] === false && group.label !== activeGroup)}>
                  {group.items.map((it) => {
                    const to = PAGE_PATHS[it.page];
                    if (!to) return null;
                    const Icon = PAGE_ICONS[it.page] || Wrench;
                    const isActive = to === "/"
                      ? location.pathname === "/"
                      : atPath(location.pathname, to);
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
            ))}
          </SidebarContent>
          <SidebarFooter>
            <SessionMenu />
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>

        <SidebarInset className="bg-muted/40">
          <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background/70 px-4 backdrop-blur-md">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-1 h-4" />
            {/* The page name, and nothing else. The system's name is already
                in the sidebar's brand block and on the board row above the
                nav, so repeating it here -- with the word "standalone", which
                names a deployment shape nobody using this box has to think
                about -- was a second line saying nothing new. */}
            {/* Breadcrumb: the section, then every page of that section UP TO
                the current one -- the pages in a section are ordered as a
                setup sequence (models, then detection, then zones, then
                workflow), so the trail reads as "the steps that got me here":

                  AI Configuration › AI Models › Detection › Zones › Workflow

                and each earlier step is a link, because "go back one step" is
                exactly what a breadcrumb is for. Pages AFTER the current one
                are left off: a breadcrumb states where you are, not what
                remains -- the sidebar already lists the whole section. */}
            <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
              <ol className="flex min-w-0 items-center gap-1.5 text-sm leading-tight">
                {activeGroup && (
                  <>
                    <li className="shrink-0 truncate text-muted-foreground">{activeGroup}</li>
                    <li aria-hidden className="shrink-0 text-muted-foreground/60">›</li>
                  </>
                )}
                {trail.map((it) => (
                  <li key={it.page} className="hidden shrink-0 items-center gap-1.5 sm:flex">
                    <NavLink to={PAGE_PATHS[it.page]}
                             className="truncate text-muted-foreground transition-colors hover:text-foreground">
                      {it.label}
                    </NavLink>
                    <span aria-hidden className="text-muted-foreground/60">›</span>
                  </li>
                ))}
                <li className="min-w-0 truncate font-semibold" aria-current="page">
                  {title}
                </li>
              </ol>
            </nav>
            {/* The same box-wide meters the hub header carries: an operator
                inside a system should not have to go back to the hub to see
                whether another can be started. */}
            <CapacityMeters />
            {/* Beside the theme toggle: both are "how this screen is shown",
                and a shop-floor panel wants the browser chrome gone. */}
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
        {/* The guided setup, floating bottom-right on every page. Mounted in
            BOTH shells: an appliance is the standalone one, so putting it only
            in MasterShell meant the guide existed everywhere except on the
            boxes that ship to customers. Renders nothing once the tour is
            finished or skipped. */}
        <SetupWizard />
      </SidebarProvider>
    </TooltipProvider>
  );
}
