// Version comparison, the same rule the server uses (core/version.py parse /
// is_newer): numeric major.minor.patch, a leading "v" ignored, anything after
// the digits of a part ignored, missing parts read 0. Never a string compare --
// "1.10.0" is newer than "1.9.0".

export function parseVersion(v) {
  const s = String(v ?? "").trim().replace(/^v/i, "");
  const parts = s.split(".").slice(0, 3).map((piece) => {
    const m = piece.match(/^\d+/);
    return m ? Number(m[0]) : 0;
  });
  while (parts.length < 3) parts.push(0);
  return parts;
}

/** True when `candidate` is strictly newer than `current`. */
export function isNewer(candidate, current) {
  const a = parseVersion(candidate);
  const b = parseVersion(current);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

/** Does this update state offer an update RIGHT NOW? The server's flag AND a
 *  release genuinely newer than the version this page is talking to. The
 *  second half is what keeps an Update button from surviving the update it
 *  offered: a state read before the box restarted (or a cache written by the
 *  old version) still says "available", but its `latest` is no longer newer
 *  than `current`. The same version re-cut is not newer either. */
export function offersUpdate(state) {
  return !!(state && state.update_available && state.latest
            && isNewer(state.latest, state.current));
}
