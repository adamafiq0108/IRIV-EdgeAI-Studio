// Hailo load balancer (testing) -- one card on Settings > System.
//
// The switch is [system] chip_balance, saved through /api/system/settings
// like the other box switches (SystemPanel hands it `on` and `save`). The
// detection apps read it live and agree on fair shares through each other's
// heartbeat files (systems/demo/app/chipshare.py); this card only SHOWS what
// they say, from the read-only GET /api/system/chip-balance.
//
// The table polls every 5 s, only while this card is mounted and the tab is
// visible (usePoll), and is drawn when the balancer is on or when more than
// one system shares the chip -- one system alone has nothing to compare.
import { usePoll } from "../api/hooks.js";
import { CardHeader, StateSwitch } from "./ui/CardHeader.jsx";

// Why a camera sits where it does, in the words the table shows.
export const REASON_LABEL = {
  off: "balancer off",
  free: "not capped",
  fair: "fair share",
  floor: "floor (10 fps)",
  demand: "camera limit",
  hold: "settling",
  fallback: "fixed rule",
};

export default function ChipBalanceCard({ on, busy, onChange }) {
  const { data } = usePoll("/api/system/chip-balance", 5000);
  const rows = data?.rows || [];
  const showTable = on || (data?.systems || 0) > 1;
  return (
    <section className="rounded-xl border bg-card p-5">
      <CardHeader title="Hailo load balancer (testing)"
                  subtitle={<>
                    Splits the AI chip's frame rate fairly between all cameras on this
                    box. Off = today's fixed rule.
                  </>}>
        <StateSwitch checked={!!on} disabled={busy} aria-label="Hailo load balancer"
                     onCheckedChange={onChange} />
      </CardHeader>
      {showTable && (
        rows.length === 0 ? (
          <p className="muted small" style={{ margin: "8px 0 0" }}>
            No camera is running AI detection right now.
          </p>
        ) : (
          <table className="w-full text-sm" style={{ marginTop: 12 }}>
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-2 pr-3">System</th>
                <th className="py-2 pr-3">Camera</th>
                <th className="py-2 pr-3">Model</th>
                <th className="py-2 pr-3">Target fps</th>
                <th className="py-2 pr-3">Measured fps</th>
                <th className="py-2 pr-3">Share</th>
                <th className="py-2">Why</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.slug}/${r.camera}`} className="border-b">
                  <td className="py-1 pr-3">{r.slug}</td>
                  <td className="py-1 pr-3">{r.camera}</td>
                  <td className="py-1 pr-3">{r.model || "-"}</td>
                  <td className="py-1 pr-3">{r.target || "-"}</td>
                  <td className="py-1 pr-3">{r.fps}</td>
                  <td className="py-1 pr-3">{r.chip ? `${r.share}%` : "-"}</td>
                  <td className="py-1">{REASON_LABEL[r.reason] || r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
    </section>
  );
}
