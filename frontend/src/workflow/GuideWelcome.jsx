// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The first page of every guide and slideshow: what the page is for, and the
// steps the guide goes through.
import { BookOpen } from "lucide-react";

export function Welcome({ heading, lead, learn }) {
  return (
    <div className="ng-snap ng-pg-shot ng-welcome" data-ng-snap="welcome">
      <BookOpen size={34} style={{ color: "var(--primary, #04bade)" }} />
      <div className="ng-welcome-title">{heading}</div>
      <div className="muted">{lead}</div>
      <div className={"ng-welcome-list" + (learn.length > 6 ? " two" : "")} data-ng-welcome-list>
        <div className="small muted">In this guide</div>
        {learn.map((t) => (
          <div key={t} className="ng-pg-row">
            <span className="ng-pg-tick">&#10003;</span>
            <span>{t}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
