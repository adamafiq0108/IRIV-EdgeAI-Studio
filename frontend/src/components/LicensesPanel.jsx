// LicensesPanel - issue and track store license keys (hub → Licenses,
// DEVELOPER only, and functional only on the owner's hub).
//
// A key is created when a customer pays: it names their device's serial,
// their tier, and an ABSOLUTE expiry counted from today - re-entering an old
// key later cannot restart the clock. The customer's read-only GitHub token
// rides inside the key, encrypted against the serial, so the customer pastes
// exactly one thing.
//
// Issuing needs config/store_signing.pem, which exists only on the owner's
// hub - on any other box this page says so and the endpoints refuse.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { Label } from "./ui/label.jsx";
import { Badge } from "./ui/badge.jsx";
import { Textarea } from "./ui/textarea.jsx";
import { SkelPage } from "./Loading.jsx";
import { KeyRound, Copy, ShieldOff } from "lucide-react";

function fmtDate(epoch) {
  if (!epoch) return "";
  try { return new Date(epoch * 1000).toLocaleDateString(); }
  catch { return ""; }
}

export default function LicensesPanel() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ customer: "", serial: "", tier: "pro",
                                     days: "365", github_token: "",
                                     atecc_serial: "", device_pubkey: "" });
  // The freshly issued key, shown ONCE - it is not stored anywhere readable,
  // so this screen is the only chance to copy it.
  const [issuedKey, setIssuedKey] = useState("");

  async function load() {
    try { setData(await api.get("/api/hub/store/licenses")); }
    catch (e) { toast.error("Could not load the license ledger", e.message); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  async function issue() {
    setBusy(true);
    try {
      const d = await api.post("/api/hub/store/licenses/issue", {
        ...form, days: parseInt(form.days, 10) || 0,
      });
      setIssuedKey(d.key || "");
      setForm({ customer: "", serial: "", tier: "pro", days: "365",
                github_token: "", atecc_serial: "", device_pubkey: "" });
      toast.success("Key issued", `Valid until ${fmtDate(d.expires_at)}. Copy it now - it is shown only once.`);
      load();
    } catch (e) {
      toast.error("The key could not be issued", e.message);
    } finally { setBusy(false); }
  }

  async function toggleRevoked(row) {
    try {
      await api.post("/api/hub/store/licenses/revoke", {
        serial: row.serial, issued_at: row.issued_at, revoked: !row.revoked,
      });
      load();
    } catch (e) {
      toast.error("Could not update the ledger", e.message);
    }
  }

  if (!data) return <SkelPage cards={2} rows={4} />;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      {!data.can_issue && (
        <div className="flex items-center gap-2 rounded-xl border bg-card px-4 py-3 text-sm text-muted-foreground">
          <ShieldOff className="size-4 shrink-0" />
          This hub has no signing key (config/store_signing.pem), so it cannot
          issue licenses. Issue them on the hub that holds the key.
        </div>
      )}

      {/* Issue a key - the "customer paid" workflow. */}
      <div className="rounded-xl border bg-card p-5">
        <div className="mb-3 flex items-center gap-2">
          <KeyRound className="size-4 text-muted-foreground" />
          <p className="text-sm font-semibold">Issue a key</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Customer">
            <Input value={form.customer} placeholder="Acme Sdn Bhd"
                   onChange={(e) => setForm({ ...form, customer: e.target.value })} />
          </Field>
          <Field label="Device serial"
                 hint="The Pi CPU serial, shown on the customer's hub (Performance page).">
            <Input value={form.serial} placeholder="10000000abcdef01"
                   className="font-mono"
                   onChange={(e) => setForm({ ...form, serial: e.target.value })} />
          </Field>
          <Field label="Tier">
            <select
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={form.tier}
              onChange={(e) => setForm({ ...form, tier: e.target.value })}>
              <option value="pro">pro</option>
              <option value="free">free</option>
            </select>
          </Field>
          <Field label="Duration (days)"
                 hint="Expiry counts from TODAY - the purchase - not from activation.">
            <Input type="number" min="1" max="3660" value={form.days}
                   onChange={(e) => setForm({ ...form, days: e.target.value })} />
          </Field>
          {/* THE SECURE ELEMENT. A CPU serial is what the box's operating
              system says it is, so a copied card can be made to say the same.
              These two bind the key to a chip instead: the serial names it,
              and the public key is what its signature is checked against, so
              the box has to PROVE it holds the private half rather than claim
              a number. Both optional -- a board with no chip still gets a key
              -- and they travel together, because a serial with no key to
              check is another number the software could be patched to report. */}
          <Field label="Secure element serial (optional)"
                 hint="18 hex digits, shown on the customer's hub beside the CPU serial. Leave blank for a board with no ATECC fitted.">
            <Input value={form.atecc_serial} placeholder="0123d140fd0b7bdbee"
                   className="font-mono" autoComplete="off"
                   onChange={(e) => setForm({ ...form, atecc_serial: e.target.value })} />
          </Field>
          <Field label="Device public key (optional)"
                 hint="128 hex digits, printed by provision_atecc.py when the board was built. With it, activation makes the chip sign a challenge; without it the key can only compare serials.">
            <Input value={form.device_pubkey} placeholder="04a1…"
                   className="font-mono" autoComplete="off"
                   onChange={(e) => setForm({ ...form, device_pubkey: e.target.value })} />
          </Field>
          <div className="sm:col-span-2">
            <Field label="Customer's GitHub token (read-only, store repo only)"
                   hint="Create a fine-grained PAT limited to the pro store repo. It travels inside the key, encrypted against the device serial.">
              <Input value={form.github_token} placeholder="github_pat_…"
                     className="font-mono" autoComplete="off"
                     onChange={(e) => setForm({ ...form, github_token: e.target.value })} />
            </Field>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button disabled={busy || !data.can_issue || !form.serial.trim()}
                  onClick={issue}>
            {busy ? "Issuing…" : "Issue key"}
          </Button>
        </div>

        {issuedKey && (
          <div className="mt-4 rounded-lg border bg-muted/40 p-3">
            <div className="mb-1 flex items-center justify-between">
              <p className="text-xs font-medium">
                The key - send it to the customer. It is shown once.
              </p>
              <Button size="sm" variant="outline"
                      onClick={() => {
                        navigator.clipboard?.writeText(issuedKey);
                        toast.info("Copied");
                      }}>
                <Copy className="size-3.5" /> Copy
              </Button>
            </div>
            <Textarea readOnly rows={3} value={issuedKey}
                      className="font-mono text-xs"
                      style={{ overflowWrap: "anywhere" }} />
          </div>
        )}
      </div>

      {/* The ledger. */}
      <div className="rounded-xl border bg-card p-5">
        <p className="mb-3 text-sm font-semibold">Issued keys</p>
        {(data.issued || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing issued yet.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="py-2 pr-3">Customer</th>
                  <th className="py-2 pr-3">Serial</th>
                  <th className="py-2 pr-3">Tier</th>
                  <th className="py-2 pr-3">Issued</th>
                  <th className="py-2 pr-3">Expires</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {data.issued.map((row, i) => {
                  const expired = row.expires_at * 1000 < Date.now();
                  return (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-2 pr-3">{row.customer || "-"}</td>
                      <td className="py-2 pr-3 font-mono text-xs">{row.serial}</td>
                      <td className="py-2 pr-3">{row.tier}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtDate(row.issued_at)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtDate(row.expires_at)}</td>
                      <td className="py-2 pr-3">
                        {row.revoked ? <Badge variant="secondary">revoked</Badge>
                          : expired ? <Badge variant="secondary">expired</Badge>
                                    : <Badge>active</Badge>}
                      </td>
                      <td className="py-2 text-right">
                        <Button size="sm" variant="ghost"
                                title={row.revoked
                                  ? "Mark as active again (bookkeeping)"
                                  : "Mark revoked. Keys verify offline - to truly cut access, rotate this customer's GitHub token."}
                                onClick={() => toggleRevoked(row)}>
                          {row.revoked ? "Unrevoke" : "Revoke"}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          Revoking here is bookkeeping - issued keys verify offline. To cut a
          customer's downloads immediately, rotate their GitHub token.
        </p>
      </div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
