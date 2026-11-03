// The settings of the two STATIC cards (Tok Aba, 2026-10-14): a Text card
// (the operator's own words) and an Image card (a picture uploaded to this
// box). They read no data, so instead of the Data section the Builders'
// inspector shows these. Server side: builder._static_fields cleans every
// field, services/builder_assets.py stores and serves the picture.
//
// The words are plain text -- no HTML, no markdown, no clickable links -- and
// the picture is an UPLOAD only (no web address: boxes are often offline, and
// a URL is a request every viewer would make to somebody else). The browser's
// file name is never sent; the server decides what the file is from its bytes.
import { useState } from "react";
import { api, friendlyError } from "../../api/client.js";
import { Field } from "../ui/primitives.jsx";

//: Mirrors builder.MAX_TEXT_BODY / builder_assets.MAX_BYTES (the server
//: enforces both; these only answer early).
export const MAX_TEXT_BODY = 2000;
export const MAX_PICTURE_BYTES = 2 * 1024 * 1024;
const PICTURE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

/** Text card: the words, their size, alignment and weight. */
export function TextCardFields({ current, patch }) {
  const body = String(current.body || "");
  return (
    <>
      <Field label="Text" hint="Plain text. Line breaks are kept; links are not clickable.">
        <textarea className="input" rows={6} maxLength={MAX_TEXT_BODY} value={body}
                  aria-label="Text"
                  onChange={(e) => patch(current.id, { body: e.target.value.slice(0, MAX_TEXT_BODY) })} />
        <span className="muted small">{body.length} / {MAX_TEXT_BODY}</span>
      </Field>
      <Field label="Text size">
        <select className="input" value={current.text_size || "medium"} aria-label="Text size"
                onChange={(e) => patch(current.id, { text_size: e.target.value })}>
          <option value="small">Small</option>
          <option value="medium">Medium</option>
          <option value="large">Large</option>
        </select>
      </Field>
      {/* Alignment and Position are under Appearance, with the stat cards'
          (Tok Aba, 2026-10-14): how the card looks. */}
      <label className="wf-check" style={{ margin: "2px 0 6px" }}>
        <input type="checkbox" checked={!!current.bold} aria-label="Bold"
               onChange={(e) => patch(current.id, { bold: e.target.checked })} />
        <span className="small">Bold</span>
      </label>
    </>
  );
}

/** Image card: upload (or replace) the picture, its description, how it
 *  fits the card, and an optional caption. */
export function ImageCardFields({ current, patch }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const upload = async (file) => {
    setError("");
    if (!file) return;
    if (file.size > MAX_PICTURE_BYTES) { setError("The picture must be at most 2 MB."); return; }
    if (file.type && !PICTURE_TYPES.includes(file.type)) {
      setError("Use a PNG, JPEG, WebP or GIF picture."); return;
    }
    setBusy(true);
    try {
      const res = await api.post("/api/builder/assets", file);
      // The previous picture is removed by the server when the page is
      // saved without it (builder_assets.after_save).
      patch(current.id, { asset: res.id });
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Field label="Picture" hint="PNG, JPEG, WebP or GIF, up to 2 MB and 4096 pixels a side.">
        <input type="file" className="input" aria-label="Upload a picture" disabled={busy}
               accept={PICTURE_TYPES.join(",")}
               onChange={(e) => { upload(e.target.files && e.target.files[0]); e.target.value = ""; }} />
        <span className="muted small">
          {busy ? "Uploading..." : current.asset ? "A picture is set. Choose another to replace it."
            : "No picture yet."}
        </span>
        {error && <span className="small c-warn" role="alert">{error}</span>}
      </Field>
      <Field label="Description" hint="Says what the picture shows, for anyone who cannot see it.">
        <input className="input" maxLength={120} value={current.alt || ""} aria-label="Description"
               onChange={(e) => patch(current.id, { alt: e.target.value })} />
      </Field>
      <Field label="Fit">
        <select className="input" value={current.fit || "contain"} aria-label="Fit"
                onChange={(e) => patch(current.id, { fit: e.target.value })}>
          <option value="contain">Show the whole picture</option>
          <option value="cover">Fill the card (may crop)</option>
        </select>
      </Field>
      <Field label="Caption" hint="A short line under the picture. Blank shows nothing.">
        <input className="input" maxLength={120} value={current.caption || ""} aria-label="Caption"
               onChange={(e) => patch(current.id, { caption: e.target.value })} />
      </Field>
    </>
  );
}
