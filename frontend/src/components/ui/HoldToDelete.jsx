// Hold-to-delete: the button arms only after the pointer has been held down
// for its full duration, and releasing early cancels with nothing done.
//
// Why hold rather than one more confirm dialog: a dialog trains the muscle
// memory that gets data deleted -- click, click-through, gone -- because the
// second click is in the same place as the first and costs nothing. A hold
// cannot be muscle-memoried away. It takes a continuous, deliberate three
// seconds, it shows its own progress the whole time, and abandoning it is the
// default (let go) rather than an extra decision.
//
// It does NOT replace the typed-name gate for the widest destructive actions
// ("delete everything for this system") -- that one is about picking the right
// target, which a hold cannot check. Hold answers "did you mean to", typing
// answers "did you mean THIS one".
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "./button.jsx";
import { cn } from "@/lib/utils";

const TICK_MS = 50;

/**
 * @param {object}   o
 * @param {number}   o.holdMs    how long to hold; default 3000
 * @param {function} o.onConfirm fired once, only on a completed hold
 * @param {string}   o.label     resting label
 * @param {string}   o.holdLabel label while holding; default "Hold to delete…"
 * @param {boolean}  o.disabled
 */
export function HoldToDelete({
  onConfirm,
  label = "Delete",
  holdLabel = "Hold to delete…",
  holdMs = 3000,
  disabled = false,
  className,
  size,
  // The countdown fill. A translucent red reads on the soft destructive
  // tint; a button drawn solid red needs a darker fill or the countdown
  // vanishes into it.
  fillClassName = "bg-destructive/30",
  ...rest
}) {
  const [progress, setProgress] = useState(0);     // 0..1
  const timer = useRef(null);
  const startedAt = useRef(0);
  // Guards the one case a plain progress check cannot: pointerup fires after
  // the tick that reached 1.0, and without this the release would count as a
  // second confirm.
  const fired = useRef(false);

  const stop = useCallback(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
    setProgress(0);
  }, []);

  // A hold in flight when the button unmounts (the row it lives in got
  // deleted, the panel closed) must not keep ticking toward a delete.
  useEffect(() => stop, [stop]);

  function start(e) {
    // Primary button only: a right-click opening a context menu never gets a
    // matching pointerup here, which would leave the timer running.
    if (disabled || e.button != null && e.button !== 0) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    fired.current = false;
    startedAt.current = Date.now();
    timer.current = setInterval(() => {
      const p = Math.min(1, (Date.now() - startedAt.current) / holdMs);
      setProgress(p);
      if (p >= 1 && !fired.current) {
        fired.current = true;
        stop();
        onConfirm?.();
      }
    }, TICK_MS);
  }

  const holding = progress > 0;
  const remaining = Math.max(0, Math.ceil((holdMs * (1 - progress)) / 1000));

  return (
    <Button
      type="button"
      variant="destructive"
      size={size}
      disabled={disabled}
      // Pointer events, not mouse/touch pairs: one code path for mouse, touch
      // and pen, and pointercancel (a scroll taking over the gesture on a
      // phone) is a real event we have to treat as "let go".
      onPointerDown={start}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      // Keyboard operators get the same gesture: hold Enter/Space. Repeat
      // keydowns are ignored so the interval is started once.
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !e.repeat) { e.preventDefault(); start(e); }
      }}
      onKeyUp={stop}
      className={cn("relative overflow-hidden", className)}
      aria-label={holding ? `${label} - keep holding` : label}
      {...rest}
    >
      {/* The fill IS the countdown: it reaches the far edge exactly when the
          action fires, so the operator can see how long is left without
          reading anything. aria-hidden -- the label carries the same fact in
          words for anyone not looking at it. */}
      <span
        aria-hidden
        className={cn("absolute inset-y-0 left-0 transition-[width] duration-75 ease-linear", fillClassName)}
        style={{ width: `${progress * 100}%` }}
      />
      <span className="relative tabular-nums">
        {holding ? `${holdLabel} ${remaining}s` : label}
      </span>
    </Button>
  );
}
