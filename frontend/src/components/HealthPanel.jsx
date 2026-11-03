// Health - two cards, because there are two things an operator asks about:
// how the BOARD is doing (CPU, memory, every storage device, temperature) and
// how the HAILO is doing (module, load, inference rate, temperature, what it
// is running). Everything comes from /api/hub/health + /api/hub/pipelines;
// anything the box cannot measure shows "-" rather than a zero.
import { usePoll } from "../api/hooks.js";
import { ErrorNote, SkelCard } from "./Loading.jsx";

function pct(v) { return v == null ? "-" : `${Math.round(v)}%`; }
function temp(v) { return v == null ? "-" : `${Math.round(v)}°C`; }
function barColor(v, warn = 75, crit = 90) {
  if (v == null) return "#6b7280";
  return v >= crit ? "#ef4444" : v >= warn ? "#f59e0b" : "#22c55e";
}

/** One labelled metric with a bar when it is a percentage. */
function Meter({ label, value, sub, fmt = pct, warn, crit, bar = true }) {
  return (
    <div style={{ minWidth: 130, flex: "1 1 130px" }}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{fmt(value)}</div>
      {bar && typeof value === "number" && (
        <div style={{ height: 4, borderRadius: 3, background: "rgba(128,128,128,.2)", marginTop: 4 }}>
          <div style={{ height: 4, borderRadius: 3, width: `${Math.min(100, value)}%`,
                        background: barColor(value, warn, crit) }} />
        </div>
      )}
      {sub && <div className="text-xs text-muted-foreground" style={{ marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function Card({ title, sub, right, children }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div style={{ display: "flex", alignItems: "flex-start",
                    justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
        <div>
          <div className="font-medium">{title}</div>
          {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
        </div>
        {right}
      </div>
      {children}
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

  return (
    <Card
      title={`IRIV Edge AI · ${host.hostname || "board"}`}
      sub={[
        host.cpu_count ? `${host.cpu_count} cores` : null,
        host.load1 != null ? `load ${host.load1}` : null,
      ].filter(Boolean).join(" · ") || "the board running the dashboards and detection"}
    >
      <div style={{ display: "flex", flexWrap: "wrap", gap: 18 }}>
        <Meter label="CPU" value={host.cpu_percent} />
        <Meter label="RAM" value={host.mem_percent} />
        {storages.map((s, i) => (
          <Meter key={s.mount || i}
                 label={`Storage ${i + 1} · ${s.label || s.mount}`}
                 value={s.percent} warn={80} crit={92}
                 sub={[s.mount,
                         s.total_gb && s.used_gb != null ? `${s.used_gb} of ${s.total_gb} GB used`
                           : s.total_gb ? `${s.total_gb} GB` : null]
                        .filter(Boolean).join(" · ")} />
        ))}
        <Meter label="CPU temp" value={host.cpu_temp_c} fmt={temp}
               warn={70} crit={82} bar={false} />
      </div>
    </Card>
  );
}


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

  return (
    <Card
      title="Hailo accelerator"
      sub={[npu.fw ? `firmware ${npu.fw}` : null,
            npu.serial ? `serial ${npu.serial}` : null]
             .filter(Boolean).join(" · ") || "the NPU every system on this board shares"}
      right={present ? (
        <span className="text-xs" style={{
          padding: "2px 10px", borderRadius: 999,
          background: npu.status === "hot" ? "rgba(239,68,68,.18)"
            : npu.status === "running" ? "rgba(34,197,94,.18)" : "rgba(128,128,128,.15)",
          color: npu.status === "hot" ? "#ef4444"
            : npu.status === "running" ? "#22c55e" : "#9ca3af" }}>
          {npu.status || "present"}
        </span>
      ) : null}
    >
      {!present ? (
        <p className="text-sm text-muted-foreground">
          No Hailo NPU detected on this board (hailortcli not present, or no
          accelerator fitted).
        </p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18 }}>
          <Meter label="Hailo HAT" value={hat || "-"} fmt={(v) => v} bar={false}
                 sub={npu.arch ? "architecture" : undefined} />
          <Meter label="NPU load (est)" value={loadPct}
                 sub={loadPct >= 80 ? "heavy" : loadPct >= 40 ? "moderate" : "light"}
                 warn={70} crit={88} />
          <Meter label="Inference FPS" value={peak || null} bar={false}
                 fmt={(v) => (v == null ? "-" : String(Math.round(v)))}
                 sub={`${Math.round(fpsSum)} fps across pipelines`} />
          <Meter label="NPU temp" value={npu.temp_c} fmt={temp} bar={false}
                 warn={75} crit={85}
                 sub={npu.power_w != null ? `${npu.power_w.toFixed(1)} W`
                        : (npu.power_note || undefined)} />
          <Meter label="Running pipelines" value={running.length} bar={false}
                 fmt={(v) => String(v)}
                 sub={`${chipPipes.length} on the NPU`} />
        </div>
      )}
    </Card>
  );
}

/** What is actually loaded on the chip, one row per system. */
function LoadedNetworks({ pipelines }) {
  return (
    <Card title="Loaded networks"
          sub="every system's model, and what it is achieving right now">
      {pipelines.length ? pipelines.map((p) => (
        <div key={p.slug} style={{ display: "flex", alignItems: "center", gap: 10,
                                   padding: "6px 0", borderBottom: "1px solid rgba(128,128,128,.12)" }}>
          <span style={{ width: 8, height: 8, borderRadius: 8,
                         background: p.running ? "#22c55e" : "#6b7280" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="text-sm font-medium">
              {p.model || p.kind || "-"}
              <span className="text-xs text-muted-foreground"> · {p.name}</span>
            </div>
            <div className="text-xs text-muted-foreground font-mono">
              {p.running && p.fps != null ? `${p.fps} fps` : "0 fps"}
              {p.pid ? ` · pid ${p.pid}` : ""}
              {p.camera != null ? ` · cam ${p.camera}` : ""}
              {Object.entries(p.infer_fps || {}).map(([m, f]) => ` · ${m} ${f}fps`).join("")}
            </div>
          </div>
          <span className="text-xs" style={{
            padding: "2px 10px", borderRadius: 999,
            background: p.running ? "rgba(59,130,246,.2)" : "rgba(128,128,128,.15)",
            color: p.running ? "#3b82f6" : "#9ca3af" }}>
            {p.running ? "Active" : "Stopped"}
          </span>
        </div>
      )) : <p className="text-sm text-muted-foreground">No detection pipelines yet.</p>}
    </Card>
  );
}

export default function HealthPanel() {
  const { data, error } = usePoll("/api/hub/health", 10000);
  const { data: pipes } = usePoll("/api/hub/pipelines", 5000);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelCard rows={5} />;
  const pipelines = pipes?.pipelines || [];

  return (
    <div className="space-y-4">
      {data.host && <BoardCard host={data.host} />}
      <HailoCard npu={data.host?.npu} pipelines={pipelines} arch={pipes?.arch} />
      <LoadedNetworks pipelines={pipelines} />
    </div>
  );
}
