// Toast + error surface. One provider at the shell, `useToast()` anywhere.
//
// Why this exists: every page was reporting failures as a string in its own
// corner, so a failed save on a page you had already scrolled past was silent.
// A toast is the smallest thing that makes an error impossible to miss without
// making it a modal.
//
// Errors carry an optional `detail` (the server's message) that stays collapsed
// until asked for - an operator needs "Save failed", a developer needs the 400.
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { friendlyError } from "../../api/client.js";

const ToastCtx = createContext(null);
// Errors leave after 5 s like the rest: a sticky one piled up
// over the page until each was closed by hand. 0 would mean sticky.
const AUTO_DISMISS_MS = { info: 4000, success: 3500, warn: 8000, error: 5000 };

/**
 * The DETAIL slot is the developer's half of the toast, and it must stay
 * technical.
 *
 * friendlyError() is the OPERATOR's sentence -- "The system couldn't complete
 * that", "You don't have access to do that". Running it on the detail put
 * that sentence underneath a headline that already said the same thing in
 * the same words, and threw away the only copy of the real reason: the
 * collapsed panel repeated the banner, and the 400 was gone.
 *
 * So: friendly on top, technical underneath. Callers hand this a string
 * (`e.message`) most of the time; an ApiError arrives whole, and is unpacked
 * into the three things a developer reads off a screenshot.
 */
function technical(detail) {
  if (!detail || typeof detail === "string") return detail;
  const parts = [];
  if (detail.status) parts.push(`HTTP ${detail.status}`);
  if (detail.message) parts.push(detail.message);
  const rid = detail.data?.rid;
  if (rid) parts.push(`request ${rid}`);
  return parts.join(" - ") || String(detail);
}

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const seq = useRef(0);

  const dismiss = useCallback((id) => {
    setItems((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((kind, message, detail, opts) => {
    const id = ++seq.current;
    setItems((list) => [...list, { id, kind, message, detail,
                                   action: opts?.action, onExpire: opts?.onExpire }]);
    const ms = opts?.duration ?? AUTO_DISMISS_MS[kind] ?? 4000;
    if (ms) setTimeout(() => dismiss(id), ms);
    return id;
  }, [dismiss]);

  /**
   * An UNDOABLE action: the toast holds the operator's regret window open, and
   * the real work only happens when it closes.
   *
   * This is how a delete gets to be one click instead of a click plus a
   * dialog. The dialog asks "are you sure" BEFORE, when the operator has the
   * least information; undo asks nothing and lets them look at the result. The
   * cost is that `commit` must not run until the window expires -- so nothing
   * is deleted server-side while Undo is still on screen, and Undo is a real
   * cancellation rather than a second request hoping to restore something.
   *
   * @param {string}   message  what just (apparently) happened
   * @param {function} commit   runs when the window closes un-undone
   * @param {object}   o        {detail, undoLabel, duration, onUndo}
   */
  const undoable = useCallback((message, commit, o = {}) => {
    const ms = o.duration ?? 7000;
    let undone = false;
    const id = ++seq.current;
    const finish = () => {
      dismiss(id);
      if (!undone) commit?.();
    };
    setItems((list) => [...list, {
      id, kind: "info", message, detail: o.detail,
      action: {
        label: o.undoLabel || "Undo",
        onClick: () => { undone = true; dismiss(id); o.onUndo?.(); },
      },
    }]);
    setTimeout(finish, ms);
    return id;
  }, [dismiss]);

  const api = useMemo(() => ({
    info: (m, d) => push("info", m, d),
    success: (m, d) => push("success", m, d),
    warn: (m, d) => push("warn", m, d),
    // The headline is the operator's. Callers pass a sentence already written
    // for one ("Could not save the camera"); an error object passed by mistake
    // is turned into one rather than shown raw. `o.action` ({label, onClick})
    // is the way out when there is one ("Go to account setup").
    error: (m, d, o) => push("error", typeof m === "string" ? m : friendlyError(m),
                             technical(d), o),
    // Wrap any promise: one call gets you a failure toast with the real reason.
    async run(promise, { success, failure = "Something went wrong" } = {}) {
      try {
        const value = await promise;
        if (success) push("success", success);
        return value;
      } catch (e) {
        push("error", failure, technical(e));
        throw e;
      }
    },
    undoable,
    dismiss,
  }), [push, dismiss, undoable]);

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <div className="toast-body">
              <div className="toast-message">{t.message}</div>
              {t.detail ? <div className="toast-detail">{t.detail}</div> : null}
            </div>
            {t.action ? (
              <button className="toast-action" onClick={t.action.onClick}>
                {t.action.label}
              </button>
            ) : null}
            <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">×</button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastCtx);
  // A page rendered outside the provider must not crash on its first error -
  // that would turn a small bug into a blank screen.
  return ctx || { info() {}, success() {}, warn() {}, error() {}, dismiss() {},
                  // Outside the provider there is no window to undo IN, so the
                  // honest fallback is to do the thing rather than drop it.
                  undoable: (_m, commit) => { commit?.(); },
                  run: (p) => p };
}
