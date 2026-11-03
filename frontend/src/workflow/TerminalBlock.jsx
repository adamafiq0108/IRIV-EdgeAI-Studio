// TerminalBlock - the board's screw terminals, drawn as they are printed,
// with what each one is doing right now.
//
// The question this answers is the one an installer actually asks, standing
// at a cabinet with a rule that is not working: "is DO0 switching, is
// something else already holding it, does the PLC answer on RS485?" The
// workflow canvas cannot answer any of that - it shows intent, not the plant.
//
// A LIGHT MEANS THE SIGNAL IS ACTIVE NOW, read back from the hardware by
// /api/board/io, and nothing else. Not "a rule uses this terminal", not "the
// UI last sent 1": until 2026-10-07 DO0/DO1 were printed green and orange
// permanently, and a terminal a running app had claimed lit whether or not it
// was on, so the picture said "connected" about terminals with no wire in
// them. A screw terminal cannot tell whether a wire is in it, so the honest
// signal per group is:
//   * DI0 / DI1  - the input reads high (contact closed / sensor on);
//   * DO0 / DO1  - the output pin reads back high (relay energised);
//   * L0 / L1    - the LED driver output is on (duty above 0);
//   * RS485      - a Modbus RTU device answered in the last few seconds
//                  (amber: the latest exchange got no answer);
//   * VIN, GND, S/S - power and the input common: no light, by design.
// Two more marks, drawn differently so they never read as "on": a pulsing red
// edge on a line another program holds, and a ring on the ports a workflow
// uses (when a caller passes `usedPorts`).
import { useEffect, useMemo, useState } from "react";
import { authHeaders } from "../lib/session.js";
import { apiUrl } from "../lib/base.js";

//: How often to re-read the board. Fast enough to watch a contact close
//: while somebody holds it, slow enough that a panel left open is not a
//: load on the box.
const POLL_MS = 1000;

//: The physical layout, exactly as the terminals are printed. Two rows of
//: eight; `port` names the logical port a terminal belongs to, `group` the
//: bracket it sits under, `tone` the colour it lights in. Power and the
//: input common have neither port nor tone: they have no state to show.
const TOP = [
  { text: "VIN", vertical: true, port: null },
  { text: "A", port: null, group: "rs485", tone: "bus" },
  { text: "B", port: null, group: "rs485", tone: "bus" },
  { text: "G", port: null, group: "rs485", tone: "bus" },
  { text: "DO0", port: "DO0", tone: "do0" },
  { text: "DO0", port: "DO0", tone: "do0" },
  { text: "+", port: "L0", group: "l0", tone: "led" },
  { text: "−", port: "L0", group: "l0", tone: "led" },
];
const BOTTOM = [
  { text: "GND", vertical: true, port: null },
  { text: "S/S", vertical: true, port: null, group: "din" },
  { text: "DI0", vertical: true, port: "DI0", tone: "din" },
  { text: "DI1", vertical: true, port: "DI1", tone: "din" },
  { text: "DO1", port: "DO1", tone: "do1" },
  { text: "DO1", port: "DO1", tone: "do1" },
  { text: "+", port: "L1", group: "l1", tone: "led" },
  { text: "−", port: "L1", group: "l1", tone: "led" },
];

function Staple({ from, to, bottom, tone }) {
  // grid-column is 1-based and end-exclusive, so a staple over pins 2..4
  // spans 2/5. Kept as numbers here rather than baked into the markup so a
  // future board revision changes the table above and nothing else.
  // `tone` is passed ONLY while the group is lit; unlit, it is the grey
  // silkscreen line like every other bracket.
  return (
    <span style={{ gridColumn: `${from} / ${to}`, position: "relative" }}>
      <span className={"iriv-term-staple"
                       + (bottom ? " is-bottom" : "")
                       + (tone ? ` is-${tone}` : "")}>
        <i style={{ left: 0 }} />
        <i style={{ right: 0 }} />
        <b />
      </span>
    </span>
  );
}

/**
 * `usedPorts` is the set of port ids a workflow touches - passed in rather
 * than derived here, because the canvas already knows and this component
 * should not have to understand node types to draw a ring.
 * `poll = false` freezes it (a closed drawer must not keep asking the board).
 */
export default function TerminalBlock({ usedPorts, poll = true, onState }) {
  const [board, setBoard] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!poll) return undefined;
    let dead = false;
    const read = () => {
      // A background tab is not looking at the terminals either.
      if (typeof document !== "undefined" && document.hidden) return;
      fetch(apiUrl("/api/board/io"), { credentials: "same-origin",
                               headers: { ...authHeaders() } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d) => { if (!dead) { setBoard(d); setError(""); } })
        .catch((e) => { if (!dead) setError(String(e.message || e)); });
    };
    read();
    const t = setInterval(read, POLL_MS);
    return () => { dead = true; clearInterval(t); };
  }, [poll]);

  const byId = useMemo(() => {
    const map = {};
    for (const p of board?.ports || []) map[p.id] = p;
    return map;
  }, [board]);

  // Tell the caller what is wrong, so the button can carry a badge without
  // the drawer being open. Conflicts first: they are the actionable ones.
  const busy = useMemo(
    () => (board?.ports || []).filter((p) => p.busy).map((p) => p.id),
    [board]);
  useEffect(() => { if (onState) onState({ busy, detected: !!board?.detected }); },
            [busy, board?.detected, onState]);

  const used = useMemo(() => new Set(usedPorts || []), [usedPorts]);

  // "Active now", from the board's own read-back and nothing else. An input
  // is its measured level; an output or LED driver is the server's `lit`,
  // which reads the pin (and, for PWM, the duty its app is running). An
  // older server without `lit` leaves outputs dark - dark is "cannot see
  // it", never a guess.
  const isLive = (portId) => {
    const p = byId[portId];
    if (!p) return false;
    if (p.kind === "input") return p.value === 1;
    return p.lit === true;
  };

  const rs485 = board?.rs485 || null;
  const busState = rs485?.state === "active" ? "on"
    : rs485?.state === "error" ? "warn" : "";
  const window_s = rs485?.window_s || 5;

  // "on" | "warn" | "" for one terminal.
  const lightOf = (pin) => {
    if (pin.group === "rs485") return busState;
    return pin.port && isLive(pin.port) ? "on" : "";
  };

  const groupLit = {
    rs485: busState,
    din: isLive("DI0") || isLive("DI1") ? "on" : "",
    do0: isLive("DO0") ? "on" : "",
    do1: isLive("DO1") ? "on" : "",
    l0: isLive("L0") ? "on" : "",
    l1: isLive("L1") ? "on" : "",
  };

  const portTitle = (pin, p, on) => {
    if (pin.group === "rs485") {
      const what = {
        active: `a device answered ${rs485?.ok_age_s ?? 0} s ago`,
        error: `no answer on the latest exchange${rs485?.error ? ` (${rs485.error})` : ""}`,
        idle: `nothing has talked in the last ${window_s} s`,
        absent: "no RS485 adapter found",
      }[rs485?.state] || "state unknown";
      return [`RS485 ${pin.text} - ${rs485?.device || "/dev/ttyACM0"}`, what,
              "lit = a Modbus RTU device answered recently"].join(" - ");
    }
    if (!pin.port) {
      return pin.text === "S/S"
        ? "S/S - common for DI0/DI1 (no state to show)"
        : `${pin.text} - power (no state to show)`;
    }
    let now;
    if (p?.kind === "input") {
      now = p?.value == null ? "level unreadable" : (on ? "input active (reads high)" : "input off (reads low)");
    } else if (p?.kind === "pwm") {
      now = on ? `LED driver on${p?.duty != null ? ` (${p.duty}%)` : ""}` : "LED driver off";
    } else {
      now = p?.level == null ? "output state unreadable"
        : (on ? "output energised (read back high)" : "output off (read back low)");
    }
    return [`${pin.port} - BCM ${p?.pin ?? "?"}`,
            p?.label,
            now,
            p?.busy ? `held by "${p.consumer}"` : null,
            used.size ? (used.has(pin.port) ? "used in this workflow" : "not used here") : null,
           ].filter(Boolean).join(" - ");
  };

  const cell = (pin, i, bottomRow) => {
    const p = pin.port ? byId[pin.port] : null;
    const light = lightOf(pin);
    const cls = [];
    if (pin.vertical) cls.push("iriv-term-vertical");
    if (pin.tone) cls.push("iriv-term-io", `is-${pin.tone}`);
    if (light === "on") cls.push("is-on");
    if (light === "warn") cls.push("is-warn");
    if (p?.busy) cls.push("is-busy");
    return (
      <span key={`${bottomRow ? "b" : "t"}${i}`}
            className={cls.join(" ")}
            title={portTitle(pin, p, light === "on")}>
        {pin.text}
      </span>
    );
  };

  const socket = (pin, i, bottomRow) => {
    const p = pin.port ? byId[pin.port] : null;
    const light = lightOf(pin);
    const cls = ["iriv-term-socket"];
    if (pin.tone) cls.push(`is-${pin.tone}`);
    if (light === "on") cls.push("is-active");
    if (light === "warn") cls.push("is-warn");
    if (p?.busy) cls.push("is-busy");
    if (pin.port && used.has(pin.port)) cls.push("is-used");
    return <i key={`${bottomRow ? "b" : "t"}${i}`} className={cls.join(" ")} />;
  };

  // A caption takes its group's colour only while the group is lit.
  const caption = (label, key, tone, col) => (
    <span className={"iriv-term-grp"
                     + (groupLit[key] === "on" ? ` is-on is-${tone}` : "")
                     + (groupLit[key] === "warn" ? " is-warn" : "")}
          style={{ gridColumn: col }}>{label}</span>
  );
  const staple = (key, tone, from, to, bottom) => (
    <Staple from={from} to={to} bottom={bottom}
            tone={groupLit[key] === "on" ? tone : groupLit[key] === "warn" ? "warn" : ""} />
  );

  return (
    <div className="iriv-term">
      {/* group captions + brackets, top */}
      <div className="iriv-term-row8">
        <span />
        {caption("RS485", "rs485", "bus", "2 / 5")}
        {caption("DO0", "do0", "do0", "5 / 7")}
        {caption("L0", "l0", "led", "7 / 9")}
      </div>
      <div className="iriv-term-row8 iriv-term-bracket">
        <span />
        {staple("rs485", "bus", 2, 5, false)}
        {staple("do0", "do0", 5, 7, false)}
        {staple("l0", "led", 7, 9, false)}
      </div>

      <div className="iriv-term-row8 iriv-term-pins">
        {TOP.map((pin, i) => cell(pin, i, false))}
      </div>

      {/* the connector body: top row of sockets, then bottom */}
      <div className="iriv-term-block" aria-hidden="true">
        {TOP.map((pin, i) => socket(pin, i, false))}
        {BOTTOM.map((pin, i) => socket(pin, i, true))}
      </div>
      <div className="iriv-term-tick" title="Pin 1 orientation mark" />

      <div className="iriv-term-row8 iriv-term-pins">
        {BOTTOM.map((pin, i) => cell(pin, i, true))}
      </div>

      <div className="iriv-term-row8 iriv-term-bracket">
        <span />
        {staple("din", "din", 2, 5, true)}
        {staple("do1", "do1", 5, 7, true)}
        {staple("l1", "led", 7, 9, true)}
      </div>
      <div className="iriv-term-row8">
        <span />
        {caption("DIN", "din", "din", "2 / 5")}
        {caption("DO1", "do1", "do1", "5 / 7")}
        {caption("L1", "l1", "led", "7 / 9")}
      </div>

      {/* What the picture cannot say on its own: what a light means (a
          terminal cannot sense a wire), a board that is not there, and lines
          somebody else is holding. */}
      <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
        {error && <p className="muted small" style={{ margin: 0 }}>
          Cannot read the board: {error}
        </p>}
        {board && !board.detected && (
          <p className="muted small" style={{ margin: 0 }}>
            No IRIV board detected on this box - the terminals above are the
            reference layout, not live state.
          </p>
        )}
        {!!busy.length && (
          <p className="small" style={{ margin: 0, color: "var(--alert, #ef4444)" }}>
            <strong>{busy.join(", ")}</strong> {busy.length > 1 ? "are" : "is"} held
            by another program{board?.ports?.find((p) => p.busy)?.consumer
              ? ` ("${board.ports.find((p) => p.busy).consumer}")` : ""} -
            a rule driving {busy.length > 1 ? "them" : "it"} will not take
            effect until that program releases the line. Disable the board's
            factory Node-RED flow if it is still running.
          </p>
        )}
        {board?.detected && (
          <p className="muted small iriv-term-legend" style={{ margin: 0 }}>
            <strong>Lit = signal active now.</strong> DI: input reads high.
            DO: output energised. L: LED driver on. RS485: a Modbus device
            answered in the last {window_s} s (amber: no answer). A terminal
            cannot sense whether a wire is attached.
          </p>
        )}
      </div>
    </div>
  );
}
