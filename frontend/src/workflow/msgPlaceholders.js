// The {placeholders} a message node (Email, Telegram) can use, worked out
// from the canvas the way the compiler works them out (core/services/
// workflow.py), so the inspector's chips offer exactly the names the message
// will resolve. No imports: a plain node test can load it.

// Mirrors core/services/workflow.py _slug.
function slug(text) {
  return String(text || "").trim().toLowerCase()
    .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}

/** Mirrors unique_csv_names: a second "same.csv" is "same_2.csv". */
export function uniqueCsvNames(names) {
  const seen = new Set();
  return names.map((name) => {
    const stem = name.endsWith(".csv") ? name.slice(0, -4) : name;
    let pick = `${stem}.csv`;
    let i = 1;
    while (seen.has(pick)) { i += 1; pick = `${stem}_${i}.csv`; }
    seen.add(pick);
    return pick.slice(0, 60);
  });
}

/** The file name of every CSV node wired into message `msgId`, in wire
 *  order, as the compiler names them (_csv_spec + unique_csv_names). A CSV
 *  node with no Query wired in makes no file, so it offers no chip. */
export function csvNames(msgId, nodes, edges) {
  const byId = new Map((nodes || []).map((n) => [n.id, n]));
  const names = [];
  for (const e of edges || []) {
    if (e.target !== msgId) continue;
    const c = byId.get(e.source);
    if (!c || c.type !== "storage.csv") continue;
    const qEdge = (edges || []).find((x) => x.target === c.id
      && (byId.get(x.source) || {}).type === "storage.query");
    if (!qEdge) continue;
    const q = byId.get(qEdge.source);
    const name = slug((c.data || {}).name) || slug((q.data || {}).name) || "query";
    names.push(`${name}.csv`.slice(0, 60));
  }
  return uniqueCsvNames(names.slice(0, 8));
}

// What the app fills by itself (outputs.py _fill), mirrored from
// core/services/workflow.py MESSAGE_BUILTINS.
export const MESSAGE_BUILTINS = ["fn", "value", "label", "message", "type", "time",
                                 "csv", "csv_text", "csv_rows"];
const PLACEHOLDER_RE = /\{([A-Za-z0-9_:.\-]+)\}/g;

export function csvTokens(files) {
  return (files || []).flatMap((f) => [`csv:${f}`, `csv_text:${f}`, `csv_rows:${f}`]);
}

/** Every {name} in these texts that nothing fills, first-seen order, once
 *  each. Mirrors unknown_placeholders: such a name is sent as written. */
export function unknownPlaceholders(texts, wired, files = []) {
  const known = new Set([...MESSAGE_BUILTINS, ...(wired || []), ...csvTokens(files)]);
  const out = [];
  for (const text of texts || []) {
    for (const m of String(text || "").matchAll(PLACEHOLDER_RE)) {
      if (!known.has(m[1]) && !out.includes(m[1])) out.push(m[1]);
    }
  }
  return out;
}

function distance(a, b) {
  let prev = Array.from({ length: b.length + 1 }, (_x, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    const cur = [i];
    for (let j = 1; j <= b.length; j += 1) {
      cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1,
                        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)));
    }
    prev = cur;
  }
  return prev[b.length];
}

/** Mirrors closest_placeholder: the nearest candidate, or "" when more than
 *  half the name differs. */
export function closestPlaceholder(name, candidates) {
  let best = "";
  let score = null;
  for (const c of candidates || []) {
    const d = distance(name, c);
    if (score === null || d < score) { best = c; score = d; }
  }
  return score !== null && score <= Math.max(2, Math.floor(name.length / 2)) ? best : "";
}
