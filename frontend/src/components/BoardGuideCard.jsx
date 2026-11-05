// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Board guide - the IRIV Edge AI CM5 as Cytron draws it, with every port marked
// on the picture and what is on each one RIGHT NOW beside it.
//
// The picture is the product page's own "External Layout" (front, left and
// right views, core/static/img/iriv-cm5-layout.png); the block diagram and
// the board layout under it are the page's too. Each port is a dot drawn over
// the picture at the connector, in the picture's own pixel coordinates (an SVG
// viewBox crops it to one view, so the numbers never need converting). The
// dot's colour is the port's state, read from GET /api/hub/board every two
// seconds while the card is on screen:
//
//   green   something is on it and active: a link with a cable, a camera, a
//           contact closed, a relay energised, a Modbus device answering
//   grey    nothing on it, or quiet
//   amber   a warning: RS485 asked and got no answer
//   red     the line is held by another program
//   dashed  the box could not read it (never shown as "off")
//
// A LIGHT MEANS THE SIGNAL IS ACTIVE NOW, read back from the hardware - not
// "a rule uses this terminal". A screw terminal cannot tell whether a wire is
// in it, so for the terminals the honest words are "reads high", "energised",
// "on", "answering" - never "connected".
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, Cpu } from "lucide-react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";

const IMG = {
  layout: "/static/img/iriv-cm5-layout.png",       // 1314 x 1600
  block: "/static/img/iriv-cm5-block.png",
  internal: "/static/img/iriv-cm5-internal.png",
  guide: "/static/img/iriv-edge-ai-cm5.png",
};
const LAYOUT_W = 1314, LAYOUT_H = 1600;

//: Where each port is on the layout picture, in its pixels, per view.
const FRONT = {
  box: "340 230 960 270",
  spots: [
    { id: "BUTTON", x: 516, y: 372, r: 10 }, { id: "POWER", x: 594, y: 372, r: 10 },
    { id: "VIN", x: 655, y: 355, r: 6 }, { id: "RS485", x: 703, y: 355, r: 6 },
    { id: "DO0", x: 764, y: 355, r: 6 }, { id: "L0", x: 811, y: 355, r: 6 },
    { id: "GND", x: 655, y: 382, r: 6 }, { id: "SS", x: 680, y: 382, r: 6 },
    { id: "DI0", x: 703, y: 382, r: 6 }, { id: "DI1", x: 727, y: 382, r: 6 },
    { id: "DO1", x: 764, y: 382, r: 6 }, { id: "L1", x: 811, y: 382, r: 6 },
    { id: "ETH0", x: 907, y: 355, r: 12 }, { id: "ETH1", x: 1040, y: 355, r: 12 },
    { id: "USBC", x: 1190, y: 385, r: 10 },
    { id: "LEDS", x: 475, y: 413, r: 9 }, { id: "LED01", x: 599, y: 413, r: 9 },
  ],
};
const LEFT = {
  box: "500 660 790 320",
  spots: [
    { id: "DIN", x: 553, y: 833, r: 11 }, { id: "WIFI", x: 653, y: 770, r: 11 },
    { id: "4G", x: 820, y: 770, r: 11 }, { id: "OLED", x: 962, y: 820, r: 11 },
  ],
};
const RIGHT = {
  box: "470 1190 790 300",
  spots: [
    { id: "USB0", x: 725, y: 1300, r: 11 }, { id: "USB1", x: 725, y: 1350, r: 11 },
    { id: "HDMI", x: 857, y: 1357, r: 11 },
    { id: "MIPI0", x: 980, y: 1375, r: 8 }, { id: "MIPI1", x: 1097, y: 1375, r: 8 },
    { id: "SIM", x: 1085, y: 1415, r: 10 },
  ],
};

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

const NONE = { tone: "idle", status: "", detail: "Not monitored" };

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
    { panel: "Front", id: "POWER", label: "Power button", can: "Power on and off", ...NONE, detail: "Hold to switch the box off" },
    { panel: "Front", id: "BUTTON", label: "User button", can: "1 programmable button (GPIO4)",
      ...(button ? gpioState(button, "input") : { tone: "unknown", status: "not detected", detail: "" }),
      status: button?.busy ? "held by another program" : button?.value === 1 ? "pressed" : button?.value === 0 ? "released" : (button ? "cannot read" : "not detected") },
    { panel: "Front", id: "VIN", label: "VIN", can: "DC 10-30 V in, surge-protected", ...NONE, detail: "Field power; the box is on, so it is powered" },
    { panel: "Front", id: "GND", label: "GND", can: "Power return", ...NONE, detail: "" },
    { panel: "Front", id: "RS485", label: "RS485 (A, B, G)", can: "Isolated, up to 500 kbps, via USB-to-UART", ...rsState },
    { panel: "Front", id: "SS", label: "S/S", can: "Input common for DI0 and DI1", ...NONE, detail: "" },
    { panel: "Front", id: "DI0", label: "DI0", can: "Isolated input, 3-50 V high (GPIO22)", ...gpioState(byId(ports, "DI0"), "input") },
    { panel: "Front", id: "DI1", label: "DI1", can: "Isolated input, 3-50 V high (GPIO27)", ...gpioState(byId(ports, "DI1"), "input") },
    { panel: "Front", id: "DO0", label: "DO0", can: "Isolated dry contact, 50 V 500 mA (GPIO23)", ...gpioState(byId(ports, "DO0"), "output") },
    { panel: "Front", id: "DO1", label: "DO1", can: "Isolated dry contact, 50 V 500 mA (GPIO24)", ...gpioState(byId(ports, "DO1"), "output") },
    { panel: "Front", id: "L0", label: "L0 (+ -)", can: "LED driver, Vin up to 2 A, PWM (GPIO12)", ...gpioState(byId(ports, "L0"), "pwm") },
    { panel: "Front", id: "L1", label: "L1 (+ -)", can: "LED driver, Vin up to 2 A, PWM (GPIO13)", ...gpioState(byId(ports, "L1"), "pwm") },
    { panel: "Front", id: "ETH0", label: "ETH0", can: "Gigabit RJ45, the module's own port", ...ethState("eth0") },
    { panel: "Front", id: "ETH1", label: "ETH1", can: "Gigabit RJ45, a controller on USB 3", ...ethState("eth1") },
    { panel: "Front", id: "USBC", label: "USB-C BOOT/DBG", can: "Boot and debug from a computer", ...NONE, detail: "Not monitored; not for field power" },
    { panel: "Front", id: "LEDS", label: "Status LEDs", can: "PWR, ACT, M.2 · 4G PWR, WWAN", ...NONE, status: "read the lights", detail: "Power, activity, M.2 and 4G module state" },
    { panel: "Front", id: "LED01", label: "LED 0, LED 1", can: "2 programmable LEDs (GPIO20, GPIO21)", ...NONE, status: "programmable", detail: "Driven by a system's rules" },
    { panel: "Left", id: "DIN", label: "DIN bracket", can: "DIN-rail mount", ...NONE, detail: "" },
    { panel: "Left", id: "WIFI", label: "WiFi antenna", can: "Wi-Fi 2.4 / 5 GHz, Bluetooth 5.0", ...wifiState },
    { panel: "Left", id: "4G", label: "4G antenna (reserved)", can: "For a 4G module in the mPCIe slot", ...NONE },
    { panel: "Left", id: "OLED", label: "OLED display", can: '0.96" SSD1306 on I2C1 (0x3C)', ...NONE, detail: "The front panel the box prints by itself" },
    { panel: "Right", id: "USB0", label: "USB 3.0 (upper)", can: "Camera or drive, via the USB 3.0 hub", ...usbState(0) },
    { panel: "Right", id: "USB1", label: "USB 3.0 (lower)", can: "Camera or drive, via the USB 3.0 hub", ...usbState(1) },
    { panel: "Right", id: "HDMI", label: "HDMI", can: "HDMI 2.0, 4K at 60 Hz", ...NONE },
    { panel: "Right", id: "MIPI0", label: "MIPI 0", can: "4-lane CSI camera (I2C0)", ...NONE },
    { panel: "Right", id: "MIPI1", label: "MIPI 1", can: "4-lane CSI camera", ...NONE },
    { panel: "Right", id: "SIM", label: "Micro SIM socket", can: "For a 4G module in the mPCIe slot", ...NONE },
    { panel: "Inside", id: "BUZZER", label: "Buzzer", can: "Local alarm (GPIO19)",
      ...(buzzer ? gpioState(buzzer, "output") : { tone: "unknown", status: "not detected", detail: "" }),
      status: buzzer?.busy ? "held by another program" : buzzer?.lit ? "sounding" : buzzer ? "silent" : "not detected" },
    { panel: "Inside", id: "ATECC", label: "ATECC608 secure chip", can: "I2C1 (0x60)", ...NONE },
    { panel: "Inside", id: "M2", label: "M.2 Key-M (NVMe)", can: "Behind the PCIe switch, with the AI HAT+", ...NONE, detail: "The NVMe shows on the board card above" },
    { panel: "Inside", id: "MPCIE", label: "Mini PCIe (4G, LoRa)", can: "USB only, with the micro-SIM", ...NONE },
  ];
}

/** One view of the layout picture with the dots on it. */
function View({ view, states, selected, onSelect, label }) {
  return (
    <svg viewBox={view.box} className="w-full rounded-lg border bg-white" role="group" aria-label={label}
         style={{ display: "block" }}>
      <image href={IMG.layout} x="0" y="0" width={LAYOUT_W} height={LAYOUT_H} />
      {view.spots.map((s) => {
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
            {sel && <circle cx={s.x} cy={s.y} r={s.r + 7} fill="none" stroke="#04bade" strokeWidth="2.5" className="bg-spot-ring" />}
            <circle cx={s.x} cy={s.y} r={s.r + 2.5} fill="#ffffff" opacity="0.95" />
            <circle cx={s.x} cy={s.y} r={s.r} fill={st?.tone === "unknown" ? "none" : tone}
                    stroke={tone} strokeWidth={st?.tone === "unknown" ? 2 : 0}
                    strokeDasharray={st?.tone === "unknown" ? "3 2" : undefined} />
          </g>
        );
      })}
    </svg>
  );
}

function Legend() {
  const words = { ok: "active / connected", idle: "quiet / empty", warn: "warning", crit: "held elsewhere" };
  return (
    <div className="flex flex-wrap gap-1.5 text-xs">
      {Object.keys(words).map((t) => (
        <span key={t} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5">
          <span className="size-2 rounded-full" style={{ background: TONE[t] }} />{words[t]}
        </span>
      ))}
      <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5">
        <span className="size-2 rounded-full" style={{ border: `2px dashed ${TONE.unknown}` }} />cannot read
      </span>
    </div>
  );
}

export default function BoardGuideCard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("ETH0");
  const [inside, setInside] = useState(false);
  const load = useCallback(async () => {
    try { setData(await api.get("/api/hub/board")); setError(""); }
    catch (e) { setError(e.message || "cannot read the board"); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useVisibleInterval(load, 2000);

  const states = useMemo(() => describe(data), [data]);
  const sel = states.find((s) => s.id === selected);
  const panels = ["Front", "Left", "Right", "Inside"];

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
          Printable guide: what each port can do and what to plug in
        </a>
      </div>
      {error && <p className="mb-2 text-xs text-destructive">{error}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-3">
          <View view={FRONT} states={states} selected={selected} onSelect={setSelected} label="Front" />
          <div className="grid gap-3 sm:grid-cols-2">
            <View view={LEFT} states={states} selected={selected} onSelect={setSelected} label="Left side" />
            <View view={RIGHT} states={states} selected={selected} onSelect={setSelected} label="Right side" />
          </div>
          {sel && (
            <div className="rounded-lg border px-3 py-2 text-sm" aria-live="polite">
              <span className="inline-block size-2.5 rounded-full align-middle"
                    style={sel.tone === "unknown" ? { border: `2px dashed ${TONE.unknown}` } : { background: TONE[sel.tone] }} />
              <strong className="ml-2">{sel.label}</strong>
              {sel.status ? <span className="ml-2">{sel.status}</span> : null}
              <div className="mt-0.5 text-xs text-muted-foreground">
                {sel.can}{sel.detail ? ` · ${sel.detail}` : ""}
              </div>
            </div>
          )}
          <Legend />
        </div>

        <div className="min-w-0 overflow-hidden rounded-lg border">
          <div className="max-h-[720px] overflow-y-auto">
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

      {/* How it is wired inside, from the product page: the block diagram and the board. */}
      <button type="button" className="mt-4 flex items-center gap-1.5 text-sm font-medium"
              aria-expanded={inside} onClick={() => setInside((v) => !v)}>
        <ChevronDown size={15} style={{ transition: "transform .15s", transform: inside ? "rotate(180deg)" : "none" }} />
        Inside the box: block diagram and board layout
      </button>
      {inside && (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <a href={IMG.block} target="_blank" rel="noopener noreferrer" className="block">
            <img src={IMG.block} loading="lazy" decoding="async" className="w-full rounded-lg border bg-white"
                 alt="Block diagram: what each port is connected to on the Compute Module 5" />
          </a>
          <a href={IMG.internal} target="_blank" rel="noopener noreferrer" className="block">
            <img src={IMG.internal} loading="lazy" decoding="async" className="w-full rounded-lg border bg-white"
                 alt="Internal board layout: the M.2 socket, mPCIe socket, GPIO header for the AI HAT+, RTC battery, fuses and buzzer" />
          </a>
        </div>
      )}
    </section>
  );
}
