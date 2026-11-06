// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Client-side display config for workflow node types + the edge-validation
// mirror. The AUTHORITATIVE registry (types, ranks, data defaults) comes from
// GET /api/workflow - this file only adds what the server doesn't care about:
// colours, icons, and instant client-side connection checks so an invalid wire
// is refused before a round-trip. The server re-validates on save regardless.
import {
  Video, Filter, Shapes, Activity, LayoutDashboard, Zap, Variable, StickyNote,
  GitBranch, Database, Cpu, Sigma, Clock, MessagesSquare,
} from "lucide-react";

// Each category has its own GRADIENT, and the gradients are grouped by STAGE:
// common, input, processing, logic, storage, visualisation, actions. Hue
// tells you which stage a node belongs to -- slate for the canvas tools,
// blue takes data in, greens measure, oranges support, and the three singles
// are the three places a pipeline ends -- while the shade within that hue
// tells the categories apart.
//
// A gradient rather than a flat colour because a category needs to be
// distinguishable from its neighbours (Class Filters from Zones) AND read as
// the same family at a glance; two related stops do both, where eleven flat
// hues did neither.
//
// `color` is the solid the gradient starts from, for the places that cannot
// take one: a handle dot, a 1px border, an icon.
//: The stages a graph moves through, in order, each with the base colour its
//: categories are shades of. The palette groups its drawers under these, so
//: the shape of a pipeline is visible before any node is read. Common is not
//: a step of the pipeline but the canvas tools (comments, link jumps, the
//: debug probe), and it heads the list because that is where a Node-RED user
//: looks for them (Tok Aba 2026-10-05: its own heading, above Input). Slate,
//: not orange: under its own heading an orange would read as more Logic.
export const STAGES = [
  { id: "common",        title: "Common",        base: "#64748B" },
  { id: "input",         title: "Input",         base: "#2563EB" },
  { id: "processing",    title: "Processing",    base: "#16A34A" },
  { id: "logic",         title: "Logic",         base: "#EA580C" },
  { id: "storage",       title: "Storage",       base: "#7C3AED" },
  { id: "visualization", title: "Visualization", base: "#CA8A04" },
  { id: "actions",       title: "Actions",       base: "#DC2626" },
];

export const CATEGORY_STYLE = {
  // -- common --------------------------------------------------------------
  // Canvas furniture: comments, jump wires, probes. Things that help you read
  // and wire a graph rather than compute anything in it.
  common:    { color: "#94A3B8", stage: "common",
               icon: StickyNote,      title: "Common" },
  // -- input ---------------------------------------------------------------
  source:    { color: "#3B82F6", stage: "input",
               icon: Video,           title: "Source" },
  // A filter narrows what comes IN -- it measures nothing, it decides which
  // detections are even looked at. Two headings, because "what am I looking
  // at" is one question with two halves: which CLASSES (Class Filter) and
  // which PLACE (Zone Filter, and the zones themselves). The zones used to be
  // listed three drawers down beside the functions that measure them, which
  // put the two halves of one decision in two places. A lighter blue than the
  // Source it sits behind, because it is the same stream with less in it.
  filter:    { color: "#38BDF8", stage: "input",
               icon: Filter,          title: "Filters" },
  // A PALETTE-only category: the box's own terminal block. Its nodes' WIRING
  // categories differ (an input is a variable, an output is an output), but
  // an installer looking for "the DI0 terminal" is asking one question and
  // must not have to know which half of the graph the answer lives in.
  // Input-stage blue, because the wire in the cabinet is where this pipeline
  // starts as often as the camera is.
  board:     { color: "#60A5FA", stage: "input",
               icon: Cpu,             title: "Board I/O" },
  // The CLOCK. A palette-only category (its node wires as a variable): a
  // schedule is where a pipeline starts as surely as a camera is, so it sits
  // in the input stage -- cyan, so "the time came round" never reads as one
  // more camera.
  time:      { color: "#06B6D4", stage: "input",
               icon: Clock,           title: "Schedule" },
  // -- processing ----------------------------------------------------------
  // WIRING-only: "analytics" is the server's category for everything the
  // pipeline computes, and it is what a node is coloured by when it declares
  // no palette of its own. It is deliberately absent from PALETTE_ORDER --
  // its nodes are filed under AI Processing and Math, and the drawer was
  // down to a single node nobody would think to look in it for.
  analytics: { color: "#10B981", stage: "processing",
               icon: Activity,        title: "Functions" },
  // What the box works out from what it sees, in one drawer under two
  // headings: Zones (measured at a PLACE) and Objects (measured on what is
  // in frame). They were two drawers, which split the counting nodes -- the
  // Counter takes a zone port and sat under Objects, the Line Counter is a
  // counter and sat under Zones. One drawer, so "what can this box measure"
  // is one list.
  zone:      { color: "#22C55E", stage: "processing",
               icon: Shapes,          title: "AI Processing" },
  // A PALETTE-only category: the Math family. It computes rather than
  // measures, so it is not a Function -- but it works on what the Functions
  // produce and feeds what comes after, which is why it sits between the
  // greens that measure and the oranges that decide. Teal reads as "still
  // processing" beside both without claiming to be either.
  math:      { color: "#14B8A6", stage: "processing",
               icon: Sigma,           title: "Math" },
  // -- logic ---------------------------------------------------------------
  // "Control", not "Conditions": the drawer holds everything that decides
  // WHETHER and WHEN the rest of the graph runs -- Compare, Switch, Delay,
  // Catch today; a Loop or Retry would belong here too, which "Conditions"
  // could not have said.
  logic:     { color: "#F97316", stage: "logic",
               icon: GitBranch,       title: "Control" },
  variable:  { color: "#F59E0B", stage: "logic",
               icon: Variable,        title: "Variables" },
  // -- storage -------------------------------------------------------------
  // A PALETTE-only category: the Data Table node's WIRING category is
  // "output" (it is a sink -- nothing comes back out of it), but recording to
  // the database is not an output the way a siren or an email is. It is where
  // this system's own history comes from.
  database:  { color: "#8B5CF6", stage: "storage",
               icon: Database,        title: "Database" },
  // -- visualisation -------------------------------------------------------
  dashboard: { color: "#EAB308", stage: "visualization",
               icon: LayoutDashboard, title: "Dashboard & Analytics" },
  // -- actions -------------------------------------------------------------
  output:    { color: "#EF4444", stage: "actions",
               icon: Zap,             title: "Outputs" },
  // Messaging is an action, but not the same KIND of action. A siren, a relay
  // and a snapshot happen ON the box; an email or a Telegram message leaves
  // it, goes to a person, and -- once commands exist -- gets answered. That
  // two-way relationship with somebody outside is what earns its own drawer,
  // and a lighter red than Outputs says "same stage, different job". The
  // protocol families (MQTT, HTTP, API, Modbus) live here too, each with its
  // reading and its sending half together -- which is why the External
  // Values drawer that held the reading halves is gone.
  integration: { color: "#F87171", stage: "actions",
               icon: MessagesSquare,  title: "Integration" },
};

// The BRANCH family: which first-match shape each node is, and the fixed
// names of its ways out where it has them. Mirrors branch_ctype() and the
// registry's "ctype"/"ports" in core/services/workflow.py -- the shape is the
// node you dragged, not a picker inside it.
//
// The invariant every one of them keeps, and the reason these ports are safe
// to wire anywhere a number goes: exactly ONE output is 1 at any moment and
// every other is 0. First match wins, top to bottom; the last port is the
// catch-all.
export const BRANCH_CTYPE = {
  "branch.compare": "compare",
  "branch.cases": "compare",
  "branch.range": "range",
  "branch.match": "switch",
  "branch.multi": "multi",
};

export const BRANCH_PORTS = {
  "branch.compare": ["True", "False"],
  "branch.multi": ["Pass", "Fail"],
};

//: What a published metric is CALLED on the node's own output port. The node
//: face and the Debug panel read the same map, because a number under two
//: names is two numbers to whoever is reading: the canvas said "Inside" and
//: Debug said "net", of the same tally.
export const PORT_LABELS = {
  in: "In", out: "Out", net: "Inside", cross: "Crossings",
  total_in: "Total in", total_out: "Total out", total_net: "Inside (total)",
  total_cross: "Crossings (total)",
};

/** What a camera is CALLED on the canvas: its SLOT, not its hardware.
 *
 *  A branch points at a camera by id, which is stable across a swap -- but it
 *  used to be DRAWN with the device's name ("Arducam B0499 (USB3 1.58MP)"),
 *  so replacing the camera in slot 2 left every node on the canvas naming a
 *  camera that is no longer plugged in, and every reader wondering whether the
 *  rule still applied. The slot is the thing the rule is actually about: the
 *  picture from that position, whatever hardware is making it today. Camera
 *  Setup labels its rail the same way, which is what ties the two together.
 *
 *  The number is the camera's place in the system's list, so it counts up to
 *  however many cameras exist -- four at most on this box.
 */
export function camLabel(cameras, id) {
  const list = cameras || [];
  const i = list.findIndex((c) => String(c.id) === String(id));
  // An id no camera answers to is a camera that was removed under the rule.
  // Naming it rather than silently blanking it is what makes that fixable.
  return i < 0 ? String(id || "") : `Camera ${i + 1}`;
}

/** Whether this type publishes numbered branch ports (the family, or the
 *  deprecated Switch a saved canvas may still hold). */
export function isBranch(type) {
  return type === "logic.switch" || type in BRANCH_CTYPE;
}

/** Which shape a branch node compiles as -- fixed by type for the family,
 *  read from `data.ctype` only for the Switch it replaced. */
export function branchCtype(type, data) {
  return BRANCH_CTYPE[type] || String((data || {}).ctype || "switch");
}

// Mirror of core/services/workflow.py _ALLOWED_TARGETS.
const ALLOWED_TARGETS = {
  // A Link in may feed anything a real value feeds - after the jump is
  // resolved it IS whatever fed its Link out. Per-type rules below stop a
  // Comment being wired at all.
  common: ["common", "filter", "zone", "analytics", "logic", "output",
           "dashboard", "variable"],
  source: ["filter", "zone", "analytics", "common"],
  filter: ["filter", "zone", "analytics", "common"],
  zone: ["analytics", "common"],
  // analytics -> variable: a rule's output can be wired INTO a Variable,
  // which then holds it (holder kind only - see the per-type rule below).
  analytics: ["dashboard", "output", "analytics", "logic", "common", "variable"],
  variable: ["dashboard", "output", "analytics", "logic", "common", "variable"],
  // A When condition sits between a value and what reacts to it; logic→logic
  // so a Delay can follow a When.
  // logic -> analytics/variable: a Switch BRANCH is a value (1 while it
  // holds), so it may feed a Formula, a Running Total or a Variable.
  logic: ["output", "dashboard", "logic", "common", "analytics", "variable"],
  // A Query reads the tables wired into it. Its result goes to a card, or
  // into a Variable - which is how a number only the database knows ("rejects
  // this shift") becomes something a formula, a When and an alert can read.
  // Kept to the HOLDER kind of variable by the per-type rule below, like
  // every other value.
  query: ["dashboard", "common", "variable"],
  dashboard: [],
  // An Alert is the trigger; what happens because of one is wired AFTER it.
  // Kept to the Alert by the per-type rule in edgeAllowed -- no other output
  // publishes anything to bind to.
  // ...and into analytics, so the LOGIC after an alarm lives in a Formula
  // instead of being rebuilt as a second condition. An Alert reaching a
  // Formula stands for its rule's `active`, 1 while the condition holds.
  output: ["output", "dashboard", "common", "analytics"],
};

// DEPRECATED, kept so an un-migrated canvas still wires: the two OUTPUT nodes
// that stood for the rule behind them rather than for themselves. A Condition
// defined its trigger, an Alert decorated it. Their replacement -- Branch +
// Trigger -- publishes real ports instead, so nothing new joins this list.
// Mirrors _TRANSPARENT_OUTPUTS in core/services/workflow.py.
const TRANSPARENT_OUT = ["output.alert", "output.event"];

// Every node that TAKES a value on an ordinary input: the Expression, a
// Running Total, the old Formula, and each node of the Math family. The Math
// nodes are read off the server registry (`math`) rather than listed here --
// there are thirteen of them, and a hand-kept copy is a list that goes stale
// the first time one is added. Mirrors VALUE_SINKS in
// core/services/workflow.py.
const VALUE_SINK_TYPES = ["analytics.expr", "analytics.formula",
                          "analytics.total"];

export function isValueSink(registry, type) {
  return VALUE_SINK_TYPES.includes(type) || !!(registry[type] || {}).math;
}

// The port a Math node (and the Formula it grew out of) offers for the FLOW
// rather than for a number: "run this here". A condition wired to it still
// gates the arithmetic, and anything else is sequence alone -- which is how a
// calculation says where in a pipeline it belongs. Mirrors RUN_HANDLE in
// core/services/workflow.py.
export const RUN_HANDLE = "when";

export function takesRunWire(registry, type) {
  return type === "analytics.formula" || !!(registry[type] || {}).math;
}

/** (ok, reason) for one prospective edge, given the server registry. */
// The three nodes that bracket a function rather than run in one. Mirrors
// FUNCTION_NODES in core/services/workflow.py.
export const FUNCTION_NODES = ["function.start", "function.end", "function.call"];

export function edgeAllowed(registry, srcType, tgtType, tgtHandle) {
  const s = registry[srcType], t = registry[tgtType];
  if (!s || !t) return [false, "unknown node type"];
  // A Comment takes no wires at all, including from a probe -- asked first so
  // the probe rule below cannot open a hole in it.
  if ([srcType, tgtType].includes("note.comment")) {
    return [false, "a Comment is a note on the canvas - it takes no wires"];
  }
  // A PROBE MAY WATCH ANYTHING, and nothing may read one. Mirrors the server,
  // which asks the same two questions in the same order and before the same
  // rules: the pipeline-order rules below all ask whether a value may flow
  // from here to there, and nothing flows into a probe -- it is stripped
  // before the graph compiles. Both ends must agree or the wire draws on the
  // canvas and is then refused on save, which reads as the save being broken.
  if (srcType === "common.debug") {
    return [false, `${s.label} takes no wire on that side`];
  }
  if (tgtType === "common.debug") return [true, ""];
  // A function BOUNDARY takes and gives anything: it is not a stage in the
  // pipeline, it is a bracket around one. Every rule below asks whether a node
  // of this rank may meet a node of that rank, and by the time the compiler
  // asks it these three are gone -- resolve_functions() has replaced each Call
  // with a copy of the body, and the body's own wires are what get validated.
  // Mirrors the server, which allows exactly the same wires.
  if (FUNCTION_NODES.includes(srcType) || FUNCTION_NODES.includes(tgtType)) {
    if (["note.comment", "common.debug"].includes(srcType)) return [false, ""];
    if (tgtType === "function.start") {
      return [false, "a Function Start receives nothing - wire the value into the Call instead"];
    }
    if (srcType === "function.end") {
      return [false, "a Function End gives nothing on - the value comes out of the Call"];
    }
    return [true, ""];
  }
  // The RUN port takes anything: every rule below asks whether this is a
  // number fit for arithmetic, which is the right question for an operand and
  // the wrong one here. Mirrors the server, which allows the same wire.
  if (String(tgtHandle || "") === RUN_HANDLE && takesRunWire(registry, tgtType)
      && ![srcType, tgtType].includes("note.comment")
      && srcType !== "common.debug") {
    return [true, ""];
  }
  // A Trigger's ZONE port: which area it recolours while it holds. Mirrors
  // the server -- a zone reaches nothing else in the output stage, because
  // it is a place rather than a value, and this port asks for a place.
  if (String(tgtHandle || "") === "zone" && tgtType === "output.trigger"
      && srcType === "zone.ref") {
    return [true, ""];
  }
  // ...and its duplicate guard ports: what a Data Table already holds for
  // the object (Current OID, Current value), off a column's read port.
  // Mirrors the server.
  if (["cur_oid", "cur_value"].includes(String(tgtHandle || ""))
      && tgtType === "output.trigger" && srcType === "output.dataset") {
    return [true, ""];
  }
  // Alert behaves like When for wiring: it defines the function's trigger, so
  // a card after it binds to that function's alert state (mirrors the server).
  // ...and a Dataset takes that same alert as its "write a row now" signal,
  // the one output that legitimately sits behind another.
  if (TRANSPARENT_OUT.includes(srcType)
      && (t.category === "dashboard" || tgtType === "output.dataset")) {
    return [true, ""];
  }
  // The Trigger IS the moment, so what reacts to one sits after it: a card, a
  // Data Table row, a Snapshot, a Publish, or arithmetic on the rule it named.
  // It keeps the wiring category "output" (that is what the pipeline rules
  // reason about, and moving it would change which nodes may legally meet on
  // every graph already saved), so its reach is named here.
  if (srcType === "output.trigger"
      && (["dashboard", "output"].includes(t.category)
          || isValueSink(registry, tgtType))) {
    return [true, ""];
  }
  // An Alert into a Formula: the logic that follows an alarm belongs where
  // logic is written, and the alert arrives as its rule's `active` (1 while
  // the condition holds).
  if (TRANSPARENT_OUT.includes(srcType) && isValueSink(registry, tgtType)) {
    return [true, ""];
  }
  // A Data Table is both a sink and a source: every column has a write port
  // and a read port, and the read side feeds a card.
  if (srcType === "output.dataset" && t.category === "dashboard") {
    return [true, ""];
  }
  // ...and a Math input: the column's recorded rows, reduced the way a card
  // reduces them (total, average, latest... over a period), as a number the
  // calculation reads. Mirrors the server.
  if (srcType === "output.dataset" && tgtType.startsWith("math.")) {
    return [true, ""];
  }
  // A Card or Chart asking RECORDED data, into a Chart: the one wire between
  // two dashboard nodes. Such a card (recordedMode set) states a QUESTION about
  // recorded events or a table, and a chart drawing several series has no
  // other way to be handed one. Allowed by type, as on the server: only a
  // card in that mode draws the output port the wire starts from. Kept to
  // the Chart -- every other card shows a single value. The retired Event
  // Card is the same node under its old name.
  if (["dashboard.events", "dashboard.card", "dashboard.graph"].includes(srcType)
      && tgtType === "dashboard.graph") {
    return [true, ""];
  }
  // ...and into a Query, which is how a table is named as readable: the wire
  // says which tables the statement may see.
  if (srcType === "output.dataset" && tgtType === "storage.query") {
    return [true, ""];
  }
  // A Query's rows become a file HERE, and the file goes to a message. Both
  // halves are named explicitly: the shared "query" category would otherwise
  // let a Query feed a Query and a CSV feed a card, neither of which means
  // anything. The wire carries DATA, never a trigger, so a message fed by a
  // CSV still needs a Schedule or an Alert to say when it goes.
  if (srcType === "storage.query" && tgtType === "storage.csv") {
    return [true, ""];
  }
  // A Query answers a BUTTON on a Command node - same shape as the CSV wire,
  // and the wire is what says which button runs which statement.
  if (srcType === "storage.query" && tgtType === "integration.command") {
    return [true, ""];
  }
  // ...and a Snapshots node answers one the same way: two kinds of answer,
  // one kind of wire.
  if (srcType === "integration.photos" && tgtType === "integration.command") {
    return [true, ""];
  }
  if (tgtType === "integration.photos") {
    return [false, `${s.label} has nothing to add to a Snapshots node - it `
      + "reads the pictures this system already saved"];
  }
  if (srcType === "integration.photos") {
    return [false, "Snapshots answers a command button - wire it to the "
      + "button that should send the pictures"];
  }
  if (tgtType === "integration.command") {
    return [false, `${s.label} cannot answer a command - wire a Query into `
      + "the button you want it to answer"];
  }
  if (srcType === "integration.command") {
    return [false, "a Command is answered where it is asked, in the chat - "
      + "it feeds nothing on the canvas"];
  }
  // A Watch reads a Query on a timer and sends through a message node. Both
  // halves are named explicitly, for the same reason the Command wires are:
  // the message node is an OUTPUT, and output-to-output means nothing
  // anywhere else on the canvas.
  if (srcType === "storage.query" && tgtType === "integration.watch") {
    return [true, ""];
  }
  if (tgtType === "integration.watch") {
    return [false, `${s.label} is not something to watch - wire a Query in, `
      + "and the Recorded Trigger runs it on a timer"];
  }
  if (srcType === "integration.watch") {
    if (["output.email", "output.telegram"].includes(tgtType)) return [true, ""];
    return [false, "a Recorded Trigger sends a message when its number crosses - wire it "
      + "into a Telegram or an Email node"];
  }
  if (srcType === "storage.csv") {
    if (["output.email", "output.telegram"].includes(tgtType)) return [true, ""];
    return [false, `a CSV file attaches to an Email or a Telegram message - `
      + `${t.label} cannot carry one`];
  }
  if (tgtType === "storage.csv") {
    return [false, `${s.label} has no rows to write - a CSV file is made from `
      + "a Query"];
  }
  // A PRE-PROCESS works on the PICTURE, before the detector runs, so the only
  // thing that may feed one is the stream whose camera it adjusts: a Class
  // Filter or a Zone wired in would read as "filter the detections, then
  // change the image they came from", which is backwards in time. What comes
  // AFTER is unrestricted -- its category is "filter", so it feeds a filter,
  // a zone or a rule like any other narrowing of the stream. Mirrors the
  // server.
  //
  // Two in a row is refused so the wire is not drawable at all: the steps
  // inside one node are an ordered list the operator arranged, and a second
  // node would be a second list with no visible order between the two.
  if (srcType === "filter.preprocess" && tgtType === "filter.preprocess") {
    return [false, "one Pre-process per camera - add the extra steps to the "
      + "chain on the first one, where their order is visible"];
  }
  // A Link out is allowed in because it IS whatever fed its Link in: jumps
  // are how a graph with several cameras keeps its wires readable.
  // A Detection Stream in Snapshot mode takes a SNAP input: an Inject, a
  // Trigger or a sensor wired in says WHEN to detect. Only that one port of a
  // source opens; every other stays closed. Mirrors edge_allowed on the server
  // (SNAP_HANDLE) and sits before the same rules, or the wire would draw here
  // and be refused on save.
  if (tgtType === "source.detection" && String(tgtHandle || "") === "snap") {
    if (["common.inject", "output.trigger"].includes(srcType)
        || ["variable", "logic"].includes(s.category)) return [true, ""];
    return [false, `${s.label} cannot start a snapshot - wire an Inject, a `
      + "Trigger or a sensor into the Detection Stream's snap input"];
  }
  if (tgtType === "filter.preprocess" && srcType !== "source.detection"
      && s.category !== "common") {
    return [false, `${s.label} cannot feed a Pre-process - it adjusts the `
      + "picture BEFORE anything is detected, so wire the Detection Stream "
      + "of the camera it should apply to"];
  }
  // Mirrors the server's per-type rules.
  if ([srcType, tgtType].includes("note.comment")) {
    return [false, "a Comment is a note on the canvas - it takes no wires"];
  }
  if (tgtType === "common.link_in" || srcType === "common.debug") {
    return [false, `${srcType === "common.debug" ? s.label : t.label} takes no wire on that side`];
  }
  // variable.ref may feed anything a value feeds: wired, it publishes a real
  // variable (it stores); unwired misuse is caught at compile on the sink.
  // Only the holder kind of variable takes an input; a constant or an API
  // reading makes its own value.
  // ...except a Modbus Read's TRIGGER port (interval 0): what arrives there
  // is WHEN to read, not a value, so an Inject, a Schedule or a rule may.
  // Mirrors the server's edge_allowed.
  if (tgtType === "variable.rs485" && String(tgtHandle || "") === "trig"
      && ["variable", "analytics", "logic"].includes(s.category)) {
    return [true, ""];
  }
  if (t.category === "variable" && tgtType !== "variable.ref") {
    return [false, `${t.label} makes its own value - only a Variable (the holder) takes a wire in`];
  }
  if (srcType === "variable.ref" && tgtType === "variable.ref") {
    return [false, "a Variable into a Variable stores nothing new - wire the rule itself into the second one"];
  }
  // A branch is a DECISION, not a reading. Storing "which way it went" keeps
  // the verdict and loses the number it was about, so the Variable publishes
  // a 1 that nothing can be recomputed from. Mirrors the server.
  if (isBranch(srcType) && tgtType === "variable.ref") {
    return [false, `${s.label} is a decision, not a value to keep - wire the `
      + "number it tests into the Variable and branch on that, or wire this "
      + "port into a Trigger"];
  }
  if (!(ALLOWED_TARGETS[s.category] || []).includes(t.category)) {
    return [false, `${s.label} cannot connect to ${t.label} - the pipeline flows ` +
                   "source → filter → zone → analytics → dashboard/output"];
  }
  // The whole-frame counters count the WHOLE frame; a Zone wired in would be
  // silently ignored, which reads as a broken node rather than a wrong wire.
  if (s.category === "zone"
      && ["analytics.frame", "analytics.frame_count",
          "analytics.frame_total"].includes(tgtType)) {
    return [false, `${t.label} counts the whole frame - wire the Detection ` +
                   "Stream (through a Class Filter) instead of a Zone"];
  }
  // Only the Alert may be followed. Every other output DOES something and
  // publishes no value, so a node after one would have nothing to bind to.
  if (s.category === "output" && !TRANSPARENT_OUT.includes(srcType)) {
    return [false, `${s.label} is what happens when a rule fires, not `
      + "something to fire on - wire this after the Condition instead"];
  }
  // Every other output still reaches nothing analytical: an Email publishes
  // no value, so a Formula behind one would have nothing to read.
  if (s.category === "output" && t.category === "analytics") {
    return [false, `${s.label} publishes no value to calculate with - ` +
                   "only a Condition can feed a Formula"];
  }
  // A zone or a line is a PLACE, not a quantity. It reaches a measuring node
  // on that node's zone port, where it says WHERE to measure -- but arithmetic
  // on one has no meaning, and the wire would compile to a plausible-looking
  // wrong number rather than to nothing.
  if (["zone", "source", "filter"].includes(s.category)
      && (registry[tgtType]?.math || isBranch(tgtType))) {
    return [false, `${t.label} works on NUMBERS. `
      + (s.category === "zone" ? "A Zone says where to measure"
                               : "Detections are objects, not a quantity")
      + " - wire this into a measuring node, and wire that node's value in here"];
  }
  if (t.category === "analytics" && ["analytics", "variable"].includes(s.category)
      && !isValueSink(registry, tgtType)) {
    return [false, "only a Math node, an Expression or a Running Total can " +
                   "take another analytics/variable as input"];
  }
  return [true, ""];
}

/** The metrics a node publishes, for the MODE it is in.
 *
 *  Most nodes have one fixed list. A multi-mode node (In Frame: now / total)
 *  compiles to a different rule per mode, so its metrics differ per node
 *  rather than per type - mirrors node_metrics() in core/services/workflow.py.
 */
/** Where a Card or a Chart reads from when it is not simply the wire.
 *  Mirrors EVENT_SOURCE_MODES / asks_recorded in core/services/workflow.py:
 *  "events" is the recorded event store, "dataset" a table named on the
 *  card, and "" follows whatever is wired in. The retired Event Card meant
 *  "events" by an unset mode, and "metric" (only ever on a migrated Event
 *  Card) is the wired value under its old name, so it reads as "". */
export function recordedMode(node) {
  const d = (node && node.data) || {};
  const mode = node?.type === "dashboard.events"
    ? (d.source_mode || "events") : (d.source_mode || "");
  return mode === "events" || mode === "dataset" ? mode : "";
}

export function metricsForSpec(spec, mode) {
  const modes = (spec || {}).modes;
  if (modes) {
    const picked = modes[String(mode || "").toLowerCase()]
      || modes[Object.keys(modes)[0]];
    if (picked?.metrics) return picked.metrics;
  }
  return (spec || {}).metrics || [];
}

export function metricsOf(registry, node) {
  const spec = registry?.[node?.type];
  const base = metricsForSpec(spec, node?.data?.mode);
  // A live Line Counter also offers its running totals as ports -- mirrors
  // node_metrics() on the server.
  if (spec?.live_metrics && node?.data?.momentary) return [...base, ...spec.live_metrics];
  return base;
}

// -- the ONE Counter -----------------------------------------------------------
//
// Tok Aba, 2026-10-13: "Counter" and "Line Counter" are one node on the
// canvas, and what is wired into it says what it counts -- a Zone counts
// inside it, a Line counts what crosses it, nothing counts the whole view.
//
// On disk they stay the two types they always were, analytics.counter and
// analytics.entry_exit, and the canvas swaps one for the other as the line
// goes on or comes off. That is the compatibility guarantee: every graph
// already saved on a box holds one of the two, and both still mean exactly
// what they meant -- nothing is migrated, so nothing can migrate wrongly.
// The server's lower_counter_lines() does the same swap for a graph drawn
// anywhere else, so the two halves agree.
export const COUNTER = "analytics.counter";
export const LINE_COUNTER = "analytics.entry_exit";
//: The Counter's Line port. Mirrors COUNTER_LINE_PORT in
//: core/services/workflow.py.
export const COUNTER_LINE_PORT = "line";
//: The settings each half keeps while the node is the OTHER half, so wiring a
//: line on and off again gives back what was set: `mode` means "now / total
//: entered" on a Counter and "which way" on a line counter.
const COUNT_MODES = ["realtime", "distinct"];
const LINE_MODES = ["in", "out", "both"];

export function isCounter(type) {
  return type === COUNTER || type === LINE_COUNTER;
}

/** What a Counter is counting, read off its type and its wires:
 *  "line" | "zone" | "object", or "" for any other node.
 *
 *  A line counter is "line" whether or not its line is wired yet (an old
 *  Line Counter saved without one is still a line counter -- it says what is
 *  missing rather than quietly counting the frame). */
export function counterMode(node, edges) {
  if (!node || !isCounter(node.type)) return "";
  if (node.type === LINE_COUNTER) return "line";
  const ins = (edges || []).filter((e) => e.target === node.id);
  if (ins.some((e) => e.targetHandle === COUNTER_LINE_PORT)) return "line";
  if (ins.some((e) => e.targetHandle === "zone")) return "zone";
  return "object";
}

//: The mode, as the node's subtitle says it.
export const COUNTER_MODE_LABEL = {
  zone: "zone counter", line: "line counter", object: "object counter",
};

/** A Counter becomes a line counter: a line was wired onto its Line port.
 *
 *  Returns { node, edges, dropped } -- the node as analytics.entry_exit (same
 *  id, same fnId, so every key bound to it keeps its name), every edge with
 *  the line moved onto the line counter's own port, and how many OUTPUT
 *  wires had nothing to land on. The count goes to the line counter's lead
 *  tally (In, Out or In for both), the OID stays the OID, and the two ports
 *  only a zone counter has -- `new` and `in zone (s)` -- are taken off. */
export function counterToLine(registry, node, edges) {
  const spec = registry?.[LINE_COUNTER] || {};
  const d = node.data || {};
  const way = LINE_MODES.includes(d.line_mode) ? d.line_mode
    : ((spec.data || {}).mode || "in");
  const data = { ...(spec.data || {}), ...d, mode: way,
                 count_mode: COUNT_MODES.includes(d.mode) ? d.mode : "realtime" };
  delete data.line_mode;
  const lead = (metricsForSpec(spec, way) || ["in"])[0];
  let dropped = 0;
  const out = [];
  for (const e of edges || []) {
    if (e.target === node.id && e.targetHandle === COUNTER_LINE_PORT) {
      out.push({ ...e, targetHandle: "zone" });
    } else if (e.source === node.id) {
      const sh = String(e.sourceHandle || "");
      if (sh === "" || sh === "out") out.push({ ...e, sourceHandle: lead });
      else if (sh === "OID") out.push(e);
      else dropped += 1;
    } else {
      out.push(e);
    }
  }
  return { node: { ...node, type: LINE_COUNTER, data }, edges: out, dropped };
}

/** ...and back: its line was taken off, so it counts a zone or the frame.
 *
 *  The lead tally's wires go to the count, the OID stays, and the other
 *  tallies (Out, Inside, Crossings, the totals) are taken off -- a Counter
 *  has one number. The direction is kept as `line_mode` so wiring a line
 *  back on gives the same counter back. */
export function lineToCounter(registry, node, edges) {
  const lineSpec = registry?.[LINE_COUNTER] || {};
  const d = node.data || {};
  const way = LINE_MODES.includes(d.mode) ? d.mode
    : ((lineSpec.data || {}).mode || "in");
  const data = { ...d, line_mode: way,
                 mode: COUNT_MODES.includes(d.count_mode) ? d.count_mode : "realtime" };
  delete data.count_mode;
  const lead = (metricsForSpec(lineSpec, way) || ["in"])[0];
  let dropped = 0;
  const out = [];
  for (const e of edges || []) {
    if (e.source !== node.id) { out.push(e); continue; }
    const sh = String(e.sourceHandle || "");
    if (sh === "" || sh === lead) out.push({ ...e, sourceHandle: "out" });
    else if (sh === "OID") out.push(e);
    else dropped += 1;
  }
  return { node: { ...node, type: COUNTER, data }, edges: out, dropped };
}

/** The line counters whose LAST line wire just went, and the Counters that
 *  just got one on their Line port, between two versions of the edge list.
 *  { toLine: [id], toCounter: [id] }.
 *
 *  Asked of a change rather than of the graph: an old Line Counter saved
 *  with no line at all stays what it is -- turning it into a Counter on load
 *  would start it counting the whole frame, which it never did. Only taking
 *  a line OFF a line counter is the operator saying "not a line any more". */
export function counterSwaps(nodes, before, after, prevNodes) {
  const byId = new Map((nodes || []).map((n) => [n.id, n]));
  // A wire's SOURCE may be the node that was just deleted (the Zone itself),
  // so its type is looked up in the graph as it was.
  const wasId = new Map((prevNodes || nodes || []).map((n) => [n.id, n]));
  const kept = new Set((after || []).map((e) => e.id));
  const had = new Set((before || []).map((e) => e.id));
  const toLine = new Set();
  const toCounter = new Set();
  for (const e of after || []) {
    if (had.has(e.id)) continue;
    const t = byId.get(e.target);
    if (t?.type === COUNTER && e.targetHandle === COUNTER_LINE_PORT) toLine.add(t.id);
  }
  for (const e of before || []) {
    if (kept.has(e.id)) continue;
    const t = byId.get(e.target);
    if (t?.type !== LINE_COUNTER) continue;
    // Only a PLACE coming off: the In wire (detections) going is not the
    // operator un-choosing the line.
    if (!["zone", COUNTER_LINE_PORT].includes(e.targetHandle || "in")
        && (wasId.get(e.source) || byId.get(e.source))?.type !== "zone.ref") continue;
    const stillLined = (after || []).some(
      (x) => x.target === t.id && byId.get(x.source)?.type === "zone.ref");
    if (!stillLined) toCounter.add(t.id);
  }
  return { toLine: [...toLine], toCounter: [...toCounter] };
}

let _uid = 0;
/** A fresh node id: time-based + counter so paste bursts never collide. */
export function mintId() {
  _uid += 1;
  return `n${Date.now().toString(36)}${_uid.toString(36)}`;
}

/** slug helper matching the server's (lowercase, non-alnum -> _). */
export function slug(v) {
  return String(v || "").trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}

/** Mint data.fnId for an analytics node, same recipe as the server backstop. */
export function mintFnId(name, nodeId) {
  const base = slug(name) || "fn";
  return `${base}_${slug(nodeId).slice(-4) || "x"}`.slice(0, 40);
}

/**
 * The ON / OFF points of a Detection Stream (is the AI running?) and a Class
 * Filter (is it switched on on the dashboard?): the node's only two ports,
 * headed "Connection". ON carries the detections into whatever reads them;
 * every other wire carries the state, and the server turns it into a wire
 * from a hidden clock node, "state.watch", which feeds whatever an Inject
 * feeds (core/services/state_points.py). Any node may take either.
 */
export const STATE_POINT_NODES = ["source.detection", "filter.class"];
export const STATE_POINTS = ["on", "off"];

/** A wire on the old unnamed Detections output of either node is on ON
 *  now (state_points.migrate does the same to the stored graph). */
export function migrateStateEdges(nodes, edges) {
  const types = new Map((nodes || []).map((n) => [n.id, n.type]));
  return (edges || []).map((e) => (STATE_POINT_NODES.includes(types.get(e.source))
    && ["", "detections"].includes(String(e.sourceHandle || ""))
    ? { ...e, sourceHandle: "on" } : e));
}

export function isStatePoint(type, handle) {
  return STATE_POINT_NODES.includes(type) && STATE_POINTS.includes(String(handle || ""));
}

/** The function id a point publishes under (fn_<id>_value is 1 while the
 *  state matches it). Same recipe as state_points.fn_id, including the
 *  server's _slug, which turns EACH odd character into "_". */
export function statePointFn(nodeId, want) {
  const s = String(nodeId || "").trim().toLowerCase()
    .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
  return `st_${s.slice(0, 30)}_${want}`;
}

/**
 * { key: label } for both points of every Detection Stream and Class Filter
 * drawn: the 1/0 values a value picker offers ("Detection Stream is ON").
 * The server mints a point's function once something is wired to it or a
 * node names its key, so the picker lists them before they exist. Labels as
 * state_points.label words them.
 */
export function statePointKeys(nodes) {
  const out = {};
  for (const n of nodes || []) {
    if (!STATE_POINT_NODES.includes(n?.type)) continue;
    const d = n.data || {};
    let what;
    if (n.type === "filter.class") {
      const name = String(d.name || "").trim()
        || (d.classes || []).map(String).filter(Boolean).slice(0, 3).join(", ");
      what = `Class Filter ${name}`.trim();
    } else {
      const bits = [d.model, d.camera].map((v) => String(v || "").trim())
        .filter(Boolean);
      what = "Detection Stream" + (bits.length ? ` (${bits.join(", ")})` : "");
    }
    for (const want of STATE_POINTS) {
      out[`fn_${statePointFn(n.id, want)}_value`] =
        `${what} is ${want.toUpperCase()} (1 = yes, 0 = no)`;
    }
  }
  return out;
}

/**
 * Which object classes a node is watching: the union of every Class Filter on
 * any path back to the source, plus the node's own legacy list.
 *
 * Here rather than in either caller because BOTH the node face and the
 * inspector answer "what is this watching", and two walks of the same graph
 * are two chances to disagree about it on the same screen.
 */
export function classesFor(nodes, edges, id) {
  const byId = new Map((nodes || []).map((n) => [n.id, n]));
  const self = byId.get(id);
  if (!self) return [];
  const preds = new Map();
  for (const e of edges || []) {
    // OFF carries a state, not the filter's objects (ON is the objects).
    if (String(e.sourceHandle || "") === "off") continue;
    if (!preds.has(e.target)) preds.set(e.target, []);
    preds.get(e.target).push(e.source);
  }
  const found = new Set((self.data?.classes || []).map(String));
  const stack = [id];
  const seen = new Set();
  while (stack.length) {
    const at = stack.pop();
    if (seen.has(at)) continue;
    seen.add(at);
    for (const src of preds.get(at) || []) {
      const sn = byId.get(src);
      if (!sn) continue;
      if (sn.type === "filter.class") {
        for (const c of sn.data?.classes || []) found.add(String(c));
      }
      stack.push(src);
    }
  }
  return [...found].sort();
}

/**
 * The Class Filter naming rule, as the panel says it: { nodeId: message } for
 * every Class Filter that breaks it. With MORE THAN ONE running, each needs a
 * name and the names are unique (case and outer spaces ignored), because the
 * dashboard's Class filter card lists them by name. One alone may stay
 * unnamed; a switched-off one does not count.
 *
 * The same rule, in the same words, as core/services/class_filter.py
 * naming_problems -- the save is refused there; this is what lets the panel
 * say so before the save is even sent.
 */
export function classFilterNaming(nodes) {
  const live = (nodes || []).filter((n) => n?.type === "filter.class"
                                           && !n.data?.disabled);
  if (live.length < 2) return {};
  const out = {};
  const seen = new Set();
  for (const n of live) {
    const name = String(n.data?.name || "").trim();
    if (!name) {
      out[n.id] = `Name each Class Filter: this workflow has ${live.length}, `
        + "and the dashboard lists them by name";
    } else if (seen.has(name.toLowerCase())) {
      out[n.id] = `Another Class Filter is already called "${name}" - give `
        + "each one its own name";
    }
    seen.add(name.toLowerCase());
  }
  return out;
}
