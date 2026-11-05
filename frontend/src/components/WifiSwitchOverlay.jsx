// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// WifiSwitchOverlay - the whole window while wlan0 changes job, and the page
// that finds the box again afterwards.
//
// Tok Aba (2026-10-07): switching AP <-> Wi-Fi must block the page until it is
// over, and then open the page at wherever the box is now, without a refresh
// or a typed address. The one thing no page can do is move the COMPUTER to
// another network: a browser has no say over the Wi-Fi it is on. So the
// overlay says which network to join, and does everything else itself:
//
//   - it polls the box at THIS address (same origin, with the session): a
//     finished switch seen here (the caller is on a cable, or on the .local
//     name that follows the box) or a failed one (wifiswitch.py: a failed
//     join to Wi-Fi leaves the hotspot OFF, never raised by itself) closes
//     it, with the outcome said in words;
//   - meanwhile it probes every address the box said, BEFORE it moved, that
//     it may answer on (switch.candidates, wifiswitch.candidates) through the
//     one open, cross-origin route there is, GET /api/health/box. An address
//     counts only when the box there is THIS box (box_id) and its switch is
//     over: a lease the box used to hold may be another device's by now.
//   - the first match opens the same page, path and port at that address.
//     The session goes with it ONLY when this computer is not changing
//     networks (onWlan false: it reaches the box over a cable) and the
//     address is the box's by construction (`own`). A computer that has just
//     joined another network cannot tell the box from whatever answers there
//     with the same open id, so it opens the page bare and the user signs in
//     once (Pipi M1, Tok Aba 2026-10-07). The manual links never carry it.
//
// It never hangs: after a while it shows the addresses to open by hand, and
// after the overall budget it says so and offers Close.
//
// to "test" (Test connection, core/services/wifitest.py): the box leaves the
// access point, tries a network and comes BACK, so there is nowhere else to
// look -- the overlay only waits for this address to answer with the outcome.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2 } from "lucide-react";
import { api } from "../api/client.js";
import { goWithSession, withSession } from "../lib/session.js";

const POLL_MS = 2000;
//: One probe's budget. A host that is not there at all often never refuses:
//: the packets go nowhere and fetch would wait for the OS (a minute or more).
const PROBE_MS = 3000;

// Rejects after `ms`, aborting the fetch behind it when there is one.
function within(ms, run) {
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  let timer;
  return Promise.race([
    run(ctrl?.signal),
    new Promise((_, reject) => {
      timer = setTimeout(() => { ctrl?.abort(); reject(new Error("timeout")); }, ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

// http://<host>:<this page's port> -- the port is the hub's, whichever
// address reaches it, so the one this page was served on is kept.
export function originFor(host, loc = window.location) {
  return `${loc.protocol}//${host}${loc.port ? `:${loc.port}` : ""}`;
}

// The page the user is on, at another host.
export function samePageAt(host, loc = window.location) {
  return `${originFor(host, loc)}${loc.pathname}${loc.search}`;
}

// The URL the overlay opens for candidate `c` (exported for the jsdom check):
// the session in its #fragment only off Wi-Fi and only to an `own` address.
export function openUrl(c, job, loc = window.location) {
  const url = samePageAt(c.host, loc);
  return !job.onWlan && c.own ? withSession(url) : url;
}

// {box, switch} from one candidate, or null. CORS mode, no credentials:
// GET /api/health/box sets Access-Control-Allow-Origin on itself for this.
async function probe(host) {
  try {
    const r = await within(PROBE_MS, (signal) => fetch(`${originFor(host)}/api/health/box`,
      { mode: "cors", cache: "no-store", credentials: "omit", signal }));
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

/**
 * job: {to: "ap"|"wifi"|"test", network, apName, apPassword, candidates: [{host, why,
 *       own}], boxId, budgetS, onWlan}
 * onDone({ok, message, data}): the switch finished and THIS address answers
 *       (data is the status it answered with), or the overlay was closed.
 */
export default function WifiSwitchOverlay({ job, onDone }) {
  const [phase, setPhase] = useState("switching"); // switching|waiting|found|timeout
  const [found, setFound] = useState(null);         // the candidate it opens
  const [elapsed, setElapsed] = useState(0);
  const box = useRef(null);
  const deadline = useRef(0);
  const leaving = useRef(false);
  const done = useRef(onDone);
  done.current = onDone;

  const budget = (job.budgetS || 45);
  const manualAfter = budget + 15;          // about a minute: show the links
  const giveUpAfter = budget + 135;         // about three: say it, offer Close
  if (!deadline.current) deadline.current = Date.now() + giveUpAfter * 1000;

  // BLOCK THE PAGE. Everything behind is inert (no click, no focus); every
  // key outside the overlay's own links and buttons is swallowed; a reload or
  // a closed tab asks first; Back is put back. Undone on close, and before
  // the redirect so the move itself is not "leave this page?".
  useEffect(() => {
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
    const onKey = (e) => {
      const inside = box.current && box.current.contains(e.target);
      if (inside && (e.key === "Tab" || e.key === "Enter" || e.key === " ")) return;
      e.preventDefault();
      e.stopPropagation();
    };
    const onUnload = (e) => {
      if (leaving.current) return undefined;
      e.preventDefault();
      e.returnValue = "";
      return "";
    };
    // Capture at the window runs before the router's own listener.
    const here = window.location.href;
    const onPop = (e) => {
      e.stopImmediatePropagation();
      window.history.pushState(null, "", here);
    };
    window.history.pushState(null, "", here);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("beforeunload", onUnload);
    window.addEventListener("popstate", onPop, true);
    return () => {
      root?.removeAttribute("inert");
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("beforeunload", onUnload);
      window.removeEventListener("popstate", onPop, true);
    };
  }, []);

  useEffect(() => {
    let stop = false;
    const started = Date.now();
    const here = window.location.hostname;
    const others = (job.candidates || []).filter((c) => c.host && c.host !== here);

    function go(c) {
      if (stop) return;
      stop = true;
      setFound(c);
      setPhase("found");
      const url = openUrl(c, job);
      setTimeout(() => {
        leaving.current = true;
        goWithSession(url);
      }, 800);
    }

    async function tick() {
      // This address first: the outcome, in the box's own words.
      const local = within(PROBE_MS + 1000, () => api.get("/api/hub/network"))
        .then((d) => {
          const s = (d && d.switch) || {};
          if (s.state === "done") return { end: { ok: true, message: s.message, data: d } };
          if (s.state === "failed") return { end: { ok: false, message: s.message, data: d } };
          return { here: true };
        })
        .catch(() => ({ here: false }));
      // All at once; this address's answer is acted on without waiting for
      // a candidate that is not there to time out.
      const remote = others.map((c) => probe(c.host).then((r) => ({ c, r })));
      const mine = await local;
      if (stop) return;
      if (mine.end) { stop = true; done.current(mine.end); return; }
      const theirs = await Promise.all(remote);
      if (stop) return;
      // No id to match (an older box's status, a lost reply): nothing
      // counts as this box, whatever answers -- the links are the way then.
      const hit = job.boxId && theirs.find(({ r }) => r && r.box === job.boxId
                                         && (r.switch === "done" || r.switch === "idle"));
      if (hit) { go(hit.c); return; }
      const fell = job.boxId
        && theirs.find(({ r }) => r && r.box === job.boxId && r.switch === "failed");
      if (fell) { go(fell.c); return; }      // it is there; the page there says why
      setPhase(Date.now() > deadline.current ? "timeout" : mine.here ? "switching" : "waiting");
    }

    (async () => {
      while (!stop) {
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (stop) return;
        setElapsed(Math.round((Date.now() - started) / 1000));
        // eslint-disable-next-line no-await-in-loop
        await tick();
      }
    })();
    return () => { stop = true; };
  }, [job]);

  const toAp = job.to === "ap";
  const test = job.to === "test";
  const hosts = (job.candidates || []).map((c) => c.host);
  const waitingAt = hosts.length ? hosts.join(", ") : "its new address";
  const links = (job.candidates || []).map((c) => (
    <li key={c.host}>
      <a href={samePageAt(c.host)}
         onClick={() => { leaving.current = true; }}>
        {originFor(c.host)}
      </a>
      <span style={{ color: "var(--muted-foreground, #a1a1aa)" }}> {c.why}</span>
    </li>
  ));

  let step;
  if (phase === "found") {
    step = <>Found the box at <b>{found.host}</b>. Opening the page there...
      {!job.onWlan && found.own ? "" : " You will be asked to sign in once there."}</>;
  } else if (phase === "timeout") {
    step = <>The box has not answered at any address it could be at. It may still
      be joining, or this computer is not on its network yet.
      {job.to === "wifi" && " If it could not join, its hotspot is off: reach the box "
        + "over a cable to see why and turn the hotspot on."}</>;
  } else if (phase === "waiting") {
    step = test ? <>Waiting for the access point <b>{job.apName}</b> to come back...</>
      : <>Waiting for the box at {waitingAt}...</>;
  } else {
    step = test ? <>Testing <b>{job.network}</b>: the password, then an address...</>
      : toAp ? "Switching to the access point..." : "Switching to Wi-Fi...";
  }

  const join = !job.onWlan ? (
    <p style={{ margin: 0 }}>
      This computer does not reach the box over its Wi-Fi, so this page stays where
      it is and closes by itself when the {test ? "test" : "switch"} is over.
    </p>
  ) : test ? (
    <p style={{ margin: 0 }} data-wifi-test-note="1">
      The access point <b>{job.apName}</b> is off while the box tries the network, up
      to {budget} s. This computer drops off it meanwhile and normally rejoins it by
      itself when it is back; if it does not, join <b>{job.apName}</b>
      {job.apPassword ? <> with the password <b>{job.apPassword}</b></> : null} again.
      This page carries on by itself and shows the result.
    </p>
  ) : toAp ? (
    <p style={{ margin: 0 }}>
      On this computer, join the Wi-Fi network <b>{job.apName}</b>
      {job.apPassword ? <> with the password <b>{job.apPassword}</b></> : null}. A web
      page cannot change which network your computer is on; as soon as you have
      joined, this page opens the box at <b>{originFor(hosts[0] || "10.42.0.1")}</b> by itself.
      You will be asked to sign in once at the new address.
    </p>
  ) : (
    <p style={{ margin: 0 }}>
      On this computer, join the Wi-Fi network <b>{job.network || "the box is joining"}</b>
      {" "}again. A web page cannot change which network your computer is on; as soon as
      you have joined, this page finds the box&apos;s new address and opens it by
      itself. You will be asked to sign in once at the new address. If the box
      cannot join, its hotspot <b>{job.apName}</b> stays off: reach the box over
      a cable to see why and turn the hotspot on.
    </p>
  );

  return createPortal(
    <div className="ui-dialog-backdrop" data-wifi-overlay="1"
         style={{ zIndex: 2000, background: "rgba(10,14,18,.7)" }}>
      <div className="ui-dialog" role="alertdialog" aria-modal="true" aria-busy={phase !== "timeout"}
           aria-labelledby="wifi-overlay-title" ref={box}>
        <div className="ui-dialog-head">
          <h3 id="wifi-overlay-title">
            {test ? "Testing the Wi-Fi network"
              : toAp ? "Switching to the access point" : "Switching to Wi-Fi"}
          </h3>
        </div>
        <div className="ui-dialog-body" style={{ fontSize: 13 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }} data-wifi-step="1">
            {phase === "timeout" ? null
              : <Loader2 size={18} className="animate-spin" style={{ flex: "none" }} />}
            <span style={{ flex: 1 }}>{step}</span>
          </div>
          {phase !== "found" && join}
          {(phase === "timeout" || elapsed >= manualAfter) && links.length > 0 && (
            <div data-wifi-links="1">
              <div>If it does not open by itself, open one of these once this
                computer is on the box&apos;s network (or read the address on the
                box&apos;s screen):</div>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{links}</ul>
            </div>
          )}
          <div style={{ color: "var(--muted-foreground, #a1a1aa)", fontSize: 12 }}>
            {elapsed} s. Please keep this page open until it finishes.
          </div>
        </div>
        {phase === "timeout" && (
          <div className="ui-dialog-foot">
            <button type="button" className="btn btn-sm"
                    onClick={() => { deadline.current = Date.now() + 120000; setPhase("waiting"); }}>
              Keep waiting
            </button>
            <button type="button" className="btn btn-sm btn-accent"
                    onClick={() => done.current({ ok: false, closed: true,
                      message: "Stopped waiting. Open the box at one of its addresses, "
                        + "or read the address on its screen." })}>
              Close
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
