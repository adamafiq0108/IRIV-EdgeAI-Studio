// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Workflow - the visual pipeline editor. Wire the detection pipeline as nodes:
//
//   Detection Stream → Class Filter → Zone → Analytics → Dashboard / Output
//
// The graph is an authoring format: on save the SERVER compiles it into the
// artifacts the platform already runs ([[functions]], [[outputs]], Builder
// widgets) - see core/services/workflow.py. This page owns editing UX only:
// palette drag-drop, wires with validation, zoom/pan/minimap, undo/redo,
// copy/paste, context menu, search, auto-save with compile badges.
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useUnsavedGuard } from "../hooks/use-unsaved-guard.jsx";
import { useDarkClass } from "../hooks/use-theme.js";
import { Drawer, PALETTE_ORDER, PaletteItem, SubHead, nodeMatches } from "../workflow/PaletteParts.jsx";
import { GUIDE_REQUEST_EVENT, guidesEnabled } from "../workflow/guideState.js";
// Loaded only when it opens, so the canvas does not wait on it.
import GuideDock from "../workflow/GuideDock.jsx";
import { InfoHint } from "../components/ui/InfoHint.jsx";
import BusSettingsPanel from "../components/BusSettingsPanel.jsx";
import EmailListField from "../components/EmailListField.jsx";
import ModbusServerEditor, { ModbusServerRow, REQUIRED_EMPTY, useModbusServers }
  from "../components/ModbusServerEditor.jsx";
import MqttConnectionEditor, { MqttConnectionRow }
  from "../components/MqttConnectionEditor.jsx";
import { Link, useLocation } from "react-router-dom";
import {
  ReactFlow, ReactFlowProvider, Background, MiniMap, Controls, Panel, SelectionMode,
  applyNodeChanges, applyEdgeChanges, addEdge, useReactFlow, useStoreApi,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, saveBlob } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { ErrorNote, SkelWorkflow } from "../components/Loading.jsx";
import { Badge, Dialog } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { useAsk, useConfirm } from "../components/ui/ask.jsx";
import { exprNames } from "../workflow/ExprEditor.jsx";
import { TypedInput } from "../workflow/TypedInput.jsx";
import { CALL as FN_CALL, END as FN_END, FRAME as FN_FRAME, GROUP, START as FN_START, assignDropped, expandFrameDrag, fitGroups, groupMinSize, seedGroups, expandGroupDrag, fnKey, frameMembership, frameMinSize, frameOf, framePins, hiddenByFrames, legacyFrames, pinPositions, syncFrames, wireStaysInside } from "../workflow/functionFrame.js";
import { edgeTypes } from "../workflow/ProblemEdge.jsx";
import FunctionPicker from "../workflow/FunctionPicker.jsx";
import { CATEGORY_STYLE, COUNTER, COUNTER_LINE_PORT, COUNTER_MODE_LABEL, FUNCTION_NODES, LINE_COUNTER, PORT_LABELS, STAGES, branchCtype, camLabel, classFilterNaming, classesFor, counterMode, counterSwaps, counterToLine, edgeAllowed, isBranch, isCounter, isStatePoint, lineToCounter, metricsForSpec, metricsOf, mintId, mintFnId, migrateStateEdges, recordedMode, statePointFn, statePointKeys } from "../workflow/registry.js";

/** A palette drawer, preceded by its stage heading when it starts one. */
function StageGroup({ heading, children }) {
  return <>{heading}{children}</>;
}
import SearchList from "../workflow/SearchList.jsx";
// in / does / out at the top of every node's Properties (nodeSummary.js).
import { summaryFor } from "../workflow/nodeSummary.js";
import { closestPlaceholder, csvNames, csvTokens, unknownPlaceholders }
  from "../workflow/msgPlaceholders.js";
import { ArrowRight } from "lucide-react";
import {
  WorkflowCtx, buildNodeTypes, scheduleSentence, WEEKDAYS, slug,
  triggerAdvanced,
  WHEN_MODES, WHEN_NEEDS_VALUE, WHEN_NEEDS_VALUE2, GRAPH_SERIES_HANDLES,
} from "../workflow/nodes.jsx";
// One list of treatments for the whole product: the Builder inspector and this
// canvas offer exactly the same choices, because they dress the same widget.
import { BandEditor, SeriesBands } from "../components/widgets/builderShared.jsx";
// A card's properties -- labels, order, option lists -- are ONE definition
// shared with the Builders' widget panel, so a chart reads the same here and
// on Analytics (see cardFields.jsx for why).
import {
  BucketField, CARD_FIELDS, CARD_SHAPES, ChartColourHint, EventTypeField,
  IntervalField, PerField, PeriodField, ShapeField, TimeRangeField,
  StepField, RangeField, cleanStep, X_FIELD_REASON,
  UNIT_KINDS, UnitField, WorkOutField, showsColours, StatusFields,
  CardDateRange, cardRangeOf,
} from "../components/widgets/cardFields.jsx";
// The chart builder (X / Y / interval / date range, one per chart), drawn by
// the same component the Builders draw; chartModel reads and writes it.
import { ChartAxisFields, InputRowControls } from "../components/widgets/chartFields.jsx";
import { readNodeModel, nodePatch, modelNodeFields } from "../components/widgets/chartModel.js";
// Choose the chart type first, then only that type's settings (2026-10-14):
// the same picker, strip and in-family "Show as" the Builders draw.
import { ChartTypePicker, ChartTypeStrip, ChartShowAs } from "../components/widgets/ChartTypePicker.jsx";
import {
  chartTypeOf, planTypeSwitch, isFreshChart, clearFreshChart, markFreshChart,
} from "../components/widgets/chartTypes.js";
// Workflow Guideline is hidden until its content/videos are ready; to re-enable, uncomment this import, the <Guideline/> render, the tab button and restore helpOpen/rightW below.
// import Guideline from "../workflow/Guideline.jsx";
import ContextMenu from "../workflow/ContextMenu.jsx";
// A finger has no middle/right button and no rubber band: on a touch screen one
// finger pans the canvas (nodes still drag by their body) and pinch zooms.
const COARSE_POINTER = typeof window !== "undefined"
  && !!window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
import useGraphHistory from "../workflow/useGraphHistory.js";
import ExprEditor from "../workflow/ExprEditor.jsx";
import VariablesPanel from "../workflow/VariablesPanel.jsx";
import NewVariableForm from "../workflow/NewVariableForm.jsx";
import DebugPanel from "../workflow/DebugPanel.jsx";
import UndoRedo from "../components/ui/UndoRedo.jsx";
import { ChevronsLeft } from "lucide-react";
import { Maximize2, Minimize2, Eye, EyeOff } from "lucide-react";
import useCanvasFullscreen from "../workflow/useCanvasFullscreen.js";
import TriggerFields from "../workflow/TriggerInspector.jsx";
import BlueprintEditor from "../workflow/BlueprintEditor.jsx";
import TutorialNav from "../components/TutorialNav.jsx";
import BrokerStatus from "../components/BrokerStatus.jsx";
import AiDot, { aiOf } from "../components/AiDot.jsx";
import { DebugHistory, ListenTest, PublishTest } from "../workflow/MqttTest.jsx";
import { PasswordInput } from "../components/ui/input.jsx";

// "common" replaced "note": the group now holds jump wires and a probe as
// well as comments, and it sits FIRST, under its own Common heading above
// Input, because that is where a Node-RED user looks for them.
//: Sub-headings inside the Functions drawer, in the order they are read:
//: what is measured at a PLACE, then on the objects in frame, then computed
//: from other numbers. Mirrors the `group` each node declares in the server
//: registry (core/services/workflow.py).
const SUBGROUP_ORDER = [
  // Filters: which CLASSES, then which PLACE (the Zone Filter and the zones),
  // then what the PICTURE itself looks like before either is asked.
  "Class", "Zone", "Picture",
  // AI Processing: measured at a place, then on whatever is in frame.
  "Zones", "Objects",
  // Board I/O, read the way a terminal block is: what arrives on a terminal,
  // then what the box drives out of one.
  "Inputs", "Outputs",
  // Math: arithmetic first, the two logic nodes after it, storing a value,
  // and the free-text formula last -- it is the escape hatch for what the
  // one-operation nodes above cannot say.
  "Math", "Logic", "State", "Formula",
  // Control: nodes that pick a WAY OUT, the brackets that make a piece of
  // canvas reusable, then nodes that change WHEN.
  "Branch", "Function",
  "Cards", "Charts",
  // Actions: what the box does and what it writes to disk.
  "Footage",
  // Integration: the systems the box talks to, the protocol families first
  // (each keeps its reading and its sending half together, the way an
  // operator arriving from Node-RED looks for them), then messaging.
  "MQTT", "HTTP", "API", "Modbus",
  "Email", "Telegram", "Alerts",
];

// The palette reads in the order a pipeline is BUILT, with the canvas tools
// on top.
//
// Common first (comments, link jumps, the debug probe): stationery, not a
// step of the pipeline, under its own heading above Input (Tok Aba
// 2026-10-05). Then the main run: the Detection Stream every graph starts
// from, narrowed to classes and places, measured, then shown and acted on.
// Between them the supporting cast -- Conditions gate a value that already
// exists, Variables park one anywhere along the run.
// Grouped by COLOUR, and the colours run in the order a graph is built: slate
// for the canvas tools, blue where data comes in, green where it is
// measured, orange for the things that support the graph without being a
// stage in it, then the three places a pipeline actually ends -- kept
// (purple), shown (amber), acted on (red).
// The two OUTPUT nodes that stand for the rule behind them rather than for
// themselves: a Condition defines its trigger, an Alert decorates it.
// Mirrors _TRANSPARENT_OUTPUTS in core/services/workflow.py.
const TRANSPARENT_OUT = ["output.alert", "output.event"];

//: The nodes that must be NAMED when they are added (see nameNode): their
//: name is their identity somewhere else -- an alert, a Dashboard title, a
//: Footage page, the dashboard's Class filter card.
const NAME_FIRST = {
  "output.trigger": { field: "name", label: "Trigger Name", placeholder: "e.g. Overcrowded, Door left open",
    title: "Name this Trigger",
    description: "The name identifies this trigger in the workflow and is what its alerts say." },
  "integration.watch": { field: "name", label: "Recorded Trigger Name", placeholder: "e.g. Violations today",
    title: "Name this Recorded Trigger",
    description: "The name is used in the message it sends and in the log." },
  "filter.class": { field: "name", label: "Class Filter Name", placeholder: "e.g. people, forklifts",
    title: "Name this Class Filter",
    description: "The dashboard's Class filter card lists it by this name, so it can be paused there." },
  "dashboard.card": { field: "name", label: "Card Widget Name", placeholder: "e.g. People in Zone 1",
    title: "Name this Card Widget",
    description: "The title shown on the card on your Dashboard and Analytics pages." },
  "dashboard.graph": { field: "name", label: "Chart Widget Name", placeholder: "e.g. Violations per day",
    title: "Name this Chart Widget",
    description: "The title shown on the chart on your Dashboard and Analytics pages." },
  "output.snapshot": { field: "page_name", label: "Image Snapshot Name", placeholder: "e.g. Handrail violations",
    title: "Name this Image Snapshot",
    description: "The photos this node takes get their own page in Footage under this name, "
      + "so you can tell them from another Image Snapshot's." },
  "output.record": { field: "page_name", label: "Recording Clip Name", placeholder: "e.g. Crane approach",
    title: "Name this Recording Clip",
    description: "The clips this node records get their own page in Footage under this name, "
      + "so you can tell them from another Recording Clip's." },
};

// Nodes that WIRE as outputs but are not fired by a rule. A Command is fired
// by a person typing it and a Watch by its own timer, so the whole output
// panel -- "wire an Analytics node in first", "fires on a decision" -- is
// wrong for them, and told an operator to draw a wire the compiler refuses.
// They keep the output category because that is what the pipeline rules
// reason about; only the help they are shown differs.
const SELF_FIRED_OUT = ["integration.command", "integration.watch",
                        "integration.photos"];

//: What a violation box is drawn in when an Alert names no colour. Matches
//: the app's own fallback (app/camera.py annotate) -- shown as the swatch's
//: starting value so the picker opens on the colour actually in use.
const DEFAULT_BOX_COLOR = "#FF0000";

// Renaming a function renames the whole function.
//
// The Start, the End and every Call are tied together by NAME and nothing
// else -- there is no id linking them, deliberately, so that copying a pair
// and retyping the name makes a second function. The cost of that is that
// renaming one half silently orphans the other: the End still answers to the
// old name, so the bracket no longer closes, the body stops existing and
// every Call goes looking for a function that is no longer there. Nothing
// errors; the canvas just quietly compiles to less than it says.
//
// So a rename on the Start carries the End and the Calls with it. Only from
// the START: it is the one that DECLARES the function, and a rename typed on
// a Call means "call a different function", which is a normal thing to want.
function renameFunction(nodes, before, fields) {
  if (!before || !["function.start", "function.frame"].includes(before.type)) return nodes;
  if (fields.fn === undefined) return nodes;
  const from = String(before.data?.fn || "").trim().toLowerCase();
  const to = String(fields.fn || "").trim();
  if (!from || from === to.toLowerCase()) return nodes;
  // A name somebody else already declares is not ours to take: renaming onto
  // it would merge two functions into one without saying so. The Start keeps
  // the new name (the operator typed it, and a duplicate name reads as the
  // clash it is), but nothing else moves.
  const taken = nodes.some((n) => n.type === before.type
    && n.id !== before.id
    && String(n.data?.fn || "").trim().toLowerCase() === to.toLowerCase());
  if (taken) return nodes;
  return nodes.map((n) => {
    if (n.id === before.id) return n;
    if (!["function.start", "function.end", "function.call", "function.frame"]
      .includes(n.type)) return n;
    if (String(n.data?.fn || "").trim().toLowerCase() !== from) return n;
    return { ...n, data: { ...n.data, fn: to } };
  });
}

// The name a freshly dropped function gets. Numbered rather than blank: a
// pair that arrives already sharing a name is a pair that already works, and
// renaming it afterwards is one field, not two that have to agree.
function nextFunctionName(nodes) {
  const taken = new Set((nodes || [])
    .filter((n) => ["function.start", "function.end", "function.frame"].includes(n.type))
    .map((n) => String(n.data?.fn || "").trim().toLowerCase()));
  for (let i = 1; i < 500; i += 1) {
    if (!taken.has(`fn${i}`)) return `fn${i}`;
  }
  return `fn${Date.now().toString(36)}`;
}

// The interchange format for Export / Import. Deliberately the same shape the
// canvas already saves with (id/type/position/data + plain edges) so a file is
// readable, hand-editable and forward-compatible with the server's graph.
// Tidy the canvas: layered left-to-right in pipeline order (source → filter/
// zone → function → condition → card/output), wires kept short by following
// each node's predecessors' rows. Comments keep their positions - they were
// placed next to what they explain.
function arrangeGraph(nodes, edges, registry) {
  const CAT_COL = { source: 0, filter: 1, zone: 1, analytics: 2, variable: 2,
                    logic: 3, dashboard: 4, output: 4 };
  const flow = nodes.filter((n) => (registry[n.type] || {}).category !== "common");
  const ids = new Set(flow.map((n) => n.id));
  // A Trigger's duplicate guard reads back the table it writes: that wire
  // loops by design, so it does not place anything.
  const es = edges.filter((e) => ids.has(e.source) && ids.has(e.target)
    && !["cur_oid", "cur_value"].includes(e.targetHandle));
  const col = {};
  flow.forEach((n) => {
    col[n.id] = CAT_COL[(registry[n.type] || {}).category] ?? 2;
  });
  // Longest-path relaxation: a target always sits right of its source. The
  // editor forbids cycles, so this terminates.
  for (let pass = 0; pass < flow.length; pass++) {
    let moved = false;
    for (const e of es) {
      if (col[e.target] < col[e.source] + 1) {
        col[e.target] = col[e.source] + 1;
        moved = true;
      }
    }
    if (!moved) break;
  }
  const byCol = new Map();
  flow.forEach((n) => {
    if (!byCol.has(col[n.id])) byCol.set(col[n.id], []);
    byCol.get(col[n.id]).push(n);
  });
  const rowOf = {};
  [...byCol.keys()].sort((a, b) => a - b).forEach((c) => {
    const list = byCol.get(c);
    const key = (n) => {
      const rows = es.filter((e) => e.target === n.id)
        .map((e) => rowOf[e.source]).filter((v) => v !== undefined);
      // No placed predecessor: keep the operator's rough vertical order.
      return rows.length ? rows.reduce((s, v) => s + v, 0) / rows.length
                         : (n.position?.y ?? 0) / 100000;
    };
    list.sort((a, b) => key(a) - key(b));
    list.forEach((n, i) => { rowOf[n.id] = i; });
  });
  const X0 = 60, Y0 = 60, COL_W = 300, ROW_H = 150;
  return nodes.map((n) => (ids.has(n.id)
    ? { ...n, position: { x: X0 + col[n.id] * COL_W, y: Y0 + rowOf[n.id] * ROW_H } }
    : n));
}

const FILE_APP = "irivai-workflow";
const FILE_VERSION = 1;
const MAX_IMPORT_NODES = 200;
const MAX_IMPORT_EDGES = 400;

/** The selected nodes (or ALL of them when nothing is selected) + inner edges. */
function serializeSelection(nodes, edges) {
  const sel = (nodes || []).filter((n) => n.selected);
  const use = sel.length ? sel : (nodes || []);
  const ids = new Set(use.map((n) => n.id));
  return {
    nodes: use.map((n) => ({ id: n.id, type: n.type, position: n.position,
                             data: { ...(n.data || {}) } })),
    edges: (edges || [])
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ id: e.id, source: e.source, target: e.target,
                     sourceHandle: e.sourceHandle ?? null,
                     targetHandle: e.targetHandle ?? null })),
  };
}

function todayStamp() {
  const d = new Date();
  const p = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

// The compiler skips a watching node that no Detection Stream reaches, and says
// so in that node's compile warning (core/services/workflow.py compile_graph).
// The wording is the only signal we get back, so match the phrase it emits and
// nothing else -- guessing which nodes "look unconnected" from the graph would
// be a second source of truth free to disagree with what actually compiled.
const SWITCHED_OFF_RE = /switched off/i;

/** Nodes the compiler is not running, as id -> "was it warned itself?": the
 *  ones it warned about, plus the branch below them, which it skips silently
 *  along with them.
 *
 *  Downstream is derived from the edges, but only where it cannot contradict
 *  the compiler: a node counts as off when EVERY wire into it comes from an off
 *  node. One live input means the compiler kept it, so it stays lit. */
// Mirrors core/services/workflow.py _slug: the name a wired input answers to
// in a formula has to be spelled the same on both sides of the wire.
function slugify(text) {
  // Character-for-character, not run-collapsing: the compiler replaces EACH
  // non-alphanumeric with an underscore, and a name that slugged differently
  // here would be offered as an input the formula then could not resolve.
  return String(text || "").trim().toLowerCase()
    .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}

// What an expression may call the node wired into it: a Function answers to
// its fnId, a Variable to its own name. Mirrors formula_inputs() on the
// server, which resolves a wired input the same two ways -- and is the reason
// a name typed into a Formula finds the wire at all.
/** fn_test_value -> test: what a person calls a variable. The id form is
 *  never shown to the user. */
function plainName(key) {
  const m = /^fn_(.+)_value$/.exec(String(key || ""));
  return m ? m[1] : String(key || "");
}

function inputAlias(node, registry) {
  const nd = (node || {}).data || {};
  const spec = registry[(node || {}).type] || {};
  if (!["analytics", "variable"].includes(spec.category)) return null;
  // A Variable node stands for a value that already exists: it answers to its
  // own label and resolves to that key, not to one of its own.
  if (node.type === "variable.ref") {
    return nd.key
      ? { name: slugify(nd.name) || plainName(nd.key),
          label: nd.name || plainName(nd.key), key: nd.key }
      : null;
  }
  if (!nd.fnId) return null;
  const metric = (spec.metrics || ["value"])[0];
  return { name: nd.fnId, label: nd.name || nd.fnId,
           key: `fn_${nd.fnId}_${metric}` };
}

//: The functions that FIRE a message rather than give it a value: the
//: compiler drops them from a message's {placeholders} (CLOCK_FN_METRIC).
const CLOCK_FN_TYPES = ["schedule", "inject", "state_watch"];

//: Sentinel in the Variable node's value list. Not a stat key -- no key can
//: contain a space, so it can never collide with a real one.
const NEW_VARIABLE = "+ new variable";

/**
 * A variable picker WITH a search box - used everywhere a node passes or
 * reads a variable. A system quickly publishes dozens of keys, and a bare
 * dropdown of them all is scroll-and-squint; typing three letters is how a
 * person actually finds "total_violation".
 */
function KeyPicker({ value, keys, labelFor, editable, onPick, extra }) {
  // The search lives INSIDE the list (SearchList): a box above a closed
  // dropdown reads as a filter on the page, and three of them stacked on one
  // inspector is a wall of identical boxes.
  const label = (k) => String(labelFor ? labelFor(k) : k);
  const hintOf = (k) => (/^fn_/.test(String(k)) ? "" : k);
  const items = keys.map((k) => ({ key: k, label: label(k), hint: hintOf(k) }));
  // The saved choice stays listed even if it is no longer offered - a node's
  // current setting must never render blank.
  if (value && !keys.includes(value)) {
    items.unshift({ key: value, label: label(value), hint: hintOf(value) });
  }
  // Actions ("+ New variable…") sit in their own group at the end, so they
  // are never mistaken for a value and never buried mid-list.
  const groups = extra?.length ? ["Values", "New"] : null;
  if (groups) {
    items.forEach((it) => { it.group = "Values"; });
    extra.forEach((e) => items.push({ ...e, group: "New" }));
  }
  return (
    <SearchList value={value || ""} items={items} groups={groups}
                editable={editable} placeholder="- choose a value -"
                searchLabel="Search variables…" onPick={onPick} />
  );
}

//: Nodes that hide the publishing chrome -- the key field, the "publishes …"
//: line and the widget toggles. A Formula is a STEP: you wire its answer
//: onward to a card, an alert or another formula, so a key to hand-bind and
//: two palette switches are three decisions nobody asked to make. It still
//: publishes (the id is minted for it); it just does not make that your
//: problem. A Variable holds a value and publishes nothing at all.
const HIDES_KEY_CHROME = new Set(["variable.ref", "analytics.formula"]);

/**
 * A stat key as a person would say it: "Zone 1 violations · count (Bay cam)"
 * rather than cam_lrcp_u3_imx307__lrcp_u3_imx307_fn_z_zone_msf_a1_count.
 *
 * The raw keys are machine-correct and unreadable - camera-scoped ones carry
 * the camera id twice before the rule even starts - so a dropdown of them is
 * a dropdown nobody can choose from. The key is still what gets stored and
 * shown as the subtitle; only the LABEL changes.
 */
function statLabel(key, functions, cameras, zones, extra) {
  // A value the canvas offers before anything publishes it (an ON/OFF
  // point not wired yet, registry.js statePointKeys) has its own words.
  if (extra && extra[key] && !(functions || []).some(
    (f) => String(key).startsWith(`fn_${f.id}_`))) return extra[key];
  let rest = String(key || "");
  let camera = "";
  const scoped = rest.match(/^cam_(.+?)__(.+)$/);
  if (scoped) {
    camera = camLabel(cameras, scoped[1]);
    rest = scoped[2];
    // The id appears TWICE in a camera-scoped key (cam_<id>__<id>_<stat>),
    // which is most of why these read as noise. Both copies go.
    if (rest.startsWith(`${scoped[1]}_`)) rest = rest.slice(scoped[1].length + 1);
  }
  let label = rest;
  if (rest.startsWith("fn_")) {
    const body = rest.slice(3);
    // Longest id wins: function ids contain underscores, so a short id can
    // prefix a longer one and would otherwise claim its keys.
    let best = null;
    for (const f of functions || []) {
      if (body === f.id || body.startsWith(`${f.id}_`)) {
        if (!best || f.id.length > best.id.length) best = f;
      }
    }
    if (best) {
      const metric = body.slice(best.id.length).replace(/^_/, "");
      // An ON/OFF point's value IS the state, so it reads as the sentence
      // its name already is ("Detection Stream is ON"), with what 1 means.
      label = best.type === "state_watch" && metric === "value"
        ? `${best.name} (1 = yes, 0 = no)`
        : metric ? `${best.name} · ${metric}` : best.name;
    }
  } else {
    const zoneKey = rest.match(/^(?:zone|line)_(.+?)_([a-z_]+)$/);
    const zone = zoneKey && (zones || []).find((z) => z.id === zoneKey[1]);
    if (zone) label = `${zone.name} · ${zoneKey[2]}`;
    else if (rest === "objects_in_frame") label = "Objects in frame";
    else if (rest === "violations_now") label = "Violations now";
    else if (rest === "depth_nearest_mm") label = "Nearest object (mm)";
    else if (rest === "depth_farthest_mm") label = "Farthest object (mm)";
    else if (rest.startsWith("measure_")) {
      // measure_<profile>_<field>: the depth camera's numbers for the
      // nearest object of that profile's class.
      const m = rest.match(/^measure_(.+)_(count|length_mm|width_mm|height_mm|distance_mm|angle_deg|area_mm2|diameter_mm|radius_mm|perimeter_mm)$/);
      if (m) label = `Measure ${m[1]} · ${m[2].replace(/_/g, " ")}`;
    }
  }
  // A key nothing here names (a variable declared but not fed yet): the
  // person's own name for it, never the id.
  if (label.startsWith("fn_")) label = plainName(label);
  return camera ? `${label}  -  ${camera}` : label;
}

/**
 * The Switch node's branches. Order is the logic: a value leaves by the FIRST
 * branch it passes, so moving a row changes the meaning and the list is
 * deliberately ordered rather than a set of independent rules.
 */
//: The shapes a Switch's condition can take. `apply` coerces the node's data
//: into what that shape needs, so picking a type from the dropdown always
//: leaves a valid node -- and the list is the ONLY thing to extend for a new
//: shape: the panel, the node's ports (nodes.jsx) and the compiler all read
//: the same ctype + rules storage.
const SWITCH_TYPES = [
  { key: "compare", label: "If / Else if (conditions)",
    hint: "Ordered conditions, each reading its own wired value. The FIRST one that holds sends the signal out its own port - wire what should happen to that port. Adding a condition adds its input and its output.",
    apply: (d) => ({ ctype: "compare",
                     rules: ((d.rules || []).length ? d.rules : [{ op: ">", value: 0 }])
                       .slice(0, 8)
                       .map((r, i) => ({
                         input: r.input || `in${Math.min(8, i + 1)}`,
                         ...(r.op === "between"
                           ? { op: ">", value: r.value ?? 0 }
                           : { op: r.op || ">", value: r.value ?? 0,
                               ...(r.rhs_input ? { rhs_input: r.rhs_input } : {}) }),
                       })),
                     otherwise: d.otherwise !== false }) },
  { key: "switch", label: "Switch (route by value)",
    hint: "One value in, one case per branch: the value leaves by the first case it matches.",
    apply: (d) => ({ ctype: "switch",
                     rules: ((d.rules || []).length ? d.rules : [{ op: "==", value: 1 }])
                       .map((r) => (r.op === "between"
                         ? { op: "==", value: r.value ?? 0 }
                         : { op: r.op || "==", value: r.value ?? 0 })),
                     otherwise: d.otherwise !== false }) },
  { key: "range", label: "Range (bands)",
    hint: "From x up to (not including) y per branch, so adjacent bands never overlap.",
    apply: (d) => ({ ctype: "range",
                     rules: (d.rules || []).filter((r) => r.op === "between").length
                       ? (d.rules || []).filter((r) => r.op === "between")
                       : [{ op: "between", value: 0, value2: 10 }],
                     otherwise: d.otherwise !== false }) },
  { key: "boolean", label: "Boolean (on / off)",
    hint: "Anything non-zero goes out the ON port, zero goes out OFF.",
    apply: () => ({ ctype: "boolean",
                    rules: [{ op: "!=", value: 0 }], otherwise: true }) },
  { key: "multi", label: "Multi-condition (AND / OR)",
    hint: "Several wired values, one combined test. Each condition names the input port it tests; adding one adds the port.",
    apply: (d) => {
      const rows = (d.rules || []).slice(0, 8).map((r, i) => ({
        input: r.input || `in${Math.min(8, i + 1)}`,
        ...(r.op === "between"
          ? { op: ">", value: r.value ?? 0 }
          : { op: r.op || ">", value: r.value ?? 0 }),
      }));
      // Two conditions is what "combine several" means; one is a Compare.
      while (rows.length < 2) {
        rows.push({ input: `in${rows.length + 1}`, op: ">", value: 0 });
      }
      return { ctype: "multi", join: d.join || "and", rules: rows,
               otherwise: d.otherwise !== false };
    } },
];

//: How many input PORTS a shape reads. Cases and bands route one value; the
//: two condition shapes read one per row.
export const SWITCH_MULTI_INPUT = ["compare", "multi"];

function SwitchFields({ d, editable, metrics = [], edges, nodeId, nodeType,
                        onChange }) {
  // The shape is the NODE for the branch family, and a stored setting only
  // for the deprecated Switch it replaced -- so the picker below is drawn for
  // that one alone. Choosing between five behaviours inside a node whose face
  // then changed under you was the thing splitting them fixed.
  const ctype = branchCtype(nodeType, d);
  const rules = d.rules || [];
  const patch = (i, fields) => onChange({
    rules: rules.map((r, j) => (j === i ? { ...r, ...fields } : r)),
  });
  const patchSub = (i, k, fields) => patch(i, {
    more: (rules[i].more || []).map((m, j) => (j === k ? { ...m, ...fields } : m)),
  });
  // Ports a row may read: ONLY the ones with something wired into them --
  // two linked nodes means two inputs to choose from, full stop. A saved
  // row still pointing at a port whose wire is gone shows that port inside
  // its own dropdown, flagged, so it can be read and re-pointed -- but a
  // dead port never haunts the other rows' menus.
  const ports = [...new Set((edges || [])
    .filter((e) => e.target === nodeId && /^in\d+$/.test(e.targetHandle || ""))
    .map((e) => e.targetHandle))]
    .sort((a, b) => Number(a.slice(2)) - Number(b.slice(2)));
  if (!ports.length) ports.push("in1");
  const opPick = (value, onPick) => (
    <select className="input" style={{ width: 70 }} value={value}
            disabled={!editable} onChange={(e) => onPick(e.target.value)}>
      {[">", ">=", "<", "<=", "==", "!="].map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
  // The stored handle ids stay in1/in2 (edges and the compiler key on
  // them); the OPERATOR reads "input1" -- "in1" was one letter from a
  // preposition and read like one.
  const portLabel = (h) => String(h).replace(/^in(\d+)$/, "input$1");
  const portPick = (value, onPick, title) => (
    <select className="input" style={{ width: 96 }} title={title}
            value={value} disabled={!editable}
            onChange={(e) => onPick(e.target.value)}>
      {!ports.includes(value) && (
        <option value={value}>{portLabel(value)} (not wired)</option>
      )}
      {ports.map((h) => <option key={h} value={h}>{portLabel(h)}</option>)}
    </select>
  );
  // Boolean is the one fixed shape: on and off, nothing to add or remove.
  const fixedRows = ctype === "boolean";
  const perRowInput = SWITCH_MULTI_INPUT.includes(ctype);
  const rowWord = { compare: "condition", switch: "case", range: "range",
                    multi: "condition" }[ctype] || "branch";

  function addRow() {
    const last = rules[rules.length - 1] || {};
    const next = ctype === "range"
      ? { op: "between", value: last.value2 ?? 0, value2: (last.value2 ?? 0) + 10 }
      : ctype === "switch"
        ? { op: "==", value: (Number(last.value) || 0) + 1 }
        : { input: ports[Math.min(ports.length - 1, rules.length)] || ports[0],
          op: ">", value: 0 };
    onChange({ rules: [...rules, next] });
  }

  return (
    <>
      {nodeType === "logic.switch" && (
        <Field label="Switch type"
               hint={SWITCH_TYPES.find((t) => t.key === ctype)?.hint}>
          <select className="input" value={ctype} disabled={!editable}
                  onChange={(e) => {
                    const t = SWITCH_TYPES.find((x) => x.key === e.target.value);
                    if (t) onChange(t.apply(d));
                  }}>
            {SWITCH_TYPES.map((t) => (
              <option key={t.key} value={t.key}>{t.label}</option>
            ))}
          </select>
        </Field>
      )}

      {ctype === "multi" && (
        <Field label="Passes when"
               hint="ALL = every condition must hold (AND). ANY = one is enough (OR).">
          <select className="input" value={d.join || "and"} disabled={!editable}
                  onChange={(e) => onChange({ join: e.target.value })}>
            <option value="and">ALL conditions hold (AND)</option>
            <option value="or">ANY condition holds (OR)</option>
          </select>
        </Field>
      )}

      {/* Which number to route on -- only for the shapes that route ONE
          value. A condition row names its own input instead. */}
      {!!metrics.length && !perRowInput && (
        <Field label="Which value"
               hint="Which of the wired node's numbers to route on.">
          <select className="input" value={d.metric || metrics[0]}
                  disabled={!editable}
                  onChange={(e) => onChange({ metric: e.target.value })}>
            {metrics.map((m) => <option key={m} value={m}>{metricLabel(m)}</option>)}
          </select>
        </Field>
      )}

      {ctype !== "boolean" && (
        <Field label={{ compare: "Conditions", multi: "Conditions",
                        range: "Ranges", switch: "Cases" }[ctype] || "Branches"}
               hint={{
                 compare: "IF the first holds, else IF the second... Each row reads its own input port and leaves by its own output.",
                 multi: "Each row tests one input port; ALL / ANY above combines them into one answer.",
                 range: "From x up to (not including) y. Each range is its own output; the first that fits wins.",
                 switch: "One case per row, matched against the wired value. Checked top to bottom.",
               }[ctype]}>
        <div style={{ display: "grid", gap: 6 }}>
          {rules.map((r, i) => (
            <div key={i} className="wf-listrow">
              {/* WHAT this row reads. A condition names its port; a case or a
                  band is the next test on the one wired value, so it carries
                  its order number instead. */}
              {perRowInput ? (
                <>
                  <span className="wf-listrow-n">
                    {ctype === "compare" ? (i === 0 ? "if" : "else if") : i + 1}
                  </span>
                  {portPick(r.input || "in1", (input) => patch(i, { input }),
                            "Which wired value this condition reads")}
                </>
              ) : (
                <span className="wf-listrow-n">{i + 1}</span>
              )}
              {ctype === "range" ? (
                <>
                  <input className="input" type="number" step="any"
                         style={{ width: 80 }} title="from (inclusive)"
                         value={r.value ?? 0} disabled={!editable}
                         onChange={(e) => patch(i, { value: +e.target.value })} />
                  <span className="muted small">up to</span>
                  <input className="input" type="number" step="any"
                         style={{ width: 80 }} title="to (exclusive)"
                         value={r.value2 ?? 0} disabled={!editable}
                         onChange={(e) => patch(i, { value2: +e.target.value })} />
                </>
              ) : ctype === "switch" ? (
                <>
                  {/* A case matches EXACTLY. The comparison is still stored,
                      so a branch saved before cases existed keeps its own
                      operator and shows it rather than being rewritten. */}
                  {(r.op || "==") === "=="
                    ? <span className="muted small">is</span>
                    : opPick(r.op, (op) => patch(i, { op }))}
                  <input className="input" type="number" step="any" style={{ width: 90 }}
                         value={r.value ?? 0} disabled={!editable}
                         onChange={(e) => patch(i, { value: +e.target.value })} />
                </>
              ) : (
                <>
                  {opPick(r.op || ">", (op) => patch(i, { op }))}
                  {/* Against a NUMBER, or against another wired value --
                      "in1 > in2" is a real question a number cannot ask. */}
                  {ctype === "compare" && r.rhs_input
                    ? portPick(r.rhs_input, (rhs_input) => patch(i, { rhs_input }),
                               "Compare against this wired value")
                    : (
                      <input className="input" type="number" step="any" style={{ width: 90 }}
                             value={r.value ?? 0} disabled={!editable}
                             onChange={(e) => patch(i, { value: +e.target.value })} />
                    )}
                  {ctype === "compare" && (
                    <button type="button" className="btn btn-sm" disabled={!editable}
                            title={r.rhs_input ? "Compare against a number instead"
                                               : "Compare against another wired value"}
                            onClick={() => patch(i, r.rhs_input
                              ? { rhs_input: undefined, value: r.value ?? 0 }
                              : { rhs_input: ports[Math.min(ports.length - 1, i + 1)] })}>
                      {r.rhs_input ? "123" : "in"}
                    </button>
                  )}
                </>
              )}
              {ctype === "compare" && (
                <button type="button" className="btn btn-sm" disabled={!editable}
                        title="AND / OR another test onto this condition - if input1 > 0 and input2 == 0"
                        onClick={() => patch(i, { more: [...(r.more || []),
                          { link: "and",
                            input: ports[Math.min(ports.length - 1,
                                                  i + (r.more || []).length + 1)],
                            op: "==", value: 0 }] })}>+&amp;&amp;</button>
              )}
              {!fixedRows && (
                <button type="button" className="btn wf-x"
                        disabled={!editable || rules.length < 2}
                        title={rules.length < 2 ? "It needs at least one " + rowWord
                                                : "Remove this " + rowWord}
                        onClick={() => onChange({
                          rules: rules.filter((_, j) => j !== i) })}>×</button>
              )}
            </div>
          )).flatMap((row, i) => [row,
            /* The extra tests a condition carries, indented under it. Each
               names its own input and its own AND/OR link; the row above and
               these together are ONE condition with ONE output port. */
            ...(ctype === "compare" ? (rules[i].more || []) : []).map((m, k) => (
              <div key={`${i}-sub-${k}`} className="wf-listrow"
                   style={{ marginLeft: 18 }}>
                <select className="input" style={{ width: 64 }}
                        value={m.link === "or" ? "or" : "and"} disabled={!editable}
                        onChange={(e) => patchSub(i, k, { link: e.target.value })}>
                  <option value="and">and</option>
                  <option value="or">or</option>
                </select>
                {portPick(m.input || "in1",
                          (input) => patchSub(i, k, { input }),
                          "Which wired value this test reads")}
                {opPick(m.op || "==", (op) => patchSub(i, k, { op }))}
                {m.rhs_input
                  ? portPick(m.rhs_input,
                             (rhs_input) => patchSub(i, k, { rhs_input }),
                             "Compare against this wired value")
                  : (
                    <input className="input" type="number" step="any"
                           style={{ width: 90 }}
                           value={m.value ?? 0} disabled={!editable}
                           onChange={(e) => patchSub(i, k, { value: +e.target.value })} />
                  )}
                <button type="button" className="btn btn-sm" disabled={!editable}
                        title={m.rhs_input ? "Compare against a number instead"
                                           : "Compare against another wired value"}
                        onClick={() => patchSub(i, k, m.rhs_input
                          ? { rhs_input: undefined, value: m.value ?? 0 }
                          : { rhs_input: ports[ports.length - 1] })}>
                  {m.rhs_input ? "123" : "in"}
                </button>
                <button type="button" className="btn wf-x" disabled={!editable}
                        title="Remove this test"
                        onClick={() => patch(i, {
                          more: (rules[i].more || []).filter((_, j) => j !== k),
                        })}>×</button>
              </div>
            ))])}
          {!fixedRows && (
            <button type="button" className="btn wf-add"
                    disabled={!editable || rules.length >= 8}
                    onClick={addRow}>
              {/* The port comes from the WIRE now: drop another value on
                  the node and its input appears with a condition ready. */}
              {"+ Add " + rowWord}
            </button>
          )}
        </div>
        </Field>
      )}

      {/* What a branch SENDS. The commonest misreading of this node is that
          a port carries the value that matched, and then nothing downstream
          makes sense: a port is a SIGNAL saying "this is the branch that
          holds right now", and whatever is wired to it acts while that is
          true. Said here, next to the rows, because that is where the
          question gets asked. */}
      <p className="muted small" style={{ marginTop: 4 }}>
        Each {rowWord} has its own output port on the node. While that{" "}
        {rowWord} is the one that holds, everything wired to its port fires -
        a Condition, a Snapshot, an MQTT message, a card. The ports carry a
        signal, not the number: put the action on the branch it belongs to.
      </p>

      {ctype !== "boolean" && (
        // A checkbox row is not a field: it has no label above it to give it
        // air, so it brings its own -- flush against the rows above and the
        // next field below, it read as a line of one of them.
        <label className="wf-check">
          <input type="checkbox" checked={d.otherwise !== false} disabled={!editable}
                 onChange={(e) => onChange({ otherwise: e.target.checked })} />
          <span className="small">
            {{ multi: "Add an output for when the test fails",
               compare: "Add an ELSE output for when none of them holds",
               range: "Add a Default output for values outside every range",
             }[ctype] || "Add a Default output for values matching no case"}
          </span>
        </label>
      )}
    </>
  );
}

/**
 * The controls for the Formula and for the Math family that replaced it: pick
 * the operation (where there is one to pick), then say where each operand
 * comes from. An operand is either WIRED (a Variable or another rule feeding
 * that handle) or a plain number typed here - so the common "count + 1" needs
 * no wiring and no expression syntax at all.
 *
 * ONE form, because a Math node IS a Formula with its operation fixed by the
 * drawer it came out of. Two forms would be two implementations of one
 * semantics, and they would drift the way the compile paths would have.
 */
function FormulaFields({ spec, d, editable, edges, nodes, nodeId, onChange }) {
  const ops = spec.ops || {};
  const isMath = !!spec.math;
  const opKeys = Object.keys(ops);
  // A Math node's operation is clamped to its own list - a stale `op` left by
  // a retype or a hand-edited graph must not make an Average subtract. Mirrors
  // math_op() in core/services/workflow.py, so the form and the compiler
  // always agree on what is running.
  const op = ops[d.op] ? d.op : (isMath ? (opKeys[0] || d.op) : "add");
  // Whether the node CHAINS is the node's own property for a Math node
  // (Clamp never grows an eighth input however chainable `clamp` looks from
  // the operations table) and the operation's for the old Formula.
  const chain = isMath
    ? !!spec.chain && (!spec.chain_ops || spec.chain_ops.includes(op))
    : !!ops[op]?.chain;
  const maxInputs = spec.max_inputs || 8;
  // A one-operation Math node carries no `ops` map at all - nothing to pick -
  // so its shape comes from the handles the registry minted for it.
  const arity = chain
    ? Math.max(2, Math.min(maxInputs, parseInt(d.inputs, 10) || 2))
    : (ops[op]?.arity || (isMath ? (spec.handles || []).length : 0) || 2);
  const wireOnly = !!(ops[op]?.wire_only || spec.wire_only);
  // WHAT KIND of value this node stores, where it stores one (Set Variable).
  // The list comes from the server's registry entry, so the form can never
  // offer a kind the compiler would not accept; clamped the same way the
  // operation is, and the first entry is the node's original behaviour.
  const valueTypes = spec.value_types || [];
  const handles = ["a", "b", "c", "d", "e", "f", "g", "h"].slice(0, arity);
  // Which handles already have something wired into them: those operands come
  // from the wire, and a number box beside them would be a lie.
  const wiredHandles = new Set((edges || [])
    .filter((e) => e.target === nodeId)
    .map((e) => e.targetHandle || "a"));
  // AUTO, unless a kind was chosen: the kind is read off the value the way
  // the compiler reads it (setvar_kind) - a wire carries a number, "12" is
  // whole, "4.5" decimal, anything else typed is text. Nobody has to open a
  // select before typing "Enter"; the box takes anything and says what it saw.
  // The Variable wired into input a may declare its type; that wins, the way
  // the compiler's setvar_kind reads it.
  const aEdge = (edges || []).find((e) => e.target === nodeId && (e.targetHandle || "a") === "a");
  const aNode = aEdge ? (nodes || []).find((n) => n.id === aEdge.source) : null;
  const varKind = aNode?.type === "variable.ref"
    && ["text", "decimal", "number"].includes(aNode?.data?.value_type)
    ? aNode.data.value_type : "";
  const detectKind = () => {
    if (varKind) return varKind;
    if (wiredHandles.has("b")) return "decimal";
    const s = String(d.b_value ?? "").trim();
    if (s === "") return "decimal";
    if (/^[+-]?\d+$/.test(s)) return "number";
    return Number.isFinite(Number(s)) ? "decimal" : "text";
  };
  const autoKind = valueTypes.length > 1 && !valueTypes.includes(d.value_type);
  const valueType = autoKind ? detectKind()
    : (valueTypes.includes(d.value_type) ? d.value_type : (valueTypes[0] || ""));
  const kindLabel = (v) => (v === "number" ? "whole number" : v === "text" ? "text" : "decimal");
  const gated = wiredHandles.has("when");
  // A TABLE COLUMN wired into an input: which column, so the input can say
  // how its recorded rows become one number (Show) and over what (Period).
  const tableInputOf = (h) => {
    const e = (edges || []).find((x) => x.target === nodeId && (x.targetHandle || "a") === h);
    const src = e && (nodes || []).find((n) => n.id === e.source);
    if (!src || src.type !== "output.dataset") return null;
    const sh = String(e.sourceHandle || "");
    return { column: sh.startsWith("o_") ? sh.slice(2) : "", table: src.data?.name || src.data?.dataset || "table" };
  };
  // The Variable wired into a set-to's input a, by name -- what "publish as
  // the variable" means on this node.
  const setVarName = (() => {
    if (!wireOnly) return "";
    const e = (edges || []).find((x) => x.target === nodeId
      && (x.targetHandle || "a") === "a");
    const src = e && (nodes || []).find((n) => n.id === e.source);
    if (!src) return "";
    if (src.type === "variable.ref") {
      const m = String(src.data?.key || "").match(/^fn_(.+)_value$/);
      return m ? m[1] : "";
    }
    if (src.type === "variable.number") return src.data?.fnId || "";
    return "";
  })();
  // Grouped options for a plain <select> over every operation -- the first
  // interleaved Operation IS the node's operation, so it has to offer all of
  // them, not only the chainable ones.
  const groups = spec.op_groups
    || [...new Set(Object.values(ops).map((o) => o.group))];
  const allOpOptions = groups.map((g) => (
    <optgroup key={g} label={g}>
      {Object.entries(ops)
        .filter(([key, o]) => o.group === g && (!o.hidden || key === d.op))
        .map(([key, o]) => <option key={key} value={key}>{o.label}</option>)}
    </optgroup>
  )).filter((g) => g.props.children.length > 0);

  return (
    <>
      {/* THE RUN PORT, explained where the checkbox that used to mint it was.
          It was opt-in ("only when a condition holds", ticking the box put a
          "when true" port on the node) and is simply always there now, called
          In: arithmetic is logic that belongs somewhere in a pipeline, and a
          node whose only inputs are operands has nowhere to say where. Same
          handle underneath, so every gate already drawn keeps its wire. */}
      <p className="muted small" style={{ margin: "8px 0 10px" }}>
        <strong>In</strong> - where this {isMath ? "calculation" : "formula"}
        {" "}sits in the flow. Wire a Detection Stream, or any node it follows,
        and the wire is sequence alone. Wire a condition instead (an
        If / Else if branch, a Delay, any 0/1 value) and it runs only while
        that holds, reading 0 otherwise.
      </p>
      {gated && (
        <p className="muted small" style={{ marginTop: 0 }}>
          Something is wired to In - if it carries a value, this runs only
          while that value is true.
        </p>
      )}
      {/* The chainable layout reads top to bottom the way the chain computes
          -- a, op, b, op, c -- and the FIRST of those op pickers is the
          node's operation, so a second "Operation" field above it was the
          same decision twice. Fixed-arity operations (iff, sqrt, distance…)
          keep the searchable picker: their inputs are positional, not a
          chain, and there is no interleaved slot to fold the choice into. */}
      {/* A Math node picks only among ITS OWN operations, and only where it
          has more than one: an Average has nothing to choose, and a field with
          a single option is furniture. The chain's interleaved pickers below
          are the Formula's alone - one Math node is one operation, and mixing
          add with multiply inside an "Arithmetic" node would put two jobs
          behind a face that names one. Chain two nodes for that. */}
      {isMath && opKeys.length > 1 && (
        <Field label="Operation" hint={ops[op]?.hint || "What this node does."}>
          {opKeys.length > 6 ? (
            <SearchList
              value={op} editable={editable} searchLabel="Search operations…"
              items={opKeys.map((key) => ({
                key, label: ops[key].label, hint: ops[key].hint,
              }))}
              onPick={(key) => onChange({ op: key })} />
          ) : (
            <select className="input" value={op} disabled={!editable}
                    onChange={(e) => onChange({ op: e.target.value })}>
              {opKeys.map((key) => (
                <option key={key} value={key}>{ops[key].label}</option>
              ))}
            </select>
          )}
        </Field>
      )}
      {!isMath && !chain && (
        <Field label="Operation"
               hint={ops[op]?.hint
                     || "Pick what this node computes. Search by name or by "
                        + "what you want - 'percent', 'distance', 'average'."}>
          <SearchList
            value={op} editable={editable} groups={spec.op_groups}
            searchLabel="Search operations…"
            items={Object.entries(ops)
              /* Comparisons retired from the picker: a comparison is a
                 condition, and conditions live on the Switch. A node already
                 using one keeps it listed so nothing silently changes. */
              .filter(([key, o]) => !o.hidden || key === op)
              .map(([key, o]) => ({
                key, label: o.label, hint: o.hint, group: o.group,
              }))}
            onPick={(key) => onChange({ op: key })} />
        </Field>
      )}
      {/* Sits ABOVE the value box it describes: the kind decides what that
          box even is (a whole number, a decimal, a line of text), so reading
          it after the box would be reading the answer before the question. */}
      {valueTypes.length > 1 && (
        <Field label="Value type"
               hint={(autoKind ? "Read off the value: " : "")
                 + (valueType === "text"
                   ? "a word. The variable then holds it, and anything wired "
                     + "from it reads the word: a Data Table column records it, "
                     + "a card shows it, a Compare can test it against a text constant."
                   : valueType === "number"
                     ? "a whole number - a wired value is rounded too, not "
                       + "only what you type."
                     : "a number with decimals, exactly as it comes.")}>
          <select className="input" value={autoKind ? "auto" : valueType} disabled={!editable}
                  onChange={(e) => {
                    const next = e.target.value;
                    if (next === "auto") {
                      onChange({ value_type: "auto" });
                      return;
                    }
                    // Re-typed to what the new box edits, so "night shift"
                    // never sits in a number field and a leftover 0 never
                    // reads as the word "0".
                    const raw = d.b_value;
                    onChange({
                      value_type: next,
                      b_value: next === "text" ? String(raw ?? "")
                        : next === "number" ? Math.round(Number(raw) || 0)
                        : (Number(raw) || 0),
                    });
                  }}>
            <option value="auto">{`auto (now: ${kindLabel(detectKind())}${varKind ? ", from the Variable" : ""})`}</option>
            {valueTypes.map((v) => (
              <option key={v} value={v}>{kindLabel(v)}</option>
            ))}
          </select>
        </Field>
      )}
      {handles.map((h, i) => (
        <div key={h}>
          {/* Named by the PORT where the node has real port names: "Base" and
              "Exponent" say which way round a power goes, where "input a" is
              a lookup against the node's face. */}
          <Field label={wireOnly
                   ? (h === "a" ? "Variable to set" : "Set it to")
                   : (spec.port_labels || [])[i] || `Input ${h}`}
                 hint={wireOnly
                   ? (h === "a"
                     ? "Wire the Variable this sets - its output then wires back into that Variable to store."
                     : "The value it becomes - type a number or wire a value in.")
                   : wiredHandles.has(h)
                     ? "Comes from what is wired into this input."
                     : "Nothing wired in - type a number, or wire a value onto this input."}>
            {wiredHandles.has(h) && tableInputOf(h) ? (
              <input className="input" disabled readOnly
                     value={`- column ${tableInputOf(h).column || "?"} of ${tableInputOf(h).table} -`} />
            ) : wiredHandles.has(h) ? (
              <input className="input" value="- wired in -" disabled readOnly />
            ) : wireOnly && h === "a" ? (
              /* input a IS the variable being set: it comes from a wire by
                 definition, so there is no number to type here. */
              <input className="input" value="- wire the Variable in -"
                     disabled readOnly />
            ) : (autoKind || valueType === "text") && h !== "a" ? (
              /* Auto takes anything - a number or a word - and the kind
                 above says what it read. */
              <input className="input" maxLength={120}
                     placeholder={autoKind ? "12, 4.5 or night shift" : "night shift"}
                     value={d[`${h}_value`] ?? ""} disabled={!editable}
                     onChange={(e) => onChange({ [`${h}_value`]: e.target.value })} />
            ) : (
              /* A whole-number variable steps by one: a spinner that offers
                 12.4 for a headcount invites a value the compiler then
                 rounds away behind the operator's back. */
              <input className="input" type="number"
                     step={valueType === "number" && h !== "a" ? "1" : "any"}
                     value={d[`${h}_value`] ?? 0} disabled={!editable}
                     onChange={(e) => onChange({ [`${h}_value`]: e.target.value })} />
            )}
          </Field>
          {/* A table column feeding this input: the same two questions a
              card answers about a column, asked here for the input. The
              recorded rows become one number, read from the database every
              few seconds, and the calculation runs on that number. */}
          {tableInputOf(h) && (
            <>
              <Field label={`Show (${tableInputOf(h).column || "column"})`}
                     hint={"How this column's recorded rows become the one number this "
                           + "input reads. Refreshed from the database every few seconds."}>
                <select className="input" value={d[`${h}_agg`] || ""} disabled={!editable}
                        onChange={(e) => onChange({ [`${h}_agg`]: e.target.value })}>
                  <option value="">the latest value (rows counted for a text column)</option>
                  <option value="latest">the latest value</option>
                  <option value="sum">total value</option>
                  <option value="avg">average value</option>
                  <option value="min">smallest value</option>
                  <option value="max">largest value</option>
                  <option value="count">total rows</option>
                  <option value="unique">total different values</option>
                  <option value="changes">value changes</option>
                </select>
              </Field>
              <Field label="Period"
                     hint={"Which rows to include. A calculation has no page to follow, so "
                           + "the Analytics range is not offered here - pick a fixed period."}>
                <select className="input" value={d[`${h}_range`] || ""} disabled={!editable}
                        onChange={(e) => onChange({ [`${h}_range`]: e.target.value })}>
                  <option value="">all time</option>
                  <option value="today">today</option>
                  <option value="week">this week</option>
                  <option value="month">this month</option>
                </select>
              </Field>
            </>
          )}
          {/* The operation sits BETWEEN the inputs it joins, reading top to
              bottom the way the chain computes: a, op, b, op, c. The first
              one IS the node's operation and offers everything (picking a
              fixed-arity one switches the layout); the later ones join and
              so offer only the operations that can. */}
          {!isMath && chain && i < arity - 1 && (
            <Field label="Operation"
                   hint={i === 0
                     ? (ops[op]?.hint || "What this node computes.")
                     : "Joins everything above with the next input - the "
                       + "chain computes top to bottom: (a op b) op c."}>
              <select className="input" disabled={!editable}
                      value={i === 0 ? op : ((d.ops || [])[i - 1] || op)}
                      onChange={(e) => {
                        if (i === 0) { onChange({ op: e.target.value }); return; }
                        const next = [...(d.ops || [])];
                        while (next.length < i) next.push(op);
                        next[i - 1] = e.target.value;
                        onChange({ ops: next });
                      }}>
                {i === 0 ? allOpOptions
                  : Object.entries(ops).filter(([, o]) => o.chain)
                      .map(([key, o]) => (
                        <option key={key} value={key}>{o.label}</option>
                      ))}
              </select>
            </Field>
          )}
        </div>
      ))}
      {/* What the OUTPUT is. Setting a variable and publishing a second
          number named after this node are different acts; the wire into
          input a says which variable, this says which act. */}
      {/* A Set Variable SETS the variable wired into input a - there is no
          other place for its result to go, so there is no choice to offer.
          (It used to default to "a new value named after this node", and the
          variable it was wired into then read 0 for ever.) Two Set Variables
          on one variable fold into one rule: the later wins when both hold. */}
      {wireOnly && (
        <Field label="Sets"
               hint={setVarName
                 ? `Everything bound to ${setVarName} follows this rule. Several Set Variables may write the same variable - gate each with a condition on its run port, and the later one wins when both hold.`
                 : "Wire the Variable to set into input a."}>
          <div className="input" style={{ opacity: setVarName ? 1 : 0.6 }}>
            {setVarName ? `the variable ${setVarName}` : "no Variable wired into input a yet"}
          </div>
        </Field>
      )}
      {/* Inputs grow the way the Switch's conditions do: a button, not a
          count dropdown -- "how many inputs" is never the question, "one
          more" is. Removing takes the LAST input, because the chain folds
          left and pulling one out of the middle would silently re-pair every
          join below it. */}
      {chain && (
        <div className="filter-bar" style={{ gap: 8, marginBottom: 14 }}>
          {arity < maxInputs && (
            <button type="button" className="btn wf-add" disabled={!editable}
                    onClick={() => onChange({ inputs: arity + 1 })}>
              + Add input (adds a wiring point)
            </button>
          )}
          {arity > 2 && (
            <button type="button" className="btn" disabled={!editable}
                    title="Removes the last input and its joining operation"
                    onClick={() => onChange({
                      inputs: arity - 1,
                      ops: (d.ops || []).slice(0, Math.max(0, arity - 3)),
                    })}>
              − Remove last input
            </button>
          )}
        </div>
      )}
    </>
  );
}

function offNodeIds(warnings, edges, nodes) {
  const off = new Map(Object.entries(warnings)
    .filter(([, m]) => SWITCHED_OFF_RE.test(m || ""))
    .map(([id]) => [id, true]));
  // A node switched off in its own data is off whatever the compiler said -
  // it never reached the compiler. This is how a zone's chain reads once the
  // zone is unticked: cut from the stream, drawn, and visibly dim.
  for (const n of nodes || []) {
    if ((n.data || {}).disabled && !off.has(n.id)) off.set(n.id, false);
  }
  if (!off.size) return off;
  const sources = new Map();                   // target id -> [source ids]
  for (const e of edges) {
    if (!sources.has(e.target)) sources.set(e.target, []);
    sources.get(e.target).push(e.source);
  }
  // Fixpoint rather than one pass: a chain (off -> When -> Alert) only settles
  // once the middle node is known off. Bounded by the node count either way.
  for (let pass = 0; pass < sources.size + 1; pass += 1) {
    let grew = false;
    for (const [target, srcs] of sources) {
      if (off.has(target)) continue;
      if (srcs.every((s) => off.has(s))) { off.set(target, false); grew = true; }
    }
    if (!grew) break;
  }
  return off;
}

// One system, one canvas. There used to be a management table over up to 50
// named workflows; it made an operator choose where a rule should live before
// writing it, then remember - for nothing, because every workflow compiled into
// the same detection app regardless. The page now opens straight into the
// editor, and older multi-workflow files are merged onto this canvas on read
// (core/services/workflow.py load_store).
export default function Workflow() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}

// ---------------------------------------------------------------------------
// editor
// ---------------------------------------------------------------------------

function Editor() {
  const toast = useToast();
  const rf = useReactFlow();
  const [meta, setMeta] = useState(null);        // {registry, variables, editable}
  const [error, setError] = useState(null);
  const [values, setValues] = useState({});
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  // Which node's settings are OPEN. Set by a double click, by creating a node
  // (you are already editing it, so the panel is the point), and cleared with
  // the selection.
  const [inspectId, setInspectId] = useState(null);
  // Reactive theme follow -- see the comment where colorMode is set.
  const isDarkCanvas = useDarkClass();
  // The hovered wire, driven from onEdgeMouseEnter/Leave rather than CSS
  // :hover. React Flow's own dark-mode stylesheet (colorMode="dark", set
  // below) sets its own stroke on the same path, and CSS specificity between
  // a library stylesheet and this file's rules changes with colorMode -- the
  // highlight worked in light mode and silently lost the fight in dark. An
  // inline style set by THIS component, last, on the actual edge object
  // cannot lose a cascade fight: React Flow only fills in what an edge does
  // not already specify.
  const [hoveredEdgeId, setHoveredEdgeId] = useState(null);
  // The hovered NODE, for the same reason and by the same mechanism: hovering
  // a card lights every wire touching it and dims the rest, which is the
  // shortest answer to "what is this connected to" on a canvas where a dozen
  // wires cross each other on their way past.
  const [hoveredNodeId, setHoveredNodeId] = useState(null);
  const [saveState, setSaveState] = useState("saved");   // saved | saving | error
  // Auto-save is a preference, not a law: a graph mid-rewiring compiles to
  // nonsense, and pushing that at the running app every 1.5s is exactly what
  // an operator wants to stop. Remembered per browser; ON keeps the old feel.
  const [autoSave, setAutoSave] = useState(
    () => localStorage.getItem("wf.autosave") !== "off");
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState({});      // node id -> message
  const [warnings, setWarnings] = useState({});
  const [search, setSearch] = useState("");
  const [menu, setMenu] = useState(null);        // {x, y, variant, nodeId|null, flowPos}
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  // Palette width is a preference like auto-save: a long zone list wants room,
  // a small screen wants none of it. A width the operator has already dragged
  // is never overridden - only the FIRST-RUN default reads the viewport, so a
  // 1366/1440 laptop does not start out with two fat rails around a slot.
  const [paletteW, setPaletteW] = useState(() => {
    const v = parseInt(localStorage.getItem("wf.palette.w") || "", 10);
    if (Number.isFinite(v)) return v <= 0 ? 0 : Math.max(200, Math.min(480, v));
    return window.innerWidth < 1500 ? 200 : 215;
  });
  // The inspector too: a long recorded-events form wants room.
  const [inspectorW, setInspectorW] = useState(() => {
    const v = parseInt(localStorage.getItem("wf.inspector.w") || "", 10);
    if (Number.isFinite(v)) return v <= 0 ? 0 : Math.max(240, Math.min(560, v));
    return window.innerWidth < 1500 ? 250 : 270;
  });
  // The "Pipeline" primer explains the canvas - once. Left in the right-hand
  // column it rents a whole rail of laptop width for prose, permanently,
  // whenever no node is selected - which is most of the time you spend
  // arranging a graph. It now lives in a collapsible rail: closed it is a 34px
  // tab, open it is the same panel it always was, and selecting a node still
  // opens the Inspector at full width with no extra click.
  //
  // null = never answered, and the answer is now OPEN, whatever is on the
  // canvas. It used to open only on an empty graph, on the reasoning that a
  // graph with nodes in it is not a first run -- but the person opening it is
  // often not the person who built it, and a guide nobody has been shown is a
  // guide nobody knows exists. It costs one click to hide, and hiding it is
  // remembered for good.
  // Same localStorage idiom as `wf.autosave` and `models.card.depth`.
  const [helpPref, setHelpPref] = useState(() => {
    const v = localStorage.getItem("wf.help.open");
    return v === null ? null : v === "1";
  });
  const [varsOpen, setVarsOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  // The toolbar's overflow menu. Everything that is not Debug or Tidy up
  // lives behind it: eight buttons in a row crowded the canvas, and those two
  // are the only ones reached often enough to earn a permanent place.
  const [toolsOpen, setToolsOpen] = useState(false);
  const [bpId, setBpId] = useState(null);        // Function Block whose blueprint is open
  // Declared variables: {name, var}. A declaration is a NAME, not a node --
  // it holds nothing until something stores into it, and it appears on the
  // canvas only when a Variable node reads it or an output is stored in it.
  const [customVars, setCustomVars] = useState([]);
  const [functions, setFunctions] = useState([]); // what the graph currently compiles to
  const [appRunning, setAppRunning] = useState(false);
  // The "AI running" dot ({ok, reason}): toolbar and every Detection Stream.
  const [aiStatus, setAiStatus] = useState(null);
  // The Class Filters paused on the dashboard (node ids): which of each one's
  // ON / OFF points is lit. The Detection Stream's two follow aiStatus.on.
  const [filtersPaused, setFiltersPaused] = useState(() => new Set());
  // The nodes after an ON / OFF point: { node id: "AI detection is off" },
  // the state they run in (state_points.py). Labelled on the node.
  const [runsWhen, setRunsWhen] = useState({});
  // The node ids the compiler says wait for detection (the app with its AI
  // off holds them). A Debug probe on one says "AI off" rather than "nothing
  // published yet"; nothing else on the canvas changes with the AI off, on
  // purpose (see NodeShell).
  const [waitsForDetection, setWaitsForDetection] = useState(new Set());
  // Presses made from this tab, by node id: {runs before, at, failed?}. The
  // Inject's green/red circle reads it to say "injecting..." until the box's
  // run counter moves, and "did not take it" when it never does.
  const [injectPress, setInjectPress] = useState({});

  const history = useGraphHistory();
  const clipboard = useRef(null);
  // ...mirrored in state so the context menu's Paste row re-evaluates: a ref
  // changes nothing React watches, so the row could stay greyed after a copy
  // until something else happened to re-render.
  const [hasClip, setHasClip] = useState(false);
  const dragSnap = useRef(null);
  // True while a selection BOX is being dragged (see onNodesChange).
  const boxSel = useRef(false);
  const rfStore = useStoreApi();
  // The last selection box in FLOW coordinates. React Flow clears its own
  // copy before it calls onSelectionEnd, so it is caught here as it changes.
  const lastBox = useRef(null);
  useEffect(() => rfStore.subscribe((st) => {
    const r = st.userSelectionRect;
    if (!r || !r.width) return;
    const [tx, ty, zoom] = st.transform || [0, 0, 1];
    lastBox.current = { x: (r.x - tx) / zoom, y: (r.y - ty) / zoom,
                        w: r.width / zoom, h: r.height / zoom };
  }), [rfStore]);
  const invalidReason = useRef("");
  const saveTimer = useRef(null);
  const wrapperRef = useRef(null);
  // Full screen for the whole workspace (useCanvasFullscreen.js). Escape is
  // left to the context menu and the blueprint editor while they are open.
  const canvasFs = useCanvasFullscreen({ escBlocked: !!menu || !!bpId });
  // Where the right button went down, so a right-DRAG (pan) never ends in a
  // context menu the operator did not ask for.
  const rightDown = useRef(null);
  const paletteDrag = useRef(null);
  const paletteWRef = useRef(paletteW);
  const inspectorDrag = useRef(null);
  const inspectorWRef = useRef(inspectorW);
  // The right column can be dragged shut. Selecting a node, or asking for the guide, opens it again
  // at the width it had; with neither asked for it stays shut.
  const lastOpenW = useRef(inspectorW || 270);
  // `auto`: opened only because a node was picked, so it shuts again when the node is let go.
  const autoOpened = useRef(false);
  // Whether the right column is folded to the edge this render (guide hidden,
  // no node open), for the resize handlers, which are stable callbacks.
  const rightCollapsedRef = useRef(false);
  const openRight = useCallback((auto = false) => {
    if (!auto) autoOpened.current = false;
    if (inspectorWRef.current !== 0) return;
    const w = lastOpenW.current || 270;
    inspectorWRef.current = w;
    setInspectorW(w);
    autoOpened.current = auto;
    // A column opened for one node is not the new saved state: the closed one stays saved.
    if (!auto) { try { localStorage.setItem("wf.inspector.w", String(w)); } catch { /* private mode */ } }
  }, []);
  // A node picked for its properties opens a shut column; once no node is picked and nobody
  // took the column over, it shuts again. (Before any early return: hooks run on every render.)
  useEffect(() => {
    if (selectedId && selectedId === inspectId) { openRight(true); return; }
    if (autoOpened.current && inspectorWRef.current !== 0) {
      autoOpened.current = false;
      inspectorWRef.current = 0;
      setInspectorW(0);
    }
  }, [selectedId, inspectId, openRight]);
  const fileRef = useRef(null);
  // Live refs so keyboard handlers and the debounced save never read stale state.
  const stateRef = useRef({ nodes, edges, customVars: [] });
  // The drag-to-delete bin: only on screen while a node is actually moving,
  // so it is not a permanently armed delete button sitting over the canvas.
  stateRef.current = { nodes, edges, customVars };

  const registry = meta?.registry || {};
  const editable = !!meta?.editable;
  const nodeTypes = useMemo(() => buildNodeTypes(registry), [registry]);
  // The main guideline opens first, over the canvas: how a workflow is built,
  // before the nodes. "Go to Workflow" closes it; the Guideline button under the
  // palette search opens it again. A tick box in it keeps it from opening on load.
  // The guideline is a card at the top of the right-hand column, always there; folded or open.
  // First visit: open when page guides are on. After that, whatever was last chosen.
  const [guideOpen, setGuideOpen] = useState(() => {
    try {
      const v = localStorage.getItem("workflow.guide.open");
      if (v !== null) return v === "1";
    } catch { /* private mode */ }
    return guidesEnabled();
  });
  const setGuide = (on) => {
    setGuideOpen(on);
    try { localStorage.setItem("workflow.guide.open", on ? "1" : "0"); } catch { /* private mode */ }
  };
  // The eye on the canvas hides the whole guide card (and gives its column back to the
  // properties), and brings it back.
  const [guideHidden, setGuideHiddenState] = useState(() => {
    try { return localStorage.getItem("workflow.guide.hidden") === "1"; } catch { return false; }
  });
  const setGuideHidden = (on) => {
    setGuideHiddenState(on);
    try { localStorage.setItem("workflow.guide.hidden", on ? "1" : "0"); } catch { /* private mode */ }
  };
  // The Guide button in the top bar: the guide card is right here, so it is opened and lit up
  // for a moment so the eye finds it.
  const [guideFlash, setGuideFlash] = useState(false);
  useEffect(() => {
    let t = null;
    const ask = (e) => {
      if (e.detail?.key !== "workflow") return;
      openRight();
      setGuideHidden(false);
      setGuide(true);
      setGuideFlash(true);
      clearTimeout(t);
      t = setTimeout(() => setGuideFlash(false), 1800);
    };
    window.addEventListener(GUIDE_REQUEST_EVENT, ask);
    return () => { window.removeEventListener(GUIDE_REQUEST_EVENT, ask); clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // How much of the column the guide takes while a node's properties are also open (percent);
  // dragged with the bar between them. Alone, the guide fills the whole column.
  const [dockPct, setDockPct] = useState(50);
  const colRef = useRef(null);
  const dragDock = useRef(false);
  const moveDockBar = (e) => {
    if (!dragDock.current || !colRef.current) return;
    // Only while the button is held: a drag that never saw its pointer-up (released outside the
    // window, or the capture lost) must not keep following the pointer as it merely hovers.
    if (e.buttons !== 1) { dragDock.current = false; return; }
    const r = colRef.current.getBoundingClientRect();
    const pct = ((e.clientY - r.top) / r.height) * 100;
    setDockPct(Math.max(20, Math.min(80, pct)));
  };
  // The setup guide's Next opens this page with ?guide=main: the card is opened, even when this
  // page is already showing.
  const wfLocation = useLocation();
  useEffect(() => {
    if (new URLSearchParams(wfLocation.search).get("guide") === "main") { openRight(); setGuideHidden(false); setGuide(true); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wfLocation.search]);

  // -- load + poll -----------------------------------------------------------
  useEffect(() => {
    api.get("/api/workflow")
      .then((d) => {
        setMeta(d);
        setValues(d.values || {});
        // The workflow's name is not read here any more: a system has exactly
        // one workflow, so the page is titled "Workflow" and the stored name
        // is never shown or edited. It stays server-side untouched.
        setCustomVars(d.workflow?.variables || []);
        setFunctions(d.functions || []);
        setAppRunning(!!d.app_running);
        setAiStatus(d.ai || null);
        setFiltersPaused(new Set(d.filters_paused || []));
        setRunsWhen(d.runs_when || {});
        setWaitsForDetection(new Set(d.waits_for_detection || []));
        setNodes((d.graph?.nodes || []).map((n) => ({ ...n, data: n.data || {} })));
        setEdges(migrateStateEdges(d.graph?.nodes,
                                   (d.graph?.edges || []).map((e) => ({ ...e }))));
        // The server compiles on GET too -- show its errors and warnings on
        // arrival. They used to appear only after the first save, so a page
        // opened onto a broken chain looked perfectly healthy until touched.
        const errMap = {}, warnMap = {};
        for (const e of d.errors || []) if (e.node) errMap[e.node] = e.message;
        for (const w of d.warnings || []) if (w.node) warnMap[w.node] = w.message;
        setErrors(errMap);
        setWarnings(warnMap);
        if (d.migrated) {
          toast.success("Your canvas is ready",
                        "Existing rules were imported, your dashboard cards became "
                        + "nodes, and one worked example shows how a value reaches "
                        + "a dashboard. Review, then Save.");
        }
      })
      .catch(setError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // The live half, polled: /api/workflow/live answers only what changes
  // while the canvas is open (values, the app and AI state, the variables),
  // with the compile cached server-side.
  // The editor payload -- registry, graph, mailboxes -- is fetched once,
  // above. usePoll pauses while the tab is hidden and refetches on return.
  const { data: live } = usePoll("/api/workflow/live", 4000);
  // Is this box's MQTT broker up? The dot on every MQTT node that names no
  // Connection reads it (nodeStatus.js); the toolbar pill polls the same
  // answer for its words. Null until the first answer.
  const { data: brokerData } = usePoll("/api/mqtt/broker/status", 10000);
  const brokerUp = brokerData?.status ? brokerData.status.state === "running" : null;
  useEffect(() => {
    if (!live) return;
    setValues(live.values || {});
    setAppRunning(!!live.app_running);
    setAiStatus(live.ai || null);
    if (Array.isArray(live.filters_paused)) {
      setFiltersPaused(new Set(live.filters_paused));
    }
    if (live.runs_when && typeof live.runs_when === "object") {
      setRunsWhen(live.runs_when);
    }
    if (Array.isArray(live.waits_for_detection)) {
      setWaitsForDetection(new Set(live.waits_for_detection));
    }
    setMeta((m) => (m ? { ...m, variables: live.variables } : m));
  }, [live]);

  // -- save (debounced) ------------------------------------------------------
  const doSave = useCallback(async () => {
    if (!editable) return;
    const { nodes: ns, edges: es } = stateRef.current;
    setSaveState("saving");
    try {
      const d = await api.put("/api/workflow", {
        variables: stateRef.current.customVars,
        graph: {
          nodes: ns.map((n) => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
          edges: es.map((e) => ({ id: e.id, source: e.source, target: e.target,
                                  sourceHandle: e.sourceHandle, targetHandle: e.targetHandle })),
        },
      });
      const errMap = {}, warnMap = {};
      for (const e of d.errors || []) if (e.node) errMap[e.node] = e.message;
      for (const w of d.warnings || []) if (w.node) warnMap[w.node] = w.message;
      setErrors(errMap);
      setWarnings(warnMap);
      setFunctions(d.functions || []);
      if (d.runs_when) setRunsWhen(d.runs_when);
      // Server may have minted fnIds - fold them back so keys stay stable.
      const byId = Object.fromEntries((d.graph?.nodes || []).map((n) => [n.id, n]));
      setNodes((cur) => cur.map((n) => {
        const srv = byId[n.id];
        return srv && srv.data?.fnId && !n.data?.fnId
          ? { ...n, data: { ...n.data, fnId: srv.data.fnId } } : n;
      }));
      const general = (d.errors || []).find((e) => !e.node);
      if (general) toast.error("Workflow not applied", general.message);
      setSaveState(d.ok ? "saved" : "error");
      setDirty(false);
    } catch (e) {
      setSaveState("error");
      toast.error("Could not save workflow", e.message);
    }
  }, [editable, toast]);

  const scheduleSave = useCallback(() => {
    setDirty(true);
    if (!autoSaveRef.current) return;            // manual mode: Save button only
    clearTimeout(saveTimer.current);
    setSaveState("saving");
    saveTimer.current = setTimeout(doSave, 1500);
  }, [doSave]);

  // Read through a ref so every existing scheduleSave() caller sees the
  // current preference without re-creating half the page's callbacks.
  const autoSaveRef = useRef(autoSave);
  useEffect(() => {
    autoSaveRef.current = autoSave;
    localStorage.setItem("wf.autosave", autoSave ? "on" : "off");
    // Turning it back on flushes whatever is pending, so the switch never
    // leaves edits stranded.
    if (autoSave && dirty) scheduleSaveRef.current?.();
  }, [autoSave, dirty]);
  const scheduleSaveRef = useRef(null);
  useEffect(() => { scheduleSaveRef.current = scheduleSave; }, [scheduleSave]);

  const saveNow = useCallback(async () => {
    clearTimeout(saveTimer.current);
    await doSave();
    setDirty(false);
  }, [doSave]);

  // With auto-save off, leaving would silently bin the graph. The guard covers
  // both ways out: router navigation (the sidebar, any Link) gets the dialog,
  // and the browser's own close/reload gets the native prompt. Only when
  // autosave is OFF -- with it on the graph is already on the server.
  const unsavedDialog = useUnsavedGuard({
    when: !autoSave && dirty,
    what: "Workflow",
    detail: "This workflow has changes that have not been saved to the board yet.",
    busy: saveState === "saving",
    onSave: saveNow,
  });
  useEffect(() => () => clearTimeout(saveTimer.current), []);

  // -- change handlers -------------------------------------------------------
  const onNodesChange = useCallback((changes) => {
    const { nodes: ns, edges: es } = stateRef.current;
    // What the operator just LET GO of -- the nodes they dragged themselves,
    // not the ones a frame or group carried. Only these join or leave a
    // function frame or group (assignDropped): passing a frame over a node
    // never swallows it.
    const dropped = changes.filter((c) => c.type === "position" && c.dragging === false)
      .map((c) => c.id);
    // A selection BOX picks nodes it merely touches (SelectionMode.Partial),
    // which is right for a node and wrong for a function frame or a group:
    // they are big, so sweeping over two nodes inside one selected the whole
    // frame too. A frame or group joins a box selection only when the box
    // covers it completely.
    if (boxSel.current && changes.some((c) => c.type === "select" && c.selected)) {
      const st = rfStore.getState();
      const r = st.userSelectionRect;
      const [tx, ty, zoom] = st.transform || [0, 0, 1];
      if (r) {
        const box = { x: (r.x - tx) / zoom, y: (r.y - ty) / zoom,
                      w: r.width / zoom, h: r.height / zoom };
        const byId = new Map(ns.map((n) => [n.id, n]));
        changes = changes.filter((c) => {
          if (c.type !== "select" || !c.selected) return true;
          const n = byId.get(c.id);
          if (!n || (n.type !== FN_FRAME && n.type !== GROUP)) return true;
          const w = Number(n.data?.width) || 0;
          const h = n.type === FN_FRAME && n.data?.collapsed ? 44 : Number(n.data?.height) || 0;
          return n.position.x >= box.x && n.position.y >= box.y
            && n.position.x + w <= box.x + box.w && n.position.y + h <= box.y + box.h;
        });
      }
    }
    // GROUPS carry whatever sits inside them. First, so a function frame
    // carried by a group is already moving when frames are expanded below.
    if (ns.some((n) => n.type === GROUP)) changes = expandGroupDrag(changes, ns);
    // FUNCTION FRAMES. Dragging a frame carries its Start, End and body;
    // deleting one takes its Start and End with it (the body stays, as plain
    // nodes on the canvas); a Start or End is never deleted on its own while
    // its frame is there -- it is the frame's edge, not a node of its own.
    if (ns.some((n) => n.type === FN_FRAME)) {
      const pins = framePins(ns);
      const gone = new Set(changes.filter((c) => c.type === "remove").map((c) => c.id));
      const pinned = new Map();
      for (const [fid, p] of Object.entries(pins)) {
        if (p.start) pinned.set(p.start.id, fid);
        if (p.end) pinned.set(p.end.id, fid);
      }
      changes = changes.filter((c) => !(c.type === "remove" && pinned.has(c.id)
                                        && !gone.has(pinned.get(c.id))));
      for (const fid of gone) {
        const p = pins[fid];
        if (!p) continue;
        for (const pin of [p.start, p.end]) {
          if (pin && !changes.some((c) => c.type === "remove" && c.id === pin.id)) {
            changes = [...changes, { type: "remove", id: pin.id }];
          }
        }
      }
      changes = expandFrameDrag(changes, ns);
    }
    if (changes.some((c) => c.type === "remove")) history.push(ns, es);
    if (changes.some((c) => c.type === "position" && c.dragging) && !dragSnap.current) {
      dragSnap.current = { nodes: ns, edges: es };
    }
    const dragEnd = changes.some((c) => c.type === "position" && c.dragging === false);
    setNodes((cur) => {
      const next = applyNodeChanges(changes, cur);
      return dropped.length ? assignDropped(next, dropped) : next;
    });
    if (dragEnd && dragSnap.current) {
      history.push(dragSnap.current.nodes, dragSnap.current.edges);
      dragSnap.current = null;
    }
    if (changes.some((c) => c.type === "remove" || (c.type === "position" && c.dragging === false))) {
      scheduleSave();
    }
    // Selection is NOT tracked here - onSelectionChange below owns it, so a
    // rubber-band that touches five nodes cannot leave the inspector showing
    // whichever one happened to report last.
  }, [history, scheduleSave]);

  // One node selected → inspect it. None → the pipeline help. Many → keep the
  // inspector shut (there is no such thing as editing five nodes at once) while
  // the canvas keeps every highlight.
  const onSelectionChange = useCallback(({ nodes: sel }) => {
    setSelectedId((sel || []).length === 1 ? sel[0].id : null);
  }, []);

  const onEdgesChange = useCallback((changes) => {
    const { nodes: ns, edges: es } = stateRef.current;
    if (changes.some((c) => c.type === "remove")) { history.push(ns, es); scheduleSave(); }
    setEdges((cur) => applyEdgeChanges(changes, cur));
  }, [history, scheduleSave]);

  const isValidConnection = useCallback((conn) => {
    const { nodes: ns, edges: es } = stateRef.current;
    const src = ns.find((n) => n.id === conn.source);
    const tgt = ns.find((n) => n.id === conn.target);
    if (!src || !tgt) return false;
    // Link ports pair with link ports and nothing else: they carry no
    // value, so a link_out into a real input would LOOK like a feed and
    // feed nothing.
    const srcLink = conn.sourceHandle === "link_out";
    const tgtLink = conn.targetHandle === "link_in";
    if (srcLink !== tgtLink) {
      invalidReason.current = "a link port only connects to another link "
        + "port - it ties nodes together and passes no value";
      return false;
    }
    if (srcLink && tgtLink) { invalidReason.current = ""; return true; }
    // An ON / OFF point may be wired into ANY node (Tok Aba, 2026-10-09):
    // what it reaches runs only while the state matches, and a node that
    // cannot take it (it needs detections) is shown off with the reason by
    // the compile (state_points.py), not refused here. Only a Comment and a
    // Link in take no wire at all.
    if (isStatePoint(src.type, conn.sourceHandle)) {
      if (["note.comment", "common.link_in"].includes(tgt.type)) {
        invalidReason.current = `${registry[tgt.type]?.label || tgt.type} `
          + "takes no wire on that side";
        return false;
      }
      invalidReason.current = "";
      return true;
    }
    const [ok, reason] = edgeAllowed(registry, src.type, tgt.type,
                                     conn.targetHandle);
    if (!ok) { invalidReason.current = reason; return false; }
    {
      const [inOk, inWhy] = wireStaysInside(src.id, tgt.id, ns);
      if (!inOk) { invalidReason.current = inWhy; return false; }
    }
    // THE COUNTER takes a Zone OR a Line, and the port says which. Asked
    // before the generic kind check below so the refusal is in the Counter's
    // own words -- "drop it on the Zone port" -- rather than a line counter's
    // "needs a line zone", which names a node the palette no longer shows.
    if (src.type === "zone.ref" && isCounter(tgt.type)) {
      const z = (meta?.variables?.zone_list || []).find((x) => x.id === src.data?.zone);
      const port = conn.targetHandle || "in";
      const mode = counterMode(tgt, es);
      if (tgt.type === LINE_COUNTER && (port === "zone" || port === "in")) {
        if (es.some((e) => e.target === tgt.id
                           && ns.find((n) => n.id === e.source)?.type === "zone.ref")) {
          invalidReason.current = "this Counter already counts a line - take "
            + "that wire off first";
          return false;
        }
        if (z && z.kind !== "line") {
          invalidReason.current = `'${z.name}' is an area, and this Counter `
            + "counts a line. To count inside the area instead, take the line "
            + "wire off first - the Zone port comes back.";
          return false;
        }
      }
      if (tgt.type === COUNTER && port === COUNTER_LINE_PORT) {
        if (mode === "zone") {
          invalidReason.current = "A Counter counts inside a Zone OR across a "
            + "Line, not both - take the Zone wire off first";
          return false;
        }
        if (z && z.kind !== "line") {
          invalidReason.current = `'${z.name}' is an area - drop it on the `
            + "Zone port to count inside it";
          return false;
        }
      }
      if (tgt.type === COUNTER && port === "zone" && mode === "line") {
        invalidReason.current = "A Counter counts inside a Zone OR across a "
          + "Line, not both - take the Line wire off first";
        return false;
      }
    }
    // Zone-kind check at wire time: refuse an area into a line analytics and
    // vice versa, with the reason the server would give on save.
    // A zone-optional node declares the kind it takes when one IS wired, so
    // "this needs an area" still answers for it.
    if (src.type === "zone.ref"
        && (registry[tgt.type]?.zone_kind || registry[tgt.type]?.zone_optional)) {
      const z = (meta?.variables?.zone_list || []).find((x) => x.id === src.data?.zone);
      const wants = registry[tgt.type].zone_kind
        || registry[tgt.type].zone_optional;
      // Area rules take a LINE too -- the engine reads it as a thin place
      // ("at the line"). Only the line-native rules (crossings, travel)
      // still insist: an area has no crossing direction to count.
      if (z && z.kind !== wants && !(wants === "area" && z.kind === "line")) {
        invalidReason.current =
          `${registry[tgt.type].label} needs a ${wants} zone - ` +
          `'${z.name}' is a ${z.kind}`;
        return false;
      }
    }
    // The PER-OBJECT ports -- OID ("which object") and the in-zone timer
    // ("how long has THIS one been inside") -- are one value PER OBJECT, and
    // everything else on this canvas reads one value for the whole system.
    // Only a Data Table column can hold them, which is why they exist.
    //
    // Refused HERE rather than at compile: the wire used to land, look right,
    // and turn the target red -- and the message arrived after the drawing was
    // done. Mirrors the same rule in core/services/workflow.py, which still
    // catches a graph built anywhere else.
    //
    // The one other home for an OID: a Trigger's own OID port, where it says
    // WHO the alarm is about so "raise again for a new object" can tell a
    // newcomer from a lingerer. Mirrors the same exception on the server.
    if (["OID", "timer"].includes(conn.sourceHandle)
        && !(tgt.type === "output.dataset"
             && String(conn.targetHandle || "").startsWith("c_"))
        && !(conn.sourceHandle === "OID" && tgt.type === "output.trigger"
             && conn.targetHandle === "oid")) {
      invalidReason.current =
        (conn.sourceHandle === "OID"
          ? "OID names WHICH object, so it can only be recorded a row at a time"
          : "this timer runs per object, so it can only be recorded a row at "
            + "a time")
        + ` - drag it onto a Data Table column. To react to it instead, branch `
        + "on a value the rule publishes for the whole system (a Counter's "
        + "count, or a Cycle Time's longest stay).";
      return false;
    }
    // ...and that port takes an OID and nothing else: a count dropped on it
    // would compile to "which object is number 3", which is no object.
    if (tgt.type === "output.trigger" && conn.targetHandle === "oid") {
      if (conn.sourceHandle !== "OID") {
        invalidReason.current = "this port wants to know WHICH object - drag "
          + "the rule's OID output onto it (tick OID on the rule first)";
        return false;
      }
      if (es.some((e) => e.target === conn.target && e.targetHandle === "oid")) {
        invalidReason.current = "this Trigger already knows who from another "
          + "OID wire - remove it first";
        return false;
      }
    }
    // The duplicate guard's ports read what a Data Table ALREADY HOLDS, so
    // they take a column's read port and nothing else, one wire each.
    // Mirrors the compile (core/services/workflow.py _trigger_guard).
    if (tgt.type === "output.trigger"
        && ["cur_oid", "cur_value"].includes(conn.targetHandle)) {
      if (src.type !== "output.dataset"
          || !String(conn.sourceHandle || "").startsWith("o_")) {
        invalidReason.current = "this port reads what a Data Table already "
          + "holds - drag a column's read port (the dot on the right of a "
          + "column) onto it";
        return false;
      }
      if (es.some((e) => e.target === conn.target
                         && e.targetHandle === conn.targetHandle)) {
        invalidReason.current = "this port already reads a column - remove "
          + "that wire first";
        return false;
      }
    }
    // One wire per target handle (except filter chaining fan-in is fine).
    if (registry[tgt.type]?.category !== "filter"
        && es.some((e) => e.target === conn.target
                          && (e.targetHandle || "in") === (conn.targetHandle || "in")
                          && registry[src.type]?.category === "zone")) {
      invalidReason.current = "this input already has a zone wired - remove it first";
      return false;
    }
    invalidReason.current = "";
    return true;
  }, [registry, meta]);

  const onConnect = useCallback((conn) => {
    const { nodes: ns, edges: es } = stateRef.current;
    history.push(ns, es);
    // A SECOND value dropped on a Switch is the operator asking to test both
    // -- an if/else over two things is still an if/else to everyone but the
    // compiler. Compare and AND/OR read one value PER CONDITION, so the wire
    // lands on a new port and a condition is added to read it; the node grows
    // instead of refusing. A node still in a shape that ROUTES one value
    // (cases, bands, on/off) is moved to Compare first, keeping its tests --
    // "which case is this" has no meaning for two separate values.
    const target = ns.find((n) => n.id === conn.target);
    let edge = { ...conn, id: `e_${mintId()}` };
    // A LINE dropped on a Counter's Zone port is a line to count across --
    // what anybody dragging a line onto a counter means. It goes onto the
    // Line port, and the swap effect below makes the node a line counter.
    // (A Counter SAVED with a line on its Zone port keeps counting what is
    // at the line, as it always did: only a new wire is redirected.)
    if (target?.type === COUNTER && conn.targetHandle === "zone") {
      const src0 = ns.find((n) => n.id === conn.source);
      const z0 = src0?.type === "zone.ref"
        && (meta?.variables?.zone_list || []).find((x) => x.id === src0.data?.zone);
      if (z0 && z0.kind === "line") edge = { ...edge, targetHandle: COUNTER_LINE_PORT };
    }
    let grew = "";
    if (isBranch(target?.type)
        // Only a wire with NO port of its own grows the node: dropped on the
        // + port, or on the node's body. A wire dropped on a NAMED port
        // means that port -- rerouting it to a fresh one was moving the
        // operator's hand.
        && (!conn.targetHandle || conn.targetHandle === "in_new")) {
      const ctype = branchCtype(target.type, target.data);
      const wiredIn = es.filter((e) => e.target === target.id);
      if (wiredIn.length >= 1 || conn.targetHandle === "in_new") {
        const used = new Set(wiredIn.map((e) => e.targetHandle || "in1"));
        let port = "";
        for (let i = 1; i <= 8 && !port; i += 1) {
          if (!used.has(`in${i}`)) port = `in${i}`;
        }
        if (!port) {
          toast.error("This Switch already reads eight values",
                      "Combine some of them in a Formula first, or use a "
                      + "second Switch.");
        } else {
          edge = { ...edge, targetHandle: port };
          const keepsShape = ctype === "compare" || ctype === "multi";
          const rows = (target.data?.rules || []).slice(0, 7).map((r) => ({
            ...r,
            input: r.input || "in1",
            // Bands cannot become conditions: "0 up to 10" is a shape the
            // condition row has no operator for, so it becomes "> from".
            ...(r.op === "between" ? { op: ">", value: r.value ?? 0,
                                       value2: undefined } : {}),
          }));
          const nextRules = [...rows, { input: port, op: ">", value: 0 }];
          grew = keepsShape ? ctype : "compare";
          // For the branch FAMILY the shape is the node, so growing past one
          // value means becoming a different node: If / Else if is the one
          // that reads a value per condition. A plain Compare has exactly two
          // ways out and cannot hold a second test; Range and Match route one
          // number and have no meaning for two. The old Switch keeps its
          // ctype-in-data instead, which is what it has always done.
          const grownType = target.type === "logic.switch" ? null
            : ["branch.cases", "branch.multi"].includes(target.type)
              ? target.type
              : "branch.cases";
          // AFTER the gesture, not during it. Growing the rows adds HANDLES
          // to the node being connected to, and re-rendering a target's
          // handles inside onConnect leaves the connection line stuck to the
          // cursor -- the element the drag was measuring against is gone
          // before the release finishes. One tick later the wire is drawn,
          // the gesture is over, and the new port simply appears.
          const grown = { ctype: grew, rules: nextRules,
                          join: grew === "multi" ? (target.data?.join || "and")
                                                 : target.data?.join };
          setTimeout(() => {
            setNodes((cur) => cur.map((n) => (n.id !== target.id ? n : {
              ...n,
              ...(grownType && grownType !== n.type ? { type: grownType } : {}),
              data: { ...n.data, ...grown },
            })));
            scheduleSave();
          }, 0);
        }
      }
    }
    setEdges((cur) => addEdge(edge, cur));
    if (grew === "multi") {
      toast.info("Condition added",
                 "This Switch combines its inputs - set ALL / ANY and the "
                 + "tests in the inspector.");
    } else if (grew) {
      toast.info("Switch now compares one value after another",
                 "IF / ELSE IF, each condition on its own input and its own "
                 + "output. Set the tests in the inspector.");
    }
    // The Formula's + port: mint the next input under the wire. The edge
    // is retargeted to the fresh letter, so no stored wire ever holds the
    // + id, and the node's inputs count follows.
    if (conn.targetHandle === "in_new" && target
        && (target.type === "analytics.formula"
            || registry[target.type]?.math)) {
      const tspec = registry[target.type] || {};
      const opsMap = tspec.ops || {};
      const dd = target.data || {};
      // A Math node chains where its OWN entry says so; the Formula chains
      // where its operation does.
      const chain = tspec.math
        ? !!tspec.chain && (!tspec.chain_ops || tspec.chain_ops.includes(dd.op))
        : !!opsMap[dd.op]?.chain;
      const arity = chain
        ? Math.max(2, Math.min(8, parseInt(dd.inputs, 10) || 2))
        : (opsMap[dd.op]?.arity || (tspec.handles || []).length || 2);
      const letters = ["a", "b", "c", "d", "e", "f", "g", "h"];
      if (chain && arity < 8) {
        edge = { ...edge, targetHandle: letters[arity] };
        setTimeout(() => {
          setNodes((cur) => cur.map((n) => (n.id !== target.id ? n : {
            ...n, data: { ...n.data, inputs: arity + 1 },
          })));
          scheduleSave();
        }, 0);
      } else {
        edge = { ...edge, targetHandle: letters[Math.min(arity, 7)] };
      }
    }
    // The Chart's + port: mint the next SERIES input under the wire, the
    // same gesture as the Formula's above. The edge is retargeted to a real
    // series handle, so no stored wire ever holds the + id, and the node's
    // series count follows so the port stays on the node afterwards.
    if (conn.targetHandle === "in_new" && target?.type === "dashboard.graph") {
      const used = new Set(es.filter((e) => e.target === target.id)
                             .map((e) => e.targetHandle || "in"));
      const free = GRAPH_SERIES_HANDLES.find((h) => !used.has(h));
      if (!free) {
        // Unreachable through the + port (it is not drawn once all eight
        // exist), kept because a wire must never be left holding "in_new".
        edge = { ...edge,
                 targetHandle: GRAPH_SERIES_HANDLES[GRAPH_SERIES_HANDLES.length - 1] };
        toast.error("A chart draws at most eight series",
                    "Combine some of the values first, or use a second "
                    + "chart.");
      } else {
        const slot = GRAPH_SERIES_HANDLES.indexOf(free) + 1;
        edge = { ...edge, targetHandle: free };
        // AFTER the gesture, for the reason the Switch's growth is: adding a
        // handle to the node being connected to mid-drag leaves the
        // connection line stuck to the cursor.
        setTimeout(() => {
          setNodes((cur) => cur.map((n) => (n.id !== target.id ? n : {
            ...n,
            data: { ...n.data,
                    series_n: Math.max(slot, parseInt(n.data?.series_n, 10) || 2) },
          })));
          scheduleSave();
        }, 0);
      }
    }
    // A SECOND value wired into a Card. Nothing is converted for the
    // operator -- a chart is a shape they pick, not one the canvas guesses
    // -- but each shape's meaning for several wires is said where the wire
    // lands: a VERDICT weighs them (first wired rule that fires decides the
    // state, labels in the inspector); a tile or gauge shows one value, so
    // the extra wire wants a chart shape or a Graph node.
    {
      const dst0 = ns.find((n) => n.id === conn.target);
      if (dst0 && dst0.type === "dashboard.card"
          && es.some((e) => e.target === dst0.id)) {
        const kind0 = (dst0.data || {}).kind || "tile";
        if (kind0 === "verdict") {
          toast.info("Verdict now weighs several rules",
                     "The first wired rule that fires decides the state - "
                     + "wire order is the priority. Name each state under "
                     + "\u201cUse custom states\u201d in the inspector.");
        } else {
          toast.info("A tile shows one value",
                     "Set the card's shape to Verdict to weigh both rules, "
                     + "or use a Graph node to draw them as series.");
        }
      }
    }
    // An Alert wired into a Card means "report whether this rule is raised",
    // and there is exactly one shape that says that: the verdict. Setting it
    // here rather than at compile time keeps it EDITABLE -- the operator sees
    // a verdict card appear and can change it to a tile, which a compile-time
    // default would silently undo on the next apply.
    //
    // Only an untouched card is adopted: one already set to a shape or bound
    // to a metric is a decision somebody made, and wiring something into it is
    // not consent to overwrite that.
    const src = ns.find((n) => n.id === conn.source);
    const dst = ns.find((n) => n.id === conn.target);
    // ...but not a card counting RECORDED events: an Alert wired into one of
    // those scopes the count to what that rule raised, and turning it into a
    // verdict would throw the count away.
    if (TRANSPARENT_OUT.includes(src?.type) && dst?.type === "dashboard.card"
        && !recordedMode(dst)) {
      const d = dst.data || {};
      const untouched = (!d.metric && (!d.kind || d.kind === "tile"));
      if (untouched) {
        setNodes((cur) => cur.map((n) => (n.id !== dst.id ? n : {
          ...n,
          data: { ...n.data, kind: "verdict", metric: "active" },
        })));
        toast.info("Card set to Verdict",
                   "It reports whether this alert is raised. Change the shape "
                   + "in the inspector if you wanted a number.");
      }
    }
    scheduleSave();
  }, [history, scheduleSave, toast, meta]);

  const onConnectEnd = useCallback((_evt, connectionState) => {
    if (connectionState && connectionState.isValid === false && invalidReason.current) {
      toast.error("Invalid connection", invalidReason.current);
      invalidReason.current = "";
    }
  }, [toast]);

  // -- the ONE Counter: a line on or off changes what it is -----------------
  //
  // A Counter with a line on its Line port IS a line counter, and a line
  // counter whose line comes off is a Counter again. The swap rewrites the
  // node's type (registry.js counterToLine / lineToCounter say what moves),
  // and it is driven by the CHANGE in the wires rather than by the gesture
  // that made it: a wire can go by Delete, by dragging it off, by deleting
  // the Zone node, by paste -- one place catches them all. Undo needs no
  // case of its own: a snapshot restores the node and its wires together,
  // so the change it makes is never a swap.
  const counterPrev = useRef({ nodes: [], edges: [] });
  useEffect(() => {
    const before = counterPrev.current;
    counterPrev.current = { nodes, edges };
    // The load is not a change anybody made: an old Line Counter saved with
    // no line stays one (it would otherwise start counting the frame).
    if (!before.nodes.length || !registry[LINE_COUNTER]) return;
    const { toLine, toCounter } = counterSwaps(nodes, before.edges, edges,
                                               before.nodes);
    if (!toLine.length && !toCounter.length) return;
    const swaps = [...toLine.map((id) => [id, counterToLine]),
                   ...toCounter.map((id) => [id, lineToCounter])];
    let ns = nodes, es = edges, dropped = 0;
    for (const [id, swap] of swaps) {
      const n = ns.find((x) => x.id === id);
      if (!n) continue;
      const r = swap(registry, n, es);
      ns = ns.map((x) => (x.id === id ? r.node : x));
      es = r.edges;
      dropped += r.dropped;
    }
    counterPrev.current = { nodes: ns, edges: es };
    setNodes(ns);
    setEdges(es);
    const lost = dropped
      ? ` ${dropped} output wire${dropped === 1 ? "" : "s"} it no longer has `
        + "came off - undo brings them back." : "";
    if (toLine.length) {
      toast.info("Counter now counts a line",
                 "It counts what crosses the line wired in. Pick which way "
                 + "counts in its settings." + lost);
    } else {
      toast.info("Counter no longer counts a line",
                 "With nothing on its Zone or Line port it counts the whole "
                 + "view." + lost);
    }
    scheduleSave();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  // -- add / duplicate / clipboard ------------------------------------------
  // A Snapshot node owns a page in Footage under its name, so a new one asks
  // for the name at once (it can be changed in the node's settings).
  const askPage = useAsk();
  // NAME IT FIRST. A node whose NAME is its identity -- what an alert says,
  // what a card is titled on the Dashboard, the page its photos land on, the
  // filter the dashboard pauses by name -- asks for that name the moment it is
  // dropped, and is not added without one: an unnamed one is a card called
  // "Card", an alert nobody can tell from the next, a Footage page with no
  // title. A copy dragged from "On this canvas" already carries its name.
  const nameNode = useCallback(async (id, type) => {
    const ask = NAME_FIRST[type];
    if (!ask) return;
    const name = await askPage({
      title: ask.title, description: ask.description,
      label: ask.label, placeholder: ask.placeholder, submitLabel: "Add node" });
    const clean = String(name || "").split(/\s+/).join(" ").trim().slice(0, 60);
    if (!clean) {
      // No name, no node: take it back off the canvas (one undo step was
      // already pushed when it was added, so this leaves no trace).
      setNodes((cur) => cur.filter((n) => n.id !== id));
      setEdges((cur) => cur.filter((e) => e.source !== id && e.target !== id));
      setSelectedId((cur) => (cur === id ? null : cur));
      setInspectId((cur) => (cur === id ? null : cur));
      toast.info("Not added", `${registry[type]?.label || "This node"} needs a name - drop it again and give it one.`);
      scheduleSave();
      return;
    }
    setNodes((cur) => cur.map((n) => (n.id === id
      ? { ...n, data: { ...n.data, [ask.field]: clean } } : n)));
    scheduleSave();
  }, [askPage, registry, toast, scheduleSave]);

  // A FUNCTION arrives as one frame with its Start and End already pinned to
  // its edges and already sharing a name, so it works before it is renamed.
  // The pins are placed by syncFrames once React Flow has measured them.
  const addFunctionFrame = useCallback((position) => {
    if (!registry[FN_FRAME]) return;
    const { nodes: ns, edges: es } = stateRef.current;
    history.push(ns, es);
    const fn = nextFunctionName(ns);
    const frame = { id: mintId(), type: FN_FRAME, position, selected: true,
                    data: { ...(registry[FN_FRAME].data || {}), fn, members: [] } };
    const start = { id: mintId(), type: FN_START, position, data: { fn, params: [] } };
    const end = { id: mintId(), type: FN_END, position, data: { fn, returns: [] } };
    const at = pinPositions(frame, start, end);
    start.position = at.start;
    end.position = at.end;
    setNodes((cur) => assignDropped([...cur.map((n) => ({ ...n, selected: false })),
                                     frame, start, end], [frame.id]));
    setSelectedId(frame.id);
    setInspectId(frame.id);
    scheduleSave();
  }, [registry, history, scheduleSave]);

  const addNode = useCallback((type, position, extraData) => {
    // A function is a frame with its Start and End, never one of the three.
    if (type === FN_FRAME || type === FN_START) { addFunctionFrame(position); return; }
    const spec = registry[type];
    if (!spec) return;
    const { nodes: ns, edges: es } = stateRef.current;
    history.push(ns, es);
    const id = mintId();
    const data = { ...(spec.data || {}), ...(extraData || {}) };
    // Anything that DECLARES an fnId in the registry gets a unique one now,
    // whatever its category. It used to be analytics/variable only, which
    // left the Switch (category logic, fnId in its defaults) publishing
    // under a slug of its NAME -- so a second Switch, still called "Switch",
    // compiled to the same branch ids as the first and the graph was refused
    // as a duplicate. The id is minted once and never regenerated, so
    // renaming a node afterwards never orphans what is bound to it.
    if ((spec.data || {}).fnId !== undefined) {
      // A Variable node stays NAMELESS until the operator chooses: its name
      // IS which variable it stores into, and defaulting it to the label
      // "Variable" made every new node look pre-assigned to a variable
      // called `variable` that nobody ever declared.
      if (type !== "variable.ref") {
        data.name = data.name || spec.label;
        data.fnId = data.fnId || mintFnId(data.name, id);
      }
    }
    // A Chart opens on "Choose a chart type" (chartTypes.markFreshChart):
    // remembered for this session only, never saved with the node.
    if (type === "dashboard.graph" && !(extraData || {}).kind) markFreshChart(id);
    // Dropped inside a function or a group: it joins it, as if dragged in.
    setNodes((cur) => assignDropped([...cur.map((n) => ({ ...n, selected: false })),
                                     { id, type, position, data, selected: true }], [id]));
    setSelectedId(id);
    // A node you just created opens its settings: you made it in order to
    // configure it, so the double-click rule would only be a step in the way.
    setInspectId(id);
    scheduleSave();
    if (NAME_FIRST[type] && !String(data[NAME_FIRST[type].field] || "").trim()) nameNode(id, type);
  }, [registry, history, scheduleSave, nameNode, addFunctionFrame]);

  // A SAVED query dropped from the palette: the Query node plus the wires
  // that make it read.
  //
  // A Query with no table wired in compiles to nothing and says so, and the
  // statement was saved knowing which tables it names -- asking the operator
  // to redraw those lines from the sub-label is asking them to copy an answer
  // the palette already had. A table already on the canvas is REUSED rather
  // than duplicated: two nodes for one table is two places to read the same
  // rows from, which is exactly the confusion the registry exists to prevent.
  const addSavedQuery = useCallback((position, data) => {
    const { nodes: ns, edges: es } = stateRef.current;
    const want = (data._tables || []).filter(Boolean);
    history.push(ns, es);
    const qid = mintId();
    const qdata = { ...(registry["storage.query"]?.data || {}),
                    name: data.name || "", sql: data.sql || "" };
    const made = [];
    const wires = [];
    want.forEach((key, i) => {
      const already = ns.find((n) => n.type === "output.dataset"
                                     && (n.data || {}).dataset === key);
      let src = already?.id;
      if (!src) {
        src = mintId();
        made.push({
          id: src, type: "output.dataset",
          // Left of the query and stacked, so the wires read left to right
          // like every other line on the canvas.
          position: { x: position.x - 300, y: position.y + i * 120 },
          data: { ...(registry["output.dataset"]?.data || {}), dataset: key,
                  name: (meta?.variables?.tables || [])
                    .find((t) => t.key === key)?.name || key },
        });
      }
      wires.push({ id: `e_${mintId()}`, source: src, target: qid,
                   targetHandle: "table" });
    });
    setNodes((cur) => [...cur.map((n) => ({ ...n, selected: false })), ...made,
                       { id: qid, type: "storage.query", position,
                         data: qdata, selected: true }]);
    if (wires.length) setEdges((cur) => [...cur, ...wires]);
    setSelectedId(qid);
    setInspectId(qid);
    scheduleSave();
  }, [registry, history, scheduleSave, meta]);

  // Where a node lands when nothing on the canvas (a click position, a drop
  // point) says where -- the middle of whatever the operator is looking at,
  // the same spot Import uses for the same reason.
  const viewportCenter = useCallback(() => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    return rect ? rf.screenToFlowPosition({ x: rect.left + rect.width / 2,
                                            y: rect.top + rect.height / 2 })
      : { x: 0, y: 0 };
  }, [rf]);

  // A constant declared from the Variables panel gets a real node -- see the
  // long comment on variable.number in core/services/workflow.py for why:
  // it is a visible, editable thing on the canvas, not an entry in a list.
  const addConstant = useCallback((name, varName, constOpts) => {
    addNode("variable.number", viewportCenter(), {
      fnId: varName, name,
      value: constOpts.value, value_type: constOpts.value_type,
    });
  }, [addNode, viewportCenter]);

  // Declare a variable name. Every variable needs its OWN name: two with the
  // same name would be one value read and written from two places, and the
  // person would never know why. A name that is already taken is refused
  // here, for every way of making one (the palette, the Variables panel, a
  // node's own "+ New variable"). Returns false when refused.
  const declareVariable = useCallback((name, varName, constOpts) => {
    const { nodes: ns, customVars: cv } = stateRef.current;
    const taken = new Set((cv || []).map((v) => v.var));
    for (const n of ns || []) {
      const d = n.data || {};
      if (n.type === "variable.ref") {
        if (d.name && d.name !== (registry[n.type] || {}).label) taken.add(slugify(d.name));
      } else if (d.fnId) {
        taken.add(slugify(d.fnId));
      }
    }
    if (taken.has(varName)) {
      toast.error("That name is already used",
                  `A variable called "${String(name).trim()}" already exists. `
                  + "Every variable needs its own name: choose a different one.");
      return false;
    }
    setCustomVars((cur) => [...cur, { name: String(name).trim(), var: varName }]);
    if (constOpts) addConstant(name, varName, constOpts);
    scheduleSave();
    return true;
  }, [registry, addConstant, scheduleSave]);

  const duplicateNode = useCallback((nodeId) => {
    const { nodes: ns } = stateRef.current;
    const n = ns.find((x) => x.id === nodeId);
    if (!n) return;
    const data = { ...n.data };
    // Same rule as creation: a copy of anything that publishes gets its OWN
    // id, or the copy and the original compile to the same rule.
    if ((registry[n.type]?.data || {}).fnId !== undefined
        && n.type !== "variable.ref") {
      data.name = `${data.name || registry[n.type].label} copy`;
      data.fnId = mintFnId(data.name, mintId());
    }
    addNode(n.type, { x: n.position.x + 24, y: n.position.y + 24 }, data);
  }, [addNode, registry]);

  const copySelection = useCallback(() => {
    const { nodes: ns, edges: es } = stateRef.current;
    const sel = ns.filter((n) => n.selected);
    if (!sel.length) return false;
    clipboard.current = serializeSelection(ns, es);
    setHasClip(true);
    toast.info(`Copied ${sel.length} node${sel.length > 1 ? "s" : ""}`);
    return true;
  }, [toast]);

  // Drop a fragment of graph onto the canvas: fresh node ids, edges remapped
  // onto them, fresh fnIds (two nodes must never publish the same variable),
  // positions moved so the fragment's top-left lands at `at`. Both Paste and
  // Import go through here - an imported file is just a fragment from a file
  // instead of from the clipboard.
  const insertFragment = useCallback((fragNodes, fragEdges, at) => {
    const src = (fragNodes || []).filter((n) => n && registry[n.type]);
    if (!src.length) return 0;
    const { nodes: ns, edges: es } = stateRef.current;
    history.push(ns, es);
    const posOf = (n) => ({ x: +(n.position?.x ?? 0) || 0, y: +(n.position?.y ?? 0) || 0 });
    const minX = Math.min(...src.map((n) => posOf(n).x));
    const minY = Math.min(...src.map((n) => posOf(n).y));
    const base = at || { x: minX + 24, y: minY + 24 };
    const idMap = {};
    const newNodes = src.map((n) => {
      const id = mintId();
      idMap[n.id] = id;
      const spec = registry[n.type] || {};
      const data = { ...(n.data || {}) };
      if ((spec.data || {}).fnId !== undefined) {
        // A pasted Variable node still just holds a value -- two copies of it
        // point at the same key, which is fine and needs no new id or name.
        if (n.type === "variable.ref") delete data.fnId;
        else {
          data.name = data.name || spec.label;
          data.fnId = mintFnId(data.name, id);
        }
      }
      const p = posOf(n);
      return { id, type: n.type, data,
               position: { x: base.x + (p.x - minX), y: base.y + (p.y - minY) },
               selected: true };
    });
    const newEdges = (fragEdges || [])
      .filter((e) => e && idMap[e.source] && idMap[e.target])
      .map((e) => ({ id: `e_${mintId()}`,
                     source: idMap[e.source], target: idMap[e.target],
                     sourceHandle: e.sourceHandle ?? null,
                     targetHandle: e.targetHandle ?? null }));
    setNodes((cur) => [...cur.map((n) => ({ ...n, selected: false })), ...newNodes]);
    setEdges((cur) => [...cur, ...newEdges]);
    scheduleSave();
    return newNodes.length;
  }, [history, registry, scheduleSave]);

  const paste = useCallback((at) => {
    const clip = clipboard.current;
    if (!clip || !clip.nodes?.length) return;
    insertFragment(clip.nodes, clip.edges, at);
  }, [insertFragment]);

  // Cut is copy-then-delete, in that order: if the copy finds nothing selected
  // there is nothing to delete either.
  const cutSelection = useCallback(() => {
    const { nodes: ns, edges: es } = stateRef.current;
    if (!copySelection()) return;
    history.push(ns, es);
    const ids = new Set(ns.filter((n) => n.selected).map((n) => n.id));
    setNodes((cur) => cur.filter((n) => !ids.has(n.id)));
    setEdges((cur) => cur.filter((e) => !ids.has(e.source) && !ids.has(e.target)));
    setSelectedId(null);
    scheduleSave();
  }, [copySelection, history, scheduleSave]);

  /** Delete these node ids and every wire touching them. Shared by the
   *  context menu, the Delete key and the drag-to-bin gesture -- one undo
   *  entry, one save, whichever way the operator asked. */
  const deleteNodes = useCallback((ids) => {
    const gone = new Set(ids || []);
    if (!gone.size) return;
    const { nodes: ns, edges: es } = stateRef.current;
    history.push(ns, es);
    setNodes((cur) => cur.filter((n) => !gone.has(n.id)));
    setEdges((cur) => cur.filter((e) => !gone.has(e.source) && !gone.has(e.target)));
    setSelectedId(null);
    scheduleSave();
  }, [history, scheduleSave]);

  const deleteSelection = useCallback(() => {
    const { nodes: ns, edges: es } = stateRef.current;
    const ids = new Set(ns.filter((n) => n.selected).map((n) => n.id));
    const edgeIds = new Set(es.filter((e) => e.selected).map((e) => e.id));
    if (!ids.size && !edgeIds.size) return;
    history.push(ns, es);
    setNodes((cur) => cur.filter((n) => !ids.has(n.id)));
    setEdges((cur) => cur.filter((e) => !ids.has(e.source) && !ids.has(e.target)
                                        && !edgeIds.has(e.id)));
    setSelectedId(null);
    scheduleSave();
  }, [history, scheduleSave]);

  const selectAll = useCallback(() => {
    setNodes((cur) => cur.map((n) => ({ ...n, selected: true })));
  }, []);

  // -- export / import -------------------------------------------------------
  // The 4s poll refreshes `variables` on its own; this is for right after a
  // write, so a freshly imported zone appears in the palette immediately
  // instead of up to four seconds later.
  const refreshVariables = useCallback(() => {
    api.get("/api/workflow")
      .then((d) => setMeta((m) => (m ? { ...m, variables: d.variables } : m)))
      .catch(() => {});
  }, []);

  const doExport = useCallback(() => {
    const { nodes: ns, edges: es } = stateRef.current;
    const frag = serializeSelection(ns, es);
    if (!frag.nodes.length) { toast.info("Nothing to export - the canvas is empty"); return; }
    // A Zone node is a REFERENCE to a shape drawn on another page. Exported
    // alone it lands on the next system pointing at a zone that does not
    // exist, so the shapes travel with it: id, kind and the normalised points,
    // which is everything needed to re-draw the same area on any camera.
    const wanted = new Set(frag.nodes
      .filter((n) => n.type === "zone.ref" && (n.data || {}).zone)
      .map((n) => n.data.zone));
    const zones = (meta?.variables?.zone_list || []).filter((z) => wanted.has(z.id))
      .map((z) => ({ id: z.id, name: z.name, kind: z.kind, points: z.points,
                     match: z.match || "", color: z.color || "",
                     violation: !!z.violation, enabled: z.enabled !== false }));
    const payload = { app: FILE_APP, version: FILE_VERSION,
                      nodes: frag.nodes, edges: frag.edges, zones };
    const blob = new Blob([JSON.stringify(payload, null, 2)],
                          { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    // No name in the filename: a system has one workflow, so it only ever
    // read "workflow-workflow-<date>".
    a.download = `workflow-${todayStamp()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${frag.nodes.length} node${frag.nodes.length > 1 ? "s" : ""}`,
                  zones.length ? `${zones.length} zone shape${zones.length > 1 ? "s" : ""} `
                    + "travel with it." : undefined);
  }, [toast, meta]);

  /** Re-draw imported zone shapes on THIS system. Returns {id: localId} for
   *  remapping the imported nodes, or null when the write failed.
   *
   *  Never touches a zone that already exists: an id that is taken by a
   *  DIFFERENT shape gets a fresh id instead, so importing can add work but can
   *  never silently move an area somebody is already relying on. */
  const importZones = useCallback(async (incoming) => {
    const existing = meta?.variables?.zone_list || [];
    const byId = new Map(existing.map((z) => [z.id, z]));
    const same = (a, b) => a.kind === b.kind
      && JSON.stringify(a.points || []) === JSON.stringify(b.points || []);
    const remap = {};
    const created = [];
    const taken = new Set(existing.map((z) => z.id));
    for (const z of incoming) {
      if (!z || !z.id || !Array.isArray(z.points) || z.points.length < 2) continue;
      const local = byId.get(z.id);
      if (local && same(local, z)) { remap[z.id] = z.id; continue; }
      let id = z.id;
      if (taken.has(id)) {
        let i = 2;
        while (taken.has(`${id}_${i}`)) i += 1;
        id = `${id}_${i}`;
      }
      taken.add(id);
      remap[z.id] = id;
      created.push({ ...z, id, name: id === z.id ? z.name : `${z.name} (imported)` });
    }
    if (!created.length) return remap;
    try {
      await api.post("/api/detector/control/zones",
                     { zones: [...existing, ...created] });
      toast.success(`Added ${created.length} zone${created.length > 1 ? "s" : ""}`,
                    "Drawn from the file - open Zones to adjust the shapes to this camera.");
      return remap;
    } catch (e) {
      toast.error("Could not add the zones", e.message);
      return null;
    }
  }, [meta, toast]);

  const doImport = useCallback(async (raw) => {
    let payload = null;
    try {
      payload = JSON.parse(raw);
    } catch {
      toast.error("Could not read that", "It is not valid JSON.");
      return;
    }
    if (!payload || typeof payload !== "object" || payload.app !== FILE_APP) {
      toast.error("Not a workflow file",
                  `Expected a JSON object with "app": "${FILE_APP}".`);
      return;
    }
    const inNodes = Array.isArray(payload.nodes) ? payload.nodes : [];
    const inEdges = Array.isArray(payload.edges) ? payload.edges : [];
    if (inNodes.length > MAX_IMPORT_NODES || inEdges.length > MAX_IMPORT_EDGES) {
      toast.error("That file is too big",
                  `Up to ${MAX_IMPORT_NODES} nodes and ${MAX_IMPORT_EDGES} edges - `
                  + `this one has ${inNodes.length} and ${inEdges.length}.`);
      return;
    }
    const good = inNodes.filter((n) => n && registry[n.type]);
    const skipped = inNodes.length - good.length;
    if (!good.length) {
      toast.error("Nothing to import",
                  "None of the nodes in that file are node types this system knows.");
      return;
    }
    // Zone shapes first: the nodes must point at zones that already exist, or
    // the graph compiles to "this Zone node points at a zone that no longer
    // exists" the moment it lands.
    let placed = good;
    const incomingZones = Array.isArray(payload.zones) ? payload.zones : [];
    if (incomingZones.length) {
      const remap = await importZones(incomingZones);
      if (!remap) return;                       // the zone write failed; import nothing
      placed = good.map((n) => (n.type === "zone.ref" && (n.data || {}).zone
        ? { ...n, data: { ...n.data, zone: remap[n.data.zone] || n.data.zone } }
        : n));
    }
    const rect = wrapperRef.current?.getBoundingClientRect();
    const at = rect
      ? rf.screenToFlowPosition({ x: rect.left + rect.width / 2,
                                  y: rect.top + rect.height / 2 })
      : null;
    const added = insertFragment(placed, inEdges, at);
    if (skipped) {
      toast.info(`${skipped} unknown node${skipped > 1 ? "s" : ""} skipped`,
                 "They are node types this system does not have.");
    }
    toast.success(`Imported ${added} node${added > 1 ? "s" : ""}`);
    setImportOpen(false);
    setImportText("");
    refreshVariables();                         // pick up the new zone list
  }, [registry, rf, insertFragment, importZones, toast, refreshVariables]);

  // -- keyboard --------------------------------------------------------------
  useEffect(() => {
    function onKey(e) {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT"
                || t.isContentEditable)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        const { nodes: ns, edges: es } = stateRef.current;
        const prev = history.undo(ns, es);
        if (prev) { setNodes(prev.nodes); setEdges(prev.edges); scheduleSave(); }
      } else if (mod && (e.key === "y" || (e.key === "z" && e.shiftKey))) {
        e.preventDefault();
        const { nodes: ns, edges: es } = stateRef.current;
        const next = history.redo(ns, es);
        if (next) { setNodes(next.nodes); setEdges(next.edges); scheduleSave(); }
      } else if (mod && e.key === "c") {
        copySelection();
      } else if (mod && e.key === "x") {
        e.preventDefault();
        cutSelection();
      } else if (mod && e.key === "v") {
        paste(null);
      } else if (mod && e.key === "a") {
        e.preventDefault();
        selectAll();
      } else if (mod && e.key === "s") {
        e.preventDefault();
        clearTimeout(saveTimer.current);
        doSave();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [history, copySelection, cutSelection, paste, selectAll, doSave, scheduleSave]);

  // Every function DEFINED on this canvas: the name a Start declares and the
  // parameters it hands out. A function is identified by NAME rather than by
  // node id, so copying a Start/End pair and renaming it makes a second
  // function, and a Call names the one it means in words an operator typed.
  const fnDefs = useMemo(() => {
    const out = {};
    for (const n of nodes) {
      if (n.type !== "function.start") continue;
      const name = String(n.data?.fn || "").trim();
      if (!name) continue;
      // An END is what closes the bracket. Without one there is no body,
      // which the Start's own face says rather than compiling to nothing --
      // and it is also where the function's RETURNS are named, which is why
      // a Call reads them from here rather than from the Start.
      const end = nodes.find((m) => m.type === "function.end"
        && String(m.data?.fn || "").trim().toLowerCase() === name.toLowerCase());
      out[name.toLowerCase()] = {
        name, params: (n.data?.params || []).filter(Boolean),
        closed: !!end,
        returns: (end?.data?.returns || []).filter(Boolean),
      };
    }
    return out;
  }, [nodes]);

  // How big each function is and how often it is used: "3 nodes · called by
  // 1 Call" on the frame, "3 nodes" on every Call of it.
  const fnStats = useMemo(() => {
    const out = {};
    const frames = nodes.filter((n) => n.type === FN_FRAME);
    const membership = frames.length ? frameMembership(nodes) : {};
    for (const f of frames) {
      const k = fnKey(f);
      if (!k) continue;
      out[k] = { nodes: Object.values(membership).filter((fid) => fid === f.id).length,
                 calls: 0, frameId: f.id };
    }
    for (const n of nodes) {
      if (n.type !== FN_CALL) continue;
      const k = fnKey(n);
      if (out[k]) out[k].calls += 1;
    }
    return out;
  }, [nodes]);

  // KEEP EVERY FRAME HONEST. After any change: an old Start / End pair with
  // no frame gets one drawn around it; every frame's Start and End snap back
  // to its edges; `members` is rewritten to what is inside it now, which is
  // what the server folds into the body. Converges: a second pass over its
  // own output changes nothing, so it never loops.
  useEffect(() => {
    if (!registry || !registry[FN_FRAME]) return;
    if (!nodes.some((n) => n.type === FN_START || n.type === FN_FRAME
                           || n.type === GROUP)) return;
    const added = editable ? legacyFrames(nodes, edges, mintId) : [];
    const base = added.length ? [...added, ...nodes] : nodes;
    const next = fitGroups(syncFrames(seedGroups(base)));
    if (next === nodes) return;
    // Only membership and pin positions changed -- nothing worth an undo
    // step, but the members ARE what the server reads, so save.
    // Membership, pins and group sizes changed -- nothing worth an undo step,
    // but members ARE what the server reads and a grown group is a new size,
    // so save when a frame or group itself changed.
    const before = new Map(nodes.map((n) => [n.id, n]));
    const membersMoved = next.some((n) => (n.type === FN_FRAME || n.type === GROUP)
      && before.get(n.id) !== n);
    setNodes(next);
    if (editable && (added.length || membersMoved) && !dragSnap.current) scheduleSave();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, registry, editable]);

  // OPEN a function from one of its Calls: unfold the frame, select it, and
  // bring it into view. Edit it once and every Call of it changes.
  const openFunction = useCallback((name) => {
    const k = String(name || "").trim().toLowerCase();
    const frame = stateRef.current.nodes.find((n) => n.type === FN_FRAME && fnKey(n) === k);
    if (!frame) return;
    setNodes((cur) => cur.map((n) => (n.id === frame.id
      ? { ...n, selected: true, data: { ...n.data, collapsed: false } }
      : { ...n, selected: false })));
    setSelectedId(frame.id);
    setInspectId(frame.id);
    const d = frame.data || {};
    rf.fitBounds({ x: frame.position.x, y: frame.position.y,
                   width: Number(d.width) || 760, height: Number(d.height) || 340 },
                 { padding: 0.15, duration: 300 });
    if (d.collapsed) scheduleSave();
  }, [rf, scheduleSave]);

  // The frame inspector edits its Start (parameters) and End (returns):
  // those two nodes are still where a function's shape lives.
  const setFnData = useCallback((frameId, type, fields) => {
    const { nodes: ns, edges: es } = stateRef.current;
    const frame = ns.find((n) => n.id === frameId);
    if (!frame) return;
    history.push(ns, es);
    setNodes((cur) => cur.map((n) => (n.type === type && fnKey(n) === fnKey(frame)
      ? { ...n, data: { ...n.data, ...fields } } : n)));
    scheduleSave();
  }, [history, scheduleSave]);

  // A Custom Formula's wire follows the NAME its expression uses.
  //
  // Clicking a name under "Wired in - use these names" (or typing one) grows
  // a port for that name -- and the wire that already carries the value was
  // left sitting on the plain `any` port. So the same value was in two places
  // at once on one node's face, with the named port looking unwired and the
  // unnamed one looking like it fed something anonymous. The wire moves
  // itself instead: an unnamed wire whose source answers to a name the
  // expression mentions is retargeted onto that name's port.
  //
  // It converges -- a moved wire has a handle and is skipped next time -- and
  // it never steals a port something else already occupies.
  useEffect(() => {
    if (!editable || !registry) return;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const moves = new Map();
    for (const n of nodes) {
      if (n.type !== "analytics.expr") continue;
      const names = exprNames(n.data?.expr);
      if (!names.length) continue;
      for (const e of edges) {
        if (e.target !== n.id) continue;
        const held = String(e.targetHandle || "");
        if (held && held !== "in") continue;
        const alias = inputAlias(byId.get(e.source), registry);
        if (!alias || !names.includes(alias.name)) continue;
        const slot = `p_${alias.name}`;
        if (edges.some((x) => x.target === n.id && x.targetHandle === slot)) continue;
        moves.set(e.id, slot);
      }
    }
    if (!moves.size) return;
    setEdges((cur) => cur.map((e) => (moves.has(e.id)
      ? { ...e, targetHandle: moves.get(e.id) } : e)));
    scheduleSave();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, registry, editable]);

  // -- palette drag-drop -----------------------------------------------------
  const onDrop = useCallback((evt) => {
    evt.preventDefault();
    const raw = evt.dataTransfer.getData("application/x-wf-node");
    if (!raw) return;
    const { type, zone, data } = JSON.parse(raw);
    const position = rf.screenToFlowPosition({ x: evt.clientX, y: evt.clientY });
    const extra = data || (zone ? { zone } : undefined);
    // A saved query carries the tables it reads (`_tables`, never node data):
    // it arrives wired rather than as a statement with nothing to read.
    if (type === "storage.query" && extra && extra._tables) {
      addSavedQuery(position, extra);
      return;
    }
    // A function is a PAIR. Dragging the Start alone would leave a bracket
    // with nothing to close it -- and an unclosed function has no body, so it
    // compiles to nothing while looking finished. Both arrive named the same,
    // far enough apart to wire between.
    if (type === "function.start" || type === FN_FRAME) {
      addFunctionFrame(position);
      return;
    }
    addNode(type, position, extra);
  }, [rf, addNode, addSavedQuery, addFunctionFrame]);

  // -- context menu ----------------------------------------------------------
  // The right button both pans (drag) and opens this menu (click), so a menu is
  // only honest if the pointer barely moved since the button went down.
  const openMenu = useCallback((evt, variant, nodeId) => {
    evt.preventDefault();
    const down = rightDown.current;
    rightDown.current = null;
    if (down && (Math.abs(evt.clientX - down.x) > 5
                 || Math.abs(evt.clientY - down.y) > 5)) return;
    // Right-clicking a node SELECTS it, the way every editor does. Without
    // this the menu opened on a node nothing had selected, so Copy, Cut and
    // Delete were greyed out on the very node that was right-clicked -- the
    // menu looked broken. An existing multi-selection that already contains
    // this node is left alone, so right-clicking one of five keeps all five.
    if (nodeId) {
      const { nodes: ns } = stateRef.current;
      const already = ns.some((n) => n.id === nodeId && n.selected);
      if (!already) {
        setNodes((cur) => cur.map((n) => (n.selected === (n.id === nodeId)
          ? n : { ...n, selected: n.id === nodeId })));
        setEdges((cur) => cur.map((e) => (e.selected ? { ...e, selected: false } : e)));
        setSelectedId(nodeId);
      }
    }
    const rect = wrapperRef.current?.getBoundingClientRect();
    if (!rect) return;
    setMenu({ x: evt.clientX - rect.left, y: evt.clientY - rect.top,
              variant, nodeId: nodeId || null,
              flowPos: rf.screenToFlowPosition({ x: evt.clientX, y: evt.clientY }) });
  }, [rf]);

  // -- palette resize --------------------------------------------------------
  const startPaletteResize = useCallback((evt) => {
    evt.preventDefault();
    paletteDrag.current = { x0: evt.clientX, w0: paletteWRef.current, moved: false };
    evt.currentTarget.setPointerCapture?.(evt.pointerId);
    document.body.style.userSelect = "none";
  }, []);
  const movePaletteResize = useCallback((evt) => {
    const drag = paletteDrag.current;
    if (!drag) return;
    if (evt.buttons !== 1) { paletteDrag.current = null; document.body.style.userSelect = ""; return; }
    if (Math.abs(evt.clientX - drag.x0) > 3) drag.moved = true;
    // Dragged far enough left, the node list shuts (width 0); dragged back, it opens again.
    const raw = drag.w0 + (evt.clientX - drag.x0);
    const w = raw < 130 ? 0 : Math.max(200, Math.min(480, raw));
    paletteWRef.current = w;
    setPaletteW(w);
  }, []);
  const endPaletteResize = useCallback((evt) => {
    if (!paletteDrag.current) return;
    // A click (no drag) on the handle of a closed node list opens it.
    if (!paletteDrag.current.moved && paletteWRef.current === 0) {
      paletteWRef.current = 240;
      setPaletteW(240);
    }
    paletteDrag.current = null;
    document.body.style.userSelect = "";
    evt.currentTarget.releasePointerCapture?.(evt.pointerId);
    localStorage.setItem("wf.palette.w", String(paletteWRef.current));
  }, []);

  // -- inspector resize (mirror of the palette, dragging its LEFT edge, so
  //    moving the handle left GROWS the panel) ------------------------------
  const startInspectorResize = useCallback((evt) => {
    evt.preventDefault();
    autoOpened.current = false;
    // From the folded edge (guide hidden, nothing open) the drag grows the
    // column out of nothing, and showing it at all means showing the guide.
    const fromEdge = rightCollapsedRef.current;
    inspectorDrag.current = { x0: evt.clientX, w0: fromEdge ? 0 : inspectorWRef.current,
                              moved: false, fromEdge };
    evt.currentTarget.setPointerCapture?.(evt.pointerId);
    document.body.style.userSelect = "none";
  }, []);
  const moveInspectorResize = useCallback((evt) => {
    const drag = inspectorDrag.current;
    if (!drag) return;
    // Held button only: a drag that missed its pointer-up must not follow a hovering pointer.
    if (evt.buttons !== 1) { inspectorDrag.current = null; document.body.style.userSelect = ""; return; }
    if (Math.abs(evt.clientX - drag.x0) > 3) drag.moved = true;
    const raw = drag.w0 - (evt.clientX - drag.x0);
    const w = raw < 150 ? 0 : Math.max(240, Math.min(560, raw));
    if (drag.fromEdge) {
      if (!w) return;                 // not pulled out far enough yet: stay folded
      drag.fromEdge = false;
      setGuideHidden(false);
    }
    if (w) lastOpenW.current = w;
    inspectorWRef.current = w;
    setInspectorW(w);
  }, []);
  const endInspectorResize = useCallback((evt) => {
    if (!inspectorDrag.current) return;
    // A click (no drag) on the handle of a closed column opens it; on the
    // folded edge (guide hidden), it brings the guide back.
    const clicked = !inspectorDrag.current.moved && inspectorWRef.current === 0;
    const unfold = !inspectorDrag.current.moved && inspectorDrag.current.fromEdge;
    inspectorDrag.current = null;
    document.body.style.userSelect = "";
    evt.currentTarget.releasePointerCapture?.(evt.pointerId);
    if (unfold) { setGuideHidden(false); return; }
    if (clicked) { openRight(); return; }
    localStorage.setItem("wf.inspector.w", String(inspectorWRef.current));
  }, [openRight]);

  // -- search ----------------------------------------------------------------
  const searchLower = search.trim().toLowerCase();
  function nodeMatches(n) {
    if (!searchLower) return false;
    const spec = registry[n.type] || {};
    return (spec.label || "").toLowerCase().includes(searchLower)
      || (n.data?.name || "").toLowerCase().includes(searchLower);
  }
  function jumpToMatch() {
    const hit = stateRef.current.nodes.find(nodeMatches);
    if (hit) rf.fitView({ nodes: [{ id: hit.id }], duration: 300, maxZoom: 1.2 });
  }

  if (error) return <ErrorNote error={error} />;
  if (!meta) return <SkelWorkflow />;

  // SELECTED and INSPECTED are two different things. A single click picks a
  // node -- to move it, copy it, delete it, or just see what it is wired to --
  // and that should not throw a settings panel over the canvas every time.
  // Opening the settings is a second, deliberate gesture: a double click.
  //
  // `inspectId` follows the selection rather than standing apart from it: a
  // node that is no longer the selected one must not keep an open panel
  // editing it, or a click elsewhere leaves you changing something you can no
  // longer see.
  const selected = (() => {
    const n = nodes.find((m) => m.id === selectedId && m.id === inspectId);
    // A Start or End is the edge of its frame: its settings ARE the frame's.
    if (n && (n.type === FN_START || n.type === FN_END) && fnStats[fnKey(n)]) {
      return nodes.find((m) => m.id === fnStats[fnKey(n)].frameId) || n;
    }
    return n;
  })();
  // See `helpPref`: unanswered means open. Selecting a node still takes the
  // panel over, so the guide is never in the way of actual work -- it is what
  // the column shows when nothing else needs it.
  // Hidden for now (see the Guideline import). Re-enable: const helpOpen = helpPref === null ? true : helpPref;
  const helpOpen = false;
  function setHelp(open) {
    localStorage.setItem("wf.help.open", open ? "1" : "0");
    setHelpPref(open);
  }
  // A selected node ALWAYS gets the full, user-chosen rail; only the primer
  // collapses to its tab.
  // Guideline hidden: no tab, so the column is 0px when nothing is selected.
  // Re-enable: selected || helpOpen ? inspectorW : 34
  // The guide hidden and no node open: the column has nothing to show, so it
  // folds to the edge and its handle with it, instead of an empty column with
  // the handle stranded in the middle of the page. A click on the handle, or a
  // drag back out, brings the guide back (endInspectorResize / move).
  const rightCollapsed = guideHidden && !selected;
  rightCollapsedRef.current = rightCollapsed;
  const rightW = rightCollapsed ? 0 : inspectorW;
  const zoneList = meta.variables?.zone_list || [];
  const modelList = meta.variables?.models || [];
  const selCount = nodes.filter((n) => n.selected).length
    + edges.filter((e) => e.selected).length;
  // Which nodes have something wired IN - a few cards (recorded events) mean
  // different things wired and unwired, and a node cannot see the edge list.
  const incoming = new Set(edges.map((e) => e.target));
  // ...and HOW MANY, for the nodes whose meaning changes with the count (a
  // chart with three wires draws three series and should say so).
  const fanIn = new Map();
  for (const e of edges) fanIn.set(e.target, (fanIn.get(e.target) || 0) + 1);
  // Which ALERTS each measuring node feeds, by name. A counter and the alarm
  // it raises are one rule read as two nodes, and on a canvas of any size the
  // wire between them is the only thing that says so -- follow it off-screen
  // and the counter stops explaining what it is for. Resolved here rather
  // than in the node, which can see neither the edges nor the other nodes.
  //
  // One hop only, deliberately: "this count raises THAT alarm" is a fact
  // about the wire in front of you. Walking the whole graph would put the
  // name of an alert five nodes away on a node that does not raise it.
  const alertsBySource = new Map();
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  for (const e of edges) {
    const target = nodeById.get(e.target);
    if (!target || !["output.alert", "output.trigger"].includes(target.type)) continue;
    const name = String(target.data?.name || "").trim()
      || (target.type === "output.trigger" ? "Trigger" : "Condition");
    const list = alertsBySource.get(e.source) || [];
    if (!list.includes(name)) list.push(name);
    alertsBySource.set(e.source, list);
  }
  // Which individual PORTS have a wire on them, so a port can draw itself
  // filled or hollow. Keyed per node and side because "this node has an input
  // wired" is not the question a Formula with eight operands is asking.
  // A default source handle carries no id and its edge records sourceHandle
  // null, so "" is that port's key on both sides of this lookup.
  const wired = new Set();
  for (const e of edges) {
    wired.add(`${e.source}|s|${e.sourceHandle || ""}`);
    wired.add(`${e.target}|t|${e.targetHandle || ""}`);
  }
  // Which classes each OBJECT WATCH is watching for -- the union of every
  // Class Filter on any path back to the source, plus the node's own legacy
  // list -- so the node can read back "person: 2 / chair: 0" live.
  //
  // The two COUNTERS are in this walk as well, though they grow no per-class
  // ports from it (AnalyticsNode gates those on node type). They need it to
  // say what they are counting: "count person inside the zone" is the whole
  // difference between a node that explains itself and one that reads as a
  // number from nowhere.
  const objClasses = (() => {
    const out = new Map();
    const WATCHERS = new Set(["analytics.object", "analytics.object_frame",
                              "analytics.object_zone", "analytics.counter",
                              "analytics.frame", "analytics.violations",
                              // ...and the Counter while it counts a line,
                              // so its face says WHAT crosses, too.
                              "analytics.entry_exit"]);
    for (const n of nodes) {
      if (!WATCHERS.has(n.type)) continue;
      // classesFor (workflow/registry.js) is the one walk; the inspector reads
      // the same one, so a node's face and the panel that edits it cannot
      // disagree about what it is watching.
      const found = classesFor(nodes, edges, n.id);
      if (found.length) out.set(n.id, found);
    }
    return out;
  })();
  // -- what a node's FACE reads, when it is not the node's own fnId ----------
  //
  // Two nodes need the GRAPH to answer "which live value am I showing", and a
  // node component can see neither the edges nor the other nodes:
  //
  //   A Set Variable publishes as the VARIABLE wired into its input a, never
  //   under its own fnId (_set_target_fid in core/services/workflow.py). Its
  //   footer read fn_set_variable_xxxx_value, which nothing has ever
  //   published, so every Set Variable on every box showed "= ..." for ever
  //   while the variable beside it counted perfectly.
  //
  //   A Debug probe shows whatever is wired INTO it. The server cannot help:
  //   the whole "common" category is deleted before the graph is validated,
  //   so the probe's wire is resolved here or nowhere.
  //
  // One map because it is one question. `kind` says how to read the answer:
  //   value    key is a stat key in the live values map
  //   output   id is an output document, whose out_<id>_* stats say whether
  //            it fired - an Email publishes no reading, only a fate
  //   table    a Data Table column, read back from the database by a card or
  //            a query and never present in the stats dict
  //   none     nothing to read, and `why` says so in words
  const faceReads = (() => {
    const out = new Map();
    const preds = new Map();
    for (const e of edges) {
      const list = preds.get(e.target) || [];
      list.push(e);
      preds.set(e.target, list);
    }
    // The variable a Set Variable writes: whatever is on its input `a`.
    // Mirrors _set_target_fid -- a reader Variable resolves to the key it
    // holds, a constant to its own fnId, and anything else to nothing, which
    // is the same case the compiler reports as "wire the Variable to set
    // into input a".
    const setTarget = (id) => {
      for (const e of preds.get(id) || []) {
        if (String(e.targetHandle || "a") !== "a") continue;
        const src = nodeById.get(e.source);
        const sd = (src || {}).data || {};
        if (src?.type === "variable.ref") {
          const key = String(sd.key || "");
          if (key.startsWith("fn_") && key.endsWith("_value")) {
            return { key, label: sd.name || key };
          }
        }
        if (src?.type === "variable.number" && slug(sd.fnId)) {
          return { key: `fn_${slug(sd.fnId)}_value`, label: sd.name || sd.fnId };
        }
      }
      return null;
    };
    // FIRST, so a probe wired to one can borrow the answer below: a Set
    // Variable's key is not its own, and resolving it twice would be two
    // mirrors of _set_target_fid to keep in step instead of one.
    for (const n of nodes) {
      if (n.type !== "math.setvar") continue;
      const t = setTarget(n.id);
      out.set(n.id, t ? { kind: "value", key: t.key, label: t.label }
                      : { kind: "none",
                          why: "wire the Variable to set into a" });
    }
    // The name of the node a probe is reading, for the line under the value:
    // "which of my six counters is this" is most of the question.
    const nameOf = (nd) => String(nd.data?.name || "").trim()
      || (registry[nd.type] || {}).label || nd.type;
    // A CONDITION publishes no value of its own -- it is a trigger ON the
    // rule wired before it -- so the reading is that rule's `active`, 1 while
    // it holds. Mirrors _condition_alias, one hop at a time back through the
    // transparent nodes. Kept short on purpose: a probe says what the wire in
    // front of it carries, not what a walk five nodes away found.
    //
    // The rule BEHIND a Trigger is a branch port far more often than a rule
    // wired straight in (the compiler refuses a measurement into a Trigger:
    // "put a Compare between"), so a branch resolves here to fn_<base>_b<n>
    // exactly as the branch case below does. And the zone and OID ports name
    // a place and a who, not the moment: walking the OID wire found the
    // detection rule and read ITS `active`, which nothing publishes (the
    // moment belongs to the branch), so a probe on a Trigger with OID wired
    // said "waiting" for ever (Tok Aba, 2026-10-05, v1.0.16).
    const CONDITIONS = ["output.alert", "output.event", "output.trigger",
                        "logic.when", "logic.delay", "logic.catch"];
    const ruleBehind = (nd, hops = 0) => {
      if (hops > 4) return null;
      for (const e of preds.get(nd.id) || []) {
        // ...and the duplicate guard's ports read the table, not the moment.
        if (["zone", "oid", "cur_oid", "cur_value"].includes(String(e.targetHandle || ""))) continue;
        const p = nodeById.get(e.source);
        if (!p) continue;
        const alias = inputAlias(p, registry);
        if (alias) return alias;
        const base = isBranch(p.type)
          && (slug(p.data?.fnId) || slug(p.data?.name));
        const way = /^out(\d+)$/.exec(String(e.sourceHandle || "out0"));
        if (base && way) {
          const fid = `${base}_b${Number(way[1])}`.slice(0, 40);
          return { name: fid, label: nameOf(p), key: `fn_${fid}_value` };
        }
        if (CONDITIONS.includes(p.type)) {
          const deeper = ruleBehind(p, hops + 1);
          if (deeper) return deeper;
        }
      }
      return null;
    };
    for (const n of nodes) {
      if (n.type !== "common.debug") continue;
      const wire = (preds.get(n.id) || [])[0];
      const src = wire && nodeById.get(wire.source);
      if (!src) {
        out.set(n.id, { kind: "none", why: "wire something into this probe" });
        continue;
      }
      const spec = registry[src.type] || {};
      const name = nameOf(src);
      // An ON / OFF point: 1 while the AI (or the filter) is in that state,
      // published by the hidden point the server compiles the wire into.
      if (isStatePoint(src.type, wire.sourceHandle)) {
        const want = String(wire.sourceHandle);
        out.set(n.id, { kind: "value",
                        key: `fn_${statePointFn(src.id, want)}_value`,
                        label: `${name} - ${want.toUpperCase()}`,
                        node: src.id });
        continue;
      }
      // A Data Table's read ports hand back ROWS, out of the database. No
      // stat exists behind them at all, so a probe drawing a dash there would
      // read as "nothing is happening" on a table that is filling nicely.
      if (src.type === "output.dataset") {
        out.set(n.id, { kind: "table", label: name });
        continue;
      }
      if (CONDITIONS.includes(src.type)) {
        const behind = ruleBehind(src);
        if (!behind) {
          out.set(n.id, { kind: "none", why: `nothing is wired into ${name}` });
        } else if (src.type === "output.trigger" && triggerAdvanced(src.data)) {
          // An Advanced Trigger SENDS something: its payload while it fires
          // (and for its hold), its idle payload between -- published as
          // fn_<rule>_out (trigger_out.stamp_out_keys), which is what every
          // card and output wired after it reads. The rule's `active` is
          // the moment, not the payload, and "holding" is the wrong word
          // for a node whose reading is 0 or 17.
          out.set(n.id, { kind: "value", key: `fn_${behind.name}_out`,
                          label: name, node: src.id });
        } else {
          out.set(n.id, { kind: "value", key: `fn_${behind.name}_active`,
                          label: `${name} - holding`, node: src.id });
        }
        continue;
      }
      if (spec.category === "output") {
        // Mirrors the compiler, which ids every output document
        // f"wf_{node id}"[:40] - the truncation included, or a probe on a
        // node inside a long-named Function would read a key nobody writes.
        out.set(n.id, { kind: "output", id: `wf_${src.id}`.slice(0, 40),
                        label: name });
        continue;
      }
      // A Set Variable answers with the variable it writes, for the same
      // reason its own footer does.
      const own = out.get(src.id);
      if (src.type === "math.setvar" && own) {
        out.set(n.id, own.kind === "value" ? { ...own }
          : { kind: "none", why: `${name} is not writing a variable yet` });
        continue;
      }
      // A BRANCH publishes one 0/1 rule per way out, so WHICH PORT the wire
      // left by is the whole answer. Mirrors branch_alias over switch_bases.
      // The collision suffix switch_bases appends when two branches slug the
      // same is not mirrored: it depends on node order inside the compile,
      // and a probe that silently read the other branch's key would be worse
      // than one that says it cannot tell. Two same-named branches therefore
      // fall through to "publishes nothing to read".
      const base = isBranch(src.type)
        && (slug(src.data?.fnId) || slug(src.data?.name));
      const way = /^out(\d+)$/.exec(String(wire.sourceHandle || "out0"));
      if (base && way) {
        const fid = `${base}_b${Number(way[1])}`.slice(0, 40);
        out.set(n.id, { kind: "value", key: `fn_${fid}_value`,
                        label: `${name} - way out ${Number(way[1]) + 1}`,
                        node: src.id });
        continue;
      }
      const alias = inputAlias(src, registry);
      out.set(n.id, alias
        ? { kind: "value", key: alias.key, label: alias.label, node: src.id }
        : { kind: "none", why: `${name} publishes nothing to read` });
    }
    return out;
  })();
  // -- which NODE published a compiled function ------------------------------
  //
  // Debug matches its rows to nodes on node.data.fnId. Two kinds of node
  // publish under an id that is not their fnId, and for both of them the
  // panel could never find the node: a Set Variable's rule takes the name of
  // the VARIABLE it writes, and a Variable fed by a wire publishes under its
  // own name and has no fnId at all. The consequence was silent -- a heading
  // with a dead jump arrow, and a star on the node that pinned nothing.
  const fnOwners = (() => {
    const out = {};
    for (const n of nodes) {
      if (n.type === "math.setvar") {
        const k = String((faceReads.get(n.id) || {}).key || "");
        if (k.startsWith("fn_") && k.endsWith("_value")) {
          out[k.slice(3, -6)] = n.id;
        }
        continue;
      }
      // A STORE variable: wired in, and not explicitly declared read-only.
      // Same test the node's own face uses, and the same one the compiler
      // stamps _wired with.
      if (n.type === "variable.ref" && slug(n.data?.name)
          && String(n.data?.io || "auto").toLowerCase() !== "in"
          && incoming.has(n.id)) {
        out[slug(n.data.name)] = n.id;
      }
    }
    return out;
  })();
  const offNodes = offNodeIds(warnings, edges, nodes);
  // A folded function hides its Start, End and body; React Flow then hides
  // every wire touching them too.
  const folded = hiddenByFrames(nodes);
  const shown = nodes.map((n) => {
    // THE FRAME sits behind everything, below even a Comment, and only its
    // title bar drags it (the rest of it lets the pointer through -- see
    // FunctionFrameNode). Its Start and End are pinned: not draggable, not
    // deletable on their own.
    if (n.type === GROUP) {
      return { ...n, zIndex: -3, dragHandle: ".group-drag",
               style: { ...(n.style || {}), pointerEvents: "none" } };
    }
    if (n.type === FN_FRAME) {
      return { ...n, zIndex: -2, dragHandle: ".fn-frame-drag",
               style: { ...(n.style || {}), pointerEvents: "none" } };
    }
    if (folded.has(n.id)) return { ...n, hidden: true };
    if ((n.type === FN_START || n.type === FN_END) && fnStats[fnKey(n)]) {
      return { ...n, draggable: false, deletable: false };
    }
    const marked = searchLower && nodeMatches(n)
      ? { ...n, style: { ...n.style, outline: "2px solid var(--accent, #04bade)", borderRadius: 12 } }
      : { ...n, style: n.style && n.style.outline ? {} : n.style };
    // A COMMENT sits BEHIND the nodes it explains unless its Layer says
    // otherwise (and every note saved before that option existed sits
    // behind too): a note is background for the part of the graph it
    // describes, and a big one drawn over three nodes swallowed their
    // clicks. ReactFlow elevates a SELECTED node by 1000, so a note comes
    // to the front while it is being written or resized and drops back the
    // moment it is deselected.
    if (n.type === "note.comment" && (n.data || {}).layer !== "front") {
      return { ...marked, zIndex: -1 };
    }
    return marked;
  });
  // A wire INTO a node that is not running is drawn as a dashed grey line:
  // nothing flows down it, and a solid wire into a dim node reads as
  // "connected but broken" instead of "switched off". The edge itself is
  // untouched - flipping the switch back on restores it, so a capture switch
  // can never cost an operator their wiring.
  // A wire with a PROBLEM gets a red X at its midpoint (ProblemEdge), the
  // reason on hover: a wire the rules no longer allow (drawn before a rule
  // changed, or crossing into a function), or a wire touching a node the
  // compiler rejected. A warning on the node it feeds marks it amber.
  const fnMembership = frameMembership(nodes);
  const fnPinsMap = framePins(nodes);
  const wireProblem = (e) => {
    const src = nodeById.get(e.source), tgt = nodeById.get(e.target);
    if (!src || !tgt || !registry) return null;
    if (e.targetHandle === "link_in" || e.sourceHandle === "link_out") return null;
    if (!isStatePoint(src.type, e.sourceHandle)) {
      const [ok, why] = edgeAllowed(registry, src.type, tgt.type, e.targetHandle);
      if (!ok) return { level: "error", reason: why || "this wire is not allowed" };
    }
    const fa = frameOf(src.id, nodes, fnMembership, fnPinsMap);
    const fb = frameOf(tgt.id, nodes, fnMembership, fnPinsMap);
    if (fa !== fb) {
      return { level: "error",
               reason: "this wire crosses a function frame - a node inside a function only connects to its Start, End or other nodes in the same frame" };
    }
    if (errors[tgt.id]) return { level: "error", reason: errors[tgt.id] };
    if (errors[src.id]) return { level: "error", reason: errors[src.id] };
    if (warnings[tgt.id] && !offNodes.has(tgt.id)) {
      return { level: "warn", reason: warnings[tgt.id] };
    }
    return null;
  };
  const shownEdges = edges.map((e) => {
    const bad = wireProblem(e);
    if (bad) {
      const hot = e.id === hoveredEdgeId || e.selected;
      return { ...e, type: "problem", animated: false, data: { ...(e.data || {}), ...bad },
               style: hot ? { stroke: "#2f8fff", strokeWidth: 3 } : undefined };
    }
    // A LINK wire is an association, not a flow: dotted, accent-coloured,
    // never animated -- deliberately unlike the 5-5 grey dash a switched-off
    // branch gets, because "tied together" and "not running" must never
    // read as the same line.
    if ((e.targetHandle === "link_in") || (e.sourceHandle === "link_out")) {
      return { ...e, animated: false,
               style: { ...e.style, stroke: "var(--accent, #04bade)",
                        strokeWidth: 1.5, strokeDasharray: "2 7",
                        strokeLinecap: "round", opacity: 0.9 } };
    }
    const base = offNodes.has(e.target)
      ? { ...e, animated: false,
          style: { ...e.style, stroke: "rgba(148,163,184,.45)",
                   strokeDasharray: "5 5" } }
      // A live wire FLOWS, the way the guideline draws it: the dash moving
      // from source to target says which way the value goes.
      // The ink is stated per edge, not left to defaultEdgeOptions: a hover
      // fade replaces the edge's style object, which threw the default away
      // and left a faint grey dash on the light canvas. Black on light,
      // white on dark.
      : { ...e, animated: true,
          style: { ...(e.style || {}), strokeDasharray: undefined,
                   stroke: isDarkCanvas ? "#FFFFFF" : "#111111" } };
    // Hovered or selected: which wire is this, answered at the cursor. A wire
    // into a switched-off node still highlights -- inspecting a dashed wire
    // matters at least as much as a live one.
    //
    // A fixed bright blue, not var(--accent): the accent is a per-deployment
    // brand colour injected from brand.toml (state/app.jsx), so it is not
    // guaranteed to READ as blue at all -- a brand set to amber or green
    // would highlight the wire in amber or green. "Which wire did I just
    // click" wants one unmistakable colour every deployment agrees on.
    if (e.id === hoveredEdgeId || e.selected) {
      return { ...base, style: { ...base.style, stroke: "#2f8fff",
                                 strokeWidth: 3, strokeOpacity: 1 } };
    }
    // Hovering a NODE lights its wires and fades everything else. Fading the
    // rest rather than only brightening these is what makes it readable: on a
    // graph where wires cross, a brighter line among a dozen equally bright
    // ones is still a line to be traced.
    if (hoveredNodeId) {
      const touches = e.source === hoveredNodeId || e.target === hoveredNodeId;
      return { ...base,
               style: { ...base.style,
                        ...(touches
                          ? { stroke: "#2f8fff", strokeWidth: 2.5, strokeOpacity: 1 }
                          : { strokeOpacity: 0.18 }) } };
    }
    return base;
  });
  // React Flow's own default wire colour is a dim grey tuned for a light
  // canvas -- against this app's near-black background a live connection is
  // nearly invisible. defaultEdgeOptions only sets the FALLBACK; the "off"
  // dashed-grey override above still wins per edge since it sets its own
  // stroke explicitly.
  //
  // isDarkCanvas comes from useDarkClass() at the top of the component (a
  // reactive watch on the <html> class), not a one-shot classList read: the
  // read was not reactive, so flipping the theme left the canvas in the OLD
  // colour scheme until the next poll happened to re-render this component
  // -- the "transition changes so late" bug.

  return (
    <WorkflowCtx.Provider value={{ registry, values, errors, warnings,
                                   // The BOX's clock, so a Schedule can say
                                   // when it next runs in the zone it runs in
                                   // rather than in the reader's.
                                   clock: meta.clock || null,
                                   // Every function DEFINED on this canvas,
                                   // by name: what it is called and what it
                                   // takes. A Call and an End read their ports
                                   // off the Start this way, so renaming a
                                   // parameter in one place renames the port
                                   // everywhere instead of leaving two faces
                                   // disagreeing about the function's shape.
                                   fnDefs,
                                   variables: meta.variables || {}, incoming,
                                   fanIn, edges, objClasses, alertsBySource,
                                   offNodes, wired, faceReads,
                                   // The dot every Detection Stream wears.
                                   aiStatus,
                                   // ...and which Class Filters are paused,
                                   // for the ON / OFF points' lights.
                                   filtersPaused, runsWhen,
                                   // ...and which nodes the app holds while
                                   // the AI is off, for a probe on one.
                                   waitsForDetection,
                                   // ...and the one every MQTT node on this
                                   // box's broker wears.
                                   brokerUp,
                                   // Whether the app is UP. A node showing a
                                   // number from the last run as though it
                                   // were live is the one thing a probe must
                                   // never do -- "0 crossings" and "nothing
                                   // is running" look identical otherwise.
                                   appRunning,
                                   // An Inject's button: why it cannot fire
                                   // right now ("" = it can), and the press.
                                   // Only what is SAVED runs on the box, so an
                                   // unsaved canvas, a node the compile did
                                   // not run, a stopped app or an account
                                   // without edit rights all disable it --
                                   // with the reason as its tooltip.
                                   injectBlocked: (node) => {
                                     if (!editable) {
                                       return "You need the right to edit the "
                                         + "workflow to inject";
                                     }
                                     if (dirty || saveState === "saving") {
                                       return "Save the workflow first - the "
                                         + "box runs the saved version";
                                     }
                                     const fid = node?.data?.fnId;
                                     if (!fid || !(functions || []).some(
                                       (f) => f.id === fid)) {
                                       return errors[node?.id]
                                         ? "Fix the error on this node and save"
                                         : "Save the workflow first";
                                     }
                                     // Detection off is FINE: the app runs
                                     // the workflow either way, and a press
                                     // with no app up brings it up (AI off).
                                     // Only a system whose app cannot do that
                                     // has nothing to press into.
                                     if (!appRunning && meta.logic_supported === false) {
                                       return "Detection is off and this "
                                         + "system's app cannot run the "
                                         + "workflow on its own - start "
                                         + "detection, or update the system";
                                     }
                                     return "";
                                   },
                                   injectPress,
                                   onInject: async (node, action = "press") => {
                                     // What the circle waits for: the run
                                     // counter moving past this. A Stop
                                     // injects nothing, so it is not waited on.
                                     const fid = node.data?.fnId;
                                     const before = Number(values[`fn_${fid}_runs`]) || 0;
                                     if (action !== "stop") {
                                       setInjectPress((p) => ({
                                         ...p, [node.id]: { runs: before, at: Date.now() } }));
                                     }
                                     try {
                                       await api.post("/api/workflow/inject",
                                                      { node: node.id, action });
                                       toast.success({ press: "Injected",
                                                       start: "Started",
                                                       stop: "Stopped" }[action]
                                                     || "Injected",
                                         node.data?.name || "Inject");
                                     } catch (e) {
                                       setInjectPress((p) => ({
                                         ...p, [node.id]: { ...(p[node.id] || {}),
                                                            failed: e.message } }));
                                       toast.error("Inject did not fire",
                                                   e.message);
                                     }
                                   },
                                   // Comment notes resize by dragging their
                                   // corner; the node itself owns the grip
                                   // and this is its way to write the size.
                                   resizeNode: (id, dims, done) => {
                                     setNodes((cur) => {
                                       const node = cur.find((n) => n.id === id);
                                       // A frame never shrinks smaller than
                                       // the nodes inside it.
                                       // ...and a group never smaller than
                                       // the nodes it holds.
                                       if (node?.type === GROUP) {
                                         const min = groupMinSize(node, cur);
                                         dims = { width: Math.round(Math.max(min.width, dims.width)),
                                                  height: Math.round(Math.max(min.height, dims.height)) };
                                       }
                                       if (node?.type === FN_FRAME) {
                                         const min = frameMinSize(node, cur, frameMembership(cur));
                                         dims = { width: Math.round(Math.max(min.width, dims.width)),
                                                  height: Math.round(Math.max(min.height, dims.height)) };
                                       }
                                       return cur.map((n) => (n.id === id
                                         ? { ...n, data: { ...n.data, ...dims } } : n));
                                     });
                                     if (done) scheduleSave();
                                   },
                                   fnStats,
                                   toggleFrame: (id) => {
                                     setNodes((cur) => cur.map((n) => (n.id === id
                                       ? { ...n, data: { ...n.data, collapsed: !n.data?.collapsed } }
                                       : n)));
                                     scheduleSave();
                                   },
                                   openFunction,
                                   addFnPort: editable ? (id, key) => {
                                     const { nodes: ns, edges: es } = stateRef.current;
                                     history.push(ns, es);
                                     setNodes((cur) => cur.map((n) => {
                                       if (n.id !== id) return n;
                                       const list = (n.data?.[key] || []).filter(Boolean);
                                       let i = list.length + 1;
                                       while (list.includes(`param${i}`)) i += 1;
                                       return { ...n, data: { ...n.data, [key]: [...list, `param${i}`] } };
                                     }));
                                     scheduleSave();
                                   } : null,
                                   // The star. Saved with the graph like any
                                   // other node setting -- what somebody
                                   // pinned while tuning a line is a property
                                   // of the line, not of the browser tab.
                                   pinNode: (id) => {
                                     setNodes((cur) => cur.map((n) => (n.id === id
                                       ? { ...n, data: { ...n.data,
                                                         pinned: !n.data?.pinned } }
                                       : n)));
                                     scheduleSave();
                                   } }}>
      {unsavedDialog}
      {/* `wf-page` (theme.css) trims the reading-page padding and the page-head
          margin this one page does not need: the canvas IS the content here,
          and on an 800px-tall laptop every 10px of chrome is a row of nodes. */}
      <section className={"page active wf-page" + (canvasFs.full ? " wf-fullscreen" : "")}
               data-wf-fullscreen={canvasFs.full ? "on" : "off"}
               style={{ display: "flex", flexDirection: "column" }}>
        <div className="page-head">
          {/* The page name, not a field. A system has exactly ONE workflow
              (services/workflow.py MAIN_ID), so naming it named nothing --
              the box invited an edit that no other screen ever showed back,
              and cost 220px of a row that has to stay on one line. The name
              is still stored and still saved; it is simply not a decision. */}
          <div className="page-title">Workflow</div>
          <div className="page-actions" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <TutorialNav step="workflow" />
            {/* Is the box's MQTT broker up? Shown on every workflow. */}
            <BrokerStatus nodes={nodes} />
            {/* Is the AI actually running? A status, not a switch: Start and
                Stop detection stay on System Control and the Detection page. */}
            <AiDot ai={aiStatus} label />
            {/* The "workflow running (detection off)" badge that used to sit
                here is gone (Tok Aba, 2026-10-14): the AI Detection label
                beside the dot, red while detection is off, says it. */}
            {/* The node search used to live here - a screen away from the list
                it filters. It now sits pinned at the top of the palette, which
                also gives this row back ~180px and keeps it on one line on a
                laptop instead of wrapping and stealing canvas height. */}
            <Badge tone={saveState === "error" ? "warn"
                     : dirty ? "warn" : saveState === "saving" ? "muted" : "ok"}>
              {saveState === "error" ? "compile errors"
                : saveState === "saving" ? "saving…"
                : dirty ? "unsaved" : "saved"}
            </Badge>
            <label className="muted small"
                   style={{ display: "flex", alignItems: "center", gap: 4 }}
                   title="Off: nothing reaches the running app until you press Save">
              <input type="checkbox" checked={autoSave} disabled={!editable}
                     onChange={(e) => setAutoSave(e.target.checked)} />
              auto-save
            </label>

            {!autoSave && (
              <button className="btn btn-sm btn-accent" disabled={!editable || !dirty}
                      onClick={saveNow}>Save</button>
            )}
            {/* The canvas had no VISIBLE undo: only Ctrl+Z and a context-menu
                row, so a mouse-only operator had none, and the shortcut was
                undiscoverable. Same control the Zones toolbar carries. */}
            <UndoRedo canUndo={history.canUndo} canRedo={history.canRedo}
                      onUndo={() => {
                        const { nodes: ns, edges: es } = stateRef.current;
                        const prev = history.undo(ns, es);
                        if (prev) { setNodes(prev.nodes); setEdges(prev.edges);
                                    scheduleSave(); }
                      }}
                      onRedo={() => {
                        const { nodes: ns, edges: es } = stateRef.current;
                        const next = history.redo(ns, es);
                        if (next) { setNodes(next.nodes); setEdges(next.edges);
                                    scheduleSave(); }
                      }} />
            {/* Debug answers the question the canvas cannot: is this thing
                actually doing anything? Compile errors, every published value,
                and the events that fired - beside the graph that produced
                them, not on three other pages. The only tool out here: it is
                the one reached mid-build, over and over. */}
            <button className={"btn btn-sm" + (debugOpen ? " btn-accent" : "")}
                    onClick={() => setDebugOpen((v) => !v)}>Debug</button>
            {/* Tidy up takes the second permanent place. It was behind the
                overflow mark and is reached constantly -- a canvas is dragged
                out of shape by every edit, and straightening it is one of the
                two things done to a graph rather than to a node. The Ports
                drawer that used to sit here is gone: the board's terminals
                are a page of their own (Settings > Board I/O), and a second
                view of them living inside the graph editor meant two places
                to look and two to keep in step. */}
            <button className="btn btn-sm" disabled={!editable}
                    title="Auto-arrange every node left→right in pipeline order"
                    onClick={() => {
                      const { nodes: ns, edges: es } = stateRef.current;
                      history.push(ns, es);
                      setNodes(arrangeGraph(ns, es, registry));
                      setTimeout(() => rf.fitView({ duration: 300, padding: 0.15 }), 60);
                      scheduleSave();
                    }}>
              Tidy up
            </button>
            {/* Everything else, behind one mark. Zones is gone from here
                entirely -- it is in the sidebar now, which is where a page
                belongs. */}
            <div style={{ position: "relative" }}>
              <button className={"btn btn-sm" + (toolsOpen ? " btn-accent" : "")}
                      title="More tools" aria-expanded={toolsOpen}
                      onClick={() => setToolsOpen((v) => !v)}>☰</button>
              {toolsOpen && (
                <>
                  {/* Click-away catcher: a menu that only closes via its own
                      button is a menu left open across the canvas. */}
                  <div style={{ position: "fixed", inset: 0, zIndex: 40 }}
                       onClick={() => setToolsOpen(false)} />
                  <div style={{ position: "absolute", right: 0, top: "calc(100% + 4px)",
                                zIndex: 41, minWidth: 172, padding: 4,
                                display: "grid", gap: 4,
                                background: "var(--surface2, #1d2026)",
                                border: "1px solid rgba(128,128,128,.3)",
                                borderRadius: 8,
                                boxShadow: "0 8px 24px rgba(0,0,0,.4)" }}>
                    <button className="btn btn-sm"
                            style={{ justifyContent: "flex-start" }}
                            onClick={() => { setToolsOpen(false); doExport(); }}
                            title={selCount ? "Export the selected nodes to a JSON file"
                                            : "Export the whole canvas to a JSON file"}>
                      Export
                    </button>
                    <button className="btn btn-sm" disabled={!editable}
                            style={{ justifyContent: "flex-start" }}
                            onClick={() => { setToolsOpen(false); setImportOpen(true); }}
                            title="Add nodes from a workflow JSON file">Import</button>
                    {/* No Variables entry: a variable is created and read
                        from the palette, where the node that uses it is
                        picked, and the drawer was a second way to see the
                        same list. */}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="wf-grid"
             style={{ display: "grid", position: "relative",
                      gridTemplateColumns: `${paletteW}px minmax(0,1fr) ${rightW}px`,
                      gap: 12, flex: 1, minHeight: 0 }}>
          {/* The two vertical split handles sit in the gaps between the columns (not inside a
              column, which would clip them). The palette one can drag the node list shut. */}
          <div onPointerDown={startPaletteResize}
               onPointerMove={movePaletteResize}
               onPointerUp={endPaletteResize}
               onPointerCancel={endPaletteResize}
               className="wf-col-bar" data-wf-col-bar="palette"
               title={paletteW ? "Drag to resize the node list, or all the way left to close it"
                               : "Drag, or click, to open the node list"}
               style={{ position: "absolute", top: 0, bottom: 0, left: paletteW - 5, width: 22,
                        cursor: "col-resize", zIndex: 6, touchAction: "none" }} />
          <div onPointerDown={startInspectorResize}
               onPointerMove={moveInspectorResize}
               onPointerUp={endInspectorResize}
               onPointerCancel={endInspectorResize}
               className="wf-col-bar" data-wf-col-bar="panel"
               title={rightCollapsed ? "Click, or drag out, to show the guide"
                      : inspectorW ? "Drag to resize the panel, or all the way right to close it"
                      : "Drag, or click, to open the panel"}
               style={{ position: "absolute", top: 0, bottom: 0, right: rightW - 5, width: 22,
                        cursor: "col-resize", zIndex: 6, touchAction: "none" }} />
          {/* ---- palette (drag its right edge to resize) ----
              minHeight 0 overrides the grid item's min-height:auto, so a long
              node list scrolls INSIDE the palette instead of stretching the
              row (and the whole page) to fit it. */}
          <div className="wf-rail wf-rail-left"
               style={{ position: "relative", minWidth: 0, minHeight: 0,
                        display: "flex", overflow: "hidden" }}>
            <Palette registry={registry} zoneList={zoneList} models={modelList}
                     editable={editable} nodes={nodes} fnDefs={fnDefs}
                     search={searchLower} searchText={search}
                     onSearch={setSearch} onSearchEnter={jumpToMatch}
                     functions={functions} values={values}
                     vars={meta.variables || {}}
                     released={meta.released_ports || []}
                     customVars={customVars}
                     onCreateVar={declareVariable}
                     onDeleteVar={(varName) => {
                       setCustomVars((cur) => cur.filter((v) => v.var !== varName));
                       scheduleSave();
                     }}
                     // ...and fnOwners on top, for the same reason Debug
                     // needs it: a Set Variable's rule is named after the
                     // variable it writes and a wired Variable has no fnId at
                     // all, so neither could be jumped to from this list
                     // either.
                     nodeByFn={{ ...Object.fromEntries(nodes
                       .filter((n) => (n.data || {}).fnId)
                       .map((n) => [n.data.fnId, n.id])), ...fnOwners }}
                     onShowNode={(id) => {
                       if (!id) return;
                       setSelectedId(id);
                       setInspectId(id);
                       // Inside a folded function: unfold it, or the jump
                       // lands on a node nobody can see.
                       const folded = hiddenByFrames(nodes);
                       const fold = folded.has(id) && nodes.find((f) => f.type === FN_FRAME
                         && f.data?.collapsed
                         && hiddenByFrames(nodes.map((m) => (m.type === FN_FRAME && m.id !== f.id
                           ? { ...m, data: { ...m.data, collapsed: false } } : m))).has(id));
                       setNodes((cur) => cur.map((n) => ({
                         ...n, selected: n.id === id,
                         ...(fold && n.id === fold.id
                           ? { data: { ...n.data, collapsed: false } } : {}) })));
                       rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.2 });
                     }} />
          </div>

          {/* ---- canvas ---- */}
          <div ref={wrapperRef} className="wf-canvas-wrap"
               style={{ position: "relative", borderRadius: 10, overflow: "hidden",
                        border: "1px solid rgba(128,128,128,.25)", minHeight: 420 }}
               onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
               onDrop={onDrop}
               onPointerDownCapture={(e) => {
                 if (e.button === 2) rightDown.current = { x: e.clientX, y: e.clientY };
               }}
               onContextMenu={(e) => e.preventDefault()}>
            {/* Node-RED's mouse: right-drag pans, left-drag rubber-bands
                (touching a node is enough), and dragging a selection box never
                grabs the nodes it crosses.

                fitViewOptions clamps only the automatic fit: a two-node graph
                used to open blown up to fill the canvas and a twenty-node one
                too small to read a label. 0.65–1.0 keeps node text legible at
                either end; zooming by hand is untouched. */}
            {/* deleteKeyCode goes dead while the blueprint editor is open:
                the Function Block stays selected on the canvas underneath,
                so a Backspace that missed an input deleted the very node
                being edited - "I clicked save and my node was gone". */}
            <ReactFlow
              nodes={shown} edges={shownEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onConnect={onConnect} onConnectEnd={onConnectEnd}
              onSelectionChange={onSelectionChange}
              onSelectionStart={() => { boxSel.current = true; }}
              onSelectionEnd={() => {
                boxSel.current = false;
                // Final say for frames and groups: selected only when the box
                // that just ended covers them completely (see onNodesChange).
                const box = lastBox.current;
                lastBox.current = null;
                if (!box) return;
                setNodes((cur) => {
                  let changed = false;
                  const next = cur.map((n) => {
                    if (n.type !== FN_FRAME && n.type !== GROUP) return n;
                    const w = Number(n.data?.width) || 0;
                    const h = n.type === FN_FRAME && n.data?.collapsed ? 44 : Number(n.data?.height) || 0;
                    const inside = n.position.x >= box.x && n.position.y >= box.y
                      && n.position.x + w <= box.x + box.w && n.position.y + h <= box.y + box.h;
                    if (!!n.selected === inside) return n;
                    changed = true;
                    return { ...n, selected: inside };
                  });
                  return changed ? next : cur;
                });
              }}
              isValidConnection={isValidConnection}
              nodesDraggable={editable} nodesConnectable={editable}
              elementsSelectable
              deleteKeyCode={editable && !bpId ? ["Delete", "Backspace"] : []}
              panOnDrag={COARSE_POINTER ? true : [1, 2]} selectionOnDrag={!COARSE_POINTER}
              selectionMode={SelectionMode.Partial}
              selectNodesOnDrag={false}
              // Shift does both halves of "select several", because that is
              // the one key people reach for and having to learn which of two
              // modifiers does which is the whole friction: hold Shift and
              // DRAG to sweep a box over a run of nodes, hold Shift and CLICK
              // to add one more to what is already selected. Control does the
              // click half as well, for the hand that expects it there.
              // Stated rather than left to the defaults -- the default
              // selection key is Shift too, and a config that relies on that
              // silently breaks the day the default moves.
              selectionKeyCode="Shift"
              multiSelectionKeyCode={["Shift", "Control"]}
              // Double click opens the settings. A Function block opens its
              // blueprint instead -- that IS its settings, one level down.
              onNodeDoubleClick={(e, n) => {
                if (n.type === "analytics.block") { setBpId(n.id); return; }
                if (n.type === FN_CALL && fnStats[fnKey(n)]) { openFunction(n.data.fn); return; }
                setSelectedId(n.id);
                setInspectId(n.id);
              }}
              onNodeContextMenu={(e, n) => (editable
                ? openMenu(e, "node", n.id) : e.preventDefault())}
              onSelectionContextMenu={(e) => (editable
                ? openMenu(e, "selection", null) : e.preventDefault())}
              onPaneContextMenu={(e) => (editable
                ? openMenu(e, "pane", null) : e.preventDefault())}
              onPaneClick={() => { setMenu(null); setSelectedId(null); }}
              onEdgeMouseEnter={(e, edge) => setHoveredEdgeId(edge.id)}
              onEdgeMouseLeave={() => setHoveredEdgeId(null)}
              onNodeMouseEnter={(e, node) => setHoveredNodeId(node.id)}
              onNodeMouseLeave={() => setHoveredNodeId(null)}
              fitView fitViewOptions={{ padding: 0.15, minZoom: 0.65, maxZoom: 1 }}
              proOptions={{ hideAttribution: true }}
              defaultEdgeOptions={isDarkCanvas
                ? { style: { stroke: "#C0C0C0" } } : undefined}
              colorMode={isDarkCanvas ? "dark" : "light"}>
              <Background gap={18} size={1} />
              {/* Same colour resolution as the node itself (nodes.jsx
                  NodeShell): where a node LIVES in the palette first, its
                  wiring category only as a fallback. Reading the category
                  alone painted the map in colours the canvas does not use --
                  a Data Table wires as an output but is a Database node, so
                  it showed red on the map and purple on the canvas.

                  The map also follows the theme: its default is a light
                  panel, which on the dark canvas was the one white rectangle
                  on the page. */}
              <MiniMap pannable zoomable
                       maskColor={isDarkCanvas ? "rgba(12,14,19,.72)" : "rgba(240,240,240,.7)"}
                       style={{
                         background: isDarkCanvas ? "#12151b" : "var(--surface, #fff)",
                         border: "1px solid var(--border, rgba(128,128,128,.25))",
                         borderRadius: 8,
                       }}
                       nodeColor={(n) => {
                         const spec = registry[n.type] || {};
                         return (CATEGORY_STYLE[spec.palette?.cat]
                                 || CATEGORY_STYLE[spec.category] || {}).color || "#888";
                       }} />
              <Panel position="top-right">
                <button type="button" className="wf-guide-eye" data-wf-guide-eye
                        aria-pressed={!guideHidden}
                        aria-label={guideHidden ? "Unhide the workflow guide" : "Hide the workflow guide"}
                        title={guideHidden ? "Click to unhide the guide" : "Click to hide the guide"}
                        onClick={() => { if (guideHidden) openRight(); setGuideHidden(!guideHidden); }}>
                  {guideHidden ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}
                </button>
              </Panel>
              <Controls showInteractive={false}>
                {/* The workspace (palette, canvas, inspector) over the whole
                    screen, and back: with the zoom and fit buttons, where a
                    canvas keeps its view controls, and off the toolbar row
                    that already has to fit at 1024 px. React Flow's own
                    button class, so it is drawn like its neighbours. */}
                <button type="button" className="react-flow__controls-button"
                        data-wf-fs-toggle="" aria-pressed={canvasFs.full}
                        aria-label={canvasFs.full ? "Exit full screen" : "Full screen"}
                        title={canvasFs.full ? "Exit full screen (Esc)" : "Full screen"}
                        onClick={canvasFs.toggle}>
                  {canvasFs.full ? <Minimize2 aria-hidden="true" />
                                 : <Maximize2 aria-hidden="true" />}
                </button>
              </Controls>
            </ReactFlow>

            <VariablesPanel
              open={varsOpen} onClose={() => setVarsOpen(false)}
              values={values} statKeys={meta.variables?.stat_keys || []}
              nodes={nodes} editable={editable}
              customVars={customVars}
              labelFor={(k) => statLabel(k, functions, meta.variables?.cameras,
                                         meta.variables?.zone_list)}
              onCreateVar={declareVariable}
              onDeleteVar={(varName) => {
                setCustomVars((cur) => cur.filter((v) => v.var !== varName));
                scheduleSave();
              }}
              onSelectNode={(id) => {
                setSelectedId(id);
                setInspectId(id);
                setNodes((cur) => cur.map((n) => ({ ...n, selected: n.id === id })));
                rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.2 });
              }}
              />

            <DebugPanel
              open={debugOpen} onClose={() => setDebugOpen(false)}
              functions={functions} values={values} appRunning={appRunning}
              aiStatus={aiStatus} waitsForDetection={waitsForDetection}
              errors={errors} warnings={warnings} nodes={nodes}
              // WHICH NODE PUBLISHED THIS FUNCTION, for the ones whose
              // function id is not their own fnId. Debug matched rows to
              // nodes on node.data.fnId alone, and a Set Variable's rule is
              // named after the VARIABLE it writes (set_variable_j066 on the
              // canvas, object_name in the compile), so its heading could
              // never find its node: the jump arrow beside it was disabled on
              // every canvas in the field, starring it did nothing, and the
              // values under it were attributed to no node at all. Resolved
              // here because it takes the edges; the panel cannot see them.
              fnOwners={fnOwners}
              // The words the NODE uses for its own outputs. Debug listed raw
              // metric keys -- live_s, avg_s, net -- while the node beside it
              // said "Longest wait now" and "Inside": the same number under
              // two names, one of them nobody's.
              // A LABEL, not a description. METRIC_INFO entries come in two
              // shapes -- "Longest wait now (s) - the longest anyone has
              // waited" has a label in front of the dash, and "the formula's
              // result, or the number an API value last read" is a sentence
              // with no label at all. Taking the text before the dash gave
              // the whole sentence for the second shape, so Debug printed
              // that same paragraph against every formula, API value and Set
              // Variable on the canvas. A row needs a word; the sentence is
              // what the tooltip is for.
              labelFor={(metric) => {
                if (PORT_LABELS[metric]) return PORT_LABELS[metric];
                const info = METRIC_INFO[metric];
                if (info && info.includes(" - ")) return info.split(" - ")[0];
                // No label to be had: the key itself, read as words. Short,
                // honest, and the same thing the node's own port says.
                return String(metric).replace(/_/g, " ");
              }}
              hintFor={(metric) => METRIC_INFO[metric] || ""}
              onSelectNode={(id) => {
                setSelectedId(id);
                setInspectId(id);
                setNodes((cur) => cur.map((n) => ({ ...n, selected: n.id === id })));
                rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.2 });
              }} />

            <ContextMenu
              menu={menu} registry={registry} order={PALETTE_ORDER}
              zoneList={zoneList} models={modelList} editable={editable}
              fnDefs={fnDefs}
              canUndo={history.canUndo} canRedo={history.canRedo}
              hasSelection={selCount > 0} hasClipboard={hasClip}
              onClose={() => setMenu(null)}
              onInsert={(type, extra) => addNode(type, menu?.flowPos
                || { x: 0, y: 0 }, extra)}
              onUndo={() => {
                const { nodes: ns, edges: es } = stateRef.current;
                const prev = history.undo(ns, es);
                if (prev) { setNodes(prev.nodes); setEdges(prev.edges); scheduleSave(); }
              }}
              onRedo={() => {
                const { nodes: ns, edges: es } = stateRef.current;
                const next = history.redo(ns, es);
                if (next) { setNodes(next.nodes); setEdges(next.edges); scheduleSave(); }
              }}
              onCut={cutSelection} onCopy={copySelection}
              onPaste={() => paste(menu?.flowPos || null)}
              onDelete={deleteSelection}
              onExport={doExport} onSelectAll={selectAll}
              onDuplicate={() => menu?.nodeId && duplicateNode(menu.nodeId)}
              onDisconnect={() => {
                const id = menu?.nodeId;
                if (!id) return;
                const { nodes: ns, edges: es } = stateRef.current;
                history.push(ns, es);
                setEdges((cur) => cur.filter((e) => e.source !== id && e.target !== id));
                scheduleSave();
              }}
              onDeleteNode={() => {
                const id = menu?.nodeId;
                if (!id) return;
                const { nodes: ns, edges: es } = stateRef.current;
                // A node that is part of a selection of several goes with the rest of it.
                const picked = ns.filter((n) => n.selected);
                if (picked.length > 1 && picked.some((n) => n.id === id)) { deleteSelection(); return; }
                history.push(ns, es);
                setNodes((cur) => cur.filter((n) => n.id !== id));
                setEdges((cur) => cur.filter((e) => e.source !== id && e.target !== id));
                if (selectedId === id) setSelectedId(null);
                scheduleSave();
              }} />
          </div>

          <BlueprintEditor
            open={!!bpId}
            title={nodes.find((n) => n.id === bpId)?.data?.name}
            blueprint={nodes.find((n) => n.id === bpId)?.data?.blueprint || { nodes: [], edges: [] }}
            statKeys={meta.variables?.stat_keys || []}
            editable={editable}
            onClose={() => setBpId(null)}
            onSave={(blueprint) => {
              const { nodes: ns, edges: es } = stateRef.current;
              history.push(ns, es);
              setNodes((cur) => cur.map((n) => (n.id === bpId
                ? { ...n, data: { ...n.data, blueprint } } : n)));
              scheduleSave();
            }} />

          {/* ---- inspector / primer (drag its left edge to resize) ----
              Three states in one column: a node is selected (Inspector, full
              width, immediately), no node but the primer is open (same panel,
              same width), or neither - in which case the column is a 34px tab
              and the canvas keeps the ~250px the prose used to hold. The
              resize handle only exists while there is something to resize. */}
          <div ref={colRef} className="wf-rail wf-rail-right"
               style={{ position: "relative", minWidth: 0, minHeight: 0,
                        display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* The guideline card: above the properties, never over them. Folded it is a header;
                open and alone it fills the column; open with a node selected it shares it, and
                the bar below sets the split, also while nothing is selected (the place below stays
                empty until a node is). */}
            {!guideHidden && (
            <div className="wf-guide-slot"
                 style={!guideOpen ? { flex: "none" } : { flex: `0 0 ${dockPct}%` }}>
              <GuideDock open={guideOpen} flash={guideFlash} onToggle={() => setGuide(!guideOpen)}
                         registry={registry} nodeTypes={nodeTypes} />
            </div>
            )}
            {guideOpen && !guideHidden && (
              <div role="separator" aria-orientation="horizontal" title="Drag to resize the guide"
                   data-wf-dock-bar className="wf-dock-bar"
                   onPointerDown={(e) => {
                     if (e.button !== 0) return;
                     dragDock.current = true;
                     e.currentTarget.setPointerCapture(e.pointerId);
                   }}
                   onPointerMove={moveDockBar}
                   onPointerUp={() => { dragDock.current = false; }}
                   onPointerCancel={() => { dragDock.current = false; }}
                   onLostPointerCapture={() => { dragDock.current = false; }} />
            )}
            {/* Guideline hidden until ready; uncomment to restore the "show guide" tab.
            {!selected && !helpOpen && (
              <button type="button" className="wf-help-tab" onClick={() => setHelp(true)}
                      aria-label="Show the pipeline guide"
                      title="Show the pipeline guide: wiring order, shortcuts and mouse map">
                <span><ChevronsLeft size={15} /></span>
              </button>
            )} */}
            {guideOpen && !guideHidden && !(selected || helpOpen) && (
              <div className="wf-props-empty" data-wf-props-empty>
                Select a node to see its properties here.
              </div>
            )}
            {(selected || helpOpen) && (
            <div className="panel-card wf-inspector rail-form"
                 style={{ overflowY: "auto", alignSelf: "stretch", flex: 1, minWidth: 0 }}>
            {selected ? (
              <Inspector node={selected} registry={registry} meta={meta}
                         nodes={nodes} edges={edges} editable={editable}
                         aiStatus={aiStatus} waitsForDetection={waitsForDetection}
                         errors={errors} warnings={warnings}
                         // The LIVE compile result, not meta's first copy:
                         // only `variables` is refreshed on the poll, so
                         // meta.functions still names rules that were renamed
                         // or deleted minutes ago.
                         functions={functions}
                         customVars={customVars}
                         onCreateVar={declareVariable}
                         onDone={async () => {
                           await saveNow();
                           setSelectedId(null);
                           setNodes((cur) => cur.map((n) => ({ ...n, selected: false })));
                         }}
                         onOpenBlueprint={() => setBpId(selected.id)}
                         onFnData={(type, fields) => setFnData(selected.id, type, fields)}
                         // Turning a Variable that HOLDS one into a variable
                         // that IS one: same node, same place on the canvas,
                         // so making a new value never means finding a
                         // different palette entry and rewiring.
                         onRetype={(type, data) => {
                           history.push(nodes, edges);
                           setNodes((cur) => cur.map((n) => (n.id === selected.id
                             ? { ...n, type, data } : n)));
                           scheduleSave();
                         }}
                         onChange={(fields) => {
                           // A constant name holds ONE value: every constant
                           // node with this name takes the change.
                           const wasNode = nodes.find((n) => n.id === selected.id);
                           const shared = wasNode && wasNode.type === "variable.number"
                             && wasNode.data?.fnId
                             ? Object.fromEntries(["value", "value_type", "name"]
                                 .filter((k) => k in fields).map((k) => [k, fields[k]]))
                             : null;
                           if (shared && "name" in shared) {
                             const clash = nodes.some((n) => n.type === "variable.number"
                               && n.data?.fnId !== wasNode.data.fnId
                               && slugify(n.data?.name) === slugify(shared.name));
                             if (clash && slugify(shared.name)) {
                               toast.error("That name is already used",
                                           "Another constant already has this name. "
                                           + "A constant name holds one value: choose a different name.");
                               delete shared.name;
                               delete fields.name;
                             }
                           }
                           setNodes((cur) => {
                             const was = cur.find((n) => n.id === selected.id);
                             const next = cur.map((n) => {
                               if (n.id === selected.id) return { ...n, data: { ...n.data, ...fields } };
                               if (shared && Object.keys(shared).length
                                   && n.type === "variable.number"
                                   && n.data?.fnId === wasNode.data.fnId) {
                                 return { ...n, data: { ...n.data, ...shared } };
                               }
                               return n;
                             });
                             return renameFunction(next, was, fields);
                           });
                           scheduleSave();
                         }} />
            ) : null
            /* Guideline hidden until ready: replace "null" above with
               <Guideline onHide={() => setHelp(false)} /> to restore. */}
            </div>
            )}
          </div>
        </div>

        {/* ---- import ---- */}
        <Dialog open={importOpen} title="Import workflow JSON"
                onClose={() => { setImportOpen(false); setImportText(""); }}
                footer={(
                  <>
                    <button className="btn btn-sm"
                            onClick={() => { setImportOpen(false); setImportText(""); }}>
                      Cancel
                    </button>
                    <button className="btn btn-sm btn-accent"
                            disabled={!importText.trim()}
                            onClick={() => doImport(importText)}>Import</button>
                  </>
                )}>
          <p className="muted small">
            Adds the file's nodes to this canvas - nothing is replaced. Node ids
            and variable names are re-minted, so importing the same file twice
            gives you two independent copies.
          </p>
          <input ref={fileRef} type="file" accept=".json,application/json"
                 style={{ marginBottom: 8 }}
                 onChange={(e) => {
                   const f = e.target.files && e.target.files[0];
                   if (!f) return;
                   const reader = new FileReader();
                   reader.onload = () => doImport(String(reader.result || ""));
                   reader.onerror = () => toast.error("Could not read that file");
                   reader.readAsText(f);
                   e.target.value = "";
                 }} />
          <textarea className="input" rows={8} value={importText}
                    placeholder={`…or paste the JSON here\n{"app": "${FILE_APP}", "version": 1, "nodes": […], "edges": […]}`}
                    onChange={(e) => setImportText(e.target.value)} />
        </Dialog>
      </section>
    </WorkflowCtx.Provider>
  );
}

// How often a Snapshot / Record node may act while its trigger keeps firing.
// A violation that lasts a minute would otherwise write hundreds of near
// identical frames, so the default is one capture per episode.
const CAPTURE_MODES = [
  { key: "once", label: "Once per violation",
    hint: "One capture per episode. Re-arms after the gap below passes with no further alert." },
  { key: "per_id", label: "Once per object",
    hint: "One capture per tracked object (by object id) - the same person is never captured twice." },
  { key: "interval", label: "Every N seconds",
    hint: "At most one capture per interval while the alert keeps firing." },
  { key: "every", label: "Every alert",
    hint: "One capture per trigger event. Noisy: a per-frame trigger writes a file per frame." },
];

// The standard tail of a node's settings panel: everything an operator does
// not need on a normal visit, collapsed by default. What goes in here is
// PLUMBING -- the published key, palette publication, extra outputs -- not
// behaviour: a setting that changes what the node DOES stays in the open
// part of the panel where it can be seen to be set.
//: The counters that can be told WHEN an object counts. Both count a
//: population over time, which is what makes "only if it stays" and "do not
//: flicker" answerable; a line crossing or a completed stay is an EVENT, and
//: an event has no duration to wait out.
const COUNT_TIMING = new Set(["analytics.counter", "analytics.violations",
                              "analytics.frame", "analytics.occupancy",
                              "analytics.frame_count"]);

/**
 * How a counter decides an object is there -- real time, or on a timer.
 *
 * One question with two halves, so they are one control rather than two
 * numbers an operator has to work out the relationship between:
 *
 *   * real-time counting -- what is in view this frame. The default, and what
 *     every counter did before this existed.
 *   * minimum stay -- an object counts as 1 only once it has been there that
 *     long, so a forklift driving through a zone does not read as one parked
 *     in it. Staying longer does not count it again; it is one object.
 *   * still counts for -- covers a tracker losing an object for a frame, or a
 *     person stepping a foot over the line. Without it a count flickers and
 *     every alert wired to it fires twice.
 *
 * "Timer" was the first name for this and it was the wrong one: it says a
 * clock is involved without saying what the clock decides.
 *
 * Both halves need object tracking, because both are questions about a
 * particular object over time and only the tracker knows which object is
 * which. Said here, where the number is typed.
 */
function CountTiming({ d, editable, onChange }) {
  const enter = Number(d.enter_after_s || 0);
  const leave = Number(d.leave_after_s || 0);
  const timed = enter > 0 || leave > 0;
  const num = (key) => (e) => {
    const v = Math.max(0, Math.min(3600, +e.target.value || 0));
    onChange({ [key]: v });
  };
  return (
    <div style={{ marginTop: 10 }}>
      <div className="muted small" style={{ fontWeight: 600 }}>Counting</div>
      <div className="filter-bar" style={{ marginTop: 6, flexWrap: "wrap" }}>
        <button className={"btn btn-sm" + (timed ? "" : " btn-accent")}
                disabled={!editable}
                title="Count what is there this frame - the live number"
                onClick={() => onChange({ enter_after_s: 0, leave_after_s: 0 })}>
          Real-time counting
        </button>
        <button className={"btn btn-sm" + (timed ? " btn-accent" : "")}
                disabled={!editable}
                title="Count an object only once it has stayed put"
                onClick={() => { if (!timed) onChange({ enter_after_s: 3 }); }}>
          Minimum stay
        </button>
      </div>
      {timed && (
        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
          <label className="muted small">
            <div>Counts as 1 once it has stayed for</div>
            <div className="filter-bar" style={{ marginTop: 2 }}>
              <input className="input" type="number" min="0" max="3600" step="0.5"
                     style={{ width: 90 }} value={enter} disabled={!editable}
                     onChange={num("enter_after_s")} />
              <span className="muted small">
                seconds - passing through counts for nothing
              </span>
            </div>
          </label>
          <label className="muted small">
            <div>Still counts for</div>
            <div className="filter-bar" style={{ marginTop: 2 }}>
              <input className="input" type="number" min="0" max="3600" step="0.5"
                     style={{ width: 90 }} value={leave} disabled={!editable}
                     onChange={num("leave_after_s")} />
              <span className="muted small">
                seconds after it is last seen - covers a dropped frame
              </span>
            </div>
          </label>
          <p className="muted small" style={{ margin: 0 }}>
            Both need object tracking - they are questions about one object
            over time, and only the tracker knows which object is which. With
            tracking off (<Link to="/models">AI Models</Link>) this counts in
            real time regardless.
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * The VIOLATION TIMER of an Anomaly or an Object Detection node, in plain view:
 * how long something must stay before it counts. It is the counters' "Minimum
 * stay" (the same `enter_after_s` the engine already honours) said the way a
 * violation is said, and put where it is found instead of folded into
 * Advanced. The time a photo reports as "timer" is read from this setting
 * (app/snapmeta.py: the object's time on camera against this threshold).
 */
function ViolationTimer({ d, editable, onChange, anomaly }) {
  const secs = Number(d.enter_after_s || 0);
  const on = secs > 0;
  return (
    <div style={{ marginTop: 10 }} data-violation-timer>
      <div className="muted small" style={{ fontWeight: 600 }}>Violation timer</div>
      <div className="filter-bar" style={{ marginTop: 6, flexWrap: "wrap" }}>
        <button className={"btn btn-sm" + (on ? "" : " btn-accent")} disabled={!editable}
                title={anomaly ? "NG the moment something is found"
                               : "Found the moment something is seen"}
                onClick={() => onChange({ enter_after_s: 0 })}>
          Immediately
        </button>
        <button className={"btn btn-sm" + (on ? " btn-accent" : "")} disabled={!editable}
                title="Only if it stays longer than the time you set"
                onClick={() => { if (!on) onChange({ enter_after_s: 5 }); }}>
          After a time
        </button>
      </div>
      {on && (
        <label className="muted small" style={{ display: "block", marginTop: 8 }}>
          <div>
            {anomaly ? "Counts as NG" : "Counts as found"} once it has stayed for
          </div>
          <div className="filter-bar" style={{ marginTop: 2 }}>
            <input className="input" type="number" min="0.5" max="3600" step="0.5"
                   style={{ width: 90 }} value={secs} disabled={!editable}
                   onChange={(e) => onChange({
                     enter_after_s: Math.max(0.5, Math.min(3600, +e.target.value || 0.5)) })} />
            <span className="muted small">
              seconds - passing through counts for nothing
            </span>
          </div>
          <p className="muted small" style={{ margin: "6px 0 0" }}>
            It needs object tracking and a camera that is always watching, so it
            works on a Video stream. A Snapshot stream only looks when asked and
            cannot see how long something stayed.
          </p>
        </label>
      )}
    </div>
  );
}

function AdvancedSection({ children, label }) {
  return (
    <details className="wf-advanced">
      <summary className="muted small">{label || "Advanced"}</summary>
      {children}
    </details>
  );
}

// The per-metric "Show as a widget on Dashboard/Analytics" checkboxes used to
// live here, on every PROCESSING node. Removed: how a measurement is shown is
// the dashboard nodes' business -- wire a Card/Chart in and choose there, or
// name it to publish the composed card to the editors' palette. The stored
// `expose` map still compiles server-side, so a graph that set it keeps its
// palette entries; there is simply no per-node UI for it any more.

// Free-form filters for a Card or Chart on recorded events: any recorded
// field, several at once.
// Values come from what the store has actually seen where possible, because a
// card that counts nothing because of a typo looks identical to a card that
// counts nothing because nothing happened.
function EventConditions({ d, vars, editable, onChange }) {
  const fields = (vars.event_fields || []).filter((f) => f !== "type");
  const values = vars.event_values || {};
  const rows = d.conditions || [];
  const set = (i, patch) => onChange({
    conditions: rows.map((r, j) => (j === i ? { ...r, ...patch } : r)),
  });
  return (
    <Field label="And where…" hint="Extra conditions. All must match.">
      {rows.map((r, i) => (
        <div key={i} className="wf-row">
          <select className="input" style={{ flex: "1 1 104px" }} value={r.field || ""}
                  disabled={!editable} onChange={(e) => set(i, { field: e.target.value })}>
            <option value="">- field -</option>
            {fields.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          {(values[r.field] || []).length ? (
            <select className="input" style={{ flex: "1 1 104px" }}
                    value={r.value || ""} disabled={!editable}
                    onChange={(e) => set(i, { value: e.target.value })}>
              <option value="">- value -</option>
              {values[r.field].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          ) : (
            <input className="input" style={{ flex: "1 1 104px" }}
                   value={r.value || ""} disabled={!editable} placeholder="value"
                   onChange={(e) => set(i, { value: e.target.value })} />
          )}
          <button className="btn btn-sm" style={{ flex: "0 0 auto" }} disabled={!editable}
                  onClick={() => onChange({ conditions: rows.filter((_, j) => j !== i) })}>×</button>
        </div>
      ))}
      {rows.length < 8 && (
        <button className="btn btn-sm" disabled={!editable}
                onClick={() => onChange({ conditions: [...rows, { field: "", value: "" }] })}>
          + condition
        </button>
      )}
    </Field>
  );
}

/** THE QUESTION A CARD OR CHART ASKS OF RECORDED DATA -- what the retired
 *  Event Card's panel was, now drawn inside the Card and the Chart when their
 *  Source is "recorded events" or "a data table" (EVENT_SOURCE_MODES in
 *  workflow.py; the compile is the Event Card's, unchanged).
 *
 *  `chart` is whether there is an axis to fill: a one-value card has no
 *  "one bar per" or time bucket to ask about, so those rows only appear on a
 *  Chart -- the Event Card offered them on a tile too, where they did
 *  nothing a reader could see.
 */
function RecordedQuery({ mode, d, vars, editable, set, onChange, chart,
                         canvasDatasets, chosenDataset, upstreamValue,
                         chartBuilder = false, oneRange = false }) {
  // `chartBuilder`: a Chart asks this question through the chart builder
  // (X, Y, interval, date range drawn by GraphSeriesRows), so only the rows
  // that say WHERE it reads -- the table, the event type, the conditions --
  // are drawn here.
  const table = d.kind === "table";
  if (mode === "dataset") {
    return (
      <>
        <Field label="Which table">
          <select className="input" value={d.dataset || ""} disabled={!editable}
                  onChange={set("dataset")}>
            <option value="">- choose a table -</option>
            {canvasDatasets.map((ds) => (
              <option key={ds.key} value={ds.key}>{ds.name}</option>
            ))}
          </select>
        </Field>
        {!table && chosenDataset && !chartBuilder && (<>
          <WorkOutField field={Field} value={d.agg || "count"} disabled={!editable}
                        onChange={(v) => onChange({ agg: v })} />
          {(d.agg || "count") !== "count" && (
            <Field label={CARD_FIELDS.column.label}>
              <select className="input" value={d.field || ""} disabled={!editable}
                      onChange={set("field")}>
                <option value="">- choose a column -</option>
                {chosenDataset.columns
                  // sum/avg/min/max need arithmetic; offering a text column
                  // here is offering a question Postgres cannot answer.
                  .filter((c) => ["unique", "changes"].includes(d.agg)
                                 || c.type === "number")
                  .map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </Field>
          )}
          {/* A bar each of WHAT: a column of the table, or a period. They are
              alternatives, not a pair -- picking a period clears the column
              and the other way round, so a chart never carries two
              groupings that contradict. A TEXT column is how a chart gets
              its categories from words: one bar per value, counted, in name
              order. */}
          {chart && (
            <>
              <PerField field={Field} groupBy={d.group_by} emptyLabel="nothing - one total, or by time"
                        columns={chosenDataset.columns} disabled={!editable}
                        onChange={onChange} />
              <StepField field={Field} value={d.bucket || ""} disabled={!editable}
                         emptyLabel="None - one total"
                         lockedReason={d.group_by ? X_FIELD_REASON : ""}
                         onChange={(v) => onChange({ bucket: v })} />
            </>
          )}
          {chart && d.group_by && d.group_by === d.field
            && (chosenDataset.columns.find((c) => c.name === d.field) || {}).type
               === "number" && (
            <p className="small c-warn">
              Grouping by the same number you are working out draws one bar
              per value of it - a 0/1 column gives exactly two, one of them
              always zero. Pick a period of time, or another column.
            </p>
          )}
        </>)}
        {chosenDataset && table && (
          <p className="muted small">
            Shows the rows themselves: {chosenDataset.columns
              .map((c) => c.name).join(", ")}.
          </p>
        )}
        {/* A card's period is its one Date range (CardDateRange); a
            chart's is the chart builder's. */}
        {!chartBuilder && !oneRange && (
          <PeriodField field={Field} value={d.range || ""} disabled={!editable}
                       onChange={(v) => onChange({ range: v })} />
        )}
        <p className="muted small">
          Reads the table a Data Table node records - your own columns, so
          you can group and total by anything you chose to log.
        </p>
      </>
    );
  }
  return (
    <>
      {!table && !chartBuilder && (
        <>
          {/* The event store answers two of the questions: the same
              "Work out" list as everywhere, narrowed to those. */}
          <WorkOutField field={Field} value={d.agg || "count"} disabled={!editable}
                        only={["count", "unique"]}
                        onChange={(v) => onChange({ agg: v })} />
          {d.agg === "unique" && (
            <Field label={CARD_FIELDS.column.label} hint="camera, zone, detection_label…">
              <input className="input" value={d.field || ""} disabled={!editable}
                     onChange={set("field")} placeholder="camera" />
            </Field>
          )}
          {chart && (
            <>
              <Field label="Group by"
                     hint="A recorded field - one bar per value, in name order. Empty for one total.">
                <input className="input" value={d.group_by || ""} disabled={!editable}
                       onChange={set("group_by")} placeholder="camera / zone" />
              </Field>
              {/* One interval grammar, matching what the store accepts
                  (events_repo.time_bucket_exprs): calendar buckets plus
                  fixed sub-hour resolutions. */}
              <StepField field={Field} value={d.bucket || ""} disabled={!editable}
                         emptyLabel="None - one total"
                         onChange={(v) => onChange({ bucket: v })} />
            </>
          )}
        </>
      )}
      <EventTypeField field={Field} value={(d.filters || {}).type || ""}
                      disabled={!editable}
                      onChange={(v) => onChange({ filters: v ? { type: v } : {} })} />
      <EventConditions d={d} vars={vars} editable={editable} onChange={onChange} />
      {!chartBuilder && !oneRange && (
        <PeriodField field={Field} value={d.range || ""} disabled={!editable}
                     onChange={(v) => onChange({ range: v })} />
      )}
      <p className="muted small">
        Reads the recorded events, not a live value - so it answers “how many
        so far”, which no analytics node can.
      </p>
      {!upstreamValue && (
        <p className="muted small">
          Nothing is wired in, so it counts <strong>every</strong> recorded
          event that matches the filters above. Wire a function into it to
          count only what that function raised.
        </p>
      )}
    </>
  );
}

/** How many frames a Snapshot stream detects each time its source fires. The
 *  source (an Inject or a Trigger) holds the number, because that is what the
 *  operator is looking at when they decide it; the stream only listens. */
function SnapFrames({ d, editable, set }) {
  return (
    <Field label="Frames per snapshot"
           hint={"How many camera frames the Detection Stream detects each time this "
                 + "fires. When they are taken it goes back to false by itself, and "
                 + "waits for the next true."}>
      <input className="input" type="number" min="1" max="600" step="1"
             value={d.frames ?? 1} disabled={!editable} onChange={set("frames")} />
    </Field>
  );
}

/** An Inject node's settings: what it injects, and when besides a press.
 *
 *  Payload is a TypedInput (string / number / true-false / JSON / timestamp);
 *  topic is a plain label carried beside it. "Inject once after" and Repeat
 *  are the two automatic moments -- at start (and when this node is saved
 *  changed, as a redeploy restarts a flow), and on an interval. The compile
 *  turns the repeat's number and unit into one interval_s for the engine.
 */
function InjectFields({ d, editable, onChange, set, showFrames }) {
  const repeat = d.repeat === "interval" ? "interval" : "none";
  return (
    <>
      {/* Only while this Inject is wired into a Snapshot Detection Stream. */}
      {showFrames && <SnapFrames d={d} editable={editable} set={set} />}
      {/* A div, not a <Field>: the control holds several inputs, and a label
          wrapping them all sends every click to the first. */}
      <div className="wf-field">
        <span className="wf-field-label">
          Payload
          <InfoHint label="About Payload">
            What each inject puts on the wire. A number or true/false (1/0)
            can be tested and calculated with; text and JSON are sent on by
            MQTT, a Webhook or Python. A timestamp is the moment it fires.
          </InfoHint>
        </span>
        <TypedInput type={d.ptype || "timestamp"} value={d.payload ?? ""}
                    disabled={!editable} label="Payload"
                    onChange={({ type, value }) => onChange({ ptype: type,
                                                              payload: value })} />
      </div>
      <Field label="Topic" hint="Optional. A label carried with the payload.">
        <input className="input" value={d.topic || ""} disabled={!editable}
               onChange={set("topic")} placeholder="none" />
      </Field>
      <label className="filter-bar" style={{ alignItems: "center", gap: 6 }}>
        <input type="checkbox" checked={!!d.once} disabled={!editable}
               onChange={(e) => onChange({ once: e.target.checked })} />
        <span className="small">Inject once after</span>
        <input className="input" type="number" min="0" max="3600" step="0.1"
               style={{ width: 72 }} value={d.once_delay_s ?? 0.1}
               disabled={!editable || !d.once}
               aria-label="Seconds after start"
               onChange={(e) => onChange({ once_delay_s: +e.target.value })} />
        <span className="small">seconds, then</span>
      </label>
      {/* The two ways to fire it, named as the client names them (UAT
          2026-10-04): by hand, clicking the node, or by itself on an
          interval. Stored as `repeat` none / interval, as before. */}
      <Field label="Trigger"
             hint="Manual: click Inject on the node (or its dashboard button). Automatic: it fires by itself every interval below; the button becomes Start / Stop.">
        <select className="input" value={repeat} disabled={!editable}
                onChange={(e) => onChange({ repeat: e.target.value })}>
          <option value="none">manual - click the node</option>
          <option value="interval">automatic - every interval</option>
        </select>
      </Field>
      {repeat === "interval" && (
        <label className="filter-bar" style={{ alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={d.autostart !== false}
                 disabled={!editable}
                 onChange={(e) => onChange({ autostart: e.target.checked })} />
          <span className="small">Start repeating when the app starts</span>
          <InfoHint label="About starting the repeat">
            Off, the repeat waits for Start on the node or its dashboard
            button. Start and Stop hold until the app restarts, which goes back
            to this setting.
          </InfoHint>
        </label>
      )}
      {repeat === "interval" && (
        <div className="wf-row" style={{ alignItems: "center", gap: 6 }}>
          <span className="small">every</span>
          <input className="input" type="number" min="1" step="1"
                 style={{ width: 72 }} value={d.every ?? 1}
                 disabled={!editable} aria-label="Repeat every"
                 onChange={(e) => onChange({ every: +e.target.value })} />
          <select className="input" value={d.every_unit || "seconds"}
                  disabled={!editable} aria-label="Repeat unit"
                  onChange={(e) => onChange({ every_unit: e.target.value })}>
            <option value="seconds">seconds</option>
            <option value="minutes">minutes</option>
            <option value="hours">hours</option>
          </select>
        </div>
      )}
      <p className="muted small">
        Click Inject on the node to inject now - it works on the saved
        workflow, with or without detection: with detection off, everything
        a Detection Stream does not feed runs on its own (no camera, no AI
        model). Wired
        into an output, each inject fires it once; wired into a Math, Compare
        or card, it is the value it last injected.
      </p>
      <p className="muted small">
        The circle beside the button: <strong>green</strong> - the last
        injection passed (the box took it, and every output wired straight
        after it worked); <strong>red</strong> - it failed (the box did not
        take it, or an output after it failed - the reason is on hover);
        grey - not injected yet.
      </p>
    </>
  );
}

function CaptureMode({ node, d, editable, onChange }) {
  const isSnap = node.type === "output.snapshot";
  const mode = d.mode || "once";
  const spec = CAPTURE_MODES.find((m) => m.key === mode) || CAPTURE_MODES[0];
  return (
    <>
      <Field label={isSnap ? "Take a photo" : "Record"}>
        <select className="input" value={mode} disabled={!editable}
                onChange={(e) => onChange({ mode: e.target.value })}>
          {CAPTURE_MODES.map((m) => (
            <option key={m.key} value={m.key}>{m.label}</option>
          ))}
        </select>
      </Field>
      <p className="muted small">{spec.hint}</p>
      {(mode === "interval" || mode === "once") && (
        <Field label={mode === "interval" ? "Interval (s)" : "Quiet gap that ends the episode (s)"}>
          <input className="input" type="number" min="0.5" step="0.5"
                 value={d.interval_s ?? (isSnap ? 5 : 10)} disabled={!editable}
                 onChange={(e) => onChange({ interval_s: Math.max(0.5, +e.target.value) })} />
        </Field>
      )}
      <p className="muted small">
        {isSnap ? (<>
          Saves the annotated frame as a photo - it shows up in Footage. Works only
          while the <strong>Snapshot</strong> switch is ON in the hub's System Control.
        </>) : (<>
          Records a short clip of the annotated feed, listed on the Detection page.
          Works only while the <strong>Record</strong> switch is ON in System Control.
          Overlapping alerts extend a clip, never past 5s from the newest trigger.
        </>)}
      </p>
    </>
  );
}

// Severity + message + cooldown: the three fields every node that RAISES
// something needs, worded the same way everywhere so "warn" never means two
// things on two nodes.
//
// `repeat` (the Trigger only): HOW a held alarm is raised again. The cooldown
// is a clock; "new object" is a headcount -- once for the crowd, again for
// each person who joins it, silence for the ones already in it. That needs to
// know who, so the Trigger grows an OID port and `oidWired` says whether a
// rule's OID output has been dragged onto it yet.
function AlertBits({ d, editable, set, placeholder, defaultCooldown,
                     repeat = false, oidWired = false, rateSet = false }) {
  const perObject = repeat && d.repeat === "new_object" && !rateSet;
  return (
    <>
      <Field label="Raise as">
        <select className="input" value={d.severity || "warn"} disabled={!editable}
                onChange={set("severity")}>
          <option value="info">note (info)</option>
          <option value="warn">alert (warn)</option>
          <option value="crit">violation (critical)</option>
        </select>
      </Field>
      <Field label="Message (optional)">
        <input className="input" value={d.message || ""} disabled={!editable}
               placeholder={placeholder} onChange={set("message")} />
      </Field>
      {/* A Trigger whose Advanced "How often it can fire" is set has
          answered this already, for the alarm and its payload alike; a
          second answer here could only disagree with it. */}
      {rateSet && (
        <p className="muted small" style={{ marginTop: -4 }}>
          How often it raises is set by <strong>How often it can
          fire</strong>, above.
        </p>
      )}
      {repeat && !rateSet && (
        <Field label="Raise again"
               hint="While the moment keeps holding: repeat on a timer, or only when a different object joins it. The second needs the rule's OID wired into this Trigger's OID port, so it knows who is new.">
          <select className="input" value={d.repeat || "cooldown"} disabled={!editable}
                  onChange={set("repeat")}>
            <option value="cooldown">every cooldown (timer)</option>
            <option value="new_object">only for a new object (watchdog)</option>
          </select>
        </Field>
      )}
      {!perObject && !rateSet && (
        <Field label="Cooldown (s)" >
          <input className="input" type="number" min="0" step="1"
                 value={d.cooldown_s ?? defaultCooldown} disabled={!editable}
                 onChange={set("cooldown_s")} />
        </Field>
      )}
      {perObject && (
        <p className="muted small" style={{ marginTop: -4 }}>
          {oidWired
            ? <>Fires once when this holds, then again the moment an object
                that was not already in it is counted. The same objects
                lingering raise nothing more.</>
            : <span className="c-warn">
                Needs to know who: tick <strong>OID</strong> on the rule this
                Trigger watches and wire its OID output into this Trigger's
                <strong> OID</strong> port (it appears on the node while this
                is chosen).
              </span>}
        </p>
      )}
    </>
  );
}

/** Does a node upstream of this Trigger have OID ticked -- the who "Once per
 *  object" counts? The compile asks the same (trigger_out.oid_rule_upstream):
 *  a walk back over the wires, the Trigger's own zone port skipped (a place,
 *  not a who). A wire on its OID port answers too, and is the caller's. */
function oidTrackedUpstream(nodeId, nodes, edges) {
  const byId = new Map((nodes || []).map((n) => [n.id, n]));
  const seen = new Set([nodeId]);
  let frontier = [nodeId];
  for (let hop = 0; hop < 12 && frontier.length; hop += 1) {
    const next = [];
    for (const id of frontier) {
      for (const e of edges || []) {
        if (e.target !== id || seen.has(e.source)) continue;
        if (id === nodeId && ["zone", "cur_oid", "cur_value"]
          .includes(e.targetHandle || "")) continue;
        seen.add(e.source);
        if (byId.get(e.source)?.data?.OID) return true;
        next.push(e.source);
      }
    }
    frontier = next;
  }
  return false;
}

// The Trigger's duplicate guard and output fields live in
// workflow/TriggerInspector.jsx since the 2026-10-14 layout.

// ---------------------------------------------------------------------------
// palette
// ---------------------------------------------------------------------------

/** Every declared variable name the CANVAS mentions, however it mentions it.
 *
 *  Read from the graph rather than from the compiled functions, because a
 *  variable can be used without being published: a text constant wired into a
 *  Data Table column becomes part of the column, a name typed into a formula
 *  is a reference the compiler resolves later, and a Variable node holding a
 *  value publishes under the name it stores. All three are "in use" to the
 *  person who drew them, and none of them is a function on the values list.
 */
function usedVarNames(nodes) {
  const out = new Set();
  for (const n of nodes || []) {
    const d = n.data || {};
    // The node that IS this variable: a constant or formula created from the
    // Variables panel carries the variable's own name as its function id.
    if (d.fnId) out.add(String(d.fnId));
    // A Variable node STORING under a name, or READING one.
    if (n.type === "variable.ref") {
      if (d.name) out.add(slug(d.name));
      if (d.key) {
        const m = String(d.key).match(/^fn_(.+)_value$/);
        if (m) out.add(m[1]);
      }
    }
    // ...and any name TYPED into an expression, a message or a SQL statement:
    // fn_<name>_value is how a variable is referred to everywhere text is
    // allowed, so one pass over the strings catches every one of them.
    for (const value of Object.values(d)) {
      if (typeof value !== "string" || !value.includes("fn_")) continue;
      for (const m of value.matchAll(/fn_([a-z0-9_]+)_value\b/g)) out.add(m[1]);
    }
  }
  return out;
}

function Palette({ registry, zoneList, models, editable, search, searchText,
                   // Every function DEFINED on this canvas, so the drawer can
                   // list them the way it lists the zones drawn on this box: a
                   // function you cannot find is a function nobody calls twice.
                   fnDefs = {},
                   onSearch, onSearchEnter, onGuide, functions, values, vars = {},
                   customVars, onCreateVar, onDeleteVar, nodeByFn, onShowNode,
                   // The canvas itself, for "is this variable used": a node
                   // may use a name without publishing one (see usedVarNames).
                   nodes = [], released = [] }) {
  const confirm = useConfirm();
  // "+ New variable" opens a real dialog rather than window.prompt: naming a
  // thing that will outlive the click deserves a field you can correct, and
  // the browser prompt cannot show the slug the name becomes.
  const [newVarOpen, setNewVarOpen] = useState(false);
  // Which drawers are open. CLOSED by default, so the page opens as a short
  // readable list of categories and you open the one you want -- nine
  // categories expanded is the landmark-less scroll the drawers exist to fix.
  // Remembered per browser, because which parts of the palette someone works
  // in is a habit, and closing the same four every visit is a small tax that
  // makes a tool feel unhelpful.
  const [openCats, setOpenCats] = useState(() => {
    try { return JSON.parse(localStorage.getItem("wf.palette.open") || "{}"); }
    catch { return {}; }
  });
  const toggleCat = (cat) => setOpenCats((cur) => {
    const next = { ...cur, [cat]: !cur[cat] };
    try { localStorage.setItem("wf.palette.open", JSON.stringify(next)); } catch { /* private mode */ }
    return next;
  });
  // Where a node APPEARS is spec.palette; what it may be WIRED to is
  // spec.category, which the compiler validates. Keeping them apart is what
  // lets the Zone functions sit beside the zones they measure without
  // changing a single edge rule.
  // Every threshold line drawn on a card, as something to WIRE. The rule
  // already exists the moment the line is drawn -- it publishes 1/0 and
  // raises nothing until asked -- but a rule you cannot find is a rule that
  // gets rebuilt by hand in a Compare. Listed under Alerts, which is where
  // somebody goes looking for "the thing that fires when we go over".
  //
  // Dragged out it is a Variable pointed at that rule's value: a plain number
  // to branch on, chart, or wire into a Telegram. Nothing new to learn --
  // it is the same node a saved query or a zone drops as.
  const thresholds = (functions || [])
    .filter((f) => String(f.id || "").startsWith("th_"))
    .filter((f) => !search || String(f.name || "").toLowerCase().includes(search))
    .map((f) => ({ ...f, key: `fn_${f.id}_value` }));

  // The functions defined on this canvas, by name, filtered by the same
  // search box as everything else. An unclosed one is still listed, saying so:
  // half a function is a normal state while wiring, and hiding it would read
  // as "the function you just drew is gone".
  const fnHere = Object.values(fnDefs || {})
    .filter((f) => !search || f.name.toLowerCase().includes(search)
                   || (f.params || []).some((p) => p.toLowerCase().includes(search)))
    .sort((a, b) => a.name.localeCompare(b.name));
  // Every Custom Formula already written on this canvas, so one built once
  // can be dragged out again as many times as needed instead of retyped.
  // One entry per distinct expression (copies of the same formula are one
  // formula), named after the first node that holds it. Dragging one out is a
  // new Custom Formula with the same expression and settings, and its own id.
  const formulasHere = (() => {
    const seen = new Map();
    for (const n of nodes || []) {
      if (n.type !== "analytics.expr") continue;
      const expr = String(n.data?.expr || "").trim();
      if (!expr) continue;
      const k = expr.replace(/\s+/g, " ");
      const cur = seen.get(k);
      if (cur) { cur.count += 1; cur.ids.push(n.id); continue; }
      const { fnId, pinned, ...rest } = n.data || {};
      seen.set(k, { key: k, name: String(n.data?.name || "").trim() || "Formula",
                    expr, data: rest, count: 1, ids: [n.id] });
    }
    return [...seen.values()]
      .filter((f) => !search || f.name.toLowerCase().includes(search)
                     || f.expr.toLowerCase().includes(search))
      .sort((a, b) => a.name.localeCompare(b.name));
  })();
  // Every Card Widget and Chart Widget on this canvas, one entry per node
  // (each one is its own widget on the dashboard). Double-click jumps to it;
  // dragging one out is a NEW widget with the same shape and settings and
  // nothing wired in yet.
  const widgetsHere = (nodes || [])
    .filter((n) => n.type === "dashboard.card" || n.type === "dashboard.graph")
    .map((n) => {
      const { fnId, pinned, builder_edit, ...rest } = n.data || {};
      const kind = n.type === "dashboard.graph" ? "chart" : "card";
      return { id: n.id, type: n.type, data: rest,
               name: String(n.data?.name || "").trim()
                 || (n.type === "dashboard.graph" ? "Chart Widget" : "Card Widget"),
               sub: `${n.data?.kind || (kind === "chart" ? "area" : "tile")} ${kind}` };
    })
    .filter((w) => !search || w.name.toLowerCase().includes(search))
    .sort((a, b) => a.name.localeCompare(b.name));
  // ON THIS CANVAS, for the nodes worth finding again on a busy canvas. Each
  // is listed in its own drawer, by name; double-click jumps to it.
  //   copy: true  -> it can also be DRAGGED out as a new node with the same
  //                  settings (lots of set-up, and a second one makes sense);
  //   copy: false -> find only (a copy would fire a second alert, write into
  //                  the same Footage page, or is rarely what anyone wants).
  // The measuring nodes (Counter, Cycle Time, ...) are every analytics node
  // in the Processing drawer, find only. Functions, formulas, cards, charts
  // and links have lists of their own.
  const paletteCatOf = (sp) => sp.palette?.cat || sp.category;
  const HERE_TYPES = {
    "filter.class": true, "filter.preprocess": true,
    "variable.mqtt": true, "output.mqtt_publish": true,
    "variable.rs485": true, "output.modbus_write": true,
    "variable.api": true, "output.webhook": true, "output.webhook_publish": true,
    "output.telegram": true, "output.email": true, "variable.schedule": true,
    "output.trigger": false, "integration.watch": false,
    "output.snapshot": false, "output.record": false,
    "common.inject": false, "note.group": false,
  };
  const hereCopy = (type) => {
    if (type in HERE_TYPES) return HERE_TYPES[type];
    const sp = registry[type];
    if (sp && sp.category === "analytics" && paletteCatOf(sp) === "zone") return false;
    return undefined;                                    // not listed
  };
  const hereByCat = {};
  for (const n of nodes || []) {
    const copy = hereCopy(n.type);
    if (copy === undefined) continue;
    const sp = registry[n.type] || {};
    const d = n.data || {};
    const name = String(d.name || d.page_name || "").trim() || sp.label || n.type;
    if (search && !name.toLowerCase().includes(search)
        && !String(sp.label || "").toLowerCase().includes(search)) continue;
    const detail = n.type === "filter.class" ? (d.classes || []).join(", ") || "all classes"
      : d.topic ? String(d.topic) : "";
    const { fnId, pinned, builder_edit, ...rest } = d;
    (hereByCat[paletteCatOf(sp)] ||= []).push({
      id: n.id, type: n.type, name, copy, data: rest,
      sub: [sp.label, detail].filter(Boolean).join(" · "),
    });
  }
  for (const list of Object.values(hereByCat)) list.sort((a, b) => a.name.localeCompare(b.name));
  // Every link name already on this canvas, with how many Link ins and Link
  // outs carry it, so a name made once is picked rather than retyped (a
  // typo'd jump name carries nothing and says nothing). Dragging one out is
  // a Link in or a Link out already named; the side that has none yet is the
  // one the name is still waiting for.
  // DOUBLE-CLICK an "On this canvas" entry to jump to it. Several nodes behind
  // one entry (copies of a formula, the ins and outs of a link): each double
  // click goes to the next one.
  const hop = useRef({});
  const jumpTo = (key, ids) => {
    if (!ids || !ids.length || !onShowNode) return;
    const i = (hop.current[key] ?? -1) + 1;
    hop.current[key] = i % ids.length;
    onShowNode(ids[i % ids.length]);
  };
  const jumpHint = " - double-click to find it on the canvas";
  const linkNames = (() => {
    const seen = new Map();
    for (const n of nodes || []) {
      if (n.type !== "common.link_in" && n.type !== "common.link_out") continue;
      const name = String(n.data?.link || "").trim();
      if (!name) continue;
      const row = seen.get(name) || { name, ins: 0, outs: 0, ids: [] };
      if (n.type === "common.link_in") row.ins += 1; else row.outs += 1;
      row.ids.push(n.id);
      seen.set(name, row);
    }
    return [...seen.values()]
      .filter((r) => !search || r.name.includes(search))
      .sort((a, b) => a.name.localeCompare(b.name));
  })();
  const paletteCat = (sp) => sp.palette?.cat || sp.category;
  const paletteGroup = (sp) => sp.palette?.group || sp.group || "";
  // A node may be drawn in more drawers than its home (spec.palette.also_in:
  // the Delay in Common as well as Logic). Not while SEARCHING: a result
  // list that shows one node twice reads as two different nodes.
  const inDrawer = (sp, cat) => paletteCat(sp) === cat
    || (!search && (sp.palette?.also_in || []).includes(cat));
  const groups = PALETTE_ORDER.map((cat) => ({
    cat,
    style: CATEGORY_STYLE[cat],
    items: Object.entries(registry)
      .filter(([, s]) => inDrawer(s, cat) && !s.deprecated && !s.hidden)
      // Searching only the LABEL meant the palette could not find a node by
      // what it DOES: "modbus" missed the Modbus Register until that word was
      // in its name, and "http" missed the Webhook. The description and the
      // sub-heading are what an operator actually remembers.
      .filter(([type, s]) => !search || nodeMatches(s, type, search)),
  }));

  // Every variable this graph publishes, with its live value. The canvas shows
  // them scattered across whatever node produced them; this is the flat list
  // of what EXISTS, which is what you want when writing a formula or wondering
  // whether a name is taken.
  // fnId -> the node that publishes it, so a row can jump to its node. The
  // compiled function does not carry the node id (it is a config document, not
  // an editor object), so it is resolved from the canvas.
  // A branch is LOGIC, not a variable (see isBranchFn). They stay readable
  // where they mean something -- on the node itself, and in the Debug
  // drawer's Values tab, which lists what the graph publishes rather than
  // what an operator declared.
  const logicBases = new Set((nodes || [])
    .filter((n) => String(n.type || "").startsWith("branch.")
                   || n.type === "logic.switch")
    .map((n) => slug(n.data?.fnId))
    .filter(Boolean));
  const published = (functions || [])
    .filter((f) => !isBranchFn(f.id, logicBases))
    .filter((f) => !search || (f.name || f.id).toLowerCase().includes(search))
    .map((f) => {
      const metrics = f.metrics || Object.keys(values || {})
        .filter((k) => k.startsWith(`fn_${f.id}_`))
        .map((k) => k.slice(`fn_${f.id}_`.length));
      const metric = metrics[0];
      const node = nodeByFn?.[f.id];
      // A Set Variable publishes under the VARIABLE it writes, while the
      // compiled rule carries the NODE's name -- and nobody renames a Set
      // Variable, so three of them put three rows called "Set Variable" in
      // this drawer and the names an operator actually chose (object_name,
      // object_type, no_object) appeared nowhere. Looking up a variable you
      // created and not finding it is indistinguishable from it not existing,
      // which is what was reported. The variable's own name is the row.
      const owner = (nodes || []).find((n) => n.id === node);
      const asVar = owner && owner.type === "math.setvar";
      // Only what the person made: a variable they declared, a Variable node
      // storing a value, or a Change Variable. A counter, a schedule or a
      // table reading is not a variable, and listing them made people ask
      // why there were variables they never created.
      const mine = asVar || (owner && String(owner.type).startsWith("variable.")
                             && owner.type !== "variable.schedule")
        || (customVars || []).some((v) => v.var === f.id);
      return { ...f, metric, key: metric ? `fn_${f.id}_${metric}` : null,
               name: asVar ? f.id : f.name, node, mine: !!mine };
    })
    .filter((f) => f.mine);

  // A Data Table node writes into a table that must already exist -- tables
  // are designed on the Database page, and the compiler refuses a node naming
  // one that is not there. So the node is not draggable until there is one:
  // dropping a node that cannot compile, to be told why afterwards, is a
  // worse way to learn that rule than not being able to pick it up.
  const tables = vars.tables || [];
  const savedQueries = vars.saved_queries || [];
  // A Query is gated the same way and for the same reason: it reads tables,
  // so with none designed there is nothing for it to read.
  // Board terminals handed to another program (Board I/O, hub settings). A
  // node is only pickable while it has SOME terminal left: with every port
  // released, dropping one would hand the operator a node that can never be
  // anything but idle. Gated here for the same reason Data Table is -- being
  // told after the drop is a worse way to learn the rule than not being able
  // to pick it up.
  const gone = new Set(released || []);
  const PORTS_OF = {
    "variable.board_input": ["DI0", "DI1"],
    "output.io_out": ["DO0", "DO1"],
    "output.buzzer": ["BUZZER"],
    "output.io_pwm": ["L0", "L1"],
  };
  const allReleased = (type) => {
    const ports = PORTS_OF[type];
    return !!ports && ports.every((id) => gone.has(id));
  };
  const blocked = (type) => (["output.dataset", "storage.query"].includes(type)
    && !tables.length) || allReleased(type);
  const lockedWhy = (type) => (allReleased(type)
    ? "Its terminals are handed to another program - take one back on"
    : blocked(type) ? "No tables yet - create one on the" : null);

  // `extra` presets the dropped node's data (a chosen zone, a chosen model).
  function dragProps(type, extra) {
    if (blocked(type)) return { draggable: false };
    return {
      draggable: editable,
      onDragStart: (e) => {
        e.dataTransfer.setData("application/x-wf-node",
                               JSON.stringify({ type, data: extra || null }));
        e.dataTransfer.effectAllowed = "copy";
      },
    };
  }

  return (
    // Column, not a single scroller: the filter is a fixed header and only the
    // list below it scrolls, so the box you type in cannot scroll away from the
    // list it is filtering. (`position: sticky` would work too, but it leaks the
    // list under the card's 10px padding as it passes.)
    <div className="panel-card wf-palette"
         style={{ alignSelf: "stretch", padding: 10, flex: 1, minWidth: 0,
                  display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* The filter belongs on top of what it filters. It used to sit in the
          page header, a screen-width away from the nodes it hides - and it
          hides nodes HERE. Enter still jumps the canvas to the first node on it
          that already exists, which is the other half of what it is for. */}
      <input className="input" placeholder="Search nodes…" value={searchText || ""}
             style={{ flex: "none", marginBottom: 8 }}
             onChange={(e) => onSearch?.(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && onSearchEnter?.()} />
      {/* Under the search box, where the nodes are: every node, what it
          connects to and what each wire means. */}
      <Link className="btn btn-sm btn-accent" data-node-guideline-link to="/workflow/guideline"
            title="What every node does, what it connects to, and what each connection means"
            style={{ flex: "none", marginBottom: 8, justifyContent: "center" }}>
        Nodes Guideline
      </Link>
      <div style={{ overflowY: "auto", flex: 1, minHeight: 0 }}>
      {(() => {
        // The stage last given a heading. A heading belongs to the first
        // drawer of its stage that is actually DRAWN: while searching, a
        // drawer with no match is not drawn, and neither is a heading over
        // nothing. (A beta tester typed "mqtt" and saw a screen of empty
        // Filters / Variables / Database drawers and stage titles; the two
        // MQTT nodes were there, under ACTIONS, below the fold.)
        let shownStage = null;
        return groups.map(({ cat, style, items }) => {
        // The stage this category belongs to, labelled once when it starts.
        // The categories are already ordered so each stage is contiguous
        // (check_palette_colors.mjs enforces it).
        const stage = STAGES.find((s) => s.id === style.stage);
        const headingFor = (first, top) => first ? (
          <div key={`s-${stage.id}`}
               style={{ display: "flex", alignItems: "center", gap: 6,
                        margin: top ? "0 2px 5px" : "12px 2px 5px",
                        fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
                        textTransform: "uppercase", color: stage.base }}>
            <span style={{ width: 6, height: 6, borderRadius: 2,
                           background: stage.base, flex: "none" }} />
            {stage.title}
          </div>
        ) : null;
        const body = (() => {
        // Database lists the TABLES themselves, the way Zones lists zones:
        // what you want on the canvas is "the bay log", not "a Data Table
        // node I then have to point at the bay log".
        if (cat === "database") {
          const ts = tables.filter((t) => !search
            || t.name.toLowerCase().includes(search)
            || t.key.toLowerCase().includes(search));
          // The tables are shown as themselves ("the bay log", not "a Data
          // Table node I then point at the bay log"), so output.dataset is
          // spoken for. Every OTHER node in this drawer still has to be
          // listed, the way Zones lists its functions beside its zones --
          // without this a node whose palette.cat is `database` is in the
          // group and drawn nowhere, which is invisible rather than wrong.
          const fns = items.filter(([type]) => type !== "output.dataset");
          // The SELECTs somebody kept on the Database page, offered by NAME.
          // Dragging one out is the point of saving it: the node arrives with
          // the statement already in it and wired to the tables it reads,
          // rather than as an empty Query to retype from memory.
          const qs = (savedQueries || []).filter((q) => !search
            || q.name.toLowerCase().includes(search)
            || q.sql.toLowerCase().includes(search));
          // The Data Table NODE is drawn as the tables themselves, so a
          // search for it ("data table", "record") keeps the drawer and lists
          // every table -- or says there are none yet.
          const dtWanted = !!search && items.some(([t]) => t === "output.dataset");
          if (dtWanted) ts.splice(0, ts.length, ...tables);
          if (search && !dtWanted && !(ts.length + qs.length + fns.length)) {
            return null;
          }
          return (
            <Drawer key={cat} title={style.title} color={style.color}
                    icon={style.icon} count={ts.length + qs.length + fns.length}
                    open={!!search || !!openCats[cat]}
                    onToggle={() => toggleCat(cat)}>
              <SubHead>Data Tables ({ts.length})</SubHead>
              {tables.length === 0 ? (
                <p className="muted small">
                  No tables yet - create one on the{" "}
                  <Link to="/database">Database</Link> page, then drag it here
                  to record into it.
                </p>
              ) : (
                ts.map((t) => (
                  <PaletteItem key={t.key} color={style.color}  label={t.name}
                               sub={`${t.columns.length} column`
                                    + `${t.columns.length === 1 ? "" : "s"}`}
                               {...dragProps("output.dataset",
                                             { dataset: t.key, name: t.name })} />
                ))
              )}
              {qs.length > 0 && <SubHead>Saved queries ({qs.length})</SubHead>}
              {qs.map((q) => (
                <PaletteItem key={q.key} color={style.color} label={q.name}
                             sub={q.tables.length
                               ? `reads ${q.tables.join(", ")}`
                               : "reads no table this system has"}
                             {...dragProps("storage.query",
                                           { name: q.name, sql: q.sql,
                                             _tables: q.tables })} />
              ))}
              {fns.length > 0 && <SubHead>Ask of them</SubHead>}
              {fns.map(([type, sp]) => (
                <PaletteItem key={type} color={style.color}  label={sp.label}
                             sub={sp.description} locked={lockedWhy(type)}
                             {...dragProps(type)} />
              ))}
            </Drawer>
          );
        }
        // Filters: the two halves of "what am I looking at" -- which
        // CLASSES, and which PLACE. The zones drawn on this box are listed
        // under the Zone Filter they are an argument to, because picking one
        // IS the filter. They used to sit three drawers down beside the
        // functions that measure them, which is where you look to answer a
        // different question. AI Processing needs no special case now: it is
        // Zones and Objects, which the generic drawer already groups.
        if (cat === "filter") {
          const zs = zoneList.filter((z) => !search
            || z.name.toLowerCase().includes(search));
          const byGroup = {};
          items.forEach((it) => {
            const g = paletteGroup(it[1]) || "Class";
            (byGroup[g] = byGroup[g] || []).push(it);
          });
          const row = ([type, sp]) => (
            <PaletteItem key={type} color={style.color} label={sp.label}
                         sub={sp.description} locked={lockedWhy(type)}
                         {...dragProps(type)} />
          );
          if (search && !(items.length + zs.length)) return null;
          return (
            <Drawer key={cat} title={style.title} color={style.color}
                    icon={style.icon} count={items.length + zs.length}
                    open={!!search || !!openCats[cat]}
                    onToggle={() => toggleCat(cat)}>
              {(byGroup.Class || []).length > 0 && <SubHead>Class</SubHead>}
              {(byGroup.Class || []).map(row)}
              {(!search || (byGroup.Zone || []).length + zs.length > 0) && (
                <SubHead>Zone</SubHead>
              )}
              {(byGroup.Zone || []).map(row)}
              {/* ONE colour for every zone, deliberately. Per-zone colours made
                  the palette a rainbow in which the colour meant nothing you
                  could act on -- the NAME is what tells zones apart. */}
              {zs.length === 0 ? (!search && (
                <p className="muted small">
                  No zones yet - draw them on <Link to="/zones">Camera Setup's
                  Zones tab</Link>.
                </p>
              )) : zs.map((z) => (
                <PaletteItem key={z.id} color={style.color} label={z.name}
                             sub={z.kind + (z.enabled === false ? " · disabled" : "")}
                             {...dragProps("zone.ref", { zone: z.id })} />
              ))}
              {/* Pre-process (the Picture group) was filed in this drawer and
                  drawn by nothing: only Class and Zone were listed, so the
                  node existed in the registry and could not be picked up. */}
              {(byGroup.Picture || []).length > 0 && <SubHead>Picture</SubHead>}
              {(byGroup.Picture || []).map(row)}
            </Drawer>
          );
        }
        // The Variables drawer holds everything about values, because that is
        // where someone looking for a variable looks: the node types, the
        // values the graph is publishing RIGHT NOW, the names declared but
        // not yet fed by anything, and the button that declares another.
        if (cat === "variable") {
          const declared = (customVars || []).map((v) => ({
            ...v, key: `fn_${v.var}_value`,
          })).filter((v) => !search || v.name.toLowerCase().includes(search)
                            || v.var.toLowerCase().includes(search));
          const liveKeys = new Set(published.map((f) => f.key).filter(Boolean));
          // "Unused" is not a warning, it is a fact worth seeing: a declared
          // name that nothing publishes reads as 0 wherever it is used, which
          // is the quietest bug this editor can produce.
          //
          // But "nothing PUBLISHES it" is not the same as "nothing uses it",
          // and reading the compiled functions alone got that wrong on the
          // commonest case there is: a TEXT constant wired into a Data Table
          // column compiles INTO the column, as a constant, and publishes no
          // fn_<name>_value at all. Its node sits on the canvas, wired, doing
          // its job, and the panel called it unused -- and offered to delete
          // it saying nothing would change.
          const onCanvas = usedVarNames(nodes);
          // Every constant ON THE CANVAS, with the value it is set to. Read
          // from the nodes rather than from the published values, because
          // that is exactly what a constant may not have: wired into a Data
          // Table column it compiles into the column and publishes nothing.
          const constants = (nodes || [])
            .filter((n) => n.type === "variable.number")
            .map((n) => {
              const nd = n.data || {};
              const name = nd.name || nd.fnId || "constant";
              const raw = nd.value_type === "boolean"
                ? (nd.value ? "on" : "off") : nd.value;
              return { id: n.id, name,
                       key: `fn_${nd.fnId || slug(name)}_value`,
                       // What dragging this row out drops: a constant like it.
                       drag: { type: "variable.number",
                               data: { name: nd.name || name,
                                       ...(nd.fnId ? { fnId: nd.fnId } : {}),
                                       value: nd.value ?? 0,
                                       value_type: nd.value_type || "number" } },
                       shown: raw === undefined || raw === "" ? "-" : raw };
            })
            .filter((c) => !search || c.name.toLowerCase().includes(search));
          const unused = declared.filter((v) => isUnusedVar(v, liveKeys, onCanvas));
          if (search && !(items.length + published.length + declared.length
                          + constants.length)) return null;
          return (
            <Drawer key={cat} title={style.title} color={style.color} 
                    icon={style.icon}
                    count={items.length + published.length + constants.length + unused.length}
                    open={!!search || !!openCats[cat]}
                    onToggle={() => toggleCat(cat)}>
              {items.map(([type, sp]) => (
                <PaletteItem key={type} color={style.color}  label={sp.label}
                             sub={sp.description} locked={lockedWhy(type)}
                             {...dragProps(type)} />
              ))}
              {editable && (
                <button type="button" className="btn btn-sm"
                        onClick={() => setNewVarOpen(true)}
                        style={{ width: "100%", marginBottom: 6 }}>
                  + New variable…
                </button>
              )}
              {published.length > 0 && (
                <>
                  <SubHead>In use ({published.length})</SubHead>
                  {published.map((f) => (
                    <ValueRow key={f.id} label={f.name || f.id} keyName={f.key}
                              value={f.key ? values?.[f.key] : undefined}
                              editable={editable}
                              onClick={() => onShowNode?.(f.node)} />
                  ))}
                </>
              )}
              {/* The CONSTANTS on this canvas, with what each one is set to.
                  They belong in this drawer -- they are variables, and this
                  is where somebody looking for a variable looks -- but not
                  under "Live values": a constant publishes nothing when it is
                  wired into a Data Table column (it compiles INTO the column),
                  so it was in the drawer's blind spot and read as missing. */}
              {constants.length > 0 && (
                <>
                  <SubHead>Constant values ({constants.length})</SubHead>
                  {constants.map((c) => (
                    <ValueRow key={c.id} label={c.name} keyName={c.key}
                              dragNode={c.drag}
                              value={c.shown} editable={editable}
                              onClick={() => onShowNode?.(c.id)} />
                  ))}
                </>
              )}
              {unused.length > 0 && (
                <>
                  <SubHead>Unused ({unused.length})</SubHead>
                  {/* An unused name is a declaration nothing feeds, and the
                      only thing to do with one is either wire it up or be rid
                      of it -- so the row carries the second. Only offered
                      here: a variable something PUBLISHES is deleted by
                      deleting the node that publishes it. */}
                  {unused.map((v) => (
                    <ValueRow key={v.var} label={v.name} keyName={v.key}
                              value={undefined} editable={editable} dim
                              onDelete={editable && onDeleteVar
                                ? async () => {
                                    if (!await confirm({
                                      title: `Delete the variable "${v.name}"?`,
                                      description: "Nothing publishes it, so "
                                        + "nothing on the canvas changes. Any "
                                        + "expression that types its name will "
                                        + "read 0.",
                                      destructive: true })) return;
                                    onDeleteVar(v.var);
                                  }
                                : null} />
                  ))}
                </>
              )}
            </Drawer>
          );
        }
        if (!items.length) return null;
        // Several models on one camera: give each its own Detection Stream item
        // so "which model" is a choice made once, when the node is dragged in,
        // instead of a field to remember afterwards.
        const perModel = cat === "source" && (models || []).length >= 2
          ? (models || []).filter((m) => !search || m.toLowerCase().includes(search))
          : [];
        // Functions are many and mean different things: measured at a PLACE,
        // measured on the objects in frame, or computed from other numbers.
        // Sub-headings inside the drawer, in a fixed order, so the list is
        // read rather than scanned.
        const subs = [];
        const grouped = {};
        items.forEach(([type, sp]) => {
          // A guest from another drawer (also_in) sits ungrouped here: its
          // sub-heading belongs to its home drawer.
          const g = paletteCat(sp) === cat ? paletteGroup(sp) : "";
          (grouped[g] = grouped[g] || []).push([type, sp]);
        });
        SUBGROUP_ORDER.forEach((g) => { if (grouped[g]) subs.push([g, grouped[g]]); });
        Object.keys(grouped).forEach((g) => {
          if (g && !SUBGROUP_ORDER.includes(g)) subs.push([g, grouped[g]]);
        });
        const ungrouped = grouped[""] || [];
        return (
          <Drawer key={cat} title={style.title} color={style.color} 
                  icon={style.icon}
                  count={items.length + perModel.length
                         + (cat === "logic" ? fnHere.length : 0)
                         + (cat === "math" ? formulasHere.length : 0)
                         + (cat === "dashboard" ? widgetsHere.length : 0)
                         + (hereByCat[cat] || []).length
                         + (cat === "common" ? linkNames.length : 0)
                         + (cat === "integration" ? thresholds.length : 0)}
                  open={!!search || !!openCats[cat]} onToggle={() => toggleCat(cat)}>
            {ungrouped.map(([type, sp]) => (
              <PaletteItem key={type} color={style.color}  label={sp.label}
                           sub={sp.description} locked={lockedWhy(type)}
                           {...dragProps(type)} />
            ))}
            {subs.map(([group, list]) => (
              <div key={group} style={{ marginBottom: 2 }}>
                <div className="muted" style={{ fontSize: 11, fontWeight: 700,
                                                letterSpacing: ".05em",
                                                textTransform: "uppercase",
                                                margin: "2px 0 4px 3px" }}>
                  {group}
                </div>
                {list.map(([type, sp]) => (
                  <PaletteItem key={type} color={style.color}  label={sp.label}
                               sub={sp.description} locked={lockedWhy(type)}
                               {...dragProps(type)} />
                ))}
              </div>
            ))}
            {/* The functions THIS canvas defines, offered by name. Dragging
                one out is a Call of it, already pointed at the right
                function -- the same reason the Filters drawer lists the zones
                drawn on this box rather than one generic Zone Filter to go
                and configure. A function nobody can find is a function that
                gets built a second time instead of called. */}
            {cat === "integration" && thresholds.length > 0 && (
              <div style={{ marginBottom: 2 }}>
                <SubHead>Thresholds ({thresholds.length})</SubHead>
                {thresholds.map((t) => (
                  <PaletteItem key={t.id} color={style.color} label={t.name}
                               sub={t.trigger ? "raises an alert - drag to use the value"
                                 : "1 while it is over - drag to use it"}
                               {...dragProps("variable.ref",
                                             { name: t.name, key: t.key, io: "in" })} />
                ))}
              </div>
            )}
            {(() => {
              // One "On this canvas" section per drawer: the drawer's own
              // special list (functions, formulas, widgets) and then every
              // other listed node that lives in this drawer.
              const generic = hereByCat[cat] || [];
              const special = cat === "logic" ? fnHere.length
                : cat === "math" ? formulasHere.length
                : cat === "dashboard" ? widgetsHere.length : 0;
              if (!generic.length && !special) return null;
              return (
                <div style={{ marginBottom: 2 }}>
                  <SubHead>On this canvas ({generic.length + special})</SubHead>
                  {cat === "dashboard" && widgetsHere.map((w) => (
                    <PaletteItem key={w.id} color={style.color} label={w.name}
                                 sub={w.sub + jumpHint}
                                 onDoubleClick={() => jumpTo(`wd:${w.id}`, [w.id])}
                                 {...dragProps(w.type, w.data)} />
                  ))}
                  {cat === "math" && formulasHere.map((f) => (
                    <PaletteItem key={f.key} color={style.color} label={f.name}
                                 sub={f.expr + (f.count > 1 ? `  ·  used ${f.count}×` : "") + jumpHint}
                                 onDoubleClick={() => jumpTo(`fx:${f.key}`, f.ids)}
                                 {...dragProps("analytics.expr", f.data)} />
                  ))}
                  {cat === "logic" && fnHere.map((f) => (
                    <PaletteItem key={f.name} color={style.color} label={f.name}
                                 sub={(!f.closed ? "no Function End yet"
                                   : f.params.length
                                     ? `${f.params.length} parameter`
                                       + `${f.params.length === 1 ? "" : "s"}: `
                                       + f.params.join(", ")
                                     : "no parameters") + jumpHint}
                                 onDoubleClick={() => jumpTo(`fn:${f.name}`, nodes
                                   .filter((n) => n.type === "function.frame"
                                     && String(n.data?.fn || "").trim().toLowerCase() === f.name.toLowerCase())
                                   .map((n) => n.id))}
                                 {...dragProps("function.call", { fn: f.name })} />
                  ))}
                  {generic.map((g) => (
                    <PaletteItem key={g.id} color={style.color} label={g.name}
                                 sub={g.sub + (g.copy ? jumpHint
                                   : " - double-click to find it on the canvas (find only)")}
                                 cursor={g.copy && editable ? undefined : "pointer"}
                                 onDoubleClick={() => jumpTo(`nd:${g.id}`, [g.id])}
                                 {...(g.copy ? dragProps(g.type, g.data) : {})} />
                  ))}
                </div>
              );
            })()}
            {cat === "common" && linkNames.length > 0 && (
              <div style={{ marginBottom: 2 }}>
                <SubHead>Links on this canvas ({linkNames.length})</SubHead>
                {linkNames.map((l) => (
                  <div key={l.name}
                       style={{ border: "1px solid rgba(128,128,128,.25)",
                                borderLeft: `3px solid ${style.color}`,
                                borderRadius: 7, padding: "6px 8px", marginBottom: 6,
                                background: "var(--surface, transparent)" }}>
                    <div className="mono" style={{ fontSize: 12, fontWeight: 600,
                                                   overflow: "hidden",
                                                   textOverflow: "ellipsis",
                                                   whiteSpace: "nowrap", cursor: "pointer" }}
                         onDoubleClick={() => jumpTo(`ln:${l.name}`, l.ids)}
                         title={l.name + jumpHint}>
                      {l.name}
                    </div>
                    <div style={{ display: "flex", gap: 6, marginTop: 5 }}>
                      {[["common.link_in", "Link in", l.ins,
                         "Receives from every Link out with this name"],
                        ["common.link_out", "Link out", l.outs,
                         "Sends to every Link in with this name"]].map(([type, text, n, tip]) => (
                        <div key={type} {...dragProps(type, { link: l.name })}
                             title={`${tip} - drag onto the canvas`}
                             style={{ flex: 1, textAlign: "center", fontSize: 11,
                                      fontWeight: 600, borderRadius: 6,
                                      padding: "3px 6px",
                                      cursor: editable ? "grab" : "default",
                                      // The side nothing carries yet is the one
                                      // this name is waiting for.
                                      border: n === 0
                                        ? `1px solid ${style.color}`
                                        : "1px solid rgba(128,128,128,.3)",
                                      opacity: n === 0 ? 1 : 0.8 }}>
                          {text} <span className="muted">{n}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {perModel.map((m) => (
              <PaletteItem key={`model-${m}`} color={style.color}
                           label={`Detection Stream - ${m}`}
                           sub={`only this model's detections`}
                           {...dragProps("source.detection", { model: m })} />
            ))}
          </Drawer>
        );
        })();
        if (!body) return null;
        const first = !!stage && shownStage !== stage.id;
        const top = shownStage === null;
        if (stage) shownStage = stage.id;
        return <StageGroup key={cat} heading={headingFor(first, top)}>{body}</StageGroup>;
      });
      })()}
      </div>

      {/* Declaring a variable is naming a thing that outlives the click, so it
          gets a real dialog you can correct -- window.prompt could not even
          show the key the name becomes. Same form the Variables panel uses
          (NewVariableForm) -- a plain name, or a "Constant" typed right here. */}
      <Dialog open={newVarOpen} title="New variable"
              onClose={() => setNewVarOpen(false)}>
        <NewVariableForm editable={editable}
                        onCreate={(name, varName, constOpts) => {
                          onCreateVar?.(name, varName, constOpts);
                          setNewVarOpen(false);
                        }}
                        onCancel={() => setNewVarOpen(false)} />
      </Dialog>
    </div>
  );
}

/** A value you can drag onto the canvas: a live reading or a declared name.
 *
 *  Dropping it IS a Variable node already pointed at that value, which is the
 *  whole point -- the alternative was dragging a blank node out and then
 *  searching for a key that was already named on the list you dragged from.
 */
// A Function Start's parameter names, as rows. One port per row, in order.
// Names are folded the way every other identifier on this canvas is, because
// a parameter is written into expressions downstream and a space in one would
// be a name nothing could refer to.
function ParamList({ value, disabled, onChange }) {
  const rows = value || [];
  const put = (i, v) => onChange(rows.map((r, j) => (j === i ? v : r)));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", gap: 4 }}>
          <input className="input mono" value={r} disabled={disabled}
                 placeholder="name" style={{ flex: 1 }}
                 onChange={(e) => put(i, e.target.value.toLowerCase()
                   .replace(/[^a-z0-9_]+/g, "_").slice(0, 24))} />
          <button className="btn btn-sm" disabled={disabled}
                  title="Remove this parameter"
                  onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="btn btn-sm" disabled={disabled}
              onClick={() => onChange([...rows, ""])}>+ Parameter</button>
    </div>
  );
}

//: The kinds a parameter or return value may be declared as. A label for the
//: reader, not a type the compiler checks: every wire on this canvas carries
//: a value, and an "event" is a value that only matters when it arrives.
const FN_PORT_TYPES = ["any", "event", "number", "boolean", "string"];

/** One list of named, typed ports: a function's parameters or its returns. */
function FnPortList({ names, types, disabled, onChange, addLabel, word }) {
  const rows = names || [];
  const kinds = types || {};
  const emit = (nextRows, nextKinds) => {
    const keep = {};
    for (const r of nextRows) if (r && nextKinds[r]) keep[r] = nextKinds[r];
    onChange(nextRows, keep);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", gap: 6, alignItems: "center",
                              padding: "4px 6px 4px 10px", borderRadius: 8,
                              background: "rgba(128,128,128,.08)" }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                         background: "#F97316" }} />
          <input className="input mono" value={r} disabled={disabled}
                 placeholder="name" style={{ flex: 1, minWidth: 0 }}
                 onChange={(e) => {
                   const v = e.target.value.toLowerCase()
                     .replace(/[^a-z0-9_]+/g, "_").slice(0, 24);
                   const nk = { ...kinds };
                   if (kinds[r]) { nk[v] = kinds[r]; delete nk[r]; }
                   emit(rows.map((x, j) => (j === i ? v : x)), nk);
                 }} />
          <select className="input" value={kinds[r] || "any"} disabled={disabled}
                  style={{ width: 92 }}
                  onChange={(e) => emit(rows, { ...kinds,
                    [r]: e.target.value === "any" ? undefined : e.target.value })}>
            {FN_PORT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button className="btn btn-sm" disabled={disabled}
                  title={"Remove this " + word}
                  onClick={() => emit(rows.filter((_, j) => j !== i), kinds)}>×</button>
        </div>
      ))}
      <button className="btn btn-sm" disabled={disabled}
              style={{ borderStyle: "dashed" }}
              onClick={() => emit([...rows, ""], kinds)}>{addLabel}</button>
    </div>
  );
}

/** The settings of a function frame: its name, what it takes (the Start's
 *  outputs) and what it gives back (the End's inputs). */
function FunctionFrameInspector({ node, nodes, editable, onChange, onFnData }) {
  const k = String(node.data?.fn || "").trim().toLowerCase();
  const same = (n) => String(n.data?.fn || "").trim().toLowerCase() === k;
  const start = nodes.find((n) => n.type === "function.start" && same(n));
  const end = nodes.find((n) => n.type === "function.end" && same(n));
  const calls = nodes.filter((n) => n.type === "function.call" && same(n)).length;
  return (<>
    <Field label="Function Name"
           hint="Renaming updates the Start, the End and every Call of this function.">
      <input className="input mono" value={node.data?.fn || ""} disabled={!editable}
             placeholder="e.g. detect_bump"
             onChange={(e) => onChange({
               fn: e.target.value.toLowerCase().replace(/[^a-z0-9_]+/g, "_").slice(0, 30),
             })} />
    </Field>
    <Field label="Parameters (Start outputs)"
           hint="One port per name on the Start and on every Call. With none, the plain run port is the way in.">
      <FnPortList names={start?.data?.params || []} types={start?.data?.types}
                  disabled={!editable || !start} word="parameter" addLabel="+ Add parameter"
                  onChange={(params, types) => onFnData("function.start", { params, types })} />
    </Field>
    <Field label="Returns (End inputs)"
           hint="One port per value the function gives back, on the End and on every Call. With none, the End takes a single result.">
      <FnPortList names={end?.data?.returns || []} types={end?.data?.types}
                  disabled={!editable || !end} word="return value" addLabel="+ Add return value"
                  onChange={(returns, types) => onFnData("function.end", { returns, types })} />
    </Field>
    <p className="muted small" style={{ margin: "10px 0 0", paddingTop: 10,
                                        borderTop: "1px solid rgba(128,128,128,.2)" }}>
      Used by {calls} Call node{calls === 1 ? "" : "s"}. Renaming updates every caller.
      Every Call runs its own copy of the body.
    </p>
  </>);
}

/** One live value as a row shows it.
 *
 *  A value can be a WORD now that a rule which picks one reaches the browser,
 *  and a word does not round: `Math.round("No Object Detected" * 100) / 100`
 *  is NaN, so every text variable in the drawer read NaN the moment text
 *  stats started arriving -- which looks like a broken rule rather than a
 *  working one. Numbers still round to two places, a numeric STRING is still
 *  treated as the number it is (that is what the stats channel sends for a
 *  value read back off a device), and anything else is shown as it stands.
 */
export function showValue(value) {
  if (value === undefined || value === null) return "-";
  if (typeof value === "number") {
    return Number.isFinite(value) ? Math.round(value * 100) / 100 : "-";
  }
  const asNum = Number(value);
  if (String(value).trim() !== "" && Number.isFinite(asNum)) {
    return Math.round(asNum * 100) / 100;
  }
  return String(value);
}

/** Is this declared variable one nothing publishes and nothing uses?
 *
 *  "Unused" is not a warning, it is a fact worth seeing: a declared name that
 *  nothing publishes reads as 0 wherever it is used, which is the quietest
 *  bug this editor can produce.
 *
 *  It used to require that the variable have NO live value either, and that
 *  is what made a variable VANISH when its node was deleted: the compiled
 *  functions lose it immediately, so it drops out of Live values, while the
 *  running app goes on publishing the last value it had until it picks the
 *  new graph up. For those seconds the name is in neither list, and an
 *  operator who has just deleted a node sees the variable they were looking
 *  for disappear entirely rather than move to Unused. Reported exactly that
 *  way: "after i remove from the flow, why it didnt show as not used
 *  variable? it turn out to be missing".
 *
 *  A stale reading says nothing about whether anything still writes the name,
 *  so it is not part of the question.
 */
export function isUnusedVar(v, liveKeys, onCanvas) {
  return !liveKeys.has(v.key) && !onCanvas.has(v.var);
}

/** Is `id` one of the rules a BRANCH node compiles into?
 *
 *  An If / Else if compiles to one rule per port (`<fnId>_b0`, `_b1`, ...) so
 *  the engine has something to gate on. They are logic, not values somebody
 *  declared, and listing them as variables put three rows called "If / Else
 *  if - branch 1 / branch 2 / otherwise" among an operator's own names. On a
 *  busy canvas the branches outnumber the variables.
 *
 *  `bases` is the set of branch nodes' fnIds, so a variable an operator
 *  happens to call `thing_b2` is not mistaken for one.
 */
export function isBranchFn(id, bases) {
  const at = String(id).lastIndexOf("_b");
  return at > 0 && /^\d+$/.test(String(id).slice(at + 2))
         && bases.has(String(id).slice(0, at));
}

function ValueRow({ label, value, keyName, editable, onClick, dim, onDelete,
                    dragNode = null }) {
  return (
    <div draggable={editable && !!keyName}
         onDragStart={(e) => {
           // A constant is dropped as a constant (same name, type and value);
           // everything else is a normal variable reading that value.
           e.dataTransfer.setData("application/x-wf-node", JSON.stringify(
             dragNode || { type: "variable.ref", data: { key: keyName, io: "in" } }));
           e.dataTransfer.effectAllowed = "copy";
         }}
         title={keyName ? `${label} - drag onto the canvas`
                        : "declared but nothing publishes it yet"}
         onClick={onClick}
         style={{ display: "flex", alignItems: "baseline", gap: 6, width: "100%",
                  border: "1px solid rgba(128,128,128,.25)",
                  borderLeft: `3px solid ${CATEGORY_STYLE.variable.color}`,
                  borderRadius: 7, padding: "5px 8px", marginBottom: 5,
                  background: "var(--surface, transparent)",
                  opacity: dim ? 0.72 : 1,
                  cursor: editable && keyName ? "grab" : "default",
                  color: "inherit", textAlign: "left" }}>
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                     textOverflow: "ellipsis", whiteSpace: "nowrap",
                     fontSize: 12, fontWeight: 600 }}>
        {label}
      </span>
      <strong style={{ fontSize: 13, color: value === undefined
                         ? "var(--muted, #6b6b6b)" : "var(--accent, #04bade)" }}>
        {showValue(value)}
      </strong>
      {onDelete && (
        // Stops its own click: the row is a drag handle and, for a live
        // value, a jump to the node that publishes it.
        <button type="button" className="btn btn-sm" title={`Delete ${label}`}
                onClick={(e) => { e.stopPropagation(); onDelete(); }}
                style={{ padding: "0 5px", lineHeight: "16px", fontSize: 12 }}>
          ×
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// inspector
// ---------------------------------------------------------------------------

// What each function metric MEANS, so a condition is written against the
// meaning rather than a bare key name. One map for every picker (Alert, When,
// cards) - two copies is how "pairs" ends up described two different ways.
const METRIC_INFO = {
  pairs: "pairs within the distance right now",
  nearest_px: "Closest distance - the nearest pair, in pixels (-1 = none seen)",
  alerts: "alerts fired so far (total)",
  // One key, three nodes: on Proximity and Safety Bubble it is the alarm
  // state, on Motion Tracking it is "Moving now". Said in one line rather
  // than made per-type -- the label map is flat, and a per-type map for one
  // metric is machinery nobody would remember to update.
  active: "1 while the condition holds, else 0 - on Motion Tracking, "
          + "Moving now",
  count: "objects inside right now",
  current: "objects inside right now",
  avg_s: "Average wait (s) - on Travel Time, the average trip",
  max_s: "Longest ever (s) - on Travel Time, the slowest trip",
  live_s: "Longest wait now (s)",
  last_s: "Latest trip time (s)",
  min_s: "Fastest trip (s)",
  total: "distinct visitors so far (total)",
  new: "new visitors this instant",
  in: "crossings in (total)",
  out: "crossings out (total)",
  // Shown as "Inside" on the node. An ESTIMATE, and said so wherever it is
  // offered: it assumes everyone who enters through this line also leaves
  // through it, which is true of a doorway and false of a room with two.
  net: "Inside - in minus out; estimated, assumes everyone leaves the same way",
  cross: "crossings either way (total) - what a line with no face reports",
  total_in: "crossings in since the app started (live counter's running tally)",
  total_out: "crossings out since the app started",
  total_net: "Inside (total) - total in minus total out; estimated",
  total_cross: "crossings either way since the app started",
  trips: "completed trips (total)",
  last_speed: "last trip's speed",
  avg_speed: "average speed",
  intrusions: "bubble intrusions so far (total)",
  inside_now: "intruders inside a bubble right now",
  overlap_pct: "how deep the worst encroachment is, %",
  value: "the formula's result, or the number an API value last read",
  ok: "1 while the endpoint is answering, 0 once it has been failing",
  remaining_s: "time left in the budget, seconds",
  used_s: "time the arrivals have spent, seconds",
  entries: "arrivals so far (total)",
  occupied_s: "how long the zone has been occupied right now, seconds",
  total_s: "occupied time accumulated, seconds",
  sessions: "times the zone went from empty to occupied (total)",
  found: "1 while a chosen object is there, 0 while none is",
  arrived: "objects that appeared this instant",
  // Motion Tracking's three, in the words the node uses on the canvas. The
  // keys are what the engine publishes and never move; these are the labels.
  tracked: "Being tracked - objects leaving a trail right now",
  idle: "Idle now - tracked objects that have stopped moving",
};
const metricLabel = (m) => METRIC_INFO[m] ? `${m} - ${METRIC_INFO[m]}` : m;

// Verdict Card states - what each VALUE of the wired metric means. Two modes:
// the simple pair (0 = normal label, 1 = alert label), or a custom list for
// MORE than two outcomes (0 idle, 1 warning, 2 violation) driven by whatever
// number the function, variable or formula sends.
//: The states a status card starts with when it is NOT fed by an alert: a
//: plain 0 / 1 reading, named for what it means rather than for compliance.
//: A card wired from a Trigger or a Condition keeps the simple normal / alert
//: pair instead -- there the 1 IS an alert.
export const DEFAULT_STATES = [
  { value: 0, label: "NOT DETECTED", tone: "empty" },
  { value: 1, label: "DETECTED", tone: "ok" },
];

function VerdictStates({ d, editable, set, onChange, wiredNames = [], fromAlert = false }) {
  const states = Array.isArray(d.states) ? d.states : [];
  function patchRow(i, fields) {
    onChange({ states: states.map((s, j) => (j === i ? { ...s, ...fields } : s)) });
  }
  // One state per WIRED input: the card weighs its wires (first that fires
  // wins), so with three rules wired in there are exactly four things to
  // name -- none of them, and each of the three. Built from the wire names
  // so the button hands back an editable list, not homework.
  const fromWires = () => onChange({ states: [
    { value: 0, label: (d.ok_label || "ALL CLEAR").slice(0, 24), tone: "ok" },
    ...wiredNames.slice(0, 6).map((name, i) => ({
      value: i + 1, label: String(name).slice(0, 24).toUpperCase(),
      tone: i === 0 ? "alert" : "warn",
    })),
  ] });
  if (!states.length) {
    return (
      <>
        {wiredNames.length > 1 && (
          <>
            <p className="muted small">
              {wiredNames.length} inputs wired - the first that fires decides
              the state. Unnamed, the card shows{" "}
              {["0 · " + (d.ok_label || "ALL CLEAR"),
                ...wiredNames.map((n, i) => `${i + 1} · ${String(n).toUpperCase()}`)]
                .join(" / ")}.
            </p>
            <button type="button" className="btn btn-sm" disabled={!editable}
                    style={{ marginBottom: 8 }}
                    onClick={fromWires}>
              Name these states (one per wired input)
            </button>
          </>
        )}
        {/* Label and colour side by side: they are one decision -- what this
            state looks like from across the room. Clearing the colour goes
            back to the stock green/red. */}
        <Field label="Normal label">
          <div className="wf-row">
            <input className="input" style={{ flex: 1 }}
                   value={d.ok_label ?? "COMPLIANT"}
                   disabled={!editable} onChange={set("ok_label")} />
            <input type="color" value={d.ok_color || "#16a34a"}
                   disabled={!editable} title="Colour for the normal state"
                   onChange={set("ok_color")} />
            {d.ok_color && (
              <button className="btn btn-sm" disabled={!editable} title="Stock green"
                      onClick={() => onChange({ ok_color: "" })}>reset</button>
            )}
          </div>
        </Field>
        <Field label="Alert label">
          <div className="wf-row">
            <input className="input" style={{ flex: 1 }}
                   value={d.alert_label ?? "VIOLATION"}
                   disabled={!editable} onChange={set("alert_label")} />
            <input type="color" value={d.alert_color || "#dc2626"}
                   disabled={!editable} title="Colour for the alert state"
                   onChange={set("alert_color")} />
            {d.alert_color && (
              <button className="btn btn-sm" disabled={!editable} title="Stock red"
                      onClick={() => onChange({ alert_color: "" })}>reset</button>
            )}
          </div>
        </Field>
        <p className="muted small">
          Value 0 shows the normal label; 1 shows the alert label, each in its
          colour.
        </p>
        <button className="btn btn-sm" disabled={!editable}
                onClick={() => onChange({ states: fromAlert ? [
                  { value: 0, label: d.ok_label || "COMPLIANT", tone: "ok" },
                  { value: 1, label: "WARNING", tone: "warn" },
                  { value: 2, label: d.alert_label || "VIOLATION", tone: "alert" },
                ] : DEFAULT_STATES })}>
          {fromAlert ? "Use custom states (3 or more)" : "Use named states"}
        </button>
      </>
    );
  }
  return (
    <Field label="States"
           hint="When the value equals this number, the card shows this label in this colour. Anything unlisted shows NO DATA.">
      {states.map((s, i) => (
        <div key={i} className="wf-row">
          <input className="input" type="number" style={{ flex: "0 0 64px" }}
                 value={s.value} disabled={!editable}
                 onChange={(e) => patchRow(i, { value: Number(e.target.value) })} />
          <input className="input" style={{ flex: "1 1 96px" }}
                 value={s.label} disabled={!editable}
                 onChange={(e) => patchRow(i, { label: e.target.value })} />
          {/* Picking "custom colour" materialises the row's own colour so
              the swatch beside it becomes the control; picking a stock tone
              clears it. The tone underneath still names the MEANING (and
              the glyph) -- custom only repaints. */}
          <select className="input" style={{ flex: "0 0 96px" }}
                  value={s.color ? "custom" : s.tone}
                  disabled={!editable}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "custom") {
                      patchRow(i, { color: { ok: "#16a34a", warn: "#d97706",
                                             alert: "#dc2626", empty: "#64748b" }[s.tone]
                                           || "#d97706" });
                    } else {
                      patchRow(i, { tone: v, color: undefined });
                    }
                  }}>
            <option value="ok">green</option>
            <option value="warn">amber</option>
            <option value="alert">red</option>
            <option value="empty">grey</option>
            <option value="custom">custom colour…</option>
          </select>
          {s.color && (
            <input type="color" style={{ flex: "0 0 auto" }}
                   value={s.color}
                   disabled={!editable} title="Exact colour for this state"
                   onChange={(e) => patchRow(i, { color: e.target.value })} />
          )}
          <button className="btn btn-sm" style={{ flex: "0 0 auto" }}
                  disabled={!editable} title="Remove"
                  onClick={() => onChange({ states: states.filter((_, j) => j !== i) })}>
            ✕
          </button>
        </div>
      ))}
      <div className="filter-bar">
        {states.length < 6 && (
          <button className="btn btn-sm wf-add" disabled={!editable}
                  onClick={() => onChange({ states: [...states,
                    { value: states.length, label: `STATE ${states.length}`,
                      tone: "warn" }] })}>
            + Add state
          </button>
        )}
        {wiredNames.length > 1 && wiredNames.length + 1 !== states.length && (
          <button className="btn btn-sm" disabled={!editable}
                  title="Replace these rows with one state per wired input"
                  onClick={fromWires}>
            Rebuild from the {wiredNames.length} wired inputs
          </button>
        )}
        {/* The normal / alert pair is offered only where the value IS an
            alert: a card fed by a Trigger or a Condition. Anywhere else a
            status names states (0 nobody, 1 somebody), not compliance. */}
        {fromAlert && (
          <button className="btn btn-sm" disabled={!editable}
                  onClick={() => onChange({ states: [] })}>
            Back to simple normal / alert
          </button>
        )}
      </div>
    </Field>
  );
}

const MAX_HEADERS = 8;

// Custom HTTP headers for the webhook outputs. Rows of name/value, because an
// endpoint that needs an API key needs exactly one line of config, and a free
// text blob would have to be parsed (and mis-parsed) somewhere.
/** The inspector for the three board-I/O nodes.
 *
 *  One component for all three because they ask the same first question --
 *  WHICH TERMINAL -- and that question is only answerable against the board
 *  itself: the port list, whether a line is already held by another program,
 *  and what a digital input reads right now all come from /api/board/io.
 *  A port picked from a hardcoded list would look identical and be a guess.
 */
// The IRIV's terminal block, as the palette knows it without asking anyone:
// the picker must ALWAYS offer DI0/DI1 and DO0/DO1, because choosing the
// terminal is part of designing the workflow -- which happens on a laptop as
// often as at the cabinet, and a dropdown that is empty until a live board
// answers reads as a broken node, not a disconnected board. The live poll
// then layers on what only the board knows: busy, consumer, current level.
const BOARD_FALLBACK = [
  { id: "DI0", kind: "input",  pin: 22, label: "Digital input 0" },
  { id: "DI1", kind: "input",  pin: 27, label: "Digital input 1" },
  { id: "DO0", kind: "output", pin: 23, label: "Digital output 0" },
  { id: "DO1", kind: "output", pin: 24, label: "Digital output 1" },
  { id: "L0",  kind: "pwm",    pin: 12, label: "LED / PWM 0" },
  { id: "L1",  kind: "pwm",    pin: 13, label: "LED / PWM 1" },
  { id: "BUZZER", kind: "output", pin: 19, label: "Buzzer" },
  { id: "BUTTON", kind: "input",  pin: 4,  label: "User button" },
];

function BoardFields({ node, d, editable, set, upMetrics = [] }) {
  const { data: io } = usePoll("/api/board/io", 3000);
  // The static list is the skeleton; a live answer replaces each entry with
  // the board's own (busy/consumer/value ride along).
  const live = new Map((io?.ports || []).map((p) => [p.id, p]));
  const ports = BOARD_FALLBACK.map((p) => live.get(p.id) || p);
  const kinds = { "variable.board_input": "input",
                  "output.io_out": "output",
                  "output.buzzer": "output",
                  "output.io_pwm": "pwm" }[node.type];
  const want = Array.isArray(kinds) ? kinds : [kinds];
  // The buzzer is its own node with its port fixed; the DO node offers the
  // relay outputs only. One dropdown mixing "close a relay" with "make a
  // noise" is how a horn ends up wired to a conveyor.
  const usable = ports.filter((p) => want.includes(p.kind)
    && (node.type === "output.buzzer" ? p.id === "BUZZER" : p.id !== "BUZZER"));
  const chosen = usable.find((p) => p.id === d.port);
  const [testMsg, setTestMsg] = useState("");

  async function testPort() {
    setTestMsg("…");
    try {
      const body = { port: d.port, ms: 600 };
      if (node.type === "output.io_pwm") {
        body.percent = Math.round(((d.level ?? 128) / 255) * 100);
      }
      const r = await api.post("/api/board/io/test", body);
      setTestMsg(r?.message || "pulsed");
    } catch (e) {
      setTestMsg(e?.message || "failed");
    }
  }

  return (<>
    {io && !io.detected && (
      <p className="muted small">
        No IRIV board detected on this box - these ports exist on the IRIV Edge
        AI's terminal block. The node still saves; it simply has nothing to
        drive here.
      </p>
    )}
    {node.type !== "output.buzzer" && (
      <Field label="Port"
             hint={chosen ? `${chosen.label} · BCM ${chosen.pin}` : "The terminal on the board."}>
        <select className="input" value={d.port || ""} disabled={!editable}
                onChange={set("port")}>
          {!d.port && <option value="">- choose a terminal -</option>}
          {/* Each option carries its live state, so the operator picks the
              terminal that is actually wired: an input shows what it reads
              right now (a closed contact reads 1), an output says ready or
              who is holding it. No live board -> the bare list. */}
          {usable.map((p) => {
            const state = !io?.detected ? ""
              : p.busy ? " - in use by another program"
              : p.kind === "input" && typeof p.value === "number"
                ? ` - reads ${p.value} now`
              : " - ready";
            return (
              <option key={p.id} value={p.id}>
                {p.id} - {p.label}{state}
              </option>
            );
          })}
          {/* The saved port survives a board that cannot be asked right now:
              dropping it from the list would silently rewrite the node. */}
          {d.port && !usable.some((p) => p.id === d.port) && (
            <option value={d.port}>{d.port}</option>
          )}
        </select>
      </Field>
    )}
    {/* Two different problems, and they had one sentence between them.
        `owner` names the SYSTEM whose app holds the line, which the board
        alone cannot say -- libgpiod calls every lgpio consumer "lg", so a
        pin one of our own workflows drives looked identical to one the
        factory Node-RED flow owns, and the warning blamed Node-RED for it.
        A terminal held by THIS system never reaches here at all: the server
        knows which system is asking and does not call that a conflict. */}
    {chosen?.busy && (
      <p className="muted small c-warn">
        {chosen.owner ? (<>
          {chosen.id} is already driven by the <strong>{chosen.owner}</strong> system.
          Two rules on one terminal fight over the relay - free it there, or
          pick another terminal.
        </>) : (<>
          {chosen.id} is held by another program{chosen.consumer
            ? ` (${chosen.consumer})` : ""} - most often the factory Node-RED
          flow. Free it there, or this node will fail to drive the pin.
        </>)}
      </p>
    )}

    {node.type === "variable.board_input" && (<>
      <label className="muted small" style={{ display: "flex", gap: 6,
                                              alignItems: "center" }}>
        <input type="checkbox" checked={!!d.invert} disabled={!editable}
               onChange={(e) => set("invert")({ target: { value: e.target.checked } })} />
        Contact is normally closed (invert)
      </label>
      <p className="muted small">
        Publishes <code>state</code> (1 while closed), <code>changes</code>{" "}
        (how many times it switched), <code>held_s</code> and <code>ok</code>.
        {chosen && typeof chosen.value === "number" && (
          <> Reading now: <strong>{d.invert ? 1 - chosen.value : chosen.value}</strong>.</>
        )}
      </p>
    </>)}

    {(node.type === "output.io_out" || node.type === "output.buzzer") && (<>
      <Field label="When this fires">
        <select className="input" value={d.action || "on"} disabled={!editable}
                onChange={set("action")}>
          <option value="on">turn it on (and leave it on)</option>
          <option value="off">turn it off</option>
          <option value="pulse">pulse it</option>
          <option value="toggle">toggle it</option>
        </select>
      </Field>
      {d.action === "pulse" && (
        <Field label="Pulse length" hint="Milliseconds, then it releases.">
          <input className="input" type="number" min="10" max="60000" step="50"
                 value={d.pulse_ms ?? 500} disabled={!editable}
                 onChange={set("pulse_ms")} />
        </Field>
      )}
      {node.type === "output.io_out" && (
        <label className="muted small" style={{ display: "flex", gap: 6,
                                                alignItems: "center" }}>
          <input type="checkbox" checked={d.active_high !== false} disabled={!editable}
                 onChange={(e) => set("active_high")({ target: { value: e.target.checked } })} />
          Active high (uncheck for a relay that closes on 0)
        </label>
      )}
      <p className="muted small">
        {node.type === "output.buzzer"
          ? "Pulse is the sane default - a buzzer latched \"on\" is an alarm nobody can silence."
          : "\"On\" latches: the output stays put after the alert clears. Pair it with a second node set to \"off\" on the rule that ends the condition."}
      </p>
    </>)}

    {node.type === "output.io_pwm" && (<>
      <Field label="Level comes from">
        <select className="input" value={d.source || "fixed"} disabled={!editable}
                onChange={set("source")}>
          <option value="fixed">a fixed level</option>
          <option value="value">the value wired into this node</option>
        </select>
      </Field>
      {d.source !== "value" ? (
        <Field label="Level" hint="0–255, like any PWM datasheet: 0 off, 128 half, 255 full.">
          <input className="input" type="number" min="0" max="255" step="1"
                 value={d.level ?? 128} disabled={!editable} onChange={set("level")} />
        </Field>
      ) : (<>
        {upMetrics.length > 0 && (
          <Field label="Which value">
            <select className="input" value={d.metric || upMetrics[0]}
                    disabled={!editable} onChange={set("metric")}>
              {upMetrics.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>
        )}
        <Field label="Value range"
               hint="What counts as 0 and what counts as full (255). A metric that only ever reaches 12 would otherwise look like a broken lamp.">
          <div className="wf-row">
            <input className="input" type="number" value={d.in_min ?? 0}
                   disabled={!editable} onChange={set("in_min")} />
            <span className="muted small">to</span>
            <input className="input" type="number" value={d.in_max ?? 255}
                   disabled={!editable} onChange={set("in_max")} />
          </div>
        </Field>
      </>)}
      <Field label="Update">
        <select className="input" value={d.when || "alert"} disabled={!editable}
                onChange={set("when")}>
          <option value="alert">when the rule fires</option>
          <option value="always">continuously (follow the value)</option>
        </select>
      </Field>
      <Field label="Frequency" hint="Hz. 1 kHz suits a lamp; software PWM is not accurate enough for a servo.">
        <input className="input" type="number" min="50" max="20000" step="50"
               value={d.frequency ?? 1000} disabled={!editable}
               onChange={set("frequency")} />
      </Field>
    </>)}

    {node.type !== "variable.board_input" && (
      <div className="wf-row" style={{ alignItems: "center" }}>
        <button className="btn btn-sm" type="button" disabled={!editable || !d.port}
                onClick={testPort}>
          Test - pulse it now
        </button>
        {testMsg && <span className="muted small">{testMsg}</span>}
      </div>
    )}
  </>);
}

function HeadersEditor({ d, editable, onChange }) {
  const rows = Array.isArray(d.headers) ? d.headers : [];
  function patchRow(i, fields) {
    onChange({ headers: rows.map((r, j) => (j === i ? { ...r, ...fields } : r)) });
  }
  return (
    <Field label="Headers"
           hint="Sent with every request. Host, Content-Length and Transfer-Encoding are set by the system and refused here.">
      {rows.map((r, i) => (
        <div key={i} className="wf-row">
          <input className="input" style={{ flex: "1 1 104px" }} placeholder="X-Api-Key"
                 value={r.name || ""} disabled={!editable}
                 onChange={(e) => patchRow(i, { name: e.target.value })} />
          <input className="input" style={{ flex: "1 1 104px" }} placeholder="value"
                 value={r.value || ""} disabled={!editable} maxLength={500}
                 onChange={(e) => patchRow(i, { value: e.target.value })} />
          <button className="btn btn-sm" style={{ flex: "0 0 auto" }}
                  disabled={!editable} title="Remove"
                  onClick={() => onChange({ headers: rows.filter((_, j) => j !== i) })}>
            ✕
          </button>
        </div>
      ))}
      <div className="filter-bar">
        {rows.length < MAX_HEADERS ? (
          <button className="btn btn-sm wf-add" disabled={!editable}
                  onClick={() => onChange({ headers: [...rows, { name: "", value: "" }] })}>
            + Add header
          </button>
        ) : (
          <span className="muted small">{MAX_HEADERS} headers is the limit.</span>
        )}
      </div>
    </Field>
  );
}

//: What a Dataset column can capture off the event that triggered the row.
//: Mirrors DATASET_EVENT_FIELDS in core/services/workflow.py - a field this
//: list offers and the app cannot find would log blanks for ever in silence.
//: in_zone_s is server-only on purpose: it needs the zone scope a timer-port
//: WIRE carries, so it is never picked from this list.
const DATASET_EVENT_FIELDS = [
  ["ts", "when it happened"],
  ["camera", "which camera"],
  ["zone", "which zone"],
  ["direction", "which way it crossed (in / out / cross)"],
  ["track_id", "the tracked object's id"],
  ["detection_label", "what was detected (person, car…)"],
  ["label", "the alert text"],
  ["value", "the value that triggered it"],
  ["duration_s", "how long the stay lasted, seconds (Time in Zone + OID)"],
  ["entered_at", "when the object entered (Time in Zone + OID)"],
  ["exited_at", "when the object left (Time in Zone + OID)"],
  ["exceeded_s", "how far past the limit the stay went, seconds (Trigger set to wait for the timer)"],
  ["type", "event type"],
  ["severity", "severity"],
  ["model", "which AI model"],
  // From a depth camera, on an object whose class has a measurement profile.
  ["depth_mm", "how far from the camera, mm (depth camera)"],
  ["length_mm", "measured length, mm (depth camera)"],
  ["width_mm", "measured width, mm (depth camera)"],
  ["height_mm", "measured height, mm (depth camera)"],
  ["distance_mm", "distance to the object's top, mm (depth camera)"],
  ["diameter_mm", "measured diameter, mm (depth camera)"],
];
//: Counters that count each object ONCE, by tracking id -- so they know which
//: object they counted and can hand that id on. A counter that counts bodies
//: per frame has no id to give.
const COUNTS_BY_PID = new Set(["analytics.counter", "analytics.frame",
                               "analytics.violations", "analytics.dwell",
                               "analytics.occupancy",
                               // A line knows exactly which object crossed
                               // it -- the crossing is recorded against a
                               // track id -- so it can hand that id on for
                               // a Data Table row per person through the
                               // door.
                               "analytics.entry_exit",
                               // ...and an Object Detection knows which
                               // object ARRIVED: one row per newcomer, with
                               // its id and its class.
                               "analytics.object"]);

const MAX_DATASET_COLS = 16;

/**
 * Where each of a table's columns gets its value.
 *
 * The COLUMNS are the table's, not this node's - they are listed here in the
 * table's own order and cannot be renamed or retyped from the canvas. What
 * this node decides is what feeds each one.
 *
 * A column wired on the canvas is shown as wired and nothing else: the wire
 * is the assignment, and offering a second way to set the same column here
 * would be two controls for one fact, disagreeing the moment either changes.
 */
function SourcesEditor({ d, editable, statKeys, labelFor, onChange, columns }) {
  const sources = d.sources || {};
  const wired = new Set(d._wired || []);

  function patch(name, fields) {
    const next = { ...sources };
    if (fields === null) delete next[name];
    else next[name] = { ...(next[name] || {}), ...fields };
    onChange({ sources: next });
  }

  if (!columns.length) {
    return (
      <p className="muted small">
        This table has no columns yet - add some on the{" "}
        <Link to="/database">Database</Link> page.
      </p>
    );
  }

  return (
    <Field label="Columns"
           hint="Drag a value onto a column on the canvas, or set where it comes from here. A column with nothing attached is simply not recorded by this node.">
      {columns.map((c) => {
        const cfg = sources[c.name] || {};
        const isWired = wired.has(c.name);
        const src = isWired ? "wire" : (cfg.src || "");
        return (
          <div key={c.name}
               style={{ border: "1px solid rgba(128,128,128,.2)",
                        borderRadius: 8, padding: 8, marginBottom: 6 }}>
            <div className="wf-row" style={{ alignItems: "baseline" }}>
              <strong style={{ flex: "1 1 auto" }}>{c.name}</strong>
              <span className="muted small">{c.type}</span>
            </div>
            <div className="wf-row" style={{ marginTop: 6 }}>
              {isWired ? (
                <span className="muted small">
                  wired on the canvas - drop a different value on it to change,
                  or delete the wire to set it here
                </span>
              ) : (
                <select className="input" style={{ flex: "0 0 130px" }} value={src}
                        disabled={!editable}
                        onChange={(e) => (e.target.value
                          ? patch(c.name, { src: e.target.value })
                          : patch(c.name, null))}>
                  <option value="">not recorded</option>
                  <option value="stat">pick a value</option>
                  <option value="event">from the event</option>
                  <option value="const">fixed value</option>
                </select>
              )}
              {!isWired && src === "event" && (
                <select className="input" style={{ flex: "1 1 auto" }}
                        value={cfg.field || "ts"} disabled={!editable}
                        onChange={(e) => patch(c.name, { field: e.target.value })}>
                  {DATASET_EVENT_FIELDS.map(([f, text]) => (
                    <option key={f} value={f}>{text}</option>
                  ))}
                </select>
              )}
              {!isWired && src === "const" && (
                <input className="input" style={{ flex: "1 1 auto" }}
                       placeholder="night shift" value={cfg.value || ""}
                       disabled={!editable} maxLength={120}
                       onChange={(e) => patch(c.name, { value: e.target.value })} />
              )}
            </div>
            {!isWired && src === "stat" && (
              <div style={{ marginTop: 6 }}>
                <KeyPicker value={cfg.key || ""} keys={statKeys} editable={editable}
                           labelFor={labelFor}
                           onPick={(k) => patch(c.name, { key: k })} />
              </div>
            )}
          </div>
        );
      })}
    </Field>
  );
}


//: What a card can ask of a column it is showing is cardFields.WORK_OUT --
//: the Builders' list, word for word, so "Work out: total rows" reads the
//: same on this node and on the Analytics card it compiles to.

/**
 * The logic a card applies to the column wired into it.
 *
 * Only a number can be totalled, averaged or ranked -- a text column has no
 * average, and offering one is offering a question Postgres answers with an
 * error. So the list is filtered by the column's declared type rather than
 * shown in full and refused later.
 */
/**
 * How a dashboard node's widget is DRESSED.
 *
 * On the node, not in the Builder, because a `wf_*` card is rebuilt from its
 * node on every compile: the Builder's partial save keeps the stored compiled
 * copy of those cards on purpose (so a recompile cannot lose them), which
 * meant a template picked there was thrown away and every workflow card
 * rendered plain whatever the picker said.
 *
 * The list is the Builder's own, keyed by the kind the node draws as, so the
 * two editors never drift into offering different treatments for one widget.
 */
//: How a query's rows become one number, in the words the summary line and
//: the picker both read from -- one list, so what the summary says a variable
//: keeps is literally the option that is selected. Mirrors sqlconsole.PICKS.
const PICK_WORDS = {
  first: "the first row",
  last: "the last row",
  sum: "the total of every row",
  avg: "the average across rows",
  min: "the smallest",
  max: "the largest",
  count: "how many rows came back",
};


/**
 * The thresholds a card reads by -- and nothing else.
 *
 * This used to offer the Template as well, and before that the animation, the
 * colours and the text sizes. Every one of them was ALSO in the dashboard
 * editors, which made each a question with an invisible answer: two panels,
 * one stored value, and whichever was saved last winning silently.
 *
 * A card's LOOK is now chosen where the card is looked at (Analytics or the
 * Dashboard builder), written back onto this node by /api/workflow/card-style
 * so it survives every recompile. The bands stay here because they are not
 * dressing: a limit is a statement about the METRIC, and the metric is the
 * node's business. They are the one knob BOTH sides edit -- the Builders
 * offer them under Data, beside the source, and write them onto this node
 * the same way -- so the last edit wins whichever panel made it.
 *
 * The LIMITS, that is. The colour each band paints is look, and on a chart
 * the look is the Builders' alone, in their Appearance section
 * (cardFields.showsColours, builderShared BandColours): the node shows the
 * limits and a hint line where the swatches were.
 */
function CardBands({ kind, d, editable, onChange }) {
  // Editable only -- a locked node's bands are shown by the card itself.
  if (!editable) return null;
  const edges = (d.bands?.at || []).filter((v) => v !== "" && v !== null);
  const mode = String(d.band_alert || "value");
  return (
    <>
      <BandEditor kind={kind} value={d.bands} surface="node"
                  onChange={(bands) => onChange({ bands })} />
      {/* A line drawn at 80 is a DECISION at 80. It used to be only a
          picture, so the same number had to be said again in a Compare or a
          Watch -- one limit in two places, drifting apart the first time
          somebody moved the line. Each line compiles to a rule named after
          this card: it raises an alert and publishes 1/0 that any formula,
          branch or card can read. Live, off the same number the card reads,
          so it answers as the value crosses rather than on a timer.

          Under Advanced, and summarised on the closed row: the DEFAULT is
          the right answer for almost every line, and severity and a quiet
          period are tuning. Three stacked controls in the main column made
          the lines above them -- the thing being set -- look like a
          footnote to their own settings. */}
      {edges.length > 0 && (
        <AdvancedSection label={`Threshold rules - ${
          mode === "off" ? "none"
            : mode === "alert" ? `alert, ${d.band_severity || "warn"}`
              : "value only"}`}>
          <Field label="What each line does"
                 hint="The rule exists either way and appears under Alerts in the palette - drag it out to branch on it, chart it or send it. Raising an event is the extra step.">
            <select className="input" value={mode}
                    onChange={(e) => onChange({ band_alert: e.target.value })}>
              <option value="value">Publish a value I can wire (no alert)</option>
              <option value="alert">...and raise an alert when it is crossed</option>
              <option value="off">Nothing - the lines only colour the card</option>
            </select>
          </Field>
          {mode === "alert" && (
            <>
              <Field label="Severity">
                <select className="input" value={d.band_severity || "warn"}
                        onChange={(e) => onChange({ band_severity: e.target.value })}>
                  <option value="info">Info</option>
                  <option value="warn">Warning</option>
                  <option value="crit">Critical</option>
                </select>
              </Field>
              {/* A value sitting ON the line would raise an event per frame
                  without this, which is the one way a threshold can make a
                  box unusable. */}
              <Field label="Quiet period"
                     hint="How long it stays silent after firing. A value hovering on the line would otherwise raise an event every frame.">
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <input className="input" type="number" min="0" style={{ width: 110 }}
                         value={d.band_cooldown_s ?? 60}
                         onChange={(e) => onChange({
                           band_cooldown_s: Math.max(0, parseInt(e.target.value, 10) || 0),
                         })} />
                  <span className="muted small">seconds</span>
                </div>
              </Field>
              <Field label="Message"
                     hint="What the alert says in the events log. Left empty it reads '<card> reached <value>'.">
                <input className="input" value={d.band_message || ""}
                       placeholder={`${d.name || "This card"} reached ${edges[0]}`}
                       onChange={(e) => onChange({ band_message: e.target.value })} />
              </Field>
            </>
          )}
        </AdvancedSection>
      )}
    </>
  );
}

/**
 * Which columns a table card shows.
 *
 * Empty means all of them, so a card wired up and left alone shows the table
 * -- which is what wiring the table to a table card plainly says. Ticking
 * narrows it, and the ticked set is what the widget carries, so it is also
 * the only thing that widget can read: narrower than the table, which is the
 * safe direction.
 */
function TableColumns({ d, table, editable, onChange, set }) {
  const all = table.columns || [];
  const picked = Array.isArray(d.show) ? d.show : [];
  const shown = picked.length ? picked : all.map((c) => c.name);
  function toggle(name) {
    const next = shown.includes(name)
      ? shown.filter((n) => n !== name) : [...shown, name];
    // Every column ticked is the same as none ticked; store the simpler one
    // so a table that gains a column later still shows it.
    onChange({ show: next.length === all.length ? [] : next });
  }
  return (
    <>
      <p className="muted small">
        Showing rows from <strong>{table.name}</strong>.
      </p>
      <Field label="Columns"
             hint="Which columns this card shows. All of them unless you narrow it - so a table that gains a column later still shows it.">
        {all.map((c) => (
          <label key={c.name} className="muted small"
                 style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={shown.includes(c.name)}
                   disabled={!editable} onChange={() => toggle(c.name)} />
            <span style={{ color: "var(--text, #e8eaed)" }}>{c.name}</span>
            <span>{c.type}</span>
          </label>
        ))}
        {!all.length && (
          <span className="muted small">
            This table has no columns yet - add some on the{" "}
            <Link to="/database">Database</Link> page.
          </span>
        )}
      </Field>
      <Field label="Rows" hint="How many to show, newest first.">
        <input className="input" type="number" min="1" max="50"
               value={d.limit ?? 15} disabled={!editable}
               onChange={set("limit")} />
      </Field>
      {/* Which rows: the card's ONE Date range, below (CardDateRange). */}
    </>
  );
}

function ColumnLogic({ d, column, editable, onChange, set, kind }) {
  const numeric = column.type === "number";
  const fallback = numeric ? "latest" : "count";
  // A chart draws a series, so it has to be told what a bar (or a point) IS.
  // A tile does not: it shows one number.
  const charts = ["line", "area", "spline", "bar", "donut", "pie", "radar"];
  const isChart = charts.includes(kind);
  const cols = column.table?.columns || [];
  return (
    <>
      <p className="muted small">
        Showing <strong>{column.column}</strong> from{" "}
        <strong>{column.table?.name || "a table"}</strong> ({column.type}).
      </p>
      <WorkOutField field={Field} value={d.agg || fallback} numeric={numeric}
                    disabled={!editable} onChange={(v) => onChange({ agg: v })} />
      {!numeric && (
        <p className="muted small">
          {column.column} is {column.type}, so it can be counted but not
          totalled or averaged.
        </p>
      )}
      {/* WHAT ONE BAR IS. Without this the chart grouped by the column it was
          showing, which for a 0/1 column is two bars labelled 0 and 1, one of
          them always empty -- a chart of a column against itself. A period is
          usually what was meant ("the total per day"); another column is the
          other honest answer ("the total per zone"). */}
      {/* Empty is the compiler's own pick for a wired column, named as
          what it is: a period for a number (an hour, a day once the period
          is a week or a month), the column itself for words on a pie. */}
      {isChart && (
        <>
          <PerField field={Field} groupBy={d.group_by}
                    columns={cols} disabled={!editable} onChange={onChange}
                    emptyLabel={numeric || ["line", "area", "spline"].includes(kind)
                      ? "nothing - group by time"
                      : `each ${column.column} (default)`} />
          {/* The time grouping, when no X field is chosen: Automatic is the
              compiler's own pick (an hour, a day once the range is a week). */}
          <StepField field={Field} value={d.bucket || ""} disabled={!editable}
                     lockedReason={d.group_by ? X_FIELD_REASON : ""}
                     onChange={(v) => onChange({ bucket: v })} />
        </>
      )}
      {isChart && numeric && d.group_by && d.group_by === column.column && (
        <p className="small c-warn">
          One bar per value of the column you are showing - a 0/1 column gives
          exactly two, and one of them is always empty. Pick a period instead.
        </p>
      )}
      {/* Which rows: the card's ONE Date range, below (CardDateRange) --
          a Period here wrote the same `range` a second control wrote. */}
    </>
  );
}

//: Which columns can date a row. Mirrors TIME_COL_TYPES in builderShared.jsx
//: and datasets_repo.TIME_TYPES: a declaration says "datetime", the store
//: answers "timestamp", and both mean the same column.
const SERIES_TIME_TYPES = ["timestamp", "date", "datetime"];

/** Which wire draws which series. Mirrors series_slots in workflow.py.
 *
 *  A wire on a NAMED port (s2..s8) means that series and nothing else; a wire
 *  on the plain `in` port - which is every chart drawn before the ports
 *  existed - falls into the first free slot in wire order. Read here so the
 *  inspector lists exactly the lines the compiler will draw, in the same
 *  order, with the same ids: a row that named a different series than the one
 *  on the canvas would put a colour on the wrong line.
 */
function graphSeriesSlots(node, nodes, edges) {
  const slots = new Map();
  const spare = [];
  for (const e of edges || []) {
    if (e.target !== node.id) continue;
    const src = (nodes || []).find((n) => n.id === e.source);
    if (!src) continue;
    // "s1" is `in` under another name: nothing on the canvas mints it, but a
    // graph written by hand or shipped in a template may well say it.
    let handle = String(e.targetHandle || "in");
    if (handle === "s1") handle = "in";
    const at = GRAPH_SERIES_HANDLES.indexOf(handle);
    if (at > 0 && !slots.has(at + 1)) slots.set(at + 1, { edge: e, src, named: true });
    else spare.push({ edge: e, src });
  }
  for (const wire of spare) {
    let free = 0;
    for (let i = 1; i <= GRAPH_SERIES_HANDLES.length; i += 1) {
      if (!slots.has(i)) { free = i; break; }
    }
    if (!free) continue;             // past eight the compiler warns and stops
    slots.set(free, { ...wire, named: false });
  }
  return [...slots.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([slot, wire]) => ({ ...wire, slot, sid: `s${slot}` }));
}

/** WHAT ONE WIRE MEANS, read the way the compiler reads it (series_entry).
 *
 *  The wire is the truth about where a line comes from - a rule publishes a
 *  live value, a Card or Chart on recorded data carries its own question
 *  about it, a Data Table column carries the column. None of that is editable
 *  on this panel, because none of it is this panel's to decide: it is shown
 *  as the row's summary, and what the wire leaves open is what the row can
 *  answer.
 *
 *  `byValue` is the compiler's CATEGORY rule (series_entry by_value): on a
 *  bar, donut, pie or radar whose every input is a table column, a text
 *  column counts each of its values -- one bar per value -- unless the row
 *  says otherwise.
 */
function graphSeriesWire({ edge, src }, { registry, dbTables, timed, byValue }) {
  const sd = src.data || {};
  const handle = String(edge.sourceHandle || "");
  const label = registry[src.type]?.label || src.type;
  if (src.type === "output.dataset" && handle.startsWith("o_")) {
    const column = handle.slice(2);
    const table = (dbTables || []).find((t) => t.key === sd.dataset) || null;
    const type = ((table?.columns || []).find((c) => c.name === column) || {}).type
      || "number";
    // The same types the compiler counts as words (series_entry by_value).
    const words = ["text", "boolean", "uuid"].includes(type);
    return {
      source: "dataset", open: true, table, column, words, type,
      byValue: !!byValue && words,
      title: column.replace(/_/g, " "),
      from: `${table?.name || sd.dataset || "a data table"} · ${column}`,
    };
  }
  // A Card or Chart asking recorded data -- or the Event Card it replaced,
  // on a canvas not yet migrated. A migrated "metric" card charts its rule.
  const legacy = src.type === "dashboard.events";
  const mode = legacy ? (sd.source_mode || "events")
    : (["dashboard.card", "dashboard.graph"].includes(src.type)
       ? (sd.source_mode || "") : null);
  if (mode !== null) {
    const name = sd.name || label;
    if (mode === "dataset") {
      return { source: "dataset", open: false, title: name,
               from: `${name} - the table question on that card` };
    }
    if (mode === "metric") {
      return { source: "history", open: true, title: name,
               from: `${name} - the value that card's function publishes` };
    }
    if (mode === "events") {
      return { source: "query", open: false, title: name,
               from: `${name} - the question that card asks of the `
                     + "recorded events" };
    }
    // Wired while it asked recorded data, switched back since: the compiler
    // skips it with a warning, and this row says so before the save does.
    return { source: "none", open: false, title: name,
             from: `${name} shows the value wired into it - set its Source `
                   + "to recorded events or a data table to draw it here" };
  }
  return { source: timed ? "history" : "stat", open: timed,
           title: sd.name || label,
           from: `${sd.name || label} - the value this rule publishes` };
}

/** A chart node's model as its inspector reads it: the wires, what each
 *  input reads, and the chart builder model (chartModel.readNodeModel) with
 *  its overrides. Shared by GraphSeriesRows and the Inspector's chart type
 *  strip, which names a Time-based or a Comparison Bar by the model's X. */
function graphNodeModel({ node, nodes, edges, registry, d, dbTables, single = null,
                          recorded = null }) {
  const kind = d.kind || "area";
  const timed = ["line", "area", "spline"].includes(kind);
  const wires = recorded ? [] : graphSeriesSlots(node, nodes, edges);
  // The compiler's category rule for chart titles (series_entry by_value).
  const legacyPeriod = cleanStep(d.bucket) || "";
  const byValue = !legacyPeriod && ["bar", "donut", "pie", "radar"].includes(kind)
    && wires.every((w) => w.src.type === "output.dataset"
                          && String(w.edge.sourceHandle || "").startsWith("o_"));
  const infos = wires.map((w) => graphSeriesWire(w, { registry, dbTables, timed, byValue }));
  // Two inputs reading the SAME column of two tables are named by their Data
  // Table, as the compiler names them (workflow.py series_table_name).
  const columnTitles = infos.filter((x) => x.column).map((x) => x.title);
  infos.forEach((x, i) => {
    if (x.column && columnTitles.filter((t) => t === x.title).length > 1) {
      const sd = wires[i].src.data || {};
      x.title = sd.name || x.table?.name || sd.dataset || x.title;
    }
  });
  const series = !recorded && !single && wires.length > 0;
  const inputs = series
    ? wires.map((w, i) => ({ sid: w.sid, source: infos[i].source, column: infos[i].column,
                             type: infos[i].type, columns: infos[i].table?.columns || [] }))
    : single ? [{ sid: "s1", source: "dataset", column: single.column, type: single.type,
                  columns: single.table?.columns || [] }]
      : wires.length ? [{ sid: "s1", source: timed ? "history" : "stat" }] : [];
  const { model, overrides } = readNodeModel(d, kind, inputs,
    { single: !!single, recorded: !!recorded });
  return { kind, wires, infos, inputs, model, overrides, series };
}

/**
 * THE CHART BUILDER ON THE CANVAS: one shared X axis, Y axis, interval and
 * date range (chartFields.ChartAxisFields, the same component the Builders
 * draw), then one short row per wired input.
 *
 * The owner's screenshots (2026-10-13) showed CAM 1 and CAM 2 each repeating
 * Work out, One point per, Date column and Period, and the chart below them
 * adding Group by time, Count over and Time range -- six controls for four
 * questions. Now the chart answers them once; a row keeps what is really its
 * own: where it reads from (said, from the wire), its name, whether it is
 * drawn, and -- collapsed -- an override of the chart's Y calculation (plus
 * any per-input setting saved before, kept and clearable, never lost).
 *
 * A chart saved before the builder is READ through the legacy rules
 * (chartModel.readNodeModel) and written in the new shape on the first edit
 * (nodePatch). Where a line reads from is still the wire's and is never
 * offered here; a line's colour is the Builders' (showsColours).
 *
 * `single` is a one-wire chart fed by a table column ({table, column, type}),
 * `recorded` a chart asking recorded data itself ({mode, columns}), and
 * `advanced` extra rows for the collapsed Advanced section (the thresholds).
 */
export function GraphSeriesRows({ node, nodes, edges, registry, d, dbTables,
                                 editable, onChange, single = null,
                                 recorded = null, advanced = null }) {
  const { kind, wires, infos, inputs, model, overrides, series } = graphNodeModel(
    { node, nodes, edges, registry, d, dbTables, single, recorded });
  const timed = ["line", "area", "spline"].includes(kind);
  const conf = (d.series && typeof d.series === "object") ? d.series : {};
  const write = (m, ovr = overrides) => onChange(nodePatch(d, kind, m, ovr,
    { recorded: !!recorded }));
  // What X, the Date column and Y may name: the inputs' tables, together.
  const tables = recorded ? [{ columns: recorded.columns || [] }]
    : inputs.map((x) => ({ columns: x.columns || [] }));
  const columns = [];
  for (const t of tables) {
    for (const c of t.columns || []) {
      if (!columns.some((x) => x.name === c.name)) columns.push(c);
    }
  }
  const timeColumns = columns.filter((c) => SERIES_TIME_TYPES.includes(c.type))
    .map((c) => c.name);
  const hasStore = !!recorded || inputs.some((x) => ["dataset", "query"].includes(x.source));
  const wiredCols = inputs.filter((x) => x.column);
  const yHint = wiredCols.length
    ? `Each input measures its own wired column (${wiredCols
      .map((x, i) => `${(infos[i] || {}).title || x.column}: ${x.column}`).join(", ")}).`
    : "";
  if (!recorded && !single && !wires.length) {
    return (
      <p className="muted small">
        Nothing is wired in yet. Every wire onto a Series port draws one line,
        and each gets its own row here - a name, whether it is shown, and an
        optional override.
      </p>
    );
  }
  return (
    <>
      <ChartAxisFields field={Field} kind={kind} model={model} disabled={!editable}
                       onChange={(m) => write(m)} columns={columns}
                       yColumns={recorded ? (recorded.columns || []) : null}
                       yHint={yHint} hasStore={hasStore} timeColumns={timeColumns}
                       legacyWindow={timed && !hasStore && d.window && d.window !== "live"
                         ? d.window : ""}
                       advanced={advanced} />
      {series && (
        // A plain block wearing the field classes rather than <Field> itself:
        // every ROW below contains Fields, Field renders a <label>, and a
        // label inside a label is invalid.
        <div className="wf-field">
          <span className="wf-field-label">
            Inputs
            <InfoHint label="About Inputs">
              One row per wire. Every input shares the chart&rsquo;s X axis,
              Y axis and date range; an override changes one input only.
              Where a line reads from is the wire&rsquo;s to say. Its colour
              is set in the Analytics or Dashboard Builder.
            </InfoHint>
          </span>
          <div style={{ display: "grid", gap: 8 }}>
            {wires.map((w, i) => {
              const cfg = conf[w.sid] || {};
              const info = infos[i];
              const o = overrides[w.sid] || {};
              return (
                <div key={w.sid}
                     style={{ border: "1px solid rgba(128,128,128,.28)",
                              borderRadius: 8, padding: 8, overflow: "hidden" }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <strong className="small" style={{ flex: 1, minWidth: 0,
                                                       overflow: "hidden",
                                                       textOverflow: "ellipsis",
                                                       whiteSpace: "nowrap" }}>
                      {cfg.label || info.title || `Series ${w.slot}`}
                    </strong>
                    <span className="muted small" style={{ flexShrink: 0 }}>
                      {w.named ? `series ${w.slot}` : `series ${w.slot} · plain port`}
                    </span>
                  </div>
                  {/* WHERE IT READS FROM, said rather than offered. */}
                  <p className="muted small" style={{ margin: "4px 0 0" }}>{info.from}</p>
                  {!info.open && (
                    <p className="muted small" style={{ margin: "4px 0 0" }}>
                      What this line works out is asked on the node that feeds
                      it - open that one to change it.
                    </p>
                  )}
                  <InputRowControls field={Field} index={w.slot}
                                    label={cfg.label} placeholder={info.title || `Series ${w.slot}`}
                                    disabled={!editable} show={o.show !== false}
                                    override={o} chartCalc={model.y_agg}
                                    onLabel={(v) => onChange({ series: {
                                      ...conf, [w.sid]: { ...cfg, label: v } } })}
                                    onShow={(on) => write(model, { ...overrides,
                                      [w.sid]: { ...o, show: on ? undefined : false } })}
                                    onOverride={(next) => write(model, { ...overrides,
                                      [w.sid]: { ...next, ...(o.show === false ? { show: false } : {}) } })}>
                    {/* This camera's own threshold LIMITS (no colours on the
                        node: a grey line says where they are set). */}
                    <SeriesBands kind={kind} surface="node"
                                 name={cfg.label || info.title || `Series ${w.slot}`}
                                 value={cfg.bands} shared={d.bands}
                                 onChange={(b) => {
                                   const next = { ...cfg };
                                   if (b) next.bands = b; else delete next.bands;
                                   onChange({ series: { ...conf, [w.sid]: next } });
                                 }} />
                  </InputRowControls>
                </div>
              );
            })}
            {/* Once for the list, where each row's swatch used to be. */}
            {!showsColours("node", kind) && <ChartColourHint />}
          </div>
        </div>
      )}
      {!series && !showsColours("node", kind) && <ChartColourHint />}
    </>
  );
}

/** The picture adjustments a Pre-process may hold, with the range the server
 *  clamps each one to. Mirrors PREPROCESS_OPS in core/services/workflow.py:
 *
 *    params  [key, label, min, max, default, step, note]  - a slider
 *    bools   [key, label, default, note]                  - a tick
 *
 *  A control offering more than the server keeps would be a control that
 *  silently snaps back on save, so the numbers here are the server's numbers.
 *  Every KERNEL steps by 2 from an odd start: a kernel is centred on the
 *  pixel it replaces, an even one has no centre, and the server rounds an
 *  even one UP - a slider that can produce one is a slider that lies about
 *  what was saved.
 */
const PREPROCESS_OPS = {
  grayscale: {
    label: "Grayscale",
    hint: "Drop the colour. Everything after this works on one channel.",
    params: [],
  },
  exposure: {
    label: "Exposure",
    hint: "Brightness and mid-tones, for a camera that is too dark or washed out.",
    params: [["gain", "Gain", 0.05, 4, 1, 0.05, "1 leaves the brightness alone"],
             ["gamma", "Gamma", 0.1, 5, 1, 0.05, "above 1 lifts the shadows"]],
  },
  sharpness: {
    label: "Sharpness",
    hint: "An unsharp mask: the picture, plus the difference between it and a "
          + "blurred copy. Past about 2 it draws halos a detector can read as "
          + "objects of their own.",
    params: [["amount", "Amount", 0, 3, 1, 0.05, "0 is no sharpening"],
             ["ksize", "Kernel", 3, 9, 5, 2,
              "how wide the blurred copy is - the size of the detail it lifts"]],
  },
  blur: {
    label: "Blur",
    hint: "Smooth the picture - the usual way to stop sensor noise reading as "
          + "movement.",
    params: [["ksize", "Kernel", 3, 31, 5, 2, "odd numbers only"],
             ["sigma", "Sigma", 0, 25, 0, 0.5,
              "0 lets the kernel size decide, which is what most chains want"]],
  },
  sobel: {
    label: "Edges (Sobel)",
    hint: "Keep the edges and throw the rest away.",
    params: [["dx", "Across", 0, 2, 1, 1, "0 ignores vertical edges"],
             ["dy", "Down", 0, 2, 1, 1, "0 ignores horizontal edges"],
             ["ksize", "Kernel", 1, 7, 3, 2, "odd numbers only"],
             ["scale", "Scale", 0.05, 8, 1, 0.05,
              "multiplies the result - lift it when the edges come out faint"]],
    bools: [["normalize", "Normalize", false,
             "Stretch the result back to the full range, so a faint edge map "
             + "is still readable."]],
  },
  // NOT a Zone Filter, and the two sit next to each other in the palette. A
  // Zone Filter drops DETECTIONS after the AI has run, by where they turned
  // out to be; a mask hides the PIXELS before the AI runs, so it never looks
  // there at all. Said on the card, because reaching for the wrong one of
  // these produces a system that works and quietly costs twice the compute.
  mask: {
    label: "Mask to a zone",
    hint: "The detector only sees inside the zones you pick. Not a Zone Filter: "
          + "that one drops detections AFTER the AI has run, by where they are. "
          + "A mask hides the picture BEFORE the AI runs, so it never looks there.",
    params: [],
    zones: true,
  },
};
//: Mirrors MAX_PREPROCESS_STEPS. Every step is work on every frame of every
//: camera on the stream, so the ceiling is a performance decision rather than
//: a tidiness one.
const MAX_PREPROCESS_STEPS = 8;

/** WHAT A STEP COSTS, in milliseconds per frame at 1280x720.
 *
 *  Reference figures measured on this build, not a reading off the box in
 *  front of the operator - which is exactly how the panel labels them. They
 *  are here because the number that decides whether a chain is a good idea is
 *  not visible anywhere else: a four-camera system at 25 fps has about 10 ms
 *  per frame in TOTAL, and a chain that eats it is a detector that has
 *  quietly stopped keeping up, with nothing on the canvas to say so.
 *
 *  Where a cost depends on the kernel, both measured ends are given and the
 *  figure is interpolated between them. `box` is SLOWER than `gaussian` on
 *  this build (4.1 against 0.9 at k5), which is the opposite of what everyone
 *  assumes - so nothing here or in the hints calls a box blur the cheap one.
 */
const PREPROCESS_COST = {
  grayscale: () => 0.75,
  exposure: () => 0.85,
  sharpness: () => 2.4,
  blur: (s) => (s.kind === "box"
    ? 4.1
    // gaussian: 0.9 at k5, 4.7 at k31.
    : 0.9 + (Math.min(Math.max(+s.ksize || 5, 5), 31) - 5) * (3.8 / 26)),
  // sobel: 4.2 at k3, 9.2 at k7.
  sobel: (s) => 4.2 + (Math.min(Math.max(+s.ksize || 3, 3), 7) - 3) * (5 / 4),
  mask: (s) => (s.fill === "blur" ? 7.8 : 0.8),
};
//: What a four-camera system at 25 fps has per frame, all together. The
//: number the readout is read AGAINST - a chain is not "slow", it is slow
//: for the system it is in.
const PREPROCESS_BUDGET_MS = 10;

/** The reference cost of the steps that are switched ON. */
function chainCost(steps) {
  let ms = 0;
  for (const s of steps || []) {
    if (!s || s.enabled === false) continue;
    const cost = PREPROCESS_COST[s.op];
    if (cost) ms += cost(s);
  }
  return ms;
}

/** The zones this box has drawn, as <option>s.
 *
 *  ONE list, worn by the Zone node's picker and by a Pre-process mask: both
 *  are the operator choosing a shape off the Zones page, and two copies of
 *  the wording would disagree the first time a zone gained a state worth
 *  marking (the disabled note below was exactly that).
 */
function zoneOptions(zoneList, exclude = []) {
  return (zoneList || [])
    .filter((z) => !exclude.includes(z.id))
    .map((z) => (
      <option key={z.id} value={z.id}>
        {z.name} ({z.kind}){z.enabled === false ? " · disabled" : ""}
      </option>
    ));
}

/**
 * The Pre-process chain: an ORDERED list of adjustments the picture goes
 * through before the detector sees it.
 *
 * The order is the operator's, and it is the meaning of the node - a blur
 * then an edge pass is an edge map of a smoothed picture, the other way round
 * smears the edges. So this panel is a list you ARRANGE, not a set of
 * switches, and every step carries only its own op's controls.
 *
 * Each slider shows its number beside it: a slider alone in a column this
 * narrow cannot be read back to a value, and these are numbers somebody
 * copies from one camera to the next.
 *
 * `camera` is the stream this chain hangs off, used to catch a mask pointed
 * at a zone drawn over a different camera's picture - a mistake with no
 * symptom at all except a detector that sees nothing.
 */
export function PreprocessSteps({ d, editable, onChange, zoneList = [], cameras = [],
                           camera = "" }) {
  const steps = Array.isArray(d.steps) ? d.steps : [];
  const write = (next) => onChange({ steps: next });
  const patch = (i, fields) => write(
    steps.map((s, j) => (j === i ? { ...s, ...fields } : s)));
  const move = (i, by) => {
    const to = i + by;
    if (to < 0 || to >= steps.length) return;
    const next = [...steps];
    [next[i], next[to]] = [next[to], next[i]];
    write(next);
  };
  const zoneName = (id) => (zoneList.find((z) => z.id === id) || {}).name || id;
  const ms = chainCost(steps);
  function add(op) {
    const spec = PREPROCESS_OPS[op];
    if (!spec) return;
    // Born with every number the op declares, at the server's own default:
    // a step saved half-empty is a step the compiler fills in silently, and
    // the panel would then show one thing while the camera did another.
    // `enabled` is written down rather than left to default, so a step
    // switched off and on again is the same step it was.
    const step = { op, enabled: true };
    if (op === "blur") step.kind = "gaussian";
    if (op === "mask") {
      step.zones = []; step.invert = false; step.fill = "black"; step.ksize = 15;
    }
    for (const [name, , , , dflt] of spec.params) step[name] = dflt;
    for (const [name, , dflt] of spec.bools || []) step[name] = dflt;
    write([...steps, step]);
  }
  return (
    // A plain block wearing the field classes: the cards below hold Fields of
    // their own, Field renders a <label>, and a label inside a label is
    // invalid - clicking the inner one lands on the outer one's control.
    <div className="wf-field">
      <span className="wf-field-label">
        Steps
        <InfoHint label="About Steps">
          The picture goes through these in order, before anything is
          detected. Order matters: a blur then an edge pass is an edge map of
          a smoothed picture; the other way round smears the edges.
        </InfoHint>
      </span>
      {!steps.length && (
        <p className="muted small" style={{ margin: 0 }}>
          No steps - the picture passes through unchanged.
        </p>
      )}
      <div style={{ display: "grid", gap: 8 }}>
        {steps.map((s, i) => {
          const spec = PREPROCESS_OPS[s.op];
          const name = spec ? spec.label : String(s.op || "step");
          // A step SWITCHED OFF is still a step: the operator turns one off
          // to see what it was doing, and turns it back on a second later.
          // So it keeps its card, its numbers and its place in the order -
          // its controls go to the browser's own disabled rendering, which
          // is the dimming every other disabled control on this page wears,
          // and the header stays at full strength so the card cannot be
          // mistaken for a deleted one.
          const off = s.enabled === false;
          const locked = !editable || off;
          const picked = Array.isArray(s.zones) ? s.zones : [];
          // A zone is drawn over ONE camera's picture. Masked onto another
          // camera it is a shape in the wrong place - which looks exactly
          // like a detector that has stopped working.
          const strays = spec && spec.zones && camera
            ? picked.filter((id) => {
                const z = zoneList.find((x) => x.id === id);
                return z && z.camera && camera !== "*" && z.camera !== camera;
              })
            : [];
          return (
            <div key={i}
                 style={{ border: "1px solid rgba(128,128,128,.28)",
                          borderRadius: 8, padding: 8, overflow: "hidden" }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span className="muted small" style={{ flexShrink: 0 }}>{i + 1}</span>
                <input type="checkbox" checked={!off} disabled={!editable}
                       style={{ flexShrink: 0 }}
                       title={off ? "Switch this step on" : "Switch this step off"}
                       aria-label={`${off ? "Enable" : "Disable"} ${name}`}
                       onChange={(e) => patch(i, { enabled: e.target.checked })} />
                <strong className="small" style={{ flex: 1, minWidth: 0 }}>{name}</strong>
                {off && <span className="muted small" style={{ flexShrink: 0 }}>off</span>}
                {/* Up and down rather than a drag handle: this rail is the
                    narrowest column on the product, and a drag inside it
                    competes with the canvas panning behind it. */}
                <button type="button" className="btn btn-sm btn-ghost"
                        disabled={!editable || i === 0}
                        title="Move this step earlier"
                        aria-label={`Move ${name} earlier`}
                        onClick={() => move(i, -1)}>↑</button>
                <button type="button" className="btn btn-sm btn-ghost"
                        disabled={!editable || i === steps.length - 1}
                        title="Move this step later"
                        aria-label={`Move ${name} later`}
                        onClick={() => move(i, 1)}>↓</button>
                <button type="button" className="btn btn-sm btn-ghost"
                        disabled={!editable} title="Remove this step"
                        aria-label={`Remove ${name}`}
                        onClick={() => write(steps.filter((_, j) => j !== i))}>×</button>
              </div>
              {/* THE SUMMARY STAYS LEGIBLE WHILE THE STEP IS OFF. It is what
                  says which step this is when its controls have gone grey. */}
              {spec && spec.zones && (
                <p className="small" style={{ margin: "4px 0 0" }}>
                  {/* The same sentence the NODE FACE draws (stepLabel in
                      nodes.jsx): one step, read in two places, must not be
                      described two ways. */}
                  {picked.length
                    ? `mask: ${picked.map(zoneName).join(", ")}`
                      + (s.invert ? " inverted" : "")
                    : "mask: no zone picked yet - the picture is unchanged"}
                </p>
              )}
              {off && (
                <p className="muted small" style={{ margin: "4px 0 0" }}>
                  Switched off - the picture skips this step. Its settings are
                  kept.
                </p>
              )}
              {/* A step this build has never heard of still draws by name: it
                  comes from a newer version, the compiler refuses it, and a
                  card saying nothing would leave that error pointing at a
                  node with no visible cause. */}
              {!spec && (
                <p className="small c-warn" style={{ margin: "4px 0 0" }}>
                  This build does not know a step called &ldquo;{String(s.op)}&rdquo;,
                  so it will be refused when the graph is applied.
                </p>
              )}
              {spec && spec.hint && (
                <p className="muted small" style={{ margin: "4px 0 0" }}>{spec.hint}</p>
              )}
              {spec && spec.zones && (
                <>
                  <Field label="Zones"
                         hint="Drawn and edited on the Zones page - this step points at them.">
                    <div style={{ display: "grid", gap: 4 }}>
                      {picked.map((id) => (
                        <div key={id} className="wf-row">
                          <span className="small" style={{ flex: 1, minWidth: 0,
                                                           overflow: "hidden",
                                                           textOverflow: "ellipsis",
                                                           whiteSpace: "nowrap" }}>
                            {zoneName(id)}
                          </span>
                          <button type="button" className="btn btn-sm btn-ghost"
                                  disabled={locked} title="Stop masking to this zone"
                                  aria-label={`Remove zone ${zoneName(id)}`}
                                  onClick={() => patch(i, {
                                    zones: picked.filter((x) => x !== id) })}>×</button>
                        </div>
                      ))}
                      <select className="input" value="" disabled={locked}
                              aria-label="Add a zone to the mask"
                              onChange={(e) => e.target.value && patch(i, {
                                zones: [...picked, e.target.value] })}>
                        <option value="">
                          {picked.length ? "+ add another zone..." : "- choose a zone -"}
                        </option>
                        {zoneOptions(zoneList, picked)}
                      </select>
                    </div>
                  </Field>
                  {!zoneList.length && (
                    <p className="muted small" style={{ margin: "4px 0 0" }}>
                      No zones drawn yet - draw one on the <strong>Zones</strong>{" "}
                      page and it appears here.
                    </p>
                  )}
                  {strays.length > 0 && (
                    <p className="small c-warn" style={{ margin: "4px 0 0" }}>
                      {strays.map(zoneName).join(", ")}{" "}
                      {strays.length === 1 ? "is" : "are"} drawn over{" "}
                      {camLabel(cameras, (zoneList.find(
                        (z) => z.id === strays[0]) || {}).camera)}, and this
                      chain adjusts {camLabel(cameras, camera)} - a zone masked
                      onto another camera hides the wrong part of the picture.
                    </p>
                  )}
                  <Field label="Invert (keep the outside)"
                         hint="On: everything INSIDE the zones is hidden and the rest is kept.">
                    <div className="wf-row">
                      <input type="checkbox" checked={!!s.invert} disabled={locked}
                             aria-label="Invert the mask"
                             onChange={(e) => patch(i, { invert: e.target.checked })} />
                      <span className="muted small">
                        Off: only what is inside the zones reaches the detector.
                      </span>
                    </div>
                  </Field>
                  <Field label="Fill the hidden part with">
                    <select className="input" value={s.fill || "black"} disabled={locked}
                            onChange={(e) => patch(i, { fill: e.target.value })}>
                      <option value="black">black - 0.8 ms a frame</option>
                      <option value="blur">blur - no hard edge, 7.8 ms a frame</option>
                    </select>
                  </Field>
                  <span className="muted small">
                    Black is the cheap one by a long way. A blurred fill leaves
                    no hard edge along the zone border, which a detector can
                    read as an object of its own - worth the ten times the cost
                    only when that is actually happening.
                  </span>
                  {/* The blur's own kernel, offered only while something is
                      being blurred: a control that does nothing for the fill
                      that is chosen is a control that teaches people to
                      ignore this panel. */}
                  {s.fill === "blur" && (
                    <Field label="Blur kernel"
                           hint="How heavy the blur over the hidden part is. Odd numbers only.">
                      <div className="filter-bar">
                        <input type="range" min={9} max={31} step={2}
                               value={s.ksize ?? 15} disabled={locked}
                               style={{ flex: 1, minWidth: 0 }}
                               aria-label="Mask to a zone Blur kernel"
                               onChange={(e) => patch(i, { ksize: +e.target.value })} />
                        <span className="small" style={{ whiteSpace: "nowrap",
                                                         minWidth: 32,
                                                         textAlign: "right" }}>
                          {s.ksize ?? 15}
                        </span>
                      </div>
                      <span className="muted small">9 to 31</span>
                    </Field>
                  )}
                </>
              )}
              {s.op === "blur" && (
                <Field label="Kind"
                       hint="Gaussian is both softer AND cheaper on this build - a box blur costs about four times as much at the same kernel.">
                  <select className="input" value={s.kind || "gaussian"} disabled={locked}
                          onChange={(e) => patch(i, { kind: e.target.value })}>
                    <option value="gaussian">gaussian - weighted, 0.9 ms at k5</option>
                    <option value="box">box - a flat average, 4.1 ms at k5</option>
                  </select>
                </Field>
              )}
              {(spec ? spec.params : []).map(
                ([key, label, min, max, dflt, step, note]) => (
                  <Field key={key} label={label} hint={note || undefined}>
                    <div className="filter-bar">
                      <input type="range" min={min} max={max} step={step}
                             value={s[key] ?? dflt} disabled={locked}
                             style={{ flex: 1, minWidth: 0 }}
                             aria-label={`${name} ${label}`}
                             onChange={(e) => patch(i, { [key]: +e.target.value })} />
                      {/* The value, in the rail's own text colour rather than
                          the muted one: it is the setting, not a caption. */}
                      <span className="small" style={{ whiteSpace: "nowrap",
                                                       minWidth: 32,
                                                       textAlign: "right" }}>
                        {s[key] ?? dflt}
                      </span>
                    </div>
                    <span className="muted small">{min} to {max}</span>
                  </Field>
                ))}
              {(spec ? spec.bools || [] : []).map(([key, label, dflt, note]) => (
                <Field key={key} label={label}>
                  <div className="wf-row">
                    <input type="checkbox" checked={s[key] ?? dflt} disabled={locked}
                           aria-label={`${name} ${label}`}
                           onChange={(e) => patch(i, { [key]: e.target.checked })} />
                    <span className="muted small">{note}</span>
                  </div>
                </Field>
              ))}
            </div>
          );
        })}
      </div>
      {/* A picker rather than six buttons: the rail is one column wide, and
          six buttons wrap into three rows that push the chain itself off
          screen on a short panel. */}
      <div className="wf-row" style={{ marginTop: 6 }}>
        <select className="input" style={{ flex: 1, minWidth: 0 }} value=""
                disabled={!editable || steps.length >= MAX_PREPROCESS_STEPS}
                aria-label="Add a step"
                onChange={(e) => add(e.target.value)}>
          <option value="">+ add a step...</option>
          {Object.entries(PREPROCESS_OPS).map(([op, spec]) => (
            <option key={op} value={op}>{spec.label}</option>
          ))}
        </select>
      </div>
      {steps.length >= MAX_PREPROCESS_STEPS && (
        <span className="muted small">
          {MAX_PREPROCESS_STEPS} steps is the most - each one runs on every
          frame of every camera on this stream, so the chain is capped rather
          than left to become the reason the detector is slow.
        </span>
      )}
      {/* WHAT THE CHAIN COSTS. A reference figure, said as one: these are
          measured timings for each step at 720p added up, not a reading off
          this box. It is here because the number that decides whether a chain
          is a good idea is otherwise invisible until the frame rate drops. */}
      {steps.length > 0 && (
        <p className={ms > PREPROCESS_BUDGET_MS ? "small c-warn" : "muted small"}
           style={{ marginTop: 6 }}>
          ~{ms.toFixed(1)} ms per frame at 720p, for the steps switched on -
          a reference figure, not a measurement of this box.
          {ms > PREPROCESS_BUDGET_MS
            ? ` Four cameras at 25 fps have about ${PREPROCESS_BUDGET_MS} ms a `
              + "frame between them, so this chain is likely to cost frame rate."
            : ` Four cameras at 25 fps have about ${PREPROCESS_BUDGET_MS} ms a `
              + "frame between them."}
        </p>
      )}
      <Field label="Show the processed picture"
             hint="Only what you SEE. The detector reads the processed frame whenever the chain is on, whichever way this is set.">
        <div className="wf-row">
          <input type="checkbox" checked={!!d.preview} disabled={!editable}
                 onChange={(e) => onChange({ preview: e.target.checked })} />
          <span className="muted small">
            Changes the live preview for this camera only - never what is
            recorded, and never what the detector runs on. The footage and the
            snapshots keep the camera&rsquo;s own picture; the detector sees
            the processed frame either way.
          </span>
        </div>
      </Field>
    </div>
  );
}

/** The in / does / out rows at the top of a node's Properties: a label pill
 *  and the words beside it (theme.css .node-summary). null draws nothing --
 *  a Comment or a Link has no summary (nodeSummary.SUMMARY_SKIP). */
function NodeSummaryBox({ box }) {
  if (!box) return null;
  return (
    <div className="node-summary" data-testid="node-summary" aria-label="What this node does">
      {[["in", box.in], ["does", box.does], ["out", box.out]].map(([k, v]) => (
        <div key={k} className="node-summary-row">
          <span className="node-summary-pill">{k}</span>
          <span className="node-summary-text">{v}</span>
        </div>
      ))}
    </div>
  );
}

/** On / Off at the top right of a Class Filter's panel: the state pill to
 *  the LEFT of the switch (the combined branch's StateSwitch look; swap to
 *  ui/CardHeader.jsx StateSwitch when the branches meet). A real switch:
 *  role="switch", aria-checked, Space / Enter. */
function ClassFilterSwitch({ on, disabled, onChange }) {
  return (
    <span className="state-switch" style={{ marginLeft: "auto", fontWeight: 400 }}
          data-state-switch={on ? "on" : "off"}>
      <span className={`state-pill${on ? " is-on" : ""}`} aria-hidden="true">{on ? "On" : "Off"}</span>
      <button type="button" role="switch" aria-checked={on} disabled={disabled}
              aria-label="Class Filter on"
              title={on ? "On: filters as set. Switch off to pause the rules after it."
                        : "Off: the rules after it are paused, here and on the dashboard."}
              className={`cf-toggle cf-bare ${on ? "is-on" : "is-off"}`}
              onClick={() => onChange(!on)}>
        <span className="cf-track" aria-hidden="true"><span className="cf-knob" /></span>
      </button>
    </span>
  );
}

export function Inspector({ node, registry, meta, nodes, edges, editable, errors, warnings, onChange, onOpenBlueprint, onFnData, onRetype, functions, onDone, customVars = [], onCreateVar, aiStatus = null, waitsForDetection = null }) {
  const ask = useAsk();
  const spec = registry[node.type] || {};
  const d = node.data || {};
  // The ON/OFF points' values join the keys every picker offers, from the
  // nodes drawn, so "Detection Stream is ON" can be picked before it is
  // wired (statePointKeys).
  const stateLabels = statePointKeys(nodes);
  const metaVars = meta.variables || {};
  const vars = { ...metaVars, state_labels: stateLabels,
                 stat_keys: [...new Set([...(metaVars.stat_keys || []),
                                         ...Object.keys(stateLabels)])] };
  const zoneList = vars.zone_list || [];
  // The box's mailboxes (Settings > Email), for the Email node's picker.
  const mailAccounts = meta.mail_accounts || [];
  const defaultMailbox = mailAccounts.find((a) => a.default) || null;
  // ...and the box's Telegram bots (Settings > Telegram), same idea.
  const tgBots = meta.telegram_bots || [];
  const defaultBot = tgBots.find((b) => b.default) || null;
  // The chat the chosen bot posts into on its own, shown as the Send to
  // placeholder: blank means THAT, and a blank box with no hint reads as
  // "nowhere".
  const chatOfBot = (bots, account, fallback) => {
    const bot = (account ? bots.find((b) => b.id === account) : fallback) || null;
    return bot?.chat_id ? `${bot.chat_id} (the bot’s own chat)` : "";
  };
  // The message box, so a placeholder button inserts AT THE CARET rather than
  // always at the end -- half the time the value belongs mid-sentence.
  const bodyRef = useRef(null);

  /** Put `token` into field `key` where the caret is, and leave the caret
   *  after it so typing continues naturally. Falls back to appending when the
   *  textarea is not focused, which is the case on the very first click. */
  function insertInto(key, ref, token) {
    const el = ref.current;
    const cur = String(d[key] || "");
    if (!el || typeof el.selectionStart !== "number") {
      onChange({ ...d, [key]: cur + token });
      return;
    }
    const a = el.selectionStart;
    const b = el.selectionEnd;
    const next = cur.slice(0, a) + token + cur.slice(b);
    onChange({ ...d, [key]: next });
    // After React has written the new value, or the caret jumps to the end.
    requestAnimationFrame(() => {
      try {
        el.focus();
        el.setSelectionRange(a + token.length, a + token.length);
      } catch { /* not focusable any more */ }
    });
  }
  const err = errors[node.id];
  const warn = warnings[node.id];

  // What is wired INTO this node, named the way the compiler names it
  // (core/services/workflow.py formula_inputs). Only values -- a Zone or a
  // Class Filter publishes nothing a formula could read.
  const wiredInputs = (edges || [])
    .filter((e) => e.target === node.id)
    .map((e) => (nodes || []).find((n) => n.id === e.source))
    .filter((n) => n && ["analytics", "variable"].includes(
      (registry[n.type] || {}).category))
    .map((n) => inputAlias(n, registry))
    .filter(Boolean);
  // A QUERY wired in is a store too, but it cannot appear above: that list is
  // values the engine already publishes, and a Query publishes nothing until
  // somebody runs it. It is found separately so the Variable inspector can
  // ask the two questions a table cannot answer for itself -- which column,
  // and which row.
  const querySrc = (edges || [])
    .filter((e) => e.target === node.id)
    .map((e) => (nodes || []).find((n) => n.id === e.source))
    .find((n) => n && n.type === "storage.query") || null;
  // ...and where a CSV node's file GOES. The compiled spec rides on the
  // message, not on the CSV node, so a CSV wired to nothing has no document to
  // render and the Download button would 404 rather than explain itself.
  const csvSink = (edges || [])
    .filter((e) => e.source === node.id)
    .some((e) => ["output.email", "output.telegram"].includes(
      ((nodes || []).find((n) => n.id === e.target) || {}).type));
  // A CSV node feeding this message is what makes {csv} mean anything.
  const hasCsv = (edges || [])
    .filter((e) => e.target === node.id)
    .some((e) => ((nodes || []).find((n) => n.id === e.source) || {}).type
                 === "storage.csv");
  // ...and WHICH files, named as the compiler names them: with two or more
  // each gets its own chips ({csv:name}, {csv_text:name}), Tok Aba
  // 2026-10-14.
  const csvFiles = csvNames(node.id, nodes, edges);
  // The values a MESSAGE's chips offer: the wired ones minus the Schedule /
  // Inject / State Watch that fires it. The compiler drops those (their "1"
  // is no value), so a chip for one could never resolve.
  const msgWiredInputs = (edges || [])
    .filter((e) => e.target === node.id)
    .map((e) => (nodes || []).find((n) => n.id === e.source))
    .filter((n) => n && !CLOCK_FN_TYPES.includes((registry[n.type] || {}).fn_type))
    .map((n) => inputAlias(n, registry))
    .filter(Boolean);
  // AI detection is OFF and this message waits for it: the compile's own
  // waits_for_detection (the nodes the app holds while the AI is off), so the
  // note says exactly what the app will do. Tok Aba kept the behaviour; the
  // inspector only says it. Unknown AI state (null) says nothing.
  const heldWhileAiOff = !!aiStatus && !aiOf(aiStatus).ok
    && !!waitsForDetection && waitsForDetection.has(node.id);
  // The value names a MESSAGE answers to, as the compiler builds them
  // (formula_inputs, minus the Schedule / Inject / State Watch that fired it:
  // their "1" is not a value). Both the fnId and the node's own name work.
  const msgNames = [...new Set((edges || [])
    .filter((e) => e.target === node.id)
    .map((e) => (nodes || []).find((n) => n.id === e.source))
    .filter((n) => n && !CLOCK_FN_TYPES.includes((registry[n.type] || {}).fn_type))
    .flatMap((n) => {
      const a = inputAlias(n, registry);
      return a ? [a.name, slugify((n.data || {}).name)].filter(Boolean) : [];
    }))];

  // What "Into variable" may store into: the DECLARED variables (made in the
  // Variables panel - a name, nothing else), plus this node's own saved
  // choice so it never vanishes from its own dropdown. Stored as the slug
  // (`var`), displayed by name.
  // A node saved by an older build carries the default label "Variable" as
  // its name; that is nobody's variable and must not appear chosen.
  // A Variable that already held a chosen variable, and then got a wire,
  // stores INTO that one: its name is the variable it had picked.
  const chosenVar = d.name && d.name !== spec.label ? slugify(d.name)
    : (node.type === "variable.ref" && d.key ? plainName(d.key) : "");
  const storeCandidates = [...new Set([
    ...customVars.map((v) => v.var),
    ...(chosenVar ? [chosenVar] : []),
  ])].filter(Boolean).map((varName) => ({
    var: varName,
    label: (customVars.find((v) => v.var === varName) || {}).name || varName,
  }));

  // The analytics node feeding this sink (for metric choices). When and Alert
  // nodes are transparent: a card wired after one binds to the function whose
  // trigger they define, so the search steps THROUGH them one hop.
  const upstreamFn = (() => {
    const findInto = (id, hop) => {
      for (const e of edges) {
        if (e.target !== id) continue;
        // A Trigger's OID port names WHO, not the rule the moment is about
        // -- that one sits behind the branch on the main input.
        if (["oid", "cur_oid", "cur_value"].includes(e.targetHandle)) continue;
        const src = nodes.find((n) => n.id === e.source);
        if (!src) continue;
        const cat = registry[src.type]?.category;
        if (cat === "analytics" || cat === "variable") return src;
        if (hop < 2 && (cat === "logic" || TRANSPARENT_OUT.includes(src.type))) {
          const through = findInto(src.id, hop + 1);
          if (through) return through;
        }
      }
      return null;
    };
    return findInto(node.id, 0);
  })();
  // Why a Trigger may not WAIT FOR THE TIMER to end, or "": there has to be a
  // Cycle Time upstream that records each stay, or a node with a minimum stay.
  // The compiler refuses it the same way (workflow.py _hold_blocker); this only
  // keeps the switch from being offered.
  const holdBlocker = (() => {
    if (node.type !== "output.trigger") return "";
    const seen = new Set([node.id]);
    const up = [];
    let at = [node.id];
    for (let hop = 0; hop < 8 && at.length; hop += 1) {
      const next = [];
      for (const e of edges || []) {
        if (!at.includes(e.target) || seen.has(e.source)) continue;
        seen.add(e.source);
        const src = nodes.find((n) => n.id === e.source);
        if (src) { up.push(src); next.push(src.id); }
      }
      at = next;
    }
    const clock = up.find((n) => n.type === "analytics.dwell");
    if (clock) {
      const cd = clock.data || {};
      const recording = cd.OID && ((cd.delivery || "live") === "once" || cd.mode === "stamps");
      return recording ? "" : "Set the Cycle Time to record each stay: tick OID and pick "
        + "Once, when they leave.";
    }
    return up.some((n) => Number(n.data?.enter_after_s) > 0) ? ""
      : "Needs a Cycle Time (or a node with a minimum stay) before it.";
  })();
  // WHICH zone a Trigger's zone colour would paint: the one the rule wired
  // into it watches. Read off the wiring rather than picked here, because the
  // runtime finds it exactly this way (run.py _color_rules keys on the
  // function's own params.zone) -- a picker of its own would be a second
  // answer, free to disagree with the wire.
  //
  // A Trigger may NAME the area on its own zone port, which is the answer
  // when it has one: a rule about a doorway may want the whole floor lit,
  // and a frame-wide count watches no zone at all and could otherwise paint
  // nothing. Falling back to the rule's own zone keeps every graph drawn
  // before that port existed painting exactly what it painted.
  // Whether a rule's OID output reaches this Trigger's OID port -- what the
  // "raise again for a new object" setting needs, and what its hint reports.
  const triggerOidWired = (edges || []).some((e) => e.target === node.id
    && (e.targetHandle || "") === "oid");
  const triggerZone = (() => {
    const wired = (edges || []).find((e) => e.target === node.id
      && (e.targetHandle || "") === "zone");
    const src = wired && (nodes || []).find((n) => n.id === wired.source);
    const zid = src?.data?.zone || upstreamFn?.data?.zone;
    if (!zid) return null;
    return (vars.zone_list || []).find((z) => z.id === zid)
      || { id: zid, name: zid };
  })();
  // The zone "Only boxes inside the zone" would test against, or null (then
  // the option is not offered). Mirrors trigger_paint.zone_behind: the
  // Trigger's own Zone port first, else the nearest Zone node behind it,
  // its zone and OID ports skipped.
  const boxZone = (() => {
    if (node.type !== "output.trigger") return null;
    const byId = new Map((nodes || []).map((n) => [n.id, n]));
    const into = (id) => (edges || []).filter((e) => e.target === id);
    const own = into(node.id).find((e) => (e.targetHandle || "") === "zone"
      && byId.get(e.source)?.type === "zone.ref");
    let zid = own ? byId.get(own.source)?.data?.zone || "" : "";
    const seen = new Set([node.id]);
    let frontier = own ? [] : [node.id];
    for (let hop = 0; hop < 12 && frontier.length && !zid; hop += 1) {
      const next = [];
      for (const id of frontier) {
        for (const e of into(id)) {
          if (zid) break;
          if (id === node.id && ["zone", "oid", "cur_oid", "cur_value"]
            .includes(e.targetHandle || "")) continue;
          if (seen.has(e.source)) continue;
          seen.add(e.source);
          const src = byId.get(e.source);
          if (!src) continue;
          if (src.type === "zone.ref") { zid = src.data?.zone || ""; continue; }
          next.push(src.id);
        }
      }
      frontier = next;
    }
    if (!zid) return null;
    return (vars.zone_list || []).find((z) => z.id === zid)
      || { id: zid, name: zid };
  })();
  // A multi-mode node (In Frame: now / total) publishes different metrics per
  // MODE, so the card downstream must read the mode of the actual node it is
  // wired to, not the type's default list.
  const slugify0 = (c) => String(c).toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const upMetrics = upstreamFn
    ? [...metricsOf(registry, upstreamFn),
       // An object watch also publishes one count per watched class
       // (fn_<id>_person) -- offer them wherever its metrics are offered.
       ...(["analytics.object", "analytics.object_frame",
            "analytics.object_zone"].includes(upstreamFn.type)
         ? (() => {
             const found = new Set((upstreamFn.data?.classes || []).map(slugify0));
             for (const e of edges || []) {
               if (e.target !== upstreamFn.id) continue;
               const src = (nodes || []).find((n) => n.id === e.source);
               if (src?.type === "filter.class") {
                 for (const c of src.data?.classes || []) found.add(slugify0(c));
               }
             }
             return [...found].filter(Boolean).sort();
           })()
         : [])]
    : [];
  // The value-producing nodes wired into THIS node, in wire order -- what a
  // multi-input verdict weighs, and the names its states default to.
  // Is this card fed by an ALERT (a Trigger, a Condition)? Then its status is
  // a normal / alert pair; otherwise it names states (DEFAULT_STATES).
  const cardFromAlert = (edges || []).some((e) => e.target === node.id
    && ["output.alert", "output.event", "output.trigger", "logic.when"]
      .includes((nodes || []).find((n) => n.id === e.source)?.type));
  const verdictWires = (edges || [])
    .filter((e) => e.target === node.id)
    .map((e) => (nodes || []).find((n) => n.id === e.source))
    .filter((n) => n && ["analytics", "variable", "logic"]
      .includes(registry[n.type]?.category || "")
      || TRANSPARENT_OUT.includes(n?.type))
    .map((n) => (n.data || {}).name || registry[n.type]?.label || n.id);

  // Anything that PUBLISHES a value wired into this node (analytics or a
  // variable). With one wired, a Card or Chart's Source reads "the value it
  // publishes" / "the events it raised"; unwired, the event store needs none.
  const upstreamValue = (() => {
    for (const e of edges) {
      if (e.target !== node.id) continue;
      const src = nodes.find((n) => n.id === e.source);
      const cat = src ? registry[src.type]?.category : null;
      if (cat === "analytics" || cat === "variable") return src;
      // An Alert or a When is transparent on the server (the card binds to
      // the function whose trigger it defines), so it counts here too --
      // without this, wiring an Alert into a recorded-events card left the inspector
      // with no Source picker at all, and no way to show the alert's state.
      if (src && (TRANSPARENT_OUT.includes(src.type) || cat === "logic")) return src;
    }
    return null;
  })();

  // The Dataset tables drawn on this canvas, so a card can read one by name.
  // Taken from the NODES rather than from the server: a table an operator has
  // just added is choosable before the graph is even saved, and the compiler
  // reads the same declaration (workflow.py dataset_defs).
  const canvasDatasets = nodes
    .filter((n) => n.type === "output.dataset")
    .map((n) => {
      const nd = n.data || {};
      const key = String(nd.dataset || "").trim();
      const columns = (nd.columns || [])
        .map((c) => ({ name: String(c.name || "").trim(), type: c.type || "number" }))
        .filter((c) => c.name);
      return key && columns.length
        ? { key, name: nd.name || key, columns } : null;
    })
    .filter(Boolean);
  // Tables designed on the Database page. A Data Table node picks one of
  // these; it cannot create one, so this list is the whole vocabulary.
  const dbTables = vars.tables || [];
  const pickedTable = dbTables.find((t) => t.key === d.dataset) || null;
  // For a Query node: the tables wired into it, which ARE the ones its
  // statement may read. Reported rather than picked -- the wire is the
  // choice, and a second control for it could disagree with the canvas.
  const queryTables = (edges || [])
    .filter((e) => e.target === node.id)
    .map((e) => (nodes || []).find((n) => n.id === e.source))
    .filter((n) => n && n.type === "output.dataset")
    .map((n) => dbTables.find((t) => t.key === (n.data || {}).dataset))
    .filter((t, i, a) => t && a.indexOf(t) === i);
  // What the wire actually offered this query. A Data Table publishes its
  // whole table on the `table` port and each column on its own `o_<name>`
  // port, and the difference is the operator saying which they meant: from
  // the table, everything it holds; from a column, that column. Either way
  // the row stamp `ts` rides along -- every row has one, it is what "when did
  // this happen" means here, and a query written without knowing its name is
  // a query that cannot ask about time.
  const queryColumns = (() => {
    const out = [];
    const seen = new Set();
    const add = (table, name, note, title) => {
      const id = `${table}.${name}`;
      if (seen.has(id)) return;
      seen.add(id);
      out.push({ table, name, note, title });
    };
    for (const e of edges || []) {
      if (e.target !== node.id) continue;
      const src = (nodes || []).find((n) => n.id === e.source);
      if (!src || src.type !== "output.dataset") continue;
      const table = dbTables.find((t) => t.key === (src.data || {}).dataset);
      if (!table) continue;
      const handle = String(e.sourceHandle || "");
      if (handle.startsWith("o_")) {
        add(table.key, handle.slice(2), "wired", table.name);
      } else {
        for (const c of table.columns || []) {
          add(table.key, c.name, c.type, table.name);
        }
      }
      // Under both names, the one the Database page shows first: its table
      // header and its CSV export call this "Datetime", and a name nobody has
      // ever seen on screen is not a hint.
      add(table.key, "datetime", "when the row was written", table.name);
      add(table.key, "ts", "the same stamp, its database name", table.name);
    }
    return out;
  })();
  // Where a clicked column name lands: at the caret, not at the end. Half the
  // time the name belongs mid-statement.
  const sqlRef = useRef(null);
  // Which of this node's columns already have a wire on the canvas. The wire
  // is the assignment, so those columns are shown as wired rather than being
  // offered a second, disagreeing control.
  const wiredColumns = (edges || [])
    .filter((e) => e.target === node.id
                   && String(e.targetHandle || "").startsWith("c_"))
    .map((e) => e.targetHandle.slice(2));
  // Whether this Data Table records WHICH object a row is about -- the same
  // test the compiler makes (an OID wire on a column, or a column set to the
  // tracked object's id). "Update the existing row" means nothing without it.
  const tracksObjects = (pickedTable?.columns || []).some((c) => {
    if (wiredColumns.includes(c.name)) {
      return (edges || []).some((e) => e.target === node.id
                                       && e.targetHandle === `c_${c.name}`
                                       && e.sourceHandle === "OID");
    }
    const s = (d.sources || {})[c.name] || {};
    return s.src === "event" && s.field === "track_id";
  });
  // Fed from the TABLE itself rather than one of its columns: the card shows
  // rows, and which columns appear is picked here.
  const readsTable = (() => {
    for (const e of edges) {
      if (e.target !== node.id || e.sourceHandle !== "table") continue;
      const src = nodes.find((n) => n.id === e.source);
      if (src?.type !== "output.dataset") continue;
      return dbTables.find((t) => t.key === (src.data || {}).dataset) || null;
    }
    return null;
  })();
  // Fed from a Data Table COLUMN's read port, rather than from a live rule.
  // {table, column, type} -- what the card is actually showing, which is what
  // decides which questions can sensibly be asked of it.
  const readsColumn = (() => {
    for (const e of edges) {
      if (e.target !== node.id) continue;
      if (!String(e.sourceHandle || "").startsWith("o_")) continue;
      const src = nodes.find((n) => n.id === e.source);
      if (src?.type !== "output.dataset") continue;
      const table = dbTables.find((t) => t.key === (src.data || {}).dataset);
      const name = e.sourceHandle.slice(2);
      const col = (table?.columns || []).find((c) => c.name === name);
      if (col) return { table, column: name, type: col.type };
    }
    return null;
  })();
  // Fed from a Query: the statement decides what the card shows, so the card
  // contributes only how it is drawn. A third way to feed a card, beside a
  // live rule and a recorded column.
  // WHICH CAMERA A PRE-PROCESS ADJUSTS: the Detection Stream wired into it.
  // A Pre-process works on a PICTURE, so its camera is the stream's, and the
  // panel needs it to catch a mask pointed at a zone drawn over a different
  // camera - a mistake whose only symptom is a detector that sees nothing.
  // Mirrors upstream_camera in workflow.py, walking back through whatever
  // sits between.
  const streamCamera = (() => {
    if (node.type !== "filter.preprocess") return "";
    const seen = new Set();
    const walk = (id, hop) => {
      if (hop > 6 || seen.has(id)) return "";
      seen.add(id);
      for (const e of edges || []) {
        if (e.target !== id) continue;
        const src = (nodes || []).find((n) => n.id === e.source);
        if (!src) continue;
        if (src.type === "source.detection") {
          return (src.data || {}).camera
                 || ((vars.cameras || [])[0] || {}).id || "";
        }
        const up = walk(src.id, hop + 1);
        if (up) return up;
      }
      return "";
    };
    return walk(node.id, 0);
  })();
  const readsQuery = (edges || []).some((e) => {
    if (e.target !== node.id) return false;
    return (nodes || []).find((n) => n.id === e.source)?.type === "storage.query";
  });
  // IS THIS NODE WIRED INTO A DETECTION STREAM SET TO SNAPSHOT? The Frames
  // field exists for exactly that and nothing else: anywhere else an Inject or
  // a Trigger is what it always was, and shows nothing extra.
  const wiredToSnap = (edges || []).some((e) => {
    if (e.source !== node.id || e.targetHandle !== "snap") return false;
    const tgt = (nodes || []).find((n) => n.id === e.target);
    return tgt?.type === "source.detection" && (tgt.data || {}).capture === "snapshot";
  });
  // A CHART DRAWING SEVERAL LINES, and therefore editing them as a list.
  //
  // The same test the compiler makes: more than one wire, or any wire dropped
  // on a named Series port, and the chart compiles to a series list rather
  // than to one value. Below that it is still the old single-wire chart, and
  // its one wire is edited by the controls that have always edited it - two
  // panels for one wire would be two answers to one question.
  // WHERE A CARD OR CHART READS FROM when it is not simply its wire: the
  // recorded event store or a table named on it -- what the retired Event
  // Card was, as a setting. "" follows the wire. Mirrors EVENT_SOURCE_MODES.
  const sourceMode = recordedMode(node);
  const graphWires = node.type === "dashboard.graph"
    ? graphSeriesSlots(node, nodes, edges) : [];
  // A chart asking recorded data draws ITS question; a rule wired into it
  // scopes that question rather than drawing a line (the compiler agrees).
  // A Card or Chart wired in makes a series chart even alone on the plain
  // port, as it does in the compiler: it hands over a question, not a value.
  const graphSeries = !sourceMode && (graphWires.length > 1
    || graphWires.some((w) => w.named
                       || registry[w.src.type]?.category === "dashboard"));
  // The shapes the Card's "Show as" offers for this source -- and the saved
  // one on top when it is not among them, so the select never says "stat
  // tile" over a card the compiler still draws as a gauge or a verdict (a
  // migrated or adopted card can carry any shape the Event Card allowed).
  const cardShapes = ["tile",
    ...(sourceMode !== "events" ? ["gauge"] : []),
    ...(!sourceMode ? ["verdict", "status"] : []),
    ...(sourceMode ? ["table"] : [])];
  const savedShapeOff = node.type === "dashboard.card" && !!d.kind
    && !cardShapes.includes(d.kind);
  // Whether this node feeds a Chart as a series: its axis rows (group by,
  // time bucket) then shape that series even on a one-value Card.
  const feedsChart = (edges || []).some((e) => e.source === node.id
    && (nodes || []).find((n) => n.id === e.target)?.type === "dashboard.graph");
  // Which shape this card draws as. The one-shape-per-node types are
  // deprecated but still load, so their own kind stands in.
  const cardKind = d.kind || ({ "dashboard.gauge": "gauge",
    "dashboard.verdict": "verdict" }[node.type]) || "tile";
  const chosenDataset = canvasDatasets.find((x) => x.key === d.dataset) || null;
  // A Chart node is drawn by the chart builder (GraphSeriesRows) whatever
  // feeds it -- a table column, several inputs, a rule, or its own recorded
  // question -- except a Query (its statement decides) and a whole table.
  // Where a CARD's value comes from, for its one Date range: a Query (its
  // own period applies), recorded rows, or a live rule's value.
  const cardSource = readsQuery ? "none"
    : (sourceMode || readsColumn || readsTable) ? "recorded"
      : upstreamFn ? "live" : null;
  const chartBuilder = node.type === "dashboard.graph" && !readsQuery && !readsTable
    && (graphSeries || !!readsColumn || !!sourceMode || !!upstreamFn);
  // What the chart builder is handed: a one-wire column, or the recorded
  // question the chart asks itself.
  const graphSingle = !graphSeries && !sourceMode ? readsColumn : null;
  const graphRecorded = sourceMode ? {
    mode: sourceMode,
    columns: sourceMode === "dataset"
      ? (chosenDataset?.columns || [])
      : (vars.event_fields || []).map((f) => ({ name: f, type: "text" })),
  } : null;
  // THE CHART TYPE (chartTypes.js): its kind plus the model's X type -- a
  // Time-based and a Comparison Bar are both "bar". Read the way the chart
  // builder reads it, so the strip names what the fields below show.
  const chartRead = node.type !== "dashboard.graph" ? null
    : chartBuilder
      ? graphNodeModel({ node, nodes, edges, registry, d, dbTables,
                         single: graphSingle, recorded: graphRecorded })
      : { kind: d.kind || "area", overrides: {},
          model: readNodeModel(d, d.kind || "area", [], {}).model };
  const chartTypeId = chartRead ? chartTypeOf(chartRead.kind, chartRead.model?.x_type) : "";

  const set = (k) => (e) => onChange({ [k]: e.target?.type === "checkbox"
    ? e.target.checked : e.target?.type === "number" ? +e.target.value : e.target.value });

  // The bus settings take over this panel rather than a page -- see the
  // button below. Reset whenever the selected node changes, so clicking a
  // different node never lands on somebody else's settings screen.
  const [showBus, setShowBus] = useState(false);
  useEffect(() => { setShowBus(false); }, [node.id]);
  // The Modbus Server editor, the same way: null = the node, "" = adding a
  // new server, an id = editing that one. Node-RED opens its config node over
  // the node's own tray; this is that, inside the rail.
  const [serverEdit, setServerEdit] = useState(null);
  useEffect(() => { setServerEdit(null); }, [node.id]);
  // The MQTT Connection editor, the same way: null = the node, "" = adding.
  const [mqttEdit, setMqttEdit] = useState(null);
  useEffect(() => { setMqttEdit(null); }, [node.id]);
  // The chart type picker: open for a chart just dropped on the canvas
  // (chartTypes.isFreshChart, session only) or after "Change chart type".
  const [typePickFor, setTypePickFor] = useState(null);
  const [, bumpPicker] = useState(0);
  const pickingType = node.type === "dashboard.graph"
    && (isFreshChart(node.id) || typePickFor === node.id);
  const applyChartType = (id) => {
    const fresh = isFreshChart(node.id);
    const plan = planTypeSwitch(chartRead.kind, chartRead.model, id, { fresh });
    clearFreshChart(node.id);
    setTypePickFor(null);
    bumpPicker((n) => n + 1);
    if (!plan || plan.same) return;
    if (!plan.full) { onChange({ kind: plan.kind }); return; }
    // The whole model, as the chart builder's first edit writes it; a chart
    // with nothing wired yet has no inputs to rewrite, only its own fields.
    onChange({ kind: plan.kind, ...(chartBuilder
      ? nodePatch(d, plan.kind, plan.model, chartRead.overrides, { recorded: !!graphRecorded })
      : modelNodeFields(plan.model, plan.kind)) });
  };
  // Does the statement name the period at all? A Period setting on a query
  // that never mentions %(from)s is a setting with no effect, and the only
  // honest thing to do is say so where it is set.
  const sqlWantsDates = /%\((?:from|to)\)s/.test(String(d.sql || ""));

  if (showBus) {
    return <BusSettingsPanel onBack={() => setShowBus(false)}
                             editable={editable} />;
  }
  if (mqttEdit !== null) {
    return <MqttConnectionEditor connId={mqttEdit}
                                 onBack={() => setMqttEdit(null)}
                                 // A connection ADDED from a node is picked
                                 // for it, which is what the plus was for.
                                 onSaved={(c) => { if (!mqttEdit && c?.id) onChange({ connection: c.id }); }}
                                 editable={editable} />;
  }
  if (serverEdit !== null) {
    return <ModbusServerEditor serverId={serverEdit}
                               onBack={() => setServerEdit(null)}
                               // A server ADDED from a node is picked for it,
                               // which is what the plus was pressed for.
                               onSaved={(srv) => { if (!serverEdit && srv?.id) onChange({ server: srv.id }); }}
                               editable={editable} />;
  }

  return (
    <>
      {/* Name + a hover "?" instead of a paragraph: the same sentence on
          every visit is furniture, and it pushed the actual controls down. */}
      <div className="section-title" style={{ display: "flex", gap: 6, alignItems: "center" }}>
        {spec.label}
        {/* children, not a text= prop -- InfoHint renders its children and
            returns null without any, so the wrong prop name here meant the
            node-type description dot silently never rendered at all. */}
        {spec.description && <InfoHint>{spec.description}</InfoHint>}
        {/* A Class Filter's On / Off, at the top right: the SAME state as the
            dashboard's Class filter card (Off = paused, the rules after it
            held back), stored on this node (services/class_filter.py). */}
        {node.type === "filter.class" && (
          <ClassFilterSwitch on={d.running !== false} disabled={!editable}
                             onChange={(on) => onChange({ running: on })} />
        )}
        {/* OID rides the TITLE, at the far right, because it is not a setting
            among settings: it changes what the node OUTPUTS -- a port appears
            on the canvas and a Data Table can record who each row was about.
            It sat inside Advanced, collapsed, which is where a plumbing detail
            belongs and not where the switch that adds an output does. The
            sentence explaining it stays below, next to the ports it adds. */}
        {COUNTS_BY_PID.has(node.type)
          && !(node.type === "analytics.dwell"
               && ["countdown", "occupancy"].includes(d.mode)) && (
          <label className="muted small"
                 title={node.type === "analytics.dwell"
                   ? "Also record each completed stay, with the object's id"
                   : "Also give out the object id of each counted object"}
                 style={{ marginLeft: "auto", display: "inline-flex",
                          alignItems: "center", gap: 4, fontWeight: 400 }}>
            <input type="checkbox" checked={!!d.OID} disabled={!editable}
                   onChange={set("OID")} />
            OID
          </label>
        )}
        {/* The RS485 nodes read and write a bus whose SETTINGS -- transport,
            port, baud, unit id -- belong to the WIRE and not to any one node
            on it, so they are not fields on the node.

            They open IN THIS PANEL rather than on their own page. Leaving the
            canvas to change one number means losing your place in the graph
            and then hunting for the node again on the way back -- the page
            grew a "back to workflow" button precisely because that round trip
            was bad, which is the wrong end of the problem to fix. The page
            still exists for the rail; both are views of the same endpoint. */}
        {/* The READ node no longer offers it: where it reads is its own
            Server row (a Modbus Server connection), and these settings are
            the box answering a PLC -- a different conversation. */}
        {node.type === "output.modbus_reg" && (
          <button type="button" className="btn btn-sm"
                  onClick={() => setShowBus(true)}
                  title="Modbus/RS485 bus settings - transport, port, baud, unit id"
                  style={{ marginLeft: "auto", display: "inline-flex",
                           alignItems: "center", gap: 4, padding: "2px 8px" }}>
            Bus settings <ArrowRight size={13} />
          </button>
        )}
      </div>
      {/* WHAT THIS NODE IS, in three read-only rows -- what goes in, what it
          does, what comes out -- straight under its name, above everything
          that changes it (Tok Aba, 2026-10-14: in the Properties of every
          node, not on the node itself). Re-read on every render, so it
          follows each setting and each wire the moment they change. */}
      <NodeSummaryBox box={summaryFor({
        type: node.type, data: d, spec, zones: vars.zone_list,
        zoneWired: (edges || []).some((e) => e.target === node.id
                                       && (e.targetHandle || "in") === "zone"),
        cmode: counterMode(node, edges),
      })} />
      {err && <p className="small c-danger">{err}</p>}
      {!err && warn && <p className="small c-warn">{warn}</p>}

      {node.type === "note.group" && (
        <>
          <Field label="Group Name" hint="The tag on the group. Changes nothing that runs.">
            <input className="input" value={d.name || ""} disabled={!editable}
                   placeholder="e.g. Gate A"
                   onChange={(e) => onChange({ name: e.target.value.slice(0, 60) })} />
          </Field>
          <Field label="Colour">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
              {["#3B82F6", "#06B6D4", "#10B981", "#84CC16", "#EAB308", "#F97316",
                "#EF4444", "#EC4899", "#8B5CF6", "#64748B"].map((c) => (
                <button key={c} type="button" disabled={!editable} title={c}
                        onClick={() => onChange({ color: c })}
                        style={{ width: 24, height: 24, borderRadius: 6, background: c,
                                 cursor: editable ? "pointer" : "default", padding: 0,
                                 border: (d.color || "#3B82F6").toLowerCase() === c.toLowerCase()
                                   ? "2px solid var(--text, #111)" : "2px solid transparent" }} />
              ))}
              <input type="color" value={d.color || "#3B82F6"} disabled={!editable}
                     title="Any colour" style={{ width: 32, height: 26, padding: 0,
                                                 border: 0, background: "none" }}
                     onChange={(e) => onChange({ color: e.target.value })} />
            </div>
          </Field>
          <p className="muted small">
            Drop nodes inside the group to tag them; dragging the name tag moves the
            group and everything in it. Drag the corner to resize.
          </p>
        </>
      )}
      {node.type === "note.comment" && (
        <>
          <Field label="Note">
            <textarea className="input" rows={7} value={d.text || ""} disabled={!editable}
                      placeholder="Why does this part of the graph exist?"
                      onChange={(e) => onChange({ text: e.target.value.slice(0, 2000) })} />
          </Field>
          <Field label="Layer"
                 hint={"Behind: the note sits under the nodes it explains, so a big one "
                       + "can wrap a whole branch without covering it. In front: the note "
                       + "sits over them. Either way it comes to the top while it is "
                       + "selected, so you can always move and resize it."}>
            <select className="input" value={d.layer === "front" ? "front" : "back"}
                    disabled={!editable} onChange={set("layer")}>
              <option value="back">behind the nodes</option>
              <option value="front">in front of the nodes</option>
            </select>
          </Field>
          <p className="muted small">
            Drag the corner of the note to resize it.
          </p>
        </>
      )}

      {/* With one model there is nothing to choose, so the field stays out of
          the way; with several, blank still means "all of them". */}
      {node.type === "source.detection" && (
        <Field label="Capture"
               hint={"Video detects every frame, as it always has. Snapshot detects only "
                     + "when something wired into the node's snap input goes true - an "
                     + "Inject, a Trigger or a sensor - for the number of frames that "
                     + "source asks for, then waits for the next one."}>
          <select className="input" value={d.capture === "snapshot" ? "snapshot" : "video"}
                  disabled={!editable} onChange={set("capture")}>
            <option value="video">Video - every frame</option>
            <option value="snapshot">Snapshot - only when triggered</option>
          </select>
        </Field>
      )}

      {node.type === "source.detection" && d.capture === "snapshot" && (
        <Field label="Camera between snaps"
               hint={"Automatic closes a USB or other local camera between snaps "
                     + "(lowest CPU; the camera takes about a second to open when "
                     + "a snap starts) and keeps a network (RTSP) camera open but "
                     + "reading slowly (it is slow to reopen). Choose Close or Keep "
                     + "to override."}>
          <select className="input" value={d.camera_idle || "auto"}
                  disabled={!editable} onChange={set("camera_idle")}>
            <option value="auto">Automatic</option>
            <option value="close">Close the camera (lowest CPU)</option>
            <option value="keep">Keep it open (fastest snap)</option>
          </select>
        </Field>
      )}

      {node.type === "source.detection" && (vars.models || []).length > 0 && (
        <>
          <Field label="Model"
                 hint="Blank uses every enabled model's detections. Pick one to build a branch that only sees what THAT model found.">
            <select className="input" value={d.model || ""} disabled={!editable}
                    onChange={set("model")}>
              <option value="">All models</option>
              {(vars.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>
        </>
      )}

      {/* Same rule for cameras: a single-camera system has nothing to choose,
          so the field never appears. Zones already belong to a camera, so
          picking one here that contradicts the zone is a compile error. */}
      {node.type === "source.detection" && Array.isArray(vars.cameras)
        && vars.cameras.length > 1 && (
        <>
          {/* Every camera is offered BY NAME. "First camera" used to lead the
              list and was the stored default, which meant the inspector said
              one thing ("first") while the canvas, the compile error and the
              saved photo all said another (the camera's name) -- and moving a
              camera to the top of the list silently repointed every branch
              that had been left alone. A branch names the picture it reads. */}
          <Field label="Camera"
                 hint={"Every camera runs this branch on all of them and adds "
                       + "the results up - for rules about objects, not places. "
                       + "A rule that uses a Zone runs on that zone's camera, "
                       + "so it cannot be set to every camera."}>
            <select className="input" disabled={!editable}
                    value={d.camera || (vars.cameras[0] || {}).id || ""}
                    onChange={set("camera")}>
              <option value="*">Every camera</option>
              {(vars.cameras || []).map((c) => (
                <option key={c.id} value={c.id}>{camLabel(vars.cameras, c.id)}</option>
              ))}
            </select>
          </Field>
        </>
      )}

      {/* A Pre-process is wired as a filter but narrows nothing by CLASS -
          it adjusts the picture itself, so the class list would be a control
          that does nothing on it. */}
      {/* A Class Filter's NAME is what the dashboard's Class filter card
          lists it by. Optional with one filter; with several the save is
          refused until each has its own (classFilterNaming), and the reason
          is said right here rather than only as a red dot on the node. */}
      {node.type === "filter.class" && (() => {
        const why = classFilterNaming(nodes)[node.id];
        return (
          <Field label="Class Filter Name"
                 hint={"How the dashboard's Class filter card lists this filter, "
                       + "so it can be paused there. Needed when the workflow has "
                       + "more than one Class Filter."}>
            <input className="input" value={d.name || ""} disabled={!editable}
                   placeholder="e.g. people" maxLength={60}
                   aria-label="Class Filter name"
                   onChange={(e) => onChange({ name: e.target.value.slice(0, 60) })} />
            {why && <p className="small c-danger" data-naming="1"
                       style={{ margin: "4px 0 0" }}>{why}</p>}
          </Field>
        );
      })()}

      {spec.category === "filter" && node.type !== "filter.preprocess" && (
        <ClassPicker classes={vars.classes || []} active={vars.classes_active}
                     value={d.classes || []}
                     editable={editable}
                     onChange={(classes) => onChange({ classes })} />
      )}

      {node.type === "filter.preprocess" && (
        <PreprocessSteps d={d} editable={editable} onChange={onChange}
                         zoneList={zoneList} cameras={vars.cameras || []}
                         camera={streamCamera} />
      )}

      {node.type === "zone.ref" && (
        <Field label="Zone"
               hint="Draw and edit zones on the Zones page - this node points a branch at one of them.">
          <select className="input" value={d.zone || ""} disabled={!editable}
                  onChange={set("zone")}>
            <option value="">- choose a zone -</option>
            {zoneOptions(zoneList)}
          </select>
        </Field>
      )}

      {/* Two node TYPES, one concept: a value typed here (variable.number,
          made from the Variables panel's "Constant" checkbox), or an empty
          holder that gets its value once something is wired in (variable.ref,
          unwired). Switching retypes the node IN PLACE -- same spot on the
          canvas, no rewiring -- for turning an existing one of these into
          the other after the fact. */}
      {(node.type === "variable.number" || node.type === "variable.ref") && (
        <Field label="Variable type"
               hint="Normal: takes its value from whatever is wired in. Constant: a value you type here that never changes.">
          <select className="input" disabled={!editable}
                  value={node.type === "variable.number" ? "static" : "normal"}
                  onChange={(e) => {
                    const toStatic = e.target.value === "static";
                    // `name` is the one thing both faces mean the same way; a
                    // stray key/io from the holder or a stray value from the
                    // constant would be dead data on the other side, so only
                    // it survives the switch.
                    // The SAME variable either way: a normal variable that
                    // picked "test" keeps its name in `key` (fn_test_value),
                    // so the name is read from there when it has none of
                    // its own, and the constant publishes under that same
                    // name (fnId), so everything already reading it keeps
                    // reading it.
                    const declared = customVars.find(
                      (v) => `fn_${v.var}_value` === d.key);
                    const name = d.name || (declared && declared.name)
                      || plainName(d.key) || "";
                    const stem = slugify(name) || d.fnId || "";
                    // Already a constant by this name? It holds ONE value:
                    // this one joins it.
                    const same = toStatic && stem
                      ? (nodes || []).find((n) => n.type === "variable.number"
                          && n.data?.fnId === stem) : null;
                    onRetype?.(toStatic ? "variable.number" : "variable.ref",
                              toStatic ? { name: (same && same.data.name) || name,
                                           value: same ? same.data.value ?? 0 : 0,
                                           value_type: same ? same.data.value_type || "number" : "number",
                                           ...(stem ? { fnId: stem } : {}) }
                                       : { name, io: "auto",
                                           ...(stem ? { key: `fn_${stem}_value` } : {}) });
                  }}>
            <option value="static">constant variable</option>
            <option value="normal">normal variable</option>
          </select>
        </Field>
      )}

      {/* Which SIDES this variable offers. It used to be inferred purely from
          whether something happened to be wired in, so the node changed
          meaning under the operator as they built, and there was no way to
          say "this one is for reading" before wiring anything. */}
      {node.type === "variable.ref" && (
        <Field label="Use as"
               hint={"Pass = use the variable: its value feeds whatever this "
                     + "node is wired into. Store = whatever is wired in is "
                     + "saved under this name. Both = store it and pass it "
                     + "on. Automatic keeps the original behaviour: wired "
                     + "means store, unwired means pass."}>
          <select className="input" value={d.io || "auto"} disabled={!editable}
                  onChange={set("io")}>
            <option value="auto">automatic (from the wiring)</option>
            <option value="in">pass - use the variable's value</option>
            <option value="out">store - save what is wired in</option>
            <option value="both">both - store it and pass it on</option>
          </select>
        </Field>
      )}
      {node.type === "variable.ref" && (
        <Field label="Value type"
               hint={"What this variable holds. Every Set Variable that writes it stores "
                     + "this kind, whatever the setter says. Automatic reads it off what is "
                     + "stored: a word is text, 12 is a whole number, 4.5 decimal."}>
          <select className="input" value={d.value_type || "auto"} disabled={!editable}
                  onChange={set("value_type")}>
            <option value="auto">automatic - from what is stored in it</option>
            <option value="text">text</option>
            <option value="decimal">decimal</option>
            <option value="number">whole number</option>
          </select>
        </Field>
      )}

      {/* Fed by a wire: this node STORES the wired output, so the thing to
          choose is the variable NAME it lands in. */}
      {node.type === "variable.ref" && querySrc && (
        <>
          <Field label="Stores"
                 hint="The Query wired into this node. Its answer is a table, so the two fields below say which one number to keep.">
            <input className="input" readOnly disabled
                   value={(querySrc.data || {}).name || "Query"} />
          </Field>
          <Field label="Into variable"
                 hint="The name this number is published under.">
            <KeyPicker value={chosenVar}
                       keys={storeCandidates.map((c) => c.var)}
                       editable={editable}
                       labelFor={(v) => (storeCandidates
                         .find((c) => c.var === v) || {}).label || v}
                       onPick={async (v) => {
                         if (v !== NEW_VARIABLE) { onChange({ name: v }); return; }
                         const name = await ask({
                           title: "New variable",
                           description: "A name for the number this Query stores. "
                             + "Every variable needs its own name.",
                           label: "Name", defaultValue: "shift_total" });
                         if (!name) return;
                         const varName = slugify(name);
                         if (!varName) return;
                         if (onCreateVar?.(name.trim(), varName) === false) return;
                         onChange({ name: varName });
                       }}
                       extra={[{ key: NEW_VARIABLE, label: "+ New variable…" }]} />
          </Field>
          <Field label="Every" hint="Seconds between runs. Ten is the floor.">
            <input className="input" type="number" min="10" max="3600"
                   value={d.q_interval_s ?? 60} disabled={!editable}
                   onChange={set("q_interval_s")} />
          </Field>
          {/* WHY these two are folded away rather than shown.
              A query written for a variable is usually SELECT count(*): one
              row, one column, and there is nothing to choose -- the defaults
              are the only sensible answer, so asking reads as a decision the
              operator has to make when it is not one. But a query CAN return
              many rows ("SELECT zone, count(*) GROUP BY zone"), and a variable
              holds one number, so the choice has to exist somewhere: guessing
              it would mean a variable that means "the first row" today and
              "the total" tomorrow.
              Nothing can tell which case this is without running the
              statement, so the answer is a summary line that says what will
              be kept, and the controls one click behind it. */}
          <details className="wf-pick-more">
            <summary className="muted small">
              Keeping {PICK_WORDS[d.q_pick || "first"]}
              {(d.q_column || "").trim()
                ? <> of <code className="mono">{d.q_column.trim()}</code></>
                : " of the first column holding a number"}
              {" - change"}
            </summary>
            <Field label="Column"
                   hint="Which column of the result to read. Blank takes the first one holding a number.">
              <input className="input" value={d.q_column || ""} disabled={!editable}
                     placeholder="the first numeric column"
                     onChange={set("q_column")} />
            </Field>
            <Field label="Keep"
                   hint="A query answers with rows; a variable holds one number. This is which.">
              <select className="input" value={d.q_pick || "first"} disabled={!editable}
                      onChange={set("q_pick")}>
                {Object.entries(PICK_WORDS).map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
            </Field>
          </details>
          <p className="muted small" style={{ marginTop: -4 }}>
            While the query fails or returns no rows the variable keeps its
            last value and its "ok" signal{" "}
            goes to 0 - “no rows” and “zero” are different answers.
          </p>
        </>
      )}
      {node.type === "variable.ref" && !querySrc && wiredInputs.length > 0 && (
        <>
          <Field label="Stores"
                 hint="What is wired into this node. Delete the wire to make this a reader instead.">
            <input className="input" readOnly disabled
                   value={wiredInputs[0].label} />
          </Field>
          <Field label="Into variable"
                 hint={`Your DECLARED variables (Variables panel). Values the
                   pipeline publishes are not offered here - each belongs to
                   the rule that makes it, and a store would be a second
                   writer.`}>
            <KeyPicker value={chosenVar}
                       keys={storeCandidates.map((c) => c.var)}
                       editable={editable}
                       labelFor={(v) => (storeCandidates
                         .find((c) => c.var === v) || {}).label || v}
                       onPick={async (v) => {
                         if (v !== NEW_VARIABLE) {
                           onChange({ name: v });
                           return;
                         }
                         const name = await ask({
                           title: "New variable",
                           description: "A name for the value this node stores. "
                             + "Every variable needs its own name.",
                           label: "Name", defaultValue: "total_violation" });
                         if (!name) return;
                         const varName = slugify(name);
                         if (!varName) return;
                         if (onCreateVar?.(name.trim(), varName) === false) return;
                         onChange({ name: varName });
                       }}
                       extra={[{ key: NEW_VARIABLE, label: "+ New variable…" }]} />
          </Field>
        </>
      )}
      {node.type === "variable.ref" && !querySrc && wiredInputs.length === 0 && (
        <Field label="Variable"
               hint="Pick a variable by its name, or wire a value into this node and it holds that value.">
          {/* Only the variables the person declared, by name. The values the
              system publishes (every counter, branch and camera stat) are
              not offered: a variable holds what is wired into it. */}
          <KeyPicker value={d.key || ""}
                     keys={customVars.map((v) => `fn_${v.var}_value`)}
                     editable={editable}
                     labelFor={(k) => {
                       const dec = customVars.find((v) => `fn_${v.var}_value` === k);
                       return dec ? dec.name
                         : statLabel(k, functions, vars.cameras, vars.zone_list,
                                     vars.state_labels);
                     }}
                     onPick={async (v) => {
                       if (v !== NEW_VARIABLE) {
                         // The name travels with the pick, so wiring a value
                         // in later stores into this same variable.
                         onChange({ key: v, name: plainName(v) });
                         return;
                       }
                       // Declaring, not building: a new variable is a NAME.
                       const name = await ask({
                         title: "New variable",
                         description: "A name to declare now and feed later. "
                           + "Until something publishes it, it reads 0.",
                         label: "Name", defaultValue: "my_variable" });
                       if (!name) return;
                       const varName = slugify(name);
                       if (!varName) return;
                       if (onCreateVar?.(name.trim(), varName) === false) return;
                       onChange({ key: `fn_${varName}_value`, name: varName });
                     }}
                     extra={[{ key: NEW_VARIABLE, label: "+ New variable…" }]} />
        </Field>
      )}

      {(spec.category === "analytics" || spec.category === "variable")
        && node.type !== "variable.ref" && (
        <>
          {/* The user's name for this node, and the ONLY name they meet: the
              internal variable id (data.fnId, the fn_<id>_<metric> key stem)
              is minted automatically on add/paste/import and never re-minted,
              so renaming here can never orphan history or hand-bound widgets.
              The editable "Variable name" field this replaces existed only to
              expose that id -- and editing it was exactly the rename its own
              tooltip warned against. The key is still READABLE, under
              Advanced below, for anyone hand-binding a widget. */}
          {/* A Formula has no name to give. It is not a measurement somebody
              looks up later -- it is an operation applied to whatever is wired
              in, and what it produces is read where it is wired OUT (a card
              carries its own name). Naming it meant inventing a label for a
              piece of arithmetic before knowing what the arithmetic was for.
              Everything else keeps its name: a Zone Counter's number turns up
              in pickers, cards and tables, and "fn_ntma91_count" is not an
              answer to which counter that is. */}
          {/* ...and a Math node is that same argument, thirteen times over:
              "Add" is an operation applied to what is wired in, not a
              measurement anybody looks up by name later. */}
          {node.type !== "analytics.formula" && !spec.math && (
            <Field label={`${spec.label || "Node"} Name`}
                   hint="Shown everywhere this node's numbers appear - cards, pickers, tables.">
              <input className="input" value={d.name || ""} disabled={!editable}
                     onChange={set("name")} />
            </Field>
          )}
          {(node.type === "analytics.travel" || node.type === "analytics.speed") && (
            <>
              {/* Speed is OPT-IN, because it needs a number nobody on the box
                  can work out: how far apart the two lines really are. Off,
                  the node measures times only and asks nothing. A node saved
                  before this switch existed has no `speed` key and WAS
                  publishing speeds, so absent means on. */}
              <Field label="Also show speed"
                     hint="Needs the real-world distance between the two lines. Off measures trip times only.">
                <select className="input"
                        value={d.speed === undefined ? "on" : (d.speed || "off")}
                        disabled={!editable} onChange={set("speed")}>
                  <option value="off">Off</option>
                  <option value="on">On</option>
                </select>
              </Field>
              {(d.speed === undefined ? "on" : d.speed) !== "off" && (
                <>
                  <Field label="Distance between the lines (m)"
                         hint="Measured on the ground, entrance to exit. Every speed this node reports is worked out from it.">
                    <input className="input" type="number" min="0" step="0.1"
                           value={d.distance_m ?? 10} disabled={!editable}
                           onChange={set("distance_m")} />
                  </Field>
                  <Field label="Speed unit">
                    <select className="input" value={d.speed_unit || "kmh"}
                            disabled={!editable} onChange={set("speed_unit")}>
                      <option value="kmh">km/h</option>
                      <option value="mph">mph</option>
                      <option value="ms">m/s</option>
                    </select>
                  </Field>
                </>
              )}
              <p className="muted small">
                Wire the line they cross first into <strong>Entrance line</strong>{" "}
                and the one they cross second into <strong>Exit line</strong> -
                a trip is one crossing of each, in that order.
              </p>
            </>
          )}
          {node.type === "analytics.cycle_time" && (
            <p className="muted small">
              Wire a Zone into <code>start</code> and another into <code>end</code> - each
              end can be an area or a line.
            </p>
          )}
          {(node.type === "analytics.expr" || node.type === "variable.formula") && (
            <>
              {/* Anything wired into this formula can be used BY NAME: the
                  editor's Variables panel lists the wired names, and the
                  Expression box is the one place the formula lives
                  (ExprEditor.jsx, Tok Aba's layout 2026-10-14). A <div>, not
                  the Field <label>: the editor holds buttons of its own. */}
              <div className="wf-field">
                <ExprEditor value={d.expr || ""} editable={editable}
                            statKeys={vars.stat_keys || []}
                            knownNames={wiredInputs.map((w) => w.name)}
                            wired={wiredInputs} fnId={d.fnId}
                            labelFor={(k) => statLabel(k, functions,
                                                       vars.cameras, vars.zone_list,
                                                       vars.state_labels)}
                            onChange={(expr) => onChange({ expr })} />
              </div>
            </>
          )}
          {/* A node whose spec declares modes: one entry in the palette, the
              choice made here. The metrics it publishes follow the mode. */}
          {/* Hidden modes stay OUT of the picker (retired choices -- distinct
              counting has supported paths now) but a node already SET to one
              keeps showing it: taking a setting away from a node that uses
              it would silently re-decide what that node counts. When only
              one choice remains there is no choice, so no picker. */}
          {/* THE COUNTER says what it is counting before anything else, in
              the words its face uses, with what to wire to make it count
              something else. One node, three jobs, and the wiring picks the
              job -- so the panel's first line is the answer to "which one am
              I looking at", and only that job's settings follow. */}
          {isCounter(node.type) && (() => {
            const cmode = counterMode(node, edges);
            const say = {
              zone: <>Counts the objects <strong>inside the zone</strong> wired
                    into its Zone port. Wire a Line instead to count crossings,
                    or take the zone off to count the whole view.</>,
              line: <>Counts the objects <strong>crossing the line</strong> wired
                    into its Line port. Take the line off to count a zone or
                    the whole view instead.</>,
              object: <>Counts the objects <strong>in the whole view</strong>.
                      Wire a Zone into its Zone port to count inside an area,
                      or a Line into its Line port to count what crosses it.</>,
            }[cmode];
            return (
              <Field label="What it counts">
                <div className="input" style={{ height: "auto", lineHeight: 1.45,
                                                 whiteSpace: "normal" }}>
                  <strong style={{ textTransform: "capitalize" }}>
                    {COUNTER_MODE_LABEL[cmode]}
                  </strong>
                  <div className="muted small" style={{ marginTop: 2 }}>{say}</div>
                </div>
              </Field>
            );
          })()}
          {spec.modes && (() => {
            const current = d.mode || Object.keys(spec.modes)[0];
            const offered = Object.entries(spec.modes)
              .filter(([key, m]) => !m.hidden || key === current);
            if (offered.length < 2) return null;
            const isCycle = node.type === "analytics.dwell";
            // The Counter's two choices, named for WHERE it is counting: "in
            // zone now" on a counter with no zone was the one word on the
            // panel that was wrong. Same stored values, so nothing it counts
            // changes.
            const cmode = counterMode(node, edges);
            const words = node.type === COUNTER
              ? (cmode === "zone"
                ? { realtime: "How many are in the zone now",
                    distinct: "Total that entered (each object once)" }
                : { realtime: "How many are in view now",
                    distinct: "Total seen (each object once)" })
              : node.type === LINE_COUNTER
              ? { in: "Going in", out: "Going out", both: "Both ways (in and out)" }
              : {};
            return (
              <Field label={isCycle ? "What to measure"
                            : node.type === LINE_COUNTER ? "Which way counts"
                            : "Count"}
                     hint={isCycle
                           ? "Time present measures how long objects stay, "
                             + "Countdown gives each arrival a budget, and "
                             + "Occupied clock runs while the place is in use "
                             + "at all. The outputs follow the choice."
                           : node.type === LINE_COUNTER
                           ? "Which crossings this Counter counts. A line drawn "
                             + "with no in/out side counts crossings only, "
                             + "whichever you pick. The outputs follow the choice."
                           : "'Now' modes are the number present this instant. "
                             + "'Distinct' modes count each object once by object id "
                             + "from a per-day tally kept on disk, so today / "
                             + "this week / this month survive a restart."}>
                <select className="input" value={current}
                        disabled={!editable} onChange={set("mode")}>
                  {offered.map(([key, m]) => (
                    <option key={key} value={key}>{words[key] || m.label || key}</option>
                  ))}
                </select>
              </Field>
            );
          })()}
          {/* Counting each object ONCE is a different question from which
              direction counts, so it is its own switch rather than another
              mode: every mode above can be asked either way. */}
          {node.type === "analytics.entry_exit" && (
            <Field label="How it counts"
                   hint={"Live: In is how many objects are on the line going in right now, "
                         + "Out how many going out - 1 while one is over it, 2 while two are, "
                         + "0 once they have passed. Wire them straight into a Data Table's "
                         + "columns (record on change) and each crossing is its "
                         + "own row; the day's total is the sum of the column, and the "
                         + "node's Total ports carry the running tally. "
                         + "Running total: every crossing adds to a number that "
                         + "only grows - a turnstile. Each object once: a person "
                         + "who paces back and forth through the door all "
                         + "morning is one in and one out."}>
              <select className="input"
                      value={d.momentary ? "moment" : d.distinct ? "once" : "every"}
                      disabled={!editable}
                      onChange={(e) => onChange({ distinct: e.target.value === "once",
                                                  momentary: e.target.value === "moment" })}>
                <option value="moment">live - objects on the line right now, 0 once passed</option>
                <option value="every">running total - every crossing</option>
                <option value="once">running total - each object once (by OID)</option>
              </select>
            </Field>
          )}
          {/* The band is a property of the LINE, not of this counter: the
              side decision runs in the zone engine before any function sees
              a crossing, and every counter, travel timer and table on that
              line must agree on what a crossing is. So no control here --
              a pointer to where the one control lives, because the symptom
              (in AND out from one person) is noticed on this node. */}
          {node.type === "analytics.entry_exit" && (
            <p className="muted small">
              Counted in and out by a shivering box, or missing short steps? The
              line's <strong>Crossing threshold</strong> is set on the Zones page,
              on the line itself - every counter on that line shares it.
            </p>
          )}
          {node.type === "analytics.entry_exit" && d.momentary && (
            <Field label="Totals"
                   hint={"Beside the live In and Out, the node offers Total in, Total out, "
                         + "Inside (total) and Crossings (total) as ports - the running tally "
                         + "since the app started. Wire a card or a Math node to them."}>
              <div className="input muted small">total in · total out · inside · crossings</div>
            </Field>
          )}
          {node.type === "analytics.dwell" && d.mode === "countdown" && (
            <>
              <Field label="Time budget (s)"
                     hint={"The countdown starts from this many seconds. "
                           + "remaining_s is the value to watch - bind a card "
                           + "to it, or alert when it reaches 0."}>
                <input className="input" type="number" min="1" step="1"
                       value={d.start_s ?? 60} disabled={!editable}
                       onChange={(e) => onChange({ start_s: +e.target.value })} />
              </Field>
              <Field label="Each arrival uses (s)"
                     hint={"Every object entering the zone spends this much of "
                           + "the budget. Leaving and coming back counts as a "
                           + "new arrival."}>
                <input className="input" type="number" min="0" step="1"
                       value={d.deduct_s ?? 1} disabled={!editable}
                       onChange={(e) => onChange({ deduct_s: +e.target.value })} />
              </Field>
              <Field label="Start over"
                     hint="Daily refills the budget at local midnight; never keeps spending until detection restarts.">
                <select className="input" value={d.reset || "daily"} disabled={!editable}
                        onChange={(e) => onChange({ reset: e.target.value })}>
                  <option value="daily">every day</option>
                  <option value="never">never</option>
                </select>
              </Field>
            </>
          )}
          {node.type === "analytics.dwell" && d.mode === "occupancy" && (
            <p className="muted small">
              One clock for the zone itself: <code>occupied_s</code> runs while
              anything is inside and stops at 0 when it empties.{" "}
              <code>last_s</code> keeps the previous stretch,{" "}
              <code>total_s</code> the day's accumulation and{" "}
              <code>sessions</code> how many times it went from empty to
              occupied. Put a Condition on <code>occupied_s</code> to catch "occupied too
              long".
            </p>
          )}
          {node.type === "analytics.total" && (
            <>
              <Field label="Accumulate"
                     hint={"Count events: +1 each time the value goes from 0 to "
                           + "above 0, however big it is (1 → total 1, back to 0 "
                           + "and up again → total 2). Increment: adds every "
                           + "increase (1 → total 1, up to 3 → total 3, back down "
                           + "to 1 → still 3, up to 2 → total 4). Drops never "
                           + "subtract."}>
                <select className="input" value={d.mode || "count"} disabled={!editable}
                        onChange={(e) => onChange({ mode: e.target.value })}>
                  <option value="count">count events (+1 each time it turns on)</option>
                  <option value="sum">increment (adds every increase)</option>
                </select>
              </Field>
              <Field label="Ignore blips shorter than (s)"
                     hint={"Detection can blink: a person missed for a frame "
                           + "makes the wired value bounce 1 → 0 → 1, and every "
                           + "re-rise adds again. With a hold time, a change only "
                           + "counts once the value has stayed there this many "
                           + "seconds. 2–3 s is a good start for zone violations. "
                           + "0 = react instantly."}>
                <input className="input" type="number" min="0" max="60" step="0.5"
                       value={d.debounce_s ?? 0} disabled={!editable}
                       onChange={(e) => onChange({ debounce_s: e.target.value })} />
              </Field>
              <Field label="Start over"
                     hint="Daily resets the running total at local midnight; never keeps adding until detection restarts.">
                <select className="input" value={d.reset || "never"} disabled={!editable}
                        onChange={(e) => onChange({ reset: e.target.value })}>
                  <option value="never">never</option>
                  <option value="daily">every day</option>
                </select>
              </Field>
              <Field label="After a restart"
                     hint={"Zero: the total starts again when detection restarts. "
                           + "Last saved total: it carries on from the number it "
                           + "had, saved on this box beside the database."}>
                <select className="input" value={d.resume || "zero"} disabled={!editable}
                        onChange={(e) => onChange({ resume: e.target.value })}>
                  <option value="zero">start from zero</option>
                  <option value="last">continue from the last saved total</option>
                </select>
              </Field>
              <p className="muted small">
                Wire a 0/1 answer (a Compare, a Logic node, a button) into the
                Reset port: each time it goes from 0 to 1 the total starts
                again from zero.
              </p>
            </>
          )}
          {node.type === "analytics.python" && (
            <Field label="Python"
                   hint="Runs once per frame. Return a number, or a dict of numbers. Saving needs a developer account and a box that has opted in.">
              <textarea className="input mono" rows={16} spellCheck={false}
                        value={d.code || ""} disabled={!editable}
                        style={{ fontSize: 11, lineHeight: 1.45,
                                 whiteSpace: "pre", overflowWrap: "normal",
                                 overflowX: "auto" }}
                        onChange={(e) => onChange({ code: e.target.value })} />
            </Field>
          )}
          {(node.type === "analytics.formula" || spec.math) && (
            <FormulaFields spec={spec} d={d} editable={editable}
                           wired={wiredInputs} edges={edges} nodes={nodes}
                           nodeId={node.id} onChange={onChange} />
          )}
          {node.type === "variable.number" && (
            <>
              <Field label="Value type">
                <select className="input" disabled={!editable}
                        value={d.value_type || "number"}
                        onChange={(e) => onChange({
                          value_type: e.target.value,
                          // Re-typed to what the new control edits, so it
                          // does not show "1" in a number box or a checked
                          // box for "night shift". Boolean IS 1.0/0.0
                          // underneath; text is a plain string, never a
                          // number -- neither is a stand-in for the other.
                          value: e.target.value === "boolean" ? !!d.value
                                : e.target.value === "text" ? String(d.value ?? "")
                                : (Number(d.value) || 0),
                        })}>
                  <option value="number">number</option>
                  <option value="boolean">on / off</option>
                  <option value="text">text</option>
                </select>
              </Field>
              {d.value_type === "boolean" ? (
                <Field label="Value">
                  <label className="filter-bar" style={{ alignItems: "center", gap: 8 }}>
                    <input type="checkbox" checked={!!d.value} disabled={!editable}
                           onChange={(e) => onChange({ value: e.target.checked })} />
                    <span className="muted small">{d.value ? "on" : "off"}</span>
                  </label>
                </Field>
              ) : d.value_type === "text" ? (
                <>
                  <Field label="Value">
                    <input className="input" value={d.value ?? ""} disabled={!editable}
                           maxLength={120}
                           onChange={(e) => onChange({ value: e.target.value })} />
                  </Field>
                  <p className="muted small">
                    Wire this into a Data Table column to record it as this
                    fixed text. Formulas and cards read numbers, not text.
                  </p>
                </>
              ) : (
                <Field label="Value">
                  <input className="input" type="number" step="any" value={d.value ?? 0}
                         disabled={!editable}
                         onChange={(e) => onChange({ value: +e.target.value })} />
                </Field>
              )}
            </>
          )}
          {node.type === "variable.schedule" && (
            <>
              <ScheduleFields d={d} editable={editable} onChange={onChange}
                              clock={meta.clock} />
              {/* The "and only then" half: the clock says how often, this
                  says when it counts. It belongs here rather than on each
                  Email and Telegram it feeds -- one window in front of three
                  messages, instead of the same window typed three times and
                  kept in step by hand. */}
              <RunWindow d={d} set={set} disabled={!editable} />
            </>
          )}
          {node.type === "common.inject" && (
            <InjectFields d={d} editable={editable} onChange={onChange}
                          set={set} showFrames={wiredToSnap} />
          )}
          {/* Board Input is a VARIABLE, so its fields belong in this
              section -- parked with the outputs it rendered never, and the
              node looked like it had no setup at all. */}
          {node.type === "variable.board_input" && (
            <BoardFields node={node} d={d} editable={editable}
                         set={set} upMetrics={upMetrics} />
          )}
          {node.type === "variable.rs485" && (
            <>
              {/* WHERE it reads is a Modbus Server, the connection the Write
                  node picks too - Node-RED's Server row. A node saved before
                  servers existed has no `server` key and keeps reading the
                  board's RS485 port at its own baud rate; its row says so,
                  and picking a server moves it over. The field labels are
                  the Write node's and the server's, word for word. */}
              <Field label="Server"
                     hint="The Modbus Server (TCP or RTU) this reads from. Several nodes can share one.">
                <ModbusServerRow value={d.server || ""} editable={editable}
                                 required={"server" in d}
                                 onChange={(v) => onChange({ server: v })}
                                 onEdit={(id) => setServerEdit(id)}
                                 onAdd={() => setServerEdit("")} />
              </Field>
              {!("server" in d) && (
                <p className="muted small">
                  Saved before Modbus Servers: reads the board&apos;s RS485
                  port at the baud rate below. Pick a server to read over TCP
                  or another serial port instead.
                </p>
              )}
              <Field label="FC"
                     hint="From the device's datasheet - which table it publishes.">
                <select className="input" value={d.fc ?? 3} disabled={!editable}
                        onChange={(e) => onChange({ fc: +e.target.value })}>
                  <option value={1}>FC 1: Read Coil Status</option>
                  <option value={2}>FC 2: Read Input Status</option>
                  <option value={3}>FC 3: Read Holding Registers</option>
                  <option value={4}>FC 4: Read Input Registers</option>
                </select>
              </Field>
              <Field label="Unit-Id"
                     hint={"server" in d ? "Blank = the server's Unit-Id." : "The device's slave address, 1-247."}>
                <input className="input" type="number"
                       min={"server" in d ? "0" : "1"}
                       max={"server" in d ? "255" : "247"}
                       value={d.unit ?? ("server" in d ? "" : 1)} disabled={!editable}
                       placeholder={"server" in d ? "server's" : undefined}
                       onChange={(e) => onChange({ unit: e.target.value === "" ? "" : +e.target.value })} />
              </Field>
              <Field label="Address"
                     hint="Raw protocol address: a datasheet's 40001 usually means holding register 0.">
                <input className="input" type="number" min="0" max="65535"
                       value={d.address ?? 0} disabled={!editable}
                       onChange={(e) => onChange({ address: +e.target.value })} />
              </Field>
              <Field label="Quantity"
                     hint="How many registers to read, counting up from the address.">
                <input className="input" type="number" min="1" max="32"
                       value={d.quantity ?? 1} disabled={!editable}
                       onChange={(e) => onChange({ quantity: +e.target.value })} />
              </Field>
              {!("server" in d) && (
                <Field label="Baud rate"
                       hint="Must match the device - a mismatch is silence on the wire, not an error.">
                  <select className="input" value={d.baudrate ?? 9600} disabled={!editable}
                          onChange={(e) => onChange({ baudrate: +e.target.value })}>
                    {[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
                      .map((b) => <option key={b} value={b}>{b}</option>)}
                  </select>
                </Field>
              )}
              {/* 0 = read on a trigger (client UAT item 5): the node grows a
                  Trigger port and reads once each time an Inject, a Schedule
                  or a Condition wired into it fires. Any other value is a
                  clock, floored at 2 s by the engine. */}
              <Field label="How often to read"
                     hint="Seconds. 2s is the floor. 0 = read only when the trigger fires">
                <input className="input" type="number" min="0" max="3600" step="1"
                       value={d.interval_s ?? 5} disabled={!editable}
                       onChange={set("interval_s")} />
              </Field>
              <p className="muted small">
                {Number(d.interval_s ?? 5) === 0
                  ? "Reads once each time the wire into its Trigger port fires - an Inject, a Schedule or a Condition. "
                  : "Reads on its own clock - no camera or trigger needed. "}
                The first
                register is <code>value</code>; a quantity above 1 also
                publishes <code className="mono">r0…r{Math.max(0, (d.quantity ?? 1) - 1)}</code>,
                usable from a Custom Formula or a Data Table by key. Readers and
                writers on one serial port take turns automatically - the bus
                is sequential by nature, wire them anywhere.
              </p>
            </>
          )}
          {node.type === "variable.mqtt" && (
            <>
              {/* The broker, its login and the client settings: a named MQTT
                  Connection, set up once and shared (MqttConnectionEditor).
                  A node from before Connections shows its own fields until
                  the workflow moves them into one (on open). */}
              {"connection" in d ? (
                <Field label="Connection"
                       hint="The broker this listens to (IP address, port, user name and password). Several nodes can share one.">
                  <MqttConnectionRow value={d.connection || ""} editable={editable} required={false}
                                     onChange={(v) => onChange({ connection: v })}
                                     onEdit={(id) => setMqttEdit(id)}
                                     onAdd={() => setMqttEdit("")} />
                {!d.connection && (
                  <div className="muted small" data-mqtt-default="">
                    None picked: this box's own broker (127.0.0.1:1883).
                  </div>
                )}
                </Field>
              ) : (
                <Field label="Broker">
                  <div className="filter-bar">
                    <input className="input" value={d.host || ""} disabled={!editable}
                           placeholder="127.0.0.1" onChange={set("host")} />
                    <input className="input" type="number" style={{ width: 90 }}
                           value={d.port ?? 1883} disabled={!editable}
                           onChange={(e) => onChange({ port: +e.target.value })} />
                  </div>
                </Field>
              )}
              <Field label="Topic"
                     hint="Type the topic to listen to. Wildcards work: line/+/count for one level, line/# for everything under it. '#' may only be last.">
                <input className="input mono" value={d.topic || ""} disabled={!editable}
                       placeholder="plc/line1/parts" onChange={set("topic")} />
              </Field>
              <Field label="Value in the message"
                     hint="Which field in the payload, e.g. data.count. Leave blank if the whole message IS the number.">
                <input className="input mono" value={d.json_path || ""}
                       disabled={!editable} placeholder="data.count"
                       onChange={set("json_path")} />
              </Field>
              {/* No fallback box: the value is whatever the broker last
                  sent, and `ok` already answers "has anything arrived". */}
              {!("connection" in d) && (<>
                <Field label="Username" hint="Leave blank for an open broker.">
                  <input className="input" value={d.username || ""} disabled={!editable}
                         autoComplete="off" onChange={set("username")} />
                </Field>
                <Field label="Password">
                  <PasswordInput native className="input" value={d.password || ""}
                         autoComplete="new-password"
                         disabled={!editable} maxLength={120} onChange={set("password")} />
                </Field>
                <p className="muted small" data-mqtt-legacy="">
                  {d.mqtt_unmigrated
                    ? `This broker could not be moved into a Connection (${d.mqtt_unmigrated}) - fix the address, or add a Connection and pick it.`
                    : "Saved before MQTT Connections: reopen the workflow and these move into a Connection by themselves."}
                </p>
              </>)}
              {/* Is anything arriving on this topic, and does the path pick
                  the right field? Asked of the box, on the saved topic. */}
              <ListenTest node={node} disabled={!editable} />
            </>
          )}
          {node.type === "variable.api" && (
            <>
              <Field label="URL">
                <input className="input" value={d.url || ""} disabled={!editable}
                       placeholder="https://plc.local/api/status"
                       onChange={set("url")} />
              </Field>
              <Field label="How often to read"
                     hint="Seconds. 2s is the floor - anything smaller is clamped to 2s, because each of these is a thread on a box that is also running inference.">
                <input className="input" type="number" min="2" max="3600" step="1"
                       value={d.interval_s ?? 5} disabled={!editable}
                       onChange={set("interval_s")} />
              </Field>
              <Field label="Path to the value"
                     hint="Which value in the answer, e.g. data.count. Leave blank if the answer IS the number. A list becomes its length and true/false becomes 1/0.">
                <input className="input mono" value={d.json_path || ""} disabled={!editable}
                       placeholder="data.count"
                       onChange={set("json_path")} />
              </Field>
              <Field label="Fallback" hint="Used until the first successful read.">
                <input className="input" type="number" step="any" value={d.fallback ?? 0}
                       disabled={!editable}
                       onChange={(e) => onChange({ fallback: +e.target.value })} />
              </Field>
              <HeadersEditor d={d} editable={editable} onChange={onChange} />
              <Field label="Bearer token"
                     hint="Sent as Authorization: Bearer …. Overrides an Authorization header row.">
                <PasswordInput native className="input" value={d.token || ""}
                       autoComplete="off"
                       disabled={!editable} maxLength={500} onChange={set("token")} />
              </Field>
              <p className="muted small">
                Publishes <code className="mono">ok</code> alongside the value, so a
                condition can tell “the reading is zero” from “there is no reading”.
              </p>
            </>
          )}
          {node.type === "analytics.block" && (
            <>
              <button className="btn btn-accent" style={{ marginBottom: 8 }}
                      onClick={onOpenBlueprint}>
                Open blueprint
              </button>
              <p className="muted small">
                Or double-click the node. Build the function from nodes - values,
                math, compare, if/else, switch, range - and wire them into
                Output. Until something is wired in, the variable publishes
                nothing (shows as “-”). Use the trigger below to fire outputs;
                its cooldown is the “delay” between repeats.
              </p>
            </>
          )}
          {/* The limit belongs to the DISTINCT rule ("count an entrant only
              past this occupancy") -- the realtime mode has no such knob, so
              showing it there would be a setting that silently does nothing. */}
          {node.type === "analytics.violations" && d.mode === "distinct" && (
            <>
              <Field label="Count when more than"
                     hint="0 = EVERY object that enters counts once (a keep-out zone). Higher = only objects past that occupancy count (a crowd rule).">
                <div className="filter-bar">
                  <input className="input" type="number" min="0" step="1"
                         style={{ width: 90 }} value={d.limit ?? 1} disabled={!editable}
                         onChange={(e) => onChange({ limit: +e.target.value })} />
                  <span className="muted small">objects in the zone</span>
                </div>
              </Field>
              <p className="muted small">
                Counts by object id: each object counts ONCE however long it stays and
                however many frames pass - <code>total</code> only ever grows,
                the same object never recounts. To raise the alarm, wire an{" "}
                <strong>Condition</strong> node after this (<code>new &gt; 0</code>),
                then Snapshot / Record / MQTT after that.
              </p>
            </>
          )}
          {node.type === "analytics.crowd" && (
            <>
              <Field label="Alert when more than">
                <div className="filter-bar">
                  <input className="input" type="number" min="0" step="1"
                         style={{ width: 90 }} value={d.limit ?? 4} disabled={!editable}
                         onChange={(e) => onChange({ limit: +e.target.value })} />
                  <span className="muted small">objects in the zone</span>
                </div>
              </Field>
              <Field label="Raise as">
                <select className="input" value={d.severity || "warn"} disabled={!editable}
                        onChange={set("severity")}>
                  <option value="warn">alert (warn)</option>
                  <option value="crit">violation (critical)</option>
                </select>
              </Field>
              <Field label="Message (optional)">
                <input className="input" value={d.message || ""} disabled={!editable}
                       placeholder={`more than ${d.limit ?? 4} in zone`}
                       onChange={set("message")} />
              </Field>
              <p className="muted small">
                Fires its own event when the count passes the limit - wire MQTT/GPIO/
                Snapshot/Record after it to act on it. Cooldown {d.cooldown_s ?? 30}s.
              </p>
            </>
          )}
          {node.type === "analytics.proximity" && (
            <>
              <div className="filter-bar">
                <Field label="Class A">
                  <select className="input" value={d.class_a || ""} disabled={!editable}
                          onChange={set("class_a")}>
                    <option value="">- any class -</option>
                    {(vars.classes || []).map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </Field>
                <Field label="Class B">
                  <select className="input" value={d.class_b || ""} disabled={!editable}
                          onChange={set("class_b")}>
                    <option value="">- any class -</option>
                    {(vars.classes || []).map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </Field>
              </div>
              {(vars.models || []).length >= 2 && (
                <div className="filter-bar">
                  <Field label="Model A">
                    <select className="input" value={d.model_a || ""} disabled={!editable}
                            onChange={set("model_a")}>
                      <option value="">any model</option>
                      {(vars.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </Field>
                  <Field label="Model B">
                    <select className="input" value={d.model_b || ""} disabled={!editable}
                            onChange={set("model_b")}>
                      <option value="">any model</option>
                      {(vars.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </Field>
                </div>
              )}
              <Field label="Too close when nearer than">
                <div className="filter-bar">
                  <input type="range" min="0.02" max="0.8" step="0.01"
                         value={d.max_distance ?? 0.15} disabled={!editable}
                         style={{ flex: 1, minWidth: 0 }}
                         onChange={(e) => onChange({ max_distance: +e.target.value })} />
                  <span className="muted small" style={{ whiteSpace: "nowrap" }}>
                    {Math.round((d.max_distance ?? 0.15) * 100)}% of frame width
                  </span>
                </div>
              </Field>
              <Field label="Draw the link line">
                <select className="input" value={d.draw_link || "always"} disabled={!editable}
                        onChange={set("draw_link")}>
                  <option value="always">always</option>
                  <option value="alert">only while alerting</option>
                  <option value="off">never</option>
                </select>
              </Field>
              <p className="muted small">
                Measured between box centres as a fraction of the frame width, so
                it does not change when the camera resolution does. Same class on
                both sides measures nearest neighbours of that class. To raise
                an alarm, wire a <strong>Condition</strong> or <strong>Compare</strong>{" "}
                node after this - <code>active</code> is 1 while a pair is too close.
              </p>
            </>
          )}

          {["analytics.object", "analytics.object_frame",
            "analytics.object_zone"].includes(node.type) && (
            <>
              {/* WHAT to watch for comes from Class Filters wired in front,
                  the same way every other rule names its objects -- and more
                  than one may feed a watch, their classes adding together. */}
              <p className="muted small" style={{ marginTop: 6 }}>
                Wire one or more <strong>Class Filter</strong> nodes in to say
                WHAT to watch for - several add together, so “a person or a
                forklift” is two filters into this one node.
                {node.type === "analytics.object_frame"
                  ? " It watches the whole camera view."
                  : " A Zone wired in says WHERE - an area means inside it, a line means AT it (a narrow band around the line). With none wired it watches the whole camera view."}
              </p>
              {!!(d.classes || []).length && (
                <p className="muted small">
                  This node also carries its own list ({(d.classes || []).join(", ")}),
                  kept from an earlier version - it is added to whatever the
                  filters name.
                </p>
              )}
              <p className="muted small">
                <code>found</code> is 1 while one of those objects is there and
                0 when none is - wire it straight to a <strong>Condition</strong>,
                a <strong>Snapshot</strong> or a <strong>Data Table</strong>;
                no comparison needed.
              </p>
            </>
          )}

          {node.type === "analytics.guard" && (
            <>
              <ClassPicker
                label={"Bubble around (" + ((d.classes || []).length
                  ? (d.classes || []).length + " chosen" : "none = nothing") + ")"}
                classes={vars.classes || []} active={vars.classes_active}
                value={d.classes || []}
                editable={editable}
                onChange={(classes) => onChange({ classes })} />
              <Field label="Bubble size">
                <div className="filter-bar">
                  <input type="range" min="5" max="200" step="5"
                         value={d.margin_pct ?? 50} disabled={!editable}
                         style={{ flex: 1, minWidth: 0 }}
                         onChange={(e) => onChange({ margin_pct: +e.target.value })} />
                  <span className="muted small" style={{ whiteSpace: "nowrap" }}>
                    {d.margin_pct ?? 50}% of the object's box
                  </span>
                </div>
              </Field>
              <ClassPicker
                label={"Intruders: count these entering it (" + ((d.alert_classes || []).length
                  ? (d.alert_classes || []).length + " chosen" : "none chosen") + ")"}
                classes={vars.classes || []} active={vars.classes_active}
                value={d.alert_classes || []}
                editable={editable}
                onChange={(alert_classes) => onChange({ alert_classes })} />
              <Field label="Counts as an intrusion when">
                <select className="input" value={d.touch || "center"} disabled={!editable}
                        onChange={set("touch")}>
                  <option value="center">the intruder's centre is inside the bubble</option>
                  <option value="overlap">the two bubbles touch / overlap</option>
                </select>
              </Field>
              {(d.touch || "center") === "overlap" && (
                <Field label="How far in before it counts">
                  <div className="filter-bar">
                    <input type="range" min="0" max="100" step="5"
                           value={d.min_overlap_pct ?? 0} disabled={!editable}
                           style={{ flex: 1, minWidth: 0 }}
                           onChange={(e) => onChange({ min_overlap_pct: +e.target.value })} />
                    <span className="muted small" style={{ whiteSpace: "nowrap" }}>
                      {(d.min_overlap_pct ?? 0) === 0
                        ? "any touch"
                        : `${d.min_overlap_pct}% overlap`}
                    </span>
                  </div>
                </Field>
              )}
              <label className="muted small"
                     style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
                <input type="checkbox" checked={d.draw !== false} disabled={!editable}
                       onChange={(e) => onChange({ draw: e.target.checked })} />
                draw the bubble on the feed
              </label>
              <p className="muted small">
                The bubble follows each object - no zone to draw. It is that
                object's own box grown by the percentage above. To raise an
                alarm, wire a <strong>Condition</strong> or <strong>Compare</strong>{" "}
                node after this - <code>active</code> is 1 while an intruder is
                inside, and <code>overlap_pct</code> is how deep the worst
                encroachment is right now.
              </p>
            </>
          )}

          {node.type === "analytics.trails" && (
            <>
              <ClassPicker
                label={"Trail behind (" + ((d.classes || []).length
                  ? (d.classes || []).length + " chosen" : "none = every class") + ")"}
                classes={vars.classes || []} active={vars.classes_active}
                value={d.classes || []}
                editable={editable}
                onChange={(classes) => onChange({ classes })} />
              <Field label="Trail length">
                <div className="filter-bar">
                  <input type="range" min="2" max="60" step="1"
                         value={d.length_s ?? 10} disabled={!editable}
                         style={{ flex: 1, minWidth: 0 }}
                         onChange={(e) => onChange({ length_s: +e.target.value })} />
                  <span className="muted small" style={{ whiteSpace: "nowrap" }}>
                    {d.length_s ?? 10}s of movement
                  </span>
                </div>
              </Field>
              <Field label="Counts as idle after"
                     hint="An object with no stored movement for this long counts in `idle` - “the forklift stopped” as a number a Condition or a Zone Switch can act on.">
                <div className="filter-bar">
                  <input type="range" min="1" max="60" step="1"
                         value={d.idle_s ?? 5} disabled={!editable}
                         style={{ flex: 1, minWidth: 0 }}
                         onChange={(e) => onChange({ idle_s: +e.target.value })} />
                  <span className="muted small" style={{ whiteSpace: "nowrap" }}>
                    {d.idle_s ?? 5}s still
                  </span>
                </div>
              </Field>
              <label className="muted small"
                     style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
                <input type="checkbox" checked={d.draw !== false} disabled={!editable}
                       onChange={(e) => onChange({ draw: e.target.checked })} />
                draw the dotted trail on the feed
              </label>
              <p className="muted small">
                Each tracked object leaves a dotted line where it has been -
                a dot is stored only when the object MOVES, so a parked object
                is a dot and a walking person is a line. The trail is time,
                not distance: it keeps the seconds above and fades out.
                <code> tracked</code> is how many objects are leaving a trail;
                <code> active</code> is 1 while any of them is moving - wire
                a <strong>Condition</strong> after it for “something moved”.
              </p>
            </>
          )}

          {/* The trigger checkbox that used to sit here is gone. A rule
              measures; the Alert node decides. Six node types already
              excluded themselves from it on exactly that reasoning ("a
              Formula is a calculation, not an alarm; a built-in trigger
              would be a second place alerts can hide") -- which was the
              whole argument against having it anywhere. Wire an Alert after
              the rule instead, and the reason it fires is on the canvas
              rather than folded inside a panel. Graphs that carried one are
              migrated to exactly that (migrate_inline_triggers). */}
          {/* The violation timer, where it is found: an Anomaly or an Object
              Detection counts a find only after it has stayed this long. */}
          {(node.type === "analytics.anomaly" || node.type === "analytics.object") && (
            <ViolationTimer d={d} editable={editable} onChange={onChange}
                            anomaly={node.type === "analytics.anomaly"} />
          )}
          {/* The plumbing, folded shut: the published key (read-only -- it is
              minted once and renaming it is what orphans history), which
              pages offer this node's numbers as widgets, and the optional OID
              output. None of it changes what the node MEASURES, so none of
              it belongs between the operator and the settings that do. */}
          {!HIDES_KEY_CHROME.has(node.type) && !spec.math
            // Nothing inside for a node that neither times its count nor
            // offers an OID: then the fold is not drawn at all.
            && (COUNT_TIMING.has(node.type) || (COUNTS_BY_PID.has(node.type)
              && !(node.type === "analytics.dwell"
                   && ["countdown", "occupancy"].includes(d.mode)))) && (
            <AdvancedSection>
              {/* The published key is GONE from here, on every node.
                  It is minted automatically, it is read-only, and renaming it
                  is impossible -- so it was a disabled box that could only be
                  looked at, repeated on every node in the product, at the top
                  of the one section an operator opens to change something.
                  The places that genuinely need the string offer it where it
                  is used: the widget key picker lists it, and a Variable that
                  stores a value says what it publishes. */}
              {/* WHEN an object counts. Real-time counting is what a counter
                  has always done and stays the default; the timer is for the
                  two questions it cannot answer -- "only if it STAYS" and
                  "do not let a dropped frame change the number". */}
              {COUNT_TIMING.has(node.type) && (
                <CountTiming d={d} editable={editable} onChange={onChange} />
              )}
              {/* The counters know WHICH object they counted, not just how
                  many. The SWITCH is on the title row (it adds an output, and
                  a control that changes the node's ports should not be behind
                  a fold); what it means stays here, beside the other wiring. */}
              {/* Countdown and Occupancy measure the PLACE, not objects --
                  there is no per-object stay to stamp, so no OID to offer. */}
              {COUNTS_BY_PID.has(node.type)
                && !(node.type === "analytics.dwell"
                     && ["countdown", "occupancy"].includes(d.mode)) && (
                <>
                  {!d.OID && (
                    <p className="muted small">
                      <strong>OID</strong> (top right){" "}
                      {node.type === "analytics.dwell"
                        ? "also records each completed stay, with the object's id."
                        : "also gives out the object id of each counted object."}
                    </p>
                  )}
                  {/* TOLERANCE: time past a limit that still counts as fine.
                      An If / Else if on this clock's time fires only past the
                      limit plus this, and the photo reports both. */}
                  {node.type === "analytics.dwell"
                    && !["countdown", "occupancy"].includes(d.mode) && (
                    <Field label="Tolerance (seconds)"
                           hint={"Time past the limit that is still allowed. A test "
                                 + "on this time (If / Else if) raises the violation "
                                 + "only after the limit plus this. 0 = none."}>
                      <input className="input" type="number" min="0" max="3600"
                             step="0.5" style={{ width: 90 }} data-tolerance
                             value={d.tolerance_s ?? 0} disabled={!editable}
                             onChange={(e) => onChange({
                               tolerance_s: Math.max(0, Math.min(3600, +e.target.value || 0)) })} />
                    </Field>
                  )}
                  {/* HOW the per-object clock arrives. Two different things
                      were two MODES before ("OID Timer", "OID Measure Time"),
                      which made one measurement look like three. Live is the
                      default: the running clock is what a table column, a
                      card or a threshold usually wants, and a record of
                      completed stays is the deliberate choice. */}
                  {d.OID && node.type === "analytics.dwell" && (
                    <Field label="Per-object time arrives"
                           hint={"Live hands out the clock of the stay running "
                                 + "now. Once records each COMPLETED stay as an "
                                 + "event carrying the id, the seconds and the "
                                 + "entry and exit times."}>
                      <select className="input" value={d.delivery || "live"}
                              disabled={!editable} onChange={set("delivery")}>
                        <option value="live">Live, continuously</option>
                        <option value="once">Once, when they leave</option>
                      </select>
                    </Field>
                  )}
                  {d.OID && node.type === "analytics.dwell"
                    && (d.delivery || "live") === "once" && (
                    <p className="muted small">
                      Every time an object leaves the zone, its stay is recorded
                      with its <strong>OID</strong>, the seconds it stayed
                      (<code>duration_s</code>) and when it entered and left.
                      Wire the <strong>OID</strong> output to a Data Table to
                      save cycle time per object - event columns offer{" "}
                      <code>duration_s</code>, <code>entered_at</code> and{" "}
                      <code>exited_at</code>. The <strong>in zone (s)</strong>{" "}
                      output is the live clock - how long each object has been
                      inside <em>right now</em>, for a number column. Add the
                      columns on the <Link to="/database">Database</Link> page
                      first.
                    </p>
                  )}
                  {d.OID && node.type !== "analytics.dwell" && (
                    <p className="muted small">
                      Adds a <strong>OID</strong> output. Wire it to a Data Table
                      column to record which object each row was about - add a
                      column for it on the{" "}
                      <Link to="/database">Database</Link> page first.
                      {node.type === "analytics.object" && (
                        <> One row is written each time a watched object
                        ARRIVES; a <code>detection_label</code> event column
                        records which class it was.</>
                      )}
                      {(["analytics.violations", "analytics.occupancy"]
                        .includes(node.type)
                        // The Counter has an "inside" only while a zone is
                        // wired; on the whole view the port is not drawn.
                        || counterMode(node, edges) === "zone") && (
                        <> A zone rule also adds <strong>in zone (s)</strong> -
                        the live clock of how long each object has been inside,
                        for a number column.</>
                      )}
                    </p>
                  )}
                </>
              )}
            </AdvancedSection>
          )}
        </>
      )}

      {node.type === "logic.catch" && (
        <>
          <p className="muted small">
            Fires when the code node wired into this one throws. Wire a Condition
            or an output after it.
          </p>
          <AlertBits d={d} editable={editable} set={set} defaultCooldown={60}
                     placeholder="e.g. the counting script stopped working" />
        </>
      )}

      {isBranch(node.type) && (<>
        {/* No Name field: a branch is named after the shape you dragged and
            renamed by double-clicking its header, like every other node. It
            was the last inspector asking for a name it could work out
            itself, and two Switches sharing one still compiled apart
            (switch_bases gives the second its own stem). */}
        {(() => {
          const mine = String(d.name || "").trim().toLowerCase();
          const twin = mine && (nodes || []).some(
            (n) => n.id !== node.id && isBranch(n.type)
              && String((n.data || {}).name || "").trim().toLowerCase() === mine);
          return twin ? (
            <p className="small c-warn">
              Another branch is called “{d.name}” too - they still compile
              apart, but rename one (double-click its header) or their ways
              out are impossible to tell apart in a card picker.
            </p>
          ) : null;
        })()}
        <SwitchFields d={d} editable={editable} metrics={upMetrics}
                      edges={edges} nodeId={node.id} nodeType={node.type}
                      onChange={onChange} />
      </>)}

      {/* Jump wires: two nodes sharing a name stand for one connection. */}
      {(node.type === "common.link_out" || node.type === "common.link_in") && (
        <Field label="Jump name"
               hint="Every Link out sends to every Link in with the same name. Unmatched names simply carry nothing.">
          <input className="input mono" value={d.link || ""} disabled={!editable}
                 placeholder="e.g. to_alerts"
                 onChange={(e) => onChange({
                   link: e.target.value.toLowerCase()
                     .replace(/[^a-z0-9_]+/g, "_").slice(0, 30),
                 })} />
        </Field>
      )}

      {/* A function: the Start names it and declares what it takes, the End
          and the Call name the one they mean. The body is whatever is wired
          between the two brackets on the canvas -- reachability, not
          position, so moving a node never changes what a function does. */}
      {node.type === "function.frame" && (
        <FunctionFrameInspector node={node} nodes={nodes} editable={editable}
                                onChange={onChange} onFnData={onFnData} />
      )}
      {node.type === "function.call" && (
        <Field label="Function"
               hint="Which function this Call runs. Every function on this canvas is listed; type to search. Every Call runs its own copy of the function.">
          <FunctionPicker value={d.fn || ""} disabled={!editable}
                          fnDefs={Object.fromEntries(nodes
                            .filter((n) => n.type === "function.start" && String(n.data?.fn || "").trim())
                            .map((n) => {
                              const k = String(n.data.fn).trim().toLowerCase();
                              const end = nodes.find((m) => m.type === "function.end"
                                && String(m.data?.fn || "").trim().toLowerCase() === k);
                              return [k, { name: String(n.data.fn).trim(),
                                           params: n.data?.params || [],
                                           returns: end?.data?.returns || [] }];
                            }))}
                          onPick={(fn) => onChange({ fn })} />
        </Field>
      )}
      {FUNCTION_NODES.includes(node.type) && node.type !== "function.call" && (<>
        <Field label="Function name"
               hint={node.type === "function.start"
                 ? "The Function End with this name closes the bracket. Everything wired between the two is the body."
                 : "Which function to run. It must be the name on a Function Start somewhere on this canvas."}>
          <input className="input mono" value={d.fn || ""} disabled={!editable}
                 placeholder="e.g. rate"
                 onChange={(e) => onChange({
                   fn: e.target.value.toLowerCase()
                     .replace(/[^a-z0-9_]+/g, "_").slice(0, 30),
                 })} />
        </Field>
        {node.type === "function.end" && (
          <Field label="Returns"
                 hint="One port per value the function gives back, on this node and on every Call. Leave it empty for a function that returns a single thing.">
            <ParamList value={d.returns || []} disabled={!editable}
                       onChange={(returns) => onChange({ returns })} />
          </Field>
        )}
        {node.type === "function.start" && (
          <Field label="Parameters"
                 hint="One port per name, on this node and on every Call. Leave it empty for a function that simply runs - the plain port is still the way in.">
            <ParamList value={d.params || []} disabled={!editable}
                       onChange={(params) => onChange({ params })} />
          </Field>
        )}
      </>)}

      {node.type === "common.debug" && (
        // The field is a FALLBACK now, not the way in. The node reads its own
        // wire, which is the question somebody drawing one has; this box is
        // kept because probes saved before that worked only from a typed key,
        // and guessing which wire one of those meant is not a migration
        // anybody could check.
        <Field label="Watching"
               hint="Reads whatever is wired in. Type a stat key here only to watch a value with no wire to it. A probe only - it publishes nothing and changes nothing.">
          <input className="input mono" value={d.metric || ""} disabled={!editable}
                 placeholder="a stat key, when nothing is wired in"
                 onChange={set("metric")} />
          <DebugHistory nodeId={node.id} />
        </Field>
      )}

      {/* A Delay holds what comes after it. No condition, no value, no name:
          one number is the whole node. */}
      {node.type === "logic.delay" && (
        <Field label="Wait for"
               hint="The outputs wired after this one fire this many seconds later. Two delays in a row add up.">
          <div className="filter-bar">
            <input className="input" type="number" min="0" max="3600" step="0.5"
                   style={{ width: 110 }} value={d.delay_s ?? 5}
                   disabled={!editable}
                   onChange={(e) => onChange({ delay_s: +e.target.value })} />
            <span className="muted small">seconds</span>
          </div>
        </Field>
      )}

      {/* A When node is a condition on somebody else's value: no name, no
          variable of its own, nothing to publish. Just "when does this fire". */}
      {/* The Compare node's own editor. NOT for the Switch: its rows above
          are the whole condition, and rendering this block under them put a
          second, unrelated "Fires when the value..." form on the same panel
          -- nobody could tell which of the two the node obeyed. */}
      {spec.category === "logic" && !isBranch(node.type)
        && !["logic.delay", "logic.catch"].includes(node.type) && (
        <>
          <Field label="Which value">
            {upMetrics.length > 0 ? (
              <select className="input" value={d.metric || ""} disabled={!editable}
                      onChange={set("metric")}>
                <option value="">primary metric</option>
                {upMetrics.map((m) => <option key={m} value={m}>{metricLabel(m)}</option>)}
              </select>
            ) : (
              <input className="input" value={d.metric || ""} disabled={!editable}
                     placeholder="primary metric" onChange={set("metric")} />
            )}
          </Field>
          <Field label="Fires when the value…">
            <select className="input" value={d.mode || "above"} disabled={!editable}
                    onChange={(e) => {
                      const mode = e.target.value;
                      // A range needs two DIFFERENT bounds. Leaving value2 at
                      // its 0 default made "in 1..0" mean 0..1 -- which is true
                      // for a 0/1 metric always, so the alert never stopped.
                      const fix = WHEN_NEEDS_VALUE2.includes(mode)
                        && (d.value2 ?? 0) <= (d.value ?? 0)
                        ? { value2: (d.value ?? 0) + 1 } : {};
                      onChange({ mode, ...fix });
                    }}>
              {WHEN_MODES.map((m) => (
                <option key={m.key} value={m.key}>{m.label}</option>
              ))}
            </select>
          </Field>
          {WHEN_NEEDS_VALUE2.includes(d.mode || "above")
            && (d.value2 ?? 0) <= (d.value ?? 0) && (
            <p className="small c-warn">
              “To” must be above “From”, or the range covers nothing useful.
            </p>
          )}
          {WHEN_NEEDS_VALUE.includes(d.mode || "above") && (
            <Field label={WHEN_NEEDS_VALUE2.includes(d.mode || "above") ? "From" : "Value"}>
              <input className="input" type="number" step="any" value={d.value ?? 0}
                     disabled={!editable} onChange={set("value")} />
            </Field>
          )}
          {WHEN_NEEDS_VALUE2.includes(d.mode || "above") && (
            <Field label="To">
              <input className="input" type="number" step="any" value={d.value2 ?? 0}
                     disabled={!editable} onChange={set("value2")} />
            </Field>
          )}
          <Field label="Must hold for (s)">
            <input className="input" type="number" min="0" step="0.5"
                   value={d.hold_s ?? 0} disabled={!editable}
                   onChange={set("hold_s")} />
          </Field>
          <p className="muted small">
            0 = fire the moment it is true. Anything else ignores a condition
            that flickers and fires only once it has held that long.
          </p>
          <AlertBits d={d} editable={editable} set={set}
                     placeholder="condition met" defaultCooldown={10} />
          <p className="muted small">
            Wire a value in on the left and what should react on the right -
            MQTT, GPIO, Snapshot, a dashboard card. Outputs after this node fire
            on THIS condition instead of the function's own trigger, and several
            When nodes on one value are several independent triggers.
          </p>
        </>
      )}

      {/* A NEW CHART chooses its type first (ChartTypePicker, the same one
          the Builders show); "Change chart type" comes back here. Nothing is
          written until Continue. */}
      {spec.category === "dashboard" && pickingType && (
        <ChartTypePicker key={node.id} value={isFreshChart(node.id) ? "" : chartTypeId}
                         kind={chartRead.kind} model={chartRead.model}
                         fresh={isFreshChart(node.id)} disabled={!editable}
                         onContinue={applyChartType}
                         onCancel={isFreshChart(node.id) ? null
                           : () => setTypePickFor(null)} />
      )}
      {spec.category === "dashboard" && !pickingType && (
        <>
          {node.type === "dashboard.graph" && (
            <ChartTypeStrip typeId={chartTypeId} disabled={!editable}
                            onChange={editable ? () => setTypePickFor(node.id) : null} />
          )}
          {/* Untitled, a card is named after the function feeding it - so one
              function feeding three cards produced three cards with the same
              name. Titling one also lists it in the Dashboard and Analytics
              editors, where it can be dropped onto another page as composed. */}
          <Field label={`${spec.label || "Card Widget"} Name`}
                 hint="The title shown on the Dashboard and Analytics. Also lists this card in the editors' “From workflow” palette.">
            <input className="input" value={d.name || ""} disabled={!editable}
                   onChange={set("name")}
                   placeholder={upstreamFn?.data?.name || "Line 2 rejects"} />
          </Field>
          {/* Alignment and Position are how the card LOOKS: set in the
              Builders' Appearance only (Tok Aba, 2026-10-14), still stored
              on this node and compiled onto the card. */}
          {/* The caption is identity, like the title: the small line under a
              tile's value, edited here and in the Builders' Card section,
              stored on this node so the two cannot disagree. */}
          {"sub" in (spec.data || {}) && cardKind === "tile" && (
            <Field label="Caption"
                   hint="The small line under the value. Blank shows nothing.">
              <input className="input" value={d.sub || ""} disabled={!editable}
                     onChange={set("sub")} placeholder="since 6 am" />
            </Field>
          )}
          {/* "Show as" under the title and caption, where the Builders' panel
              has it: the SHAPE is data, not a node type per shape, and its
              names come from the same list (cardFields.jsx). A Card offers
              only the shapes its source can draw: a verdict is a live STATE,
              and the event store answers a count, a chart or rows. */}
          {node.type === "dashboard.card" && (
            <ShapeField field={Field} value={d.kind || "tile"} disabled={!editable}
                        // Picking "status" on a card NOT fed by an alert
                        // starts it with named states, not COMPLIANT /
                        // VIOLATION -- a count of people is not a breach.
                        onChange={(v) => onChange(v === "verdict" && !cardFromAlert
                          && !(Array.isArray(d.states) && d.states.length)
                          ? { kind: v, states: DEFAULT_STATES } : { kind: v })}
                        extra={savedShapeOff ? [[d.kind, `${d.kind} - as saved`]] : []}
                        shapes={CARD_SHAPES.filter(([k]) => (
                          k === "tile"
                          || (k === "gauge" && sourceMode !== "events")
                          || (["verdict", "status"].includes(k) && !sourceMode)
                          || (k === "table" && !!sourceMode)))} />
          )}
          {/* A Chart's "Show as" offers its own family only (a switch that
              keeps every field); another family is "Change chart type". */}
          {node.type === "dashboard.graph" && (
            <ChartShowAs field={Field} typeId={chartTypeId} kind={d.kind || "area"}
                         disabled={!editable} onType={applyChartType}
                         onKind={(k) => onChange({ kind: k })} />
          )}
          {/* THE SOURCE. A wired column, table or Query decides for itself
              (and wins at compile time), so the choice is only offered where
              the wire leaves it open: nothing wired, or a rule wired in --
              whose value this can show, or whose raised events it can
              count. */}
          {!readsTable && !readsColumn && !readsQuery
            && ["dashboard.card", "dashboard.graph"].includes(node.type) && (
            <Field label="Source"
                   hint={upstreamValue
                     ? "The wired rule's live value, the events it raised, or a table it records."
                     : "Recorded events need no wire: unwired, every matching event counts. A data table is named here, not wired."}>
              <select className="input" value={sourceMode} disabled={!editable}
                      onChange={(e) => {
                        const v = e.target.value;
                        const patch = { source_mode: v };
                        // A chart's live range ("24h") is not a period the
                        // recorded stores know; "all time" is their default.
                        if (v && !["", "page", "today", "week", "month"]
                          .includes(d.range || "")) patch.range = "";
                        // Only the shapes the new source can draw: a verdict
                        // is a live STATE, and the event store answers a
                        // count, a chart or rows.
                        if (node.type === "dashboard.card") {
                          const ok = v === "events" ? ["tile", "table"]
                            : v === "dataset" ? ["tile", "gauge", "table"]
                              : ["tile", "gauge", "verdict", "status"];
                          if (!ok.includes(d.kind || "tile")) patch.kind = "tile";
                        }
                        onChange(patch);
                      }}>
                <option value="">
                  {upstreamValue ? "the value it publishes"
                                 : "what is wired in - a rule, a column or a Query"}
                </option>
                <option value="events">
                  {upstreamValue ? "the events it raised" : "recorded events"}
                </option>
                {(canvasDatasets.length > 0 || sourceMode === "dataset") && (
                  <option value="dataset">a data table</option>
                )}
              </select>
            </Field>
          )}
          {!upstreamFn && !readsColumn && !readsQuery && !readsTable && !sourceMode && (
            <p className="small c-warn">
              Wire an Analytics node - a Data Table column, or a Query - into
              this card first, or set its Source to recorded events.</p>
          )}
          {readsQuery && (
            <p className="small" style={{ color: "var(--muted-text, #6b6b6b)" }}>
              Shows the result of the Query wired into it. The statement decides
              the numbers; this card decides how they are drawn - a tile takes
              the first number, a chart takes the first column as labels and the
              next number column as values, and a table card shows every column.
            </p>
          )}
          {readsTable && (
            <TableColumns d={d} table={readsTable} editable={editable}
                          onChange={onChange} set={set} />
          )}
          {/* One wire, one question: ColumnLogic -- on a CARD. A Chart is
              drawn by the chart builder below whatever feeds it. */}
          {!readsTable && readsColumn && !graphSeries && node.type !== "dashboard.graph" && (
            <ColumnLogic d={d} column={readsColumn} editable={editable}
                         onChange={onChange} set={set} kind={cardKind} />
          )}
          {sourceMode && (
            <RecordedQuery mode={sourceMode} d={d} vars={vars}
                           editable={editable} set={set} onChange={onChange}
                           // A Card has no axis -- unless it feeds a Chart,
                           // or already carries a grouping that still
                           // compiles and must be visible to be cleared. A
                           // Chart's axes are the chart builder's (below).
                           chart={node.type !== "dashboard.graph"
                                  && (feedsChart || !!d.group_by || !!d.bucket)}
                           chartBuilder={node.type === "dashboard.graph"}
                           oneRange={node.type !== "dashboard.graph"}
                           canvasDatasets={canvasDatasets}
                           chosenDataset={chosenDataset}
                           upstreamValue={upstreamValue} />
          )}
          {/* THE CHART BUILDER: one X axis, Y axis, interval and date range
              for the whole chart, then a short row per input -- under the
              SHAPE, because the shape decides which sections there are.
              The thresholds sit in its collapsed Advanced section. */}
          {chartBuilder && (
            <GraphSeriesRows node={node} nodes={nodes} edges={edges}
                             registry={registry} d={d} dbTables={dbTables}
                             editable={editable} onChange={onChange}
                             single={graphSingle} recorded={graphRecorded}
                             advanced={(
                               <CardBands kind={d.kind || "area"} d={d}
                                          editable={editable} onChange={onChange} />
                             )} />
          )}
          {/* `cardKind` defaults to "tile", which is wrong for a Chart node
              that has not had its shape picked yet - it would offer the tile
              treatments for something that draws an area chart. */}
          {!chartBuilder && (
            <CardBands
              kind={node.type === "dashboard.graph" ? (d.kind || "area") : cardKind}
              d={d} editable={editable} onChange={onChange} />
          )}
          {upMetrics.length > 0 && !sourceMode && (
            <Field label="Metric"
                   hint={cardKind === "verdict"
                     ? "active = 1 while the function's alert condition holds - the usual driver for a verdict."
                     : undefined}>
              <select className="input" disabled={!editable}
                      value={d.metric || (cardKind === "verdict"
                        ? "active" : upMetrics[0])}
                      onChange={set("metric")}>
                {(cardKind === "verdict"
                  ? [...upMetrics, "active"] : upMetrics)
                  .map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>
          )}
          {/* THE CARD'S ONE DATE RANGE (cardFields.CardDateRange), the same
              control the Builders draw. It used to be three: a Period beside
              the column, "Count over" and a second Period at the foot of the
              panel (owner, 2026-10-13: "many duplicate or redundant, please
              make it into one"). It writes the field this card's SOURCE
              reads -- `window` for a live rule, `range` for recorded rows --
              and a dead value the old panel left in the other one is shown
              as saved under Advanced, never dropped. */}
          {node.type !== "dashboard.graph" && cardSource
            && ("range" in (spec.data || {}) || "window" in (spec.data || {})) && (() => {
            const r = cardRangeOf(d, cardSource);
            return (
              <CardDateRange field={Field} source={cardSource} value={r.value}
                             from={r.from} to={r.to} note={r.note} disabled={!editable}
                             onChange={onChange} />
            );
          })()}
          {cardKind === "tile" && !sourceMode && (
            <Field label="Show the value as"
                   hint="Words fit a 0-or-1 state (an object watch's `found`); the number fits a count. Text is for a value that IS a word - a Set Variable holding a class name - and prints it as it stands instead of testing it against zero.">
              <select className="input" value={d.format || ""} disabled={!editable}
                      onChange={set("format")}>
                <option value="">the number itself</option>
                <option value="text">the text itself</option>
                <option value="yesno">YES / NO</option>
                <option value="onoff">ON / OFF</option>
                <option value="truefalse">TRUE / FALSE</option>
              </select>
            </Field>
          )}
          {/* A status pill's rule, the same StatusFields the Builders draw:
              the compile builds the pill from these (it used to carry no
              rule at all, so every compiled pill said ">= 1 / OK / ALERT"). */}
          {node.type === "dashboard.card" && cardKind === "status" && !sourceMode && (
            <StatusFields field={Field} op={d.op} value={d.value}
                          okLabel={d.ok_label} alertLabel={d.alert_label}
                          disabled={!editable} onChange={onChange} />
          )}
          {cardKind === "verdict" && !sourceMode && (
            <VerdictStates d={d} editable={editable} set={set}
                           onChange={onChange} wiredNames={verdictWires}
                           fromAlert={cardFromAlert} />
          )}
          {/* THE CARD'S LOOK IS NOT SET HERE. Colour, card colour, animation
              and the text sizes all used to be on this node as well as in the
              dashboard editors, and two panels offering one setting is a
              question ("which one wins?") whose answer -- whichever was saved
              last -- nobody can see. A card is dressed where it is looked at,
              on Analytics or the Dashboard builder, and that choice is written
              back onto this node (/api/workflow/card-style) so it survives
              every recompile. This panel stays about what the card SHOWS --
              and the save route holds it to that: whatever look this graph
              carries up, the stored node's is kept (workflow.keep_card_look),
              so a stale tab cannot put an old look back either. */}
          {/* On the shapes that print one, whatever feeds the card: the
              compile carries it onto every source's widget now, and the
              Builders offer it on the same shapes (cardFields.UNIT_KINDS). */}
          {"unit" in (spec.data || {})
            && UNIT_KINDS.includes(node.type === "dashboard.graph"
              ? (d.kind || "area") : cardKind) && (
            <UnitField field={Field} value={d.unit} disabled={!editable}
                       onChange={(v) => onChange({ unit: v })} />
          )}
          <p className="muted small">
            Appears on the Analytics <strong>Overview</strong> after save - the
            one page Analytics shows. Move and resize it there; recompiling
            this graph keeps where you put it.
          </p>
        </>
      )}

      {node.type === "integration.photos" && (<>
        <Field label="Camera"
               hint="Blank sends whatever any camera saved in the window.">
          <select className="input" value={d.camera || ""} disabled={!editable}
                  onChange={set("camera")}>
            <option value="">Any camera</option>
            {(vars.cameras || []).map((c) => (
              <option key={c.id} value={c.id}>{camLabel(vars.cameras, c.id)}</option>
            ))}
          </select>
        </Field>
        {/* How many, or all of them. "All" is 0 rather than a second
            setting, because 0 is the one number "how many" cannot otherwise
            be -- and a checkbox beside a number box is two controls that can
            disagree. */}
        <Field label="How many to send"
               hint="Telegram carries ten in one album, so more than ten arrives as several albums. All means every one in the window, up to 60.">
          <div className="rail-line">
            <label className="wf-check" style={{ margin: 0 }}>
              <input type="checkbox" checked={Number(d.most ?? 10) <= 0}
                     disabled={!editable}
                     onChange={(e) => onChange({ most: e.target.checked ? 0 : 10 })} />
              <span className="small">All of them</span>
            </label>
            {Number(d.most ?? 10) > 0 && (
              <input className="input" type="number" min={1} max={60}
                     value={d.most ?? 10} disabled={!editable}
                     style={{ width: 90 }}
                     onChange={(e) => onChange({
                       most: Math.max(1, Math.min(60, Number(e.target.value) || 10)) })} />
            )}
          </div>
        </Field>
        <p className="muted small" style={{ marginTop: 8 }}>
          Wire this into a <b>Command</b> button - tapping it sends the
          snapshots from that button&apos;s window. The window may cover at
          most <b>24 hours</b>; for a longer stretch use{" "}
          <b>Footage → Export</b>, which sends the originals as a zip rather
          than the copies Telegram recompresses.
        </p>
        <p className="muted small">
          When the window holds more than this many, the <b>newest</b> are sent
          and the caption says how many there were. <b>All</b> stops at 60 -
          past that the answer stops being a message and becomes a download.
        </p>
      </>)}

      {node.type === "integration.watch" && (<>
        <Field label="Recorded Trigger Name"
               hint="What this Recorded Trigger is called in the message and in the log.">
          <input className="input" value={d.name || ""} disabled={!editable}
                 placeholder="Violations today" onChange={set("name")} />
        </Field>
        <Field label="Check every"
               hint="How often the Query runs. A watch is a timer, not a live feed - for something that must fire the instant a camera sees it, use an analytics node with a Condition.">
          <div className="rail-line">
            <input className="input" type="number" min={1} max={1440}
                   value={d.every_min ?? 15} disabled={!editable}
                   style={{ width: 90 }}
                   onChange={(e) => onChange({
                     every_min: Math.max(1, Math.min(1440,
                       Number(e.target.value) || 15)) })} />
            <span className="muted small">minutes</span>
          </div>
        </Field>
        <Field label="Over which period"
               hint="Bound into the Query as %(from)s and %(to)s, the same way a Command button binds its own.">
          <select className="input" value={d.period || "today"}
                  disabled={!editable} onChange={set("period")}>
            <option value="hour">Last hour</option>
            <option value="last24">Last 24 hours</option>
            <option value="today">Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="week">This week</option>
            <option value="last7">Last 7 days</option>
            <option value="month">This month</option>
            <option value="last30">Last 30 days</option>
            <option value="none">All time</option>
          </select>
        </Field>
        <Field label="Send when"
               hint="The first number in the first row the Query returns - SELECT COUNT(*) … is exactly that.">
          <div className="rail-line">
            <select className="input" value={d.op || ">"} disabled={!editable}
                    style={{ flex: "0 0 90px" }} onChange={set("op")}>
              <option value=">">&gt;</option>
              <option value=">=">&ge;</option>
              <option value="<">&lt;</option>
              <option value="<=">&le;</option>
              <option value="==">=</option>
              <option value="!=">&ne;</option>
            </select>
            <input className="input" type="number" value={d.value ?? 0}
                   disabled={!editable} style={{ flex: 1, minWidth: 0 }}
                   onChange={(e) => onChange({
                     value: Number(e.target.value) || 0 })} />
          </div>
        </Field>
        <Field label="Then stay quiet for"
               hint="A number stuck over the line is one situation. It sends on the crossing, then says nothing for this long even if it crosses again.">
          <div className="rail-line">
            <input className="input" type="number" min={0} max={1440}
                   value={d.quiet_min ?? 60} disabled={!editable}
                   style={{ width: 90 }}
                   onChange={(e) => onChange({
                     quiet_min: Math.max(0, Math.min(1440,
                       Number(e.target.value) || 0)) })} />
            <span className="muted small">minutes</span>
          </div>
        </Field>
        <p className="muted small" style={{ marginTop: 8 }}>
          Wire a <b>Query</b> in and a <b>Telegram</b> or <b>Email</b> node
          out - the message is written on that node. It may quote{" "}
          <code className="mono">{"{value}"}</code>,{" "}
          <code className="mono">{"{threshold}"}</code>,{" "}
          <code className="mono">{"{name}"}</code>,{" "}
          <code className="mono">{"{period}"}</code> and{" "}
          <code className="mono">{"{table}"}</code>.
        </p>
      </>)}

      {node.type === "integration.command" && (<>
        <Field label="Command"
               hint="The word people type after the slash. Letters, numbers and underscores - /summary, /shift_report.">
          <div className="rail-line">
            <span className="mono" style={{ opacity: .6 }}>/</span>
            <input className="input mono" value={d.command || ""}
                   disabled={!editable} placeholder="summary"
                   onChange={(e) => onChange({
                     command: e.target.value.replace(/[^A-Za-z0-9_]/g, "")
                       .toLowerCase().slice(0, 31) })} />
          </div>
        </Field>
        <Field label="Menu heading"
               hint="Shown above the buttons when somebody sends the command. Blank uses the command word.">
          <input className="input" value={d.title || ""} disabled={!editable}
                 placeholder="Shift summary" onChange={set("title")} />
        </Field>
        <Field label="Note under the heading (optional)"
               hint="One line of context - what this report covers, or who it is for.">
          <input className="input" value={d.intro || ""} disabled={!editable}
                 placeholder="Counts from the packing line" onChange={set("intro")} />
        </Field>

        {/* THE BUTTONS. Each one grows a port on the node, and the Query
            wired to that port is what the button runs -- so the canvas shows
            what each button reports on rather than hiding it in this panel.
            A period is a preset for the same statement: the query is written
            once with %(from)s and %(to)s and every button re-runs it over its
            own window. */}
        <SubHead>Buttons</SubHead>
        {(d.buttons || []).map((b, i) => (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "center",
                                marginBottom: 6 }}>
            <input className="input" value={b.label || ""} disabled={!editable}
                   placeholder="Today" style={{ flex: 1, minWidth: 0 }}
                   onChange={(e) => onChange({
                     buttons: (d.buttons || []).map((x, j) => (j === i
                       ? { ...x, label: e.target.value.slice(0, 32) } : x)) })} />
            <select className="input" value={b.period || "today"}
                    disabled={!editable} style={{ flex: "0 0 130px" }}
                    onChange={(e) => onChange({
                      buttons: (d.buttons || []).map((x, j) => (j === i
                        ? { ...x, period: e.target.value } : x)) })}>
              <option value="hour">Last hour</option>
              <option value="last24">Last 24 hours</option>
              <option value="today">Today</option>
              <option value="yesterday">Yesterday</option>
              <option value="week">This week</option>
              <option value="last7">Last 7 days</option>
              <option value="month">This month</option>
              <option value="last30">Last 30 days</option>
              <option value="custom">Ask for dates</option>
              <option value="none">All time</option>
            </select>
            {/* Table or picture. Same statement, same bound window -- only
                how the answer is drawn. */}
            <select className="input" value={b.chart || "table"}
                    disabled={!editable} style={{ flex: "0 0 96px" }}
                    onChange={(e) => onChange({
                      buttons: (d.buttons || []).map((x, j) => (j === i
                        ? { ...x, chart: e.target.value } : x)) })}>
              <option value="table">Table</option>
              <option value="bar">Bar</option>
              <option value="line">Line</option>
              <option value="pie">Pie</option>
            </select>
            {editable && (
              <button type="button" className="btn btn-sm" title="Remove"
                      onClick={() => onChange({
                        buttons: (d.buttons || []).filter((_x, j) => j !== i) })}>
                ×
              </button>
            )}
          </div>
        ))}
        {editable && (d.buttons || []).length < 12 && (
          <button type="button" className="btn btn-sm"
                  onClick={() => onChange({
                    buttons: [...(d.buttons || []),
                              { label: "Today", period: "today",
                                chart: "table" }] })}>
            + Add button
          </button>
        )}
        <p className="muted small" style={{ marginTop: 8 }}>
          Wire a <b>Query</b> into each button's port - that statement is what
          the button runs. Write the period into it as{" "}
          <code className="mono">%(from)s</code> and{" "}
          <code className="mono">%(to)s</code> and every button reports over
          its own window from one statement. The dates are bound, never pasted
          into the text.
        </p>
        <p className="muted small">
          A chart reads the first column as the labels and the first number
          column after it as the values - <code className="mono">SELECT date,
          COUNT(*) … GROUP BY date</code> draws as it stands. A result with no
          numbers to plot arrives as the table instead.
        </p>
        <p className="muted small">
          Only chats on this bot's allowlist are answered -{" "}
          <strong>Settings → Telegram</strong> on the hub.
        </p>
      </>)}
      {node.type === "storage.csv" && (<>
        <Field label="File name"
               hint="What the attachment is called. Blank borrows the Query's own name, so the usual case needs nothing typed.">
          {/* The placeholder is the name it will ACTUALLY borrow, not a
              description of the rule: beside a Query called "Violation Data",
              "the Query's name" reads as a node that has not noticed the
              wire. */}
          <input className="input" value={d.name || ""} disabled={!editable}
                 placeholder={querySrc?.data?.name
                   ? `${querySrc.data.name} (from the Query)`
                   : "the Query's name"}
                 onChange={set("name")} />
        </Field>
        <Field label="Rows to include"
               hint="All rows: every row the wired Query returns. Most rows: keep only the first rows, to keep a message small - the rest are cut from the end.">
          <select className="input" disabled={!editable}
                  value={Number(d.max_rows) > 0 ? "most" : "all"}
                  onChange={(e) => onChange({
                    max_rows: e.target.value === "most"
                      ? (Number(d.max_rows) > 0 ? Number(d.max_rows) : 100) : 0,
                  })}>
            <option value="all">all rows from the link</option>
            <option value="most">most rows (a limit)</option>
          </select>
        </Field>
        {Number(d.max_rows) > 0 && (
          <Field label="Most rows to include"
                 hint="The most rows the file holds, counted from the top.">
            <input className="input" type="number" min="1" max="100000"
                   value={d.max_rows} disabled={!editable}
                   onChange={(e) => onChange({
                     max_rows: Math.max(1, parseInt(e.target.value, 10) || 1),
                   })} />
          </Field>
        )}
        <p className="muted small">
          Wire a <b>Query</b> into this node and this node into an{" "}
          <b>Email</b> or <b>Telegram</b>. The rows travel as a <b>file</b> -
          nothing else is needed. In the message,{" "}
          <code className="mono">{"{csv}"}</code> names that file and{" "}
          <code className="mono">{"{csv_text}"}</code> pastes the table into
          the text as well, for a short result worth reading without opening
          anything.
        </p>
        {/* The file, now, in the browser. Wiring a Query in and then waiting
            for 22:00 to find out whether the attachment is the right shape is
            not a way to build anything -- and the same rows go out to whoever
            receives the message, so seeing them first is the check. */}
        {/* A Query is the whole requirement. Where the file GOES is a separate
            decision, and made later: looking at the rows is how somebody
            decides whether to send them at all. */}
        <CsvDownload node={node} ready={!!querySrc}
                     why="Wire a Query into this node to give it rows." />
      </>)}
      {node.type === "storage.query" && (<>
        <Field label="Query Name"
               hint="Required. The Dashboard and Analytics editors list what this graph publishes by name - an unnamed query is a card you can only identify by dragging it out.">
          <input className="input" value={d.name || ""} disabled={!editable}
                 onChange={(e) => onChange({ name: e.target.value })}
                 placeholder="Busiest zones" />
        </Field>
        {!String(d.name || "").trim() && (
          <p className="small c-warn">
            Name this Query so you can find its card later.
          </p>
        )}
        <Field label="Statement"
               hint="SELECT only. Read the tables you wired in by their key - the same names the Database page shows.">
          <textarea className="input mono" rows={7} disabled={!editable}
                    ref={sqlRef}
                    value={d.sql || ""} spellCheck={false}
                    onChange={(e) => onChange({ sql: e.target.value })}
                    placeholder={"select pid, count(*) as seen\nfrom counting\ngroup by pid\norder by seen desc"} />
        </Field>
        <QueryColumns cols={queryColumns} editable={editable}
                      onInsert={(text) => onChange({
                        sql: insertAtCaret(sqlRef.current, d.sql || "", text) })} />
        {/* WHICH ROWS, asked once and answered here rather than baked into
            the statement. A window written as CURRENT_DATE - INTERVAL '1 day'
            is a card that can never follow the date picker above it, and
            every card built from this query would have to be rewritten to
            change its mind. The period is bound into the statement at
            %(from)s / %(to)s -- bound, never pasted, which is why they are
            spelled as placeholders. */}
        <Field label="Period"
               hint="Which rows the statement sees. It reaches the SQL at %(from)s and %(to)s - use the button below to write them in.">
          <select className="input" value={d.range || ""} disabled={!editable}
                  onChange={(e) => onChange({ range: e.target.value })}>
            <option value="">all time</option>
            <option value="page">follow the Analytics range</option>
            <option value="today">today</option>
            <option value="week">this week</option>
            <option value="month">this month</option>
          </select>
        </Field>
        <div className="rail-line" style={{ marginBottom: 12 }}>
          <button type="button" className="btn btn-sm" disabled={!editable}
                  title="Write the period filter at the cursor"
                  onClick={() => onChange({
                    sql: insertAtCaret(sqlRef.current, d.sql || "",
                                       "datetime >= %(from)s AND datetime < %(to)s") })}>
            + period filter
          </button>
          {sqlWantsDates && (
            <span className="small muted">bound from Period above</span>
          )}
        </div>
        {/* A statement that ignores the placeholders ignores the Period, and
            silently: the card runs, it just answers about all time. Said
            here, next to the setting it makes inert. */}
        {!!d.range && !sqlWantsDates && (
          <p className="small c-warn">
            This statement never names the period, so it reads every row
            whatever Period says. Add <code>%(from)s</code> / <code>%(to)s</code>
            to a WHERE clause - the button above writes one.
          </p>
        )}
        {/* The tables are a WIRE, not a picker: the canvas should show what a
            query depends on. So the inspector reports what is wired rather
            than offering a second way to choose it that could disagree. */}
        <p className="small" style={{ color: "var(--muted-text, #6b6b6b)" }}>
          {queryTables.length
            ? <>Reads {queryTables.map((t) => <code key={t.key}>{t.key}</code>)
                  .reduce((a, b) => [a, ", ", b])}.</>
            : "Nothing wired in yet - drag a Data Table onto the table port on the left, "
              + "which is what says the query may read it."}
        </p>
        {/* Try it HERE. The statement used to be writable only in this box and
            readable only on the Database page's console, so the way to find
            out whether it worked was to save the graph, walk to another page
            and paste it in. The console's own endpoint answers this box just
            as well -- same read-only role, same scoped views, same row cap. */}
        <QueryRunner sql={d.sql} />
        <p className="small" style={{ color: "var(--muted-text, #6b6b6b)" }}>
          Runs read-only, against this system's tables alone. A card wired to
          the result draws it: a tile takes the first number, or the first cell when
          the answer is a word; a chart takes the
          first column as labels and the next number column as values, and a
          table card shows every column.
        </p>
      </>)}

      {spec.category === "output" && !TRANSPARENT_OUT.includes(node.type)
        && !SELF_FIRED_OUT.includes(node.type) && (
        <>
          {/* No "wire an Analytics node in first" here. An output with nothing
              upstream yet is a graph half drawn, not a mistake: compile
              already treats it as a DRAFT (see _STARTS_A_CHAIN in
              core/services/workflow.py) and reports it once, by name, on save.
              Repeating it in the panel scolds the operator while they are
              still placing the node. */}
          {node.type === "output.dataset" && (<>
            <Field label="Table"
                   hint="Tables are designed on the Database page. This node fills one in; it cannot create one.">
              <select className="input" value={d.dataset || ""} disabled={!editable}
                      onChange={(e) => {
                        const picked = dbTables.find((t) => t.key === e.target.value);
                        onChange({
                          dataset: e.target.value,
                          name: picked?.name || "",
                          // Switching table keeps nothing: the old sources
                          // name columns the new table does not have, and
                          // silently carrying them over is how a node ends up
                          // writing into columns that no longer exist.
                          sources: {},
                        });
                      }}>
                <option value="">- choose a table -</option>
                {dbTables.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.name} ({t.columns.length} column
                    {t.columns.length === 1 ? "" : "s"})
                  </option>
                ))}
              </select>
            </Field>
            {!dbTables.length && (
              <p className="small c-warn">
                No tables exist yet. Create one on the Database page, then pick
                it here.
              </p>
            )}
            {d.dataset && !pickedTable && (
              <p className="small c-danger">
                There is no table called <span className="mono">{d.dataset}</span>{" "}
                any more. Pick another, or recreate it on the Database page -
                rows already recorded under that name are still there.
              </p>
            )}
            <Field label="Operation"
                   hint="Insert records rows. Select writes nothing - use it when you only want to read columns back out into cards.">
              <select className="input" value={d.op || "insert"} disabled={!editable}
                      onChange={set("op")}>
                <option value="insert">Insert - record rows</option>
                <option value="select">Select - read only</option>
              </select>
            </Field>
            {d.op === "select" ? (
              <p className="muted small">
                This node writes nothing. Wire a column's <strong>right</strong>{" "}
                port into a card to show what the table holds.
              </p>
            ) : (
            <Field label="Write a row">
              <select className="input" value={d.mode || "alert"} disabled={!editable}
                      onChange={set("mode")}>
                <option value="alert">when something alerts</option>
                <option value="crossing">each time something crosses a line</option>
                <option value="every">every few seconds</option>
                <option value="change">when a value changes</option>
              </select>
            </Field>
            )}
            {d.op !== "select" && (d.mode || "alert") === "alert" && (
              <p className="muted small">
                Records a row whenever a rule wired into it alerts - several
            rules may feed one table, and each writes its own row. Nothing
                extra to connect - add a Condition to that rule if it has none.
              </p>
            )}
            {/* The reported "two rows per crossing": a live counter's In/Out
                is a level, and on-change records both its edges. This mode
                rides the per-crossing event instead, so the row count IS the
                crossing count -- said plainly here, together with what a
                wired In/Out column reads on such a row, because the number
                on that column (1, or 2 when two cross together) is not "1 per
                row" and would otherwise be summed as if it were. */}
            {d.op !== "select" && d.mode === "crossing" && (
              <p className="muted small">
                One row the moment an object crosses the line of the Line Counter
                wired into this table - whichever way, and never a second row
                when it steps off the line. Give a column <strong>from the
                event</strong> - <strong>which way it crossed</strong> for
                in / out. A column wired from the counter's own In or Out port
                records the counter's live value at that moment (1 for a lone
                crosser, 2 when two cross together), not 1 per row - count rows
                by direction for exact totals. A counter set to each-object-once
                still writes a row for every crossing here; a Class Filter
                upstream of the counter filters the rows.
              </p>
            )}
            {d.op !== "select" && d.mode === "every" && (
              <Field label="Every"
                     hint="Seconds between rows. Five is the floor - every row is also an entry in the event log, which is re-read in full on each restart.">
                <input className="input" type="number" min="5" max="3600" step="5"
                       value={d.interval_s ?? 60} disabled={!editable}
                       onChange={set("interval_s")} />
              </Field>
            )}
            {d.op !== "select" && d.mode === "change" && (
              // The row is written the moment the value moves -- the limit
              // below is a damper for a value that jitters, held PER OBJECT,
              // so it never hides an arrival. Zero is the real-time setting
              // and the button is the plain way to say it: an operator who
              // wants "record it as it happens" should not have to work out
              // that the answer is typing a 0 into a box called "no faster
              // than". The app caps rows per second regardless.
              <Field label="No faster than"
                     hint="Seconds between rows about the SAME object - a damper for a value that jitters every frame. Real time writes every change; the app still caps a table at 20 rows a second.">
                <div className="rail-line">
                  <input className="input" type="number" min="0" max="3600" step="1"
                         style={{ flex: 1, minWidth: 0 }}
                         value={d.min_interval_s ?? 0} disabled={!editable}
                         onChange={set("min_interval_s")} />
                  <button type="button"
                          className={"btn" + (Number(d.min_interval_s ?? 0) ? "" : " btn-primary")}
                          disabled={!editable}
                          title="Write the row the moment the value changes"
                          style={{ flex: "none", whiteSpace: "nowrap" }}
                          onClick={() => onChange({ min_interval_s: 0 })}>
                    Real time
                  </button>
                </div>
              </Field>
            )}
            {/* One row per object, or a row per sighting. Only an identity
                column makes "the same object" findable, so without one the
                choice is shown disabled with the reason rather than hidden:
                an operator looking for it should learn what it needs. The
                compiler drops it the same way (workflow.py, with a warning). */}
            {d.op !== "select" && (
              <Field label="When the same object is seen again"
                     hint={tracksObjects
                       ? "Add a new row keeps every sighting. Update the existing row keeps ONE row per object, refreshed with its latest values and the time it was last seen."
                       : "Needs a column that records which object it is - wire an object's OID into a column, or set a column to from the event: the tracked object's id. Until then every write adds a new row."}>
                <select className="input"
                        value={tracksObjects ? (d.on_repeat || "insert") : "insert"}
                        disabled={!editable || !tracksObjects}
                        onChange={set("on_repeat")}>
                  <option value="insert">Add a new row</option>
                  <option value="update">Update the existing row</option>
                </select>
              </Field>
            )}
            {/* A row per object, or one row listing the whole group (a tray
                of two boards = one row, two ids). Only offered when a column
                records the object -- there are no ids to list otherwise --
                and not for line crossings, which are one row per crossing.
                The compiler and validator drop it the same way. */}
            {d.op !== "select" && tracksObjects && d.mode !== "crossing" && (
              <Field label="Object IDs in a row"
                     hint={(d.ids_in_row || "object") === "group"
                       ? "One row lists every object present, IDs together (\"4, 5\"). A new row is saved only when that group changes - one added, replaced or gone. The same group again is not saved. Note: a Count Difference Value on this column counts groups, not objects."
                       : "Each object gets its own row. Pick one row for the whole group to list all IDs together and save only when the group changes."}>
                <select className="input"
                        value={d.ids_in_row || "object"}
                        disabled={!editable}
                        onChange={set("ids_in_row")}>
                  <option value="object">One row per object</option>
                  <option value="group">One row for the whole group (IDs listed together)</option>
                </select>
              </Field>
            )}
            {d.dataset && d.op !== "select" && (
              <SourcesEditor d={{ ...d, _wired: wiredColumns }} editable={editable}
                             statKeys={vars.stat_keys || []}
                             columns={pickedTable?.columns || []}
                             labelFor={(k) => statLabel(k, functions, vars.cameras,
                                                        vars.zone_list,
                                                        vars.state_labels)}
                             onChange={onChange} />
            )}
            <p className="muted small">
              Rows land in the database and can be charted, filtered and
              exported from Analytics. The table's shape is set on the{" "}
              <Link to="/database">Database page</Link> - a column added here
              is added to the table too.
            </p>
          </>)}
          {(node.type === "output.io_out"
            || node.type === "output.buzzer"
            || node.type === "output.io_pwm") && (
            <BoardFields node={node} d={d} editable={editable}
                         set={set} upMetrics={upMetrics} />
          )}
          {node.type === "output.zone_switch" && (<>
            <Field label="Zone to switch"
                   hint="Armed while the wired value is truthy. Runtime only - the zone's saved checkbox on Camera Setup returns on restart.">
              <select className="input" value={d.zone || ""} disabled={!editable}
                      onChange={set("zone")}>
                <option value="">choose a zone…</option>
                {(meta?.variables?.zone_list || []).map((z) => (
                  <option key={z.id} value={z.id}>{z.name || z.id}</option>
                ))}
              </select>
            </Field>
            {upMetrics.length > 0 && (
              <Field label="Which value decides"
                     hint="From the node wired in - e.g. an Object Motion Tracking's `idle` for “arm while everything holds still”.">
                <select className="input" value={d.metric || upMetrics[0]}
                        disabled={!editable} onChange={set("metric")}>
                  {upMetrics.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
            )}
            <Field label="When the wired value is 1"
                   hint="And the opposite when it is 0 - the two states are the whole node.">
              <select className="input"
                      value={d.action || (d.invert ? "disable" : "enable")}
                      disabled={!editable} onChange={set("action")}>
                <option value="enable">enable the zone (0 disables it)</option>
                <option value="disable">disable the zone (0 enables it)</option>
              </select>
            </Field>
            <p className="muted small">
              Wire any 0/1 value in: a <strong>Object Motion Tracking</strong>'s{" "}
              <code>active</code> or <code>idle</code>, a{" "}
              <strong>When</strong> node's verdict, a counter. The zone counts
              and draws while enabled, and goes silent and invisible while
              disabled.
            </p>
          </>)}
          {node.type === "output.modbus_write" && (<>
            {/* Node-RED's Modbus Write, field for field and label for label.
                Required-and-empty fields get Node-RED's red outline, so what
                is missing is visible before the save says so. */}
            <Field label="Name">
              <input className="input" value={d.name || ""} disabled={!editable}
                     onChange={set("name")} placeholder="Name" />
            </Field>
            <Field label="Unit-Id" hint="Blank = the server's Unit-Id (0-255).">
              <input className="input" type="number" min="0" max="255"
                     value={d.unit ?? ""} disabled={!editable} placeholder="server's"
                     onChange={(e) => onChange({ unit: e.target.value === "" ? "" : +e.target.value })} />
            </Field>
            <Field label="FC">
              <select className="input" value={d.fc ?? 6} disabled={!editable}
                      onChange={(e) => onChange({ fc: +e.target.value })}>
                <option value={5}>FC 5: Force Single Coil</option>
                <option value={6}>FC 6: Preset Single Register</option>
                <option value={15}>FC 15: Force Multiple Coils</option>
                <option value={16}>FC 16: Preset Multiple Registers</option>
              </select>
            </Field>
            <Field label="Address" hint="0-65535, the raw protocol address.">
              <input className="input" type="number" min="0" max="65535"
                     value={d.address ?? ""} disabled={!editable} placeholder="0-65535"
                     style={d.address === "" || d.address == null ? REQUIRED_EMPTY : undefined}
                     onChange={(e) => onChange({ address: e.target.value === "" ? "" : +e.target.value })} />
            </Field>
            <Field label="Quantity"
                   hint="FC 15/16: how many coils or registers, from the address up. A single number wired in is repeated this many times; a list from a Python node is written as given.">
              <input className="input" type="number" min="1" max="65535"
                     value={d.quantity ?? ""} disabled={!editable} placeholder="1-65535"
                     style={d.quantity === "" || d.quantity == null ? REQUIRED_EMPTY : undefined}
                     onChange={(e) => onChange({ quantity: e.target.value === "" ? "" : +e.target.value })} />
            </Field>
            <label style={{ display: "flex", alignItems: "center", gap: 8,
                            padding: "4px 0" }}>
              <input type="checkbox" checked={!!d.delay_start} disabled={!editable}
                     onChange={set("delay_start")} />
              <span className="small">Delay to activate input</span>
              <InfoHint label="About Delay to activate input">
                Ignore what arrives for a few seconds after the app starts, so
                a restart never writes a stale value onto the machine.
              </InfoHint>
            </label>
            {d.delay_start && (
              <Field label="Start delay (s)">
                <input className="input" type="number" min="0" max="600" step="1"
                       value={d.start_delay_s ?? 5} disabled={!editable}
                       onChange={set("start_delay_s")} />
              </Field>
            )}
            {/* A drive that stops a moment after each write: the server's
                Drive watchdog switch (Tok Aba 2026-10-07). */}
            <ModbusWatchdogNote server={d.server} />
            <Field label="Server">
              <ModbusServerRow value={d.server || ""} editable={editable}
                               onChange={(v) => onChange({ server: v })}
                               onEdit={(id) => setServerEdit(id)}
                               onAdd={() => setServerEdit("")} />
            </Field>
            <p className="muted small">
              Wire the <strong>value</strong> into the top port - a metric, a
              variable, Math or a Formula. Wire a Condition, Compare or Trigger
              into <strong>trigger</strong> to write when it fires; leave it
              empty to write whenever the value changes. Injects wired into
              the value port (trigger empty) write on every press, in turn,
              the same payload included. A Python node may
              return <code className="mono">{"{value, fc, unitid, address, quantity}"}</code>{" "}
              to override these settings for that write.
            </p>
          </>)}
          {node.type === "output.rs485" && (<>
            <Field label="Serial port"
                   hint="The isolated RS485 port, or a USB adapter.">
              <input className="input" value={d.device || ""} disabled={!editable}
                     onChange={set("device")} placeholder="/dev/ttyAMA2" />
            </Field>
            <Field label="Baud rate" hint="Must match the device at the other end - a mismatch is silence, not an error.">
              <select className="input" value={d.baudrate ?? 9600} disabled={!editable}
                      onChange={set("baudrate")}>
                {[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
                  .map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </Field>
            <Field label="Slave / unit id">
              <input className="input" type="number" min="1" max="247"
                     value={d.unit ?? 1} disabled={!editable} onChange={set("unit")} />
            </Field>
            <Field label="Write to">
              <select className="input" value={d.target || "holding"} disabled={!editable}
                      onChange={set("target")}>
                <option value="holding">holding register (a word)</option>
                <option value="coil">coil (a single bit)</option>
              </select>
            </Field>
            <Field label="Address">
              <input className="input" type="number" min="0" max="65535"
                     value={d.register ?? 0} disabled={!editable} onChange={set("register")} />
            </Field>
            <Field label="Value" hint="Written when this rule fires. For a coil, 0 or 1.">
              <input className="input" type="number" value={d.value ?? 1}
                     disabled={!editable} onChange={set("value")} />
            </Field>
          </>)}
          {node.type === "output.mqtt" && (<>
            {"connection" in d ? (
              <Field label="Connection">
                <MqttConnectionRow value={d.connection || ""} editable={editable} required={false}
                                   onChange={(v) => onChange({ connection: v })}
                                   onEdit={(id) => setMqttEdit(id)}
                                   onAdd={() => setMqttEdit("")} />
                {!d.connection && (
                  <div className="muted small" data-mqtt-default="">
                    None picked: this box's own broker (127.0.0.1:1883).
                  </div>
                )}
              </Field>
            ) : (<>
              <Field label="Broker host"><input className="input" value={d.host || ""} disabled={!editable} onChange={set("host")} /></Field>
              <Field label="Port"><input className="input" type="number" value={d.port ?? 1883} disabled={!editable} onChange={set("port")} /></Field>
            </>)}
            <Field label="Topic"><input className="input" value={d.topic || ""} disabled={!editable} onChange={set("topic")} /></Field>
            <Field label="Payload"><input className="input" value={d.payload || ""} disabled={!editable} onChange={set("payload")} /></Field>
          </>)}
          {node.type === "output.webhook" && (<>
            <Field label="URL"><input className="input" value={d.url || ""} disabled={!editable} onChange={set("url")} placeholder="https://…" /></Field>
            <Field label="Method">
              <select className="input" value={d.method || "POST"} disabled={!editable} onChange={set("method")}>
                <option>POST</option><option>GET</option>
              </select>
            </Field>
            <Field label="Payload (JSON)"><textarea className="input" rows={2} value={d.payload || ""} disabled={!editable} onChange={set("payload")} /></Field>
            <HeadersEditor d={d} editable={editable} onChange={onChange} />
            <Field label="Bearer token"
                   hint="Sent as Authorization: Bearer …. Overrides an Authorization header row.">
              <PasswordInput native className="input" value={d.token || ""}
                     autoComplete="off"
                     disabled={!editable} maxLength={500} onChange={set("token")} />
            </Field>
          </>)}
          {node.type === "output.webhook_publish" && (<>
            <Field label="URL"><input className="input" value={d.url || ""} disabled={!editable} onChange={set("url")} placeholder="https://…" /></Field>
            <Field label="Method">
              <select className="input" value={d.method || "POST"} disabled={!editable} onChange={set("method")}>
                <option>POST</option><option>GET</option>
              </select>
            </Field>
            {upMetrics.length > 0 && (
              <Field label="Which value">
                <select className="input" value={d.metric || upMetrics[0]} disabled={!editable}
                        onChange={set("metric")}>
                  {upMetrics.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
            )}
            <Field label="Every" hint="Seconds between requests.">
              <input className="input" type="number" min="0.2" max="3600" step="0.5"
                     value={d.interval_s ?? 5} disabled={!editable}
                     onChange={set("interval_s")} />
            </Field>
            <Field label="Body">
              <select className="input" value={d.format || "value"} disabled={!editable}
                      onChange={set("format")}>
                <option value="value">just the number (3)</option>
                <option value="json">JSON - key, value, timestamp</option>
              </select>
            </Field>
            <HeadersEditor d={d} editable={editable} onChange={onChange} />
            <Field label="Bearer token"
                   hint="Sent as Authorization: Bearer …. Overrides an Authorization header row.">
              <PasswordInput native className="input" value={d.token || ""}
                     autoComplete="off"
                     disabled={!editable} maxLength={500} onChange={set("token")} />
            </Field>
            <p className="muted small">
              Posts on a clock, not on an alert - the endpoint gets the current
              number whether or not anything is wrong. No trigger needed on the
              analytics node.
            </p>
          </>)}
          {node.type === "output.mqtt_publish" && (<>
            {"connection" in d ? (
              <Field label="Connection"
                     hint="The broker this publishes to (IP address, port, user name and password). Several nodes can share one.">
                <MqttConnectionRow value={d.connection || ""} editable={editable} required={false}
                                   onChange={(v) => onChange({ connection: v })}
                                   onEdit={(id) => setMqttEdit(id)}
                                   onAdd={() => setMqttEdit("")} />
                {!d.connection && (
                  <div className="muted small" data-mqtt-default="">
                    None picked: this box's own broker (127.0.0.1:1883).
                  </div>
                )}
              </Field>
            ) : (<>
              <Field label="Broker host"><input className="input" value={d.host || ""} disabled={!editable} onChange={set("host")} /></Field>
              <Field label="Port"><input className="input" type="number" value={d.port ?? 1883} disabled={!editable} onChange={set("port")} /></Field>
            </>)}
            <Field label="Topic" hint="Type the topic to publish to - one exact topic, no + or #."><input className="input mono" value={d.topic || ""} disabled={!editable} onChange={set("topic")} placeholder="line1/count" /></Field>
            {/* Does a message to this topic actually get through? */}
            <PublishTest node={node} disabled={!editable} />
            {upMetrics.length > 0 && (
              <Field label="Which value">
                <select className="input" value={d.metric || upMetrics[0]} disabled={!editable}
                        onChange={set("metric")}>
                  {upMetrics.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
            )}
            {/* No "Every" and no payload of its own (Tok Aba 2026-10-08):
                WHEN it publishes is the Trigger's or the Inject's setting,
                one place to change it (core/services/publish_follow.py).
                Nothing wired in, it publishes nothing, and says so. */}
            {!(edges || []).some((e) => e.target === node.id) && (
              <p className="small" data-publish-unwired="">
                Nothing is wired in, so this publishes nothing. Wire a Trigger
                or an Inject into it to publish.
              </p>
            )}
            {/* No message/format field: the body IS the wired value, which
                is the one thing a PLC tag or a broker consumer reads. */}
            <label className="muted small"
                   style={{ display: "flex", gap: 6, alignItems: "flex-start",
                            margin: "8px 0" }}>
              <input type="checkbox" checked={!!d.retain} disabled={!editable}
                     onChange={set("retain")} /> retain (late subscribers get the last value)
            </label>
            <p className="muted small">
              Publishes when what is wired in says so, never on a clock of its
              own. A Trigger decides: Real-time sends its payload when it fires
              and the idle payload when it lets go, Interval sends it again
              every N seconds while the trigger holds, For N seconds sends it
              once and then the idle payload, and once per object sends it for
              each new object. An Inject publishes each time it fires, so its
              repeat is the clock. A Condition, a Compare, a Formula or a value
              publishes whenever it changes.
            </p>
          </>)}
          {node.type === "output.email" && (<>
            {/* One address per row, stored as the same ", " string as ever
                (EmailListField). A div, not the Field label: a label holding
                several inputs and buttons would send every click on it to
                the first input. 10 is detector_control's email cap. */}
            <div className="wf-field" data-wf-email-to="">
              <span className="wf-field-label">To</span>
              <EmailListField label="To" value={d.to || ""} disabled={!editable}
                              max={10} onChange={(to) => onChange({ to })} />
            </div>
            <Field label="Subject">
              <input className="input" value={d.subject || ""} disabled={!editable}
                     onChange={set("subject")} placeholder="{label}" />
            </Field>
            <Field label="Message">
              <textarea className="input" rows={3} value={d.body || ""} disabled={!editable}
                        ref={bodyRef}
                        onChange={set("body")}
                        placeholder="{label}&#10;Function: {fn}   Value: {value}" />
              <PlaceholderBar wired={msgWiredInputs} disabled={!editable}
                              hasCsv={hasCsv} csvFiles={csvFiles}
                              onInsert={(token) => insertInto("body", bodyRef, token)} />
              <UnknownPlaceholders texts={[d.subject, d.body]} known={msgNames}
                                   files={csvFiles} disabled={!editable}
                                   onReplace={(from, to) => onChange({
                                     ...d,
                                     subject: String(d.subject || "").split(from).join(to),
                                     body: String(d.body || "").split(from).join(to) })} />
            </Field>
            {/* WHICH mailbox, by name. The node carries no credentials: it
                used to take a sender and an app password of its own, which
                put the same secret in as many nodes as used it. Mailboxes are
                declared once under Settings > Email. */}
            <Field label="Send from">
              <select className="input" value={d.account || ""}
                      disabled={!editable} onChange={set("account")}>
                <option value="">
                  {mailAccounts.length
                    ? `Default mailbox${defaultMailbox ? ` (${defaultMailbox.name})` : ""}`
                    : "Default mailbox"}
                </option>
                {mailAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}{a.name === a.sender ? "" : ` - ${a.sender}`}
                  </option>
                ))}
                {/* A node pointing at a DELETED mailbox must not silently
                    read as "Default": the send will fail, and the picker is
                    where that is discoverable. */}
                {d.account && !mailAccounts.some((a) => a.id === d.account) && (
                  <option value={d.account}>
                    (this mailbox no longer exists)
                  </option>
                )}
              </select>
            </Field>
            {!mailAccounts.length && (
              <p className="muted small">
                No mailboxes are set up yet - add one under{" "}
                <strong>Settings → Email</strong> on the hub, or this node has
                nothing to send through.
              </p>
            )}
            {heldWhileAiOff && (
              <p className="small c-warn" data-held-ai-off>
                This report is not sent while AI detection is off.
              </p>
            )}
            <TestSend node={node} disabled={!editable} />
          </>)}
          {node.type === "output.telegram" && (<>
            {/* WHICH bot is no longer a question. A bot belongs to one
                system and a system to one bot (Settings > Telegram), so there
                is nothing here to pick -- and the picker that used to stand
                here offered every bot on the BOX, which let a node on one
                system be wired to another system's bot with nothing to stop
                it and nothing to report it. What is left is the fact: which
                bot this system posts as, or that it has none yet. */}
            {tgBots.length ? (
              <Field label="Posts as"
                     hint="The bot assigned to this system under Settings → Telegram. One system, one bot - change it there and every Telegram node here follows.">
                <div className="input" style={{ display: "flex", gap: 8,
                                                alignItems: "center",
                                                background: "transparent" }}>
                  <strong>{tgBots[0].name}</strong>
                  {tgBots[0].chat_id && (
                    <span className="muted small">
                      test chat {tgBots[0].chat_id}
                    </span>
                  )}
                </div>
              </Field>
            ) : (
              <p className="small c-warn">
                No bot is assigned to this system - this node cannot send.
                Assign one under <strong>Settings → Telegram</strong> on the
                hub.
              </p>
            )}
            {/* WHO hears it. The bot is who the box posts AS; the chat is a
                separate question, and one bot posting into the maintenance
                group, the shift channel and one supervisor's own chat is the
                ordinary shape. A bot that still keeps a chat of its own (the
                Settings page's test destination) is the fallback, which is
                what a graph drawn before this relies on. */}
            {/* WHO hears it (Tok Aba, 2026-10-14): the people linked to
                this system's bot under Settings > Telegram -- all of them, or
                the ones ticked -- or, as before, one typed chat. A node saved
                before this has no to_mode and opens on the chat, unchanged. */}
            <Field label="Send to"
                   hint="All linked people and Selected people follow Connected Bots under Settings → Telegram: someone linked later is included, someone removed there never gets another message.">
              <select className="input" data-tg-sendto
                      value={["all", "selected"].includes(d.to_mode) ? d.to_mode : "chat"}
                      disabled={!editable}
                      onChange={(e) => onChange({ ...d, to_mode: e.target.value })}>
                <option value="all">All linked people</option>
                <option value="selected">Selected people</option>
                <option value="chat">A chat ID (advanced)</option>
              </select>
            </Field>
            {["all", "selected"].includes(d.to_mode) ? (
              <TelegramPeople d={d} mode={d.to_mode} editable={editable}
                              onChange={onChange} />
            ) : (<>
            <Field label="Chat ID"
                   hint={"WHO hears this. A NUMERIC chat id - a group's "
                         + "starts with -100. "
                         + "@name addresses a public channel only: a private "
                         + "person has no @ address a bot can send to, "
                         + "whatever their profile shows, so a person is "
                         + "always their number. Two rules Telegram will not "
                         + "bend: a person must message the bot once before "
                         + "it may message them, and the bot must be a member "
                         + "of a group before it can post there. Get the "
                         + "number from "
                         + "api.telegram.org/bot<token>/getUpdates after they "
                         + "have written to it."}>
              <input className="input" value={d.chat_id || ""} disabled={!editable}
                     placeholder={chatOfBot(tgBots, d.account, defaultBot)
                       || "-1001234567890, 123456789 or @channel"}
                     onChange={set("chat_id")} />
            </Field>
            {/* Said here as well as at compile: the node is the only place
                this can be answered now that a bot need not carry a chat, and
                a blank one is a message with nowhere to go. */}
            {!String(d.chat_id || "").trim()
              && !chatOfBot(tgBots, d.account, defaultBot) && (
              <p className="small c-warn">
                Nobody hears this yet - type the chat id of the person, group
                or channel it should go to.
              </p>
            )}
            </>)}
            {/* A graph written before the bot list existed still carries its
                own token. It keeps working, and is shown so it can be moved:
                repointing it silently at the box's default bot would send
                somebody's alerts into a different chat without saying so. */}
            {!d.account && !!d.bot_token && (
              <p className="muted small">
                This node carries its own bot token, from before bots were
                declared once under Settings. It still sends. Pick a bot above
                to move the credential off the canvas.
              </p>
            )}
            <Field label="Message">
              <textarea className="input" rows={3} value={d.message || ""} disabled={!editable}
                        ref={bodyRef}
                        onChange={set("message")} placeholder="{label} ({fn} = {value})" />
              <PlaceholderBar wired={msgWiredInputs} disabled={!editable}
                              hasCsv={hasCsv} csvFiles={csvFiles}
                              onInsert={(token) => insertInto("message", bodyRef, token)} />
              <UnknownPlaceholders texts={[d.message]} known={msgNames}
                                   files={csvFiles} disabled={!editable}
                                   onReplace={(from, to) => onChange({
                                     ...d,
                                     message: String(d.message || "").split(from).join(to) })} />
            </Field>
            {heldWhileAiOff && (
              <p className="small c-warn" data-held-ai-off>
                This report is not sent while AI detection is off.
              </p>
            )}
            <TestSend node={node} disabled={!editable} />
          </>)}
          {node.type === "output.record" && (
            <>
              {/* TWO halves, because a clip that starts at the alarm has
                  already missed how the thing got there. */}
              <Field label="Include after the moment (s, max 5)">
                <input className="input" type="number" min="0.5" max="5" step="0.5"
                       value={Math.min(5, d.after_s ?? d.duration_s ?? 5)}
                       disabled={!editable}
                       onChange={(e) => {
                         const v = Math.min(5, Math.max(0.5, +e.target.value));
                         // duration_s kept in step: an older app build on a
                         // box mid-update reads that field and nothing else.
                         onChange({ after_s: v, duration_s: v });
                       }} />
              </Field>
              <Field label="Include before the moment (s, max 5)"
                     hint="The run-up. Not free: the camera keeps a rolling buffer of rendered frames for as long as this is above zero, whether or not anything ever fires.">
                <input className="input" type="number" min="0" max="5" step="0.5"
                       value={Math.min(5, d.before_s ?? 0)} disabled={!editable}
                       onChange={(e) => onChange({
                         before_s: Math.min(5, Math.max(0, +e.target.value)) })} />
              </Field>
              {(d.before_s ?? 0) > 0 && (
                <p className="muted small" style={{ marginTop: 0 }}>
                  This camera now renders every frame for the buffer, not only
                  while recording - about {Math.round((d.before_s || 0) * 10)}{" "}
                  frames held in memory. Set it to 0 to stop that.
                </p>
              )}
            </>
          )}
          {node.type === "output.record" && (
            <>
              {/* The page this node's clips go to in Footage. */}
              <Field label="Recording Clip Name"
                     hint="The page name in Footage. Footage shows this node's clips on a page of this name, apart from every other Record Clip's.">
                <input className="input" value={d.page_name || ""} maxLength={60}
                       placeholder="Crane approach" disabled={!editable}
                       data-page-name onChange={set("page_name")} />
              </Field>
              {!String(d.page_name || "").trim() && (
                <p className="small c-warn" style={{ margin: "0 0 8px" }}>
                  Give this Record Clip a name: its clips get their own page in
                  Footage under it.
                </p>
              )}
            </>
          )}
          {["output.snapshot", "output.record"].includes(node.type) && (
            <>
              {/* WHICH camera. The engine has always honoured o.camera -- a
                  rule on camera 2 saves camera 2's frame -- but nothing could
                  set it, so a multi-camera system could not photograph a
                  camera other than the one its rule watches. Only shown where
                  there is a choice to make. */}
              {Array.isArray(vars.cameras) && vars.cameras.length > 1 && (
                <Field label="Camera"
                       hint={"Which camera this saves from. Blank follows the "
                             + "rule that fires it, which is what you want "
                             + "unless the picture you need is somewhere else."}>
                  <select className="input" value={d.camera || ""} disabled={!editable}
                          onChange={set("camera")}>
                    <option value="">Follow the rule</option>
                    {(vars.cameras || []).map((c) => (
                      <option key={c.id} value={c.id}>{camLabel(vars.cameras, c.id)}</option>
                    ))}
                  </select>
                </Field>
              )}
              <CaptureMode node={node} d={d} editable={editable} onChange={onChange} />
              {/* WHICH object this is a picture of. It rides on the event --
                  the rule names the object whose clock or whose intrusion
                  fired it -- so nothing has to be wired for it. A soft no-op
                  where the rule is about a place rather than about anybody:
                  the stamp is simply absent, which is honest, and erroring
                  for asking would refuse a setting that is right the moment
                  the rule behind it changes. */}
              <label className="wf-check">
                <input type="checkbox" checked={!!d.oid_tag} disabled={!editable}
                       onChange={(e) => onChange({ oid_tag: e.target.checked })} />
                <span className="small">
                  Tag with object ID (when available) - stamps the object's id
                  into the corner of the{" "}
                  {node.type === "output.snapshot" ? "picture" : "clip"} and into
                  its filename. Nothing is stamped when the rule that fires
                  cannot say which object it was.
                </span>
              </label>
              {node.type === "output.snapshot" && (
                <>
                  {/* A close-up of every detected object, saved beside the
                      photo: Footage shows them beside it (Previous / Next
                      when there are several) and the PDF notice can carry
                      them. Off by default - it costs a little disk. */}
                  <label className="wf-check">
                    <input type="checkbox" checked={!!d.crop} disabled={!editable}
                           onChange={(e) => onChange({ crop: e.target.checked })} />
                    <span className="small">
                      Crop the violation - saves a close-up of the objects that
                      tripped the rule (its classes, inside its zone) beside
                      the photo, not of every box in view. The full photo is
                      kept either way.
                    </span>
                  </label>
                  {/* The page this Snapshot's photos go to in Footage. */}
                  <Field label="Image Snapshot Name"
                         hint="The page name in Footage. Footage shows this node's photos on a page of this name, apart from every other Snapshot's.">
                    <input className="input" value={d.page_name || ""} maxLength={60}
                           placeholder="Handrail violations" disabled={!editable}
                           data-page-name onChange={set("page_name")} />
                  </Field>
                  {!String(d.page_name || "").trim() && (
                    <p className="small c-warn" style={{ margin: "0 0 8px" }}>
                      Give this Snapshot a name: its photos get their own page in
                      Footage under it.
                    </p>
                  )}
                  {/* The words the PDF notice prints. Blank prints "--" (or the
                      stock heading), never a guess. */}
                  <Field label="Notice title"
                         hint="The red heading of the PDF notice. Blank prints VIOLATION NOTICE.">
                    <input className="input" value={d.notice_title || ""} maxLength={60}
                           disabled={!editable} placeholder="e.g. HANDRAIL VIOLATION NOTICE"
                           onChange={set("notice_title")} />
                  </Field>
                  <Field label="What was detected"
                         hint="Printed under Observation on the notice. Blank prints the detected class.">
                    <input className="input" value={d.violation || ""} maxLength={120}
                           disabled={!editable} placeholder="e.g. Not holding the handrail"
                           onChange={set("violation")} />
                  </Field>
                  <Field label="Location"
                         hint="Printed on the PDF notice as the place of the violation. Leave blank to print nothing.">
                    <input className="input" value={d.location || ""} maxLength={120}
                           disabled={!editable} placeholder="e.g. Staircase 4, level 2"
                           onChange={set("location")} />
                  </Field>
                </>
              )}
            </>
          )}
          <p className="muted small">
            Fires on a decision: a <strong>Condition</strong> or a{" "}
            <strong>Compare</strong> wired in front of it, or a{" "}
            <strong>Formula</strong> straight in - a formula's answer is
            already the decision, so it fires whenever that answer is not zero.
          </p>
        </>
      )}

      {/* The TRIGGER: a named moment, laid out as Tok Aba's mockup has it
          (2026-10-14): identity, when it fires, what it does, and the
          technical rest folded away. Everything it can be wired to has
          already decided (a branch port, a formula's 0/1 answer), so there
          is no threshold here. workflow/TriggerInspector.jsx; every control
          writes the field it always wrote. */}
      {node.type === "output.trigger" && (
        <>
          {wiredToSnap && <SnapFrames d={d} editable={editable} set={set} />}
          <TriggerFields d={d} editable={editable} onChange={onChange} node={node}
                         edges={edges} oidWired={triggerOidWired} boxZone={boxZone}
                         triggerZone={triggerZone}
                         oidTracked={triggerOidWired
                           || oidTrackedUpstream(node.id, nodes, edges)} />
          {/* Fire now, finish later: the alert, the colours and the log happen
              at the violation; the Snapshot photo and the Data Table row after
              this Trigger are completed once the object has left the zone (or
              been lost), with its total time and how far past the limit it
              went. Only behind a timer. */}
          <label className="wf-check">
            <input type="checkbox" checked={!!d.wait_end} data-wait-end
                   disabled={!editable || (!d.wait_end && !!holdBlocker)}
                   onChange={(e) => onChange({ wait_end: e.target.checked })} />
            <span className="small">
              Wait for the timer to end - the alert fires now, but the Data
              Table row and the Snapshot photo after this are completed once
              the object has left the zone (or been lost). A table column can
              then record the total time (duration_s) and how far past the
              limit it went (exceeded_s).
            </span>
          </label>
          {holdBlocker && (
            <p className="muted small" data-hold-why style={{ margin: "0 0 8px" }}>
              {d.wait_end ? "This will be switched off when you save. " : "Not available: "}
              {holdBlocker}
            </p>
          )}
        </>
      )}

      {node.type === "output.alert" && (
        <>
          {!upstreamFn && <p className="small c-warn">
            Wire an Analytics node into this alert first.</p>}
          <Field label="Metric"
                 hint={upstreamFn
                   ? `What ${(upstreamFn.data || {}).name || "the wired node"} publishes - the condition watches this value.`
                   : undefined}>
            <select className="input" value={d.metric || (upMetrics[0] || "")} disabled={!editable}
                    onChange={set("metric")}>
              {(upMetrics.length ? upMetrics : [d.metric || "count"]).map((m) => (
                <option key={m} value={m}>{metricLabel(m)}</option>
              ))}
            </select>
          </Field>
          <div className="wf-row">
            <Field label="Condition" style={{ flex: "1 1 88px" }}>
              <select className="input" value={d.op || ">"} disabled={!editable} onChange={set("op")}>
                {[">", ">=", "<", "<=", "==", "!="].map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="Value" style={{ flex: "1 1 88px" }}>
              <input className="input" type="number" value={d.value ?? 1} disabled={!editable}
                     onChange={set("value")} />
            </Field>
          </div>
          {/* The rule in one sentence - what actually fires, no guessing. */}
          <p className="muted small">
            Fires when <strong>{d.metric || upMetrics[0] || "the metric"}</strong>
            {METRIC_INFO[d.metric || upMetrics[0]]
              ? ` (${METRIC_INFO[d.metric || upMetrics[0]]})` : ""}{" "}
            is <strong>{d.op || ">"} {d.value ?? 1}</strong>. While it holds,
            the function's <code>active</code> value is 1 - bind a Verdict Card
            to show it.
          </p>
          {/* Severity, message, cooldown and colour left this panel for an
              Alert node of their own: picking a threshold should not mean
              scrolling past a colour picker. Wired one is what the operator
              sees here; with none, the event is a plain one and the values
              still stored on this node (an old graph) are what compiles. */}
          <p className="muted small">
            Wire an <strong>Alert</strong> after this to choose the severity,
            the message, how often it may repeat and the colour objects are
            drawn in - and to fire on this being <em>false</em> instead.
            Without one it raises a plain event, which every output after it
            still fires on.
          </p>
        </>
      )}

      {node.type === "output.event" && (
        <>
          {!upstreamFn && <p className="small c-warn">
            Wire something that is already true or false into this Alert - a
            Condition, a Formula, a Switch branch. It says what an alarm looks
            like, not when one happens.</p>}
          {/* The one thing here that changes WHEN rather than what: firing on
              the condition being false. It inverts the Condition's own
              comparison, so the threshold stays written down once. */}
          <Field label="Fire when the answer is"
                 hint="False reads the same answer the other way round - it inverts the Condition's comparison, or fires while a Formula is zero. One threshold, written once.">
            <select className="input" value={d.when || "true"} disabled={!editable}
                    onChange={set("when")}>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </Field>
          {/* What the objects this rule is about are drawn in. Red by
              default -- the colour a violation has always been -- so leaving
              it alone changes nothing. */}
          <Field label="Colour what"
                 hint="A zone rule often reads better with the zone itself going red than with the boxes inside it.">
            <select className="input" value={d.color_target || "box"}
                    disabled={!editable} onChange={set("color_target")}>
              <option value="box">the bounding boxes</option>
              <option value="zone">the zone outline</option>
              <option value="both">both</option>
            </select>
          </Field>
          <Field label="Colour"
                 hint="Applied while this rule holds. With a zone, to the objects inside it; without one, to the objects it counts.">
            <div className="filter-bar" style={{ alignItems: "center" }}>
              <input type="color" className="input"
                     style={{ width: 52, padding: 2, flex: "none" }}
                     value={d.box_color || DEFAULT_BOX_COLOR}
                     disabled={!editable}
                     onChange={(e) => onChange({ box_color: e.target.value.toUpperCase() })} />
              <span className="muted small" style={{ flex: 1 }}>
                {d.box_color && d.box_color.toUpperCase() !== DEFAULT_BOX_COLOR
                  ? d.box_color.toUpperCase() : "red (default)"}
              </span>
              {d.box_color && (
                <button className="btn btn-sm" disabled={!editable}
                        onClick={() => onChange({ box_color: "" })}>Reset</button>
              )}
            </div>
          </Field>
          <Field label="Severity">
            <select className="input" value={d.severity || "warn"} disabled={!editable}
                    onChange={set("severity")}>
              <option value="info">info</option><option value="warn">warn</option><option value="crit">crit</option>
            </select>
          </Field>
          <Field label="Message">
            <input className="input" value={d.message || ""} disabled={!editable}
                   onChange={set("message")} placeholder="e.g. Restricted area crowded" />
          </Field>
          <Field label="Cooldown (s)">
            <input className="input" type="number" value={d.cooldown_s ?? 30} disabled={!editable}
                   onChange={set("cooldown_s")} />
          </Field>
        </>
      )}

      {/* One explicit "I'm done here". Everything above already lands via
          auto-save; this button exists because finishing a node's setup
          deserves a definite end -- it saves NOW and closes the panel, so
          the operator is never left wondering whether the last edit took. */}
      {editable && onDone && (
        // STICKY, because a long form (a Data Table's columns, a Branch with
        // eight conditions) put this below the fold: the operator scrolled
        // to the end of a form they had already finished just to press it.
        <div className="wf-done">
          <button className="btn btn-accent" onClick={onDone}>
            Save
          </button>
        </div>
      )}
    </>
  );
}

// Label + control (+ the hint half the call sites already pass and which used
// to be dropped on the floor). `style` lets a caller give the field a flex
// basis when it sits in a wf-row.
/**
 * The placeholders a message may use, as buttons that insert them.
 *
 * An Email or Telegram node can be wired to SEVERAL rules at once, and each
 * one it is wired to becomes a value the message can name. Typing
 * `{people_count}` by hand means knowing the exact slug the compiler chose --
 * get it wrong and the message ships with empty text where a number should
 * be, which is indistinguishable from a value that happened to be blank. So
 * the names are offered, not remembered: what is on the canvas is what is on
 * the bar.
 *
 * The built-ins are shown in the same place and the same shape, because from
 * where the operator sits they are the same kind of thing.
 */
/**
 * WHEN a message output may send. Folded away, because the honest default is
 * "whenever the rule fires" and most nodes should stay that way.
 *
 * Every field is a restriction only when it is filled in, and a value that
 * does not parse is DROPPED server-side rather than stored. That asymmetry is
 * deliberate: the failure mode of a bad entry here is an alert that silently
 * never sends, and nobody discovers that until the thing it was watching for
 * has already happened.
 */
/** The Schedule node's form: one "Runs" selector, then only the fields that
 *  choice needs. Daily/Weekly/Monthly as three nodes would have been the same
 *  clock read three ways -- and three forms to keep in step. */
function ScheduleFields({ d, editable, onChange, clock }) {
  const every = String(d.every || "daily");
  const at = (
    <Field label="Time"
           hint="The box's own clock - the same time the Settings page shows.">
      <input className="input" type="time" value={d.at || "12:00"}
             disabled={!editable}
             onChange={(e) => onChange({ at: e.target.value })} />
    </Field>
  );
  return (
    <>
      <Field label="Runs"
             hint="How often this comes round. Everything below follows from it.">
        <select className="input" value={every} disabled={!editable}
                onChange={(e) => onChange({ every: e.target.value })}>
          <option value="minutes">Every N minutes</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </Field>
      {every === "minutes" && (
        <Field label="Every"
               hint="Counted from midnight, so 15 lands on :00 :15 :30 :45.">
          <input className="input" type="number" min="1" max="1440"
                 value={d.interval_min ?? 15} disabled={!editable}
                 onChange={(e) => onChange({ interval_min: +e.target.value })} />
        </Field>
      )}
      {every === "hourly" && (
        <Field label="Minutes past the hour">
          <input className="input" type="number" min="0" max="59"
                 value={d.minute ?? 0} disabled={!editable}
                 onChange={(e) => onChange({ minute: +e.target.value })} />
        </Field>
      )}
      {every === "weekly" && (
        <Field label="Day">
          <select className="input" value={d.weekday ?? 0} disabled={!editable}
                  onChange={(e) => onChange({ weekday: +e.target.value })}>
            {WEEKDAYS.map((name, i) => (
              <option key={name} value={i}>{name}</option>
            ))}
          </select>
        </Field>
      )}
      {every === "monthly" && (
        <>
          <Field label="Which day">
            <select className="input" value={(d.day ?? 1) === 0 ? "last" : "day"}
                    disabled={!editable}
                    onChange={(e) => onChange({ day: e.target.value === "last"
                      ? 0 : 1 })}>
              <option value="day">A day I pick</option>
              <option value="last">The last day of the month</option>
            </select>
          </Field>
          {(d.day ?? 1) !== 0 && (
            <Field label="Day of the month"
                   hint="A month too short for the day you pick runs on its last day, so February never loses the report.">
              <input className="input" type="number" min="1" max="31"
                     value={d.day ?? 1} disabled={!editable}
                     onChange={(e) => onChange({ day: +e.target.value })} />
            </Field>
          )}
        </>
      )}
      {every !== "minutes" && every !== "hourly" && at}
      <p className="muted small" style={{ marginTop: -2 }}>
        {scheduleSentence(d)}
        {clock?.zone ? ` · box clock, ${clock.zone}` : " · the box's own clock"}
        {clock?.now ? ` (now ${clock.now})` : ""}
      </p>
      <AdvancedSection label="Advanced - a run missed while the box was off">
        <label className="wf-check">
          <input type="checkbox" checked={!!d.catch_up} disabled={!editable}
                 onChange={(e) => onChange({ catch_up: e.target.checked })} />
          Send it when the box comes back
        </label>
        <p className="muted small" style={{ marginTop: 4 }}>
          Off, a run the box slept through is skipped and the next one goes out
          on time. On, it is sent as soon as the box is back - carrying the
          values as they are AT THAT MOMENT, not as they were at the missed
          time, because nothing was running to measure them.
        </p>
      </AdvancedSection>
      <p className="muted small" style={{ marginTop: 4 }}>
        Wire this into an Email or Telegram; wire your counters into the same
        node and their values fill the {"{placeholders}"} in the message.
      </p>
    </>
  );
}

/** Send this node's message once, now, and say what happened.
 *
 *  Everything between writing a message and reading it is invisible. The rule
 *  has to fire, the schedule has to come round, the mailbox credentials have
 *  to be right -- and each of those fails silently, at whatever hour the rule
 *  trips, in a log nobody is watching. The first evidence is a report that
 *  never arrived, which looks exactly like nothing having happened.
 *
 *  It sends what is SAVED, not what is on screen: the server compiles the
 *  stored canvas, because a message built from the request body would let a
 *  caller name their own recipients and have this box send it.
 */
/** The CSV node's rows, downloaded now.
 *
 *  Renders from the SAVED graph, like the test send beside it: what leaves the
 *  browser is a node id, never SQL. So an unsaved edit to the Query is not in
 *  the file yet, and the button says so rather than handing over yesterday's
 *  rows as though they were the new ones.
 */
function CsvDownload({ node, ready, why }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState(null);   // {ok, text}
  return (
    <div style={{ marginTop: 6 }}>
      <button type="button" className="btn btn-sm" disabled={!ready || busy}
              title={ready ? "Run the wired Query and download its rows" : why}
              onClick={async () => {
                setBusy(true); setSaid(null);
                try {
                  const file = await api.download("/api/workflow/csv-file",
                                                  { node: node.id });
                  saveBlob(file);
                  setSaid({ ok: true, text: `downloaded ${file.filename}` });
                } catch (e) {
                  setSaid({ ok: false, text: e.message || "the query failed" });
                } finally { setBusy(false); }
              }}>
        {busy ? "Running…" : "⭳ Download the CSV"}
      </button>
      <div className="muted small" style={{ marginTop: 4 }}>
        {said
          ? <span style={said.ok ? { color: "var(--ok, #16a34a)" }
                                 : { color: "var(--alert, #dc2626)" }}>
              {said.text}
            </span>
          : ready
            ? "The rows a message would attach right now. Save the canvas first."
            : why}
      </div>
    </div>
  );
}

function TestSend({ node, disabled }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState(null);   // {ok, text}
  return (
    <div style={{ marginTop: 6 }}>
      <button type="button" className="btn btn-sm" disabled={disabled || busy}
              onClick={async () => {
                setBusy(true); setSaid(null);
                try {
                  const r = await api.post("/api/workflow/test-output",
                                           { node: node.id });
                  setSaid({ ok: true, text: r.message || "sent" });
                } catch (e) {
                  setSaid({ ok: false, text: e.message || "it did not send" });
                } finally { setBusy(false); }
              }}>
        {busy ? "Sending…" : "Send a test now"}
      </button>
      <div className="muted small" style={{ marginTop: 4 }}>
        {said
          ? <span style={said.ok ? { color: "var(--ok, #16a34a)" }
                                 : { color: "var(--alert, #dc2626)" }}>
              {said.text}
            </span>
          : "Sends the saved message, with today’s values, ignoring the "
            + "schedule. Save the canvas first."}
      </div>
    </div>
  );
}


/** When a Schedule is allowed to run: a daily window, which weekdays, an
 *  absolute date range. All optional, all blank = always.
 *
 *  This used to live on the Email and Telegram nodes, one copy each -- so a
 *  rule that sent an email AND a Telegram needed the same window typed twice
 *  and kept in step by hand, and the node whose job is "what to say" was
 *  answering "when". One clock in front of both says it once.
 */
function RunWindow({ d, set, disabled }) {
  const DAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"],
                ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
  const picked = String(d.days || "").split(",").map((s) => s.trim())
                       .filter(Boolean);
  const toggleDay = (key) => {
    const next = picked.includes(key) ? picked.filter((k) => k !== key)
                                      : [...picked, key];
    // All seven is the same as none, and saying it the short way keeps the
    // stored value honest about there being no restriction.
    set("days")({ target: { value: next.length === 7 ? "" : next.join(",") } });
  };
  return (
    <details style={{ marginTop: 10 }}>
      <summary className="small" style={{ cursor: "pointer", opacity: 0.85 }}>
        Advanced - when it may run
      </summary>
      <div style={{ marginTop: 8 }}>
        <Field label="Only between">
          <div className="rail-line">
            <input className="input" type="time" disabled={disabled}
                   value={d.hours_from || ""} onChange={set("hours_from")} />
            <span className="muted small">and</span>
            <input className="input" type="time" disabled={disabled}
                   value={d.hours_to || ""} onChange={set("hours_to")} />
          </div>
          <div className="muted small">
            Leave both blank for any time. A window that crosses midnight
            works and means it - 22:00 to 06:00 is the night shift.
          </div>
        </Field>

        <Field label="Only on">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {DAYS.map(([key, label]) => {
              const on = picked.includes(key);
              return (
                <button key={key} type="button" className="btn btn-sm"
                        disabled={disabled} onClick={() => toggleDay(key)}
                        style={on ? { borderColor: "var(--accent, #04bade)",
                                      color: "var(--accent-text, #04bade)",
                                      background: "rgba(4,186,222,.10)" }
                                  : undefined}>
                  {label}
                </button>
              );
            })}
          </div>
          <div className="muted small">
            None selected means every day.
          </div>
        </Field>

        <Field label="Only between the dates">
          <div className="rail-line">
            <input className="input" type="date" disabled={disabled}
                   value={d.from_date || ""} onChange={set("from_date")} />
            <span className="muted small">and</span>
            <input className="input" type="date" disabled={disabled}
                   value={d.to_date || ""} onChange={set("to_date")} />
          </div>
          <div className="muted small">
            Both ends included. For an alert that only matters during a trial
            or a shutdown; blank for no end.
          </div>
        </Field>
      </div>
    </details>
  );
}

/** The people linked to this system's bot, for a Telegram node that sends to
 *  "All linked people" or "Selected people" (Tok Aba, 2026-10-14).
 *
 *  Read from the master (/api/telegram/recipients: workflow.edit, the email
 *  only for a site admin, never cached). The node stores Telegram user ids
 *  only -- never a name or an address -- and the app resolves them again at
 *  every send, so this list is for choosing, not a copy that goes stale.
 *
 *  A person is reached in their PRIVATE chat with the bot; one linked only
 *  inside a group cannot be messaged privately and is shown but not offered.
 *  A GROUP is a target only when ticked here, never as part of "All". */
function TelegramPeople({ d, mode, editable, onChange }) {
  const [list, setList] = useState(null);
  const [failed, setFailed] = useState(null);
  useEffect(() => {
    let alive = true;
    api.get("/api/telegram/recipients")
      .then((r) => { if (alive) setList(r || {}); })
      .catch((e) => { if (alive) setFailed(e); });
    return () => { alive = false; };
  }, []);
  if (failed) {
    return (
      <p className="small c-warn">
        The linked people could not be read: {String(failed.message || failed)}
      </p>
    );
  }
  if (!list) return <p className="muted small">Reading who is linked…</p>;
  const people = list.people || [];
  const groups = list.groups || [];
  const reachable = people.filter((p) => p.private);
  const who = (p) => [p.name ? `name shown as "${p.name}"` : `ID ${p.user_id}`,
                      p.username ? `@${p.username}` : "", p.email || ""]
    .filter(Boolean).join(" · ");
  if (!people.length) {
    return (
      <p className="small c-warn" data-tg-nobody>
        Nobody is linked to this bot yet: they send /start to the bot first.
      </p>
    );
  }
  if (mode === "all") {
    const n = reachable.length;
    return (
      <div data-tg-all style={{ marginBottom: 10 }}>
        <p className="small" style={{ margin: "0 0 4px" }}
           title={reachable.map(who).join("\n")}>
          <strong>{n} {n === 1 ? "person" : "people"}</strong> linked to{" "}
          {list.bot || "this bot"} will get it.
        </p>
        <details>
          <summary className="muted small">Who</summary>
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {reachable.map((p) => <li key={p.user_id}>{who(p)}</li>)}
          </ul>
        </details>
        {people.length > n && (
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            {people.length - n} linked only in a group, so the bot cannot
            message them privately.
          </p>
        )}
        {groups.length > 0 && (
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Groups are not included in All. To post into a group, choose
            Selected people and tick it.
          </p>
        )}
      </div>
    );
  }
  const picked = (d.to_people || []).map(String);
  const known = new Set([...reachable.map((p) => p.user_id),
                         ...groups.map((g) => g.chat_id)]);
  const stale = picked.filter((id) => !known.has(id)).length;
  const toggle = (id) => onChange({
    ...d, to_people: picked.includes(id) ? picked.filter((x) => x !== id)
                                         : [...picked, id] });
  const row = { display: "flex", gap: 6, alignItems: "center", marginBottom: 4 };
  return (
    <div data-tg-selected style={{ marginBottom: 10 }}>
      {people.map((p) => (
        <label key={p.user_id} style={row}
               title={p.private ? undefined
                 : "Linked only in a group: the bot cannot message them privately"}>
          <input type="checkbox" checked={picked.includes(p.user_id)}
                 disabled={!editable || !p.private}
                 onChange={() => toggle(p.user_id)} />
          <span className={p.private ? "small" : "small muted"}>{who(p)}</span>
        </label>
      ))}
      {groups.length > 0 && (
        <div className="muted small" style={{ margin: "6px 0 4px" }}>Groups</div>
      )}
      {groups.map((g) => (
        <label key={g.chat_id} style={row}>
          <input type="checkbox" checked={picked.includes(g.chat_id)}
                 disabled={!editable} onChange={() => toggle(g.chat_id)} />
          <span className="small">
            Group {g.chat_id} (linked there by {g.people.length}{" "}
            {g.people.length === 1 ? "person" : "people"})
          </span>
        </label>
      ))}
      {stale > 0 && (
        <p className="small c-warn" data-tg-stale>
          {stale} selected {stale === 1 ? "person is" : "people are"} no longer
          linked.
        </p>
      )}
      {!picked.length && (
        <p className="small c-warn">Nobody is selected yet.</p>
      )}
    </div>
  );
}

/** A {name} in the message that nothing fills is SENT AS WRITTEN, braces
 *  and all (Tok Aba, 2026-10-14: "{violation_summary}" arrived in an inbox).
 *  The compiler warns on the node; this says it while typing, under the
 *  field, with the nearest wired name one click away. */
function UnknownPlaceholders({ texts, known, files, onReplace, disabled }) {
  const unknown = unknownPlaceholders(texts, known, files);
  if (!unknown.length) return null;
  const candidates = [...known, ...csvTokens(files)];
  return (
    <div className="small c-warn" data-unknown-placeholders style={{ marginTop: 6 }}>
      {unknown.map((name) => {
        const near = closestPlaceholder(name, candidates);
        return (
          <div key={name}>
            {`{${name}} is not wired into this message, so it would be sent as written.`}
            {near && (
              <>
                {" Did you mean "}
                <button type="button" className="btn btn-sm" disabled={disabled}
                        title={`Replace {${name}} with {${near}}`}
                        onClick={() => onReplace(`{${name}}`, `{${near}}`)}>
                  {`{${near}}`}
                </button>
                {"?"}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PlaceholderBar({ wired = [], disabled, onInsert, hasCsv = false,
                         csvFiles = [] }) {
  const BUILTINS = [
    ["{label}", "what the alert said"],
    ["{value}", "the value that tripped it"],
    ["{fn}", "which rule fired"],
    ["{time}", "when it fired"],
    // Offered only when a CSV node feeds this message. A token that resolves
    // to nothing is worse than one that was never offered: it looks like a
    // table that came back empty.
    // The file is attached either way; these are about what the SENTENCE
    // says. {csv} names it, which is what a message with an attachment
    // normally does; {csv_text} pastes the table in as well, for a short
    // result somebody wants to read without opening anything.
    // Two or more files: one pair per file, by its name, because {csv}
    // alone would only ever say the first one.
    ...(hasCsv && csvFiles.length < 2
      ? [["{csv}", "the attached file’s name"],
         ["{csv_text}", "the table itself, in the message"]] : []),
    ...(csvFiles.length > 1 ? csvFiles.flatMap((f) => [
      [`{csv:${f}}`, `the file ${f}, by name`],
      [`{csv_text:${f}}`, `the rows of ${f}, in the message`]]) : []),
  ];
  const chip = {
    padding: "2px 7px", fontSize: 11, lineHeight: "17px", borderRadius: 6,
    border: "1px solid var(--line, rgba(128,128,128,.28))",
    background: "transparent", cursor: disabled ? "default" : "pointer",
    fontFamily: "var(--mono, ui-monospace, monospace)",
  };
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4,
                    alignItems: "center" }}>
        {wired.map((w) => (
          <button key={w.name} type="button" disabled={disabled}
                  title={`Insert the value from "${w.label}"`}
                  onClick={() => onInsert(`{${w.name}}`)}
                  style={{ ...chip, borderColor: "var(--accent, #04bade)66",
                           color: "var(--accent-text, #04bade)" }}>
            {`{${w.name}}`}
          </button>
        ))}
        {BUILTINS.map(([token, why]) => (
          <button key={token} type="button" disabled={disabled} title={why}
                  onClick={() => onInsert(token)} style={chip}>
            {token}
          </button>
        ))}
      </div>
      <div className="muted small" style={{ marginTop: 4 }}>
        {wired.length
          ? "Click to insert. The highlighted ones come from the rules wired "
            + "into this node - wire another in and it appears here."
          : "Wire a rule into this node and its value appears here as a "
            + "placeholder you can drop into the message."}
      </div>
    </div>
  );
}

/** Text inserted where the caret is, or appended when there is no caret. */
function insertAtCaret(el, current, text) {
  const value = String(current || "");
  if (!el || typeof el.selectionStart !== "number") {
    return value + (value && !value.endsWith(" ") ? " " : "") + text;
  }
  const { selectionStart: a, selectionEnd: b } = el;
  const next = value.slice(0, a) + text + value.slice(b);
  // Put the caret after what was just inserted, on the next paint -- React
  // rewrites the box's value first, and setting it before that is undone.
  requestAnimationFrame(() => {
    try {
      el.focus();
      el.setSelectionRange(a + text.length, a + text.length);
    } catch { /* the inspector moved on */ }
  });
  return next;
}

/** The columns a Query may read, as buttons that write themselves in.
 *
 *  A name typed from memory is the commonest way a query returns nothing with
 *  no visible reason -- and the row stamp is the one nobody guesses right,
 *  because "the time column" is called `ts` here and people write `Datetime`.
 */
function QueryColumns({ cols, editable, onInsert }) {
  if (!cols.length) return null;
  const tables = [...new Set(cols.map((c) => c.table))];
  return (
    <div style={{ margin: "-2px 0 10px" }}>
      {tables.map((t) => (
        <div key={t} style={{ marginTop: 6 }}>
          {/* The name to TYPE, and after it the name on the node -- they are
              not the same string and the difference is what makes a query
              fail with "relation does not exist". A display name may carry
              spaces, dashes and capitals; a table name may not. */}
          <button type="button" className="mono" disabled={!editable}
                  title="the table's name in SQL - click to write it in"
                  onClick={() => onInsert(t)}
                  style={{ fontSize: 11, padding: 0, border: 0, cursor: "pointer",
                           background: "transparent", color: "inherit",
                           fontWeight: 650 }}>
            {t}
          </button>
          {cols.find((c) => c.table === t)?.title
            && cols.find((c) => c.table === t).title !== t && (
            <span className="muted small"> · shown as {cols.find((c) => c.table === t).title}</span>
          )}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
            {cols.filter((c) => c.table === t).map((c) => (
              <button key={c.name} type="button" className="chip mono"
                      disabled={!editable}
                      title={c.note ? `${c.name} - ${c.note}` : c.name}
                      onClick={() => onInsert(c.name)}
                      style={{ fontSize: 11, padding: "2px 7px", borderRadius: 999,
                               border: "1px solid rgba(128,128,128,.35)",
                               background: "transparent", cursor: "pointer",
                               color: "inherit" }}>
                {c.name}
              </button>
            ))}
          </div>
        </div>
      ))}
      <p className="muted small" style={{ marginTop: 6 }}>
        Click a column to write it in. Times are PostgreSQL, so the hour of a
        row is <code>extract(hour from ts)</code>.
      </p>
    </div>
  );
}

/** Run the statement here and show what comes back.
 *
 *  The same endpoint the Database page's console uses, so it is the same
 *  read-only role against the same scoped views with the same row cap -- this
 *  box only saves the walk to another page to find out whether the statement
 *  was even valid.
 */
function QueryRunner({ sql }) {
  const [state, setState] = useState(null);   // {busy|error|columns,rows}
  const text = String(sql || "").trim();
  async function run() {
    setState({ busy: true });
    try {
      const d = await api.post("/api/datasets/sql", { sql: text });
      setState(d?.ok ? { columns: d.columns || [], rows: d.rows || [],
                         truncated: d.truncated, bound: d.bound }
                     : { error: d?.error || "the query did not run" });
    } catch (e) {
      setState({ error: String(e?.message || e) });
    }
  }
  const rows = state?.rows || [];
  return (
    <div style={{ marginBottom: 10 }}>
      <button type="button" className="btn" disabled={!text || state?.busy}
              onClick={run}>
        {state?.busy ? "Running…" : "Run and preview"}
      </button>
      {state?.error && (
        <p className="small c-danger" style={{ marginTop: 6,
                                      whiteSpace: "pre-wrap" }}>
          {state.error}
        </p>
      )}
      {/* A statement written for a button is incomplete on its own -- the
          period arrives when somebody taps it. Saying which window this
          preview used stops a correct query from reading as a wrong one. */}
      {state?.bound && (
        <p className="muted small" style={{ marginTop: 6 }}>
          Takes a period - {state.bound}. Each button supplies its own.
        </p>
      )}
      {state?.columns && (
        rows.length ? (
          <div style={{ marginTop: 6, maxHeight: 190, overflow: "auto",
                        border: "1px solid rgba(128,128,128,.25)",
                        borderRadius: 7 }}>
            <table className="mono" style={{ fontSize: 11, borderCollapse: "collapse",
                                             width: "100%" }}>
              <thead>
                <tr>
                  {state.columns.map((c) => (
                    <th key={c} style={{ textAlign: "left", padding: "3px 6px",
                                         position: "sticky", top: 0,
                                         background: "var(--card, #fff)",
                                         borderBottom: "1px solid rgba(128,128,128,.25)" }}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 20).map((r, i) => (
                  <tr key={i}>
                    {state.columns.map((c) => (
                      <td key={c} style={{ padding: "3px 6px", whiteSpace: "nowrap" }}>
                        {r[c] === null || r[c] === undefined ? "" : String(r[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted small" style={{ marginTop: 6 }}>
            It ran, and returned no rows.
          </p>
        )
      )}
      {state?.columns && rows.length > 20 && (
        <p className="muted small" style={{ marginTop: 4 }}>
          First 20 of {rows.length}{state.truncated ? "+" : ""} rows.
        </p>
      )}
    </div>
  );
}

// The Modbus Write's pointer to its server's Drive watchdog switch (Tok Aba
// 2026-10-07). A motor drive with a Modbus comms watchdog (Cytron's WDT_MS)
// resets its speed to 0 when no request arrives in time, so one write of 1000
// runs the motor only for a moment. The fix lives on the CONNECTION
// (ModbusServerEditor.jsx, DriveWatchdog), not on each node, so the node only
// says where it is -- and, when the switch is on, what that does here.
function ModbusWatchdogNote({ server }) {
  const servers = useModbusServers();
  const conn = (servers?.clients || []).find((c) => c.id === server);
  if (conn?.watchdog === "on") {
    const ms = Number(conn.watchdog_ms) || 1000;
    return (
      <p className="muted small" data-watchdog-note="on">
        This server&apos;s Drive watchdog is on ({ms} ms): this node keeps
        sending its last value every {ms / 2000} s, and the motor stops by
        itself if the box or the cable fails.
      </p>
    );
  }
  return (
    <p className="muted small" data-watchdog-note="">
      A drive that stops a moment after each write has a Modbus watchdog: set
      it under the server&apos;s Drive watchdog (the pencil beside Server).
    </p>
  );
}

function Field({ label, hint, children, style }) {
  // Same shape as the shared Field: the hint is a hover mark on the label, not
  // a paragraph under the input. The inspector rail is the narrowest column on
  // the product, so a wrapped sentence per field cost several rows each.
  //
  // Spacing lives in CSS (.wf-field), not here: every node's form is built
  // from this one component, so the rhythm between label and control is one
  // decision made once rather than an inline number per field.
  return (
    <label className="wf-field" style={style}>
      <span className="wf-field-label">
        {label}
        {hint ? <InfoHint label={`About ${label}`}>{hint}</InfoHint> : null}
      </span>
      {children}
    </label>
  );
}

// `active` = the classes the AI Models keep-filter currently lets through.
// Everything the model CAN emit is listed either way: hiding the rest made a
// board that keeps one class look like a one-class model, with no way to write
// a rule for another. The excluded ones are marked instead, because a rule on
// one of them can never fire until it is ticked on the AI Models page.
function ClassPicker({ classes, value, editable, onChange, label, active }) {
  // A fresh system with no AI model has no classes to offer -- an empty
  // checkbox list reads as broken, so say what is missing and where.
  if (!classes.length) {
    return (
      <Field label={label || "Object classes"}>
        <p className="muted small" style={{ margin: 0 }}>
          No AI model chosen yet - pick one in <strong>AI Models</strong> and
          its classes appear here.
        </p>
      </Field>
    );
  }
  const [q, setQ] = useState("");
  const off = active ? classes.filter((c) => !active.includes(c)) : [];
  // Only what the model can actually DETECT. A class switched off in AI
  // Models can never fire a rule, so offering it is offering a choice that
  // does nothing -- and on a typical box that was 79 rows of nothing to
  // scroll past to reach the one that works. A class already CHOSEN stays
  // listed even if it was switched off later, or a saved rule would quietly
  // lose the setting it is running on with no way to see it.
  const offerable = active
    ? classes.filter((c) => active.includes(c) || value.includes(c))
    : classes;
  const shown = offerable.filter((c) => !q || c.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <Field label={label
        || `Object classes (${value.length ? value.length + " chosen" : "none = all"})`}>
        <input className="input" placeholder="filter…" value={q} onChange={(e) => setQ(e.target.value)} />
      </Field>
      <div style={{ maxHeight: 220, overflowY: "auto", border: "1px solid rgba(128,128,128,.2)",
                    borderRadius: 7, padding: 6, marginBottom: 8 }}>
        {shown.map((c) => {
          const filtered = active ? !active.includes(c) : false;
          return (
            <label key={c} title={filtered
              ? "Switched off by 'Classes to keep' on the AI Models page - "
                + "a rule on it will not fire until you tick it there"
              : undefined}
                   style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12,
                            padding: "2px 4px", opacity: filtered ? 0.55 : 1 }}>
              <input type="checkbox" disabled={!editable} checked={value.includes(c)}
                     onChange={(e) => onChange(e.target.checked
                       ? [...value, c] : value.filter((x) => x !== c))} />
              {c}
              {filtered && (
                <span className="ui-badge ui-badge-warn rail-pill">not detected</span>
              )}
            </label>
          );
        })}
        {!shown.length && (
          <p className="muted small">
            {q ? "no matching class" : "no class is switched on to detect"}
          </p>
        )}
      </div>
      {/* The hidden ones are still ACCOUNTED for. Silently showing one class
          out of eighty would read as a broken model rather than as a
          deliberate setting, and this is the sentence that says where the
          other seventy-nine went and how to get one back. */}
      {!!off.length && !q && (
        <p className="muted small" style={{ marginTop: -4, marginBottom: 8 }}>
          Showing the {classes.length - off.length} class
          {classes.length - off.length === 1 ? "" : "es"} this box detects.
          The other {off.length} are switched off in{" "}
          <Link to="/models">AI Models</Link> → Classes to keep.
        </p>
      )}
    </>
  );
}

function TriggerEditor({ spec, mode, trigger, editable, onChange }) {
  // Mode-aware: a multi-mode node's trigger can only name a metric the
  // CURRENT mode publishes (a countdown has remaining_s, not live_s).
  const metrics = metricsForSpec(spec, mode);
  const on = !!trigger;
  return (
    <div className="wf-fieldset">
      <label className="muted small"
             style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
        <input type="checkbox" checked={on} disabled={!editable}
               onChange={(e) => onChange(e.target.checked
                 ? { metric: metrics[0], op: ">", value: 1, severity: "warn",
                     message: "", cooldown_s: 30 }
                 : null)} />
        <strong>Trigger</strong> - raise an event when the value passes a threshold
      </label>
      {on && (
        <div style={{ marginTop: 6, minWidth: 0 }}>
          {/* metric / operator / value. Each carries a flex BASIS rather than a
              fixed width, so at the rail's minimum width the row wraps onto a
              second line instead of pushing the value box out of the card. */}
          <div className="wf-row">
            <select className="input" style={{ flex: "1 1 110px" }}
                    value={trigger.metric || metrics[0]} disabled={!editable}
                    onChange={(e) => onChange({ ...trigger, metric: e.target.value })}>
              {metrics.map((m) => <option key={m} value={m}>{metricLabel(m)}</option>)}
            </select>
            <select className="input" style={{ flex: "0 0 62px" }} value={trigger.op || ">"} disabled={!editable}
                    onChange={(e) => onChange({ ...trigger, op: e.target.value })}>
              {[">", ">=", "<", "<=", "==", "!="].map((o) => <option key={o}>{o}</option>)}
            </select>
            <input className="input" type="number" style={{ flex: "1 1 74px" }} value={trigger.value ?? 1}
                   disabled={!editable}
                   onChange={(e) => onChange({ ...trigger, value: +e.target.value })} />
          </div>
          <div className="wf-row">
            <select className="input" style={{ flex: "0 0 84px" }} value={trigger.severity || "warn"}
                    disabled={!editable}
                    onChange={(e) => onChange({ ...trigger, severity: e.target.value })}>
              <option value="info">info</option><option value="warn">warn</option><option value="crit">crit</option>
            </select>
            <input className="input" style={{ flex: "1 1 100px" }} placeholder="message"
                   value={trigger.message || ""} disabled={!editable}
                   onChange={(e) => onChange({ ...trigger, message: e.target.value })} />
          </div>
          <label className="muted small" style={{ display: "block", marginTop: 6 }}>
            <span style={{ display: "block", marginBottom: 3 }}>cooldown s</span>
            <input className="input" type="number" style={{ maxWidth: 100 }}
                   value={trigger.cooldown_s ?? 30} disabled={!editable}
                   onChange={(e) => onChange({ ...trigger, cooldown_s: +e.target.value })} />
          </label>
        </div>
      )}
    </div>
  );
}

