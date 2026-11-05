// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// InfoHint - the explanation, folded into a mark you can hover.
//
// Why: every setting on this product had its "why" printed underneath it. Each
// paragraph was right, and together they buried the controls -- a five-field
// card read as five headings, five essays and five inputs, and the operator
// scanning for the input had to read past all of it EVERY time. The
// explanation is needed ONCE, on the first encounter; after that it is noise
// sitting where the answer should be.
//
// So the words move behind a mark: the row stays one line, and the reason is
// one hover away for as long as anyone still wants it.
//
// Visibility is still CSS-only (opacity/visibility on :hover/:focus, no
// TooltipProvider, works on every page shell). POSITION is measured on
// hover/focus and applied as `position: fixed`:
//
//   * `absolute` inside a scrollable panel (the workflow inspector, a
//     dialog body) is CLIPPED by that panel's overflow whatever its
//     z-index -- the exact half-a-sentence-cut-off bubble this replaces.
//     `fixed` positions against the viewport and escapes every overflow.
//   * The bubble used to open UPWARD only; the first field of a panel sits
//     near the top of the window, so its hint had nowhere to go. Now it
//     opens above when there is room and below when there is not, and is
//     clamped inside the viewport horizontally.
//
// Measured from this element's own rects only, so it still needs no context
// and nothing outside this file. (The one thing `fixed` cannot survive is a
// CSS-transformed ancestor, which re-anchors it -- no InfoHint renders
// inside the React Flow canvas, the only transformed surface in the app.)
import { useId, useRef, useState } from "react";

/**
 * @param {object} o
 * @param {string} o.children  the explanation (plain text or nodes)
 * @param {string} o.label     accessible name; defaults to "More information"
 */
export function InfoHint({ children, label = "More information" }) {
  const id = useId();
  const dotRef = useRef(null);
  const bubbleRef = useRef(null);
  const [pos, setPos] = useState(null);     // {top, left} once measured
  if (!children) return null;

  function place() {
    const dot = dotRef.current, bubble = bubbleRef.current;
    if (!dot || !bubble) return;
    // The bubble is `visibility: hidden`, not `display: none` -- still laid
    // out, so its size is real before the hover-in transition even starts.
    const d = dot.getBoundingClientRect();
    const bw = bubble.offsetWidth, bh = bubble.offsetHeight;
    const margin = 8, gap = 7;
    const left = Math.max(margin,
      Math.min(d.left - 6, window.innerWidth - bw - margin));
    // Above when it fits, below when the field is near the top of the window.
    const top = d.top - gap - bh >= margin
      ? d.top - gap - bh
      : d.bottom + gap;
    setPos({ top, left });
    // ...then CHECK where it actually landed, and correct by the difference.
    //
    // Everything above is arithmetic on numbers measured while the bubble was
    // still `position: absolute` inside whatever panel it lives in, and it
    // assumes `fixed` will resolve against the window. It does not always: a
    // transformed ancestor -- an animation mid-flight, a zoomed canvas, a
    // panel that slides -- becomes the containing block, and then `left: 400`
    // means 400px from THAT element. The bubble is correctly positioned in a
    // coordinate system nobody asked for, which is how it ended up out by the
    // width of the sidebar and off the edge of the window.
    //
    // Clamping alone would only have dragged it back on screen, still nowhere
    // near the mark that opened it. So this measures the error and removes
    // it: whatever the offset between where we asked for the bubble and where
    // it is, subtract it, and the bubble lands ON the dot again. Then, and
    // only then, keep it inside the window. getBoundingClientRect is always
    // viewport-relative, which is what makes both steps true under any
    // ancestor.
    requestAnimationFrame(() => {
      const b = bubbleRef.current;
      if (!b) return;
      const r = b.getBoundingClientRect();
      let dx = left - r.left, dy = top - r.top;   // the containing-block error
      const w = r.width, h = r.height;
      // Re-clamp in window coordinates, now that the real size is known.
      let wantL = Math.max(margin,
        Math.min(d.left - 6, window.innerWidth - w - margin));
      let wantT = d.top - gap - h >= margin ? d.top - gap - h : d.bottom + gap;
      wantT = Math.max(margin, Math.min(wantT, window.innerHeight - h - margin));
      dx += wantL - left;
      dy += wantT - top;
      if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
        setPos((p) => (p ? { top: p.top + dy, left: p.left + dx } : p));
      }
    });
  }

  return (
    <span className="info-hint">
      <button type="button" ref={dotRef} className="info-hint-dot"
              aria-label={label} aria-describedby={id}
              onMouseEnter={place} onFocus={place}
              // The button does nothing on click: it exists to be hovered and
              // focused. Clicking it must not submit the form it sits in.
              onClick={(e) => e.preventDefault()}>
        i
      </button>
      <span ref={bubbleRef} role="tooltip" id={id} className="info-hint-bubble"
            style={pos ? { position: "fixed", top: pos.top, left: pos.left,
                           bottom: "auto", right: "auto" } : undefined}>
        {children}
      </span>
    </span>
  );
}
