// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Board guide - the IRIV Edge AI CM5 on one picture: every part and port with a
// number, what each is connected to inside, and what can be plugged into it.
// It replaces the live terminals popup that used to sit in the footer of every
// system page: the board is the same on every system, so it is shown once, here.
// The picture is core/static/img/iriv-edge-ai-cm5.png (source: docs/images).
import { Cpu, ExternalLink } from "lucide-react";

const IMAGE = "/static/img/iriv-edge-ai-cm5.png";

export default function BoardGuideCard() {
  return (
    <section className="rounded-xl border bg-card p-4" data-board-guide>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
            <Cpu size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold leading-tight">IRIV Edge AI CM5 board guide</h2>
            <div className="mt-0.5 text-xs text-muted-foreground">
              Every part and port, what it is connected to, and what you can plug in.
            </div>
          </div>
        </div>
        <a href={IMAGE} target="_blank" rel="noopener noreferrer"
           className="inline-flex items-center gap-1.5 text-xs font-medium underline-offset-2 hover:underline">
          Open full size <ExternalLink size={12} aria-hidden="true" />
        </a>
      </div>
      <a href={IMAGE} target="_blank" rel="noopener noreferrer" className="block">
        <img src={IMAGE} loading="lazy" decoding="async"
             alt="The IRIV Edge AI CM5: front and back panel with numbered ports, a diagram of
                  what is connected to what inside, and a table of what each port can do and
                  what can be plugged into it."
             className="mx-auto h-auto w-full max-w-[1000px] rounded-lg border bg-white" />
      </a>
    </section>
  );
}
