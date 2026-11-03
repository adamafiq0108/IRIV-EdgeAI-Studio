// OverlayCanvas - the detection overlay drawn by the BROWSER, over a live
// <video> or <img>, for a system in [video] overlay = "client" mode.
//
// The board sends the plain picture plus small JSON messages (boxes, zones,
// links, bubbles, trails, HUD - lib/overlay.js). This canvas sits over the
// player, finds the picture's real rectangle inside its element (a 16:9 feed
// in a 4:3 tile under object-fit: contain is letterboxed, and the boxes must
// land on the picture, not in the bars), and redraws whenever a message
// arrives or the element changes size.
//
// Rendered ONLY in client mode; a server-mode page never mounts it, so it
// opens nothing. Like StreamImg it lets go of its subscription when it is
// scrolled off screen or the tab is hidden - the last subscriber leaving
// closes the page's overlay stream, and the board stops building messages.
//
// `delayMs` ([video] overlay_delay_ms) holds each message back before it is
// drawn, so the boxes can be tuned to land with the picture they belong to:
// the video reaches the screen through an encoder, a jitter buffer and a
// decoder, the boxes through none of them.
//
// It must be placed INSIDE the player's wrapper, next to the media element,
// with the wrapper positioned (WebRTCVideo and StreamImg do both when given
// one).
//
// `slug` is for a HUB page showing another system's feed (the Live Wall):
// the messages then come through the hub's one relayed stream for the page
// (subscribeHubOverlay) instead of this system's own /api/detector/overlay.
import { useCallback, useEffect, useRef, useState } from "react";
import { drawOverlay, mediaRect } from "../lib/overlay.js";
import { subscribeHubOverlay, subscribeOverlay } from "../lib/overlayStream.js";

const HAS_IO = typeof IntersectionObserver !== "undefined";

export default function OverlayCanvas({ camera, delayMs = 0, slug }) {
  const ref = useRef(null);
  const msgRef = useRef(null);
  const rafRef = useRef(0);
  const [onScreen, setOnScreen] = useState(!HAS_IO);
  const [tabShown, setTabShown] = useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  const live = onScreen && tabShown;

  // One redraw per animation frame however many reasons arrive in it.
  const paint = useCallback(() => {
    if (rafRef.current || typeof requestAnimationFrame === "undefined") return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      const c = ref.current;
      const media = c && c.parentElement
        ? c.parentElement.querySelector("video, img") : null;
      if (!c) return;
      const ctx = c.getContext("2d");
      if (!ctx) return;
      const msg = msgRef.current;
      if (!media || !media.clientWidth || !media.clientHeight) {
        ctx.clearRect(0, 0, c.width, c.height);
        return;
      }
      // Cover exactly the media element; the picture's own rectangle inside
      // it is mediaRect's business.
      const bw = media.clientWidth;
      const bh = media.clientHeight;
      c.style.left = `${media.offsetLeft}px`;
      c.style.top = `${media.offsetTop}px`;
      c.style.width = `${bw}px`;
      c.style.height = `${bh}px`;
      const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
      const pw = Math.round(bw * dpr);
      const ph = Math.round(bh * dpr);
      if (c.width !== pw) c.width = pw;
      if (c.height !== ph) c.height = ph;
      const natW = media.videoWidth || media.naturalWidth || (msg && msg.w) || 0;
      const natH = media.videoHeight || media.naturalHeight || (msg && msg.h) || 0;
      const fit = (typeof getComputedStyle === "function"
        && getComputedStyle(media).objectFit) || "fill";
      drawOverlay(ctx, msg, mediaRect(bw, bh, natW, natH, fit), dpr);
    });
  }, []);

  useEffect(() => {
    const host = ref.current && ref.current.parentElement;
    if (!HAS_IO || !host) return undefined;
    const io = new IntersectionObserver(
      (entries) => setOnScreen(entries[entries.length - 1].isIntersecting),
      { threshold: 0.01 },
    );
    io.observe(host);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    const onVis = () => setTabShown(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  // The player resizes (a window, a collapsing panel) and its media element
  // is replaced (a reconnect): both are a redraw of the last message.
  useEffect(() => {
    const host = ref.current && ref.current.parentElement;
    if (!host || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(paint);
    ro.observe(host);
    const mo = typeof MutationObserver !== "undefined"
      ? new MutationObserver(paint) : null;
    if (mo) mo.observe(host, { childList: true });
    return () => { ro.disconnect(); if (mo) mo.disconnect(); };
  }, [paint]);

  useEffect(() => {
    if (!live) {
      msgRef.current = null;
      paint();
      return undefined;
    }
    const timers = new Set();
    const show = (m) => { msgRef.current = m; paint(); };
    const onMessage = (m) => {
      if (!(delayMs > 0)) { show(m); return; }
      const t = setTimeout(() => { timers.delete(t); show(m); }, delayMs);
      timers.add(t);
    };
    const onReset = () => {
      timers.forEach(clearTimeout);
      timers.clear();
      show(null);
    };
    const cam = camera == null ? "" : camera;
    const unsubscribe = slug
      ? subscribeHubOverlay(slug, cam, onMessage, onReset)
      : subscribeOverlay(cam, onMessage, onReset);
    return () => {
      unsubscribe();
      timers.forEach(clearTimeout);
    };
  }, [live, camera, delayMs, slug, paint]);

  useEffect(() => () => {
    if (rafRef.current && typeof cancelAnimationFrame !== "undefined") {
      cancelAnimationFrame(rafRef.current);
    }
  }, []);

  return (
    <canvas ref={ref} aria-hidden="true" data-overlay="client"
            style={{ position: "absolute", left: 0, top: 0,
                     pointerEvents: "none" }} />
  );
}
