// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// About - who made this product, how the BOARD is doing (CPU, memory, every
// storage device, temperature), how the HAILO is doing (module, load,
// inference rate, temperature, what it is running), and the software update.
// Everything comes from /api/hub/health + /api/hub/pipelines +
// /api/hub/about; anything the box cannot measure shows "-" rather than a zero.
//
// Laid out as a headline card, then two columns: the live readings on the
// left, the software on the right (stacked on a narrow screen). A reading is a
// stat tile: a sentence-case label, the value, and for a ratio a meter whose
// fill carries severity. A state other than normal is also WRITTEN ("High",
// "Critical") beside an icon, so it never rests on colour alone.
import { Activity, Cpu, HardDrive, OctagonAlert, TriangleAlert } from "lucide-react";
import { usePoll } from "../api/hooks.js";
import AboutCard from "./AboutCard.jsx";
import { ErrorNote, SkelAbout } from "./Loading.jsx";
import SoftwareUpdateCard from "./SoftwareUpdateCard.jsx";

function pct(v) { return v == null ? "-" : `${Math.round(v)}%`; }
function temp(v) { return v == null ? "-" : `${Math.round(v)}°C`; }

const ACCENT = "var(--primary, #04bade)";
const SEVERITY = {
  ok:   { fill: ACCENT,    word: "",         Icon: null },
  warn: { fill: "#fab219", word: "High",     Icon: TriangleAlert },
  crit: { fill: "#d03b3b", word: "Critical", Icon: OctagonAlert },
};

function severity(v, warn, crit) {
  if (typeof v !== "number") return "ok";
  if (crit != null && v >= crit) return "crit";
  if (warn != null && v >= warn) return "warn";
  return "ok";
}

/** One reading: label, value, and (for a ratio) a meter. The unfilled track is
 *  a lighter step of the fill's own colour, so the state reads across the bar. */
function Tile({ label, value, sub, fmt = pct, warn = 75, crit = 90, bar = true }) {
  const sev = SEVERITY[severity(value, warn, crit)];
  const Icon = sev.Icon;
  return (
    <div className="min-w-0 rounded-lg border bg-background/60 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-semibold leading-none">{fmt(value)}</span>
        {Icon && (
          <span className="inline-flex items-center gap-1 text-xs font-medium">
            <Icon size={13} style={{ color: sev.fill }} aria-hidden="true" />
            {sev.word}
          </span>
        )}
      </div>
      {bar && typeof value === "number" && (
        <div className="mt-2.5 h-2 overflow-hidden rounded-full"
             style={{ background: `color-mix(in srgb, ${sev.fill} 20%, transparent)` }}
             role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100}
             aria-valuenow={Math.round(value)}>
          <div className="h-2 rounded-full"
               style={{ width: `${Math.max(2, Math.min(100, value))}%`, background: sev.fill }} />
        </div>
      )}
      {sub && <div className="mt-1.5 truncate text-xs text-muted-foreground" title={sub}>{sub}</div>}
    </div>
  );
}

/** A section: an icon and a heading, what it covers, and its tiles. */
function Section({ icon: Icon, title, sub, right, children }) {
  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
            <Icon size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold leading-tight">{title}</h2>
            {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
          </div>
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

/** Tiles in rows of three, and the last row shares the full width: five tiles
 *  are three, then two wider ones; four are three, then one across. On a
 *  narrow screen they are simply two to a row. */
function TileGrid({ children }) {
  const items = [children].flat(2).filter(Boolean);
  const n = items.length;
  const rem = n % 3;
  const lastRow = n - (rem || 3);
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
      {items.map((el, i) => {
        let span = "md:col-span-2";
        if (i >= lastRow && rem === 2) span = "md:col-span-3";
        if (i >= lastRow && rem === 1) span = "md:col-span-6";
        return <div key={el.key ?? i} className={"min-w-0 " + span}>{el}</div>;
      })}
    </div>
  );
}

/** The board itself: CPU, RAM, each storage device, CPU temperature. */
function BoardCard({ host }) {
  // Every mounted filesystem worth showing (root + NVMe/USB under /mnt,
  // /media). Falls back to the single root figure on a host that reports no
  // partition list.
  const storages = host.storages && host.storages.length
    ? host.storages
    : [{ mount: "/", label: "Storage", percent: host.disk_percent, total_gb: null }];
  const sub = [host.hostname,
               host.cpu_count ? `${host.cpu_count} cores` : null,
               host.load1 != null ? `load ${host.load1}` : null].filter(Boolean).join(" · ");
  return (
    <Section icon={Cpu} title="This board"
             sub={sub || "the board running the dashboards and detection"}>
      <TileGrid>
        <Tile label="CPU" value={host.cpu_percent} />
        <Tile label="Memory" value={host.mem_percent} />
        <Tile label="CPU temperature" value={host.cpu_temp_c} fmt={temp}
              warn={70} crit={82} bar={false} />
        {storages.map((s, i) => (
          <Tile key={s.mount || i}
                label={`Storage ${i + 1} · ${s.label || s.mount}`}
                value={s.percent} warn={80} crit={92}
                sub={[s.mount,
                      s.total_gb && s.used_gb != null ? `${s.used_gb} of ${s.total_gb} GB used`
                        : s.total_gb ? `${s.total_gb} GB` : null]
                       .filter(Boolean).join(" · ")} />
        ))}
      </TileGrid>
    </Section>
  );
}

const CHIP = {
  running: { word: "Running", dot: "#22a06b" },
  hot:     { word: "Hot",     dot: "#d03b3b" },
  idle:    { word: "Idle",    dot: "#8a8f98" },
};

/** The accelerator: module, estimated load, inference rate, temperature,
 *  and how many pipelines are on it. */
function HailoCard({ npu = {}, pipelines, arch }) {
  const present = npu.present || npu.temp_c != null;
  const running = pipelines.filter((p) => p.running);
  const chipPipes = running.filter((p) =>
    ["hailo", "hailo-multi", "gstreamer"].includes(p.kind));
  // HailoRT exposes no utilisation %, so load is ESTIMATED from what the apps
  // actually achieve against a nominal 60fps chip budget. Labelled as an
  // estimate rather than dressed up as a hardware reading.
  const fpsSum = chipPipes.reduce((a, p) => a + (Number(p.fps) || 0), 0);
  const peak = Math.max(0, ...running.map((p) => Number(p.fps) || 0));
  const loadPct = Math.min(100, Math.round((fpsSum / 60) * 100));
  const hat = (arch || npu.arch || "").toUpperCase();
  const chip = CHIP[npu.status] || (present ? CHIP.idle : null);

  return (
    <Section icon={Activity} title="AI accelerator"
             sub={[npu.fw ? `Hailo firmware ${npu.fw}` : null,
                   npu.serial ? `serial ${npu.serial}` : null]
                    .filter(Boolean).join(" · ") || "the NPU every system on this board shares"}
             right={present && chip ? (
               <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium">
                 <span className="size-2 rounded-full" style={{ background: chip.dot }} aria-hidden="true" />
                 {chip.word}
               </span>
             ) : null}>
      {!present ? (
        <p className="text-sm text-muted-foreground">
          No Hailo NPU detected on this board (hailortcli not present, or no
          accelerator fitted).
        </p>
      ) : (
        <TileGrid>
          <Tile label="Hailo HAT" value={hat || "-"} fmt={(v) => v} bar={false}
                sub={npu.arch ? "architecture" : undefined} />
          <Tile label="NPU load (estimate)" value={loadPct} warn={70} crit={88}
                sub={loadPct >= 80 ? "heavy" : loadPct >= 40 ? "moderate" : "light"} />
          <Tile label="Inference speed" value={peak || null} bar={false}
                fmt={(v) => (v == null ? "-" : `${Math.round(v)} fps`)}
                sub={`${Math.round(fpsSum)} fps across pipelines`} />
          <Tile label="NPU temperature" value={npu.temp_c} fmt={temp} bar={false}
                warn={75} crit={85}
                sub={npu.power_w != null ? `${npu.power_w.toFixed(1)} W`
                       : (npu.power_note || undefined)} />
          <Tile label="Running pipelines" value={running.length} bar={false}
                fmt={(v) => String(v)} sub={`${chipPipes.length} on the NPU`} />
        </TileGrid>
      )}
    </Section>
  );
}

/** What is actually loaded on the chip, one row per system. */
function LoadedNetworks({ pipelines }) {
  return (
    <Section icon={HardDrive} title="Loaded networks"
             sub="every system's model, and what it is achieving right now">
      {pipelines.length ? (
        <ul className="divide-y">
          {pipelines.map((p) => (
            <li key={p.slug} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
              <span className="size-2 shrink-0 rounded-full" aria-hidden="true"
                    style={{ background: p.running ? "#22a06b" : "#8a8f98" }} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {p.model || p.kind || "-"}
                  <span className="font-normal text-muted-foreground"> · {p.name}</span>
                </div>
                <div className="truncate font-mono text-xs text-muted-foreground">
                  {p.running && p.fps != null ? `${p.fps} fps` : "0 fps"}
                  {p.pid ? ` · pid ${p.pid}` : ""}
                  {p.camera != null ? ` · cam ${p.camera}` : ""}
                  {Object.entries(p.infer_fps || {}).map(([m, f]) => ` · ${m} ${f}fps`).join("")}
                </div>
              </div>
              <span className="shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-medium">
                {p.running ? "Active" : "Stopped"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          No detection pipelines yet. A system's model appears here once it is running.
        </p>
      )}
    </Section>
  );
}

export default function HealthPanel() {
  const { data, error } = usePoll("/api/hub/health", 10000);
  const { data: pipes } = usePoll("/api/hub/pipelines", 5000);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelAbout />;
  const pipelines = pipes?.pipelines || [];

  return (
    <div className="mx-auto w-full max-w-[1280px] space-y-5">
      <AboutCard hostname={data.host?.hostname} />
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-5">
          {data.host && <BoardCard host={data.host} />}
          <HailoCard npu={data.host?.npu} pipelines={pipelines} arch={pipes?.arch} />
        </div>
        <div className="min-w-0 space-y-5">
          <SoftwareUpdateCard />
          <LoadedNetworks pipelines={pipelines} />
        </div>
      </div>
    </div>
  );
}
