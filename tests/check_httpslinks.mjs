// The HTTPS card's pure words (frontend/src/components/httpsLinks.js), with
// plain node: no bundler, no node_modules.
//   1. the addresses: names then addresses, no localhost/loopback, no bare
//      one-label hostname (no root vouches for one), IPv6 in brackets, the
//      HTTPS port spelled out unless 443 is redirected
//   2. nothing to offer while HTTPS is off or nothing is issued
//   3. the one-line state for off, on, on with 443, on but not answering
//
//   node tests/check_httpslinks.mjs
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const { httpsUrls, httpsState, shortDate } = await import(
  pathToFileURL(join(ROOT, "frontend", "src", "components", "httpsLinks.js")).href);

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const on = {
  enabled: true, listening: true, port: 8443, port443: false,
  leaf: { names: ["iriv-a1.local", "iriv-a1", "localhost"],
          ips: ["127.0.0.1", "::1", "192.168.1.234", "fd00::12"] },
};
const urls = httpsUrls(on);
check("names first, then addresses, with the HTTPS port",
      JSON.stringify(urls) === JSON.stringify([
        "https://iriv-a1.local:8443/",
        "https://192.168.1.234:8443/", "https://[fd00::12]:8443/"]), JSON.stringify(urls));
check("no localhost or loopback offered", !urls.some((u) => /localhost|127\.0\.0\.1|\[::1\]/.test(u)));
check("no bare hostname offered (an older root's certificate may still carry it)",
      !urls.includes("https://iriv-a1:8443/"));
check("443 redirected: no port in the address",
      httpsUrls({ ...on, port443: true })[0] === "https://iriv-a1.local/");
check("off: nothing offered", httpsUrls({ ...on, enabled: false }).length === 0);
check("nothing issued: nothing offered", httpsUrls({ ...on, leaf: null }).length === 0);
check("state off", httpsState({ enabled: false }) === "Off" && httpsState(null) === "Off");
check("state on", httpsState(on) === "On - port 8443");
check("state on with 443", httpsState({ ...on, port443: true }) === "On - ports 443 and 8443");
check("state on but not answering", httpsState({ ...on, listening: false }) === "On, not answering");
check("a date reads as a date", /2027/.test(shortDate("2027-11-11T00:00:00+00:00")));
check("no date, no words", shortDate(null) === "");

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
