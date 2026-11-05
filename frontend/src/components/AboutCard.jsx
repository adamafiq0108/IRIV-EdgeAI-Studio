// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// About - the headline of the About page: whose product this is and which
// version is running. From GET /api/hub/about, so the credit reads the same
// here as in `hub_server.py --about`. The logo is the deployment's own mark
// (brand.toml); `hostname` is the box's name, passed in from the health reading.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";

function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate font-medium">{children}</dd>
    </div>
  );
}

export default function AboutCard({ hostname }) {
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
    <section className="overflow-hidden rounded-xl border bg-card" data-about>
      <div className="h-1" style={{ background: "var(--primary, #04bade)" }} />
      <div className="flex flex-wrap items-center gap-5 p-5">
        <img src={logo} alt="" className="h-14 w-auto max-w-44 shrink-0 object-contain" />
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold leading-tight tracking-tight">{about.product}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span className="rounded-full border px-2.5 py-0.5 text-xs font-medium text-foreground">
              Version {about.version}
            </span>
            <span>by {about.company}</span>
          </div>
        </div>
      </div>
      <dl className="grid gap-x-8 gap-y-3 border-t px-5 py-4 text-sm sm:grid-cols-3">
        <Fact label="Developed by">{about.company}</Fact>
        <Fact label="Website">
          <a href={about.website} target="_blank" rel="noopener noreferrer"
             className="underline-offset-2 hover:underline">
            {about.website.replace(/^https?:\/\//, "")}
          </a>
        </Fact>
        <Fact label="This box">{hostname || "-"}</Fact>
      </dl>
      <p className="border-t px-5 py-3 text-xs text-muted-foreground">{about.copyright}</p>
    </section>
  );
}
