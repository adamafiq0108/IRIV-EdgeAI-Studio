// Root route. The same SPA serves both the hub and every master; bootstrap's
// `mode` decides what "/" is: the systems grid (hub) or the system's Main
// overview (master).
//
// Both are SPLIT (perf item L6): a hub never shows Main and a master never
// shows Hub, and Main drags the charting library (recharts) in with its
// widgets -- statically imported here it all sat in the entry chunk every
// page, the login included, had to download and parse first.
import { lazy, Suspense } from "react";
import { useApp } from "../state/app.jsx";
import { SkelPage } from "../components/Loading.jsx";

const Hub = lazy(() => import("./Hub.jsx"));
const Main = lazy(() => import("./Main.jsx"));

export default function Home() {
  const { mode } = useApp();
  return (
    <Suspense fallback={<SkelPage title={mode === "hub" ? "Systems" : "Dashboard"}
                                  cards={2} rows={4} />}>
      {mode === "hub" ? <Hub /> : <Main />}
    </Suspense>
  );
}
