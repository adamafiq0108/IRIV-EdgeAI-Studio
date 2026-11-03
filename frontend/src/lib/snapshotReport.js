// snapshotReport.js - one snapshot as a PDF violation notice.
//
// The notice is built from the record the master hands the photo window
// (/api/detector/snapshots/<id>/meta, services/snapshot_meta.py): the photo at
// its own shape, the detection id, time and date, what was detected, where and
// on which device, the timer when a real one was running, and -- when the user
// ticks it -- a page for each close-up of a detected object. Nothing unknown is
// invented: a missing value prints "--", and a timer that has no data is left
// out altogether.
//
// jsPDF is loaded only when the button is pressed. assembleNotice() takes it as
// an argument so tests/check_snapshotreport.mjs can run the real library with a
// stand-in picture.

const PAGE = { w: 210, h: 297, m: 14 };
const RED = [179, 38, 30];
const INK = [26, 31, 44];
const GREY = [110, 118, 135];
const LINE = [214, 219, 228];

/** "--" for anything empty, so a blank is a visible dash and not a gap. */
export const noticeDash = (v) => (v === null || v === undefined || String(v).trim() === "" ? "--" : String(v));

/** Seconds as the notice writes them: "20 seconds", "1 second", "20.5 seconds". */
export function secondsText(s) {
  const n = Number(s);
  if (!Number.isFinite(n)) return "--";
  const v = Math.abs(n % 1) < 0.05 ? Math.round(n) : Math.round(n * 10) / 10;
  return `${v} ${v === 1 ? "second" : "seconds"}`;
}

/** "Going up" for "up", "Going down" for "down"; anything else as it came. */
export function directionText(d) {
  const t = String(d || "").trim();
  if (!t) return "";
  const k = t.toLowerCase();
  if (k === "up" || k === "going up") return "Going up";
  if (k === "down" || k === "going down") return "Going down";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** The notice number: the day and the second the photo was taken. */
export function noticeNumber(view) {
  const d = String(view?.date || "").replace(/-/g, "");
  const t = String(view?.time || "").replace(/:/g, "");
  return d && t ? `${d}-${t}` : noticeDash(view?.detection_id);
}

/** What was detected: the words the Snapshot node was given, else the class of
 *  the object the photo is about, else nothing ("--"). */
export function detectedText(view) {
  if (view?.violation) return view.violation;
  const crops = view?.crops || [];
  const lead = crops.find((c) => c.primary) || crops[0];
  const label = lead?.label || view?.label || "";
  return label ? label.charAt(0).toUpperCase() + label.slice(1).toLowerCase() : "";
}

/**
 * The timer rows, or null when the snapshot has no timer. The same figures the
 * photo window shows: configured threshold, total duration and the time past
 * the threshold. While the stay is still running the total is not known, so the
 * notice says what WAS known and shows no excess.
 */
export function timerRows(timer) {
  if (!timer || !(timer.threshold_s > 0)) return null;
  const rows = [["Configured timer", secondsText(timer.threshold_s)]];
  if (timer.tolerance_s > 0) rows.push(["Tolerance", `+${secondsText(timer.tolerance_s)}`]);
  if (timer.ended && timer.total_s !== null && timer.total_s !== undefined) {
    rows.push(["Total duration", secondsText(timer.total_s)]);
    rows.push(["Violation duration",
               timer.exceeded_s > 0 ? `+${secondsText(timer.exceeded_s)}`
                                    : "0 seconds (the timer was not exceeded)"]);
  } else {
    rows.push(["Total duration",
               `still running when photographed (at least ${secondsText(timer.elapsed_s)})`]);
    rows.push(["Violation duration", "not known yet"]);
  }
  return rows;
}

/** "2026-10-30 11:19" in the browser's clock. */
export function createdText(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** A safe file name: the detection id, cleaned. */
export function noticeFileName(view) {
  const id = String(view?.detection_id || view?.id || "snapshot").replace(/[^A-Za-z0-9_-]+/g, "-");
  return `violation-notice-${id}.pdf`;
}

/** A picture fitted inside a box, keeping its shape. */
export function fitPicture(w, h, boxW, boxH) {
  if (!(w > 0) || !(h > 0)) return { w: boxW, h: boxH };
  const k = Math.min(boxW / w, boxH / h);
  return { w: w * k, h: h * k };
}

function header(doc, { systemName, device, title, notice }) {
  const L = PAGE.m;
  const R = PAGE.w - PAGE.m;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  doc.setTextColor(...INK);
  doc.text(`IRIV EdgeAI Studio · ${systemName}`, L, 17);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8.5);
  doc.setTextColor(...GREY);
  if (device) doc.text(device, L, 22.5);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  doc.setTextColor(...RED);
  doc.text(title, R, 17, { align: "right" });
  doc.setFontSize(9);
  doc.setTextColor(...INK);
  doc.text(`Notice no. ${notice}`, R, 22.5, { align: "right" });
  doc.setDrawColor(...RED);
  doc.setLineWidth(0.5);
  doc.line(L, 27, R, 27);
}

function label(doc, text, x, y) {
  doc.setFont("helvetica", "bold");
  doc.setFontSize(6.5);
  doc.setTextColor(...GREY);
  doc.text(text.toUpperCase(), x, y);
}

function value(doc, text, x, y, size = 10, color = INK) {
  doc.setFont("helvetica", "bold");
  doc.setFontSize(size);
  doc.setTextColor(...color);
  doc.text(text, x, y);
}

function description(view, systemName) {
  const device = view.device ? ` on ${view.device}` : "";
  const parts = [`The violation was detected by the IRIV EdgeAI Studio monitor${device} of ${systemName}, ` +
    "and the photo was taken at the same moment. The time comes from the device clock."];
  if (view.timer) {
    const t = view.timer;
    if (t.ended && t.exceeded_s > 0) {
      parts.push(`The timer was set to ${secondsText(t.threshold_s)}; the object stayed ` +
                 `${secondsText(t.total_s)}, which is ${secondsText(t.exceeded_s)} over.`);
    } else if (t.ended) {
      parts.push(`The timer was set to ${secondsText(t.threshold_s)}; the object stayed ` +
                 `${secondsText(t.total_s)}, so the timer was not exceeded.`);
    } else {
      parts.push(`The timer was set to ${secondsText(t.threshold_s)}. The object was still ` +
                 "there when the photo was taken, so the total time is not known.");
    }
  }
  return parts.join(" ");
}

/**
 * Build the notice.
 *   view    the snapshot's record (snapshot_meta.view)
 *   main    { dataUrl, w, h } the photo
 *   crops   [{ dataUrl, w, h, label, confidence, primary }] to add, or []
 *   systemName, created
 * Returns the jsPDF document.
 */
export function assembleNotice(jsPDF, { view, main, crops = [], systemName = "System",
                                        created = createdText() }) {
  const doc = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });
  const L = PAGE.m;
  const W = PAGE.w - 2 * PAGE.m;
  const title = view.notice_title || "VIOLATION NOTICE";
  const notice = noticeNumber(view);
  const head = { systemName, device: view.device, title, notice };

  // -- page 1: the photo, the facts, the timer ---------------------------------
  header(doc, head);
  let y = 32;
  const box = fitPicture(main.w, main.h, W, 120);
  doc.addImage(main.dataUrl, "JPEG", L + (W - box.w) / 2, y, box.w, box.h, undefined, "FAST");
  y += box.h + 5;

  // Detection ID | Time | Date
  doc.setDrawColor(...LINE);
  doc.setLineWidth(0.3);
  doc.roundedRect(L, y, W, 15, 1.5, 1.5);
  const cols = [[L + 4, "Detection ID", noticeDash(view.detection_id)],
                [L + W * 0.52, "Time", noticeDash(view.time)],
                [L + W * 0.76, "Date", noticeDash(view.date)]];
  cols.forEach(([x, name, val], i) => {
    if (i > 0) doc.line(x - 4, y + 2, x - 4, y + 13);
    label(doc, name, x, y + 5.5);
    value(doc, val, x, y + 11);
  });
  y += 22;

  // Observation | Place / Device
  const mid = L + W * 0.52;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(...RED);
  doc.text("OBSERVATION", L, y);
  doc.text("PLACE / DEVICE", mid, y);
  label(doc, "Detected", L, y + 7);
  value(doc, noticeDash(detectedText(view)), L, y + 14, 15, RED);
  // Direction and Location are printed only when something says them: a blank
  // one is left out, not drawn as "--".
  if (view.direction) {
    label(doc, "Direction", L, y + 22);
    value(doc, noticeDash(directionText(view.direction)), L, y + 27.5);
  }
  let py = y + 7;
  if (view.location) {
    label(doc, "Location", mid, py);
    value(doc, noticeDash(view.location), mid, py + 5.5);
    py += 13;
  }
  label(doc, "Device", mid, py);
  value(doc, noticeDash(view.device), mid, py + 5.5);
  y += 36;

  // the timer, only when a real one was running
  const rows = timerRows(view.timer);
  if (rows) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8);
    doc.setTextColor(...RED);
    doc.text("TIMER", L, y);
    y += 3;
    doc.setDrawColor(...LINE);
    doc.setLineWidth(0.3);
    doc.roundedRect(L, y, W, rows.length * 7 + 2, 1.5, 1.5);
    rows.forEach(([name, val], i) => {
      const ry = y + 6 + i * 7;
      doc.setFont("helvetica", "normal");
      doc.setFontSize(9);
      doc.setTextColor(...GREY);
      doc.text(name, L + 4, ry);
      doc.setFont("helvetica", "bold");
      doc.setTextColor(...INK);
      doc.text(val, L + W * 0.38, ry);
    });
    y += rows.length * 7 + 7;
  }

  doc.setDrawColor(...LINE);
  doc.line(L, y, L + W, y);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(...GREY);
  doc.text(doc.splitTextToSize(description(view, systemName), W), L, y + 5);

  // -- one page for each close-up the user asked for -----------------------------
  crops.forEach((c, i) => {
    doc.addPage();
    header(doc, head);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(9);
    doc.setTextColor(...RED);
    doc.text(`CLOSE-UP ${i + 1} OF ${crops.length}`, L, 36);
    const f = fitPicture(c.w, c.h, W, 170);
    doc.addImage(c.dataUrl, "JPEG", L + (W - f.w) / 2, 40, f.w, f.h, undefined, "FAST");
    const by = 40 + f.h + 8;
    label(doc, "Detected object", L, by);
    value(doc, noticeDash(c.label), L, by + 6);
    label(doc, "Confidence", L + W * 0.52, by);
    value(doc, c.confidence === null || c.confidence === undefined ? "--"
      : `${Math.round(Number(c.confidence) * 100)}%`, L + W * 0.52, by + 6);
    if (c.primary) {
      doc.setFont("helvetica", "normal");
      doc.setFontSize(8);
      doc.setTextColor(...GREY);
      doc.text("This is the object the notice is about.", L, by + 13);
    }
  });

  // -- every page: when it was made, and the page number -------------------------
  const n = doc.getNumberOfPages();
  for (let p = 1; p <= n; p += 1) {
    doc.setPage(p);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(140, 146, 158);
    doc.text(`Created ${created}`, L, PAGE.h - 8);
    doc.text(`Page ${p} of ${n}`, PAGE.w - PAGE.m, PAGE.h - 8, { align: "right" });
  }
  return doc;
}

async function loadPicture(url) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`The picture could not be read (${res.status}).`);
  const blob = await res.blob();
  const dataUrl = await new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(r.result);
    r.onerror = () => fail(new Error("The picture could not be read."));
    r.readAsDataURL(blob);
  });
  const size = await new Promise((ok) => {
    const img = new Image();
    img.onload = () => ok({ w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => ok({ w: 0, h: 0 });
    img.src = dataUrl;
  });
  return { dataUrl, ...size };
}

/**
 * Fetch the pictures, build the notice and hand it to the browser.
 *   photoUrl  the full photo; cropUrl(n) a close-up's address
 *   includeCrops  add a page per close-up (only when the record has any)
 */
export async function downloadNotice({ view, photoUrl, cropUrl, includeCrops,
                                       systemName }) {
  const { jsPDF } = await import("jspdf");
  const main = await loadPicture(photoUrl);
  const crops = [];
  if (includeCrops) {
    for (const c of view.crops || []) {
      crops.push({ ...(await loadPicture(cropUrl(c.n))), label: c.label,
                   confidence: c.confidence, primary: c.primary });
    }
  }
  const doc = assembleNotice(jsPDF, { view, main, crops, systemName });
  doc.save(noticeFileName(view));
  return { pages: doc.getNumberOfPages() };
}
