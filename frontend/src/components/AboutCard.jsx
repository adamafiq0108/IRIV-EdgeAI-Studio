// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// About - the headline of the About page: whose product this is, which version
// is running, and the facts about THIS box an operator asks first (its name,
// its address, its accelerator). From GET /api/hub/about, so the credit reads
// the same here as in `hub_server.py --about`; the box facts are passed in from
// the health and board readings the page already has. The logo is the
// deployment's own mark (brand.toml).
import { useEffect, useState } from "react";
import { Cpu, Globe, Network, Server } from "lucide-react";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";

function Fact({ icon: Icon, label, children }) {
  return (
    <div className="flex min-w-0 items-start gap-2.5">
      <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
        <Icon size={14} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd className="truncate text-sm font-medium">{children}</dd>
      </div>
    </div>
  );
}

export default function AboutCard({ hostname, address, hat }) {
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
            <span className="rounded-full border px-2.5 py-0.5 text-xs font-medium text-foreground"
                  style={{ borderColor: "var(--primary, #04bade)" }}>
              Version {about.version}
            </span>
            <span>by {about.company}</span>
          </div>
        </div>
      </div>
      {/* The facts an operator asks first, in one row: this box, how to reach
          it, what accelerates it, and where the product comes from. */}
      <dl className="grid gap-x-6 gap-y-3 border-t px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact icon={Server} label="This box">{hostname || "-"}</Fact>
        <Fact icon={Network} label="Address">{address || "no wired address"}</Fact>
        <Fact icon={Cpu} label="AI accelerator">{hat || "none detected"}</Fact>
        <Fact icon={Globe} label="Website">
          <a href={about.website} target="_blank" rel="noopener noreferrer"
             className="underline-offset-2 hover:underline">
            {about.website.replace(/^https?:\/\//, "")}
          </a>
        </Fact>
      </dl>
    </section>
  );
}
