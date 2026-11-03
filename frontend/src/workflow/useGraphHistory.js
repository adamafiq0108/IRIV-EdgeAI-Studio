// Undo/redo for the workflow canvas: a bounded stack of {nodes, edges}
// snapshots. Callers push() BEFORE a structural mutation (add/delete/connect/
// paste, drag-end) - never during drag-move, which would flood the stack.
//
// canUndo/canRedo are derived from the stacks. The stacks live in refs (a push
// must never be lost to a stale closure), so a version counter forces the
// re-render that keeps a menu's greyed-out Undo/Redo honest.
import { useCallback, useRef, useState } from "react";

const LIMIT = 50;

export default function useGraphHistory() {
  const past = useRef([]);
  const future = useRef([]);
  const [, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const push = useCallback((nodes, edges) => {
    past.current.push({ nodes, edges });
    if (past.current.length > LIMIT) past.current.shift();
    future.current = [];
    bump();
  }, [bump]);

  const undo = useCallback((nodes, edges) => {
    const prev = past.current.pop();
    if (!prev) return null;
    future.current.push({ nodes, edges });
    bump();
    return prev;
  }, [bump]);

  const redo = useCallback((nodes, edges) => {
    const next = future.current.pop();
    if (!next) return null;
    past.current.push({ nodes, edges });
    bump();
    return next;
  }, [bump]);

  return { push, undo, redo,
           canUndo: past.current.length > 0,
           canRedo: future.current.length > 0 };
}
