import { friendlyError } from "../api/client.js";

// Shared loading and empty states.
//
// SKELETONS over spinners, wherever the shape of the answer is already known.
// A spinner says "something is happening"; a skeleton says "a table with rows
// is coming, here is where it will be" -- and when the data lands nothing
// jumps, because the space was already the right size. On a board polling a
// camera over the network, that difference is most of the page's felt speed.
//
// `Loading` stays for the places where the shape genuinely is not known yet,
// and for small inline waits where a skeleton would be more furniture than
// the thing it stands in for.
export function Loading({ label = "Loading…" }) {
  return <div className="muted" style={{ padding: 24, textAlign: "center" }}>{label}</div>;
}

export function Empty({ children }) {
  return <div className="muted" style={{ padding: 24, textAlign: "center" }}>{children}</div>;
}

export function ErrorNote({ error }) {
  const msg = friendlyError(error);
  return <div className="muted" style={{ padding: 24, textAlign: "center" }}>{msg}</div>;
}

/** One grey bar. `w` accepts any CSS width so callers can vary line lengths --
 *  a skeleton of identical bars reads as a loading GRAPHIC rather than as
 *  text that is about to arrive. */
export function SkelLine({ w = "100%", h = 12, style }) {
  return <div className="skel" style={{ width: w, height: h, ...style }} />;
}

/** A card-shaped placeholder: title, a couple of lines, optional rows. */
export function SkelCard({ rows = 3, title = true }) {
  return (
    <div className="panel-card">
      {title && <SkelLine w="38%" h={14} style={{ marginBottom: 14 }} />}
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} style={{ display: "flex", gap: 12, alignItems: "center",
                              marginBottom: 10 }}>
          <SkelLine w={`${58 - (i % 3) * 9}%`} />
          <SkelLine w="72px" style={{ marginLeft: "auto", flex: "none" }} />
        </div>
      ))}
    </div>
  );
}

/* A KEY on every skeleton, and it is load-bearing.
 *
 * A page renders <section className="page active"> and its skeleton renders
 * <section> too, at the same position -- so React reconciles them as the SAME
 * element and simply swaps the children. The DOM node is never replaced, so
 * the CSS entrance animation (which runs on mount) never runs: the content
 * appeared instantly however long the transition was set to.
 *
 * A key the real page does not share forces the placeholder to unmount and
 * the page to mount, which is what makes the fade happen at all.
 */
/** The whole-page shape most of this app uses: a title, then cards. */
export function SkelPage({ title = "", cards = 2, rows = 3 }) {
  return (
    <section key="skel" className="page active page-skel">
      <div className="page-head">
        <div className="page-title">{title || <SkelLine w={160} h={18} />}</div>
      </div>
      {Array.from({ length: cards }, (_, i) => <SkelCard key={i} rows={rows} />)}
    </section>
  );
}

/** A table waiting for rows: header bar plus evenly spaced row bars. */
export function SkelTable({ rows = 6, cols = 4 }) {
  return (
    <div className="panel-card">
      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        {Array.from({ length: cols }, (_, c) => (
          <SkelLine key={c} w={`${100 / cols}%`} h={10} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} style={{ display: "flex", gap: 12, marginBottom: 9 }}>
          {Array.from({ length: cols }, (_, c) => (
            <SkelLine key={c} w={`${100 / cols}%`} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** A live-video panel before the first frame: the box the feed will fill. */
export function SkelFeed({ height = 240 }) {
  return <div className="skel" style={{ height, borderRadius: 10 }} />;
}

/** A two-column editor: canvas/feed on the left, inspector rail on the right.
 *  Workflow, Zones, Builder and the analytics editor all share this shape.
 *  `title` prints the page's real name when the caller knows it (the route
 *  does), so the placeholder and the page share a heading and nothing
 *  changes shape between them. */
export function SkelEditor({ railFirst = false, title = "" }) {
  const canvas = <div className="panel-card" style={{ flex: "1 1 0", minWidth: 0 }}>
    <SkelLine w="30%" h={14} style={{ marginBottom: 12 }} />
    <SkelFeed height={320} />
  </div>;
  const rail = <div className="panel-card" style={{ flex: "0 1 320px", minWidth: 0 }}>
    <SkelLine w="45%" h={14} style={{ marginBottom: 12 }} />
    {[0, 1, 2, 3, 4].map((i) => (
      <div key={i} style={{ marginBottom: 12 }}>
        <SkelLine w={`${40 + (i % 3) * 12}%`} h={10} style={{ marginBottom: 5 }} />
        <SkelLine h={28} />
      </div>
    ))}
  </div>;
  return (
    <section key="skel" className="page active page-skel">
      <div className="page-head">
        <div className="page-title">{title || <SkelLine w={150} h={18} />}</div>
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start",
                    flexWrap: "wrap" }}>
        {railFirst ? <>{rail}{canvas}</> : <>{canvas}{rail}</>}
      </div>
    </section>
  );
}

/** The whole app before its first answer: sidebar, header bar, content.
 *  This is the ONE place a skeleton earns the most -- it stands in for a
 *  blank screen on a cold load, which is the slowest moment the product has. */
export function SkelShell() {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <div style={{ width: 220, flex: "none", padding: 12,
                    borderRight: "1px solid var(--line)" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 18 }}>
          <SkelLine w={32} h={32} style={{ borderRadius: 8, flex: "none" }} />
          <SkelLine w="60%" h={12} />
        </div>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <SkelLine key={i} w={`${62 + (i % 3) * 12}%`} h={14}
                    style={{ marginBottom: 12 }} />
        ))}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ height: 64, borderBottom: "1px solid var(--line)",
                      display: "flex", alignItems: "center", padding: "0 16px" }}>
          <SkelLine w={160} h={14} />
        </div>
        <div style={{ padding: 16 }}>
          <SkelCard rows={3} />
          <SkelCard rows={4} />
        </div>
      </div>
    </div>
  );
}
