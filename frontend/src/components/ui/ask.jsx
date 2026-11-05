// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// ask.jsx -- confirm and prompt, as the product's own dialogs.
//
// ConfirmDialog and FormDialog already existed, but using them costs a piece
// of state and a rendered element per question -- so a page with eleven
// destructive buttons kept reaching for window.confirm instead, and the
// operator got the browser's chrome: "192.168.1.234:5101 says", the host and
// port announced above the sentence, no styling, no destructive colour, no
// theme. Worse, that box is modal to the whole TAB (it freezes rendering and
// polling behind it) and some browsers suppress it outright after a few --
// an action that silently does nothing is the worst failure of the three.
//
// So the same two dialogs, behind a promise:
//
//     const confirm = useConfirm();
//     if (!await confirm({ title: "Delete X?", destructive: true })) return;
//
// which is the same shape the call sites already had (`if (!confirm(...))
// return;`), one await longer. One dialog element for the whole app, mounted
// beside the toaster in AppShell.
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

import { ConfirmDialog, FormDialog } from "./dialogs.jsx";

const AskCtx = createContext(null);

export function AskProvider({ children }) {
  // {kind, props, resolve} or null. One at a time on purpose: a second
  // question arriving while one is open is a bug in the caller, and stacking
  // modals is how an operator answers the wrong one.
  const [req, setReq] = useState(null);
  const reqRef = useRef(null);
  reqRef.current = req;

  // Settle and close in one step. Called from the dialog's own handlers AND
  // from onOpenChange(false) -- Escape and the backdrop are answers too, and
  // a promise left pending would hang the caller for the life of the page.
  const settle = useCallback((value) => {
    const cur = reqRef.current;
    setReq(null);
    cur?.resolve(value);
  }, []);

  const api = useMemo(() => ({
    confirm: (props) => new Promise((resolve) => {
      setReq({ kind: "confirm", props: props || {}, resolve });
    }),
    ask: (props) => new Promise((resolve) => {
      setReq({ kind: "ask", props: props || {}, resolve });
    }),
  }), []);

  const p = req?.props || {};

  return (
    <AskCtx.Provider value={api}>
      {children}
      <ConfirmDialog
        open={req?.kind === "confirm"}
        onOpenChange={(o) => { if (!o) settle(false); }}
        title={p.title || "Are you sure?"}
        description={p.description}
        confirmLabel={p.confirmLabel || (p.destructive ? "Delete" : "Confirm")}
        cancelLabel={p.cancelLabel || "Cancel"}
        destructive={!!p.destructive}
        requireText={p.requireText || null}
        requireHint={p.requireHint}
        onConfirm={() => settle(true)}
      />
      <FormDialog
        open={req?.kind === "ask"}
        onOpenChange={(o) => { if (!o) settle(null); }}
        title={p.title || ""}
        description={p.description}
        submitLabel={p.submitLabel || "OK"}
        fields={[{ name: "value", label: p.label || "", required: p.required !== false,
                   defaultValue: p.defaultValue, placeholder: p.placeholder }]}
        // The one field's value, not a values object: every caller here is
        // replacing a window.prompt, which answers with a string or nothing.
        onSubmit={(values) => settle(values.value ?? null)}
      />
    </AskCtx.Provider>
  );
}

/** `await confirm({title, description, destructive})` -> true / false.
 *  False for Cancel, Escape and the backdrop alike. */
export function useConfirm() {
  const ctx = useContext(AskCtx);
  // Outside the provider (a test harness, a stray render) the honest answer
  // is "nobody said yes" rather than a crash on a delete path.
  return ctx ? ctx.confirm : async () => false;
}

/** `await ask({title, label, defaultValue})` -> the string, or null. */
export function useAsk() {
  const ctx = useContext(AskCtx);
  return ctx ? ctx.ask : async () => null;
}
