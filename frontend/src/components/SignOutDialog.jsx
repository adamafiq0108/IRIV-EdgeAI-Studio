// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// SignOutDialog - the one sign-out confirmation, used by every shell.
//
// This replaces window.confirm("Sign out?"): the native box cannot be styled,
// names the host:port as if the PRODUCT were asking, and on some browsers is
// suppressed entirely. Signing out is deliberately NOT destructive-styled -
// nothing is lost, the account just leaves the session.
import { useState } from "react";
import { api, loginUrl } from "../api/client.js";
import { clearToken } from "../lib/session.js";
import { ConfirmDialog } from "./ui/dialogs.jsx";

export function SignOutDialog({ open, onOpenChange, username }) {
  const [busy, setBusy] = useState(false);

  async function signOut() {
    setBusy(true);
    // Even if the logout call fails (expired session, box mid-restart), the
    // right destination is still the login page. This tab's own token goes
    // regardless -- signing out must never leave a live identity behind.
    let hub = "";
    try {
      const d = await api.post("/api/auth/logout");
      hub = d?.hub_url || "";
    } catch { /* ignore */ }
    clearToken();
    // Out the same door a sign-in comes in: on a master, the hub's login. A
    // login screen on a system port is a door into ONE system, and the next
    // person at this browser should be met by the board. No session travels
    // with it -- the whole point of this click is that there isn't one.
    // `undefined`, not "" -- an empty string would override what the shell
    // remembered at boot and what the port fallback knows. A logout whose
    // request never landed still has to leave for the hub.
    window.location.assign(loginUrl(hub || undefined));
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(v) => { if (!busy) onOpenChange(v); }}
      title="Sign out?"
      description={username
        ? `${username} will be signed out on this browser. Systems keep running.`
        : "You will be signed out on this browser. Systems keep running."}
      confirmLabel="Sign out"
      busy={busy}
      onConfirm={signOut}
    />
  );
}
