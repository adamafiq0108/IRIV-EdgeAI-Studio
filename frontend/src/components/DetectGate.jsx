// DetectGate - "Show video only when an object is detected" ([video]
// view_on_detect) for one live tile: the video element is MOUNTED only while
// the AI reports detections on that camera (and view_hold_s after), and a
// placeholder stands in otherwise. Unmounting is the whole saving: an
// unmounted <img> or <video> closes its connection (StreamImg's header), so
// nothing is pulled from the box, and a passthrough camera is not pulled by
// the gateway either.
//
// The detections come from the overlay stream the page already shares
// (lib/overlayStream.js): the same one-per-page connection the OverlayCanvas
// uses, keyed the same way, so a gated tile adds no request of its own -
// on the hub's Live Wall that is the hub relay (`slug`). The rule itself is
// lib/viewOnDetect.js.
//
// With the gate off (`on` false: the switch off, overlay = "server", or the
// Detection page) this renders its children exactly as before and opens
// nothing.
import { useEffect, useRef, useState } from "react";
import { VideoOff } from "lucide-react";
import { createGate, PLACEHOLDER } from "../lib/viewOnDetect.js";
import { subscribeHubOverlay, subscribeOverlay } from "../lib/overlayStream.js";

/** Is the video shown for this camera right now? Always true when `on` is
 *  false. `slug` routes through the hub's stream (a Live Wall tile). */
export function useViewOnDetect({ on, camera, slug, holdS, classes = null }) {
  const [show, setShow] = useState(false);
  // The chosen classes by value: a new array with the same names must not
  // rebuild the gate, but a changed choice must apply at once.
  const classKey = Array.isArray(classes) ? JSON.stringify(classes) : "";
  const classesRef = useRef(classes);
  classesRef.current = classes;
  const gateRef = useRef(null);
  useEffect(() => {
    if (gateRef.current) gateRef.current.update(classesRef.current);
  }, [classKey]);
  useEffect(() => {
    if (!on) return undefined;
    const gate = createGate(holdS, undefined, classesRef.current);
    let timer = null;
    const update = () => {
      clearTimeout(timer);
      timer = null;
      setShow(gate.showVideo());
      const end = gate.deadline();
      if (end != null) {
        timer = setTimeout(update, Math.max(0, end - Date.now()) + 5);
      }
    };
    setShow(false);
    gateRef.current = { update: (c) => { gate.changeClasses(c); update(); } };
    const cam = camera == null ? "" : String(camera);
    const onMessage = (m) => { gate.message(m); update(); };
    const onReset = () => { gate.reset(); update(); };
    const unsubscribe = slug
      ? subscribeHubOverlay(slug, cam, onMessage, onReset)
      : subscribeOverlay(cam, onMessage, onReset);
    return () => { unsubscribe(); clearTimeout(timer); gateRef.current = null; };
  }, [on, camera, slug, holdS]);
  return on ? show : true;
}

/** The stand-in while nothing is detected. Fills its box like the feed. */
export function DetectPlaceholder({ className, style }) {
  return (
    <div className={className} data-detect-gate="idle"
         style={{ position: "relative", width: "100%", height: "100%",
                  minHeight: 120, background: "#000", display: "flex",
                  flexDirection: "column", alignItems: "center",
                  justifyContent: "center", gap: 8, padding: 12,
                  textAlign: "center", color: "var(--muted-text, #94a3b8)",
                  ...style }}>
      <VideoOff size={24} style={{ opacity: 0.7 }} aria-hidden="true" />
      <div className="small">{PLACEHOLDER}</div>
    </div>
  );
}

/** The children (the player) while the gate shows video, the placeholder
 *  otherwise. */
export default function DetectGate({ on, camera, slug, holdS, classes, className,
                                     style, children }) {
  const show = useViewOnDetect({ on, camera, slug, holdS, classes });
  if (show) return children;
  return <DetectPlaceholder className={className} style={style} />;
}
