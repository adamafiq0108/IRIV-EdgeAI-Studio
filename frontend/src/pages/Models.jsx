// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// AI Models - bring your own model instead of the built-in ones.
//
// Upload a compiled Hailo .hef and describe it in a form.
// Only HEFs are accepted: this platform runs models on the accelerator, and
// ONNX/TFLite/Torch weights would need a compile step the board cannot do --
// accepting them only moved the failure to load time. The form IS the
// manifest: every field maps 1:1 to a key in
// <config_dir>/models/<name>.json, and the JSON is shown alongside so the same
// thing can be committed to a deployment repo later.
//
// The fields are the ones a detector cannot guess - input size the model was
// compiled for, whether NMS ran on-chip, which Hailo architecture the HEF
// targets. Guessing any of them yields a model that loads and returns nonsense.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, ChevronsDown, ChevronsUp, RefreshCw } from "lucide-react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { Badge, Dialog, EmptyState, Field } from "../components/ui/primitives.jsx";
import { InfoHint } from "../components/ui/InfoHint.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import CollapsibleCard from "../components/ui/CollapsibleCard.jsx";
import { HoldToDelete } from "../components/ui/HoldToDelete.jsx";
import TutorialNav from "../components/TutorialNav.jsx";
import PageGuideHost from "../workflow/PageGuides.jsx";
import NextPageButton from "../components/NextPageButton.jsx";
import { authHeaders } from "../lib/session.js";
import { apiUrl } from "../lib/base.js";

const BLANK = {
  name: "", task: "detection", arch: "", postprocess: "nms_on_chip",
  input: { width: 640, height: 640 }, confidence: 0.45, classes: [], description: "",
};

export default function Models() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(BLANK);
  const [labelsText, setLabelsText] = useState("");
  // The class list's own search box (this form's; DetectorParams has its own).
  const [formClassQuery, setFormClassQuery] = useState("");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [selecting, setSelecting] = useState(null);   // model name in flight
  // What the detector is set to right now. Same source the Custom Model list
  // uses, so the two cards can never both claim to hold the running model.
  const [params, setParams] = useState(null);
  // The package's BUILT-IN models (/api/detector/builtin): the catalogue HEFs
  // on this box and which one the app is set to run. Never fetched before
  // UAT-101, so a new system running yolov8s out of the box showed "Nothing
  // saved yet" on the very page the setup tour sends it to.
  const [builtin, setBuiltin] = useState(null);
  // The settings editor is a DIALOG now, opened by clicking a model (or "Add
  // custom model"). The card itself is a list: on a page where the list is the
  // thing you scan, a permanently-open 20-field form is what pushes it off
  // the screen. null = closed; "" = a new model; a name = editing that one.
  const [editing, setEditing] = useState(null);
  // Which cards are unfolded. Every card opens FOLDED (name and chips only):
  // a page of five models was five sets of buttons before anyone had picked
  // one. The chevrons at each card's foot open it.
  const [expanded, setExpanded] = useState({});
  // Custom models are paged, PAGE_SIZE at a time -- a board with twenty
  // uploaded models turned this card into the whole page.
  const [page, setPage] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  // Landing in a 15-field form with no cue where to start was the actual
  // complaint: the preset picker (the fast path -- pick one, everything else
  // fills in) sat above the form looking like any other field. A fresh "+ Add
  // a model" now focuses it and pulses its border, so the eye goes there
  // first instead of to the Name box at the top of the grid.
  const presetRef = useRef(null);
  const [presetPulse, setPresetPulse] = useState(false);
  useEffect(() => {
    // Only a brand-new model, not editing a saved one -- an existing model
    // already has its fields filled in, so "start here" would point at the
    // one control least relevant to what the operator actually opened it for.
    if (editing !== "") return;
    const t = setTimeout(() => {
      presetRef.current?.focus();
      setPresetPulse(true);
    }, 50);                      // after the swapped-in form has painted
    const clear = setTimeout(() => setPresetPulse(false), 2000);
    return () => { clearTimeout(t); clearTimeout(clear); };
  }, [editing]);

  // Three at a time in the Default Model card: it shares its row with the
  // custom card, and a fourth row is what starts pushing the page long.
  const PAGE_SIZE = 3;

  const load = useCallback(() => {
    api.get("/api/models").then((d) => { setData(d); setError(null); }).catch(setError);
    api.get("/api/detector/control/params").then(setParams).catch(() => setParams(null));
    api.get("/api/detector/builtin").then(setBuiltin).catch(() => setBuiltin(null));
  }, []);
  useEffect(load, [load]);
  // The params alone, on a timer: `classes_locked` follows AI detection, so
  // the class ticks grey out while it runs and come back on the first read
  // after it has fully stopped, with no page refresh. The forms below keep
  // an edit in progress (they follow the server only while not dirty).
  useVisibleInterval(() => {
    api.get("/api/detector/control/params").then(setParams).catch(() => {});
  }, 4000);

  /** Run a saved manifest's model on this board - alone. */
  async function useModel(name) {
    setSelecting(name);
    try {
      const d = await api.post("/api/detector/builtin/select", { name });
      // A filter kept from the previous model would have silenced this one
      // completely, so it is pruned server-side - say which names went, or the
      // workflow rules bound to them look fine while matching nothing.
      const gone = d.dropped_classes || [];
      toast.success(`Now running ${name}`, gone.length
        ? `Class filter cleaned up: ${gone.join(", ")} ${gone.length === 1 ? "is" : "are"}`
          + " not in this model - re-pick classes on any workflow node that used them."
        : "The detection app switches within a few seconds.");
      load();
    } catch (e) {
      toast.error("Could not switch model", e.message);
    } finally { setSelecting(null); }
  }

  // Which models are running right now. The multi list is the truth when
  // present; otherwise the single selection. Names here are hef basenames -
  // the same identity the engine tags detections with.
  const activeSet = (params?.models || []).length
    ? (params.models || []).map((x) => x.model)
    : (params?.model ? [params.model] : []);

  // Paging over the saved custom models. Clamped rather than trusted: deleting
  // the last model on the last page would otherwise leave `page` pointing past
  // the end and render an empty card with no way back.
  const allModels = data?.models || [];
  const total = allModels.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageStart = Math.min(page, pages - 1) * PAGE_SIZE;
  const pageItems = allModels.slice(pageStart, pageStart + PAGE_SIZE);

  // Built-in rows: the catalogue models whose .hef is on this box, plus the
  // one the app is set to run even if its file has gone (so the page never
  // hides what is selected). Uploaded entries in the same listing are the
  // saved models below, so they are not drawn twice.
  const builtinRows = (builtin?.models || []).filter((b) => b.source === "builtin"
    && (b.downloaded || b.current || activeSet.includes(b.name)));
  // A saved manifest named after a built-in (the "+ Add a model" preset path
  // writes yolov8s.json) runs the built-in's .hef: the catalogue shadows an
  // upload of the same name in select(). Its weights are on the box even when
  // the upload folder has no copy, so it must not read "Needs its .hef".
  const builtinOnBox = new Set(builtinRows.filter((b) => b.downloaded).map((b) => b.name));

  /** Open the settings dialog for one saved model, seeded from its manifest. */
  async function openSettings(m) {
    setForm({ ...BLANK, ...m });
    setFile(null);
    // The class checklist is drawn FROM the labels box, so an empty box hides
    // every saved highlight. Load the manifest's own labels file back into it;
    // no labels saved = box empty.
    try {
      const res = await fetch(
        apiUrl(`/api/models/${encodeURIComponent(m.name)}/file?what=labels`),
        { credentials: "same-origin", headers: { ...authHeaders() } });
      setLabelsText(res.ok ? await res.text() : "");
    } catch {
      setLabelsText("");
    }
    setEditing(m.name);
  }

  /** Add/remove a model from the running set (they share the one camera). */
  async function toggleEnable(name) {
    const next = activeSet.includes(name)
      ? activeSet.filter((n) => n !== name)
      : [...activeSet, name];
    // Emptying the set is allowed, and refused by the SERVER while detection
    // is live -- it is the only party that can honestly answer whether it is.
    // The browser used to refuse it outright, which meant the last model
    // could never be switched off at all, not even on a stopped system.
    setSelecting(name);
    try {
      const d = await api.post("/api/detector/builtin/select_many", { names: next });
      const gone = d.dropped_classes || [];
      if (!next.length) {
        toast.success("No model is running on this system",
                      "It detects nothing until you switch one on.");
      } else {
        toast.success(
          (d.models || next).length > 1
            ? `Running ${(d.models || next).length} models together`
            : `Now running ${next[0]}`,
          gone.length
            ? `Class filter cleaned up: ${gone.join(", ")} not emitted by these models.`
            : "All models share the camera; each detection is tagged with the model that produced it.");
      }
      load();
    } catch (e) {
      // The server refuses an empty set while detection is live, and its
      // message says what to do about it -- passed through rather than
      // buried under a generic one.
      toast.error(next.length ? "Could not change the running models"
                              : "Cannot switch off the last model", e.message);
    } finally { setSelecting(null); }
  }

  // Default the architecture to the accelerator actually plugged into this box,
  // so a freshly-loaded form (and every preset) is already correct and Save
  // never trips the "arch must be one of…" validation. Only fills a blank arch,
  // so it never overrides a choice the operator already made.
  const detectedArch = data?.vocab?.detected_arch || "";
  useEffect(() => {
    if (detectedArch) setForm((f) => (f.arch ? f : { ...f, arch: detectedArch }));
  }, [detectedArch]);

  const manifest = useMemo(() => ({
    ...form,
    classes: Array.isArray(form.classes) ? form.classes
      : String(form.classes || "").split(",").map((s) => s.trim()).filter(Boolean),
  }), [form]);

  function set(field, value) { setForm((f) => ({ ...f, [field]: value })); }
  function setInput(dim, value) {
    setForm((f) => ({ ...f, input: { ...f.input, [dim]: Number(value) || 0 } }));
  }

  // Load a one-click detection preset: the form and the labels fill from a
  // known-good Model Zoo manifest, so all that's left is the arch and the .hef.
  async function loadPreset(name) {
    if (!name) return;
    try {
      const { preset } = await api.get(`/api/models/presets/${name}`);
      const { labels_text, ...manifestFields } = preset;
      // The preset leaves arch blank (a HEF is arch-specific); fill it with the
      // accelerator detected on this box so Save works with no manual step.
      const arch = manifestFields.arch || detectedArch;
      setForm((f) => ({ ...f, ...manifestFields, arch, classes: manifestFields.classes || [] }));
      if (labels_text) setLabelsText(labels_text);
      // The preset fills the SETTINGS FORM, so the form has to be open or the
      // pick looks like it did nothing at all -- which is exactly how this
      // read when the picker sat above the list.
      setEditing((cur) => (cur === null ? "" : cur));
      toast.info(`Loaded ${name}`, arch
        ? "Add the .hef and save - it then appears in this card's list."
        : "Set the architecture, add the .hef, then save.");
    } catch (e) {
      toast.error("Could not load preset", e.message);
    }
  }

  // "Classes to keep" as a checklist over THIS model's labels - the Labels
  // field is the source of truth (line order = class index), so a 3-class
  // custom model shows 3 chips, not the 80 COCO ones. COCO is only the
  // fallback while the labels box is still empty. Empty selection = every
  // class. Toggling never reorders anything - order stays the labels' order.
  const modelLabels = String(labelsText || "")
    .split("\n").map((s) => s.trim()).filter(Boolean);
  // data?.vocab: `vocab` itself is declared after the early returns below -
  // reading it here is a temporal-dead-zone crash.
  // Only THIS model's labels make a checklist. No labels yet (no preset
  // picked, nothing typed) -> no chips: showing the COCO 80 before a model is
  // chosen implied a class list the model may not have.
  const classChoices = modelLabels;
  const selectedClasses = Array.isArray(form.classes)
    ? form.classes
    : String(form.classes || "").split(",").map((s) => s.trim()).filter(Boolean);
  // Editing the RUNNING model's manifest re-applies its classes at once, so
  // its ticks wait for detection to stop like the Detection settings' do
  // (the save route refuses the same change). A model not in use is free.
  const formLocked = !!params?.classes_locked && !!editing
    && activeSet.includes(editing);
  // Chosen classes first, then the rest; the search narrows both halves.
  const fq = formClassQuery.trim().toLowerCase();
  const shownClassChoices = [...classChoices]
    .sort((a, b) => (selectedClasses.includes(b) ? 1 : 0)
                  - (selectedClasses.includes(a) ? 1 : 0))
    .filter((c) => !fq || c.toLowerCase().includes(fq));

  function toggleClass(name) {
    if (formLocked) return;
    setForm((f) => {
      const cur = Array.isArray(f.classes) ? f.classes
        : String(f.classes || "").split(",").map((s) => s.trim()).filter(Boolean);
      const next = cur.includes(name) ? cur.filter((c) => c !== name) : [...cur, name];
      return { ...f, classes: next };
    });
  }

  async function save() {
    setBusy(true);
    try {
      if (file) {
        const body = new FormData();
        body.append("name", manifest.name);
        body.append("manifest", JSON.stringify(manifest));
        body.append("file", file);
        const res = await fetch(apiUrl("/api/models/upload"),
                                { method: "POST", credentials: "same-origin", body,
                                  headers: { ...authHeaders() } });
        const payload = await res.json().catch(() => null);
        if (!res.ok) throw new Error(payload?.error || `HTTP ${res.status}`);
        toast.success(`${manifest.name} uploaded (${Math.round((payload.bytes || 0) / 1e6)} MB)`);
      } else {
        const d = await api.post("/api/models", { manifest, labels_text: labelsText });
        toast.success(`${manifest.name} saved`, d?.reapplied
          ? "It is the running model, so the classes and confidence applied "
            + "straight away - the app picks them up within a few seconds."
          : undefined);
      }
      if (labelsText && file) await api.post("/api/models", { manifest, labels_text: labelsText });
      setFile(null);
      // Close on success only: a failed save keeps the dialog (and the work in
      // it) open, which is the whole reason the error toast is useful.
      setEditing(null);
      load();
    } catch (e) {
      toast.error("Could not save the model", e.message);
    } finally { setBusy(false); }
  }

  async function remove(name) {
    try {
      const d = await api.del(`/api/models/${name}`);
      // The delete that removed the LAST model also stopped detection (the
      // server's guard): a system detecting with no model is a dashboard
      // lying about "AI Running". Said in words, not inferred from a pill.
      if (d.no_models) {
        toast.error("No AI Models Detected",
                    d.detection_stopped
                      ? "That was the last model, so AI detection was stopped. "
                        + "Add or download a model to start it again."
                      : "This system has no model left to run. Add or "
                        + "download one before starting detection.");
      } else {
        toast.info(`${name} deleted`);
      }
      load();
    } catch (e) { toast.error("Delete failed", e.message); }
  }

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="AI Models" cards={2} rows={4} />;
  const vocab = data.vocab || {};
  // The offered architectures, plus whatever the model being edited already
  // says -- a value missing from its own dropdown reads as blank, and saving
  // over a blank arch is how an imported model would lose the arch its weights
  // were actually compiled for.
  const archOptions = (vocab.archs || []).includes(form.arch) || !form.arch
    ? (vocab.archs || [])
    : [form.arch, ...(vocab.archs || [])];

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">AI Models</div>
        <div className="page-actions">
          <PageGuideHost guide="models" />
          <TutorialNav step="models" />
          {/* Tracking lives here rather than on Camera Setup because it belongs
              to the MODEL's output: the ids these knobs decide are what every
              count is keyed on. Behind "Advanced" because the defaults are
              right for most scenes and a wrong value here quietly corrupts
              every total built on identity. */}
          <button className="btn" onClick={() => setAdvanced(true)}>
            Advanced
          </button>
          {/* Picking a model and pointing it at a camera are one job done in
              two places, and the model is chosen first -- so the way onward
              belongs here rather than in the sidebar the operator has to go
              back out to. This IS that: the standard next-page button, which
              from AI Models is always Camera Setup. */}
          <NextPageButton />
        </div>
      </div>

      {/* Two cards side by side. LEFT is what the dashboard manages: the
          presets it knows and anything uploaded through it. RIGHT is what an
          operator copied onto the board themselves. One column when narrow. */}
      <div className="models-split">
        <CollapsibleCard storageKey="models.card.default" title="Default Model"
                         chips={editing !== null
                           ? <Badge tone="muted">settings</Badge>
                           : <>
                               {!!total && <Badge tone="muted">{total} saved</Badge>}
                               {/* One pill per running model of THIS card's,
                                   worded as the Custom Models card words it. */}
                               {[...new Set([
                                 ...builtinRows.filter((b) => activeSet.includes(b.name)).map((b) => b.name),
                                 ...allModels.filter((m) => activeSet.includes(m.name)).map((m) => m.name),
                               ])].map((n) => (
                                 <Badge key={n} tone="ok">running {n}</Badge>
                               ))}
                             </>}>
          {/* The card is a CONTAINER with two pages: its list, and ONE model's
              settings. Swapped in place rather than opened in a dialog -- the
              settings belong to this card, so Save returns to its list rather
              than dropping the operator somewhere else. */}
          {editing !== null ? (
            <>
              <div className="filter-bar" style={{ marginBottom: 10 }}>
                <button className="btn btn-sm" onClick={() => setEditing(null)}>
                  ← Back
                </button>
                <strong>{editing || "New model"}</strong>
              </div>
            {/* The preset picker lives HERE, in the add/edit form it fills.
                Above the list it filled a form nobody could see, so picking a
                model looked like a button that did nothing.

                Framed as step one rather than just another field: a fresh
                "+ Add a model" is a 15-field form with no obvious place to
                begin, and the fast path -- pick one, everything below fills
                itself in -- is the whole point of this control existing. */}
            {!!(vocab.presets || []).length && (
              <div className={"models-preset-start" + (presetPulse ? " is-pulsing" : "")}>
                <Field label={<>
                  <span className="models-preset-step">1</span>
                  {" "}Start from a default detection model
                </>}
                       hint="Fills every field below for a known COCO detector - pick one, then set the architecture and add the .hef.">
                  <select ref={presetRef} className="input" value=""
                          onChange={(e) => { loadPreset(e.target.value); e.target.value = ""; }}>
                    <option value="">- pick a default detection model -</option>
                    {(vocab.presets || []).map((p) => (
                      <option key={p.name} value={p.name}>{p.label}</option>
                    ))}
                  </select>
                </Field>
              </div>
            )}
            <p className="muted small" style={{ margin: "10px 0 4px" }}>
              2 - or fill in the fields below by hand.
            </p>
            {/* Seven fields in one undifferentiated grid asked the operator to
                sort them: the model's IDENTITY (what to call it, what it is)
                and its RUNTIME configuration (what it was compiled for, how
                strict to be) are different decisions, so they get different
                boxes and a heading each. */}
            <div className="model-form-group">
              <div className="model-form-title">Model</div>
              <div className="form-grid" style={{ marginBottom: 0 }}>
                <Field label="Name" hint="lowercase, becomes the filename">
                  <input className="input" value={form.name}
                         onChange={(e) => set("name", e.target.value.toLowerCase())} />
                </Field>
                <Field label="Task">
                  <select className="input" value={form.task} onChange={(e) => set("task", e.target.value)}>
                    {(vocab.tasks || []).map((t) => <option key={t}>{t}</option>)}
                  </select>
                </Field>
                {/* The list is this BOARD's accelerator, not every
                    architecture the format knows. Five of the six were always
                    wrong here, and a wrong one is not caught on save: the
                    model installs, sits in the list looking ready, and fails
                    at load with a mismatch nobody connects to a dropdown they
                    picked from a week ago. An arch already ON the model stays
                    in the list (a model imported from another box), so editing
                    its name cannot silently re-target its weights. */}
                <Field label="Architecture"
                       hint={archOptions.length === 1
                         ? `This board runs ${archOptions[0]}. A HEF built for anything else will not load.`
                         : "A HEF built for one Hailo arch will not load on another."}>
                  <select className="input" value={form.arch} onChange={(e) => set("arch", e.target.value)}>
                    {!form.arch && <option value="">- select the accelerator -</option>}
                    {archOptions.map((a) => <option key={a}>{a}</option>)}
                  </select>
                </Field>
              </div>
            </div>
            <div className="model-form-group">
              <div className="model-form-title">
                Model configuration
                <span className="muted small" style={{ fontWeight: 400 }}>
                  {" "}- what this model was compiled for, and how strict to be
                </span>
              </div>
              <div className="form-grid" style={{ marginBottom: 0 }}>
                <Field label="Input size (px)" hint="What it was compiled for, not your camera.">
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input className="input" type="number" value={form.input.width}
                           style={{ minWidth: 0 }}
                           onChange={(e) => setInput("width", e.target.value)} />
                    <span className="muted small">×</span>
                    <input className="input" type="number" value={form.input.height}
                           style={{ minWidth: 0 }}
                           onChange={(e) => setInput("height", e.target.value)} />
                  </div>
                </Field>
                <Field label="Confidence threshold"
                       hint="Below this, a detection is dropped on the accelerator - raising it costs nothing.">
                  <input className="input" type="number" step="0.05" min="0.05" max="0.95"
                         value={form.confidence}
                         onChange={(e) => set("confidence", Number(e.target.value))} />
                </Field>
                <Field label="Post-processing"
                       hint="Runs on the Hailo accelerator - the only mode this board supports.">
                  <input className="input" value="nms_on_chip" readOnly disabled />
                </Field>
              </div>
            </div>
            <Field label="Classes to keep"
                   hint="Tick the classes that should fire events. None ticked = every class. The list follows this model's Labels below.">
              {formLocked && <ClassLockNote reason={params.classes_locked_reason} />}
              {classChoices.length ? (
                <>
                  {/* CHOSEN first, as removable chips, then a searchable
                      list: eighty toggle buttons look identical from a metre
                      away, so picking "traffic light" was a scan of the wall
                      rather than a choice, and what had been picked was
                      invisible among them. */}
                  {!!selectedClasses.length && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 5,
                                  marginBottom: 6 }}>
                      {selectedClasses.map((c) => (
                        <button type="button" key={c} className="btn"
                                title="Click to remove" disabled={formLocked}
                                style={{ padding: "2px 9px", fontSize: 12,
                                         borderColor: "var(--accent, #04bade)",
                                         color: "var(--accent-text, #04bade)" }}
                                onClick={() => toggleClass(c)}>{c} ✕</button>
                      ))}
                    </div>
                  )}
                  <input className="input" value={formClassQuery} disabled={formLocked}
                         placeholder="Search classes…"
                         onChange={(e) => setFormClassQuery(e.target.value)}
                         style={{ marginBottom: 6 }} />
                  <div style={{ border: "1px solid var(--line, rgba(128,128,128,.25))",
                                borderRadius: 8, maxHeight: 200, overflowY: "auto" }}>
                    {shownClassChoices.length === 0 ? (
                      <p className="muted small" style={{ margin: 0, padding: "10px 12px" }}>
                        No class matches “{formClassQuery}”.
                      </p>
                    ) : shownClassChoices.map((c, i) => {
                      const on = selectedClasses.includes(c);
                      return (
                        <label key={c}
                               style={{ display: "flex", alignItems: "center", gap: 8,
                                        padding: "6px 10px", cursor: "pointer",
                                        fontSize: 13,
                                        borderTop: i ? "1px solid var(--line, rgba(128,128,128,.14))"
                                                     : "none",
                                        background: on ? "rgba(4,186,222,.08)" : "transparent" }}>
                          <input type="checkbox" checked={on} disabled={formLocked}
                                 onChange={() => toggleClass(c)} />
                          <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                         textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {c}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                  <div className="filter-bar" style={{ marginTop: 6 }}>
                    <span className="muted small">
                      {selectedClasses.length
                        ? `${selectedClasses.length} of ${classChoices.length} classes kept`
                        : `every class kept (${classChoices.length} available)`}
                    </span>
                    <div style={{ flex: 1 }} />
                    {!!selectedClasses.length && (
                      <button type="button" className="btn btn-sm" disabled={formLocked}
                              onClick={() => set("classes", [])}>Clear</button>
                    )}
                  </div>
                </>
              ) : (
                <p className="muted small">
                  Pick a preset - its classes show here once it has labels.
                </p>
              )}
            </Field>
            {/* The LABELS are not edited here any more.
                Line order in that textarea IS the class index, so a line moved
                or removed silently relabels every detection the model will
                ever make -- person becomes bicycle, and nothing anywhere says
                so. The list is not lost: a preset fills it, an uploaded
                model's own .labels file supplies it, and the Classes field
                above still chooses which of them this model reports. What is
                gone is the one control whose only use was to get it wrong.

                The weights stay, and stand alone: attaching a .hef is the one
                manual step a custom model still needs, and it was the other
                half of a folded section most operators never opened. */}
            <div className="models-advanced-fields">
              <Field label="Weights file (.hef)"
                     hint="A Hailo HEF compiled for this board's architecture. Nothing else runs here.">
                <input className="input" type="file" accept=".hef"
                       onChange={(e) => {
                         const f = e.target.files?.[0] || null;
                         if (f && !f.name.toLowerCase().endsWith(".hef")) {
                           toast.error("Only .hef weights are supported",
                                       "This platform runs compiled Hailo models; "
                                       + "ONNX/TFLite/PyTorch files cannot be compiled on the board.");
                           e.target.value = "";
                           setFile(null);
                           return;
                         }
                         setFile(f);
                       }} />
              </Field>
              {/* Whether anything was actually attached, and what the model
                  will call its classes -- read-only, because "80 labels" is
                  the fact that matters and the textarea was the risk. */}
              {(file || !!labelsText.trim()) && (
                <p className="muted small" style={{ margin: "2px 0 0" }}>
                  {file && <>{file.name} attached</>}
                  {file && !!labelsText.trim() && " · "}
                  {!!labelsText.trim() && <>{modelLabels.length} labels</>}
                </p>
              )}
            </div>
            <details>
              <summary className="muted small">Manifest JSON (what gets written)</summary>
              <pre className="log-pre">{JSON.stringify(manifest, null, 2)}</pre>
            </details>
            <div className="filter-bar">
              <button className="btn btn-accent" disabled={busy || !form.name || !data.editable}
                      onClick={save}>
                {file ? "Upload + save" : "Save manifest"}
              </button>
              <button className="btn" onClick={() => setEditing(null)}>
                Cancel
              </button>
            </div>
            </>
          ) : (
            <>
              <p className="muted small">
                <strong>+ Add a model</strong> fills a default detector in for you.
              </p>
              {/* What is running is said ONCE, in the header pill -- the same
                  place the Custom Models card says it. A "Running now" list
                  under it repeated the pill in a second voice. */}
              <div className="filter-bar" style={{ margin: "10px 0 8px" }}>
                <button className="btn btn-accent" style={{ width: "100%" }}
                        disabled={!data.editable}
                        onClick={() => { setForm(BLANK); setLabelsText("");
                                         setFile(null); setEditing(""); }}>
                  + Add a model
                </button>
              </div>

              {/* The built-in models first: they are what a new system runs
                  out of the box, and the setup tour's "Choose an AI model"
                  step has nothing to point at without them (UAT-101). Same
                  buttons as a saved model, same running set. */}
              {builtinRows.map((b) => {
                const running = activeSet.includes(b.name);
                const alone = running && activeSet.length === 1;
                const runnable = !!b.downloaded;
                // Its own prefix in `expanded`: a saved manifest of the same
                // name (the preset path writes yolov8s.json) is a different
                // card, and must not open and shut with this one.
                const foldKey = `builtin:${b.name}`;
                return (
                  <div key={`builtin:${b.name}`}
                       className={"model-card" + (running ? " is-running" : "")}>
                    <div className="model-card-head">
                      <span className="model-card-name">{b.name}</span>
                      <span style={{ flex: 1 }} />
                      {running && <Badge tone="ok">Active</Badge>}
                    </div>
                    <div className="model-card-badges">
                      <span className="model-chip">built-in</span>
                      {(b.downloaded_archs || []).map((a) => (
                        <span key={a} className="model-chip is-accent">{a}</span>
                      ))}
                      <span className={"model-chip" + (runnable ? "" : " is-warn")}>
                        {runnable ? ".hef on this box" : "not on this box"}
                      </span>
                    </div>
                    {b.description && <div className="model-card-meta">{b.description}</div>}
                    <div className="filter-bar model-card-actions">
                      <button className={"btn" + (running || !runnable ? "" : " btn-accent")}
                              data-tour="use-model"
                              disabled={!builtin?.editable || !runnable || !!selecting}
                              title={!runnable
                                ? "This model's .hef is not on this box"
                                : running
                                  ? (alone
                                      ? "Switch this model off - stop detection first if it is running"
                                      : "Stop this model; the others keep running")
                                  : activeSet.length
                                    ? "Run this model as well - both see the same camera"
                                    : "Run this model on this system"}
                              onClick={() => toggleEnable(b.name)}>
                        {selecting === b.name ? "Switching…"
                          : !runnable ? "Not on this box"
                          : running ? "In use · click to stop"
                          : activeSet.length ? "Add onto this system"
                          : "Use on this system"}
                      </button>
                      {runnable && !alone && activeSet.length > 0 && (
                        <button className="btn"
                                disabled={!builtin?.editable || !!selecting}
                                title="Stop the others and run only this model"
                                onClick={() => useModel(b.name)}>
                          Only this
                        </button>
                      )}
                    </div>
                    {/* The RUNNING built-in's detection settings -- classes
                        and confidence -- under the card, behind the same
                        fold the Custom Models card uses, shut by default.
                        Since the built-ins were listed on their own
                        (19e6a3c7) the form only ever hung under a custom
                        card, so a system running yolov8s out of the box had
                        no place on this page to set either. Same route as
                        the dashboard's "AI model classes" card, so the two
                        stay in step; `model` names the entry on a
                        multi-model system. */}
                    {running && expanded[foldKey] && (
                      <div style={{ padding: "0 0 4px 0" }} data-builtin-settings>
                        <DetectorParams params={params} model={b.name} onSaved={load} />
                      </div>
                    )}
                    {running && (
                      <CardFold open={!!expanded[foldKey]}
                                onToggle={() => setExpanded((o) => ({ ...o, [foldKey]: !o[foldKey] }))} />
                    )}
                  </div>
                );
              })}

              {!total ? (
                <p className="muted small">
                  {builtinRows.length
                    ? <>No models of your own yet. The built-in ones above are
                        ready to run; <strong>+ Add a model</strong> is for a
                        .hef of your own.</>
                    : <>Nothing saved yet. <strong>+ Add a model</strong> opens the
                        settings; whatever you save appears here.</>}
                </p>
              ) : (
              pageItems.map((m) => {
                const running = activeSet.includes(m.name);
                const alone = running && activeSet.length === 1;
                // weights_present, OR a built-in of the same name is on the box
                // (builtinOnBox above): the preset path saves yolov8s.json with
                // no upload, and select() runs the catalogue's .hef for it.
                const hasWeights = m.weights_present || builtinOnBox.has(m.name);
                const runnable = (m.file || "").toLowerCase().endsWith(".hef") && hasWeights;
                return (
                  // The whole row opens the settings, so reaching them does not
                  // depend on finding one small button. The action buttons below
                  // stopPropagation -- "Use on this system" must not also open a
                  // dialog on top of the switch it just made.
                  <div key={m.name}
                       className={"model-card" + (running ? " is-running" : "")}
                       role="button" tabIndex={0}
                       onClick={() => openSettings(m)}
                       onKeyDown={(e) => {
                         if (e.key === "Enter" || e.key === " ") {
                           e.preventDefault(); openSettings(m);
                         }
                       }}>
                    <div className="model-card-head">
                      <span className="model-card-name">{m.name}</span>
                      <span style={{ flex: 1 }} />
                      {running && <Badge tone="ok">Active</Badge>}
                    </div>
                    {/* What it IS, as chips: architecture, weight size, how
                        many classes it can name. One line, one volume. */}
                    <div className="model-card-badges">
                      <span className="model-chip is-accent">{m.arch}</span>
                      <span className={"model-chip"
                        + (hasWeights ? "" : " is-warn")}>
                        {m.weights_present
                          ? `${Math.round(m.weights_bytes / 1e6)} MB`
                          : hasWeights ? "built-in .hef" : "no weights"}
                      </span>
                      <span className="model-chip">{m.postprocess}</span>
                      <span className="model-chip">
                        {m.labels_count || 0} class{(m.labels_count || 0) === 1 ? "" : "es"}
                      </span>
                      {m.shared && <span className="model-chip is-warn">shared</span>}
                    </div>
                    {expanded[m.name] && <>
                    <div className="model-card-meta">
                      {m.input?.width}×{m.input?.height}
                      {m.description ? ` · ${m.description}` : ""}
                    </div>
                    <div className="filter-bar model-card-actions">
                      {/* Several models run at once on one camera, so this
                          button is MEMBERSHIP, not a switch: it adds this
                          model to the running set or takes it out again.
                          "In use" used to be a dead label, which left no way
                          to stop a model except picking a different one. */}
                      {/* Never painted as the primary action while it cannot
                          BE the action. A disabled button in accent colour
                          reads as the thing to press, and the only clue it
                          was dead lived in a tooltip nobody hovers -- which
                          is how "why can't I start this" happens in front of
                          a card that already says "no weights". */}
                      <button className={"btn" + (running || !runnable ? "" : " btn-accent")}
                              /* The last running model IS clickable now. It
                                 used to be disabled outright, which meant a
                                 system with one model could never switch it
                                 off -- not even while stopped, when doing so
                                 is perfectly reasonable. The server refuses
                                 it only while detection is LIVE, and says to
                                 stop detection first; it is the only party
                                 that can answer that honestly. */
                              disabled={!data.editable || !runnable || !!selecting}
                              title={!runnable
                                ? "Upload the .hef first - a manifest alone has nothing to run"
                                : running
                                  ? (alone
                                      ? "Switch this model off - stop detection first if it is running"
                                      : "Stop this model; the others keep running")
                                  : activeSet.length
                                    ? "Run this model as well - both see the same camera"
                                    : "Run this model on this system"}
                              onClick={(e) => { e.stopPropagation(); toggleEnable(m.name); }}>
                        {/* "In use" alone read as a status. It is a button,
                            and clicking it stops the model, so it says so. */}
                        {selecting === m.name ? "Switching…"
                          : !runnable ? "Needs its .hef"
                          : running ? "In use · click to stop"
                          : activeSet.length ? "Add onto this system"
                          : "Use on this system"}
                      </button>
                      {/* And said out loud, next to the button that cannot be
                          pressed, with where to go about it. A manifest
                          describes a model; the .hef IS the model. */}
                      {!runnable && (
                        <span className="small" style={{ color: "var(--warn-text)" }}>
                          {(m.file || "").toLowerCase().endsWith(".hef")
                            ? "The weights file was never uploaded - open Settings and add the .hef."
                            : "This manifest names no .hef - open Settings and add one."}
                        </span>
                      )}
                      {/* Still reachable when it would actually change
                          something: drop every other model and run this one
                          by itself. */}
                      {runnable && !alone && activeSet.length > 0 && (
                        <button className="btn"
                                disabled={!data.editable || !!selecting}
                                title="Stop the others and run only this model"
                                onClick={(e) => { e.stopPropagation(); useModel(m.name); }}>
                          Only this
                        </button>
                      )}
                      <button className="btn"
                              onClick={(e) => { e.stopPropagation(); openSettings(m); }}>
                        Settings
                      </button>
                      {/* Deleting a model takes its weights with it, and a
                          workflow bound to its classes stops matching. A
                          three-second hold is the confirmation: a dialog's
                          second click lands where the first did. */}
                      <span onClick={(e) => e.stopPropagation()}>
                        <HoldToDelete label="Hold to delete"
                                      holdLabel="Keep holding…"
                                      disabled={!data.editable}
                                      onConfirm={() => remove(m.name)} />
                      </span>
                    </div>
                    </>}
                    <CardFold open={!!expanded[m.name]}
                              onToggle={() => setExpanded((o) => ({ ...o, [m.name]: !o[m.name] }))} />
                  </div>
                );
                })
              )}

              {/* Three at a time: this card shares the row with another one,
                  and a fourth model is what starts pushing the page long. */}
              {pages > 1 && (
                <div className="filter-bar" style={{ marginTop: 10, alignItems: "center" }}>
                  <button className="btn btn-sm" disabled={page <= 0}
                          onClick={() => setPage((p) => Math.max(0, p - 1))}>
                    ← Previous
                  </button>
                  <span className="muted small tnum">
                    {pageStart + 1}–{Math.min(total, pageStart + PAGE_SIZE)} of {total}
                  </span>
                  <button className="btn btn-sm" disabled={page >= pages - 1}
                          onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}>
                    Next →
                  </button>
                </div>
              )}
            </>
          )}
        </CollapsibleCard>

        <LocalModels params={params} activeSet={activeSet}
                     onToggle={toggleEnable} onChanged={load}
                     editable={!!data.editable} arch={detectedArch} />
      </div>

      <TrackerAdvanced open={advanced} onClose={() => setAdvanced(false)} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Advanced - object TRACKING, the thing that decides whether two boxes on
// consecutive frames are the same object.
//
// It lives behind a button because it is the highest-leverage and most
// dangerous panel on the page: every count that keys on identity -- unique
// visitors, line crossings, dwell, travel, cycle time -- is only as good as
// the id, and a wrong value here does not error, it just quietly miscounts.
// The defaults suit most scenes; this is for the ones they do not.
//
// Bounds come from the SERVER (`fields`), not from constants here, so the
// inputs can never offer a value the API would refuse.
// ---------------------------------------------------------------------------

// The exact Hailo tracker knob names - the vocabulary anyone who has tuned a
// Hailo pipeline already knows and can google. The mono name IS the label;
// the hint says what it does on this box.
const TRACKER_COPY = {
  kalman_dist_thr: {
    label: "kalman_dist_thr",
    hint: "How far an object may MOVE between frames and keep its id, when "
        + "the boxes no longer overlap. Higher = more permissive; raise it "
        + "for fast objects, lower it in a crowd.",
  },
  iou_thr: {
    label: "iou_thr",
    hint: "Match tolerance for a CONFIRMED object (a distance: higher = more "
        + "permissive overlap matching). Lower it if two objects passing "
        + "close swap ids.",
  },
  init_iou_thr: {
    label: "init_iou_thr",
    hint: "Match tolerance while an object is still NEW - stricter than "
        + "iou_thr on purpose, so a ghost must keep matching tightly before "
        + "it earns an id.",
  },
  keep_new_frames: {
    label: "keep_new_frames",
    hint: "Frames a new object must be seen before it is CONFIRMED and gets "
        + "an id. One-frame detector ghosts never mint an id, so by-PID "
        + "counts (violations, visitors) cannot be inflated by flicker.",
  },
  keep_tracked_frames: {
    label: "keep_tracked_frames",
    hint: "Frames a confirmed object survives unseen - long enough to ride "
        + "out missed detections.",
  },
  keep_lost_frames: {
    label: "keep_lost_frames",
    hint: "Extra frames in the lost state before the object is gone for "
        + "good. After that, something re-entering counts as NEW.",
  },
};

function TrackerAdvanced({ open, onClose }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [edit, setEdit] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    api.get("/api/detector/control/tracker")
      .then((d) => { setData(d); setEdit(d.tracker || {}); })
      .catch(() => setData(null));
  }, [open]);

  async function save() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/tracker", edit);
      setEdit(d.tracker);
      toast.success("Tracking updated",
                    "Applied within a few seconds - live object ids are kept, "
                    + "so counts in progress are not reset.");
      onClose();
    } catch (e) { toast.error("Could not save tracking settings", e.message); }
    finally { setBusy(false); }
  }

  const fields = data?.fields || {};
  const dirty = data && Object.keys(fields)
    .some((k) => Number(edit[k]) !== Number(data.tracker?.[k]));

  return (
    <Dialog open={open} title="Advanced - object tracking" onClose={onClose}>
      {!data ? (
        <p className="muted small">Loading…</p>
      ) : (
        <>
          <p className="muted small">
            These decide when two boxes on consecutive frames are the same
            object.
            <InfoHint label="Why tracking matters">
              Every count that follows an object over time - unique visitors,
              line crossings, dwell, travel, cycle time - is keyed on the track
              id. Get these wrong and the numbers stay plausible while being
              wrong, so change one at a time and watch the feed.
            </InfoHint>
          </p>

          {/* Two columns: eight sliders in one column is a long scroll. */}
          <div className="tracker-grid">
          {Object.entries(fields).map(([key, spec]) => {
            const copy = TRACKER_COPY[key] || { label: key, hint: "" };
            return (
              <Field key={key} label={copy.label} hint={copy.hint}>
                <div className="filter-bar" style={{ alignItems: "center" }}>
                  <input type="range" className="slider" style={{ flex: 1 }}
                         min={spec.min} max={spec.max} step={spec.step}
                         value={edit[key] ?? spec.default}
                         disabled={!data.editable || busy}
                         onChange={(e) => setEdit({ ...edit, [key]: Number(e.target.value) })} />
                  <input className="input tnum" type="number"
                         style={{ width: 90, flex: "none" }}
                         min={spec.min} max={spec.max} step={spec.step}
                         value={edit[key] ?? spec.default}
                         disabled={!data.editable || busy}
                         onChange={(e) => setEdit({ ...edit, [key]: Number(e.target.value) })} />
                </div>
                <span className="muted small tnum">
                  {spec.min}–{spec.max} · default {spec.default}
                </span>
              </Field>
            );
          })}
          </div>

          <div className="filter-bar" style={{ marginTop: 12 }}>
            <button className="btn"
                    disabled={!data.editable || busy}
                    onClick={() => setEdit(Object.fromEntries(
                      Object.entries(fields).map(([k, v]) => [k, v.default])))}>
              Reset to defaults
            </button>
            <div style={{ flex: 1 }} />
            <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
            <button className="btn btn-accent"
                    disabled={!data.editable || !dirty || busy}
                    onClick={save}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Models on the board - a folder an operator copies models INTO (default
// /home/pi/model), listed and runnable without going through the upload form.
// Nothing here uploads or writes: the folder belongs to whoever manages the
// board, and the dashboard only reads it and points the detector at a choice.
// ---------------------------------------------------------------------------

/**
 * Upload a custom model: the .hef + .json pair a compile produces.
 *
 * Pick the model FOLDER (or the two files together); the dialog pairs them by
 * stem and shows what it found before anything is sent, with the same checks
 * the server makes -- same name, JSON parses, a class list, `target` is this
 * box's chip -- so the operator learns about a mistake here, not after a
 * 400 MB upload. The upload itself streams to the drop folder as
 * <root>/<name>/<name>.hef + .json, the layout the guide describes.
 */
function UploadModelDialog({ open, onClose, arch, existing, onDone, onGuide }) {
  const toast = useToast();
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [replace, setReplace] = useState(false);
  const [pct, setPct] = useState(0);
  useEffect(() => { if (!open) { setFiles([]); setReplace(false); setPct(0); } }, [open]);

  const stem = (f) => f.name.replace(/\.[^.]+$/, "");
  const hef = files.find((f) => /\.hef$/i.test(f.name));
  const json = files.find((f) => /\.json$/i.test(f.name)
                                && (!hef || stem(f) === stem(hef)))
    || files.find((f) => /\.json$/i.test(f.name));
  const [manifest, setManifest] = useState(null);
  const [parseError, setParseError] = useState("");
  useEffect(() => {
    setManifest(null); setParseError("");
    if (!json) return;
    json.text().then((t) => {
      try { setManifest(JSON.parse(t)); } catch (e) { setParseError(String(e.message || e)); }
    }).catch(() => setParseError("could not read the .json"));
  }, [json]);

  const problems = [];
  if (files.length && !hef) problems.push("no .hef in what you picked");
  if (files.length && !json) problems.push("no .json in what you picked");
  if (hef && json && stem(hef) !== stem(json)) {
    problems.push(`the files must share one name: ${hef.name} and ${json.name}`);
  }
  if (hef && !/^[A-Za-z0-9._-]+$/.test(stem(hef))) {
    problems.push("model name: letters, digits, dot, dash and underscore only");
  }
  if (parseError) problems.push(`the .json does not parse: ${parseError}`);
  if (manifest) {
    const classes = manifest.classes || manifest.labels || manifest.class_names;
    if (!Array.isArray(classes) || !classes.length) {
      problems.push('the .json needs "classes": a list of class names');
    } else if (manifest.num_classes != null && Number(manifest.num_classes) !== classes.length) {
      problems.push(`"num_classes" says ${manifest.num_classes} but "classes" lists ${classes.length}`);
    }
    const target = String(manifest.target || manifest.arch || "").toLowerCase();
    if (target && arch && target !== arch) {
      problems.push(`compiled for ${target}; this box has a ${arch}`);
    }
  }
  const clash = hef && existing.includes(stem(hef));
  const ready = hef && json && manifest && !problems.length && (!clash || replace);

  async function send() {
    setBusy(true); setPct(0);
    const fd = new FormData();
    fd.append("hef", hef, hef.name);
    fd.append("manifest", json, json.name);
    if (replace) fd.append("replace", "true");
    // XHR, not fetch: a 400 MB upload with no progress reads as a hang.
    await new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", apiUrl("/api/detector/local_models/upload"));
      Object.entries(authHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v));
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) setPct(Math.round(100 * e.loaded / e.total)); };
      xhr.onload = () => {
        let d = {};
        try { d = JSON.parse(xhr.responseText || "{}"); } catch { /* fall through */ }
        if (xhr.status >= 200 && xhr.status < 300 && d.ok) {
          toast.success(`${d.model?.name || stem(hef)} uploaded`,
                        `${d.model?.labels_count ?? ""} classes. Switch it on in the list to run it.`);
          onDone();
        } else {
          toast.error("The box refused the model", d.detail || d.error || `HTTP ${xhr.status}`);
        }
        resolve();
      };
      xhr.onerror = () => { toast.error("Upload failed", "the connection dropped"); resolve(); };
      xhr.send(fd);
    });
    setBusy(false);
  }

  const fmt = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`);

  return (
    <Dialog open={open} title="Upload a custom model" onClose={busy ? undefined : onClose}>
      <p className="muted small">
        Pick the model <strong>folder</strong> (a <code>.hef</code> and a{" "}
        <code>.json</code> with the same name), or pick the two files together.
        Not sure how to make one? Open the{" "}
        <button type="button" className="link-btn" onClick={onGuide}>setup guide</button>.
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0" }}>
        <label className="btn btn-sm">
          Choose folder
          <input type="file" hidden webkitdirectory="" directory="" multiple
                 onChange={(e) => { setFiles(Array.from(e.target.files || [])
                   .filter((f) => /\.(hef|json)$/i.test(f.name))); e.target.value = ""; }} />
        </label>
        <label className="btn btn-sm">
          Choose files
          <input type="file" hidden multiple accept=".hef,.json"
                 onChange={(e) => { setFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
        </label>
      </div>
      {!!files.length && (
        <div className="small" style={{ display: "grid", gap: 4 }}>
          <div>{hef ? <>weights: <code>{hef.name}</code> ({fmt(hef.size)})</> : "weights: -"}</div>
          <div>{json ? <>description: <code>{json.name}</code></> : "description: -"}</div>
          {manifest && (
            <div className="muted">
              {(manifest.classes || manifest.labels || []).length} classes
              {manifest.target ? <>, target <code>{manifest.target}</code></> : null}
              {arch ? <> (this box: <code>{arch}</code>)</> : null}
            </div>
          )}
          {problems.map((p) => <div key={p} className="c-warn">{p}</div>)}
          {clash && !problems.length && (
            <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
              a model called <code>{stem(hef)}</code> is already in the folder - replace it
            </label>
          )}
        </div>
      )}
      {busy && (
        <div className="muted small" style={{ marginTop: 8 }}>
          Uploading… {pct}%
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        <button className="btn btn-sm" disabled={busy} onClick={onClose}>Cancel</button>
        <button className="btn btn-sm btn-accent" disabled={!ready || busy} onClick={send}>
          {busy ? "Uploading…" : "Upload"}
        </button>
      </div>
    </Dialog>
  );
}

/** The strip at a model card's foot: chevrons down while the card is folded
 *  to its name and chips, up while its buttons and settings show. Stops the
 *  click so a card whose whole face opens the editor does not also do that. */
function CardFold({ open, onToggle }) {
  return (
    <button type="button" className="model-card-fold" aria-expanded={open}
            title={open ? "Minimise" : "Show details and settings"}
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            onKeyDown={(e) => e.stopPropagation()}>
      {open ? <ChevronsUp size={16} /> : <ChevronsDown size={16} />}
    </button>
  );
}

/** The custom-model setup guide, in a dialog. The HTML is built at
 *  development time from core/static/guides/*.md and served by the box, so
 *  it always describes the rules THIS release enforces. */
function GuideDialog({ guide, onClose }) {
  return (
    <Dialog open={!!guide} title={guide?.title || "Setup guide"} onClose={onClose}
            footer={<button className="btn btn-sm" onClick={onClose}>Close</button>}>
      <div className="guide-body" dangerouslySetInnerHTML={{ __html: guide?.html || "" }} />
    </Dialog>
  );
}

function LocalModels({ params, activeSet, onToggle, onChanged, editable = false, arch = "" }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState({});   // model id -> true = unfolded
  const [guide, setGuide] = useState(null);                // {title, html} while open
  // Five per page: this card sits beside the Default Model one, and a board
  // with a folder full of HEFs would otherwise make the row unreadably tall.
  const LOCAL_PAGE = 5;
  const [page, setPage] = useState(0);
  // `params` -- what the detector is set to right now -- is the PAGE's, not
  // this card's. Both cards answer "is this model in use", and a card holding
  // its own copy of that answer is how they came to disagree: switching a
  // model in one left the other showing the state from before, each certain a
  // different model was running.
  const load = useCallback(() => {
    api.get("/api/detector/local_models").then(setData).catch(() => setData(null));
    onChanged?.();                      // the page re-reads what is running
  }, [onChanged]);
  useEffect(load, [load]);

  const openGuide = useCallback(() => {
    api.get("/api/detector/local_models/guide").then(setGuide)
      .catch((e) => toast.error("Could not open the guide", e.message));
  }, [toast]);

  if (!data) return null;                       // hub, or the endpoint 400d

  // Match on folder AND filename: the same HEF name appears in more than one
  // folder, so the name alone would light up the wrong row.
  // Clamped, so deleting the last model on the last page cannot strand the
  // card on an empty page with no way back.
  const localAll = data.models || [];
  const localPages = Math.max(1, Math.ceil(localAll.length / LOCAL_PAGE));
  const localStart = Math.min(page, localPages - 1) * LOCAL_PAGE;
  const pageItems = localAll.slice(localStart, localStart + LOCAL_PAGE);

  // Match on folder AND filename: the same HEF name appears in more than one
  // folder, so the name alone lights up the wrong row.
  //
  // The MULTI list is the truth when several models run: the singular
  // `model`/`models_dir` keys mirror only the FIRST entry, so checking those
  // alone left every other running model showing "Use on this system" while
  // it was already running.
  const sameFolder = (a, b) => !a || !b
    || String(a).replace(/\\/g, "/") === String(b).replace(/\\/g, "/");
  const isRunning = (m) => {
    if (!params) return false;
    const entries = (params.models || []).length
      ? params.models
      : (params.model ? [{ model: params.model, models_dir: params.models_dir }] : []);
    return entries.some((e) => e.model === m.name
      && sameFolder(e.models_dir, m.folder));
  };

  /** Remove a dropped HEF from the model folder. The service refuses the one
   *  running, so a live system cannot be broken from here. */
  async function removeLocal(m) {
    setBusy(m.id);
    try {
      const d = await api.post("/api/detector/local_models/delete", { name: m.id });
      if (d.no_models) {
        toast.error("No AI Models Detected",
                    d.detection_stopped
                      ? "That was the last model, so AI detection was stopped. "
                        + "Add or download a model to start it again."
                      : "This system has no model left to run. Add or "
                        + "download one before starting detection.");
      } else {
        toast.info(`${m.name} deleted`, d.deleted);
      }
      load();
    } catch (e) {
      toast.error("Could not delete the model", e.message);
    } finally { setBusy(null); }
  }

  async function use(m) {
    setBusy(m.id);
    try {
      const d = await api.post("/api/detector/local_models/select", { name: m.id });
      toast.success(`Now running ${m.name}`,
                    `From ${d.models_dir} - the detection app switches within a few seconds.`);
      load();
    } catch (e) {
      toast.error("Could not switch model", e.message);
    } finally { setBusy(null); }
  }

  return (
    <CollapsibleCard storageKey="models.card.local" title="Custom Models"
                     chips={<>
                       {!!(data.models || []).length && (
                         <Badge tone="muted">{data.models.length} in folder</Badge>
                       )}
                       {(data.models || []).filter(isRunning).map((m) => (
                         <Badge key={m.path} tone="ok">running {m.name}</Badge>
                       ))}
                     </>}
                     actions={<>
                       {/* A file, not a page: the guide is read at a desk with
                           the compile tools open, and printed. Served by the
                           box so it always describes the rules THIS release
                           enforces. */}
                       <button type="button" className="btn btn-icon" aria-label="Setup guide"
                               title="Setup guide: folder, .json, upload"
                               onClick={openGuide}>
                         <BookOpen size={15} />
                       </button>
                       <button type="button" className="btn btn-icon" disabled={!!busy}
                               onClick={load} title="Re-scan the folder" aria-label="Reload">
                         <RefreshCw size={15} />
                       </button>
                     </>}>
      <p className="muted small">
        A model is a <code>.hef</code> + <code>.json</code> pair with one name.
        Upload it here, or copy the pair into <code>{data.root}</code> on the
        board and it shows up the same.
      </p>
      {/* Same shape as "+ Add a model" on the card beside it: the two cards
          are read as a pair, so their one primary action lines up. */}
      <div className="filter-bar" style={{ margin: "10px 0 8px" }}>
        <button className="btn btn-accent" style={{ width: "100%" }}
                disabled={!editable || !!busy}
                title={editable ? "Upload a .hef + .json pair" : "Admin only"}
                onClick={() => setUploadOpen(true)}>
          + Upload a model
        </button>
      </div>
      <UploadModelDialog open={uploadOpen} onClose={() => setUploadOpen(false)}
                         arch={arch} existing={(data.models || []).map((m) => m.name)}
                         onGuide={openGuide}
                         onDone={() => { setUploadOpen(false); load(); }} />
      <GuideDialog guide={guide} onClose={() => setGuide(null)} />

      {!data.available ? (
        <p className="muted small">
          <code>{data.root}</code> does not exist on this board. Create it and drop a
          model in, then reload.
        </p>
      ) : !data.models.length ? (
        <p className="muted small">No models in that folder yet.</p>
      ) : pageItems.map((m) => (
        <div key={m.path}
             className={"model-card" + (isRunning(m) ? " is-running" : "")}
             style={{ cursor: "default" }}>
        <div>
          <div style={{ minWidth: 0 }}>
            <div className="model-card-head">
              <span className="model-card-name">{m.name}</span>
              <span style={{ flex: 1 }} />
              {isRunning(m) && <Badge tone="ok">Active</Badge>}
            </div>
            {/* Same chip vocabulary as the saved models above, so a dropped
                HEF and a manifest model are read the same way. */}
            <div className="model-card-badges">
              {m.arch ? <span className="model-chip is-accent">{m.arch}</span>
                      : <span className="model-chip is-warn">arch unknown</span>}
              <span className="model-chip">
                {Math.max(1, Math.round(m.bytes / 1e6))} MB
              </span>
              <span className="model-chip">{m.task}</span>
              <span className={"model-chip" + (m.labels_count ? "" : " is-warn")}>
                {m.labels_count ? `${m.labels_count} classes` : "no labels"}
              </span>
              {m.folder_name && <span className="model-chip">{m.folder_name}</span>}
              {m.manifest_error ? <span className="model-chip is-warn">bad .json</span>
                : !m.has_manifest && <span className="model-chip is-warn">no .json</span>}
            </div>
            {settingsOpen[m.id] && <>
            <div className="model-card-meta">
              {m.description || (m.manifest_file ? `from ${m.manifest_file}` : "no description")}
              {m.confidence != null ? ` · confidence ${m.confidence}` : ""}
              {m.input?.width ? ` · ${m.input.width}×${m.input.height}` : ""}
            </div>
            {m.manifest_error && (
              <div className="small" style={{ color: "var(--warn-text)", overflowWrap: "anywhere" }}>
                {m.manifest_error}
              </div>
            )}
            <div className="mono muted" style={{ fontSize: 10, overflowWrap: "anywhere" }}>
              {m.path}
            </div>
            </>}
          </div>
          {settingsOpen[m.id] && <div className="filter-bar model-card-actions">
            {/* Same membership semantics as the other card: several models
                share the one camera, so this ADDS or REMOVES rather than
                replacing -- and the last one IS removable. It was disabled
                here too, which is the half of this change that got missed:
                a custom model is exactly the case where a system has only
                one, so the button that could never be clicked was the only
                button on the page. The server refuses an empty set while
                detection is live and says to stop it first. */}
            <button className={"btn" + (isRunning(m) ? "" : " btn-accent")}
                    disabled={!data.editable || !!busy}
                    title={isRunning(m)
                      ? ((activeSet || []).length <= 1
                          ? "Switch this model off - stop detection first if it is running"
                          : "Stop this model; the others keep running")
                      : (activeSet || []).length
                        ? "Run this model as well - both see the same camera"
                        : "Run this model on this system"}
                    onClick={() => onToggle?.(m.name)}>
              {busy === m.id ? "Switching…"
                : isRunning(m) ? "In use · click to stop"
                : (activeSet || []).length ? "Add onto this system"
                : "Use on this system"}
            </button>
            {/* Drop the others and run only this one. */}
            {!isRunning(m) && (activeSet || []).length > 0 && (
              <button className="btn" disabled={!data.editable || !!busy}
                      title="Stop the others and run only this model"
                      onClick={() => use(m)}>
                Only this
              </button>
            )}
            {/* Hold, not a dialog: this deletes a file the operator dropped
                on the box, and a second click lands where the first did.
                The model that is RUNNING cannot be deleted at all -- the
                server refuses it, so a live system stays live. */}
            <HoldToDelete label="Hold to delete" holdLabel="Keep holding…"
                          disabled={!data.editable || !!busy || isRunning(m)}
                          onConfirm={() => removeLocal(m)} />
          </div>}
        </div>
        {/* The settings belong to the model they act on, so they live under
            it rather than in a panel elsewhere on the page. The whole body
            -- meta, buttons, settings -- sits behind the fold at the card's
            foot: chevrons down while shut (the default), up while open. */}
        {settingsOpen[m.id] && isRunning(m) && (
          <div style={{ padding: "0 0 4px 0" }} data-local-settings>
            <DetectorParams params={params} model={m.name} onSaved={load} />
          </div>
        )}
        <CardFold open={!!settingsOpen[m.id]}
                  onToggle={() => setSettingsOpen((o) => ({ ...o, [m.id]: !o[m.id] }))} />
        </div>
      ))}

      {localPages > 1 && (
        <div className="filter-bar" style={{ marginTop: 10, alignItems: "center" }}>
          <button className="btn btn-sm" disabled={page <= 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}>
            ← Previous
          </button>
          <span className="muted small tnum">
            {localStart + 1}–{Math.min(localAll.length, localStart + LOCAL_PAGE)}
            {" "}of {localAll.length}
          </span>
          <button className="btn btn-sm" disabled={page >= localPages - 1}
                  onClick={() => setPage((p) => Math.min(localPages - 1, p + 1))}>
            Next →
          </button>
        </div>
      )}
    </CollapsibleCard>
  );
}

/** Why the class ticks are grey: classes are added or removed only with AI
 *  detection stopped. Points at Camera Setup, where the Stop button is --
 *  that page owns starting and stopping, so it is not duplicated here. */
function ClassLockNote({ reason }) {
  return (
    <p className="muted small" data-class-lock
       style={{ margin: "0 0 6px", color: "var(--warn-text)" }}>
      {reason || "Stop AI detection to add or remove classes"}.{" "}
      <Link to="/detection">Open Camera Setup</Link>
    </p>
  );
}

// ---------------------------------------------------------------------------
// Detection settings - the two knobs that belong to the RUNNING model rather
// than to any one model source: which classes count, and how sure the model
// must be. Works the same whether the model came from the catalogue, an
// upload or the drop folder, because it writes detector config, not model
// metadata. The class list is the active model's own labels, so a 2-class
// board model offers 2 boxes, not the COCO 80.
// ---------------------------------------------------------------------------

function DetectorParams({ params, model, onSaved }) {
  const toast = useToast();
  // THIS model's own settings when several run: each entry of the multi list
  // carries its own classes and confidence. Reading the shared keys instead
  // showed one model the OTHER model's vocabulary -- COCO's 80 classes under
  // a two-class board-inspection model -- and saving there applied the filter
  // to every model at once.
  const entry = model
    ? (params?.models || []).find((e) => e.model === model)
    : null;
  const ownConf = entry?.confidence ?? params?.confidence ?? 0.45;
  const ownClasses = entry?.classes ?? params?.classes ?? [];
  const [conf, setConf] = useState(ownConf);
  const [picked, setPicked] = useState(ownClasses);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [classQuery, setClassQuery] = useState("");

  // Follow the server unless the operator is mid-edit, so a poll never wipes
  // a half-made change.
  const serverKey = JSON.stringify([ownConf, ownClasses, model, params?.model]);
  useEffect(() => {
    if (dirty) return;
    setConf(ownConf);
    setPicked(ownClasses);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey]);

  if (!params) return null;
  const data = params;
  // Classes wait for AI detection to be fully stopped (core/services/
  // class_lock.py); confidence does not. `classes_locked` rides the page's
  // params poll, so the ticks come back by themselves after Stop.
  const locked = !!data.classes_locked;
  const classEdit = data.editable && !locked;
  // Only what THIS model can emit. labels_by_model is keyed by model name;
  // the shared list is the fallback for a single-model system.
  const labels = (model && (params.labels_by_model || {})[model])
    || data.labels || [];

  // Chosen classes first in the list, then the rest -- what is kept stays
  // where it was put, and the search narrows both halves the same way.
  const q = classQuery.trim().toLowerCase();
  const shownLabels = [...labels]
    .sort((a, b) => (picked.includes(b) ? 1 : 0) - (picked.includes(a) ? 1 : 0))
    .filter((c) => !q || c.toLowerCase().includes(q));

  function toggle(c) {
    if (locked) return;
    setDirty(true);
    setPicked((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));
  }

  async function save() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/params",
                               { classes: picked, confidence: conf, model });
      setDirty(false);
      toast.success("Detection settings saved",
                    `Confidence ${d.confidence} · `
                    + (d.classes.length ? `${d.classes.length} class(es)` : "every class")
                    + " - the app applies it within a few seconds.");
      onSaved?.();
    } catch (e) { toast.error("Could not save", e.message); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ border: "1px solid rgba(128,128,128,.25)", borderRadius: 8,
                  padding: 10, background: "rgba(128,128,128,.05)" }}>
      <div className="section-title" style={{ marginTop: 0 }}>
        Detection settings {dirty && <Badge tone="warn">unsaved</Badge>}
      </div>
      <p className="muted small">
        What this model reports. Confidence is enforced on the accelerator, so
        raising it costs nothing.
      </p>

      <label className="muted small" style={{ display: "flex", alignItems: "center",
                                              gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        Minimum confidence
        <input type="range" min="0.05" max="0.95" step="0.05" value={conf}
               disabled={!data.editable}
               onChange={(e) => { setConf(+e.target.value); setDirty(true); }}
               style={{ width: 200 }} />
        <input className="input" type="number" min="0.05" max="0.95" step="0.05"
               style={{ width: 80, flex: "none" }} value={conf} disabled={!data.editable}
               onChange={(e) => { setConf(+e.target.value); setDirty(true); }} />
        <span className="muted">{Math.round(conf * 100)}%</span>
      </label>

      <div className="muted small" style={{ marginTop: 10, marginBottom: 4 }}>
        Classes to keep {picked.length === 0 && <em>(none ticked = every class)</em>}
      </div>
      {locked && data.editable && <ClassLockNote reason={data.classes_locked_reason} />}
      {labels.length === 0 ? (
        <p className="muted small">
          This model ships no labels file, so its classes cannot be listed - every
          detection is kept and named by class number.
        </p>
      ) : (
        <>
          {/* CHOSEN first, as removable chips: with 80 classes in the list
              below, "what did I actually keep" was a hunt through a wall of
              highlighted buttons. */}
          {picked.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5,
                          marginBottom: 6 }}>
              {picked.map((c) => {
                const stray = !labels.includes(c);
                return (
                  <button key={c} type="button" disabled={!classEdit}
                          className="btn"
                          // A filter can carry classes this model does not
                          // emit -- left over from a template or an earlier
                          // model. Invisible they silently narrow detection
                          // for ever; flagged, they are one click to remove.
                          title={stray
                            ? "Not a class of this model - click to remove it"
                            : "Click to remove"}
                          style={{ padding: "2px 9px", fontSize: 12,
                                   ...(stray
                                     ? { borderColor: "#f59e0b", color: "var(--warn-text)" }
                                     : { borderColor: "var(--accent, #04bade)",
                                         color: "var(--accent-text, #04bade)" }) }}
                          onClick={() => toggle(c)}>{c} ✕</button>
                );
              })}
            </div>
          )}
          {/* A searchable LIST, not a wall of buttons: 80 toggles look the
              same from a metre away, and finding "traffic light" among them
              is a scan, not a choice. Typing narrows; the row is the target. */}
          <input className="input" value={classQuery} disabled={!classEdit}
                 placeholder="Search classes…"
                 onChange={(e) => setClassQuery(e.target.value)}
                 style={{ marginBottom: 6 }} />
          <div style={{ border: "1px solid var(--line, rgba(128,128,128,.25))",
                        borderRadius: 8, maxHeight: 190, overflowY: "auto" }}>
            {shownLabels.length === 0 ? (
              <p className="muted small" style={{ margin: 0, padding: "10px 12px" }}>
                No class matches “{classQuery}”.
              </p>
            ) : shownLabels.map((c, i) => {
              const on = picked.includes(c);
              return (
                <label key={c}
                       style={{ display: "flex", alignItems: "center", gap: 8,
                                padding: "6px 10px", cursor: "pointer",
                                fontSize: 13,
                                borderTop: i ? "1px solid var(--line, rgba(128,128,128,.14))"
                                             : "none",
                                background: on ? "rgba(4,186,222,.08)" : "transparent" }}>
                  <input type="checkbox" checked={on} disabled={!classEdit}
                         onChange={() => toggle(c)} />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {c}
                  </span>
                </label>
              );
            })}
          </div>
          <p className="muted small" style={{ margin: "6px 0 0" }}>
            {picked.length
              ? `${picked.length} of ${labels.length} classes kept`
              : `every class kept (${labels.length} available)`}
          </p>
        </>
      )}

      <div className="filter-bar" style={{ marginTop: 10 }}>
        {picked.length > 0 && (
          <button className="btn btn-sm" disabled={!classEdit}
                  onClick={() => { setPicked([]); setDirty(true); }}>Clear classes</button>
        )}
        <div style={{ flex: 1 }} />
        <button className="btn btn-accent" disabled={!data.editable || !dirty || busy}
                onClick={save}>{busy ? "Saving…" : "Save settings"}</button>
      </div>
    </div>
  );
}
