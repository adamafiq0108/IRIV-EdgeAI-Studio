// BoxRestartNote - "a restart is needed", shared by the Software port and
// Hostname cards on the Network page.
//
// Neither card's Apply changes anything live: the port and the name are SAVED
// and the box takes them the next time it restarts (core/services/
// boxpending.py says why the restart is a reboot). Both cards read the same
// `restart` block from the server - what waits, where the dashboard will be
// afterwards, whether the page may restart the box, what the last restart
// did - so one restart, and one note, covers either change or both.
//
// The note is drawn ONCE: by the port card when a port waits, else by the
// name card. After any save either card fires RESTART_EVENT so the other
// redraws from the server's truth.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { goWithSession, withSession } from "../lib/session.js";
import { Loader2, TriangleAlert } from "lucide-react";

export const RESTART_EVENT = "irivai:restart-pending";

export function announceChange() {
  window.dispatchEvent(new CustomEvent(RESTART_EVENT));
}

export function useRestartEvent(reload) {
  useEffect(() => {
    window.addEventListener(RESTART_EVENT, reload);
    return () => window.removeEventListener(RESTART_EVENT, reload);
  }, [reload]);
}

// Where THIS browser goes after the restart, and with what (Pipi L1,
// 2026-10-05). The session token is handed over ONLY to an address: the
// one the browser used, else the box's own interface address - both are
// this box. A .local name is first-come on the LAN, and a NEW name is one
// the box does not own yet: another device answering it during the reboot
// would receive the master token. With no address the name is opened bare,
// and the user signs in again there.
export function handoff(after) {
  const ip = after?.ip_urls?.[0] || "";
  if (ip) return { poll: ip, go: withSession(ip) };
  const name = after?.name_url || "";
  return { poll: name, go: name };
}

// "After the restart, open A or B."
export function AfterLine({ after }) {
  if (!after) return null;
  const ip = after.ip_urls?.[0];
  return (
    <>
      After the restart, open <b>{after.name_url}</b>
      {ip ? <> or <b>{ip}</b></> : null}.
    </>
  );
}

// What waits, in one sentence per change.
export function summary(r) {
  const out = [];
  if (r?.port) out.push(`Port: ${r.current?.port} now, ${r.port} after the restart.`);
  if (r?.hostname) {
    out.push(`Device name: ${r.current?.name} now, ${r.hostname}.local after the restart.`);
  }
  if (r?.access) {
    out.push(`Address: ${accessWords(r.current?.access)} now, `
             + `${accessWords(r.access)} after the restart.`);
  }
  return out.join(" ");
}

// The two ways to reach the box (core/services/webaccess.py), in the card's
// words: no "proxy" in what the customer reads (copy pass 2026-10-11).
export function accessWords(mode) {
  return mode === "proxy" ? "Standard Access (port 80)" : "a custom port";
}

export const RESTART_COST = "Restarting the box stops every running system for "
  + "about a minute; they start again with it.";

// Said when `restart.applies` is false: this hub is not irivai-hub.service,
// the one unit that applies a saved port or name at the restart (a second
// unit, a dev box's hand-started hub). Apply is refused by the server too
// (hubport.NOT_OWN_SERVICE); the cards disable it and promise nothing.
export const NOT_OWN_SERVICE = "This copy of the software is not the box's own "
  + "service, the only one that applies a saved port or hostname when the box "
  + "restarts. Change it where this copy was started.";

export function appliesHere(restart) {
  return restart?.applies !== false;
}

// The confirm after a save: Restart now, or Later. `extra` is one more
// sentence (the name card's "you opened this page by the old name").
export async function askRestart(confirm, r, extra = "") {
  if (!r?.any) return false;
  if (!r.can_reboot) return false;
  return confirm({
    title: "Restart the box now?",
    description: `${summary(r)}${extra ? ` ${extra}` : ""} The box keeps the current port and hostname until it `
      + `restarts. After the restart, open ${r.after.name_url}`
      + (r.after.ip_urls?.length ? ` or ${r.after.ip_urls[0]}` : "")
      + `. ${RESTART_COST}`,
    confirmLabel: "Restart now", cancelLabel: "Later" });
}

// POST the restart, then wait for the box to answer at its new address and
// move there (handoff() decides whether the session travels).
export async function restartBox(onMoving) {
  const d = await api.post("/api/hub/restart", {});
  const { poll: target, go } = handoff(d.after);
  onMoving({ url: target, after: d.after });
  const started = Date.now();
  const poll = async () => {
    if (Date.now() - started > 5 * 60 * 1000) return;
    try {
      await fetch(`${target}/api/health`, { mode: "no-cors", cache: "no-store" });
      goWithSession(go);
    } catch { setTimeout(poll, 5000); }
  };
  setTimeout(poll, 25000);          // the box takes longer than this to come back
  return d;
}

export function RestartNote({ restart, busy, onRestart, moving }) {
  if (moving) {
    return (
      <Note>
        The box is restarting. This page moves to <a href={moving.url}>{moving.url}</a> by
        itself when the box answers, in about a minute. If it does not,
        {" "}<AfterLine after={moving.after} />
      </Note>
    );
  }
  if (!restart?.any) return null;
  if (!appliesHere(restart)) {
    // Saved (by the box's own service, or before this hub learned to refuse),
    // but nothing THIS hub does will apply it: say so, promise nothing.
    return (
      <Note>
        <span style={{ flex: 1 }}>
          <b>A change is saved.</b> {summary(restart)} {NOT_OWN_SERVICE}
        </span>
      </Note>
    );
  }
  return (
    <Note>
      <span style={{ flex: 1 }}>
        <b>A restart is needed.</b> {summary(restart)} <AfterLine after={restart.after} />
        {restart.can_reboot ? ` ${RESTART_COST}`
          : " This box cannot restart itself from the page: switch it off and on "
            + "again, and the change applies then."}
      </span>
      {restart.can_reboot && (
        <button type="button" className="btn btn-sm" disabled={!!busy} onClick={onRestart}>
          {busy === "restart" ? <Loader2 size={14} className="animate-spin" /> : null}
          Restart now
        </button>
      )}
    </Note>
  );
}

// The last restart's outcome for one change ("port" or "name"), this boot only.
export function LastResult({ last, what, label }) {
  const [hidden, setHidden] = useState(false);
  if (!last || hidden || last[`${what}_ok`] === undefined) return null;
  const ok = last[`${what}_ok`];
  return (
    <Note ok={ok}>
      <span style={{ flex: 1 }}>
        {ok ? `The box restarted with the new ${label}: ${last[`${what}_to`]}.`
          : `The box restarted but kept the ${label} ${last[`${what}_from`]}, because `
            + `${last[`${what}_detail`]}. Nothing else changed.`}
      </span>
      <button type="button" className="btn btn-sm" onClick={() => setHidden(true)}>OK</button>
    </Note>
  );
}

export function Note({ children, ok }) {
  const tone = ok ? "22,163,74" : "217,119,6";
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8,
                  padding: "8px 10px", borderRadius: 8, fontSize: 12,
                  background: `rgba(${tone},.08)`, border: `1px solid rgba(${tone},.3)` }}>
      <TriangleAlert size={14} style={{ flex: "none",
                                        color: ok ? "var(--ok, #16a34a)" : "var(--warn, #d97706)" }} />
      <span style={{ flex: 1 }}>{children}</span>
    </div>
  );
}
