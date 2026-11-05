// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Board guide - the IRIV Edge AI CM5 as it really looks, with every port
// marked on the photo and what is on each one RIGHT NOW beside it.
//
// The photos are the product's own (core/static/img/iriv-cm5-*.png). Each port
// is a dot drawn over the photo at the connector's place, in the photo's own
// pixel coordinates (an SVG viewBox crops the photo to its panel, so the
// numbers never need converting). The dot's colour is the port's state, read
// from GET /api/hub/board every two seconds while the card is on screen:
//
//   green   something is on it and active: a link with a cable, a camera, a
//           contact closed, a relay energised, a Modbus device answering
//   grey    nothing on it, or quiet
//   amber   a warning: RS485 asked and got no answer, a line busy elsewhere
//   red     the line is held by another program
//   dashed  the box could not read it (never shown as "off")
//
// A LIGHT MEANS THE SIGNAL IS ACTIVE NOW, read back from the hardware - not
// "a rule uses this terminal". A screw terminal cannot tell whether a wire is
// in it, so for the terminals the honest words are "reads high", "energised",
// "on", "answering" - never "connected".
import { useCallback, useEffect, useMemo, useState } from "react";
import { Cpu } from "lucide-react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";

const IMG = {
  front: "/static/img/iriv-cm5-front.png",
  side: "/static/img/iriv-cm5-side.png",
  guide: "/static/img/iriv-edge-ai-cm5.png",
};

//: Where each port is on the 800x800 front photo, in its pixels.
const FRONT = [
  { id: "LEDS", x: 245, y: 590, r: 9 },
  { id: "LED01", x: 330, y: 590, r: 9 },
  { id: "BUTTON", x: 277, y: 565, r: 9 },
  { id: "POWER", x: 334, y: 565, r: 9 },
  { id: "VIN", x: 372, y: 541, r: 6 },
  { id: "RS485", x: 404, y: 541, r: 6 },
  { id: "DO0", x: 444, y: 541, r: 6 },
  { id: "L0", x: 477, y: 541, r: 6 },
  { id: "GND", x: 372, y: 573, r: 6 },
  { id: "SS", x: 388, y: 573, r: 6 },
  { id: "DI0", x: 404, y: 573, r: 6 },
  { id: "DI1", x: 420, y: 573, r: 6 },
  { id: "DO1", x: 444, y: 573, r: 6 },
  { id: "L1", x: 477, y: 573, r: 6 },
  { id: "ETH0", x: 537, y: 550, r: 11 },
  { id: "ETH1", x: 621, y: 550, r: 11 },
  { id: "USBC", x: 720, y: 573, r: 10 },
];
//: The side photo: the two antenna sockets, the OLED window, the DIN clip.
const SIDE = [
  { id: "WIFI", x: 155, y: 585, r: 11 },
  { id: "4G", x: 323, y: 572, r: 11 },
  { id: "OLED", x: 465, y: 625, r: 11 },
  { id: "DIN", x: 60, y: 640, r: 11 },
];

const TONE = {
  ok: "#22a06b", idle: "#8a8f98", warn: "#fab219", crit: "#d03b3b", unknown: "#8a8f98",
};

const byId = (list, id) => (list || []).find((p) => p.id === id);
const mbps = (v) => (v >= 1000 ? "1 Gb/s" : `${v} Mb/s`);

/** The live state of a GPIO-backed terminal: tone + words, from one port
 *  record of /api/hub/board (board_io.ports). */
function gpioState(p, kind) {
  if (!p) return { tone: "unknown", status: "not detected", detail: "This box is not an IRIV board." };
  if (p.busy) return { tone: "crit", status: "held by another program",
                       detail: `${p.consumer || "another program"} holds this line; a rule driving it will not take effect until it is released.` };
  const who = p.owner ? ` · driven by ${p.owner}` : "";
  if (kind === "input") {
    if (p.value === 1) return { tone: "ok", status: "reads high", detail: "Contact closed or sensor on" + who };
    if (p.value === 0) return { tone: "idle", status: "reads low", detail: "Contact open or nothing wired" + who };
    return { tone: "unknown", status: "cannot read", detail: "The input could not be read" + who };
  }
  if (kind === "pwm") {
    if (p.lit) return { tone: "ok", status: p.duty != null ? `on · ${Math.round(p.duty)}%` : "on", detail: "LED driver output is on" + who };
    return { tone: "idle", status: "off", detail: "LED driver output is off" + who };
  }
  if (p.lit) return { tone: "ok", status: "energised", detail: "Output is on (relay or load driven)" + who };
  if (p.level === 0 || p.level === 1) return { tone: "idle", status: "off", detail: "Output is off" + who };
  return { tone: "unknown", status: "cannot read", detail: "The output could not be read back" + who };
}

/** Every port, in the order they are listed, with its live state. */
function describe(d) {
  const ports = d?.ports || [];
  const eth = (name) => (d?.eth || []).find((e) => e.name === name);
  const usb = (slot) => (d?.usb || []).find((u) => u.slot === slot);
  const ethState = (name) => {
    const e = eth(name);
    if (!e) return { tone: "unknown", status: "not found", detail: "No such interface on this box" };
    if (e.up && e.carrier !== false) {
      return { tone: "ok", status: e.speed_mbps ? mbps(e.speed_mbps) : "link up",
               detail: [e.ip ? `Address ${e.ip}` : "No address yet", e.mac ? `MAC ${e.mac}` : ""].filter(Boolean).join(" · ") };
    }
    return { tone: "idle", status: "unplugged", detail: e.mac ? `MAC ${e.mac}` : "No link" };
  };
  const usbState = (slot) => {
    const u = usb(slot);
    if (!u) return { tone: "unknown", status: "cannot read", detail: "The USB sockets could not be listed" };
    if (!u.present) return { tone: "idle", status: "empty", detail: "Nothing detected in this socket" };
    const used = u.used_by?.length ? `used by ${u.used_by.join(", ")}` : "not in use by any system";
    return { tone: "ok", status: u.name || "device", detail: `${u.device || ""} · ${used}`.replace(/^ · /, "") };
  };
  const rs = d?.rs485 || {};
  const rsState = rs.state === "active" ? { tone: "ok", status: "answering", detail: `A Modbus device answered in the last ${rs.window_s || 10} s on ${rs.device || "the RS485 port"}` }
    : rs.state === "error" ? { tone: "warn", status: "no answer", detail: rs.error || "The latest request got no answer" }
    : rs.state === "absent" || rs.present === false ? { tone: "unknown", status: "no port", detail: "No RS485 serial device found" }
    : { tone: "idle", status: "quiet", detail: `No Modbus traffic in the last ${rs.window_s || 10} s on ${rs.device || "the RS485 port"}` };
  const w = d?.wifi || {};
  const wifiState = !w.available ? { tone: "unknown", status: "cannot read", detail: "NetworkManager is not available" }
    : w.ap ? { tone: "ok", status: `hotspot "${w.ap}"`, detail: "The box broadcasts its own access point" }
    : w.joined ? { tone: "ok", status: `joined "${w.joined}"`, detail: "Connected to a Wi-Fi network" }
    : w.radio ? { tone: "idle", status: "radio on, not connected", detail: "No hotspot and no network joined" }
    : { tone: "idle", status: "radio off", detail: "" };
  const button = byId(ports, "BUTTON");
  const buzzer = byId(ports, "BUZZER");

  return [
    { panel: "Front", id: "LEDS", label: "Status LEDs", can: "PWR, ACT, M.2 · 4G, PWR, WWAN", tone: "idle", status: "read the lights", detail: "Power, activity, M.2 and 4G module state" },
    { panel: "Front", id: "LED01", label: "LED 0, LED 1", can: "2 programmable LEDs", tone: "idle", status: "programmable", detail: "Driven by a system's rules" },
    { panel: "Front", id: "BUTTON", label: "USER button", can: "1 programmable button",
      ...(button ? gpioState({ ...button, value: button.value }, "input") : { tone: "unknown", status: "not detected", detail: "" }),
      status: button?.value === 1 ? "pressed" : button?.value === 0 ? "released" : (button ? "cannot read" : "not detected") },
    { panel: "Front", id: "POWER", label: "POWER button", can: "Power on and off", tone: "idle", status: "", detail: "Hold to switch the box off" },
    { panel: "Front", id: "VIN", label: "VIN", can: "DC 10-30 V in, surge-protected", tone: "idle", status: "", detail: "Field power; the box is on, so it is powered" },
    { panel: "Front", id: "GND", label: "GND", can: "Power return", tone: "idle", status: "", detail: "" },
    { panel: "Front", id: "RS485", label: "RS485 (A, B, G)", can: "Isolated, up to 500 kbps", ...rsState },
    { panel: "Front", id: "SS", label: "S/S", can: "Input common", tone: "idle", status: "", detail: "Common for DI0 and DI1" },
    { panel: "Front", id: "DI0", label: "DI0", can: "Isolated input, 3-50 V high", ...gpioState(byId(ports, "DI0"), "input") },
    { panel: "Front", id: "DI1", label: "DI1", can: "Isolated input, 3-50 V high", ...gpioState(byId(ports, "DI1"), "input") },
    { panel: "Front", id: "DO0", label: "DO0", can: "Isolated dry contact, 50 V 500 mA", ...gpioState(byId(ports, "DO0"), "output") },
    { panel: "Front", id: "DO1", label: "DO1", can: "Isolated dry contact, 50 V 500 mA", ...gpioState(byId(ports, "DO1"), "output") },
    { panel: "Front", id: "L0", label: "L0 (+ -)", can: "LED driver, Vin up to 2 A, PWM", ...gpioState(byId(ports, "L0"), "pwm") },
    { panel: "Front", id: "L1", label: "L1 (+ -)", can: "LED driver, Vin up to 2 A, PWM", ...gpioState(byId(ports, "L1"), "pwm") },
    { panel: "Front", id: "ETH0", label: "ETH0", can: "Gigabit RJ45, the module's own port", ...ethState("eth0") },
    { panel: "Front", id: "ETH1", label: "ETH1", can: "Gigabit RJ45 (Realtek on USB)", ...ethState("eth1") },
    { panel: "Front", id: "USBC", label: "USB-C BOOT/DBG", can: "Boot and debug from a computer", tone: "idle", status: "", detail: "Not monitored; not for field power" },
    { panel: "Back", id: "USB0", label: "USB 3.0 (socket 1)", can: "Camera or drive", ...usbState(0) },
    { panel: "Back", id: "USB1", label: "USB 3.0 (socket 2)", can: "Camera or drive", ...usbState(1) },
    { panel: "Back", id: "HDMI", label: "HDMI", can: "HDMI 2.0, 4K at 60 Hz", tone: "idle", status: "", detail: "Not monitored" },
    { panel: "Side", id: "WIFI", label: "WIFI antenna", can: "SMA socket · Wi-Fi 2.4 / 5 GHz, Bluetooth 5.0", ...wifiState },
    { panel: "Side", id: "4G", label: "4G antenna socket", can: "For a 4G module in the mPCIe slot", tone: "idle", status: "", detail: "Not monitored" },
    { panel: "Side", id: "OLED", label: "OLED window", can: '0.96" display: name, address, state', tone: "idle", status: "", detail: "The front panel the box prints by itself" },
    { panel: "Side", id: "DIN", label: "DIN clip", can: "DIN-rail mount", tone: "idle", status: "", detail: "" },
    { panel: "Inside", id: "BUZZER", label: "Buzzer", can: "Local alarm", ...(buzzer ? gpioState(buzzer, "output") : { tone: "unknown", status: "not detected", detail: "" }),
      status: buzzer?.lit ? "sounding" : buzzer ? "silent" : "not detected" },
  ];
}

/** The photo with the dots on it. `box` is the viewBox that crops the 800x800
 *  photo to its panel; `spots` the dots in photo pixels. */
function Photo({ src, box, spots, states, selected, onSelect, label }) {
  return (
    <svg viewBox={box} className="bg-black/90 w-full rounded-lg" role="group" aria-label={label}
         style={{ display: "block" }}>
      <image href={src} x="0" y="0" width="800" height="800" />
      {spots.map((s) => {
        const st = states.find((x) => x.id === s.id);
        const tone = TONE[st?.tone || "unknown"];
        const sel = selected === s.id;
        return (
          <g key={s.id} role="button" tabIndex={0} className="bg-spot"
             aria-label={`${st?.label || s.id}: ${st?.status || ""}`}
             onMouseEnter={() => onSelect(s.id)} onFocus={() => onSelect(s.id)}
             onClick={() => onSelect(s.id)}
             onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect(s.id); }}>
            <title>{`${st?.label || s.id}${st?.status ? ` - ${st.status}` : ""}`}</title>
            {sel && <circle cx={s.x} cy={s.y} r={s.r + 7} fill="none" stroke="#ffffff" strokeWidth="2" className="bg-spot-ring" />}
            <circle cx={s.x} cy={s.y} r={s.r + 2.5} fill="#ffffff" opacity="0.9" />
            <circle cx={s.x} cy={s.y} r={s.r} fill={st?.tone === "unknown" ? "none" : tone}
                    stroke={tone} strokeWidth={st?.tone === "unknown" ? 2 : 0}
                    strokeDasharray={st?.tone === "unknown" ? "3 2" : undefined} />
          </g>
        );
      })}
    </svg>
  );
}

export default function BoardGuideCard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("ETH0");
  const load = useCallback(async () => {
    try { setData(await api.get("/api/hub/board")); setError(""); }
    catch (e) { setError(e.message || "cannot read the board"); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useVisibleInterval(load, 2000);

  const states = useMemo(() => describe(data), [data]);
  const sel = states.find((s) => s.id === selected);
  const panels = ["Front", "Back", "Side", "Inside"];

  return (
    <section className="rounded-xl border bg-card p-4" data-board-guide>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
            <Cpu size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold leading-tight">IRIV Edge AI CM5 board</h2>
            <div className="mt-0.5 text-xs text-muted-foreground">
              Every port, and what is on it right now. Hover a dot or a row.
              {data?.model ? ` · ${data.model}` : ""}
              {data && data.detected === false ? " · this box is not an IRIV board, so the terminals cannot be read" : ""}
            </div>
          </div>
        </div>
        <a href={IMG.guide} target="_blank" rel="noopener noreferrer"
           className="text-xs font-medium underline-offset-2 hover:underline">
          Full guide: what each port can do and what to plug in
        </a>
      </div>
      {error && <p className="mb-2 text-xs text-destructive">{error}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-3">
          <Photo src={IMG.front} box="60 468 740 184" spots={FRONT} states={states}
                 selected={selected} onSelect={setSelected} label="Front panel" />
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <Photo src={IMG.side} box="0 488 800 284" spots={SIDE} states={states}
                   selected={selected} onSelect={setSelected} label="Side" />
            <div className="rounded-lg border p-3 text-xs">
              <div className="font-semibold">Back panel</div>
              <div className="mt-1 text-muted-foreground">2x USB 3.0 and HDMI. Their state is in the list.</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {["ok", "idle", "warn", "crit"].map((t) => (
                  <span key={t} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5">
                    <span className="size-2 rounded-full" style={{ background: TONE[t] }} />
                    {{ ok: "active / connected", idle: "quiet / empty", warn: "warning", crit: "held elsewhere" }[t]}
                  </span>
                ))}
              </div>
            </div>
          </div>
          {sel && (
            <div className="rounded-lg border px-3 py-2 text-sm" aria-live="polite">
              <span className="inline-block size-2.5 rounded-full align-middle" style={{ background: TONE[sel.tone] }} />
              <strong className="ml-2">{sel.label}</strong>
              {sel.status ? <span className="ml-2">{sel.status}</span> : null}
              <div className="mt-0.5 text-xs text-muted-foreground">
                {sel.can}{sel.detail ? ` · ${sel.detail}` : ""}
              </div>
            </div>
          )}
        </div>

        <div className="min-w-0 overflow-hidden rounded-lg border">
          <div className="max-h-[640px] overflow-y-auto">
            {panels.map((panel) => (
              <div key={panel}>
                <div className="sticky top-0 bg-muted/90 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground backdrop-blur">
                  {panel}
                </div>
                <ul className="divide-y">
                  {states.filter((s) => s.panel === panel).map((s) => (
                    <li key={s.id}
                        className={"flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-sm " + (selected === s.id ? "bg-primary/10" : "hover:bg-muted/60")}
                        onMouseEnter={() => setSelected(s.id)} onClick={() => setSelected(s.id)}>
                      <span className="size-2.5 shrink-0 rounded-full"
                            style={s.tone === "unknown"
                              ? { border: `2px dashed ${TONE.unknown}` }
                              : { background: TONE[s.tone] }} />
                      <span className="w-36 shrink-0 truncate font-medium">{s.label}</span>
                      <span className="min-w-0 flex-1 truncate text-muted-foreground" title={s.detail}>
                        {s.status || <span className="opacity-60">-</span>}
                        {s.detail && s.status ? <span className="opacity-70"> · {s.detail}</span> : null}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
