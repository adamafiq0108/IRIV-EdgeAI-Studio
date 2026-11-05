// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A short slideshow for a page: what the page is for and how to use it, one
// picture per step. Opens the first time a page is visited (a tick box keeps it
// from opening again) and from the Guide button in the page's header. The
// pictures are drawn with the app's own styles and the wording of its own
// buttons, in the same way as the Workflow guideline (GuideFlow.jsx).
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { GuideSwitchRow } from "../components/GuideSwitch.jsx";
import { useLocation } from "react-router-dom";
import { BookOpen, Camera, Circle, Menu, Pentagon, Play, SlidersHorizontal, Spline, SquareDashed } from "lucide-react";
import { GUIDE_REQUEST_EVENT, guidesEnabled, setupTourActive } from "./guideState.js";
import { Welcome } from "./GuideWelcome.jsx";
import { BarSnapshot, BuilderSnapshot, DroppedSnapshot, MenuSnapshot, PageSnapshot }
  from "./GuideFlow.jsx";

// ---- small pictures of the page's own controls --------------------------
function Panel({ title, children }) {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="panel">
      {title && <div className="ng-pg-title">{title}</div>}
      {children}
    </div>
  );
}

/** A model card as the AI Models page draws it. `hot` rings the part to press. */
function ModelCard({ hot }) {
  return (
    <div className={"ng-snap ng-pg-shot ng-model" + (hot === "card" ? " ng-new" : "")} data-ng-snap="model">
      <div className="ng-model-head">
        <strong>yolov8n</strong>
        <span className="ng-model-active">Active</span>
      </div>
      <div className="ng-model-tags">
        <span>built-in</span><span className="ng-model-tag-on">hailo8</span><span>.hef on this box</span>
      </div>
      <div className="muted small">YOLOv8-nano, 80 COCO classes. Fastest; best for Hailo-8L or 4+ streams.</div>
      <div className="ng-pg-row"><span className="btn btn-sm">In use &middot; click to stop</span></div>
      <div className={"ng-model-chevron" + (hot === "chevron" ? " ng-new" : "")}>&#8964;</div>
    </div>
  );
}

/** The Detection settings that open under a model card. */
function ModelSettings({ hot }) {
  const ring = (k) => (hot === k ? " ng-new" : "");
  return (
    <div className="ng-snap ng-pg-shot ng-model" data-ng-snap="model-settings">
      <div className="ng-pg-title">Detection settings</div>
      <div className={"ng-model-conf" + ring("confidence")}>
        <span className="muted small">Minimum confidence</span>
        <input type="range" min="0" max="100" value="50" readOnly style={{ flex: 1 }} />
        <span className="input" style={{ width: 54, textAlign: "center" }}>0.5</span>
        <span className="muted small">50%</span>
      </div>
      <div className={"ng-model-classes" + ring("classes")}>
        <span className="muted small">Classes to keep</span>
        <span className="ng-model-chip">person &times;</span>
        <span className="input">Search classes...</span>
        <span className="ng-pg-row"><input type="checkbox" checked readOnly /> person</span>
        <span className="ng-pg-row"><input type="checkbox" readOnly /> bicycle</span>
        <span className="ng-pg-row"><input type="checkbox" readOnly /> car</span>
        <span className="muted small">1 of 80 classes kept</span>
      </div>
      <div className="ng-pg-row" style={{ justifyContent: "space-between" }}>
        <span className="btn btn-sm">Clear classes</span>
        <span className={"btn btn-sm btn-accent" + ring("save")}>Save settings</span>
      </div>
    </div>
  );
}

/** The Cameras panel on Camera Setup. */
function CamList({ hot, two = false, empty = false }) {
  const row = (n, src, ring) => (
    <div className="ng-pg-row" style={{ justifyContent: "space-between" }}>
      <span><strong>Camera {n}</strong> <span className="muted small">{src}</span><br />
        <span className="muted small">Idle</span></span>
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className={"ng-toggle" + (ring ? " ng-new" : "")}><i /></span>
        <SlidersHorizontal size={16} />
        <span className="ng-x">&times;</span>
      </span>
    </div>);
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="cameras">
      <div className="ng-pg-title">Cameras ({empty ? 0 : two ? 2 : 1})</div>
      {empty && <span className="muted small">No camera yet.</span>}
      {!empty && row(1, two ? "capture_20261030_121513.mp4" : "camera", hot === "toggle")}
      {two && row(2, "capture_20261030_121513.mp4", false)}
      <span className={"ng-pg-row ng-dashed" + (hot === "add" ? " ng-new" : "")}
            style={{ justifyContent: "center", fontWeight: 600 }}>+ Add camera</span>
      <span className="muted small">{empty ? 0 : two ? 2 : 1} out of 4</span>
    </div>
  );
}

/** The Add a camera dialog; `hot` rings the part the step is about. */
function CamDialog({ hot }) {
  const ring = (k) => (hot === k ? " ng-new" : "");
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="add-camera">
      <div className="ng-pg-title">Add a camera</div>
      <div className={"ng-cam-kinds" + ring("kind")}>
        <span className="btn btn-sm btn-accent">USB</span>
        <span className="btn btn-sm">RTSP / IP</span>
        <span className="btn btn-sm">GigE</span>
        <span className="btn btn-sm">Video / Image</span>
      </div>
      <div className={"ng-cam-device" + ring("device")}>
        <span className="muted small">USB / UVC cameras (V4L2)</span>
        <span style={{ display: "flex", gap: 6 }}>
          <span className="input" style={{ flex: 1 }}>- select a camera -</span>
          <span className="btn btn-sm">&#8635;</span>
        </span>
      </div>
      <div className={"ng-cam-quality" + ring("quality")}>
        <span className="ng-pg-title">Quality</span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="ng-toggle"><i /></span> High Resolution</span>
        <span className="btn btn-sm" style={{ width: "fit-content" }}>Check Camera Capabilities</span>
      </div>
      <div className={"ng-cam-boxes" + ring("boxes")}>
        <span className="ng-pg-title">Detection Box Display</span>
        <span><input type="radio" readOnly /> Draw boxes in the browser</span>
        <span><input type="radio" checked readOnly /> Embed boxes in the video</span>
      </div>
      <div className="ng-pg-row" style={{ justifyContent: "space-between" }}>
        <span className="btn btn-sm">Preview Snapshot</span>
        <span style={{ display: "flex", gap: 6 }}>
          <span className="btn btn-sm">Cancel</span>
          <span className={"btn btn-sm btn-accent" + ring("add")}>&#10003; Add camera</span>
        </span>
      </div>
    </div>
  );
}

/** The camera panel before detection runs: Start to press, a black picture to draw on. */
function StartFeed() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="start-feed">
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span className="btn btn-sm"><Menu size={14} /></span>
        <span className="btn btn-sm ng-btn-ico"><SquareDashed size={14} /> Box</span>
        <span className="btn btn-sm ng-btn-ico"><Circle size={14} /> Circle</span>
        <span className="btn btn-sm ng-btn-ico"><Pentagon size={14} /> Polygon</span>
        <span className="btn btn-sm ng-btn-ico"><Spline size={14} /> Line</span>
        <span style={{ flex: 1 }} />
        <span className="btn btn-sm btn-accent ng-btn-ico ng-new"><Play size={13} /> Start</span>
      </span>
      <div className="ng-dash-feed" style={{ height: 120 }}>
        Press Start Detection above to see the feed and draw zones
      </div>
    </div>
  );
}

function ZoneTools() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="zone-tools">
      <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <span className="btn btn-sm ng-btn-ico ng-new"><SquareDashed size={14} /> Box</span>
        <span className="btn btn-sm ng-btn-ico"><Circle size={14} /> Circle</span>
        <span className="btn btn-sm ng-btn-ico"><Pentagon size={14} /> Polygon</span>
        <span className="btn btn-sm ng-btn-ico"><Spline size={14} /> Line</span>
      </span>
      <div className="ng-dash-feed">Draw on the camera picture</div>
      <div className="ng-pg-title">Zones (0)</div>
      <span className="muted small">No zones yet. Draw one on the feed.</span>
    </div>
  );
}

function ZoneList() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="zones">
      <div className="ng-pg-title">Zones (2) <span className="mono muted small">on Camera 1</span></div>
      <div className="ng-pg-row ng-new" style={{ justifyContent: "space-between" }}>
        <span>Zone (Cam 1)</span><span className="muted small">area</span></div>
      <div className="ng-pg-row" style={{ justifyContent: "space-between" }}>
        <span>Zone (Sensor-Plate) CAM1</span><span className="muted small">area</span></div>
    </div>
  );
}

function CamTopBar() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="cam-topbar">
      <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <span className="ng-seg"><b>Live View</b><i>Stopped</i></span>
        <span className="btn btn-sm">hailo &middot; panel</span>
        <span className="btn btn-sm ng-btn-ico ng-new"><Camera size={14} /> Camera Setting</span>
        <span className="btn btn-sm ng-pill-green">Snapshots</span>
        <span className="btn btn-sm ng-pill-blue">Clips</span>
      </span>
    </div>
  );
}

function StatusBox() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="status-box">
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span className="btn btn-sm ng-new"><Menu size={14} /></span>
        <span className="btn btn-sm ng-btn-ico"><SquareDashed size={14} /> Box</span>
        <span className="btn btn-sm ng-btn-ico"><Circle size={14} /> Circle</span>
      </span>
      <span className="muted small">Status box :</span>
      <span style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <span className="btn btn-sm ng-pill-blue">Model</span><span className="btn btn-sm ng-pill-blue">Camera</span>
        <span className="btn btn-sm ng-pill-blue">Hailo FPS</span><span className="btn btn-sm ng-pill-blue">Camera FPS</span>
      </span>
    </div>
  );
}

/** The builder's header: the unsaved badge and the Save button. */
function SaveBar() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="save-bar">
      <span style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end" }}>
        <span className="ng-model-active" style={{ background: "rgba(245,158,11,.18)", color: "#b45309" }}>unsaved</span>
        <span className="btn btn-sm ng-new">Save</span>
      </span>
    </div>
  );
}

/** The Dashboard: one page, no tabs. The camera pictures on top, the cards under them. */
function DashboardShot() {
  return (
    <div className="ng-snap ng-pg-shot ng-dash" data-ng-snap="dashboard">
      <div className="ng-dash-head">
        <strong>Dashboard</strong>
        <span className="ng-dash-btns">
          <span className="btn btn-sm btn-accent">Start</span>
          <span className="btn btn-sm">Edit dashboard</span>
          <span className="btn btn-sm">Analytics &rarr;</span>
        </span>
      </div>
      <div className="ng-dash-video">
        <span>Panel CAM 1</span>
        <div className="ng-dash-feed">Press Start above to bring the feed back</div>
      </div>
      <div className="ng-snap-row">
        <div className="stat-card ng-snap-card ng-new">
          <div className="stat-label">People Counter</div>
          <div className="stat-value">12</div>
        </div>
        <div className="stat-card ng-snap-card">
          <div className="stat-label">Total Violations (Day)</div>
          <div className="stat-value">393</div>
        </div>
      </div>
    </div>
  );
}

const NEEDED = [
  ["AI Models", "a model chosen, and the classes you want to detect"],
  ["Camera Setup", "the camera added and working, with your zones drawn"],
  ["Database", "the table created for anything you want to chart"],
  ["Workflow", "the pipeline built, with the cards and charts you want to see"],
];

const Checks = ({ rows, title = "Set these up first, in this order" }) => (
  <Panel title={title}>
    {rows.map(([name, hint, hot]) => (
      <div key={name} className={"ng-pg-row" + (hot ? " ng-new" : "")}>
        <span className="ng-pg-tick">&#10003;</span>
        <span><strong>{name}</strong><br /><span className="muted small">{hint}</span></span>
      </div>
    ))}
  </Panel>
);

// ---- the guides ---------------------------------------------------------
export const GUIDES = {
  dashboard: {
    title: "Dashboard guide",
    welcome: ["Welcome to the Dashboard", "Your live page: counts, charts and the camera picture, all in one place."],
    steps: [
      { title: "Your live page", shot: () => <DashboardShot />,
        body: "The Dashboard is one page, with no tabs. It shows the cards you built on the Workflow, live: counts, "
          + "violations, charts and the camera picture. Add or move cards any time with "
          + "Edit dashboard." },
      { title: "Before you use this page", shot: () => (
          <Checks title="Needed before this page has anything to show" rows={NEEDED} />),
        body: "This page shows what your workflow measures, so finish these first: AI "
          + "Models, Camera Setup, Database and the pipeline on the Workflow. The Start "
          + "button only appears once the camera and model are set up." },
      { title: "Edit dashboard", shot: () => <BarSnapshot kind="dashbar" />,
        body: "Press Edit dashboard to lay the page out." },
      { title: "Add your cards", shot: () => <BuilderSnapshot />,
        body: "The cards you drew on the Workflow are listed on the left under Dashboard "
          + "cards. Drag one onto the page, or click it to add it at the end." },
      { title: "Size and arrange", shot: () => <DroppedSnapshot />,
        body: "Choose Small, Medium or Big for the card size, drag a card to move it, and "
          + "drag its corner to resize. Save when it looks right." },
      { title: "Press Start", shot: () => <BarSnapshot kind="startbar" />,
        body: "Last, press Start at the top right to begin detection, and Stop to end it. It "
          + "works even if no video feed is on the page: the cards fill in as soon as it runs." },
    ],
  },
  analytics: {
    title: "Analytics guide",
    welcome: ["Welcome to Analytics", "The history of what your system saw, over the dates you pick, on pages you lay out yourself."],
    steps: [
      { title: "Before you use this page", shot: () => (
          <Checks title="Needed before this page has anything to show" rows={NEEDED} />),
        body: "Analytics shows what your workflow measures, so finish these first: AI "
          + "Models, Camera Setup, Database (a table for each chart) and the pipeline "
          + "on the Workflow." },
      { title: "Open Analytics", shot: () => <MenuSnapshot hot="Analytics" />,
        body: "Go to the sidebar, find Overview, and open Analytics. It is for history: "
          + "totals, charts and tables over the dates you pick." },
      { title: "Your analytics page", shot: () => <PageSnapshot />,
        body: "Everything your Workflow publishes to Analytics shows here as cards, "
          + "charts and tables. Pick the dates at the top to change the period." },
      { title: "Press Edit layout", shot: () => <BarSnapshot kind="analyticsbar" />,
        body: "At the top right, press Edit layout to choose what the page shows and where." },
      { title: "Add cards and charts", shot: () => <BuilderSnapshot chart />,
        body: "The same Dashboard cards list appears on the left, with your charts under "
          + "Line chart, Bar chart and so on. Drag one onto the page, or click it to add "
          + "it, the same way as on the Dashboard. A chart needs a Data Table on the "
          + "Workflow to read from." },
      { title: "Add another page", shot: () => <PageSnapshot />,
        body: "Press + New Page to start a second page with its own cards, for example "
          + "one page per product line. Each page has its own tab at the top." },
    ],
  },
  models: {
    title: "AI Models guide",
    welcome: ["Welcome to AI Models", "Choose the AI model that decides what your system can detect."],
    steps: [
      { title: "Choose a model", shot: () => <ModelCard hot="card" />,
        body: "Each model is a card. Pick one that suits your board (the small tags show "
          + "where it runs, for example hailo8) and press its button to use it. The card "
          + "then shows Active and \"In use \u00b7 click to stop\". Nothing can be detected "
          + "until a model is in use." },
      { title: "Open its settings", shot: () => <ModelCard hot="chevron" />,
        body: "Press the arrow at the bottom of the card to open the model's Detection "
          + "settings." },
      { title: "Set the minimum confidence", shot: () => <ModelSettings hot="confidence" />,
        body: "Minimum confidence is how sure the model must be before it reports "
          + "something. Raising it ignores weak guesses. It is checked on the accelerator, "
          + "so raising it costs nothing." },
      { title: "Choose what to detect", shot: () => <ModelSettings hot="classes" />,
        body: "Under Classes to keep, tick what you want to detect, for example person. "
          + "Search the list if it is long. Anything left unticked is ignored everywhere "
          + "in the Workflow. Clear classes unticks them all." },
      { title: "Save settings", shot: () => <ModelSettings hot="save" />,
        body: "Press Save settings. It is only available once you have changed something. "
          + "Next is Camera Setup: the model is only useful once a camera is pointed at "
          + "something." },
    ],
  },
  camera: {
    title: "Camera Setup guide",
    welcome: ["Welcome to Camera Setup", "Connect your camera and draw the zones your workflow watches."],
    steps: [
      { title: "Add a camera first", shot: () => <CamList empty hot="add" />,
        body: "A new system has no camera yet, so there are no camera settings to change. "
          + "The Cameras panel lists what this system watches, and how many of the box's "
          + "camera slots are used. Start by pressing + Add camera. The next steps are the "
          + "settings you choose while adding it." },
      { title: "Choose the kind of camera", shot: () => <CamDialog hot="kind" />,
        body: "Pick the device for the new camera: USB, RTSP / IP, GigE, or a Video / "
          + "Image file." },
      { title: "Select the camera", shot: () => <CamDialog hot="device" />,
        body: "For USB, open the list and choose your camera. If none is listed, plug it "
          + "in and press the rescan button next to the list." },
      { title: "Set the quality", shot: () => <CamDialog hot="quality" />,
        body: "High Resolution gives more image detail but more processing load. Left "
          + "off, the camera runs at 640x480, the AI's working size, which is enough for "
          + "most detection. Check Camera Capabilities lists the resolutions and frame "
          + "rates this camera supports." },
      { title: "Choose how boxes are drawn", shot: () => <CamDialog hot="boxes" />,
        body: "Draw boxes in the browser is lighter on the CPU but can lag in fast "
          + "movement. Embed boxes in the video is more accurate and stays visible in "
          + "other video players. Snapshots and clips keep their boxes either way." },
      { title: "Add the camera", shot: () => <CamDialog hot="add" />,
        body: "Press Preview Snapshot to check the picture first, then Add camera." },
      { title: "Turn it on", shot: () => <CamList hot="toggle" />,
        body: "Switch the camera on with the toggle on its row. Its status changes from "
          + "Idle once the picture is working. The sliders button opens its settings again." },
      { title: "Add another camera", shot: () => <CamList two hot="add" />,
        body: "Press + Add camera again for a second camera. The panel counts the slots "
          + "used (2 out of 4 here), and each camera has its own toggle, settings button "
          + "and remove button. Zones and the Workflow can then be set up for each camera." },
      { title: "Press Start to see the picture", shot: () => <StartFeed />,
        body: "The camera picture stays black until detection runs. Press Start at the top "
          + "right of the picture. The picture appears, and then you can draw zones on it." },
      { title: "Draw your zones", shot: () => <ZoneTools />,
        body: "With the picture showing, choose Box, Circle or Polygon above it for an area, or "
          + "Line to count crossings, then draw it on the picture. A new system says "
          + "\"No zones yet. Draw one on the feed.\"" },
      { title: "Your zones", shot: () => <ZoneList />,
        body: "Each zone you keep is listed with its camera and its kind (area or line). "
          + "Counters and alerts on the Workflow use these zones." },
      { title: "The top bar", shot: () => <CamTopBar />,
        body: "Live View shows the camera picture (Stopped until detection runs). Camera "
          + "Setting reopens the camera's settings, Snapshots and Clips open the pictures "
          + "and clips taken so far. Save all keeps your changes, Discard drops them." },
      { title: "Choose what shows on the picture", shot: () => <StatusBox />,
        body: "Press the three-line button beside the zone tools to open the Status box. "
          + "Pick what to show on the camera picture: the Model, the Camera, the Hailo "
          + "FPS (how fast the AI chip works) and the Camera FPS." },
    ],
  },
  dashboardBuilder: {
    title: "Dashboard Builder guide",
    welcome: ["Welcome to the Dashboard Builder",
              "Lay out the page your team sees: drag in the cards you made on the Workflow."],
    steps: [
      { title: "Find your card", shot: () => <BuilderSnapshot />,
        body: "The cards you drew on the Workflow are listed on the left under Dashboard "
          + "cards, grouped by kind. Your People Counter is under Number tile." },
      { title: "Drag it onto the page", shot: () => <DroppedSnapshot />,
        body: "Drag a card onto the page, or click it to add it at the end. Choose Small, "
          + "Medium or Big for the card size, drag a card to move it, and drag its corner "
          + "to resize it." },
      { title: "Save the layout", shot: () => <SaveBar />,
        body: "The badge says unsaved while you have changes. Press Save to keep the layout, "
          + "then open Dashboard in the sidebar to see it live." },
    ],
  },
  analyticsBuilder: {
    title: "Analytics Builder guide",
    welcome: ["Welcome to the Analytics Builder",
              "Choose the cards and charts each Analytics page shows, and where."],
    steps: [
      { title: "Find your chart", shot: () => <BuilderSnapshot chart />,
        body: "Your charts are listed on the left under Dashboard cards, by kind: Line chart, "
          + "Bar chart and so on. A chart reads from a Data Table on the Workflow." },
      { title: "Drag it onto the page", shot: () => <DroppedSnapshot />,
        body: "Drag a card or chart onto the page, or click it to add it. Drag it to move it "
          + "and drag its corner to resize it." },
      { title: "Another page", shot: () => <PageSnapshot />,
        body: "Each Analytics page has its own tab, and each tab is laid out separately: "
          + "switch tab to edit that page. Add a new page with + New Page on Analytics." },
      { title: "Save the layout", shot: () => <SaveBar />,
        body: "Press Save to keep the layout, then open Analytics in the sidebar to see it." },
    ],
  },
  dataset: {
    title: "Dataset guide",
    welcome: ["Welcome to Dataset", "Capture training photos and clips from your camera to build a better model."],
    steps: [
      { title: "What it is for", shot: () => (
          <Panel title="Dataset"><span className="muted small">NVMe ready</span></Panel>),
        body: "Dataset captures training photos and short clips from the camera, "
          + "with no boxes or text drawn on them, so you can train a better model later. "
          + "It needs the NVMe drive to be ready." },
      { title: "Name the capture", shot: () => (
          <Panel title="Capture">
            <span className="input ng-new" style={{ display: "block" }}>e.g. forklift_bay3</span>
          </Panel>),
        body: "Type a name for what you are capturing. Every photo and clip is saved under it. "
          + "With several cameras, pick the camera first." },
      { title: "Take photos and clips", shot: () => (
          <Panel title="Capture">
            <span style={{ display: "flex", gap: 8 }}>
              <span className="btn btn-sm btn-accent ng-new">Take photo</span>
              <span className="btn btn-sm">Record clip</span>
            </span>
          </Panel>),
        body: "Press Take photo for a still, or Record clip for a short clip of up to 10 "
          + "seconds. A dataset is many short samples, not long footage." },
      { title: "Find them in the library", shot: () => (
          <Panel title="Where it goes">
            <span className="muted small">Photos &rarr; dataset_image</span>
            <span className="muted small">Clips &rarr; dataset_rec</span>
            <span className="btn btn-sm ng-new" style={{ width: "fit-content" }}>Dataset Library</span>
          </Panel>),
        body: "Open the Dataset Library page to look through, download or delete what you captured." },
    ],
  },
};

/** The guide's steps with its welcome page in front. */
function stepsOf(g) {
  const [heading, lead] = g.welcome;
  return [{ title: "Welcome", body: lead,
            shot: () => <Welcome heading={heading} lead={lead} learn={g.steps.map((s) => s.title)} /> },
          ...g.steps];
}

export function PageGuide({ guide, open, onClose }) {
  const g0 = GUIDES[guide];
  const g = g0 && { ...g0, steps: stepsOf(g0) };
  const [at, setAt] = useState(0);
  const [mini, setMini] = useState(false);
  // Closing ends the guide: the next time it opens it starts from the first page.
  const close = () => { setAt(0); setMini(false); onClose?.(); };
  useEffect(() => {
    if (!open || mini) return undefined;
    const key = (e) => {
      if (e.key === "Escape") { setAt(0); onClose?.(); }
      if (e.key === "ArrowRight") setAt((n) => Math.min(g.steps.length - 1, n + 1));
      if (e.key === "ArrowLeft") setAt((n) => Math.max(0, n - 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, onClose, guide, g, mini]);
  if (!open || !g) return null;
  const step = g.steps[at];
  const last = at === g.steps.length - 1;
  return createPortal(
    <div className={mini ? "ng-dock" : "ui-dialog-backdrop"} onMouseDown={mini ? undefined : close}
         data-page-guide={guide}>
      <div className={"ui-dialog ng-popup" + (mini ? " ng-minicard" : "")} role="dialog"
           aria-modal={!mini} aria-label={g.title}
           style={mini ? undefined : { width: "min(760px, 96vw)" }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="ui-dialog-head">
          <h3>{g.title}</h3>
          <button type="button" className="btn btn-sm ng-minbtn" onClick={() => setMini(!mini)} aria-label={mini ? "Expand" : "Minimize"} title={mini ? "Expand the guide" : "Minimize - keep the guide in the corner"} data-guide-minimize>{mini ? "Expand" : "Minimize"}</button>
          <button className="toast-close" onClick={close} aria-label="Close">×</button>
        </div>
        <div className="ui-dialog-body">
          <div className="ng-slider-head">
            <span className="ng-tag">Step {at + 1} of {g.steps.length}</span>
            <strong>{step.title}</strong>
          </div>
          <div key={at} className="ng-pg-pic">{step.shot()}</div>
          <p className="ng-slide-body" data-ng-slide-body>{step.body}</p>
          <div className="ng-slider-nav">
            <button type="button" className="btn btn-sm" disabled={at === 0}
                    onClick={() => setAt(at - 1)} data-ng-prev>Back</button>
            <div className="ng-dots">
              {g.steps.map((s, i) => (
                <button key={s.title} type="button" aria-label={`Step ${i + 1}: ${s.title}`}
                        title={s.title} className={"ng-dot" + (i === at ? " on" : "")}
                        onClick={() => setAt(i)} />
              ))}
            </div>
            {last
              ? <button type="button" className="btn btn-sm btn-accent" onClick={close}
                        data-ng-done>Done</button>
              : <button type="button" className="btn btn-sm btn-accent"
                        onClick={() => setAt(at + 1)} data-ng-next>Next</button>}
          </div>
        </div>
        {!mini && <div className="ui-dialog-foot ng-popup-foot"><GuideSwitchRow /></div>}
      </div>
    </div>,
    document.body);
}

/** The page's guide: it opens with the page the first time guides are on, and whenever the Guide
 *  button in the top bar asks for it. */
export default function PageGuideHost({ guide }) {
  const [open, setOpen] = useState(false);
  // Opens with the page the first time, unless the first-time setup guide is
  // still going: that one has a "Show me how" button for this same guide.
  useEffect(() => {
    if (!guidesEnabled()) return undefined;
    let live = true;
    setupTourActive().then((active) => { if (live && !active) setOpen(true); });
    return () => { live = false; };
  }, [guide]);
  // The first-time setup guide's "Show me how" opens it with ?guide=open, also
  // when the page is already showing.
  const { search } = useLocation();
  useEffect(() => {
    if (new URLSearchParams(search).get("guide") === "open") setOpen(true);
  }, [search]);
  // The Guide button in the top bar asks for this page's guide.
  useEffect(() => {
    const ask = (e) => { if (e.detail?.key === guide) setOpen(true); };
    window.addEventListener(GUIDE_REQUEST_EVENT, ask);
    return () => window.removeEventListener(GUIDE_REQUEST_EVENT, ask);
  }, [guide]);
  return <PageGuide guide={guide} open={open} onClose={() => setOpen(false)} />;
}
