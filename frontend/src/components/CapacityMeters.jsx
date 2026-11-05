// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The two capacities this box enforces, in the hub's header.
//
//   Systems Created  how many exist, of how many may exist
//   AI Running       how many detection apps are running, of how many may run
//                    (NOT cameras -- systems can share one camera, and the
//                    camera-device budget lives on the Camera Setup page)
//
// Both are REAL limits the server refuses past (provision.MAX_SYSTEMS and
// detector_control.MAX_RUNNING_APPS), and meeting one used to mean filling in
// a form or pressing Start and reading a 400. A meter turns that into
// something known before the attempt -- and, at the limit, into a warning
// that says which limit rather than a number to compare by eye.
//
// Both numbers come from /api/hub/pipelines, caps included: a UI holding its
// own copy of a server limit is a UI that lies the day the limit moves.
import { usePoll } from "../api/hooks.js";
import { Camera, Layers, Video } from "lucide-react";

function Meter({ icon: Icon, label, used, max }) {
  // max 0 = no cap (the developer's workshop). A meter with no end to fill
  // is a bar that means nothing, so it states the count and stops.
  const capped = max > 0;
  const full = capped && used >= max;
  const pct = capped ? Math.min(100, Math.round((used / max) * 100)) : 0;
  return (
    <div className={"cap-meter" + (full ? " is-full" : "")}
         title={full
           ? `${label}: at the limit of ${max}`
           : `${label}: ${used}${capped ? ` of ${max}` : ""}`}>
      <Icon className="cap-meter-icon" size={16} aria-hidden />
      <div className="cap-meter-body">
        <div className="cap-meter-top">
          <span className="cap-meter-label">{label}</span>
          <span className="cap-meter-count">
            {used}{capped ? <span className="cap-meter-max"> / {max}</span> : null}
          </span>
        </div>
        {capped && (
          <div className="cap-meter-track" aria-hidden>
            <div className="cap-meter-fill" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
      {full && <span className="cap-meter-max-tag">MAX</span>}
    </div>
  );
}

export default function CapacityMeters() {
  // /api/capacity is mounted on the hub AND on every master, so these stay
  // put when an operator opens a system -- the counts are facts about the
  // BOX, and the answer is the same whichever process is asked.
  const { data } = usePoll("/api/capacity", 5000);
  if (!data) return null;

  const systems = data.systems ?? 0;
  const running = data.running ?? 0;
  const cameras = data.cameras ?? 0;
  const maxSystems = data.max_systems ?? 0;
  const maxRunning = data.max_running ?? 0;
  const maxCameras = data.max_cameras ?? 0;

  return (
    <div className="cap-meters">
      {/* A cap of 0 means "no cap here" (the developer's workshop): the
          meter states the count and skips the bar, rather than measuring
          against a limit that is not theirs. */}
      <Meter icon={Layers} label="Systems Created" used={systems} max={maxSystems} />
      {/* Running detection APPS, not camera devices: four systems can share
          one camera. Labelling this "Camera Running" made 4 running systems
          on 2 cameras read as a full camera budget. */}
      <Meter icon={Video} label="AI Running" used={running} max={maxRunning} />
      {/* ...and the camera DEVICES those apps capture, against the box's
          own cap -- the two run out independently (four apps on one shared
          camera, or one app hogging four cameras), so each gets a meter. */}
      <Meter icon={Camera} label="Cameras Running" used={cameras} max={maxCameras} />
    </div>
  );
}
