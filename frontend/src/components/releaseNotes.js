// The release notes an operator reads before taking an update.
//
// Its own module because it is PURE: no React, no browser. That is what
// lets tests/check_releasenotes.mjs run it directly -- importing it from
// the panel dragged in every other import that panel has, one of which
// reads window.location at module scope.

/** What changed, grouped by the area of the product it touched.
 *
 * Returns [{area, items: [text, ...]}] in the order the notes were written.
 *
 * Release notes are written one change per line, and the tag may use either
 * of two shapes -- or both in one tag, since they come from a person:
 *
 *     Area: what changed                  one line, area and change together
 *
 *     Area:                               a heading, then the changes under it
 *     - what changed
 *     - what else changed
 *
 * The first shape is how every tag up to v1.0.6 was written. The second is
 * how the owner lays out a release with thirty lines: eleven starting with
 * "Workflow:" read as a wall, the same eleven under one "Workflow" heading
 * read as a list. Consecutive lines about the same area are folded into one
 * group either way, so the two shapes render identically.
 *
 * They used to be shown as the raw block of text they arrived as, which read
 * as a commit message because that is what it was -- and an operator
 * deciding whether to take an update is not reading a commit message. So the
 * text is turned into entries here rather than trusted to be pre-formatted:
 * people write bullets four different ways, and the list draws its own.
 */
export function releaseNoteGroups(raw) {
  const lines = String(raw || "").replace(/\r\n/g, "\n").split("\n");
  const groups = [];
  let current = null;                    // the heading the next items fall under
  const add = (area, text) => {
    const last = groups[groups.length - 1];
    if (last && last.area === area) last.items.push(text);
    else groups.push({ area, items: [text] });
  };
  for (const line of lines) {
    // Git trailers are metadata about the commit, not about the release.
    if (/^\s*(Co-Authored-By|Signed-off-by|Reviewed-by|Cc)\s*:/i.test(line)) continue;
    let text = line.trim();
    if (!text) continue;
    // A conventional-commit subject ("chore: bump to v2.1.5") is the version
    // restated, and the heading above already says which version this is.
    if (!groups.length && !current
        && /^(chore|docs|feat|fix|refactor|test|build|ci)(\([^)]*\))?!?:/i.test(text)) {
      continue;
    }
    // However the author marked the bullet. The list draws its own.
    const bulleted = /^[-*•·]\s+/.test(text);
    text = text.replace(/^[-*•·]\s+/, "").trim();
    // A line of dashes is a separator somebody typed, not a change.
    if (/^[-=_—–]{2,}$/.test(text)) continue;
    if (!text) continue;
    // "Area:" alone opens a heading; what follows falls under it.
    const heading = !bulleted && /^(?:[IVXivx]+\.\s+)?[A-Za-z][A-Za-z0-9 /&.]{0,48}:$/.test(text);
    if (heading) {
      current = text.slice(0, -1).trim();
      continue;
    }
    // "Area: what changed" names its own area, unless it is a bullet under a
    // heading -- then the colon is the author's own punctuation.
    const m = !bulleted || !current
      ? text.match(/^((?:[IVXivx]+\.\s+)?[A-Za-z][A-Za-z0-9 /&.]{0,48}):\s+(.+)$/) : null;
    if (m) {
      current = null;
      add(m[1].trim(), m[2].trim());
    } else {
      add(current || "", text);
    }
  }
  return groups;
}

/** "I.", "II.", ... for the n-th group heading (1-based). The numbering is
 *  drawn by the card, never written into the notes: the tag stays "Area:"
 *  plus bullets, and the reader gets the outline look Tok Aba asked for
 *  without every author counting sections by hand. */
export function roman(n) {
  const table = [[10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]];
  let out = "";
  let left = Math.max(0, Math.floor(n));
  for (const [value, letters] of table) {
    while (left >= value) { out += letters; left -= value; }
  }
  return out;
}

/** A point's kind, when the author put one in braces at its start:
 *  "{BUG SOLVED} the gauge shows the word" -> {tag: "BUG SOLVED", text: ...}.
 *  The card draws the tag as a small badge; a point without one is drawn
 *  as it is. The vocabulary is the author's (FEATURES UPDATE, BUG SOLVED,
 *  INTERFACE UPDATE, NOTIFICATION UPDATE so far) and is not checked here:
 *  an unknown tag is still a tag, shown in the plain colour. */
export function noteTag(item) {
  const m = String(item || "").match(/^\{([^}]{1,40})\}\s*(.*)$/s);
  if (!m) return { tag: "", text: String(item || "") };
  return { tag: m[1].trim().toUpperCase(), text: m[2].trim() };
}

/** The badge colour for a tag, as Tailwind classes. */
export function tagClass(tag) {
  switch (tag) {
    case "FEATURES UPDATE": return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
    case "BUG SOLVED": return "bg-rose-500/15 text-rose-700 dark:text-rose-300";
    case "INTERFACE UPDATE": return "bg-sky-500/15 text-sky-700 dark:text-sky-300";
    case "NOTIFICATION UPDATE": return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
    default: return "bg-muted text-muted-foreground";
  }
}

/** The same notes as a flat list, "Area: what changed" per entry -- what the
 *  card drew before areas became headings, kept for anything that wants one
 *  line per change (a toast, a test, a log line). A group with no area gives
 *  its items bare. */
export function releaseNoteItems(raw) {
  const out = [];
  for (const g of releaseNoteGroups(raw)) {
    for (const item of g.items) out.push(g.area ? `${g.area}: ${item}` : item);
  }
  return out;
}
