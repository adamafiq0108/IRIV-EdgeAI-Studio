// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Data-fetching hooks over the api client. useApi fetches once; usePoll refetches
// on an interval (the master refreshes device/monitoring state every sync cycle,
// so pages poll to stay live without a websocket). Both surface {data, error,
// loading, reload}. A 401 is re-thrown to the caller via `error` so pages/guards
// can route to /login.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./client.js";

//: The last good payload per path, kept for the life of the tab. Navigating
//: away unmounts a page and threw its data away, so coming back showed a
//: skeleton and waited for the network again -- on a Pi that is the whole
//: "pages are slow to open" feeling, for data that was correct a second ago.
//: A cached payload paints at once and is replaced by the fresh one the
//: moment it lands; nothing is served from here without a refetch behind it.
const lastGood = new Map();

export function useApi(path, { immediate = true } = {}) {
  const [data, setData] = useState(() => lastGood.get(path) ?? null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(immediate && !lastGood.has(path));
  // Responses can arrive out of order - a poll issued before an action lands
  // after the post-action reload and re-writes the state the user just
  // changed, which reads as the UI flickering back. Only the newest request
  // may set data.
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    try {
      const d = await api.get(path);
      lastGood.set(path, d);
      if (mine === seq.current) {
        setData(d);
        setError(null);
      }
      return d;
    } catch (e) {
      if (mine === seq.current) setError(e);
      throw e;
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [path]);

  // A path change re-reads the cache, so switching camera/table/system shows
  // that path's last answer instead of the previous path's numbers.
  useEffect(() => {
    const cached = lastGood.get(path);
    if (cached !== undefined) setData(cached);
    if (immediate) reload().catch(() => {});
  }, [reload, immediate, path]);

  return { data, error, loading, reload, setData };
}

// The same pause-when-hidden rule for pages that poll by hand rather than by
// path. `fn` is re-read from a ref, so an inline callback does not restart the
// timer on every render.
export function useVisibleInterval(fn, intervalMs) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    let id = null;
    const start = () => { if (id == null) id = setInterval(() => saved.current(), intervalMs); };
    const stop = () => { if (id != null) { clearInterval(id); id = null; } };
    const onVisibility = () => {
      if (document.hidden) stop();
      else { saved.current(); start(); }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, [intervalMs]);
}

export function usePoll(path, intervalMs = 5000) {
  const { data, error, loading, reload, setData } = useApi(path);
  const saved = useRef(reload);
  saved.current = reload;

  // A hidden tab polls nothing: the answers would be thrown away, and every
  // open browser tab costs the master a request per interval. Coming back
  // refetches at once, so a change made elsewhere (detection switched on from
  // System Control) is already true by the time the page is readable, instead
  // of showing a stale value for a whole interval.
  useEffect(() => {
    let id = null;
    const start = () => { if (id == null) id = setInterval(() => saved.current().catch(() => {}), intervalMs); };
    const stop = () => { if (id != null) { clearInterval(id); id = null; } };
    const onVisibility = () => {
      if (document.hidden) stop();
      else { saved.current().catch(() => {}); start(); }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, [intervalMs]);

  return { data, error, loading, reload, setData };
}
