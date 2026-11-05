// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// boxAccess - how ONE SYSTEM is reached, as Edit system and the create
// wizard say it (Tok Aba, 2026-10-07).
//
// THE HUB DECIDES. The access method belongs to the box, set on the Software
// port card (core/services/webaccess.py): the Hub on the proxy means every
// system is on the proxy, the Hub on ports means every system is back on its
// own port. So these dialogs offer no switch and keep no per-system mode;
// they read what the card reads (GET /api/hub/port) and say which applies:
//
//   proxy  "Uses the proxy, like the Hub: http://<box>/s/<slug>/". The
//          system's own port stays (the hub relays to it), so it is still
//          editable, as an internal detail behind Advanced.
//   port   today's port field, with a note pointing at the card.
//
// The links themselves follow the Hub with nothing stored per system: every
// address is made from the box's mode at the moment it is asked for
// (core/net.py rewrite -> weblinks.link), so switching the Hub back to ports
// returns every card, back link and redirect to its port at once.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";

export const PROXY = "proxy";

//: The stream line under the proxy: MJPEG rides the page's own address,
//: WebRTC media still needs the gateway's UDP port (scripts/mediamtx).
export const STREAM_PROXY = "Its video stream goes through the same address (MJPEG); "
  + "live WebRTC video still uses the box's UDP port 8189.";

/** True when the Hub is on the proxy, false on ports, null while unknown or
 *  when the server has no access setting. The mode IN FORCE, not a saved
 *  one waiting for a restart: that is what the system's address is now. */
export function useHubProxy(open) {
  const [proxy, setProxy] = useState(null);
  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    setProxy(null);
    api.get("/api/hub/port")
      .then((d) => { if (live) setProxy(d?.access ? d.access.mode === PROXY : false); })
      // An unanswered ask is the ports layout, as before: null is only for
      // "not known YET", so a caller can hold back what depends on it.
      .catch(() => { if (live) setProxy(false); });
    return () => { live = false; };
  }, [open]);
  return proxy;
}

/** http://<box>/s/<slug>/ on the host this page was opened by. */
export function proxyAddress(slug, loc = window.location) {
  return `${loc.protocol}//${loc.hostname}/s/${slug || "<system>"}/`;
}

/** Open Settings > Network on the Hub (HubShell listens: the same tab key
 *  a notice's link uses). */
export function goToSoftwarePort() {
  window.dispatchEvent(new CustomEvent("irivai:goto",
                                       { detail: { page: "settings", tab: "network" } }));
}

/** The port-mode note: the Hub uses ports, and where the proxy is chosen. */
export function HubUsesPorts({ onGo }) {
  return (
    <p className="text-xs text-muted-foreground" data-hub-ports>
      {/* Named as the Network page names it since the 2026-10-11 copy pass. */}
      The Hub uses ports; switch it to Standard Access in{" "}
      <button type="button" className="underline" data-goto-software-port
              onClick={() => { onGo?.(); goToSoftwarePort(); }}>
        Settings &gt; Network &gt; Dashboard Access
      </button>{" "}
      to give every system an address without a port.
    </p>
  );
}
