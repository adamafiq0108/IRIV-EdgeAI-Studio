// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
import { useEffect } from "react";

/**
 * Close on Escape, while `active`.
 *
 * WHY THIS EXISTS. Every overlay in the app dismisses by clicking its
 * backdrop, and a backdrop is a <div> -- there is no way to reach it with a
 * keyboard, and making one (role + tabIndex) would put a meaningless stop in
 * the tab order that reads as "an element" to a screen reader and does
 * nothing when it is focused. The keyboard's answer to a dialog is Escape,
 * not tabbing to the dark part.
 *
 * Bound to the DOCUMENT rather than to the overlay, deliberately: focus is
 * usually inside the dialog and may be nowhere in particular (a portalled
 * dialog opens with focus still on the button that opened it), so a listener
 * on the overlay itself would only fire when something inside it happened to
 * be focused -- which is exactly when the user least needs it.
 */
export function useEscape(active, onEscape) {
  useEffect(() => {
    if (!active) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onEscape();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, onEscape]);
}

/**
 * Make a non-button element behave like one for the keyboard.
 *
 * Spread onto a clickable <div>/<tr>/<li>: it gains a role, a place in the
 * tab order, and Enter/Space -- which is what a real <button> gives for free.
 * Used where a button element cannot be: a table row that expands, a card
 * whose whole surface is the target, a grid tile.
 *
 * Space is preventDefault'ed because its default is to scroll the page, and a
 * control that also scrolls when you activate it is a control that feels
 * broken.
 *
 * `role: false` sets no role at all, for the one case where a role would cost
 * more than it gives: a <tr> that expands. role="button" there REPLACES the
 * row role, and a table whose rows are not rows stops being navigable as a
 * table -- so that row keeps its own role and takes only the tab stop and the
 * keys, with aria-expanded saying what it does.
 */
export function clickable(onActivate, { role = "button", disabled = false } = {}) {
  if (disabled) return {};
  return {
    ...(role ? { role } : {}),
    tabIndex: 0,
    onKeyDown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onActivate(e);
      }
    },
  };
}
