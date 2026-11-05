// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The audit log's `detail` field, as something React can render.
//
// It is a JSONB column, so it arrives as an OBJECT -- `{}` for most actions,
// which is truthy, so rendering it straight ("{e.detail ? ... : null}") handed
// React an empty object and took the whole page down with "objects are not
// valid as a React child". Some callers pass a plain string instead, and both
// shapes have to survive here.
//
// Its own module, with no imports, because that is what makes it testable: the
// component it lives in pulls in React, the router and the whole UI kit, none
// of which this needs (tests/check_activity.mjs).

/** Every key is kept, never a chosen few: a summary that drops a field is a
 *  log you cannot trust on the one occasion it mattered. Always a string --
 *  that property is what keeps this from being a page crash again. */
export function detailText(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(detailText).filter(Boolean).join(", ");
  if (typeof detail !== "object") return String(detail);
  return Object.entries(detail)
    .map(([k, v]) => `${k}: ${v && typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
}
