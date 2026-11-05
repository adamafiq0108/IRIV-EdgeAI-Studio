// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The off-the-happy-path guard: leaving an editor with unsaved edits asks
// first, instead of silently throwing the work away.
//
// One hook for every editor page, because the alternative is each page
// inventing its own answer -- and the pages that never got round to it are
// exactly where the work gets lost. It intercepts ROUTER navigation (the
// sidebar's NavLinks, Link, navigate() -- all of them go through the data
// router, so one blocker per page catches every route out) and, separately,
// the browser's own close/reload via beforeunload.
//
// THREE choices, not two. "Cancel" and "Save" are the pair an operator wants
// almost always, so those are the buttons. But a Save that is failing (the
// board is unreachable, the graph does not compile) would trap them on the
// page with no way out, so "Discard" is offered as a quiet third -- present
// when it is needed, never the thing the eye lands on first.
//
// Discard is NOT styled destructive. Red is reserved for actions that destroy
// STORED data; this one abandons an unsaved edit, which is a smaller thing and
// recoverable by doing the edit again. Spending red here would make it mean
// less where it matters.
import { useCallback, useEffect } from "react";
import { useBlocker } from "react-router-dom";

import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../components/ui/dialog.jsx";
import { Button } from "../components/ui/button.jsx";

/**
 * @param {object}   o
 * @param {boolean}  o.when     true while there is unsaved work
 * @param {string}   o.what     what is being left, e.g. "Workflow" -> "Close Workflow?"
 * @param {function} o.onSave   async; resolve = saved, throw/false = stay put
 * @param {string}   o.detail   optional sentence naming what would be lost
 * @param {boolean}  o.busy     true while a save is in flight
 * @returns {JSX.Element} render this anywhere in the page
 */
export function useUnsavedGuard({ when, what = "this page", onSave, detail, busy = false }) {
  const blocker = useBlocker(
    useCallback(({ currentLocation, nextLocation }) =>
      // Not a block when the path did not actually change: a search-param or
      // hash edit (a selected tab, a scrolled anchor) is the same page, and
      // asking there reads as a bug.
      !!when && currentLocation.pathname !== nextLocation.pathname,
    [when]));

  // The browser's own exit (tab close, reload, typed URL) never reaches the
  // router, so it needs the native prompt -- ugly and unstylable, but the only
  // thing a browser will show there.
  useEffect(() => {
    if (!when) return undefined;
    const onBeforeUnload = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [when]);

  const open = blocker.state === "blocked";

  async function saveAndGo() {
    try {
      const ok = await onSave?.();
      if (ok === false) return;          // the page said no; stay put
      blocker.proceed?.();
    } catch {
      // The page has already told the operator what failed (its own toast).
      // Staying blocked is the right outcome: the work still is not saved.
      blocker.reset?.();
    }
  }

  const dialog = (
    <Dialog open={open} onOpenChange={(o) => { if (!o) blocker.reset?.(); }}>
      <DialogContent className="cy-app">
        <DialogHeader>
          <DialogTitle>Close {what}?</DialogTitle>
          <DialogDescription>
            {detail || "Your changes have not been saved yet."}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="mt-2 gap-2 sm:justify-between">
          <Button variant="ghost" disabled={busy}
                  onClick={() => blocker.proceed?.()}>
            Discard
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" disabled={busy}
                    onClick={() => blocker.reset?.()}>
              Cancel
            </Button>
            <Button disabled={busy} onClick={saveAndGo}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  return dialog;
}
