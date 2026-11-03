// Workflow node components - one shell, per-category summaries. All types
// render through NodeShell; what varies is the one-line config summary and the
// live value chip. Registry/spec, live values, and compile errors arrive via
// WorkflowCtx so a 4s poll never rebuilds the node array.
import { createContext, memo, useContext, useEffect, useState } from "react";
import AiDot, { aiOf } from "../components/AiDot.jsx";
import StatusDot from "../components/StatusDot.jsx";
import { PRESS_WAIT_MS, injectDot, mqttDot } from "./nodeStatus.js";
import { Handle, Position, useStore, useUpdateNodeInternals } from "@xyflow/react";
import { Play, Square } from "lucide-react";
import { exprNames } from "./ExprEditor.jsx";
import { entryFor, record as recordProbe } from "./probeHistory.js";
import { BRANCH_PORTS, CATEGORY_STYLE, COUNTER, COUNTER_LINE_PORT,
         COUNTER_MODE_LABEL, LINE_COUNTER, PORT_LABELS, branchCtype, camLabel,
         counterMode, isBranch, metricsForSpec, recordedMode } from "./registry.js";

export const WorkflowCtx = createContext({
  registry: {}, values: {}, errors: {}, warnings: {}, variables: {},
  // {zone, now, offset} for the BOX. A Schedule set to 12:00 means noon where
  // the box stands, and whoever is reading the canvas may be somewhere else.
  clock: null,
  // (id, {width, height}, done) -> void - how a Comment writes its size
  // while its corner grip is dragged.
  resizeNode: null,
  // {ok, reason}: the "AI running" dot a Detection Stream wears (AiDot).
  aiStatus: null,
  // ids of nodes that have at least one incoming edge - a node cannot see the
  // edge array, but "am I wired to anything?" changes what some cards MEAN.
  incoming: null,
  // Map of node id -> was it warned itself, for the nodes the compiler did not
  // run: no function, no output, no dashboard card.
  offNodes: null,
  // Set of "<node id>|t|<handle id>" / "|s|" for every port a wire actually
  // lands on. A port cannot see the edge array, and "is anything plugged in
  // here" is the question the canvas was worst at answering.
  wired: null,
  // node id -> which live value that node's FACE reads, for the two nodes
  // whose key is not their own fnId: a Set Variable (it publishes as the
  // variable wired into it) and a Debug probe (it reads whatever is wired
  // in). Resolved on the page, which can see the edges. See faceReads in
  // pages/Workflow.jsx for the shape.
  faceReads: null,
  // Is the detection app UP? A stale number drawn as a live one is the
  // failure a probe exists to prevent.
  appRunning: true,
  // Set of node ids the compiler says WAIT for detection (the app with its
  // AI off holds them; routes/workflow_live.py waits_for_detection). A probe
  // on one of them says "AI off" instead of "nothing published yet".
  waitsForDetection: null,
  // node id -> the state it runs in, for the nodes after an ON / OFF point
  // ("AI detection is off"); see state_points.py. Said on the node.
  runsWhen: null,
  // Set of Class Filter node ids paused on the dashboard: which of each
  // one's ON / OFF points is lit.
  filtersPaused: null,
  // node id -> the names of the Alert nodes it feeds directly. A counter and
  // the alarm it raises are one rule drawn as two nodes; on a big canvas the
  // wire that says so runs off-screen.
  alertsBySource: null,
  // (id) -> void: star this node, so Debug shows its values first. Saved with
  // the graph rather than in the session -- "the three numbers I watch while
  // tuning this line" is a property of the line, not of the tab it was
  // opened in, and losing it on reload is losing the point.
  pinNode: null,
});

//: Port geometry. Bigger than React Flow's 6px default because these now carry
//: SHAPE -- a 6px triangle is a smudge, and the whole point is that direction
//: is readable without tracing the wire.
const PORT = 10;

/**
 * How one VALUE port is drawn: a circle, both sides.
 *
 * Shape answers "what kind of wire is this", not "which way does it go" --
 * direction is already unambiguous from the side of the node a port sits on,
 * and spending shape on it left nothing to distinguish a value wire from a
 * sequence wire. So there is one rule, and it is the same rule Unreal
 * Blueprint uses:
 *
 *   circle     a VALUE moves along this wire
 *   triangle   only the SEQUENCE does; nothing passes  (flowStyle below)
 *
 * Fill still answers "is anything on it": a port with a wire is solid, an
 * empty one is hollow.
 */
function portStyle(kind, wired, color, extra) {
  return {
    width: PORT, height: PORT, ...extra,
    // React Flow positions handles by their centre; a bigger dot needs the
    // offset restated or it hangs off the edge it is supposed to sit on.
    ...(kind === "source" ? { right: -(PORT / 2) } : { left: -(PORT / 2) }),
    borderRadius: "50%",
    border: `2px solid ${color}`,
    background: wired ? color : "var(--surface, #16181d)",
  };
}

/**
 * The Counter's JOB, as the small pill under its name: "zone counter", "line
 * counter", "object counter". One node with one name does three jobs, and the
 * wiring decides which (registry.counterMode), so the face says it. Its
 * in / does / out rows are in the Properties panel (nodeSummary.js), like
 * every node's. "" for every other type.
 */
export function counterJob(type, zoneWired, cmode) {
  if (type === LINE_COUNTER) return COUNTER_MODE_LABEL.line;
  if (type !== COUNTER) return "";
  if (cmode === "line") return COUNTER_MODE_LABEL.line;
  return COUNTER_MODE_LABEL[zoneWired ? "zone" : "object"];
}

/**
 * What the Counter's place ports SAY while empty, and which one is shut.
 *
 * A Counter takes a Zone OR a Line, never both, so while one is wired the
 * other port is shut and says how to open it -- the refusal arrives before
 * the drag instead of as a toast after it. Every other node keeps its plain
 * "wire an area in". Returns {hint, blocked} (either may be absent).
 */
export function counterPortWords(type, id, cmode) {
  if (type === COUNTER && id === "zone") {
    return cmode === "line"
      ? { blocked: "take the Line wire off to count a zone" }
      : { hint: "optional - an area to count inside" };
  }
  if (type === COUNTER && id === COUNTER_LINE_PORT) {
    return cmode === "zone"
      ? { blocked: "take the Zone wire off to count a line" }
      : { hint: "optional - a line to count crossings" };
  }
  if (type === LINE_COUNTER && id === "zone") {
    return { hint: "wire the line to count across" };
  }
  return {};
}

/**
 * The FLOW port: a triangle, on the header row, on both sides of every node
 * that has something before it.
 *
 * Two kinds of wire, two shapes, and the shape is the whole explanation:
 *
 *   circle     a VALUE moves along this wire
 *   triangle   only the SEQUENCE moves; nothing passes
 *
 * These reuse the link_in / link_out handle ids the Formula node has always
 * had, rather than minting a new pair. That is not a shortcut: those ids
 * already have a validated pairing rule (a link out may only meet a link in),
 * their own wire styling, and a compiler that erases them -- and every Formula
 * link wire already saved on a customer's box keeps working untouched. What
 * changes is where they appear and what they look like.
 *
 * Worth being exact, because the shape invites the assumption: a flow wire
 * does not currently REORDER anything. The compiler derives execution order
 * from data dependency (core/services/workflow.py -- the graph is an authoring
 * format, never an execution format), so this draws the sequence an author
 * means rather than one the engine obeys.
 */
function flowStyle(wired, color) {
  // Dotted and hollow, the way it has always been drawn: an association, not
  // a value, and deliberately unlike either end of a wire that carries one.
  // `wired` is accepted and ignored -- a link says the same thing whether or
  // not one is attached, and the port contract test knows this pair is the
  // exception to fill-means-wired.
  return {
    top: 20, background: "transparent",
    border: `2px dotted ${color}`,
  };
}

/**
 * A port's label, drawn the same way everywhere on the canvas.
 *
 * One definition, because a named output on a Cycle Time and the word "out"
 * on an Object Detection are the same thing and were wearing different
 * clothes -- a bare 9.5px muted word in one place, a bordered pill in
 * another. The pill wins: it separates the label from whatever the body
 * says behind it, which a floating word never did.
 */
function chipStyle(color) {
  return {
    flexShrink: 0, fontSize: 10, lineHeight: 1.15, fontWeight: 500,
    padding: "3px 7px", borderRadius: 6,
    maxWidth: 96, overflow: "hidden", textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    color: "var(--text2, #cfd3da)",
    background: "color-mix(in srgb, var(--surface, #16181d) 82%, transparent)",
    border: `1px solid ${color}59`,
  };
}

/**
 * Labelled ports, one per row, under the node's body.
 *
 * Every node that hands out more than one way in or out used to float its
 * chips down the side of the card at even fractions of its height. That
 * only works over a body with nothing in it: the fractions know how tall
 * the card is but not what is written inside it, so on any node with rows
 * of its own the chips landed across them. Rows cannot collide with rows.
 *
 * Inputs read left-to-right (dot, then name), outputs right-to-left (name,
 * then dot), so each label sits beside the dot it belongs to and a wire
 * always arrives at the edge nearest its own name.
 */
function PortRows({ kind, handles, color, isWired, heading }) {
  if (!handles || !handles.length) return null;
  const out = kind === "source";
  return (
    <div style={{ padding: out ? "2px 10px 7px" : "5px 10px 2px" }}>
      {heading && <SectionLabel color={color}>{heading}</SectionLabel>}
      {handles.map((h) => {
        const wired = isWired(kind, h.id);
        // A port may carry its OWN colour, and exactly one kind does: a
        // chart's series ports, where the colour IS the line the operator
        // picked. Everywhere else the node's category colour stands.
        const hue = h.color || color;
        // A port that would refuse every wire (the Counter's Zone while a
        // Line is on it, and back) says so by not taking one, rather than by
        // an error toast after the drag.
        const shut = h.blocked && !wired;
        return (
          <div key={h.id || "default"}
               style={{ position: "relative", display: "flex", gap: 6,
                        alignItems: "center", minHeight: 22,
                        justifyContent: out ? "flex-end" : "flex-start",
                        ...(shut ? { opacity: 0.55 } : {}) }}>
            <Handle
              type={kind}
              position={out ? Position.Right : Position.Left}
              {...(h.id ? { id: h.id } : {})}
              {...(shut ? { isConnectableEnd: false } : {})}
              title={`${h.label || (out ? "output" : "input")} - `
                + (wired ? "wired" : shut ? h.blocked : h.hint || "nothing connected")}
              style={portStyle(kind, wired, hue, {
                top: "50%", transform: "translateY(-50%)",
                ...(out ? { right: -(10 + PORT / 2) } : { left: -(10 + PORT / 2) }),
              })} />
            <span style={{ ...chipStyle(hue), ...(h.lit ? LIT_CHIP : {}) }}
                  title={h.title || undefined}>{h.label}</span>
          </div>
        );
      })}
    </div>
  );
}

//: The ON / OFF points' lights: green for the side the state is on right
//: now, grey for the other (and for both while the state is not known yet).
const POINT_ON = "#22c55e";
const POINT_IDLE = "#6b7280";
const LIT_CHIP = { color: "#fff", fontWeight: 700, background: POINT_ON,
                   border: `1px solid ${POINT_ON}` };

/**
 * The CONNECTION of a Detection Stream or a Class Filter: exactly two
 * ports, ON and OFF (registry.js STATE_POINTS; Tok Aba, after v1.0.31 --
 * "it not call output. it is connection", headed "Connection"). ON carries
 * the detections into whatever reads them, and the state into anything
 * else; OFF carries the state (core/services/state_points.py). `on` is the
 * state now -- true, false, or null while nobody has said -- and lights one
 * of the two.
 */
function statePorts(on, what) {
  const now = (yes) => (on === null ? ""
    : ` - ${yes ? "it is" : "it is not"} right now`);
  return [
    { id: "on", label: "ON", lit: on === true,
      color: on === true ? POINT_ON : POINT_IDLE,
      title: `ON connection: the detections, while ${what} runs; into any `
        + `other node it fires when ${what} is switched on, and once at `
        + `start${now(on === true)}` },
    { id: "off", label: "OFF", lit: on === false,
      color: on === false ? POINT_ON : POINT_IDLE,
      title: `OFF connection: fires when ${what} is switched off, and once `
        + `at start${now(on === false)}` },
  ];
}

/** A small uppercase section heading -- WHAT, OUTPUT -- in the node's colour. */
function SectionLabel({ color, children }) {
  return (
    <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".07em",
                  textTransform: "uppercase", color, marginBottom: 3 }}>
      {children}
    </div>
  );
}

//: Categories whose nodes cannot do anything without something wired in.
//: Deliberately NOT "every node with an input": a Formula may take every
//: operand as a literal and compile perfectly with no wire on it at all, and
//: an alarm on a node that is working correctly teaches people to ignore
//: alarms. Analytics is excluded for the same reason -- too many of its types
//: read a zone or a constant instead.
//: `zone` is NOT here. A Zone names an area on the camera and says so on its
//: own -- it is a thing the deployment HAS, not a rule computed from
//: something upstream. It could always be wired into (a detection stream
//: passes through it to the rules below), but demanding one made an
//: independent node wear an alarm for a wire it never needed.
// "query" is here for the CSV File alone: a Query draws its own node and never
// reaches this shell, while a CSV with nothing wired in is an empty attachment.
const NEEDS_INPUT = new Set(["filter", "logic", "dashboard", "output", "query"]);

// `title` overrides the header for nodes the operator can NAME. A canvas with
// four cards on it all headed "Card" says nothing about which dashboard tile
// each one is; the name it was given does.
/**
 * Tell React Flow when a node's PORTS change.
 *
 * React Flow measures each handle once and caches it by id. A node whose
 * ports change shape after that -- a Trigger growing a Zone port, a Math node
 * gaining an input, a Branch gaining a condition, a Data Table gaining a
 * column, a counter turning on its PID port -- leaves the cache describing
 * handles that are no longer there, and an edge whose handle is missing from
 * it is simply not drawn. That is the "the line between two nodes vanished"
 * report: the wire is still in the graph and still compiles, and comes back
 * on reload, because only the cache was wrong.
 *
 * The signature is the handle IDS, in order, on both sides. Ids rather than a
 * count: swapping which ports exist without changing how many is the same
 * problem, and comparing strings costs nothing next to a re-measure.
 */
function useHandleSync(id, signature) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => {
    if (id) updateNodeInternals(id);
  }, [id, signature, updateNodeInternals]);
}

function NodeShell({ id, type, selected, children, title, targetHandles: rawTargets,
                     hasSource, sourceHandles, linkPorts, footer,
                     portsInBody, data,
                     // What the two port sections are CALLED. "Inputs" and
                     // "Outputs" are right for a node that sits in the flow,
                     // and wrong for a Function Start, whose ports are the
                     // parameters it hands to its body -- a box headed
                     // OUTPUTS listing what an operator just typed as the
                     // function's arguments reads as the opposite of what it
                     // does. The wiring is unchanged: the definition emits a
                     // parameter into the body, the value for it arrives at
                     // the Call.
                     targetHeading = "Inputs", sourceHeading = "Outputs" }) {
  const { registry, errors, warnings, offNodes, wired, incoming,
          pinNode, aiStatus, values, appRunning, brokerUp,
          runsWhen } = useContext(WorkflowCtx);
  // The MQTT dot: is the broker this node talks to online? Null on every
  // node that is not an MQTT node (nodeStatus.js).
  const mq = mqttDot({ type, data, values, appRunning, brokerUp });
  // Read off the node's own data (every caller spreads its React Flow props
  // in), so a star costs no plumbing at fourteen call sites.
  const pinned = !!(data || {}).pinned;
  // "Is a wire on THIS port." The default single source handle carries no id,
  // and React Flow records that edge with sourceHandle null, so "" is its key.
  // An output named "out" is wired either way an edge ever recorded it: by
  // name, or id-less (null attaches to the first source handle).
  const isWired = (kind, hid) => !!wired
    && (wired.has(`${id}|${kind === "source" ? "s" : "t"}|${hid || ""}`)
        || (kind === "source" && hid === "out" && wired.has(`${id}|s|`)));
  // Inputs may be given as plain ids ("a", "b") or as {id, label} - a Dataset
  // labels each input with the COLUMN it fills, and "c3" tells nobody which
  // one that is.
  const targetHandles = rawTargets && rawTargets.map(
    (h) => (typeof h === "string" ? { id: h, label: h } : h));
  // Every port this node draws, so React Flow re-measures the moment the set
  // changes rather than leaving an edge pointing at a handle it no longer
  // knows about (see useHandleSync).
  useHandleSync(id, [
    (targetHandles || []).map((h) => h.id).join(","),
    (sourceHandles || []).map((h) => (typeof h === "string" ? h : h.id)).join(","),
    hasSource ? "s" : "", linkPorts ? "l" : "", portsInBody ? "b" : "",
  ].join("|"));
  const spec = registry[type] || {};
  // Colour follows where the node LIVES in the palette, falling back to its
  // wiring category. A Data Table wires as an output but belongs to Database,
  // and a node that reads red among the sirens is a node in the wrong place.
  const style = CATEGORY_STYLE[spec.palette?.cat]
    || CATEGORY_STYLE[spec.category] || CATEGORY_STYLE.source;
  const Icon = style.icon;
  const err = errors[id];
  const warn = warnings[id];
  // A node the compiler skipped must not look identical to one that is
  // counting and alerting. The warned node names its own cause; the branch
  // below it was skipped as a consequence, so it says only that it is idle.
  const off = !!offNodes && offNodes.has(id);
  // Detection being OFF changes nothing here, on purpose (Tok Aba,
  // 2026-10-05): a Detection Stream and the nodes after it look exactly as
  // they do while detecting. They do not run until detection is back on (the
  // app holds them; no made-up zeros), and the one place that says detection
  // is off is the AI dot -- on the Detection Stream, the toolbar and System
  // Control. A greyed half of the canvas read as "this part is broken".
  // The link pair, on the Formula alone, exactly as it always was.
  //
  // It was briefly on every dependent node as a sequence port, and taken back
  // off: a second port pair on every card is a second thing to understand on
  // every card, and it bought nothing an author could not already read from
  // the value wires. It stays on the Formula because wires drawn there are
  // already saved on boxes in the field, and a handle that disappears is a
  // wire with nothing to hang on.
  const showFlow = linkPorts;

  return (
    <div style={{
      // Roomier than it was. The old 170-240 forced every value into an
      // ellipsis and put the port chips on top of the body text; the template
      // this follows gives a row enough width to hold a label, a value and a
      // port without any of the three fighting the others.
      minWidth: 210, maxWidth: 280, borderRadius: 11,
      background: "var(--surface, #16181d)",
      border: `1.5px solid ${err ? "#ef4444" : selected ? "var(--accent, #04bade)" : style.color + "66"}`,
      boxShadow: selected ? "0 0 0 3px color-mix(in srgb, var(--accent, #04bade) 25%, transparent)" : "var(--card-shadow, none)",
      fontSize: 12, color: "var(--text, #e8eaed)",
      // Selection must stay readable even on a dimmed node, or an operator
      // cannot see what they just clicked in order to fix it.
      opacity: off && !selected ? 0.5 : 1,
    }}>
      {/* Header: the icon in a tile of its own, the name, and the type code.
          The icon used to float loose against the title at 13px and read as
          punctuation; in a tinted tile it is the node's mark, legible at the
          zoom people actually work at. */}
      <div style={{ display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 10px",
                    borderBottom: "1px solid rgba(128,128,128,.15)",
                    background: style.color + "1f",
                    borderRadius: "11px 11px 0 0" }}>
        <span style={{ display: "flex", alignItems: "center",
                       justifyContent: "center", width: 24, height: 24,
                       flexShrink: 0, borderRadius: 7,
                       background: style.color + "26",
                       border: `1px solid ${style.color}59` }}>
          <Icon size={14} style={{ color: style.color }} />
        </span>
        <strong style={{ overflow: "hidden", textOverflow: "ellipsis",
                         whiteSpace: "nowrap", fontSize: 12.5 }}
                title={title ? `${title} - ${spec.label}` : spec.label}>
          {title || spec.label}
        </strong>
        {/* The corner. One group with ONE auto margin: each of these used to
            carry `marginLeft: auto` of its own, and two auto margins split
            the free space between them rather than both hugging the right --
            so the star landed in the middle of the header. */}
        <span style={{ marginLeft: "auto", flexShrink: 0, display: "flex",
                       alignItems: "center", gap: 6 }}>
          {/* A Detection Stream IS the AI on the canvas: its dot says
              whether the detector and camera are really producing. */}
          {type === "source.detection" && aiStatus && <AiDot ai={aiStatus} />}
          {/* An MQTT node wears the broker's light: green online, red
              offline, the reason on hover. */}
          {mq && <StatusDot ok={mq.ok} title={mq.title} attr="mqtt" />}
          {(err || warn) && (
            <span title={err || warn}>{err ? "🔴" : "🟡"}</span>
          )}
          {/* The type code, when the registry gives one. It sits where the
              error dot does, so the two never both claim the corner: a node
              with something wrong to say says that instead. */}
          {!err && !warn && spec.badge && (
            <span title={spec.label}
                  style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: ".04em",
                           padding: "2px 6px", borderRadius: 6,
                           color: style.color,
                           background: "color-mix(in srgb, var(--surface, #16181d) 65%, transparent)",
                           border: `1px solid ${style.color}59` }}>
              {spec.badge}
            </span>
          )}
          {/* The STAR: "one of the numbers I am watching". Offered only on
              nodes that publish something to watch -- a rule, or a variable --
              because starring a Comment or a siren would promise a Debug row
              that can never appear. Saved with the graph, so what somebody
              pinned while tuning a line is still pinned tomorrow.
              LAST and larger: at 12px between the title and the type code it
              read as punctuation, and it is a control -- the only one on the
              header -- so it takes the corner. */}
          {pinNode && (registry[type]?.metrics
                       || registry[type]?.category === "variable") && (
            <button type="button" className="nodrag"
                    title={pinned ? "Unpin - stop showing this first in Debug"
                                  : "Pin - show this first in Debug"}
                    onClick={(e) => { e.stopPropagation(); pinNode(id); }}
                    style={{ flexShrink: 0, cursor: "pointer",
                             background: "none", border: "none", padding: 0,
                             lineHeight: 1, fontSize: 17,
                             color: pinned ? "#f59e0b" : "var(--muted, #8b93a1)",
                             opacity: pinned ? 1 : 0.65 }}>
              {pinned ? "★" : "☆"}
            </button>
          )}
        </span>
      </div>
      {/* Plain padding, the same on every node.
          It used to step aside for the port chips floated over it -- a
          right inset sized to the widest label, a left inset for the
          inputs, and a minimum height of 24px per port so the dots had
          distinct rows to sit on. All of that existed to keep floated
          chips off the body text. Nothing floats now, so the body is
          simply a body, and the space those insets reserved goes back to
          the content that needed it. */}
      <div style={{ padding: "7px 10px" }}>{children}</div>
      {/* Labelled ports, under the body rather than floated over it. Only
          when there is more than one on a side: a lone unnamed input or
          output is a dot on the edge, and a heading over a single row would
          be a section with nothing to distinguish inside it. */}
      {!portsInBody && (
        <>
          <PortRows kind="target" heading={targetHeading} color={style.color}
                    isWired={isWired}
                    handles={(targetHandles || []).length > 1 ? targetHandles : null} />
          <PortRows kind="source" heading={sourceHeading} color={style.color}
                    isWired={isWired}
                    handles={(sourceHandles || []).length > 1 ? sourceHandles : null} />
        </>
      )}
      {/* The third partition: a caption in its own strip under the port
          zone. Inside the body it sat exactly where the port chips cross,
          and "first condition that holds wins" with two chips through it
          was noise, not help. */}
      {footer && (
        <div style={{ padding: "4px 10px 6px", fontSize: 10,
                      color: "var(--muted-text, #999)",
                      borderTop: "1px solid rgba(128,128,128,.15)" }}>
          {footer}
        </div>
      )}
      {off && (
        // Short on purpose: the compile warning in the inspector already says
        // how to fix it, and the same sentence twice reads as two problems.
        <div style={{ padding: "0 10px 7px" }}>
          <span title={warn || "skipped: nothing upstream is running"}
                style={{ display: "inline-block", fontSize: 9.5, padding: "1px 6px",
                         borderRadius: 999, color: "var(--muted-text, #999)",
                         background: "rgba(148,163,184,.14)",
                         border: "1px solid rgba(148,163,184,.4)" }}>
            {offNodes.get(id) ? "No detection stream" : "Not running"}
          </span>
        </div>
      )}
      {/* After an ON / OFF point: the state this node runs in, said where
          it is drawn ("Runs when AI detection is off"). The app holds it
          the rest of the time (state_points.py), so a node that reads idle
          says why rather than looking broken. */}
      {!off && runsWhen && runsWhen[id] && (
        <div style={{ padding: "0 10px 7px" }}>
          <span title={`Wired after an ON/OFF connection: this runs only while `
                       + `${runsWhen[id]}, and is held the rest of the time`}
                style={{ display: "inline-block", fontSize: 9.5, padding: "1px 6px",
                         borderRadius: 999, color: "var(--text2, #cfd3da)",
                         background: "rgba(34,197,94,.12)",
                         border: "1px solid rgba(34,197,94,.45)" }}>
            Runs when {runsWhen[id]}
          </span>
        </div>
      )}
      {/* Nothing wired in, on a node that cannot mean anything without it.
          Shown only when nothing louder is already being said: a red border
          (err) or a "Not running" badge (off) is the same news arriving
          first, and one problem wearing three labels reads as three
          problems. The hollow input port says this too -- this is the same
          fact at node scale, for someone scanning the canvas rather than
          inspecting one card. */}
      {!off && !err && NEEDS_INPUT.has(spec.category)
        && (targetHandles || [{ id: "in" }]).length > 0 && spec.rank > 0
        && !!incoming && !incoming.has(id) && (
        <div style={{ padding: "0 10px 7px" }}>
          <span title="Nothing is connected to this node's input"
                style={{ display: "inline-block", fontSize: 9.5, padding: "1px 6px",
                         borderRadius: 999, color: "var(--warn-text)",
                         background: "rgba(251,191,36,.12)",
                         border: "1px solid rgba(251,191,36,.45)" }}>
            Not wired
          </span>
        </div>
      )}

      {/* Skipped when the body draws them on their own rows: two handles
          sharing one id is a wire that anchors to whichever React Flow
          measured last. */}
      {!portsInBody && (targetHandles || []).length <= 1
        && (targetHandles || [{ id: "in" }]).length === 1
        && (spec.rank > 0 || (targetHandles || [])[0]?.id === "snap") && (
        <Handle type="target" position={Position.Left}
                id={(targetHandles || [{ id: "in" }])[0].id}
                title={isWired("target", (targetHandles || [{ id: "in" }])[0].id)
                  ? "input - wired" : "input - nothing connected"}
                style={portStyle("target",
                  isWired("target", (targetHandles || [{ id: "in" }])[0].id),
                  style.color,
                  { top: `calc(41px + (100% - ${41 + (footer ? 25 : 0)}px) * 0.5)` })} />
      )}
      {/* Several ways in/out: dots spread EVENLY down the side, each label
          beside its own dot, inside the card. The old fixed-percent spacing
          put the second label on top of the body text (the "b over the
          node's name" bug) because it never asked how tall the card was. */}
      {/* Port labels render as small CHIPS, not bare 9px muted text: a port's
          label is the one thing that tells a first-time reader what to wire
          where, and grey letters floating over the body text told them
          nothing they could read. The chip's background separates it from
          whatever the body says behind it, and long labels truncate so they
          can never run under the opposite side's ports. */}
      {/* Ports sit in the BODY, never on the tinted header: the header is
          the node's name, and a dot on it reads as a mystery fifth input.
          41px is the header's rendered height (8px padding, 24px icon tile,
          border); the ports share out what remains. */}
      {portsInBody || (sourceHandles || []).length > 1
        ? null : hasSource !== false && (
        <>
          <Handle type="source" position={Position.Right}
                  {...((sourceHandles || [])[0]?.id
                    ? { id: sourceHandles[0].id } : {})}
                  title={isWired("source", (sourceHandles || [])[0]?.id)
                    ? "output - wired" : "output - nothing connected"}
                  style={portStyle("source",
                    isWired("source", (sourceHandles || [])[0]?.id),
                    style.color,
                    { top: `calc(41px + (100% - ${41 + (footer ? 25 : 0)}px) * 0.5)` })} />
          {(sourceHandles || [])[0]?.label && (
            <span style={{ ...chipStyle(style.color), position: "absolute",
                           right: 9,
                           top: `calc(41px + (100% - ${41 + (footer ? 25 : 0)}px) * 0.5)`,
                           transform: "translateY(-50%)",
                           pointerEvents: "none" }}>
              {sourceHandles[0].label}
            </span>
          )}
        </>
      )}
      {/* LINK ports (rendered LAST on purpose: React Flow anchors a
          null-handle wire to the FIRST source handle in the DOM, and
          with the link ring first every plain output wire visually
          left the node from the top corner). Top corners: an association, not a value -- the wire
          between two link ports passes nothing and compiles to nothing, it
          only SAYS "these belong together" across a crowded canvas. Drawn
          dotted in the accent colour (Workflow.jsx shownEdges) so it cannot
          be read as the grey dash of a switched-off branch. */}
      {showFlow && (
        <>
          {/* On the HEADER partition's own sides, not the top edge: in on
              the left, out on the right, same reading direction as every
              value port below -- the header row is the flow row. */}
          <Handle type="target" position={Position.Left} id="link_in"
                  title="flow in - sequence only; no value passes here"
                  style={flowStyle(isWired("target", "link_in"), style.color)} />
          <Handle type="source" position={Position.Right} id="link_out"
                  title="flow out - sequence only; no value passes here"
                  style={flowStyle(isWired("source", "link_out"), style.color)} />
        </>
      )}
    </div>
  );
}

const Muted = ({ children }) => (
  <span style={{ color: "var(--muted-text, #999)" }}>{children}</span>
);

const SourceNode = memo(function SourceNode(props) {
  const { variables, aiStatus } = useContext(WorkflowCtx);
  const d = props.data || {};
  // What this stream is scoped to, if anything. A camera is named by id in the
  // graph and by its SLOT on the canvas -- "cam_2" tells the next reader
  // nothing, and the device's own name goes stale the day it is swapped.
  const bits = [];
  if (d.model) bits.push(d.model);
  if (d.camera) {
    bits.push(d.camera === "*" ? "Every camera"
              : camLabel(variables.cameras, d.camera));
  }
  // ON while AI detection runs, OFF while it does not -- the AI dot's `on`.
  const on = aiStatus ? !!aiStatus.on : null;
  // Snapshot mode is the only time a stream has an input: the SNAP port, which
  // an Inject, a Trigger or a sensor drives. In Video mode (the default, and
  // every stream saved before this) there is no port at all, so no existing
  // canvas gains a dot it did not have.
  const snap = d.capture === "snapshot";
  return (
    <NodeShell {...props}
               targetHandles={snap ? [{ id: "snap", label: "Snap" }] : []}
               sourceHandles={statePorts(on, "AI detection")}
               sourceHeading="Connection">
      {snap && (
        <div style={{ fontWeight: 600, marginBottom: 2 }}>
          Snapshot
          <span className="muted small" style={{ fontWeight: 400 }}>
            {" "}· detects when the snap input fires
          </span>
        </div>
      )}
      {bits.length
        ? <span>{bits.join(" · ")}</span>
        : <Muted>{snap ? "Waiting for a snap" : "All detected objects, every frame"}</Muted>}
    </NodeShell>
  );
});

const FilterNode = memo(function FilterNode(props) {
  const { filtersPaused } = useContext(WorkflowCtx);
  const classes = props.data?.classes || [];
  // A Class Filter's NAME heads it, as on the dashboard's Class filter card
  // that lists it by that name. A Pre-process has none, so it keeps the type.
  const name = props.type === "filter.class"
    ? String(props.data?.name || "").trim() : "";
  // A Class Filter's ON / OFF points: OFF while it is paused on the
  // dashboard or switched off on the canvas. A Pre-process has neither.
  // Its own On / Off switch (data.running, absent = On) is the dashboard
  // card's run / pause: one state (services/class_filter.py).
  const switchedOff = props.type === "filter.class" && props.data?.running === false;
  const ports = props.type === "filter.class"
    ? statePorts(!props.data?.disabled && !switchedOff
                 && !(filtersPaused && filtersPaused.has(props.id)),
                 "this Class Filter")
    : undefined;
  const paused = switchedOff || (props.type === "filter.class" && !!filtersPaused
                                 && filtersPaused.has(props.id));
  return (
    <NodeShell {...props} title={name || undefined} sourceHandles={ports}
               {...(ports ? { sourceHeading: "Connection" } : {})}>
      <span style={paused ? { opacity: 0.55 } : undefined}>
        {classes.length
          ? classes.slice(0, 4).join(", ") + (classes.length > 4 ? ` +${classes.length - 4}` : "")
          : <Muted>no classes chosen - passes everything</Muted>}
      </span>
      {paused && (
        <div style={{ marginTop: 4 }}>
          <span className="state-pill" data-filter-off="1"
                title="Switched off: the rules after this Class Filter are paused">
            Off · rules after it paused
          </span>
        </div>
      )}
    </NodeShell>
  );
});

/** One pre-process step, in as few characters as the canvas can afford.
 *
 *  A chain is read at canvas zoom, in a box the width of a node, so each step
 *  gets the ONE number that changes what you would see ("blur 5", not
 *  "blur box k=5 sigma=0"). The rest is in the inspector, which is where
 *  somebody who wants to change it is already going.
 */
function stepLabel(s, zoneList) {
  const n = (v, d) => (v === undefined || v === null ? d : v);
  switch (s.op) {
    // A mask names ZONES, and a zone is known by the name somebody gave it in
    // the Zone Manager -- "mask(z_3f2a)" is the id, which is the one thing a
    // reader cannot match to the shape they drew.
    case "mask": {
      const ids = s.zones || [];
      const named = ids.map((id) => {
        const z = (zoneList || []).find((x) => x.id === id);
        return z ? z.name : id;
      });
      const what = named.length ? named.slice(0, 2).join(", ") : "no zone";
      return `mask(${what}${named.length > 2 ? ` +${named.length - 2}` : ""})`
        + (s.invert ? " inverted" : "");
    }
    case "grayscale": return "grayscale";
    // Gamma only when it is doing something: on most chains it is left at 1
    // and printing "γ1" on every node is noise in the place there is least
    // room for it.
    case "exposure":
      return `exposure ${n(s.gain, 1)}`
        + (Number(n(s.gamma, 1)) !== 1 ? ` γ${s.gamma}` : "");
    case "sharpness": return `sharpen ${n(s.amount, 1)}`;
    // The defaults here are the compiler's (workflow.PREPROCESS_OPS), so a
    // step saved without a number reads on the canvas as what will run.
    case "blur": return `${s.kind === "box" ? "blur" : "gauss"} ${n(s.ksize, 5)}`;
    case "sobel": return "sobel";
    // An op this build does not know still gets drawn by name. The compiler
    // refuses it, and a node reading "(3 steps)" while the canvas reports an
    // error names nothing to go and fix.
    default: return String(s.op || "?");
  }
}

/** The Pre-process node: the chain the picture goes through, in order.
 *
 *  The order is the whole meaning of the node -- blur then sobel is an edge
 *  map of a smoothed picture, sobel then blur is a smeared edge map -- so the
 *  face draws the sequence rather than a count.
 */
const PreprocessNode = memo(function PreprocessNode(props) {
  const { variables } = useContext(WorkflowCtx);
  const d = props.data || {};
  const steps = Array.isArray(d.steps) ? d.steps : [];
  const shown = steps.slice(0, 4);
  return (
    <NodeShell {...props}>
      {steps.length ? (
        <span>
          {shown.map((s, i) => {
            // A step switched off is DRAWN, struck through: deleting it to
            // try the chain without it is what the flag exists to avoid, and
            // a canvas that simply omitted it would say the chain is shorter
            // than it is.
            const off = s.enabled === false;
            return (
              <span key={i}>
                {i ? " → " : ""}
                <span style={off ? { textDecoration: "line-through",
                                     color: "var(--muted-text, #999)" } : undefined}>
                  {stepLabel(s, variables.zone_list)}
                </span>
              </span>
            );
          })}
          {steps.length > 4 ? ` +${steps.length - 4}` : ""}
          {d.preview ? <Muted> · preview</Muted> : null}
        </span>
      ) : <Muted>no steps - passes the picture through</Muted>}
    </NodeShell>
  );
});

const ZoneNode = memo(function ZoneNode(props) {
  const { variables } = useContext(WorkflowCtx);
  const z = (variables.zone_list || []).find((x) => x.id === props.data?.zone);
  return (
    // No input. A zone is an area someone drew on the camera -- it is not
    // computed from anything, so there is nothing that could arrive here.
    // It used to sit in SERIES (Class Filter into Zone into the rule), which
    // read as detections passing through the zone on their way somewhere;
    // rules take the zone on a port of their own now, so the series wire has
    // nothing left to carry. Graphs drawn the old way are rewired on load
    // (migrate_series_zones, core/services/workflow.py).
    <NodeShell {...props} targetHandles={[]}>
      {z ? (
        <span>
          <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2,
                         // A zone carries its own colour from the Zones page;
                         // with none set it falls back to its category's,
                         // rather than to a hue used nowhere else.
                         background: z.color || CATEGORY_STYLE.zone.color,
                         marginRight: 6 }} />
          {z.name} <Muted>({z.kind}{z.enabled === false ? " · disabled" : ""})</Muted>
        </span>
      ) : <Muted>zone missing - pick one in the inspector</Muted>}
      {/* A zone wired straight into an Alert says so too. Rare -- a zone
          usually feeds a counter first -- but when it is drawn that way the
          zone IS the thing raising the alarm. */}
      <AlertChips id={props.id} />
    </NodeShell>
  );
});

//: The nodes whose single output handle is named "out" (shellSources below).
const OUT_NAMED = ["analytics.object", "analytics.object_frame", "analytics.object_zone",
                   "analytics.counter", "analytics.entry_exit", "analytics.frame",
                   "analytics.violations"];

const AnalyticsNode = memo(function AnalyticsNode(props) {
  const { registry, values, edges: ctxEdges, objClasses,
          faceReads } = useContext(WorkflowCtx);
  const spec = registry[props.type] || {};
  const d = props.data || {};
  // Mode-aware: a multi-mode counter publishes a different first metric per
  // mode (a realtime Zone Counter publishes `count`, a distinct one `total`),
  // and reading the type-level list showed "…" for the modes it missed.
  const metric = (metricsForSpec(spec, d.mode) || [])[0];
  // A SET VARIABLE DOES NOT PUBLISH UNDER ITS OWN ID. It publishes as the
  // variable wired into its input a (_set_target_fid on the server), so
  // fn_set_variable_xxxx_value -- the key this line composes for every other
  // node -- is a key nobody has ever written, and the footer below read
  // "Set Variable = ..." for ever on every box in the field while the
  // variable beside it counted correctly. The page resolves the real key
  // from the wire (faceReads) because a node cannot see its own edges.
  const readKey = props.type === "math.setvar"
    ? ((faceReads && faceReads.get(props.id)) || {}).key
    : (d.fnId != null && metric ? `fn_${d.fnId}_${metric}` : null);
  const v = readKey ? values[readKey] : undefined;
  // A Formula shows exactly as many inputs as its operation takes: `not` has
  // one, `clamp` has three -- and a CHAINABLE operation (add, and, or, min…)
  // as many as its "Number of inputs" asks for. A handle nothing can be
  // wired to is a handle that invites a wire the compiler will reject.
  // A Math node is the same node with its operation fixed by the drawer it
  // came out of: it draws the same face, and whether it CHAINS is its own
  // property (Clamp never grows an eighth input however chainable `clamp`
  // looks from the operations table) rather than the operation's.
  const isMath = !!spec.math;
  const opSpec = (spec.ops || {})[d.op] || (spec.ops || {}).add;
  const chains = isMath
    ? !!spec.chain && (!spec.chain_ops || spec.chain_ops.includes(d.op))
    : !!(opSpec || {}).chain;
  const fmArity = chains
    ? Math.max(2, Math.min(spec.max_inputs || 8, parseInt(d.inputs, 10) || 2))
    // A single-operation Math node has no picker and no `ops` map, so its
    // shape comes from the handles the registry minted for it.
    : (opSpec || {}).arity || (isMath ? (spec.handles || []).length : 0) || 2;
  // THE RUN PORT. `when` began as an opt-in gate -- tick a box, get a port,
  // wire a condition and the arithmetic computes only while it holds. It is
  // always there now and simply called "In", because arithmetic is logic that
  // belongs somewhere in a pipeline and this is the only place to say where:
  // wire a Detection Stream (or anything else that carries no number) and the
  // wire is sequence alone, wire a condition and it still gates. Same handle
  // id, so every gate already drawn on a box in the field keeps its wire.
  // The LINK pair (the two ring ports on the header) is retired from these
  // nodes: it said "these belong together" and passed nothing, which is what
  // the run port now says while also being real. Drawn only where one is
  // already wired -- a handle that vanishes is a wire with nothing to hang on.
  const linkWired = (ctxEdges || []).some(
    (e) => (e.target === props.id && e.targetHandle === "link_in")
        || (e.source === props.id && e.sourceHandle === "link_out"));
  // A zone-optional node reads WHERE it measures off its wiring, so its face
  // has to see the wire too -- "whole frame" and "in the zone wired in" is
  // the difference between the two nodes this one replaced.
  const zoneWired = (ctxEdges || []).some(
    (e) => e.target === props.id && (e.targetHandle || "in") === "zone");
  // A Custom Formula grows a port for every NAME its expression refers to.
  // The wire used to be matched by the source node's own name -- which meant
  // renaming a node three drawers away silently zeroed a formula, with
  // nothing on either face to show it had happened. A named port says which
  // value is which on the node itself, and the binding survives a rename
  // because it is the PORT that carries the name, not the source.
  //
  // The plain `in` port stays first: every wire already drawn on a Formula
  // records no handle id, and React Flow attaches an id-less edge to the
  // first handle. The server still falls back to name-matching for those.
  const exprPorts = props.type === "analytics.expr"
    ? exprNames(d.expr).map((n) => ({ id: `p_${n}`, label: n }))
    : [];
  const handles = props.type === "analytics.expr"
    ? [{ id: "in", label: "any" }, ...exprPorts]
    : props.type === "analytics.formula" || isMath
    ? [{ id: "when", label: "In" },
       // Named ports where the operation has real ones: "Base"/"Exponent"
       // says which way round a power goes, where a/b does not.
       ...["a", "b", "c", "d", "e", "f", "g", "h"].slice(0, fmArity)
         .map((h, i) => ((spec.port_labels || [])[i]
           ? { id: h, label: spec.port_labels[i] } : h)),
       // Chainable operations grow: the + port mints the next input under
       // a dropped wire, same gesture as the Switch's.
       ...(chains && fmArity < 8 ? [{ id: "in_new", label: "+" }] : [])]
    // A rule that applies INSIDE an area gets a port for the area, separate
    // from the port for the objects. The zone used to arrive in series --
    // Class Filter into Zone into Zone Counter -- which read as the zone
    // being something the detections passed THROUGH on their way, when it
    // is really a second thing the rule needs to know. Series wiring still
    // compiles (the compiler reads a zone on either port), so no saved
    // graph changes; new ones can say the two inputs separately.
    : (spec.handles && spec.handles.length ? spec.handles
      : (spec.zone_kind
        ? [{ id: "in", label: "In" }, { id: "zone", label: "Zone" }]
        : ["in"]));
  // A counter that counts each object ONCE has more than one number worth
  // wiring, so it hands them out by name:
  //
  //   count -- the running total of distinct objects
  //   new   -- +1 the instant a new one is counted, and 0 otherwise
  //   PID   -- WHICH object it just counted (an id, not a number)
  //
  // Without the named ports the wire always meant the first metric and the
  // rest were reachable only through a card's metric picker -- so "add a row
  // when a new person appears" had nowhere to come from.
  // Both counters count once BY PID only in their distinct modes; the
  // realtime modes count bodies per frame and have no "new" (or PID) to
  // speak of. Both fall back to realtime -- a pre-mode Zone Counter is
  // stamped mode="distinct" by the server migration on load, so no node
  // this renders is ever mode-less.
  const countsOnce = ["analytics.counter", "analytics.violations",
                      "analytics.frame"]
    .includes(props.type) && (d.mode || "realtime") !== "realtime";
  // Zone-scoped rules with PID on also hand out the live per-person clock:
  // "how long has THIS one been inside", as a wire for a Data Table column.
  // "how long has THIS one been inside" needs an inside. The merged Counter
  // has one only when a zone is actually wired to it.
  const zoneScoped = ["analytics.violations", "analytics.dwell",
                      "analytics.occupancy"].includes(props.type)
    || (props.type === "analytics.counter" && zoneWired);
  // Cycle Time's countdown / occupancy modes measure the PLACE, not objects:
  // no per-object stay exists, so a lingering PID tick offers no ports.
  const placeMode = props.type === "analytics.dwell"
    && ["countdown", "occupancy"].includes(d.mode);
  // An Object Detection's OID port sits beside its class ports (below),
  // not on the counters' out/new pair.
  const withPid = d.OID && !placeMode && props.type !== "analytics.object";
  // The Line Counter hands out one port per tally it leads with, named the
  // way an operator says it. The handle IS the metric key (that is how a
  // named wire resolves, see the compiler), so the labels can be plain words
  // without moving a single stored wire. "Inside" is in − out, and it is an
  // ESTIMATE: it assumes everyone who came in through this line also leaves
  // through it, which the inspector says in one line.
  const LINE_PORTS = PORT_LABELS;
  const lineMode = props.type === "analytics.entry_exit"
    ? (d.mode || "in") : null;
  const sources = lineMode
    ? [...metricsForSpec(spec, lineMode).map((m) => ({
        id: m,
        // One direction only: the tally IS the count, so the port says so
        // rather than repeating the setting above it.
        label: (lineMode !== "both" && (m === "in" || m === "out"))
          ? "Count" : LINE_PORTS[m] || m,
      })),
       // ...and WHICH object crossed, when the node is asked for it. The
       // tallies are one number for the whole line; this is one id per
       // crossing, which only a Data Table column can hold.
       ...(withPid ? [{ id: "OID", label: "OID" }] : [])]
    : (countsOnce || withPid)
    ? [{ id: "out", label: props.type === "analytics.dwell" ? "value" : "count" },
       ...(countsOnce ? [{ id: "new", label: "new" }] : []),
       // OID, not PID. The port hands out WHICH object was just counted, and
       // "object id" is what that is -- the old name came from the tracker's
       // internal vocabulary, which nobody using this page shares. The handle
       // id stays `pid`: every wire already drawn on it is saved under that
       // name, and renaming a label must never move a graph.
       ...(withPid ? [{ id: "OID", label: "OID" }] : []),
       ...(withPid && zoneScoped ? [{ id: "timer", label: "in zone (s)" }] : [])]
    : props.type === "analytics.anomaly"
    // The one answer is NAMED, so the wire carries the metric: left unnamed it
    // would read the rule's first metric (`found`), which is not GOOD / NG.
    ? [{ id: "verdict", label: "GOOD / NG" }]
    : (props.type === "analytics.formula" || isMath
      // The output gets a name the way every input has one -- an unlabelled
      // dot opposite three labelled chips read as something else entirely.
      // No id: the default source handle is what every saved wire holds.
      ? [{ label: "output" }]
      : ["analytics.object", "analytics.object_frame", "analytics.object_zone"]
        .includes(props.type)
        && ((objClasses?.get(props.id) || []).length
            || (props.type === "analytics.object" && d.OID))
        // One port per watched class, beside the main `found` port -- so
        // "the chair count" is a wire, not a metric picker. `found` keeps
        // the plain first position: every saved wire holds no handle id and
        // anchors there.
        ? [{ id: "found", label: "found" },
           ...(objClasses?.get(props.id) || []).slice(0, 6).map((c) => ({
             id: String(c).toLowerCase().replace(/[^a-z0-9]+/g, "_")
               .replace(/^_+|_+$/g, ""),
             label: c,
           })),
           // ...and WHICH object arrived, when OID is ticked: one id per
           // arrival, for a Data Table column (the counters' OID port). One
           // port for every watched class -- the row records the class too.
           ...(props.type === "analytics.object" && d.OID
             ? [{ id: "OID", label: "OID" }] : [])]
        : undefined);
  // The Formula and the Math family share one face: labelled input chips, a
  // named output, and the words in the lower partition.
  const isFormula = props.type === "analytics.formula" || isMath;
  // The Counter's job, off its type and its wires (registry.js counterMode),
  // as the pill under its name; its in / does / out rows are in Properties.
  const cmode = counterMode({ id: props.id, type: props.type }, ctxEdges);
  const job = counterJob(props.type, zoneWired, cmode);
  const jobColor = (CATEGORY_STYLE[spec.palette?.cat]
    || CATEGORY_STYLE[spec.category] || CATEGORY_STYLE.source).color;
  // The Counter's place ports say while empty which one is shut, and the
  // shut one takes no wire (counterPortWords, drawn by PortRows).
  const shellTargets = [COUNTER, LINE_COUNTER].includes(props.type)
    ? handles.map((h) => {
      const id = typeof h === "string" ? h : h.id;
      return { ...(typeof h === "string" ? { id: h, label: h } : h),
               ...counterPortWords(props.type, id, cmode) };
    }) : handles;
  // The object watches' and counters' single output is NAMED "out": edges
  // saved while their face box drew it carry sourceHandle "out", and React
  // Flow silently drops a named edge whose handle id does not exist. An
  // id-less edge still attaches (null matches the first source handle).
  const shellSources = !sources && OUT_NAMED.includes(props.type)
    ? [{ id: "out" }] : sources;
  return (
    <NodeShell {...props} targetHandles={shellTargets} sourceHandles={shellSources}
               linkPorts={isFormula && linkWired}
               // The Formula's port zone holds only chips; what it is called
               // and what it reads live in the lower partition, where the
               // Switch already keeps its words.
               footer={isFormula
                 // A Math node carries no name (it is arithmetic, not a
                 // measurement anyone looks up later), so the line says WHICH
                 // operation of that node's own is running: "Arithmetic ·
                 // Multiply". With one operation there is nothing to add.
                 ? `${d.name || ((spec.ops || {})[d.op]
                     ? `${spec.label} · ${spec.ops[d.op].label}` : spec.label)}`
                   + ` = ${v === undefined ? "…"
                     : (typeof v === "number" ? Math.round(v * 100) / 100 : v)}`
                 : undefined}>
      {isFormula ? (
        <div style={{ minHeight: 6 }} />
      ) : (
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {d.name || spec.label}
        </span>
        {v !== undefined && (
          <strong style={{ marginLeft: "auto", fontSize: 15, color: "var(--accent-text, #04bade)" }}>
            {typeof v === "number" ? Math.round(v * 100) / 100 : v}
          </strong>
        )}
      </div>
      )}
      {/* The Counter's job, under its name: the same node is a zone, a line
          or an object counter by what is wired into it. */}
      {job && (
        <div style={{ marginTop: 6 }}>
          <span title="What this Counter counts follows what is wired into it"
                style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".03em",
                         padding: "2px 8px", borderRadius: 999,
                         color: jobColor, border: `1px solid ${jobColor}59`,
                         background: `${jobColor}14` }}>
            {job}
          </span>
        </div>
      )}
      {/* An object watch reads back WHAT it is seeing, per class it was
          asked about: person: 2 / chair: 0. The classes come from the Class
          Filters wired in; the numbers are the per-class stats the engine
          publishes for exactly this readout. */}
      {["analytics.object", "analytics.object_frame", "analytics.object_zone"]
        .includes(props.type)
        && (objClasses?.get(props.id) || []).length > 0 && (
        <div style={{ marginTop: 2 }}>
          {(objClasses.get(props.id) || []).slice(0, 6).map((c) => {
            const key = "fn_" + (d.fnId || "") + "_" + String(c).toLowerCase()
              .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
            const n = values[key];
            return (
              <div key={c} style={{ display: "flex", gap: 8, fontSize: 11 }}>
                <span style={{ color: "var(--muted-text, #999)" }}>{c}</span>
                <strong style={{ marginLeft: "auto",
                                 color: n ? "var(--accent, #04bade)"
                                          : "var(--muted, #666)" }}>
                  {n === undefined ? "-" : n}
                </strong>
              </div>
            );
          })}
        </div>
      )}
      {props.type === "analytics.block" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          {(d.blueprint?.nodes || []).length > 1
            ? `${d.blueprint.nodes.length} blueprint nodes · double-click to edit`
            : "double-click to build the blueprint"}
        </div>
      )}
      {props.type === "analytics.expr" && (
        <div className="mono" style={{ fontSize: 10, color: "var(--muted-text, #999)",
                                       overflow: "hidden", textOverflow: "ellipsis",
                                       whiteSpace: "nowrap" }}>
          {d.expr || "…"}
        </div>
      )}
      {props.type === "analytics.proximity" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          {(d.class_a || "any")} ↔ {(d.class_b || "any")} · closer than{" "}
          {Math.round((d.max_distance ?? 0.15) * 100)}%
        </div>
      )}
      {props.type === "analytics.trails" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          dotted trail behind{" "}
          {(d.classes || []).length ? (d.classes || []).join(", ") : "every object"}
          {" · "}{d.length_s ?? 10}s
        </div>
      )}
      {/* The trail's three published values, live on the card -- what there
          is to wire, with its current number next to it. `active` is the 0/1
          bit; the other two are counts. Shown once the node has compiled
          (fnId minted on save). */}
      {props.type === "analytics.trails" && d.fnId && (
        <div style={{ marginTop: 2 }}>
          {[["tracked", "leaving a trail"],
            ["active", "moving now (0/1)"],
            ["idle", "idle now"]].map(([m, label]) => {
            const n = values["fn_" + d.fnId + "_" + m];
            return (
              <div key={m} style={{ display: "flex", gap: 8, fontSize: 11 }}>
                <span style={{ color: "var(--muted-text, #999)" }}>
                  <code>{m}</code> · {label}
                </span>
                <strong style={{ marginLeft: "auto",
                                 color: n ? "var(--accent, #04bade)"
                                          : "var(--muted, #666)" }}>
                  {n === undefined ? "-" : n}
                </strong>
              </div>
            );
          })}
        </div>
      )}
      {props.type === "analytics.guard" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          around {(d.classes || []).length ? (d.classes || []).join(", ") : "…"} ·{" "}
          {d.margin_pct ?? 50}% bubble
          {(d.alert_classes || []).length ? ` · on ${(d.alert_classes || []).join(", ")}` : ""}
          {d.touch === "overlap"
            ? ` · bubbles touch${d.min_overlap_pct ? ` ≥${d.min_overlap_pct}%` : ""}`
            : " · centre inside"}
        </div>
      )}
      {props.type === "analytics.dwell" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          {/* Mode, then SCOPE -- the zone port is optional on this node now,
              and "time present · whole frame" is a different measurement from
              "time present · loading bay". */}
          {({ countdown: "time budget each arrival spends",
              present: "how long objects stay",
              dwell: "how long objects stay",
              timer: "how long objects stay",
              stamps: "how long objects stay",
              occupancy: "clock runs while occupied",
            }[d.mode || "present"] || "how long objects stay")
            + (["countdown", "present", "dwell", "timer", "stamps"]
               .includes(d.mode || "present")
              ? (zoneWired ? " · in the zone wired in" : " · whole frame")
              : "")}
        </div>
      )}
      {props.type === "analytics.violations" ? (
        (d.mode || "realtime") === "realtime" ? (
          // The realtime mode is plain occupancy -- nothing per-object fires,
          // so the amber "+1 per new object" line would describe a behaviour
          // this mode does not have.
          <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
            objects in the zone right now
          </div>
        ) : (
          <div style={{ fontSize: 10, color: "var(--warn-text)" }}>
            ⚡ +1 per new object past {d.limit ?? 1} · {d.severity === "crit" ? "violation" : "alert"}
          </div>
        )
      ) : props.type === "analytics.crowd" ? (
        <div style={{ fontSize: 10, color: "var(--warn-text)" }}>
          ⚡ more than {d.limit ?? 4} → {d.severity === "crit" ? "violation" : "alert"}
        </div>
      ) : d.trigger && <div style={{ fontSize: 10, color: "var(--warn-text)" }}>⚡ trigger set</div>}

      {/* A minimum stay is a real change to what the number MEANS -- a count
          of "people who stayed 5s" is a different measurement from a count of
          "people in shot" -- so it is on the node's face, not folded into
          Advanced where it was set. */}
      {(Number(d.enter_after_s) > 0 || Number(d.leave_after_s) > 0) && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          ⏱ {Number(d.enter_after_s) > 0
               ? `stays ${d.enter_after_s}s to count` : "counts at once"}
          {Number(d.leave_after_s) > 0 ? ` · holds ${d.leave_after_s}s` : ""}
        </div>
      )}

      <AlertChips id={props.id} />
    </NodeShell>
  );
});

/**
 * "This feeds <Alert>" -- shown on the node that raises it.
 *
 * A measurement and the alarm it raises are ONE rule drawn as two nodes, and
 * the only thing joining them is a wire. On a canvas big enough to need
 * scrolling, that wire leaves the screen and the counter stops saying what it
 * is for; worse, a counter wired to nothing looks identical to one wired to
 * an alert somewhere off to the right.
 *
 * Named, not counted: "raises Zone 1 breach" is the fact. "1 alert" would be
 * a number that still needs the wire followed to be of any use.
 */
function AlertChips({ id }) {
  const { alertsBySource } = useContext(WorkflowCtx);
  const names = (alertsBySource && alertsBySource.get(id)) || [];
  if (!names.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginTop: 3 }}>
      {names.slice(0, 3).map((name) => (
        <span key={name}
              title={`Wired to the Condition "${name}" - this is what raises it`}
              style={{ display: "inline-flex", alignItems: "center", gap: 3,
                       fontSize: 9.5, padding: "1px 6px", borderRadius: 999,
                       color: "var(--warn-text)", background: "rgba(245,158,11,.14)",
                       border: "1px solid rgba(245,158,11,.4)",
                       maxWidth: 130, overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          ⚠ {name}
        </span>
      ))}
      {names.length > 3 && (
        <span style={{ fontSize: 9.5, color: "var(--muted-text, #999)" }}>
          +{names.length - 3}
        </span>
      )}
    </div>
  );
}

// Mirrors the compiler's _slug, so the key this card reads is the key the
// wired store actually publishes under.
export const slug = (t) => String(t || "").trim().toLowerCase()
  .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);

const VariableNode = memo(function VariableNode(props) {
  const { values, incoming, clock } = useContext(WorkflowCtx);
  const d = props.data || {};
  // The node's declared role, which the operator can now set explicitly
  // ("Use as" in the inspector). "auto" is the original behaviour: whether
  // anything is wired in decides.
  const io = String(d.io || "auto").toLowerCase();
  const fedByWire = props.type === "variable.ref"
    && !!incoming && incoming.has(props.id);
  const wired = props.type === "variable.ref"
    && (io === "out" || io === "both" || (io === "auto" && fedByWire));
  // Live value: a wired Variable STORES under its own name; an unwired one
  // reads the key it holds; the makers read their own fn value.
  const v = props.type === "variable.ref"
    ? (wired
        ? (slug(d.name) ? values[`fn_${slug(d.name)}_value`] : undefined)
        : (d.key ? values[d.key] : undefined))
    // A Schedule publishes no `value`: what it has to say is WHEN it next
    // comes round, which is the one thing a schedule cannot be checked for by
    // looking at it.
    : props.type === "variable.schedule"
      ? undefined
      : (d.fnId ? values[`fn_${d.fnId}_value`] : undefined);
  const nextRun = props.type === "variable.schedule" && d.fnId
    ? values[`fn_${d.fnId}_next_s`] : undefined;
  // WHAT the variable holds: declared on the node (Type in the inspector),
  // else read off the live value -- a word is text, 12 is whole, 4.5 decimal.
  const KIND_LABEL = { text: "text", decimal: "decimal", number: "whole number" };
  const kind = KIND_LABEL[d.value_type] ? d.value_type
    : typeof v === "string" ? "text"
    : typeof v === "number" ? (Number.isInteger(v) ? "number" : "decimal")
    : "";
  return (
    // The holder kind takes a wire IN (it can hold what a rule outputs);
    // the makers (constant, formula, API, MQTT) produce their own value.
    <NodeShell {...props}
               // "store" only saves what is wired in: it has an input point
               // and nothing to wire out.
               hasSource={!(props.type === "variable.ref" && io === "out")}
               // A reader takes no wire IN -- offering a handle it will
               // ignore is how the node ends up meaning something the
               // operator did not choose. The one exception is a Modbus
               // Read set to interval 0: it reads WHEN the trigger port
               // rises (an Inject, a Schedule, a Condition), so that port
               // exists exactly while the interval is 0 (client UAT item 5).
               targetHandles={props.type === "variable.ref" && io !== "in"
                 ? ["in"]
                 : props.type === "variable.rs485" && Number(d.interval_s ?? 5) === 0
                   ? [{ id: "trig", label: "Trigger" }] : []}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" }}>
          {props.type === "variable.ref"
            ? (d.name || "variable")
            : (d.fnId || d.name || "variable")}
        </span>
        <strong style={{ marginLeft: "auto", fontSize: 15, color: "var(--accent-text, #04bade)" }}>
          {props.type === "variable.schedule"
            ? (nextRun === undefined ? "-" : `in ${countdown(nextRun)}`)
            // A variable may hold a WORD (a text Set Variable): shown as it
            // is. Rounding a string gave NaN, and before that the word never
            // arrived at all.
            : v !== undefined ? (typeof v === "string" ? (v === "" ? "-" : v) : Math.round(v * 100) / 100)
            : props.type === "variable.number"
              // A raw boolean renders as NOTHING in JSX (React skips
              // true/false children), so it needs a real string here or a
              // constant switched to on/off would show a blank value.
              ? (d.value_type === "boolean" ? (d.value ? "on" : "off")
                : (d.value ?? 0))
              : "…"}
        </strong>
      </div>
      {props.type === "variable.ref" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {wired ? (slug(d.name) ? `stores it as ${slug(d.name)}`
                                 : "name the variable that stores it")
                 : ((d.key ? String(d.key).replace(/^fn_(.+)_value$/, "$1") : "")
                    || (io === "in" ? "choose a value to read"
                                          : "choose a value, or wire one in"))}
          {kind && <span> · {KIND_LABEL[kind]}</span>}
        </div>
      )}
      {props.type === "variable.api" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {apiHost(d.url)} · every {d.interval_s ?? 5}s
        </div>
      )}
      {props.type === "variable.rs485" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          FC{d.fc ?? 3} · unit {d.unit === "" || d.unit == null
            ? ("server" in d ? "server's" : 1) : d.unit} · addr {d.address ?? 0}
          {(d.quantity ?? 1) > 1 ? ` ×${d.quantity}` : ""}
          {Number(d.interval_s ?? 5) === 0 ? " · on trigger" : ""}
          {"server" in d && !d.server ? " · no server" : ""}
        </div>
      )}
      {props.type === "variable.schedule" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {scheduleSentence(d)}
          {nextRun === undefined ? "" : ` · next ${nextRunAt(nextRun, clock)}`}
        </div>
      )}
      {props.type === "variable.board_input" && (
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {d.port || "choose a terminal"}
          {d.invert ? " · normally closed" : ""}
        </div>
      )}
    </NodeShell>
  );
});

//: Weekday names for a Schedule, Monday first -- the order params.weekday
//: counts in (0 = Monday), shared with the inspector so the node and the
//: dropdown cannot drift apart.
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
                         "Friday", "Saturday", "Sunday"];

/** "every day at 12:00" -- what a Schedule does, as a sentence. */
export function scheduleSentence(d) {
  const at = d.at || "12:00";
  const day = d.day ?? 1;
  switch (String(d.every || "daily")) {
    case "minutes": return `every ${d.interval_min ?? 15} min`;
    case "hourly": return `hourly at :${String(d.minute ?? 0).padStart(2, "0")}`;
    case "weekly": return `every ${WEEKDAYS[d.weekday ?? 0]} at ${at}`;
    case "monthly": return day === 0 ? `last day of the month at ${at}`
                                     : `day ${day} of the month at ${at}`;
    default: return `every day at ${at}`;
  }
}

/** When the next run lands, on the BOX's clock: "today 12:00", "Mon 08:00".
 *
 *  Built from the engine's own countdown rather than from the schedule fields,
 *  so the month-length and weekday rules live in ONE place (the engine) and
 *  the canvas cannot drift from what actually fires. `clock.now` anchors it to
 *  the box: the browser reading this page may be in another country, and a
 *  report labelled "today 12:00" in the wrong zone is worse than no label.
 */
export function nextRunAt(seconds, clock) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const [h, m] = String(clock?.now || "").split(":").map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return `in ${countdown(s)}`;
  const minsNow = h * 60 + m;
  const minsThen = minsNow + Math.floor(s / 60);
  const hh = String(Math.floor((minsThen % 1440) / 60)).padStart(2, "0");
  const mm = String(minsThen % 60).padStart(2, "0");
  const days = Math.floor(minsThen / 1440);
  if (days === 0) return `today ${hh}:${mm}`;
  if (days === 1) return `tomorrow ${hh}:${mm}`;
  return `in ${countdown(s)}`;
}

/** Seconds as the biggest unit that still says something: "2h 5m", "40s". */
export function countdown(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

// Host only -- a full URL with a query string does not fit the node box, and
// the host is the part that says WHICH machine is being read.
function apiHost(url) {
  if (!url) return "no URL";
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

// The 13 ways a When condition can read a value. Shared with the inspector so
// the sentence on the node and the dropdown can never drift apart.
export const WHEN_MODES = [
  { key: "above",        label: "goes above",              phrase: (a) => `> ${a}` },
  { key: "at_least",     label: "reaches at least",        phrase: (a) => `≥ ${a}` },
  { key: "below",        label: "drops below",             phrase: (a) => `< ${a}` },
  { key: "at_most",      label: "is at most",              phrase: (a) => `≤ ${a}` },
  { key: "equals",       label: "equals",                  phrase: (a) => `= ${a}` },
  { key: "not_equals",   label: "does not equal",          phrase: (a) => `≠ ${a}` },
  { key: "is_true",      label: "is true (non-zero)" },
  { key: "is_false",     label: "is false (zero)" },
  { key: "rising",       label: "becomes true (rising edge)" },
  { key: "falling",      label: "becomes false (falling edge)" },
  { key: "changed",      label: "changes at all" },
  { key: "in_range",     label: "is inside a range",
    phrase: (a, b) => `between ${a} and ${b}` },
  { key: "out_of_range", label: "is outside a range",
    phrase: (a, b) => `outside ${a}…${b}` },
];

export const WHEN_NEEDS_VALUE = ["above", "at_least", "below", "at_most",
                                 "equals", "not_equals", "in_range", "out_of_range"];
export const WHEN_NEEDS_VALUE2 = ["in_range", "out_of_range"];

/** "when count ≥ 3 for 5s" - the node's whole configuration as one sentence. */
export function whenSentence(d) {
  const data = d || {};
  const mode = WHEN_MODES.find((m) => m.key === (data.mode || "above")) || WHEN_MODES[0];
  const tail = mode.phrase
    ? mode.phrase(data.value ?? 0, data.value2 ?? 0) : mode.label;
  const hold = (data.hold_s ?? 0) > 0 ? ` for ${data.hold_s}s` : "";
  return `when ${data.metric || "value"} ${tail}${hold}`;
}

const WhenNode = memo(function WhenNode(props) {
  const d = props.data || {};
  // A Switch is the branching kind of logic: labelled ways out, and a value
  // leaves by the first branch it passes. Its PORTS follow its settings --
  // one output per branch, and in AND/OR shape one input per condition -- so
  // adding a condition in the inspector is what adds the port here.
  // A BRANCH node: labelled ways out, and exactly one of them is lit at a
  // time -- a value leaves by the first test it passes. Its PORTS follow its
  // settings (one output per branch, and in the AND/OR shape one input per
  // condition), so adding a condition in the inspector is what adds the port
  // here.
  //
  // The shape comes from the TYPE for the five branch nodes and from stored
  // `ctype` only for the deprecated Switch, so one component draws all six
  // and they cannot drift apart.
  if (isBranch(props.type)) {
    const ctype = branchCtype(props.type, d);
    const fixed = BRANCH_PORTS[props.type] || null;
    const rules = (d.rules || []).slice(0, 8);
    const ruleLabel = (r) => (r.op === "between"
      ? (r.value ?? 0) + " - " + (r.value2 ?? 0)
      : (r.op || ">") + " " + (r.rhs_input
          ? String(r.rhs_input).replace(/^in(\d+)$/, "input$1")
          : (r.value ?? 0)));

    if (ctype === "multi") {
      // The combined test has ONE answer, so two ways out; the ways IN are
      // the rows -- each condition's port label carries its own test, so the
      // wiring reads off the node: "in1 > 3, in2 > 7 -> all pass".
      const seen = new Map();
      for (const r of rules) {
        const h = r.input || "in1";
        if (!seen.has(h)) seen.set(h, ruleLabel(r));
      }
      const inWord = (h) => String(h).replace(/^in(\d+)$/, "input$1");
      const ins = [...seen].map(([h, test]) => (
        { id: h, label: inWord(h) + " " + test }));
      // The growth port: drop a wire on + and a real input is minted under
      // it (Workflow.jsx onConnect retargets the edge, so no stored wire
      // ever holds the + id).
      if (rules.length < 8) ins.push({ id: "in_new", label: "+" });
      // Pass / Fail on the Multi node: two words that say what the port
      // CARRIES. "all pass" described the test rather than the way out, and
      // "otherwise" said nothing at all about a two-way answer.
      const outs = [{ id: "out0",
                      label: fixed ? fixed[0]
                        : d.join === "or" ? "any pass" : "all pass" }];
      if (d.otherwise !== false) {
        outs.push({ id: "out1", label: fixed ? fixed[1] : "otherwise" });
      }
      return (
        <NodeShell {...props} title={d.name !== "Switch" ? d.name : undefined}
                   targetHandles={ins} sourceHandles={outs}
                   footer={`${d.join === "or" ? "ANY" : "ALL"} of ${rules.length} must hold`}>
          <div style={{ minHeight: Math.max(ins.length, outs.length) * 18 }} />
        </NodeShell>
      );
    }

    if (ctype === "compare") {
      // IF / ELSE IF / ELSE: each row reads its OWN value and leaves by its
      // own way out, so the ports come in pairs down the node and the labels
      // carry the test -- the branch order is the logic and has to be read
      // off the node, not remembered.
      const ins = [];
      const seen = new Set();
      const inWord = (h) => String(h).replace(/^in(\d+)$/, "input$1");
      const addIn = (h) => {
        if (!h || seen.has(h)) return;
        seen.add(h);
        ins.push({ id: h, label: inWord(h) });
      };
      for (const r of rules) {
        addIn(r.input || "in1");
        for (const m of r.more || []) addIn(m.input || "in1");
      }
      // A right-hand side wired from another value needs its port too.
      for (const r of rules) {
        addIn(r.rhs_input);
        for (const m of r.more || []) addIn(m.rhs_input);
      }
      if (rules.length < 8) ins.push({ id: "in_new", label: "+" });
      // A plain Compare has ONE test and two ways out, and they are called
      // True and False -- "if count > 4" on a port said the test again where
      // the answer belonged. The multi-row shape keeps the test on the label,
      // because there the branch ORDER is the logic and has to be readable
      // off the node rather than remembered.
      const outs = fixed
        ? [{ id: "out0", label: fixed[0] }]
        // "if" / "elif" / "else", the words the logic is written in
        // everywhere else. "Case 1:" is a SWITCH's vocabulary -- it numbers
        // arms of one value's match -- and this node tests a different
        // expression per row, so borrowing the word said the wrong thing.
        // Match (branch.match) is the switch, and keeps `case`.
        : rules.map((r, i) => ({
            id: "out" + i,
            label: (i === 0 ? "if " : "elif ") + inWord(r.input || "in1") + " "
                   + ruleLabel(r)
                   + ((r.more || []).length
                     ? " " + (r.more || []).map((m) =>
                         (m.link === "or" ? "or " : "and ")
                         + inWord(m.input || "in1") + " " + ruleLabel(m)).join(" ")
                     : ""),
          }));
      if (d.otherwise !== false) {
        outs.push({ id: "out" + (fixed ? 1 : rules.length),
                    label: fixed ? fixed[1] : "else" });
      }
      return (
        <NodeShell {...props} title={d.name !== "Switch" ? d.name : undefined}
                   targetHandles={ins} sourceHandles={outs}
                   footer={fixed
                     ? "one of the two is lit, never both"
                     : "the first case that holds wins, top to bottom"}>
          <div style={{ minHeight: Math.max(ins.length, outs.length) * 18 }} />
        </NodeShell>
      );
    }

    // Branches are NUMBERED on their port labels ("1. > 10"), because the
    // whole contract of this node is the order: a value leaves by the first
    // test it passes, and unnumbered tests do not say which one that is.
    const outs = ctype === "boolean"
      ? [{ id: "out0", label: "on" }, { id: "out1", label: "off" }]
      : ctype === "switch"
        ? rules.map((r, i) => ({
            id: "out" + i,
            // Match IS the switch/case node, so it speaks that language:
            // "case 3:" routes the value that equals 3, "default" catches the
            // rest. The If / Else if node keeps if/elif/else.
            label: "case " + ((r.op || "==") === "==" ? "" : (r.op || "") + " ")
                   + (r.value ?? 0) + ":",
          }))
        : rules.map((r, i) => ({
            id: "out" + i,
            label: rules.length > 1 ? (i + 1) + ". " + ruleLabel(r) : ruleLabel(r),
          }));
    if (!["boolean"].includes(ctype) && d.otherwise !== false) {
      // The catch-all NAMES what it catches where it can: on bands that is
      // everything above the last one, and "Above 40" is a band the way the
      // others are. "default" described the port's role in the compiler.
      const last = rules[rules.length - 1] || {};
      outs.push({
        id: "out" + rules.length,
        label: ctype === "switch" ? "default"
          : ctype === "range" && last.op === "between"
            ? "Above " + (last.value2 ?? 0)
            : "Otherwise",
      });
    }
    const word = { boolean: "routes on != 0 / = 0",
                   range: "the first band that fits; exactly one is lit",
                   switch: "the first case that matches; exactly one is lit"
                 }[ctype]
      || (rules.length > 1 ? "first match wins, top to bottom" : "one test");
    return (
      <NodeShell {...props} title={d.name !== "Switch" ? d.name : undefined}
                 sourceHandles={outs} footer={word}>
        <div style={{ minHeight: outs.length * 18,
                      display: "flex", alignItems: "center" }}>
          <span className="mono">{d.metric || "value"} in -&gt;</span>
        </div>
      </NodeShell>
    );
  }
  // A Delay is the other kind of logic node: it decides WHEN what follows
  // happens, not WHETHER, so it has no condition line and no severity.
  if (props.type === "logic.catch") {
    return (
      <NodeShell {...props}>
        <div>when the rule before this one throws</div>
        <div style={{ fontSize: 10, color: "var(--warn-text)" }}>
          ⚡ {d.severity || "warn"} · cooldown {d.cooldown_s ?? 60}s
        </div>
      </NodeShell>
    );
  }
  if (props.type === "logic.delay") {
    const secs = Number(d.delay_s ?? 5);
    return (
      <NodeShell {...props}>
        <div>
          wait <strong>{Number.isFinite(secs) ? secs : 5}s</strong>, then carry on
        </div>
      </NodeShell>
    );
  }
  return (
    <NodeShell {...props}>
      <div>{whenSentence(d)}</div>
      <div style={{ fontSize: 10, color: "var(--warn-text)" }}>
        ⚡ {d.severity || "warn"} · cooldown {d.cooldown_s ?? 10}s
      </div>
    </NodeShell>
  );
});

const GRAPH_KIND_LABEL = { line: "line chart", area: "area chart",
                           spline: "spline chart", bar: "bar chart" };

// Mirror of workflow.py GRAPH_SERIES_HANDLES: the chart's series ports, in
// the order they are drawn. The first is the plain `in` port -- every chart
// already drawn in the field is wired on it, so series 1 keeps that id and
// only the ports added afterwards are named for their series.
export const GRAPH_SERIES_HANDLES = ["in", "s2", "s3", "s4", "s5", "s6",
                                    "s7", "s8"];

const DashboardNode = memo(function DashboardNode(props) {
  const { registry, incoming, fanIn, edges: ctxEdges } = useContext(WorkflowCtx);
  const spec = registry[props.type] || {};
  const d = props.data || {};
  // Several values wired in draw as separate series on the chart shapes --
  // the node says so, because "which of my three wires is it showing" is
  // otherwise answered by opening the dashboard.
  const wires = (fanIn && fanIn.get(props.id)) || 0;
  const seriesNote = wires > 1
    ? `${wires} series, one per wired value` : null;
  // A card or chart ASKING RECORDED DATA (the recorded event store, or a
  // table named on it -- what the retired Event Card was) normally takes no
  // input, so it shows what it counts instead of which metric it is wired to.
  // The Event Card itself still draws here, for a canvas not yet migrated.
  const recorded = recordedMode({ type: props.type, data: d });
  // Which shape it draws, said first: the node type no longer tells a
  // recorded Card from a recorded Chart at a glance.
  const shapeWord = props.type === "dashboard.graph"
    ? (GRAPH_KIND_LABEL[d.kind] || `${d.kind || "area"} chart`)
    : ({ tile: "stat tile", table: "recent rows", gauge: "gauge",
         bar: "bar chart", line: "line chart", donut: "donut" }[d.kind]
       || "stat tile");
  // A migrated "metric" card that already feeds a Chart keeps its output
  // port: the wire must have somewhere to attach, or it vanishes from the
  // canvas while still drawing a line on the dashboard.
  const feedsChart = (ctxEdges || []).some((e) => e.source === props.id);
  if (props.type === "dashboard.events"
      && !recorded && d.source_mode === "metric") {
    const bits = [d.metric || "…", d.range || "all time"];
    return (
      <NodeShell {...props} title={d.name}>
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>{bits.join(" · ")}</div>
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          charts the wired value
        </div>
      </NodeShell>
    );
  }
  if (recorded) {
    const wired = !!incoming && incoming.has(props.id);
    // The ports wires actually sit on, `in` first -- a Chart keeps its s2..s8
    // wires when switched to recorded data, and a port that is not drawn is
    // a wire that vanishes from the canvas while still being compiled.
    const recordedPorts = ["in", ...new Set((ctxEdges || [])
      .filter((e) => e.target === props.id && (e.targetHandle || "in") !== "in")
      .map((e) => e.targetHandle))]
      .map((h) => ({ id: h, label: h === "in" ? "scope" : h }));
    const bits = recorded === "dataset" ? [
      shapeWord,
      d.dataset ? `table ${d.dataset}` : "choose a table",
      d.group_by ? `by ${d.group_by}` : (d.bucket ? `per ${d.bucket}` : null),
      d.range || "all time",
    ].filter(Boolean) : [
      shapeWord,
      d.agg === "unique" ? `distinct ${d.field || "…"}` : "count",
      (d.filters || {}).type ? `${d.filters.type}s` : null,
      d.group_by ? `by ${d.group_by}` : null,
      d.bucket ? `per ${d.bucket}` : null,
      d.range || "all time",
    ].filter(Boolean);
    return (
      // The name rides the HEADER; repeating it in the body was the same
      // string twice on one small node.
      //
      // It has an OUTPUT port, for one wire only: into a Chart, where the
      // question this card counts becomes one series beside the live rules.
      // Nothing else on the canvas states a question about recorded events,
      // so without the port a chart could never be given one (edgeAllowed in
      // registry.js keeps the wire to the Chart). ONE input, too, even on a
      // Chart: a rule wired in scopes the question, it is not a series --
      // but every port a wire still sits on is drawn, so a Chart switched
      // over with rules on its series ports shows every one of them (the
      // compiler counts the first and names the rest).
      <NodeShell {...props} title={d.name}
                 targetHandles={recordedPorts.length > 1 ? recordedPorts : undefined}>
        <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>{bits.join(" · ")}</div>
        {recorded === "dataset" ? null : wired ? (
          <div style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
            {wires > 1 ? `${wires} wires - counts what the first raised`
                       : "counts what the wired function raised"}
          </div>
        ) : (
          // Unwired this card counts EVERY recorded event - that is a real
          // choice, but it must not look like an accident either way.
          <span style={{ display: "inline-block", marginTop: 3, fontSize: 9.5,
                         padding: "1px 6px", borderRadius: 999, color: "var(--warn-text)",
                         background: "rgba(245,158,11,.14)",
                         border: "1px solid rgba(245,158,11,.4)" }}>
            all events
          </span>
        )}
      </NodeShell>
    );
  }
  if (props.type === "dashboard.graph") {
    // SERIES PORTS, one per series, named and coloured.
    //
    // The chart has always drawn one series per wired value -- and said so
    // only AFTER the second wire existed, on a node that showed a single
    // unnamed port and invited nothing. So nobody found it. A labelled port
    // per series is the invitation, and the + port mints the next one under
    // a dropped wire exactly as a Formula grows operands (see Workflow.jsx
    // onConnect).
    //
    // Series 1 is the plain `in` port: every chart already wired keeps its
    // wire, and the compiler gives that wire the id "s1".
    const conf = d.series || {};
    // Never fewer ports than there are wires: a graph migrated onto the
    // named handles (or one the operator shrank) must still show every
    // series it actually draws.
    const wiredSlot = (ctxEdges || []).reduce((mx, e) => (
      e.target !== props.id ? mx
        : Math.max(mx, GRAPH_SERIES_HANDLES.indexOf(e.targetHandle || "in") + 1)), 0);
    const slots = Math.max(2, wiredSlot,
                           Math.min(8, parseInt(d.series_n, 10) || 2));
    const ports = GRAPH_SERIES_HANDLES.slice(0, slots).map((h, i) => {
      const cfg = conf[`s${i + 1}`] || {};
      return { id: h, label: cfg.label || `Series ${i + 1}`,
               ...(cfg.color ? { color: cfg.color } : {}) };
    });
    if (slots < GRAPH_SERIES_HANDLES.length) ports.push({ id: "in_new", label: "+" });
    return (
      <NodeShell {...props} hasSource={feedsChart} title={d.name}
                 targetHandles={ports} targetHeading="Series">
        <Muted>
          {GRAPH_KIND_LABEL[d.kind] || `${d.kind || "area"} chart`}
          {seriesNote ? ` · ${seriesNote}` : (d.metric ? ` · ${d.metric}` : "")}
        </Muted>
      </NodeShell>
    );
  }
  // The card node says which shape it is, since that is the only thing that
  // distinguishes it from the next card along.
  if (props.type === "dashboard.card") {
    return (
      <NodeShell {...props} hasSource={feedsChart} title={d.name}>
        <Muted>
          {{ tile: "stat tile", gauge: "gauge", status: "status pill" }[d.kind]
            || "stat tile"}
          {d.metric ? ` · ${d.metric}` : ""}
        </Muted>
      </NodeShell>
    );
  }
  if (props.type === "dashboard.verdict"
      || (props.type === "dashboard.card" && d.kind === "verdict")) {
    const custom = Array.isArray(d.states) && d.states.length > 0;
    return (
      <NodeShell {...props} hasSource={false}>
        <Muted>
          {custom
            ? `${d.states.length} states: ${d.states.map((s) => s.label).join(" / ")}`
            : `${d.ok_label || "COMPLIANT"} / ${d.alert_label || "VIOLATION"}`}
          {d.metric ? ` · ${d.metric}` : " · active"}
        </Muted>
      </NodeShell>
    );
  }
  return (
    <NodeShell {...props} hasSource={false}>
      <Muted>{d.metric ? `metric: ${d.metric}` : spec.description}</Muted>
    </NodeShell>
  );
});

/** What each column type looks like at a glance, the way a database tool
 *  marks them. A row of identical grey names hides the one fact that decides
 *  what you can chart. */
const COL_MARK = { number: "#", text: "T", datetime: "⏱" };

/**
 * A Database Entity node: the table itself, one row per column, and every
 * column independently connectable on BOTH sides.
 *
 *     ○──── column_name ────○
 *     ^                     ^
 *     write into it         read it back out
 *
 * The layout is the interaction model, so it is fixed: left port, centred
 * name, right port, never stacked. A port above or below its name stops
 * saying WHICH column it belongs to the moment the node has more than three
 * rows, which is exactly when it matters.
 *
 * There is deliberately no single node-wide input or output. A table is not
 * one value, and one connector for six columns would put the mapping back in
 * an inspector -- the wire IS the assignment here.
 *
 * Handles are named after the column (c_<name> in, o_<name> out) rather than
 * numbered, so reordering the table cannot silently re-point a wire that is
 * already drawn.
 */
const DatasetNode = memo(function DatasetNode(props) {
  const { registry, variables, errors, warnings, offNodes, wired } = useContext(WorkflowCtx);
  const isWired = (kind, hid) => !!wired
    && wired.has(`${props.id}|${kind === "source" ? "s" : "t"}|${hid || ""}`);
  const spec = registry[props.type] || {};
  const style = CATEGORY_STYLE.database;
  const d = props.data || {};
  const table = (variables.tables || []).find((t) => t.key === d.dataset);
  const columns = table?.columns || [];
  const sources = d.sources || {};
  const alertMode = (d.mode || "alert") === "alert";
  // The trigger port exists for the two modes whose row is FIRED by
  // something wired in -- an alert, or a Line Counter's crossing. The clock
  // modes have nothing to wire there.
  const trigPort = alertMode || d.mode === "crossing";
  const err = errors[props.id];
  const warn = warnings[props.id];
  const off = !!offNodes && offNodes.has(props.id);

  const op = d.op || "insert";
  const when = op === "select" ? "read only"
    : { alert: "on alert", every: `every ${d.interval_s ?? 60}s`,
        change: "on change", crossing: "on crossing" }[d.mode || "alert"];
  // This node draws its own ports (one pair per column) rather than going
  // through NodeShell, so it re-measures for itself: adding, renaming or
  // reordering a column changes every handle below it, and React Flow does
  // not notice on its own (see useHandleSync).
  useHandleSync(props.id, columns.map((c) => c.name).join(",") + `|${trigPort}`);

  // Header, trigger row, then the columns. Each row is a fixed height so the
  // handle beside it lines up with its own name rather than being spread
  // evenly down a box.
  const ROW = 22;
  return (
    <div style={{
      // NO `overflow: hidden`. A port sits half outside the node's edge --
      // that is what makes it look like a socket on the border rather than a
      // dot inside the card -- so clipping the overflow clipped every port
      // off. The column handles were all there and wired, and none of them
      // could be seen: a Database node looked like the one node type with no
      // way in or out. The rounded corners it was there for are done by the
      // header and footer radiusing themselves instead.
      minWidth: 210, borderRadius: 10,
      background: "var(--surface, #16181d)",
      border: `1.5px solid ${err ? "#ef4444"
        : props.selected ? "var(--accent, #04bade)" : style.color + "66"}`,
      boxShadow: props.selected
        ? "0 0 0 3px color-mix(in srgb, var(--accent, #04bade) 25%, transparent)"
        : "var(--card-shadow, none)",
      fontSize: 12, color: "var(--text, #e8eaed)",
      opacity: off && !props.selected ? 0.5 : 1,
    }}>
      {/* No trigger port. "Write a row" on the node already says WHEN, and the
          column wires already say which rules feed it -- so which alert to
          fire on is derivable rather than something to wire twice. A port
          that only restates a dropdown is a port that can disagree with it. */}
      <div style={{ display: "flex", alignItems: "center",
                    gap: 6, padding: "6px 10px",
                    background: style.color + "22",
                    // Its own top corners, now that the card no longer clips
                    // them for it. 9 rather than 10: the card's 1.5px border
                    // sits outside this fill.
                    borderRadius: "9px 9px 0 0",
                    borderBottom: "1px solid rgba(128,128,128,.15)" }}>
        <style.icon size={13} style={{ color: style.color, flexShrink: 0 }} />
        <strong style={{ overflow: "hidden", textOverflow: "ellipsis",
                         whiteSpace: "nowrap" }}>
          {table?.name || d.dataset || spec.label}
        </strong>
        {/* The name a QUERY has to type, whenever it differs from the name on
            the header. A table called "PCB-SENSATA-CAM1" is `pcb_sensata` in
            SQL -- a display name may carry spaces, dashes and capitals, and a
            table name may not -- and a Query node written from the header
            fails with "relation does not exist", naming the one thing the
            canvas never showed. */}
        {table && d.dataset && table.name !== d.dataset && (
          <code style={{ fontSize: 9.5, opacity: 0.75, flexShrink: 0,
                         padding: "0 4px", borderRadius: 4,
                         border: "1px solid rgba(128,128,128,.3)" }}
                title="the name to use in a Query">
            {d.dataset}
          </code>
        )}
        {/* WHEN to write, on the header rather than among the columns --
            because it is the one input that is not a value. A rule wired
            here fires the row; the columns then only say what the row
            holds. Left unwired, the columns' own rules fire it, which is
            how every table built before this port existed still works. In
            crossing mode the same port names the Line Counter whose line
            the table records, with the same fallback to the column wires. */}
        {table && trigPort && (
          <Handle type="target" position={Position.Left} id="trig"
                  title={alertMode
                    ? "what fires the row - wire a Condition, Alert or Trigger here. Leave it empty and the columns' own rules fire it."
                    : "what fires the row - wire the Line Counter whose line this table records. Leave it empty and the Line Counter wired into a column fires it."}
                  style={portStyle("target", isWired("target", "trig"),
                                   style.color, { top: 20 })} />
        )}
        {/* The whole table, beside its name. A column port hands out one
            value; this hands out the rows -- which is what a table card
            wants, and what picking six columns one at a time cannot say. */}
        {table && (
          <Handle type="source" position={Position.Right} id="table"
                  title="the whole table - wire this to a table card"
                  style={portStyle("source", isWired("source", "table"),
                                   style.color, { top: 20 })} />
        )}
        {(err || warn) && (
          <span title={err || warn} style={{ marginLeft: "auto", flexShrink: 0 }}>
            {err ? "🔴" : "🟡"}
          </span>
        )}
      </div>

      {!table ? (
        <div style={{ padding: "8px 10px" }}>
          <Muted>{d.dataset
            ? `no table called ${d.dataset}`
            : "pick a table in the inspector"}</Muted>
        </div>
      ) : !columns.length ? (
        <div style={{ padding: "8px 10px" }}>
          <Muted>this table has no columns yet</Muted>
        </div>
      ) : (
        <div style={{ position: "relative", padding: "3px 0" }}>
          {columns.map((c) => {
            const cfg = sources[c.name] || {};
            // What fills this column, if anything does. A column with nothing
            // attached is not an error -- two nodes may each fill part of one
            // table -- so it reads as empty rather than as broken.
            const filled = cfg.src === "stat" ? "•"
              : cfg.src === "event" ? "•"
              : cfg.src === "const" ? "•"
              : null;
            return (
              <div key={c.name}
                   style={{ position: "relative", height: ROW,
                            display: "flex", alignItems: "center",
                            justifyContent: "center", gap: 5,
                            // Room for a port on each side. The name stays
                            // centred BETWEEN them, which is what makes the
                            // row read as one column with two ends.
                            padding: "0 18px" }}>
                <Handle type="target" position={Position.Left} id={`c_${c.name}`}
                        title={`write into ${c.name}`}
                        style={portStyle("target",
                                         isWired("target", `c_${c.name}`),
                                         style.color, { top: ROW / 2 })} />
                <span className="mono" title={c.type}
                      style={{ fontSize: 9.5, flexShrink: 0,
                               color: "var(--muted-text, #6b6b6b)" }}>
                  {COL_MARK[c.type] || "#"}
                </span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{c.name}</span>
                {filled && (
                  <span title="a value is attached to this column"
                        style={{ fontSize: 9, flexShrink: 0,
                                 color: style.color }}>{filled}</span>
                )}
                <Handle type="source" position={Position.Right} id={`o_${c.name}`}
                        title={`read ${c.name} back out`}
                        style={portStyle("source",
                                         isWired("source", `o_${c.name}`),
                                         style.color, { top: ROW / 2 })} />
              </div>
            );
          })}
        </div>
      )}

      <div style={{ display: "flex", gap: 6, padding: "3px 10px 5px",
                    borderTop: "1px solid rgba(128,128,128,.12)",
                    borderRadius: "0 0 9px 9px",
                    fontSize: 9.5, color: "var(--muted-text, #6b6b6b)" }}>
        <span style={{ color: style.color, fontWeight: 600 }}>{op}</span>
        <span>{when}</span>
      </div>
    </div>
  );
});

//: Outputs that take several rules on one input, each firing the node on
//: its own moment (the compiler writes one output per rule; FAN_IN_OUTPUTS
//: in core/services/workflow.py). The face says so, because two wires into
//: a siren otherwise read as "which one is it?".
const FAN_IN_TYPES = ["output.io_out", "output.buzzer", "output.io_pwm",
                      "output.snapshot", "output.record", "output.webhook",
                      "output.mqtt"];
const FAN_IN_PUBLISHERS = ["output.mqtt_publish", "output.webhook_publish"];

const OutputNode = memo(function OutputNode(props) {
  const { edges: ctxEdges, values, fanIn } = useContext(WorkflowCtx);
  const d = props.data || {};
  const wires = (fanIn && fanIn.get(props.id)) || 0;
  const fanNote = wires > 1 && FAN_IN_TYPES.includes(props.type)
    && !(props.type === "output.io_pwm" && d.when === "always")
    ? `${wires} rules wired in - fires on each of them`
    : wires > 1 && FAN_IN_PUBLISHERS.includes(props.type)
      ? `${wires} values wired in - publishes each of them` : null;
  // A Trigger that recolours a zone needs to be TOLD WHICH, and a wire is
  // how a place is named on this canvas. The port exists only while the
  // colour is chosen -- a port nobody meant to use invites a wire -- but one
  // already wired keeps it whatever the setting says, so no saved graph
  // loses a wire to a checkbox.
  const zoneWired = (ctxEdges || []).some(
    (e) => e.target === props.id && e.targetHandle === "zone");
  const takesZone = props.type === "output.trigger"
    && (!!d.zone_color || (!!d.draw && !!d.box_in_zone) || zoneWired);
  // Same rule for the OID port: it exists while the alarm is set to raise
  // again per NEW object (that setting cannot work without knowing who), or
  // while a wire is already on it.
  const oidWired = (ctxEdges || []).some(
    (e) => e.target === props.id && e.targetHandle === "oid");
  const takesOid = props.type === "output.trigger"
    && ((!!d.alert && d.repeat === "new_object" && !d.rate) || oidWired);
  // The duplicate guard's ports (Tok Aba, 2026-10-13), by the same rule:
  // they exist while "Skip an object the table already holds" is ticked, or
  // while a wire is on them. Current value only in timer mode -- an object
  // already stored is never sent again in watchdog mode, so a value there
  // would be a port that changes nothing.
  const guardWired = (h) => (ctxEdges || []).some(
    (e) => e.target === props.id && e.targetHandle === h);
  const takesCurOid = props.type === "output.trigger"
    && (!!d.dup_guard || guardWired("cur_oid"));
  const takesCurValue = props.type === "output.trigger"
    && ((!!d.dup_guard && !triggerWatchdog(d)) || guardWired("cur_value"));
  const sum = {
    // The broker is the node's MQTT Connection now; only a node saved before
    // Connections still has its own host to show.
    "output.mqtt": d.topic
      ? ("connection" in d ? d.topic : `${d.host || "127.0.0.1"} · ${d.topic}`)
      : null,
    // No clock of its own: the Trigger or Inject wired in says when. With
    // nothing wired in it publishes nothing, and the face says what to wire.
    "output.mqtt_publish": d.topic
      ? d.topic + ((ctxEdges || []).some((e) => e.target === props.id) ? ""
        : " · wire a Trigger or Inject to publish")
      : null,
    "output.webhook": d.url || null,
    "output.webhook_publish": d.url
      ? `${d.url} · every ${d.interval_s ?? 5}s` : null,
    "output.email": d.to ? `to ${d.to}` : null,
    "output.telegram": d.chat_id ? `chat ${d.chat_id}` : null,
    "output.alert": d.metric ? `${d.metric} ${d.op || ">"} ${d.value ?? 1}` : null,
    // What this Trigger IS, in the two words that differ between one and the
    // next: whether it alarms, and whether it paints. An untouched one says
    // "event only" -- which is a real and useful thing to be, not a node
    // waiting to be finished.
    // What this Trigger DOES, in the few words that differ between one and
    // the next. "event only" is the honest description of one that alarms
    // nothing and paints nothing -- but it is wrong for one that paints, so
    // it is said only when there is nothing else to say.
    "output.trigger": triggerWords(d).join(" · ") || "event only",
    "output.snapshot": "photo on alert",
    "output.record": `clip ${Math.min(5, d.duration_s ?? 5)}s on alert`,
    // The terminal and what happens to it: an installer reads the canvas to
    // check the wiring, and "DO0 · pulse 500ms" is the whole answer.
    "output.io_out": d.port
      ? `${d.port} · ${d.action || "on"}`
        + (d.action === "pulse" ? ` ${d.pulse_ms ?? 500}ms` : "")
      : null,
    "output.buzzer": `${d.action || "pulse"}`
      + (d.action !== "off" && (d.pulse_ms ?? 500) ? ` ${d.pulse_ms ?? 500}ms` : ""),
    "output.io_pwm": d.port
      ? `${d.port} · ${d.source === "value" ? "follows value" : `level ${d.level ?? 128}`}`
        + (d.when === "always" ? " · live" : "")
      : null,
    // FC, where, and -- once it has written -- what the wire last saw:
    // "wrote 42 to 0" or the device's error. The app writes these down per
    // output (out_<id>_note / _error, app/outputs.py), and the canvas is
    // where somebody checking a PLC wiring actually looks.
    "output.modbus_write": [
      `FC ${d.fc ?? 6} · addr ${d.address === "" || d.address == null ? "?" : d.address}`
        + ((d.fc === 15 || d.fc === 16) ? ` ×${d.quantity ?? 1}` : ""),
      d.server ? null : "no server",
    ].filter(Boolean).join(" · "),
    "output.rs485": d.register !== undefined
      ? `${d.target === "coil" ? "coil" : "reg"} ${d.register ?? 0} = ${d.value ?? 1}`
        + ` · unit ${d.unit ?? 1}`
      : null,
  }[props.type];
  // A Modbus Write's last result, from the stats the app publishes about each
  // output. Nothing is drawn until it has tried: "never written" is already
  // what the absence says.
  const mbw = props.type === "output.modbus_write" ? (() => {
    const oid = `wf_${props.id}`.slice(0, 40);
    const at = Number(outStat(values || {}, oid, "at")) || 0;
    if (!at) return null;
    const ok = Number(outStat(values || {}, oid, "ok")) === 1;
    const text = ok ? String(outStat(values || {}, oid, "note") || "ok")
      : String(outStat(values || {}, oid, "error") || "failed");
    return { ok, text };
  })() : null;
  return (
    // The Trigger (and the Condition it replaces) is the one output with a
    // way OUT. Everything else here is a terminus -- an email has no value to
    // pass on -- but a named moment is a state, and that state is exactly
    // what a card shows, what a Data Table row is written on, and what a
    // Snapshot fires on. Wiring it onward is how "this is the moment" gets
    // said on the canvas instead of by retyping a metric into every sink.
    <NodeShell {...props}
               // The Trigger's own name IS the moment, so it goes in the
               // header where a name belongs rather than into the body line.
               // A Modbus Write's Name goes there too, as Node-RED draws
               // it: four writes to one PLC are told apart by what they
               // switch, not by "Modbus Write" four times.
               title={["output.trigger", "output.modbus_write"].includes(props.type)
                 ? d.name || undefined : undefined}
               hasSource={["output.alert", "output.trigger"].includes(props.type)}
               targetHandles={props.type === "output.modbus_write"
                 // Node-RED's write takes one msg; here the value and the
                 // moment are two wires, so they are two named ports.
                 ? [{ id: "in", label: "Value" }, { id: "trig", label: "Trigger" }]
                 : (takesZone || takesOid || takesCurOid || takesCurValue)
                 ? [{ id: "in", label: "In" },
                    ...(takesZone ? [{ id: "zone", label: "Zone" }] : []),
                    ...(takesOid ? [{ id: "oid", label: "OID" }] : []),
                    ...(takesCurOid ? [{ id: "cur_oid", label: "Current OID" }] : []),
                    ...(takesCurValue ? [{ id: "cur_value", label: "Current value" }] : [])]
                 : undefined}>
      <Muted>{sum || "configure in the inspector"}</Muted>
      {fanNote && (
        <div data-fan-in={wires} style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>
          {fanNote}
        </div>
      )}
      {mbw && (
        <div title={mbw.text}
             style={{ fontSize: 10, overflow: "hidden", textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: mbw.ok ? "var(--ok-text)" : "var(--alert-text)" }}>
          {mbw.text}
        </div>
      )}
    </NodeShell>
  );
});

//: The four stats an OUTPUT publishes about itself, per output document id.
//: Fixed contract with the app: an Email has no reading to show, only a fate,
//: and "did that actually send?" was until now answerable nowhere on the
//: canvas at all. `at` is epoch seconds, 0 for never; `error` is "" when fine.
const outStat = (values, id, part) => values[`out_${id}_${part}`];

/**
 * What a Debug probe reads out, in the five states it can be in.
 *
 * A probe is the answer to "what is actually coming down THIS wire", asked
 * while the wire is being drawn. Everything here is about refusing to answer
 * it wrongly: a probe that draws 0 when it is reading NOTHING, or a number
 * from the last run while the app is down, is worse than no probe -- it is a
 * measurement of the operator's own confusion. So each state says which one
 * it is, in words, and only the live state shows a value.
 *
 * `read` is the descriptor the page resolved from the wire (faceReads in
 * pages/Workflow.jsx); it is undefined on a canvas that has not resolved yet,
 * which reads the same as nothing wired.
 */
function DebugReadout({ read, values, appRunning, typedKey, nodeId,
                        aiStatus, waitsForDetection }) {
  const big = { fontSize: 16, fontWeight: 700, overflowWrap: "anywhere",
                color: "var(--accent-text, #04bade)" };
  const line = { fontSize: 10, color: "var(--muted-text, #999)",
                 overflow: "hidden", textOverflow: "ellipsis",
                 whiteSpace: "nowrap" };
  // A HAND-TYPED key still works. Probes drawn before this node could read
  // its own wire have a metric typed into the inspector and nothing wired,
  // and migrating them would mean guessing which wire the operator meant.
  // The wire wins when there is one; the typed key is the fallback, which is
  // also what the inspector's old placeholder always promised.
  const spec = read && read.kind !== "none" ? read
    : typedKey ? { kind: "value", key: typedKey, label: typedKey }
    : read || { kind: "none", why: "wire something into this probe" };
  // Every new reading goes into this probe's history (probeHistory.js),
  // which its inspector lists with times, Node-RED debug-tab style.
  useEffect(() => {
    if (appRunning !== false) recordProbe(nodeId, entryFor(spec, values));
  });
  if (spec.kind === "none") {
    return <div style={{ ...line, whiteSpace: "normal" }}>{spec.why}</div>;
  }
  // THE APP IS DOWN. Said before anything is read, because every stat in the
  // map is then a leftover from the last run and drawing one as the live
  // answer is the single most misleading thing this node could do.
  if (appRunning === false) {
    return (
      <>
        <div style={big}>app stopped</div>
        <div style={line} title={spec.label}>
          nothing is publishing · {spec.label}
        </div>
      </>
    );
  }
  // DETECTION IS OFF and the node this probe reads waits for it. The app
  // then publishes nothing for that node -- not a 0, not an idle payload:
  // "not triggered" is a statement about a scene nobody is watching -- so
  // the probe would say "nothing published yet" for ever, which reads as a
  // graph that is broken (Tok Aba, 2026-10-05: an Advanced Trigger with an
  // idle 0 "waiting"). The one true sentence is that the AI is off.
  const aiOff = aiStatus ? !aiOf(aiStatus).ok : false;
  if (aiOff && spec.node && waitsForDetection && waitsForDetection.has(spec.node)) {
    const reason = aiOf(aiStatus).reason;
    return (
      <>
        <div style={{ ...big, color: "var(--muted-text, #999)" }}>AI off</div>
        <div style={{ ...line, whiteSpace: "normal" }}
             title={reason ? `${spec.label}\n${reason}` : spec.label}>
          {spec.label} waits for detection
        </div>
      </>
    );
  }
  // A Data Table column is read back OUT of the database by whatever asks for
  // it; no stat exists, so a dash here would read as "nothing is happening"
  // on a table that is filling perfectly well.
  if (spec.kind === "table") {
    return (
      <div style={{ ...line, whiteSpace: "normal" }}>
        {spec.label} keeps its rows in the database - open the table, or wire
        a card to the column, to see them
      </div>
    );
  }
  if (spec.kind === "output") {
    const fired = Number(outStat(values, spec.id, "fired"));
    const ok = outStat(values, spec.id, "ok");
    const at = Number(outStat(values, spec.id, "at")) || 0;
    const err = String(outStat(values, spec.id, "error") || "");
    // NEVER FIRED and FIRED-AND-FAILED are different problems with different
    // fixes -- a condition that never holds, versus a token that is wrong --
    // and they are the two an operator confuses. Nothing published at all is
    // a third: the graph has not been saved to the app yet.
    if (!Number.isFinite(fired)) {
      return (
        <>
          <div style={{ ...big, color: "var(--muted-text, #999)" }}>no report</div>
          <div style={{ ...line, whiteSpace: "normal" }}>
            {spec.label} has not reported yet - save the canvas so the app
            picks it up
          </div>
        </>
      );
    }
    if (fired <= 0) {
      return (
        <>
          <div style={{ ...big, color: "var(--muted-text, #999)" }}>never fired</div>
          <div style={line} title={spec.label}>
            {spec.label} · nothing has met its condition
          </div>
        </>
      );
    }
    const failed = Number(ok) === 0;
    const ago = at > 0 ? `${countdown(Date.now() / 1000 - at)} ago` : "";
    return (
      <>
        {/* The theme's own status text tokens, not literal red and green:
            #ef4444 on the light shell fails AA (test_contrast.mjs), and this
            word is the whole reading. */}
        <div style={{ ...big,
                      color: failed ? "var(--alert-text)" : "var(--ok-text)" }}>
          {failed ? "FAILED" : "sent"}
        </div>
        <div style={line} title={spec.label}>
          {spec.label} · fired {fired}×{ago ? ` · last ${ago}` : ""}
        </div>
        {/* What went out, when the output says ("sent 42 to line1/count"
            from an MQTT Publish, "wrote 42 to 0" from a Modbus Write). */}
        {!failed && outStat(values, spec.id, "note") && (
          <div style={line} title={String(outStat(values, spec.id, "note"))}>
            {String(outStat(values, spec.id, "note"))}
          </div>
        )}
        {failed && err && (
          <div style={{ ...line, whiteSpace: "normal",
                        color: "var(--alert-text)" }}
               title={err}>
            {err}
          </div>
        )}
      </>
    );
  }
  const v = values[spec.key];
  // An MQTT Subscribe also publishes its last message as it arrived: the
  // raw payload and the topic (a + or # filter matches many). Shown under
  // the value, because a path that picks the wrong field is only visible
  // beside the message it picked from.
  const base = String(spec.key || "").endsWith("_value")
    ? spec.key.slice(0, -"value".length) : "";
  const rawMsg = base && values[`${base}at`] ? {
    payload: String(values[`${base}payload`] ?? ""),
    topic: String(values[`${base}topic`] ?? ""),
  } : null;
  return (
    <>
      <div style={v === undefined ? { ...big, color: "var(--muted-text, #999)" }
                                  : big}>
        {/* A WORD IS A VALUE. Rounding one gave NaN, which is how a text
            variable looked broken on every face that showed it. */}
        {v === undefined ? "waiting"
          : typeof v === "string" ? (v === "" ? '""' : v)
          : typeof v === "number" ? Math.round(v * 100) / 100
          : String(v)}
      </div>
      {/* WHICH value this is. A probe showing 3 with nothing to say what the
          3 counts is a number from nowhere, and a canvas with six counters
          on it has six of them. The stat key is the tooltip rather than the
          line: it is the precise answer, and the node's name is the readable
          one. */}
      <div style={line} title={spec.key}>
        {spec.label}{v === undefined ? " · nothing published yet" : ""}
      </div>
      {rawMsg && (
        <div style={line} title={`${rawMsg.topic}\n${rawMsg.payload}`}
             data-debug-mqtt="">
          {rawMsg.topic} · {rawMsg.payload}
        </div>
      )}
    </>
  );
}

// A comment: no shell, no handles, no header. It deliberately looks unlike a
// node - it is paper stuck to the canvas, and dressing it as a node would
// invite an operator to wire it to something.
/**
 * Everything in the Common group. A Comment draws as a sticky note (it is not
 * a node in the pipeline sense); the jump wires and the probe draw as small
 * ordinary nodes, with only the one handle each actually accepts.
 */
/** "5 · every 30 s" -- what an Inject sends and when, as the node's line. */
export function injectSummary(d) {
  const t = d.ptype || "timestamp";
  const what = t === "timestamp" ? "timestamp"
    : t === "json" ? "{…} JSON"
      : t === "boolean" ? (String(d.payload) === "false" ? "false" : "true")
        : t === "string" ? `"${String(d.payload ?? "").slice(0, 18)}"`
          : String(d.payload ?? "0");
  const bits = [what];
  if (d.topic) bits.push(`topic ${String(d.topic).slice(0, 16)}`);
  if (d.repeat === "interval") {
    const unit = { minutes: "min", hours: "h" }[d.every_unit] || "s";
    bits.push(`every ${d.every ?? 1} ${unit}`);
  }
  if (d.once) bits.push("once at start");
  return bits.join(" · ");
}

/**
 * INJECT: a button on the node that fires it on the running app, the way
 * the square on the left of a Node-RED inject does.
 *
 * The button is disabled -- with the reason as its tooltip -- whenever a
 * press could not reach anything: the canvas is unsaved, the compile did not
 * run this node, the app is down, or the account may not edit (see
 * injectBlocked in pages/Workflow.jsx). `nodrag` keeps a press from starting
 * a drag of the node it sits on.
 */
const InjectNode = memo(function InjectNode(props) {
  const { values, injectBlocked, onInject, appRunning, edges: ctxEdges,
          injectPress } = useContext(WorkflowCtx);
  const d = props.data || {};
  const why = injectBlocked ? injectBlocked({ id: props.id, data: d }) : "not ready";
  const last = d.fnId ? values?.[`fn_${d.fnId}_value`] : undefined;
  const runs = d.fnId ? values?.[`fn_${d.fnId}_runs`] : undefined;
  // A REPEATING Inject's button is Start / Stop, read back from the app's
  // own `running` stat; one without a repeat is the single press it was.
  const repeat = d.repeat === "interval";
  const running = repeat && !!(d.fnId && values?.[`fn_${d.fnId}_running`]);
  const action = !repeat ? "press" : running ? "stop" : "start";
  const verb = { press: "Inject", start: "Start", stop: "Stop" }[action];
  const unit = { minutes: "min", hours: "h" }[d.every_unit] || "s";
  // The circle beside the button: did the last injection pass or fail
  // (nodeStatus.js injectDot)? A press just made waits for the run counter
  // to move; the tick re-asks once the wait is over, so a press the box
  // never took goes red without another poll having to arrive.
  const pending = injectPress ? injectPress[props.id] : undefined;
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!pending || pending.failed) return undefined;
    const t = setTimeout(() => setTick((x) => x + 1), PRESS_WAIT_MS + 500);
    return () => clearTimeout(t);
  }, [pending]);
  const dot = injectDot({ nodeId: props.id, data: d, values, appRunning,
                          edges: ctxEdges, pending });
  return (
    <NodeShell {...props} targetHandles={[]} title={d.name}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button type="button" className="nodrag btn btn-sm"
                aria-label={`${verb} ${d.name || "now"}`}
                data-action={action}
                title={why || (repeat ? `${verb} the repeat` : "Click to inject now")}
                disabled={!!why}
                style={{ height: 26, padding: "0 8px 0 6px", flexShrink: 0,
                         display: "inline-flex", alignItems: "center",
                         justifyContent: "center", gap: 4, fontSize: 11 }}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!why) onInject?.({ id: props.id, data: d }, action);
                }}>
          {action === "stop" ? <Square size={12} aria-hidden />
                             : <Play size={13} aria-hidden />}
          {verb}
        </button>
        {dot && <StatusDot ok={dot.ok} title={dot.title} attr="inject" />}
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 10, color: "var(--muted-text, #999)",
                        overflow: "hidden", textOverflow: "ellipsis",
                        whiteSpace: "nowrap" }}>
            {injectSummary(d)}
          </div>
          {repeat && (
            <div style={{ fontSize: 10, display: "flex", alignItems: "center",
                          gap: 4 }}>
              {/* The subtle half of "is it going": a dot, lit while it is. */}
              <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999,
                                      background: running ? "var(--ok, #16a34a)"
                                                          : "rgba(128,128,128,.5)" }} />
              {running ? `running, every ${d.every ?? 1} ${unit}` : "stopped"}
            </div>
          )}
          {runs ? (
            <div className="mono" style={{ fontSize: 10, overflow: "hidden",
                                           textOverflow: "ellipsis",
                                           whiteSpace: "nowrap" }}>
              last {String(last)} · {runs}×
            </div>
          ) : null}
        </div>
      </div>
    </NodeShell>
  );
});

const CommonNode = memo(function CommonNode(props) {
  const { values, faceReads, appRunning, aiStatus,
          waitsForDetection } = useContext(WorkflowCtx);
  const d = props.data || {};
  if (props.type === "common.link_out" || props.type === "common.link_in") {
    const jumping = props.type === "common.link_out";
    return (
      <NodeShell {...props} targetHandles={jumping ? ["in"] : []}
                 hasSource={!jumping}>
        <span className="mono">{d.link || "name this jump"}</span>
      </NodeShell>
    );
  }
  if (props.type === "common.debug") {
    return (
      // One port, always: a probe takes a wire and gives nothing back, and a
      // port set that never changes is a port set React Flow cannot lose an
      // edge to.
      <NodeShell {...props} hasSource={false} targetHandles={["in"]}>
        <DebugReadout read={faceReads && faceReads.get(props.id)}
                      values={values} appRunning={appRunning}
                      aiStatus={aiStatus} waitsForDetection={waitsForDetection}
                      typedKey={d.key || d.metric || ""} nodeId={props.id} />
      </NodeShell>
    );
  }
  return <CommentNode {...props} />;
});

/**
 * The three nodes that bracket a function: a Start that hands out its
 * parameters, an End that takes what comes back, and a Call that runs its own
 * copy of the body wherever it is dropped.
 *
 * There is no nested editor and no second palette because the body is the
 * MAIN canvas between the two brackets -- which is the whole reason a function
 * is drawn this way here. Every node in the palette works inside one, with its
 * own ports, its own inspector and its own wiring rules, because it is the
 * same canvas it always was.
 *
 * The plain unnamed port on each face is the FLOW wire: Start to the first
 * node of the body, and on the Call the matching way in. It is what brackets
 * a body that takes no parameters at all.
 */
const FunctionNode = memo(function FunctionNode(props) {
  const { fnDefs, wired, registry } = useContext(WorkflowCtx);
  const d = props.data || {};
  const name = String(d.fn || "").trim();
  // A Start declares its own parameters; an End and a Call read them off the
  // Start, so renaming one there renames the port here rather than leaving
  // two faces disagreeing about what the function takes.
  const def = fnDefs?.[name.toLowerCase()];
  const params = (props.type === "function.start"
    ? (d.params || []) : (def?.params || [])).filter(Boolean);
  const ports = params.map((p) => ({ id: `p_${p}`, label: p }));
  // What the function gives back. Named on the End the way the parameters are
  // named on the Start, and handed out of every Call under the same names --
  // a function that works out a count AND how long it took has two things to
  // say, and one port could only carry one of them. An End that names none
  // keeps the single unnamed port, which is what every function drawn before
  // this uses.
  const rets = (props.type === "function.end"
    ? (d.returns || []) : (def?.returns || [])).filter(Boolean)
    .map((r) => ({ id: `p_${r}`, label: r }));
  const title = name || "name this function";
  const color = (CATEGORY_STYLE[registry?.[props.type]?.palette?.cat]
    || CATEGORY_STYLE.logic).color;

  if (props.type === "function.end") {
    if (!rets.length) {
      return (
        <NodeShell {...props} targetHandles={[{ id: "in", label: "returns" }]}
                   hasSource={false}>
          <span className="mono">{title}</span>
        </NodeShell>
      );
    }
    // One row per value it gives back, mirroring the Start's parameter rows:
    // ports on the LEFT, because this is where the body hands them over.
    const ROW = 22;
    const on = (hid) => !!wired && wired.has(`${props.id}|t|${hid || ""}`);
    return (
      <NodeShell {...props} targetHandles={[]} hasSource={false} portsInBody>
        <span className="mono">{title}</span>
        <div style={{ position: "relative", marginTop: 5 }}>
          <SectionLabel color={color}>Returns</SectionLabel>
          {rets.map((r) => (
            <div key={r.id}
                 style={{ position: "relative", height: ROW, margin: "0 -10px",
                          display: "flex", alignItems: "center",
                          justifyContent: "flex-start", padding: "0 18px" }}>
              <Handle type="target" position={Position.Left} id={r.id}
                      title={`${r.label} - wire what the function gives back here`}
                      style={portStyle("target", on(r.id), color,
                                       { top: ROW / 2 })} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                             whiteSpace: "nowrap", fontSize: 11.5 }}>
                {r.label}
              </span>
            </div>
          ))}
        </div>
      </NodeShell>
    );
  }
  if (props.type === "function.start") {
    // Parameters hand OUT, and only out. A parameter is what the body takes;
    // the value for it arrives at the Call, which is where an operator asks
    // for the function -- so there is nothing to wire into the definition
    // itself, and a port offering to receive one was a port that could only
    // be misread.
    const ROW = 22;
    const on = (hid) => !!wired && wired.has(`${props.id}|s|${hid || ""}`);
    return (
      <NodeShell {...props} targetHandles={[]} hasSource={false} portsInBody>
        <span className="mono">{title}</span>
        {!def?.closed && name && (
          <Muted>no Function End called {name} yet</Muted>
        )}
        <div style={{ position: "relative", marginTop: 5 }}>
          <SectionLabel color={color}>Parameters</SectionLabel>
          {ports.map((prm) => (
            <div key={prm.id}
                 style={{ position: "relative", height: ROW, margin: "0 -10px",
                          display: "flex", alignItems: "center",
                          justifyContent: "flex-end", padding: "0 18px" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                             whiteSpace: "nowrap", fontSize: 11.5 }}>
                {prm.label}
              </span>
              <Handle type="source" position={Position.Right} id={prm.id}
                      title={`${prm.label} - drag it to wherever the body uses it`}
                      style={portStyle("source", on(prm.id), color,
                                       { top: ROW / 2 })} />
            </div>
          ))}
          {!ports.length && (
            <Muted>none - add them in the inspector</Muted>
          )}
          <div style={{ position: "relative", height: ROW, margin: "0 -10px",
                        display: "flex", alignItems: "center",
                        justifyContent: "flex-end", padding: "0 18px" }}>
            <span style={{ fontSize: 11.5, color: "var(--muted-text, #999)" }}>body</span>
            <Handle type="source" position={Position.Right}
                    title="the first node of the function's body"
                    style={portStyle("source", on(""), color, { top: ROW / 2 })} />
          </div>
        </div>
      </NodeShell>
    );
  }
  // A Call. Unknown function = nothing to run, said on the face rather than
  // left as an empty node that silently compiles to nothing.
  return (
    <NodeShell {...props}
               targetHandles={[{ id: "in", label: "run" }, ...ports]}
               // The Call is where a parameter gets its value, so its ports
               // are the arguments for the parameters named on the Start --
               // and what comes back out of it is whatever the End names.
               targetHeading="Arguments"
               sourceHandles={rets.length ? rets : undefined}
               sourceHeading="Returns">
      <span className="mono">{title}</span>
      {name && !def && <Muted>no function called {name} on this canvas</Muted>}
    </NodeShell>
  );
});

const CommentNode = memo(function CommentNode(props) {
  const { resizeNode } = useContext(WorkflowCtx);
  const d = props.data || {};

  // Resize by dragging the corner grip -- no number fields. `nodrag` keeps
  // ReactFlow from treating the grip drag as moving the note.
  function startResize(e) {
    if (!resizeNode) return;
    e.stopPropagation();
    e.preventDefault();
    const startX = e.clientX, startY = e.clientY;
    const startW = d.width || 220, startH = d.height || 100;
    // Screen pixels -> canvas pixels: divide by the current zoom, or the note
    // races ahead of (or lags behind) the pointer at any zoom but 100%.
    const zoom = (() => {
      const el = document.querySelector(".react-flow__viewport");
      const m = el && /scale\(([\d.]+)\)/.exec(el.style.transform || "");
      return m ? parseFloat(m[1]) || 1 : 1;
    })();
    const move = (ev) => resizeNode(props.id, {
      width: Math.max(140, Math.min(900, startW + (ev.clientX - startX) / zoom)),
      height: Math.max(60, Math.min(900, startH + (ev.clientY - startY) / zoom)),
    });
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      resizeNode(props.id, {
        width: Math.max(140, Math.min(900, startW + (ev.clientX - startX) / zoom)),
        height: Math.max(60, Math.min(900, startH + (ev.clientY - startY) / zoom)),
      }, true);                          // done -> save
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div style={{
      width: d.width || 220, minHeight: d.height || 100, padding: 10,
      borderRadius: 8, fontSize: 12, lineHeight: 1.45, whiteSpace: "pre-wrap",
      background: d.color || "rgba(250, 204, 21, .12)",
      border: `1px dashed ${props.selected ? "var(--accent, #04bade)" : "rgba(148,163,184,.55)"}`,
      color: "var(--text, #e8eaed)",
      position: "relative",
    }}>
      {d.text
        ? d.text
        : <span style={{ color: "var(--muted-text, #999)" }}>
            Double-click the node, then write your note in the inspector.
          </span>}
      <div className="nodrag" onPointerDown={startResize}
           title="Drag to resize"
           style={{ position: "absolute", right: 0, bottom: 0, width: 16,
                    height: 16, cursor: "nwse-resize",
                    borderRight: "2px solid rgba(148,163,184,.7)",
                    borderBottom: "2px solid rgba(148,163,184,.7)",
                    borderBottomRightRadius: 8 }} />
    </div>
  );
});

/**
 * A Command: what a Telegram chat may ask for, and the buttons it is offered.
 *
 * One port per button, labelled with the button's own text, because the wire
 * IS the answer: whichever Query is wired to a button's port is the statement
 * that button runs. Drawn this way rather than as a list inside the inspector
 * so the canvas shows what each button reports on -- the same reason a Query's
 * tables are wires rather than a picker.
 */
const CommandNode = memo(function CommandNode(props) {
  const d = props.data || {};
  const buttons = Array.isArray(d.buttons) ? d.buttons : [];
  const handles = buttons.map((b, i) => ({
    id: `b${i}`, label: b?.label || `Button ${i + 1}`,
  }));
  return (
    // No portsInBody: that flag means "this node draws its own port rows in
    // the body", which is what a Formula and a Switch do. This one has
    // nothing to draw, so it wants NodeShell's own labelled rows -- and
    // setting the flag suppressed them, leaving a Command with buttons and
    // no dots to wire a Query to.
    <NodeShell {...props} targetHandles={handles} hasSource={false}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono" style={{ overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap",
                                        ...(d.command ? {}
                                          : { fontStyle: "italic",
                                              color: "var(--muted-text, #6b6b6b)" }) }}>
          {d.command ? `/${d.command}` : "name the command"}
        </span>
        {!handles.length && (
          <span style={{ marginLeft: "auto" }}>
            <Muted>no buttons</Muted>
          </span>
        )}
      </div>
    </NodeShell>
  );
});

/**
 * Snapshots: the pictures a command button answers with.
 *
 * No input port -- it reads what this system already saved, and a camera is a
 * setting rather than a wire (there is no node that "is" a camera to draw one
 * from). Its face says the two things that decide what arrives: which camera,
 * and how many at most.
 */
const PhotosNode = memo(function PhotosNode(props) {
  const d = props.data || {};
  return (
    <NodeShell {...props} targetHandles={[]}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono">{d.camera ? d.camera : "any camera"}</span>
        <span style={{ marginLeft: "auto" }}>
          <Muted>{Number(d.most ?? 10) <= 0 ? "all of them"
                                            : `most ${d.most ?? 10}`}</Muted>
        </span>
      </div>
    </NodeShell>
  );
});

/**
 * A Watch: a Query in, a message node out, a threshold in between.
 *
 * The face of it says the test - "> 10, today, every 15m" - because that is
 * the whole node, and an operator scanning a canvas for why a phone went off
 * at 3am should read it without opening anything.
 */
const WatchNode = memo(function WatchNode(props) {
  const d = props.data || {};
  const every = Number(d.every_min) || 15;
  return (
    <NodeShell {...props} targetHandles={[{ id: "in", label: "Query" }]}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono">
          {`${d.op || ">"} ${d.value ?? 0}`}
        </span>
        <span style={{ marginLeft: "auto" }}>
          <Muted>{`${PERIOD_WORDS[d.period] || d.period || "today"} · every ${every}m`}</Muted>
        </span>
      </div>
    </NodeShell>
  );
});

/** What each period is called on the face of a Watch. */
const PERIOD_WORDS = {
  today: "today", yesterday: "yesterday", week: "this week",
  last7: "last 7 days", month: "this month", last30: "last 30 days",
  none: "all time",
};

/**
 * A CSV File: a Query's rows in, an attachment out.
 *
 * Its own renderer for one reason: the wiring category "query" matches none of
 * the shells, so it fell through to SourceNode -- which draws NO input port,
 * because a source has nothing upstream. The compiler accepted Query into CSV
 * the whole time; there was simply no dot to start the wire from, so the one
 * connection this node exists to make could not be drawn.
 */
// The filename the compiler will actually mint (services/workflow.py _slug):
// non-alphanumerics become underscores. Shown rather than the typed text so
// the node says what lands in the inbox -- "Violation Data" arrives as
// violation_data.csv, and a node that promised otherwise was lying quietly.
const _fileStem = (text) => String(text || "").trim().toLowerCase()
  .replace(/[^a-z0-9]/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);

const CsvNode = memo(function CsvNode(props) {
  const d = props.data || {};
  const rows = Number(d.max_rows) || 0;
  // Blank borrows the wired Query's name, and the node has to SHOW that: "the
  // query's name.csv" is a description of a rule, not a filename, and beside a
  // Query plainly called "Violation Data" it reads as a node that has not
  // noticed the wire. Read through the store with a selector so this
  // re-renders when that name changes and not on every drag of every node.
  const wiredName = useStore((s) => {
    if (String(d.name || "").trim()) return "";
    const edge = [...(s.edges || [])].find((e) => e.target === props.id);
    const src = edge && s.nodeLookup?.get(edge.source);
    return src?.type === "storage.query"
      ? String(src.data?.name || "").trim() : "";
  });
  const stem = String(d.name || "").trim() || wiredName;
  return (
    <NodeShell {...props}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono" style={{ overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap",
                                        ...(stem ? {} : { fontStyle: "italic",
                                                          color: "var(--muted-text, #6b6b6b)" }) }}>
          {stem ? `${_fileStem(stem)}.csv` : "name the Query, or this file"}
        </span>
        <span style={{ marginLeft: "auto", flex: "none" }}>
          <Muted>{rows > 0 ? `first ${rows} rows` : "every row"}</Muted>
        </span>
      </div>
    </NodeShell>
  );
});

/**
 * A Query: the statement, and the tables it is allowed to read.
 *
 * Drawn as its own node rather than as a setting on a card because one query
 * is worth showing several ways -- a number, a bar chart and a table off the
 * same statement -- and because the tables it reads are WIRES: rename a table
 * and the line still points at it, where a name typed into a card would rot.
 */
const QueryNode = memo(function QueryNode(props) {
  const { registry, errors, warnings, offNodes, wired } = useContext(WorkflowCtx);
  const isWired = (kind, hid) => !!wired
    && wired.has(`${props.id}|${kind === "source" ? "s" : "t"}|${hid || ""}`);
  const spec = registry[props.type] || {};
  const style = CATEGORY_STYLE.database;
  const d = props.data || {};
  const err = errors[props.id];
  const warn = warnings[props.id];
  const off = !!offNodes && offNodes.has(props.id);
  const sql = String(d.sql || "").trim();
  // The first line is what the operator recognises it by -- "SELECT zone,
  // count(*)" says more than any name they would have bothered to type.
  const preview = sql ? sql.split("\n")[0].slice(0, 46) : "";

  return (
    <div style={{
      // No clipping, for the same reason as the Database node above: a port
      // straddles the edge, and hiding the overflow hides the port.
      minWidth: 200, maxWidth: 260, borderRadius: 10,
      background: "var(--surface, #16181d)",
      border: `1.5px solid ${err ? "#ef4444"
        : props.selected ? "var(--accent, #04bade)" : style.color + "66"}`,
      boxShadow: props.selected
        ? "0 0 0 3px color-mix(in srgb, var(--accent, #04bade) 25%, transparent)"
        : "var(--card-shadow, none)",
      fontSize: 12, color: "var(--text, #e8eaed)",
      opacity: off && !props.selected ? 0.5 : 1,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6,
                    padding: "6px 10px", background: style.color + "22",
                    borderRadius: "9px 9px 0 0",
                    borderBottom: "1px solid rgba(128,128,128,.15)" }}>
        <style.icon size={13} style={{ color: style.color, flexShrink: 0 }} />
        {/* The name IS the label here -- a Query publishes a card, and the
            editors list what a graph publishes by name. Unnamed reads as
            unfinished rather than as a node called "Query". */}
        <strong style={{ overflow: "hidden", textOverflow: "ellipsis",
                         whiteSpace: "nowrap",
                         color: d.name ? undefined : "var(--muted, #6b6b6b)",
                         fontStyle: d.name ? undefined : "italic" }}>
          {d.name || "name this query"}
        </strong>
        {(err || warn) && (
          <span title={err || warn} style={{ marginLeft: "auto", flexShrink: 0 }}>
            {err ? "🔴" : "🟡"}
          </span>
        )}
      </div>
      {/* Tables in on the left, the result out on the right. */}
      <Handle type="target" position={Position.Left} id="table"
              title="a Data Table this query may read"
              style={portStyle("target", isWired("target", "table"),
                               style.color, { top: 34 })} />
      <Handle type="source" position={Position.Right} id="out"
              title="the result - wire this to a card"
              style={portStyle("source", isWired("source", "out"),
                               style.color, { top: 34 })} />
      <div style={{ padding: "8px 14px" }}>
        {preview ? (
          <span className="mono" style={{ fontSize: 10, lineHeight: 1.4,
                                          wordBreak: "break-all" }}>
            {preview}{sql.length > preview.length ? "…" : ""}
          </span>
        ) : (
          <Muted>write a SELECT in the inspector</Muted>
        )}
      </div>
    </div>
  );
});

/** nodeTypes for <ReactFlow>, keyed by the stored node type string. */
/** What a Trigger DOES, in the few words that differ between one and the
 *  next: the node face says them, and so does the closed Advanced fold in
 *  its inspector (Workflow.jsx). Empty for one that alarms nothing, paints
 *  nothing and sends the moment's own 1 -- "event only", said by the caller. */
export function triggerWords(d) {
  const shown = (v) => String(v ?? "").slice(0, 12);
  return [d.alert ? `${d.severity || "warn"} alert` : null,
          d.alert && d.repeat === "new_object" && !d.rate ? "per new OID" : null,
          d.draw ? (d.box_in_zone ? "paints boxes in zone" : "paints boxes")
            : null,
          d.zone_color ? "paints zone" : null,
          d.payload_type
            ? (d.payload_type === "timestamp" ? "sends the time"
              : `sends ${shown(d.payload)}`) : null,
          d.rate === "cooldown" ? `at most every ${d.rate_s ?? 10}s` : null,
          d.rate === "once_per_oid" ? "once per OID" : null,
          d.dup_guard ? "skips stored object" : null,
          holdWords(d)]
    .filter(Boolean);
}

/** Is this Trigger in WATCHDOG mode -- each object once ("only for a new
 *  object", or Advanced "once per object") -- rather than on a timer? The
 *  duplicate guard reads the two differently: a watchdog never sends a
 *  stored object again, a timer does once its value has moved past the
 *  threshold. Mirrors the runtime (app/functions.py _stored_repeat). */
export function triggerWatchdog(d) {
  d = d || {};
  return d.rate === "once_per_oid"
    || (!d.rate && !!d.alert && d.repeat === "new_object");
}

/** How a Trigger holds its payload: "realtime", "interval" or "hold".
 *  Mirrors core/services/trigger_out.py hold_mode: a node saved before the
 *  choice has no out_hold, and its seconds answer -- 0 is Real-time (what
 *  it always did), N is Hold N (likewise). */
export function holdMode(d) {
  const mode = String(d?.out_hold || "").trim().toLowerCase();
  if (mode) return mode;
  return (Number(d?.out_hold_s) || 0) !== 0 ? "hold" : "realtime";
}

/** The hold, in the node line's few words. Real-time is said only on a
 *  Trigger that sends something (an Advanced one): on a plain Trigger it is
 *  simply what a Trigger is, and the line stays empty. */
function holdWords(d) {
  const mode = holdMode(d);
  if (mode === "interval") return `re-checks every ${d.out_hold_s}s`;
  if (mode === "hold") return `holds ${d.out_hold_s}s`;
  return triggerAdvanced(d) ? "holds live" : null;
}

/** Does a Trigger use any Advanced output setting? Mirrors
 *  core/services/trigger_out.py is_set: such a Trigger publishes its payload
 *  as fn_<rule>_out, and that -- not the rule's 0/1 -- is what a probe or a
 *  card wired after it reads. */
export function triggerAdvanced(d) {
  d = d || {};
  return !!(String(d.payload_type || "").trim()
            || String(d.idle_type || "").trim()
            || String(d.rate || "").trim()
            || holdMode(d) !== "realtime");
}

export function buildNodeTypes(registry) {
  const byCategory = {
    common: CommonNode,
    source: SourceNode, filter: FilterNode, zone: ZoneNode,
    analytics: AnalyticsNode, variable: VariableNode, logic: WhenNode,
    dashboard: DashboardNode, output: OutputNode,
  };
  // Per-TYPE overrides, for the few nodes whose category does not describe
  // how they draw. A Data Table wires as an output but is rendered as the
  // table it writes into, one handle per column.
  const byType = { "function.start": FunctionNode,
                   "function.end": FunctionNode,
                   "function.call": FunctionNode,
                   // A Pre-process wires as a filter but shows a CHAIN, not a
                   // class list: what the picture goes through, in order.
                   "filter.preprocess": PreprocessNode,
                   "output.dataset": DatasetNode, "storage.query": QueryNode,
                   "storage.csv": CsvNode,
                   "common.inject": InjectNode,
                   "integration.command": CommandNode,
                   "integration.watch": WatchNode,
                   "integration.photos": PhotosNode };
  const out = {};
  for (const [type, spec] of Object.entries(registry)) {
    out[type] = byType[type] || byCategory[spec.category] || SourceNode;
  }
  return out;
}
