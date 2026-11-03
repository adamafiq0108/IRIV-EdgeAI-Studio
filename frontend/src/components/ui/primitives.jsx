// Small shared UI primitives: Badge, EmptyState, Skeleton, Dialog, Field,
// Section.
//
// Deliberately hand-rolled and dependency-free. A component library would be a
// second design system next to theme.css, and the CSP on a published dashboard
// blocks external CSS anyway. Everything here reads its colors from theme.css
// tokens, so a deployment that rebrands gets these for free.
import { InfoHint } from "./InfoHint.jsx";
import { createPortal } from "react-dom";
import { useEffect } from "react";

export function Badge({ tone = "muted", children }) {
  return <span className={`ui-badge ui-badge-${tone}`}>{children}</span>;
}

export function Skeleton({ rows = 3 }) {
  return (
    <div className="ui-skeleton" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="ui-skeleton-row" />)}
    </div>
  );
}

/**
 * What a page shows when it has nothing to show.
 *
 * An empty page is a QUESTION -- "is this broken, or have I just not done the
 * thing yet?" -- so it answers with a line and, where there is one, the button
 * that does the thing. Not a paragraph explaining the feature.
 *
 * `icon` takes either a glyph string (the original call sites pass "▦") or a
 * lucide component, so adopting the framed-icon look did not mean editing
 * every caller. `actions` is the newer shape; `action` (a single node) still
 * works for the callers written before it.
 */
export function EmptyState({ icon = "◇", title, hint, action, actions = [] }) {
  // Decided by "is it a STRING", not by inspecting the component's shape.
  // A lucide icon is a forwardRef OBJECT, not a function, so a
  // `typeof === "function"` test silently failed it and rendered the
  // component object itself as a child -- React error #31, a blank page.
  // Every non-string is something React can render as an element; a string
  // is the glyph.
  const isGlyph = typeof icon === "string";
  const Icon = isGlyph ? null : icon;
  return (
    <div className="ui-empty">
      <div className="ui-empty-icon">
        {isGlyph ? icon : <Icon size={22} strokeWidth={1.75} />}
      </div>
      <div className="ui-empty-title">{title}</div>
      {hint ? <div className="ui-empty-hint">{hint}</div> : null}
      {actions.length > 0 && (
        <div className="ui-empty-actions">
          {actions.map((a, i) => (
            a.to ? (
              <a key={i} href={a.to}
                 className={"btn btn-sm" + (a.primary ? " btn-accent" : "")}>
                {a.label}
              </a>
            ) : (
              <button key={i} type="button" disabled={a.disabled}
                      className={"btn btn-sm" + (a.primary ? " btn-accent" : "")}
                      onClick={a.onClick}>{a.label}</button>
            )
          ))}
        </div>
      )}
      {action}
    </div>
  );
}

// Modal dialog. Escape closes, the backdrop closes, focus is not trapped -
// which is honest about what this is: a confirmation, not an application.
export function Dialog({ open, title, onClose, children, footer }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  // PORTALLED TO THE BODY, and that is the whole fix for "the dialog is
  // centred on the page instead of the window". `position: fixed` resolves
  // against the viewport only while NO ancestor is a containing block, and
  // any transform, filter, backdrop-filter or contain on any ancestor makes
  // one -- the page entrance animation, a card that lifts on hover, a blurred
  // header. Then `inset: 0` becomes the size of that ancestor, so on a long
  // page the backdrop is as tall as the CONTENT and its centred child sits
  // halfway down it, below the fold, needing a scroll to read.
  //
  // Rendering outside the whole app subtree means no ancestor exists to do
  // that -- today's, or the one somebody adds a transform to next year.
  return createPortal(
    <div className="ui-dialog-backdrop" onMouseDown={onClose}>
      <div className="ui-dialog" role="dialog" aria-modal="true"
           onMouseDown={(e) => e.stopPropagation()}>
        <div className="ui-dialog-head">
          <h3>{title}</h3>
          <button className="toast-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="ui-dialog-body">{children}</div>
        {footer ? <div className="ui-dialog-foot">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

/**
 * One section of a form: a heading that says what the fields under it decide.
 *
 * A form with thirty controls in it is not a form, it is a search. Grouping
 * them under the question they answer -- who gets this, what is being sent,
 * how it is attached -- means reading four words instead of thirty labels.
 * `count` is how many of the section's fields are actually SET, so a folded
 * section still reports: "Appearance 2" is a reason to open it, and a bare
 * heading says the defaults are untouched.
 *
 * A <details>, so a heading opens from the keyboard and the browser handles
 * the state. Lives here rather than beside its first caller because the
 * second caller wanted exactly the same thing, and two copies of "what a
 * section looks like" is how two panels end up looking like two products.
 */
export function Section({ title, count = 0, open = true, children }) {
  return (
    <details className="rail-section" open={open}>
      <summary>
        {title}
        {count ? <span className="rail-section-n">{count}</span> : null}
      </summary>
      {children}
    </details>
  );
}

export function Field({ label, hint, children, style }) {
  // The hint rides the LABEL as a hover mark rather than sitting under the
  // input as a second line of prose. Changing it here rather than at the ~46
  // call sites means every form on the product gets the shorter shape at once
  // -- and a form of five fields stops being five inputs and five paragraphs.
  return (
    <label className="ui-field" style={style}>
      <span className="ui-field-label">
        {label}
        {hint ? <InfoHint label={`About ${label}`}>{hint}</InfoHint> : null}
      </span>
      {children}
    </label>
  );
}
