// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// WebRTCVideo - the live feed over WebRTC, with MJPEG as the safety net.
//
// The board publishes H.264 into its local gateway (MediaMTX); this component
// does the WHEP handshake through the master's authenticated proxy and hands
// the resulting track to a <video>. Media flows straight from the board's
// media port to this browser, so neither the master nor the hub ever touches
// a frame.
//
// It is a DROP-IN for StreamImg and deliberately falls back to it. WebRTC has
// more ways to fail than an <img> does - no gateway, no publish yet, a UDP
// path a corporate network blocks, a codec this browser will not take - and
// every one of them must end with the operator looking at their camera, not
// at an explanation. So: ask the master what to use, try WebRTC when it says
// so, and drop to MJPEG on any failure without saying much about it.
//
// Like StreamImg it tears the connection down when it scrolls off screen or
// the tab is hidden: an idle WebRTC session still costs the board an encode
// and the network its bitrate.
//
// The same answer says who draws the boxes. A system in [video] overlay =
// "client" mode sends the plain picture, and this player lays an
// OverlayCanvas over whichever renderer it ends up using; in "server" mode
// the answer carries no `overlay` and nothing extra is mounted or opened.
//
// `detectGate` (a live feed, never the Detection page): when that answer
// also says [video] view_on_detect, the player opens neither WebRTC nor the
// img until the overlay stream reports a detection on this camera, shows
// the DetectGate placeholder meanwhile, and closes again view_hold_s after
// the last one (lib/viewOnDetect.js).
import { useEffect, useRef, useState } from "react";
import StreamImg from "./StreamImg.jsx";
import CameraLoader from "./CameraLoader.jsx";
import OverlayCanvas from "./OverlayCanvas.jsx";
import { DetectPlaceholder, useViewOnDetect } from "./DetectGate.jsx";
import { gateFor } from "../lib/viewOnDetect.js";
import { authHeaders } from "../lib/session.js";
import { apiUrl } from "../lib/base.js";

//: How long the handshake gets before we stop waiting and use the img. Local
//: signalling plus ICE on a LAN is well under a second; anything past this is
//: a path that is not going to come good while somebody watches it.
const HANDSHAKE_TIMEOUT_MS = 6000;

//: How long a connection may sit in "connecting"/"disconnected" before it is
//: treated as failed. WebRTC recovers from brief blips on its own, so this is
//: not eager.
const CONNECT_TIMEOUT_MS = 9000;

//: How long a CONNECTED session may deliver no picture before it is abandoned
//: for the img.
//
// "Connected" is not "showing video". ICE can succeed and the track can carry
// nothing -- a WHEP session churned by rapid camera toggling, a publish
// replaced underneath the session, a codec the browser accepted in SDP and
// then could not decode. Every one of them ends with connectionState
// "connected", so the timeout above never fires, onLoadedMetadata never
// fires, and the caller's "Camera connecting -- opening the video stream..."
// overlay stays up for ever. That is the "click active/inactive a few times
// and the feed never comes back" report: the box is fine (measured: every
// camera serves a frame over MJPEG in ~50ms and publishes to the gateway
// ready=true) and the page is waiting on a session that will never paint.
//
// Measured from the START of the handshake rather than from `connected`, so
// it also covers a session that connects late. Comfortably longer than a LAN
// handshake plus the publisher's one-second GOP, so a slow-but-working join
// is never cut off.
const FIRST_FRAME_TIMEOUT_MS = 12000;

//: How often a component that FELL BACK to MJPEG re-asks whether WebRTC has
//: become servable. Fallback used to be sticky for the component's whole
//: life, which is exactly wrong for the common failure (the app was
//: restarting, the publish was seconds away): every camera on the page then
//: held an MJPEG socket forever, and three cameras plus their polls saturate
//: a browser's six-per-host connection budget -- the "page takes forever to
//: reload" complaint. Media over WebRTC is UDP and holds no HTTP socket.
const RETRY_MS = 30000;
//: Give up rejoining after this many failed WebRTC attempts: a network that
//: blocks UDP is not going to unblock it while somebody watches, and
//: flickering the picture every retry would be worse than the img.
const MAX_RETRIES = 5;

/**
 * `camera` picks which camera of the system (undefined = the first, matching
 * the MJPEG relay). `mjpegSrc` is the img URL to fall back to, and every
 * other prop is passed through to whichever renderer ends up being used.
 */
export default function WebRTCVideo({
  camera,
  mjpegSrc,
  onLoad,
  className,
  style,
  imgClassName,
  imgStyle,
  paused = false,
  //: Stay on the MJPEG img whatever the master could serve: the depth view
  //: (view=depth) exists only on that path, the WebRTC publish carries the
  //: camera's own picture.
  mjpegOnly = false,
  //: Apply [video] view_on_detect when the system asks for it. Off by
  //: default, so the Detection page's camera setup never gates.
  detectGate = false,
  //: Told true while the gate holds the video closed (and false when it
  //: opens), so a caller's own "connecting" loader can stand down.
  onDetectIdle,
  ...rest
}) {
  // null = still asking the master; "webrtc" | "mjpeg" = decided.
  //
  // Held WITH the camera it was decided for. A camera change used to leave
  // the previous camera's answer in place for one render, and the handshake
  // effect below (which keys on the camera too) ran in that render -- so a
  // switch to Camera 2 opened a WHEP session for Camera 2 under Camera 1's
  // verdict, then tore it down a moment later when the real answer came
  // back. A session per switch for nothing, and on the gateway, a session
  // nobody ever released. Reading the answer through the camera key makes a
  // stale verdict read as "still asking", which it is.
  const camKey = camera == null ? "" : String(camera);
  const [verdict, setVerdict] = useState({ key: camKey, mode: null });
  const mode = verdict.key === camKey ? verdict.mode : null;
  const setMode = (m) => setVerdict({ key: camKey, mode: m });
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  //: The gateway session this player holds, as the master's own address for
  //: it (the WHEP Location header, proxied). Released with a DELETE on
  //: teardown: pc.close() alone only drops the browser's end, and the
  //: gateway kept every abandoned session until its own timeout -- a
  //: switch-happy operator left a trail of them.
  const sessionRef = useRef(null);
  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;
  // The caller's onError rides on to the img in `rest`; kept here as well so
  // a WebRTC session that WAS painting and then drops to the img can say the
  // picture is gone. Without it a caller whose loader keys off "has a frame
  // arrived" kept believing the dead video and showed nothing at all.
  const onErrorRef = useRef(rest.onError);
  onErrorRef.current = rest.onError;
  // Failed WebRTC attempts on THIS camera -- the rejoin loop's budget.
  const attemptsRef = useRef(0);

  useEffect(() => { attemptsRef.current = 0; }, [camKey]);

  // {mode: "client", delay_ms} when the browser draws this system's overlay,
  // null otherwise. Keyed by camera like the verdict above.
  const [overlayAns, setOverlayAns] = useState({ key: camKey, info: null });
  const overlayInfo = !mjpegOnly && overlayAns.key === camKey ? overlayAns.info : null;
  // The view-on-detect gate, from the same answer (nothing extra asked).
  const gate = detectGate && !mjpegOnly && overlayAns.key === camKey
    ? gateFor(overlayAns.raw) : { on: false };
  const showVideo = useViewOnDetect({ on: gate.on, camera: camKey,
                                      holdS: gate.holdS, classes: gate.classes });
  const gatedIdle = gate.on && !showVideo;
  const onDetectIdleRef = useRef(onDetectIdle);
  onDetectIdleRef.current = onDetectIdle;
  useEffect(() => {
    if (onDetectIdleRef.current) onDetectIdleRef.current(gatedIdle);
  }, [gatedIdle]);
  // A closed gate is a pause: no handshake, no img, no rejoin loop.
  const held = paused || gatedIdle;

  // Ask the master whether WebRTC is actually servable for THIS camera. It
  // answers "mjpeg" for a system on the img transport, a box with no gateway
  // and a camera nothing is publishing - one question, every reason.
  useEffect(() => {
    let dead = false;
    setMode(null);
    if (mjpegOnly) { setMode("mjpeg"); return undefined; }
    const qs = camKey ? `?camera=${encodeURIComponent(camKey)}` : "";
    fetch(apiUrl(`/api/detector/webrtc${qs}`),
          { credentials: "same-origin", headers: { ...authHeaders() } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (dead) return;
        setMode(d?.mode === "webrtc" ? "webrtc" : "mjpeg");
        setOverlayAns({ key: camKey,
                        info: d?.overlay?.mode === "client" ? d.overlay : null,
                        raw: d });
      })
      .catch(() => { if (!dead) setMode("mjpeg"); });
    return () => { dead = true; };
  }, [camKey, mjpegOnly]);

  // A gated tile re-reads its answer now and then, so a change of the chosen
  // classes or of the hold time applies to a tile that is already open. Only
  // the gate's own part is replaced; the transport is never touched.
  useEffect(() => {
    if (!detectGate || mjpegOnly) return undefined;
    let dead = false;
    const qs = camKey ? `?camera=${encodeURIComponent(camKey)}` : "";
    const timer = setInterval(() => {
      fetch(apiUrl(`/api/detector/webrtc${qs}`),
            { credentials: "same-origin", headers: { ...authHeaders() } })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (dead || !d) return;
          setOverlayAns((cur) => (
            cur.key === camKey
            && JSON.stringify(cur.raw?.video_saving) !== JSON.stringify(d.video_saving)
              ? { ...cur, raw: d } : cur));
        })
        .catch(() => {});
    }, 5000);
    return () => { dead = true; clearInterval(timer); };
  }, [detectGate, camKey, mjpegOnly]);

  // While on the img, keep asking (gently) whether WebRTC has become
  // servable -- the publish catches up seconds after an app restart, and
  // staying on MJPEG forever holds an HTTP socket per camera. Flip back only
  // when the master says ready (publish present, gateway up), so the swap
  // almost always succeeds instead of flickering through a doomed handshake.
  useEffect(() => {
    if (mode !== "mjpeg" || held || mjpegOnly) return undefined;
    if (attemptsRef.current >= MAX_RETRIES) return undefined;
    let dead = false;
    const t = setInterval(() => {
      const qs = camKey ? `?camera=${encodeURIComponent(camKey)}` : "";
      fetch(apiUrl(`/api/detector/webrtc${qs}`),
            { credentials: "same-origin", headers: { ...authHeaders() } })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (dead) return;
          if (d?.mode === "webrtc" && d?.ready) {
            attemptsRef.current += 1;
            setMode("webrtc");
          }
        })
        .catch(() => { /* master unreachable: stay on the img */ });
    }, RETRY_MS);
    return () => { dead = true; clearInterval(t); };
  }, [mode, camKey, held]);

  // The handshake. Runs only once the answer above is "webrtc"; a failure
  // drops this component back to the img, and the rejoin loop above decides
  // whether it gets another try (a remount always asks afresh).
  useEffect(() => {
    if (mode !== "webrtc" || held) return undefined;
    let dead = false;
    let connectTimer = null;
    const fallback = (why) => {
      if (dead) return;
      // Not an error the operator needs: the picture keeps working.
      if (why) console.debug("[webrtc] falling back to mjpeg:", why);
      dead = true;
      const hadPicture = Boolean(videoRef.current?.videoWidth);
      cleanup();
      setMode("mjpeg");
      if (hadPicture && onErrorRef.current) onErrorRef.current();
    };
    // Armed from the START of the handshake, not from `connected`: a session
    // that connects and carries no media satisfies every other check here.
    // videoWidth is the honest test -- it is zero until a frame has actually
    // been decoded, where readyState and connectionState both go green on a
    // track that never paints.
    const frameTimer = setTimeout(() => {
      if (!videoRef.current?.videoWidth) fallback("connected, no picture");
    }, FIRST_FRAME_TIMEOUT_MS);
    const cleanup = () => {
      clearTimeout(connectTimer);
      clearTimeout(frameTimer);
      const pc = pcRef.current;
      pcRef.current = null;
      if (pc) { try { pc.close(); } catch { /* already gone */ } }
      // Tell the gateway too. keepalive so the DELETE survives this page
      // being left; the answer is of no interest, a session that was
      // already gone is the outcome either way.
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) {
        fetch(apiUrl(session), { method: "DELETE", credentials: "same-origin",
                                 keepalive: true, headers: { ...authHeaders() } })
          .catch(() => { /* gateway or master gone: nothing left to release */ });
      }
    };

    (async () => {
      if (typeof RTCPeerConnection === "undefined") return fallback("no webrtc");
      let pc;
      try {
        // No ICE servers: board and browser are on one LAN, so host
        // candidates are the whole answer. A public STUN server here would
        // leak that this deployment exists, for nothing.
        pc = new RTCPeerConnection({ iceServers: [] });
      } catch (e) { return fallback(e); }
      pcRef.current = pc;
      pc.addTransceiver("video", { direction: "recvonly" });

      pc.ontrack = (e) => {
        const v = videoRef.current;
        if (v && e.streams && e.streams[0]) v.srcObject = e.streams[0];
      };
      pc.onconnectionstatechange = () => {
        const s = pc.connectionState;
        if (s === "connected") {
          clearTimeout(connectTimer);
          // A working connection restores the rejoin budget: attempts exist
          // to stop a hopeless network from flickering, not to punish a
          // camera for the restarts it recovered from.
          attemptsRef.current = 0;
        }
        if (s === "failed" || s === "closed") fallback(`connection ${s}`);
      };

      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        // Wait for ICE gathering: this is a NON-trickle handshake, so the
        // offer has to carry every candidate before it is sent. On a LAN
        // that is a few milliseconds; the timeout is for the case where a
        // browser never reports completion.
        await new Promise((resolve) => {
          if (pc.iceGatheringState === "complete") return resolve();
          const done = () => {
            if (pc.iceGatheringState === "complete") {
              pc.removeEventListener("icegatheringstatechange", done);
              resolve();
            }
          };
          pc.addEventListener("icegatheringstatechange", done);
          setTimeout(resolve, 1500);
          return undefined;
        });
        if (dead) return;

        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), HANDSHAKE_TIMEOUT_MS);
        const qs = camKey ? `?camera=${encodeURIComponent(camKey)}` : "";
        const res = await fetch(apiUrl(`/api/detector/webrtc/whep${qs}`), {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/sdp", ...authHeaders() },
          body: pc.localDescription.sdp,
          signal: ctrl.signal,
        });
        clearTimeout(t);
        if (!res.ok) return fallback(`whep ${res.status}`);
        // The master's address for this session (its proxy of the gateway's
        // Location), released by cleanup(). Recorded even if this effect is
        // already dead -- then it is released right here.
        const session = res.headers && res.headers.get
          ? res.headers.get("Location") : null;
        const answer = await res.text();
        if (dead) {
          if (session) {
            fetch(apiUrl(session), { method: "DELETE", credentials: "same-origin",
                                     keepalive: true, headers: { ...authHeaders() } })
              .catch(() => {});
          }
          return undefined;
        }
        if (session) sessionRef.current = session;
        await pc.setRemoteDescription({ type: "answer", sdp: answer });
        connectTimer = setTimeout(() => {
          if (pc.connectionState !== "connected") fallback("connect timeout");
        }, CONNECT_TIMEOUT_MS);
      } catch (e) {
        return fallback(e);
      }
      return undefined;
    })();

    return () => { dead = true; cleanup(); };
  }, [mode, camKey, held]);

  // Deciding is NOT the same as deciding against.
  //
  // This used to start the img while the answer was still in flight, so a
  // page must not "hold a black box open waiting to find out how it will show
  // video". The cost of that turned out to be the bug: on a box that answers
  // "webrtc" the img is opened and thrown away on EVERY mount, and an MJPEG
  // stream is a long-lived HTTP connection. Measured during rapid camera
  // toggling: 48 preview opens for 15 toggles. A browser allows six
  // connections per origin and aborted MJPEG ones linger seconds
  // server-side, so the pool empties and the NEXT request never gets sent --
  // which is the camera toggle that "stops working" and the feed that never
  // arrives, both at once.
  //
  // The question it is waiting on is one local call that measures 7-54ms on
  // this box. That is not worth a connection, so the wait renders the
  // caller's placeholder instead. `mode === null` only.
  //
  // `in`, not `??`: a caller that draws its OWN loader over the whole feed box
  // (Camera Setup, LiveFeed) passes placeholder={null} to say "none here".
  // `??` read that null as "not given" and drew a second ring inside this
  // wrapper -- which has no height of its own yet, so it sat half cut off at
  // the top edge while the caller's ring sat in the middle: two loaders.
  if (mode === null) {
    return (
      <div className={className} style={{ position: "relative", ...style }}>
        {"placeholder" in rest ? rest.placeholder : <CameraLoader />}
      </div>
    );
  }
  // Nothing detected on a gated feed: the placeholder, and no media element
  // at all -- an unmounted <video>/<img> holds no connection.
  if (gatedIdle) {
    return <DetectPlaceholder className={className} style={style} />;
  }
  // The depth view stays server-painted (mjpegOnly), so it never gets one.
  const overlay = overlayInfo
    ? <OverlayCanvas camera={camKey} delayMs={Number(overlayInfo.delay_ms) || 0} />
    : undefined;
  if (mode !== "webrtc") {
    return (
      <StreamImg src={mjpegSrc} paused={paused} onLoad={onLoad}
                 className={className} style={style}
                 imgClassName={imgClassName} imgStyle={imgStyle}
                 overlay={overlay} {...rest} />
    );
  }

  return (
    <div className={className}
         style={overlay ? { position: "relative", ...style } : style}>
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className={imgClassName}
        style={imgStyle}
        // Callers measure the picture to size their overlay, and they were
        // written against <img>: naturalWidth/naturalHeight. A <video>
        // spells the same numbers videoWidth/videoHeight, so they are
        // published under BOTH names rather than every caller learning
        // which element it happens to be looking at today.
        onLoadedMetadata={(e) => {
          const v = e.target;
          if (v && v.videoWidth && !v.naturalWidth) {
            try {
              Object.defineProperty(v, "naturalWidth",
                                    { value: v.videoWidth, configurable: true });
              Object.defineProperty(v, "naturalHeight",
                                    { value: v.videoHeight, configurable: true });
            } catch { /* a browser that refuses: the caller falls back */ }
          }
          if (onLoadRef.current) onLoadRef.current(e);
        }}
      />
      {overlay}
    </div>
  );
}
