// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// LiveFeed - the MJPEG stream in its own container. It owns the connection
// lifecycle (connect, reconnect when the app comes up) so the rest of the page
// can re-render freely without ever remounting the <img> and tearing down the
// stream. Memoized: only `running` / `transition` flips or an aspect change
// touch it.
//
// Retry-on-error and the off-screen / hidden-tab teardown live in StreamImg,
// which every MJPEG consumer shares.
//
// transition: "start" | "stop" | null - the power action in flight. While set,
// a loading overlay explains what is happening instead of a black box or a
// broken image.
//
// A live feed honours [video] view_on_detect (WebRTCVideo detectGate): with
// nothing detected the player shows its placeholder instead of the video,
// and the "connecting" loader stands down so it does not cover it.
import { memo, useEffect, useRef, useState } from "react";
import WebRTCVideo from "./WebRTCVideo.jsx";
import CameraLoader from "./CameraLoader.jsx";
import { apiUrl } from "../lib/base.js";

// A camera in a turning ring, not a sentence: see CameraLoader. The words
// survive as the accessible label, which is where they were doing their real
// work -- on screen they were a line to read before being allowed to look at
// a camera.
function Overlay({ children }) {
  return <CameraLoader label={children} dim />;
}

// `camera` is optional: undefined means "the first camera", which is exactly
// what the relay does with no query param - so a single-camera page produces
// the same URL it always did.
const LiveFeed = memo(function LiveFeed({ running, transition, camera }) {
  const [key, setKey] = useState(0);        // bump = new connection attempt
  const [live, setLive] = useState(false);  // first frame arrived
  const [gateIdle, setGateIdle] = useState(false);  // view_on_detect: no video
  const wasRunning = useRef(running);
  const lastCamera = useRef(camera);

  // App just came up: drop the old (refused) socket and connect fresh.
  useEffect(() => {
    if (running && !wasRunning.current) {
      setLive(false);
      setKey((k) => k + 1);
    }
    if (!running) setLive(false);
    wasRunning.current = running;
  }, [running]);

  // Switched cameras: the old stream is a different feed, so drop it and
  // reconnect rather than letting a stale frame linger under the new label.
  useEffect(() => {
    if (lastCamera.current !== camera) {
      lastCamera.current = camera;
      setLive(false);
      setKey((k) => k + 1);
    }
  }, [camera]);

  if (!running) {
    return transition === "start" ? (
      <Overlay>Starting detection - camera and model are loading…</Overlay>
    ) : transition === "stop" ? (
      <Overlay>Stopping detection…</Overlay>
    ) : (
      <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center",
                    color: "var(--muted-text, #6b6b6b)", fontSize: 14 }}>
        Start detection to see the feed
      </div>
    );
  }

  return (
    <>
      {/* WebRTC when the board can serve it, the MJPEG img otherwise --
          WebRTCVideo decides and falls back on its own, so this call site
          does not care which one is actually running. */}
      <WebRTCVideo
        camera={camera}
        mjpegSrc={camera === undefined || camera === null || camera === ""
               ? apiUrl(`/api/detector/preview?k=${key}`)
               : apiUrl(`/api/detector/preview?camera=${encodeURIComponent(camera)}&k=${key}`)}
        alt="live feed"
        detectGate
        onDetectIdle={(idle) => { setGateIdle(idle); if (idle) setLive(false); }}
        onLoad={() => setLive(true)}
        onError={() => setLive(false)}
        // The Overlay below is THE loader, over the whole box. Without this
        // the player drew its own default ring too, inside a wrapper only a
        // few pixels tall -- a second, cut-off spinner at the top edge.
        placeholder={null}
        style={{ width: "100%" }}
        imgStyle={{ width: "100%", height: "auto", display: "block" }} />
      {transition === "stop" ? (
        <Overlay>Stopping detection…</Overlay>
      ) : !live && !gateIdle ? (
        <Overlay>Connecting to the video feed…</Overlay>
      ) : null}
    </>
  );
});

export default LiveFeed;
