// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// BrokerStatus - the MQTT broker pill on the Workflow page.
//
// An MQTT Subscribe node reading nothing and a broker that is not running
// look the same on the canvas: a value that never arrives. So the page says
// which it is, in one line beside the save state: "MQTT broker: running on
// :1883 (local only)", "not running", "not installed", or that another
// program holds the port.
//
// Drawn on EVERY workflow, MQTT nodes or not (Tok Aba, 2026-09-29: "make the
// broker status seen always"): whether the box has a working broker is worth
// knowing before the first MQTT node goes down, not after. It stays one small
// pill, polled every 10 s. An admin gets a button that opens Settings > MQTT Broker in a dialog here,
// rather than a link to the hub - the hub is another origin (another port),
// and the question came up while wiring this canvas.
import { useState } from "react";
import { usePoll } from "../api/hooks.js";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import { BrokerSettings } from "./BrokerPanel.jsx";
import { pillFor } from "./brokerPill.js";

function Pill({ nodes }) {
  const { data } = usePoll("/api/mqtt/broker/status", 10000);
  const [open, setOpen] = useState(false);
  const s = data?.status;
  const { tone, text } = pillFor(s);
  const manage = !!data?.can_manage;
  return (
    <>
      <span className={`ui-badge ui-badge-${tone}`} data-broker-pill={s?.state || ""}
            title={manage ? "Open the broker settings" : text}
            role={manage ? "button" : undefined}
            tabIndex={manage ? 0 : undefined}
            style={{ cursor: manage ? "pointer" : "default", whiteSpace: "nowrap" }}
            onClick={manage ? () => setOpen(true) : undefined}
            onKeyDown={manage ? (e) => { if (e.key === "Enter") setOpen(true); } : undefined}>
        {text}
      </span>
      {manage && (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="cy-app z-[80] max-h-[90vh] overflow-y-auto sm:max-w-2xl"
                         overlayClassName="z-[80]">
            <DialogHeader>
              <DialogTitle>MQTT Broker</DialogTitle>
              <DialogDescription>
                The broker on this box. The same settings as Settings &gt; MQTT Broker.
              </DialogDescription>
            </DialogHeader>
            {open && <BrokerSettings />}
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

export default function BrokerStatus({ nodes }) {
  return <Pill nodes={nodes} />;
}
