// The first screens are split out of the entry chunk (perf audit item L6,
// 2026-10-02).
//
// Home statically imported Main (and with it the dashboard widgets and the
// charting library, recharts) and Hub; the router statically imported Camera
// Setup; HubShell statically imported Hub. All of it sat in the one chunk
// every page -- the login included -- downloads and parses before painting.
// Measured on the dev build: entry 1,536,284 -> 889,414 bytes (459,791 ->
// 275,577 gzipped). Pinned by source: a static import anywhere puts a page
// back into the entry chunk, so none may exist.
//
//   node tests/check_lazypages.mjs
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "frontend", "src");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) { if (detail) console.log(`      ${detail}`); bad += 1; }
};

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx?|mjs)$/.test(name)) out.push(p);
  }
  return out;
}

const files = walk(SRC);
for (const page of ["Main", "Hub", "Detection"]) {
  const re = new RegExp(`^\\s*import\\s+[^;]*from\\s+["'][./]*(pages/)?${page}\\.jsx["']`, "m");
  const statics = files.filter((f) => re.test(readFileSync(f, "utf8"))
                                      && !f.endsWith(`${page}.jsx`));
  check(`no static import of ${page} anywhere`, statics.length === 0,
        statics.map((f) => f.slice(SRC.length + 1)).join(", "));
}
const read = (p) => readFileSync(join(SRC, p), "utf8");
check("Home splits both of its pages",
      /const Hub = lazy\(\(\) => import\("\.\/Hub\.jsx"\)\)/.test(read("pages/Home.jsx"))
      && /const Main = lazy\(\(\) => import\("\.\/Main\.jsx"\)\)/.test(read("pages/Home.jsx"))
      && read("pages/Home.jsx").includes("<Suspense"));
check("the router splits Camera Setup, both addresses",
      read("router.jsx").includes('const Detection = lazy(() => import("./pages/Detection.jsx"))')
      // With the EDITOR skeleton as the chunk's placeholder: the page's own
      // first render is that shape, so one skeleton stands from click to page
      // (the card-shaped default showed a second, different one before it).
      && read("router.jsx").includes('{ path: "/detection", element: L(<Detection />, "Camera Setup", CAMERA_SKEL) }')
      && read("router.jsx").includes('{ path: "/zones", element: L(<Detection />, "Camera Setup", CAMERA_SKEL) }')
      && read("router.jsx").includes('const CAMERA_SKEL = <SkelEditor title="Camera Setup" />'));
check("HubShell splits the systems grid behind a Suspense",
      read("components/HubShell.jsx").includes('const Hub = lazy(() => import("../pages/Hub.jsx"))'));

if (bad) { console.log(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
