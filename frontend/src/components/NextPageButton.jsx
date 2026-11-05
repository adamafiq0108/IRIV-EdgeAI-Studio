// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The standard top-right "and now go here" button, on every page that HAS a
// next step: the following page of the same nav section, drawn the way the
// sidebar draws it -- icon, then name -- so the button and the rail entry it
// points at read as the same thing.
//
// The last page of a section renders nothing: pointing across a section
// boundary would invent an order (Workflow then... Database?) that the
// product does not actually have. Pages the backend nav did not emit (a
// capability this box lacks) are skipped over rather than pointed at.
import { Link, useLocation } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useApp, PAGE_PATHS, atPath } from "../state/app.jsx";
import { SECTIONS, PAGE_ICONS } from "./nav-sections.js";

export default function NextPageButton() {
  const { nav } = useApp();
  const location = useLocation();

  const current = (nav || []).find((it) => {
    const to = PAGE_PATHS[it.page];
    return to && (to === "/" ? location.pathname === "/"
                             : atPath(location.pathname, to));
  });
  if (!current) return null;

  const section = SECTIONS.find((s) => s.pages.includes(current.page));
  if (!section) return null;

  // Only pages this box actually offers, in section order.
  const offered = section.pages.filter(
    (p) => PAGE_PATHS[p] && (nav || []).some((it) => it.page === p));
  const next = offered[offered.indexOf(current.page) + 1];
  if (!next) return null;                       // last page of its section

  const label = (nav || []).find((it) => it.page === next)?.label || next;
  const Icon = PAGE_ICONS[next];
  return (
    <Link className="btn" to={PAGE_PATHS[next]}
          title={`Next in ${section.label}: ${label}`}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      {Icon && <Icon size={14} aria-hidden />}
      {label}
      <ArrowRight size={13} aria-hidden />
    </Link>
  );
}
