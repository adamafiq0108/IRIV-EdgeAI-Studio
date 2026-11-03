// PanelSettings - the box's OLED, button and buzzer, with the screen on screen.
//
// The panel itself needs none of this: it runs headless, which is the point of
// a front panel. What this page adds is the one thing a headless daemon cannot
// give you - SEEING the glass while editing what it says. The alternative is
// walking to the box, changing a string, restarting, and walking back.
//
// The preview is not a mock-up. `rows` is the panel's own framebuffer, one
// character per pixel, drawn straight onto a canvas: whatever is wrong on the
// device is wrong here too, which is the only kind of preview worth having.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Skeleton } from "./ui/skeleton.jsx";
import SettingsPage from "./ui/SettingsPage.jsx";
import { Switch } from "./ui/switch.jsx";
import { CardHeader, StateSwitch } from "./ui/CardHeader.jsx";
import {
  Bell, Loader2, MonitorSmartphone, RefreshCw, RotateCw, TriangleAlert,
} from "lucide-react";

//: What each editable line is, in words rather than in its config key.
const LINE_LABEL = {
  about_title: "Headline",
  about_model: "Model line",
  about_accel: "Accelerator line",
  wifi_title: "Title",
  wifi_step: "Instruction",
  wifi_password: "Password line",
  url_title: "Title",
  url_step: "Instruction",
  ip_title: "Title",
  ip_eth0: "First interface",
  ip_eth1: "Second interface",
  ip_wlan0: "Wireless",
  status_title: "Title",
  status_load: "Load and temperature",
  status_ram: "Memory",
  status_disk: "Disk",
};

const PAGE_LABEL = {
  about: "Identity",
  wifi: "Wi-Fi access point",
  url: "Dashboard address",
  ip: "IP addresses",
  status: "CM5 status",
};

/** The framebuffer, painted at 3x so the pixels read as pixels. */
function Screen({ frame, scale = 3 }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !frame?.rows?.length) return;
    const w = frame.width || 128;
    const h = frame.height || 64;
    canvas.width = w * scale;
    canvas.height = h * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // The panel's own blue-white, not pure white: an OLED at 3x with full
    // white pixels reads as a spreadsheet, not a screen.
    ctx.fillStyle = "#d7f2ff";
    frame.rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x += 1) {
        if (row[x] === "1") ctx.fillRect(x * scale, y * scale, scale, scale);
      }
    });
  }, [frame, scale]);
  return (
    <canvas ref={ref}
            style={{ display: "block", borderRadius: 6, imageRendering: "pixelated",
                     background: "#000", maxWidth: "100%",
                     border: "1px solid var(--line, rgba(128,128,128,.3))" }} />
  );
}

// One editable line, plus the way back to what it said out of the box.
function TextLine({ name, value, fallback, onChange, onFocus }) {
  return (
    <label>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div className="muted small" style={{ flex: 1 }}>
          {LINE_LABEL[name] || name}
        </div>
        {value !== fallback && (
          <button type="button" className="btn btn-sm"
                  style={{ padding: "0 6px", fontSize: 11 }}
                  title={`Back to "${fallback}"`}
                  onClick={() => onChange(fallback)}>reset</button>
        )}
      </div>
      <input className="input" value={value} placeholder={fallback}
             data-line={name}
             onFocus={(e) => onFocus?.(name, e.target)}
             onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export default function PanelSettings() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [page, setPage] = useState("about");
  const [frame, setFrame] = useState(null);
  const [busy, setBusy] = useState("");
  const focused = useRef(null);     // {key, el} - where a placeholder lands
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/hub/panel");
      setData(d);
      setForm((f) => f || { ...d.settings });
      if (d.frame) setFrame(d.frame);
      return d;
    } catch (e) {
      toast.error("Could not read the front panel", e.message);
      setData({ settings: null });
      return null;
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  // Re-render the chosen page from the UNSAVED form, so the preview answers
  // "what would this look like" rather than "what does it look like now".
  // Debounced: the text fields fire per keystroke and each render reads the
  // box's live facts.
  useEffect(() => {
    if (!form) return undefined;
    let dead = false;
    const t = setTimeout(() => {
      api.post("/api/hub/panel/preview", { page, settings: form })
        .then((d) => { if (!dead) setFrame(d); })
        .catch(() => {});
    }, 250);
    return () => { dead = true; clearTimeout(t); };
  }, [form, page]);

  if (!data || !form) return <Skeleton className="h-64 w-full" />;

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // A placeholder button drops its field where the cursor is, in whichever
  // line was last edited -- the same contract the Workflow's message nodes
  // keep. Falling back to the page's first line rather than doing nothing:
  // clicking a chip and having nothing happen reads as a broken button.
  function insertPlaceholder(name) {
    const token = `{${name}}`;
    const lines = data.text_by_page?.[page] || [];
    const target = focused.current;
    const key = (target && lines.includes(target.key)) ? target.key : lines[0];
    if (!key) return;
    const el = target && target.key === key ? target.el : null;
    const current = form.text?.[key] ?? "";
    const at = el ? (el.selectionStart ?? current.length) : current.length;
    const next = current.slice(0, at) + token + current.slice(at);
    set({ text: { ...(form.text || {}), [key]: next } });
    if (el) {
      // After React has written the new value, or the caret jumps to the end.
      requestAnimationFrame(() => {
        el.focus();
        try { el.setSelectionRange(at + token.length, at + token.length); }
        catch { /* the input may have unmounted */ }
      });
    }
  }
  const nodered = data.nodered || {};
  const conflict = form.enabled && nodered.active;

  async function save(patch) {
    // `patch` lets a control apply ITSELF without the Save button. The master
    // switch uses it: a power switch that needs a second click somewhere else
    // to take effect reads as broken, which is exactly how it read -- the
    // toggle moved, the sentence under it still said "Off", and both were
    // telling the truth about different things.
    // A PLAIN OBJECT or nothing. Passed straight to onClick, `save` receives
    // React's click event, the event goes into the request body, and
    // JSON.stringify dies on a DOM node with a circular reference back to
    // itself -- which is what "Converting circular structure to JSON" was.
    // The call sites are correct now; this is what keeps a future one from
    // failing that way instead of loudly.
    const plain = patch && typeof patch === "object" && !patch.nativeEvent
                  && !(patch instanceof Event);
    const next = plain ? { ...form, ...patch } : form;
    setBusy(plain ? "enabled" : "save");
    try {
      const d = await api.post("/api/hub/panel/settings", next);
      setData(d);
      setForm({ ...d.settings });
      toast.success("Front panel saved",
                    d.settings.enabled
                      ? (d.screen ? "The screen is showing it now."
                                  : `Saved, but the screen did not open: ${d.error || "not found"}`)
                      : "The panel is off and has let go of the screen.");
    } catch (e) {
      toast.error("Could not save", e.message);
    } finally { setBusy(""); }
  }

  return (
    <SettingsPage
      title="Front panel" icon={MonitorSmartphone}
      description="The board's OLED, the USER button and the buzzer - what they
                   show, and what they do."
      actions={
        <button className="btn btn-sm" disabled={!!busy} onClick={load}>
          <RefreshCw size={14} /> Refresh
        </button>
      }>

      {!data.detected && (
        <p className="muted small">
          No IRIV board detected on this box. The pages below still render, so
          they can be written and checked from here, but nothing is driving a
          screen on this machine.
        </p>
      )}

      {/* The conflict is the first thing on the page when it is real: two
          programs writing opposite pixels down one I2C bus is not a subtle
          failure, but it is a confusing one if nobody says why. */}
      {conflict && (
        <div role="alert"
             style={{ display: "flex", gap: 10, alignItems: "flex-start",
                      border: "1px solid color-mix(in srgb, var(--warn, #d97706) 45%, transparent)",
                      background: "color-mix(in srgb, var(--warn, #d97706) 10%, transparent)",
                      borderRadius: 10, padding: "10px 12px" }}>
          <TriangleAlert size={16} style={{ color: "var(--warn, #d97706)", flex: "none", marginTop: 2 }} />
          <div style={{ minWidth: 0 }}>
            <strong style={{ fontSize: 13 }}>Node-RED is also driving this screen</strong>
            <div className="muted small">
              The board's factory flow owns the same OLED, button and buzzer.
              While both run they fight over the bus and the display flickers
              between two sets of pages. Stop Node-RED on the <b>Board I/O</b>
              {" "}page, or leave this panel off.
            </div>
          </div>
        </div>
      )}

      <div className="panel-card">
        {/* The card's on/off at the top right with its On / Off label
            left of it (ui/CardHeader.jsx, 2026-10-14). */}
        <CardHeader title="Front panel"
                    subtitle={data.running
                      ? (data.screen ? "Running - the screen is open."
                                     : `Running, but the screen did not open: ${data.error || "not found"}`)
                      : "Off - nothing here is touching the hardware."}>
          {busy === "enabled" && <Loader2 size={14} className="animate-spin" />}
          <StateSwitch checked={!!form.enabled} aria-label="Front panel"
                       disabled={busy === "enabled"}
                       onCheckedChange={(on) => { set({ enabled: on }); save({ enabled: on }); }} />
        </CardHeader>

        {/* THREE pieces of hardware, three answers. The screen can be working
            while the button does nothing, and that is not a paradox: I2C has
            no exclusive claim, so two programs can both draw; a GPIO line has
            exactly one owner, so the second program to ask gets nothing. That
            asymmetry is the whole explanation for "the pages are there but
            the button is dead", and it is worth three rows to say it. */}
        {data.running && (
          <div style={{ display: "grid", gap: 4, marginTop: 10, paddingTop: 10,
                        borderTop: "1px solid var(--line, rgba(128,128,128,.22))" }}>
            {[["Screen", data.screen, data.error],
              ["USER button", data.button, data.button_error],
              ["Buzzer", data.buzzer, data.buzzer_error]].map(([label, ok, err]) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 8,
                                        fontSize: 12 }}>
                <span style={{ width: 7, height: 7, borderRadius: 999, flex: "none",
                               background: ok ? "var(--ok, #16a34a)"
                                              : "var(--warn, #d97706)" }} />
                <span style={{ width: 96, flex: "none" }}>{label}</span>
                <span className="muted" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                  {ok ? "ready" : (err || "not available")}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ---- the screen, and the page being edited ---- */}
      <div className="panel-card">
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <div style={{ flex: "0 0 auto" }}>
            <Screen frame={frame} />
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <button className="btn btn-sm" disabled={!data.running || !!busy}
                      title="Step the real screen to its next page"
                      onClick={async () => {
                        setBusy("page");
                        try {
                          const d = await api.post("/api/hub/panel/page", { next: true });
                          if (d.frame) { setFrame(d.frame); setPage(d.frame.page); }
                        } catch (e) { toast.error("Could not step the panel", e.message); }
                        finally { setBusy(""); }
                      }}>
                <RotateCw size={14} /> Next on device
              </button>
              <button className="btn btn-sm" disabled={!data.running || !!busy}
                      title="Sound the buzzer, to prove it"
                      onClick={() => api.post("/api/hub/panel/beep", { ms: 60 })
                        .catch((e) => toast.error("Could not beep", e.message))}>
                <Bell size={14} /> Beep
              </button>
            </div>
          </div>

          <div style={{ flex: "1 1 260px", minWidth: 240 }}>
            <div className="muted small" style={{ fontWeight: 600 }}>Pages</div>
            <p className="muted small" style={{ marginTop: 2 }}>
              The USER button walks these in order. Untick one to drop it from
              the rotation; click a name to preview it.
            </p>
            <div style={{ display: "grid", gap: 4, marginTop: 8 }}>
              {(data.page_ids || []).map((id) => {
                const on = (form.pages || []).includes(id);
                return (
                  <div key={id}
                       style={{ display: "flex", alignItems: "center", gap: 8,
                                border: "1px solid var(--line, rgba(128,128,128,.22))",
                                borderRadius: 8, padding: "6px 9px",
                                background: page === id
                                  ? "color-mix(in srgb, var(--brand-accent, #04bade) 12%, transparent)"
                                  : "transparent" }}>
                    <input type="checkbox" checked={on}
                           title={on ? "Shown on the panel" : "Not in the rotation"}
                           onChange={(e) => {
                             const next = e.target.checked
                               ? [...(form.pages || []), id]
                                   .sort((a, b) => data.page_ids.indexOf(a)
                                                 - data.page_ids.indexOf(b))
                               : (form.pages || []).filter((p) => p !== id);
                             // Never all-off: a panel with no pages is a
                             // panel that looks broken, and the server would
                             // refuse the empty list anyway.
                             if (next.length) set({ pages: next });
                           }} />
                    <button type="button" className="btn btn-sm"
                            style={{ flex: 1, justifyContent: "flex-start",
                                     border: "none", background: "none",
                                     padding: 0, textAlign: "left" }}
                            onClick={() => setPage(id)}>
                      {PAGE_LABEL[id] || id}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* ---- what the pages say ---- */}
      <div className="panel-card">
        <div className="section-title">
          Wording - {PAGE_LABEL[page] || page}
        </div>
        <p className="muted small" style={{ marginTop: -4 }}>
          Every line on the panel is yours to write. A name in braces is
          replaced by the box's own fact when it draws - click one to insert
          it where the cursor is.
        </p>

        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
          {(data.text_by_page?.[page] || []).map((key) => (
            <TextLine key={key} name={key} value={form.text?.[key] ?? ""}
                      fallback={data.default_text?.[key] || ""}
                      onFocus={(k, el) => { focused.current = { key: k, el }; }}
                      onChange={(v) => set({ text: { ...(form.text || {}), [key]: v } })} />
          ))}

          {page === "url" && (
            <label>
              <div className="muted small">Address to show</div>
              {/* Blank = iriv.local on the software's port (Settings ->
                  Network -> Software port); this page is served by the hub,
                  so the port in the address bar IS that port. */}
              <input className="input" value={form.dashboard_url || ""}
                     placeholder={`iriv.local${window.location.port ? `:${window.location.port}` : ""}`}
                     onChange={(e) => set({ dashboard_url: e.target.value })} />
            </label>
          )}
          {page === "wifi" && (
            // No field for the key: the panel has no copy of its own. The
            // <code>{"{ap_password}"}</code> line prints the hotspot key from
            // Settings -> Network, so a re-key there shows here at once.
            <div className="muted small">
              The <code>{"{ap_password}"}</code> field prints the access point's
              password set on Settings -&gt; Network.
            </div>
          )}

          <label>
            <div className="muted small">Footer line, under the rule (every page)</div>
            <input className="input" value={form.hint || ""}
                   onChange={(e) => set({ hint: e.target.value })} />
          </label>

          <div>
            <div className="muted small" style={{ marginBottom: 4 }}>
              Facts you can drop in
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {(data.placeholders || []).map((p) => (
                <button key={p} type="button" className="btn btn-sm"
                        style={{ padding: "1px 7px", fontSize: 11 }}
                        title={`Insert {${p}} - currently "${data.facts?.[p] ?? ""}"`}
                        onClick={() => insertPlaceholder(p)}>
                  {`{${p}}`}
                </button>
              ))}
            </div>
          </div>

          <p className="muted small" style={{ margin: 0 }}>
            A small row holds 21 characters and the large ones on the identity
            page hold six - a longer value there is drawn small rather than
            over its neighbour. The preview shows exactly what happens.
          </p>
        </div>
      </div>

      {/* ---- what the panel responds to ---- */}
      <div className="panel-card">
        <div className="section-title">Controls</div>
        {[
          ["button", "USER button steps the pages",
           "Off leaves the pages showing but stops them cycling."],
          ["buzzer", "Buzzer",
           "A short beep on every press, a longer one when the long press acts."],
          // The key keeps its old name (hold_shutdown) so a deployment's saved
          // choice still applies; what the hold DOES changed on 2026-09-14.
          ["hold_shutdown", "Hold USER for 5 seconds to reset the master password",
           "Mails the reset link to the master account's email and says so on "
           + "the screen. Power off and reboot are in the sign-out menu."],
        ].map(([key, label, hint]) => (
          <div key={key}
               style={{ display: "flex", alignItems: "center", gap: 12,
                        padding: "10px 0",
                        borderBottom: "1px solid var(--line, rgba(128,128,128,.22))" }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              {label}
              <div className="muted small">{hint}</div>
            </div>
            <Switch checked={!!form[key]} aria-label={label}
                    onCheckedChange={(on) => set({ [key]: on })} />
          </div>
        ))}

        {/* The power buttons used to sit here. They are in the sign-out
            menu on every page already; twice on the box is once too many. */}
        {data.ap_name && (
          <div className="muted small" style={{ marginTop: 12 }}>
            Access point name: <b>{data.ap_name}</b>
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ flex: 1 }} />
        <button className="btn btn-sm"
                disabled={!!busy}
                onClick={() => setForm({ ...data.settings })}>
          Discard
        </button>
        {/* `onClick={save}` handed React's click event in as the patch, which
            then went into the request body -- a DOM node with a circular
            reference back to itself, so the save died in JSON.stringify
            before it ever reached the box. Arrow, so Save saves the form. */}
        <button className="btn btn-sm btn-accent" disabled={!!busy}
                onClick={() => save()}>
          {busy === "save" ? <Loader2 size={14} className="animate-spin" /> : null}
          Save
        </button>
      </div>
    </SettingsPage>
  );
}
