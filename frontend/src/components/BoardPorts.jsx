// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// BoardPorts - the board's USB sockets and ethernet jacks, drawn like the
// faceplate, with live state.
//
// The question every caller is answering is physical: "which socket is this
// camera in / can I unplug it / is the LAN cable actually up?" So the panels
// are drawn to be RECOGNISED against the board - stacked blue USB-A sockets
// under a "USB 3.0" cap, two RJ45s with their green/amber LEDs under ETH0 and
// ETH1 - rather than rendered as another table.
//
// One fetch serves both panels (/api/board/ports); polling runs only while a
// panel is mounted, so a closed popover costs the box nothing.
import { useEffect, useState } from "react";
import { authHeaders } from "../lib/session.js";
import { apiUrl } from "../lib/base.js";

const POLL_MS = 2500;

export function useBoardPorts(active = true) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!active) return undefined;
    let dead = false;
    const read = () => {
      fetch(apiUrl("/api/board/ports"), { credentials: "same-origin",
                                  headers: { ...authHeaders() } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d) => { if (!dead) { setData(d); setError(""); } })
        .catch((e) => { if (!dead) setError(String(e.message || e)); });
    };
    read();
    const t = setInterval(read, POLL_MS);
    return () => { dead = true; clearInterval(t); };
  }, [active]);
  return { data, error };
}

/** The two USB-A sockets, stacked like the faceplate. `usb` is the payload's
    list; sockets render even when empty - an empty socket is an answer. */
export function UsbPorts({ usb, labels = false }) {
  const extra = (usb || []).filter((p) => p.slot == null);
  // ALWAYS both sockets, filled in by slot. Rendering only the slots the
  // payload names drew ONE socket on a board that has two, the moment a
  // camera was pinned to socket 0 -- and an empty socket is an answer to
  // "where do I plug the second camera", so it has to be on the faceplate.
  const by = new Map((usb || []).filter((p) => p.slot != null)
                                .map((p) => [p.slot, p]));
  const shown = [0, 1].map((slot) => by.get(slot) || { slot });
  return (
    // The socket IS the readout: lit when a camera in it is being used, plain
    // when something is merely plugged in, hollow when empty. The sentences
    // that used to sit beside it ("Port 0 - USB HD Camera audio: USB HD Cam ·
    // used by counting_template_editor") said the same three things in forty
    // words, wrapped onto three lines, and pushed the panel wide enough to
    // need a scrollbar. They are the socket's tooltip now.
    <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
      <div className="iriv-usb">
        {shown.map((p) => (
          <div key={p.slot}
               className={"iriv-usb-sock " + (p.present
                 ? (p.used_by?.length ? "is-live" : "is-plugged")
                 : "is-empty")}
               title={p.present
                 ? `USB port ${p.slot} - ${p.name || "camera"}`
                   + (p.used_by?.length
                       ? ` · used by ${p.used_by.join(", ")}`
                       : " · plugged in, not used by any running system")
                 : `USB port ${p.slot} - empty`} />
        ))}
        <div className="iriv-usb-cap"
             title={extra.length
               ? "Also attached, on a node this board does not pin to a "
                 + "socket: " + extra.map((p) => p.name || p.device).join(", ")
               : undefined}>
          USB 3.0{extra.length ? " *" : ""}
        </div>
      </div>
      {/* Camera Setup only. Everywhere else the socket IS the readout and a
          sentence beside it pushed the panel wide enough to need a scrollbar
          -- but on the page where an operator is CHOOSING a camera, "which
          socket is this one in" is the question being asked, and a tooltip
          answers it only for whoever thinks to hover. */}
      {labels && (
        // Padded and gapped to match .iriv-usb exactly (12px padding, 8px gap,
        // 46px sockets) so each line sits beside ITS OWN socket. A loose block
        // of text next to two sockets makes the reader match them up by
        // counting, which is the work the drawing is supposed to save.
        <div style={{ display: "grid", gap: 8, padding: "12px 0",
                      fontSize: 12, minWidth: 0 }}>
          {shown.map((p) => (
            <div key={p.slot}
                 style={{ minHeight: 46, display: "flex", flexDirection: "column",
                          justifyContent: "center", minWidth: 0 }}>
              <div style={{ opacity: 0.6, fontSize: 11, letterSpacing: ".04em",
                            textTransform: "uppercase" }}>
                Port {p.slot}
              </div>
              {p.present ? (
                <>
                  <div style={{ fontWeight: 600, minWidth: 0, overflow: "hidden",
                                textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                       title={p.name || "camera"}>
                    {p.name || "camera"}
                  </div>
                  <div style={{ opacity: 0.6 }}>
                    {p.device}
                    {p.used_by?.length
                      ? ` · in use by ${p.used_by.join(", ")}`
                      : " · not in use"}
                  </div>
                </>
              ) : (
                <div style={{ opacity: 0.5 }}>nothing connected</div>
              )}
            </div>
          ))}
          {extra.map((p, i) => (
            <div key={"x" + i} style={{ opacity: 0.7 }}>
              <div style={{ fontWeight: 600 }}>{p.name || p.device}</div>
              <div>on a node this board does not pin to a socket</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The two RJ45 jacks with link (green) and activity (amber) LEDs. */
export function EthPorts({ eth }) {
  const jacks = (eth || []).length ? eth : [{ name: "eth0" }, { name: "eth1" }];
  return (
    <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
      <div className="iriv-eth">
        {jacks.map((j) => (
          <div key={j.name}>
            <div className={"iriv-eth-jack " + (j.up ? "" : "is-empty")}
                 title={j.up
                   ? `${j.name} - ${j.speed_mbps ? `${j.speed_mbps} Mb/s` : "up"}${j.ip ? ` · ${j.ip}` : ""}`
                   : `${j.name} - no link`}>
              <span className={"iriv-eth-led is-left " + (j.up ? "is-green" : "")} />
              <span className={"iriv-eth-led is-right " + (j.up ? "is-amber" : "")} />
            </div>
            <div className="iriv-eth-cap">{j.name.toUpperCase()}</div>
            <div className="muted iriv-port-note">
              {j.up
                ? <>{j.speed_mbps ? `${j.speed_mbps >= 1000 ? "1 Gb/s" : `${j.speed_mbps} Mb/s`}` : "link up"}
                    {j.ip ? <><br />{j.ip}</> : null}</>
                : "unplugged"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
