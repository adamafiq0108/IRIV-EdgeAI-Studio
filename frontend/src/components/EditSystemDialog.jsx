// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Edit system: the hub card's pencil (Tok Aba, 2026-10-06). Was "Rename
// system", one Name field; the Name field is unchanged, and the system's PORT
// -- the address on the card -- sits under it.
//
// The port shows what the system has now. Typing another one (or pressing
// Auto, the next free port) checks it against the box as it is typed, and
// says before Save what saving does: the system restarts on the new port and
// anything pointing at the old address has to follow. Only the master's port
// moves; the video stream keeps its own (the app serves it to the master on
// this box). A system systemd runs (irivai-master@<slug>) or one on another
// host shows its port read-only with the reason -- the server refuses it too.
//
// Leaving the port alone sends no port: Save is exactly the old rename.
//
// THE HUB DECIDES the address (Tok Aba, 2026-10-07; boxAccess.jsx): with the
// Hub on the proxy this system "uses the proxy, like the Hub" at
// http://<box>/s/<slug>/ and its port is the internal one the hub relays to,
// behind Advanced; with the Hub on ports, the port field as before and a
// note pointing at the Software port card. No switch here.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { Label } from "./ui/label.jsx";
import { useToast } from "./ui/Toast.jsx";
import { usePortCheck } from "./usePortCheck.js";
import { HubUsesPorts, STREAM_PROXY, proxyAddress, useHubProxy } from "./boxAccess.jsx";

// Above every modal layer, like FormDialog (ui/dialogs.jsx says why).
const LAYER = "z-[1100]";

// "192.168.1.21:5112" out of the card's browser-facing url.
function hostPort(url) {
  try { return new URL(url).host; } catch { return ""; }
}

export default function EditSystemDialog({ card, onOpenChange, onSaved }) {
  const open = !!card;
  const slug = card?.slug || "";
  const toast = useToast();
  const [name, setName] = useState("");
  const [info, setInfo] = useState(null);
  const [port, setPort] = useState("");
  const [busy, setBusy] = useState(false);
  const proxyMode = useHubProxy(open);
  const proxy = proxyMode === true;
  const modeKnown = proxyMode !== null;     // null = hub not answered yet
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => { if (open) setAdvanced(false); }, [open]);
  const showPort = modeKnown && (!proxy || advanced);

  // Seeded each time it opens, like FormDialog: a cancel leaves no residue.
  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    setName(card?.name || "");
    setInfo(null);
    setPort("");
    api.get(`/api/hub/systems/${encodeURIComponent(slug)}/port`)
      .then((d) => { if (live) { setInfo(d); setPort(d?.port ? String(d.port) : ""); } })
      .catch((e) => { if (live) setInfo({ editable: false, reason: e?.message || "" }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, slug]);

  const current = info?.port ? String(info.port) : "";
  const typed = port.trim();
  const changed = !!info?.editable && !!typed && typed !== current;
  const { suggested, verdict } = usePortCheck({ port: changed ? typed : "", slug,
                                                enabled: open && !!info?.editable });
  const portOk = !changed || (verdict && verdict.usable);
  const oldAddr = hostPort(card?.url || "") || (current ? `port ${current}` : "");

  async function save(e) {
    e.preventDefault();
    if (busy || !portOk) return;
    setBusy(true);
    // Blank name is meaningful (restore the package's name), so it is sent.
    const body = { name: name.trim() };
    if (changed) body.port = Number(typed);
    try {
      const d = await api.patch(`/api/hub/systems/${encodeURIComponent(slug)}`, body);
      if (d.moved && d.restarted) {
        toast.success(`${d.name} moved to port ${d.port}`,
                      d.url ? `It restarted and answers at ${d.url}.` : undefined);
      } else if (d.moved) {
        toast.success(`${d.name} set to port ${d.port}`,
                      "It was not running, so it starts on the new port next time.");
      } else {
        toast.success(`Renamed to ${d.name}`);
      }
      onSaved?.(d);
      onOpenChange(false);
    } catch (err) {
      toast.error("Could not save the system", err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!busy) onOpenChange(o); }}>
      <DialogContent className={`cy-app ${LAYER}`} overlayClassName={LAYER}>
        <form onSubmit={save}>
          <DialogHeader>
            <DialogTitle>Edit system</DialogTitle>
            <DialogDescription>
              {`The id ${slug} stays as it is, so saved settings keep working.`}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 py-3">
            <div className="grid gap-1.5">
              <Label htmlFor="es-name">Name</Label>
              <Input id="es-name" value={name} autoFocus autoComplete="off"
                     placeholder={card?.name || ""}
                     onChange={(e) => setName(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                Up to 60 characters. Leave blank to restore the name the system package declares.
              </p>
            </div>

            {modeKnown && proxy && (
              <div className="grid gap-1.5">
                <Label>Address</Label>
                <p className="text-xs text-muted-foreground" data-proxy-address>
                  {"Uses the proxy, like the Hub: "}<code>{proxyAddress(slug)}</code>.{" "}
                  <button type="button" className="underline" data-advanced
                          onClick={() => setAdvanced((a) => !a)}>
                    {advanced ? "Hide internal port" : "Advanced: internal port"}
                  </button>
                </p>
                <p className="text-xs text-muted-foreground" data-stream-proxy>{STREAM_PROXY}</p>
              </div>
            )}

            {showPort && (
            <div className="grid gap-1.5">
              <Label htmlFor="es-port">{proxy ? "Internal port" : "Port"}</Label>
              <div className="flex items-center gap-2">
                <Input id="es-port" className="w-28" inputMode="numeric" autoComplete="off"
                       value={port} disabled={!info?.editable || busy}
                       placeholder={info ? current : "…"}
                       aria-label="Port"
                       onChange={(e) => setPort(e.target.value.replace(/[^\d]/g, ""))} />
                {info?.editable && (
                  <Button type="button" size="sm" variant="outline" disabled={!suggested || busy}
                          title="Pick the next free port"
                          onClick={() => setPort(String(suggested))}>
                    Auto
                  </Button>
                )}
                {info?.editable && suggested ? (
                  <span className="text-xs text-muted-foreground">Suggested: {suggested}</span>
                ) : null}
              </div>
              {info && !info.editable ? (
                <p className="text-xs text-muted-foreground" data-port-reason>
                  {`The port cannot be changed here: ${info.reason}.`}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {proxy ? "The port the proxy relays this system to" : "The port the system's pages answer on"}
                  {current ? `, now ${current}` : ""}.
                  {info?.stream_port && !proxy ? ` Its video stream keeps port ${info.stream_port}.` : ""}
                </p>
              )}
              {changed && verdict && !verdict.usable ? (
                <p className="text-xs text-amber-600 dark:text-amber-500" data-port-verdict>
                  {verdict.detail}
                </p>
              ) : null}
              {changed && verdict?.usable ? (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs" data-port-move>
                  {proxy
                    ? `Saving moves ${slug} to internal port ${typed} and restarts it; its address stays ${proxyAddress(slug)}.`
                    : `Saving moves ${slug} to port ${typed} and restarts it; anything that points at ${oldAddr} must be changed.`}
                </p>
              ) : null}
              {!proxy && info?.editable ? <HubUsesPorts onGo={() => onOpenChange(false)} /> : null}
            </div>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy}
                    onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !portOk || (changed && !verdict)}>
              {busy ? (changed ? "Moving…" : "Working…") : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
