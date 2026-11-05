// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// SessionMenu - the sidebar's bottom button: who you are, and the three ways
// to leave.
//
// It used to sign out on click and nothing else, so the two OTHER ways of
// ending a session -- rebooting the box, shutting it down -- lived only on
// Settings > Front Panel, several clicks from the button somebody presses when
// they are done. They belong on the same control: "I am finished here" is one
// thought with three answers.
//
// Reboot and Shut down are the site admin's and above. Whoever runs the
// operation is the person in front of the box when a camera wedges, and "ask
// the owner to reboot" is an outage with a phone call in the middle of it --
// while anybody in the room can already do it by holding the button in. An
// ordinary account cannot: the route says the same thing, and this only
// decides whether the item is drawn.
import { useState } from "react";

import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { useToast } from "./ui/Toast.jsx";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu.jsx";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "./ui/sidebar.jsx";
import { SignOutDialog } from "./SignOutDialog.jsx";
import { LogOut, Power, RotateCw } from "lucide-react";

export default function SessionMenu() {
  const { user } = useApp();
  const toast = useToast();
  const confirm = useConfirm();
  const [signOutOpen, setSignOutOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const mayPower = !!user?.is_site_admin;

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
      // No redirect and no reload: the box is going away, and a page that
      // navigates now lands on a port that has stopped answering. Saying what
      // is happening is the whole of the job left.
      toast.success(shutdown ? "Shutting down" : "Rebooting",
                    shutdown ? "This page stops answering in a moment."
                             : "Reload in a minute or so.");
    } catch (e) {
      toast.error(shutdown ? "Could not shut down" : "Could not reboot",
                  e.message);
    } finally { setBusy(false); }
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          {/* `render`, not `asChild`. This menu is Base UI, which composes
              through a render prop and ignores `asChild` entirely -- so the
              trigger drew its OWN button around the sidebar button, and a
              nested auto-width button shrank the row to a pill floating in
              the middle of the rail instead of the full-width row every
              other item in the sidebar is. */}
          <DropdownMenuTrigger
            render={<SidebarMenuButton tooltip="Session" disabled={busy}
                                       className="w-full" />}>
            <LogOut />
            <span className="truncate">
              {user?.name || "guest"}
              {user?.role_label ? ` · ${user.role_label}` : ""}
            </span>
          </DropdownMenuTrigger>
          {/* Above the button, because the button is at the bottom of the
              rail and a menu opening downwards would open off-screen. */}
          <DropdownMenuContent side="top" align="start" className="w-48">
            <DropdownMenuItem onClick={() => setSignOutOpen(true)}>
              <LogOut /> Sign out
            </DropdownMenuItem>
            {mayPower && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => power("reboot")}>
                  <RotateCw /> Reboot
                </DropdownMenuItem>
                <DropdownMenuItem variant="destructive"
                                  onClick={() => power("shutdown")}>
                  <Power /> Shut down
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
      <SignOutDialog open={signOutOpen} onOpenChange={setSignOutOpen}
                     username={user?.name} />
    </SidebarMenu>
  );
}
