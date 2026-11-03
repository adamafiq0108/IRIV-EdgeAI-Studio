// Shared confirm / form dialogs, composed from the existing Dialog, Input,
// Label and Button components.
//
// Why these exist: sensitive operator actions used to go through the browser's
// prompt() / confirm() / alert(). Those cannot be styled, cannot be reviewed
// before committing, cannot mark an action destructive, and on some browsers
// are suppressed entirely - an action that silently does nothing is worse than
// one that fails loudly. Two shapes cover every case in the app:
//
//   ConfirmDialog  "are you sure", optionally requiring the operator to TYPE a
//                  value (a device label) before a destructive action arms.
//   FormDialog     one or more declared inputs -> a values object.
//
// Both portal to <body> (Dialog does), which is OUTSIDE the .cy-app subtree the
// shadcn design tokens are scoped to - see index.css. That is why the content
// carries `cy-app` itself: without it a dialog opened from a legacy page picks
// up theme.css's meaning of --muted/--accent/--border and renders wrong.
//
// They are also asked from INSIDE other modals -- "Remove this column?" from
// the Database page's Edit-table dialog, for one -- so they sit above every
// modal layer the app has. Dialog's stock z-50 is under .db-modal-back (60)
// and .ui-dialog-backdrop (70): the question opened behind the Edit-table
// modal, the operator saw nothing, and the column could not be removed. The
// top of the pile is .modal-backdrop / .fx-menu at 1000-1001 (theme.css), so
// these go one step over that.
import { useEffect, useState } from "react";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "./dialog.jsx";
import { Button } from "./button.jsx";
import { Input, PasswordInput } from "./input.jsx";
import { Label } from "./label.jsx";

const ASK_LAYER = "z-[1100]";
const ASK_CONTENT = `cy-app ${ASK_LAYER}`;

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  // When set, the operator must type this string exactly before Confirm arms.
  requireText = null,
  requireHint,
  busy = false,
  onConfirm,
}) {
  const [typed, setTyped] = useState("");

  // Never carry a previous answer into the next open - a pre-armed destructive
  // dialog is exactly the accident this component exists to prevent.
  useEffect(() => { if (!open) setTyped(""); }, [open]);

  const armed = !requireText || typed.trim() === String(requireText).trim();

  function submit(e) {
    e?.preventDefault?.();
    if (!armed || busy) return;
    onConfirm?.();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={ASK_CONTENT} overlayClassName={ASK_LAYER}>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>

          {children ? <div className="py-2">{children}</div> : null}

          {requireText ? (
            <div className="grid gap-1.5 py-2">
              <Label htmlFor="confirm-typed">
                {requireHint || <>Type <span className="font-semibold">{requireText}</span> to confirm</>}
              </Label>
              <Input
                id="confirm-typed"
                value={typed}
                autoComplete="off"
                autoFocus
                placeholder={String(requireText)}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
          ) : null}

          <DialogFooter className="mt-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
              {cancelLabel}
            </Button>
            <Button
              type="submit"
              variant={destructive ? "destructive" : "default"}
              disabled={!armed || busy}
            >
              {busy ? "Working…" : confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * fields: [{ name, label, type, required, defaultValue, placeholder, hint, autoComplete }]
 * onSubmit(values) - values only contains non-empty entries, so an optional
 * field left blank is absent rather than "" (the old prompt() flow relied on
 * that distinction when building action params).
 */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  fields = [],
  submitLabel = "Submit",
  cancelLabel = "Cancel",
  destructive = false,
  busy = false,
  onSubmit,
}) {
  const [values, setValues] = useState({});
  const [touched, setTouched] = useState(false);
  // Which password fields are currently readable. Per FIELD, not per dialog:
  // "show the new one while I type it" should not also reveal the current one
  // sitting above it.
  const [shown, setShown] = useState(() => new Set());

  // Seed from the field defaults each time it opens, so reopening after a
  // cancel starts clean rather than showing the last attempt.
  useEffect(() => {
    // Masked again every time it opens. A dialog that reopens showing the last
    // password in clear is a password on screen that nobody asked to see.
    setShown(new Set());
    if (!open) { setTouched(false); return; }
    const seed = {};
    for (const f of fields) seed[f.name] = f.defaultValue == null ? "" : String(f.defaultValue);
    setValues(seed);
    // fields is rebuilt per render by callers; keying on `open` is deliberate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const missing = fields.filter((f) => f.required && !String(values[f.name] ?? "").trim());

  function submit(e) {
    e.preventDefault();
    setTouched(true);
    if (missing.length || busy) return;
    const out = {};
    for (const f of fields) {
      const v = String(values[f.name] ?? "").trim();
      if (v !== "") out[f.name] = v;
    }
    onSubmit?.(out);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={ASK_CONTENT} overlayClassName={ASK_LAYER}>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>

          <div className="grid gap-3 py-3">
            {fields.map((f, i) => {
              const show = touched && f.required && !String(values[f.name] ?? "").trim();
              return (
                <div className="grid gap-1.5" key={f.name}>
                  <Label htmlFor={`fd-${f.name}`}>
                    {f.label || f.name}
                    {/* Muted, not red: "this field is required" is a hint
                        about the form, not a warning about consequences. */}
                    {f.required ? <span className="text-muted-foreground"> *</span> : null}
                  </Label>
                  {/* A field with `options` is a choice, not a typed value.
                      Native select on purpose: it is one control, it is
                      keyboard- and screen-reader-correct for free, and the
                      lists it holds here are short. */}
                  {f.options ? (
                    <select
                      id={`fd-${f.name}`}
                      className="input"
                      value={values[f.name] ?? ""}
                      autoFocus={i === 0}
                      aria-invalid={show || undefined}
                      onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                    >
                      {f.options.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  ) : f.type === "password" ? (
                    <PasswordInput
                      id={`fd-${f.name}`}
                      value={values[f.name] ?? ""}
                      placeholder={f.placeholder}
                      autoComplete={f.autoComplete || "off"}
                      autoFocus={i === 0}
                      aria-invalid={show || undefined}
                      shown={shown.has(f.name)}
                      onToggle={() => setShown((cur) => {
                        const next = new Set(cur);
                        if (next.has(f.name)) next.delete(f.name);
                        else next.add(f.name);
                        return next;
                      })}
                      onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                    />
                  ) : (
                  <Input
                    id={`fd-${f.name}`}
                    type={f.type || "text"}
                    value={values[f.name] ?? ""}
                    placeholder={f.placeholder}
                    autoComplete={f.autoComplete || "off"}
                    autoFocus={i === 0}
                    aria-invalid={show || undefined}
                    onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                  />
                  )}
                  {f.hint ? <p className="text-xs text-muted-foreground">{f.hint}</p> : null}
                  {/* Amber, not red: an unfilled field is recoverable by
                      filling it in -- nothing has been destroyed. */}
                  {show ? <p className="text-xs text-amber-600 dark:text-amber-500">{f.name} is required</p> : null}
                </div>
              );
            })}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
              {cancelLabel}
            </Button>
            <Button type="submit" variant={destructive ? "destructive" : "default"} disabled={busy}>
              {busy ? "Working…" : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
