// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// About - the top of the About page: whose product this is and what version is
// running. From GET /api/hub/about, so the credit reads the same here as in
// `hub_server.py --about`. The logo is the deployment's own mark (brand.toml).
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";

export default function AboutCard() {
  const { brand } = useApp();
  const [about, setAbout] = useState(null);
  useEffect(() => {
    let alive = true;
    api.get("/api/hub/about").then((d) => { if (alive) setAbout(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!about) return null;
  const logo = "/" + String(brand?.logo || "static/img/cytron.png").replace(/^\/+/, "");
  return (
    <div className="rounded-xl border bg-card p-4" data-about>
      <div className="flex flex-wrap items-center gap-4">
        <img src={logo} alt="" className="h-12 w-auto max-w-40 shrink-0 object-contain" />
        <div className="min-w-0 flex-1">
          <div className="text-lg font-semibold leading-tight">{about.product}</div>
          <div className="text-sm text-muted-foreground">Version {about.version}</div>
        </div>
      </div>
      <dl className="mt-4 grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-muted-foreground">Developed by</dt>
          <dd className="font-medium">{about.company}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Website</dt>
          <dd>
            <a href={about.website} target="_blank" rel="noopener noreferrer"
               className="font-medium underline-offset-2 hover:underline">
              {about.website.replace(/^https?:\/\//, "")}
            </a>
          </dd>
        </div>
      </dl>
      <p className="mt-4 text-xs text-muted-foreground">{about.copyright}</p>
    </div>
  );
}
