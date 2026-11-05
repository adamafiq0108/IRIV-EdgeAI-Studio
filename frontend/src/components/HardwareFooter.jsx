// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// HardwareFooter - one thin strip along the bottom of every system page, so the
// operator can watch what the box is doing WHILE it does it.
//
// The two things that load this board hardest are a detection run and an OCR
// read, and both happen on pages where you are aiming a camera or reading a
// label - you cannot leave for a health page to find out whether the Pi is
// throttling, because leaving is the thing you are trying not to do.
//
// It reads /api/host/meter (core/routes/system.py), which is a DIFFERENT and
// much cheaper endpoint than the hub's /api/hub/health: 0.5 ms per sample on
// this Pi against 385 ms, because a footer samples every couple of seconds and
// a monitor that costs 12% of a core is measuring itself.
//
// Two rules the whole component is built around:
//
//   1. A value that cannot be read shows as "-". Never 0. A CPU genuinely at 0%
//      and a CPU nobody could measure are different facts and must not render
//      the same, or the footer quietly becomes a decoration.
//   2. It is a gauge, not an alarm panel. Colour appears only when a number is
//      actually hot - the rest of the time the strip is grey and ignorable,
//      which is what lets it be always visible without stealing attention.
//
// It sits in the shell's normal flow BELOW <main>, not fixed over it, so it can
// never cover page content. The two pages that size themselves to the viewport
// (.wf-page, .ocr-page) subtract --hw-footer-h - see theme.css.
import { useCallback, useEffect, useState } from "react";
import { Cable } from "lucide-react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { UsbPorts, EthPorts, useBoardPorts } from "./BoardPorts.jsx";
import TerminalBlock from "../workflow/TerminalBlock.jsx";

// While the box is working: fast enough to see inside one job. An OCR read is a
// few seconds end to end, so a 10 s poll can miss an entire read - the exact
// moment the user wants to watch. 2 s puts at least one sample, usually two,
// inside every read, and gives CPU% a 2 s averaging window, which is about as
// short as a number a human is reading can usefully be.
const BUSY_MS = 2000;
// Idle: nothing is moving, so this is only "is the box still there". Matches
// the existing HealthPanel cadence.
const IDLE_MS = 10000;

// Thresholds. CPU/RAM warn late - a detection run is SUPPOSED to be busy, and a
// footer that goes amber every time work starts has said nothing. The
// temperatures are where the real warning is: this Pi throttles.
const LIMITS = {
  cpu: [85, 96],
  mem: [85, 95],
  cpuTemp: [70, 80],
  npuTemp: [80, 88],
};

function level(value, limits) {
  if (value == null || !limits) return "";
  if (value >= limits[1]) return "hw-crit";
  if (value >= limits[0]) return "hw-warn";
  return "";
}

// The "-" in each of these is the whole point: null means the box could not
// measure the thing, and that is a different fact from measuring it as zero. A
// stopped detector must not render like one managing 0.0 fps, and an
// unreadable CPU must not render like an idle one.
const pct = (v) => (v == null ? "-" : `${Math.round(v)}%`);
const deg = (v) => (v == null ? "-" : `${Math.round(v)}°`);
const fps = (v) => (v == null ? "-" : String(Math.round(v)));

/** One label + value pair. `tone` is "" unless the number is genuinely hot. */
function Stat({ label, value, tone = "", title }) {
  return (
    <span className={`hw-stat ${tone}`} title={title}>
      <span className="hw-k">{label}</span>
      <span className="hw-v">{value}</span>
    </span>
  );
}

/**
 * What the strip says about one sample, as plain data: `{key, label, value,
 * tone, title}` per stat, in reading order. Rendering is then a map over this,
 * which keeps the "unreadable shows as -" rule in ONE place instead of repeated
 * across seven pieces of JSX - and makes it checkable without a browser.
 *
 * `meter` is null before the first answer arrives and after a failed sample.
 */
export function describeStats(meter) {
  const npu = meter?.npu || {};
  return [
    { key: "cpu", label: "CPU", value: pct(meter?.cpu_percent),
      tone: level(meter?.cpu_percent, LIMITS.cpu),
      title: "Processor use since the previous sample" },
    { key: "mem", label: "RAM", value: pct(meter?.mem_percent),
      tone: level(meter?.mem_percent, LIMITS.mem),
      title: "Memory in use" },
    { key: "cputemp", label: "CPU°", value: deg(meter?.cpu_temp_c),
      tone: level(meter?.cpu_temp_c, LIMITS.cpuTemp),
      title: "CPU temperature - this board throttles when it gets hot" },
    { key: "sep1", sep: true },
    // No accelerator is not a cold accelerator: with no Hailo there is no
    // temperature and no model count, so both are "-" rather than 0.
    { key: "nputemp", label: "NPU°",
      value: npu.present ? deg(npu.temp_c) : "-",
      tone: npu.present ? level(npu.temp_c, LIMITS.npuTemp) : "",
      title: npu.present
        ? `Hailo ${npu.arch || "accelerator"} temperature`
        : "No Hailo accelerator detected on this board" },
    { key: "models", label: "models",
      value: npu.present && npu.models != null ? String(npu.models) : "-",
      tone: "",
      title: "Cameras inferencing on the accelerator right now" },
    { key: "sep2", sep: true },
    { key: "fps", label: "FPS", value: fps(meter?.fps), tone: "",
      title: "Frames a second the detection app is actually managing" },
  ];
}

export default function HardwareFooter() {
  const [meter, setMeter] = useState(null);
  const [down, setDown] = useState(false);
  // The board's connectors, drawn like the faceplate, one click away from
  // the same strip that shows its load: "what is this box doing" and "what
  // is plugged into it" are the two halves of one question. The panel polls
  // only while open; Escape and the button both close it.
  const [portsOpen, setPortsOpen] = useState(false);
  const boardPorts = useBoardPorts(portsOpen);
  useEffect(() => {
    if (!portsOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setPortsOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [portsOpen]);

  const sample = useCallback(async () => {
    try {
      const d = await api.get("/api/host/meter");
      setMeter(d);
      setDown(false);
    } catch {
      // An unreachable master must not leave the last reading on screen looking
      // live. Drop to "-" everywhere and say so.
      setMeter(null);
      setDown(true);
    }
  }, []);

  // Poll fast while there is work in flight, slowly when there is not, and not
  // at all while the tab is hidden - useVisibleInterval stops the timer on
  // visibilitychange and resamples the moment the tab comes back, so returning
  // to the page shows the box as it is now rather than as it was.
  const busy = !!meter?.busy;
  useVisibleInterval(sample, busy ? BUSY_MS : IDLE_MS);

  // useVisibleInterval fires the FIRST time one period after mount, so without
  // this the strip would sit blank for two seconds. It primes on return from a
  // hidden tab by itself; only the very first sample needs asking for.
  useEffect(() => { sample(); }, [sample]);

  return (
    <footer className="hw-footer" aria-label="Hardware">
      <span className={`hw-dot ${down ? "is-down" : busy ? "is-busy" : ""}`} />
      {describeStats(meter).map((s) => (s.sep
        ? <span key={s.key} className="hw-sep" />
        : <Stat key={s.key} label={s.label} value={s.value}
                tone={s.tone} title={s.title} />))}
      <span className="hw-tail">
        {down ? "no reading" : busy ? "analysing" : "idle"}
      </span>
      <button type="button"
              className={"btn btn-sm" + (portsOpen ? " btn-accent" : "")}
              style={{ padding: "1px 7px", lineHeight: 1 }}
              title="The board's connectors - USB sockets, ethernet jacks, screw terminals, live"
              onClick={() => setPortsOpen((v) => !v)}>
        <Cable size={12} />
      </button>
      {portsOpen && (
        <div className="hw-ports-pop" role="dialog" aria-label="Board ports">
          {/* One ROW, in the order the board is read at a cabinet: the screw
              terminals first (that is what a wire goes into), then the
              ethernet jacks, then USB. Stacked, the three panels made a
              column taller than the window and put the terminal block --
              the reason anyone opens this -- below the fold. */}
          <div className="hw-ports-row">
            <div className="hw-ports-col hw-ports-col-wide">
              <div className="hw-ports-title">Terminals</div>
              <TerminalBlock usedPorts={[]} poll={portsOpen} />
            </div>
            <div className="hw-ports-col">
              <div className="hw-ports-title">Ethernet</div>
              <EthPorts eth={boardPorts.data?.eth} />
            </div>
            <div className="hw-ports-col">
              <div className="hw-ports-title">USB</div>
              <UsbPorts usb={boardPorts.data?.usb} />
            </div>
          </div>
          {boardPorts.error && (
            <p className="muted small" style={{ margin: 0 }}>
              Cannot read the board: {boardPorts.error}
            </p>
          )}
        </div>
      )}
    </footer>
  );
}
