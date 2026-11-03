// Why dataset capture cannot write - in the words of the actual problem.
//
// Both dataset pages used to say the same thing: "No NVMe drive mounted at
// /mnt/nvme". On the box that produced this component the drive WAS mounted,
// with 111 GB free; what was wrong is that a stock mount is root-owned and
// 0755, so the service (running as its own user) cannot create anything at the
// top of it. The message sent somebody to fix the one thing that was already
// right.
//
// So the server says which of the two failures it is (`reason`) and what it
// saw (`detail`), and this says what to do about that one.
//
// Tok Aba 2026-10-07: a customer never types sudo. A mounted drive whose
// folders cannot be written is repaired by the box itself (core/services/
// storage_job.py, through the root helper) BEFORE this card is drawn, so
// "Storage is not writable" means the repair genuinely failed: `detail` is
// the helper's reason ("mounted read-only", ...), and Try again asks for one
// more repair (core/routes/storage_repair.py). A drive that is not mounted
// at all is the hub's "New storage detected" popup, not a command here.
import { useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";

export default function NoStorage({ data, verb = "Capture is off" }) {
  const toast = useToast();
  const root = data?.root || "/mnt/nvme";
  const readonly = data?.reason === "readonly";
  const [busy, setBusy] = useState(false);
  // A Try again answer, shown until the page's own poll catches up.
  const [again, setAgain] = useState(null);
  const detail = again?.detail ?? data?.detail;

  async function tryAgain() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/dataset/repair", {});
      setAgain(d);
      if (d.ok) {
        toast.success("Storage fixed", "The drive's folders are ready; the page reloads in a moment.");
      } else {
        toast.error("Still not writable", d.detail || "The box could not fix the drive's folders.");
      }
    } catch (e) { toast.error("Could not try again", e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="panel-card" data-no-storage={readonly ? "readonly" : "unmounted"}>
      <div className="section-title">
        {readonly ? "Storage is not writable" : "No NVMe storage"}
      </div>
      <p className="muted small">
        {readonly
          ? <>The drive at <code>{root}</code> is mounted, but the box could
              not prepare its folders on it. {verb} - nothing would be saved.</>
          : <>No NVMe drive mounted at <code>{root}</code>. {verb} - the boot
              card wears out fast under training data.</>}
      </p>
      {/* What the box actually saw, in its own words: the reason the
          automatic repair gave up, not a command to type. */}
      {detail && <p className="muted small" data-no-storage-detail="1">{detail}</p>}
      {readonly ? (
        <button type="button" className="btn" disabled={busy} onClick={tryAgain}>
          {busy ? "Trying..." : "Try again"}
        </button>
      ) : (
        <p className="muted small">
          Plug in an NVMe drive: the box notices it and offers to set it up
          (the master account sees the question on the hub dashboard), then
          reload this page.
        </p>
      )}
    </div>
  );
}
