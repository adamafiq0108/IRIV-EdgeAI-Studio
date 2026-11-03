// A trigger composes with `render`, never with `asChild`.
//
// The menus and popovers in this app are Base UI, which composes a trigger
// with its child through a RENDER PROP. `asChild` is Radix's spelling of the
// same idea, and Base UI does not know it: passing it is not an error, it is
// silently ignored -- so the trigger draws its OWN button around the one it
// was given.
//
// That is a button inside a button: invalid markup, two focus stops for one
// control, and -- the visible half -- an auto-width wrapper around a w-full
// child, which is why the sidebar's session row rendered as a small pill
// floating in the middle of the rail instead of the full-width row every
// other sidebar item is.
//
//   node tests/check_trigger_compose.mjs
import { readFileSync, readdirSync, statSync } from "node:fs";
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "frontend", "src");

// The vendored reui templates are upstream copies, not ours to police: they
// are reference material, not code this app renders.
const SKIP = ["reui-library"];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (SKIP.includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.(jsx?|tsx?)$/.test(name)) out.push(p);
  }
  return out;
}

let bad = 0;
for (const file of walk(SRC)) {
  const src = readText(file);
  src.split("\n").forEach((line, i) => {
    // The PROP, not the word. Explaining in a comment why `asChild` does
    // nothing here is exactly what a comment is for, and a check that
    // forbids naming the mistake forbids documenting it.
    const code = line.replace(/`[^`]*`/g, "").split("//")[0];
    const trimmed = line.trim();
    if (trimmed.startsWith("*") || trimmed.startsWith("/*")
        || trimmed.startsWith("//")) return;
    if (!/(^|\s)asChild(\s|=|>|$)/.test(code)) return;
    console.error(`${file.slice(SRC.length + 1)}:${i + 1}: asChild does `
      + `nothing here -- this is Base UI. Compose with render={<El />} and put `
      + `the contents in the trigger's children.`);
    bad += 1;
  });
}

// ...and the sidebar's session row is the one that showed the symptom, so it
// is pinned by name: full width, composed the way Base UI composes.
const session = readText(join(SRC, "components", "SessionMenu.jsx"));
if (!/render=\{<SidebarMenuButton/.test(session)) {
  console.error("SessionMenu.jsx: the session row must compose with render=");
  bad += 1;
}
if (!/className="w-full"/.test(session)) {
  console.error("SessionMenu.jsx: the session row must fill the sidebar");
  bad += 1;
}

if (bad) {
  console.error(`\n${bad} trigger problem(s).`);
  process.exit(1);
}
console.log("== every trigger composes the way this UI library composes");
