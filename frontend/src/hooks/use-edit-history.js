// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Undo/redo for a page's working copy, with the keyboard shortcuts an operator
// already has in their fingers from every other editor: Ctrl/Cmd+Z to undo,
// Ctrl+Shift+Z or Ctrl+Y to redo.
//
// Why a page needs this at all: a zone is drawn by hand, and drawing by hand
// means mis-drawing by hand. Without undo the only way back from a dragged
// corner or a deleted shape is Discard, which throws away every OTHER edit
// too -- so the operator either loses good work or keeps bad work. Undo makes
// a mistake cost one keystroke instead of a decision.
//
// SNAPSHOTS, not diffs. The state these pages edit is a small array of plain
// objects, so a structural clone per step is cheap and cannot drift out of
// sync the way a hand-written inverse operation can. `LIMIT` keeps a long
// session from growing without bound.
import { useCallback, useEffect, useRef, useState } from "react";

const LIMIT = 50;

/** Cheap deep copy for plain JSON-ish state (arrays of objects). */
function clone(v) {
  return typeof structuredClone === "function"
    ? structuredClone(v)
    : JSON.parse(JSON.stringify(v));
}

/**
 * @param {any}      value    the current working copy (must be JSON-ish)
 * @param {function} setValue how to put a past/future snapshot back
 * @param {object}   o        {enabled} - false disables the shortcuts entirely
 * @returns {{push, undo, redo, canUndo, canRedo, reset}}
 *
 * `push()` is called BEFORE a change, with the state as it is about to stop
 * being. Callers that forget to push simply get no undo step for that edit --
 * never a corrupted one.
 */
export function useEditHistory(value, setValue, { enabled = true } = {}) {
  const past = useRef([]);
  const future = useRef([]);
  // Mirrors `value` without making every callback depend on it, so `push` and
  // the key handler stay stable across renders.
  const latest = useRef(value);
  useEffect(() => { latest.current = value; }, [value]);
  // Only to re-render the buttons; the stacks themselves live in refs.
  const [, bump] = useState(0);
  const rerender = useCallback(() => bump((n) => n + 1), []);

  const push = useCallback(() => {
    past.current = [...past.current, clone(latest.current)].slice(-LIMIT);
    // A new edit after an undo makes the redo branch unreachable -- keeping it
    // would let Redo jump to a state this edit never came from.
    future.current = [];
    rerender();
  }, [rerender]);

  const undo = useCallback(() => {
    if (!past.current.length) return;
    const prev = past.current[past.current.length - 1];
    past.current = past.current.slice(0, -1);
    future.current = [clone(latest.current), ...future.current].slice(0, LIMIT);
    setValue(prev);
    rerender();
  }, [setValue, rerender]);

  const redo = useCallback(() => {
    if (!future.current.length) return;
    const next = future.current[0];
    future.current = future.current.slice(1);
    past.current = [...past.current, clone(latest.current)].slice(-LIMIT);
    setValue(next);
    rerender();
  }, [setValue, rerender]);

  const reset = useCallback(() => {
    past.current = []; future.current = []; rerender();
  }, [rerender]);

  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      // Never steal the shortcut from a field the operator is typing in --
      // Ctrl+Z there means "undo my typing", which the browser does better.
      const el = e.target;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
          || el?.isContentEditable) return;
      const key = String(e.key || "").toLowerCase();
      if (key === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if ((key === "z" && e.shiftKey) || key === "y") { e.preventDefault(); redo(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, undo, redo]);

  return { push, undo, redo, reset,
           canUndo: past.current.length > 0,
           canRedo: future.current.length > 0 };
}
