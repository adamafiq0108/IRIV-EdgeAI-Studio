// Live Wall - up to 4 live feeds at once, each slot picking any system's
// streaming device. The browser pulls each MJPEG straight from that system's
// master (/video_feed/<host>): the hub never proxies video (one master's
// outage stays that master's outage), and the signed session cookie is valid
// on every port of this box, so no re-login. Unmounting an <img> closes its
// connection - clearing a slot actually stops the pull.
//
// A system in [video] overlay = "client" sends that picture PLAIN; its boxes
// come as events, which the hub DOES relay (a few KB of JSON, never video):
// such a tile draws them on an OverlayCanvas fed by the page's one
// /api/hub/overlay stream. The mode comes with /api/hub/streams; a
// server-mode tile mounts no canvas and opens nothing new.
//
// A client-mode system with [video] view_on_detect on also gates the
// picture itself (DetectGate): the tile pulls no video until that stream
// reports a detection on its camera, and lets go view_hold_s after the
// last one. Same relayed stream, no extra connection.
import { useLayoutEffect, useRef, useState } from "react";
import { useApi } from "../api/hooks.js";
import { ErrorNote, SkelCard } from "./Loading.jsx";
import { Button } from "./ui/button.jsx";
import OverlayCanvas from "./OverlayCanvas.jsx";
import DetectGate from "./DetectGate.jsx";
import { gateFor } from "../lib/viewOnDetect.js";
import StreamImg, { withParams } from "./StreamImg.jsx";
import { MonitorPlay, X, Pin } from "lucide-react";

// Matches the box's running-camera cap (detector_control.MAX_CAMERAS): a wall
// slot per camera that can actually be live, and no empty slot promising a
// feed the box would refuse to run.
const SLOTS = 4;

// Past this many open tiles the wall stops being "a few feeds" and starts
// being a load problem - every stream costs a blocking thread on ITS master
// and a decode on this browser. Above the line each tile asks for a cheaper
// stream; at or below it, nothing is appended and the URL is byte-for-byte
// what it always was.
const BUSY_SLOTS = 4;
const BUSY_STREAM = { fps: 5, quality: 50 };

function SlotPicker({ systems, onPick, onPickLocal }) {
  const [slug, setSlug] = useState("");
  const system = systems.find((s) => s.slug === slug);
  const online = (system?.devices || []).filter((d) => d.online);
  // A system may run up to 10 cameras behind ONE app, all served on the same
  // port as /api/detector/preview?camera=<id>. With more than one the operator
  // must say which; with one (or a hub that could not read the package, so the
  // list is empty) the unqualified feed IS the answer and asking would be noise.
  const cams = (system?.cameras || []).filter((c) => c && c.id);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
      <MonitorPlay className="size-6 text-muted-foreground" />
      <select
        className="h-9 w-full max-w-56 rounded-md border bg-background px-2 text-sm"
        value={slug}
        onChange={(e) => setSlug(e.target.value)}
        aria-label="System"
      >
        <option value="">- choose a system -</option>
        {systems.map((s) => (
          <option key={s.slug} value={s.slug} disabled={!s.reachable}>
            {s.name}{s.reachable ? "" : " (master unreachable)"}
          </option>
        ))}
      </select>
      {system && system.reachable && (
        <>
          {/* The system's OWN local detection app, straight from its master's
              /api/detector/preview -- no pairing needed for a standalone board.
              502s into a broken image if the app isn't running, which is
              honest: start it on the system's Detection page. */}
          {cams.length > 1 ? (
            <div className="flex max-h-32 w-full max-w-56 flex-col gap-1 overflow-y-auto">
              <p className="text-xs text-muted-foreground">Local AI feed - pick a camera</p>
              {cams.map((c) => (
                <Button key={c.id} variant="outline" size="sm" className="w-full"
                        onClick={() => onPickLocal(system, c)}>
                  {c.name || c.label || c.id}
                </Button>
              ))}
            </div>
          ) : (
            <Button variant="outline" size="sm" className="w-full max-w-56"
                    onClick={() => onPickLocal(system, null)}>
              Local AI feed
            </Button>
          )}
          {online.length ? (
            <select
              className="h-9 w-full max-w-56 rounded-md border bg-background px-2 text-sm"
              defaultValue=""
              onChange={(e) => e.target.value && onPick(system, e.target.value)}
              aria-label="Device"
            >
              <option value="">- or a paired camera -</option>
              {online.map((d) => (
                <option key={d.host_serial} value={d.host_serial}>{d.label}</option>
              ))}
            </select>
          ) : (
            <p className="text-xs text-muted-foreground">
              No paired camera - use the local AI feed above, or pair a device in Settings.
            </p>
          )}
        </>
      )}
      {system && !system.reachable && (
        <p className="text-xs text-muted-foreground">
          Master unreachable - start it from System Control.
        </p>
      )}
    </div>
  );
}

/** The page's visible height from `ref`'s top edge down to the bottom of the
 *  window (less the page's own bottom padding), so the wall fills the screen
 *  exactly: not shorter, and not so tall that it scrolls. Only where the grid
 *  is two columns wide (sm and up); on a phone the tiles stack at their own
 *  16:9 and the page scrolls. */
function useFitHeight(ref) {
  const [h, setH] = useState(undefined);
  useLayoutEffect(() => {
    const measure = () => {
      if (!ref.current || window.innerWidth < 640) { setH(undefined); return; }
      const top = ref.current.getBoundingClientRect().top;
      setH(Math.max(320, Math.floor(window.innerHeight - top - 24)));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [ref]);
  return h;
}

export default function LiveWallPanel() {
  const { data, error } = useApi("/api/hub/streams");
  const [slots, setSlots] = useState(() => Array(SLOTS).fill(null));
  // "grid" - every tile equal, responsive columns (the old, only layout).
  // "spotlight" - the pinned slot fills the main area; every other slot rides
  // a scrollable column on the right, small. Pinning never moves a feed
  // between slots - it just changes which slot draws big.
  const [layout, setLayout] = useState("grid");
  const wallRef = useRef(null);
  const fitH = useFitHeight(wallRef);
  const [pinned, setPinned] = useState(0);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelCard rows={4} />;
  const systems = data.systems || [];

  function fill(i, system, host) {
    const label = (system.devices.find((d) => d.host_serial === host) || {}).label || host;
    setSlots((cur) => cur.map((s, j) => (j === i ? {
      slug: system.slug, name: system.name, url: system.url, host, label,
    } : s)));
  }
  function fillLocal(i, system, camera) {
    setSlots((cur) => cur.map((s, j) => (j === i ? {
      slug: system.slug, name: system.name, url: system.url, local: true,
      camera: camera ? String(camera.id) : "",
      label: camera ? (camera.name || camera.label || String(camera.id)) : "Local AI",
    } : s)));
  }

  /** The camera id this slot should still ask for. A slot remembers an id that
      may have been deleted since (the slots array is unkeyed local state); a
      stale one degrades to the system's unqualified feed rather than a dead
      tile. An EMPTY camera list is not evidence of deletion -- the hub simply
      could not read the package -- so the stored id is trusted then. */
  function cameraOf(s) {
    if (!s.local || !s.camera) return "";
    const sys = systems.find((x) => x.slug === s.slug);
    const list = (sys?.cameras || []).filter((c) => c && c.id);
    if (list.length && !list.some((c) => String(c.id) === s.camera)) return "";
    return s.camera;
  }
  /** The system's {mode, delay_ms} when the browser draws its boxes, else
      null. Only the LOCAL AI feed has them; a paired camera's feed does not
      come from the system's detection app. */
  function overlayOf(s) {
    if (!s.local) return null;
    const sys = systems.find((x) => x.slug === s.slug);
    return sys?.overlay?.mode === "client" ? sys.overlay : null;
  }
  /** {on, holdS} for the view-on-detect gate; off for a paired feed. */
  function gateOf(s) {
    if (!s.local) return { on: false };
    return gateFor(systems.find((x) => x.slug === s.slug));
  }
  function clear(i) {
    setSlots((cur) => cur.map((s, j) => (j === i ? null : s)));
  }

  const open = slots.filter(Boolean).length;
  const tuning = open > BUSY_SLOTS ? BUSY_STREAM : null;

  /** The URL a filled slot pulls from. Some already carry `?camera=`, so the
      tuning params have to pick their own separator. */
  function urlOf(s) {
    const base = s.local
      ? (cameraOf(s)
          ? `${s.url}/api/detector/preview?camera=${encodeURIComponent(cameraOf(s))}`
          : `${s.url}/api/detector/preview`)
      : `${s.url}/video_feed/${encodeURIComponent(s.host)}`;
    return withParams(base, tuning);
  }

  function tile(i, { big = false } = {}) {
    const s = slots[i];
    const ov = s ? overlayOf(s) : null;
    return (
      <div key={i}
           className={`relative overflow-hidden rounded-xl border bg-card ${
             layout === "grid" ? "aspect-video sm:aspect-auto sm:h-full sm:min-h-0"
               : big ? "aspect-video lg:aspect-auto lg:h-full" : "aspect-video"}`}>
        {s ? (
          <>
            {/* Straight from the master; cookies ride along, no proxy. A
                local-AI slot pulls the system's own detection feed. The
                tiles are cross-origin, but IntersectionObserver watches
                the ELEMENT, not the response, so StreamImg still tears a
                scrolled-away tile down. */}
            <DetectGate {...gateOf(s)} slug={s.slug} camera={cameraOf(s)}
                        className="size-full">
            <StreamImg
              src={urlOf(s)}
              alt={`${s.name} - ${s.label}`}
              className="size-full"
              imgClassName="size-full object-contain"
              overlay={ov
                ? <OverlayCanvas slug={s.slug} camera={cameraOf(s)}
                                 delayMs={ov.delay_ms || 0} />
                : undefined}
            />
            </DetectGate>
            <div className="absolute inset-x-0 top-0 flex items-center justify-between gap-2 bg-gradient-to-b from-black/60 to-transparent p-2">
              <span className="truncate text-xs font-medium text-white">
                {s.name} · {s.label}
              </span>
              <div className="flex items-center gap-1">
                {layout === "spotlight" && i !== pinned && (
                  <button
                    type="button"
                    onClick={() => setPinned(i)}
                    aria-label="Make this the main feed"
                    className="flex size-6 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/70"
                  >
                    <Pin className="size-3.5" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => clear(i)}
                  aria-label="Close this feed"
                  className="flex size-6 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/70"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            </div>
          </>
        ) : (
          <SlotPicker systems={systems}
                      onPick={(sys, host) => fill(i, sys, host)}
                      onPickLocal={(sys, cam) => fillLocal(i, sys, cam)} />
        )}
      </div>
    );
  }

  return (
    <div ref={wallRef} className="flex flex-col gap-3" style={fitH ? { height: fitH } : undefined}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {open ? `${open} of ${SLOTS} feeds open - each pulls continuously; clear the ones you are not watching.`
                : `Pick a system and camera in any slot - up to ${SLOTS} live feeds at once.`}
        </p>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border p-0.5">
            <Button variant={layout === "grid" ? "default" : "ghost"} size="sm"
                    className="h-7 px-2" onClick={() => setLayout("grid")}>
              Grid
            </Button>
            <Button variant={layout === "spotlight" ? "default" : "ghost"} size="sm"
                    className="h-7 px-2" onClick={() => setLayout("spotlight")}>
              Spotlight
            </Button>
          </div>
        </div>
      </div>
      {layout === "grid" ? (
        <div className="grid min-h-0 flex-1 gap-3 sm:grid-cols-2 sm:grid-rows-2">
          {slots.map((_, i) => tile(i))}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
          <div className="min-h-0 lg:flex-1">{tile(pinned, { big: true })}</div>
          <div className="flex gap-3 overflow-x-auto lg:w-56 lg:flex-none lg:flex-col lg:overflow-y-auto lg:overflow-x-visible">
            {slots.map((_, i) => i === pinned ? null : (
              <div key={i} className="w-40 shrink-0 lg:w-auto">{tile(i)}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
