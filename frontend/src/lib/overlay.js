// overlay.js - draw the detection overlay in the BROWSER, from the app's
// metadata, the way the app used to burn it into the frame.
//
// With [video] overlay = "client" the board sends the plain picture and a
// stream of small JSON messages (systems/demo/app/overlay_feed.py build()):
// boxes, zones, links, bubbles, trails and the HUD, every coordinate a
// FRACTION of the frame. This file turns one message into canvas strokes that
// match camera.py annotate() closely: the same colours, 2px lines and the
// same draw order, all in FRAME pixels under one transform, so a line that
// was 2px on a 1280-wide frame stays the same share of the picture at any
// size the page shows it.
//
// Pure on purpose - no React, no fetch - so tests/check_overlayclient.mjs can
// import it with plain node.

//: The measurement green, camera.py's (82, 197, 35) BGR.
export const GREEN = "#23c552";
//: The HUD's text colour, camera.py's (80, 220, 255) BGR.
export const HUD_TEXT = "#ffdc50";

/**
 * The rectangle the picture actually occupies inside its element, in CSS
 * pixels relative to the element's top-left, for a CSS object-fit.
 *
 * WHY this and not the element's own box: a 16:9 camera in a 4:3 tile under
 * `contain` is letterboxed, and boxes mapped onto the ELEMENT would be
 * stretched into the black bars. The browser's own rule is reproduced here
 * (object-position is assumed to be the default, centred).
 */
export function mediaRect(boxW, boxH, natW, natH, fit = "fill") {
  if (!(boxW > 0 && boxH > 0)) return { x: 0, y: 0, w: 0, h: 0 };
  if (!(natW > 0 && natH > 0) || !fit || fit === "fill") {
    return { x: 0, y: 0, w: boxW, h: boxH };
  }
  let s;
  if (fit === "contain") s = Math.min(boxW / natW, boxH / natH);
  else if (fit === "cover") s = Math.max(boxW / natW, boxH / natH);
  else if (fit === "none") s = 1;
  else if (fit === "scale-down") s = Math.min(1, Math.min(boxW / natW, boxH / natH));
  else return { x: 0, y: 0, w: boxW, h: boxH };
  const w = natW * s;
  const h = natH * s;
  return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h };
}

/** A normalised [x, y] -> CSS pixels inside `rect` (for tests and hit work;
 *  drawing goes through the canvas transform instead). */
export function toScreen(rect, x, y) {
  return [rect.x + x * rect.w, rect.y + y * rect.h];
}

/**
 * Split an SSE byte stream (already decoded to text) into complete events.
 * Returns { events: [data string, ...], rest } - `rest` is the incomplete
 * tail to prepend to the next chunk. Comments (": keepalive") and fields
 * other than `data` are dropped.
 */
export function parseSSE(text) {
  const events = [];
  let buf = text.replace(/\r\n/g, "\n");
  let i = buf.indexOf("\n\n");
  while (i >= 0) {
    const block = buf.slice(0, i);
    buf = buf.slice(i + 2);
    const data = block.split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""));
    if (data.length) events.push(data.join("\n"));
    i = buf.indexOf("\n\n");
  }
  return { events, rest: buf };
}

/** Is this message for the feed showing `camera`? "" is the bare request,
 *  which the app answers with its first camera and flags as such. */
export function forCamera(msg, camera) {
  if (!msg) return false;
  if (!camera) return msg.first === 1;
  return msg.cam === camera;
}

// -- the hub's Live Wall stream (core/services/hub_overlay.py) ----------------
// One stream for every system on the wall; each event is an envelope
// {"sys": slug, "m": message} or {"sys": slug, "reset": 1}.

/** The key one wall tile subscribes under: its system and its camera. */
export function hubFeedKey(slug, camera) {
  return `${slug}\u0000${camera == null ? "" : camera}`;
}

/** The query for GET /api/hub/overlay from a set of hubFeedKey()s:
 *  `slugs=a,b` plus one `camera=<slug>:<id>` per tile ("<slug>:" = first). */
export function hubOverlayQuery(keys) {
  const slugs = [];
  const cams = [];
  for (const key of keys) {
    const [slug, camera] = key.split("\u0000");
    if (!slugs.includes(slug)) slugs.push(slug);
    cams.push(`camera=${encodeURIComponent(`${slug}:${camera || ""}`)}`);
  }
  return [`slugs=${slugs.map(encodeURIComponent).join(",")}`, ...cams].join("&");
}

/** What one hub envelope means to the tile showing `camera` of `slug`:
 *  "msg" (draw env.m), "reset" (clear its boxes) or null (not its feed). */
export function routeHubEvent(env, slug, camera) {
  if (!env || env.sys !== slug) return null;
  if (env.reset) return "reset";
  return forCamera(env.m, camera) ? "msg" : null;
}

/** camera.py _face_arrow: the IN arrow of a faced line, frame pixels, as
 *  [tail, tip] - tail on the way-in side, tip on the line. */
export function faceArrow(pts, facing) {
  if ((facing !== "forward" && facing !== "reverse") || pts.length !== 2) return null;
  const [[x1, y1], [x2, y2]] = pts;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.hypot(dx, dy);
  if (length < 1) return null;
  const sign = facing === "reverse" ? -1 : 1;
  const nx = (-dy / length) * sign;
  const ny = (dx / length) * sign;
  const reach = Math.max(18, Math.min(70, length * 0.28));
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  return [[Math.trunc(mx - nx * reach), Math.trunc(my - ny * reach)],
          [Math.trunc(mx), Math.trunc(my)]];
}

function rgba(hex, a) {
  const n = parseInt(String(hex || "#000000").slice(1), 16) || 0;
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

//: Hershey SIMPLEX at OpenCV scale `s` is ~24*s px from baseline to cap top;
//: a sans-serif at 32*s px has about the same cap height and advance.
const font = (s) => `${(32 * s).toFixed(1)}px sans-serif`;

function line(ctx, a, b) {
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
}

function poly(ctx, pts) {
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
}

function dot(ctx, x, y, r) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}

/** cv2.arrowedLine: the shaft, then two strokes at 45 degrees from the tip,
 *  `tip` of the shaft's length. */
function arrow(ctx, from, to, tip) {
  line(ctx, from, to);
  const ang = Math.atan2(from[1] - to[1], from[0] - to[0]);
  const len = Math.hypot(to[0] - from[0], to[1] - from[1]) * tip;
  for (const d of [Math.PI / 4, -Math.PI / 4]) {
    line(ctx, to, [to[0] + len * Math.cos(ang + d), to[1] + len * Math.sin(ang + d)]);
  }
}

/** camera.py _size_tag: black text on a filled tag in the line's colour. */
function sizeTag(ctx, text, at, color, W, H, scale = 0.5) {
  ctx.font = font(scale);
  const tw = ctx.measureText(text).width;
  const th = 24 * scale;
  const base = 5;
  const x = Math.min(Math.max(2, at[0] - tw / 2), W - tw - 6);
  const y = Math.min(Math.max(th + 4, at[1] + th / 2), H - base - 2);
  ctx.fillStyle = color;
  ctx.fillRect(x - 3, y - th - 3, tw + 6, th + base + 5);
  ctx.fillStyle = "#000000";
  ctx.fillText(text, x, y);
}

/**
 * Draw one message onto a canvas whose backing store is `dpr` times its CSS
 * size, with the picture occupying `rect` (CSS px, from mediaRect). Clears
 * first, so a null message leaves the canvas empty.
 */
export function drawOverlay(ctx, msg, rect, dpr = 1) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  if (!msg || !rect || !(msg.w > 0 && msg.h > 0) || !(rect.w > 0 && rect.h > 0)) return;
  const W = msg.w;
  const H = msg.h;
  // From here on everything is in FRAME pixels, exactly the numbers
  // annotate() used; the transform scales them onto the picture.
  ctx.setTransform(dpr * rect.w / W, 0, 0, dpr * rect.h / H, dpr * rect.x, dpr * rect.y);
  const P = ([x, y]) => [x * W, y * H];
  ctx.lineWidth = 2;
  ctx.textBaseline = "alphabetic";

  // Trails at the very bottom; brighter toward the newest point, grey once
  // the object has left.
  for (const [pts, live] of msg.trails || []) {
    const n = pts.length;
    pts.forEach((p, i) => {
      const [x, y] = P(p);
      if (!(x >= 0 && x < W && y >= 0 && y < H)) return;
      const k = 0.4 + 0.6 * (i / Math.max(1, n - 1));
      ctx.fillStyle = live
        ? `rgb(${Math.trunc(255 * k)},${Math.trunc(200 * k)},${Math.trunc(60 * k)})`
        : "#808080";
      dot(ctx, x, y, i === n - 1 ? 3 : 2);
    });
  }
  // Bubbles: an 18% tint and an outline, red while intruded.
  for (const [x1, y1, x2, y2, hit] of msg.bubbles || []) {
    const [a, b] = P([x1, y1]);
    const [c, d] = P([x2, y2]);
    if (c <= a || d <= b) continue;
    const color = hit ? "#ff0000" : "#3ca0c8";
    ctx.fillStyle = rgba(color, 0.18);
    ctx.fillRect(a, b, c - a, d - b);
    ctx.strokeStyle = color;
    ctx.strokeRect(a, b, c - a, d - b);
  }
  // Zones: a line (with its IN arrow) or a polygon with the vertical
  // gradient, 5% at the top to 30% at the bottom, under a firm edge.
  for (const z of msg.zones || []) {
    const pts = (z.pts || []).map(P);
    if (pts.length < 2) continue;
    ctx.strokeStyle = z.color;
    if (z.kind === "line") {
      line(ctx, pts[0], pts[1]);
      const arr = faceArrow(pts, z.facing);
      if (arr) arrow(ctx, arr[0], arr[1], 0.35);
      continue;
    }
    const ys = pts.map((p) => p[1]);
    const top = Math.min(...ys);
    const bot = Math.max(...ys);
    if (bot - top >= 2) {
      const g = ctx.createLinearGradient(0, top, 0, bot);
      g.addColorStop(0, rgba(z.color, 0.05));
      g.addColorStop(1, rgba(z.color, 0.3));
      ctx.fillStyle = g;
      poly(ctx, pts);
      ctx.fill();
    }
    poly(ctx, pts);
    ctx.stroke();
  }
  // Boxes: outline only, the label above, the measured size under, and the
  // measurement lines in green.
  for (const d of msg.dets || []) {
    const [x1, y1] = P([d.box[0], d.box[1]]);
    const [x2, y2] = P([d.box[2], d.box[3]]);
    ctx.strokeStyle = d.color;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.fillStyle = d.color;
    ctx.font = font(0.5);
    const tag = d.id != null ? `#${d.id} ` : "";
    ctx.fillText(`${tag}${d.label} ${Math.round((d.conf || 0) * 100)}%`,
                 x1, Math.max(12, y1 - 6));
    if (d.size) {
      ctx.font = font(0.45);
      ctx.fillText(d.size, x1, Math.min(H - 4, y2 + 15));
    }
    const m = d.measure;
    if (!m) continue;
    ctx.strokeStyle = GREEN;
    if (m.sil && m.sil.length >= 3) {
      poly(ctx, m.sil.map(P));
      ctx.fillStyle = rgba(GREEN, 0.18);
      ctx.fill();
      ctx.stroke();
    }
    for (const [ax, ay, bx, by, text] of m.segs || []) {
      const a = P([ax, ay]);
      const b = P([bx, by]);
      line(ctx, a, b);
      ctx.fillStyle = GREEN;
      dot(ctx, a[0], a[1], 3);
      dot(ctx, b[0], b[1], 3);
      if (text) sizeTag(ctx, text, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], GREEN, W, H);
    }
    if (m.ring) {
      const [cx, cy, r, text] = m.ring;
      const c = P([cx, cy]);
      ctx.beginPath();
      ctx.arc(c[0], c[1], Math.max(1, r * W), 0, Math.PI * 2);
      ctx.stroke();
      if (text) sizeTag(ctx, text, c, GREEN, W, H);
    }
  }
  // Links: red within the threshold, grey otherwise, the distance at the
  // midpoint.
  for (const [x1, y1, x2, y2, within, dist] of msg.links || []) {
    const a = P([x1, y1]);
    const b = P([x2, y2]);
    const color = within ? "#ff0000" : "#aaaaaa";
    ctx.strokeStyle = color;
    line(ctx, a, b);
    ctx.fillStyle = color;
    ctx.font = font(0.45);
    const mx = Math.floor((a[0] + b[0]) / 2);
    const my = Math.floor((a[1] + b[1]) / 2);
    ctx.fillText(`${dist}px`, mx + 4, Math.max(12, my - 4));
  }
  // The HUD, top right, on top of everything: camera.py draw_hud's box.
  const rows = (msg.hud || []).map(([label, value]) =>
    (label ? `${label} : ${value}` : String(value)));
  if (rows.length) {
    const rowH = 18;
    const pad = 8;
    const boxW = Math.max(...rows.map((r) => 12 + 8 * r.length)) + pad;
    const boxH = pad + rowH * rows.length;
    const x0 = Math.max(0, W - boxW);
    ctx.fillStyle = "#000000";
    ctx.fillRect(x0, 0, W - x0, boxH);
    ctx.fillStyle = HUD_TEXT;
    ctx.font = font(0.45);
    rows.forEach((r, i) => ctx.fillText(r, x0 + 6, pad + 5 + rowH * i + 8));
  }
}
