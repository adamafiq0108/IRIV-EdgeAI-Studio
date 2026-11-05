// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The Events Log's "Clear events" button, beside Export CSV / Export LOG.
//
// Drawn only for someone the server says may clear (`allowed` is the query
// answer's can_clear, the same rule the route enforces), as Logs.jsx draws
// Clear Log. The confirm text and the round trip are lib/eventsClear.js.
import { useState } from "react";
import { api } from "../api/client.js";
import { useConfirm } from "./ui/ask.jsx";
import { useToast } from "./ui/Toast.jsx";
import { clearEvents, nothingToClear } from "../lib/eventsClear.js";

export default function ClearEventsButton({ allowed, total, capped, dirty, onCleared }) {
  const confirm = useConfirm();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!allowed) return null;

  const empty = nothingToClear({ total, dirty });
  async function run() {
    setBusy(true);
    try {
      await clearEvents({ post: () => api.post("/api/events/clear", {}),
                          confirm, toast, total, capped, dirty, onCleared });
    } finally { setBusy(false); }
  }
  return (
    <button type="button" className="btn btn-sm btn-danger" onClick={run}
            disabled={busy || empty}
            title={empty ? "No events recorded, nothing to clear"
                         : "Delete every stored event of this system"}>
      {busy ? "Clearing…" : "Clear events"}
    </button>
  );
}
