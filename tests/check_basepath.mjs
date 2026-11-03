// Every root-relative address in the SPA must survive the reverse proxy.
//
// Under "Use proxy (port 80)" (core/services/webaccess.py) a system's pages
// are served from /s/<slug>/ of the hub's origin. A literal "/api/x" there
// asks the HUB, not the system: a fetch that 404s, a video that never
// starts, a download of the wrong box's file. frontend/src/lib/base.js is the
// one place that knows the prefix; this check holds the tree to it:
//   a. a string that starts with /api/, /video_feed, /footage/, /static/,
//      /system-static/ or /assets/ must be the first argument of api.get/
//      post/put/patch/del/download, usePoll, useApi, apiUrl or pageUrl --
//      or it fails here, naming the file and line
//   b. location.assign/replace, location.href = and window.open never take
//      a root-relative literal
//   c. lib/base.js itself: BASE only from a well-formed <base href>,
//      apiUrl prefixes root paths once and leaves URLs alone, stripBase
//   d. the client sends every call through apiUrl, the router takes BASE,
//      and the build is relative (vite base "./")
//
//   node tests/check_basepath.mjs
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "frontend", "src");
const SKIP = [join(SRC, "components", "reui-library"), join(SRC, "lib", "base.js")];
const WATCHED = ["/api/", "/api?", "/video_feed", "/footage/", "/static/",
                 "/system-static/", "/assets/"];
const ALLOWED_BEFORE = /(?:\bapi\.(?:get|post|put|patch|del|download)|\busePoll|\buseApi|\bapiUrl|\bpageUrl)\s*\(\s*$/;
const NAV_SINK = /(?:location\.(?:assign|replace)\s*\(|location\.href\s*=|window\.open\s*\()\s*$/;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (SKIP.some((s) => p === s || p.startsWith(s + "\\") || p.startsWith(s + "/"))) continue;
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx?|tsx?)$/.test(name)) out.push(p);
  }
  return out;
}

// [{at, quote}] for every string literal start, comments and regex literals
// skipped, template literals followed through ${...} nesting.
function literals(src) {
  const out = [];
  const stack = [];                 // "`" for a template, "{" for code in ${}
  let i = 0;
  let prev = "";                    // last significant code character
  // Not after "<" or ">": in JSX those are tags, and "</div>" is no regex.
  const regexOk = () => prev === "" || /[(,=:[!&|?{};+\-*%~^]/.test(prev);
  while (i < src.length) {
    const c = src[i];
    const top = stack[stack.length - 1];
    if (top === "`") {
      if (c === "\\") { i += 2; continue; }
      if (c === "`") { stack.pop(); prev = "`"; i += 1; continue; }
      if (c === "$" && src[i + 1] === "{") { stack.push("{"); prev = "{"; i += 2; continue; }
      i += 1; continue;
    }
    if (c === "/" && src[i + 1] === "/") { i = src.indexOf("\n", i); if (i < 0) break; continue; }
    if (c === "/" && src[i + 1] === "*") { i = src.indexOf("*/", i + 2); if (i < 0) break; i += 2; continue; }
    if (c === "\"" || c === "'") {
      out.push({ at: i, quote: c });
      let j = i + 1;
      while (j < src.length && src[j] !== c && src[j] !== "\n") j += src[j] === "\\" ? 2 : 1;
      i = j + 1; prev = c; continue;
    }
    if (c === "`") { out.push({ at: i, quote: c }); stack.push("`"); i += 1; continue; }
    if (c === "/" && regexOk()) {
      let j = i + 1, cls = false;
      while (j < src.length && src[j] !== "\n") {
        if (src[j] === "\\") { j += 2; continue; }
        if (src[j] === "[") cls = true;
        else if (src[j] === "]") cls = false;
        else if (src[j] === "/" && !cls) break;
        j += 1;
      }
      i = j + 1; prev = "/"; continue;
    }
    if (c === "{" && top === "{") stack.push("{");
    else if (c === "}" && top === "{") { stack.pop(); }
    if (!/\s/.test(c)) prev = c;
    i += 1;
  }
  return out;
}

const lineOf = (src, at) => src.slice(0, at).split("\n").length;

// -- a + b: the tree --------------------------------------------------------
const offenders = [];
const navs = [];
let seen = 0;
for (const file of walk(SRC)) {
  const src = readFileSync(file, "utf8");
  for (const { at } of literals(src)) {
    const body = src.slice(at + 1, at + 20);
    const before = src.slice(Math.max(0, at - 80), at);
    if (body.startsWith("/") && !body.startsWith("//") && NAV_SINK.test(before)) {
      navs.push(`${relative(ROOT, file)}:${lineOf(src, at)}`);
    }
    if (!WATCHED.some((w) => body.startsWith(w))) continue;
    seen += 1;
    if (!ALLOWED_BEFORE.test(before)) {
      offenders.push(`${relative(ROOT, file)}:${lineOf(src, at)}  ${src.slice(at, at + 50).split("\n")[0]}`);
    }
  }
}
check(`every root-relative literal goes through the client or apiUrl (${seen} seen)`,
      seen > 100 && offenders.length === 0,
      `wrap these in apiUrl() (frontend/src/lib/base.js):\n      ${offenders.join("\n      ")}`);
check("no root-relative literal is handed straight to the browser's navigation",
      navs.length === 0, `use pageUrl():\n      ${navs.join("\n      ")}`);

// the scanner itself, on the shapes that trip a naive one
const probe = 'const re = /filename\\*?=(?:UTF-8\'\')?"?([^";]+)"?/i;\n'
  + 'const t = `a ${x ? "/api/in" : `/api/nested`} b`;\n'
  + '// fetch("/api/comment")\n'
  + 'fetch("/api/raw");\n';
const found = literals(probe).map(({ at }) => probe.slice(at, at + 12));
check("the scanner skips regex literals and comments, follows ${} nesting",
      found.some((s) => s.startsWith('"/api/in"')) && found.some((s) => s.startsWith("`/api/nested"))
      && found.some((s) => s.startsWith('"/api/raw"'))
      && !found.some((s) => s.includes("comment")) && !found.some((s) => s.startsWith("'')")),
      JSON.stringify(found));

// -- c: lib/base.js, loaded with each kind of <base> ----------------------------
const baseUrl = pathToFileURL(join(SRC, "lib", "base.js")).href;
async function load(href, n) {
  globalThis.document = { querySelector: () => (href === null ? null
    : { getAttribute: () => href }) };
  return import(`${baseUrl}?case=${n}`);
}
const proxied = await load("/s/demo/", 1);
check("<base href=/s/demo/> reads as /s/demo", proxied.BASE === "/s/demo");
check("apiUrl prefixes a root path once",
      proxied.apiUrl("/api/x?a=1") === "/s/demo/api/x?a=1"
      && proxied.apiUrl(proxied.apiUrl("/api/x")) === "/s/demo/api/x"
      && proxied.apiUrl("/s/demo") === "/s/demo");
check("...and leaves other origins, protocol-relative and relative URLs alone",
      proxied.apiUrl("http://box/s/other/api/x") === "http://box/s/other/api/x"
      && proxied.apiUrl("//cdn/x") === "//cdn/x" && proxied.apiUrl("api/x") === "api/x"
      && proxied.apiUrl(undefined) === "");
check("stripBase gives the router-relative path",
      proxied.stripBase("/s/demo/account-setup") === "/account-setup"
      && proxied.stripBase("/s/demo") === "/" && proxied.stripBase("/login") === "/login");
check("pageUrl is the same rule", proxied.pageUrl("/account-setup") === "/s/demo/account-setup");
const root = await load("/", 2);
check("<base href=/> (the hub, a system on its port) changes nothing",
      root.BASE === "" && root.apiUrl("/api/x") === "/api/x");
const none = await load(null, 3);
check("no <base> (the Vite dev server) changes nothing", none.BASE === "");
for (const [n, evil] of [[4, "/s/demo/../../x/"], [5, "https://evil/"], [6, "/s/Demo/"],
                         [7, "/s/demo/extra/"], [8, "/s/demo\"/"]]) {
  const m = await load(evil, n);
  check(`a malformed <base href=${evil}> reads as ""`, m.BASE === "");
}

// -- d: the wiring ----------------------------------------------------------------
const client = readFileSync(join(SRC, "api", "client.js"), "utf8");
check("the api client fetches through apiUrl (calls and downloads)",
      (client.match(/fetch\(apiUrl\(path\)/g) || []).length === 2
      && !/fetch\(path[,)]/.test(client));
check("downloadFile fetches through apiUrl",
      /fetch\(apiUrl\(url\)/.test(readFileSync(join(SRC, "lib", "download.js"), "utf8")));
check("the router's basename is BASE",
      /\{ basename: BASE \|\| "\/" \}/.test(readFileSync(join(SRC, "router.jsx"), "utf8")));
check("the build is relative (vite base \"./\")",
      /base: "\.\/"/.test(readFileSync(join(ROOT, "frontend", "vite.config.js"), "utf8")));

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
