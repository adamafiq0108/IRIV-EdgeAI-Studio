// WHICH CARD IS IN FRONT where cards overlap on a Builder page (Tok Aba,
// 2026-10-14: "put at front / put at back"). Pure functions; the Builders'
// Arrange buttons, the canvas chrome and both grids use them.
//
// The model. Cards on the 12-column grid have always been allowed to overlap
// -- dragging one onto another was never refused -- and the one on top was
// whichever came later in the page's list. A card's layer is now `grid.z`
// (builder._clean_grid): higher is in front, a tie keeps the list order, and
// absent is 0. So a page nobody arranges keeps exactly the stacking it had,
// and no new switch is needed: overlap stays allowed on old and new pages.
//
// Arranging RE-NUMBERS the page: the cards are put in their current front-to-
// back order, the one card is moved (one step, or to the end), and each takes
// its position in that order as its z (0, 1, 2 ...). Only cards whose z
// changes are written, and 0 is stored as nothing. That is what makes "one
// step forward" mean one card, however many cards shared a layer before.

/** z of a widget (0 when unset or not a number). */
export const zOf = (w) => {
  const z = w && w.grid ? w.grid.z : undefined;
  return Number.isInteger(z) ? z : 0;
};

/** The widgets' ids from the back to the front. */
export function layerOrder(widgets) {
  return (widgets || []).map((w, i) => [w, i])
    .sort(([a, ia], [b, ib]) => zOf(a) - zOf(b) || ia - ib)
    .map(([w]) => w.id);
}

//: The four moves, in the words the buttons say.
export const ARRANGE = [
  ["front", "Bring to front"], ["forward", "Bring forward"],
  ["backward", "Send backward"], ["back", "Send to back"],
];

/** Whether `op` would move the card (false at the extreme it goes to). */
export function canArrange(widgets, id, op) {
  const order = layerOrder(widgets);
  const i = order.indexOf(id);
  if (i < 0 || order.length < 2) return false;
  return op === "front" || op === "forward" ? i < order.length - 1 : i > 0;
}

/** {id: newGrid} for every card whose layer changes when `id` makes `op`. */
export function arrange(widgets, id, op) {
  const list = widgets || [];
  if (!canArrange(list, id, op)) return {};
  const order = layerOrder(list);
  const i = order.indexOf(id);
  order.splice(i, 1);
  const to = op === "front" ? order.length : op === "back" ? 0
    : op === "forward" ? i + 1 : i - 1;
  order.splice(to, 0, id);
  const out = {};
  order.forEach((wid, z) => {
    const w = list.find((x) => x.id === wid);
    if (!w || zOf(w) === z) return;
    const grid = { ...(w.grid || {}) };
    if (z) grid.z = z; else delete grid.z;
    out[wid] = grid;
  });
  return out;
}
