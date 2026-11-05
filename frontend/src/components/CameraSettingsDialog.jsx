// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// One camera's own settings: brightness, exposure, white balance -- whatever
// THIS camera says it has.
//
// Nothing here is a fixed list of sliders. The server asks the driver what
// controls exist, with their real ranges, and this draws that answer. A cheap
// module offers brightness and nothing else; an Arducam offers eleven, three
// of which are locked while auto exposure owns them. Drawing a standard panel
// and hoping would mean sliders that move and change nothing, which is worse
// than no slider at all.
//
// Three states this has to tell apart, because they look identical if you
// only ever draw controls:
//
//   * the camera HAS controls          -> draw them
//   * the camera has none, and that is fine (an IP camera is adjusted in its
//     own web page)                    -> say where to go instead
//   * the camera cannot even open because its vendor SDK is missing
//                                      -> offer to install it
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { Button } from "./ui/button.jsx";
import { Switch } from "./ui/switch.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import { RotateCcw, Download } from "lucide-react";
import { camErrorToast } from "../lib/camErrors.js";

export default function CameraSettingsDialog({ open, onOpenChange, camera, editable }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [installing, setInstalling] = useState(false);
  const cameraId = camera?.id;

  const load = useCallback(() => {
    if (!open || !cameraId) return;
    setData(null);
    api.get(`/api/detector/control/cameras/controls?camera_id=${encodeURIComponent(cameraId)}`)
      .then(setData)
      .catch((e) => camErrorToast(toast, "Could not read this camera's settings", e));
  }, [open, cameraId]);
  useEffect(load, [load]);

  // The value a row SHOWS: what this system chose, else what the camera is
  // set to now. Those differ after a replug (the camera is back to its own
  // defaults) and the operator's choice is the one that means something.
  const shown = (c) => (c.chosen !== undefined && c.chosen !== null ? c.chosen : c.value);

  async function change(name, value) {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/cameras/controls",
                               { camera_id: cameraId, controls: { [name]: value } });
      setData(d);
      // The camera refusing a value is not a failure of the request -- the
      // setting is saved either way -- so it is said plainly rather than
      // thrown as an error over a panel that did what was asked.
      if (d.problem) toast.info("Saved, but not applied yet", d.problem);
    } catch (e) {
      camErrorToast(toast, "Could not change that setting", e);
      load();
    } finally { setBusy(false); }
  }

  async function resetOne(name) {
    // null means "forget what I chose", not "write the default down": the
    // camera's own default is allowed to change under us, and a stored copy
    // of today's default would quietly become a choice.
    setBusy(true);
    try {
      setData(await api.post("/api/detector/control/cameras/controls",
                             { camera_id: cameraId, controls: { [name]: null } }));
    } catch (e) {
      camErrorToast(toast, "Could not reset that setting", e);
    } finally { setBusy(false); }
  }

  async function installSdk() {
    setInstalling(true);
    try {
      const d = await api.post("/api/detector/control/cameras/sdk",
                               { camera_id: cameraId });
      toast.success("Installed", d.message);
      load();
    } catch (e) {
      camErrorToast(toast, "Could not install the camera's software", e);
    } finally { setInstalling(false); }
  }

  const controls = data?.controls || [];
  const groups = [...new Set(controls.map((c) => c.group || "Image"))];
  const sdk = data?.sdk;
  const needsSdk = sdk?.needed && !sdk?.installed;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Above the camera dialog this usually opens FROM. That one is the
          app's own .ui-dialog-backdrop at z-index 70, while a dialog here
          defaults to z-50 -- so this popup rendered underneath it and the
          backdrop swallowed every click: the panel appeared to open at the
          back and nothing on it could be touched. The backdrop is lifted too,
          or the shading stays under the camera dialog and this floats over an
          unshaded page. */}
      <DialogContent className="cy-app z-[80]" overlayClassName="z-[80]"
                     style={{ maxWidth: 560 }}>
        <DialogHeader>
          <DialogTitle>
            {camera?.name || camera?.label || "Camera"} settings
          </DialogTitle>
          <DialogDescription>
            Read from the camera itself, so only what it actually supports is
            offered. Changes are saved with the camera and applied again every
            time it opens.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 py-2" style={{ maxHeight: "60vh", overflowY: "auto" }}>
          {!data && <p className="muted small">Reading the camera…</p>}

          {/* The camera cannot open at all. Offered before the controls,
              because there is no point adjusting a camera that will not
              start -- and an empty control list here reads as "this camera
              has no settings", which is the wrong answer entirely. */}
          {needsSdk && (
            <div className="rounded-xl border p-4" style={{ borderColor: "var(--warn, #d97706)" }}>
              <strong>{sdk.label} is not installed</strong>
              <p className="muted small" style={{ margin: "4px 0 10px" }}>
                {sdk.why}
              </p>
              {/* Installing is the box owner's, while adjusting a camera is
                  the operator's -- so this asks the SERVER who the viewer is
                  rather than reusing `editable`. A button drawn for someone
                  the route will refuse is a support call, not a permission. */}
              <Button size="sm" disabled={installing || !sdk.may_install}
                      onClick={installSdk}
                      title={sdk.may_install ? ""
                        : "Installing software on this box is the owner's setting"}>
                <Download className="size-3.5" />
                {installing ? "Installing…" : `Install ${sdk.package} ${sdk.version}`}
              </Button>
              {!sdk.may_install && (
                <p className="muted small" style={{ margin: "6px 0 0" }}>
                  Ask whoever owns this box to install it.
                </p>
              )}
              {/* Which version, named on the button, because "install the
                  SDK" from a web page should say exactly what it is about to
                  put on the box. The list it comes from is compiled in; this
                  cannot be pointed somewhere else. */}
              <p className="muted small" style={{ margin: "8px 0 0" }}>
                Downloads the pinned version from PyPI. This box needs internet
                access for it; without one, install the wheel by hand.
              </p>
            </div>
          )}

          {data && !data.adjustable && !needsSdk && (
            <p className="muted small">{data.note}</p>
          )}

          {groups.map((group) => (
            <div key={group} className="grid gap-2">
              {groups.length > 1 && (
                <div className="section-title" style={{ marginTop: 4 }}>{group}</div>
              )}
              {controls.filter((c) => (c.group || "Image") === group).map((c) => {
                const value = shown(c);
                const locked = c.locked || c.read_only;
                const changed = c.chosen !== undefined && c.chosen !== null;
                return (
                  <div key={c.name} style={{ opacity: locked ? 0.55 : 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <label style={{ flex: 1, minWidth: 0, fontSize: 13 }}>
                        {c.label}
                        {changed && (
                          <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>
                            set by this system
                          </span>
                        )}
                      </label>

                      {c.type === "bool" && (
                        <Switch checked={!!value} disabled={!editable || busy || locked}
                                aria-label={c.label}
                                onCheckedChange={(on) => change(c.name, on ? 1 : 0)} />
                      )}

                      {c.type === "menu" && (
                        <select className="input" style={{ width: "auto" }}
                                value={value ?? ""} disabled={!editable || busy || locked}
                                onChange={(e) => change(c.name, Number(e.target.value))}>
                          {c.options.map((o) => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      )}

                      {c.type === "int" && (
                        <>
                          <input type="range" min={c.min} max={c.max} step={c.step || 1}
                                 value={value ?? c.default ?? c.min}
                                 disabled={!editable || busy || locked}
                                 style={{ flex: "0 0 150px" }}
                                 onChange={(e) => setData((d) => ({
                                   ...d,
                                   controls: d.controls.map((x) => x.name === c.name
                                     ? { ...x, chosen: Number(e.target.value) } : x),
                                 }))}
                                 // Dragging repaints locally; the save happens
                                 // when the operator lets go. Posting on every
                                 // pixel of a drag is a request per frame and a
                                 // config write per request.
                                 onMouseUp={(e) => change(c.name, Number(e.target.value))}
                                 onTouchEnd={(e) => change(c.name, Number(e.target.value))}
                                 onKeyUp={(e) => change(c.name, Number(e.target.value))} />
                          <span className="mono" style={{ fontSize: 11, width: 52,
                                                          textAlign: "right" }}>
                            {value ?? "-"}
                          </span>
                        </>
                      )}

                      {changed && !locked && (
                        <button className="icon-btn" title="Back to the camera's own default"
                                disabled={!editable || busy}
                                onClick={() => resetOne(c.name)}>
                          <RotateCcw size={13} />
                        </button>
                      )}
                    </div>
                    {/* A locked row says WHICH switch is holding it. "Inactive"
                        alone leaves an operator dragging a dead slider. */}
                    {locked && (
                      <p className="muted small" style={{ margin: "2px 0 0" }}>
                        {c.locked_by_label
                          ? `Turn ${c.locked_by_label} off to set this by hand.`
                          : "The camera is driving this one itself right now."}
                      </p>
                    )}
                    {!locked && c.hint && (
                      <p className="muted small" style={{ margin: "2px 0 0" }}>{c.hint}</p>
                    )}
                  </div>
                );
              })}
            </div>
          ))}

          {data?.adjustable && data?.note && (
            <p className="muted small">{data.note}</p>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
