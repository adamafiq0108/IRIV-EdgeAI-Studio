// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <section className="page active">
      <div className="page-head"><div className="page-title">Not found</div></div>
      <div className="panel-card">
        <p className="muted">That page doesn't exist. <Link to="/">Back to Main</Link>.</p>
      </div>
    </section>
  );
}
