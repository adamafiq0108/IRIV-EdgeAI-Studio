// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// UserMenu - the hub's top-right account button: who you are, the pages that are not
// day-to-day (Settings, About), and the ways to leave (sign out, reboot, shut down).
//
// It replaces three things in the left rail -- the account button at the bottom, the
// Settings entry and the Performance entry -- so the rail holds only what is used all
// the time (Systems). A round avatar with a green "online" dot opens a menu.
//
// Reboot and Shut down are the site admin's and above, as in SessionMenu: whoever runs
// the operation is the person in front of the box when a camera wedges. Settings is the
// site admin's too (what they may open inside is decided server-side).
import { useState } from "react";

import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { useToast } from "./ui/Toast.jsx";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "./ui/dropdown-menu.jsx";
import { SignOutDialog } from "./SignOutDialog.jsx";
import { ChevronDown, Home, Info, LogOut, Power, RotateCw, Settings } from "lucide-react";

// A steady colour per person, so the avatar is theirs rather than everybody's blue.
const TONES = ["#b45309", "#0e7490", "#7c3aed", "#be185d", "#15803d", "#1d4ed8", "#c2410c"];
function toneOf(name) {
  let h = 0;
  for (const ch of String(name || "?")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TONES[h % TONES.length];
}

export default function UserMenu({ onSettings, onAbout, onHome, version }) {
  const { user } = useApp();
  const toast = useToast();
  const confirm = useConfirm();
  const [signOutOpen, setSignOutOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const mayPower = !!user?.is_site_admin;
  const name = user?.name || "guest";
  const initial = name.trim().charAt(0).toUpperCase() || "?";

  async function power(action) {
    const shutdown = action === "shutdown";
    if (!await confirm({
      title: shutdown ? "Shut down this box?" : "Reboot this box?",
      description: shutdown
        ? "Every running system stops, and it can only be switched back on at "
          + "the machine."
        : "Every running system stops and comes back with it, which takes "
          + "about a minute.",
      destructive: shutdown,
      confirmLabel: shutdown ? "Shut down" : "Reboot",
    })) return;
    setBusy(true);
    try {
      await api.post("/api/hub/panel/power", { action });
      // No redirect and no reload: the box is going away.
      toast.success(shutdown ? "Shutting down" : "Rebooting",
                    shutdown ? "This page stops answering in a moment."
                             : "Reload in a minute or so.");
    } catch (e) {
      toast.error(shutdown ? "Could not shut down" : "Could not reboot", e.message);
    } finally { setBusy(false); }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<button type="button" disabled={busy} data-user-menu
                          aria-label={`Account menu for ${name}`} title={name}
                          className="group/user flex items-center gap-1.5 rounded-full p-0.5 pr-1.5
                                     outline-none transition-colors hover:bg-muted focus-visible:ring-2
                                     focus-visible:ring-ring disabled:opacity-60" />}>
          <span className="relative inline-flex">
            <span className="flex size-9 items-center justify-center rounded-full text-sm font-semibold
                             text-white shadow-sm" style={{ background: toneOf(name) }}>
              {initial}
            </span>
            {/* Online: the page is talking to the box. */}
            <span className="absolute -bottom-0.5 -right-0.5 size-3 rounded-full bg-emerald-500
                             ring-2 ring-background" />
          </span>
          <ChevronDown className="size-3.5 text-muted-foreground transition-transform
                                  group-data-[popup-open]/user:rotate-180" />
        </DropdownMenuTrigger>
        <DropdownMenuContent side="bottom" align="end" sideOffset={8} className="w-64 p-1.5">
          {/* A menu label must sit inside a group (Base UI error #31 otherwise). */}
          <DropdownMenuGroup>
          <DropdownMenuLabel className="flex items-center gap-3 px-2 py-2">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full text-base
                             font-semibold text-white" style={{ background: toneOf(name) }}>
              {initial}
            </span>
            <span className="grid min-w-0 leading-tight">
              <span className="truncate text-sm font-semibold text-foreground">{name}</span>
              <span className="truncate text-xs font-normal text-muted-foreground">
                {user?.role_label || "Signed in"}
              </span>
            </span>
          </DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          {onHome && (
            <DropdownMenuItem className="gap-2.5 py-2" onClick={onHome}>
              <Home /> All systems
            </DropdownMenuItem>
          )}
          {onSettings && user?.is_site_admin && (
            <DropdownMenuItem className="gap-2.5 py-2" onClick={onSettings}>
              <Settings /> Settings
            </DropdownMenuItem>
          )}
          {onAbout && (
            <DropdownMenuItem className="gap-2.5 py-2" onClick={onAbout}>
              <Info /> About
            </DropdownMenuItem>
          )}
          {(onHome || onSettings || onAbout) && <DropdownMenuSeparator />}
          <DropdownMenuItem className="gap-2.5 py-2" onClick={() => setSignOutOpen(true)}>
            <LogOut /> Sign out
          </DropdownMenuItem>
          {mayPower && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="gap-2.5 py-2" onClick={() => power("reboot")}>
                <RotateCw /> Reboot
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" className="gap-2.5 py-2"
                                onClick={() => power("shutdown")}>
                <Power /> Shut down
              </DropdownMenuItem>
            </>
          )}
          {/* The running platform version: a box in the field is supported by asking what it
              is running. */}
          {version ? (
            <div className="px-2 pb-1 pt-2 text-[10px] leading-none text-muted-foreground">
              v{version}
            </div>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <SignOutDialog open={signOutOpen} onOpenChange={setSignOutOpen} username={user?.name} />
    </>
  );
}
