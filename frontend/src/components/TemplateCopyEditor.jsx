// "What you get" -- a built-in template's own preview words (Tok Aba,
// 2026-10-22, third round; 2026-10-23, edit what the customer sees).
// Developer hub only: opened from a template's row on the System Templates
// page, beside Preview media.
//
// The Hub home's preview writes its "What you get" list from the template's
// build (TemplateGallery.jsx whatYouGet). Here the developer changes those
// words: a template with no words of its own opens with the AUTOMATIC lines
// already in the fields, so the job is editing what customers read today,
// not starting from an empty box. Saving stores the lines as the template's
// own; "Use automatic text" clears them and puts the automatic lines back.
// The state line says which it is, in plain words. The sentence under the
// title and the list's heading are editable the same way, and a click on any
// of them in "Content" puts the cursor in its field.
//
// Saved into the template's formats.toml entry (PUT /api/hub/formats/<key>/
// copy, core/services/template_preview.py), so the words reach customer
// boxes the way every template change does: with Publish templates.
//
// The same limits as the server: at most 8 lines, 140 characters a line, 200
// for the sentence, 40 for the heading, no empty line. An automatic line
// longer than 140 is shortened on the way in, and said so, so Save is never
// blocked by text nobody typed. Plain text: shown as text, never as HTML.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { Dialog } from "./ui/primitives.jsx";
import { DEFAULT_HEADING, TemplateCopy, whatYouGet } from "./TemplateGallery.jsx";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";

export const MAX_LINES = 8;
export const LINE_MAX = 140;
export const BLURB_MAX = 200;
export const HEADING_MAX = 40;

/** What a box will store: control characters out, spaces collapsed. */
export const plain = (s) => String(s || "").replace(/[\u0000-\u001f\u007f]+/g, " ")
  .split(/\s+/).filter(Boolean).join(" ");

/** A line cut to fit `max`, at a word where it can be, ending in "...". */
export function fit(line, max = LINE_MAX) {
  const s = plain(line);
  if (s.length <= max) return s;
  const cut = s.slice(0, max - 3);
  const at = cut.lastIndexOf(" ");
  return `${(at > max / 2 ? cut.slice(0, at) : cut).replace(/[,;:\s]+$/, "")}...`;
}

/** The automatic lines as fields: at most 8, each fitted to 140, and which
 *  of them had to be shortened. */
export function autoLines(t) {
  const lines = whatYouGet({ ...t, features: [] }).slice(0, MAX_LINES);
  return { lines: lines.map((l) => fit(l)),
           cut: lines.map((l, i) => (plain(l).length > LINE_MAX ? i : -1)).filter((i) => i >= 0) };
}

/** Why the text cannot be saved yet, or "". */
export function copyProblem(lines, blurb, heading = "") {
  if (lines.length > MAX_LINES) return `What you get can have at most ${MAX_LINES} lines.`;
  if (lines.some((l) => !plain(l))) return "A line is empty. Type something or remove it.";
  if (lines.some((l) => plain(l).length > LINE_MAX)) return `Each line can be at most ${LINE_MAX} characters.`;
  if (plain(blurb).length > BLURB_MAX) return `The additional description can be at most ${BLURB_MAX} characters.`;
  if (plain(heading).length > HEADING_MAX) return `The heading can be at most ${HEADING_MAX} characters.`;
  return "";
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const STATE_TEXT = {
  automatic: "Automatic text: these are the words customers see now, written from the template. Edit any of them and save to make them your own.",
  unsaved: "Your own text, not saved yet.",
  custom: "Your own text. Customers see these words instead of the automatic ones.",
};

export default function TemplateCopyEditor({ template, onClose, onSaved }) {
  const key = template?.key || "";
  const [t, setT] = useState(null);
  const [lines, setLines] = useState([]);
  const [blurb, setBlurb] = useState("");
  const [heading, setHeading] = useState("");
  // What is stored now: `custom` = the template has words of its own.
  const [saved, setSaved] = useState({ custom: false, lines: [], blurb: "", heading: "" });
  const [cut, setCut] = useState([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const box = useRef(null);

  // Fields from what is stored: the template's own words, or the automatic
  // lines when it has none.
  function fill(found, stored) {
    const custom = !!(stored.features.length || stored.blurb || stored.heading);
    const auto = autoLines(found);
    const nextLines = stored.features.length ? stored.features : auto.lines;
    setLines(nextLines);
    setBlurb(stored.blurb);
    setHeading(stored.heading);
    setCut(stored.features.length ? [] : auto.cut);
    setSaved({ custom, lines: nextLines, blurb: stored.blurb, heading: stored.heading });
  }

  useEffect(() => {
    let live = true;
    // The template as the Hub's cards see it -- its build summary drives the
    // automatic text.
    api.get("/api/hub/templates").then((d) => {
      if (!live) return;
      const found = (d?.templates || []).find((x) => x.key === key) || { key, label: template?.label || key };
      setT(found);
      fill(found, { features: found.features || [], blurb: found.blurb || "", heading: found.heading || "" });
    }).catch((e) => setError(e.message));
    return () => { live = false; };
  }, [key, template?.label]);

  const problem = copyProblem(lines, blurb, heading);
  const shown = t ? { ...t, features: lines.map(plain).filter(Boolean), blurb: plain(blurb), heading: plain(heading) } : null;
  const dirty = !same(lines, saved.lines) || blurb !== saved.blurb || heading !== saved.heading;
  const state = dirty ? "unsaved" : saved.custom ? "custom" : "automatic";

  async function save(nextLines, nextBlurb, nextHeading) {
    setBusy(true);
    setError("");
    setNote("");
    try {
      const d = await api.put(`/api/hub/formats/${encodeURIComponent(key)}/copy`, {
        features: nextLines.map(plain), blurb: plain(nextBlurb), heading: plain(nextHeading) });
      const stored = { features: d.features || [], blurb: d.blurb || "", heading: d.heading || "" };
      fill(t, stored);
      setNote(stored.features.length || stored.blurb || stored.heading
        ? "Saved. Customer System Templates get it when you press Publish templates."
        : "Back to the automatic text. Customer System Templates get it when you press Publish templates.");
      onSaved?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const setLine = (i, v) => setLines(lines.map((l, j) => (j === i ? v : l)));
  const move = (i, by) => {
    const next = [...lines];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    setLines(next);
  };
  // A click in "Content" lands in the matching field.
  const focusField = (part, i) => {
    const label = part === "line" ? `Line ${i + 1}` : part === "heading" ? "Heading" : "Additional description";
    const el = box.current?.querySelector(`[aria-label="${label}"]`);
    el?.focus();
    el?.select?.();
  };

  return (
    <Dialog open={!!template} title={`Description: ${template?.label || key}`} onClose={onClose}
            footer={
              <>
                <Button type="button" variant="outline" disabled={busy}
                        onClick={() => save([], "", "")}>Use automatic text</Button>
                <Button type="button" disabled={busy || !!problem || !t}
                        onClick={() => save(lines, blurb, heading)}>{busy ? "Saving..." : "Save"}</Button>
              </>
            }>
      <div className="space-y-3" data-copy-editor={key} ref={box}>
        <p className="text-sm text-muted-foreground">
          The words in this template's preview on the Hub home. Click any of
          them below, or edit the fields. Customer System Templates get your words when
          you press Publish templates.
        </p>
        <p data-copy-state={state}
           className={"rounded-lg border px-3 py-2 text-sm "
                      + (state === "automatic" ? "bg-muted/50" : "border-primary/40 bg-primary/5")}>
          {STATE_TEXT[state]}
        </p>
        {shown && (
          <div className="rounded-lg border p-3" data-copy-preview>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Content (click to edit)
            </p>
            <div className="space-y-3"><TemplateCopy t={shown} onPick={focusField} /></div>
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1">
            <span className="text-sm font-medium">Additional description (optional)</span>
            <Input value={blurb} maxLength={BLURB_MAX + 20} aria-label="Additional description"
                   placeholder={t?.hint || "One short sentence"}
                   onChange={(e) => setBlurb(e.target.value)} />
            <span className="text-xs text-muted-foreground" data-blurb-count>
              {plain(blurb).length} / {BLURB_MAX}
            </span>
          </label>
          <label className="grid gap-1">
            <span className="text-sm font-medium">Heading (optional)</span>
            <Input value={heading} maxLength={HEADING_MAX + 10} aria-label="Heading"
                   placeholder={DEFAULT_HEADING} onChange={(e) => setHeading(e.target.value)} />
            <span className="text-xs text-muted-foreground" data-heading-count>
              {plain(heading).length} / {HEADING_MAX}
            </span>
          </label>
        </div>
        <div className="grid gap-1.5">
          <span className="text-sm font-medium">
            Lines <span className="text-xs font-normal text-muted-foreground" data-line-count>
              ({lines.length} of {MAX_LINES})
            </span>
          </span>
          {lines.map((l, i) => (
            <div key={i} className="grid gap-0.5" data-copy-line={i}>
              <div className="flex items-center gap-1.5">
                <Input className="h-8 min-w-0 flex-1 text-sm" value={l} maxLength={LINE_MAX + 20}
                       aria-label={`Line ${i + 1}`} onChange={(e) => setLine(i, e.target.value)} />
                <span className={"w-14 shrink-0 text-right text-[11px] "
                                 + (plain(l).length > LINE_MAX ? "text-destructive" : "text-muted-foreground")}>
                  {plain(l).length}/{LINE_MAX}
                </span>
                <Button type="button" size="icon-sm" variant="ghost" aria-label="Move up"
                        disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp className="size-4" /></Button>
                <Button type="button" size="icon-sm" variant="ghost" aria-label="Move down"
                        disabled={i === lines.length - 1} onClick={() => move(i, 1)}><ArrowDown className="size-4" /></Button>
                <Button type="button" size="icon-sm" variant="ghost" aria-label="Remove line"
                        className="text-destructive" onClick={() => {
                          setLines(lines.filter((_, j) => j !== i));
                          setCut(cut.filter((j) => j !== i).map((j) => (j > i ? j - 1 : j)));
                        }}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
              {cut.includes(i) ? (
                <span className="text-[11px] text-amber-700 dark:text-amber-400" data-line-cut>
                  Shortened to fit {LINE_MAX} characters. Check it reads well.
                </span>
              ) : null}
            </div>
          ))}
          <div>
            <Button type="button" size="sm" variant="outline" disabled={lines.length >= MAX_LINES}
                    onClick={() => setLines([...lines, ""])}>
              <Plus className="size-4" /> Add a line
            </Button>
          </div>
        </div>
        {problem ? <p role="alert" data-copy-problem className="text-sm text-destructive">{problem}</p> : null}
        {error ? <p role="alert" data-copy-error className="text-sm text-destructive">{error}</p> : null}
        {note ? <p role="status" data-copy-note className="text-sm text-muted-foreground">{note}</p> : null}
      </div>
    </Dialog>
  );
}
