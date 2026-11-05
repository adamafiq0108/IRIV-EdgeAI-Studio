// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// SettingsPage - the one shape every page under Settings wears.
//
// The pages here got written at different times and looked it: one opened
// with a legacy `page-title`, one with a shadcn <h2> inside its first card,
// and one with no heading at all. So the header is not each page's business:
// a page supplies its name, a line saying what the setting does, and its
// content; the frame supplies everything else.
//
// The look follows the macOS System Settings pane: the entry's coloured icon
// tile beside a large title, then grouped sections - a small grey heading
// ABOVE a white rounded box, rather than a heading inside a bordered card.
// The tile colour comes from the menu entry (SettingsTone, set by
// SettingsHub), so the page and the menu agree about which pane this is.
//
// Shadcn tokens throughout, because this mounts inside HubShell's `.cy-app`
// subtree where the legacy palette means different things -- `--muted` there
// is a BACKGROUND colour, which is how a settings page shipped with text the
// same shade as the card behind it.
import { createContext, useContext } from "react";

/** The colour of the current pane's icon tile, provided by SettingsHub. */
export const SettingsTone = createContext("#8e8e93");

/** The neutral icon tile the rest of the product uses (the About page's
 *  section headers): a light grey square, the icon drawn in muted ink. On a
 *  selected (blue) row it turns translucent white, so it still reads. `color`
 *  is accepted for callers that pass one and deliberately not used. */
export function IconTile({ icon: Icon, size = 28, active = false }) {
  return (
    <span className={"grid shrink-0 place-items-center rounded-md "
                     + (active ? "bg-white/20 text-white" : "bg-muted text-muted-foreground")}
          style={{ width: size, height: size }}>
      <Icon style={{ width: size * 0.56, height: size * 0.56 }} strokeWidth={1.9} aria-hidden="true" />
    </span>
  );
}

export default function SettingsPage({ title, icon: Icon, description,
                                       actions = null, children }) {
  return (
    <div className="w-full space-y-6">
      <header className="flex items-center gap-4 pt-1">
        {Icon && <IconTile icon={Icon} size={44} />}
        <div className="min-w-0 flex-1">
          <h1 className="text-[26px] font-semibold leading-tight tracking-tight">{title}</h1>
          {description && (
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>
          )}
        </div>
        {/* Page-level actions sit on the title row, not buried above the
            first section, so "what can I do here" is answered in the same
            glance as "where am I". */}
        {actions && (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        )}
      </header>
      {children}
    </div>
  );
}

/** A section within a settings page: a small heading above one rounded box. */
export function SettingsSection({ title, description, actions = null, children }) {
  return (
    <section className="space-y-2">
      {(title || actions) && (
        <div className="flex items-end gap-3 px-1">
          <div className="min-w-0 flex-1">
            {title && (
              <h2 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground/80">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
            )}
          </div>
          {actions && (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          )}
        </div>
      )}
      <div className="rounded-2xl border border-black/[.06] bg-card p-5 shadow-[0_1px_2px_rgba(0,0,0,.04)] dark:border-white/[.08]">
        {children}
      </div>
    </section>
  );
}
