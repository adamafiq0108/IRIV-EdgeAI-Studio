// Step-by-step system creation (the hub's New System panel).
//
//   name -> review
//
// TEMPLATE FIRST (Tok Aba, 2026-10-22): the template is chosen BEFORE the
// wizard opens -- on the Hub home's "Start from a template" cards
// (TemplateGallery.jsx), whose "Use this template" lands here on the Name
// step with that template set; the toolbar's New System starts from Blank
// Canvas. The old first step, "What is it detecting?", asked someone who had
// not yet seen what the box does to name it. "Change template" on the Name
// step reopens the cards in a dialog, so a wrong pick is one click to fix
// and the name typed so far is kept.
//
// One path. Asking an operator to rank their own experience before they have
// seen the form is a question they cannot answer yet, and it split the same
// three fields across two flows. Slug, subtitle, icon and whether to start
// now all have working defaults, so they live behind "More options" on the
// name step: present for whoever wants them, absent for everyone else.
//
// The PORT is on the name step itself (Tok Aba, 2026-10-06): Automatic (next
// free) by default, showing the port it would take, or a Custom port checked
// against the box as it is typed (usePortCheck) -- the same rules the create
// enforces, so Next is never the first place a taken port is found out.
// The address follows the Hub (Tok Aba, 2026-10-07; boxAccess.jsx): with
// the Hub on the proxy the new system "uses the proxy, like the Hub" at
// http://<box>/s/<slug>/ and its port is the internal one, automatic or set
// under Advanced; with the Hub on ports, the port choice as before and a
// note pointing at the Software port card. No switch here.
//
// THE TEMPLATES COME UP TO DATE ON OPENING (Tok Aba, 2026-10-14): the wizard
// asks the hub once, as it mounts, to fetch the latest published templates
// (POST /api/hub/templates/refresh -- bounded, rate limited and single-flight
// on the server, never an error). Silently now: the line that reports it
// moved to the cards on the Hub home, where the templates are chosen. The
// list redraws if anything landed and the chosen template stays chosen (the
// key is stable, only its content moved). Nothing waits for it except
// Create, which lets an unfinished refresh land first so the system is
// built from the newest template. Offline is not an error.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/hooks.js";
import { useApp } from "../state/app.jsx";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { useToast } from "./ui/Toast.jsx";
import { usePortCheck } from "./usePortCheck.js";
import { Dialog } from "./ui/primitives.jsx";
import TemplateGallery from "./TemplateGallery.jsx";
import { HubUsesPorts, STREAM_PROXY, proxyAddress, useHubProxy } from "./boxAccess.jsx";
import { Check, ChevronLeft, RefreshCw, ScanEye } from "lucide-react";

// A system slug the publish endpoint accepts (core/services/publish.py
// SLUG_RE: ^[a-z][a-z0-9_]*$), derived from the human name.
export function slugify(name) {
  const s = String(name || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!s) return "";
  return /^[a-z]/.test(s) ? s : "s_" + s;
}

const STEPS = ["name", "review"];

const STEP_TITLES = {
  name: "Name the system",
  review: "Review & create",
};

function Row({ label, children, hint }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </label>
  );
}

export default function CreateSystemWizard({ template = "", onCreated, onCancel, onBrowseStore }) {
  const { user } = useApp();
  const toast = useToast();
  const isDev = !!user?.is_developer;
  const { data: tplData, reload: reloadTemplates } = useApi("/api/hub/templates");
  const templates = tplData?.templates || [];
  // "Check for template updates" in flight. The templates offered here are
  // the ones this box was installed with plus whatever the developer has
  // published since -- and the moment somebody wants a newer one is right
  // here, choosing what to build.
  const [checking, setChecking] = useState(false);
  // The automatic refresh, silent here. The promise is kept so Create can
  // let an unfinished one land first.
  const autoRun = useRef(null);
  const [stepIdx, setStepIdx] = useState(0);
  // The template the card chose. Its key travels to Create unchanged.
  const [tpl, setTpl] = useState(template || "");
  // "Change template": the cards again, in a dialog over the Name step.
  const [changing, setChanging] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");       // "" = follow the name
  const [subtitle, setSubtitle] = useState("");
  const [icon, setIcon] = useState("");
  const [portMode, setPortMode] = useState("auto");
  const [port, setPort] = useState("");
  const [startNow, setStartNow] = useState(true);
  const [busy, setBusy] = useState(false);
  const { suggested, verdict } = usePortCheck({
    port: portMode === "custom" ? port : "",
    enabled: !!user?.is_admin,
  });
  const proxyMode = useHubProxy(!!user?.is_admin);
  const proxy = proxyMode === true;
  // null = still asking the hub. Neither the Address nor the port block is
  // drawn until it answers, or the port block shows for a moment and is then
  // swapped for the address on a proxy box.
  const modeKnown = proxyMode !== null;
  const [advanced, setAdvanced] = useState(false);

  const isAdmin = !!user?.is_admin;
  useEffect(() => {
    if (!isAdmin || autoRun.current) return;
    autoRun.current = api.post("/api/hub/templates/refresh", {})
      .then(async (d) => {
        // Redraw the list before Create may go on.
        if (d?.state === "updated") await reloadTemplates().catch(() => {});
      })
      // Never an error to the person creating a system: a refresh that
      // could not run means the templates already here are used.
      .catch(() => {});
  }, [isAdmin, reloadTemplates]);

  if (!isAdmin) {
    return (
      <p className="text-sm text-muted-foreground">
        Creating a system is an admin action - ask your administrator.
      </p>
    );
  }

  // Chosen before the wizard opened: a card's "Use this template", or Blank
  // Canvas from New System. UAT-112's "nothing preselected" still holds in
  // spirit -- nobody gets a template without pressing a card or a button
  // for it -- and a wizard opened with none falls back to the server's
  // default (Blank Canvas), named on the step with Change template beside it.
  const chosenTpl = tpl || tplData?.default || "";
  const tplLabel = templates.find((t) => t.key === chosenTpl)?.label || chosenTpl;
  const steps = STEPS;
  const step = steps[stepIdx];
  const effSlug = slug.trim() || slugify(name);

  // A built-in template IS a whole build now -- the Built-in templates
  // folder publishes the workbench's snapshot (workflow, dashboard, classes)
  // -- so picking one is all anybody has to do. Starting from nothing is a
  // deliberate choice with a name: Blank Canvas, which cannot be deleted.
  const stepValid =
    step === "name" ? (!!chosenTpl && !!name.trim() && !!effSlug
                       && (portMode === "auto" || !!verdict?.usable))
    : true;

  function reset() {
    setStepIdx(0); setTpl(template || ""); setName(""); setSlug("");
    setSubtitle(""); setIcon("");
    setPortMode("auto"); setPort(""); setStartNow(true); setAdvanced(false);
  }

  /**
   * Fetch the templates the developer has published and install the new and
   * changed ones on this box. Only the template list changes: a system
   * already created keeps the workflow it has, whatever its template now
   * says -- that is the release update's job, not this button's.
   */
  async function checkTemplateUpdates() {
    setChecking(true);
    try {
      const d = await api.post("/api/hub/templates/update", {});
      const names = (list) => (list || []).map((t) => t.label || t.key).join(", ");
      const added = d.added || [];
      const updated = d.updated || [];
      if (d.authoring) {
        // The developer hub is where the templates come FROM: nothing is
        // installed here, the check says what differs from the published
        // copy so the developer knows whether to press Publish.
        const parts = [
          d.differs?.length ? `${names(d.differs)} differ${d.differs.length === 1 ? "s" : ""} from the published copy.` : "",
          d.unpublished?.length ? `Not published yet: ${names(d.unpublished)}.` : "",
          d.removed_here?.length ? `Published but removed here: ${names(d.removed_here)}.` : "",
          d.media?.differs?.length ? `Preview media differs for ${d.media.differs.join(", ")}.` : "",
        ].filter(Boolean);
        if (parts.length) {
          toast.info("This hub authors the templates",
            parts.join(" ") + " Press Publish templates on the System Templates page to send what is here.");
        } else {
          toast.success("Published templates match this hub",
                        "Every template here is what other boxes fetch.");
        }
      } else if (added.length || updated.length) {
        toast.success(
          `${added.length + updated.length} template${added.length + updated.length === 1 ? "" : "s"} installed`,
          [added.length ? `New: ${names(added)}.` : "",
           updated.length ? `Updated: ${names(updated)}.` : "",
           "Systems you already have are unchanged."].filter(Boolean).join(" "));
      } else {
        toast.info("Templates are up to date",
          (d.kept || []).length
            ? `${names(d.kept)} ${d.kept.length === 1 ? "is" : "are"} authored on this box and kept as is.`
            : undefined);
      }
      for (const e of d.errors || []) {
        toast.warn(`${e.key} could not be installed`, e.error);
      }
      // A picked template that the check just replaced is still the right
      // pick: the key is stable, only its content moved.
      await reloadTemplates();
    } catch (err) {
      toast.error("Could not check for template updates", err.message);
    } finally {
      setChecking(false);
    }
  }
  const updates = tplData?.updates || null;
  const lastChecked = updates?.checked_at || 0;
  // What the line beside the button says. A failed check is not a check, so
  // it is shown as the failure it was, with the reason, rather than as a
  // time; a box with no update token cannot fetch at all, and says so
  // instead of offering a button whose only outcome is that error.
  const noToken = updates ? updates.token === false : false;
  const checkNote = noToken
    ? "This box has no update token, so it cannot fetch templates. "
    : updates?.last && updates.last.ok === false
      ? `Last check failed: ${updates.last.error || "unknown error"} `
      : lastChecked
        ? `Last checked ${new Date(lastChecked * 1000).toLocaleString()}. `
        : "Fetches templates published since this box was installed. ";

  async function create() {
    setBusy(true);
    // A refresh still under way gets to finish first (bounded on the server
    // to a few seconds), so a template it changed after the card was picked
    // is the one the system is built from; the server waits for it too.
    if (autoRun.current) await autoRun.current.catch(() => {});
    // Only send what was actually set; every omitted field takes the server's
    // default, which is the same value the form shows as a placeholder.
    const body = { name: name.trim(), template: chosenTpl };
    if (slug.trim()) body.slug = slug.trim();
    if (subtitle.trim()) body.subtitle = subtitle.trim();
    if (icon.trim()) body.icon = icon.trim();
    if (portMode === "custom") body.port = Number(port.trim());
    body.start = startNow;
    try {
      const d = await api.post("/api/hub/systems", body);
      const detail = d.started
        ? `Starting at ${d.url} - the card is on the Systems grid.`
        : `Created without starting it - start it from System Control.`;
      toast.success(`${d.name} created`, detail);
      // Just "created": what is left to set up is the guide's job (SetupWizard),
      // and a second warning toast stacked under this one read as an error.
      reset();
      onCreated?.(d);
    } catch (err) {
      toast.error("Could not create the system", err.message);
      setBusy(false);
    }
  }

  // ── stepper ───────────────────────────────────────────────────────────────
  return (
    <div>
      {/* The check lives in the header, top right, on every step: the
          templates it refreshes are what Change template offers, and the
          line under the title is its status, so the question "are these the
          latest?" is answered beside the choice. It changes the LIST only -- a
          system already running keeps its own workflow until a release
          update, and the line says so. */}
      <div className="flex flex-wrap items-start gap-3">
        <ScanEye className="mt-0.5 size-5 text-primary" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">Create an AI-vision system, step by step</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {isDev ? checkNote : ""}
            {isDev && updates?.authoring
              ? "This hub authors the templates: the check compares them with what is published."
              : "Templates are updated with each software update. Systems you already created are not changed."}
          </p>
        </div>
        {/* Developer tier only: customers get their templates with each
            software update, so the between-release fetch is not offered to
            them. The routes stay; the button is what is gated. */}
        {isDev && (
          <Button type="button" variant="outline" size="sm" className="shrink-0"
                  disabled={checking || busy || noToken}
                  title={noToken ? "Set the box's update token first (System tab under the notification bell)" : undefined}
                  onClick={checkTemplateUpdates}>
            <RefreshCw className={"size-4" + (checking ? " animate-spin" : "")} />
            {checking ? "Checking…" : "Check for template updates"}
          </Button>
        )}
      </div>
      {/* Every step is LABELLED, not just the current one. Bare numbered dots
          say how far along you are but not what is coming, so the wizard's
          shape -- three steps, this is what they are -- had to be inferred by
          walking it. A done step shows a tick rather than its number: the
          number is only useful while it is still ahead of you. */}
      <ol className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {steps.map((s, i) => {
          const done = i < stepIdx;
          const here = i === stepIdx;
          return (
            <li key={s} className="flex items-center gap-2"
                aria-current={here ? "step" : undefined}>
              {i > 0 && <span className="h-px w-5 shrink-0 bg-border" aria-hidden />}
              <span className={
                "flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold " +
                (here ? "bg-primary text-primary-foreground"
                  : done ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground")
              }>
                {done ? <Check className="size-3" strokeWidth={3} /> : i + 1}
              </span>
              <span className={here ? "font-medium" : "text-muted-foreground"}>
                {STEP_TITLES[s]}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="mt-4 space-y-3">
        {step === "name" && (
          <div className="space-y-3">
            {/* The template the card chose, and the way to choose another.
                Nobody is stuck with a mis-click: the cards come back in a
                dialog and the name typed below is kept. */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border bg-card/50 px-3 py-2 text-sm"
                 data-chosen-template={chosenTpl}>
              <span className="text-muted-foreground">Template:</span>
              <span className="font-medium">{tplLabel || "none chosen"}</span>
              <button type="button" onClick={() => setChanging(true)}
                      className="ml-auto text-xs font-medium text-primary underline underline-offset-2">
                Change template
              </button>
            </div>
            <Row label="System name" hint="Everything technical (folder name, port, server process) is derived and handled for you.">
              <Input value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="e.g. Loading Bay" autoFocus aria-label="System name" />
            </Row>

            {modeKnown && proxy && (
              <div className="grid gap-1.5">
                <span className="text-sm font-medium">Address</span>
                <span className="text-xs text-muted-foreground" data-proxy-address>
                  {"Uses the proxy, like the Hub: "}<code>{proxyAddress(effSlug)}</code>.{" "}
                  {STREAM_PROXY}{" "}
                  <button type="button" className="underline" data-advanced
                          onClick={() => setAdvanced((a) => !a)}>
                    {advanced ? "Hide internal port" : "Advanced: internal port"}
                  </button>
                </span>
              </div>
            )}

            {modeKnown && (!proxy || advanced) && (
            <div className="grid gap-1.5" role="radiogroup" aria-label="Service port">
              <span className="text-sm font-medium">{proxy ? "Internal port" : "Service port"}</span>
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" size="sm" role="radio" aria-checked={portMode === "auto"}
                        variant={portMode === "auto" ? "default" : "outline"}
                        onClick={() => setPortMode("auto")}>Automatic (next free)</Button>
                <Button type="button" size="sm" role="radio" aria-checked={portMode === "custom"}
                        variant={portMode === "custom" ? "default" : "outline"}
                        onClick={() => setPortMode("custom")}>Custom port</Button>
                {portMode === "custom" && (
                  <Input className="w-28" value={port} inputMode="numeric"
                         onChange={(e) => setPort(e.target.value.replace(/[^\d]/g, ""))}
                         placeholder={suggested ? String(suggested) : "5111"}
                         aria-label="Custom port" />
                )}
              </div>
              <span className="text-xs text-muted-foreground" data-port-note>
                {portMode === "auto"
                  ? (suggested ? `Suggested: ${suggested}, the next free port.` : "The next free port is picked for you.")
                  : !port.trim()
                    ? "A number between 1024 and 65535."
                    : verdict ? verdict.detail : "Checking…"}
              </span>
              {!proxy && <HubUsesPorts onGo={onCancel} />}
            </div>
            )}
          </div>
        )}

        {step === "review" && (
          <div className="rounded-lg border bg-card/50 p-4">
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div><dt className="text-xs text-muted-foreground">Template</dt><dd className="font-medium">{tplLabel}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Name</dt><dd className="font-medium">{name.trim() || "-"}</dd></div>
              <div><dt className="text-xs text-muted-foreground">System name</dt><dd className="font-mono text-xs">{effSlug || "-"}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Subtitle</dt><dd>{subtitle.trim() || `AI vision · ${tplLabel.toLowerCase()}`}</dd></div>
              {proxy && <div><dt className="text-xs text-muted-foreground">Address</dt><dd className="font-mono text-xs">{proxyAddress(effSlug)}</dd></div>}
              <div><dt className="text-xs text-muted-foreground">{proxy ? "Internal port" : "Service port"}</dt><dd>{portMode === "custom" ? `custom, ${port}` : `automatic (next free${suggested ? `, ${suggested} now` : ""})`}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Start</dt><dd>{startNow ? "now" : "later, manually"}</dd></div>
            </dl>
            <p className="mt-3 text-xs text-muted-foreground">
              Creates <code className="font-mono">systems/{effSlug}/</code> with the {tplLabel.toLowerCase()} dashboard
              and registers it on this hub. Remove it any time with Select → Remove on the Systems grid.
            </p>
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Button type="button" variant="outline" disabled={busy}
                onClick={() => (stepIdx === 0
                  // On the Name step there is nothing earlier to go back
                  // TO inside the wizard -- the template was chosen on the
                  // Hub home -- so Back means "leave": return there.
                  // reset() only cleared the form, which read as the button
                  // doing nothing.
                  ? (onCancel ? onCancel() : reset())
                  : setStepIdx(stepIdx - 1))}>
          <ChevronLeft className="size-4" /> Back
        </Button>
        {step !== "review" ? (
          <Button type="button" disabled={!stepValid || busy}
                  onClick={() => setStepIdx(stepIdx + 1)}>Next</Button>
        ) : (
          <Button type="button" disabled={busy} onClick={create}>
            {busy ? "Creating…" : "Create system"}
          </Button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          Step {stepIdx + 1} of {steps.length}
        </span>
      </div>

      <Dialog open={changing} title="Change template" onClose={() => setChanging(false)}>
        {changing && (
          <TemplateGallery embedded onBrowseStore={onBrowseStore}
                           onUse={(key) => { setTpl(key); setChanging(false); }} />
        )}
      </Dialog>
    </div>
  );
}
