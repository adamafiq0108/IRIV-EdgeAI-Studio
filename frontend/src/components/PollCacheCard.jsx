// Shared poll cache (testing) -- one card on Settings > System.
//
// The switch is [system] share_polls, saved through /api/system/settings
// like the other box switches (SystemPanel hands it `on` and `save`). With it
// on, the hub and every master compute an expensive read once and hand it to
// every open page entitled to the identical answer (core/services/
// pollshare.py). Each page still signs in and is checked on its own.
//
// The counters line is fetched only while the switch is on and this card is
// on screen (PollCacheStats mounts then, usePoll stops in a hidden tab), from
// the read-only, master-or-developer GET /api/system/poll-cache.
import { usePoll } from "../api/hooks.js";
import { CardHeader, StateSwitch } from "./ui/CardHeader.jsx";

function PollCacheStats() {
  const { data } = usePoll("/api/system/poll-cache", 5000);
  if (!data) return null;
  return (
    <p className="muted small" style={{ margin: "8px 0 0" }}>
      Hits {data.hits} · misses {data.misses} · coalesced {data.coalesced}
      {" "}· entries {data.entries}
      {data.processes > 1 ? ` (${data.processes} servers)` : ""}
    </p>
  );
}

export default function PollCacheCard({ on, busy, onChange }) {
  return (
    <section className="rounded-xl border bg-card p-5">
      <CardHeader title="Shared poll cache (testing)"
                  subtitle={<>
                    Computes dashboard numbers once and shares them between open
                    pages. Every page still needs its own login and permissions.
                  </>}>
        <StateSwitch checked={!!on} disabled={busy} aria-label="Shared poll cache"
                     onCheckedChange={onChange} />
      </CardHeader>
      {on && <PollCacheStats />}
    </section>
  );
}
