// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
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

/** The whole app before its first answer: the top bar and a content area.
 *  The hub has no side rail any more -- the logo and name sit at the left of
 *  the bar and the account menu at the right -- so the placeholder is that bar
 *  and nothing beside it. This is the ONE place a skeleton earns the most: it
 *  stands in for a blank screen on a cold load, the slowest moment the
 *  product has. */
export function SkelShell() {
  return (
    <div style={{ minHeight: "100vh" }}>
      <div style={{ height: 56, borderBottom: "1px solid var(--line)",
                    display: "flex", alignItems: "center", gap: 10, padding: "0 16px" }}>
        <SkelLine w={36} h={36} style={{ borderRadius: 8, flex: "none" }} />
        <SkelLine w={150} h={14} />
        <SkelLine w={34} h={34} style={{ borderRadius: "50%", marginLeft: "auto", flex: "none" }} />
      </div>
      <div style={{ padding: 16 }}>
        <SkelHome />
      </div>
    </div>
  );
}

/** Home: the systems grid. Same grid and tile height as the real cards, so
 *  nothing moves when they arrive. */
export function SkelHome({ tiles = 6 }) {
  return (
    <div key="skel" className="page active page-skel mx-auto w-full max-w-[1400px] space-y-5">
      <SkelLine w={140} h={20} style={{ marginTop: 8 }} />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(17rem,1fr))] gap-5">
        {Array.from({ length: tiles }, (_, i) => (
          <div key={i} className="panel-card"
               style={{ minHeight: "14rem", display: "flex", flexDirection: "column", gap: 10 }}>
            <SkelFeed height={96} />
            <SkelLine w={`${52 + (i % 3) * 12}%`} h={14} />
            <SkelLine w="34%" h={10} />
            <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
              <SkelLine w={70} h={28} style={{ borderRadius: 8 }} />
              <SkelLine w={28} h={28} style={{ borderRadius: 8, marginLeft: "auto" }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Workflow: node palette, canvas, and the right column (the guide card above
 *  the properties). `wf-page` and `wf-grid` are the real page's own classes,
 *  so the three columns stack on a phone exactly as the page does. */
export function SkelWorkflow() {
  return (
    <section key="skel" className="page active wf-page page-skel">
      <div className="page-head">
        <div className="page-title"><SkelLine w={110} h={18} /></div>
      </div>
      <div className="wf-grid"
           style={{ display: "grid", gap: 12, minHeight: "68vh",
                    gridTemplateColumns: "240px minmax(0,1fr) 320px" }}>
        <div className="panel-card wf-rail wf-rail-left">
          <SkelLine h={32} style={{ marginBottom: 8 }} />
          <SkelLine h={30} style={{ marginBottom: 12, borderRadius: 8 }} />
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <SkelLine key={i} h={34} style={{ marginBottom: 8, borderRadius: 8 }} />
          ))}
        </div>
        <div className="panel-card wf-canvas-wrap" style={{ minWidth: 0 }}>
          <SkelFeed height={460} />
        </div>
        <div className="wf-rail wf-rail-right" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="panel-card">
            <SkelLine w="55%" h={14} style={{ marginBottom: 10 }} />
            <SkelLine h={10} style={{ marginBottom: 6 }} />
            <SkelLine w="80%" h={10} />
          </div>
          <div className="panel-card" style={{ flex: 1 }}>
            <SkelLine w="45%" h={14} style={{ marginBottom: 12 }} />
            {[0, 1, 2, 3].map((i) => (
              <div key={i} style={{ marginBottom: 12 }}>
                <SkelLine w={`${40 + (i % 3) * 12}%`} h={10} style={{ marginBottom: 5 }} />
                <SkelLine h={28} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/** Database: the table list down the left, the rows on the right. */
export function SkelDatabase() {
  return (
    <section key="skel" className="page active db-page page-skel">
      <div className="db-shell">
        <aside className="db-side">
          <SkelLine h={34} style={{ marginBottom: 10, borderRadius: 8 }} />
          <SkelLine h={30} style={{ marginBottom: 10, borderRadius: 8 }} />
          {[0, 1, 2, 3, 4].map((i) => (
            <SkelLine key={i} h={44} style={{ marginBottom: 8, borderRadius: 8 }} />
          ))}
        </aside>
        <div className="db-main">
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <SkelLine w={160} h={20} />
            <SkelLine w={90} h={30} style={{ marginLeft: "auto", borderRadius: 8 }} />
            <SkelLine w={90} h={30} style={{ borderRadius: 8 }} />
          </div>
          <SkelTable rows={10} cols={5} />
        </div>
      </div>
    </section>
  );
}

/** Nodes Guideline: node list, the selected node, then how it connects. */
export function SkelGuideline() {
  return (
    <section key="skel" className="page active page-skel">
      <div className="page-head" style={{ justifyContent: "flex-start", gap: 12 }}>
        <div className="page-title">Nodes Guideline</div>
      </div>
      <div className="ng-grid">
        <aside className="ng-col">
          <SkelLine h={32} style={{ marginBottom: 12, borderRadius: 8 }} />
          {[0, 1, 2, 3, 4, 5, 6].map((i) => (
            <SkelLine key={i} h={34} style={{ marginBottom: 8, borderRadius: 8 }} />
          ))}
        </aside>
        <div className="ng-col">
          <SkelFeed height={160} />
          <SkelLine w="40%" h={16} style={{ margin: "16px 0 10px" }} />
          {[0, 1, 2, 3].map((i) => (
            <SkelLine key={i} w={`${90 - i * 12}%`} h={10} style={{ marginBottom: 8 }} />
          ))}
        </div>
        <div className="ng-col">
          {[0, 1].map((i) => (
            <div key={i} style={{ marginBottom: 14 }}>
              <SkelLine w="50%" h={14} style={{ marginBottom: 10 }} />
              <SkelFeed height={150} />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/** About: the headline card, then the readings on the left and the software on
 *  the right, in the real page's own grid. */
export function SkelAbout() {
  // Rows of three, the last row sharing the width (as the real tiles do).
  const tiles = (n) => (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className={"rounded-lg border p-3 " + (
          i >= n - (n % 3 || 3) && n % 3 === 2 ? "md:col-span-3"
            : i >= n - (n % 3 || 3) && n % 3 === 1 ? "md:col-span-6" : "md:col-span-2")}>
          <SkelLine w="50%" h={10} style={{ marginBottom: 10 }} />
          <SkelLine w="40%" h={22} style={{ marginBottom: 12 }} />
          <SkelLine h={8} style={{ borderRadius: 8 }} />
        </div>
      ))}
    </div>
  );
  return (
    <div key="skel" className="page active page-skel mx-auto w-full max-w-[1280px] space-y-5">
      <div className="panel-card" style={{ display: "flex", gap: 20, alignItems: "center" }}>
        <SkelLine w={56} h={56} style={{ borderRadius: 10, flex: "none" }} />
        <div style={{ flex: 1 }}>
          <SkelLine w="34%" h={22} style={{ marginBottom: 10 }} />
          <SkelLine w="22%" h={12} />
        </div>
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-2 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5 lg:row-span-2 xl:row-span-1">
          <div className="panel-card"><SkelLine w="24%" h={14} style={{ marginBottom: 14 }} />{tiles(5)}</div>
          <div className="panel-card"><SkelLine w="24%" h={14} style={{ marginBottom: 14 }} />{tiles(5)}</div>
        </div>
        <SkelCard rows={3} />
        <SkelCard rows={3} />
      </div>
    </div>
  );
}
