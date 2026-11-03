// "Start from a template" -- the built-in templates as picture cards, with a
// preview of what each one gives you (Tok Aba, 2026-10-22, meeting notes
// Entries 1 and 2).
//
// TEMPLATE FIRST. A first-time user used to meet a blank "New system" button,
// then a wizard whose first question was "What is it detecting?" -- asked of
// someone who had not yet seen what the box can do. The templates are now on
// the Hub home itself: a card per template, a preview that says in plain
// words what you get, and "Use this template", which opens the create wizard
// already on its Name step with that template chosen.
//
// Two places draw it: the Hub home (preview in a dialog) and the wizard's
// "Change template" dialog (`embedded`: the preview replaces the cards in
// place, since a dialog on a dialog closes both on Escape).
//
// SLIDES (slidesOf): the preview media shipped with the template -- up to 5,
// at most one a video (core/services/template_media*.py); else the release's
// own pictures, core/static/img/templates/<key>.png or <key>-1,
// <key>-2 ... (core/services/template_preview.py). A template with neither
// draws a tinted placeholder with an icon, so a missing picture never reads
// as a broken page.
//
import { useState } from "react";
import { useApi } from "../api/hooks.js";
import { useApp } from "../state/app.jsx";
import { apiUrl } from "../lib/base.js";
import { Button } from "./ui/button.jsx";
import { Dialog } from "./ui/primitives.jsx";
import {
  ArrowLeft, Car, ChevronLeft, ChevronRight, LayoutTemplate, ScanSearch,
  ShieldAlert, SquareDashed, Users,
} from "lucide-react";

// Blank Canvas FIRST (Tok Aba, 2026-10-23; it was last since UAT-112): the
// cards are now the front door, and "start from nothing" is the first thing
// to look for there, ahead of the ready-made ones. Its key is
// provision.DEFAULT_TEMPLATE on the server. Sort is stable, so the rest keep
// the order they came in.
export function sortTemplates(list) {
  return [...(list || [])].sort(
    (a, b) => (b.key === "blank_canvas") - (a.key === "blank_canvas"));
}

// The placeholder's icon, by what the template is about. Matched loosely so a
// template published later ("smart_parking", "parking_bay") still gets a
// fitting one; anything unknown gets the generic template mark.
function iconFor(t) {
  const k = `${t?.key || ""} ${t?.label || ""}`.toLowerCase();
  if (/blank/.test(k)) return SquareDashed;
  if (/park|car|vehicle/.test(k)) return Car;
  if (/restrict|zone|intru/.test(k)) return ShieldAlert;
  if (/count|people|person/.test(k)) return Users;
  if (/detect|object/.test(k)) return ScanSearch;
  return LayoutTemplate;
}

/**
 * A template's slides, in order: the preview media uploaded on the developer
 * hub (`media`, phase 2: pictures and at most one video, brought to boxes by
 * the signed template update), else the release's own pictures (`images`).
 */
export function slidesOf(t) {
  if (t?.media?.length) return t.media;
  return (t?.images || []).map((url) => ({ url, kind: "image", caption: "" }));
}

/**
 * One slide, or the placeholder. On a card (`card`) a video is never played:
 * the first PICTURE is shown, and a template whose only slide is a video
 * shows its first frame. In the preview a video plays inline, muted, with
 * its controls.
 */
export function TemplateArt({ t, slide, card = false, className = "" }) {
  const all = slidesOf(t);
  const s = slide || (card ? all.find((x) => x.kind === "image") || all[0] : all[0]);
  const Icon = iconFor(t);
  return (
    <div className={"relative flex items-center justify-center overflow-hidden "
                    + "bg-gradient-to-br from-primary/20 via-primary/10 to-transparent " + className}
         data-template-art={s ? s.kind : "placeholder"}>
      {!s ? (
        <Icon className="size-10 text-primary/70" strokeWidth={1.5} aria-hidden />
      ) : s.kind === "video" ? (
        <video key={s.url} src={apiUrl(s.url)} muted playsInline loop
               preload="metadata" controls={!card} autoPlay={!card}
               className="absolute inset-0 h-full w-full bg-black object-contain" />
      ) : (
        <img src={apiUrl(s.url)} alt={s.caption || ""}
             className="absolute inset-0 h-full w-full object-cover" />
      )}
    </div>
  );
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const nameOf = (c) => (c === "person" ? "people" : c);

/**
 * What a template gives you, as short plain sentences. Read from the build
 * the template carries (the listing's `preview`: dashboard pages and card
 * titles, workflow steps, record tables), so a template republished with a
 * new dashboard says so here without anybody writing copy for it.
 */
export function whatYouGet(t) {
  // The developer's own lines, when the template has them (System Templates
  // > What you get; core/services/template_preview.py): they replace the
  // automatic text whole.
  if (t?.features?.length) return t.features;
  const pv = t?.preview || {};
  const cards = pv.cards || [];
  const pages = pv.pages || [];
  const tables = pv.tables || [];
  const steps = pv.steps || 0;
  const out = [];
  if (!cards.length && steps <= 1) {
    out.push("An empty dashboard and an empty Workflow page, to build your own system step by step.");
  } else {
    const classes = (t?.classes || []).map(nameOf);
    if (classes.length && classes.length <= 6) {
      out.push(`Looks for: ${classes.join(", ")}.`);
    } else if (classes.length) {
      out.push(`Looks for ${classes.length} kinds of things. You choose which.`);
    }
    if (cards.length) {
      const shown = cards.slice(0, 6).join(", ");
      out.push(`A ready-made dashboard${pages.length > 1 ? ` (${plural(pages.length, "page", "pages")})` : ""}`
               + ` showing ${shown}${cards.length > 6 ? ", and more" : ""}.`);
    }
    if (steps > 1) {
      out.push(`The rules already built for you: ${plural(steps, "block", "blocks")} on the Workflow page, connected and ready.`);
    }
    if (tables.length) {
      out.push(`Keeps a record in ${tables.join(" and ")}, to look back at later.`);
    }
  }
  out.push("Add your camera, then change anything you like.");
  return out;
}

/**
 * The preview's words: title, the sentence under it (the developer's `blurb`,
 * else the template's line), the list's heading (`heading`, else "What you
 * get") and the list. One component, so the developer's editor shows EXACTLY
 * what a customer will read. React renders every string as text, so nothing
 * typed there can become HTML.
 *
 * `onPick(part, index)` (the editor only) makes each piece clickable: a click
 * on a line, the sentence or the heading puts the cursor in its field. A
 * customer's preview passes none and reads plain text.
 */
export const DEFAULT_HEADING = "What you get";

export function TemplateCopy({ t, onPick }) {
  const under = t?.blurb || t?.hint || "";
  const pick = (part, i) => (onPick ? {
    onClick: () => onPick(part, i), title: "Click to edit", role: "button", tabIndex: 0,
    onKeyDown: (e) => { if (e.key === "Enter") onPick(part, i); },
    className: "cursor-pointer rounded hover:bg-primary/10 hover:outline hover:outline-1 hover:outline-primary/40",
  } : {});
  const cls = (base, extra) => [base, extra].filter(Boolean).join(" ");
  const pb = pick("blurb");
  const ph = pick("heading");
  return (
    <>
      <div>
        <p className="text-base font-semibold">{t.label}</p>
        {under || onPick ? (
          <p {...pb} className={cls("mt-0.5 text-sm text-muted-foreground", pb.className)}
             data-template-blurb>{under || "(no additional description)"}</p>
        ) : null}
      </div>
      <div>
        <p {...ph} className={cls("text-sm font-medium", ph.className)} data-template-heading>
          {t?.heading || DEFAULT_HEADING}
        </p>
        <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm" data-what-you-get>
          {whatYouGet(t).map((line, i) => {
            const pl = pick("line", i);
            return <li key={`${i}:${line}`} {...pl} className={pl.className}>{line}</li>;
          })}
        </ul>
      </div>
    </>
  );
}

/**
 * The preview: picture (a slider when there are several), title, what you
 * get, and the button that uses it. `onBack` (embedded only) returns to the
 * cards.
 */
export function TemplatePreview({ t, onUse, onBack, limitNote = "" }) {
  const images = slidesOf(t);
  const [idx, setIdx] = useState(0);
  const at = images.length ? Math.min(idx, images.length - 1) : 0;
  return (
    <div className="space-y-3" data-template-preview={t.key}>
      <div className="relative">
        <TemplateArt t={t} slide={images[at]} className="aspect-video w-full rounded-lg border" />
        {images.length > 1 && (
          <>
            <button type="button" aria-label="Previous picture"
                    onClick={() => setIdx((at - 1 + images.length) % images.length)}
                    className="absolute left-2 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-full border bg-background/80 backdrop-blur hover:text-primary">
              <ChevronLeft className="size-4" />
            </button>
            <button type="button" aria-label="Next picture"
                    onClick={() => setIdx((at + 1) % images.length)}
                    className="absolute right-2 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-full border bg-background/80 backdrop-blur hover:text-primary">
              <ChevronRight className="size-4" />
            </button>
            <span className="absolute bottom-2 left-1/2 flex -translate-x-1/2 gap-1.5">
              {images.map((sl, i) => (
                <button key={sl.url} type="button" aria-label={`Picture ${i + 1}`}
                        onClick={() => setIdx(i)}
                        className={"size-2 rounded-full " + (i === at ? "bg-primary" : "bg-background/80")} />
              ))}
            </span>
          </>
        )}
      </div>
      {images[at]?.caption ? (
        <p className="text-center text-xs text-muted-foreground" data-slide-caption>{images[at].caption}</p>
      ) : null}
      <TemplateCopy t={t} />
      <div className="flex items-center gap-2 pt-1">
        {onBack && (
          <Button type="button" variant="outline" onClick={onBack}>
            <ArrowLeft className="size-4" /> All templates
          </Button>
        )}
        {limitNote ? (
          <span className="ml-auto text-xs text-muted-foreground">{limitNote}</span>
        ) : null}
        <Button type="button" className={limitNote ? "" : "ml-auto"} disabled={!!limitNote}
                title={limitNote || undefined} onClick={() => onUse(t.key)}>
          Use this template
        </Button>
      </div>
    </div>
  );
}

/**
 * The cards. `onUse(key)` is "Use this template"; `embedded` draws the
 * preview in place instead of in a dialog.
 */
export default function TemplateGallery({ onUse, embedded = false, limitNote = "" }) {
  const { user } = useApp();
  const isAdmin = !!user?.is_admin;
  const { data } = useApi("/api/hub/templates");
  const templates = sortTemplates(data?.templates);
  const [open, setOpen] = useState(null);
  // Admin only, as creating is: a non-admin cannot use a template, so the
  // section is not there for them at all.
  if (!isAdmin) return null;

  // The preview opened from a card, looked up again by key so a refresh that
  // landed meanwhile shows the newest content.
  const shownT = open ? templates.find((t) => t.key === open) || null : null;

  if (embedded && shownT) {
    return <TemplatePreview key={shownT.key} t={shownT} onUse={onUse} onBack={() => setOpen(null)} />;
  }

  return (
    <section className="space-y-3" data-template-gallery>
      {!embedded && (
        <div>
          <h2 className="text-base font-semibold">Start from a template</h2>
          <p className="text-sm text-muted-foreground">
            Pick one to see what it gives you. You can change everything later.
          </p>
          {/* At the system cap the cards stay, to look at; using one waits
              until a system is removed, and this says so up front. */}
          {limitNote ? (
            <p role="status" data-template-limit
               className="mt-2 rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm">
              {limitNote}
            </p>
          ) : null}
        </div>
      )}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
        {templates.map((t) => (
          <button key={t.key} type="button" data-template-card={t.key}
                  onClick={() => setOpen(t.key)}
                  className="group overflow-hidden rounded-xl border bg-card text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[0_8px_30px_-12px_var(--primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <TemplateArt t={t} card className="h-28" />
            <span className="block border-t p-3">
              <span className="block truncate text-sm font-semibold">{t.label}</span>
              <span className="mt-0.5 block truncate text-xs text-muted-foreground" title={t.hint || ""}>
                {t.hint || " "}
              </span>
            </span>
          </button>
        ))}
      </div>
      {!embedded && (
        <Dialog open={!!shownT} title="Template preview" onClose={() => setOpen(null)}>
          {shownT && (
            <TemplatePreview key={shownT.key} t={shownT} limitNote={limitNote}
                             onUse={(key) => { setOpen(null); onUse(key); }} />
          )}
        </Dialog>
      )}
    </section>
  );
}
