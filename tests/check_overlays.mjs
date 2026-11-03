/**
 * check_overlays.mjs - every popup is fixed to the WINDOW and scrolls itself.
 *
 * THE TRAP, once, so it is written down where it keeps being rediscovered:
 * `position: fixed` resolves against the viewport only while NO ancestor is a
 * containing block, and a transform, filter, backdrop-filter, perspective or
 * `contain` on ANY ancestor makes one. This app has several by design -- the
 * page entrance animation, cards that lift on hover, a blurred sticky header,
 * and React Flow, which transforms the canvas on every pan.
 *
 * When that happens `inset: 0` stops meaning "the window" and starts meaning
 * "this element", so on a long page the backdrop is as tall as the CONTENT and
 * the dialog centred inside it sits halfway down, below the fold, reachable
 * only by scrolling the page it is supposed to be covering.
 *
 * The fix is not to hunt the ancestor -- it is to have no ancestor. Every
 * overlay portals to <body>. This file makes that a rule rather than a habit,
 * because the failure is invisible until somebody opens the dialog on a long
 * page, and it comes back every time a new one is written.
 *
 *     node tests/check_overlays.mjs
 */
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readText(path.join(ROOT, ...p));

let bad = 0;
function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : `\n      ${detail}`));
  if (!ok) bad += 1;
}

// -- 1. every overlay portals to the body ------------------------------------
const OVERLAYS = [
  ["components/ui/primitives.jsx", "the shared Dialog (eight pages use it)"],
  ["components/EmailFootageDialog.jsx", "the email dialog"],
  ["pages/Database.jsx", "the table dialogs"],
  ["pages/DatasetLibrary.jsx", "the dataset preview"],
  ["pages/Footage.jsx", "the footage lightbox"],
  ["workflow/BlueprintEditor.jsx", "the blueprint editor"],
];
for (const [file, what] of OVERLAYS) {
  const text = read("frontend", "src", ...file.split("/"));
  check(`${what} portals to the body`,
        text.includes("createPortal(") && text.includes("document.body"),
        file);
}

// The Database file has two of them, and one portalled while the other did not
// would be exactly the bug that is invisible until somebody opens the second.
const db = read("frontend", "src", "pages", "Database.jsx");
check("...both of the table dialogs, not just the first",
      (db.match(/createPortal\(/g) || []).length === 2
      && (db.match(/db-modal-back/g) || []).length === 2,
      "one portalled and one not is the bug nobody sees until the second "
      + "dialog is opened");

// -- 2. the card is capped at the window and scrolls itself ------------------
const theme = read("frontend", "src", "theme.css");
const CARDS = [
  [".ui-dialog", "the shared dialog"],
  [".db-modal", "the table dialogs"],
  [".fx-lightbox-card", "the footage lightbox"],
  [".modal-card", "the centred modal"],
];
for (const [cls, what] of CARDS) {
  const rule = (theme.match(new RegExp(`\\${cls}\\s*\\{([^}]*)\\}`)) || [])[1] || "";
  check(`${what} (${cls}) is capped at the viewport`,
        /max-height:\s*(calc\()?\s*(min\()?\s*\d+vh|max-height:\s*calc\(100vh/.test(rule),
        `no viewport-relative max-height in: ${rule.trim().slice(0, 80)}`);
  check(`...and scrolls itself, not the page`, /overflow(-y)?:\s*(auto|scroll)/.test(rule),
        rule.trim().slice(0, 80));
}

// -- 3. the backdrop centres its child ---------------------------------------
const BACKDROPS = [".ui-dialog-backdrop", ".db-modal-back", ".fx-lightbox",
                   ".modal-backdrop", ".lightbox"];
for (const cls of BACKDROPS) {
  const rule = (theme.match(new RegExp(`\\${cls}\\s*\\{([^}]*)\\}`)) || [])[1] || "";
  check(`${cls} is fixed to the window and centres what is in it`,
        /position:\s*fixed/.test(rule) && /inset:\s*0/.test(rule)
        && (/place-items:\s*center/.test(rule)
            || (/align-items:\s*center/.test(rule)
                && /justify-content:\s*center/.test(rule))),
        rule.trim().slice(0, 110));
}

// -- 4. the reason, kept where the next person will look ---------------------
check("the trap is explained on the shared Dialog",
      read("frontend", "src", "components", "ui", "primitives.jsx")
        .includes("containing block"),
      "this bug has been fixed three times now; the note is what stops a "
      + "fourth");

console.log();
if (bad) {
  console.log(`${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
