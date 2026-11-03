// "Preview media" -- the pictures and video a built-in template's preview
// shows on every box's Hub home (Tok Aba, 2026-10-22, phase 2). Developer
// hub only: opened from a template's row on the System Templates page.
//
// THE RULE (Tok Aba): at most 5 slides, at most 1 of them a video, every file
// at most 5 MB. Checked here so the button is off before anyone picks a file
// (Add video once a video is in; both Adds at 5 slides) and a wrong file is
// named at once -- and checked again on the server (core/services/
// template_media.py), which also reads the file's first bytes rather than
// trusting its name, so a renamed file is refused there.
//
// Saving is per action: an add uploads at once, a move or a caption is sent
// as the new order, a delete deletes. "Publish templates" sends what is here
// to the boxes, signed, with the templates.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { apiUrl } from "../lib/base.js";
import { authHeaders } from "../lib/session.js";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { Dialog } from "./ui/primitives.jsx";
import { ArrowDown, ArrowUp, Film, ImagePlus, Trash2 } from "lucide-react";

export const MAX_SLIDES = 5;
export const MAX_VIDEOS = 1;
export const MAX_BYTES = 5 * 1024 * 1024;
const IMAGE_TYPES = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif" };
const VIDEO_TYPES = { "video/mp4": "mp4", "video/webm": "webm" };
const IMAGE_EXT = ["jpg", "jpeg", "png", "webp", "gif"];
const VIDEO_EXT = ["mp4", "webm"];
const TYPE_ERROR = "Only JPEG, PNG, WebP or GIF pictures and MP4 or WebM videos can be used.";

/** "3 of 5 slides, video used" -- the line above the slides. */
export function mediaCount(items) {
  const videos = (items || []).filter((it) => it.kind === "video").length;
  return `${(items || []).length} of ${MAX_SLIDES} slides, ${videos ? "video used" : "no video yet"}`;
}

/**
 * What kind a picked file is, or why it cannot be used. The browser's type
 * and the name's extension must agree on an allowed kind; the server reads
 * the bytes themselves.
 */
export function checkFile(file, items) {
  const ext = String(file?.name || "").split(".").pop().toLowerCase();
  const kind = IMAGE_TYPES[file?.type] && IMAGE_EXT.includes(ext) ? "image"
    : VIDEO_TYPES[file?.type] && VIDEO_EXT.includes(ext) ? "video" : "";
  if (!kind) return { error: TYPE_ERROR };
  if (file.size > MAX_BYTES) return { error: "That file is larger than 5 MB. Pick a smaller one." };
  if ((items || []).length >= MAX_SLIDES) {
    return { error: "This template already has 5 slides. Remove one before adding another." };
  }
  if (kind === "video" && (items || []).some((it) => it.kind === "video")) {
    return { error: "This template already has a video, and only one is allowed. Remove it before adding another." };
  }
  return { kind };
}

const fileUrl = (key, it) => apiUrl(`/api/hub/template-media/${encodeURIComponent(key)}/${it.file}`);

export default function TemplateMediaEditor({ template, onClose }) {
  const key = template?.key || "";
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const imageRef = useRef(null);
  const videoRef = useRef(null);
  // Through apiUrl once here; the client's own apiUrl leaves it as is.
  const base = apiUrl(`/api/hub/formats/${encodeURIComponent(key)}/media`);

  const load = useCallback(async () => {
    if (!key) return;
    try {
      setItems((await api.get(base)).items || []);
    } catch (e) { setError(e.message); }
  }, [key, base]);
  useEffect(() => { setError(""); setItems([]); load(); }, [load]);

  const full = items.length >= MAX_SLIDES;
  const videoUsed = items.some((it) => it.kind === "video");

  async function upload(file) {
    const verdict = checkFile(file, items);
    if (verdict.error) { setError(verdict.error); return; }
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      // api.client speaks JSON only; multipart goes through fetch directly.
      const res = await fetch(base, {
        method: "POST", credentials: "same-origin", body: fd, headers: { ...authHeaders() },
      });
      const d = await res.json().catch(() => null);
      if (!res.ok) throw new Error((d && (d.detail || d.error)) || `HTTP ${res.status}`);
      setItems(d.items || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function arrange(next) {
    setBusy(true);
    setError("");
    try {
      const d = await api.put(base, {
        order: next.map((it) => it.file),
        captions: Object.fromEntries(next.map((it) => [it.file, it.caption || ""])),
      });
      setItems(d.items || []);
    } catch (e) {
      setError(e.message);
      load();
    } finally {
      setBusy(false);
    }
  }

  async function drop(it) {
    setBusy(true);
    setError("");
    try {
      setItems((await api.del(`${base}/${it.file}`)).items || []);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const move = (i, by) => {
    const next = [...items];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    arrange(next);
  };

  return (
    <Dialog open={!!template} title={`Preview media: ${template?.label || key}`} onClose={onClose}>
      <div className="space-y-3" data-media-editor={key}>
        <p className="text-sm text-muted-foreground">
          The pictures and video shown when someone opens this template on the
          Hub home. Up to 5 slides, one of them may be a video, 5 MB each.
          Publish templates sends them to every box.
        </p>
        <div
          data-media-drop
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const f = e.dataTransfer?.files?.[0];
            if (f) upload(f);
          }}
          className={"rounded-lg border border-dashed p-3 text-center text-xs text-muted-foreground "
                     + (drag ? "border-primary bg-primary/5" : "")}>
          <p className="mb-2" data-media-count>{mediaCount(items)}</p>
          <div className="flex flex-wrap justify-center gap-2">
            <Button type="button" size="sm" variant="outline" disabled={busy || full}
                    onClick={() => imageRef.current?.click()}>
              <ImagePlus className="size-4" /> Add picture
            </Button>
            <Button type="button" size="sm" variant="outline" disabled={busy || full || videoUsed}
                    title={videoUsed ? "Only one video per template" : undefined}
                    onClick={() => videoRef.current?.click()}>
              <Film className="size-4" /> Add video
            </Button>
          </div>
          <p className="mt-2">or drop a file here</p>
          <input ref={imageRef} type="file" className="hidden" aria-label="Picture file"
                 accept="image/jpeg,image/png,image/webp,image/gif"
                 onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
          <input ref={videoRef} type="file" className="hidden" aria-label="Video file"
                 accept="video/mp4,video/webm"
                 onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
        </div>
        {error ? (
          <p role="alert" data-media-error className="text-sm text-destructive">{error}</p>
        ) : null}
        <ol className="space-y-2">
          {items.map((it, i) => (
            <li key={it.file} data-media-item={it.file}
                className="flex items-center gap-2 rounded-lg border p-2">
              <span className="relative h-12 w-20 shrink-0 overflow-hidden rounded bg-muted">
                {it.kind === "video" ? (
                  <video src={fileUrl(key, it)} muted preload="metadata"
                         className="h-full w-full object-cover" />
                ) : (
                  <img src={fileUrl(key, it)} alt="" className="h-full w-full object-cover" />
                )}
              </span>
              <span className="w-10 shrink-0 text-[11px] text-muted-foreground">
                {it.kind === "video" ? "video" : "picture"}
              </span>
              {/* Keyed on the saved caption: the field starts from it and
                  is rebuilt when a save changes it. */}
              <Input key={it.caption || ""} className="h-8 min-w-0 flex-1 text-xs" maxLength={140}
                     aria-label={`Caption for slide ${i + 1}`} placeholder="Caption (optional)"
                     defaultValue={it.caption || ""} disabled={busy}
                     onBlur={(e) => {
                       const caption = e.target.value.trim();
                       if (caption !== (it.caption || "")) {
                         arrange(items.map((x) => (x.file === it.file ? { ...x, caption } : x)));
                       }
                     }} />
              <Button type="button" size="icon-sm" variant="ghost" aria-label="Move up"
                      disabled={busy || i === 0} onClick={() => move(i, -1)}>
                <ArrowUp className="size-4" />
              </Button>
              <Button type="button" size="icon-sm" variant="ghost" aria-label="Move down"
                      disabled={busy || i === items.length - 1} onClick={() => move(i, 1)}>
                <ArrowDown className="size-4" />
              </Button>
              <Button type="button" size="icon-sm" variant="ghost" aria-label="Delete slide"
                      className="text-destructive" disabled={busy} onClick={() => drop(it)}>
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ol>
      </div>
    </Dialog>
  );
}
