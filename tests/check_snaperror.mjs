// The Preview snapshot's refusal reaches explainCamError as the server's
// sentence, not as "HTTP 409".
//
// The API envelope puts its sentence in `error` ({ok:false, error, event_id,
// action}); `detail` is only the legacy FastAPI shape. snapPreview built its
// Error from `j?.detail || "HTTP <status>"`, so every refusal -- a camera in
// use, no camera chosen -- arrived as a bare status and read as the board's
// fault. Pinned:
//   1. responseError() takes `error` first, then `detail`, then the status
//   2. the envelope's sentence lands on its own camErrors cause
//   3. snapPreview goes through responseError, and no raw fetch anywhere in
//      the frontend builds an Error from `detail` without `error`
//   4. snapPreview POSTs the source in a JSON body, and nothing in the
//      frontend builds a GET /api/detector/snapshot?... -- a camera URL with
//      its password in a query string reached the access logs (a64efa56;
//      the GET is deprecated and logs a WARNING)
//
//     node tests/check_snaperror.mjs
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
let bad = 0;

function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : "\n      " + detail));
  if (!ok) bad += 1;
}

const { responseError, explainCamError } =
  await import("file://" + join(REPO, "frontend", "src", "lib", "camErrors.js"));

const res = (status, body) => ({
  ok: false, status,
  json: async () => { if (body === undefined) throw new Error("no body"); return body; },
});

const BUSY = "cannot open camera '0': in use by another program (ffmpeg, pid 901)";
const env = await responseError(res(409, { ok: false, error: BUSY, event_id: "API-4009" }));
check("the envelope's error is the message", env.message === BUSY, env.message);
check("...with the status kept", env.status === 409, env.status);
const x = explainCamError(env, { retry: "Preview snapshot" });
check("...and explainCamError reads it as the busy camera, not the board",
      x?.code === "busy_program", JSON.stringify(x));

const none = await responseError(res(400, { ok: false,
  error: "no camera chosen - choose a camera first" }));
check("no camera chosen lands on no_source",
      explainCamError(none)?.code === "no_source", JSON.stringify(explainCamError(none)));

const legacy = await responseError(res(409, { detail: BUSY }));
check("a legacy {detail} body still reads", legacy.message === BUSY, legacy.message);
const both = await responseError(res(409, { error: BUSY, detail: "other" }));
check("error wins over detail", both.message === BUSY, both.message);
const empty = await responseError(res(502));
check("no body falls back to the status", empty.message === "HTTP 502" && empty.status === 502,
      empty.message);

// -- the call sites ---------------------------------------------------------
const page = readFileSync(join(REPO, "frontend", "src", "pages", "Detection.jsx"), "utf8");
const snap = page.slice(page.indexOf("async function snapPreview"),
                        page.indexOf("if (error) return <ErrorNote"));
check("snapPreview throws responseError(res)", /throw await responseError\(res\)/.test(snap), snap);
check("...and hands the Error to explainCamError", /explainCamError\(e,/.test(snap), snap);
// apiUrl(): the same path under the reverse proxy's /s/<slug>/ (lib/base.js).
check("snapPreview POSTs to /api/detector/snapshot",
      /fetch\(apiUrl\("\/api\/detector\/snapshot"\),\s*\{\s*method:\s*"POST"/.test(snap), snap);
check("...with the source in a JSON body",
      /body:\s*JSON\.stringify\(body\)/.test(snap) && /\{\s*source,/.test(snap), snap);

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx?|mjs)$/.test(name)) out.push(p);
  }
  return out;
}
const offenders = walk(join(REPO, "frontend", "src"))
  .filter((p) => /new Error\(\s*\w+\?\.detail\s*\|\|/.test(readFileSync(p, "utf8")));
check("no raw fetch builds its Error from detail alone", offenders.length === 0,
      offenders.join(", "));
// The singular route only: /api/detector/snapshots?... (the footage list)
// carries no camera source and stays a GET.
const snapGets = walk(join(REPO, "frontend", "src"))
  .filter((p) => /\/api\/detector\/snapshot\?/.test(readFileSync(p, "utf8")));
check("no frontend code builds a GET /api/detector/snapshot?source=...",
      snapGets.length === 0, snapGets.join(", "));

console.log(bad ? `\n${bad} FAILED` : "\nall good");
process.exit(bad ? 1 : 0);
