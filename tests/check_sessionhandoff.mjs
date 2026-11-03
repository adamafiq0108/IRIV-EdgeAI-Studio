// The session token never appears in a URL: not as a query, not as a fragment,
// not for an instant. goWithSession() hands it over in window.name at the
// moment of leaving; the receiving origin takes it once and empties it, and an
// origin it was not meant for ignores it.
//
//   node tests/check_sessionhandoff.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".sessionhandoff-test.cjs");

await build({
  entryPoints: [join(FRONT, "src/lib/session.js")], bundle: true, format: "cjs",
  platform: "node", outfile: OUT, logLevel: "silent",
});

let bad = 0;
const check = (msg, ok, detail = "") => {
  if (ok) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

function tab(url, name = "") {
  const dom = new JSDOM("<!doctype html><body></body>", { url });
  const nav = [];
  const u = new URL(url);
  const win = { name, location: { href: url, origin: u.origin, assign: (x) => nav.push(x) } };
  global.window = win;
  global.sessionStorage = dom.window.sessionStorage;
  delete req.cache[req.resolve(OUT)];
  return { dom: { window: win }, nav, mod: req(OUT) };
}

// the sender: the hub on :5100 opens a system on :5300
const a = tab("http://192.168.1.21:5100/");
a.mod.setToken("tok123");
a.mod.goWithSession(a.mod.withSession("http://192.168.1.21:5300/"));
check("the address it navigates to has no token in it",
      a.nav.length === 1 && a.nav[0] === "http://192.168.1.21:5300/"
      && !/tok123|session/.test(a.nav[0]), a.nav.join());
const carried = a.dom.window.name;

// a destination nobody marked gets nothing
const b0 = tab("http://192.168.1.21:5100/");
b0.mod.setToken("tok123");
b0.mod.goWithSession("http://evil.example:80/");
check("an unmarked destination gets nothing", b0.dom.window.name === "", b0.dom.window.name);

// the receiver on :5300
const url_b = "http://192.168.1.21:5300/";
const b = tab(url_b, carried);
check("the receiving origin takes the token", b.mod.getToken() === "tok123");
check("...and empties window.name at once", b.dom.window.name === "", b.dom.window.name);
check("...the address stays clean", !/tok123|session/.test(b.dom.window.location.href));

// meant for another origin: ignored, and still emptied
const c = tab("http://192.168.1.21:5400/", carried);
check("another origin ignores it", c.mod.getToken() === "");
check("...and still empties it", c.dom.window.name === "", c.dom.window.name);

// a move that never completes: the timer takes the token back out
{
  const timers = [];
  const realSet = global.setTimeout;
  global.setTimeout = (fn, ms) => { timers.push([fn, ms]); return 0; };
  const x = tab("http://192.168.1.21:5100/", "mine");
  x.mod.setToken("tok123");
  x.mod.goWithSession(x.mod.withSession("http://192.168.1.21:5300/"));
  const armed = x.dom.window.name.startsWith("fxs:");
  timers.forEach(([fn]) => fn());
  global.setTimeout = realSet;
  check("a move that never completes disarms: window.name is back to what it was",
        armed && x.dom.window.name === "mine", x.dom.window.name);
  check("...and the disarm waits a few seconds, not zero",
        timers.some(([, ms]) => ms >= 3000 && ms <= 10000), JSON.stringify(timers.map((t) => t[1])));
}

// malformed names are emptied and adopt nothing
for (const bad of ["fxs:", "fxs:http://192.168.1.21:5300/", "fxs:http://192.168.1.21:5300|"]) {
  const y = tab("http://192.168.1.21:5300", bad);
  check(`malformed "${bad}": nothing adopted, name emptied`,
        y.mod.getToken() === "" && y.dom.window.name === "", y.dom.window.name);
}

// a page with a normal window.name is left alone
const d = tab("http://192.168.1.21:5300/", "my-window");
check("an ordinary window.name is untouched", d.dom.window.name === "my-window" && d.mod.getToken() === "");

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall session hand-off checks passed");
process.exit(0);
