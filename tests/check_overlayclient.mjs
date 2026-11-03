// [video] overlay = "client": the browser draws the boxes, so they have to
// land on the PICTURE, not on the element around it.
//
// A 16:9 camera in a 4:3 dashboard tile under object-fit: contain is
// letterboxed; boxes mapped onto the element's own rectangle would be
// stretched into the black bars and miss every object. lib/overlay.js
// reproduces the browser's object-fit rule and draws in frame pixels under
// one transform. Pinned here with plain node (no node_modules needed - the
// module imports nothing):
//   1. mediaRect: contain / cover / fill / none / scale-down at several
//      aspect ratios, and normalised boxes mapped through it
//   2. parseSSE: events split across chunks, keepalive comments dropped
//   3. forCamera: a bare ("") feed takes the first camera's messages only
//   4. faceArrow matches camera.py _face_arrow (values from the Python)
//   5. drawOverlay against a recording context: the transform is the
//      picture's rectangle, every element is drawn, in the painted colours
//   6. server mode mounts nothing: the canvas exists only behind the
//      "client" answer (source checks; a jsdom render needs node_modules)
//
//   node tests/check_overlayclient.mjs
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const lib = await import(pathToFileURL(join(ROOT, "frontend/src/lib/overlay.js")).href);
const { mediaRect, toScreen, parseSSE, forCamera, faceArrow, drawOverlay } = lib;

let bad = 0;
function check(label, ok, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${ok ? "" : `\n      ${detail}`}`);
  if (!ok) bad += 1;
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;
const rectIs = (r, x, y, w, h) => near(r.x, x) && near(r.y, y) && near(r.w, w) && near(r.h, h);

// -- 1. the picture's rectangle --------------------------------------------------
let r = mediaRect(400, 300, 1920, 1080, "contain");     // 16:9 in 4:3
check("contain, 16:9 in 4:3: bars top and bottom", rectIs(r, 0, 37.5, 400, 225), JSON.stringify(r));
r = mediaRect(400, 300, 640, 480, "contain");           // same aspect
check("contain, same aspect: the whole element", rectIs(r, 0, 0, 400, 300), JSON.stringify(r));
r = mediaRect(640, 360, 640, 480, "contain");           // 4:3 in 16:9
check("contain, 4:3 in 16:9: bars left and right", rectIs(r, 80, 0, 480, 360), JSON.stringify(r));
r = mediaRect(300, 300, 1280, 720, "contain");          // square tile
check("contain, 16:9 in a square", rectIs(r, 0, 65.625, 300, 168.75), JSON.stringify(r));
r = mediaRect(400, 300, 1920, 1080, "cover");
check("cover crops instead (picture wider than the element)",
      near(r.h, 300) && near(r.w, 533.333333, 1e-5) && near(r.x, -66.6666665, 1e-5) && near(r.y, 0),
      JSON.stringify(r));
r = mediaRect(400, 300, 1920, 1080, "fill");
check("fill (an <img> default) stretches to the element", rectIs(r, 0, 0, 400, 300));
r = mediaRect(800, 600, 320, 240, "none");
check("none: natural size, centred", rectIs(r, 240, 180, 320, 240), JSON.stringify(r));
r = mediaRect(800, 600, 320, 240, "scale-down");
check("scale-down never enlarges", rectIs(r, 240, 180, 320, 240), JSON.stringify(r));
r = mediaRect(400, 300, 0, 0, "contain");
check("unknown natural size: the element", rectIs(r, 0, 0, 400, 300));
// A box covering the right half of a 16:9 frame, shown in a 4:3 tile.
const lb = mediaRect(400, 300, 1920, 1080, "contain");
const [ax, ay] = toScreen(lb, 0.5, 0);
const [bx, by] = toScreen(lb, 1, 1);
check("a normalised box lands inside the letterbox, not in the bars",
      near(ax, 200) && near(ay, 37.5) && near(bx, 400) && near(by, 262.5), [ax, ay, bx, by]);
const pill = mediaRect(1000, 200, 640, 480, "contain");
const [cx] = toScreen(pill, 0, 0);
check("a 4:3 frame in a wide strip: x=0 is the picture's edge, not the element's",
      near(cx, (1000 - 200 * 640 / 480) / 2), cx);

// -- 2. server-sent events --------------------------------------------------------
let out = parseSSE("retry: 3000\n\ndata: {\"a\":1}\n\n: keepalive\n\ndata: {\"b\"");
check("complete events come out, comments and retry do not",
      out.events.length === 1 && out.events[0] === "{\"a\":1}", JSON.stringify(out));
out = parseSSE(`${out.rest}:2}\n\n`);
check("an event split across chunks is joined", out.events[0] === "{\"b\":2}" && out.rest === "");
out = parseSSE("data: x\r\n\r\n");
check("CRLF framing is accepted", out.events[0] === "x");

// -- 3. which feed a message is for -------------------------------------------------
check("a named feed takes its own camera only",
      forCamera({ cam: "c2" }, "c2") && !forCamera({ cam: "c1" }, "c2"));
check("a bare feed takes the first camera's messages only",
      forCamera({ cam: "c1", first: 1 }, "") && !forCamera({ cam: "c2" }, ""));

// -- 4. the line zone's arrow, as camera.py draws it ----------------------------------
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
check("faceArrow == _face_arrow (reverse, horizontal)",
      same(faceArrow([[20, 20], [300, 20]], "reverse"), [[160, 90], [160, 20]]));
check("faceArrow == _face_arrow (forward, short)",
      same(faceArrow([[100, 50], [130, 90]], "forward"), [[129, 59], [115, 70]]));
check("faceArrow == _face_arrow (forward, long, clamped)",
      same(faceArrow([[0, 0], [500, 300]], "forward"), [[286, 89], [250, 150]]));
check("an unfaced line has no arrow", faceArrow([[0, 0], [10, 0]], "") === null);

// -- 5. drawing ------------------------------------------------------------------------
function recorder(width, height) {
  const calls = [];
  const ctx = { canvas: { width, height }, calls };
  for (const name of ["setTransform", "clearRect", "fillRect", "strokeRect", "beginPath",
                      "moveTo", "lineTo", "closePath", "stroke", "fill", "arc",
                      "fillText"]) {
    ctx[name] = (...args) => calls.push({ name, args, fillStyle: ctx.fillStyle,
                                          strokeStyle: ctx.strokeStyle, font: ctx.font });
  }
  ctx.measureText = (t) => ({ width: t.length * 8 });
  ctx.createLinearGradient = (...args) => {
    const g = { args, stops: [], addColorStop: (o, c) => g.stops.push([o, c]) };
    calls.push({ name: "gradient", g });
    return g;
  };
  return ctx;
}
const MSG = {
  v: 1, cam: "cam1", seq: 4, ts: 1, w: 640, h: 360,
  trails: [[[[0.1, 0.5], [0.2, 0.5]], 1]],
  bubbles: [[0.4, 0.1, 0.6, 0.4, 1]],
  zones: [{ kind: "poly", pts: [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]], color: "#00ff00" },
          { kind: "line", pts: [[0.0, 0.9], [1.0, 0.9]], color: "#3366ff", facing: "forward" }],
  dets: [{ box: [0.25, 0.25, 0.5, 0.75], label: "person", conf: 0.87, id: 3, color: "#ffaa00" },
         { box: [0.6, 0.2, 0.7, 0.4], label: "person", conf: 0.5, color: "#ff0000", viol: 1,
           size: "1.50 m", measure: { segs: [[0.6, 0.2, 0.7, 0.2, "80 mm"]],
                                      ring: [0.65, 0.3, 0.02, "dia 40 mm"],
                                      sil: [[0.6, 0.2], [0.7, 0.2], [0.7, 0.4]] } }],
  links: [[0.1, 0.1, 0.9, 0.9, 1, 240]],
  hud: [["model", "yolov8s"], ["camera", "cam1"]],
};
const ctx = recorder(800, 600);
const rect = mediaRect(400, 300, 640, 360, "contain");
drawOverlay(ctx, MSG, rect, 2);
const tf = ctx.calls.filter((c) => c.name === "setTransform");
check("the canvas is cleared first, then drawn in frame pixels under the picture's transform",
      ctx.calls[1].name === "clearRect" && tf.length === 2
      && same(tf[1].args, [2 * rect.w / 640, 0, 0, 2 * rect.h / 360, 2 * rect.x, 2 * rect.y]),
      JSON.stringify(tf));
const strokeRects = ctx.calls.filter((c) => c.name === "strokeRect");
check("each box is outlined at its frame-pixel position in its own colour",
      strokeRects.some((c) => same(c.args, [160, 90, 160, 180]) && c.strokeStyle === "#ffaa00")
      && strokeRects.some((c) => c.strokeStyle === "#ff0000" && near(c.args[0], 384)),
      JSON.stringify(strokeRects));
const texts = ctx.calls.filter((c) => c.name === "fillText").map((c) => c.args[0]);
check("labels read as the painted ones: '#id label NN%'",
      texts.includes("#3 person 87%") && texts.includes("person 50%"), texts);
check("the measured size, the size tags, the link distance and the HUD rows",
      ["1.50 m", "80 mm", "dia 40 mm", "240px", "model : yolov8s", "camera : cam1"]
        .every((t) => texts.includes(t)), texts);
const grads = ctx.calls.filter((c) => c.name === "gradient");
check("a polygon zone gets the 5% to 30% vertical gradient in its colour",
      grads.length === 1 && grads[0].g.stops[0][1] === "rgba(0,255,0,0.05)"
      && grads[0].g.stops[1][1] === "rgba(0,255,0,0.3)", JSON.stringify(grads));
check("the bubble is tinted and outlined red while intruded",
      ctx.calls.some((c) => c.name === "fillRect" && c.fillStyle === "rgba(255,0,0,0.18)")
      && strokeRects.some((c) => c.strokeStyle === "#ff0000" && near(c.args[0], 256)));
check("trail dots are drawn (newest bigger)",
      ctx.calls.filter((c) => c.name === "arc").some((c) => c.args[2] === 3));
const hudBox = ctx.calls.find((c) => c.name === "fillRect" && c.fillStyle === "#000000");
const hudW = Math.max(..."model : yolov8s|camera : cam1".split("|").map((t) => 12 + 8 * t.length)) + 8;
check("the HUD box sits top right, sized as draw_hud sizes it",
      hudBox && same(hudBox.args, [640 - hudW, 0, hudW, 8 + 18 * 2]), JSON.stringify(hudBox));
const empty = recorder(10, 10);
drawOverlay(empty, null, rect, 1);
check("no message: cleared and nothing drawn",
      empty.calls.length === 2 && empty.calls[1].name === "clearRect");

// -- 6. server mode mounts nothing --------------------------------------------------------
const rtc = readText(join(ROOT, "frontend/src/components/WebRTCVideo.jsx"));
const img = readText(join(ROOT, "frontend/src/components/StreamImg.jsx"));
const widget = readText(join(ROOT, "frontend/src/components/widgets/Widget.jsx"));
check("WebRTCVideo builds the canvas only from a 'client' answer",
      rtc.includes('info: d?.overlay?.mode === "client" ? d.overlay : null')
      && /const overlay = overlayInfo\s*\?\s*<OverlayCanvas/.test(rtc)
      && rtc.includes(": undefined;"));
check("...never for the depth view (mjpegOnly stays server-painted)",
      rtc.includes("const overlayInfo = !mjpegOnly &&"));
check("...and the wrapper is untouched without one",
      rtc.includes('style={overlay ? { position: "relative", ...style } : style}'));
check("StreamImg renders the overlay slot only when given one, markup unchanged otherwise",
      img.includes('style={overlay ? { position: "relative", ...style } : style}')
      && img.includes("{overlay}"));
check("the dashboard tile mounts it only for a 'client' answer",
      /overlay=\{data\.overlay\?\.mode === "client"\s*\?\s*<OverlayCanvas[\s\S]*?: undefined\}/.test(widget));
const stream = readText(join(ROOT, "frontend/src/lib/overlayStream.js"));
check("the only connection is opened by a subscriber (OverlayCanvas), one per page",
      stream.includes("export function subscribeOverlay")
      && (stream.match(/await fetch\(/g) || []).length === 1);
// The app answers 404 "no such camera" while its cameras open after Start; a
// final 404 there left the view-on-detect tile on its placeholder until reload.
check("a 404 is final only when it says the overlay is drawn on the server; 'no such camera' retries",
      stream.includes("final = /on the server|no such system/i.test(said)")
      && !stream.includes("[401, 403, 404]"));

console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
