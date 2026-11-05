// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// StreamImg - an MJPEG <img> that only exists while somebody can actually see
// it. Every open preview pins a blocking threadpool thread on the master, and
// the browser happily keeps pulling frames from a backgrounded tab or an
// element scrolled far off screen, so ten idle tiles cost exactly as much as
// ten watched ones. So this component REMOVES the element when nobody can see
// it and mounts a fresh one on the way back.
//
// REMOVING THE ELEMENT IS NOT ENOUGH ON ITS OWN. Tok Aba, Camera Setup
// (2026-10-09, box .164, v1.0.34): switching Camera 1 -> 2 -> 1 -> 2 a few
// times left the feed on the loader for ever while both cameras streamed
// fine. Measured on the box while it was stuck: the browser held EXACTLY six
// connections to the origin (its HTTP/1.1 per-host cap), four of them still
// pulling ~100 MB of old /api/detector/preview each -- streams this page had
// already switched AWAY from. A detached <img> keeps its connection open
// until the browser gets round to collecting it, which under a steady stream
// of bytes is never; the pool filled with previews nobody could see and the
// NEXT preview request was simply never sent. The one thing that aborts an
// in-flight image fetch on the spot is changing the element's `src` ("update
// the image data" aborts the pending request), so abortImg() below does that
// as the element leaves, before the next one is mounted. Hiding with CSS
// still does nothing, and that part of the old rule stands.
//
// It also owns the retry-on-error timer that every caller used to write for
// itself, and suppresses it while hidden: a stream nobody is watching must not
// reconnect on a loop.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import CameraLoader from "./CameraLoader.jsx";

const DEFAULT_RETRY_MS = 3000;

//: A 1x1 transparent GIF. Pointing a leaving <img> here aborts the preview it
//: was pulling and completes instantly with no request at all.
export const BLANK_SRC =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

/** Abort whatever `img` is still loading. The src swap is what cancels the
 *  in-flight fetch; the attribute is then removed so the element holds no
 *  source at all. Safe on an element that already has none. */
export function abortImg(img) {
  if (!img) return;
  try {
    img.src = BLANK_SRC;
    img.removeAttribute("src");
  } catch { /* a detached node in a browser that refuses: nothing to close */ }
}

//: How long a CONNECTED stream may go without ever delivering a frame before
//: it is reopened.
//
// Retry-on-error is not enough, because the failure that matters is not an
// error. `/api/detector/preview` answers 200 and a multipart body the moment
// the app is up -- even when the app has that camera's device closed (a
// camera switched off, or switched back on a second before the app's config
// poll reopens it). The browser then holds an open connection that never
// yields a part: no `load`, no `error`, no retry. That is why switching a
// camera on left the picture on "Camera connecting…" until the operator left
// the page and came back -- the remount was the only thing that ever opened a
// second connection.
//
// Only armed until the FIRST frame of an attempt. A mid-stream pause is
// legitimate and common (the app stalls for seconds reopening a dropped
// camera or hot-reloading a model, and the relay is deliberately built to ride
// that out) -- tearing the connection down for it would reintroduce the
// "video ends, then comes back" flicker that timeout was removed to fix.
const DEFAULT_STALL_MS = 8000;

// ...and a SECOND watchdog for a stream that started and then died silently.
//
// "A mid-stream pause is legitimate" is true of seconds, not of minutes. A
// camera that freezes for good leaves a connection that will never yield
// another part and never error either, and the browser allows only ~6 of
// those per origin: a few of them and every other request on the page --
// stats, controls, config -- simply never gets sent. That is why one frozen
// camera took the whole system's UI with it, and why going to the Hub and
// back was the cure (a cross-origin navigation force-closes the sockets).
//
// It arms only once the browser has proved it fires `load` PER PART, which it
// does by firing a second one. Browsers that deliver a multipart stream under
// a single `load` never arm it and behave exactly as before -- a watchdog
// that assumed per-frame events would tear down healthy feeds there.
const DEFAULT_MID_STALL_MS = 20000;

//: The height the empty wrapper holds before a frame has ever arrived.
//
// Without it the component cannot start: the placeholder has no <img>, so it
// has NO HEIGHT, and a zero-area box can never satisfy the observer's
// threshold -- no image, so no height, so never "on screen", so never an
// image. The feed sits on "Connecting…" forever.
//
// A few pixels is enough for the observer to have something to measure, and
// nobody sees them: the caller's own overlay covers the box until the first
// frame lands, and the real frame replaces this the moment it does.
//
// Deliberately a minimum rather than "treat unmeasurable as visible": a
// wrapper inside a display:none panel still reports zero area whatever its
// min-height, so a hidden tile stays correctly torn down.
const BOOTSTRAP_H = 32;

// jsdom and older browsers have no IntersectionObserver. Streaming to a viewer
// we cannot measure is far better than a permanently blank feed, so the
// fallback is "always visible" - the behaviour this component replaced.
const HAS_IO = typeof IntersectionObserver !== "undefined";

/** Append query params to a URL that may already carry some (`?camera=`,
    `&k=`), picking the right separator. Empty/undefined values are dropped so
    callers can pass a conditional object and get today's exact URL back. */
export function withParams(url, params) {
  const parts = Object.entries(params || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  if (!parts.length) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${parts.join("&")}`;
}

/**
 * `className`/`style` size the WRAPPER (the box that stays put when the stream
 * is torn down); `imgClassName`/`imgStyle` and any extra props go to the
 * <img>. The split matters because the wrapper is what IntersectionObserver
 * watches - it has to exist even when the image does not.
 *
 * `paused` lets a caller stop a stream for its own reasons (a collapsed panel,
 * a modal on top) on the same machinery.
 *
 * `overlay` is an element drawn over the picture (OverlayCanvas, for a system
 * whose browser draws the boxes). Rendered beside the <img> in a positioned
 * wrapper; without one the markup is exactly what it always was.
 */
export default function StreamImg({
  src,
  paused = false,
  retryMs = DEFAULT_RETRY_MS,
  stallMs = DEFAULT_STALL_MS,
  midStallMs = DEFAULT_MID_STALL_MS,
  alt = "",
  className,
  style,
  imgClassName,
  imgStyle,
  // What stands in while there is no picture. Defaulted rather than left
  // null: every consumer wanted the same thing (a camera on its way) and
  // none of them passed one, so a tile, a wall panel and a zone editor each
  // showed an empty box instead.
  placeholder = <CameraLoader />,
  onLoad,
  onError,
  overlay,
  ...imgProps
}) {
  const wrapRef = useRef(null);
  //: The <img> currently mounted. Events from any OTHER element -- one this
  //: component has already replaced -- are stale and ignored: a replaced
  //: img's `error` (the abort fires one) must not arm a retry for a stream
  //: that was deliberately closed, and its late `load` must not tell the
  //: caller a picture is showing when the new attempt has none yet.
  const imgRef = useRef(null);
  // ONE function for the life of the component. React 19 runs a ref's cleanup
  // whenever the ref's IDENTITY changes, and an inline arrow is new on every
  // render: each re-render (a `live` flip in the caller, any state here) then
  // aborted the stream it was showing and left an <img> with no src, drawn as
  // a broken image with its alt text. The cleanup must run only as the element
  // leaves (a new key, a teardown, an unmount).
  const imgRefCallback = useCallback((el) => {
    imgRef.current = el;
    return () => {
      if (imgRef.current === el) imgRef.current = null;
      abortImg(el);
    };
  }, []);
  const retryTimer = useRef(null);
  const stallTimer = useRef(null);
  //: Has THIS attempt ever delivered a frame? A connection that has is
  //: working, whatever it does next; one that has not is the case above.
  const gotFrame = useRef(false);
  //: How many frames this attempt has delivered. Two is the proof that this
  //: browser reports parts individually, which is what makes a mid-stream
  //: watchdog meaningful rather than a timer that kills healthy feeds.
  const frames = useRef(0);
  const [onScreen, setOnScreen] = useState(!HAS_IO);
  const [tabShown, setTabShown] = useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  const [attempt, setAttempt] = useState(0);   // bump = fresh connection

  const live = onScreen && tabShown && !paused && Boolean(src);

  useEffect(() => {
    if (!HAS_IO || !wrapRef.current) return undefined;
    // A threshold this low means "any sliver of it is on screen" - an operator
    // watching the top edge of a tile is still watching it.
    const io = new IntersectionObserver(
      (entries) => setOnScreen(entries[entries.length - 1].isIntersecting),
      { threshold: 0.01 },
    );
    io.observe(wrapRef.current);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    const onVis = () => setTabShown(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  // The height the stream last occupied. Callers that let the frame drive the
  // box (Zones, LiveFeed) would collapse to nothing the moment the <img> goes,
  // yanking the rest of the page upward and - worse - scrolling the wrapper
  // back into view, which would flap the observer. Holding the last height
  // keeps the layout still. Recorded on every live render, because by the time
  // an effect sees `live === false` the element is already gone.
  const heldH = useRef(0);
  useLayoutEffect(() => {
    if (live && wrapRef.current) {
      const h = wrapRef.current.offsetHeight;
      if (h) heldH.current = h;
    }
  });

  const wasLive = useRef(live);
  useEffect(() => {
    if (live === wasLive.current) return;
    wasLive.current = live;
    // A torn-down MJPEG connection cannot resume; only a NEW element opens
    // one, so the key has to change on the way back in.
    if (live) setAttempt((n) => n + 1);
    else clearTimeout(retryTimer.current);
  }, [live]);

  // The first-frame watchdog. Re-armed per attempt, so a stream that never
  // starts keeps being reopened until it does -- which is what makes a camera
  // switched back on appear by itself instead of after a page change.
  useEffect(() => {
    gotFrame.current = false;
    frames.current = 0;
    clearTimeout(stallTimer.current);
    if (!live || !stallMs) return undefined;
    stallTimer.current = setTimeout(() => {
      if (!gotFrame.current) setAttempt((n) => n + 1);
    }, stallMs);
    return () => clearTimeout(stallTimer.current);
  }, [live, src, attempt, stallMs]);

  useEffect(() => () => {
    clearTimeout(retryTimer.current);
    clearTimeout(stallTimer.current);
  }, []);

  if (!live) {
    return (
      // `relative`, because the placeholder is positioned INSIDE this box.
      // Without it the loader would hunt up the tree for a positioned
      // ancestor and land in the middle of the page instead of in the frame.
      <div ref={wrapRef} className={className}
           style={{ position: "relative",
                    minHeight: heldH.current || BOOTSTRAP_H, ...style }}>
        {placeholder}
      </div>
    );
  }

  return (
    <div ref={wrapRef} className={className}
         style={overlay ? { position: "relative", ...style } : style}>
      <img
        // `src` is part of the identity: switching camera mid-stream must open
        // a new connection rather than re-point a live one.
        key={`${src}|${attempt}`}
        src={src}
        alt={alt}
        className={imgClassName}
        style={imgStyle}
        // The ref's cleanup runs as THIS element leaves (a new key, a
        // teardown, an unmount) and closes its connection -- see the header.
        // The old element goes before the new one mounts, so there is never
        // a moment with two previews open for one feed.
        ref={imgRefCallback}
        onLoad={(e) => {
          if (e.target !== imgRef.current) return;      // a replaced img: stale
          clearTimeout(retryTimer.current);
          // A frame landed: the FIRST-frame watchdog stands down for good on
          // this attempt. From the second frame on it is replaced by the
          // mid-stream one, which re-arms per frame -- so a connection that
          // simply stops speaking is torn down and reopened rather than
          // sitting there holding one of the origin's few sockets for ever.
          gotFrame.current = true;
          clearTimeout(stallTimer.current);
          frames.current += 1;
          if (frames.current >= 2 && midStallMs) {
            stallTimer.current = setTimeout(
              () => setAttempt((n) => n + 1), midStallMs);
          }
          const h = wrapRef.current?.offsetHeight;
          if (h) heldH.current = h;
          if (onLoad) onLoad(e);
        }}
        // The stream died (app restarting, camera still opening): reconnect
        // instead of leaving a broken image on the wall forever.
        onError={(e) => {
          if (e.target !== imgRef.current) return;      // the abort's own error
          if (onError) onError(e);
          clearTimeout(retryTimer.current);
          // The error path owns the reconnect from here; leaving the watchdog
          // armed would fire a second one into the same gap.
          clearTimeout(stallTimer.current);
          retryTimer.current = setTimeout(() => setAttempt((n) => n + 1), retryMs);
        }}
        {...imgProps}
      />
      {overlay}
    </div>
  );
}
