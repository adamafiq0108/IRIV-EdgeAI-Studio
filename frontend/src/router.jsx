// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Route table. Public auth views render standalone; everything else renders
// inside <AppShell> (which enforces the session and provides shell context).
// Client routes map 1:1 to the nav page ids (state/app.jsx PAGE_PATHS).
import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import AppShell from "./components/AppShell.jsx";
import { SkelDatabase, SkelEditor, SkelGuideline, SkelPage, SkelWorkflow } from "./components/Loading.jsx";
import { BASE } from "./lib/base.js";

import Login from "./pages/Login.jsx";
import Forgot from "./pages/Forgot.jsx";
import ResetPassword from "./pages/ResetPassword.jsx";
import AccountSetup from "./pages/AccountSetup.jsx";

import Home from "./pages/Home.jsx";
import NotFound from "./pages/NotFound.jsx";

// SPLIT, not eager: one 1.7 MB bundle had to be downloaded and PARSED before
// the first page could paint, and most of it is the canvas editor (@xyflow),
// the two builders and the database views -- pages an operator may never
// open. Each is fetched the first time it is needed and cached by the
// browser after that; `lazy` keeps them out of the main chunk entirely.
const Workflow = lazy(() => import("./pages/Workflow.jsx"));
const NodeGuideline = lazy(() => import("./pages/NodeGuideline.jsx"));
const Analytics = lazy(() => import("./pages/Analytics.jsx"));
const AnalyticsBuilder = lazy(() => import("./pages/AnalyticsBuilder.jsx"));
const EventTable = lazy(() => import("./pages/EventTable.jsx"));
const Footage = lazy(() => import("./pages/Footage.jsx"));
const Database = lazy(() => import("./pages/Database.jsx"));
const Dataset = lazy(() => import("./pages/Dataset.jsx"));
const DatasetLibrary = lazy(() => import("./pages/DatasetLibrary.jsx"));
const Audit = lazy(() => import("./pages/Audit.jsx"));
const Logs = lazy(() => import("./pages/Logs.jsx"));
const Control = lazy(() => import("./pages/Control.jsx"));
const Builder = lazy(() => import("./pages/Builder.jsx"));
const Blynk = lazy(() => import("./pages/Blynk.jsx"));
const Modbus = lazy(() => import("./pages/Modbus.jsx"));
const Models = lazy(() => import("./pages/Models.jsx"));
// Camera Setup too (perf item L6): it carries the zone canvas and the camera
// controls, and the first screen anyone sees is Home, not this.
const Detection = lazy(() => import("./pages/Detection.jsx"));

/** A split page, with the same skeleton the pages use while it arrives.
 *  `fallback` overrides the card-shaped default for a page whose own
 *  skeleton is a different shape: the chunk's placeholder and the page's
 *  first render must look the SAME, or the operator sees two skeletons in a
 *  row (generic cards, then the editor) before anything real. */
const L = (node, title, fallback = null) => (
  <Suspense fallback={fallback || <SkelPage title={title} cards={2} rows={4} />}>
    {node}
  </Suspense>
);
// Camera Setup is an editor (feed on the left, rail on the right), and holds
// that shape from the chunk request until its first status answer.
const CAMERA_SKEL = <SkelEditor title="Camera Setup" />;

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/forgot", element: <Forgot /> },
  { path: "/reset-password", element: <ResetPassword /> },
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/detection", element: L(<Detection />, "Camera Setup", CAMERA_SKEL) },
      // Zones are drawn ON Camera Setup now (one page); the old address
      // still answers so bookmarks and older links keep working.
      { path: "/zones", element: L(<Detection />, "Camera Setup", CAMERA_SKEL) },
      { path: "/workflow", element: L(<Workflow />, "Workflow", <SkelWorkflow />) },
      // Every node, what it connects to and what each wire means: opened by the
      // Guideline button on the Workflow page.
      { path: "/workflow/guideline", element: L(<NodeGuideline />, "Nodes Guideline", <SkelGuideline />) },
      { path: "/analytics", element: L(<Analytics />, "Analytics") },
      // The Overview's composer. A page of its own, not a mode of the Analytics
      // page: arranging a dashboard needs the whole window.
      { path: "/analytics/builder", element: L(<AnalyticsBuilder />, "Analytics") },
      { path: "/events", element: L(<EventTable />, "Events") },
      // This system's own tables: their shape and their rows, one tool.
      // Distinct from /dataset, which captures training photos and clips.
      { path: "/database", element: L(<Database />, "Database", <SkelDatabase />) },
      // The rows used to live on their own page. Kept as a redirect so old
      // links, bookmarks and the workflow's "view rows" still land somewhere.
      { path: "/tables", element: <Navigate to="/database" replace /> },
      { path: "/footage", element: L(<Footage />, "Footage") },
      { path: "/dataset", element: L(<Dataset />, "Dataset") },
      { path: "/dataset-library", element: L(<DatasetLibrary />, "Datasets") },
      { path: "/audit", element: L(<Audit />, "Audit") },
      { path: "/logs", element: L(<Logs />, "System Log") },
      { path: "/manage/:host", element: L(<Control />, "System") },
      { path: "/builder", element: L(<Builder />, "Dashboard") },
      { path: "/blynk", element: L(<Blynk />, "Blynk") },
      { path: "/modbus", element: L(<Modbus />, "Modbus") },
      { path: "/models", element: L(<Models />, "AI Models") },
      // Always routed, never always reachable: the page itself is what says
      // "this board has no Hailo-10H" / "no Gen AI model is switched on", so a
      // shared link lands on an explanation rather than on the 404 page.
      { path: "/account-setup", element: <AccountSetup /> },
      { path: "*", element: <NotFound /> },
    ],
  },
// Under the reverse proxy a system lives at /s/<slug>/; every route above is
// relative to that (lib/base.js). "" on the hub and on a system's own port.
], { basename: BASE || "/" });
