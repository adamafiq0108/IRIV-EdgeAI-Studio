// TemplatesPanel - the hub's library of reusable system blueprints
// (Administration → System Templates, admin only).
//
// A template is what an operator BUILT on one system - its workflow, its
// dashboards, the classes it watches - with the site left out. The panel is
// therefore a file manager, not a settings form: folders on the left are the
// categories (on disk they ARE directories), templates on the right.
//
// Built-ins are shown in the same list as a read-only group. They are a
// different kind of thing (a detection flavour, not a build), but an operator
// looking for "what can I start a system from" wants one list, not two places.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/app.jsx";
import { api } from "../api/client.js";
import { downloadFile } from "../lib/download.js";
import { authHeaders } from "../lib/session.js";
import { useToast } from "./ui/Toast.jsx";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { Label } from "./ui/label.jsx";
import { Textarea } from "./ui/textarea.jsx";
import { Badge } from "./ui/badge.jsx";
import { ConfirmDialog } from "./ui/dialogs.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import {
  Folder, FolderPlus, FolderX, Download, Upload, Pencil, Trash2,
  BookmarkPlus, Lock, FileJson, Plus, PencilRuler, ArrowRightLeft,
  CloudUpload, RefreshCw, Images, ListChecks,
} from "lucide-react";
import TemplateMediaEditor from "./TemplateMediaEditor.jsx";
import TemplateCopyEditor from "./TemplateCopyEditor.jsx";
import { apiUrl } from "../lib/base.js";

//: The category grammar the server enforces (templates.py SEG_RE / MAX_DEPTH).
//: Mirrored here so a typo is an inline message under the field rather than a
//: 400 the operator has to interpret.
const SEG_RE = /^[a-z0-9][a-z0-9_-]{0,40}$/;
const MAX_DEPTH = 3;
const BUILTIN = "built-in";      // never a real folder name

function categoryError(path) {
  const segs = String(path || "").split("/").map((s) => s.trim()).filter(Boolean);
  if (!segs.length) return "Give the folder a name.";
  if (segs.length > MAX_DEPTH) return `A category may be at most ${MAX_DEPTH} folders deep.`;
  for (const seg of segs) {
    if (!SEG_RE.test(seg)) {
      return `“${seg}” may only use lower-case letters, digits, - and _ (and must start with a letter or digit).`;
    }
  }
  return "";
}

// A category is a path parameter, so its separators must survive encoding while
// each segment is still escaped.
function encodePath(path) {
  return String(path || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
}

function templateUrl(category, slug) {
  return apiUrl(`/api/hub/templates/${encodePath(category)}/${encodeURIComponent(slug)}`);
}

export default function TemplatesPanel() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [cards, setCards] = useState([]);
  const [selected, setSelected] = useState(BUILTIN);
  const [busy, setBusy] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [editing, setEditing] = useState(null);       // the template being renamed
  const [removing, setRemoving] = useState(null);     // the template being deleted
  const [folderOpen, setFolderOpen] = useState(false);
  const [dropFolder, setDropFolder] = useState(null); // the folder being deleted
  // The built-in format being authored/edited, or null. "" key = a new one.
  const [formatEdit, setFormatEdit] = useState(null);
  // Which built-in FOLDER is open. Formats are grouped exactly like saved
  // templates, so one page teaches one idea rather than two.
  const [fmtFolder, setFmtFolder] = useState(null);
  const [publishOpen, setPublishOpen] = useState(false);
  // {format key: {slug, exists, url}} - this box's format workbenches.
  const [workbench, setWorkbench] = useState({});
  // The template being cloud-published (tier/token dialog), or null.
  const [cloudFor, setCloudFor] = useState(null);
  const [publishReady, setPublishReady] = useState(null);
  // Publishing the built-ins to the SOURCE repo (not the store): the whole
  // formats.toml, committed to the release branch. `sourceReady` is the
  // status the route reports -- token set or not, where it publishes, what
  // the last publish did. Fetched when the dialog opens.
  const [sourceOpen, setSourceOpen] = useState(false);
  // The template whose "Preview media" editor is open (developer only).
  const [mediaFor, setMediaFor] = useState(null);
  // ...and whose "What you get" words editor.
  const [copyFor, setCopyFor] = useState(null);
  const [sourceReady, setSourceReady] = useState(null);
  const { user } = useApp();
  // Editing the PRODUCT's formats is a developer act. The API enforces it
  // too -- this only decides whether the page offers the controls.
  const isDev = !!user?.is_developer;
  const fileRef = useRef(null);

  /** Create or override one built-in format. */
  async function saveFormat(draft) {
    setBusy(true);
    try {
      await api.put(`/api/hub/formats/${encodeURIComponent(draft.key)}`, {
        label: draft.label,
        hint: draft.hint,
        category: draft.category,
        zone: draft.zone,
        classes: draft.classes,
        model: draft.model ?? "",
      });
      toast.success(`${draft.label} saved`,
                    "Written to config/formats.toml - commit it to ship the format.");
      setFormatEdit(null);
      load();
    } catch (e) { toast.error("Could not save the format", e.message); }
    finally { setBusy(false); }
  }

  /**
   * Open a format's WORKBENCH -- the system it is edited as. Created on first
   * use (it counts toward the box's system limit), reused after.
   */
  async function openWorkbench(t) {
    setBusy(true);
    try {
      const d = await api.post(
        `/api/hub/formats/${encodeURIComponent(t.key)}/workbench`, {});
      if (d.created) {
        toast.success(`${t.label} opened as a system`,
          "Customise it like any system, then press Publish back here.");
        load();
      }
      if (d.url) window.open(d.url, "_blank", "noopener");
      else toast.info("The workbench exists but has no address yet",
                      "Start it from System Control, then try again.");
    } catch (e) { toast.error("Could not open the format's workbench", e.message); }
    finally { setBusy(false); }
  }

  /** Publish a template's workbench to the ONLINE store (GitHub). */
  async function cloudPublish(t, tier, token) {
    setBusy(true);
    try {
      if (token) {
        await api.post("/api/hub/store/publish_token", { token });
      }
      await api.post("/api/hub/store/publish", { key: t.key, tier });
      toast.info(`Publishing ${t.label} to the ${tier} store…`,
                 "Hashing the hosted model builds takes a minute.");
      setCloudFor(null);
      const poll = setInterval(async () => {
        try {
          const d = await api.get(
            `/api/hub/store/publish/${encodeURIComponent(t.key)}`);
          const s = d.status || {};
          if (s.state === "done") {
            clearInterval(poll);
            toast.success(`${t.label} is live in the ${s.tier} store (v${s.version})`,
                          "Boxes see it on their next catalogue refresh.");
          } else if (s.state === "failed") {
            clearInterval(poll);
            toast.error("Cloud publish failed", s.error || "unknown error");
          }
        } catch { /* transient poll miss */ }
      }, 2000);
    } catch (e) { toast.error("Could not start the cloud publish", e.message); }
    finally { setBusy(false); }
  }

  async function publishSource(token) {
    setBusy(true);
    try {
      if (token) {
        await api.post("/api/hub/formats/source/token", { token });
      }
      const r = await api.post("/api/hub/formats/source/publish", {});
      const src = sourceReady?.source;
      const mir = sourceReady?.mirror;
      const where = src ? `${src.repo}@${src.branch}` : "the source repo";
      const mirWhere = mir ? `${mir.repo}@${mir.branch}` : "the release repo";
      // Two commits, reported as two facts. The source commit IS the
      // publish (the next release is built from it); the copy for boxes is
      // what lets Check for template updates fetch it before then. Either
      // can be the one that had nothing to do.
      const fetchable = r.mirror_commit ? `Boxes can fetch them now (${r.mirror_commit} in ${mirWhere}).`
        : r.mirror_unchanged ? `${mirWhere} already held the copy for boxes.` : "";
      if (r.unchanged && (r.mirror_unchanged || r.mirror_error)) {
        toast.info("Templates already published",
                   `${where} already holds exactly these ${r.refreshed.length} template(s).`);
      } else if (r.unchanged) {
        toast.success("Copy for boxes published",
                      `${where} already held these templates; ${fetchable}`);
      } else {
        toast.success(`Published ${r.refreshed.length} template(s)`,
                      `Committed ${r.commit} to ${where}. The next release carries them. ${fetchable}`.trim());
      }
      // The copy for boxes can fail on its own: no signing key on this hub,
      // or a token that does not cover the release repo. Said separately,
      // because "published" would otherwise mean "and every box can fetch
      // it", which is exactly what did not happen.
      // Preview media rides the same signed copy, and says what it did.
      if (r.media?.error) {
        toast.warn("Preview media was not sent", r.media.error);
      } else if (r.media) {
        toast.info("Preview media",
                   r.media.uploaded ? `${r.media.uploaded} new file(s) sent with the templates (${r.media.files} in all).`
                     : `${r.media.files} file(s), already published.`);
      }
      if (r.mirror_error) {
        toast.warn("Boxes cannot fetch the templates yet",
                   `${r.mirror_error}. Committed to ${where}, but not to `
                   + `${mirWhere}, which is where Check for template updates reads.`);
      }
      if (r.errors?.length) {
        toast.error(`${r.errors.length} template(s) could not be refreshed`,
                    r.errors.map((e) => `${e.slug}: ${e.error}`).join("\n"));
      }
      setSourceOpen(false);
      await load();
    } catch (e) {
      toast.error("Could not publish the templates", e.message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * Fetch the templates the developer has published and install the new and
   * changed ones here -- the same button the create wizard has. On the
   * developer's own hub most templates are AUTHORED here and are kept as
   * they are; the answer says so rather than reporting nothing.
   */
  const [checking, setChecking] = useState(false);
  async function checkUpdates() {
    setChecking(true);
    try {
      const d = await api.post("/api/hub/templates/update", {});
      const names = (list) => (list || []).map((t) => t.label || t.key).join(", ");
      const n = (d.added || []).length + (d.updated || []).length;
      if (d.authoring) {
        // This hub is where the templates come from: nothing installs here,
        // the check reports what differs so the developer knows whether to
        // press Publish templates.
        const parts = [
          d.differs?.length ? `${names(d.differs)} differ${d.differs.length === 1 ? "s" : ""} from the published copy.` : "",
          d.unpublished?.length ? `Not published yet: ${names(d.unpublished)}.` : "",
          d.removed_here?.length ? `Published but removed here: ${names(d.removed_here)}.` : "",
          d.media?.differs?.length ? `Preview media differs for ${d.media.differs.join(", ")}.` : "",
        ].filter(Boolean);
        if (parts.length) {
          toast.info("This hub authors the templates",
                     parts.join(" ") + " Press Publish templates to send what is here.");
        } else {
          toast.success("Published templates match this hub",
                        "Every template here is what other boxes fetch.");
        }
      } else if (n) {
        toast.success(`${n} template${n === 1 ? "" : "s"} installed`,
          [(d.added || []).length ? `New: ${names(d.added)}.` : "",
           (d.updated || []).length ? `Updated: ${names(d.updated)}.` : "",
           "Systems already created are unchanged."].filter(Boolean).join(" "));
      } else {
        toast.info("Templates are up to date",
          (d.kept || []).length
            ? `${names(d.kept)}: authored on this box, kept as ${d.kept.length === 1 ? "it is" : "they are"}.`
            : undefined);
      }
      for (const e of d.errors || []) toast.warn(`${e.key} could not be installed`, e.error);
      load();
    } catch (e) { toast.error("Could not check for template updates", e.message); }
    finally { setChecking(false); }
  }

  /** Move a template between drafts (developer-only) and built-in (public). */
  async function moveDraft(t) {
    setBusy(true);
    try {
      await api.post(`/api/hub/formats/${encodeURIComponent(t.key)}/draft`,
                     { draft: !t.draft });
      toast.success(t.draft
        ? `${t.label} moved to built-in - admins now see it in the create picker`
        : `${t.label} moved to drafts - only developers see it now`);
      load();
    } catch (e) { toast.error("Could not move the template", e.message); }
    finally { setBusy(false); }
  }

  /** Write the workbench's dashboard/workflow/classes back into the format. */
  async function publishBack(t) {
    setBusy(true);
    try {
      await api.post(
        `/api/hub/formats/${encodeURIComponent(t.key)}/publish_back`, {});
      toast.success(`${t.label} updated from its workbench`,
        "Written to config/formats.toml - commit it to ship the format.");
      load();
    } catch (e) { toast.error("Could not publish the format", e.message); }
    finally { setBusy(false); }
  }

  /**
   * Remove a format. `hide` distinguishes the two things "delete" can mean on
   * a SHIPPED one: discard my edits (false), or stop offering it at all (true).
   */
  async function dropFormat(t, hide) {
    setBusy(true);
    try {
      const d = await api.del(
        `/api/hub/formats/${encodeURIComponent(t.key)}?hide=${hide ? "true" : "false"}`);
      toast.info(d.hidden ? `${t.label} hidden`
                 : d.reverted ? `${t.label} reverted to the shipped version`
                              : `${t.label} deleted`,
                 d.hidden ? "The shipped format is still in the code - Restore brings it back."
                          : undefined);
      load();
    } catch (e) { toast.error("Could not remove the format", e.message); }
    finally { setBusy(false); }
  }

  /** Publish a working system as a built-in format. */
  async function publishSystem({ from_slug, key, label, hint, category }) {
    setBusy(true);
    try {
      await api.post("/api/hub/formats/publish",
                     { from_slug, key, label, hint, category });
      toast.success(`${label || key} published`,
                    "Written to config/formats.toml - commit it to ship the format.");
      setPublishOpen(false);
      load();
    } catch (e) { toast.error("Could not publish the format", e.message); }
    finally { setBusy(false); }
  }

  const load = useCallback(async () => {
    try {
      setData(await api.get("/api/hub/templates"));
    } catch (e) {
      toast.error("Could not load the templates", e.message);
    }
    // The save dialog needs the list of systems to snapshot.
    try {
      const hub = await api.get("/api/hub");
      setCards(hub?.cards || []);
    } catch { /* the panel still works without it; the dialog says so */ }
    // Which formats have a workbench system on this box (dev editing flow).
    try {
      const f = await api.get("/api/hub/formats");
      setWorkbench(f?.workbench || {});
    } catch { /* non-developers just never see the workbench buttons */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { load(); }, [load]);

  const builtins = data?.templates || [];
  const saved = data?.saved || [];

  // Every folder that exists, plus every folder a template claims to be in -
  // the two agree in practice, but a listing that hid a template because its
  // folder was missed would be the worst possible failure here.
  const categories = useMemo(() => {
    const set = new Set(data?.categories || []);
    for (const t of saved) if (t.category) set.add(t.category);
    return [...set].sort();
  }, [data, saved]);

  // Built-ins grouped by their category, and the folders that exist.
  const fmtCategories = useMemo(() => {
    const set = new Set(builtins.map((t) => t.category || "built-in"));
    for (const c of data?.format_categories || []) set.add(c);
    return [...set].sort();
  }, [builtins, data]);
  const inFmtFolder = useMemo(
    () => builtins.filter((t) => (t.category || "built-in") === (fmtFolder || "built-in")),
    [builtins, fmtFolder]);

  const inCategory = useMemo(
    () => saved.filter((t) => t.category === selected),
    [saved, selected]);

  async function exportTemplate(t) {
    try {
      await downloadFile(`${templateUrl(t.category, t.slug)}/export`, `template-${t.slug}.json`);
    } catch (e) {
      toast.error("Could not export the template", e.message);
    }
  }

  async function importTemplate(e) {
    const file = e.target.files?.[0];
    e.target.value = "";                       // same file re-pickable later
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      // api/client.js speaks JSON only; multipart goes through fetch directly.
      const res = await fetch(apiUrl("/api/hub/templates/import"), {
        method: "POST", credentials: "same-origin", body: fd,
        headers: { ...authHeaders() },
      });
      const ct = res.headers.get("content-type") || "";
      const d = ct.includes("application/json") ? await res.json().catch(() => null) : null;
      if (!res.ok) throw new Error((d && (d.error || d.detail)) || res.statusText || `HTTP ${res.status}`);
      toast.success(`${d.name} imported`, `Filed under ${d.category}.`);
      setSelected(d.category);
      await load();
    } catch (err) {
      toast.error("Could not import the template", err.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveTemplate(values) {
    setBusy(true);
    try {
      const d = await api.post("/api/hub/templates", values);
      toast.success(`${d.name} saved as a template`,
        "New systems can start from it - pick it in the create wizard.");
      setSaveOpen(false);
      setSelected(d.category);
      await load();
    } catch (e) {
      toast.error("Could not save the template", e.message);
    } finally {
      setBusy(false);
    }
  }

  async function updateTemplate(values) {
    setBusy(true);
    try {
      const d = await api.put(templateUrl(editing.category, editing.slug), values);
      toast.success("Template updated");
      setEditing(null);
      setSelected(d.category);
      await load();
    } catch (e) {
      toast.error("Could not update the template", e.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteTemplate() {
    setBusy(true);
    try {
      await api.del(templateUrl(removing.category, removing.slug));
      toast.info(`${removing.name} deleted`);
      setRemoving(null);
      await load();
    } catch (e) {
      toast.error("Could not delete the template", e.message);
    } finally {
      setBusy(false);
    }
  }

  async function createFolder(path) {
    setBusy(true);
    try {
      const d = await api.post("/api/hub/template-folders", { path });
      toast.success(`Folder ${d.path} created`);
      setFolderOpen(false);
      setSelected(d.path);
      await load();
    } catch (e) {
      toast.error("Could not create the folder", e.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteFolder() {
    setBusy(true);
    try {
      await api.del(`/api/hub/template-folders/${encodePath(dropFolder)}`);
      toast.info(`Folder ${dropFolder} removed`);
      setDropFolder(null);
      setSelected(BUILTIN);
      await load();
    } catch (e) {
      // The usual refusal is "that folder still has templates in it" - the
      // server's wording is the instruction, so it is shown verbatim.
      toast.error("Could not remove the folder", e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="rounded-xl border bg-card p-5">
        <p className="font-medium">System templates</p>
        <p className="mt-1 text-sm text-muted-foreground">
          A saved system - workflow and dashboards included, cameras and zones not.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setSaveOpen(true)} disabled={busy}>
          <BookmarkPlus className="size-4" /> Save a system as a template
        </Button>
        <Button variant="outline" onClick={() => setFolderOpen(true)} disabled={busy}>
          <FolderPlus className="size-4" /> New folder
        </Button>
        <input ref={fileRef} type="file" accept="application/json,.json"
               className="hidden" onChange={importTemplate} />
        <Button variant="outline" disabled={busy} onClick={() => fileRef.current?.click()}>
          <Upload className="size-4" /> Import template
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
        {/* ---- folders ---- */}
        <div className="rounded-xl border bg-card p-2">
          {/* One row per built-in folder, not one row for all of them: a
              developer who files formats into Manufacturing and Retail wants
              to see those, the same way saved templates get their folders. */}
          {fmtCategories.map((c) => (
            <FolderButton
              key={"fmt:" + c}
              active={selected === BUILTIN && (fmtFolder || "built-in") === c}
              onClick={() => { setSelected(BUILTIN); setFmtFolder(c); }}
              icon={Lock}
              label={c}
              count={builtins.filter((t) => (t.category || "built-in") === c).length}
            />
          ))}
          <div className="my-2 h-px bg-border" />
          {categories.length === 0 && (
            <p className="px-2 py-3 text-xs text-muted-foreground">
              No folders yet - save a system as a template and one is made for it.
            </p>
          )}
          {categories.map((c) => (
            <FolderButton
              key={c}
              active={selected === c}
              onClick={() => setSelected(c)}
              icon={Folder}
              label={c}
              count={saved.filter((t) => t.category === c).length}
            />
          ))}
        </div>

        {/* ---- templates in the selected folder ---- */}
        <div className="space-y-3">
          {selected === BUILTIN ? (
            <>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold">Built-in templates</h2>
                {/* Read-only is a fact about the ACCOUNT, not about the
                    formats: they are product content, and the developer is the
                    person who authors product content. */}
                {isDev
                  ? <Badge variant="secondary">developer</Badge>
                  : <Badge variant="secondary">read-only</Badge>}
                <div className="ml-auto flex flex-wrap gap-2">
                  {/* Pull what the developer has published between
                      releases. Developer tier only: a customer's templates
                      arrive with each software update. */}
                  {isDev && (
                    <Button variant="outline" size="sm" disabled={busy || checking}
                            title="Fetch the templates published since this box was installed"
                            onClick={checkUpdates}>
                      <RefreshCw className={"size-4" + (checking ? " animate-spin" : "")} />
                      {checking ? "Checking…" : "Check for updates"}
                    </Button>
                  )}
                  {isDev && (
                    <>
                      {/* The real way a built-in gets authored: build a system,
                          then publish it. A format's job is to be a good
                          STARTING POINT, and the only way to know it is one is
                          to have built with it. */}
                      {/* The last step of authoring: every system in the
                          "Built-in templates" folder is re-snapshotted and
                          config/formats.toml is committed to the source repo,
                          so the next release -- and every box that takes it --
                          offers exactly these templates. */}
                      <Button variant="outline" size="sm" disabled={busy}
                              onClick={async () => {
                                setSourceOpen(true);
                                try {
                                  setSourceReady(await api.get("/api/hub/formats/source"));
                                } catch (e) {
                                  toast.error("Could not read the publish settings", e.message);
                                }
                              }}>
                        <CloudUpload className="size-4" /> Publish templates
                      </Button>
                      <Button variant="outline" size="sm" disabled={busy}
                              onClick={() => setPublishOpen(true)}>
                        <BookmarkPlus className="size-4" /> Publish a system
                      </Button>
                      <Button size="sm" disabled={busy}
                              onClick={() => setFormatEdit({
                                key: "", label: "", hint: "",
                                category: fmtFolder || "built-in",
                                zone: { mode: "optional", hint: "" }, classes: [],
                                model: "yolov8n" })}>
                        <Plus className="size-4" /> New template
                      </Button>
                    </>
                  )}
                </div>
              </div>
              <p className="text-sm text-muted-foreground">
                What a new system starts from - classes, starter dashboard,
                workflow.
                {isDev
                  ? " A draft is yours alone; Move to built-in offers it to everyone in the create picker. Edits write config/formats.toml, which is committed and ships."
                  : " Shipped with the platform; only a developer account can change them."}
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {inFmtFolder.map((t) => (
                  <div key={t.key} className="rounded-lg border bg-card p-3">
                    <div className="flex items-start gap-2">
                      <p className="min-w-0 flex-1 text-sm font-medium">{t.label}</p>
                      {t.draft && <Badge variant="outline">draft</Badge>}
                      {t.edited && <Badge variant="secondary">edited</Badge>}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t.hint}</p>
                    <p className="mt-2 font-mono text-[11px] text-muted-foreground">{t.key}</p>
                    {isDev && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {/* Editing a format means BUILDING with it: Edit opens
                            (or creates) its workbench system, where the
                            dashboard, workflow and classes are customised the
                            way any system is. Publish back writes the result
                            into the format. The dialog remains, as Details,
                            for the words: label, folder, zone guidance. */}
                        <Button variant="outline" size="sm" disabled={busy}
                                title="Open this format as a system and customise it"
                                onClick={() => openWorkbench(t)}>
                          <PencilRuler className="size-4" /> Edit in system
                        </Button>
                        {workbench[t.key]?.exists && (
                          <Button variant="outline" size="sm" disabled={busy}
                                  title="Write the workbench system's dashboard, workflow and classes into this template"
                                  onClick={() => publishBack(t)}>
                            <BookmarkPlus className="size-4" /> Publish back
                          </Button>
                        )}
                        {workbench[t.key]?.exists && (
                          <Button variant="outline" size="sm" disabled={busy}
                                  title="Push this template to the online store on GitHub"
                                  onClick={async () => {
                                    setCloudFor(t);
                                    try {
                                      setPublishReady(await api.get("/api/hub/store/publish_token"));
                                    } catch { setPublishReady(null); }
                                  }}>
                            <CloudUpload className="size-4" /> Publish to cloud
                          </Button>
                        )}
                        {/* The move between the developer's drafts and the
                            product's built-ins. Only built-ins are offered to
                            admins in the create picker. */}
                        <Button variant="outline" size="sm" disabled={busy}
                                title={t.draft
                                  ? "Offer this template to everyone in the create picker"
                                  : "Pull this template back to drafts - only you will see it"}
                                onClick={() => moveDraft(t)}>
                          <ArrowRightLeft className="size-4" />
                          {t.draft ? "Move to built-in" : "Move to drafts"}
                        </Button>
                        {/* The pictures and video the Hub home's preview
                            shows for this template; sent with Publish
                            templates. */}
                        <Button variant="outline" size="sm" disabled={busy}
                                title="Pictures and a video shown in this template's preview"
                                onClick={() => setMediaFor({ key: t.key, label: t.label })}>
                          <Images className="size-4" /> Preview media
                        </Button>
                        {/* The preview's own words: the What you get lines and
                            the sentence under the title; empty = automatic. */}
                        <Button variant="outline" size="sm" disabled={busy}
                                title="Write the What you get lines shown in this template's preview"
                                onClick={() => setCopyFor({ key: t.key, label: t.label })}>
                          <ListChecks className="size-4" /> Description
                        </Button>
                        <Button variant="ghost" size="sm" disabled={busy}
                                onClick={() => setFormatEdit({
                                  key: t.key, label: t.label, hint: t.hint || "",
                                  category: t.category || "built-in",
                                  zone: { mode: t.zone?.mode || "optional",
                                          hint: t.zone?.hint || "" },
                                  classes: t.classes || [],
                                  model: t.model ?? "",
                                  shipped: t.shipped, edited: t.edited,
                                })}>
                          Details
                        </Button>
                        {t.edited && t.shipped && (
                          <Button variant="ghost" size="sm" disabled={busy}
                                  title="Discard your changes and go back to the shipped version"
                                  onClick={() => dropFormat(t, false)}>
                            Revert
                          </Button>
                        )}
                        {/* Deleting a SHIPPED format writes a tombstone -- the
                            code cannot be edited from a board, so hiding it is
                            the only honest delete. Restore brings it back. */}
                        <Button variant="ghost" size="sm" disabled={busy}
                                className="text-destructive"
                                title={t.shipped
                                  ? "Stop offering this format (the shipped one can be restored)"
                                  : "Delete this authored format"}
                                onClick={() => dropFormat(t, t.shipped)}>
                          Delete
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">{selected}</h2>
                <span className="text-xs text-muted-foreground">
                  {inCategory.length} template{inCategory.length === 1 ? "" : "s"}
                </span>
                <Button className="ml-auto" variant="ghost" size="sm" disabled={busy}
                        onClick={() => setDropFolder(selected)}>
                  <FolderX className="size-4" /> Delete folder
                </Button>
              </div>

              {inCategory.length === 0 ? (
                <div className="rounded-xl border border-dashed bg-card/50 p-8 text-center">
                  <FileJson className="mx-auto size-6 text-muted-foreground" />
                  <p className="mt-2 text-sm font-medium">Nothing filed here yet</p>
                  <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
                    Use “Save a system as a template” to capture a system you have already
                    built - its workflow, dashboards and class filter - then create the next
                    system from it instead of starting empty.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {inCategory.map((t) => (
                    <div key={t.slug} className="flex flex-wrap items-start gap-3 rounded-lg border bg-card p-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{t.name}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {t.description || "No description."}
                        </p>
                        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                          {t.category}/{t.slug}
                          {t.modified ? ` · ${new Date(t.modified * 1000).toLocaleString()}` : ""}
                        </p>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" size="sm" disabled={busy}
                                onClick={() => exportTemplate(t)} title="Download as a file">
                          <Download className="size-4" />
                        </Button>
                        <Button variant="ghost" size="sm" disabled={busy}
                                onClick={() => setEditing(t)} title="Rename or move">
                          <Pencil className="size-4" />
                        </Button>
                        <Button variant="ghost" size="sm" disabled={busy}
                                onClick={() => setRemoving(t)} title="Delete">
                          <Trash2 className="size-4 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <SaveTemplateDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        cards={cards}
        categories={categories}
        busy={busy}
        onSubmit={saveTemplate}
      />

      <EditTemplateDialog
        template={editing}
        onOpenChange={(o) => !o && setEditing(null)}
        categories={categories}
        busy={busy}
        onSubmit={updateTemplate}
      />

      <FolderDialog
        open={folderOpen}
        onOpenChange={setFolderOpen}
        busy={busy}
        onSubmit={createFolder}
      />

      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={removing ? `Delete “${removing.name}”?` : ""}
        description="The template file is removed. Systems already created from it are untouched."
        confirmLabel="Delete"
        destructive
        busy={busy}
        onConfirm={deleteTemplate}
      />

      {/* Publishing a template ONLINE: its workbench system becomes a signed
          store bundle pushed straight to the GitHub store repo. */}
      {cloudFor && (
        <CloudPublishDialog
          open
          onOpenChange={(v) => { if (!v) setCloudFor(null); }}
          template={cloudFor}
          ready={publishReady}
          busy={busy}
          onSubmit={(tier, token) => cloudPublish(cloudFor, tier, token)}
        />
      )}
      <SourcePublishDialog
        open={sourceOpen}
        onOpenChange={(v) => { if (!v) setSourceOpen(false); }}
        ready={sourceReady}
        busy={busy}
        count={builtins.filter((t) => !t.draft).length}
        onSubmit={publishSource}
      />

      {/* Publishing a WORKING system as a format. This is the round trip the
          product is built around: create a system from a format, set it up
          until it is right, publish it back. What travels is the same
          snapshot "save as template" takes -- layout, workflow, class filter
          -- so no camera source, credential or recording follows it. */}
      {publishOpen && (
        <PublishFormatDialog
          open
          onOpenChange={setPublishOpen}
          cards={cards}
          categories={fmtCategories}
          busy={busy}
          onSubmit={publishSystem}
        />
      )}

      {/* Authoring a built-in format. Only the fields that make a format a
          FORMAT are editable here -- label, hint, zone requirement, seed
          classes. The starter dashboard's widgets stay as authored in code (or
          carried over from the shipped format), because composing a dashboard
          already has a page of its own: the Builder. */}
      {formatEdit && (
        <ConfirmDialog
          open
          onOpenChange={(o) => { if (!o) setFormatEdit(null); }}
          title={formatEdit.key ? `Edit ${formatEdit.key}` : "New built-in template"}
          description={"Built-in templates are product content: this writes "
            + "config/formats.toml, which is committed on purpose so the template "
            + "ships to the next deployment."}
          confirmLabel={busy ? "Saving…" : "Save template"}
          busy={busy}
          onConfirm={() => saveFormat(formatEdit)}
        >
          <div className="grid gap-3">
            {!formatEdit.shipped && (
              <div className="grid gap-1.5">
                <Label htmlFor="fmt-key">Key</Label>
                <Input id="fmt-key" value={formatEdit.key}
                       placeholder="conveyor_qa"
                       disabled={!!formatEdit.edited && !!formatEdit.key}
                       onChange={(e) => setFormatEdit({
                         ...formatEdit,
                         key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ""),
                       })} />
                <p className="text-xs text-muted-foreground">
                  Lowercase, letters/digits/underscores. This is the permanent id -
                  a system records which format it was built from.
                </p>
              </div>
            )}
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-label">Label</Label>
              <Input id="fmt-label" value={formatEdit.label}
                     placeholder="Conveyor QA"
                     onChange={(e) => setFormatEdit({ ...formatEdit, label: e.target.value })} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-folder">Folder</Label>
              <CategoryField id="fmt-folder"
                             value={formatEdit.category || "built-in"}
                             categories={fmtCategories}
                             onChange={(v) => setFormatEdit({ ...formatEdit, category: v })}
                             hint="Groups this format in the sidebar. A new name makes a new folder." />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-hint">Description</Label>
              <Textarea id="fmt-hint" rows={2} value={formatEdit.hint}
                        placeholder="What this format is for, in one line."
                        onChange={(e) => setFormatEdit({ ...formatEdit, hint: e.target.value })} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-mode">Zone requirement</Label>
              <select id="fmt-mode"
                      className="h-9 rounded-md border bg-background px-2 text-sm"
                      value={formatEdit.zone.mode}
                      onChange={(e) => setFormatEdit({
                        ...formatEdit,
                        zone: { ...formatEdit.zone, mode: e.target.value },
                      })}>
                <option value="none">none - count everything in frame</option>
                <option value="area">area - a violation happens inside a drawn area</option>
                <option value="line">line - items are counted crossing a line</option>
                <option value="area_line">area + line - both are needed</option>
                <option value="optional">optional - works with or without zones</option>
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-zone-hint">Zone instruction</Label>
              <Input id="fmt-zone-hint" value={formatEdit.zone.hint}
                     placeholder="Draw a line across the belt."
                     onChange={(e) => setFormatEdit({
                       ...formatEdit,
                       zone: { ...formatEdit.zone, hint: e.target.value },
                     })} />
              <p className="text-xs text-muted-foreground">
                Shown on the Detection page to whoever sets the system up.
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-classes">Classes to seed</Label>
              <Input id="fmt-classes"
                     value={(formatEdit.classes || []).join(", ")}
                     placeholder="person, forklift"
                     onChange={(e) => setFormatEdit({
                       ...formatEdit,
                       classes: e.target.value.split(",").map((c) => c.trim()).filter(Boolean),
                     })} />
              <p className="text-xs text-muted-foreground">
                The detector's starting class filter. Comma separated.
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="fmt-model">Detection model</Label>
              <Input id="fmt-model"
                     value={formatEdit.model ?? ""}
                     placeholder="yolov8n"
                     onChange={(e) => setFormatEdit({
                       ...formatEdit, model: e.target.value.trim().toLowerCase(),
                     })} />
              <p className="text-xs text-muted-foreground">
                Preselected on every system made from this template, and
                downloaded automatically - so an operator picks a camera and
                runs. Leave blank for a bring-your-own-model template.
              </p>
            </div>
          </div>
        </ConfirmDialog>
      )}

      <ConfirmDialog
        open={!!dropFolder}
        onOpenChange={(o) => !o && setDropFolder(null)}
        title={dropFolder ? `Remove the folder “${dropFolder}”?` : ""}
        description="Only an empty folder can be removed - move or delete its templates first."
        confirmLabel="Remove folder"
        destructive
        busy={busy}
        onConfirm={deleteFolder}
      />

      {isDev && mediaFor && (
        <TemplateMediaEditor template={mediaFor} onClose={() => setMediaFor(null)} />
      )}
      {isDev && copyFor && (
        <TemplateCopyEditor template={copyFor} onClose={() => setCopyFor(null)} />
      )}
    </div>
  );
}

function FolderButton({ active, onClick, icon: Icon, label, count }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors " +
        (active ? "bg-primary/10 font-medium text-primary" : "hover:bg-accent")
      }
    >
      <Icon className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="text-xs text-muted-foreground">{count}</span>
    </button>
  );
}

// A category field that validates as you type. Shared by every dialog here so
// the same rule is stated the same way wherever a folder is named.
/**
 * Publish the built-in templates to the SOURCE repository.
 *
 * Not the store. Built-ins are part of the product: the folder's snapshots
 * are refreshed and config/formats.toml is committed to the release branch, so
 * they travel with the next release the way code does -- no signing of their
 * own, no per-box install. The first publish needs a GitHub token with
 * Contents: write on that one repo, stored once on this hub, never shown back.
 */
function SourcePublishDialog({ open, onOpenChange, ready, busy, count, onSubmit }) {
  const [token, setToken] = useState("");
  // A stored token is never shown back, so the field is hidden once one is
  // set -- which left no way to REPLACE one pasted wrong except a shell on
  // the box. "Use a different token" brings the field back; a token typed
  // there is stored before the publish runs, same as the first time.
  const [replacing, setReplacing] = useState(false);
  const needsToken = ready ? (!ready.token || replacing) : false;
  const src = ready?.source;
  const mir = ready?.mirror;
  const last = ready?.last;
  useEffect(() => { if (!open) { setToken(""); setReplacing(false); } }, [open]);

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Publish the built-in templates"
      description={"Every system in the \"Built-in templates\" folder is snapshotted "
        + "again - workflow, dashboard, zones, classes, model - and "
        + "config/formats.toml is committed to the source repository. The next "
        + "release is built from that branch, so every box that updates offers "
        + "exactly these templates. The same file, signed with this hub's store "
        + "key, is also placed in the release repository, where every box's "
        + "Check for template updates reads it - so boxes can take the templates "
        + "before the next release. Nothing is committed until this button is "
        + "pressed."}
      confirmLabel={busy ? "Publishing…" : `Publish ${count} template${count === 1 ? "" : "s"}`}
      busy={busy || !ready || (needsToken && !token.trim())}
      onConfirm={() => onSubmit(token.trim())}
    >
      <div className="grid gap-3">
        {src && (
          <p className="text-xs text-muted-foreground">
            Commits <span className="font-mono">{src.path}</span> to{" "}
            <span className="font-mono">{src.owner}/{src.repo}</span> on branch{" "}
            <span className="font-mono">{src.branch}</span>
            {mir ? (
              <>
                , and <span className="font-mono">{mir.path}</span> to{" "}
                <span className="font-mono">{mir.owner}/{mir.repo}</span> on branch{" "}
                <span className="font-mono">{mir.branch}</span> for boxes to fetch.
              </>
            ) : "."}
          </p>
        )}
        {ready && !ready.signing && (
          <p className="text-xs text-amber-600 dark:text-amber-500">
            This hub has no signing key (config/store_signing.pem). The templates
            are committed to the source repository, but the copy boxes fetch is
            signed with that key and will not be written until it is installed
            here - boxes install nothing unsigned.
          </p>
        )}
        {last && (
          <p className="text-xs text-muted-foreground">
            Last publish: {last.ok
              ? ([last.commit ? `commit ${last.commit}` : "",
                  last.mirror_commit ? `copy for boxes ${last.mirror_commit}` : ""]
                  .filter(Boolean).join(", ") || "nothing had changed")
              : `failed - ${last.error}`}
            {last.ok && last.mirror_error ? ` (the copy for boxes was not written: ${last.mirror_error})` : ""}
            {" "}({new Date(last.at * 1000).toLocaleString()}).
          </p>
        )}
        {ready?.token && !replacing && (
          <p className="text-xs text-muted-foreground">
            A publish token is stored on this hub.{" "}
            <button type="button" onClick={() => setReplacing(true)}
                    className="font-medium text-primary underline underline-offset-2">
              Use a different token
            </button>
            {" "}if it was pasted wrong or has been rotated.
          </p>
        )}
        {needsToken && (
          <div className="grid gap-1.5">
            <Label htmlFor="source-token">
              {replacing ? "New GitHub token for publishing" : "GitHub token for publishing"}
            </Label>
            <Input id="source-token" value={token} autoComplete="off"
                   className="font-mono" placeholder="github_pat_…"
                   onChange={(e) => setToken(e.target.value)} />
            <p className="text-xs text-muted-foreground">
              Fine-grained, Contents: read and write on
              {" "}<span className="font-mono">{src?.repo || "the source repo"}</span>
              {" "}and <span className="font-mono">{mir?.repo || "the release repo"}</span>
              {" "}only. Stored once on this hub (config/source_publish.local.toml),
              never shown back.
            </p>
          </div>
        )}
      </div>
    </ConfirmDialog>
  );
}

/**
 * Publish a template's workbench to the ONLINE store (GitHub).
 *
 * Tier decides the repo: free is the public catalogue every box sees,
 * pro is the private one behind a license key. The first publish also needs
 * a GitHub token with write access to the store repos, stored once on this
 * hub (never shown back).
 */
function CloudPublishDialog({ open, onOpenChange, template, ready, busy, onSubmit }) {
  const [tier, setTier] = useState("free");
  const [token, setToken] = useState("");
  // Same escape hatch as the source publish dialog: a stored token is never
  // shown back, so replacing a wrong one needs a way to bring the field back.
  const [replacing, setReplacing] = useState(false);
  const needsToken = ready ? (!ready.token || replacing) : false;
  const blocked = ready && !ready.signing;
  useEffect(() => { if (!open) { setToken(""); setReplacing(false); } }, [open]);

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Publish ${template.label} to the cloud`}
      description={"The workbench system is exported, signed and pushed to the "
        + "store repo on GitHub. Model weights are not uploaded - the manifest "
        + "points at the hosted zoo builds for each Hailo chip."}
      confirmLabel={busy ? "Publishing…" : "Publish online"}
      busy={busy || blocked || (needsToken && !token.trim())}
      onConfirm={() => onSubmit(tier, token.trim())}
    >
      <div className="grid gap-3">
        {blocked && (
          <p className="text-xs text-amber-600 dark:text-amber-500">
            This hub has no signing key (config/store_signing.pem), so it
            cannot publish online.
          </p>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor="cloud-tier">Store tier</Label>
          <select id="cloud-tier" value={tier}
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  onChange={(e) => setTier(e.target.value)}>
            <option value="free">free - every box sees it, no key needed</option>
            <option value="pro">pro - only boxes with a live license key</option>
          </select>
        </div>
        {ready?.token && !replacing && (
          <p className="text-xs text-muted-foreground">
            A store publish token is stored on this hub.{" "}
            <button type="button" onClick={() => setReplacing(true)}
                    className="font-medium text-primary underline underline-offset-2">
              Use a different token
            </button>
          </p>
        )}
        {needsToken && (
          <div className="grid gap-1.5">
            <Label htmlFor="cloud-token">
              {replacing ? "New GitHub publish token" : "GitHub publish token"}
            </Label>
            <Input id="cloud-token" value={token} autoComplete="off"
                   className="font-mono" placeholder="github_pat_…"
                   onChange={(e) => setToken(e.target.value)} />
            <p className="text-xs text-muted-foreground">
              A token with write access to the store repos. Stored once on
              this hub, never shown back.
            </p>
          </div>
        )}
      </div>
    </ConfirmDialog>
  );
}

/**
 * Publish a system as a built-in format.
 *
 * Deliberately asks for a KEY separate from the label: the key is the
 * permanent id a created system records as the format it came from, so it must
 * outlive any renaming of the label.
 */
function PublishFormatDialog({ open, onOpenChange, cards, categories, busy, onSubmit }) {
  const [slug, setSlug] = useState("");
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [hint, setHint] = useState("");
  const [category, setCategory] = useState("built-in");

  useEffect(() => {
    if (!open) { setSlug(""); setKey(""); setLabel(""); setHint(""); }
  }, [open]);

  // Seed the key and label from the chosen system, but let either be changed:
  // a format's name is rarely the name of the one system it came from.
  function pick(s) {
    setSlug(s);
    const card = cards.find((c) => c.slug === s);
    if (!key) setKey(String(s || "").toLowerCase().replace(/[^a-z0-9_]/g, "_"));
    if (!label) setLabel(card?.name || s);
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Publish a system as a built-in template"
      description={"The system's dashboard, workflow and class filter become the "
        + "template's starting point (it lands in drafts first). Camera sources, "
        + "credentials and recorded data are not included."}
      confirmLabel={busy ? "Publishing…" : "Publish format"}
      busy={busy || !slug || !key || !label}
      onConfirm={() => onSubmit({ from_slug: slug, key, label, hint, category })}
    >
      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="pub-system">System to publish</Label>
          <select id="pub-system" value={slug}
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  onChange={(e) => pick(e.target.value)}>
            <option value="">- choose a system -</option>
            {cards.map((c) => (
              <option key={c.slug} value={c.slug}>{c.name || c.slug}</option>
            ))}
          </select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="pub-key">Format key</Label>
          <Input id="pub-key" value={key} placeholder="conveyor_qa"
                 onChange={(e) => setKey(
                   e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ""))} />
          <p className="text-xs text-muted-foreground">
            The permanent id. A system records which format it was built from,
            so this should not change once anything uses it.
          </p>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="pub-label">Label</Label>
          <Input id="pub-label" value={label} placeholder="Conveyor QA"
                 onChange={(e) => setLabel(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="pub-hint">Description</Label>
          <Textarea id="pub-hint" rows={2} value={hint}
                    placeholder="What this format is for, in one line."
                    onChange={(e) => setHint(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="pub-folder">Folder</Label>
          <CategoryField id="pub-folder" value={category} categories={categories}
                         onChange={setCategory} />
        </div>
      </div>
    </ConfirmDialog>
  );
}

function CategoryField({ id, value, onChange, categories, error, hint }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>Category</Label>
      <Input id={id} value={value} list={`${id}-list`} autoComplete="off"
             placeholder="general" onChange={(e) => onChange(e.target.value)}
             aria-invalid={error ? true : undefined} />
      <datalist id={`${id}-list`}>
        {categories.map((c) => <option key={c} value={c} />)}
      </datalist>
      {error
        ? <p className="text-xs text-destructive">{error}</p>
        : <p className="text-xs text-muted-foreground">
            {hint || `A folder path, up to ${MAX_DEPTH} deep - e.g. site-a/entrances.`}
          </p>}
    </div>
  );
}

function SaveTemplateDialog({ open, onOpenChange, cards, categories, busy, onSubmit }) {
  const [from, setFrom] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("general");

  useEffect(() => {
    if (!open) return;
    setFrom(""); setName(""); setDescription(""); setCategory("general");
  }, [open]);

  const catErr = categoryError(category);
  const valid = !!from && !!name.trim() && !catErr;

  function submit(e) {
    e.preventDefault();
    if (!valid || busy) return;
    onSubmit({ from_slug: from, name: name.trim(),
               description: description.trim(), category: category.trim() });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="cy-app">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Save a system as a template</DialogTitle>
            <DialogDescription>
              Captures the system's workflow, dashboards and class filter. Its cameras
              and zones stay behind - they describe this site, not the next one.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 py-3">
            <div className="grid gap-1.5">
              <Label htmlFor="tpl-from">System<span className="text-destructive"> *</span></Label>
              <select id="tpl-from" value={from} onChange={(e) => {
                        setFrom(e.target.value);
                        // The system's own name is the answer nine times out of ten.
                        const card = cards.find((c) => c.slug === e.target.value);
                        if (card && !name.trim()) setName(card.name || card.slug);
                      }}
                      className="h-9 rounded-md border bg-background px-2 text-sm">
                <option value="">Choose a system…</option>
                {cards.map((c) => (
                  <option key={c.slug} value={c.slug}>{c.name} ({c.slug})</option>
                ))}
              </select>
              {!cards.length && (
                <p className="text-xs text-muted-foreground">
                  No systems on this hub yet.
                </p>
              )}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="tpl-name">Template name<span className="text-destructive"> *</span></Label>
              <Input id="tpl-name" value={name} autoComplete="off"
                     placeholder="e.g. Loading bay counting"
                     onChange={(e) => setName(e.target.value)} />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="tpl-desc">Description</Label>
              <Textarea id="tpl-desc" rows={3} value={description}
                        placeholder="What this template is for, and what the operator still has to bind."
                        onChange={(e) => setDescription(e.target.value)} />
            </div>

            <CategoryField id="tpl-cat" value={category} onChange={setCategory}
                           categories={categories} error={catErr} />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy}
                    onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!valid || busy}>
              {busy ? "Saving…" : "Save template"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditTemplateDialog({ template, onOpenChange, categories, busy, onSubmit }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");

  useEffect(() => {
    if (!template) return;
    setName(template.name || "");
    setDescription(template.description || "");
    setCategory(template.category || "general");
  }, [template]);

  const catErr = categoryError(category);
  const valid = !!name.trim() && !catErr;

  function submit(e) {
    e.preventDefault();
    if (!valid || busy) return;
    // `slug` follows the name: the server re-slugifies it and moves the file,
    // so a renamed template does not keep a filename nobody recognises.
    onSubmit({ name: name.trim(), description: description.trim(),
               category: category.trim(), slug: name.trim() });
  }

  return (
    <Dialog open={!!template} onOpenChange={onOpenChange}>
      <DialogContent className="cy-app">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Rename or move</DialogTitle>
            <DialogDescription>
              Changing the category moves the file into that folder, creating it if needed.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 py-3">
            <div className="grid gap-1.5">
              <Label htmlFor="tpl-edit-name">Template name<span className="text-destructive"> *</span></Label>
              <Input id="tpl-edit-name" value={name} autoComplete="off" autoFocus
                     onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="tpl-edit-desc">Description</Label>
              <Textarea id="tpl-edit-desc" rows={3} value={description}
                        onChange={(e) => setDescription(e.target.value)} />
            </div>
            <CategoryField id="tpl-edit-cat" value={category} onChange={setCategory}
                           categories={categories} error={catErr} />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy}
                    onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!valid || busy}>
              {busy ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function FolderDialog({ open, onOpenChange, busy, onSubmit }) {
  const [path, setPath] = useState("");
  useEffect(() => { if (open) setPath(""); }, [open]);

  const err = path.trim() ? categoryError(path) : "";
  const valid = !!path.trim() && !err;

  function submit(e) {
    e.preventDefault();
    if (!valid || busy) return;
    onSubmit(path.trim());
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="cy-app">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>New folder</DialogTitle>
            <DialogDescription>
              Folders are the categories templates are filed under.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5 py-3">
            <Label htmlFor="tpl-folder">Folder path</Label>
            <Input id="tpl-folder" value={path} autoComplete="off" autoFocus
                   placeholder="site-a/entrances"
                   aria-invalid={err ? true : undefined}
                   onChange={(e) => setPath(e.target.value)} />
            {err
              ? <p className="text-xs text-destructive">{err}</p>
              : <p className="text-xs text-muted-foreground">
                  Lower-case letters, digits, - and _; up to {MAX_DEPTH} levels deep.
                </p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy}
                    onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!valid || busy}>
              {busy ? "Creating…" : "Create folder"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
