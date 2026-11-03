// SettingsPage - the one shape every page under Settings wears.
//
// The three pages here got written at different times and looked it: one
// opened with a legacy `page-title`, one with a shadcn <h2> inside its first
// card, and one with no heading at all, straight into a tab strip. Three
// answers to "what page am I on" on three pages that are the same KIND of
// page - box-wide decisions, managed once, admin only.
//
// So the header is not each page's business any more. A page supplies its
// name, a line saying what the setting does, and its content; the frame
// supplies everything else. Width is capped here too: these are forms and
// tables, and a settings row stretched across a 1600px screen puts the
// control a hand's width from the label it belongs to.
//
// Shadcn tokens throughout, because this mounts inside HubShell's `.cy-app`
// subtree where the legacy palette means different things -- `--muted` there
// is a BACKGROUND colour, which is how a settings page shipped with text the
// same shade as the card behind it.
export default function SettingsPage({ title, icon: Icon, description,
                                       actions = null, children }) {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-4">
      <header className="flex items-start gap-3">
        {Icon && (
          <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground"
                aria-hidden="true" />
        )}
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold leading-tight">{title}</h1>
          {description && (
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          )}
        </div>
        {/* Page-level actions sit on the title row, not buried above the
            first card, so "what can I do here" is answered in the same
            glance as "where am I". */}
        {actions && (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        )}
      </header>
      {children}
    </div>
  );
}

/** A section within a settings page: one card, one heading, one job. */
export function SettingsSection({ title, description, actions = null, children }) {
  return (
    <section className="rounded-xl border bg-card p-5">
      {(title || actions) && (
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            {title && (
              <h2 className="flex items-center gap-1.5 text-base font-semibold">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-1 text-sm text-muted-foreground">{description}</p>
            )}
          </div>
          {actions && (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          )}
        </div>
      )}
      {children}
    </section>
  );
}
