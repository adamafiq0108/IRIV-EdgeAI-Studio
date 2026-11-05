// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Authenticated file downloads.
//
// The export endpoints sit behind the session cookie, so `window.location =
// url` is not an option on a same-origin SPA that must also read the error:
// a 403 would replace the page with a JSON blob instead of showing a toast.
// Fetching with credentials keeps the failure in JavaScript, where it can be
// surfaced, and turns the success into a normal browser download.
import { authHeaders } from "./session.js";
import { apiUrl } from "./base.js";

export async function downloadFile(url, filename) {
  // Fetched, so it CAN carry the per-tab header -- exports act as this tab's
  // user, not the browser-wide cookie user.
  const res = await fetch(apiUrl(url), { method: "GET", credentials: "same-origin",
                                 headers: { ...authHeaders() } });
  if (!res.ok) {
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("application/json") ? await res.json().catch(() => null) : null;
    throw new Error((data && (data.error || data.detail)) || res.statusText || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}
